import os
import re
import json
import time
from datetime import datetime

import pandas as pd
import requests
from flask import current_app


class SerperAPIError(Exception):
    """致命的なSerper APIエラー (401/402等)"""
    pass


class InstagramSearchService:
    SERPER_SEARCH_URL = 'https://google.serper.dev/search'

    def __init__(self):
        self.config = current_app.config
        self.session = requests.Session()
        self.session.headers.update({
            'X-API-KEY': self.config['SERPER_API_KEY'],
            'Content-Type': 'application/json',
        })
        self.instance_path = current_app.instance_path
        self.logger = current_app.logger
        self.max_urls = self.config.get('INSTAGRAM_MAX_URLS', 3)

    def _is_cancelled(self, job_id):
        cancel_file = os.path.join(self.instance_path, f"{job_id}.cancel")
        if not os.path.exists(cancel_file):
            return False
        try:
            with open(cancel_file, 'r') as f:
                timestamp_str = f.read().strip()
            if not timestamp_str:
                return False
            timestamp = float(timestamp_str)
            age = time.time() - timestamp
            if age > self.config['CANCEL_FILE_TIMEOUT_SECONDS']:
                return False
            return True
        except (ValueError, IOError):
            return False

    def _search_instagram(self, salon_name, job_id):
        """1サロンのInstagram URLを検索する。最大max_urls件のURLリストを返す。"""
        payload = {
            'q': f'{salon_name} Instagram',
            'gl': 'jp',
            'hl': 'ja',
            'num': 10,
        }

        max_rate_limit_retries = 5
        rate_limit_delays = [1, 2, 4, 8, 16]
        rate_limit_count = 0
        network_attempts = 0
        last_exception = None

        while network_attempts < self.config['RETRY_COUNT']:
            if self._is_cancelled(job_id):
                return []
            try:
                response = self.session.post(self.SERPER_SEARCH_URL, json=payload, timeout=10)

                if response.status_code == 401:
                    raise SerperAPIError('Serper APIキーが無効です。.envのSERPER_API_KEYを確認してください。')
                if response.status_code == 402:
                    raise SerperAPIError('Serper APIのクレジットが不足しています。https://serper.dev/ で確認してください。')

                if response.status_code == 429:
                    rate_limit_count += 1
                    if rate_limit_count > max_rate_limit_retries:
                        self.logger.error(f"Serper API rate limit exceeded {max_rate_limit_retries} times for '{salon_name}'")
                        return []
                    delay = rate_limit_delays[min(rate_limit_count - 1, len(rate_limit_delays) - 1)]
                    self.logger.warning(f"Serper API rate limited. Retrying in {delay}s... ({rate_limit_count}/{max_rate_limit_retries})")
                    time.sleep(delay)
                    continue  # 429はnetwork_attemptsを消費しない

                response.raise_for_status()
                data = response.json()

                instagram_urls = []
                for result in data.get('organic', []):
                    link = result.get('link', '')
                    if 'instagram.com' in link and len(instagram_urls) < self.max_urls:
                        instagram_urls.append(link)
                return instagram_urls

            except SerperAPIError:
                raise
            except requests.exceptions.RequestException as e:
                network_attempts += 1
                last_exception = e
                self.logger.warning(f"Serper API request failed for '{salon_name}' (attempt {network_attempts}/{self.config['RETRY_COUNT']}): {e}")
                time.sleep(1)

        self.logger.error(f"Serper API request failed for '{salon_name}' after {self.config['RETRY_COUNT']} attempts: {last_exception}")
        return []

    def _create_instagram_excel(self, df, source_file_name):
        """Instagram検索結果のExcelファイルを作成する。"""
        # ソースファイル名からエリア名を推定: 五所川原_20260319_153045.xlsx → 五所川原
        area_match = re.match(r'^(.+?)_\d{8}_\d{6}\.xlsx$', source_file_name)
        area_name = area_match.group(1) if area_match else 'unknown'
        safe_area_name = re.sub(r'[\\/*?:"<>|]', '', area_name)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        file_name = f"Instagram_{safe_area_name}_{timestamp}.xlsx"
        output_path = os.path.join(self.config['OUTPUT_DIR'], file_name)
        os.makedirs(self.config['OUTPUT_DIR'], exist_ok=True)

        df.to_excel(output_path, index=False, sheet_name='Instagram検索結果')
        return file_name

    def run_instagram_search(self, target_file_name, job_id):
        """Instagram検索を実行し、SSEイベントをyieldするジェネレータ。"""
        try:
            # 入力ファイル読み込み
            file_path = os.path.join(self.config['OUTPUT_DIR'], target_file_name)
            if not os.path.exists(file_path):
                yield f'event: error\ndata: {json.dumps({"error": f"ファイルが見つかりません: {target_file_name}"})}\n\n'
                return

            yield f'event: message\ndata: Excelファイルを読み込んでいます...\n\n'
            df = pd.read_excel(file_path)

            if 'サロン名' not in df.columns:
                yield f'event: error\ndata: {json.dumps({"error": "Excelファイルにサロン名カラムが見つかりません。"})}\n\n'
                return

            # サロン名がNaNでない行のインデックスと名前のペアを取得
            salon_entries = [(idx, name) for idx, name in df['サロン名'].items() if pd.notna(name)]
            total = len(salon_entries)

            if total == 0:
                yield f'event: error\ndata: {json.dumps({"error": "サロン名が0件です。"})}\n\n'
                return

            yield f'event: message\ndata: {total}件のサロンに対してInstagram検索を開始します。\n\n'

            # 行インデックス → Instagram URLリストのマッピング（同名サロン対応）
            results_map = {}
            found_count = 0

            for i, (row_idx, salon_name) in enumerate(salon_entries, 1):
                if self._is_cancelled(job_id):
                    yield f'event: cancelled\ndata: Instagram検索がユーザーによって中断されました。\n\n'
                    return

                try:
                    urls = self._search_instagram(salon_name, job_id)
                except SerperAPIError as e:
                    yield f'event: error\ndata: {json.dumps({"error": str(e)})}\n\n'
                    return

                if self._is_cancelled(job_id):
                    yield f'event: cancelled\ndata: Instagram検索がユーザーによって中断されました。\n\n'
                    return

                results_map[row_idx] = urls
                if urls:
                    found_count += 1

                yield f'event: progress\ndata: {json.dumps({"current": i, "total": total})}\n\n'

            yield f'event: message\ndata: 検索完了。結果をExcelファイルに出力しています...\n\n'

            # Instagram URL カラムを追加（行インデックスベース）
            for col_idx in range(1, self.max_urls + 1):
                col_name = f'Instagram候補URL{col_idx}'
                df[col_name] = df.index.map(
                    lambda row_idx, ci=col_idx: results_map.get(row_idx, [])[ci - 1] if len(results_map.get(row_idx, [])) >= ci else ''
                )

            # カラム順序を整理: サロン名, Instagram候補URL1-3, 電話番号, 住所, サロンURL, ...残り
            ig_cols = [f'Instagram候補URL{i}' for i in range(1, self.max_urls + 1)]
            priority_cols = ['サロン名'] + ig_cols
            remaining_priority = ['電話番号', '住所', 'サロンURL']
            for col in remaining_priority:
                if col in df.columns:
                    priority_cols.append(col)
            other_cols = [c for c in df.columns if c not in priority_cols]
            df = df[priority_cols + other_cols]

            file_name = self._create_instagram_excel(df, target_file_name)

            result_payload = {
                'file_name': file_name,
                'total_salons': total,
                'found_count': found_count,
            }
            yield f'event: result\ndata: {json.dumps(result_payload)}\n\n'

        except Exception as e:
            self.logger.error(f"Instagram search service error: {e}", exc_info=True)
            yield f'event: error\ndata: {json.dumps({"error": str(e)})}\n\n'
