#!/usr/bin/env python3
"""
digest.py — генерация читаемого дайджеста из собранных постов.

Читает storage/posts_YYYY-MM-DD.json, формирует output/digest_YYYY-MM-DD.md
с группировкой по категориям, top-выделением, эмодзи-маркерами для Telegram.

Использование:
    python digest.py                                    # сегодняшний файл
    python digest.py --input storage/posts_2026-06-03.json
    python digest.py --top 5                            # top-5 в каждой категории
    python digest.py --lang ru                          # только русский
    python digest.py --lang en                          # только английский
    python digest.py --min-priority high                # только high
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from dateutil import parser as dateparser
import yaml
import re


# AI/B2B ключевые слова для фильтрации. Регистронезависимо, по подстроке.
# Если пост содержит хотя бы 1 слово из списка — проходит (score >= 1).
# Если НЕ содержит — отбрасывается как шум.
AI_B2B_KEYWORDS = [
    # LLM / модели / компании
    "ai", "llm", "gpt", "claude", "gemini", "mistral", "llama", "deepseek",
    "openai", "anthropic", "google deepmind", "deepmind", "hugging face",
    "nvidia", "meta ai", "microsoft ai", "apple intelligence",
    "model", "training", "fine-tun", "rlhf", "rag", "agent", "agents",
    "agentic", "embedding", "transformer", "diffusion", "multimodal",
    "inference", "context window", "token", "reasoning", "chain-of-thought",
    "safety", "alignment", "evaluat", "benchmark", "sota", "state-of-the-art",
    # стартапы / венчур
    "startup", "startups", "funding", "raised", "raise", "series a",
    "series b", "series c", "round", "valuation", "ipo", "acqui", "acquisition",
    "invest", "investor", "vc ", "venture", "seed", "pre-seed", "yc ",
    "y combinator", "sequoia", "a16z", "andreessen", "andreessen horowitz",
    "benchmark capital", "accel", "accel partners", "tiger global",
    "cohere", "scale ai", "stability", "midjourney", "runway", "perplexity",
    "character.ai", "inflection", "elevenlabs", "suno", "udio", "pika",
    # B2B / enterprise
    "b2b", "saas", "enterprise", "automation", "workflow", "api",
    "platform", "integration", "deploy", "production", "customer",
    "revenue", "arr", "mrr", "growth", "churn", "retention",
    # кириллица — для русских источников
    "ии", " llm ", " агент", " стартап", " раунд", " инвестиц",
    " венчур", " финансиров", " сделка", " привлеч", " оценка",
    "искусственный интеллект", "нейросет", " машинное обучение",
    "ml ", "data science", "большие языковые модели", "генератив",
]

# Компилируем regex один раз
_AI_B2B_RE = re.compile("|".join(re.escape(k) for k in AI_B2B_KEYWORDS), re.IGNORECASE)


def ai_relevance(post: dict) -> tuple[int, list[str]]:
    """
    Возвращает (score, matched_keywords) — насколько пост релевантен AI/B2B-тематике.
    Считаем совпадения в title + summary + tags.
    score = 0 → шум (отбрасываем в строгом режиме)
    score = 1 → слабо релевантен
    score >= 2 → релевантен
    """
    text = " ".join([
        post.get("title", ""),
        post.get("summary", ""),
        " ".join(post.get("tags", []) or []),
        post.get("source", ""),  # имя источника тоже учитываем
    ])
    matches = _AI_B2B_RE.findall(text)
    # уникальные
    unique = list({m.lower().strip() for m in matches})
    return len(unique), unique


CATEGORY_LABELS = {
    "ai_research": "🤖 AI / ML Research",
    "startups_vc": "💼 Стартапы и Венчур",
    "ru_ai": "🇷🇺 AI / ML (рус)",
    "ru_startups": "🇷🇺 Стартапы (рус)",
    "ru_corp_it": "🇷🇺 Корпоративный IT",
    "aggregators": "📊 Агрегаторы и обзоры",
}

PRIORITY_EMOJI = {
    "high": "🔥",
    "medium": "•",
    "low": "·",
}

PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def load_posts(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def fmt_date(iso: str) -> str:
    if not iso:
        return "—"
    try:
        dt = dateparser.parse(iso)
        return dt.strftime("%d.%m %H:%M")
    except Exception:
        return iso[:16].replace("T", " ")


def age_hours(iso: str) -> float | None:
    if not iso:
        return None
    try:
        dt = dateparser.parse(iso)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    except Exception:
        return None


def post_score(p: dict) -> tuple:
    """Сортируем посты: свежесть + приоритет источника."""
    age = age_hours(p.get("published"))
    # Чем свежее, тем меньше значение (сортируем по возрастанию)
    freshness = age if age is not None else 999
    priority_weight = PRIORITY_ORDER.get(p.get("priority"), 9) * 2
    return (freshness + priority_weight, PRIORITY_ORDER.get(p.get("priority"), 9))


def select_top(posts: list[dict], top: int, per_source_cap: int | None = None) -> list[dict]:
    """Сортируем по свежести + приоритету, опционально ограничиваем кол-во с одного источника."""
    sorted_posts = sorted(posts, key=post_score)
    if per_source_cap is None:
        return sorted_posts[:top]
    # Greedy: проходим по отсортированным, берём пока не превысили cap на источник
    out = []
    seen_src = {}
    for p in sorted_posts:
        src = p.get("source", "?")
        if seen_src.get(src, 0) >= per_source_cap:
            continue
        out.append(p)
        seen_src[src] = seen_src.get(src, 0) + 1
        if len(out) >= top:
            break
    return out


def make_telegram_summary(posts: list[dict], top_in_cat: int = 5, per_source_cap: int | None = None) -> str:
    """Короткая версия для Telegram (≤ 4000 символов)."""
    lines = []
    by_cat = defaultdict(list)
    for p in posts:
        by_cat[p["category"]].append(p)

    # Сводка по источникам
    src_counter = Counter(p["source"] for p in posts)
    top_sources = src_counter.most_common(5)

    lines.append(f"📡 *AI-Digest* — {datetime.now(timezone.utc).strftime('%d.%m.%Y')}")
    lines.append(f"📊 Постов: *{len(posts)}* | Источников: *{len(src_counter)}*")
    lines.append(f"🏆 Топ-источники: {', '.join(f'{n}({c})' for n, c in top_sources)}")
    lines.append("")

    for cat_key, cat_label in CATEGORY_LABELS.items():
        cat_posts = by_cat.get(cat_key, [])
        if not cat_posts:
            continue
        top = select_top(cat_posts, top_in_cat, per_source_cap=per_source_cap)
        lines.append(f"*{cat_label}* ({len(cat_posts)} постов)")
        for p in top:
            age = age_hours(p.get("published"))
            age_str = f"{int(age)}ч" if age is not None and age < 72 else fmt_date(p.get("published"))
            emoji = PRIORITY_EMOJI.get(p["priority"], "·")
            title = p["title"][:100] + ("…" if len(p["title"]) > 100 else "")
            # помечаем AI-релевантность, если фильтр включён
            rel_mark = ""
            if "_ai_relevance" in p:
                score = p["_ai_relevance"]
                if score >= 5:
                    rel_mark = " 🎯"
                elif score >= 2:
                    rel_mark = " ✓"
                else:
                    rel_mark = ""
            lines.append(f"{emoji} [{title}]({p['url']}){rel_mark} — {p['source']} ({age_str})")
        lines.append("")

    return "\n".join(lines)


def make_html(posts: list[dict], top_per_cat: int = 8, min_priority: str | None = None,
               sources_meta: list[dict] | None = None, title: str = "AI-Digest") -> str:
    """Генерирует автономную HTML-страницу с тёмной темой."""
    if min_priority:
        threshold = PRIORITY_ORDER[min_priority]
        posts = [p for p in posts if PRIORITY_ORDER.get(p["priority"], 9) <= threshold]

    by_cat = defaultdict(list)
    for p in posts:
        by_cat[p["category"]].append(p)

    today = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")

    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — {today}</title>
<style>
:root {{
  --bg: #0f1419;
  --surface: #1a2028;
  --border: #2d3748;
  --text: #e6e8eb;
  --text-dim: #8b95a5;
  --accent: #5b9eff;
  --high: #ff6b6b;
  --medium: #ffd93d;
  --low: #6c757d;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  line-height: 1.6;
  padding: 2rem 1rem;
  max-width: 1200px;
  margin: 0 auto;
}}
header {{
  text-align: center;
  margin-bottom: 3rem;
  padding-bottom: 2rem;
  border-bottom: 1px solid var(--border);
}}
header h1 {{
  font-size: 2.5rem;
  background: linear-gradient(135deg, #5b9eff 0%, #c084fc 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  margin-bottom: 0.5rem;
}}
header .meta {{ color: var(--text-dim); font-size: 0.95rem; }}
.summary-cards {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 1rem;
  margin: 2rem 0;
}}
.card {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1.5rem;
  text-align: center;
}}
.card .num {{ font-size: 2.5rem; font-weight: 700; color: var(--accent); }}
.card .lbl {{ color: var(--text-dim); font-size: 0.85rem; text-transform: uppercase; }}
section.category {{
  margin: 2.5rem 0;
}}
section.category > h2 {{
  font-size: 1.5rem;
  margin-bottom: 1rem;
  padding-bottom: 0.5rem;
  border-bottom: 2px solid var(--border);
}}
article.post {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-left: 3px solid var(--low);
  border-radius: 8px;
  padding: 1rem 1.25rem;
  margin-bottom: 0.75rem;
  transition: transform 0.1s, border-color 0.2s;
}}
article.post:hover {{ transform: translateX(4px); border-color: var(--accent); }}
article.post.priority-high {{ border-left-color: var(--high); }}
article.post.priority-medium {{ border-left-color: var(--medium); }}
article.post.priority-low {{ border-left-color: var(--low); }}
article.post h3 {{ font-size: 1.05rem; margin-bottom: 0.4rem; line-height: 1.4; }}
article.post h3 a {{ color: var(--text); text-decoration: none; }}
article.post h3 a:hover {{ color: var(--accent); }}
.post-meta {{
  color: var(--text-dim);
  font-size: 0.85rem;
  margin-bottom: 0.5rem;
}}
.post-summary {{
  color: var(--text);
  font-size: 0.95rem;
  padding-left: 1rem;
  border-left: 2px solid var(--border);
  margin-top: 0.5rem;
}}
.priority-dot {{
  display: inline-block;
  width: 8px; height: 8px;
  border-radius: 50%;
  margin-right: 0.4rem;
  vertical-align: middle;
}}
.priority-dot.high {{ background: var(--high); }}
.priority-dot.medium {{ background: var(--medium); }}
.priority-dot.low {{ background: var(--low); }}
.toc {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 1rem 1.5rem;
  margin: 1.5rem 0;
}}
.toc a {{ color: var(--accent); text-decoration: none; display: block; padding: 0.2rem 0; }}
.toc a:hover {{ text-decoration: underline; }}
.sources {{
  margin-top: 4rem;
  padding-top: 2rem;
  border-top: 1px solid var(--border);
}}
.sources h2 {{ margin-bottom: 1.5rem; }}
.sources .src-group {{ margin-bottom: 1.5rem; }}
.sources .src-group h3 {{ font-size: 1.1rem; margin-bottom: 0.5rem; }}
.sources ul {{ list-style: none; padding-left: 0; }}
.sources li {{ padding: 0.2rem 0; font-size: 0.9rem; }}
.sources a {{ color: var(--text-dim); text-decoration: none; font-family: monospace; }}
.sources a:hover {{ color: var(--accent); }}
footer {{
  text-align: center;
  color: var(--text-dim);
  font-size: 0.85rem;
  margin-top: 3rem;
  padding-top: 1.5rem;
  border-top: 1px solid var(--border);
}}
</style>
</head>
<body>
<header>
  <h1>📡 {title}</h1>
  <div class="meta">{today}</div>
</header>

<div class="summary-cards">
  <div class="card"><div class="num">{len(posts)}</div><div class="lbl">постов</div></div>
  <div class="card"><div class="num">{len(set(p["source"] for p in posts))}</div><div class="lbl">источников</div></div>
  <div class="card"><div class="num">{sum(1 for p in posts if p.get("priority") == "high")}</div><div class="lbl">high priority</div></div>
  <div class="card"><div class="num">{len([p for p in posts if "ru" in (p.get("language") or "")])}</div><div class="lbl">на русском</div></div>
</div>
"""

    # Оглавление
    html += '<nav class="toc"><strong>Содержание:</strong>\n'
    for cat_key, cat_label in CATEGORY_LABELS.items():
        if cat_key in by_cat and by_cat[cat_key]:
            html += f'<a href="#cat-{cat_key}">{cat_label} ({len(by_cat[cat_key])})</a>\n'
    html += '</nav>\n'

    # По категориям
    for cat_key, cat_label in CATEGORY_LABELS.items():
        cat_posts = by_cat.get(cat_key, [])
        if not cat_posts:
            continue
        top = select_top(cat_posts, top_per_cat)
        html += f'<section class="category" id="cat-{cat_key}">\n'
        html += f'<h2>{cat_label} <span style="color:var(--text-dim);font-size:0.8em;">({len(cat_posts)} постов)</span></h2>\n'

        for p in top:
            priority = p.get("priority", "low")
            age = age_hours(p.get("published"))
            if age is not None and age < 24:
                age_str = f"🟢 {int(age)}ч"
            elif age is not None and age < 72:
                age_str = f"🟡 {int(age)}ч"
            else:
                age_str = f"⚪ {fmt_date(p.get('published'))}"

            title_safe = p["title"].replace("<", "&lt;").replace(">", "&gt;")
            url = p["url"]
            source = p.get("source", "").replace("<", "&lt;").replace(">", "&gt;")
            summary = p.get("summary", "")
            if summary:
                summary = re.sub(r"<[^>]+>", "", summary)
                summary = re.sub(r"\s+", " ", summary).strip()[:400]
                summary = summary.replace("<", "&lt;").replace(">", "&gt;")

            html += f'<article class="post priority-{priority}">\n'
            html += f'  <h3><a href="{url}" target="_blank" rel="noopener">{title_safe}</a></h3>\n'
            html += f'  <div class="post-meta">'
            html += f'<span class="priority-dot {priority}"></span>'
            html += f'{source} · {age_str}</div>\n'
            if summary:
                html += f'  <div class="post-summary">{summary}…</div>\n'
            html += '</article>\n'
        html += '</section>\n'

    # Источники
    if sources_meta:
        html += '<section class="sources">\n<h2>📡 Источники</h2>\n'
        by_src_cat = defaultdict(list)
        for s in sources_meta:
            by_src_cat[s["category"]].append(s)
        for cat_key, cat_label in CATEGORY_LABELS.items():
            items = by_src_cat.get(cat_key, [])
            if not items:
                continue
            html += f'<div class="src-group"><h3>{cat_label} ({len(items)})</h3><ul>\n'
            for s in items:
                url = s["url"]
                name = s["name"].replace("<", "&lt;").replace(">", "&gt;")
                html += f'<li><span class="priority-dot {s.get("priority", "low")}"></span> '
                html += f'<strong>{name}</strong> — <a href="{url}" target="_blank">{url}</a></li>\n'
            html += '</ul></div>\n'
        html += '</section>\n'

    html += f"""
<footer>
Сгенерировано автоматически из {len(set(p["source"] for p in posts))} RSS-источников · {today}
</footer>
</body>
</html>
"""
    return html


def make_markdown(posts: list[dict], top_per_cat: int = 10, min_priority: str | None = None,
                  sources_meta: list[dict] | None = None) -> str:
    """Полная версия дайджеста в Markdown."""
    if min_priority:
        threshold = PRIORITY_ORDER[min_priority]
        posts = [p for p in posts if PRIORITY_ORDER.get(p["priority"], 9) <= threshold]

    by_cat = defaultdict(list)
    for p in posts:
        by_cat[p["category"]].append(p)

    src_counter = Counter(p["source"] for p in posts)
    lang_counter = Counter(p["language"] for p in posts)

    lines = []
    lines.append(f"# 📡 AI-Digest — {datetime.now(timezone.utc).strftime('%d.%m.%Y')}")
    lines.append("")
    lines.append(f"**Постов:** {len(posts)} | **Источников с постами:** {len(src_counter)}")
    lines.append(f"**Языки:** {dict(lang_counter)}")
    lines.append(f"**Сгенерировано:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")

    # Оглавление
    lines.append("## 📑 Содержание")
    for cat_key, cat_label in CATEGORY_LABELS.items():
        if cat_key in by_cat and by_cat[cat_key]:
            n = len(by_cat[cat_key])
            lines.append(f"- [{cat_label}](#{cat_key.replace('_', '-')}) — {n} постов")
    lines.append("")

    # Top-5 горячих (high priority + свежие)
    hot = select_top([p for p in posts if p["priority"] == "high"], 5)
    if hot:
        lines.append("## 🔥 Горячее за день")
        for p in hot:
            age = age_hours(p.get("published"))
            age_str = f"{int(age)}ч" if age is not None and age < 72 else fmt_date(p.get("published"))
            lines.append(f"- **[{p['title']}]({p['url']})**")
            lines.append(f"  {p['source']} · {p.get('category', '?')} · {age_str}")
        lines.append("")

    # По категориям
    for cat_key, cat_label in CATEGORY_LABELS.items():
        cat_posts = by_cat.get(cat_key, [])
        if not cat_posts:
            continue
        top = select_top(cat_posts, top_per_cat)
        lines.append(f"## {cat_label}")
        lines.append(f"*{len(cat_posts)} постов, показано топ-{len(top)}*")
        lines.append("")
        for p in top:
            age = age_hours(p.get("published"))
            if age is not None and age < 24:
                age_str = f"🟢 {int(age)}ч"
            elif age is not None and age < 72:
                age_str = f"🟡 {int(age)}ч"
            else:
                age_str = f"⚪ {fmt_date(p.get('published'))}"
            emoji = PRIORITY_EMOJI.get(p["priority"], "·")
            lines.append(f"### {emoji} [{p['title']}]({p['url']})")
            lines.append(f"*{p['source']} · {age_str}*")
            if p.get("summary"):
                lines.append(f"> {p['summary'][:400]}")
            if p.get("author"):
                lines.append(f"_автор: {p['author']}_")
            lines.append("")

    # Полный список в конце
    lines.append("## 📚 Полный список постов")
    lines.append(f"Всего: {len(posts)}")
    lines.append("")
    by_date = sorted(posts, key=lambda p: p.get("published") or "", reverse=True)
    for p in by_date:
        emoji = PRIORITY_EMOJI.get(p["priority"], "·")
        lines.append(f"- {emoji} [{p['title'][:80]}]({p['url']}) — _{p['source']}_, {fmt_date(p.get('published'))}")
    lines.append("")

    # Секция со ссылками на сами RSS-источники (если передали метаданные)
    if sources_meta:
        lines.append("---")
        lines.append("")
        lines.append("## 📡 Источники дайджеста (RSS-ленты)")
        lines.append(f"Всего активных: **{len(sources_meta)}**")
        lines.append("")
        by_src_cat = defaultdict(list)
        for s in sources_meta:
            by_src_cat[s["category"]].append(s)
        for cat_key, cat_label in CATEGORY_LABELS.items():
            items = by_src_cat.get(cat_key, [])
            if not items:
                continue
            lines.append(f"### {cat_label} ({len(items)})")
            for s in items:
                p_emoji = PRIORITY_EMOJI.get(s.get("priority", "low"), "·")
                lines.append(f"- {p_emoji} **{s['name']}** — [{s['url']}]({s['url']})")
                if s.get("note"):
                    note = s["note"].replace("[DEAD 2026-06] ", "").replace("[UPDATED 2026-06] ", "").strip()
                    if note:
                        lines.append(f"  - _{note}_")
            lines.append("")

    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", help="путь к posts JSON (иначе — сегодняшний)")
    ap.add_argument("--output-dir", default="output")
    ap.add_argument("--top", type=int, default=10, help="сколько постов в каждой категории в полной версии")
    ap.add_argument("--top-telegram", type=int, default=4, help="сколько постов в каждой категории в Telegram-версии")
    ap.add_argument("--lang", choices=["en", "ru", "all"], default="all")
    ap.add_argument("--min-priority", choices=["high", "medium", "low"], default=None)
    ap.add_argument("--telegram-only", action="store_true", help="только Telegram-версия в stdout")
    ap.add_argument("--trial", action="store_true",
                    help="пробный компактный режим: top-4, без STALE-источников, с блоком RSS-ссылок")
    ap.add_argument("--ai-filter", action="store_true",
                    help="AI-фильтр по ключевым словам: оставляет только релевантные AI/B2B посты")
    ap.add_argument("--strict", action="store_true",
                    help="строгий режим: --ai-filter + только high/medium priority + лимит 3 поста/источник в Telegram")
    ap.add_argument("--per-source-cap", type=int, default=None,
                    help="макс. постов с одного источника в Telegram-версии (например 3)")
    ap.add_argument("--format", choices=["md", "html", "both"], default="both",
                    help="формат вывода: md (только markdown), html, или both (по умолчанию)")
    ap.add_argument("--config", default="sources.yaml", help="путь к sources.yaml")
    args = ap.parse_args()

    base = Path(__file__).parent
    if args.input:
        in_path = Path(args.input)
    else:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        in_path = base / "storage" / f"posts_{today}.json"

    if not in_path.exists():
        print(f"❌ Нет файла {in_path}. Сначала запусти fetch.py", file=sys.stderr)
        return 1

    data = load_posts(in_path)
    posts = data["posts"]
    print(f"📄 Прочитано {len(posts)} постов из {in_path}", file=sys.stderr)

    # Подгружаем Reddit-посты из Xpoz, если есть
    xpoz_path = in_path.parent / f"xpoz_posts_{in_path.stem[-10:]}.json"
    if xpoz_path.exists():
        try:
            xpoz_data = load_posts(xpoz_path)
            xpoz_posts = xpoz_data.get("posts", [])
            if xpoz_posts:
                # Добавляем source_type для отличия от RSS
                for p in xpoz_posts:
                    p["source_type"] = "xpoz_reddit"
                posts.extend(xpoz_posts)
                print(f"📍 +{len(xpoz_posts)} постов из Reddit (Xpoz)", file=sys.stderr)
        except Exception as e:
            print(f"⚠️ Не удалось загрузить {xpoz_path}: {e}", file=sys.stderr)

    if args.lang != "all":
        posts = [p for p in posts if p.get("language") == args.lang]
        print(f"   После фильтра ({args.lang}): {len(posts)}", file=sys.stderr)

    if not posts:
        print("❌ Нет постов после фильтрации", file=sys.stderr)
        return 1

    # Загружаем метаданные активных источников (для блока RSS-ссылок)
    try:
        with open(base / args.config) as f:
            cfg = yaml.safe_load(f)
        sources_meta = [s for s in cfg["sources"] if s.get("enabled", True)]
    except Exception as e:
        print(f"⚠️  Не удалось прочитать {args.config}: {e}", file=sys.stderr)
        sources_meta = None

    # Параметры для пробного режима
    top = args.top
    top_tg = args.top_telegram
    min_pri = args.min_priority
    suffix = ""
    per_src_cap = args.per_source_cap
    ai_filter = args.ai_filter

    if args.strict:
        ai_filter = True
        min_pri = min_pri or "medium"
        per_src_cap = per_src_cap or 3
        suffix = "_strict"
        print(f"🎯 Строгий режим: AI-фильтр + priority≥{min_pri} + cap {per_src_cap}/источник", file=sys.stderr)
    elif ai_filter:
        suffix = "_ai"
        print(f"🤖 AI-фильтр включён", file=sys.stderr)

    if args.trial:
        top = min(top, 4)
        top_tg = min(top_tg, 3)
        min_pri = min_pri or "medium"
        suffix = (suffix + "_trial") if suffix else "_trial"
        print(f"🧪 Пробный режим: top-{top}, priority≥{min_pri}", file=sys.stderr)

    # Применяем AI-фильтр
    if ai_filter:
        before = len(posts)
        scored = [(ai_relevance(p), p) for p in posts]
        kept = [(s, p) for s, p in scored if s[0] > 0]
        posts = [p for _, p in kept]
        # Помечаем score для отображения в дайджесте
        for s, p in kept:
            p["_ai_relevance"] = s[0]
            p["_ai_keywords"] = s[1]
        print(f"   AI-фильтр: {before} → {len(posts)} ({before - len(posts)} отсеяно)", file=sys.stderr)
        if not posts:
            print("❌ Все посты отсеяны AI-фильтром. Слайнь через --ai-filter=false", file=sys.stderr)
            return 1

    out_dir = base / args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Telegram-версия (короткая, для отправки в мессенджер)
    tg_text = make_telegram_summary(posts, top_in_cat=top_tg, per_source_cap=per_src_cap)
    tg_path = out_dir / f"digest_{today}{suffix}_telegram.md"
    with open(tg_path, "w") as f:
        f.write(tg_text)
    print(f"💬 Telegram-версия: {tg_path} ({len(tg_text)} chars)", file=sys.stderr)

    if args.telegram_only:
        print(tg_text)
        return 0

    # Полная Markdown-версия
    if args.format in ("md", "both"):
        md_text = make_markdown(posts, top_per_cat=top, min_priority=min_pri, sources_meta=sources_meta)
        md_path = out_dir / f"digest_{today}{suffix}.md"
        with open(md_path, "w") as f:
            f.write(md_text)
        print(f"📄 Полная MD: {md_path} ({len(md_text)} chars)", file=sys.stderr)

    # HTML-версия
    if args.format in ("html", "both"):
        html_text = make_html(posts, top_per_cat=top, min_priority=min_pri,
                              sources_meta=sources_meta, title=f"AI-Digest — {today}{suffix}")
        html_path = out_dir / f"digest_{today}{suffix}.html"
        with open(html_path, "w") as f:
            f.write(html_text)
        print(f"🌐 HTML: {html_path} ({len(html_text)//1024}KB)", file=sys.stderr)

    print()
    print(tg_text)


if __name__ == "__main__":
    sys.exit(main() or 0)
