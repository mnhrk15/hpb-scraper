# HotPepper Beauty Scraper

## 概要

このアプリケーションは、[HotPepper Beauty](https://beauty.hotpepper.jp/) から指定されたエリアのサロン情報をスクレイピングし、結果をExcelファイルとしてダウンロードできるウェブアプリケーションです。

リアルタイムの進捗表示機能を備えており、時間のかかるスクレイピング処理の状況をユーザーが把握しながら実行できます。

## 主な機能

- **エリア指定スクレイピング**: 指定したエリアのサロン一覧ページから情報を収集します。
- **詳細情報取得**: 各サロンの詳細ページを並列でクロールし、以下の情報を取得します。
    - サロン名
    - 電話番号
    - 住所
    - スタッフ数
    - 関連リンク
- **Instagram URL検索**: スクレイピング結果のサロン名をもとにGoogle検索（Serper API）を行い、各サロンのInstagram候補URLを自動取得します。
- **リアルタイム進捗表示**: Server-Sent Events (SSE) を利用し、処理状況をリアルタイムでフロントエンドに通知します。
- **Excelファイル出力**: 収集したデータを整形し、Excelファイル (`.xlsx`) としてダウンロードできます。

## 技術スタック

### バックエンド
- Python 3.x
- Flask: Webフレームワーク
- Requests: HTTP通信
- BeautifulSoup4: HTMLパーサー
- Pandas: データ処理
- OpenPyXL: Excelファイル書き込み
- Gunicorn: WSGIサーバー

### フロントエンド
- HTML5
- CSS3 (Vanilla)
- JavaScript (Vanilla)
- Server-Sent Events (SSE)

## セットアップと実行方法

### 1. リポジトリのクローン

```bash
git clone https://github.com/your-username/hpb-scraper.git
cd hpb-scraper
```

### 2. Python仮想環境のセットアップ

```bash
# Python 3 の仮想環境を作成
python3 -m venv .venv

# 仮想環境を有効化 (macOS/Linux)
source .venv/bin/activate

# (Windowsの場合)
# .venv\Scripts\activate
```

### 3. 依存関係のインストール

```bash
pip install -r requirements.txt
```

### 4. 環境変数の設定

アプリケーションの設定は環境変数で行います。プロジェクトのルートディレクトリに `.env` ファイルを作成し、以下の内容を記述してください。

```bash
# .env.example をコピーして .env ファイルを作成
cp .env.example .env
```

次に、`.env` ファイルを編集して、`SECRET_KEY` に強力なキーを設定します。

**`.env`**
```ini
# ... 他の設定 ...

# セキュリティキー (本番環境では必ず変更してください)
# 以下のコマンドで生成できます: python -c 'import secrets; print(secrets.token_hex(24))'
SECRET_KEY='ここに生成した強力なキーを設定'

# ... 他の設定 ...
```

`.env.example` ファイルにすべての設定可能な変数の説明がありますので、必要に応じてカスタマイズしてください。

#### Instagram検索機能を使用する場合

Instagram URL検索機能を利用するには、[Serper.dev](https://serper.dev/) のAPIキーが必要です。

1. https://serper.dev/ でアカウントを作成
2. ダッシュボードからAPIキーを取得
3. `.env` ファイルに設定:

```ini
SERPER_API_KEY='取得したAPIキー'
```

APIキーが未設定の場合、Instagram検索ボタンは表示されません。

### 5. データベースの初期化

以下のコマンドを実行して、データベーステーブルを作成し、スクレイピング対象エリアの初期データを投入します。

```bash
flask init-db
```
これにより、`data/area.csv` の内容がデータベースに登録されます。

### 6. アプリケーションの起動

#### 開発環境

開発用のサーバーで手軽に起動できます。

```bash
flask run
```

#### 本番環境 (推奨)

Gunicorn を使用して起動します。

```bash
gunicorn --workers 4 --bind 0.0.0.0:8000 wsgi:app
```

アプリケーションが起動したら、ブラウザで `http://127.0.0.1:5000` (または `http://0.0.0.0:8000`) にアクセスしてください。

## 使い方

### サロン情報のスクレイピング

1.  ブラウザでアプリケーションにアクセスします。
2.  「エリア選択」ドロップダウンから、情報を収集したいエリアを選択します。
3.  「スクレイピング実行」ボタンをクリックします。
4.  処理状況がリアルタイムで表示されます。
5.  処理が完了すると、結果エリアにダウンロードリンクが表示されます。
6.  リンクをクリックして、収集されたデータが格納されたExcelファイルをダウンロードします。

### Instagram URL検索

スクレイピング完了後、Instagram検索機能が利用可能です（`SERPER_API_KEY` の設定が必要）。

1.  スクレイピング完了後の結果カードに「Instagram検索を実行」ボタンが表示されます。
2.  ボタンをクリックすると、各サロン名でGoogle検索を行い、Instagram URLを収集します。
3.  進捗がリアルタイムで表示されます。
4.  検索完了後、サロン情報とInstagram候補URLを含むExcelファイルをダウンロードできます。

出力されるExcelファイル（`Instagram_{エリア名}_{タイムスタンプ}.xlsx`）には、元のサロン情報に加えて最大3件のInstagram候補URLが含まれます。

## 設定

主要な設定は `.env` ファイルで変更できます。詳細は `.env.example` を参照してください。

- `SECRET_KEY`: Flaskのセッション暗号化キー。
- `MAX_WORKERS`: スクレイピング時の並列実行数（スレッド数）。
- `REQUEST_WAIT_SECONDS`: 各HTTPリクエスト間の待機時間（秒）。サーバーへの負荷を軽減します。
- `RETRY_COUNT`: HTTPリクエスト失敗時のリトライ回数。
- `SERPER_API_KEY`: Serper.dev APIキー。Instagram URL検索機能に必要。
- `INSTAGRAM_MAX_URLS`: サロンあたりのInstagram候補URL上限数（デフォルト: 3）。

## 注意事項

- **法的・倫理的リスク**: ウェブサイトのスクレイピングは、サイトの利用規約に違反する可能性があります。また、過度なアクセスは相手方サーバーに大きな負荷をかける行為となります。このツールを使用する際は、対象サイトの利用規約を遵守し、常識的な範囲で自己責任において実行してください。
- **仕様変更への対応**: スクレイピング対象サイトのHTML構造が変更されると、このツールは正常に動作しなくなる可能性があります。その場合は `selectors.json` に定義されたCSSセレクタを更新する必要があります。

## ライセンス

[MIT License](LICENSE) 