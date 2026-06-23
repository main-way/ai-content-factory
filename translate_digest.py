#!/usr/bin/env python3
"""
translate_digest.py — перевод AI-Digest на русский.

Берёт digest_YYYY-MM-DD.md, переводит заголовки и описания постов
через OpenRouter API, сохраняет результат в _ru.md.

Использование:
    python translate_digest.py output/digest_2026-06-18.md
    python translate_digest.py output/digest_2026-06-18.md --batch 25
    python translate_digest.py output/digest_2026-06-18.md --model anthropic/claude-sonnet-4
    python translate_digest.py output/digest_2026-06-18.md --dry-run
"""

import argparse
import html
import os
import re
import sys
import time
from pathlib import Path

# ── Env ──────────────────────────────────────────────────────────────────────
import _env as _env_module
_env_module._()

# ── Конфиг ──────────────────────────────────────────────────────────────────
API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
API_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL   = "openai/gpt-4o-mini"
BATCH   = 15          # постов за один API-вызов
MAX_TOKENS = 4000
TIMEOUT    = 60       # секунд на один запрос
# ────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Ты — профессиональный переводчик IT/AI-контента на русский язык.

ПЕРЕВЕДИ на русский:
- Заголовок (title) поста — лаконично, без потери смысла
- Описание (description/summary) поста — сохраняя суть

ФОРМАТ ОТВЕТА (строго JSON):
{
  "posts": [
    {"title": "Переведённый заголовок", "description": "Переведённое описание"},
    ...
  ]
}

ПРАВИЛА:
- Переводи ТОЛЬКО title и description — НЕ трогай ссылки, метаданные, эмодзи, имя автора
- Description: если оно на русском — оставь как есть
- Описания длиной менее 10 символов или «Comments» — пропускай (ставь null)
- Иероглифы (китайские, японские, корейские) — транслитерируй на русский
- HTML-сущности (&amp;, &quot;, &#8217;, &#8230; и т.д.) — декодируй перед переводом
- Сохраняй профессиональный тон: для TechCrunch/Towards Data Science — информативный стиль
- Описания новостей (Rivian, Bernie Sanders, и т.д.) — передавай суть кратко
- Ошибки и неточности в исходном тексте — исправляй в переводе
"""


def decode_html(text: str) -> str:
    """Декодирует HTML-сущности."""
    return html.unescape(text)


def extract_posts(content: str) -> list[dict]:
    """
    Извлекает посты из markdown-файла построчно.
    Структура поста:
      ### 🔥 [Title](URL)        ← уровень 3, эмодзи-маркер, ссылка
      *Source · 🟢 Xч*          ← мета-строка
      > Description               ← описание (опционально)
      _автор: Name_              ← автор (опционально)

    Возвращает список dict: {emoji, title, url, meta, description, author}
    """
    lines = content.split('\n')
    posts = []
    i = 0

    while i < len(lines):
        line = lines[i]

        # Ищем заголовок поста: ### 🔥 [Title](URL) или ### [Title](URL)
        m = re.match(r'(#{3})\s+([^\[]*?)\[([^\]]+)\]\((https?://[^\)]+)\)', line)
        if not m:
            i += 1
            continue

        level    = m.group(1)   # ###
        emoji    = m.group(2)    # "🔥 " или "" или "• "
        title    = m.group(3)   # чистый заголовок
        url      = m.group(4)    # URL

        i += 1
        if i >= len(lines):
            break

        # Следующая строка: *Meta*
        meta = ""
        meta_m = re.match(r'\*(.+?)\*', lines[i])
        if meta_m:
            meta = meta_m.group(1)
            i += 1

        # Следующая строка: > Description
        description = ""
        if i < len(lines) and lines[i].startswith('>'):
            description = lines[i][1:].strip()
            i += 1

        # Следующая строка: _автор: Name_ (опционально)
        author = ""
        if i < len(lines) and lines[i].startswith('_') and 'автор' in lines[i]:
            author = lines[i].strip('_').strip()
            i += 1

        # Пропускаем секции (## вместо ###) и "Горячее" (не содержит URL)
        if '://' not in url:
            continue

        posts.append({
            "emoji":       emoji,      # "🔥 " или "• " и т.д.
            "title":       decode_html(title),
            "url":         url,
            "meta":        meta,
            "description": decode_html(description),
            "author":      author,
        })

    return posts


def build_section_pattern() -> re.Pattern:
    """Паттерн для заголовков секций."""
    return re.compile(r'^(#{1,2})\s+(.+?)\s*\n', re.MULTILINE)


def split_into_batches(posts: list[dict], batch_size: int) -> list[list[dict]]:
    """Разбивает посты на батчи."""
    return [posts[i:i + batch_size] for i in range(0, len(posts), batch_size)]


def translate_batch(batch: list[dict], model: str) -> list[dict]:
    """Отправляет батч в OpenRouter API и возвращает переведённые посты."""
    import json, urllib.request

    # Формируем промпт
    examples = []
    for p in batch:
        desc = p["description"] if (
            p.get("description") and
            len(p["description"]) >= 10 and
            p["description"].lower() != "comments"
        ) else None
        examples.append({
            "title": p["title"],
            "description": desc,
        })

    user_prompt = json.dumps({"posts": examples}, ensure_ascii=False, indent=2)

    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Переведи следующие посты:\n\n{user_prompt}"},
        ],
        "max_tokens": MAX_TOKENS,
        "temperature": 0.3,
    }).encode("utf-8")

    req = urllib.request.Request(
        API_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://ai-digest.local",
            "X-Title": "AI-Digest Translator",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            result = json.load(resp)
            raw = result["choices"][0]["message"]["content"]

        # Извлекаем JSON из ответа (может быть обёрнут в ```json)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r'^```json?\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)

        data = json.loads(raw)
        return data.get("posts", [])

    except Exception as e:
        print(f"    ⚠️  API error: {e}", file=sys.stderr)
        # Возвращаем null-ы чтобы не сломать порядок
        return [{"title": None, "description": None}] * len(batch)


def reconstruct_markdown(posts: list[dict], translations: list[dict]) -> str:
    """Собирает markdown обратно с переведёнными title/description."""
    lines = []
    for p, t in zip(posts, translations):
        ru_title = t.get("title") or p["title"]  # fallback
        ru_desc  = t.get("description") or p["description"]

        # Собираем блок поста
        heading_level = "###" if p["emoji"] else "###"
        title_part = f"{p['emoji']}[{ru_title}]({p['url']})" if p["emoji"] else f"[{ru_title}]({p['url']})"
        lines.append(f"{heading_level} {title_part}")
        lines.append(f"*{p['meta']}*")
        if ru_desc:
            lines.append(f"> {ru_desc}")
        if p["author"]:
            lines.append(f"_{p['author']}_")
        lines.append("")

    return "\n".join(lines)


def translate_file(input_path: str, output_path: str = None,
                   batch_size: int = BATCH, model: str = MODEL,
                   dry_run: bool = False):
    """Основная функция: читает файл, переводит, пишет результат."""

    input_path = Path(input_path)
    if not input_path.exists():
        print(f"❌ Файл не найден: {input_path}", file=sys.stderr)
        sys.exit(1)

    if output_path is None:
        output_path = str(input_path).replace(".md", "_ru.md")

    content = input_path.read_text(encoding="utf-8")

    # Декодируем HTML-сущности сразу во всём файле
    content = decode_html(content)

    # Извлекаем все посты
    posts = extract_posts(content)
    print(f"📄 Извлечено {len(posts)} постов", file=sys.stderr)

    # Извлекаем ссылки из секции "Горячее за день"
    hot_items = []
    hot_pattern = re.compile(
        r'^(\- \*\*\[)([^\]]+)(\]\()(https?://[^\)]+)(\)\*\*\n\s+)'
        r'([^·\n]+)·\s+([\w_]+)\s+·\s+(\S+)',
        re.MULTILINE
    )
    for m in hot_pattern.finditer(content):
        hot_items.append({
            "title": decode_html(m.group(2)),
            "url": m.group(4),
            "source": m.group(6).strip(),
            "category": m.group(7).strip(),
            "time": m.group(8).strip(),
            "full_match": m.group(0),
        })
    if hot_items:
        print(f"🔥 Найдено {len(hot_items)} ссылок в 'Горячее за день'", file=sys.stderr)

    if dry_run:
        print(f"🔍 Dry-run: показываю первые 5 постов:")
        for p in posts[:5]:
            print(f"  [{p['emoji']}] {p['title'][:60]}")
            print(f"       → {p['description'][:80]}")
        return

    # Разбиваем на батчи
    batches = split_into_batches(posts, batch_size)
    print(f"🔄 {len(batches)} батчей по {batch_size} постов", file=sys.stderr)

    all_translations = []
    for i, batch in enumerate(batches):
        print(f"  Батч {i+1}/{len(batches)} ({len(batch)} постов)...", end="", flush=True)
        translations = translate_batch(batch, model)
        all_translations.extend(translations)

        # Rate limiting: 5 запросов/sec (OpenRouter free tier)
        if i < len(batches) - 1:
            time.sleep(0.25)
        print(" ✅", file=sys.stderr)

    # Переводим "Горячее за день"
    hot_translations = {}
    if hot_items:
        print(f"🔥 Перевод 'Горячее за день' ({len(hot_items)} ссылок)...", end="", flush=True)
        hot_batches = split_into_batches(
            [{"title": h["title"], "description": None} for h in hot_items],
            10
        )
        for hb in hot_batches:
            r = translate_batch([{"title": t["title"], "description": None} for t in hb], model)
            for orig, trans in zip(hb, r):
                if trans.get("title"):
                    hot_translations[orig["title"]] = trans["title"]
        print(" ✅", file=sys.stderr)

        # Подменяем оригинальные title на русские в content
        for h in hot_items:
            ru_title = hot_translations.get(h["title"], h["title"])
            old = f"- **[{h['title']}]({h['url']})**\n  {h['source']} · {h['category']} · {h['time']}"
            new = f"- **[{ru_title}]({h['url']})**\n  {h['source']} · {h['category']} · {h['time']}"
            content = content.replace(old, new)

    # Реконструкция: собираем новый markdown
    result_lines = []
    post_idx = 0
    skip_lines = 0   # сколько строк пропустить (мы их уже вставили из перевода)

    for line in content.split('\n'):
        # Пропускаем строки оригинального поста (мы их уже вставили из перевода)
        if skip_lines > 0:
            skip_lines -= 1
            continue

        # Определяем: это заголовок поста?
        post_title_m = re.match(
            r'(#{3})\s+([^\[]*?)\[([^\]]+)\]\((https?://[^\)]+)\)',
            line
        )

        if post_title_m and '://' in post_title_m.group(4):
            # Это заголовок поста — вставляем переведённую версию
            if post_idx < len(all_translations):
                t = all_translations[post_idx]
                ru_title = t.get("title") or posts[post_idx]["title"]
                ru_desc  = t.get("description") or posts[post_idx]["description"]
                p = posts[post_idx]

                heading = "###"
                title_part = f"{p['emoji']}[{ru_title}]({p['url']})"
                result_lines.append(f"{heading} {title_part}")
                result_lines.append(f"*{p['meta']}*")
                if ru_desc:
                    result_lines.append(f"> {ru_desc}")
                if p["author"]:
                    result_lines.append(f"_{p['author']}_")
                result_lines.append("")   # пустая строка после поста

                # Считаем сколько строк нужно пропустить в оригинале
                skip_lines = 1          # meta line
                if p["description"]:
                    skip_lines += 1     # description line
                if p["author"]:
                    skip_lines += 1     # author line
            else:
                result_lines.append(line)

            post_idx += 1
        else:
            # Обычная строка (заголовок секции, содержание и т.д.)
            result_lines.append(line)

    result = "\n".join(result_lines)

    # Сохраняем
    Path(output_path).write_text(result, encoding="utf-8")
    print(f"✅ Сохранено: {output_path}", file=sys.stderr)

    # Статистика
    translated = sum(1 for t in all_translations if t.get("title"))
    print(f"📊 Переведено: {translated}/{len(posts)} постов", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Перевод AI-Digest на русский")
    parser.add_argument("input", help="Путь к digest_YYYY-MM-DD.md")
    parser.add_argument("--output", "-o", help="Выходной файл (по умолчанию: input_ru.md)")
    parser.add_argument("--batch", "-b", type=int, default=BATCH, help=f"Размер батча (default: {BATCH})")
    parser.add_argument("--model", "-m", default=MODEL, help="Модель OpenRouter")
    parser.add_argument("--dry-run", action="store_true", help="Только показать что переводить")
    args = parser.parse_args()

    translate_file(args.input, args.output, args.batch, args.model, args.dry_run)


if __name__ == "__main__":
    main()
