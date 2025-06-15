import sqlite3
import click
import os
import pandas as pd
from flask import current_app, g
from flask.cli import with_appcontext
from sqlalchemy import create_engine, inspect, text

# グローバルスコープでengineを保持
engine = None

def get_db():
    """
    アプリケーションコンテキスト内で唯一のDB接続を返す。
    接続が存在しない場合は新たに作成する。
    """
    if 'db' not in g:
        if engine is None:
            raise RuntimeError("Database engine not initialized. Call init_app first.")
        g.db = engine.connect()
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
    if engine is None:
        raise RuntimeError("Database engine not initialized. Call init_app first.")

    with engine.connect() as connection:
        with connection.begin(): # トランザクションを開始
            connection.execute(text('DROP TABLE IF EXISTS areas'))

            # DBの種類に応じてCREATE文を切り替える
            is_sqlite = current_app.config['DATABASE_URI'].startswith('sqlite')
            if is_sqlite:
                create_stmt = '''
                    CREATE TABLE areas (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        prefecture TEXT NOT NULL,
                        name TEXT NOT NULL,
                        url TEXT NOT NULL UNIQUE
                    )
                '''
            else: # PostgreSQL
                create_stmt = '''
                    CREATE TABLE areas (
                        id SERIAL PRIMARY KEY,
                        prefecture TEXT NOT NULL,
                        name TEXT NOT NULL,
                        url TEXT NOT NULL UNIQUE
                    )
                '''
            connection.execute(text(create_stmt))
            
            csv_path = current_app.config['AREA_CSV_PATH']
            if os.path.exists(csv_path):
                df = pd.read_csv(csv_path)
                df.to_sql('areas', connection, if_exists='append', index=False)

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
    global engine
    engine = create_engine(app.config['DATABASE_URI'])

    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command) 