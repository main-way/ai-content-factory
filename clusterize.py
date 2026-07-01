#!/usr/bin/env python3
"""
clusterize.py — AI-Digest pipeline.

Собирает посты за день → embeddings → UMAP → HDBSCAN →
для каждого кластера (топ-N по score):
  1. LLM: связный ли кластер? (одна тема или свалка)
  2. LLM: проверка анти-тем
  3. LLM: пишет пересказ 1500-2500 знаков
  4. Из лучшего поста: извлекает og:image / og:video
→ формирует digest.md → сохраняет в Obsidian → отправляет в Telegram как документ

Использование:
    python clusterize.py --date 2026-06-23
    python clusterize.py --days 3
    python clusterize.py --dry-run          # без LLM и отправки
    python clusterize.py --limit 25        # сколько тем в дайджесте (default 28)
"""

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# ─── Env ──────────────────────────────────────────────────────────────────────
# Загружает ~/.hermes/.env и ./.env автоматически
import _env as _env_module
_env_module._()

# ─── Vendor imports ────────────────────────────────────────────────────────────
try:
    import torch
    torch.set_num_threads(2)          # memory optimization
    import numpy as np
    from sentence_transformers import SentenceTransformer
    import umap
    import hdbscan
except ImportError as e:
    print(f"❌ Missing dependency: {e}")
    print("   Run: .venv/bin/pip install sentence-transformers umap-learn hdbscan")
    sys.exit(1)

# ─── Paths ────────────────────────────────────────────────────────────────────
PROJECT_DIR = Path(__file__).parent
STORAGE_DIR  = PROJECT_DIR / "storage"
OBSIDIAN_BASE = Path("/srv/obsidian-base")
OBSIDIAN_DIGEST = OBSIDIAN_BASE / "BRIEFINGS" / "AI-Digest"

# ─── Model ────────────────────────────────────────────────────────────────────
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# ─── Clustering ───────────────────────────────────────────────────────────────
UMAP_N_COMPONENTS  = 20
UMAP_MIN_DIST      = 0.1
HDBSCAN_MIN_SIZE   = 3
HDBSCAN_MIN_SAMPLES = 2
MIN_DIVERSITY      = 0.12     # отсеивает кластеры-клоны одного источника

# ─── LLM ──────────────────────────────────────────────────────────────────────
LLM_API_KEY  = os.environ.get("MINIMAX_API_KEY", "")
LLM_BASE_URL = "https://api.minimax.io/v1"
LLM_MODEL    = "MiniMax-M2.7"

# ─── Telegram ─────────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "7079923530")

# ─── Digest output ────────────────────────────────────────────────────────────
TARGET_Digest_COUNT = 28      # 25-30 тем в дайджесте
MIN_DIGEST_CHARS   = 1500     # нижняя граница объёма одного поста
MAX_DIGEST_CHARS   = 2500     # верхняя граница

# ─── Anti-topics (из channel_profiles.yaml) ──────────────────────────────────
ANTI_TOPICS = [
    "Политические и геополитические ИИ-новости (санкции, регулирование, войны)",
    "Академическая теория и исследования без практического применения",
    "Узкоспециализированные темы (медицинские ИИ-исследования, научные бенчмарки)",
    "Сделки и финансовые новости ИИ-компаний на бирже (IPO, фандрейзинг, оценки)",
    "Аэрокосмические и оборонные ИИ-проекты",
    "Hardware и GPU-бенчмарки без привязки к бизнесу",
    "Посты-дайджесты: посты, которые сами являются сборниками из нескольких новостей (например, заголовки вида «5 новостей за неделю», «итоги месяца», «дайджест X от Y», «what's new this week», подборки и топы)",
]

# ─── Helpers ──────────────────────────────────────────────────────────────────

def log(msg: str, level: str = "INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", file=sys.stderr)


def log_section(msg: str):
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"  {msg}", file=sys.stderr)
    print(f"{'='*60}\n", file=sys.stderr)


# ─── Posts ────────────────────────────────────────────────────────────────────

def load_posts(date_str: str = None, days: int = 1) -> list[dict]:
    """Load posts from storage/*.json. Merges multiple days if days > 1.
    Enriches each post with full_text from archive/full_text/ if available.
    """
    # ─── Build scraped content cache from archive ──────────────────────────────
    ARCHIVE_FULLTEXT_DIR = PROJECT_DIR / "archive" / "full_text"
    scraped_cache: dict[str, str] = {}
    if ARCHIVE_FULLTEXT_DIR.is_dir():
        for fpath in ARCHIVE_FULLTEXT_DIR.iterdir():
            if fpath.suffix == ".txt":
                pid = fpath.stem  # filename without .txt = post id
                scraped_cache[pid] = fpath.read_text(encoding="utf-8", errors="ignore")[:8000]

    if date_str:
        dates = [date_str]
    else:
        today = datetime.now(timezone.utc)
        dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]

    all_posts = []
    for d in dates:
        path = STORAGE_DIR / f"posts_{d}.json"
        if not path.exists():
            log(f"⚠️  No posts file for {d}, skipping", "WARN")
            continue
        with open(path) as f:
            data = json.load(f)
        posts = data.get("posts", [])
        if posts:
            # Enrich with scraped full text if available
            for p in posts:
                pid = p.get("id", "")
                if pid in scraped_cache:
                    p["full_text"] = scraped_cache[pid]
            log(f"  {d}: {len(posts)} posts loaded (with full_text: {sum(1 for p in posts if 'full_text' in p)}/{len(posts)})")
            all_posts.extend(posts)

    # Dedup by URL
    seen, unique = set(), []
    for p in all_posts:
        url = p.get("url", "")
        if url and url not in seen:
            seen.add(url)
            unique.append(p)

    log(f"📰 Total unique posts: {len(unique)}")
    return unique


def lang_ok(p: dict) -> bool:
    return p.get("language", "en") in ("en", "ru", "")


# Posts whose title looks like a compilation/digest-of-digests
_COMPILED_TITLE_RE = re.compile(
    r"(?i)"
    r"(digest|дайджест|обзор\s*недели|еженедельн|итоги\s*(недели|месяца|года)|"
    r"top\s*\d+|5\s*новост|10\s*новост|what'?s?\s*new|weekly|monthly|"
    r"выпуск\s*\d+|issue\s*\d+|volume\s*\d+|newsletter|"
    r"подборк[аи]|сборник|этот\s*выпуск|this\s*issue)",
    re.IGNORECASE
)

def is_not_compilation(p: dict) -> bool:
    """True if the post is NOT a compilation/digest-of-digests."""
    title = p.get("title", "")
    return not bool(_COMPILED_TITLE_RE.search(title))


# Political / geopolitical keywords → these posts must be filtered out
_POLITICAL_KEYWORDS_RE = re.compile(
    r"(?i)"
    r"(санкци|войн[ау]|военн|боев|конфликт|регулирован|госполитика|"
    r"президент\s*(Росси|Украин|Беларус|Кита轨|Lukashenko|Biden|Trump|Putin|"
    r"Zelensky|Xijinping|Modi|Erdogan|Netanyahu)|"
    r"министра\s*(обороны|иностранн|внутренн)|"
    r"\bМинск\b|\bКремл|G7|G20|НАТО|ОДКБ|"
    r"выбор(ы|ах|ов|ам)|голосовани|референдум|парламент|"
    r"дипломат|визит\s*(президент|премье|lider)|"
    r"двусторонн\s*(встреч|переговор)|"
    r"\bBelarus\b.*\bIndonesia\b|Ukraine.*Russia|Russia.*Ukraine|"
    r"геополит|международн\s*(конфликт|кризис|политик)|"
    r"войска|ракет|дрон|"
    r"(атак|обстрел|взрыв|гибель|потери)\s*(мирн|граждан)|"
    r"Евросоюз.*санкц|\bЕС\b.*ограничен)",
    re.IGNORECASE
)

def is_not_political(p: dict) -> bool:
    """True if the post is NOT about politics/geopolitics."""
    title = p.get("title", "")
    summary = p.get("summary", "")[:300]
    text = title + " " + summary
    return not bool(_POLITICAL_KEYWORDS_RE.search(text))


# ─── Embeddings ───────────────────────────────────────────────────────────────

def prepare_text(post: dict) -> str:
    title   = post.get("title", "")
    summary = post.get("summary", "")
    source  = post.get("source", "")
    return f"{title} [source: {source}] {summary}".strip()


def embed_posts(posts: list[dict]) -> tuple[list[str], np.ndarray, list[dict]]:
    log("🧠 Loading embedding model...")
    model = SentenceTransformer(EMBEDDING_MODEL, device="cpu")

    texts = [prepare_text(p) for p in posts]
    valid_idx   = [i for i, t in enumerate(texts) if t.strip()]
    valid_posts = [posts[i] for i in valid_idx]
    valid_texts = [texts[i] for i in valid_idx]

    log(f"📝 Embedding {len(valid_texts)} texts (batch_size=8)...")
    t0 = time.time()
    embeddings = model.encode(valid_texts, show_progress_bar=True,
                             batch_size=8, convert_to_numpy=True)
    log(f"   Done in {time.time()-t0:.1f}s, shape={embeddings.shape}")
    import gc
    gc.collect()
    return valid_texts, embeddings, valid_posts


# ─── Clustering ───────────────────────────────────────────────────────────────

def cluster(embeddings: np.ndarray, texts: list[str], posts: list[dict]):
    log(f"🔵 UMAP: {embeddings.shape} → (n, {UMAP_N_COMPONENTS})...")
    t0 = time.time()
    reducer = umap.UMAP(n_components=UMAP_N_COMPONENTS, min_dist=0.1,
                        metric="cosine", random_state=42, n_jobs=1, n_neighbors=10)
    reduced = reducer.fit_transform(embeddings)
    log(f"   UMAP done in {time.time()-t0:.1f}s")

    log(f"🔴 HDBSCAN (min_cluster_size={HDBSCAN_MIN_SIZE})...")
    t0 = time.time()
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=HDBSCAN_MIN_SIZE, min_samples=HDBSCAN_MIN_SAMPLES,
        metric="euclidean", cluster_selection_method="eom", prediction_data=True,
    )
    labels = clusterer.fit_predict(reduced)
    n_clusters = len(set(labels)) - (1 if -1 in labels else 0)
    n_noise   = int((labels == -1).sum())
    log(f"   HDBSCAN done in {time.time()-t0:.1f}s — clusters={n_clusters}, noise={n_noise}")

    cluster_data = {}
    for i, label in enumerate(labels):
        if label == -1:
            continue
        if label not in cluster_data:
            cluster_data[label] = {"indices": [], "posts": [], "texts": []}
        cluster_data[label]["indices"].append(i)
        cluster_data[label]["posts"].append(posts[i])
        cluster_data[label]["texts"].append(texts[i])

    return cluster_data


# ─── Scoring ──────────────────────────────────────────────────────────────────

def avg_interpoint_dist(embeddings: np.ndarray, indices: list[int], centroid_idx: int) -> float:
    if len(indices) < 2:
        return 0.0
    e = embeddings[indices]
    c = embeddings[centroid_idx]
    return float(np.linalg.norm(e - c, axis=1).mean())


def score_clusters(cluster_data: dict, embeddings: np.ndarray) -> list[dict]:
    """Score and sort clusters. Returns list of dicts with full cluster info."""
    scored = []
    for cid, cd in cluster_data.items():
        indices = cd["indices"]
        posts_l = cd["posts"]

        # Centroid
        centroid_idx = indices[np.linalg.norm(
            embeddings[indices] - embeddings[indices].mean(axis=0), axis=1
        ).argmin()]

        # Source diversity
        domain_sources = set()
        for p in posts_l:
            m = re.search(r"https?://([^/]+)", p.get("url", ""))
            if m:
                domain_sources.add(m.group(1))

        # Velocity (% of posts in last 48h)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
        recent = 0
        for p in posts_l:
            pub = p.get("published", "")
            if pub:
                try:
                    from dateutil import parser as dp
                    dt = dp.parse(pub)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if dt > cutoff:
                        recent += 1
                except Exception:
                    pass

        size     = len(indices)
        diversity = len(domain_sources) / max(size, 1)
        velocity  = recent / max(size, 1)
        spread   = 1.0 / (1.0 + avg_interpoint_dist(embeddings, indices, centroid_idx))

        # Filter: low diversity = copy-paste spam
        if diversity < MIN_DIVERSITY:
            continue

        size_score = math.log1p(size)
        score = size_score * (0.5 + 0.5 * velocity) * (0.3 + 0.7 * diversity) * (0.4 + 0.6 * spread)

        # Top post (most central)
        top_post = posts_l[np.linalg.norm(
            embeddings[indices] - embeddings[indices].mean(axis=0), axis=1
        ).argmin()]

        scored.append({
            "cluster_id":  int(cid),
            "size":        size,
            "score":       round(score, 3),
            "velocity":    round(velocity, 2),
            "diversity":   round(diversity, 2),
            "spread":      round(spread, 3),
            "top_title":   top_post.get("title", "")[:120],
            "top_url":     top_post.get("url", ""),
            "top_source":  top_post.get("source", ""),
            "domains":     list(domain_sources)[:8],
            "posts":       posts_l,
            "texts":       cd["texts"],
        })

    scored.sort(key=lambda x: -x["score"])
    return scored


# ─── LLM ─────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = "Ты -- редактор русского ИИ-дайджеста. Пиши связный текст 1500-2500 знаков по теме. Без списков и нумерации. Без вступлений. В конце: Источник: [название](URL)."

def llm_text(prompt: str, max_tokens: int = 2000) -> str | None:
    """POST to MiniMax-M2, return text response or None. Retry on timeout."""
    if not LLM_API_KEY:
        log("⚠️  MINIMAX_API_KEY not set", "WARN")
        return None

    payload = json.dumps({
        "model":      LLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ],
        "temperature": 0.7,
        "max_tokens":  max_tokens,
    }).encode()

    req = urllib.request.Request(
        f"{LLM_BASE_URL}/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"},
        method="POST",
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.load(resp)
            content = data["choices"][0]["message"]["content"]
            content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
            content = re.sub(r"<[^>]+>", "", content).strip()

            # Strip chain-of-thought
            lines = content.split('\n')
            real_start = 0
            REQ_STARTS = (
                "Требования:", "Ключевые факты:", "Ключевые моменты:",
                "Мне нужно написать", "Из материалов мне нужно",
                "Давайте проанализирую", "Анализирую задачу",
                "Напиши на русском", "Let me analyze",
            )
            for i, line in enumerate(lines):
                stripped = lines[i].strip()
                if not stripped:
                    continue
                if any(stripped.startswith(p) for p in REQ_STARTS):
                    real_start = i + 1; continue
                if re.match(r'^\d+\.\s', stripped):
                    real_start = i + 1; continue
                if re.match(r'^-\s', stripped):
                    real_start = i + 1; continue
                real_start = i; break
            return '\n'.join(lines[real_start:]).strip()

        except TimeoutError:
            log(f"   LLM timeout (attempt {attempt+1}/3)", "WARN")
        except Exception as e:
            log(f"   LLM error: {e}", "ERROR")
            break
    return None


def llm_json(prompt: str, max_tokens: int = 500) -> dict | None:
    """POST to MiniMax-M2, return parsed JSON or None."""
    if not LLM_API_KEY:
        log("⚠️  MINIMAX_API_KEY not set", "WARN")
        return None

    payload = json.dumps({
        "model":       LLM_MODEL,
        "messages":    [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens":  max_tokens,
    }).encode()

    req = urllib.request.Request(
        f"{LLM_BASE_URL}/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"},
        method="POST",
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.load(resp)
            content = data["choices"][0]["message"]["content"]
            content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
            content = re.sub(r"<[^>]+>", "", content).strip()
            start, end = content.find("{"), content.rfind("}")
            if start != -1 and end != -1 and end > start:
                return json.loads(content[start:end+1])
        except TimeoutError:
            log(f"   LLM JSON timeout (attempt {attempt+1}/3)", "WARN")
        except Exception as e:
            log(f"   LLM JSON error: {e}", "ERROR")
            break
    return None


# ─── LLM Checks per Cluster ──────────────────────────────────────────────────

def check_coherence(cluster: dict) -> tuple[bool, str]:
    """
    Задаёт LLM вопрос: это одна тема или несвязная свалка?
    Returns (is_coherent: bool, reason: str)
    """
    titles = "\n".join(f"- {p.get('title','')[:120]}" for p in cluster["posts"][:10])
    prompt = f"""Analyze this news cluster. Is it ONE coherent topic/theme, or MIXED/unrelated articles?

Cluster size: {cluster['size']}
Titles:
{titles}

Respond with JSON only (no markdown):
{{
  "coherent": true or false,
  "reason": "1-2 sentences explaining why",
  "main_topic": "what is this cluster about in 5 words max"
}}"""
    result = llm_json(prompt, max_tokens=300)
    if not result:
        # Fail open — don't discard cluster on LLM error
        return True, "LLM unavailable"
    is_coherent = result.get("coherent", True)
    reason = result.get("reason", "")
    log(f"   #{cluster['rank']} {'✅' if is_coherent else '⚠️'} coherent={is_coherent}: {reason[:80]}")
    return bool(is_coherent), reason


def check_anti_topic(cluster: dict) -> tuple[bool, str]:
    """
    Проверяет кластер на антитемы.
    Returns (is_anti_topic: bool, matched_anti: str)
    """
    topic_desc = cluster.get("main_topic", cluster["top_title"][:80])
    titles_sample = "\n".join(f"- {p.get('title','')[:100]}" for p in cluster["posts"][:6])

    prompt = f"""Check if this news cluster matches ANY of the anti-topics below.

MAIN TOPIC (from cluster analysis): {topic_desc}

SAMPLE TITLES:
{titles_sample}

ANTI-TOPICS TO CHECK:
{chr(10).join(f"- {a}" for a in ANTI_TOPICS)}

Respond with JSON only:
{{
  "is_anti": true or false,
  "matched_anti_topic": "which anti-topic matched, or empty string"
}}"""
    result = llm_json(prompt, max_tokens=250)
    if not result:
        return False, ""
    is_anti = result.get("is_anti", False)
    matched = result.get("matched_anti_topic", "")
    if is_anti:
        log(f"   ⛔ anti-topic matched: {matched}")
    return bool(is_anti), matched


def write_digest_post(cluster: dict) -> dict | None:
    """
    Генерирует текст одного поста для дайджеста (1500-2500 знаков).
    Returns dict with keys: text, best_post (dict with url/title/media) or None.
    """
    # Find best post: the one with most complete info
    best_post = max(cluster["posts"],
                    key=lambda p: (len(p.get("summary", "")), bool(p.get("url"))))

    best_url    = best_post.get("url", "")
    best_title  = best_post.get("title", "")
    best_source = best_post.get("source", "")

    # Collect all post data for the LLM
    # Prefer full_text (from scrape.py) over summary (from RSS)
    # Limit content to avoid API timeout on large prompts
    posts_data = []
    for p in cluster["posts"][:6]:
        content = (
            p.get("full_text", "").strip()[:800]
            or p.get("summary", "").strip()[:200]
        )
        posts_data.append({
            "title":   p.get("title", ""),
            "url":     p.get("url", ""),
            "source":  p.get("source", ""),
            "content": content,
        })

    posts_text = "\n".join(
        f"**[{d['source']}]** {d['title']}\n   {d['content']}\n   → {d['url']}"
        for d in posts_data
    )

    prompt = f"""ROLE: Ты — редактор русскоязычного дайджеста для предпринимателей.
TASK: Напиши готовый текст объёмом 1500–2500 знаков по теме «{cluster.get('main_topic', cluster['top_title'])}» на основе материалов. Без ИТ-жаргона (запрещено: baseline, pipeline, framework, workflow, scaling, deploy, агент, пайплайн, фреймворк). В конце: 📍 Источник: [{best_source}]({best_url})
OUTPUT: Только текст дайджеста. Без списков, без нумерации, без предисловий.

Материалы:
{posts_text}

ДАЙДЖЕСТ:"""

    text = llm_text(prompt, max_tokens=3000)
    if not text:
        return None

    # Validate length
    clean_text = re.sub(r"<[^>]+>", "", text).strip()
    if len(clean_text) < MIN_DIGEST_CHARS:
        log(f"   ⚠️  Post too short ({len(clean_text)} chars), adding content from next best posts...")
        # Try adding more posts to the prompt
        extra_posts = []
        for p in cluster["posts"][8:]:
            extra_posts.append(f"- {p.get('title','')[:100]}: {p.get('summary','')[:200]}")
        if extra_posts:
            extra_prompt = prompt + "\n\nДополнительные материалы:\n" + "\n".join(extra_posts[:5])
            text = llm_text(extra_prompt, max_tokens=3000)
            if text:
                clean_text = re.sub(r"<[^>]+>", "", text).strip()

    return {
        "text":       clean_text,
        "best_post":  best_post,
        "url":        best_url,
        "title":      best_title,
        "source":     best_source,
        "topic":      cluster.get("main_topic") or cluster.get("top_title", ""),
    }


# ─── Media Extraction ─────────────────────────────────────────────────────────

def fetch_media(url: str, timeout: int = 8) -> dict:
    """Extract og:image, og:video from article HTML. Returns {images: [], videos: []}."""
    result = {"images": [], "videos": []}
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; AI-Digest/1.0)",
            "Accept": "text/html",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return result

    # og:image
    for match in re.finditer(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html, re.I):
        if match.group(1) not in result["images"]:
            result["images"].append(match.group(1))
    for match in re.finditer(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', html, re.I):
        if match.group(1) not in result["images"]:
            result["images"].append(match.group(1))

    # og:video
    for match in re.finditer(r'<meta[^>]+property=["\']og:video["\'][^>]+content=["\']([^"\']+)["\']', html, re.I):
        if match.group(1) not in result["videos"]:
            result["videos"].append(match.group(1))

    # twitter:image
    if not result["images"]:
        for match in re.finditer(r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']', html, re.I):
            if match.group(1) not in result["images"]:
                result["images"].append(match.group(1))

    return result


def enrich_with_media(posts: list[dict]) -> list[dict]:
    """Fetch og:image for top posts in parallel. Updates posts in-place."""
    def _fetch(p: dict) -> dict:
        url = p.get("url", "")
        if not url:
            return p
        media = fetch_media(url)
        p["_media"] = media
        return p

    log(f"🖼 Fetching media for {len(posts)} posts...")
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_fetch, p): p for p in posts}
        for fut in as_completed(futures):
            try:
                fut.result()
            except Exception:
                pass
    return posts


# ─── Markdown Formatter ───────────────────────────────────────────────────────

def build_digest_md(items: list[dict], date_str: str) -> str:
    """Build full digest markdown file."""

    # Table of contents
    toc_lines = ["## 📑 Содержание", ""]
    for i, item in enumerate(items, 1):
        topic_short = item.get("topic", item.get("title", "?"))[:60]
        safe_slug = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ]", "-", topic_short).lower()
        toc_lines.append(f"{i}. [{topic_short}](#{safe_slug})")

    toc = "\n".join(toc_lines)

    # Posts
    post_blocks = []
    for i, item in enumerate(items, 1):
        topic = item.get("topic", item.get("title", ""))
        safe_slug = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ]", "-", topic[:60]).lower()
        text  = item.get("text", "")
        url   = item.get("url", "")
        src   = item.get("source", "")
        media = item.get("media", {})

        # Media block
        media_lines = []
        if media.get("images"):
            media_lines.append(f"🖼 [Иллюстрация]({media['images'][0]})")
        if media.get("videos"):
            media_lines.append(f"🎬 [Видео]({media['videos'][0]})")

        media_block = ""
        if media_lines:
            media_block = "\n" + " | ".join(media_lines) + "\n"

        # Source line
        source_line = f"📍 Источник: [{src}]({url})" if url else f"📍 Источник: {src}"

        post_blocks.append(f"""## {i}. {topic}

{text}

{source_line}
{media_block}""")

    posts_section = "\n\n".join(post_blocks)

    # Header
    total_posts = sum(it.get("n_source_posts", 0) for it in items)
    header = f"""---
date: {date_str}
type: ai-digest
tags:
  - ai/дайджест
  - ai/новости
  - briefing
source_file: digest_{date_str}.md
posts_in_period: ~{total_posts}
language:
  - en
  - ru
---

**Проект:** [[AI-бизнес-mAIn-WAY]]
**Категория:** [[AI-Agency]]

# 📡 AI-Digest — {date_str}

**Тем в дайджесте:** {len(items)}
**Сгенерировано:** {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}

{toc}

---

"""

    return header + posts_section


# ─── Obsidian ─────────────────────────────────────────────────────────────────

def save_digest_obsidian(content: str, date_str: str) -> Path:
    OBSIDIAN_DIGEST.mkdir(parents=True, exist_ok=True)
    path = OBSIDIAN_DIGEST / f"digest_{date_str}.md"
    path.write_text(content, encoding="utf-8")
    log(f"💾 Saved to Obsidian: {path}")

    # Also save to output/ for convenience
    output_dir = PROJECT_DIR / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"digest_{date_str}_clusterized.md"
    output_path.write_text(content, encoding="utf-8")
    log(f"💾 Saved to output:  {output_path}")

    return path


# ─── Telegram ─────────────────────────────────────────────────────────────────

def send_telegram_document(file_path: Path, caption: str = "") -> bool:
    """Send file as Telegram document via Bot API sendDocument."""
    if not BOT_TOKEN:
        log("⚠️  TELEGRAM_BOT_TOKEN not set, skipping Telegram", "WARN")
        return False

    import subprocess
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"

    cmd = [
        "curl", "-s", "-X", "POST", url,
        "-F", f"chat_id={CHAT_ID}",
        "-F", f"document=@{file_path}",
    ]
    if caption:
        cmd += ["-F", f"caption={caption}"]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        data = json.loads(result.stdout)
        if data.get("ok"):
            doc = data["result"]["document"]
            log(f"📱 Telegram: sent {doc['file_name']} ({doc['file_size']} bytes)")
            return True
        else:
            log(f"⚠️  Telegram error: {data.get('description', result.stdout[:200])}", "ERROR")
            return False
    except Exception as e:
        log(f"⚠️  Telegram send failed: {e}", "ERROR")
        return False


# ─── Main Pipeline ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date",       help="Date YYYY-MM-DD (default: today)")
    parser.add_argument("--days",      type=int, default=1, help="Rolling window days")
    parser.add_argument("--limit",    type=int, default=TARGET_Digest_COUNT, help="Target post count")
    parser.add_argument("--embed-limit", type=int, default=300, help="Max posts to embed on CPU (default 300)")
    parser.add_argument("--dry-run",   action="store_true", help="Skip LLM, skip send")
    args = parser.parse_args()

    date_str = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    target   = args.limit
    embed_limit = args.embed_limit

    log_section(f"🚀 AI-Digest Pipeline | date={date_str} | target={target} posts | embed≤{embed_limit}")

    # 1. Load
    posts = load_posts(days=args.days)
    if not posts:
        log("❌ No posts found", "ERROR")
        sys.exit(1)

    posts = [p for p in posts if lang_ok(p)]
    log(f"🌐 After language filter: {len(posts)} posts")

    posts = [p for p in posts if is_not_compilation(p)]
    log(f"🚫 After compilation filter: {len(posts)} posts")

    posts = [p for p in posts if is_not_political(p)]
    log(f"🚫 After political filter: {len(posts)} posts")

    # 1b. CPU guard: limit posts before expensive embedding
    if len(posts) > embed_limit:
        # Keep best posts by score (first in list = freshest from scraper)
        posts = posts[:embed_limit]
        log(f"⚡ CPU mode: limiting to {embed_limit} posts for embedding")

    # 2. Embed
    texts, embeddings, valid_posts = embed_posts(posts)

    # 3. Cluster
    cluster_data = cluster(embeddings, texts, valid_posts)
    if not cluster_data:
        log("❌ No clusters found", "ERROR")
        sys.exit(1)

    # 4. Score & rank
    scored = score_clusters(cluster_data, embeddings)
    log(f"🏆 Clusters ranked: {len(scored)}")
    for i, cl in enumerate(scored[:5], 1):
        log(f"  #{i}: size={cl['size']:3d} score={cl['score']:.2f} → {cl['top_title'][:70]}")

    if args.dry_run:
        log("✅ Dry run — exiting before LLM")
        sys.exit(0)

    # 5-8. Process clusters → digest items
    log_section(f"📝 Generating digest ({target} posts)...")
    digest_items = []

    for idx, cl in enumerate(scored, 1):
        if len(digest_items) >= target:
            log(f"✅ Reached target: {target} posts")
            break

        if idx > target * 3:
            # Safety: check at most 7 clusters (≈25 posts) or 3x target, whichever is smaller
            log(f"⚠️  Checked {idx-1} clusters, only got {len(digest_items)} posts. Stopping.")
            break

        cl["rank"] = len(digest_items) + 1
        log(f"\n--- Cluster {idx}: {cl['top_title'][:70]} (score={cl['score']:.2f}) ---")

        # 5. Anti-topics check (coherence already implied by diversity filter)
        is_anti, matched = check_anti_topic(cl)
        if is_anti:
            log(f"   ⛔ Skipping: антитема — {matched}")
            continue

        # 7. Write digest post
        log(f"   ✍️  Writing digest post ({len(digest_items)+1}/{target})...")
        item = write_digest_post(cl)
        if not item:
            log(f"   ⏭  Skipping: LLM failed to generate post")
            continue

        post_text = item["text"]
        if len(post_text) < MIN_DIGEST_CHARS:
            log(f"   ⏭  Skipping: too short ({len(post_text)} chars < {MIN_DIGEST_CHARS})")
            continue

        # 8. Media
        best_url = item.get("url", "")
        media = {"images": [], "videos": []}
        if best_url:
            media = fetch_media(best_url)
            log(f"   🖼  Media: {len(media['images'])} images, {len(media['videos'])} videos")

        item["media"] = media
        item["n_source_posts"] = cl["size"]
        digest_items.append(item)
        log(f"   ✅ Added post #{len(digest_items)}: {item.get('topic','')[:60]}")

        time.sleep(0.3)  # rate limit

    if not digest_items:
        log("❌ No digest items generated", "ERROR")
        sys.exit(1)

    log(f"\n✅ Generated {len(digest_items)} digest posts")

    # 9. Build markdown
    md_content = build_digest_md(digest_items, date_str)

    # 10. Save to Obsidian
    obsidian_path = save_digest_obsidian(md_content, date_str)

    # 11. Send to Telegram
    caption = f"📡 AI-Digest за {date_str} — {len(digest_items)} тем"
    ok = send_telegram_document(obsidian_path, caption)

    log_section(f"✅ Done! {len(digest_items)} posts. Obsidian: {obsidian_path.name} | Telegram: {'✅' if ok else '❌'}")


if __name__ == "__main__":
    main()
