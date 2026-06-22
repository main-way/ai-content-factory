# AI Content Factory — Контент-завод

Автоматизированный pipeline новостей ИИ: собирает RSS-ленты, кластеризует похожие посты, оценивает по релевантности и генерирует 25–30 готовых инфоповодов для каналов, рассылок и соцсетей.

**Целевая аудитория:** B2B — производственный сектор. Вывод на русском языке.

## Архитектура

```
RSS-ленты (200+)
    ↓
fetch.py          — сбор и дедупликация → storage/posts_YYYY-MM-DD.json
    ↓
clusterize.py     — эмбеддинги → HDBSCAN → оценка → топ-кластеры
    ↓
gen_digest.py     — генерация 25-30 новостей (1500-2500 знаков)
    ↓
output/*.md       — готовый к публикации дайджест
```

## Быстрый старт

```bash
# 1. Установить зависимости
uv venv .venv
source .venv/bin/activate
uv pip install -r requirements.txt

# 2. Настроить переменные окружения
cp .env.example .env
# Заполнить .env своими ключами API

# 3. Собрать новости за сегодня
python3 fetch.py --date 2026-06-22

# 4. Кластеризовать и оценить
python3 clusterize.py --date 2026-06-22 --top-n 15

# 5. Сгенерировать дайджест (25-30 тем)
python3 gen_digest.py --date 2026-06-22 --count 28

# Результат: output/digest_28_YYYY-MM-DD.md
```

## Cron-задачи

```bash
# Сбор RSS — ежедневно в 04:00 МСК
0 4 * * * cd /home/apps_maker/ai-digest && .venv/bin/python fetch.py

# Кластеризация — ежедневно в 05:00 МСК
0 5 * * * cd /home/apps_maker/ai-digest && bash run-cluster.sh

# Генерация — ежедневно в 06:00 МСК
0 6 * * * cd /home/apps_maker/ai-digest && .venv/bin/python gen_digest.py
```

## Формула оценки

```
score = log(1 + cluster_size) × velocity × diversity × spread
```

- **cluster_size** — число постов в кластере
- **velocity** — постов в час за последние 6 часов
- **diversity** — уникальных источников в кластере (нужно ≥2)
- **spread** — разнообразие URL по источникам

## Формат вывода

Каждый инфоповод:
- 1500–2500 знаков (на русском)
- Заголовок с указанием источника
- Контекст + почему это важно
- Две ссылки: первоисточник + ссылка на локальную базу
- Без ИТ-жаргона, без рекламы, только факты

## Хранение данных

- Посты: `storage/posts_YYYY-MM-DD.json`
- Кластеры: `clusters/clusters_YYYY-MM-DD.json`
- Результат: `output/digest_*.md`
- Ежедневная очистка: хранятся только файлы за сегодня (без долгосрочного хранения)

## Зависимости

- Python 3.11+
- uv (менеджер пакетов)
- SQLite3
- MiniMax API ключ (или любой OpenAI-совместимый API)
