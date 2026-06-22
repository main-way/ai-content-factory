#!/usr/bin/env bash
# daily.sh — ежедневный запуск AI-Digest.
#
# Использование:
#   ./daily.sh                    # за 24ч (по умолчанию), trial-режим
#   ./daily.sh 48                 # за 48ч
#   ./daily.sh 24 full            # за 24ч, полный MD-дайджест (не trial)
#   ./daily.sh 24 strict          # за 24ч, строгий AI-фильтр
#
# Для cron (каждый день в 7:00 МСК = 3:00 UTC):
#   0 3 * * * cd /home/apps_maker/ai-digest && ./daily.sh 24 >> logs/cron.log 2>&1

set -euo pipefail

cd "$(dirname "$0")"

HOURS="${1:-24}"
MODE="${2:-trial}"   # trial | full | strict
LOG_DIR="logs"
DATE="$(date -u +%Y-%m-%d)"
LOG="$LOG_DIR/daily-$DATE.log"

mkdir -p "$LOG_DIR" storage output

# Активируем venv если есть
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

echo "" | tee -a "$LOG"
echo "========================================" | tee -a "$LOG"
echo "🤖 AI-Digest daily run: $(date -u '+%Y-%m-%d %H:%M:%S UTC')" | tee -a "$LOG"
echo "   hours=$HOURS, mode=$MODE" | tee -a "$LOG"
echo "========================================" | tee -a "$LOG"

# 1. Скачиваем посты и пишем в архив
echo "" | tee -a "$LOG"
echo "📡 Step 1: fetch.py --hours $HOURS --archive" | tee -a "$LOG"
FETCH_OUT=$(python fetch.py --hours "$HOURS" --archive 2>>"$LOG" | tee -a "$LOG" | tail -1)
echo "   → результат: $FETCH_OUT" | tee -a "$LOG"

# 1.5. Скрапим полный текст для новых/свежих постов (только high+medium приоритет, чтобы не нагружать)
SCRAPE_FLAG="${SCRAPE:-high}"  # high | all | none
if [ "$SCRAPE_FLAG" != "none" ]; then
    echo "" | tee -a "$LOG"
    if [ "$SCRAPE_FLAG" = "all" ]; then
        echo "🌐 Step 1.5: scrape.py --missing (все посты без полного текста)" | tee -a "$LOG"
        python scrape.py --missing 2>>"$LOG" | tail -10 | tee -a "$LOG" >/dev/null
    else
        echo "🌐 Step 1.5: scrape.py --missing (только high-priority)" | tee -a "$LOG"
        # Скрапим только high+medium, чтобы не перегружать
        python scrape.py --missing 2>>"$LOG" | tail -10 | tee -a "$LOG" >/dev/null
    fi
fi

# 2. Генерируем дайджест
echo "" | tee -a "$LOG"
echo "📰 Step 2: digest.py --input $FETCH_OUT --$MODE" | tee -a "$LOG"
case "$MODE" in
    trial)
        python digest.py --input "$FETCH_OUT" --trial --telegram-only 2>>"$LOG" \
            | tee "output/daily_telegram_$DATE.txt" \
            | tee -a "$LOG" >/dev/null
        ;;
    full)
        python digest.py --input "$FETCH_OUT" 2>>"$LOG" \
            | tee "output/daily_telegram_$DATE.txt" >/dev/null
        ;;
    strict)
        python digest.py --input "$FETCH_OUT" --strict --telegram-only 2>>"$LOG" \
            | tee "output/daily_telegram_$DATE.txt" >/dev/null
        ;;
    *)
        echo "❌ Unknown mode: $MODE (используй trial|full|strict)" | tee -a "$LOG"
        exit 1
        ;;
esac
echo "   → Telegram-версия: output/daily_telegram_$DATE.txt ($(wc -c < output/daily_telegram_$DATE.txt 2>/dev/null || echo 0) bytes)" | tee -a "$LOG"

# 3. Статистика архива
echo "" | tee -a "$LOG"
echo "📊 Step 3: archive.py --stats (кратко)" | tee -a "$LOG"
python archive.py --stats 2>>"$LOG" | head -10 | tee -a "$LOG"

# 3.5. Перевод на русский (если есть OpenRouter ключ)
if [ -n "${OPENROUTER_API_KEY:-}" ] || python -c "import os; os.environ.get('OPENROUTER_API_KEY')" 2>/dev/null; then
    echo "" | tee -a "$LOG"
    echo "🌐 Step 3.5: translate_digest.py (RU)" | tee -a "$LOG"
    RU_OUTPUT="output/digest_$(date -u +%Y-%m-%d)_ru.md"
    python translate_digest.py \
        --input "output/digest_$(date -u +%Y-%m-%d).md" \
        --output "$RU_OUTPUT" \
        --batch 15 2>>"$LOG" | tee -a "$LOG" || true
    echo "   → RU-версия: $RU_OUTPUT" | tee -a "$LOG"

    # Отправляем русскую версию в Telegram (если есть скрипт)
    if [ -f "$HOME/.hermes/scripts/telegram-send-file.py" ]; then
        echo "" | tee -a "$LOG"
        echo "📨 Отправка RU-версии в Telegram..." | tee -a "$LOG"
        python3 "$HOME/.hermes/scripts/telegram-send-file.py" \
            "$RU_OUTPUT" \
            "📡 AI-Digest — $(date -u +%d.%m.%Y)" 2>>"$LOG" || true
    fi
fi

echo "" | tee -a "$LOG"
echo "✅ Готово. Время: $(date -u '+%H:%M:%S UTC')" | tee -a "$LOG"

# 4. Ротация старых логов (оставляем 30 дней)
find "$LOG_DIR" -name "daily-*.log" -mtime +30 -delete 2>/dev/null || true
