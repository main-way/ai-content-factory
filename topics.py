#!/usr/bin/env python3
"""
topics.py — тематические отчёты из архива.

Фильтрует архив по темам (набор ключевых слов), группирует по подтемам,
генерирует развёрнутый markdown-отчёт с кликабельными ссылками.

Использование:
    python topics.py --topic agents --days 7
    python topics.py --list                       # список доступных тем
    python topics.py --custom "ai,llm,model"      # своя тема

Встроенные темы:
    agents       — AI-агенты (agent, agentic, OpenClaw, MXC, Scout)
    funding      — Финансирование, IPO, M&A
    security     — AI-безопасность
    enterprise   — Корпоративный AI, B2B, SaaS
    research     — Исследования (arXiv, model releases)
    ru_market    — Российский AI/IT
    hardware     — Чипы, GPU, инфраструктура
"""
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dateutil import parser as dateparser

sys.path.insert(0, str(Path(__file__).parent))
from archive import Archive  # noqa: E402

BASE = Path(__file__).parent
OUTPUT_DIR = BASE / "output"

# Встроенные темы — наборы ключевых слов (EN + RU)
TOPICS = {
    "agents": {
        "name": "🤖 AI-агенты",
        "description": "Agentic AI, автономные агенты, оркестрация агентов",
        "keywords": [
            "agent", "agents", "agentic", "agent-first", "openclaw", "scout",
            "orchestration", "orchestrator", "harness", "skill graph", "skilldag",
            "tool-augmented", "tool calling", "mcp", "model context protocol",
            "langchain", "autogen", "crewai", "letta",
            "агент", "агенты", "agentic",
        ],
    },
    "funding": {
        "name": "💰 Финансирование и IPO",
        "description": "Раунды, M&A, IPO, оценки стартапов",
        "keywords": [
            "funding", "raised", "raise", "round", "valuation", "series a",
            "series b", "series c", "series d", "series e", "series f",
            "ipo", "acqui", "acquisition", "merger", "invest", "investor",
            "venture", "vc ", "seed", "pre-seed", "stake",
            " раунд", "инвестиц", "венчур", "сделка", "привлеч", "оценка",
        ],
    },
    "security": {
        "name": "🔒 AI-безопасность",
        "description": "Уязвимости, атаки, защита моделей, регулирование",
        "keywords": [
            "security", "vulnerability", "exploit", "attack", "threat",
            "safety", "alignment", "jailbreak", "prompt injection",
            "data poisoning", "adversarial", "worm",
            "уязвимость", "уязвимост", "атака", "защит", "безопасност",
        ],
    },
    "enterprise": {
        "name": "🏢 Корпоративный AI / B2B",
        "description": "Enterprise-внедрения, SaaS, B2B-инструменты",
        "keywords": [
            "enterprise", "saas", "b2b", "automation", "workflow", "api",
            "platform", "integration", "deploy", "production", "customer",
            "revenue", "arr", "mrr", "growth", "churn", "retention",
            "корпоративн", "внедрен", "платформ", "интеграц",
        ],
    },
    "research": {
        "name": "🔬 Исследования и модели",
        "description": "Новые модели, исследования, бенчмарки",
        "keywords": [
            "arxiv", "benchmark", "sota", "state-of-the-art", "reasoning",
            "multimodal", "training", "fine-tun", "rlhf", "rag",
            "context window", "embedding", "transformer", "diffusion",
            "исследован", "бенчмарк", "модель",
        ],
    },
    "ru_market": {
        "name": "🇷🇺 Российский AI-рынок",
        "description": "Все посты из русских источников",
        "keywords": [],  # специальная логика — фильтр по language=ru
        "by_language": "ru",
    },
    "hardware": {
        "name": "🖥️ AI-инфраструктура и железо",
        "description": "Чипы, GPU, дата-центры, edge AI",
        "keywords": [
            "gpu", "nvidia", "amd", "intel", "tpu", "chip", "silicon",
            "data center", "datacenter", "rack", "edge ai", "jetson",
            "compute", "infrastructure", "1mw", "megawatt",
            "чип", "дата-центр", "сервер", "инфраструкт",
        ],
    },
}


def collect_posts(arch: Archive, keywords: list[str], by_language: str | None,
                  days: int, min_priority: str | None) -> list[dict]:
    """Фильтрует архив по теме и периоду."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    if by_language:
        candidates = [p for p in arch.posts if p.get("language") == by_language]
    else:
        candidates = arch.posts

    if min_priority:
        priority_order = {"high": 0, "medium": 1, "low": 2}
        threshold = priority_order[min_priority]
        candidates = [p for p in candidates
                      if priority_order.get(p.get("priority", "low"), 2) <= threshold]

    # Фильтр по дате (если published есть)
    result = []
    for p in candidates:
        if p.get("published"):
            try:
                pub = dateparser.parse(p["published"])
                if pub.tzinfo is None:
                    pub = pub.replace(tzinfo=timezone.utc)
                if pub < since:
                    continue
            except Exception:
                pass
        # Фильтр по keywords
        if keywords:
            text = " ".join([
                p.get("title", ""),
                p.get("summary", ""),
                " ".join(p.get("tags", []) or []),
            ]).lower()
            if not any(kw.lower() in text for kw in keywords):
                continue
        result.append(p)
    return result


def group_by_subtopic(posts: list[dict]) -> dict[str, list[dict]]:
    """Простая группировка по ключевым подтемам."""
    subtopics = {
        "🔥 Главное": [],  # high priority
        "Продукты и релизы": [],
        "Сделки и инвестиции": [],
        "Исследования": [],
        "Обзоры и мнения": [],
    }
    deal_kw = ["raise", "raised", "funding", "round", "acqui", "ipo", "valuation",
               "раунд", "сделка", "инвестиц", "привлеч"]
    research_kw = ["arxiv", "research", "study", "paper", "model", "benchmark",
                   "исследован", "бенчмарк", "модель"]
    opinion_kw = ["why", "how", "what", "future", "view", "opinion", "interview",
                  "почему", "как", "что", "будущ"]

    for p in posts:
        text = " ".join([p.get("title", ""), p.get("summary", "")]).lower()
        if p.get("priority") == "high":
            subtopics["🔥 Главное"].append(p)
        elif any(kw in text for kw in deal_kw):
            subtopics["Сделки и инвестиции"].append(p)
        elif any(kw in text for kw in research_kw):
            subtopics["Исследования"].append(p)
        elif any(kw in text for kw in opinion_kw):
            subtopics["Обзоры и мнения"].append(p)
        else:
            subtopics["Продукты и релизы"].append(p)
    return subtopics


def make_topic_report(topic_key: str, posts: list[dict], topic_meta: dict,
                      days: int) -> str:
    """Генерирует markdown-отчёт по теме."""
    lines = []
    lines.append(f"# {topic_meta['name']}")
    lines.append(f"*{topic_meta['description']}*")
    lines.append("")
    lines.append(f"**Период:** {days} дней | **Постов:** {len(posts)} | "
                 f"**Источников:** {len(set(p['source'] for p in posts))}")
    if posts:
        dates = [p["published"][:10] for p in posts if p.get("published")]
        if dates:
            lines.append(f"**Период публикаций:** {min(dates)} → {max(dates)}")
    lines.append(f"**Сгенерировано:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")

    if not posts:
        lines.append("_(нет постов за выбранный период)_")
        return "\n".join(lines)

    # Топ источников
    src_counter = Counter(p["source"] for p in posts)
    lines.append("## 📡 Топ источников")
    for src, n in src_counter.most_common(10):
        lines.append(f"- **{src}** — {n} постов")
    lines.append("")

    # Группировка по подтемам
    subtopics = group_by_subtopic(posts)
    lines.append("## 📑 По подтемам")
    for st, items in subtopics.items():
        if items:
            lines.append(f"- **{st}** — {len(items)}")
    lines.append("")

    # По подтемам — развёрнуто
    for st, items in subtopics.items():
        if not items:
            continue
        lines.append(f"## {st}")
        # Сортируем по дате (свежие сверху)
        items.sort(key=lambda p: p.get("published") or "", reverse=True)
        for p in items:
            date = p.get("published", "")[:10]
            emoji = "🔥" if p.get("priority") == "high" else "•"
            title = p["title"]
            summary = p.get("summary", "")
            if summary:
                # Берём первые 2 предложения (чистим HTML)
                clean = re.sub(r"<[^>]+>", " ", summary)
                clean = re.sub(r"\s+", " ", clean).strip()
                # Разбиваем по точке и берём 1-2 первых полных предложения
                sentences = re.split(r"(?<=[.!?])\s+", clean)
                short = " ".join(sentences[:2])[:300]
                if len(short) < len(clean):
                    short += "…"
            else:
                short = ""
            lines.append(f"### {emoji} [{title}]({p['url']})")
            lines.append(f"*{p['source']} · {date}*")
            if short:
                lines.append(f"> {short}")
            lines.append("")

    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", help="название темы или кастомный набор keywords через запятую")
    ap.add_argument("--days", type=int, default=7, help="глубина периода в днях")
    ap.add_argument("--min-priority", choices=["high", "medium", "low"])
    ap.add_argument("--list", action="store_true", help="показать доступные темы")
    args = ap.parse_args()

    if args.list:
        print("📋 Доступные темы:\n")
        for key, meta in TOPICS.items():
            print(f"  {key:12s} — {meta['name']}")
            print(f"                {meta['description']}")
        print(f"\n  Кастомная тема: --topic 'слово1,слово2,слово3'")
        return 0

    if not args.topic:
        print("❌ Укажи --topic или --list", file=sys.stderr)
        return 1

    # Загружаем тему
    if args.topic in TOPICS:
        topic_meta = TOPICS[args.topic]
        keywords = topic_meta["keywords"]
        by_language = topic_meta.get("by_language")
        topic_key = args.topic
    else:
        # Кастомная тема
        keywords = [k.strip() for k in args.topic.split(",") if k.strip()]
        topic_meta = {
            "name": f"🎯 Кастомная тема: {args.topic}",
            "description": f"Посты, содержащие: {', '.join(keywords)}",
        }
        by_language = None
        topic_key = "custom"

    if not keywords and not by_language:
        print(f"❌ Тема {args.topic} не имеет keywords", file=sys.stderr)
        return 1

    arch = Archive(lazy=False)
    if arch.count() == 0:
        print("❌ Архив пуст. Сначала запусти: python archive.py --add storage/posts_*.json", file=sys.stderr)
        return 1

    posts = collect_posts(arch, keywords, by_language, args.days, args.min_priority)
    report = make_topic_report(topic_key, posts, topic_meta, args.days)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_name = f"topic_{topic_key}_{args.days}d.md" if topic_key != "custom" \
        else f"topic_custom_{args.days}d.md"
    out_path = OUTPUT_DIR / out_name
    out_path.write_text(report, encoding="utf-8")

    print(f"📊 Найдено: {len(posts)} постов", file=sys.stderr)
    print(f"📄 Отчёт: {out_path} ({len(report)//1024}KB)", file=sys.stderr)

    # Превью — топ-5 постов
    if posts:
        print(f"\n🔥 Топ-5:", file=sys.stderr)
        for p in posts[:5]:
            print(f"  • {p['title'][:90]} ({p['source']})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
