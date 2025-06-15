#!/usr/bin/env bash
# exit on error
set -o errexit

# 依存関係のインストール (Renderが自動実行する場合もあるが、明記しておくと確実)
pip install -r requirements.txt

# データベースのテーブルを作成し、area.csvから初期データを投入する
flask init-db