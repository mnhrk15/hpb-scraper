from gevent import monkey
# 必ず他のimportよりも先にモンキーパッチを適用する
monkey.patch_all()

from app import create_app

app = create_app()