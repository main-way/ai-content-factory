#!/usr/bin/env python3
"""
check_rss.py — проверка валидности RSS-источников из sources.yaml.

Делает для каждого URL:
1. GET-запрос (timeout=15s, без SOCKS5 — тут важен чистый тест)
2. Проверка HTTP-статуса
3. Проверка Content-Type (text/xml, application/rss+xml, application/atom+xml)
4. Парсинг через feedparser — есть ли реальные <item>/<entry>?
5. Сохранение отчёта в logs/check_report.json + вывод таблицы

Использование:
    python check_rss.py                  # все источники
    python check_rss.py --only-broken    # только подозрительные
    python check_rss.py --category ru_ai # только одна категория
"""
import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import requests
import yaml

# User-Agent как у нормального feed-reader — некоторые сайты блокируют python-requests
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AI-Digest/1.0; +https://github.com/main-way/ai-digest)"
}
TIMEOUT = 15
MAX_WORKERS = 8  # параллельных запросов — не больше, чтобы не упереться в rate-limit


def load_sources(path: Path) -> list[dict]:
    with open(path) as f:
        data = yaml.safe_load(f)
    return data["sources"]


def check_one(source: dict) -> dict:
    """Проверяет один источник, возвращает отчёт."""
    result = {
        "name": source["name"],
        "url": source["url"],
        "category": source["category"],
        "language": source["language"],
        "priority": source["priority"],
        "enabled": source["enabled"],
        "note": source.get("note", ""),
        "http_status": None,
        "content_type": None,
        "is_rss": False,
        "items_count": 0,
        "latest_title": None,
        "latest_date": None,
        "error": None,
        "verdict": "UNKNOWN",
    }

    try:
        resp = requests.get(source["url"], headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        result["http_status"] = resp.status_code
        result["content_type"] = resp.headers.get("Content-Type", "")

        if resp.status_code != 200:
            result["error"] = f"HTTP {resp.status_code}"
            result["verdict"] = "DEAD"
            return result

        # Пробуем распарсить фид
        feed = feedparser.parse(resp.content)

        # feedparser ставит .bozo=1 при ошибках парсинга
        if feed.bozo and not feed.entries:
            result["error"] = f"parse error: {str(feed.bozo_exception)[:100]}"
            result["verdict"] = "BROKEN"
            return result

        entries = feed.entries
        result["items_count"] = len(entries)
        result["is_rss"] = True

        if entries:
            result["latest_title"] = entries[0].get("title", "")[:120]
            published = entries[0].get("published_parsed") or entries[0].get("updated_parsed")
            if published:
                result["latest_date"] = datetime(*published[:6], tzinfo=timezone.utc).isoformat()

        # Финальный вердикт
        if result["items_count"] == 0:
            result["verdict"] = "EMPTY"  # валидный фид, но без постов
        elif result["latest_date"]:
            # Считаем свежесть
            try:
                last = datetime.fromisoformat(result["latest_date"])
                age_days = (datetime.now(timezone.utc) - last).days
                if age_days > 90:
                    result["verdict"] = "STALE"  # последний пост старше 3 месяцев
                else:
                    result["verdict"] = "ALIVE"
            except Exception:
                result["verdict"] = "ALIVE"
        else:
            result["verdict"] = "ALIVE"

    except requests.exceptions.Timeout:
        result["error"] = "timeout"
        result["verdict"] = "TIMEOUT"
    except requests.exceptions.ConnectionError as e:
        result["error"] = f"connection: {str(e)[:80]}"
        result["verdict"] = "DEAD"
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {str(e)[:80]}"
        result["verdict"] = "ERROR"

    return result


def run_checks(sources: list[dict], workers: int = MAX_WORKERS) -> list[dict]:
    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(check_one, s): s for s in sources}
        for future in as_completed(futures):
            r = future.result()
            results.append(r)
            # Прогресс в stderr
            mark = {"ALIVE": "✓", "STALE": "○", "EMPTY": "∅", "DEAD": "✗",
                    "BROKEN": "⚠", "TIMEOUT": "⏱", "ERROR": "?", "UNKNOWN": "?"}.get(r["verdict"], "?")
            print(f"{mark} {r['verdict']:8s} {r['name'][:40]:40s} → {r['url']}", file=sys.stderr)
    return results


def print_summary(results: list[dict]):
    """Группировка по вердиктам + статистика."""
    by_verdict = {}
    for r in results:
        by_verdict.setdefault(r["verdict"], []).append(r)

    print("\n" + "=" * 70)
    print("📊 СВОДКА ПО ПРОВЕРКЕ")
    print("=" * 70)
    print(f"Всего источников: {len(results)}")
    for verdict in ["ALIVE", "STALE", "EMPTY", "DEAD", "BROKEN", "TIMEOUT", "ERROR"]:
        n = len(by_verdict.get(verdict, []))
        if n:
            print(f"  {verdict:8s}: {n}")

    # Мёртвые/проблемные — для внимания
    print("\n🔴 ТРЕБУЮТ ВНИМАНИЯ:")
    for verdict in ["DEAD", "BROKEN", "TIMEOUT", "ERROR", "STALE", "EMPTY"]:
        items = by_verdict.get(verdict, [])
        for r in items:
            detail = r["error"] or f"items={r['items_count']}, last={r.get('latest_date', 'n/a')}"
            print(f"  [{r['verdict']:8s}] {r['name'][:35]:35s} — {detail[:60]}")

    # Категорийная разбивка живых
    print("\n✅ ЖИВЫЕ ИСТОЧНИКИ ПО КАТЕГОРИЯМ:")
    by_cat = {}
    for r in results:
        if r["verdict"] == "ALIVE":
            by_cat.setdefault(r["category"], []).append(r["name"])
    for cat, names in sorted(by_cat.items()):
        print(f"  {cat} ({len(names)}):")
        for n in names:
            print(f"    • {n}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="sources.yaml")
    parser.add_argument("--report", default="logs/check_report.json")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS)
    parser.add_argument("--only-broken", action="store_true",
                        help="показать только проблемные (после прогона)")
    parser.add_argument("--category", help="фильтр по категории")
    args = parser.parse_args()

    base = Path(__file__).parent
    sources = load_sources(base / args.config)
    if args.category:
        sources = [s for s in sources if s["category"] == args.category]
    if not args.only_broken:
        sources = [s for s in sources if s["enabled"]]

    print(f"🔍 Проверяю {len(sources)} источников (workers={args.workers})...\n", file=sys.stderr)
    started = time.time()
    results = run_checks(sources, args.workers)
    elapsed = time.time() - started

    # Сохраняем отчёт
    report_path = base / args.report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w") as f:
        json.dump({
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_sec": round(elapsed, 1),
            "total": len(results),
            "results": results,
        }, f, ensure_ascii=False, indent=2)

    if not args.only_broken:
        print_summary(results)
        print(f"\n⏱  Заняло: {elapsed:.1f}s")
        print(f"📄 Отчёт: {report_path}")


if __name__ == "__main__":
    main()
