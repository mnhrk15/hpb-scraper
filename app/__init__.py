import os
from flask import Flask

def create_app(test_config=None):
    """
    Flaskアプリケーションインスタンスを作成し、設定を行うApplication Factory。
    """
    app = Flask(__name__, instance_relative_config=True)

    # 設定はconfig.pyから読み込む
    app.config.from_object('config')

    if test_config is not None:
        # テスト用の設定で上書きする
        app.config.from_mapping(test_config)

    # instanceフォルダが存在することを確認する
    try:
        os.makedirs(app.instance_path)
    except OSError:
        pass

    # データベース関連の初期化
    from . import db
    db.init_app(app)

    # ブループリントの登録
    from .main import routes
    app.register_blueprint(routes.bp)

    return app 