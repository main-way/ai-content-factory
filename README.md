# AI Content Factory

Automated AI news pipeline: fetches RSS feeds, clusters similar posts, scores by relevance, and generates 25–30 ready-to-use news items for channels, newsletters, and social media.

**Target audience:** B2B — manufacturing sector professionals. Russian language output.

## Architecture

```
RSS feeds (200+)
    ↓
fetch.py          — fetch & deduplicate posts → storage/posts_YYYY-MM-DD.json
    ↓
clusterize.py     — embed → HDBSCAN cluster → score → top clusters
    ↓
gen_digest.py     — generate 25-30 news items (1500-2500 chars each)
    ↓
output/*.md       — ready-to-publish digest
```

## Scripts

| File | Purpose |
|------|---------|
| `fetch.py` | Fetch RSS feeds, deduplicate, save to SQLite + JSON |
| `clusterize.py` | Cluster posts by semantic similarity, score by relevance |
| `gen_digest.py` | Generate final news items via LLM |
| `composer.py` | Compose newsletters for ListMonk |
| `content_bank.py` | Content storage and selection |
| `channel_profiles.yaml` | Per-channel topic profiles |
| `sources.yaml` | RSS feed sources (100+ feeds) |

## Quick Start

```bash
# 1. Install dependencies
uv venv .venv
source .venv/bin/activate
uv pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env with your API keys

# 3. Fetch today's news
python3 fetch.py --date 2026-06-22

# 4. Cluster and score
python3 clusterize.py --date 2026-06-22 --top-n 15

# 5. Generate digest (25-30 items)
python3 gen_digest.py --date 2026-06-22 --count 28

# Output: output/digest_28_YYYY-MM-DD.md
```

## Cron Jobs

```bash
# Fetch RSS — daily at 04:00 MSK
0 4 * * * cd /home/apps_maker/ai-digest && .venv/bin/python fetch.py

# Cluster news — daily at 05:00 MSK
0 5 * * * cd /home/apps_maker/ai-digest && bash run-cluster.sh

# Generate digest — daily at 06:00 MSK
0 6 * * * cd /home/apps_maker/ai-digest && .venv/bin/python gen_digest.py
```

## Scoring Formula

```
score = log(1 + cluster_size) × velocity × diversity × spread
```

- **cluster_size** — number of posts in cluster
- **velocity** — posts per hour in the last 6h
- **diversity** — unique sources in cluster (≥2 required)
- **spread** — URL variety across sources

## Output Format

Each news item:
- 1500–2500 characters (Russian)
- Title with source
- Context + why it matters
- Two links: source + local DB reference
- No IT jargon, no ads, facts only

## Storage

- Posts stored in `storage/posts_YYYY-MM-DD.json`
- Clusters stored in `clusters/clusters_YYYY-MM-DD.json`
- Output in `output/digest_*.md`
- Daily cleanup: only today's files are kept (no long-term storage)

## Requirements

- Python 3.11+
- uv (package manager)
- SQLite3
- MiniMax API key (or any OpenAI-compatible API)
