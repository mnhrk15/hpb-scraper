import json
import os
import time
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from app.main.services.instagram_service import InstagramSearchService, SerperAPIError


def _make_serper_response(organic_results, status_code=200):
    """Serper APIのモックレスポンスを作成するヘルパー。"""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = {'organic': organic_results}
    mock_resp.raise_for_status = MagicMock()
    if status_code >= 400:
        from requests.exceptions import HTTPError
        mock_resp.raise_for_status.side_effect = HTTPError(response=mock_resp)
    return mock_resp


def _collect_sse_events(generator):
    """SSEジェネレータからイベントを収集してパースする。"""
    events = []
    for raw in generator:
        lines = raw.strip().split('\n')
        event_type = None
        data = None
        for line in lines:
            if line.startswith('event: '):
                event_type = line[7:]
            elif line.startswith('data: '):
                data = line[6:]
        if event_type:
            try:
                data = json.loads(data)
            except (json.JSONDecodeError, TypeError):
                pass
            events.append((event_type, data))
    return events


# --- _search_instagram ---

class TestSearchInstagram:
    def test_successful_search(self, app_context):
        """Instagram URLを含む検索結果を正しく抽出する。"""
        organic = [
            {'title': 'サロンA Instagram', 'link': 'https://www.instagram.com/salon_a/', 'snippet': '...'},
            {'title': 'サロンA 公式サイト', 'link': 'https://salon-a.com/', 'snippet': '...'},
            {'title': 'サロンA on IG', 'link': 'https://www.instagram.com/salon_a_official/', 'snippet': '...'},
        ]
        mock_resp = _make_serper_response(organic)

        with patch.object(InstagramSearchService, '__init__', lambda self: None):
            service = InstagramSearchService()
            service.config = app_context.config
            service.session = MagicMock()
            service.session.post.return_value = mock_resp
            service.instance_path = app_context.instance_path
            service.logger = app_context.logger
            service.max_urls = 3

            urls = service._search_instagram('サロンA', 'test-job-id')

        assert urls == [
            'https://www.instagram.com/salon_a/',
            'https://www.instagram.com/salon_a_official/',
        ]

    def test_no_instagram_results(self, app_context):
        """Instagram URLが含まれない場合は空リストを返す。"""
        organic = [
            {'title': 'サロンA', 'link': 'https://salon-a.com/', 'snippet': '...'},
            {'title': 'サロンA Facebook', 'link': 'https://facebook.com/salon_a', 'snippet': '...'},
        ]
        mock_resp = _make_serper_response(organic)

        with patch.object(InstagramSearchService, '__init__', lambda self: None):
            service = InstagramSearchService()
            service.config = app_context.config
            service.session = MagicMock()
            service.session.post.return_value = mock_resp
            service.instance_path = app_context.instance_path
            service.logger = app_context.logger
            service.max_urls = 3

            urls = service._search_instagram('サロンA', 'test-job-id')

        assert urls == []

    def test_max_urls_limit(self, app_context):
        """max_urls件を超えるInstagram URLは切り捨てる。"""
        organic = [
            {'title': f'Result {i}', 'link': f'https://www.instagram.com/salon{i}/', 'snippet': '...'}
            for i in range(10)
        ]
        mock_resp = _make_serper_response(organic)

        with patch.object(InstagramSearchService, '__init__', lambda self: None):
            service = InstagramSearchService()
            service.config = app_context.config
            service.session = MagicMock()
            service.session.post.return_value = mock_resp
            service.instance_path = app_context.instance_path
            service.logger = app_context.logger
            service.max_urls = 3

            urls = service._search_instagram('サロンA', 'test-job-id')

        assert len(urls) == 3

    def test_api_key_invalid_raises(self, app_context):
        """401エラー時にSerperAPIErrorを発生させる。"""
        mock_resp = MagicMock()
        mock_resp.status_code = 401

        with patch.object(InstagramSearchService, '__init__', lambda self: None):
            service = InstagramSearchService()
            service.config = app_context.config
            service.session = MagicMock()
            service.session.post.return_value = mock_resp
            service.instance_path = app_context.instance_path
            service.logger = app_context.logger
            service.max_urls = 3

            with pytest.raises(SerperAPIError, match='APIキーが無効'):
                service._search_instagram('サロンA', 'test-job-id')

    def test_credit_exhausted_raises(self, app_context):
        """402エラー時にSerperAPIErrorを発生させる。"""
        mock_resp = MagicMock()
        mock_resp.status_code = 402

        with patch.object(InstagramSearchService, '__init__', lambda self: None):
            service = InstagramSearchService()
            service.config = app_context.config
            service.session = MagicMock()
            service.session.post.return_value = mock_resp
            service.instance_path = app_context.instance_path
            service.logger = app_context.logger
            service.max_urls = 3

            with pytest.raises(SerperAPIError, match='クレジットが不足'):
                service._search_instagram('サロンA', 'test-job-id')

    @patch('app.main.services.instagram_service.time.sleep')
    def test_rate_limit_retry(self, mock_sleep, app_context):
        """429エラー時に指数バックオフでリトライし、成功する。"""
        rate_limited_resp = MagicMock()
        rate_limited_resp.status_code = 429

        success_resp = _make_serper_response([
            {'title': 'IG', 'link': 'https://www.instagram.com/salon/', 'snippet': '...'},
        ])

        with patch.object(InstagramSearchService, '__init__', lambda self: None):
            service = InstagramSearchService()
            service.config = app_context.config
            service.session = MagicMock()
            service.session.post.side_effect = [rate_limited_resp, rate_limited_resp, success_resp]
            service.instance_path = app_context.instance_path
            service.logger = app_context.logger
            service.max_urls = 3

            urls = service._search_instagram('サロンA', 'test-job-id')

        assert urls == ['https://www.instagram.com/salon/']
        # 429リトライで2回sleep、指数バックオフ: 1s, 2s
        assert mock_sleep.call_count == 2
        mock_sleep.assert_any_call(1)
        mock_sleep.assert_any_call(2)

    @patch('app.main.services.instagram_service.time.sleep')
    def test_network_error_retry_then_empty(self, mock_sleep, app_context):
        """ネットワークエラーがRETRY_COUNT回続くと空リストを返す。"""
        from requests.exceptions import ConnectionError

        with patch.object(InstagramSearchService, '__init__', lambda self: None):
            service = InstagramSearchService()
            service.config = {**app_context.config, 'RETRY_COUNT': 2}
            service.session = MagicMock()
            service.session.post.side_effect = ConnectionError('connection refused')
            service.instance_path = app_context.instance_path
            service.logger = app_context.logger
            service.max_urls = 3

            urls = service._search_instagram('サロンA', 'test-job-id')

        assert urls == []
        assert service.session.post.call_count == 2

    @patch('app.main.services.instagram_service.time.sleep')
    def test_rate_limit_does_not_consume_network_retries(self, mock_sleep, app_context):
        """429リトライはネットワークリトライ回数を消費しない。"""
        from requests.exceptions import ConnectionError

        rate_limited_resp = MagicMock()
        rate_limited_resp.status_code = 429

        success_resp = _make_serper_response([
            {'title': 'IG', 'link': 'https://www.instagram.com/salon/', 'snippet': '...'},
        ])

        with patch.object(InstagramSearchService, '__init__', lambda self: None):
            service = InstagramSearchService()
            service.config = {**app_context.config, 'RETRY_COUNT': 1}
            service.session = MagicMock()
            # 429 → 429 → success: ネットワークリトライ0回なのでRETRY_COUNT=1でも成功する
            service.session.post.side_effect = [rate_limited_resp, rate_limited_resp, success_resp]
            service.instance_path = app_context.instance_path
            service.logger = app_context.logger
            service.max_urls = 3

            urls = service._search_instagram('サロンA', 'test-job-id')

        assert urls == ['https://www.instagram.com/salon/']


# --- run_instagram_search ---

class TestRunInstagramSearch:
    @patch('app.main.services.instagram_service.time.sleep')
    def test_full_flow(self, mock_sleep, app_context, sample_excel):
        """正常フロー: Excel読み込み→検索→結果Excel生成。"""
        organic = [
            {'title': 'IG', 'link': 'https://www.instagram.com/salon_a/', 'snippet': '...'},
        ]
        mock_resp = _make_serper_response(organic)

        service = InstagramSearchService()
        service.session = MagicMock()
        service.session.post.return_value = mock_resp

        events = _collect_sse_events(service.run_instagram_search(sample_excel, 'test-job'))

        # イベントタイプの確認
        event_types = [e[0] for e in events]
        assert 'message' in event_types
        assert 'progress' in event_types
        assert 'result' in event_types
        assert 'error' not in event_types

        # result イベントの検証
        result_event = next(e[1] for e in events if e[0] == 'result')
        assert result_event['total_salons'] == 3
        assert result_event['found_count'] == 3
        assert result_event['file_name'].startswith('Instagram_五所川原_')
        assert result_event['file_name'].endswith('.xlsx')

        # 生成されたExcelの検証
        output_path = os.path.join(app_context.config['OUTPUT_DIR'], result_event['file_name'])
        assert os.path.exists(output_path)
        result_df = pd.read_excel(output_path)
        assert 'Instagram候補URL1' in result_df.columns
        assert 'Instagram候補URL2' in result_df.columns
        assert 'Instagram候補URL3' in result_df.columns
        assert result_df['Instagram候補URL1'].iloc[0] == 'https://www.instagram.com/salon_a/'

    def test_file_not_found(self, app_context):
        """存在しないファイルを指定した場合にエラーイベントを返す。"""
        service = InstagramSearchService()
        events = _collect_sse_events(service.run_instagram_search('nonexistent.xlsx', 'test-job'))

        assert len(events) == 1
        assert events[0][0] == 'error'
        assert 'ファイルが見つかりません' in events[0][1]['error']

    def test_missing_salon_name_column(self, app_context):
        """サロン名カラムがないExcelの場合にエラーイベントを返す。"""
        output_dir = app_context.config['OUTPUT_DIR']
        file_name = 'bad_20260319_153045.xlsx'
        df = pd.DataFrame({'名前': ['A', 'B'], '電話': ['111', '222']})
        df.to_excel(os.path.join(output_dir, file_name), index=False)

        service = InstagramSearchService()
        events = _collect_sse_events(service.run_instagram_search(file_name, 'test-job'))

        error_events = [e for e in events if e[0] == 'error']
        assert len(error_events) == 1
        assert 'サロン名カラムが見つかりません' in error_events[0][1]['error']

    @patch('app.main.services.instagram_service.time.sleep')
    def test_duplicate_salon_names(self, mock_sleep, app_context, duplicate_salon_excel):
        """同名サロンがある場合でも各行に個別の結果を保持する。"""
        call_count = 0

        def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            # 各呼び出しで異なるURLを返す
            organic = [
                {'title': 'IG', 'link': f'https://www.instagram.com/salon_call{call_count}/', 'snippet': '...'},
            ]
            return _make_serper_response(organic)

        service = InstagramSearchService()
        service.session = MagicMock()
        service.session.post.side_effect = mock_post

        events = _collect_sse_events(service.run_instagram_search(duplicate_salon_excel, 'test-job'))
        result_event = next(e[1] for e in events if e[0] == 'result')

        output_path = os.path.join(app_context.config['OUTPUT_DIR'], result_event['file_name'])
        result_df = pd.read_excel(output_path)

        # 同名の「チェーン店A」が2行あり、それぞれ異なるURLを持つことを確認
        chain_rows = result_df[result_df['サロン名'] == 'チェーン店A']
        assert len(chain_rows) == 2
        url1 = chain_rows.iloc[0]['Instagram候補URL1']
        url2 = chain_rows.iloc[1]['Instagram候補URL1']
        assert url1 != url2  # 異なるURLであること
        assert 'instagram.com' in url1
        assert 'instagram.com' in url2

    @patch('app.main.services.instagram_service.time.sleep')
    def test_cancellation(self, mock_sleep, app_context, sample_excel):
        """キャンセルシグナルで検索が中断される。"""
        # キャンセルファイルを作成
        job_id = 'cancel-test-job'
        cancel_file = os.path.join(app_context.instance_path, f'{job_id}.cancel')
        with open(cancel_file, 'w') as f:
            f.write(str(time.time()))

        service = InstagramSearchService()
        events = _collect_sse_events(service.run_instagram_search(sample_excel, job_id))

        event_types = [e[0] for e in events]
        assert 'cancelled' in event_types
        assert 'result' not in event_types

        # クリーンアップ
        if os.path.exists(cancel_file):
            os.remove(cancel_file)

    @patch('app.main.services.instagram_service.time.sleep')
    def test_serper_api_error_stops_search(self, mock_sleep, app_context, sample_excel):
        """SerperAPIError (401/402) 発生時に即座にエラーを返して停止する。"""
        mock_resp = MagicMock()
        mock_resp.status_code = 401

        service = InstagramSearchService()
        service.session = MagicMock()
        service.session.post.return_value = mock_resp

        events = _collect_sse_events(service.run_instagram_search(sample_excel, 'test-job'))

        error_events = [e for e in events if e[0] == 'error']
        assert len(error_events) == 1
        assert 'APIキーが無効' in error_events[0][1]['error']
        # APIエラー後は1回のみ呼ばれる（2番目以降のサロンは呼ばれない）
        assert service.session.post.call_count == 1

    @patch('app.main.services.instagram_service.time.sleep')
    def test_progress_events(self, mock_sleep, app_context, sample_excel):
        """進捗イベントがサロン数分だけ送出される。"""
        mock_resp = _make_serper_response([])

        service = InstagramSearchService()
        service.session = MagicMock()
        service.session.post.return_value = mock_resp

        events = _collect_sse_events(service.run_instagram_search(sample_excel, 'test-job'))

        progress_events = [e for e in events if e[0] == 'progress']
        assert len(progress_events) == 3  # 3サロン
        assert progress_events[-1][1] == {'current': 3, 'total': 3}

    @patch('app.main.services.instagram_service.time.sleep')
    def test_column_order(self, mock_sleep, app_context, sample_excel):
        """出力Excelのカラム順序が正しい。"""
        organic = [
            {'title': 'IG', 'link': 'https://www.instagram.com/salon/', 'snippet': '...'},
        ]
        mock_resp = _make_serper_response(organic)

        service = InstagramSearchService()
        service.session = MagicMock()
        service.session.post.return_value = mock_resp

        events = _collect_sse_events(service.run_instagram_search(sample_excel, 'test-job'))
        result_event = next(e[1] for e in events if e[0] == 'result')

        output_path = os.path.join(app_context.config['OUTPUT_DIR'], result_event['file_name'])
        result_df = pd.read_excel(output_path)

        cols = list(result_df.columns)
        # サロン名が最初、その後にInstagram候補URL、次に電話番号・住所・サロンURL
        assert cols[0] == 'サロン名'
        assert cols[1] == 'Instagram候補URL1'
        assert cols[2] == 'Instagram候補URL2'
        assert cols[3] == 'Instagram候補URL3'
        assert '電話番号' in cols
        assert '住所' in cols
        assert 'サロンURL' in cols


# --- _create_instagram_excel ---

class TestCreateInstagramExcel:
    def test_area_name_extraction(self, app_context):
        """ソースファイル名からエリア名を正しく抽出する。"""
        service = InstagramSearchService()
        df = pd.DataFrame({'サロン名': ['A']})

        file_name = service._create_instagram_excel(df, '五所川原_20260319_153045.xlsx')
        assert file_name.startswith('Instagram_五所川原_')

    def test_unknown_filename_format(self, app_context):
        """パターンに合わないファイル名はunknownを使用する。"""
        service = InstagramSearchService()
        df = pd.DataFrame({'サロン名': ['A']})

        file_name = service._create_instagram_excel(df, 'random_file.xlsx')
        assert 'Instagram_unknown_' in file_name


# --- _is_cancelled ---

class TestIsCancelled:
    def test_no_cancel_file(self, app_context):
        """キャンセルファイルがなければFalse。"""
        service = InstagramSearchService()
        assert service._is_cancelled('nonexistent-job') is False

    def test_valid_cancel_file(self, app_context):
        """有効なキャンセルファイルがあればTrue。"""
        job_id = 'cancel-check-test'
        cancel_file = os.path.join(app_context.instance_path, f'{job_id}.cancel')
        with open(cancel_file, 'w') as f:
            f.write(str(time.time()))

        service = InstagramSearchService()
        assert service._is_cancelled(job_id) is True

        os.remove(cancel_file)

    def test_stale_cancel_file(self, app_context):
        """タイムスタンプが古いキャンセルファイルはFalse。"""
        job_id = 'stale-cancel-test'
        cancel_file = os.path.join(app_context.instance_path, f'{job_id}.cancel')
        # タイムアウト期間を超えた古いタイムスタンプ
        stale_time = time.time() - app_context.config['CANCEL_FILE_TIMEOUT_SECONDS'] - 100
        with open(cancel_file, 'w') as f:
            f.write(str(stale_time))

        service = InstagramSearchService()
        assert service._is_cancelled(job_id) is False

        os.remove(cancel_file)
