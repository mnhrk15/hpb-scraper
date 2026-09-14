import os
import time
import json
import sqlite3
import re
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit, urlencode, parse_qsl
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
from bs4 import BeautifulSoup
from flask import current_app
from openpyxl import Workbook
from sqlalchemy import text

from ...db import get_db
from . import check_service

class ScrapingService:
    ITEMS_PER_PAGE = 20  # 1ページあたりのサロン表示数
    MAX_STYLIST_PAGES = 10  # スタイリストタブの取得上限ページ数（実測では28名でも1ページ）

    def __init__(self):
        # 設定値の読み込み
        self.config = current_app.config
        self.selectors = self._load_selectors()
        self.session = requests.Session()
        
        # User-Agentを設定
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
        })
        
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

    def _build_freeword_url(self, base_url, freeword):
        """エリアURLにfreewordクエリを付与する。freewordがNone/空ならbase_urlをそのまま返す（後方互換）。"""
        if not freeword:
            return base_url
        parts = urlsplit(base_url)
        query = dict(parse_qsl(parts.query))
        query['freeword'] = freeword  # urlencodeが日本語を%XXエンコードする
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    def run_scraping(self, area_id, job_id, freeword=None, ng_list_path=None):
        """
        スクレイピング処理全体を統括し、進捗をyieldするジェネレータ。

        ng_list_path を渡すと打電NGリストとの突合を行う。未指定なら突合はスキップする。
        """
        try:
            area_info = self._get_area_info(area_id)
            # フリーワードを正規化（前後空白除去、空文字はNone扱い）
            freeword = (freeword or '').strip() or None

            if self._is_cancelled(job_id):
                yield f"event: cancelled\ndata: 処理がユーザーによって中断されました。\n\n"
                return
            if freeword:
                yield f"event: message\ndata: 「{area_info['name']}」を『{freeword}』で絞り込んでスクレイピングを開始します。\n\n"
            else:
                yield f"event: message\ndata: 「{area_info['name']}」のスクレイピングを開始します。\n\n"

            # エリアURLにfreewordクエリを合成（freeword=Noneなら元のURLのまま＝後方互換）
            start_url = self._build_freeword_url(area_info['url'], freeword)

            total_pages, final_area_url = self._get_total_pages(start_url, job_id, freeword)
            if self._is_cancelled(job_id) or total_pages is None:
                yield f"event: cancelled\ndata: 処理がユーザーによって中断されました。\n\n"
                return

            yield f"event: message\ndata: 総ページ数を特定しました: {total_pages}ページ。一覧からURLを収集中...\n\n"

            salon_urls = yield from self._get_all_salon_urls(final_area_url, total_pages, job_id, freeword)
            if self._is_cancelled(job_id):
                yield f"event: cancelled\ndata: 処理がユーザーによって中断されました。\n\n"
                return
            
            yield f"event: message\ndata: {len(salon_urls)}件のサロンURLを収集しました。詳細情報の取得を開始します。\n\n"

            # 一覧の出現順のまま結果を並べる（重複除去でどの行を残すかを再現可能にするため）
            salon_details = [None] * len(salon_urls)
            with ThreadPoolExecutor(max_workers=self.config['MAX_WORKERS']) as executor:
                if not salon_urls:
                    yield f"event: message\ndata: 対象エリアにサロンが見つかりませんでした。\n\n"

                future_to_index = {
                    executor.submit(self._scrape_salon_details, url, job_id): (index, url)
                    for index, url in enumerate(salon_urls)
                }
                for i, future in enumerate(as_completed(future_to_index), 1):
                    if self._is_cancelled(job_id):
                        executor.shutdown(wait=False, cancel_futures=True)
                        yield f"event: cancelled\ndata: 処理がユーザーによって中断されました。\n\n"
                        break

                    index, url = future_to_index[future]
                    try:
                        result = future.result()
                        if result:
                            salon_details[index] = result
                        yield f"event: progress\ndata: {json.dumps({'current': i, 'total': len(salon_urls)})}\n\n"
                    except Exception as exc:
                        self.logger.error(f'{url} generated an exception: {exc}')
                        yield f"event: message\ndata: エラー発生: {url} の処理中に問題がありました。\n\n"

            salon_details = [detail for detail in salon_details if detail]

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
                df = df.reset_index(drop=True)

            # 打電リストチェック（電話番号の重複 → NG突合 → スタイリスト実人数 → 店名キーワード）
            memo_sections = check_service.new_memo_sections()
            if not df.empty:
                ng_index = None
                if ng_list_path:
                    try:
                        ng_index = check_service.build_ng_index(ng_list_path)
                        yield f"event: message\ndata: 打電NGリストを読み込みました（電話番号{len(ng_index['tels'])}件・サロンID{len(ng_index['ids'])}件）。\n\n"
                    except Exception as exc:
                        self.logger.error(f"Failed to read NG list {ng_list_path}: {exc}")
                        yield f"event: message\ndata: 打電NGリストの読み込みに失敗したため突合をスキップします。\n\n"
                yield from self._run_checks(
                    df, memo_sections, job_id, ng_index, ng_requested=bool(ng_list_path)
                )

            if self._is_cancelled(job_id):
                yield f"event: cancelled\ndata: 処理がユーザーによって中断されました。\n\n"
                return

            # データ分割
            df_target = df[df['is_excluded'] == False].copy() if not df.empty else pd.DataFrame()
            df_excluded = df[df['is_excluded'] == True].copy() if not df.empty else pd.DataFrame()
            
            yield f"event: message\ndata: 営業対象: {len(df_target)}件、除外対象: {len(df_excluded)}件に分類しました。\n\n"
            
            # Excelファイル生成
            file_name = self._create_target_excel_file(df_target, area_info['name'], freeword)
            excluded_file_name = None
            if not df_excluded.empty:
                excluded_file_name = self._create_excluded_excel_file(df_excluded, area_info['name'], freeword)
                yield f"event: message\ndata: 除外リストも生成しました。\n\n"
            
            memo_file_name = self._create_memo_excel_file(memo_sections, area_info['name'], freeword)
            yield f"event: message\ndata: 対応メモを生成しました。\n\n"

            # プレビューデータは営業対象リストから生成
            preview_data = df_target.head(5).to_dict('records') if not df_target.empty else []
            # is_excluded と exclusion_reason をプレビューデータから除去
            for item in preview_data:
                item.pop('is_excluded', None)
                item.pop('exclusion_reason', None)
            
            result_payload = {
                'file_name': file_name,
                'excluded_file_name': excluded_file_name,
                'memo_file_name': memo_file_name,
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

    def _get_total_pages(self, area_url, job_id, freeword=None):
        response = self._make_request(area_url, job_id)
        if not response:
            return None, None

        final_url = response.url
        # freeword指定時、リダイレクトでクエリが落ちても確実に保持する。
        # _build_freeword_urlは既存クエリ(searchGender等)をマージしつつfreewordを補う。
        # freeword=Noneなら何もしない（後方互換）。
        final_url = self._build_freeword_url(final_url, freeword)
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

    def _get_all_salon_urls(self, area_url, total_pages, job_id, freeword=None):
        page_urls = []

        # area_urlからクエリ(?freeword=...)をpathと分離する。
        # 単純な文字列連結だと page2 で "...salon/?freeword=kw/PN2.html" のように壊れるため、
        # PN{N}.html を path に付けてからクエリを再付与する。
        parts = urlsplit(area_url)
        query = dict(parse_qsl(parts.query))
        if freeword:
            query['freeword'] = freeword  # クエリが落ちていても確実にfreewordを含める
        query_string = urlencode(query)
        base_path = parts.path[:-1] if parts.path.endswith('/') else parts.path

        for page in range(1, total_pages + 1):
            if page == 1:
                page_path = parts.path
            else:
                page_path = f"{base_path}/PN{page}.html"
            page_urls.append(urlunsplit((parts.scheme, parts.netloc, page_path, query_string, '')))

        with ThreadPoolExecutor(max_workers=self.config['MAX_WORKERS']) as executor:
            if not page_urls:
                return []
            
            future_to_page = {
                executor.submit(self._get_salon_urls_from_page, url, job_id): (page_index, url)
                for page_index, url in enumerate(page_urls)
            }
            urls_by_page = {}
            for i, future in enumerate(as_completed(future_to_page), 1):
                if self._is_cancelled(job_id):
                    executor.shutdown(wait=False, cancel_futures=True)
                    yield f"event: cancelled\ndata: 処理がユーザーによって中断されました。\n\n"
                    break

                yield f"event: url_progress\ndata: {json.dumps({'current': i, 'total': total_pages})}\n\n"
                page_index, url = future_to_page[future]
                try:
                    urls_by_page[page_index] = future.result()
                except Exception as exc:
                    self.logger.error(f'{url} (list page) generated an exception: {exc}')

        # ページ順・ページ内の掲載順で並べる。
        # 電話番号だけが同じ行の重複除去で「どちらを残すか」を実行ごとに再現するため、
        # ここで順序を確定させる（setのまま返すと非決定になる）。
        ordered_urls = []
        seen = set()
        for page_index in sorted(urls_by_page):
            for url in urls_by_page[page_index]:
                if url not in seen:
                    seen.add(url)
                    ordered_urls.append(url)
        return ordered_urls

    def _get_salon_urls_from_page(self, page_url, job_id):
        """1つの一覧ページからサロンURLを掲載順に取得する"""
        urls_on_page = []
        response = self._make_request(page_url, job_id)
        if not response:
            return urls_on_page

        soup = BeautifulSoup(response.text, 'html.parser')
        links = soup.select(self.selectors['area_page']['salon_url_in_list'])
        for link in links:
            if 'href' in link.attrs:
                full_url = requests.compat.urljoin(page_url, link['href'])
                if full_url not in urls_on_page:
                    urls_on_page.append(full_url)
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
                # 対応メモの区分に合わせたラベル。スタイリストタブの判定と同じ区分に入れる。
                exclusion_reasons.append("スタイリスト人数が1名")
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

    def _create_target_excel_file(self, df_target, area_name, freeword=None):
        """営業対象リストのExcelファイルを作成"""
        # タイムスタンプをファイル名に追加
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_area_name = re.sub(r'[\\/*?:"<>|]', "", area_name)
        safe_freeword = re.sub(r'[\\/*?:"<>|\x00-\x1f]', "", freeword).strip() if freeword else ''
        if safe_freeword:
            file_name = f"{safe_area_name}_{safe_freeword}_{timestamp}.xlsx"
        else:
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
    
    def _create_excluded_excel_file(self, df_excluded, area_name, freeword=None):
        """除外リストのExcelファイルを作成"""
        # タイムスタンプをファイル名に追加
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_area_name = re.sub(r'[\\/*?:"<>|]', "", area_name)
        safe_freeword = re.sub(r'[\\/*?:"<>|\x00-\x1f]', "", freeword).strip() if freeword else ''
        if safe_freeword:
            file_name = f"除外リスト_{safe_area_name}_{safe_freeword}_{timestamp}.xlsx"
        else:
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

    # --- 打電リストチェック ---

    def _memo_row(self, row, note):
        """対応メモの1行（営業対象リストと同じ7列＋備考）を作る。"""
        values = {}
        for column in check_service.LIST_COLUMNS:
            value = row.get(column, '')
            if value is None or (isinstance(value, float) and pd.isna(value)):
                value = ''
            elif hasattr(value, 'item'):
                # numpyのスカラーはopenpyxlが書けないのでPythonの型に戻す
                value = value.item()
            values[column] = value
        values['備考'] = note
        return values

    def _mark_excluded(self, df, index, reason):
        """チェック段で除外する。既存のexclusion_reasonがあれば', 'で連結する。"""
        df.at[index, 'is_excluded'] = True
        current = str(df.at[index, 'exclusion_reason'] or '')
        df.at[index, 'exclusion_reason'] = f'{current}, {reason}' if current else reason

    def _target_indexes(self, df):
        """まだ除外されていない（＝営業対象の）行のindexを返す。"""
        return [index for index in df.index if not df.at[index, 'is_excluded']]

    def _run_checks(self, df, memo_sections, job_id, ng_index=None, ng_requested=False):
        """
        詳細取得後の打電リストチェック。dfを直接書き換え、対応メモの区分を埋める。

        NGと重複を先に落としてからスタイリストタブを取りに行き、リクエスト数を抑える。
        既存の除外（EPRP・エステ/リラク・電話番号なし・スタッフ数・関連リンク数）で
        落ちた行にもスタイリストタブは取りに行かない。
        """
        # 既存の除外行を対応メモの区分へ振り分ける
        for index in df.index:
            if not df.at[index, 'is_excluded']:
                continue
            for reason in str(df.at[index, 'exclusion_reason'] or '').split(','):
                section = check_service.REASON_TO_SECTION.get(reason.strip())
                if section:
                    memo_sections[section].append(self._memo_row(df.loc[index], ''))

        # (1) 電話番号だけ同じ行の重複除去（先頭を残す）
        dropped, groups = check_service.dedupe_by_tel(
            (index, df.at[index, '電話番号']) for index in self._target_indexes(df)
        )
        for index in dropped:
            self._mark_excluded(df, index, '重複店舗')
        for _tel, members in groups:
            # 残した行がどれか分かるよう、グループの全行を備考付きで載せる
            for index, is_kept in members:
                memo_sections[check_service.MEMO_SECTION_DUPLICATE].append(
                    self._memo_row(df.loc[index], '残' if is_kept else '除外')
                )
        if dropped:
            yield f"event: message\ndata: 電話番号が同じ店舗 {len(dropped)}件を重複として除外しました。\n\n"

        # (2) NGリスト突合
        if ng_index:
            ng_count = 0
            for index in self._target_indexes(df):
                matched_key = check_service.match_ng(
                    df.at[index, '電話番号'], df.at[index, 'サロンURL'], ng_index
                )
                if matched_key:
                    self._mark_excluded(df, index, 'NGリスト')
                    memo_sections[check_service.MEMO_SECTION_NG].append(
                        self._memo_row(df.loc[index], f'一致キー: {matched_key}')
                    )
                    ng_count += 1
            yield f"event: message\ndata: 打電NGリストと突合し {ng_count}件を除外しました。\n\n"
        elif not ng_requested:
            # 指定されたが読み込みに失敗した場合は、呼び出し側が既に理由を通知している
            yield f"event: message\ndata: 打電NGリスト未指定のため突合をスキップしました。\n\n"

        if self._is_cancelled(job_id):
            return

        # (3) スタイリストタブの実人数で判定
        if self.config.get('STYLIST_CHECK_ENABLED', True):
            yield from self._check_stylists(df, memo_sections, job_id)
        else:
            yield f"event: message\ndata: スタイリスト実人数の判定は設定で無効化されています。\n\n"

        if self._is_cancelled(job_id):
            return

        # (4) 店名キーワード（除外せず「要確認」に出す）
        keywords = check_service.parse_flag_keywords(self.config.get('CHECK_FLAG_KEYWORDS'))
        flagged_count = 0
        for index in self._target_indexes(df):
            hits = check_service.find_flag_keywords(df.at[index, 'サロン名'], keywords)
            if hits:
                memo_sections[check_service.MEMO_SECTION_REVIEW].append(
                    self._memo_row(df.loc[index], '店名キーワード: ' + '／'.join(hits))
                )
                flagged_count += 1
        if flagged_count:
            yield f"event: message\ndata: 店名キーワードで {flagged_count}件を「要確認」に分類しました。\n\n"

    def _check_stylists(self, df, memo_sections, job_id):
        """スタイリストタブを並列取得し、実スタイリスト1名以下の店舗を除外する。"""
        targets = self._target_indexes(df)
        if not targets:
            return

        yield f"event: message\ndata: {len(targets)}件のスタイリストタブを取得して実人数を数えます。\n\n"

        parsed_by_index = {}
        with ThreadPoolExecutor(max_workers=self.config['MAX_WORKERS']) as executor:
            future_to_index = {
                executor.submit(
                    self._fetch_stylist_info,
                    df.at[index, 'サロンURL'], df.at[index, 'サロン名'], job_id,
                ): index
                for index in targets
            }
            for count, future in enumerate(as_completed(future_to_index), 1):
                if self._is_cancelled(job_id):
                    executor.shutdown(wait=False, cancel_futures=True)
                    yield f"event: cancelled\ndata: 処理がユーザーによって中断されました。\n\n"
                    return

                index = future_to_index[future]
                try:
                    parsed_by_index[index] = future.result()
                except Exception as exc:
                    self.logger.error(
                        f"stylist page failed for {df.at[index, 'サロンURL']}: {exc}"
                    )
                    parsed_by_index[index] = None
                progress = {'current': count, 'total': len(targets), 'phase': 'stylist'}
                yield f"event: progress\ndata: {json.dumps(progress)}\n\n"

        excluded_count = 0
        failed_count = 0
        for index in targets:
            parsed = parsed_by_index.get(index)
            if not parsed or not parsed['has_section'] or parsed['slots'] == 0:
                # 取得できなかった／枠を1つも読み取れなかった店舗は落とさず「要確認」に回す
                # （機械が黙って落とさない）。
                # 枠0を除外しないのは、セレクタが陳腐化すると slots=0 のまま見出しだけ
                # 一致し、全店舗が「実0人」で除外されてしまうため。
                note = (
                    'スタイリストページ取得失敗'
                    if not parsed or not parsed['has_section']
                    else 'スタイリスト枠を読み取れず'
                )
                memo_sections[check_service.MEMO_SECTION_REVIEW].append(
                    self._memo_row(df.loc[index], note)
                )
                failed_count += 1
                continue

            if parsed['real_count'] <= 1:
                self._mark_excluded(df, index, 'スタイリスト人数が1名')
                memo_sections[check_service.MEMO_SECTION_STYLIST].append(
                    self._memo_row(df.loc[index], check_service.stylist_memo_note(parsed))
                )
                excluded_count += 1

        yield f"event: message\ndata: 実スタイリスト1名以下の {excluded_count}件を除外しました。\n\n"
        if failed_count:
            yield f"event: message\ndata: スタイリストの実人数を判定できなかった {failed_count}件は「要確認」に回しました。\n\n"

    def _fetch_stylist_info(self, salon_url, salon_name, job_id):
        """
        1店舗のスタイリストタブを取得して解析する。取得できなければNone。
        2ページ目以降は {サロンURL}stylist/PN{n}.html（2026-09-14に実サイトで確認）。
        """
        selectors = self.selectors.get('stylist_page')

        first_url = check_service.build_stylist_url(salon_url, 1)
        if not first_url:
            return None
        response = self._make_request(first_url, job_id)
        if not response:
            return None

        parsed = check_service.parse_stylist_page(response.text, salon_name, selectors)
        total_pages = min(
            check_service.parse_stylist_total_pages(response.text, selectors),
            self.MAX_STYLIST_PAGES,
        )

        for page in range(2, total_pages + 1):
            page_response = self._make_request(
                check_service.build_stylist_url(salon_url, page), job_id
            )
            if not page_response:
                break
            extra = check_service.parse_stylist_page(page_response.text, salon_name, selectors)
            for key in ('slots', 'assistants', 'placeholders', 'real_count'):
                parsed[key] += extra[key]
            parsed['placeholder_names'].extend(extra['placeholder_names'])
            parsed['names'].extend(extra['names'])

        return parsed

    def _create_memo_excel_file(self, memo_sections, area_name, freeword=None):
        """
        対応メモのExcelファイルを作成する。
        区分見出し → 該当行（7列＋備考） → 空行 の繰り返し。該当なしは「該当なし」。
        """
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_area_name = re.sub(r'[\\/*?:"<>|]', "", area_name)
        safe_freeword = re.sub(r'[\\/*?:"<>|\x00-\x1f]', "", freeword).strip() if freeword else ''
        if safe_freeword:
            file_name = f"対応メモ_{safe_area_name}_{safe_freeword}_{timestamp}.xlsx"
        else:
            file_name = f"対応メモ_{safe_area_name}_{timestamp}.xlsx"

        os.makedirs(self.config['OUTPUT_DIR'], exist_ok=True)
        output_path = os.path.join(self.config['OUTPUT_DIR'], file_name)

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = check_service.safe_sheet_name(area_name)

        for heading in check_service.MEMO_SECTION_ORDER:
            sheet.append([heading])
            rows = memo_sections.get(heading) or []
            if not rows:
                sheet.append(['該当なし'])
            else:
                for row in rows:
                    sheet.append(
                        [row.get(column, '') for column in check_service.LIST_COLUMNS]
                        + [row.get('備考', '')]
                    )
            sheet.append([])

        workbook.save(output_path)
        return file_name
