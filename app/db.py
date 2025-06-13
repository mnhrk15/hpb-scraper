import sqlite3
import click
import os
import pandas as pd
from flask import current_app, g
from flask.cli import with_appcontext

def get_db():
    """
    アプリケーションコンテキスト内で唯一のDB接続を返す。
    接続が存在しない場合は新たに作成する。
    """
    if 'db' not in g:
        g.db = sqlite3.connect(
            current_app.config['DATABASE'],
            detect_types=sqlite3.PARSE_DECLTYPES
        )
        g.db.row_factory = sqlite3.Row
    return g.db

def close_db(e=None):
    """
    コンテキスト終了時にDB接続を閉じる。
    """
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    """
    既存のテーブルを削除し、新しいテーブルを作成して初期データを投入する。
    """
    db = get_db()
    
    db.execute('DROP TABLE IF EXISTS areas')
    db.execute('''
        CREATE TABLE areas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prefecture TEXT NOT NULL,
            name TEXT NOT NULL,
            url TEXT NOT NULL UNIQUE
        )
    ''')
    
    csv_path = current_app.config['AREA_CSV_PATH']
    if os.path.exists(csv_path):
        df = pd.read_csv(csv_path)
        df.to_sql('areas', db, if_exists='append', index=False)
    
    db.commit()

@click.command('init-db')
@with_appcontext
def init_db_command():
    """DBをクリアして初期化するCLIコマンド。"""
    init_db()
    click.echo('Initialized the database.')

def init_app(app):
    """
    FlaskアプリケーションインスタンスにDB関連の機能を登録する。
    """
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command) 