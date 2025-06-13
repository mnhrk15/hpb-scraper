import os
from flask import Flask

def create_app(test_config=None):
    """
    Flaskアプリケーションインスタンスを作成し、設定を行うApplication Factory。
    """
    app = Flask(__name__, instance_relative_config=True)

    # アプリケーションのデフォルト設定
    app.config.from_mapping(
        SECRET_KEY='dev', # 本番環境ではランダムな値に上書きする
        DATABASE=os.path.join(app.instance_path, 'app.db'),
    )

    if test_config is None:
        # インスタンスフォルダに存在するconfig.pyから設定を読み込む -> ルートのconfig.pyを読み込むように変更
        app.config.from_object('config')
    else:
        # テスト用の設定を読み込む
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