#!/usr/bin/env python3
"""
xpoz_fetch.py — скачивание постов из Reddit через Xpoz API.

Использование:
    python xpoz_fetch.py                    # сегодняшний день
    python xpoz_fetch.py --hours 48         # за 2 дня
    python xpoz_fetch.py --output custom.json
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from xpoz import AsyncXpozClient

API_KEY = os.getenv("XPOZ_API_KEY")
if not API_KEY:
    print("❌ XPOZ_API_KEY не найден в переменных окружения", file=sys.stderr)
    sys.exit(1)

# Сабреддиты по категориям
SUBREDDITS = {
    "ai_research": [
        "MachineLearning",
        "LocalLLaMA",
        "ClaudeAI",
        "learnmachinelearning",
        "MLQuestions",
        "LanguageTechnology",
    ],
    "ai_general": [
        "artificial",
    ],
}

# Ключевые слова для поиска (AI/LLM тематика)
QUERY = "LLM GPT artificial intelligence model neural network"

# Какие поля запрашивать у Xpoz
FIELDS = ["id", "title", "url", "score", "created_at_date", "subreddit_name", "selftext", "comments_count"]

# Лимит постов на сабреддит
LIMIT_PER_SUB = 10

# Фильтр по времени — ограничиваем посты по возрасту
# time: "day", "week", "month", "year", "all"
TIME_FILTER = "week"


async def fetch_subreddit(client: AsyncXpozClient, subreddit: str, category: str) -> list[dict]:
    """Получает посты из одного сабреддита."""
    try:
        result = await client.reddit.search_posts(
            query=QUERY,
            subreddit=subreddit,
            fields=FIELDS,
            limit=LIMIT_PER_SUB,
            time=TIME_FILTER,  # фильтр по времени: day/week/month/year
        )
        # result — AsyncPaginatedResult, доступ к данным через .data
        raw_posts = result.data or []
        posts = []
        for r in raw_posts:
            if not r or not r.title:
                continue
            # URL: пробуем several полей
            url = r.url or getattr(r, 'post_url', None) or ""
            if not url and r.id:
                url = f"https://www.reddit.com/r/{subreddit}/comments/{r.id}"
            post = {
                "id": r.id or "",
                "title": r.title or "",
                "url": url,
                "source": f"r/{subreddit}",
                "source_url": f"https://www.reddit.com/r/{subreddit}",
                "category": category,  # для совместимости с digest.py
                "source_category": category,
                "source_language": "en",
                "source_priority": "medium",
                "published": r.created_at_date or "",
                "summary": (r.selftext or "")[:600],
                "author": getattr(r, 'author_username', None) or "unknown",
                "tags": ["reddit", subreddit.lower()],
                "score": r.score or 0,
                "comments_count": getattr(r, 'comments_count', None) or 0,
                "source_type": "xpoz_reddit",
            }
            if post["title"]:
                posts.append(post)
        return posts
    except Exception as e:
        print(f"   ⚠️ r/{subreddit}: {e}", file=sys.stderr)
        return []


async def main_async(args) -> dict:
    """Основная асинхронная логика."""
    all_posts = []
    seen_ids = set()

    async with AsyncXpozClient(API_KEY) as client:
        for category, subreddits in SUBREDDITS.items():
            print(f"📦 {category}: {len(subreddits)} сабреддитов...")
            tasks = [fetch_subreddit(client, s, category) for s in subreddits]
            results = await asyncio.gather(*tasks)
            for posts in results:
                for p in posts:
                    if p["id"] not in seen_ids:
                        seen_ids.add(p["id"])
                        all_posts.append(p)
            print(f"   → {sum(len(r) for r in results)} постов")

    # Дедупликация по title
    seen_titles = set()
    unique_posts = []
    max_age_hours = args.hours if hasattr(args, 'hours') else 48
    now = datetime.now(timezone.utc)

    for p in all_posts:
        title_key = p["title"].lower()[:80]
        if title_key not in seen_titles:
            # Фильтр по возрасту
            pub = p.get("published", "")
            if pub:
                try:
                    from dateutil import parser as dateparser
                    dt = dateparser.parse(pub)
                    age_hours = (now - dt).total_seconds() / 3600
                    if age_hours > max_age_hours:
                        continue  # пропускаем старые посты
                except Exception:
                    pass
            seen_titles.add(title_key)
            unique_posts.append(p)

    # Сортируем по score
    unique_posts.sort(key=lambda x: x.get("score", 0), reverse=True)

    print(f"\n✅ Итого: {len(unique_posts)} уникальных постов из {len(all_posts)}")
    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "since": (datetime.now(timezone.utc) - timedelta(hours=args.hours)).isoformat(),
        "hours": args.hours,
        "source_type": "xpoz_reddit",
        "sources_total": sum(len(s) for s in SUBREDDITS.values()),
        "posts": unique_posts,
    }


def main():
    parser = argparse.ArgumentParser(description="Reddit posts via Xpoz API")
    parser.add_argument("--hours", type=int, default=48, help="Сколько часов назад искать (default: 48)")
    parser.add_argument("--output", type=str, default=None, help="Путь для сохранения JSON")
    args = parser.parse_args()

    if args.output:
        output_path = Path(args.output)
    else:
        date_str = datetime.now().strftime("%Y-%m-%d")
        output_path = Path(f"storage/xpoz_posts_{date_str}.json")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"🔍 Xpoz Reddit fetch (last {args.hours}h)")
    print(f"📁 Output: {output_path}\n")

    result = asyncio.run(main_async(args))

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n💾 Сохранено: {output_path}")
    print(f"   Постов: {len(result['posts'])}")
    print(f"   Источников: {result['sources_total']}")


if __name__ == "__main__":
    main()