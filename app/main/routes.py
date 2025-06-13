import os
import uuid
from flask import (
    Blueprint, render_template, current_app, request, jsonify, send_from_directory, Response
)
from sqlalchemy import text
from . import bp
from ..db import get_db
from .services.scraping_service import ScrapingService

@bp.route('/')
def index():
    """
    トップページを表示する。
    データベースからエリア一覧を取得し、都道府県順でソートしてテンプレートに渡す。
    """
    db = get_db()
    areas_query = db.execute(
        text('SELECT id, name, prefecture FROM areas')
    ).mappings().all()

    # 47都道府県の地理的順序リスト (JIS X 0401準拠)
    prefecture_order = [
        '北海道', '青森県', '岩手県', '宮城県', '秋田県', '山形県', '福島県',
        '茨城県', '栃木県', '群馬県', '埼玉県', '千葉県', '東京都', '神奈川県',
        '新潟県', '富山県', '石川県', '福井県', '山梨県', '長野県', '岐阜県',
        '静岡県', '愛知県', '三重県', '滋賀県', '京都府', '大阪府', '兵庫県',
        '奈良県', '和歌山県', '鳥取県', '島根県', '岡山県', '広島県', '山口県',
        '徳島県', '香川県', '愛媛県', '高知県', '福岡県', '佐賀県', '長崎県',
        '熊本県', '大分県', '宮崎県', '鹿児島県', '沖縄県'
    ]
    prefecture_map = {name: i for i, name in enumerate(prefecture_order)}

    # 取得したデータをリストに変換
    areas_list = [{'id': area['id'], 'name': area['name'], 'prefecture': area['prefecture']} for area in areas_query]

    # 県の順序を第一キー、エリア名を第二キーとしてソート
    areas_sorted = sorted(areas_list, key=lambda x: (prefecture_map.get(x['prefecture'], 99), x['name']))

    return render_template('index.html', areas=areas_sorted)

@bp.route('/scrape')
def scrape():
    """
    スクレイピング実行リクエストを受け取り、進捗をストリーミング配信する。
    """
    area_id = request.args.get('area_id')
    app = current_app._get_current_object()
    job_id = uuid.uuid4().hex

    if not area_id:
        def error_generator():
            yield "event: error\ndata: {\"error\": \"エリアが選択されていません。\"}\n\n"
        return Response(error_generator(), mimetype='text/event-stream')

    def stream_with_context(app_context, area_id_param, job_id_param):
        """ジェネレータがアプリコンテキスト内で実行されるようにするラッパー"""
        cancel_file = os.path.join(app_context.instance_path, f"{job_id_param}.cancel")
        try:
            with app_context.app_context():
                yield f"event: job_id\ndata: {job_id_param}\n\n"
                
                service = ScrapingService()
                yield from service.run_scraping(area_id_param, job_id_param)
        finally:
            # ジョブ完了後、キャンセルシグナルファイルを削除
            if os.path.exists(cancel_file):
                try:
                    os.remove(cancel_file)
                except OSError as e:
                    app_context.logger.error(f"Error removing cancel file {cancel_file}: {e}")

    return Response(stream_with_context(app, area_id, job_id), mimetype='text/event-stream')

@bp.route('/scrape/cancel', methods=['POST'])
def scrape_cancel():
    """
    キャンセルシグナルファイルを作成することで、処理の中断をリクエストする。
    """
    data = request.get_json()
    job_id = data.get('job_id')

    if not (job_id and isinstance(job_id, str) and job_id.isalnum()):
        return jsonify({'status': 'error', 'message': 'Invalid job ID'}), 400

    try:
        cancel_file_path = os.path.join(current_app.instance_path, f"{job_id}.cancel")
        # 空のファイルを作成してシグナルとする
        with open(cancel_file_path, 'w') as f:
            pass
        current_app.logger.info(f"Cancellation signal created for job: {job_id}")
        return jsonify({'status': 'cancellation_requested'})
    except IOError as e:
        current_app.logger.error(f"Error creating cancel file for job {job_id}: {e}")
        return jsonify({'status': 'error', 'message': 'Failed to signal cancellation'}), 500

@bp.route('/download/<path:filename>')
def download(filename):
    """
    生成されたExcelファイルをダウンロードさせる。
    """
    directory = os.path.join(current_app.root_path, '..', current_app.config['OUTPUT_DIR'])
    return send_from_directory(directory, filename, as_attachment=True) 