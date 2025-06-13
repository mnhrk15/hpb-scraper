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

from ...db import get_db

class ScrapingService:
    def __init__(self):
        # 設定値の読み込み
        self.config = current_app.config
        self.selectors = self._load_selectors()
        self.session = requests.Session()
        
    def _load_selectors(self):
        """selectors.jsonを読み込む"""
        with open('selectors.json', 'r', encoding='utf-8') as f:
            return json.load(f)

    def _make_request(self, url):
        """信頼性を高めたHTTP GETリクエストを送信する"""
        for i in range(self.config['RETRY_COUNT']):
            try:
                time.sleep(self.config['REQUEST_WAIT_SECONDS'])
                response = self.session.get(url, timeout=20)
                response.raise_for_status()
                return response
            except requests.exceptions.RequestException as e:
                current_app.logger.warning(f"Request failed for {url} (attempt {i+1}/{self.config['RETRY_COUNT']}): {e}")
        current_app.logger.error(f"Request failed for {url} after {self.config['RETRY_COUNT']} attempts.")
        return None

    def run_scraping(self, area_id):
        """
        スクレイピング処理全体を統括し、進捗をyieldするジェネレータ。
        """
        try:
            area_info = self._get_area_info(area_id)
            yield f"event: message\ndata: 「{area_info['name']}」のスクレイピングを開始します。\n\n"

            total_pages, final_area_url = self._get_total_pages(area_info['url'])
            yield f"event: message\ndata: 総ページ数を特定しました: {total_pages}ページ。一覧からURLを収集中...\n\n"

            salon_urls = yield from self._get_all_salon_urls(final_area_url, total_pages)
            
            yield f"event: message\ndata: {len(salon_urls)}件のユニークなサロンURLを収集しました。詳細情報の取得を開始します。\n\n"

            salon_details = []
            with ThreadPoolExecutor(max_workers=self.config['MAX_WORKERS']) as executor:
                if not salon_urls:
                    yield f"event: message\ndata: 対象エリアにサロンが見つかりませんでした。\n\n"
                
                future_to_url = {executor.submit(self._scrape_salon_details, url): url for url in salon_urls}
                for i, future in enumerate(as_completed(future_to_url), 1):
                    try:
                        result = future.result()
                        if result:
                            salon_details.append(result)
                        yield f"event: progress\ndata: {json.dumps({'current': i, 'total': len(salon_urls)})}\n\n"
                    except Exception as exc:
                        url = future_to_url[future]
                        current_app.logger.error(f'{url} generated an exception: {exc}')
                        yield f"event: message\ndata: エラー発生: {url} の処理中に問題がありました。\n\n"

            yield f"event: message\ndata: {len(salon_details)}件の詳細情報を取得しました。Excelファイルを生成します。\n\n"
            file_name = self._create_excel_file(salon_details, area_info['name'])
            yield f"event: result\ndata: {json.dumps({'file_name': file_name})}\n\n"
        
        except Exception as e:
            current_app.logger.error(f"Scraping service error: {e}", exc_info=True)
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

    def _get_area_info(self, area_id):
        db = get_db()
        area = db.execute('SELECT name, url FROM areas WHERE id = ?', (area_id,)).fetchone()
        if area is None:
            raise ValueError(f"Area with ID {area_id} not found.")
        return {'name': area['name'], 'url': area['url']}

    def _get_total_pages(self, area_url):
        response = self._make_request(area_url)
        if not response:
            return 1, area_url

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
            total_pages = (total_items + 19) // 20
            return total_pages, final_url
        
        return total_pages, final_url

    def _get_all_salon_urls(self, area_url, total_pages):
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
            
            future_to_url = {executor.submit(self._get_salon_urls_from_page, url): url for url in page_urls}
            for i, future in enumerate(as_completed(future_to_url), 1):
                yield f"event: url_progress\ndata: {json.dumps({'current': i, 'total': total_pages})}\n\n"
                try:
                    urls_from_page = future.result()
                    all_urls.update(urls_from_page)
                except Exception as exc:
                    url = future_to_url[future]
                    current_app.logger.error(f'{url} (list page) generated an exception: {exc}')
        
        return list(all_urls)

    def _get_salon_urls_from_page(self, page_url):
        """1つの一覧ページからサロンURLをすべて取得する"""
        urls_on_page = set()
        response = self._make_request(page_url)
        if not response:
            return urls_on_page
        
        soup = BeautifulSoup(response.text, 'html.parser')
        links = soup.select(self.selectors['area_page']['salon_url_in_list'])
        for link in links:
            if 'href' in link.attrs:
                full_url = requests.compat.urljoin(page_url, link['href'])
                urls_on_page.add(full_url)
        return urls_on_page

    def _scrape_salon_details(self, salon_url):
        response = self._make_request(salon_url)
        if not response: return None
        soup = BeautifulSoup(response.text, 'html.parser')

        def get_text(selector):
            element = soup.select_one(selector)
            return element.text.strip() if element else ''

        phone_page_link = soup.select_one(self.selectors['salon_detail']['phone_page_link'])
        phone_number = ''
        if phone_page_link and 'href' in phone_page_link.attrs:
            phone_page_url = requests.compat.urljoin(salon_url, phone_page_link['href'])
            phone_number = self._scrape_phone_number(phone_page_url)

        related_links_elements = soup.select(self.selectors['salon_detail']['related_links'])
        related_links = [link['href'] for link in related_links_elements if 'href' in link.attrs]

        return {
            'サロン名': get_text(self.selectors['salon_detail']['name']),
            '電話番号': phone_number,
            '住所': get_text(self.selectors['salon_detail']['address']),
            'スタッフ数': get_text(self.selectors['salon_detail']['staff_count']),
            '関連リンク': "\\n".join(related_links),
            '関連リンク数': len(related_links),
            'サロンURL': salon_url,
        }

    def _scrape_phone_number(self, phone_page_url):
        response = self._make_request(phone_page_url)
        if not response: return ''
        soup = BeautifulSoup(response.text, 'html.parser')
        phone_element = soup.select_one(self.selectors['phone_page']['phone_number'])
        return phone_element.text.strip() if phone_element else ''

    def _create_excel_file(self, data, area_name):
        if not data:
            current_app.logger.warning("No data scraped, creating an empty Excel file.")
        
        df = pd.DataFrame(data)
        columns_order = ['サロン名', '電話番号', '住所', 'スタッフ数', '関連リンク', '関連リンク数', 'サロンURL']
        for col in columns_order:
            if col not in df.columns:
                df[col] = ''
        df = df[columns_order]

        safe_area_name = re.sub(r'[\\/*?:"<>|]', '_', area_name)
        file_name = f"{safe_area_name}_{datetime.now().strftime('%Y%m%d')}.xlsx"
        output_dir = 'output'
        os.makedirs(output_dir, exist_ok=True)
        file_path = os.path.join(output_dir, file_name)
        
        df.to_excel(file_path, index=False, sheet_name='サロンリスト')
        return file_name