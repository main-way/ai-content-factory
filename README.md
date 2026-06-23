# AI-Digest

Ежедневный автоматический дайджест ИИ-новостей: сбор → кластеризация → генерация → отправка.

---

## Скрипты

### fetch.py
Собирает посты из RSS-лент. Сохраняет в `storage/posts_YYYY-MM-DD.json`.

```bash
.venv/bin/python3 fetch.py
.venv/bin/python3 fetch.py --config sources.yaml
```

**Конфиг:** `sources.yaml` (101 RSS-источник). Формат JSON на выходе:
```json
{"fetched_at": "...", "sources_total": N, "posts": [...]}
```

---

### clusterize.py ⭐ Основной пайплайн
Кластеризует посты → LLM-фильтрация → генерация текстов → сохранение → отправка.

```bash
bash run-cluster.sh                        # через cron
.venv/bin/python3 clusterize.py --date 2026-06-23 --dry-run
.venv/bin/python3 clusterize.py --days 3    # окно 3 дня
```

**Пайплайн:**
```
storage/posts_*.json
  → embeddings (all-MiniLM-L6-v2, CPU, batch=8)
  → UMAP (384d → 20d)
  → HDBSCAN
  → score = log1p(size) × velocity × diversity × spread
  → топ-N:
       1. LLM coherence check
       2. LLM anti-topics filter
       3. LLM текст 1500–2500 знаков (русский, B2B)
       4. og:image / og:video
  → digest_YYYY-MM-DD.md → /srv/obsidian-base/BRIEFINGS/AI-Digest/
  → Telegram sendDocument
```

**Выход:** ТОЛЬКО в `/srv/obsidian-base/BRIEFINGS/AI-Digest/digest_YYYY-MM-DD.md`.
Директории `clusters/` и `output/` НЕ используются (мёртвый код и другие скрипты).

---

### digest.py
Читает `storage/posts_*.json`, формирует читаемый `.md` по шаблону. Использует `--ai-filter` для LLM-фильтра.

```bash
.venv/bin/python3 digest.py --date 2026-06-23 --ai-filter
```

---

### translate_digest.py
Переводит дайджест через OpenRouter API (модель `openai/gpt-4o-mini`).

```bash
OPENROUTER_API_KEY=... .venv/bin/python3 translate_digest.py digest_2026-06-23.md
```

---

### publish.py
Финальный шаг: сохраняет черновики в Obsidian, удаляет отправленные.

---

### analyze.py
Выжимка постов — краткий обзор для быстрого чтения.

---

### archive.py
Единый архив всех постов в SQLite.

---

### composer.py
Генератор постов для Telegram-каналов. Использует `channel_profiles.yaml`.

```bash
.venv/bin/python3 composer.py
```

---

### story_image_pipeline.py
Оценка и подбор иллюстраций. Vision-задачи через MiniMax-M3.

---

## Environment variables

В `~/.hermes/.env`:

```bash
MINIMAX_API_KEY=...        # MiniMax-M2: тексты дайджеста
TELEGRAM_BOT_TOKEN=...    # Telegram bot
TELEGRAM_CHAT_ID=...      # (default: 7079923530)
OPENROUTER_API_KEY=...    # перевод дайджестов
HF_TOKEN=...               # huggingface (ускоряет загрузку модели)
```

## Модели

| Задача | Модель | Скрипт |
|---|---|---|
| Тексты дайджеста | MiniMax-M2 | clusterize.py |
| Vision (картинки) | MiniMax-M3 | story_image_pipeline.py |
| Перевод | openai/gpt-4o-mini | translate_digest.py |

---

## Anti-topics

Фильтруются через LLM после кластеризации. Хардкожены в `clusterize.py` константа `ANTI_TOPICS`:

- Политические и геополитические ИИ-новости
- Академическая теория без практического применения
- Узкоспециализированные медицинские ИИ-исследования
- Сделки и IPO ИИ-компаний
- Аэрокосмические и оборонные ИИ-проекты
- GPU-бенчмарки без привязки к бизнесу

---

## Cron

```
05:00  fetch.py          → storage/posts_YYYY-MM-DD.json
05:05  run-cluster.sh   → digest_YYYY-MM-DD.md + Telegram
06:15  composer.py      → посты для каналов
```

---

## Директории

```
storage/           posts_YYYY-MM-DD.json (fetched_at, sources_total, posts)
output/            *.md от digest.py и translate_digest.py
clusters/          не используется clusterize.py
```

---

## Зависимости

```
sentence-transformers
torch
numpy
umap-learn
hdbscan
python-dateutil
pyyaml
feedparser
requests
beautifulsoup4
```

---

## Timeout

Embedding 1350 постов на CPU ≈ 90 с. UMAP ≈ 30 с. LLM (28 тем) ≈ 5–10 мин.
Рекомендуемый cron timeout: **600 с**.
