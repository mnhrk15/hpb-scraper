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
