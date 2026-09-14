import json
import os
from unittest.mock import patch, MagicMock

import pytest


class TestInstagramSearchAvailable:
    def test_available_when_key_set(self, client):
        """APIキーが設定されている場合にavailable=trueを返す。"""
        resp = client.get('/api/instagram-search-available')
        data = resp.get_json()
        assert resp.status_code == 200
        assert data['available'] is True

    def test_unavailable_when_key_empty(self, app):
        """APIキーが空の場合にavailable=falseを返す。"""
        app.config['SERPER_API_KEY'] = ''
        client = app.test_client()
        resp = client.get('/api/instagram-search-available')
        data = resp.get_json()
        assert resp.status_code == 200
        assert data['available'] is False


class TestInstagramSearchEndpoint:
    def test_missing_target_file(self, client):
        """target_fileパラメータ未指定でエラーSSEを返す。"""
        resp = client.get('/instagram-search')
        data = resp.get_data(as_text=True)
        assert 'event: error' in data
        assert '対象ファイルが指定されていません' in data

    def test_path_traversal_rejected(self, client):
        """パストラバーサルを含むファイル名が拒否される。"""
        resp = client.get('/instagram-search?target_file=../etc/passwd')
        data = resp.get_data(as_text=True)
        assert 'event: error' in data
        assert '無効なファイル名' in data

    def test_path_traversal_dot_slash(self, client):
        """./を含むパスが拒否される。"""
        resp = client.get('/instagram-search?target_file=./file.xlsx')
        data = resp.get_data(as_text=True)
        assert 'event: error' in data
        assert '無効なファイル名' in data

    def test_sse_stream_returns_job_id(self, client, sample_excel):
        """SSEストリームがjob_idイベントを返す。"""
        with patch('app.main.routes.InstagramSearchService') as MockService:
            instance = MockService.return_value
            instance.run_instagram_search.return_value = iter([
                'event: message\ndata: テスト完了\n\n',
            ])

            resp = client.get(f'/instagram-search?target_file={sample_excel}')
            data = resp.get_data(as_text=True)

        assert 'event: job_id' in data

    def test_cancel_endpoint_reusable(self, client):
        """既存のキャンセルエンドポイントがInstagram検索のjob_idでも動作する。"""
        resp = client.post(
            '/scrape/cancel',
            json={'job_id': 'abcdef1234567890'},
            content_type='application/json',
        )
        data = resp.get_json()
        assert resp.status_code == 200
        assert data['status'] == 'cancellation_requested'

    def test_cancel_invalid_job_id(self, client):
        """不正なjob_idでキャンセルするとエラー。"""
        resp = client.post(
            '/scrape/cancel',
            json={'job_id': '../evil'},
            content_type='application/json',
        )
        assert resp.status_code == 400


class TestScrapeEndpointFreeword:
    def test_freeword_passed_to_service(self, client):
        """freewordパラメータがrun_scrapingの第3引数として渡される。"""
        with patch('app.main.routes.ScrapingService') as MockService:
            instance = MockService.return_value
            instance.run_scraping.return_value = iter([
                'event: message\ndata: done\n\n',
            ])
            resp = client.get('/scrape?area_id=1&freeword=髪質改善')
            resp.get_data(as_text=True)  # ストリームを消費してジェネレータを実行

            instance.run_scraping.assert_called_once()
            args = instance.run_scraping.call_args.args
            assert args[0] == '1'          # area_id
            assert args[2] == '髪質改善'    # freeword

    def test_no_freeword_passes_none(self, client):
        """freeword未指定時はrun_scrapingの第3引数がNone（後方互換）。"""
        with patch('app.main.routes.ScrapingService') as MockService:
            instance = MockService.return_value
            instance.run_scraping.return_value = iter([
                'event: message\ndata: done\n\n',
            ])
            resp = client.get('/scrape?area_id=1')
            resp.get_data(as_text=True)

            instance.run_scraping.assert_called_once()
            args = instance.run_scraping.call_args.args
            assert args[0] == '1'
            assert args[2] is None

    def test_missing_area_id_error(self, client):
        """area_id未指定でエラーSSEを返す（freeword有無に関わらず）。"""
        resp = client.get('/scrape?freeword=髪質改善')
        data = resp.get_data(as_text=True)
        assert 'event: error' in data
        assert 'エリアが選択されていません' in data


class TestNgListUpload:
    def _xlsx_bytes(self):
        import io
        import openpyxl

        workbook = openpyxl.Workbook()
        workbook.active.append(['店名', '電話番号', 'URL'])
        buffer = io.BytesIO()
        workbook.save(buffer)
        return buffer.getvalue()

    def test_upload_returns_token(self, client):
        """xlsxをアップロードするとトークンが返る。"""
        import io

        resp = client.post(
            '/nglist',
            data={'file': (io.BytesIO(self._xlsx_bytes()), 'ng.xlsx')},
            content_type='multipart/form-data',
        )
        data = resp.get_json()
        assert resp.status_code == 200
        assert data['status'] == 'ok'
        # トークンは job_id と同じくisalnum（パストラバーサル対策）
        assert data['token'].isalnum()
        assert len(data['token']) == 32

    def test_saved_under_instance_nglist(self, app, client):
        import io

        resp = client.post(
            '/nglist',
            data={'file': (io.BytesIO(self._xlsx_bytes()), 'ng.xlsx')},
            content_type='multipart/form-data',
        )
        token = resp.get_json()['token']
        assert os.path.exists(os.path.join(app.instance_path, 'nglist', f'{token}.xlsx'))

    def test_missing_file(self, client):
        resp = client.post('/nglist', data={}, content_type='multipart/form-data')
        assert resp.status_code == 400
        assert 'ファイルが指定されていません' in resp.get_json()['message']

    def test_rejects_non_xlsx(self, client):
        import io

        resp = client.post(
            '/nglist',
            data={'file': (io.BytesIO(b'not an excel file'), 'ng.csv')},
            content_type='multipart/form-data',
        )
        assert resp.status_code == 400
        assert 'xlsx' in resp.get_json()['message']

    def test_rejects_oversized_file(self, app):
        import io

        app.config['NG_LIST_MAX_MB'] = 1
        client = app.test_client()
        resp = client.post(
            '/nglist',
            data={'file': (io.BytesIO(b'x' * (2 * 1024 * 1024)), 'ng.xlsx')},
            content_type='multipart/form-data',
        )
        assert resp.status_code == 413
        assert '上限' in resp.get_json()['message']


class TestScrapeNgListParam:
    def test_invalid_token_rejected(self, client):
        """nglistトークンに記号が混ざる場合はエラーSSEを返す。"""
        resp = client.get('/scrape?area_id=1&nglist=../evil')
        data = resp.get_data(as_text=True)
        assert 'event: error' in data
        assert '無効なNGリスト指定' in data

    def test_valid_token_is_passed_to_service(self, app, client):
        """正しいトークンならNGリストのパスがサービスに渡る。"""
        with patch('app.main.routes.ScrapingService') as MockService:
            instance = MockService.return_value
            instance.run_scraping.return_value = iter(['event: message\ndata: ok\n\n'])

            client.get('/scrape?area_id=1&nglist=abc123').get_data(as_text=True)

        args = instance.run_scraping.call_args.args
        assert args[0] == '1'
        assert args[3] == os.path.join(app.instance_path, 'nglist', 'abc123.xlsx')

    def test_without_token_passes_none(self, client):
        """nglist未指定ならNoneが渡る（突合はスキップされる）。"""
        with patch('app.main.routes.ScrapingService') as MockService:
            instance = MockService.return_value
            instance.run_scraping.return_value = iter(['event: message\ndata: ok\n\n'])

            client.get('/scrape?area_id=1').get_data(as_text=True)

        assert instance.run_scraping.call_args.args[3] is None
