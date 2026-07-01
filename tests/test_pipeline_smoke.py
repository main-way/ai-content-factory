"""
Smoke tests — run key functions without real API/Hardware calls.
Covers: clusterize pipeline steps, check_rss, fetch.
"""
import json
import sys
import os
from pathlib import Path
from unittest.mock import patch, MagicMock
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
os.environ.setdefault("MINIMAX_API_KEY", "test-key-for-smoke")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "test-chat")


# ─── check_rss ────────────────────────────────────────────────────────────────

class TestCheckRss:
    def test_check_rss_module_imports(self):
        """check_rss.py imports without errors."""
        import check_rss  # noqa: F401
        assert True


# ─── clusterize pipeline ───────────────────────────────────────────────────────

class TestClusterPipelineSmoke:
    """Smoke-test the clusterize pipeline with mocked ML/LLM calls."""

    @pytest.fixture
    def temp_dirs(self, tmp_path):
        storage = tmp_path / "storage"
        storage.mkdir()
        (storage / "posts_2026-06-29.json").write_text(json.dumps({
            "date": "2026-06-29",
            "posts": [
                {
                    "id": f"p{i}", "url": f"https://site{i}.com/{i}",
                    "title": f"AI News {i} about transformers and language models",
                    "summary": f"Summary {i} about artificial intelligence and deep learning",
                    "source": f"Source{i}", "language": "en",
                    "published": "2026-06-29T10:00:00Z",
                }
                for i in range(20)
            ],
        }))

        archive = tmp_path / "archive"
        archive.mkdir()
        ft = archive / "full_text"
        ft.mkdir()
        for i in range(5):
            (ft / f"p{i}.txt").write_text(f"Full text for post {i} " * 50)

        output = tmp_path / "output"
        output.mkdir()

        obsidian = tmp_path / "obsidian" / "BRIEFINGS" / "AI-Digest"
        obsidian.mkdir(parents=True)

        return {
            "storage": storage,
            "archive": archive,
            "output": output,
            "obsidian": obsidian,
        }

    def test_load_and_embed_flow(self, temp_dirs, mocker):
        """load_posts → prepare_text → mock embed → cluster → score."""
        # Patch paths
        with patch("clusterize.STORAGE_DIR", temp_dirs["storage"]), \
             patch("clusterize.PROJECT_DIR", temp_dirs["archive"].parent), \
             patch("clusterize.OBSIDIAN_BASE", temp_dirs["obsidian"].parent), \
             patch("clusterize.OBSIDIAN_DIGEST", temp_dirs["obsidian"]):

            # Mock LLM
            mocker.patch("clusterize.llm_json", return_value={
                "is_anti": False, "matched_anti_topic": "",
            })
            mocker.patch("clusterize.llm_text", return_value=(
                "Сгенерированный текст дайджеста. " * 30
            ))
            # Mock embed model
            import numpy as np
            mock_model = MagicMock()
            mocker.patch("clusterize.SentenceTransformer", return_value=mock_model)
            mock_model.encode.return_value = np.random.rand(20, 384).astype(np.float32)
            # Mock UMAP
            mock_reducer = MagicMock()
            mocker.patch("clusterize.umap.UMAP", return_value=mock_reducer)
            mock_reducer.fit_transform.return_value = np.random.rand(20, 20).astype(np.float32)
            # Mock HDBSCAN
            labels = np.array([i % 5 for i in range(20)])  # 5 clusters
            mock_clusterer = MagicMock()
            mocker.patch("clusterize.hdbscan.HDBSCAN", return_value=mock_clusterer)
            mock_clusterer.fit_predict.return_value = labels
            mock_clusterer.prediction_data = True
            # Mock Telegram send
            mocker.patch("clusterize.send_telegram_document", return_value=True)

            from clusterize import load_posts, lang_ok, prepare_text

            posts = load_posts(date_str="2026-06-29")
            posts = [p for p in posts if lang_ok(p)]
            assert len(posts) == 20

            texts = [prepare_text(p) for p in posts]
            assert all(texts)

            # Score clusters (no real embeddings needed for structure check)
            import clusterize
            from clusterize import score_clusters, cluster

            emb = np.random.rand(20, 384).astype(np.float32)
            cd = cluster(emb, texts, posts)
            assert len(cd) == 5  # 5 clusters

            scored = score_clusters(cd, emb)
            assert len(scored) > 0
            # Check required fields
            c = scored[0]
            for field in ("cluster_id", "size", "score", "diversity",
                          "top_title", "top_url", "domains"):
                assert field in c, f"Missing: {field}"

    def test_build_digest_md_with_mock_items(self, temp_dirs):
        """build_digest_md produces valid markdown with required sections."""
        with patch("clusterize.OBSIDIAN_BASE", temp_dirs["obsidian"].parent), \
             patch("clusterize.OBSIDIAN_DIGEST", temp_dirs["obsidian"]):

            from clusterize import build_digest_md

            items = [
                {
                    "topic": f"Topic {i}",
                    "text": ("Развёрнутый текст поста. " * 20)[:300],
                    "url": f"https://ex.com/{i}",
                    "source": f"Src{i}",
                    "media": {"images": [], "videos": []},
                    "n_source_posts": 1,
                }
                for i in range(3)
            ]

            md = build_digest_md(items, "2026-06-29")

            assert "2026-06-29" in md
            assert "ai-digest" in md
            assert "## 📑 Содержание" in md
            assert "## 1. Topic 0" in md
            assert "## 2. Topic 1" in md
            # Note: bold markers `**` wrap the label, count follows `:** 3`
            assert "Тем в дайджесте:" in md
