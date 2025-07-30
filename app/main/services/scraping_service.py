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
        """
        ジョブがキャンセルされたかどうかをチェックする。
        シグナルファイルが存在し、かつタイムスタンプが古くない場合にTrueを返す。
        """
        cancel_file = os.path.join(self.instance_path, f"{job_id}.cancel")
        if not os.path.exists(cancel_file):
            return False

        try:
            with open(cancel_file, 'r') as f:
                timestamp_str = f.read().strip()
            
            if not timestamp_str:
                self.logger.warning(f"Cancel file for job {job_id} is empty.")
                return False # or True, depending on desired behavior for empty file

            timestamp = float(timestamp_str)
            age = time.time() - timestamp

            if age > self.config['CANCEL_FILE_TIMEOUT_SECONDS']:
                self.logger.warning(f"Stale cancel file found for job {job_id} ({age:.0f}s old). Ignoring.")
                return False
            
            return True # ファイルが存在し、かつタイムスタンプが有効期間内
        except (ValueError, IOError) as e:
            self.logger.error(f"Error reading cancel file for job {job_id}: {e}")
            return False # ファイルが読めない場合はキャンセルとしない

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

            yield f"event: message\ndata: {len(salon_details)}件の詳細情報を取得しました。データを処理してExcelファイルを生成します。\n\n"
            
            # DataFrameに変換
            df = pd.DataFrame(salon_details)
            
            # 重複削除: 電話番号とサロンURLをキーとする
            if not df.empty:
                df_before_dedup = df.copy()
                df = df.drop_duplicates(subset=['電話番号', 'サロンURL'], keep='first')
                removed_count = len(df_before_dedup) - len(df)
                if removed_count > 0:
                    yield f"event: message\ndata: 重複店舗 {removed_count}件を削除しました。\n\n"
            
            # データ分割
            df_target = df[df['is_excluded'] == False].copy() if not df.empty else pd.DataFrame()
            df_excluded = df[df['is_excluded'] == True].copy() if not df.empty else pd.DataFrame()
            
            yield f"event: message\ndata: 営業対象: {len(df_target)}件、除外対象: {len(df_excluded)}件に分類しました。\n\n"
            
            # Excelファイル生成
            file_name = self._create_target_excel_file(df_target, area_info['name'])
            excluded_file_name = None
            if not df_excluded.empty:
                excluded_file_name = self._create_excluded_excel_file(df_excluded, area_info['name'])
                yield f"event: message\ndata: 除外リストも生成しました。\n\n"
            
            # プレビューデータは営業対象リストから生成
            preview_data = df_target.head(5).to_dict('records') if not df_target.empty else []
            # is_excluded と exclusion_reason をプレビューデータから除去
            for item in preview_data:
                item.pop('is_excluded', None)
                item.pop('exclusion_reason', None)
            
            result_payload = {
                'file_name': file_name,
                'excluded_file_name': excluded_file_name,
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

        staff_count_text = self._get_value_by_th_text(soup, self.selectors['salon_detail']['staff_count_label'])
        salon_name = get_text(self.selectors['salon_detail']['name'])
        address = self._get_value_by_th_text(soup, self.selectors['salon_detail']['address_label'])
        clean_salon_url = salon_url.split('?')[0]

        # 除外条件判定
        exclusion_reasons = []
        
        # EPRP店舗判定: 特集セクションが存在しない場合
        special_feature_element = soup.select_one(self.selectors['salon_detail']['special_feature_section'])
        is_eprp = special_feature_element is None
        if is_eprp:
            exclusion_reasons.append("EPRP")
        
        # エステ/リラク店舗判定: URLに/kr/が含まれる場合
        # 例: https://beauty.hotpepper.jp/kr/slnH000169389/
        is_este_relax = '/kr/' in clean_salon_url or '/kr/' in salon_url
        if is_este_relax:
            exclusion_reasons.append("エステ/リラク")
        
        # 電話番号なし判定
        is_no_phone = not phone_number or phone_number.strip() == ''
        if is_no_phone:
            exclusion_reasons.append("電話番号なし")
        
        # スタッフ数判定: 「スタイリスト1人」かつ「アシスタントなし」の店舗を除外
        is_single_stylist_no_assistant = False
        if staff_count_text:
            # デバッグ用: スタッフ数テキストをログに出力
            self.logger.debug(f"スタッフ数テキスト: '{staff_count_text}' (サロン: {salon_name})")
            
            # スタイリスト1人(名)の様々なパターンをチェック
            # 実際のデータ: 「スタイリスト1人」
            stylist_patterns = [
                r'スタイリスト\s*[：:]\s*1\s*[人名]',   # スタイリスト：1人、スタイリスト：1名
                r'スタイリスト\s+1\s*[人名]',           # スタイリスト 1人、スタイリスト 1名
                r'スタイリスト1[人名]',                  # スタイリスト1人、スタイリスト1名 (スペースなし)
                r'スタイリスト\s*1\s*[人名]'            # スタイリスト1人、スタイリスト 1 人 (柔軟なスペース対応)
            ]
            
            stylist_match = False
            matched_pattern = None
            for pattern in stylist_patterns:
                if re.search(pattern, staff_count_text):
                    stylist_match = True
                    matched_pattern = pattern
                    break
            
            # アシスタントが含まれているかチェック
            has_assistant = 'アシスタント' in staff_count_text
            
            # デバッグ用ログ
            if stylist_match:
                self.logger.debug(f"スタイリスト1人マッチ: パターン={matched_pattern}, アシスタント有無={has_assistant} (サロン: {salon_name})")
            
            # スタイリスト1人かつアシスタントなしの場合のみ除外
            if stylist_match and not has_assistant:
                is_single_stylist_no_assistant = True
                exclusion_reasons.append("スタッフ数")
                self.logger.debug(f"スタッフ数で除外: {salon_name}")
        
        # 関連リンク数判定: 4以上の場合
        is_many_links = len(related_links) >= 4
        if is_many_links:
            exclusion_reasons.append("関連リンク数")
        
        # 総合判定
        is_excluded = len(exclusion_reasons) > 0
        exclusion_reason = ', '.join(exclusion_reasons) if is_excluded else ''

        return {
            'サロン名': salon_name,
            '電話番号': phone_number,
            '住所': address,
            'スタッフ数': staff_count_text,
            '関連リンク': "\n".join(related_links),
            '関連リンク数': len(related_links),
            'サロンURL': clean_salon_url,
            'is_excluded': is_excluded,
            'exclusion_reason': exclusion_reason,
        }

    def _scrape_phone_number(self, phone_page_url, job_id):
        """電話番号が掲載されている別ページから電話番号を取得"""
        response = self._make_request(phone_page_url, job_id)
        if not response: return ''
        soup = BeautifulSoup(response.text, 'html.parser')
        phone_element = soup.select_one(self.selectors['phone_page']['phone_number'])
        return phone_element.text.strip() if phone_element else ''

    def _create_target_excel_file(self, df_target, area_name):
        """営業対象リストのExcelファイルを作成"""
        # タイムスタンプをファイル名に追加
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_area_name = re.sub(r'[\\/*?:"<>|]', "", area_name)
        file_name = f"{safe_area_name}_{timestamp}.xlsx"
        
        output_path = os.path.join(self.config['OUTPUT_DIR'], file_name)
        
        # ディレクトリが存在しない場合は作成
        os.makedirs(self.config['OUTPUT_DIR'], exist_ok=True)
        
        if df_target.empty:
            # 空のDataFrameでもカラム構造を維持
            columns_order = ['サロン名', '電話番号', '住所', 'スタッフ数', '関連リンク', '関連リンク数', 'サロンURL']
            df_target = pd.DataFrame(columns=columns_order)
        else:
            # 既存のカラム構成と順序を完全に維持（is_excluded、exclusion_reasonは除外）
            columns_order = ['サロン名', '電話番号', '住所', 'スタッフ数', '関連リンク', '関連リンク数', 'サロンURL']
            # 存在しないカラムは空で追加
            for col in columns_order:
                if col not in df_target.columns:
                    df_target[col] = None
            df_target = df_target[columns_order]

        df_target.to_excel(output_path, index=False, sheet_name='サロンリスト')
        return file_name
    
    def _create_excluded_excel_file(self, df_excluded, area_name):
        """除外リストのExcelファイルを作成"""
        # タイムスタンプをファイル名に追加
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_area_name = re.sub(r'[\\/*?:"<>|]', "", area_name)
        file_name = f"除外リスト_{safe_area_name}_{timestamp}.xlsx"
        
        output_path = os.path.join(self.config['OUTPUT_DIR'], file_name)
        
        # ディレクトリが存在しない場合は作成
        os.makedirs(self.config['OUTPUT_DIR'], exist_ok=True)
        
        if not df_excluded.empty:
            # exclusion_reasonを先頭に配置したカラム構成
            columns_order = ['exclusion_reason', 'サロン名', '電話番号', '住所', 'スタッフ数', '関連リンク', '関連リンク数', 'サロンURL']
            
            # カラム名を日本語に変更
            df_excluded = df_excluded.rename(columns={'exclusion_reason': '除外理由'})
            columns_order[0] = '除外理由'  # カラム順序も更新
            
            # 存在しないカラムは空で追加
            for col in columns_order:
                if col not in df_excluded.columns:
                    df_excluded[col] = None
            df_excluded = df_excluded[columns_order]
        else:
            # 空のDataFrameでもカラム構造を維持
            columns_order = ['除外理由', 'サロン名', '電話番号', '住所', 'スタッフ数', '関連リンク', '関連リンク数', 'サロンURL']
            df_excluded = pd.DataFrame(columns=columns_order)

        df_excluded.to_excel(output_path, index=False, sheet_name='除外リスト')
        return file_name