#!/usr/bin/env python3
"""
Composer Agent — генерирует draft-посты для каналов по их профилям.
Поддерживает: Telegram, Listmonk, Instagram, LinkedIn, Facebook, Twitter.

Использование:
    python composer.py --channel CHANNEL_ID [--preview]
    python composer.py --all          # все включённые профили
    python composer.py --preview      # preview всех
    python composer.py --daily        # ежедневная генерация (для cron)
    python composer.py --listmonk-campaign CHANNEL_ID  # создать кампанию в Listmonk
"""
from __future__ import annotations
import json
import sys
import urllib.request, urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))

from content_bank import query
from channel_profiles import load_all_profiles, load_profile

DRAFTS_DIR = Path(__file__).parent / "drafts"
DRAFTS_DIR.mkdir(exist_ok=True)

LISTMONK_API_URL = "https://unsubscribe.main-way.com"
LISTMONK_CREDS = ("Zufar_api", "jlrIzGI7wrQXGnQSax8Mz4z5Um2RCNZC")

# ─── Formatters ───────────────────────────────────────────────────────────────

def format_news(posts: list[dict], profile: dict) -> str:
    """Структурированные новости с источниками."""
    limit = profile["format"].get("max_posts", 3)
    include_source = profile["format"].get("include_source", True)
    include_link = profile["format"].get("include_link", True)
    max_len = profile["format"].get("max_length", 2000)

    lines = []
    for p in posts[:limit]:
        title = p["title"]
        if p.get("source") and include_source:
            lines.append(f"📍 {p['source']}: {title}")
        else:
            lines.append(f"📍 {title}")
        if p.get("summary") and p["summary"] not in ("Comments", "Open", ""):
            summary = p["summary"][:250].strip()
            lines.append(f"   {summary}")
        if include_link and p.get("url"):
            lines.append(f"   🔗 {p['url']}")
        lines.append("")

    text = "\n".join(lines).strip()
    if max_len and len(text) > max_len:
        text = text[:max_len] + "..."
    return text


def format_brief(posts: list[dict], profile: dict) -> str:
    """Подборка с кратким вступлением."""
    limit = profile["format"].get("max_posts", 5)
    include_source = profile["format"].get("include_source", True)
    include_link = profile["format"].get("include_link", True)
    max_len = profile["format"].get("max_length", 8000)

    lines = [f"📡 Подборка из {min(len(posts), limit)} материалов:\n"]
    for p in posts[:limit]:
        title = p["title"]
        source = f" ({p['source']})" if p.get("source") and include_source else ""
        link = f" → {p['url']}" if include_link and p.get("url") else ""
        lines.append(f"• {title}{source}{link}")

    text = "\n".join(lines)
    if max_len and len(text) > max_len:
        text = text[:max_len] + "..."
    return text


def format_single(posts: list[dict], profile: dict) -> str:
    """Одна тема — один пост. Для Instagram/Twitter."""
    if not posts:
        return ""
    p = posts[0]
    text = f"📰 {p['title']}"
    if p.get("summary") and p["summary"] not in ("Comments", "Open", ""):
        text += f"\n\n{p['summary'][:500]}"
    if p.get("url"):
        text += f"\n\n🔗 {p['url']}"
    return text.strip()


def format_html_email(posts: list[dict], profile: dict) -> str:
    """HTML-формат для email-рассылок Listmonk."""
    limit = profile["format"].get("max_posts", 5)
    max_len = profile["format"].get("max_length", 8000)
    channel_name = profile.get("name", "")

    items = []
    for p in posts[:limit]:
        title = p.get("title", "")
        source = p.get("source", "")
        summary = p.get("summary", "")
        url = p.get("url", "")
        # Clean summary
        if summary in ("Comments", "Open", ""):
            summary = ""
        summary = summary[:400].strip() if summary else ""

        item = f"""
        <tr>
          <td style="padding:12px 0;border-bottom:1px solid #eee;">
            <strong style="font-size:15px;color:#222;">{title}</strong>
            {'<br><span style="color:#888;font-size:12px;">📍 ' + source + '</span>' if source else ''}
            {'<br><span style="color:#555;font-size:13px;line-height:1.5;">' + summary + '</span>' if summary else ''}
            {'<br><a href="{url}" style="color:#1a73e8;font-size:13px;">Читать далее →</a>' if url else ''}
          </td>
        </tr>"""
        items.append(item)

    html = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{channel_name}</title>
</head>
<body style="margin:0;padding:0;background:#f5f5f5;font-family:Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f5f5f5;padding:20px;">
  <tr>
    <td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.1);">
        <!-- Header -->
        <tr>
          <td style="background:#1a73e8;padding:24px 32px;">
            <h1 style="margin:0;color:#fff;font-size:20px;font-weight:600;">{channel_name}</h1>
            <p style="margin:8px 0 0;color:rgba(255,255,255,0.85);font-size:13px;">{datetime.now().strftime('%d.%m.%Y')}</p>
          </td>
        </tr>
        <!-- Intro -->
        <tr>
          <td style="padding:20px 32px 0;color:#444;font-size:14px;line-height:1.6;">
            Подборка из <strong>{len(posts[:limit])}</strong> материалов:
          </td>
        </tr>
        <!-- Items -->
        <tr>
          <td style="padding:8px 32px 0;">
            <table width="100%" cellpadding="0" cellspacing="0">
              {''.join(items)}
            </table>
          </td>
        </tr>
        <!-- Footer -->
        <tr>
          <td style="padding:24px 32px;background:#fafafa;border-top:1px solid #eee;text-align:center;">
            <p style="margin:0;color:#888;font-size:12px;">
              Вы получили это письмо, потому что подписаны на рассылку.<br>
              <a href="{'{unsubscribe}'}" style="color:#1a73e8;">Отписаться</a>
            </p>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>
</body>
</html>"""
    if max_len and len(html) > max_len * 2:  # HTML is bigger, use chars not bytes
        pass  # don't truncate HTML
    return html.strip()


def format_story(posts: list[dict], profile: dict) -> tuple[str, list[dict]]:
    """
    Telegram/Instagram Stories: реальные картинки из статей + AI-оценка.

    2-этапный пайплайн (правильный порядок):
      ЭТАП 1 (дешёвый): find_story_candidates_by_text
        → Anti-topics filter (текст)
        → Topic relevance scoring (текст)
        → Image URL extraction (HTTP)
      ЭТАП 2 (дорогой): find_best_story_candidates
        → Vision AI scoring (только на отфильтрованных!)

    Returns: (markdown_text, stories_data_for_posting)
    """
    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from story_image_pipeline import (
            find_story_candidates_by_text,
            find_best_story_candidates,
        )
    except Exception:
        return ("⚠️  story_image_pipeline недоступен", None)

    # ── ЭТАП 1: Дешёвая текстовая фильтрация перед Vision API ──────────────────
    # Эта функция: anti-topics filter + topic scoring + image URL extraction
    # Vision API ещё НЕ вызывается
    kb_q = profile.get("kb_query", {})
    limit = profile.get("compose", {}).get("max_posts") or 3
    # Берём больше кандидатов на этапе 1, чтобы был запас после Vision-фильтра
    text_filtered = find_story_candidates_by_text(
        posts, profile, limit=limit * 4
    )

    if not text_filtered:
        return (
            "⚠️  Нет постов, соответствующих теме канала.\n"
            "Проверь topics/anti_topics в профиле.",
            None,
        )

    # ── ЭТАП 2: Vision AI scoring — только на отфильтрованных по теме ───────────
    # Vision API вызывается только на постах прошедших текстовый фильтр
    candidates = find_best_story_candidates(
        text_filtered, min_score=5, channel_profile=profile
    )
    selected = candidates[:limit]

    if not selected:
        return (
            "⚠️  Не найдено постов с подходящими картинками.\nПопробуй увеличить days_back или снизить min_score.",
            None,
        )

    blocks = []
    stories_data = []
    for i, r in enumerate(selected, 1):
        p = r["post"]
        title = p.get("title", "")
        source = p.get("source", "")
        url = p.get("url", "")

        from urllib.parse import urlparse

        short_ref = urlparse(url).netloc if url else source
        # Caption — берём из what_shows (что видно), fallback на краткий what_shows или title
        if r.get("what_shows"):
            caption = r["what_shows"][:197] + ("…" if len(r["what_shows"]) > 197 else "")
        else:
            caption = r.get("caption", "")[:200] or title[:197] + "…"
        what_shows = r.get("what_shows", "")[:100]
        score = r.get("score", 0)
        verdict = r.get("verdict", "?")
        img_path = r.get("image_path", "")
        img_size_kb = len(r.get("image_bytes", b"")) // 1024

        blocks.append(f"""## Story {i}: {title}

📷 {what_shows}

**caption:** {caption}

**источник:** {source}
**см. также:** {short_ref}

🖼 картинка: {img_size_kb}KB, {verdict} (score={score}/10)
📁 {img_path}""")

        if i < len(selected):
            blocks.append("\n---\n")

        # Store for posting phase
        stories_data.append(
            {
                "title": title,
                "source": source,
                "url": url,
                "short_ref": short_ref,
                "caption": caption,
                "what_shows": what_shows,
                "score": score,
                "verdict": verdict,
                "image_path": img_path,
                "image_url": r.get("image_url", ""),
                "post": p,
            }
        )

    return "\n".join(blocks), stories_data

def _make_story_caption(title: str, source: str, summary: str) -> str:
    """Генерирует короткую подпись к истории (до 200 символов)."""
    import re
    # Очищаем summary от HTML-тегов
    clean_summary = re.sub(r'<[^>]+>', '', summary) if summary else ''

    # Если есть summary — берём из него ключевую фразу
    if clean_summary and len(clean_summary) > 20:
        # First meaningful sentence/phrase
        sentences = clean_summary.split('.')
        phrase = sentences[0].strip() if sentences else title
        if len(phrase) > 180:
            phrase = phrase[:177] + "..."
        return phrase

    # Иначе — генерируем из названия
    # Убираем всё что после "—" или ":"
    clean_title = re.sub(r'[-—:]\s*.+$', '', title).strip()
    return clean_title[:195] + ('...' if len(clean_title) > 195 else '')


FORMATTERS = {
    "news": format_news,
    "brief": format_brief,
    "single": format_single,
    "thread": format_brief,  # reuse brief
    "article": format_brief,
    "story": format_story,
    "html": format_html_email,
}
def _compose_with_skill(posts: list[dict], profile: dict, compose_cfg: dict) -> str:
    """
    Использует Hermes skill для генерации контента.
    Пока — заглушка: готовит prompt и вызывает встроенный composer.
    Полная реализация: использует delegate_task для вызова Hermes агента
    с нужным skill.
    """
    # Собираем контекст для агента
    channel_name = profile.get("name", profile.get("channel_id", ""))
    platform = profile.get("platform", "")
    style = compose_cfg.get("style", "news")
    system_addon = compose_cfg.get("system_prompt_addon", "")
    temperature = compose_cfg.get("temperature", 0.7)
    max_tokens = compose_cfg.get("max_tokens", 0)

    # Формируем контекст
    kb_context = "\n".join([
        f"- **{p['title']}** ({p.get('source','?')})\n  {p.get('summary','')}"
        f"{' ' + p.get('url','') if p.get('url') else ''}"
        for p in posts[:5]
    ])

    prompt = f"""Составь пост для канала "{channel_name}" ({platform}).

Стиль: {style}
Температура: {temperature}
{max_tokens if max_tokens else 'Без лимита'} символов.

Вот материалы из базы знаний:
{kb_context}

{system_addon}

Сгенерируй пост на указанную тему. Если стиль=brief — подборка из нескольких материалов.
Если стиль=news — структурированные новости. Если стиль=single — один пост на одну тему."""

    # Пока используем built-in composer (full implementation would use delegate_task)
    print(f"   ℹ️  Skill-композиция: используем built-in composer (skill delegation pending)")
    return format_draft(posts, profile)


def format_draft(posts: list[dict], profile: dict) -> str:
    # style может быть в compose (YAML) или format (устаревший путь)
    style = profile.get("compose", {}).get("style") or profile["format"].get("style", "news")
    formatter = FORMATTERS.get(style, format_brief)
    return formatter(posts, profile)



# ─── Composer ────────────────────────────────────────────────────────────────

def _build_fts_query(kb_q: dict) -> str:
    """
    Topics и anti_topics теперь используются ТОЛЬКО для prompt агента (system_prompt_addon).
    FTS query не строится из них — это размывает язык и источники.
    Returns: пустая строка (естественный KB микс).
    """
    return ""


def compose_for_channel(channel_id: str, dry_run: bool = False) -> Optional[dict]:
    profile = load_profile(channel_id)
    if not profile:
        print(f"❌ Профиль {channel_id} не найден")
        return None

    if not profile.get("enabled", True):
        print(f"⏭ {channel_id} — отключён")
        return None

    kb_q = profile.get("kb_query", {})
    # Topics/anti_topics — для system_prompt агента, не для FTS
    # FTS не используется = естественный микс из KB
    kb_results = query(
        categories=kb_q.get("categories") or None,
        languages=kb_q.get("languages") or None,
        priorities=kb_q.get("priorities") or None,
        sources=kb_q.get("sources") or None,
        query_text=None,  # без FTS
        days_back=kb_q.get("days_back", 7),
        limit=kb_q.get("limit", 30),
    )

    if not kb_results:
        print(f"⚠️  {channel_id}: нет релевантных постов в KB")
        return None

    posts_per_day = profile.get("schedule", {}).get("posts_per_day", 1)
    max_posts = profile["format"].get("max_posts", 3)
    selected = kb_results[:posts_per_day * max_posts]

    # ── COMPOSE ───────────────────────────────────────────────────────────────
    compose_cfg = profile.get("compose", {})
    style = compose_cfg.get("style") or profile["format"].get("style", "news")
    skill = compose_cfg.get("skill", "")

    if style == "story":
        # format_story returns (draft_text, stories_data)
        draft_result = format_draft(selected, profile)
        if isinstance(draft_result, tuple):
            draft_text, stories_data = draft_result
        else:
            draft_text = draft_result
            stories_data = None
    elif skill:
        # Используем Hermes skill для генерации
        draft_text = _compose_with_skill(selected, profile, compose_cfg)
        stories_data = None
    else:
        # Встроенный composer (format_* функции)
        draft_text = format_draft(selected, profile)
        stories_data = None

    # For listmonk — generate HTML separately
    html_body = None
    if profile.get("platform") == "listmonk":
        html_body = format_html_email(selected, profile)

    draft = {
        "channel_id": channel_id,
        "channel_name": profile.get("name", channel_id),
        "platform": profile.get("platform", "telegram"),
        "posts_used": len(selected),
        "kb_total_hits": len(kb_results),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "posts": [
            {
                "id": p["id"],
                "title": p["title"],
                "source": p.get("source"),
                "url": p.get("url"),
                "priority": p.get("priority"),
                "language": p.get("language"),
                "category": p.get("category"),
            }
            for p in selected
        ],
        "draft_text": draft_text,
        "html_body": html_body,
        "stories": stories_data,  # Only set for story style
    }

    if not dry_run:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        draft_dir = DRAFTS_DIR / profile.get("moderation", {}).get("draft_dir", f"{channel_id}/")
        draft_dir.mkdir(parents=True, exist_ok=True)

        draft_path = draft_dir / f"{channel_id}_{date_str}.json"
        with open(draft_path, "w", encoding="utf-8") as f:
            json.dump(draft, f, indent=2, ensure_ascii=False)

        # Plain MD version
        md_path = draft_dir / f"{channel_id}_{date_str}.md"
        md_content = f"# {profile.get('name', channel_id)}\n"
        md_content += f"Канал: {channel_id} | Платформа: {profile.get('platform')}\n"
        md_content += f"Сгенерировано: {draft['generated_at']}\n"
        md_content += f"KB hits: {draft['kb_total_hits']}, использовано: {draft['posts_used']}\n"
        md_content += "---\n\n"
        md_content += draft_text
        md_path.write_text(md_content, encoding="utf-8")

        print(f"✅ {channel_id}: {draft['posts_used']} постов → {draft_path}")
    else:
        platform_tag = f"[{profile.get('platform', '?').upper()}]"
        print(f"\n{'='*55}")
        print(f"📝 DRAFT PREVIEW {platform_tag}: {profile.get('name', channel_id)}")
        print(f"{'='*55}")
        print(draft_text[:800])
        if len(draft_text) > 800:
            print(f"  ... (+ ещё {len(draft_text)-800} симв.)")
        print(f"\n💡 {draft['posts_used']} постов, {draft['kb_total_hits']} найдено в KB")

    return draft


def compose_all(dry_run: bool = False) -> list[dict]:
    profiles = load_all_profiles()
    results = []
    for p in profiles:
        if p.get("enabled", True):
            r = compose_for_channel(p["channel_id"], dry_run=dry_run)
            if r:
                results.append(r)
        else:
            print(f"⏭ {p['channel_id']}: отключён")
    return results


def _lm_request(method: str, path: str, payload: dict | None = None) -> dict:
    """Make authenticated request to Listmonk API."""
    import base64
    url = f"{LISTMONK_API_URL}{path}"
    data = json.dumps(payload).encode() if payload else None
    headers = {"Content-Type": "application/json; charset=utf-8"}
    auth = base64.b64encode(f"{LISTMONK_CREDS[0]}:{LISTMONK_CREDS[1]}".encode()).decode()
    headers["Authorization"] = f"Basic {auth}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.reason}", "body": e.read().decode()[:200]}


# ─── Listmonk API ─────────────────────────────────────────────────────────────

def create_listmonk_campaign(channel_id: str, dry_run: bool = True) -> Optional[dict]:
    """Создаёт draft-кампанию в Listmonk из драфта."""
    profile = load_profile(channel_id)
    if not profile or profile.get("platform") != "listmonk":
        print(f"❌ {channel_id}: не Listmonk-профиль")
        return None

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lm = profile.get("listmonk", {})

    # Get today's draft
    draft_dir = DRAFTS_DIR / profile.get("moderation", {}).get("draft_dir", f"{channel_id}/")
    draft_path = draft_dir / f"{channel_id}_{date_str}.json"
    if not draft_path.exists():
        print(f"❌ Drafт не найден: {draft_path}")
        return None

    with open(draft_path, encoding="utf-8") as f:
        draft = json.load(f)

    # Build campaign name
    campaign_name = lm.get("campaign_name", "{date}").format(date=date_str)
    subject = lm.get("subject", "{date}").format(date=date_str)
    content_type = lm.get("content_type", "html")
    from_name = lm.get("from_name", "AI Digest")
    reply_to = lm.get("reply_to", "")

    # Prepare content — Listmonk expects markdown for body, not HTML
    body = draft.get("draft_text", "")
    content_type = lm.get("content_type", "markdown")  # markdown | html | plain

    # Listmonk campaign create API
    # Required fields: name, subject, lists[], from_email, content_type,
    #                  messenger, type, send_later
    payload = {
        "name": campaign_name,
        "subject": subject,
        "lists": [lm.get("list_id", 0)],
        "from_email": f"{from_name} <{reply_to or 'info@main-way.com'}>",
        "content_type": content_type,
        "messenger": "email",
        "type": "regular",
        "tags": [],
        "send_later": True,
        "send_at": None,  # None = draft, or ISO timestamp for scheduled
        "template_id": lm.get("template_id", 0) or None,
        "body": body,
    }

    if dry_run:
        print(f"🔍 DRY RUN: создание кампании в Listmonk")
        print(f"   Campaign: {campaign_name}")
        print(f"   Subject: {subject}")
        print(f"   List ID: {lm.get('list_id')}")
        print(f"   Content type: {content_type}")
        print(f"   Body length: {len(body)} chars")
        return {"dry_run": True, "payload": payload}

    result = _lm_request("POST", "/api/campaigns", payload)
    if "error" in result:
        print(f"❌ Listmonk API error: {result['error']} — {result.get('body', '')}")
        return None

    campaign_id = result.get("data", {}).get("id", "?")
    print(f"✅ Кампания создана: ID={campaign_id}, name={campaign_name}")
    return result


# ─── Post dispatcher ────────────────────────────────────────────────────────

def post_for_channel(channel_id: str, dry_run: bool = True) -> Optional[dict]:
    """
    Публикует контент для канала через соответствующий skill.
    Возвращает result или None (если требуется утверждение).
    """
    profile = load_profile(channel_id)
    if not profile:
        print(f"❌ Профиль {channel_id} не найден")
        return None

    post_cfg = profile.get("post", {})
    skill = post_cfg.get("skill", "")
    use_draft = post_cfg.get("use_draft", True)
    approval_required = post_cfg.get("approval_required", True)

    # Проверяем draft
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    draft_dir = DRAFTS_DIR / profile.get("moderation", {}).get("draft_dir", f"{channel_id}/")
    draft_path = draft_dir / f"{channel_id}_{date_str}.json"

    if not draft_path.exists():
        print(f"❌ Drafт не найден: {draft_path}")
        return None

    with open(draft_path, encoding="utf-8") as f:
        draft = json.load(f)

    # Утверждение
    if approval_required:
        print(f"⏸ {channel_id}: утверждение required — draft сохранён, публикация отложена")
        print(f"   Файл: {draft_path}")
        return None

    # Публикуем через соответствующий skill
    if skill == "listmonk-campaign-api":
        return _post_listmonk(draft, profile, post_cfg, dry_run)
    elif skill == "telegram-messaging":
        return _post_telegram(draft, profile, post_cfg, dry_run)
    elif skill == "xurl":
        return _post_xurl(draft, profile, post_cfg, dry_run)
    elif skill in ("", "native"):
        return _post_native(draft, profile, post_cfg, dry_run)
    else:
        print(f"❌ Skill '{skill}' не поддерживается")
        return None


def _post_listmonk(draft: dict, profile: dict, post_cfg: dict, dry_run: bool) -> dict:
    """Публикация через Listmonk API (native)."""
    lm = profile.get("listmonk", {})
    lm_params = post_cfg.get("params", {})

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    campaign_name = lm_params.get("campaign_name", "{date}").format(date=date_str)
    subject = lm_params.get("subject", "{date}").format(date=date_str)
    content_type = lm_params.get("content_type", "markdown")
    from_name = lm_params.get("from_name", "AI Digest")
    reply_to = lm_params.get("reply_to", "info@main-way.com")
    list_id = lm_params.get("list_id") or lm.get("list_id")

    body = draft.get("draft_text", "")

    payload = {
        "name": campaign_name,
        "subject": subject,
        "lists": [list_id],
        "from_email": f"{from_name} <{reply_to}>",
        "content_type": content_type,
        "messenger": "email",
        "type": "regular",
        "tags": [],
        "send_later": True,
        "send_at": None,
        "template_id": lm_params.get("template_id") or None,
        "body": body,
    }

    if dry_run:
        print(f"🔍 DRY RUN: Listmonk campaign")
        print(f"   Campaign: {campaign_name}, List ID: {list_id}")
        print(f"   Body: {len(body)} chars")
        return {"dry_run": True}

    result = _lm_request("POST", "/api/campaigns", payload)
    if "error" in result:
        print(f"❌ Listmonk error: {result['error']}")
        return result

    cid = result.get("data", {}).get("id", "?")
    print(f"✅ Listmonk campaign created: ID={cid}")
    return result


def _post_telegram(draft: dict, profile: dict, post_cfg: dict, dry_run: bool) -> dict:
    """Публикация в Telegram через Hermes send_message."""
    tg_params = post_cfg.get("params", {})
    parse_mode = tg_params.get("parse_mode", "HTML")
    chat_id = tg_params.get("chat_id", "")

    body = draft.get("draft_text", "")

    if dry_run:
        print(f"🔍 DRY RUN: Telegram post")
        print(f"   Chat: {chat_id or '(не указан)'}")
        print(f"   Body: {len(body)} chars")
        return {"dry_run": True}

    # Вызываем Hermes send_message через subprocess
    import subprocess
    env = {
        "HERMES_DELIVER": f"telegram:{chat_id}" if chat_id else "origin",
        "PATH": "/usr/bin:/bin",
    }
    result = subprocess.run(
        ["hermes", "send", "--text", body],
        capture_output=True, text=True, env=env,
    )
    if result.returncode != 0:
        print(f"❌ Hermes send failed: {result.stderr}")
        return {"error": result.stderr}
    print(f"✅ Telegram: отправлено")
    return {"ok": True}


def _post_xurl(draft: dict, profile: dict, post_cfg: dict, dry_run: bool) -> dict:
    """Публикация через xurl (Twitter/X)."""
    if dry_run:
        print(f"🔍 DRY RUN: X/Twitter post")
        print(f"   Body: {len(draft.get('draft_text',''))} chars")
        return {"dry_run": True}
    print(f"⚠️  X/Twitter posting not implemented — используй xurl CLI вручную")
    return {"error": "not implemented"}


def _post_native(draft: dict, profile: dict, post_cfg: dict, dry_run: bool) -> dict:
    """Native posting (fallback для неподдерживаемых платформ)."""
    platform = profile.get("platform", "?")
    if dry_run:
        print(f"🔍 DRY RUN: native post ({platform})")
        return {"dry_run": True}
    print(f"⚠️  {platform}: native posting не реализован — используй drafts вручную")
    return {"error": "not implemented"}


# ─── Daily cron job ──────────────────────────────────────────────────────────

def daily_compose():
    """Ежедневная генерация драфтов для всех каналов."""
    results = compose_all(dry_run=False)

    if not results:
        print("⚠️  Ничего не сгенерировано")
        return

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    summary_lines = [f"📝 *Daily Content Drafts — {date_str}*\n"]

    for r in results:
        profile = load_profile(r["channel_id"])
        post_cfg = profile.get("post", {}) if profile else {}
        approval = post_cfg.get("approval_required", True) if profile else True
        post_skill = post_cfg.get("skill", "native") if profile else "native"

        summary_lines.append(
            f"• *{r['channel_name']}* [{r['platform']}] "
            f"— {r['posts_used']} постов"
            + (" ⏸" if approval else " ✅")
            + f" (post: {post_skill})"
        )

    summary_path = DRAFTS_DIR / f"daily_summary_{date_str}.md"
    summary_path.write_text("\n".join(summary_lines), encoding="utf-8")

    print(f"\n{'='*55}")
    print("\n".join(summary_lines))
    return results


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    import argparse
    ap = argparse.ArgumentParser(description="Composer Agent")
    ap.add_argument("--channel", metavar="ID", help="Канал")
    ap.add_argument("--all", action="store_true", help="Все включённые каналы")
    ap.add_argument("--preview", action="store_true", help="Preview без сохранения")
    ap.add_argument("--daily", action="store_true", help="Ежедневная генерация")
    ap.add_argument("--post", metavar="ID", help="Опубликовать draft через post.skill")
    ap.add_argument("--listmonk-campaign", metavar="ID",
                    help="[устарело] Используй --post")
    ap.add_argument("--dry-run", action="store_true", default=False, help="Dry run")
    args = ap.parse_args()

    if args.daily:
        daily_compose()
        return

    if args.listmonk_campaign:
        # backwards compat — теперь просто вызывает post_for_channel
        post_for_channel(args.listmonk_campaign, dry_run=args.dry_run)
        return

    if args.post:
        post_for_channel(args.post, dry_run=args.dry_run)
        return

    if args.channel:
        compose_for_channel(args.channel, dry_run=args.preview)
        return

    if args.all:
        compose_all(dry_run=args.preview)
        return

    ap.print_help()


if __name__ == "__main__":
    main()
