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

# データベースURI (RenderのDATABASE_URLを優先し、なければローカルのSQLiteを使用)
DATABASE_URI = os.getenv('DATABASE_URL')
if DATABASE_URI and DATABASE_URI.startswith("postgres://"):
    DATABASE_URI = DATABASE_URI.replace("postgres://", "postgresql://", 1)

if not DATABASE_URI:
    DATABASE_URI = f"sqlite:///{os.path.join(BASE_DIR, 'instance', 'app.db')}"

# 初期データCSVパス
AREA_CSV_PATH = os.getenv('AREA_CSV_PATH', 'data/area.csv')
# 出力ディレクトリ
OUTPUT_DIR = os.getenv('OUTPUT_DIR', 'output') 