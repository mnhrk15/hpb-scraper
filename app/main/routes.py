import os
from flask import (
    Blueprint, render_template, current_app, request, jsonify, send_from_directory, Response
)
from . import bp
from ..db import get_db
from .services.scraping_service import ScrapingService

@bp.route('/')
def index():
    """
    トップページを表示する。
    データベースからエリア一覧を取得し、テンプレートに渡す。
    """
    db = get_db()
    areas = db.execute(
        'SELECT id, name FROM areas ORDER BY id'
    ).fetchall()
    return render_template('index.html', areas=areas)

@bp.route('/scrape')
def scrape():
    """
    スクレイピング実行リクエストを受け取り、進捗をストリーミング配信する。
    """
    area_id = request.args.get('area_id')
    # コンテキストが有効なうちに、実際のアプリケーションオブジェクトを取得
    app = current_app._get_current_object()

    if not area_id:
        def error_generator():
            yield "event: error\ndata: {\"error\": \"エリアが選択されていません。\"}\n\n"
        return Response(error_generator(), mimetype='text/event-stream')

    def stream_with_context(app_context, area_id_param):
        """ジェネレータがアプリコンテキスト内で実行されるようにするラッパー"""
        # 渡された実際のアプリオブジェクトでコンテキストを作成
        with app_context.app_context():
            service = ScrapingService()
            yield from service.run_scraping(area_id_param)

    return Response(stream_with_context(app, area_id), mimetype='text/event-stream')

@bp.route('/download/<path:filename>')
def download(filename):
    """
    生成されたExcelファイルをダウンロードさせる。
    """
    directory = os.path.join(current_app.root_path, '..', 'output')
    return send_from_directory(directory, filename, as_attachment=True) 