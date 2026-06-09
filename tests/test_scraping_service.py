import re
from unittest.mock import patch, MagicMock

import pandas as pd

from app.main.services.scraping_service import ScrapingService

# 「髪質改善」のURLエンコード結果
KAMI = '%E9%AB%AA%E8%B3%AA%E6%94%B9%E5%96%84'


class TestBuildFreewordUrl:
    def test_appends_freeword(self, app_context):
        """エリアURLにfreewordクエリを付与し、日本語が%XXエンコードされる。"""
        service = ScrapingService()
        url = service._build_freeword_url(
            'https://beauty.hotpepper.jp/svcSA/macJR/salon/', '髪質改善'
        )
        assert url == f'https://beauty.hotpepper.jp/svcSA/macJR/salon/?freeword={KAMI}'

    def test_none_or_empty_unchanged(self, app_context):
        """freewordがNone/空文字ならbase_urlをそのまま返す（後方互換）。"""
        service = ScrapingService()
        base = 'https://beauty.hotpepper.jp/svcSA/macJR/salon/'
        assert service._build_freeword_url(base, None) == base
        assert service._build_freeword_url(base, '') == base

    def test_preserves_existing_query(self, app_context):
        """既存クエリを保持しつつfreewordを追加する。"""
        service = ScrapingService()
        url = service._build_freeword_url(
            'https://beauty.hotpepper.jp/svcSA/macJR/salon/?searchGender=ALL', '髪質改善'
        )
        assert 'searchGender=ALL' in url
        assert f'freeword={KAMI}' in url


class TestPageUrlConstruction:
    def test_freeword_pagination_url(self, app_context):
        """freeword指定時、page2以降が .../salon/PN{N}.html?freeword=... の正しい形になる。"""
        service = ScrapingService()
        area_url = f'https://beauty.hotpepper.jp/svcSA/macJR/salon/?freeword={KAMI}'
        with patch.object(service, '_get_salon_urls_from_page', return_value=set()) as mock_page:
            list(service._get_all_salon_urls(area_url, 3, 'job123', '髪質改善'))

        called = sorted(c.args[0] for c in mock_page.call_args_list)
        assert called == [
            f'https://beauty.hotpepper.jp/svcSA/macJR/salon/?freeword={KAMI}',
            f'https://beauty.hotpepper.jp/svcSA/macJR/salon/PN2.html?freeword={KAMI}',
            f'https://beauty.hotpepper.jp/svcSA/macJR/salon/PN3.html?freeword={KAMI}',
        ]
        # 壊れたURL（クエリのあとにパスが続く形）が生成されていないこと
        for url in called:
            assert f'{KAMI}/PN' not in url

    def test_no_freeword_backward_compat(self, app_context):
        """freeword未指定時は従来通りの {base}/PN{N}.html を生成する。"""
        service = ScrapingService()
        area_url = 'https://beauty.hotpepper.jp/svcSG/macGE/salon/'
        with patch.object(service, '_get_salon_urls_from_page', return_value=set()) as mock_page:
            list(service._get_all_salon_urls(area_url, 2, 'job123', None))

        called = sorted(c.args[0] for c in mock_page.call_args_list)
        assert called == [
            'https://beauty.hotpepper.jp/svcSG/macGE/salon/',
            'https://beauty.hotpepper.jp/svcSG/macGE/salon/PN2.html',
        ]


class TestGetTotalPagesRedirect:
    def _response(self, url, text='<html><body>no pagination</body></html>'):
        fake = MagicMock()
        fake.url = url
        fake.text = text
        return fake

    def test_freeword_reapplied_when_redirect_drops_query(self, app_context):
        """リダイレクトでfinal_urlからfreewordが落ちても再付与される。"""
        service = ScrapingService()
        # response.url がクエリ無し（=リダイレクトでfreewordが消えた状態）
        fake = self._response('https://beauty.hotpepper.jp/svcSA/macJR/salon/')
        with patch.object(service, '_make_request', return_value=fake):
            total, final = service._get_total_pages(
                f'https://beauty.hotpepper.jp/svcSA/macJR/salon/?freeword={KAMI}',
                'job', '髪質改善',
            )
        assert total == 1  # pagination要素が無いので1ページ
        assert f'freeword={KAMI}' in final

    def test_freeword_preserves_other_query_params(self, app_context):
        """final_urlの他クエリ(searchGender等)を保持しつつfreewordを補う。"""
        service = ScrapingService()
        fake = self._response(
            'https://beauty.hotpepper.jp/svcSA/macJR/salon/?searchGender=ALL'
        )
        with patch.object(service, '_make_request', return_value=fake):
            _, final = service._get_total_pages(
                f'https://beauty.hotpepper.jp/svcSA/macJR/salon/?freeword={KAMI}',
                'job', '髪質改善',
            )
        assert 'searchGender=ALL' in final
        assert f'freeword={KAMI}' in final

    def test_no_freeword_final_url_unchanged(self, app_context):
        """freeword未指定時はfinal_urlを変更しない（後方互換）。"""
        service = ScrapingService()
        url = 'https://beauty.hotpepper.jp/svcSG/macGE/salon/'
        fake = self._response(url)
        with patch.object(service, '_make_request', return_value=fake):
            _, final = service._get_total_pages(url, 'job', None)
        assert final == url


class TestExcelFilename:
    def _df(self):
        return pd.DataFrame({'サロン名': ['サロンA']})

    def test_target_filename_with_freeword(self, app_context):
        """freeword指定時、ファイル名に {area}_{freeword}_ が含まれる。"""
        service = ScrapingService()
        name = service._create_target_excel_file(self._df(), '青山・表参道・原宿', '髪質改善')
        assert name.startswith('青山・表参道・原宿_髪質改善_')
        assert name.endswith('.xlsx')

    def test_excluded_filename_with_freeword(self, app_context):
        """除外リストも 除外リスト_{area}_{freeword}_ の形になる。"""
        service = ScrapingService()
        df = pd.DataFrame({'サロン名': ['A'], 'exclusion_reason': ['EPRP']})
        name = service._create_excluded_excel_file(df, 'エリア', '髪質改善')
        assert name.startswith('除外リスト_エリア_髪質改善_')

    def test_filename_sanitizes_freeword(self, app_context):
        """freeword中のファイル名禁止文字が除去される。"""
        service = ScrapingService()
        name = service._create_target_excel_file(self._df(), 'エリア', '髪/質:改善')
        assert '/' not in name and ':' not in name
        assert name.startswith('エリア_髪質改善_')

    def test_symbol_only_freeword_falls_back(self, app_context):
        """サニタイズ後に空になるfreewordはfreewordなしのファイル名にフォールバックする。"""
        service = ScrapingService()
        name = service._create_target_excel_file(self._df(), 'エリア', '///')
        assert re.match(r'^エリア_\d{8}_\d{6}\.xlsx$', name)

    def test_no_freeword_filename_unchanged(self, app_context):
        """freeword未指定時は従来通り {area}_{timestamp}.xlsx（後方互換）。"""
        service = ScrapingService()
        name = service._create_target_excel_file(self._df(), 'エリア', None)
        assert re.match(r'^エリア_\d{8}_\d{6}\.xlsx$', name)
