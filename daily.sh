#!/bin/bash
# daily.sh — ежедневный запуск AI-Digest.
#
# Использование:
#   ./daily.sh                    # за 24ч, trial (быстрый, без LLM)
#   ./daily.sh 48 full           # за 48ч, полный цикл: clusterize → translate
#   ./daily.sh 24 strict         # за 24ч, строгий AI-фильтр + clusterize
#
# trial        — digest.py (без LLM, быстро, форматирование постов)
# full/strict  — clusterize.py (LLM-пересказы 25-30 тем × 2500 знаков)
#
# Для cron (каждый день в 7:00 МСК = 3:00 UTC):
#   0 3 * * * SCRAPE=none cd /home/apps_maker/ai-digest && ./daily.sh 24 full >> logs/cron.log 2>&1

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
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

# ─── Параметры ─────────────────────────────────────────────────────────────
HOURS="${1:-24}"
MODE="${2:-trial}"   # trial | full | strict
DATE="$(date -u +%Y-%m-%d)"
LOG_DIR="logs"
LOG="$SCRIPT_DIR/$LOG_DIR/daily-$DATE.log"
SCRAPE_FLAG="${SCRAPE:-none}"  # none|all|high

mkdir -p "$LOG_DIR" storage output

echo "" | tee -a "$LOG"
echo "========================================" | tee -a "$LOG"
echo "🤖 AI-Digest daily run: $(date -u '+%Y-%m-%d %H:%M:%S UTC')" | tee -a "$LOG"
echo "   hours=$HOURS, mode=$MODE, scrape=$SCRAPE_FLAG" | tee -a "$LOG"
echo "   python=$PYTHON" | tee -a "$LOG"
echo "========================================" | tee -a "$LOG"

# ─── Step 1: fetch ──────────────────────────────────────────────────────────────
echo "" | tee -a "$LOG"
echo "📡 Step 1: fetch.py --hours $HOURS --archive" | tee -a "$LOG"
FETCH_OUT=$("$PYTHON" fetch.py --hours "$HOURS" --archive 2>>"$LOG" | tail -1)
echo "   → $FETCH_OUT" | tee -a "$LOG"

# ─── Step 1.5: scrape (опционально) ─────────────────────────────────────────
if [ "$SCRAPE_FLAG" != "none" ]; then
    echo "" | tee -a "$LOG"
    case "$SCRAPE_FLAG" in
        all)  echo "🌐 Step 1.5: scrape.py --all" | tee -a "$LOG"
              "$PYTHON" scrape.py --all 2>>"$LOG" | tail -5 | tee -a "$LOG" ;;
        high) echo "🌐 Step 1.5: scrape.py --missing" | tee -a "$LOG"
              "$PYTHON" scrape.py --missing 2>>"$LOG" | tail -5 | tee -a "$LOG" ;;
    esac
else
    echo "⏭ Step 1.5: scrape пропущен (SCRAPE=none)" | tee -a "$LOG"
fi

# ─── Step 2: digest ───────────────────────────────────────────────────────────
echo "" | tee -a "$LOG"

case "$MODE" in
    trial)
        # Быстрый режим — без LLM, только форматирование
        echo "📰 Step 2: digest.py (trial, без LLM)" | tee -a "$LOG"
        "$PYTHON" digest.py --input "$FETCH_OUT" --trial --telegram-only 2>>"$LOG" \
            | tee "$SCRIPT_DIR/output/daily_telegram_$DATE.txt" \
            | tee -a "$LOG" >/dev/null
        echo "   → output/daily_telegram_$DATE.txt ($(wc -c < "$SCRIPT_DIR/output/daily_telegram_$DATE.txt" 2>/dev/null || echo 0) bytes)" | tee -a "$LOG"
        ;;

    full|strict)
        # Полный режим — clusterize.py: embeddings → UMAP → HDBSCAN → LLM-пересказы
        # 25-30 тем × 1500-2500 знаков каждый
        echo "📰 Step 2: clusterize.py --date $DATE (LLM-пересказы, до ~40 мин)" | tee -a "$LOG"
        echo "   ⏳ Ожидание завершения..." | tee -a "$LOG"

        if "$PYTHON" clusterize.py --date "$DATE" 2>>"$LOG"; then
            echo "   ✅ clusterize.py завершён" | tee -a "$LOG"
        else
            echo "   ❌ clusterize.py завершился с ошибкой" | tee -a "$LOG"
            exit 1
        fi

        CLUSTERIZED="$SCRIPT_DIR/output/digest_${DATE}_clusterized.md"
        if [ -f "$CLUSTERIZED" ]; then
            echo "   → $CLUSTERIZED ($(wc -c < "$CLUSTERIZED" 2>/dev/null || echo 0) bytes)" | tee -a "$LOG"
        fi
        ;;

    *)
        echo "❌ Unknown mode: $MODE (trial|full|strict)" | tee -a "$LOG"
        exit 1
        ;;
esac

# ─── Step 3: archive stats ────────────────────────────────────────────────────
echo "" | tee -a "$LOG"
echo "📊 Step 3: archive.py --stats" | tee -a "$LOG"
"$PYTHON" archive.py --stats 2>>"$LOG" | head -12 | tee -a "$LOG"

# ─── Step 3.5: translate (full/strict, если есть OpenRouter) ─────────────────
if [ "$MODE" != "trial" ]; then
    HAS_KEY=$("$PYTHON" -c "import os; print('yes' if os.environ.get('OPENROUTER_API_KEY') else 'no')" 2>/dev/null)
    if [ "$HAS_KEY" = "yes" ]; then
        echo "" | tee -a "$LOG"
        echo "🌐 Step 3.5: translate_digest.py (RU)" | tee -a "$LOG"

        CLUSTERIZED="$SCRIPT_DIR/output/digest_${DATE}_clusterized.md"
        RU_OUTPUT="$SCRIPT_DIR/output/digest_${DATE}_ru.md"

        if [ -f "$CLUSTERIZED" ]; then
            "$PYTHON" translate_digest.py \
                "$CLUSTERIZED" \
                --output "$RU_OUTPUT" \
                --batch 15 2>>"$LOG" | tee -a "$LOG" || true
            echo "   → $RU_OUTPUT ($(wc -c < "$RU_OUTPUT" 2>/dev/null || echo 0) bytes)" | tee -a "$LOG"

            # Отправляем RU в Telegram
            if [ -f "$HOME/.hermes/scripts/telegram-send-file.py" ]; then
                echo "" | tee -a "$LOG"
                echo "📨 Отправка RU-версии в Telegram..." | tee -a "$LOG"
                python3 "$HOME/.hermes/scripts/telegram-send-file.py" \
                    "$RU_OUTPUT" \
                    "📡 AI-Digest — $(date -u +%d.%m.%Y)" 2>>"$LOG" || true
            fi
        else
            echo "   ⚠️  $CLUSTERIZED не найден" | tee -a "$LOG"
        fi
    fi
fi

echo "" | tee -a "$LOG"
echo "✅ Готово. Время: $(date -u '+%H:%M:%S UTC')" | tee -a "$LOG"

# ─── Ротация логов (30 дней) ──────────────────────────────────────────────────
find "$LOG_DIR" -name "daily-*.log" -mtime +30 -delete 2>/dev/null || true
