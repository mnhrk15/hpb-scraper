import os
from dotenv import load_dotenv

# .envファイルを読み込む
load_dotenv()

# プロジェクトのルートディレクトリ
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# セキュリティキー (環境変数から取得)
# 環境変数が設定されていない場合は、開発用のデフォルト値を設定
SECRET_KEY = os.getenv('SECRET_KEY', 'your-default-secret-key')

# 並列処理の最大ワーカー数
MAX_WORKERS = int(os.getenv('MAX_WORKERS', 5))
# リクエスト間の待機時間 (秒)
REQUEST_WAIT_SECONDS = int(os.getenv('REQUEST_WAIT_SECONDS', 1))
# リトライ回数
RETRY_COUNT = int(os.getenv('RETRY_COUNT', 3))
# データベースファイルパス
DATABASE = os.getenv('DATABASE', 'instance/app.db')
# 初期データCSVパス
AREA_CSV_PATH = os.getenv('AREA_CSV_PATH', 'data/area.csv')
# 出力ディレクトリ
OUTPUT_DIR = os.getenv('OUTPUT_DIR', os.path.join(BASE_DIR, 'output')) 