#!/usr/bin/env python3
"""
publish.py — финальный шаг pipeline: сохранить полный дайджест в Obsidian,
сгенерировать Telegram-саммари, почистить скрапленные тексты.

Шаги:
  1. Берёт output/analysis_v2_YYYY-MM-DD.md
  2. Сохраняет в Obsidian vault (BRIEFINGS/AI-Digest/YYYY-MM-DD.md)
     с frontmatter и тегами
  3. Генерирует Telegram-саммари (TL;DR + топ-ссылки)
  4. Удаляет archive/full_text/ (тексты больше не нужны)
  5. Печатает саммари в stdout для отправки

Использование:
    python publish.py                              # сегодняшний v2
    python publish.py --input output/analysis_v2_*.md
    python publish.py --keep-full-text             # не удалять archive/full_text/
    python publish.py --dry-run                    # не сохранять, не удалять, только показать
"""
import argparse
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).parent
OUTPUT_DIR = BASE / "output"
ARCHIVE_DIR = BASE / "archive"
FULL_TEXT_DIR = ARCHIVE_DIR / "full_text"
INDEX_FILE = FULL_TEXT_DIR / "_index.json"

# Vault на VPS (см. /home/apps_maker/.hermes/skills/note-taking/obsidian/SKILL.md)
# В WSL был бы /mnt/h/Personal/obsiBase, на сервере — /srv/obsidian-base
VAULT_PATHS = [
    Path("/srv/obsidian-base"),
    Path("/mnt/h/Personal/obsiBase"),
    Path.home() / "Documents" / "Obsidian Vault",
]
BRIEFINGS_SUBDIR = "BRIEFINGS/AI-Digest"


def find_vault() -> Path | None:
    """Находит Obsidian vault среди стандартных путей."""
    for p in VAULT_PATHS:
        if p.exists() and (p / ".obsidian").exists():
            return p
    return None


def parse_v2_digest(md_text: str) -> dict:
    """
    Извлекает из analysis_v2_*.md:
    - title
    - tl_dr (5 пунктов)
    - body (основной текст после TL;DR)
    - links (все уникальные URL)
    """
    result = {"title": "", "tl_dr": [], "body": "", "links": []}

    # Title — первая # строка
    title_match = re.search(r"^# (.+)$", md_text, re.MULTILINE)
    if title_match:
        result["title"] = title_match.group(1).strip()

    # Все уникальные ссылки
    result["links"] = list(dict.fromkeys(re.findall(r"https?://[^\s\)\]]+", md_text)))

    # TL;DR секция — "## Если лень читать — 5 главных вещей" до следующего ##
    tldr_match = re.search(
        r"## Если лень читать.*?(?=\n## |\Z)",
        md_text, re.DOTALL
    )
    if tldr_match:
        section = tldr_match.group(0)
        # Извлекаем нумерованные пункты
        items = re.findall(r"^\d+\.\s+\*\*(.+?)\*\*", section, re.MULTILINE)
        result["tl_dr"] = items[:5]

    # Body — после TL;DR
    body_start = md_text.find("---", md_text.find("## Если лень читать"))
    if body_start == -1:
        body_start = md_text.find("## 1.")
    if body_start > 0:
        result["body"] = md_text[body_start:].strip()

    return result


def make_obsidian_note(parsed: dict, date_str: str, vault: Path, source_md: Path) -> Path:
    """Создаёт заметку в Obsidian с frontmatter + body."""
    target_dir = vault / BRIEFINGS_SUBDIR
    target_dir.mkdir(parents=True, exist_ok=True)

    # Obsidian-совместимый frontmatter
    frontmatter = f"""---
date: {date_str}
type: ai-digest
tags:
  - ai/дайджест
  - ai/новости
  - briefing
source_file: {source_md.name}
posts_in_period: ~350
unique_sources: 30
language:
  - en
  - ru
---

**Проект:** [[AI-бизнес-mAIn-Way]]
**Категория:** [[AI-Agency]]

"""

    # Берём полный текст из исходного файла (без дубля заголовка)
    full_text = source_md.read_text(encoding="utf-8")

    # Убираем footer (последняя строка "*Подготовлено ...*")
    # и дубль заголовка, если он есть
    full_text = re.sub(
        r"^# .+разбор недели.+\n",
        "",
        full_text,
        count=1,
        flags=re.MULTILINE,
    )

    out_path = target_dir / f"{date_str}.md"
    out_path.write_text(frontmatter + full_text, encoding="utf-8")
    return out_path


def make_telegram_summary(parsed: dict, date_str: str, obsidian_path: Path,
                          archived: bool) -> str:
    """Генерирует компактное саммари для Telegram (~1.5-2KB)."""

    lines = []
    lines.append(f"📡 *AI-Digest — {date_str}*")
    lines.append(f"_(авто-обзор из {len(parsed['links'])} источников)_")
    lines.append("")

    if parsed["tl_dr"]:
        lines.append("*🔥 TL;DR:*")
        for i, item in enumerate(parsed["tl_dr"], 1):
            # Укорачиваем до 200 символов
            short = item[:200] + ("…" if len(item) > 200 else "")
            lines.append(f"{i}. {short}")
        lines.append("")

    # Топ-5 ссылок (первые уникальные)
    lines.append(f"*🔗 Топ-ссылки:*")
    for link in parsed["links"][:5]:
        lines.append(f"• {link}")
    lines.append("")

    lines.append(f"📂 *Полная версия:* Obsidian → `BRIEFINGS/AI-Digest/{date_str}.md`")
    lines.append(f"📊 *Постов в обзоре:* ~350 | *Источников:* 30 | *Период:* 2-3 дня")

    if archived:
        lines.append("🧹 Сырые тексты постов очищены для экономии места")

    return "\n".join(lines)


def cleanup_full_text(dry_run: bool = False) -> dict:
    """Удаляет скрапленные полные тексты и индекс."""
    result = {"files_removed": 0, "bytes_freed": 0, "errors": []}
    if not FULL_TEXT_DIR.exists():
        return result

    for f in FULL_TEXT_DIR.glob("*.txt"):
        try:
            size = f.stat().st_size
            if not dry_run:
                f.unlink()
            result["files_removed"] += 1
            result["bytes_freed"] += size
        except Exception as e:
            result["errors"].append(f"{f.name}: {e}")

    # Удаляем _index.json
    if INDEX_FILE.exists():
        try:
            size = INDEX_FILE.stat().st_size
            if not dry_run:
                INDEX_FILE.unlink()
            result["files_removed"] += 1
            result["bytes_freed"] += size
        except Exception as e:
            result["errors"].append(f"_index.json: {e}")

    # Удаляем саму папку, если пустая
    if not dry_run and FULL_TEXT_DIR.exists():
        try:
            if not any(FULL_TEXT_DIR.iterdir()):
                FULL_TEXT_DIR.rmdir()
        except Exception:
            pass

    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", help="путь к analysis_v2_*.md (по умолчанию — сегодняшний)")
    ap.add_argument("--keep-full-text", action="store_true",
                    help="не удалять archive/full_text/")
    ap.add_argument("--dry-run", action="store_true",
                    help="только показать, без записи и удаления")
    ap.add_argument("--no-lint", action="store_true",
                    help="пропустить проверку на англицизмы (НЕ рекомендуется)")
    args = ap.parse_args()

    # 1. Находим v2-файл
    if args.input:
        src_md = Path(args.input)
    else:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        src_md = OUTPUT_DIR / f"analysis_v2_{today}.md"

    if not src_md.exists():
        print(f"❌ Нет файла {src_md}", file=sys.stderr)
        return 1

    # 2. ЛИНТЕР — проверка на англицизмы и кальки (Зуфар: "каждый фрагмент
    # переде публикацией нужно проверять на англицизмы и откровенно английский текст")
    if not args.no_lint:
        from lint import lint_file, BAD_TERMS, VERB_CALQUES
        issues = lint_file(src_md, check_phrases=False)
        # Берём только блокирующие проблемы (BAD_TERMS и VERB_CALQUES)
        blocking = [i for i in issues if i[3] in ("BAD_TERMS", "VERB_CALQUES")]
        if blocking:
            print(f"❌ ЛИНТЕР ЗАБЛОКИРОВАЛ ПУБЛИКАЦИЮ: {len(blocking)} плохих калек", file=sys.stderr)
            print(f"   Файл: {src_md}", file=sys.stderr)
            print(f"   Запусти `python lint.py {src_md}` для деталей", file=sys.stderr)
            print(f"   Или используй --no-lint (НЕ рекомендуется)", file=sys.stderr)
            return 1
        print(f"✅ Линтер: 0 плохих калек, OK", file=sys.stderr)
    else:
        print(f"⚠️  Линтер ПРОПУЩЕН (--no-lint)", file=sys.stderr)

    # 3. Парсим
    md_text = src_md.read_text(encoding="utf-8")
    parsed = parse_v2_digest(md_text)
    date_str = src_md.stem.replace("analysis_v2_", "")

    print(f"📄 Загружен: {src_md}", file=sys.stderr)
    print(f"   Title: {parsed['title']}", file=sys.stderr)
    print(f"   TL;DR: {len(parsed['tl_dr'])} пунктов", file=sys.stderr)
    print(f"   Links: {len(parsed['links'])}", file=sys.stderr)

    # 3. Находим vault
    vault = find_vault()
    if not vault:
        print(f"❌ Obsidian vault не найден. Проверьте пути: {VAULT_PATHS}", file=sys.stderr)
        return 1
    print(f"📂 Vault: {vault}", file=sys.stderr)

    # 4. Сохраняем в Obsidian
    if args.dry_run:
        print(f"🏃 DRY-RUN: не сохраняю", file=sys.stderr)
        ob_path = vault / BRIEFINGS_SUBDIR / f"{date_str}.md"
    else:
        ob_path = make_obsidian_note(parsed, date_str, vault, src_md)
        print(f"✅ Obsidian: {ob_path}", file=sys.stderr)

    # 5. Очистка archive/full_text/
    if args.keep_full_text:
        print("🛡 Скрапленные тексты сохранены (--keep-full-text)", file=sys.stderr)
        cleanup_result = {"files_removed": 0, "bytes_freed": 0, "errors": []}
    else:
        if args.dry_run:
            print(f"🏃 DRY-RUN: не удаляю archive/full_text/", file=sys.stderr)
            cleanup_result = cleanup_full_text(dry_run=True)
        else:
            cleanup_result = cleanup_full_text()
            print(f"🧹 Очищено: {cleanup_result['files_removed']} файлов, "
                  f"{cleanup_result['bytes_freed']//1024}KB", file=sys.stderr)
            if cleanup_result["errors"]:
                for e in cleanup_result["errors"]:
                    print(f"   ⚠ {e}", file=sys.stderr)

    # 6. Генерируем Telegram-саммари (в stdout)
    tg = make_telegram_summary(parsed, date_str, ob_path, archived=not args.keep_full_text)
    print(tg)

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
