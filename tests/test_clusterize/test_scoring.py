"""
Tests for cluster scoring: score_clusters, avg_interpoint_dist.
"""
import sys
import os
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
os.environ.setdefault("MINIMAX_API_KEY", "test-key")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "test-chat")


def make_cluster_data(n_posts=5, domains=None):
    """Build minimal cluster_data dict as returned by cluster()."""
    if domains is None:
        domains = [f"site{i}.com" for i in range(n_posts)]

    return {
        0: {
            "indices": list(range(n_posts)),
            "posts": [
                {
                    "id": str(i),
                    "url": f"https://{domains[i]}/post{i}",
                    "title": f"Post {i}",
                    "source": f"Source{i}",
                    "published": "2026-06-29T10:00:00Z",
                }
                for i in range(n_posts)
            ],
            "texts": [f"Text for post {i}" for i in range(n_posts)],
        }
    }


class TestAvgInterpointDist:
    def test_single_point_returns_zero(self):
        from clusterize import avg_interpoint_dist
        emb = np.random.rand(5, 10).astype(np.float32)
        dist = avg_interpoint_dist(emb, [0], 0)
        assert dist == 0.0

    def test_two_points_distance(self):
        """avg = mean(euclidean from centroid to all points in indices)."""
        from clusterize import avg_interpoint_dist
        emb = np.array([[0.0, 0.0], [3.0, 4.0]], dtype=np.float32)
        # centroid_idx=0 → centroid is embeddings[0]=[0,0]
        # dists = [euclidean([0,0],[0,0]), euclidean([0,0],[3,4])] = [0, 5]
        dist = avg_interpoint_dist(emb, [0, 1], 0)
        assert dist == 2.5  # mean(0, 5)

    def test_is_symmetric(self):
        from clusterize import avg_interpoint_dist
        emb = np.random.rand(10, 5).astype(np.float32)
        dist_a = avg_interpoint_dist(emb, [0, 1, 2], 1)
        dist_b = avg_interpoint_dist(emb, [0, 1, 2], 1)
        assert dist_a == dist_b


class TestScoreClusters:
    def test_low_diversity_filtered(self):
        """Single-source cluster → diversity=1/n → may be below MIN_DIVERSITY=0.12."""
        from clusterize import score_clusters, MIN_DIVERSITY

        # 10 posts from same domain → diversity=0.1 < 0.12 → filtered out
        cd = {
            0: {
                "indices": list(range(10)),
                "posts": [
                    {"id": str(i), "url": f"https://same.com/{i}",
                     "title": f"T{i}", "source": "SameSrc",
                     "published": "2026-06-29T10:00:00Z"}
                    for i in range(10)
                ],
                "texts": [f"T{i}" for i in range(10)],
            }
        }
        emb = np.random.rand(10, 384).astype(np.float32)
        scored = score_clusters(cd, emb)
        # diversity=1 domain/10 posts=0.1 < MIN_DIVERSITY=0.12 → filtered
        assert len(scored) == 0

    def test_multi_source_cluster_kept(self):
        """5 posts from 5 domains → diversity=1.0 ≥ 0.12 → kept."""
        from clusterize import score_clusters

        cd = make_cluster_data(n_posts=5)
        emb = np.random.rand(5, 384).astype(np.float32)
        scored = score_clusters(cd, emb)

        assert len(scored) == 1
        assert scored[0]["diversity"] == 1.0
        assert scored[0]["size"] == 5

    def test_sorted_by_score_descending(self):
        """Scored clusters are sorted highest-first."""
        from clusterize import score_clusters

        # 4 clusters of different sizes
        cd = {}
        for cid, n in enumerate([2, 5, 10, 3]):
            cd[cid] = {
                "indices": list(range(cid * 10, cid * 10 + n)),
                "posts": [
                    {"id": f"{cid}_{i}", "url": f"https://s{cid}.com/{i}",
                     "title": f"C{cid}P{i}", "source": f"SC{cid}",
                     "published": "2026-06-29T10:00:00Z"}
                    for i in range(n)
                ],
                "texts": [f"C{cid}T{i}" for i in range(n)],
            }

        emb = np.random.rand(50, 384).astype(np.float32)
        scored = score_clusters(cd, emb)

        scores = [c["score"] for c in scored]
        assert scores == sorted(scores, reverse=True)

    def test_clusters_have_required_fields(self):
        """Each scored cluster includes size, score, diversity, top_title, top_url."""
        from clusterize import score_clusters

        cd = make_cluster_data(n_posts=5)
        emb = np.random.rand(5, 384).astype(np.float32)
        scored = score_clusters(cd, emb)

        assert len(scored) == 1
        c = scored[0]
        for field in ("cluster_id", "size", "score", "diversity", "spread",
                      "velocity", "top_title", "top_url", "top_source", "domains"):
            assert field in c, f"Missing field: {field}"
        assert c["size"] == 5
        assert c["score"] > 0
