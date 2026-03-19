import os
import tempfile
import shutil

import pytest
import pandas as pd

from app import create_app


@pytest.fixture
def app(tmp_path):
    """テスト用Flaskアプリケーションを作成する。"""
    output_dir = str(tmp_path / 'output')
    os.makedirs(output_dir, exist_ok=True)

    # SQLiteのインメモリDBを使用
    app = create_app(test_config={
        'TESTING': True,
        'DATABASE_URI': 'sqlite://',
        'SERPER_API_KEY': 'test-api-key',
        'INSTAGRAM_MAX_URLS': 3,
        'RETRY_COUNT': 3,
        'REQUEST_WAIT_SECONDS': 0,
        'CANCEL_FILE_TIMEOUT_SECONDS': 3600,
        'OUTPUT_DIR': output_dir,
    })

    yield app


@pytest.fixture
def client(app):
    """テスト用HTTPクライアントを返す。"""
    return app.test_client()


@pytest.fixture
def app_context(app):
    """アプリケーションコンテキストを提供する。"""
    with app.app_context():
        yield app


@pytest.fixture
def sample_excel(app):
    """テスト用のサロンリストExcelファイルを作成する。"""
    output_dir = app.config['OUTPUT_DIR']
    file_name = '五所川原_20260319_153045.xlsx'
    file_path = os.path.join(output_dir, file_name)

    df = pd.DataFrame({
        'サロン名': ['サロンA', 'サロンB', 'サロンC'],
        '電話番号': ['012-345-6789', '098-765-4321', '011-222-3333'],
        '住所': ['青森県五所川原市1-1', '青森県五所川原市2-2', '青森県五所川原市3-3'],
        'サロンURL': [
            'https://beauty.hotpepper.jp/slnH000000001/',
            'https://beauty.hotpepper.jp/slnH000000002/',
            'https://beauty.hotpepper.jp/slnH000000003/',
        ],
    })
    df.to_excel(file_path, index=False, sheet_name='サロンリスト')
    return file_name


@pytest.fixture
def duplicate_salon_excel(app):
    """同名サロンを含むテスト用Excelファイルを作成する。"""
    output_dir = app.config['OUTPUT_DIR']
    file_name = '五所川原_20260319_160000.xlsx'
    file_path = os.path.join(output_dir, file_name)

    df = pd.DataFrame({
        'サロン名': ['チェーン店A', 'チェーン店A', 'サロンB'],
        '電話番号': ['012-111-1111', '012-222-2222', '012-333-3333'],
        '住所': ['住所1', '住所2', '住所3'],
        'サロンURL': [
            'https://beauty.hotpepper.jp/slnH000000010/',
            'https://beauty.hotpepper.jp/slnH000000011/',
            'https://beauty.hotpepper.jp/slnH000000012/',
        ],
    })
    df.to_excel(file_path, index=False, sheet_name='サロンリスト')
    return file_name
