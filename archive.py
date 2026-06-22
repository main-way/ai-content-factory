#!/usr/bin/env python3
"""
archive.py — единый архив всех постов с дедупликацией по URL.

Структура:
  archive/
  ├── posts.json            — все посты за всё время, отсортированы по дате (новые сверху)
  ├── posts_by_date/        — посты, сгруппированные по дате (для быстрого отображения)
  │   ├── 2026-06-01.json
  │   └── 2026-06-02.json
  └── stats.json            — кэш статистики

Использование как модуль:
    from archive import Archive
    a = Archive()
    a.add(new_posts)        # дедуп по URL, сохраняет на диск
    print(a.count())        # всего постов
    a.search("anthropic")   # поиск
    a.by_date("2026-06-02") # посты за дату

CLI:
    python archive.py --stats               # статистика
    python archive.py --search "anthropic"  # поиск
    python archive.py --list 10             # последние 10 постов
    python archive.py --add storage/posts_2026-06-03.json
"""
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from dateutil import parser as dateparser


BASE = Path(__file__).parent
ARCHIVE_DIR = BASE / "archive"
POSTS_FILE = ARCHIVE_DIR / "posts.json"
BY_DATE_DIR = ARCHIVE_DIR / "posts_by_date"
STATS_FILE = ARCHIVE_DIR / "stats.json"
FULL_TEXT_DIR = ARCHIVE_DIR / "full_text"


def normalize_url(url: str) -> str:
    """Убираем UTM и прочий tracking из URL для дедупликации."""
    url = url.split("?")[0] if "?" in url and ("utm_" in url or "from=rss" in url) else url
    return url.strip()


class Archive:
    def __init__(self, lazy: bool = True):
        self.posts: list[dict] = []
        self._index: dict[str, dict] = {}  # url → post (для быстрой дедуп)
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        BY_DATE_DIR.mkdir(parents=True, exist_ok=True)
        if not lazy:
            self.load()

    def load(self) -> None:
        if POSTS_FILE.exists():
            try:
                with open(POSTS_FILE) as f:
                    self.posts = json.load(f)
                self._index = {normalize_url(p["url"]): p for p in self.posts}
            except (json.JSONDecodeError, KeyError) as e:
                print(f"⚠️  Ошибка чтения {POSTS_FILE}: {e}", file=sys.stderr)
                self.posts = []
                self._index = {}

    def add(self, new_posts: Iterable[dict]) -> dict:
        """
        Добавляет посты, дедуп по нормализованному URL.
        Возвращает {'added': N, 'updated': M, 'skipped': K}.
        """
        if not self._index:
            self.load()
        added = updated = skipped = 0
        for p in new_posts:
            url = normalize_url(p.get("url", ""))
            if not url:
                skipped += 1
                continue
            if url in self._index:
                # Обновляем существующий (мог появиться author/tags/summary с деталями)
                existing = self._index[url]
                for k, v in p.items():
                    if v and (k not in existing or not existing.get(k)):
                        existing[k] = v
                updated += 1
            else:
                self.posts.append(p)
                self._index[url] = p
                added += 1
        return {"added": added, "updated": updated, "skipped": skipped}

    def save(self) -> None:
        # Сортируем по дате публикации (свежие сверху), неизвестные в конец
        def sort_key(p):
            try:
                if p.get("published"):
                    return dateparser.parse(p["published"]).timestamp()
            except Exception:
                pass
            return 0

        self.posts.sort(key=sort_key, reverse=True)
        with open(POSTS_FILE, "w") as f:
            json.dump(self.posts, f, ensure_ascii=False, indent=2)
        self._save_by_date()
        self._save_stats()

    def _save_by_date(self) -> None:
        # Группируем по дате (YYYY-MM-DD) — что было опубликовано в этот день
        by_date = defaultdict(list)
        for p in self.posts:
            date = ""
            if p.get("published"):
                try:
                    date = dateparser.parse(p["published"]).strftime("%Y-%m-%d")
                except Exception:
                    date = "unknown"
            else:
                date = "unknown"
            by_date[date].append(p)

        # Удаляем старые файлы
        for f in BY_DATE_DIR.glob("*.json"):
            f.unlink()

        for date, items in sorted(by_date.items(), reverse=True):
            with open(BY_DATE_DIR / f"{date}.json", "w") as f:
                json.dump({
                    "date": date,
                    "count": len(items),
                    "posts": items,
                }, f, ensure_ascii=False, indent=2)

    def _save_stats(self) -> None:
        # Кэш статистики — быстрый доступ к агрегатам
        by_cat = Counter(p.get("category", "?") for p in self.posts)
        by_src = Counter(p.get("source", "?") for p in self.posts)
        by_lang = Counter(p.get("language", "?") for p in self.posts)
        dates = Counter()
        for p in self.posts:
            if p.get("published"):
                try:
                    d = dateparser.parse(p["published"]).strftime("%Y-%m-%d")
                    dates[d] += 1
                except Exception:
                    pass

        stats = {
            "total_posts": len(self.posts),
            "unique_sources": len(by_src),
            "by_category": dict(by_cat),
            "top_sources": by_src.most_common(20),
            "by_language": dict(by_lang),
            "posts_by_date": dict(sorted(dates.items())),
            "first_post_date": min(dates) if dates else None,
            "last_post_date": max(dates) if dates else None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(STATS_FILE, "w") as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)

    def count(self) -> int:
        return len(self.posts)

    def dates(self) -> list[str]:
        """Список дат, за которые есть посты."""
        dates = set()
        for p in self.posts:
            if p.get("published"):
                try:
                    d = dateparser.parse(p["published"]).strftime("%Y-%m-%d")
                    dates.add(d)
                except Exception:
                    pass
        return sorted(dates, reverse=True)

    def by_date(self, date: str) -> list[dict]:
        """Посты за конкретную дату (YYYY-MM-DD)."""
        result = []
        for p in self.posts:
            if p.get("published"):
                try:
                    if dateparser.parse(p["published"]).strftime("%Y-%m-%d") == date:
                        result.append(p)
                except Exception:
                    pass
        return result

    def search(self, query: str, limit: int = 20, full_text: bool = False) -> list[dict]:
        """
        Полнотекстовый поиск по title + summary + tags.
        Если full_text=True и есть скрапнутый текст в archive/full_text/<id>.txt —
        ищет и по нему тоже.
        По релевантности.
        """
        q = query.lower()
        q_words = [w for w in re.split(r"\W+", q) if len(w) >= 2]
        if not q_words:
            return []

        scored = []
        for p in self.posts:
            text = " ".join([
                p.get("title", ""),
                p.get("summary", ""),
                " ".join(p.get("tags", []) or []),
                p.get("source", ""),
            ]).lower()

            # Опционально — добавляем полный текст
            full_text_content = ""
            if full_text and p.get("has_full_text"):
                ft_path = FULL_TEXT_DIR / f"{p['id']}.txt"
                if ft_path.exists():
                    try:
                        full_text_content = ft_path.read_text(encoding="utf-8", errors="ignore").lower()
                    except Exception:
                        pass

            score = 0
            matched = 0
            for w in q_words:
                cnt = text.count(w)
                ft_cnt = full_text_content.count(w) if full_text_content else 0
                if cnt > 0 or ft_cnt > 0:
                    matched += 1
                    # вес: вхождение в title важнее, чем в summary
                    title_cnt = p.get("title", "").lower().count(w)
                    score += cnt + title_cnt * 3 + ft_cnt  # full_text вклад обычный
            if matched == len(q_words):
                # все слова найдены — выше релевантность
                score += 10
            elif matched == 0:
                continue
            scored.append((score, p))
        scored.sort(key=lambda x: -x[0])
        return [p for _, p in scored[:limit]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--add", help="добавить посты из указанного JSON-файла")
    ap.add_argument("--stats", action="store_true", help="показать статистику")
    ap.add_argument("--search", help="поиск по архиву")
    ap.add_argument("--full-text-search", action="store_true",
                    help="искать и по скрапленным полным текстам (медленнее, но точнее)")
    ap.add_argument("--list", type=int, metavar="N", help="последние N постов")
    ap.add_argument("--date", help="посты за дату (YYYY-MM-DD)")
    args = ap.parse_args()

    arch = Archive(lazy=False)

    if args.add:
        in_path = Path(args.add)
        if not in_path.exists():
            print(f"❌ Нет файла {in_path}", file=sys.stderr)
            return 1
        with open(in_path) as f:
            data = json.load(f)
        new_posts = data.get("posts", [])
        result = arch.add(new_posts)
        arch.save()
        print(f"📥 Добавлено: {result['added']}, обновлено: {result['updated']}, "
              f"пропущено: {result['skipped']}", file=sys.stderr)
        print(f"📊 Всего в архиве: {arch.count()}")
        return 0

    if args.stats:
        if not STATS_FILE.exists():
            arch.save()
        with open(STATS_FILE) as f:
            stats = json.load(f)
        print(f"📊 Всего постов: {stats['total_posts']}")
        print(f"📡 Уникальных источников: {stats['unique_sources']}")
        print(f"📅 Период: {stats['first_post_date']} → {stats['last_post_date']}")
        print(f"🌐 Языки: {stats['by_language']}")
        print(f"\n📂 По категориям:")
        for cat, n in sorted(stats['by_category'].items(), key=lambda x: -x[1]):
            print(f"   {cat:20s}: {n:4d}")
        print(f"\n🏆 Топ-15 источников:")
        for src, n in stats['top_sources'][:15]:
            print(f"   {n:4d} — {src}")
        print(f"\n📈 По дням (последние 14):")
        for d, n in list(stats['posts_by_date'].items())[-14:]:
            bar = "█" * min(n // 5, 30)
            print(f"   {d}: {n:4d} {bar}")
        return 0

    if args.search:
        results = arch.search(args.search, limit=20, full_text=args.full_text_search)
        suffix = " (с полными текстами)" if args.full_text_search else ""
        print(f"🔍 Найдено: {len(results)}{suffix} (показано топ-20)\n")
        for i, p in enumerate(results, 1):
            date = p.get("published", "")[:10]
            ft_mark = " 📄" if p.get("has_full_text") else ""
            print(f"[{i:2d}] {p['title'][:90]}{ft_mark}")
            print(f"     {p['source']} | {date}")
            print(f"     {p['url']}\n")
        return 0

    if args.date:
        posts = arch.by_date(args.date)
        print(f"📅 {args.date}: {len(posts)} постов\n")
        for p in sorted(posts, key=lambda x: x.get("published", ""), reverse=True):
            print(f"• {p['title'][:90]}")
            print(f"  {p['source']} | {p['url']}\n")
        return 0

    if args.list:
        for p in arch.posts[:args.list]:
            date = p.get("published", "")[:10]
            print(f"• [{date}] {p['title'][:90]} — {p['source']}")
        return 0

    # дефолт — краткая сводка
    print(f"📊 В архиве: {arch.count()} постов за {len(arch.dates())} дней")
    print(f"\nИспользование:")
    print(f"  python archive.py --stats")
    print(f"  python archive.py --search 'anthropic'")
    print(f"  python archive.py --date 2026-06-02")
    print(f"  python archive.py --list 20")
    print(f"  python archive.py --add storage/posts_2026-06-03.json")


if __name__ == "__main__":
    sys.exit(main() or 0)
