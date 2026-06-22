#!/usr/bin/env python3
"""
clusterize.py — Clustering pipeline для AI-Digest.

Собирает посты за день, эмбеддит, кластеризует (UMAP + HDBSCAN),
анализирует топ-10 кластеров через LLM и отправляет результат в Telegram.

Использование:
    python clusterize.py                     # сегодня (после fetch)
    python clusterize.py --date 2026-06-22   # конкретная дата
    python clusterize.py --fetch-hours 24    # сначала fetch на 24ч
    python clusterize.py --days 3            # за 3 дня (rolling window)
    python clusterize.py --dry-run           # только clustering, без LLM
"""

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# ─── Vendor imports (installed separately) ───────────────────────────────────
try:
    import torch
    import numpy as np
    from sentence_transformers import SentenceTransformer
    import umap
    import hdbscan
    from sklearn.feature_extraction.text import CountVectorizer
    import nltk
    NLTK_AVAILABLE = True
except ImportError as e:
    print(f"❌ Missing dependency: {e}")
    print("   Run: pip install sentence-transformers umap-learn hdbscan scikit-learn nltk")
    sys.exit(1)

# ─── Constants ───────────────────────────────────────────────────────────────
PROJECT_DIR = Path(__file__).parent
STORAGE_DIR = PROJECT_DIR / "storage"
CLUSTER_DIR = PROJECT_DIR / "clusters"
CLUSTER_DIR.mkdir(exist_ok=True)

EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
UMAP_N_COMPONENTS = 20
UMAP_MIN_DIST = 0.1
HDBSCAN_MIN_CLUSTER_SIZE = 3
HDBSCAN_MIN_SAMPLES = 2

# Telegram — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "151453888")

# LLM (MiniMax API — use api.minimax.io, NOT api.minimax.chat)
LLM_API_KEY = os.environ.get("MINIMAX_API_KEY", "")
LLM_BASE_URL = "https://api.minimax.io/v1"
LLM_MODEL = "MiniMax-M2"  # NOT MiniMax-M2-7B — use exact model name

# ─── Helpers ─────────────────────────────────────────────────────────────────

def log(msg: str, level: str = "INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", file=sys.stderr)


def hash_url(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:12]


def load_posts(date_str: str = None, days: int = 1) -> list[dict]:
    """Load posts from storage. If days > 1, merge multiple days."""
    if date_str:
        dates = [date_str]
    else:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        dates = []
        for i in range(days):
            d = datetime.now(timezone.utc) - timedelta(days=i)
            dates.append(d.strftime("%Y-%m-%d"))

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
            log(f"  Loaded {len(posts)} posts from {d}")
            all_posts.extend(posts)

    # Deduplicate by URL
    seen_urls = set()
    unique_posts = []
    for p in all_posts:
        url = p.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            unique_posts.append(p)

    log(f"📰 Total unique posts: {len(unique_posts)} (deduped)")
    return unique_posts


def fetch_today(hours: int = 24) -> list[dict]:
    """Run fetch.py and load the resulting posts."""
    log(f"📡 Running fetch.py (last {hours}h)...")
    import subprocess
    result = subprocess.run(
        [sys.executable, str(PROJECT_DIR / "fetch.py"),
         "--hours", str(hours), "--storage", "storage"],
        capture_output=True, text=True, cwd=str(PROJECT_DIR)
    )
    if result.returncode != 0:
        log(f"⚠️  fetch.py failed: {result.stderr[-300:]}", "WARN")
    # Load what was just fetched
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = STORAGE_DIR / f"posts_{today}.json"
    if path.exists():
        with open(path) as f:
            data = json.load(f)
        return data.get("posts", [])
    return []


def prepare_text(post: dict) -> str:
    """Build embedding-ready text from a post."""
    title = post.get("title", "")
    summary = post.get("summary", "")
    source = post.get("source", "")
    # Title is most informative; prepend it
    return f"{title} [source: {source}] {summary}".strip()


# ─── Embedding ───────────────────────────────────────────────────────────────

def embed_posts(posts: list[dict], model_name: str = EMBEDDING_MODEL) -> tuple[list[str], np.ndarray, list[dict]]:
    """Embed posts using sentence-transformers. Returns (texts, embeddings, post_list)."""
    log(f"🧠 Loading embedding model: {model_name}")
    model = SentenceTransformer(model_name)

    texts = [prepare_text(p) for p in posts]
    valid_idx = [i for i, t in enumerate(texts) if t.strip()]

    # Filter empty
    valid_posts = [posts[i] for i in valid_idx]
    valid_texts = [texts[i] for i in valid_idx]

    log(f"📝 Embedding {len(valid_texts)} texts...")
    t0 = time.time()
    embeddings = model.encode(valid_texts, show_progress_bar=True,
                               batch_size=16, convert_to_numpy=True)
    log(f"   Done in {time.time() - t0:.1f}s, shape: {embeddings.shape}")
    return valid_texts, embeddings, valid_posts


# ─── Clustering ──────────────────────────────────────────────────────────────

def cluster(embeddings: np.ndarray, texts: list[str], posts: list[dict]
            ) -> tuple[dict, np.ndarray]:
    """
    Cluster embeddings with UMAP + HDBSCAN.
    Returns (cluster_data: dict[cluster_id -> {posts, texts, centroid_idx}],
             labels: np.ndarray)
    """
    log(f"🔵 UMAP dimensionality reduction: {embeddings.shape} → ({embeddings.shape[0]}, {UMAP_N_COMPONENTS})")
    t0 = time.time()
    reducer = umap.UMAP(
        n_components=UMAP_N_COMPONENTS,
        min_dist=UMAP_MIN_DIST,
        metric="cosine",
        random_state=42,
        n_jobs=1,
    )
    reduced = reducer.fit_transform(embeddings)
    log(f"   UMAP done in {time.time() - t0:.1f}s")

    log(f"🔴 HDBSCAN clustering (min_cluster_size={HDBSCAN_MIN_CLUSTER_SIZE})...")
    t0 = time.time()
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=HDBSCAN_MIN_CLUSTER_SIZE,
        min_samples=HDBSCAN_MIN_SAMPLES,
        metric="euclidean",
        cluster_selection_method="eom",
        prediction_data=True,
    )
    labels = clusterer.fit_predict(reduced)
    log(f"   HDBSCAN done in {time.time() - t0:.1f}s, clusters: {len(set(labels)) - (1 if -1 in labels else 0)}")

    n_noise = int((labels == -1).sum())
    log(f"   Noise points (outliers): {n_noise}/{len(labels)}")

    # Build cluster_data
    cluster_data = {}
    for i, label in enumerate(labels):
        if label == -1:
            continue
        if label not in cluster_data:
            cluster_data[label] = {"indices": [], "posts": [], "texts": []}
        cluster_data[label]["indices"].append(i)
        cluster_data[label]["posts"].append(posts[i])
        cluster_data[label]["texts"].append(texts[i])

    return cluster_data, labels


def cluster_stats(cluster_data: dict, embeddings: np.ndarray,
                  texts: list[str]) -> list[dict]:
    """Compute ranking stats for each cluster. Returns sorted list."""

    def avg_interpoint_dist(indices, centroid_idx):
        """Lower = more compact cluster."""
        if len(indices) < 2:
            return 0.0
        e = embeddings[indices]
        c = embeddings[centroid_idx]
        dists = np.linalg.norm(e - c, axis=1)
        return float(dists.mean())

    scored_clusters = []
    for cid, cd in cluster_data.items():
        indices = cd["indices"]
        posts_l = cd["posts"]
        texts_l = cd["texts"]

        # Centroid (mean embedding)
        centroid_idx = indices[np.linalg.norm(
            embeddings[indices] - embeddings[indices].mean(axis=0),
            axis=1
        ).argmin()]

        # Source diversity
        sources = set(p.get("source", "") for p in posts_l)
        domain_sources = set()
        for p in posts_l:
            url = p.get("url", "")
            if url:
                m = re.search(r"https?://([^/]+)", url)
                if m:
                    domain_sources.add(m.group(1))

        # Date spread (how recent)
        recent_count = 0
        cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
        for p in posts_l:
            pub = p.get("published", "")
            if pub:
                try:
                    from dateutil import parser as dp
                    dt = dp.parse(pub)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if dt > cutoff:
                        recent_count += 1
                except Exception:
                    pass

        size = len(indices)
        diversity = len(domain_sources) / max(size, 1)
        velocity = recent_count / max(size, 1)
        spread = 1.0 / (1.0 + avg_interpoint_dist(indices, centroid_idx))

        # ── Filters ────────────────────────────────────────────────────────────
        # Skip low-diversity clusters (near-duplicate spam, same story copy-pasted)
        MIN_DIVERSITY = 0.12
        if diversity < MIN_DIVERSITY:
            continue

        # ── Scoring ─────────────────────────────────────────────────────────────
        # Original: size × velocity × diversity × spread  (size dominates)
        # Fixed: diversity and velocity matter more; size is capped
        # log-size prevents mega-clusters from crushing everything
        size_score = math.log1p(size)           # log(1+size) — diminishing returns
        score = (
            size_score
            * (0.5 + 0.5 * velocity)           # fresh news bonus
            * (0.3 + 0.7 * diversity)           # diversity weight increased
            * (0.4 + 0.6 * spread)              # compactness matters
        )

        # Top title (most central post)
        top_post = posts_l[np.linalg.norm(
            embeddings[indices] - embeddings[indices].mean(axis=0),
            axis=1
        ).argmin()]

        scored_clusters.append({
            "cluster_id": int(cid),
            "size": size,
            "score": round(score, 3),
            "velocity": round(velocity, 2),
            "diversity": round(diversity, 2),
            "spread": round(spread, 3),
            "top_title": top_post.get("title", "")[:120],
            "top_url": top_post.get("url", ""),
            "top_source": top_post.get("source", ""),
            "sources": list(domain_sources)[:8],
            "posts": posts_l,
            "texts": texts_l,
        })

    scored_clusters.sort(key=lambda x: -x["score"])
    return scored_clusters


# ─── LLM Analysis ────────────────────────────────────────────────────────────

def llm_analyze_clusters(top_clusters: list[dict], model: str = LLM_MODEL
                         ) -> list[dict]:
    """Send top clusters to LLM for coherence analysis."""
    if not LLM_API_KEY:
        log("⚠️  MINIMAX_API_KEY not set, skipping LLM analysis", "WARN")
        return []

    import urllib.request
    import urllib.error

    results = []
    for rank, cl in enumerate(top_clusters[:10], 1):
        posts_l = cl["posts"]
        titles = [p.get("title", "")[:150] for p in posts_l[:12]]
        summaries = [p.get("summary", "")[:200] for p in posts_l[:8]]

        prompt = f"""You are analyzing a news cluster for an AI/tech newsletter.
A cluster contains {len(posts_l)} news items. Your job is to determine:
1. Is this ONE coherent story/theme, or MULTIPLE unrelated stories?
2. If ONE: write a 2-sentence narrative summary
3. If MULTIPLE: suggest how to split it
4. Rate coherence 1-10
5. Suggest the best post angle/title (in Russian)

Cluster titles:
{chr(10).join(f"{i+1}. {t}" for i, t in enumerate(titles[:12]))}

Summaries:
{chr(10).join(f"- {s}" for s in summaries[:8])}

Respond ONLY with valid JSON (no markdown):
{{
  "coherent": true or false,
  "narrative": "2-sentence summary in Russian",
  "angle": "post title suggestion in Russian (max 100 chars)",
  "coherence_score": 7,
  "story_type": "single_release" or "trend" or "multiple_unrelated",
  "key_sources": ["source1", "source2"]
}}
"""
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 500,
        }).encode()

        req = urllib.request.Request(
            f"{LLM_BASE_URL}/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {LLM_API_KEY}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.load(resp)
            content = data["choices"][0]["message"]["content"]
            # Extract JSON
            match = re.search(r"\{.*\}", content, re.DOTALL)
            if match:
                analysis = json.loads(match.group())
                cl["llm"] = analysis
                log(f"  Cluster {rank}: coherent={analysis.get('coherent')}, "
                    f"score={analysis.get('coherence_score')}, "
                    f"angle={analysis.get('angle', '')[:60]}")
            else:
                log(f"  Cluster {rank}: LLM returned non-JSON: {content[:100]}", "WARN")
                cl["llm"] = None
        except urllib.error.HTTPError as e:
            log(f"  Cluster {rank}: HTTP {e.code}: {e.read().decode()[:200]}", "ERROR")
        except Exception as e:
            log(f"  Cluster {rank}: {e}", "ERROR")

        results.append(cl)
        time.sleep(0.5)  # rate limit

    return results


# ─── Telegram ────────────────────────────────────────────────────────────────

def send_telegram(text: str, parse_mode: str = "HTML") -> bool:
    """Send message via Telegram Bot."""
    import urllib.request
    import urllib.error

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }).encode()

    req = urllib.request.Request(url, data=payload,
                                  headers={"Content-Type": "application/json"},
                                  method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.load(resp).get("ok", False)
    except Exception as e:
        log(f"⚠️  Telegram send failed: {e}", "WARN")
        return False


def format_telegram_message(clusters: list[dict]) -> str:
    """Format clustering results as Telegram HTML message."""

    header = (
        "📊 <b>AI-Digest: Кластеры за сегодня</b>\n"
        f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"🔢 Кластеров найдено: {len(clusters)}\n\n"
    )

    blocks = []
    for rank, cl in enumerate(clusters[:10], 1):
        size = cl["size"]
        score = cl["score"]
        top_title = cl["top_title"]
        top_url = cl["top_url"]
        sources = cl["sources"]
        velocity = cl["velocity"]
        diversity = cl["diversity"]

        llm_info = ""
        if cl.get("llm"):
            llm = cl["llm"]
            coherent = "✅" if llm.get("coherent") else "⚠️"
            angle = llm.get("angle", "—")
            coherence_score = llm.get("coherence_score", "—")
            llm_info = (
                f"\n   {coherent} нарратив: <i>{angle}</i>\n"
                f"   📊 Связность: {coherence_score}/10\n"
            )
            story_type = llm.get("story_type", "—")
            key_sources = llm.get("key_sources", [])
            if key_sources:
                llm_info += f"   🔗 Источники: {', '.join(key_sources[:3])}\n"

        sources_str = ", ".join(sorted(sources)[:5])

        block = (
            f"🏷 <b>Кластер #{rank}</b> | {size} шт. | score={score}\n"
            f"   📰 {top_title}\n"
            f"   🔗 {top_url[:80]}\n"
            f"   📡 Источники: {sources_str}\n"
            f"   ⚡ Velocity={velocity} | Diversity={diversity}"
            f"{llm_info}\n"
        )
        blocks.append(block)

    footer = (
        "\n━━━━━━━━━━━━━━━━━━━━\n"
        "💡 <i>Каждый кластер → тема для поста. "
        "Чем выше score, тем популярнее тема.</i>"
    )

    # Telegram message limit ~4096 chars
    msg = header + "\n\n".join(blocks) + footer
    if len(msg) > 4000:
        msg = header + "\n\n".join(blocks[:7]) + "\n\n<i>(остальные кластеры → см. в файле)</i>" + footer

    return msg


# ─── Save results ─────────────────────────────────────────────────────────────

def save_results(clusters: list[dict], date_str: str):
    """Save full clustering results to JSON."""
    out_path = CLUSTER_DIR / f"clusters_{date_str}.json"

    # Serializable version (without raw embeddings, keep posts)
    save_data = []
    for cl in clusters:
        serial = {k: v for k, v in cl.items() if k not in ("texts",)}
        # Embeddings are numpy arrays — skip. Posts are kept for digest generation.
        save_data.append(serial)

    with open(out_path, "w") as f:
        json.dump({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "date": date_str,
            "total_clusters": len(clusters),
            "clusters": save_data,
        }, f, ensure_ascii=False, indent=2)

    log(f"💾 Results saved: {out_path}")
    return out_path


# ─── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AI-Digest clustering pipeline")
    parser.add_argument("--date", help="Date in YYYY-MM-DD format (default: today)")
    parser.add_argument("--days", type=int, default=1, help="Rolling window in days (default: 1)")
    parser.add_argument("--fetch-hours", type=int, default=0,
                        help="Run fetch.py first with N hours (0=skip fetch)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip LLM analysis")
    parser.add_argument("--top-k", type=int, default=10,
                        help="Number of top clusters to analyze (default: 10)")
    parser.add_argument("--model", default=EMBEDDING_MODEL,
                        help=f"Embedding model (default: {EMBEDDING_MODEL})")
    parser.add_argument("--min-cluster-size", type=int, default=HDBSCAN_MIN_CLUSTER_SIZE,
                        help=f"HDBSCAN min_cluster_size (default: {HDBSCAN_MIN_CLUSTER_SIZE})")
    args = parser.parse_args()

    date_str = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    log(f"🚀 Clusterize pipeline | date={date_str} | days={args.days} | fetch_hours={args.fetch_hours}")

    # Step 1: Load posts
    if args.fetch_hours > 0:
        posts = fetch_today(hours=args.fetch_hours)
        if not posts:
            log("❌ No posts fetched, exiting", "ERROR")
            sys.exit(1)
    else:
        posts = load_posts(date_str=date_str, days=args.days)
        if not posts:
            log("❌ No posts found. Run with --fetch-hours 24 first.", "ERROR")
            sys.exit(1)

    # Filter to English + Russian only (per project convention)
    def lang_ok(p):
        lang = p.get("language", "en")
        return lang in ("en", "ru", "")

    posts = [p for p in posts if lang_ok(p)]
    log(f"🌐 After language filter: {len(posts)} posts")

    # Step 2: Embed
    texts, embeddings, valid_posts = embed_posts(posts, model_name=args.model)

    # Step 3: Cluster
    cluster_data, labels = cluster(embeddings, texts, valid_posts)

    if not cluster_data:
        log("❌ No clusters found (all points are noise). Exiting.", "ERROR")
        sys.exit(1)

    # Step 4: Score and rank
    scored = cluster_stats(cluster_data, embeddings, texts)
    log(f"🏆 Top clusters by score:")
    for rank, cl in enumerate(scored[:5], 1):
        log(f"  #{rank}: size={cl['size']:3d} score={cl['score']:.2f} "
            f"vel={cl['velocity']:.2f} div={cl['diversity']:.2f} → {cl['top_title'][:70]}")

    top_k = min(args.top_k, len(scored))
    top_clusters = scored[:top_k]

    # Step 5: LLM analysis
    if not args.dry_run and top_clusters:
        log(f"🤖 LLM analysis for top {top_k} clusters...")
        analyzed = llm_analyze_clusters(top_clusters)
        # Update scored with LLM results
        cl_ids = {cl["cluster_id"] for cl in analyzed}
        for cl in scored:
            if cl["cluster_id"] in cl_ids:
                for a in analyzed:
                    if a["cluster_id"] == cl["cluster_id"]:
                        cl["llm"] = a.get("llm")

    # Step 6: Save
    save_results(scored, date_str)

    # Step 7: Telegram
    msg = format_telegram_message(top_clusters if not args.dry_run else scored[:10])
    ok = send_telegram(msg)
    log(f"📱 Telegram: {'sent' if ok else 'FAILED'} ({len(msg)} chars)")

    # Summary
    log(f"\n✅ Done! Top {top_k} clusters ready.")
    log(f"   📁 {CLUSTER_DIR / f'clusters_{date_str}.json'}")
    if not args.dry_run:
        log(f"   📱 Telegram: {'✅ sent' if ok else '❌ failed'}")


if __name__ == "__main__":
    main()
