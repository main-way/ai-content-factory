#!/usr/bin/env python3
"""
fetch.py — скачивание и нормализация постов из всех enabled RSS-источников.

Скачивает параллельно, нормализует в единый JSON-формат, дедуплицирует,
сохраняет в storage/posts_YYYY-MM-DD.json.

Использование:
    python fetch.py                       # посты за последние 24ч
    python fetch.py --hours 48            # за 2 дня
    python fetch.py --hours 168 --max 50  # за неделю, до 50 с источника
    python fetch.py --source vc.ru        # только один источник (поиск по имени)
"""
import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from hashlib import md5
from pathlib import Path

import feedparser
import requests
import yaml
from dateutil import parser as dateparser

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AI-Digest/1.0; +https://github.com/main-way/ai-digest)"
}
TIMEOUT = 20
MAX_WORKERS = 10  # параллельных запросов

# SOCKS5 прокси для fallback (обход блокировок Cloudflare, geo-restrictions)
SOCKS5_PROXY = "socks5h://zufarisai:oKaL4vBTtq@91.123.78.200:50100"

# Стрип-теги для summary
HTML_TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")


def strip_html(text: str, max_len: int = 600) -> str:
    """Чистим HTML-теги, схлопываем пробелы, режем по длине."""
    if not text:
        return ""
    text = HTML_TAG_RE.sub(" ", text)
    text = WHITESPACE_RE.sub(" ", text).strip()
    if len(text) > max_len:
        text = text[:max_len].rsplit(" ", 1)[0] + "…"
    return text


def parse_date(entry) -> datetime | None:
    """Парсим дату из RSS entry, перебираем несколько полей."""
    for field in ("published_parsed", "updated_parsed", "created_parsed"):
        v = entry.get(field)
        if v:
            try:
                return datetime(*v[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    for field in ("published", "updated", "created"):
        v = entry.get(field)
        if v:
            try:
                dt = dateparser.parse(v)
                if dt:
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    else:
                        dt = dt.astimezone(timezone.utc)
                    return dt
            except Exception:
                pass
    return None


def url_hash(url: str) -> str:
    return md5(url.encode("utf-8")).hexdigest()[:12]


def fetch_one(source: dict, since: datetime, max_per_source: int, use_fallback: bool = True) -> dict:
    """Скачивает один фид, при ошибке делает retry через SOCKS5 прокси."""
    result = {
        "source_name": source["name"],
        "source_url": source["url"],
        "source_category": source["category"],
        "source_language": source["language"],
        "source_priority": source["priority"],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "ok": False,
        "error": None,
        "used_fallback": False,
        "posts": [],
    }

    def _fetch(url: str, proxy: str | None = None) -> requests.Response:
        kwargs = {"headers": HEADERS, "timeout": TIMEOUT, "allow_redirects": True}
        if proxy:
            kwargs["proxies"] = {"http": proxy, "https": proxy}
        return requests.get(url, **kwargs)

    try:
        resp = _fetch(source["url"])
        if resp.status_code != 200:
            result["error"] = f"HTTP {resp.status_code}"
            # Retry через прокси
            if use_fallback and resp.status_code in (403, 429, 500, 502, 503, 504):
                result["used_fallback"] = True
                try:
                    resp = _fetch(source["url"], proxy=SOCKS5_PROXY)
                except Exception:
                    pass
                else:
                    if resp.status_code == 200:
                        result["error"] = None
            return _finish(result, resp, max_per_source)

    except requests.exceptions.Timeout:
        result["error"] = "timeout"
        if use_fallback:
            result["used_fallback"] = True
            try:
                resp = _fetch(source["url"], proxy=SOCKS5_PROXY)
                result["error"] = None
            except Exception as e:
                result["error"] = f"fallback timeout: {str(e)[:40]}"
        if result["error"]:
            return result

    except requests.exceptions.ConnectionError as e:
        result["error"] = f"connection: {str(e)[:60]}"
        if use_fallback:
            result["used_fallback"] = True
            try:
                resp = _fetch(source["url"], proxy=SOCKS5_PROXY)
                result["error"] = None
            except Exception as e:
                result["error"] = f"fallback failed: {str(e)[:60]}"
        if result["error"]:
            return result

    except Exception as e:
        result["error"] = f"{type(e).__name__}: {str(e)[:60]}"
        return result

    return _finish(result, resp, max_per_source)


def _finish(result: dict, resp, max_per_source: int) -> dict:
    """Обработка успешного HTTP-ответа."""
    global since
    try:
        feed = feedparser.parse(resp.content)
        if feed.bozo and not feed.entries:
            result["error"] = f"parse error: {str(feed.bozo_exception)[:80]}"
            return result

        kept = 0
        for entry in feed.entries:
            post_date = parse_date(entry)
            if post_date:
                try:
                    # Ensure post_date is timezone-aware for comparison
                    if post_date.tzinfo is None:
                        post_date = post_date.replace(tzinfo=timezone.utc)
                    if post_date < since:
                        continue
                except TypeError:
                    # If comparison still fails, include the post
                    pass

            url = entry.get("link", "").strip()
            if not url:
                continue

            title = (entry.get("title") or "").strip()
            summary = strip_html(entry.get("summary") or entry.get("description") or "")
            author = (entry.get("author") or "").strip()

            tags = []
            for cat in entry.get("tags", []) or []:
                term = cat.get("term")
                if term:
                    tags.append(term.strip())

            post = {
                "id": url_hash(url),
                "title": title,
                "url": url,
                "source": result["source_name"],
                "category": result["source_category"],
                "language": result["source_language"],
                "priority": result["source_priority"],
                "published": post_date.isoformat() if post_date else None,
                "summary": summary,
                "author": author,
                "tags": tags,
            }
            result["posts"].append(post)
            kept += 1
            if kept >= max_per_source:
                break

        result["ok"] = True
        result["count"] = kept
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {str(e)[:60]}"
    return result


# global для доступа в _finish
since = None


def load_sources(path: Path) -> list[dict]:
    with open(path) as f:
        data = yaml.safe_load(f)
    return [s for s in data["sources"] if s.get("enabled", True)]


def merge_with_existing(new_posts: list[dict], existing_path: Path) -> list[dict]:
    """Дедуп по URL + ID: новые добавляются, старые остаются."""
    seen = {}
    if existing_path.exists():
        try:
            with open(existing_path) as f:
                old = json.load(f)
            for p in old:
                seen[p["url"]] = p
        except Exception:
            pass
    for p in new_posts:
        seen[p["url"]] = p
    return list(seen.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="sources.yaml")
    ap.add_argument("--storage", default="storage")
    ap.add_argument("--hours", type=int, default=24, help="брать посты не старше N часов")
    ap.add_argument("--max", type=int, default=30, help="макс. постов с одного источника")
    ap.add_argument("--workers", type=int, default=MAX_WORKERS)
    ap.add_argument("--source", help="фильтр по имени источника (substring)")
    ap.add_argument("--archive", action="store_true",
                    help="добавить результаты в archive/posts.json (дедуп по URL)")
    args = ap.parse_args()

    base = Path(__file__).parent
    sources = load_sources(base / args.config)
    if args.source:
        sources = [s for s in sources if args.source.lower() in s["name"].lower()]
        if not sources:
            print(f"❌ Нет источника с подстрокой '{args.source}'", file=sys.stderr)
            return 1

    since = datetime.now(timezone.utc) - timedelta(hours=args.hours)
    print(f"📡 Скачиваю {len(sources)} источников (с {since.isoformat()})...", file=sys.stderr)

    started = time.time()
    all_results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_one, s, since, args.max): s for s in sources}
        for future in as_completed(futures):
            r = future.result()
            all_results.append(r)
            mark = "✓" if r["ok"] else "✗"
            detail = f"{r.get('count', 0)} posts" if r["ok"] else r.get("error", "?")
            print(f"  {mark} {r['source_name'][:40]:40s} → {detail}", file=sys.stderr)

    # Собираем посты
    posts = []
    for r in all_results:
        posts.extend(r["posts"])

    # Сортировка по дате (свежие сверху), потом по source_priority (high первым)
    priority_order = {"high": 0, "medium": 1, "low": 2}
    def parse_sort_date(published_str):
        if not published_str:
            return 0
        try:
            dt = dateparser.parse(published_str)
            if dt is None:
                return 0
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return -dt.timestamp()
        except Exception:
            return 0

    posts.sort(
        key=lambda p: (
            parse_sort_date(p["published"]),
            priority_order.get(p["priority"], 9),
        )
    )

    # Сохраняем
    storage = base / args.storage
    storage.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_path = storage / f"posts_{today}.json"

    # Мерджим с уже сохранёнными за сегодня (если fetch.py запускается несколько раз)
    posts = merge_with_existing(posts, out_path)

    with open(out_path, "w") as f:
        json.dump({
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "since": since.isoformat(),
            "hours": args.hours,
            "sources_total": len(sources),
            "sources_ok": sum(1 for r in all_results if r["ok"]),
            "sources_failed": sum(1 for r in all_results if not r["ok"]),
            "posts_total": len(posts),
            "results": all_results,
            "posts": posts,
        }, f, ensure_ascii=False, indent=2)

    elapsed = time.time() - started
    print(f"\n✅ Готово за {elapsed:.1f}s", file=sys.stderr)
    print(f"   Источников OK: {sum(1 for r in all_results if r['ok'])}/{len(sources)}", file=sys.stderr)
    print(f"   Постов собрано (с дедупликацией): {len(posts)}", file=sys.stderr)
    print(f"   Файл: {out_path}", file=sys.stderr)

    # Опционально — добавить в архив
    if args.archive:
        try:
            sys.path.insert(0, str(base))
            from archive import Archive
            arch = Archive(lazy=False)
            res = arch.add(posts)
            arch.save()
            print(f"\n📥 Архив: добавлено {res['added']}, обновлено {res['updated']}, "
                  f"пропущено {res['skipped']}. Всего в архиве: {arch.count()}", file=sys.stderr)
        except Exception as e:
            print(f"⚠️  Ошибка записи в архив: {e}", file=sys.stderr)

    # Возвращаем путь для пайпа в digest.py
    print(out_path)


if __name__ == "__main__":
    sys.exit(main() or 0)
