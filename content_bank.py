#!/usr/bin/env python3
"""
Content Knowledge Base — SQLite-хранилище постов за 30 дней.
Объединяет posts_*.json (метаданные) и archive/full_text/*.txt (контент).
Используется Composer Agent для генерации постов по Channel Profile.
"""
import sqlite3
import json
import re
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional

STORAGE_DIR = Path(__file__).parent / "storage"
ARCHIVE_FULL_TEXT = Path(__file__).parent / "archive" / "full_text"
KB_DB = Path(__file__).parent / "content_kb.db"
RETENTION_DAYS = 30

# ─── Schema ─────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id          TEXT PRIMARY KEY,
    title       TEXT,
    url         TEXT,
    source      TEXT,
    category    TEXT,
    language    TEXT,
    priority    TEXT,
    published   TEXT,
    summary     TEXT,
    author      TEXT,
    tags        TEXT,
    full_text   TEXT,
    fetched_at  TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_posts_category   ON posts(category);
CREATE INDEX IF NOT EXISTS idx_posts_language  ON posts(language);
CREATE INDEX IF NOT EXISTS idx_posts_priority  ON posts(priority);
CREATE INDEX IF NOT EXISTS idx_posts_published  ON posts(published);
CREATE INDEX IF NOT EXISTS idx_posts_source    ON posts(source);

CREATE VIRTUAL TABLE IF NOT EXISTS posts_fts USING fts5(
    title, summary, full_text, source,
    content='posts',
    content_rowid='rowid'
);
"""


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(KB_DB)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_connection()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


# ─── Full-text retrieval ─────────────────────────────────────────────────────

def load_full_text(post_id: str) -> Optional[str]:
    """Загружает полный текст из archive/full_text/{id}.txt"""
    path = ARCHIVE_FULL_TEXT / f"{post_id}.txt"
    if path.exists():
        try:
            text = path.read_text(encoding="utf-8")
            return text.strip() or None
        except Exception:
            return None
    return None


# ─── Import posts from JSON ──────────────────────────────────────────────────

def import_posts_file(json_path: Path) -> int:
    """Импортирует один файл storage/posts_YYYY-MM-DD.json в KB."""
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    posts = data.get("posts", [])
    if not posts:
        return 0

    fetched_at = data.get("fetched_at", "")
    imported = 0

    conn = get_connection()
    for post in posts:
        post_id = post.get("id", "")
        if not post_id:
            continue

        # Full text — загружаем из archive, если есть
        full_text = load_full_text(post_id)

        tags = json.dumps(post.get("tags", []), ensure_ascii=False)

        conn.execute("""
            INSERT OR REPLACE INTO posts
            (id, title, url, source, category, language, priority,
             published, summary, author, tags, full_text, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            post_id,
            post.get("title"),
            post.get("url"),
            post.get("source"),
            post.get("category"),
            post.get("language"),
            post.get("priority"),
            post.get("published"),
            post.get("summary"),
            post.get("author"),
            tags,
            full_text,
            fetched_at,
        ))
        imported += 1

    conn.commit()
    conn.close()
    return imported


def import_all(force: bool = False) -> dict:
    """Импортирует все storage/posts_*.json в KB."""
    files = sorted(STORAGE_DIR.glob("posts_*.json"))
    result = {"imported": 0, "skipped": 0, "files": []}

    for f in files:
        imported = import_posts_file(f)
        result["imported"] += imported
        result["files"].append({"file": f.name, "posts": imported})

    rebuild_fts()
    cleanup_old(retention_days=RETENTION_DAYS)

    return result


# ─── FTS ─────────────────────────────────────────────────────────────────────

def rebuild_fts():
    """Перестраивает FTS-индекс после импорта."""
    conn = get_connection()
    conn.execute("INSERT INTO posts_fts(posts_fts) VALUES('rebuild')")
    conn.commit()
    conn.close()


# ─── Retention ────────────────────────────────────────────────────────────────

def cleanup_old(retention_days: int = 30):
    """Удаляет посты старше retention_days из KB (не из storage!)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    conn = get_connection()
    cur = conn.execute("SELECT COUNT(*) FROM posts WHERE created_at < ?", (cutoff,))
    count = cur.fetchone()[0]
    if count > 0:
        conn.execute("DELETE FROM posts WHERE created_at < ?", (cutoff,))
        conn.commit()
        print(f"   🗑 KB cleanup: removed {count} old posts (>{retention_days} days)")
    conn.close()


# ─── Query API ────────────────────────────────────────────────────────────────

def query(
    categories: list[str] | None = None,
    languages: list[str] | None = None,
    priorities: list[str] | None = None,
    sources: list[str] | None = None,
    query_text: str | None = None,
    limit: int = 20,
    days_back: int = 7,
) -> list[dict]:
    """
    Запрашивает посты из KB по фильтрам.
    
    Args:
        categories: ['ru_ai', 'startups_vc', ...]
        languages: ['ru', 'en']
        priorities: ['high', 'medium']
        sources: ['Hacker News', 'Habr', ...]
        query_text: свободный текст для FTS-поиска
        limit: макс. число результатов
        days_back: искать только за последние N дней
    """
    conn = get_connection()

    conditions = []
    params = []

    if days_back:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).isoformat()
        conditions.append("published >= ?")
        params.append(cutoff)

    if categories:
        placeholders = ",".join("?" * len(categories))
        conditions.append(f"category IN ({placeholders})")
        params.extend(categories)

    if languages:
        placeholders = ",".join("?" * len(languages))
        conditions.append(f"language IN ({placeholders})")
        params.extend(languages)

    if priorities:
        placeholders = ",".join("?" * len(priorities))
        conditions.append(f"priority IN ({placeholders})")
        params.extend(priorities)

    if sources:
        placeholders = ",".join("?" * len(sources))
        conditions.append(f"source IN ({placeholders})")
        params.extend(sources)

    where = " AND ".join(conditions) if conditions else "1=1"
    params.append(limit)

    columns = [desc[0] for desc in conn.execute("SELECT * FROM posts LIMIT 0").description]

    # Prefix bare column names with p. only in FTS JOIN query (avoids ambiguous column error)
    def _qualify(col):
        import re
        bare = ["source", "language", "priority", "published", "fetched_at", "category",
                "id", "title", "url", "summary", "full_text", "author", "tags"]
        for c in bare:
            if re.match(rf"\b{re.escape(c)}\b\s*(IN|>=|<=|>|<|=|\\s)", col, re.IGNORECASE):
                return f"p.{c}" + col[len(c):]
        return col

    if query_text:
        fts_conditions = [_qualify(c) for c in conditions]
        fts_where = " AND ".join(fts_conditions) if fts_conditions else "1=1"
        full_params = [query_text] + params[:-1] + [limit]
        rows = conn.execute(f"""
            SELECT p.* FROM posts p
            JOIN posts_fts fts ON p.rowid = fts.rowid
            WHERE posts_fts MATCH ? AND {fts_where}
            ORDER BY rank
            LIMIT ?
        """, full_params).fetchall()
    else:
        rows = conn.execute(f"""
            SELECT * FROM posts
            WHERE {where}
            ORDER BY published DESC
            LIMIT ?
        """, params).fetchall()

    conn.close()
    results = []
    for row in rows:
        results.append(dict(zip(columns, row)))

    # Parse tags JSON
    for r in results:
        if r.get("tags"):
            try:
                r["tags"] = json.loads(r["tags"])
            except Exception:
                r["tags"] = []
    return results


def stats() -> dict:
    """Возвращает статистику KB."""
    conn = get_connection()
    cur = conn.execute("SELECT COUNT(*), COUNT(full_text), MIN(published), MAX(published) FROM posts")
    total, with_text, min_date, max_date = cur.fetchone()

    cur2 = conn.execute("""
        SELECT category, COUNT(*) as cnt FROM posts
        WHERE published >= datetime('now', '-7 days')
        GROUP BY category ORDER BY cnt DESC
    """)
    by_category = dict(cur2.fetchall())

    cur3 = conn.execute("SELECT COUNT(*) FROM posts WHERE published >= datetime('now', '-30 days')")
    last_30 = cur3.fetchone()[0]

    conn.close()
    return {
        "total_posts": total,
        "with_full_text": with_text,
        "date_range": {"min": min_date, "max": max_date},
        "last_7_days_by_category": by_category,
        "last_30_days": last_30,
        "db_size_mb": KB_DB.stat().st_size / 1024 / 1024 if KB_DB.exists() else 0,
    }


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    import argparse
    ap = argparse.ArgumentParser(description="Content Knowledge Base")
    ap.add_argument("--init", action="store_true", help="Инициализировать БД")
    ap.add_argument("--import", dest="import_data", action="store_true", help="Импортировать все storage/posts_*.json")
    ap.add_argument("--query", nargs="+", help="Свободный текст для FTS-поиска")
    ap.add_argument("--category", nargs="+", help="Фильтр по категориям")
    ap.add_argument("--lang", nargs="+", dest="languages", help="Фильтр по языкам")
    ap.add_argument("--priority", nargs="+", help="Фильтр по приоритету")
    ap.add_argument("--source", nargs="+", help="Фильтр по источникам")
    ap.add_argument("--days", type=int, default=7, help="Искать за последние N дней (default: 7)")
    ap.add_argument("--limit", type=int, default=20, help="Лимит результатов (default: 20)")
    ap.add_argument("--stats", action="store_true", help="Показать статистику KB")
    ap.add_argument("--rebuild-fts", action="store_true", help="Перестроить FTS-индекс")
    args = ap.parse_args()

    if args.init:
        init_db()
        print("✅ KB initialized:", KB_DB)
        return

    if not KB_DB.exists():
        print("❌ KB not initialized. Run: python content_bank.py --init")
        return

    if args.stats:
        s = stats()
        print("📊 Content KB Statistics")
        print(f"   Всего постов: {s['total_posts']}")
        print(f"   С full_text:  {s['with_full_text']}")
        print(f"   За 30 дней:   {s['last_30_days']}")
        print(f"   Период:       {s['date_range']['min']} → {s['date_range']['max']}")
        print(f"   Размер БД:    {s['db_size_mb']:.1f} MB")
        print("   За 7 дней по категориям:")
        for cat, cnt in s["last_7_days_by_category"].items():
            print(f"     {cat}: {cnt}")
        return

    if args.rebuild_fts:
        rebuild_fts()
        print("✅ FTS index rebuilt")
        return

    if args.import_data:
        result = import_all()
        print(f"✅ Import complete: {result['imported']} posts from {len(result['files'])} files")
        return

    # Query mode
    results = query(
        categories=args.category,
        languages=args.languages,
        priorities=args.priority,
        sources=args.source,
        query_text=" ".join(args.query) if args.query else None,
        days_back=args.days,
        limit=args.limit,
    )

    print(f"📦 KB query: {len(results)} results")
    for p in results[:5]:
        print(f"\n  [{p['priority'] or '?'}] {p['title']}")
        print(f"  📍 {p['source']} | {p['category']} | {p['language']} | {p['published'][:10]}")
        if p.get("summary"):
            print(f"  💬 {str(p['summary'])[:120]}")
        if p.get("full_text"):
            print(f"  📄 full_text: {len(p['full_text'])} chars")
        print(f"  🔗 {p['url']}")


if __name__ == "__main__":
    main()
