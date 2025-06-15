import os
import time
import json
import sqlite3
import re
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
from bs4 import BeautifulSoup
from flask import current_app
from sqlalchemy import text

from ...db import get_db

class ScrapingService:
    ITEMS_PER_PAGE = 20  # 1ページあたりのサロン表示数

    def __init__(self):
        # 設定値の読み込み
        self.config = current_app.config
        self.selectors = self._load_selectors()
        self.session = requests.Session()
        self.instance_path = current_app.instance_path
        self.logger = current_app.logger
        
    def _is_cancelled(self, job_id):
        """ジョブがキャンセルされたかどうかをファイルシステムのシグナルでチェックする"""
        cancel_file = os.path.join(self.instance_path, f"{job_id}.cancel")
        return os.path.exists(cancel_file)

    def _load_selectors(self):
        """selectors.jsonを読み込む"""
        with open('selectors.json', 'r', encoding='utf-8') as f:
            return json.load(f)

    def _make_request(self, url, job_id):
        """
        信頼性を高めたHTTP GETリクエストを送信する。
        リクエストの成功・失敗にかかわらず、毎回指定秒数待機する。
        """
        for attempt in range(self.config['RETRY_COUNT']):
            if self._is_cancelled(job_id):
                self.logger.info(f"Request cancelled for {url} before attempt {attempt + 1}")
                return None

            try:
                response = self.session.get(url, timeout=10)
                response.raise_for_status()
                # 成功した場合、待機してからレスポンスを返す
                time.sleep(self.config['REQUEST_WAIT_SECONDS'])
                return response
            except requests.exceptions.RequestException as e:
                self.logger.warning(f"Request failed for {url} (attempt {attempt + 1}/{self.config['RETRY_COUNT']}): {e}")
                # 失敗した場合も、リトライする前に待機する
                time.sleep(self.config['REQUEST_WAIT_SECONDS'])
        
        self.logger.error(f"Request failed for {url} after {self.config['RETRY_COUNT']} attempts.")
        return None

    def run_scraping(self, area_id, job_id):
        """
        スクレイピング処理全体を統括し、進捗をyieldするジェネレータ。
        """
        try:
            area_info = self._get_area_info(area_id)
            if self._is_cancelled(job_id):
                yield f"event: cancelled\ndata: 処理がユーザーによって中断されました。\n\n"
                return
            yield f"event: message\ndata: 「{area_info['name']}」のスクレイピングを開始します。\n\n"

            total_pages, final_area_url = self._get_total_pages(area_info['url'], job_id)
            if self._is_cancelled(job_id) or total_pages is None:
                yield f"event: cancelled\ndata: 処理がユーザーによって中断されました。\n\n"
                return
            
            yield f"event: message\ndata: 総ページ数を特定しました: {total_pages}ページ。一覧からURLを収集中...\n\n"

            salon_urls = yield from self._get_all_salon_urls(final_area_url, total_pages, job_id)
            if self._is_cancelled(job_id):
                yield f"event: cancelled\ndata: 処理がユーザーによって中断されました。\n\n"
                return
            
            yield f"event: message\ndata: {len(salon_urls)}件のサロンURLを収集しました。詳細情報の取得を開始します。\n\n"

            salon_details = []
            with ThreadPoolExecutor(max_workers=self.config['MAX_WORKERS']) as executor:
                if not salon_urls:
                    yield f"event: message\ndata: 対象エリアにサロンが見つかりませんでした。\n\n"
                
                future_to_url = {executor.submit(self._scrape_salon_details, url, job_id): url for url in salon_urls}
                for i, future in enumerate(as_completed(future_to_url), 1):
                    if self._is_cancelled(job_id):
                        executor.shutdown(wait=False, cancel_futures=True)
                        yield f"event: cancelled\ndata: 処理がユーザーによって中断されました。\n\n"
                        break
                    
                    try:
                        result = future.result()
                        if result:
                            salon_details.append(result)
                        yield f"event: progress\ndata: {json.dumps({'current': i, 'total': len(salon_urls)})}\n\n"
                    except Exception as exc:
                        url = future_to_url[future]
                        self.logger.error(f'{url} generated an exception: {exc}')
                        yield f"event: message\ndata: エラー発生: {url} の処理中に問題がありました。\n\n"

            if self._is_cancelled(job_id):
                yield f"event: cancelled\ndata: 処理がユーザーによって中断されました。\n\n"
                return

            yield f"event: message\ndata: {len(salon_details)}件の詳細情報を取得しました。Excelファイルを生成します。\n\n"
            
            preview_data = salon_details[:5]
            file_name = self._create_excel_file(salon_details, area_info['name'])
            
            result_payload = {
                'file_name': file_name,
                'preview_data': preview_data
            }
            yield f"event: result\ndata: {json.dumps(result_payload)}\n\n"
        
        except Exception as e:
            self.logger.error(f"Scraping service error: {e}", exc_info=True)
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

    def _get_area_info(self, area_id):
        db = get_db()
        query = text('SELECT name, url FROM areas WHERE id = :id')
        area = db.execute(query, {'id': area_id}).mappings().first()
        if area is None:
            raise ValueError(f"Area with ID {area_id} not found.")
        return {'name': area['name'], 'url': area['url']}

    def _get_total_pages(self, area_url, job_id):
        response = self._make_request(area_url, job_id)
        if not response:
            return None, None

        final_url = response.url
        soup = BeautifulSoup(response.text, 'html.parser')
        pagination_element = soup.select_one(self.selectors['area_page']['pagination'])
        if not pagination_element:
            return 1, final_url
        
        pagination_text = pagination_element.text.strip()
        total_pages = 1

        # パターン1: "1/9ページ" 形式
        match_slash = re.search(r'\d+/(\d+)ページ', pagination_text)
        if match_slash:
            total_pages = int(match_slash.group(1))
            return total_pages, final_url

        # パターン2: "全150件" 形式
        match_ken = re.search(r'全(\d+)件', pagination_text)
        if match_ken:
            total_items = int(match_ken.group(1))
            total_pages = (total_items + self.ITEMS_PER_PAGE - 1) // self.ITEMS_PER_PAGE
            return total_pages, final_url
        
        return total_pages, final_url

    def _get_all_salon_urls(self, area_url, total_pages, job_id):
        all_urls = set()
        page_urls = []

        paginated_base_url = area_url
        if paginated_base_url.endswith('/'):
            paginated_base_url = paginated_base_url[:-1]

        for page in range(1, total_pages + 1):
            if page == 1:
                page_urls.append(area_url)
            else:
                page_urls.append(f"{paginated_base_url}/PN{page}.html")

        with ThreadPoolExecutor(max_workers=self.config['MAX_WORKERS']) as executor:
            if not page_urls:
                return []
            
            future_to_url = {executor.submit(self._get_salon_urls_from_page, url, job_id): url for url in page_urls}
            for i, future in enumerate(as_completed(future_to_url), 1):
                if self._is_cancelled(job_id):
                    executor.shutdown(wait=False, cancel_futures=True)
                    yield f"event: cancelled\ndata: 処理がユーザーによって中断されました。\n\n"
                    break

                yield f"event: url_progress\ndata: {json.dumps({'current': i, 'total': total_pages})}\n\n"
                try:
                    urls_from_page = future.result()
                    all_urls.update(urls_from_page)
                except Exception as exc:
                    url = future_to_url[future]
                    self.logger.error(f'{url} (list page) generated an exception: {exc}')
        
        return list(all_urls)

    def _get_salon_urls_from_page(self, page_url, job_id):
        """1つの一覧ページからサロンURLをすべて取得する"""
        urls_on_page = set()
        response = self._make_request(page_url, job_id)
        if not response:
            return urls_on_page
        
        soup = BeautifulSoup(response.text, 'html.parser')
        links = soup.select(self.selectors['area_page']['salon_url_in_list'])
        for link in links:
            if 'href' in link.attrs:
                full_url = requests.compat.urljoin(page_url, link['href'])
                urls_on_page.add(full_url)
        return urls_on_page

    def _get_value_by_th_text(self, soup, th_text):
        """
        指定されたテキストを持つ<th>の次の<td>要素の値を取得する。
        テーブル内の<th>を検索し、その隣の<td>のテキストを返す。
        """
        # 'slnDataTbl'クラスを持つテーブルにスコープを限定
        data_table = soup.select_one('table.slnDataTbl')
        if not data_table:
            return ''

        # th_textを部分的に含むth要素を検索
        th_element = data_table.find('th', string=lambda t: t and th_text in t.strip())
        if th_element:
            td_element = th_element.find_next_sibling('td')
            if td_element:
                # <p>タグなどが含まれるケースを考慮し、get_text()でテキストを抽出
                return td_element.get_text(separator=' ', strip=True)
        return ''

    def _scrape_salon_details(self, salon_url, job_id):
        response = self._make_request(salon_url, job_id)
        if not response: return None
        soup = BeautifulSoup(response.text, 'html.parser')

        def get_text(selector):
            element = soup.select_one(selector)
            return element.text.strip() if element else ''

        phone_page_link = soup.select_one(self.selectors['salon_detail']['phone_page_link'])
        phone_number = ''
        if phone_page_link and 'href' in phone_page_link.attrs:
            phone_page_url = requests.compat.urljoin(salon_url, phone_page_link['href'])
            phone_number = self._scrape_phone_number(phone_page_url, job_id)

        related_links_elements = soup.select(self.selectors['salon_detail']['related_links'])
        related_links = [link['href'] for link in related_links_elements if 'href' in link.attrs]

        return {
            'サロン名': get_text(self.selectors['salon_detail']['name']),
            '電話番号': phone_number,
            '住所': self._get_value_by_th_text(soup, self.selectors['salon_detail']['address_label']),
            'スタッフ数': self._get_value_by_th_text(soup, self.selectors['salon_detail']['staff_count_label']),
            '関連リンク': "\n".join(related_links),
            '関連リンク数': len(related_links),
            'サロンURL': salon_url.split('?')[0],
        }

    def _scrape_phone_number(self, phone_page_url, job_id):
        """電話番号が掲載されている別ページから電話番号を取得"""
        response = self._make_request(phone_page_url, job_id)
        if not response: return ''
        soup = BeautifulSoup(response.text, 'html.parser')
        phone_element = soup.select_one(self.selectors['phone_page']['phone_number'])
        return phone_element.text.strip() if phone_element else ''

    def _create_excel_file(self, data, area_name):
        if not data:
            self.logger.warning("No data scraped, creating an empty Excel file.")
        
        # タイムスタンプをファイル名に追加
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_area_name = re.sub(r'[\\/*?:"<>|]', "", area_name)
        file_name = f"{safe_area_name}_{timestamp}.xlsx"
        
        output_path = os.path.join(self.config['OUTPUT_DIR'], file_name)
        
        # ディレクトリが存在しない場合は作成
        os.makedirs(self.config['OUTPUT_DIR'], exist_ok=True)
        
        df = pd.DataFrame(data)
        columns_order = ['サロン名', '電話番号', '住所', 'スタッフ数', '関連リンク', '関連リンク数', 'サロンURL']
        for col in columns_order:
            if col not in df.columns:
                df[col] = None
        df = df[columns_order]

        df.to_excel(output_path, index=False, sheet_name='サロンリスト')
        return file_name