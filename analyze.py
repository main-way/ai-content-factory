#!/usr/bin/env python3
"""analyze.py — выжимка всех постов для быстрого чтения человеком/LLM."""
import json
import re
import sys
from collections import defaultdict, Counter
from pathlib import Path
from html import unescape

HTML_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
AI_B2B_KW = re.compile(
    r"\b(ai|llm|gpt|claude|gemini|mistral|llama|deepseek|openai|anthropic|"
    r"deepmind|nvidia|meta ai|microsoft|agent|agents|agentic|rag|"
    r"startup|startups|funding|raised|raise|round|valuation|series [a-d]|"
    r"ipo|acqui|venture|seed|yc |y combinator|sequoia|a16z|andreessen|"
    r"crunchbase|cbinsights|enterprise|saas|automation|workflow|api|"
    r"ии|llm|стартап|раунд|инвестиц|венчур|сделка|привлеч|оценка|"
    r"искусственный интеллект|нейросет|машинное обучение|ml|data science|"
    r"генератив)\b", re.IGNORECASE
)


def clean(text: str, n: int = 250) -> str:
    if not text:
        return ""
    text = unescape(HTML_RE.sub(" ", text))
    text = WS_RE.sub(" ", text).strip()
    if len(text) > n:
        text = text[:n].rsplit(" ", 1)[0] + "…"
    return text


def main():
    if len(sys.argv) > 1:
        in_path = Path(sys.argv[1])
    else:
        in_path = Path("storage/posts_2026-06-03.json")

    data = json.loads(in_path.read_text())
    posts = data["posts"]

    # Релевантность
    def score(p):
        text = (p.get("title", "") + " " + p.get("summary", ""))
        return len(set(m.lower() for m in AI_B2B_KW.findall(text)))

    for p in posts:
        p["_score"] = score(p)

    # Группировка
    by_cat = defaultdict(list)
    for p in posts:
        by_cat[p["category"]].append(p)

    cat_order = ["ai_research", "startups_vc", "aggregators",
                 "ru_ai", "ru_startups", "ru_corp_it"]
    cat_label = {
        "ai_research": "AI / ML RESEARCH",
        "startups_vc": "STARTUPS & VC",
        "aggregators": "AGGREGATORS",
        "ru_ai": "AI / ML (RU)",
        "ru_startups": "СТАРТАПЫ (RU)",
        "ru_corp_it": "КОРП. IT (RU)",
    }

    out = []
    out.append(f"АНАЛИТИЧЕСКАЯ ВЫЖИМКА — {len(posts)} постов, {len(set(p['source'] for p in posts))} источников")
    out.append("=" * 80)
    out.append("")

    for cat in cat_order:
        items = by_cat.get(cat, [])
        if not items:
            continue
        # сортируем по score (убывание), потом по дате (свежие)
        items.sort(key=lambda p: (-p["_score"], -(0 if not p.get("published") else __import__('dateutil').parser.parse(p["published"]).timestamp())))
        out.append(f"\n## {cat_label[cat]} ({len(items)} постов)")
        out.append("-" * 80)
        # Top 30 в каждой категории для обзора
        for i, p in enumerate(items[:50], 1):
            stars = "🎯" * min(p["_score"], 5) if p["_score"] > 0 else "  "
            title = p["title"][:90]
            summary = clean(p.get("summary", ""), 180)
            date = p.get("published", "")[:16].replace("T", " ")
            out.append(f"\n[{i:2d}] {stars} {title}")
            out.append(f"     {p['source']} | {date}")
            if summary:
                out.append(f"     → {summary}")
            out.append(f"     {p['url']}")

    Path("output").mkdir(exist_ok=True)
    out_path = Path("output/posts_brief.md")
    out_path.write_text("\n".join(out))
    print(f"📄 Краткая выжимка: {out_path} ({out_path.stat().st_size//1024}KB)")
    print(f"   Строк: {len(out)}")


if __name__ == "__main__":
    main()
