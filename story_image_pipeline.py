#!/usr/bin/env python3
"""
Story Image Pipeline — извлечение, оценка и подготовка изображений для Telegram Stories.

Пайплайн:
  1. extract_image_url()  — og:image из статьи
  2. score_image()         — Vision AI оценивает релевантность
  3. transform_to_vertical() — PIL обрезает/расширяет до 9:16
  4. verify_transform()    — Vision AI проверяет что смысл сохранён
"""
from __future__ import annotations
import base64
import io
import json
import os
import re
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional
import hashlib

import sys

sys.path.insert(0, str(Path(__file__).parent))

# ─── Env ──────────────────────────────────────────────────────────────────────
import _env as _env_module
_env_module._()

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY", "")
VISION_MODEL = "MiniMax-M3"
IMAGE_CACHE_DIR = Path(__file__).parent / ".story_images"
IMAGE_CACHE_DIR.mkdir(exist_ok=True)


def url_hash(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:12]


def _cache_image(image_bytes: bytes, key: str) -> Path:
    """Сохраняет вертикальную картинку в кэш. Returns path."""
    path = IMAGE_CACHE_DIR / f"{key}.jpg"
    if not path.exists():
        path.write_bytes(image_bytes)
    return path


# ─── OpenRouter Vision API ────────────────────────────────────────────────────

def _download_image_for_vision(image_url: str, timeout: int = 15) -> Optional[bytes]:
    """
    Download image and convert to JPEG bytes for Vision API.
    Returns None if download/conversion fails.

    Handles:
    - AVIF: Pillow 12.x natively decodes → convert to JPEG
    - HEIC/HEIF: ffmpeg conversion (Pillow doesn't support these)
    - RGBA/P/LA: alpha compositing over white background
    - All others: returned as-is if already JPEG
    """
    try:
        req = urllib.request.Request(
            image_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
                "Referer": "https://www.google.com/",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            img_bytes = resp.read()
            ct = resp.headers.get("Content-Type", "image/jpeg")
    except Exception:
        return None

    if not img_bytes or len(img_bytes) < 1000:  # Too small = probably error page
        return None

    # Check if already JPEG (fast path)
    if img_bytes.startswith(b"\xff\xd8"):
        return img_bytes

    # Try PIL decode + convert to JPEG
    try:
        from PIL import Image
        import io

        img = Image.open(io.BytesIO(img_bytes))
        fmt = img.format or ""
        fmt_lower = fmt.lower()

        # HEIC/HEIF: use ffmpeg (Pillow doesn't support these)
        if fmt_lower in ("heic", "heif"):
            import subprocess, tempfile, os

            tmp_in = tempfile.NamedTemporaryFile(suffix=f".{fmt_lower}", delete=False)
            tmp_in.write(img_bytes)
            tmp_in.close()
            tmp_out = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
            tmp_out.close()

            try:
                result = subprocess.run(
                    ["ffmpeg", "-y", "-i", tmp_in.name, "-c:v", "mjpeg", "-q:v", "2",
                     tmp_out.name],
                    capture_output=True, timeout=30,
                )
                if result.returncode == 0 and os.path.getsize(tmp_out.name) > 1000:
                    return open(tmp_out.name, "rb").read()
            finally:
                os.unlink(tmp_in.name)
                os.unlink(tmp_out.name)
            return None  # ffmpeg failed

        # RGBA/P/LA: alpha → white background
        if img.mode in ("RGBA", "P", "LA"):
            background = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            background.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=88)
        return buf.getvalue()

    except Exception:
        # PIL decode failed (unknown format) — return None
        return None


def _vision_api(image_url: str, prompt: str, json_mode: bool = False) -> str:
    """
    Вызывает MiniMax-M3 Vision API (OpenAI-compatible).
    Скачивает картинку локально и передаёт как base64 (не URL),
    чтобы обойти CDN restrictions и AI-scraper blocks.
    """
    # Download image and convert to JPEG bytes
    img_bytes = _download_image_for_vision(image_url)
    if not img_bytes:
        return ""  # Fall through to parsing_failed

    import base64
    b64 = base64.b64encode(img_bytes).decode()

    payload = {
        "model": VISION_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }
        ],
    }
    # MiniMax-M3 supports JSON mode
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://api.minimax.io/v1/chat/completions",
        data=data,
        headers={
            "Authorization": f"Bearer {MINIMAX_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        result = json.loads(resp.read())
    return result["choices"][0]["message"]["content"]


# ─── 1. Extract image URL ─────────────────────────────────────────────────────

def _is_image_accessible(url: str, timeout: int = 8) -> bool:
    """Check if image URL returns 200 (or redirect that leads to image)."""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
                "Referer": "https://www.google.com/",
            },
            method="HEAD",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ct = resp.headers.get("Content-Type", "")
            return resp.status in (200, 301, 302, 303, 307, 308) and "image" in ct
    except Exception:
        pass
    # Fallback: try GET (some CDNs don't support HEAD)
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "image/*",
                "Referer": "https://www.google.com/",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ct = resp.headers.get("Content-Type", "")
            return resp.status == 200 and "image" in ct
    except Exception:
        return False


def extract_image_url(url: str, timeout: int = 10) -> Optional[str]:
    """Извлекает og:image из статьи. Проверяет accessibility перед return."""
    # Formats not supported by Vision API (MiniMax supports JPEG, PNG, WebP, GIF only).
    # AVIF is handled by _download_image_for_vision (Pillow 12.x decodes it natively → converts to JPEG).
    # HEIC/HEIF require ffmpeg conversion in _download_image_for_vision.
    skip_formats = (".heic", ".heif", ".bmp", ".tiff", ".tif")

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; StoryBot/1.0)",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return None

    # Habr-specific: og:image points to share URL (not an image).
    # Parse <figure> tags for real article images from habrastorage.org.
    if "habr.com" in url:
        figures = re.findall(r'<figure[^>]*>.*?<img[^>]+src=["\']([^"\']+)["\']', html, re.DOTALL)
        for fig_url in figures:
            if "habrastorage.org" in fig_url and not fig_url.lower().endswith(skip_formats):
                if _is_image_accessible(fig_url):
                    return fig_url
        # Also try data-src (lazy loading)
        lazy = re.findall(r'data-src=["\']([^"\']+habrastorage\.org[^"\']+)["\']', html)
        for lazy_url in lazy:
            if not lazy_url.lower().endswith(skip_formats) and _is_image_accessible(lazy_url):
                return lazy_url
        # No habrastorage image found
        return None

    patterns = [
        r'og:image["\']\s*content=["\']([^"\']+)["\']',
        r'property=["\']og:image["\']\s*content=["\']([^"\']+)["\']',
        r'name=["\']og:image["\']\s*content=["\']([^"\']+)["\']',
    ]

    for pat in patterns:
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            img_url = m.group(1).strip()
            bad = ["pixel", "tracker", "1x1", "spacer", "logo", "icon", "blank"]
            if img_url and not any(x in img_url.lower() for x in bad):
                if img_url.lower().endswith(skip_formats):
                    return None  # Vision API can't process these
                # Check if image URL is actually accessible
                if _is_image_accessible(img_url):
                    return img_url
                return None  # Don't fallback to twitter:image if og:image is inaccessible

    # twitter:image fallback
    m = re.search(
        r'twitter:image["\']\s*content=["\']([^"\']+)["\']', html, re.IGNORECASE
    )
    if m:
        img_url = m.group(1).strip()
        if img_url and not img_url.lower().endswith(skip_formats):
            if _is_image_accessible(img_url):
                return img_url
    return None


def get_image_dimensions(data: bytes) -> Optional[tuple[int, int]]:
    """Returns (width, height) from raw image bytes. Supports JPEG, PNG, WebP."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        w = int.from_bytes(data[16:20], "big")
        h = int.from_bytes(data[20:24], "big")
        return (w, h)
    elif data[:2] == b"\xff\xd8":
        i = 2
        while i < len(data) - 1:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2):
                h = int.from_bytes(data[i + 5 : i + 7], "big")
                w = int.from_bytes(data[i + 7 : i + 9], "big")
                return (w, h)
            length = int.from_bytes(data[i + 2 : i + 4], "big")
            i += 2 + length
        return None
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        if data[12:16] == b"VP8L":
            bits = int.from_bytes(data[17:21], "little")
            w = (bits & 0x3FFF) + 1
            h = ((bits >> 14) & 0x3FFF) + 1
            return (w, h)
    return None

def _parse_json_response(text: str):
    """
    Извлекает JSON из текста, игнорируя thinking tags и markdown.
    Поддерживает формат MiniMax-M3: ... 【...】  \n\n{JSON}
    """
    NL = "\n"

    # Step 1: Remove 【...】 internal markers (MiniMax-specific)
    clean = re.sub(r"\u3010.*?\u3011", " ", text, flags=re.DOTALL)

    # Step 2: Strip from ... to the first JSON object
    first_brace = clean.find("{")
    if first_brace > 0:
        before = clean[:first_brace]
        # If there's a long non-JSON prefix (thinking block), remove it
        if len(before) > 20:
            clean = clean[first_brace:]

    # Step 3: Try direct parse
    try:
        return json.loads(clean)
    except Exception:
        pass

    # Step 4: Markdown code fence
    m = re.search(r"```(?:json)?\s*(.+?)```", clean, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except Exception:
            pass

    # Step 5: Find JSON via brace matching
    first = clean.find("{")
    last = clean.rfind("}")
    if first != -1 and last > first:
        for end in range(last, first - 1, -1):
            try:
                return json.loads(clean[first:end+1])
            except Exception:
                continue

    return None



def score_image(
    image_url: str,
    article_title: str,
    article_summary: str,
    topics: list[str] | None = None,
    anti_topics: list[str] | None = None,
    audience_description: str = "",
) -> dict:
    """
    Vision AI (MiniMax-M3) оценивает: релевантность, качество, вертикальность,
    реальность фото И тематическое соответствие аудитории.

    topics / anti_topics / audience_description — из channel profile.
    Если переданы, агент учитывает их при оценке.
    """
    # Формируем контекст аудитории для промпта
    audience_block = ""
    if audience_description:
        audience_block += f"\nAUDIENCE DESCRIPTION: {audience_description}"
    if topics:
        audience_block += f"\nTARGET TOPICS (what audience wants):\n" + "\n".join(
            f"  - {t}" for t in topics
        )
    if anti_topics:
        audience_block += f"\nANTI-TOPICS (what audience does NOT want — reject these):\n" + "\n".join(
            f"  - {t}" for t in anti_topics
        )

    prompt = f"""You are an image curator for a Telegram channel about practical AI tools for SMB/business.

Evaluate this image for a STORY (vertical 9:16 format).

Article title: {article_title}
Article summary: {article_summary[:300] if article_summary else "No summary"}
{audience_block}

Rate from 0-10:
1. RELEVANCE — does the image visually represent the article topic?
2. QUALITY — real photo (not AI-generated, not a chart/graph/logo)?
3. INTEREST — does it have visual drama/impact for this audience?
4. VERTICAL_POTENTIAL — works well in portrait 9:16?
5. AUDIENCE_FIT — is this interesting/useful for the target audience described above?

SCORING GUIDE:
- Score 8-10: Real photo showing a practical AI tool, product, or demo that SMB audience would find useful/interesting
- Score 5-7: Visually okay but topic is borderline (hardware, academic, finance deal, etc.)
- Score 0-4: Off-topic for SMB AI audience — political news, academic theory, IPO/finance deals, aerospace, benchmark comparisons

Also tell me: is this a REAL PHOTOGRAPH (not AI-generated)? What does the image show?

Respond in exactly this JSON format, no other text:
{{"score": N, "relevance": N, "quality": N, "interest": N, "vertical": N, "audience_fit": N, "is_real_photo": true/false, "verdict": "use/reject/consider", "reason": "1 sentence", "what_shows": "brief description"}}
"""
    try:
        response = _vision_api(image_url, prompt, json_mode=False)
        parsed = _parse_json_response(response)
        if parsed:
            audience_fit = parsed.get("audience_fit", 5)
            # audience_fit ниже 5 = автоматически reject
            if audience_fit < 5:
                return {
                    "score": max(0, min(10, int(parsed.get("score", 5)))),
                    "verdict": "reject",
                    "reason": parsed.get("reason", "low audience_fit"),
                    "what_shows": parsed.get("what_shows", ""),
                    "is_real_photo": parsed.get("is_real_photo", True),
                    "audience_fit": audience_fit,
                }
            return {
                "score": max(0, min(10, int(parsed.get("score", 5)))),
                "verdict": parsed.get("verdict", "consider"),
                "reason": parsed.get("reason", ""),
                "what_shows": parsed.get("what_shows", ""),
                "is_real_photo": parsed.get("is_real_photo", True),
                "audience_fit": audience_fit,
            }
    except Exception:
        pass
    return {"score": 5, "verdict": "consider", "reason": "parsing failed", "what_shows": "", "is_real_photo": False, "audience_fit": None}


# ─── 3. Transform to vertical ─────────────────────────────────────────────────

def transform_to_vertical(image_bytes: bytes, target_aspect: float = 9 / 16) -> bytes:
    """
    Обрезает изображение до 9:16 portrait (центрированный crop).
    Returns JPEG bytes.
    """
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        orig_w, orig_h = img.size
        target_w = int(orig_h * target_aspect)

        if orig_w >= target_w:
            # Center crop to target width
            left = (orig_w - target_w) // 2
            img_cropped = img.crop((left, 0, left + target_w, orig_h))
        else:
            # Too narrow — pad to center
            new_img = Image.new("RGB", (target_w, orig_h), (255, 255, 255))
            paste_x = (target_w - orig_w) // 2
            new_img.paste(img, (paste_x, 0))
            img_cropped = new_img

        buf = io.BytesIO()
        img_cropped.save(buf, format="JPEG", quality=88)
        return buf.getvalue()
    except Exception:
        return image_bytes  # Fallback: return as-is


# ─── 4. Verify transform ─────────────────────────────────────────────────────

def verify_transform(image_url: str, article_title: str) -> dict:
    """
    Vision AI проверяет что картинка после обработки всё ещё осмысленная.
    """
    prompt = f"""Describe what you see in this image in 1-2 sentences.
Does it look meaningful (not distorted, not a blank background)?
Article it should represent: {article_title[:100]}

Respond in JSON:
{{"meaningful": true/false, "description": "what you see", "distortion_detected": true/false}}
"""
    try:
        response = _vision_api(image_url, prompt, json_mode=True)
        parsed = json.loads(response)
        return {
            "meaningful": parsed.get("meaningful", True),
            "description": parsed.get("description", ""),
            "distortion": parsed.get("distortion_detected", False),
        }
    except Exception:
        return {"meaningful": True, "description": "", "distortion": False}


# ─── 5. Caption generation ───────────────────────────────────────────────────

def make_story_caption(title: str, summary: str, what_shows: str) -> str:
    """
    Формирует caption для Telegram Story (до 200 символов).
    Caption = что видно на картинке + источник.
    """
    import re

    # Clean HTML from summary
    clean_summary = re.sub(r"<[^>]+>", "", summary) if summary else ""
    if clean_summary and len(clean_summary) > 15:
        sentence = clean_summary.split(".")[0].strip()
        if len(sentence) > 195:
            sentence = sentence[:192] + "..."
        return sentence

    clean_title = re.sub(r"[-—:]\s*.+$", "", title).strip()
    return clean_title[:195] + ("..." if len(clean_title) > 195 else "")


# ─── Main pipeline ────────────────────────────────────────────────────────────

def prepare_story_image(
    post: dict,
    min_score: int = 5,
    channel_profile: dict | None = None,
) -> Optional[dict]:
    """
    Полный пайплайн для одного поста:
      pre-filter (anti-topics) → extract → score (c учётом аудитории) → transform
    Returns result dict or None.
    """
    url = post.get("url", "")
    title = post.get("title", "")
    summary = post.get("summary", "")
    kb_q = channel_profile.get("kb_query", {}) if channel_profile else {}

    # ── Pre-filter: быстрый keyword-матчинг по anti_topics (bilingual) ───────────
    # Anti_topics в профиле — на русском. Посты в KB — на English (80%).
    # Для каждой антитемы задаём ключевые слова НА ОБОИХ ЯЗЫКАХ.
    ANTI_TOPIC_KEYWORDS = {
        "Политические и геополитические": [
            "politics", "political", "geopolitics", "geopolitical",
            "regulation", "regulatory", "sanction", "policy", "government",
            "администрация", "правительство", "санкции", "регулирование",
        ],
        "Академическая теория": [
            "academic", "research paper", "arxiv", " preprint",
            "benchmark", "study shows", "scientific study", "laboratory",
            "теория", "академический", "научный", "исследование",
        ],
        "Сделки и финансовые новости": [
            "ipo", "fundraising", "funding round", "series a", "series b", "series c",
            "stock", "valuation", "investment", "investor", "spac",
            "фандрейзинг", "оценка", "инвестиции", "биржа", "размещение",
        ],
        "Аэрокосмические и оборонные": [
            "aerospace", "space", "satellite", "defense", "military",
            "космос", "аэрокосмос", "оборона", "спутник", "военный",
        ],
        "Hardware и GPU": [
            "gpu", "benchmark", "nvidia geforce", "amd radeon",
            "cpu", "processor benchmark", "fps test", "gaming performance",
            "бенчмарк", "видеокарта", "процессор",
        ],
        # fallback: универсальные слова для любой антитемы
        "_default": [
            "trump", "biden", "congress", "parliament", "война",
            "academic paper", "peer-reviewed", "journal article",
            "go public", "initial public", "acquisition deal",
            "military ai", "weapon", "drone warfare",
        ],
    }

    if kb_q.get("anti_topics"):
        text_block = f"{title} {summary}".lower()
        for anti in kb_q["anti_topics"]:
            # Найти ключевые слова для этой антитемы
            keywords = None
            for key, kws in ANTI_TOPIC_KEYWORDS.items():
                if key in anti or any(k in anti.lower() for k in kws[:3]):
                    keywords = kws
                    break
            if keywords is None:
                keywords = ANTI_TOPIC_KEYWORDS["_default"]

            matched = [k for k in keywords if len(k) > 2 and k in text_block]
            if matched:
                return None  # отклоняем сразу, без Vision API

    # ── 1. Extract (skip if already done in Stage 1) ───────────────────────────
    img_url = post.get("image_url")  # Stage 1 may have already extracted
    if not img_url:
        img_url = extract_image_url(url)
    if not img_url:
        return None

    # ── 2. Download and check dimensions ─────────────────────────────────────
    try:
        req = urllib.request.Request(img_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            img_bytes = resp.read()
    except Exception:
        return None

    if len(img_bytes) < 5000:
        return None  # too small, likely placeholder

    dims = get_image_dimensions(img_bytes)
    if dims:
        w, h = dims
        if w < 300 or h < 300:
            return None
        if w / h > 4:  # panoramic
            return None

    # ── 3. Score (с контекстом аудитории) ───────────────────────────────────
    score_result = score_image(
        img_url,
        title,
        summary,
        topics=kb_q.get("topics"),
        anti_topics=kb_q.get("anti_topics"),
        audience_description=kb_q.get("audience_description", ""),
    )
    if score_result.get("verdict") == "reject":
        return None
    if score_result.get("score", 0) < min_score:
        return None

    # ── 4. Transform to vertical ────────────────────────────────────────────
    img_vertical = transform_to_vertical(img_bytes)

    # ── 5. Caption ──────────────────────────────────────────────────────────
    caption = make_story_caption(
        title, summary, score_result.get("what_shows", "")
    )

    return {
        "image_url": img_url,
        "image_path": str(_cache_image(img_vertical, post.get("id", url_hash(url)))),
        "image_bytes": img_vertical,
        "score": score_result.get("score", 5),
        "verdict": score_result.get("verdict", "consider"),
        "what_shows": score_result.get("what_shows", ""),
        "reason": score_result.get("reason", ""),
        "caption": caption,
        "post": post,
        "audience_fit": score_result.get("audience_fit", None),
    }


def find_best_story_candidates(
    posts: list[dict],
    min_score: int = 5,
    channel_profile: dict | None = None,
) -> list[dict]:
    """
    Параллельно обрабатывает список постов через Vision API.
    Принимает channel_profile — для pre-filter и scoring с учётом аудитории.
    Возвращает отсортированный по score список.
    """
    results = []
    done = set()
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_post = {
            executor.submit(prepare_story_image, post, min_score, channel_profile): post
            for post in posts
        }
        import time

        deadline = time.time() + 180
        while len(done) < len(future_to_post) and time.time() < deadline:
            for future in list(future_to_post.keys()):
                if future not in done and future.done():
                    try:
                        result = future.result()
                        if result:
                            results.append(result)
                    except Exception:
                        pass
                    done.add(future)
            if len(done) < len(future_to_post):
                time.sleep(1)

    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results


# ─── TOPIC RELEVANCE SCORING (fast text-based, NO Vision API) ─────────────────

# Bilingual keyword dictionaries for topic scoring
TOPIC_KEYWORDS = {
    # Что интересно аудитории SMB
    "ai_tools_business": [
        # EN
        "ai tool", "chatbot", "automation", "workflow", "saas",
        "customer service", "helpdesk", "support ai", "crm ai",
        "marketing ai", "sales ai", "email ai", "writing ai",
        "document processing", "data entry", "schedule", "calendar",
        "accounting ai", "finance tool", "billing", "invoice",
        "productivity", "efficiency", "roi", "cost saving",
        "small business", "smb", "startup tool", "no code",
        "integration", "api", "pricing", "subscription",
        # RU
        "ии инструмент", "автоматизация", "чат-бот", "робот",
        "помощник", "бизнес", "малый бизнес", "предприниматель",
        "маркетинг", "продажи", "клиенты", "сервис",
    ],
    "practical_cases": [
        "case study", "how to", "tutorial", "implementation",
        "real world", "production", "deploy", "adoption",
        "success story", "results", "metrics", "outcome",
        "практика", "внедрение", "кейс", "результат",
        "опыт", "история", "пример", "как сделать",
    ],
    "consumer_smart": [
        "smart home", "smart device", "gadget", "wearable",
        "consumer electronics", "phone", "laptop", "tablet",
        "app", "application", "software",
    ],
}

ANTI_TOPIC_KEYWORDS = {
    "Политические и геополитические": [
        "politics", "political", "geopolitics", "geopolitical",
        "regulation", "regulatory", "sanction", "policy", "government",
        "administration", "congress", "parliament",
        "администрация", "правительство", "санкции", "регулирование",
    ],
    "Академическая теория": [
        "academic", "research paper", "arxiv", " preprint",
        "study shows", "scientific study", "laboratory",
        "peer-reviewed", "journal article",
        "теория", "академический", "научный", "исследование",
    ],
    "Сделки и финансовые новости": [
        " ipo", "ipo ", " ipo ",  # IPO as word
        "fundraising", "funding round",
        "series a", "series b", "series c",
        "stock", "valuation", "investment", "investor",
        " raises $", " raises £", " raises €",  # "raises $10B" = funding
        "went public", "public offering", "stock market debut",
        "фандрейзинг", "оценка", "инвестиции", "биржа", "размещение",
    ],
    "Аэрокосмические и оборонные": [
        "aerospace", "space x", "spacex", "satellite", "defense", "military",
        "космос", "аэрокосмос", "оборона", "спутник", "военный",
    ],
    "Hardware и GPU": [
        "geforce", "radeon", "nvidia geforce", "amd radeon",
        "rtx", "gtx",  # GPU model lines (short — word-boundary checked)
        "fps test", "gaming performance", "gaming laptop",
        "видеокарта", "процессор", "игровой",
    ],
    "_default": [
        " trump ", " biden ", " война",
        "military ai", "weapon", "drone warfare",
    ],
}


def _keyword_in_text(text: str, keyword: str) -> bool:
    """Check if keyword exists as a word boundary match (not substring)."""
    # For short keywords (<=3 chars) use word boundaries
    if len(keyword) <= 3:
        import re
        return bool(re.search(r'\b' + re.escape(keyword) + r'\b', text))
    return keyword in text


def _score_topic_relevance(title: str, summary: str, topics: list[str] | None) -> float:
    """
    Returns 0.0–1.0 topic relevance score based on keyword matching.
    No AI/LLM call — pure text processing.
    """
    if not topics:
        return 0.5  # Neutral if no topics defined

    text = f"{title} {summary}".lower()

    # Collect all positive keywords
    positive_kws = set()
    for kw_list in TOPIC_KEYWORDS.values():
        positive_kws.update(kw_list)

    # Count positive keyword matches
    positive_matches = [kw for kw in positive_kws if len(kw) > 2 and kw in text]
    positive_score = min(1.0, len(positive_matches) / 3)  # 3+ matches = max

    return positive_score


def _passes_anti_topics(title: str, summary: str, anti_topics: list[str] | None) -> tuple[bool, str]:
    """
    Returns (passes, matched_keyword).
    Fast text filter — rejects posts matching anti-topics.
    Uses word-boundary matching to avoid substring false positives (e.g. 'spac' in 'SpaceX').
    """
    if not anti_topics:
        return True, ""

    text = f"{title} {summary}".lower()

    for anti in (anti_topics or []):
        keywords = None
        for key, kws in ANTI_TOPIC_KEYWORDS.items():
            if key in anti or any(_keyword_in_text(anti.lower(), k) for k in kws[:3]):
                keywords = kws
                break
        if keywords is None:
            keywords = ANTI_TOPIC_KEYWORDS["_default"]

        matched = [k for k in keywords if len(k) > 2 and _keyword_in_text(text, k)]
        if matched:
            return False, matched[0]

    return True, ""


def find_story_candidates_by_text(
    posts: list[dict],
    channel_profile: dict,
    limit: int = 20,
) -> list[dict]:
    """
    STAGE 1 of story pipeline — CHEAP text-based filtering BEFORE Vision API.

    Пайплайн:
      1. Anti-topics text filter (reject obviously bad topics)
      2. Topic relevance scoring (prioritize posts matching audience interests)
      3. Image URL extraction (parallel, fast HTTP)

    Returns posts sorted by topic_relevance, each with image_url attached.
    These should be passed to find_best_story_candidates() for Vision scoring.

    This dramatically reduces Vision API calls — отсеиваем 70-80% постов ДО дорогого LLM.
    """
    kb_q = channel_profile.get("kb_query", {})
    topics = kb_q.get("topics")
    anti_topics = kb_q.get("anti_topics")

    # ── Stage 1: Anti-topics filter + topic relevance scoring ─────────────────
    scored = []
    for post in posts:
        title = post.get("title", "")
        summary = post.get("summary", "")

        passes, matched = _passes_anti_topics(title, summary, anti_topics)
        if not passes:
            continue  # Rejected by anti-topics

        topic_score = _score_topic_relevance(title, summary, topics)
        if topic_score < 0.0:  # Пропускаем всё что прошло анти-темы — Vision AI отберёт лучшее
            continue

        scored.append({
            **post,
            "_topic_relevance": topic_score,
            "_anti_topic_reject": matched,
        })

    # Sort by topic relevance descending
    scored.sort(key=lambda x: x.get("_topic_relevance", 0), reverse=True)

    # ── Stage 2: Image URL extraction (sequential, uses latest code) ─────────────
    # NOTE: called sequentially (not via ThreadPool) to ensure latest module code
    # is used (avoids stale bytecode cache in worker threads).
    # At ~0.3-0.5s per call × 20 candidates = ~10s, acceptable.
    top_posts = scored[:limit]
    results = []
    for post in top_posts:
        img_url = extract_image_url(post.get("url", ""))
        if img_url:
            results.append({**post, "image_url": img_url})

    # Sort by topic relevance (already sorted, but维持)
    results.sort(key=lambda x: x.get("_topic_relevance", 0), reverse=True)
    return results
