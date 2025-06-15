import os
import time
import glob
from flask import Flask

def _cleanup_stale_cancel_files(app):
    """
    アプリケーション起動時に、古くなったキャンセルシグナルファイルを削除する。
    """
    try:
        instance_path = app.instance_path
        if not os.path.isdir(instance_path):
            return

        lifetime = app.config.get('STALE_CANCEL_FILE_LIFETIME_SECONDS', 86400)
        current_time = time.time()

        for f in glob.glob(os.path.join(instance_path, '*.cancel')):
            try:
                file_mtime = os.path.getmtime(f)
                if (current_time - file_mtime) > lifetime:
                    os.remove(f)
                    app.logger.info(f"Removed stale cancel file: {os.path.basename(f)}")
            except (OSError, ValueError) as e:
                # ファイルの読み取り/削除エラーはログに記録するが、起動は妨げない
                app.logger.warning(f"Error processing stale cancel file {f}: {e}")
    except Exception as e:
        app.logger.error(f"Failed to run cancel file cleanup: {e}")


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

    # アプリケーションコンテキスト内で起動時処理を実行
    with app.app_context():
        # 古いキャンセルファイルをクリーンアップ
        _cleanup_stale_cancel_files(app)

    return app 