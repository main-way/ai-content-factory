#!/bin/bash
# AI Digest — Clustering pipeline
# Cron запускает в чистом окружении — скрипт сам загружает .env
set -a
source ~/.hermes/.env 2>/dev/null || true
set +a

cd /home/apps_maker/ai-digest

WATCHDOG=115
DATE=$(date +%Y-%m-%d)

# Start cluster job in background
/opt/hermes/.venv/bin/python3 clusterize.py \
    --date "$DATE" \
    --embed-limit 250 \
    2>&1 &
PID=$!

# Watchdog loop — kills if still running after WATCHDOG seconds
elapsed=0
while kill -0 $PID 2>/dev/null; do
    sleep 5
    elapsed=$((elapsed + 5))
    if [ $elapsed -ge $WATCHDOG ]; then
        echo "[WATCHDOG] Killed after ${elapsed}s" >&2
        kill -9 $PID 2>/dev/null || true
        wait $PID 2>/dev/null || true
        exit 1
    fi
done

# Process exited on its own — collect exit code
wait $PID 2>/dev/null || true
