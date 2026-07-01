"""
conftest.py — Shared pytest fixtures for AI-Digest tests.
"""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add project root to path so we can import modules
sys.path.insert(0, str(Path(__file__).parent.parent))


# ─── Sample post factories ──────────────────────────────────────────────────

def make_post(id="abc123", title="Test Article", summary="This is a test.",
              url="https://example.com/article", source="TestSource",
              language="en", published=None, full_text=None, **kwargs):
    """Create a sample post dict with sensible defaults."""
    post = {
        "id": id,
        "title": title,
        "summary": summary,
        "url": url,
        "source": source,
        "language": language,
        "published": published or "2026-06-29T10:00:00Z",
        "fetched_at": "2026-06-29T10:05:00Z",
        "category": "AI",
        **kwargs,
    }
    if full_text is not None:
        post["full_text"] = full_text
    return post


def make_posts_json(posts, date_str="2026-06-29"):
    """Wrap a list of posts into the storage JSON format."""
    return json.dumps({
        "date": date_str,
        "count": len(posts),
        "posts": posts,
    })


# ─── Temp dirs ────────────────────────────────────────────────────────────────

@pytest.fixture
def temp_storage(tmp_path):
    """Temp storage dir with a sample posts JSON."""
    storage = tmp_path / "storage"
    storage.mkdir()
    posts_file = storage / "posts_2026-06-29.json"
    posts_file.write_text(json.dumps({
        "date": "2026-06-29",
        "count": 3,
        "posts": [
            make_post(id="p1", title="AI News One", url="https://example.com/1"),
            make_post(id="p2", title="AI News Two", url="https://example.com/2"),
            make_post(id="p3", title="AI News Three", url="https://example.com/3"),
        ],
    }))
    return {"dir": storage, "posts_file": posts_file}


@pytest.fixture
def temp_archive(tmp_path):
    """Temp archive dir with full_text subdir."""
    archive = tmp_path / "archive"
    full_text = archive / "full_text"
    full_text.mkdir(parents=True)
    # Write some full_text files
    (full_text / "p1.txt").write_text("Full text for post 1 with AI content about transformers.")
    (full_text / "p2.txt").write_text("Full text for post 2 about language models and GPT.")
    return {"dir": archive, "full_text_dir": full_text}


# ─── Mock LLM ────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_llm_text(mocker):
    """Mock llm_text to return a predictable Russian paragraph."""
    return mocker.patch(
        "clusterize.llm_text",
        return_value="Это тестовый сгенерированный дайджест о важных AI-новостях за день. "
                     "В статье рассматриваются ключевые тренды развития искусственного интеллекта, "
                     "новые модели и инструменты, а также практическое применение технологий "
                     "в бизнесе и разработке продуктов."
                     "Общая длина этого текста превышает минимальные требования в 300 символов."
    )


@pytest.fixture
def mock_llm_json(mocker):
    """Mock llm_json to return coherence/anti-topic checks."""
    def _make_response(is_ok=True, reason="coherent", topic="AI news", matched=""):
        return {
            "coherent": is_ok,
            "reason": reason,
            "main_topic": topic,
            "is_anti": not is_ok,
            "matched_anti_topic": matched,
        }
    return mocker.patch("clusterize.llm_json", side_effect=lambda *a, **kw: _make_response())


# ─── Mock HTTP / urllib ───────────────────────────────────────────────────────

@pytest.fixture
def mock_urlopen(mocker):
    """Mock urllib.request.urlopen for LLM and media requests."""
    return mocker.patch("urllib.request.urlopen")


# ─── Mock Torch / Sentence Transformers ─────────────────────────────────────

@pytest.fixture
def mock_embed_model(mocker):
    """Mock SentenceTransformer so embed_posts doesn't load the real model."""
    mock_model = MagicMock()
    mock_embeddings = mocker.patch("clusterize.SentenceTransformer", return_value=mock_model)
    # encode returns shape (N, 384)
    import numpy as np
    def encode_side_effect(texts, **kwargs):
        n = len(texts)
        return np.random.rand(n, 384).astype(np.float32)
    mock_model.encode.side_effect = encode_side_effect
    return mock_embeddings


# ─── Mock UMAP / HDBSCAN ─────────────────────────────────────────────────────

@pytest.fixture
def mock_umap_hdbscan(mocker):
    """Mock UMAP and HDBSCAN so cluster() runs without real ML libs."""
    import numpy as np

    mock_reducer = MagicMock()
    mock_reducer.fit_transform.return_value = np.random.rand(10, 20).astype(np.float32)
    mocker.patch("clusterize.umap.UMAP", return_value=mock_reducer)

    mock_clusterer = MagicMock()
    # 3 clusters: labels 0, 1, 2; -1 = noise
    labels = np.array([0, 0, 1, 1, 1, 2, 2, 2, -1, -1])
    mock_clusterer.fit_predict.return_value = labels
    # prediction_data for hdbscan
    mock_clusterer.prediction_data = True
    mocker.patch("clusterize.hdbscan.HDBSCAN", return_value=mock_clusterer)

    return {"reducer": mock_reducer, "clusterer": mock_clusterer}
