#!/bin/bash
# Одноразовый запуск daily.sh — после выполнения удаляет себя из crontab
set -euo pipefail

SCRIPT_DIR="/home/apps_maker/ai-digest"
cd "$SCRIPT_DIR"

# ─── .env ───────────────────────────────────────────────────────────────────
set -a
if [ -f "$HOME/.hermes/.env" ]; then
    source "$HOME/.hermes/.env"
elif [ -f ".env" ]; then
    source ".env"
fi
set +a

# ─── venv python ────────────────────────────────────────────────────────────
if [ -f ".venv/bin/python" ]; then
    PYTHON="$SCRIPT_DIR/.venv/bin/python"
else
    PYTHON="python3"
fi

DATE="$(date +%Y-%m-%d)"
LOG="$SCRIPT_DIR/logs/run-once-$DATE.log"
mkdir -p logs

echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] run-once.sh: starting" >> "$LOG"
echo "   python=$PYTHON" >> "$LOG"

SCRAPE=none "$SCRIPT_DIR/daily.sh" 24 full >> "$LOG" 2>&1

echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] run-once.sh: done" >> "$LOG"

# Удаляем себя из crontab
TMP_CRON=$(mktemp)
crontab -l 2>/dev/null | grep -v "run-once.sh" > "$TMP_CRON" || true
crontab "$TMP_CRON" 2>/dev/null
rm -f "$TMP_CRON"
echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] run-once.sh: removed from crontab" >> "$LOG"
