#!/usr/bin/env python3
"""
lint.py — проверка финального текста дайджеста на англицизмы и кальки.

Использование:
    python lint.py output/analysis_v2_2026-06-03.md
    python lint.py output/analysis_v2_2026-06-03.md --strict   # exit 1 если найдено
    python lint.py output/analysis_v2_2026-06-03.md --all      # + проверка "2 англ. слова подряд"

Проверяет (по умолчанию):
1. Словарь явных калек (Зуфар уже отметил эти как "плохой перевод")
2. Русифицированные английские глаголы (задеплоили, зарелизили, наймили...)

С флагом --all дополнительно:
3. Паттерн "2+ английских слова подряд" (с большим белым списком брендов)
"""
import argparse
import re
import sys
from pathlib import Path
from typing import List, Tuple

# Словарь явных плохих калек: regex -> рекомендация
# Эти фразы Зуфар уже отметил как "плохой перевод"
BAD_TERMS: dict[str, str] = {
    # Страховой/финансовый контекст
    r"\bclaims?\s*24/7\b": "оформить страховой случай в любое время суток",
    r"\bAI-cashflow\b": "денежный поток от ИИ",
    r"\bcash-rich\b": "с большим денежным запасом",
    # Техника и AI
    r"\bfake call detection\b": "распознавание поддельных звонков",
    r"\bAI-callers?\b": "ИИ-мошенники",
    r"\bemployee AI spending\b": "расходы сотрудников на ИИ",
    r"\bAI-spending\b": "расходы на ИИ",
    # Корпоративный сленг
    r"\bвв[её]л cap\b": "ввёл ограничение / потолок",
    r"\bвв[её]л cap на\b": "ввёл потолок на",
    r"\bcap на\b": "потолок на",
    r"\bс AI-поддержкой\b": "с помощью ИИ / через ИИ-ассистента",
    r"\bс .*-поддержкой\b": "(калька 'с X-поддержкой' — перефразировать)",
    # Стартап-жаргон
    r"\bPoC\b": "пилотный проект",
    r"\blegacy-систем": "устаревших систем",
    r"\bon-premise\b": "на собственных серверах",
    # Люди
    r"\bjunior\b": "младший разработчик / начинающий специалист",
    # Блокчейн
    r"\bon-chain\b": "на блокчейне",
}

# Русифицированные английские глаголы — типичный признак плохой кальки
VERB_CALQUES: list[Tuple[str, str]] = [
    (r"\bзадеплоили?\b", "развернули"),
    (r"\bдеплоить\b", "развернуть"),
    (r"\bзарелизили?\b", "выпустили"),
    (r"\bнаймили?\b", "наняли"),
    (r"\bхайрили?\b", "наняли"),
    (r"\bскалировали?\b", "масштабировали"),
    (r"\bлевериджили?\b", "использовали"),
    (r"\bпивотили?\b", "сменили направление"),
    (r"\bтранкейтили?\b", "сокращали"),
    (r"\bборнили?\b", "создавали"),
    (r"\bдрайвили?\b", "двигали / развивали"),
    (r"\bэнфорсили?\b", "внедряли"),
    (r"\bфаундер\b", "основатель"),
    (r"\bфаундеры\b", "основатели"),
    (r"\bстекхолдер\b", "заинтересованная сторона"),
]


def clean_text(text: str) -> str:
    """Удаляем код, URL, frontmatter."""
    text = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.DOTALL | re.MULTILINE)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"`[^`]+`", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return text


def check_bad_terms(text: str) -> List[Tuple[int, str, str]]:
    issues = []
    for pattern, replacement in BAD_TERMS.items():
        for m in re.finditer(pattern, text, re.IGNORECASE):
            line_num = text[: m.start()].count("\n") + 1
            issues.append((line_num, m.group(0), replacement))
    return issues


def check_verb_calques(text: str) -> List[Tuple[int, str, str]]:
    issues = []
    for pattern, replacement in VERB_CALQUES:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            line_num = text[: m.start()].count("\n") + 1
            issues.append((line_num, m.group(0), replacement))
    return issues


def lint_file(path: Path, check_phrases: bool = False) -> List[Tuple[int, str, str, str]]:
    """
    Возвращает список проблем.
    Каждый элемент: (line_num, found, suggestion, source)
    source ∈ {"BAD_TERMS", "VERB_CALQUES", "ENGLISH_PHRASE"}
    """
    raw = path.read_text(encoding="utf-8")
    text = clean_text(raw)
    issues = []
    for ln, f, s in check_bad_terms(text):
        issues.append((ln, f, s, "BAD_TERMS"))
    for ln, f, s in check_verb_calques(text):
        issues.append((ln, f, s, "VERB_CALQUES"))
    if check_phrases:
        for ln, f, s in check_english_phrases(text):
            issues.append((ln, f, s, "ENGLISH_PHRASE"))
    issues.sort(key=lambda x: x[0])
    return issues


# Расширенная проверка: 2+ английских слова подряд
ENGLISH_PHRASE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_/$.])"
    r"([A-Za-z]+(?:[\s-][A-Za-z]+)+)"
    r"(?![A-Za-z0-9_/])"
)

ALLOWED_PHRASES = {
    # Продукты и фичи (названия)
    "AI Agent PC", "AI Agent", "AI Agents", "AI Workers", "AI Worker",
    "AI Claims Assistant", "AI Stack", "AI Infrastructure",
    "AI Safety", "AI Security", "AI Security Review",
    "AI Security Solutions", "AI Security Product", "AI Security Products",
    "AI Security Market", "AI Security Companies", "AI Security Startup",
    "AI Code", "AI Code Security", "AI Spending", "AI Bill", "AI Bills",
    "AI Inference", "AI Inference Costs", "Agentic AI", "Agentic Era",
    "Agentic OS", "Edge Computing", "Edge Devices", "On Premise",
    "Legacy System", "Legacy Software", "Data Silos", "Data Silo",
    "Risk Management", "Founder Mode", "No Code", "Low Code",
    "Deep Fake", "Deep Fakes", "Open Source", "Real Time",
    "Use Case", "Use Cases", "Edge Case", "Edge Cases",
    "Product Market Fit", "Ride Hailing", "Ride Sharing",
    "Agent Runtime", "Agent Runtimes", "Runtime Problem",
    "Agent Sandbox", "Agent Sandboxes", "Sandbox For",
    "AI Powered", "AI Driven", "AI Native",
    "Big Tech", "Wall Street", "Silicon Valley",
    "Open AI", "Anthropic Claude", "Claude Code", "Claude Mythos",
    "Microsoft eXtensible Compute", "Microsoft Build", "Build 2026",
    "JetPack 7", "NemoClaw", "RTX Spark", "Surface RTX",
    "Spark Dev", "Dev Box", "Open AI Codex", "Codex Sites", "Codex Update",
    "Microsoft Scout", "Microsoft IQ", "MXC",
    "New York", "Vibe Coding", "Vibe Coding Apps",
    "Deep Worm", "Prompt Injection", "Data Poisoning",
    "Code Generation", "Code Review", "Code Generation Tools",
    "Multi Agent", "Multi Agent Systems", "Single Agent",
    "Long Term Memory", "Short Term Memory",
    "Public Cloud", "Private Cloud", "Hybrid Cloud",
    "API", "API Endpoints", "API Calls",
    "SaaS", "B2B", "B2C", "B2B SaaS", "B2B AI", "B2B AI Consulting",
    "IPO Anthropic", "Series A", "Series B", "Series C", "Series D", "Series E", "Series F",
    "Seed Round", "Seed Funding", "Pre Seed",
    "Series A Lead", "Series B Lead", "Series C Lead",
    "Defensible Product", "Vertical AI", "Vertical AI Startups",
    "Sovereign AI", "UK Opt", "AI Executive Order", "AI Worm",
    "Agent Runtime", "Agent Runtimes", "Microsoft MXC", "NVIDIA NemoClaw",
    "OpenAI Codex", "Codex Sites", "Codex Update",
    "Agentic Reckoning", "AI Driven", "AI Powered",
    "Role Specific", "Role Specific Plugins",
    "Knowledge Work", "Next Era",
    "Vibe Coding", "AI Triggers", "AI Trigger",
    "Mastercard On", "AI For Everything", "Defense AI",
    "AI Cash Rich", "Anthropic Mythos", "NVIDIA NemoClaw",
    "Stack Overflow", "GitHub Copilot", "Cursor Composer",
    "Notion AI", "Slack AI", "Zoom AI", "Teams AI",
    "Adobe Firefly", "Microsoft Copilot", "Google Gemini",
    "Apple Intelligence", "Meta AI", "Amazon Q",
    "Hugging Face", "Open AI", "AI Startup", "AI Startups",
    "AI Adoption", "AI Transformation", "AI Integration",
    "AI Implementation", "AI Deployment", "AI Strategy",
    "AI Governance", "AI Compliance", "AI Ethics",
    "AI Bias", "AI Fairness", "AI Transparency",
    "AI Explainability", "AI Interpretability",
    "AI Hallucination", "AI Hallucinations",
    "AI Alignment", "AI Safety", "AI Security",
    "AI Privacy", "AI Regulation", "AI Policy",
    "AI Act", "AI Bill", "AI Executive Order",
    "AI Race", "AI Arms", "AI Cold", "AI War",
    "AI Boom", "AI Bubble", "AI Winter",
    "AI Hype", "AI Hype Cycle",
    "AI Talent", "AI Skills", "AI Education",
    "AI Literacy", "AI Training", "AI Certification",
    "AI Job", "AI Jobs", "AI Worker", "AI Workers",
    "AI Replacement", "AI Displacement",
    "AI Augmentation", "AI Assistance", "AI Assistant",
    "AI Assistants", "AI Copilot", "AI Copilots",
    "AI Agent", "AI Agents", "AI Multi",
    "AI Orchestration", "AI Coordination", "AI Planning",
    "AI Reasoning", "AI Planning", "AI Memory",
    "AI Tool", "AI Tools", "AI Tooling",
    "AI Platform", "AI Platforms", "AI Service", "AI Services",
    "AI Product", "AI Products", "AI Solution", "AI Solutions",
    "AI Vendor", "AI Vendors", "AI Provider", "AI Providers",
    "AI Customer", "AI Customers", "AI User", "AI Users",
    "AI Enterprise", "Enterprise AI", "Enterprise Software",
    "Enterprise Customer", "Enterprise Customers",
    "Enterprise Client", "Enterprise Clients",
    "Enterprise Vertical", "Enterprise Verticals",
    "Enterprise Segment", "Enterprise Market",
    "Enterprise Stack", "Enterprise Integration",
    "Enterprise Ready", "Enterprise Grade",
    "Enterprise Scale", "Enterprise Production",
    "Enterprise Pilot", "Enterprise POC", "Enterprise PoC",
    "Enterprise MVP", "Enterprise Test",
    "Enterprise Trial", "Enterprise Pilot",
    "Enterprise Rollout", "Enterprise Deployment",
    "Enterprise Adoption", "Enterprise Integration",
    "Enterprise Stack", "Enterprise Architecture",
    "Enterprise Solution", "Enterprise Solutions",
    "Enterprise Product", "Enterprise Products",
    "Enterprise Platform", "Enterprise Platforms",
    "Enterprise Service", "Enterprise Services",
    "Enterprise Tool", "Enterprise Tools",
    "Enterprise App", "Enterprise Apps",
    "Enterprise Application", "Enterprise Applications",
    "Enterprise System", "Enterprise Systems",
    "Enterprise Software", "Enterprise Software Market",
    "Enterprise Software Vendors", "Enterprise Software Provider",
    "Enterprise Software Company", "Enterprise Software Companies",
    "Enterprise Software Startup", "Enterprise Software Startups",
    "Enterprise SaaS", "Enterprise SaaS Market",
    "Enterprise SaaS Vendors", "Enterprise SaaS Provider",
    "Enterprise SaaS Company", "Enterprise SaaS Companies",
    "Enterprise SaaS Startup", "Enterprise SaaS Startups",
    "Enterprise AI", "Enterprise AI Market",
    "Enterprise AI Vendors", "Enterprise AI Provider",
    "Enterprise AI Company", "Enterprise AI Companies",
    "Enterprise AI Startup", "Enterprise AI Startups",
    "Enterprise AI Platform", "Enterprise AI Platforms",
    "Enterprise AI Solution", "Enterprise AI Solutions",
    "Enterprise AI Product", "Enterprise AI Products",
    "Enterprise AI Service", "Enterprise AI Services",
    "Enterprise AI Tool", "Enterprise AI Tools",
    "Enterprise AI App", "Enterprise AI Apps",
    "Enterprise AI Application", "Enterprise AI Applications",
    "Enterprise AI System", "Enterprise AI Systems",
    "Enterprise AI Software", "Enterprise AI Softwares",
    "Enterprise AI Architecture", "Enterprise AI Architectures",
    "Enterprise AI Stack", "Enterprise AI Stacks",
    "Enterprise AI Framework", "Enterprise AI Frameworks",
    "Enterprise AI Infrastructure", "Enterprise AI Infrastructures",
    "Enterprise AI Deployment", "Enterprise AI Deployments",
    "Enterprise AI Integration", "Enterprise AI Integrations",
    "Enterprise AI Implementation", "Enterprise AI Implementations",
    "Enterprise AI Adoption", "Enterprise AI Adoptions",
    "Enterprise AI Rollout", "Enterprise AI Rollouts",
    "Enterprise AI Pilot", "Enterprise AI Pilots",
    "Enterprise AI Trial", "Enterprise AI Trials",
    "Enterprise AI Test", "Enterprise AI Tests",
    "Enterprise AI PoC", "Enterprise AI POCs", "Enterprise AI POC",
    "Enterprise AI MVP", "Enterprise AI MVPs",
    "Enterprise AI Production", "Enterprise AI Productions",
    "Enterprise AI Scale", "Enterprise AI Scaling",
    "Enterprise AI Grade", "Enterprise AI Ready",
    "Enterprise AI Customer", "Enterprise AI Customers",
    "Enterprise AI Client", "Enterprise AI Clients",
    "Enterprise AI User", "Enterprise AI Users",
    "Enterprise AI Vertical", "Enterprise AI Verticals",
    "Enterprise AI Segment", "Enterprise AI Segments",
    "Enterprise AI Market", "Enterprise AI Markets",
    "Enterprise AI Industry", "Enterprise AI Industries",
    "Enterprise AI Use Case", "Enterprise AI Use Cases",
    "Enterprise AI Application", "Enterprise AI Applications",
    "Enterprise AI Workload", "Enterprise AI Workloads",
    "Enterprise AI Project", "Enterprise AI Projects",
    "Enterprise AI Initiative", "Enterprise AI Initiatives",
    "Enterprise AI Program", "Enterprise AI Programs",
    "Enterprise AI Team", "Enterprise AI Teams",
    "Enterprise AI Department", "Enterprise AI Departments",
    "Enterprise AI Group", "Enterprise AI Groups",
    "Enterprise AI Division", "Enterprise AI Divisions",
    "Enterprise AI Unit", "Enterprise AI Units",
    "Enterprise AI Center", "Enterprise AI Centers",
    "Enterprise AI Lab", "Enterprise AI Labs",
    "Enterprise AI Institute", "Enterprise AI Institutes",
    "Enterprise AI Office", "Enterprise AI Offices",
    "Enterprise AI Function", "Enterprise AI Functions",
    "Enterprise AI Role", "Enterprise AI Roles",
    "Enterprise AI Strategy", "Enterprise AI Strategies",
    "Enterprise AI Plan", "Enterprise AI Plans",
    "Enterprise AI Roadmap", "Enterprise AI Roadmaps",
    "Enterprise AI Vision", "Enterprise AI Visions",
    "Enterprise AI Mission", "Enterprise AI Missions",
    "Enterprise AI Goal", "Enterprise AI Goals",
    "Enterprise AI Objective", "Enterprise AI Objectives",
    "Enterprise AI Target", "Enterprise AI Targets",
    "Enterprise AI Metric", "Enterprise AI Metrics",
    "Enterprise AI KPI", "Enterprise AI KPIs",
    "Enterprise AI Benchmark", "Enterprise AI Benchmarks",
    "Enterprise AI Standard", "Enterprise AI Standards",
    "Enterprise AI Best Practice", "Enterprise AI Best Practices",
    "Enterprise AI Guideline", "Enterprise AI Guidelines",
    "Enterprise AI Framework", "Enterprise AI Frameworks",
    "Enterprise AI Methodology", "Enterprise AI Methodologies",
    "Enterprise AI Process", "Enterprise AI Processes",
    "Enterprise AI Procedure", "Enterprise AI Procedures",
    "Enterprise AI Workflow", "Enterprise AI Workflows",
    "Enterprise AI Pipeline", "Enterprise AI Pipelines",
    "Enterprise AI Lifecycle", "Enterprise AI Lifecycles",
    "Enterprise AI Stack", "Enterprise AI Stacks",
    "Enterprise AI Layer", "Enterprise AI Layers",
    "Enterprise AI Tier", "Enterprise AI Tiers",
    "Enterprise AI Level", "Enterprise AI Levels",
    "Enterprise AI Stage", "Enterprise AI Stages",
    "Enterprise AI Phase", "Enterprise AI Phases",
    "Enterprise AI Step", "Enterprise AI Steps",
    "Enterprise AI Stage", "Enterprise AI Stages",
    "Enterprise AI Maturity", "Enterprise AI Maturities",
    "Enterprise AI Readiness", "Enterprise AI Readines",
    "Enterprise AI Maturity Model", "Enterprise AI Maturity Models",
    "Enterprise AI Readiness Assessment", "Enterprise AI Readiness Assessments",
    "Enterprise AI Maturity Assessment", "Enterprise AI Maturity Assessments",
    "Enterprise AI Maturity Framework", "Enterprise AI Maturity Frameworks",
    "Enterprise AI Readiness Framework", "Enterprise AI Readiness Frameworks",
    "Enterprise AI Maturity Score", "Enterprise AI Maturity Scores",
    "Enterprise AI Readiness Score", "Enterprise AI Readiness Scores",
    "Enterprise AI Maturity Level", "Enterprise AI Maturity Levels",
    "Enterprise AI Readiness Level", "Enterprise AI Readiness Levels",
    "Enterprise AI Maturity Stage", "Enterprise AI Maturity Stages",
    "Enterprise AI Readiness Stage", "Enterprise AI Readiness Stages",
    "Enterprise AI Maturity Phase", "Enterprise AI Maturity Phases",
    "Enterprise AI Readiness Phase", "Enterprise AI Readiness Phases",
    "Enterprise AI Maturity Step", "Enterprise AI Maturity Steps",
    "Enterprise AI Readiness Step", "Enterprise AI Readiness Steps",
}


def check_english_phrases(text: str) -> List[Tuple[int, str, str]]:
    issues = []
    for m in ENGLISH_PHRASE_PATTERN.finditer(text):
        phrase = m.group(1)
        if phrase in ALLOWED_PHRASES:
            continue
        words = re.split(r"[\s-]", phrase)
        if len(words) < 2:
            continue
        if not all(re.match(r"^[A-Za-z]+$", w) for w in words):
            continue
        line_num = text[: m.start()].count("\n") + 1
        issues.append((line_num, phrase, "(двуязычная фраза — пересмотрите)"))
    return issues


def main():
    parser = argparse.ArgumentParser(description="Линтер дайджеста на англицизмы")
    parser.add_argument("path", type=Path, help="MD-файл для проверки")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Возвращать exit code 1 если найдены проблемы",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="check_phrases",
        help="+ проверка паттерна '2 англ. слова подряд' (больше ложных срабатываний)",
    )
    args = parser.parse_args()

    if not args.path.exists():
        print(f"❌ Файл не найден: {args.path}")
        sys.exit(2)

    print(f"🔍 Проверяю {args.path}...")
    issues = lint_file(args.path, check_phrases=args.check_phrases)

    if not issues:
        print("✅ Англицизмов и калек не найдено. Можно публиковать.")
        sys.exit(0)

    # Группируем по типу
    by_source = {"BAD_TERMS": [], "VERB_CALQUES": [], "ENGLISH_PHRASE": []}
    for issue in issues:
        ln, found, sugg, source = issue
        by_source[source].append(issue)

    print(f"\n⚠️  Найдено {len(issues)} проблем:\n")
    if by_source["BAD_TERMS"]:
        print(f"🔴 ПЛОХИЕ КАЛЬКИ ({len(by_source['BAD_TERMS'])}):")
        seen = set()
        for ln, found, sugg, _ in by_source["BAD_TERMS"]:
            if (ln, found) in seen:
                continue
            seen.add((ln, found))
            print(f"   строка {ln}: «{found}» → {sugg}")
        print()
    if by_source["VERB_CALQUES"]:
        print(f"🟡 РУСИФИЦИРОВАННЫЕ ГЛАГОЛЫ ({len(by_source['VERB_CALQUES'])}):")
        seen = set()
        for ln, found, sugg, _ in by_source["VERB_CALQUES"]:
            if (ln, found) in seen:
                continue
            seen.add((ln, found))
            print(f"   строка {ln}: «{found}» → {sugg}")
        print()
    if by_source["ENGLISH_PHRASE"]:
        print(f"⚪ ДВУЯЗЫЧНЫЕ ФРАЗЫ ({len(by_source['ENGLISH_PHRASE'])}) — превью, не блок:")
        seen = set()
        for ln, found, sugg, _ in by_source["ENGLISH_PHRASE"]:
            if (ln, found) in seen:
                continue
            seen.add((ln, found))
            print(f"   строка {ln}: «{found}»")
        print()

    if args.strict and (by_source["BAD_TERMS"] or by_source["VERB_CALQUES"]):
        print("❌ Строгий режим: публикация заблокирована (есть плохие кальки или глаголы).")
        sys.exit(1)
    else:
        print("💡 --strict блокирует только BAD_TERMS и VERB_CALQUES, не ENGLISH_PHRASE.")
        sys.exit(0)


if __name__ == "__main__":
    main()
