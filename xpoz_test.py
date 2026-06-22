#!/usr/bin/env python3
"""
Xpoz Reddit integration for AI Digest
Fetches AI/LLM posts from Reddit via Xpoz API using async parallel requests
"""
import asyncio
import os
from datetime import datetime
from xpoz import AsyncXpozClient

# Xpoz API key - get at https://xpoz.ai/get-token
XPOZ_API_KEY = os.environ.get("XPOZ_API_KEY", "K3EzmsB69qxuIr10l15l6wBjllKFzv4y3HoFj0dw9NQxXHtRnlg9C4MEdTF3PChMAYns7PR")

SUBREDDITS = [
    "MachineLearning",
    "LocalLLaMA",
    "ClaudeAI",
    "artificial",
    "learnmachinelearning",
    "MLQuestions",
    "LanguageTechnology",
]

QUERY = "LLM GPT artificial intelligence model neural network"


async def fetch_reddit_posts_async(query: str, limit: int = 5) -> list[dict]:
    """Fetch AI/LLM posts from Reddit via Xpoz (async, parallel)"""
    async with AsyncXpozClient(XPOZ_API_KEY) as client:

        async def fetch_sub(sub: str):
            r = await client.reddit.search_posts(
                query,
                subreddit=sub,
                fields=[
                    "id",
                    "title",
                    "url",
                    "score",
                    "created_at_date",
                    "subreddit_name",
                ],
                limit=limit,
            )
            return r.data

        results = await asyncio.gather(*[fetch_sub(s) for s in SUBREDDITS])

    all_posts = [p for r in results for p in r]

    # Dedupe by id
    seen = set()
    unique = []
    for p in all_posts:
        if p.id not in seen:
            seen.add(p.id)
            unique.append({
                "id": p.id,
                "title": p.title,
                "url": p.url or "",
                "score": p.score or 0,
                "date": p.created_at_date,
                "subreddit": p.subreddit_name,
                "source": "reddit",
            })

    # Sort by score
    unique.sort(key=lambda x: x["score"], reverse=True)
    return unique


def main():
    print(f"Fetching Reddit posts via Xpoz at {datetime.now().isoformat()}")
    print(f"Query: {QUERY}")
    print(f"Subreddits: {len(SUBREDDITS)}")
    print("-" * 60)

    posts = asyncio.run(fetch_reddit_posts_async(QUERY, limit=5))

    print(f"Found {len(posts)} unique posts")
    print()
    for i, p in enumerate(posts[:15], 1):
        print(f"{i}. [{p['subreddit']}] score={p['score']} | {p['title'][:70]}")
        print(f"   url: {p['url']}")
        print()


if __name__ == "__main__":
    main()
