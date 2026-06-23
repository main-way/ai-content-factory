#!/bin/bash
# AI Digest — Clustering pipeline
# Cron запускает в чистом окружении — скрипт сам загружает .env
set -e

cd /home/apps_maker/ai-digest

# ─── Загружаем .env ─────────────────────────────────────────────────────────────
# set -a: все присваивания после этой команды — экспортируемые
# source ~/.hermes/.env: основной файл со всеми ключами
set -a
if [ -f "$HOME/.hermes/.env" ]; then
    source "$HOME/.hermes/.env"
elif [ -f ".env" ]; then
    source ".env"
fi
set +a

DATE=$(date +%Y-%m-%d)

.venv/bin/python3 clusterize.py \
    --date "$DATE" \
    2>&1
