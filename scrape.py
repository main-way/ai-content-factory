#!/usr/bin/env python3
"""
scrape.py — скрапер полного текста статей через trafilatura.

Скачивает HTML страницы из архива, извлекает main content,
сохраняет в archive/full_text/<id>.txt. Быстро, параллельно.

Использование:
    python scrape.py --all              # скрапить все посты без текста
    python scrape.py --missing          # только посты, у которых ещё нет файла
    python scrape.py --recent 50        # только последние 50 постов
    python scrape.py --ids 8462d1d0 7c39f76d  # конкретные ID
    python scrape.py --dry-run          # показать что будет скрапиться, не качать
"""
import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from time import sleep

import requests
import trafilatura

sys.path.insert(0, str(Path(__file__).parent))
from archive import Archive  # noqa: E402

BASE = Path(__file__).parent
ARCHIVE_DIR = BASE / "archive"
FULL_TEXT_DIR = ARCHIVE_DIR / "full_text"
META_FILE = FULL_TEXT_DIR / "_index.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AI-Digest/1.0; +https://github.com/main-way/ai-digest)"
}
TIMEOUT = 30
MAX_WORKERS = 5
SLEEP_BETWEEN = 0.3  # секунд между запросами к одному домену (этика скрапинга)


def load_meta() -> dict:
    """Загружает индекс скрапнутых постов: {id: {url, length, scraped_at, status}}."""
    if META_FILE.exists():
        try:
            return json.loads(META_FILE.read_text())
        except Exception:
            pass
    return {}


def save_meta(meta: dict) -> None:
    META_FILE.write_text(json.dumps(meta, ensure_ascii=False, indent=2))


def scrape_one(post: dict) -> tuple[str, dict]:
    """Скачивает и извлекает main content из одной статьи."""
    pid = post["id"]
    url = post.get("url", "").strip()
    if not url:
        return pid, {"status": "error", "error": "no url"}

    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        if resp.status_code != 200:
            return pid, {"status": "error", "error": f"HTTP {resp.status_code}", "url": url}

        html = resp.text
        # Trafilatura: extract main content with metadata
        text = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            include_links=False,
            no_fallback=False,
            favor_precision=True,
            with_metadata=False,
        )
        if not text or len(text) < 200:
            return pid, {
                "status": "too_short",
                "error": f"extracted only {len(text)} chars",
                "url": url,
            }

        # Нормализуем текст
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r" {2,}", " ", text)
        text = text.strip()

        # Сохраняем
        FULL_TEXT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = FULL_TEXT_DIR / f"{pid}.txt"
        out_path.write_text(text, encoding="utf-8")

        return pid, {
            "status": "ok",
            "url": url,
            "length": len(text),
            "title": post.get("title", "")[:200],
            "source": post.get("source", ""),
        }
    except requests.exceptions.Timeout:
        return pid, {"status": "timeout", "url": url}
    except Exception as e:
        return pid, {"status": "error", "error": f"{type(e).__name__}: {str(e)[:80]}", "url": url}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="скрапить все посты из архива")
    ap.add_argument("--missing", action="store_true", help="только посты, для которых ещё нет файла")
    ap.add_argument("--recent", type=int, metavar="N", help="только последние N постов")
    ap.add_argument("--ids", nargs="+", help="конкретные ID постов")
    ap.add_argument("--dry-run", action="store_true", help="не качать, только показать")
    ap.add_argument("--workers", type=int, default=MAX_WORKERS)
    ap.add_argument("--limit", type=int, default=0, help="обработать не более N постов (0 = без лимита)")
    args = ap.parse_args()

    if not (args.all or args.missing or args.recent or args.ids):
        print("❌ Укажи --all, --missing, --recent N или --ids", file=sys.stderr)
        return 1

    arch = Archive(lazy=False)
    posts = arch.posts
    print(f"📚 В архиве: {len(posts)} постов", file=sys.stderr)

    # Фильтрация
    meta = load_meta()
    if args.ids:
        posts = [p for p in posts if p["id"] in args.ids]
    elif args.recent:
        posts = posts[:args.recent]
    elif args.missing:
        posts = [p for p in posts if p["id"] not in meta or meta[p["id"]].get("status") != "ok"]
    if args.limit > 0:
        posts = posts[:args.limit]
    print(f"🎯 К скрапингу: {len(posts)}", file=sys.stderr)

    if args.dry_run:
        for p in posts[:20]:
            print(f"  • {p['id']} | {p['url'][:80]}")
        if len(posts) > 20:
            print(f"  ... и ещё {len(posts) - 20}")
        return 0

    if not posts:
        print("✅ Нечего скрапить", file=sys.stderr)
        return 0

    # Скрапим
    FULL_TEXT_DIR.mkdir(parents=True, exist_ok=True)
    import time
    started = time.time()
    ok = error = too_short = timeout = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(scrape_one, p): p for p in posts}
        for i, future in enumerate(as_completed(futures), 1):
            pid, result = future.result()
            meta[pid] = result
            if result["status"] == "ok":
                ok += 1
                print(f"  ✓ {pid} | {result['length']:6d} chars | {result['url'][:70]}", file=sys.stderr)
            elif result["status"] == "too_short":
                too_short += 1
                print(f"  ∅ {pid} | {result.get('error', '?')[:60]}", file=sys.stderr)
            elif result["status"] == "timeout":
                timeout += 1
                print(f"  ⏱ {pid} | timeout", file=sys.stderr)
            else:
                error += 1
                print(f"  ✗ {pid} | {result.get('error', '?')[:60]}", file=sys.stderr)

            # Сохраняем индекс каждые 20 постов
            if i % 20 == 0:
                save_meta(meta)

    save_meta(meta)
    elapsed = time.time() - started
    print(f"\n✅ Готово за {elapsed:.1f}s", file=sys.stderr)
    print(f"   ✓ OK: {ok}", file=sys.stderr)
    print(f"   ∅ too_short: {too_short}", file=sys.stderr)
    print(f"   ⏱ timeout: {timeout}", file=sys.stderr)
    print(f"   ✗ error: {error}", file=sys.stderr)
    print(f"   📁 Файлы: {FULL_TEXT_DIR}", file=sys.stderr)

    # Обновляем посты в архиве — добавляем пометку, что текст скраплен
    if ok:
        arch.posts = [
            {**p, "has_full_text": p["id"] in meta and meta[p["id"]].get("status") == "ok"}
            for p in arch.posts
        ]
        # Сохраняем обновлённые посты
        import json as _json
        (ARCHIVE_DIR / "posts.json").write_text(
            _json.dumps(arch.posts, ensure_ascii=False, indent=2)
        )
        print(f"   📝 Пометка has_full_text проставлена в архиве", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main() or 0)
