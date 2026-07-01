"""
Tests for load_posts — patched STORAGE_DIR and PROJECT_DIR.
"""
import json
import sys
import os
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
os.environ.setdefault("MINIMAX_API_KEY", "test-key")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "test-chat")


def make_post(id="p1", url="https://ex.com/1", title="Test", language="en", published="2026-06-29T10:00:00Z"):
    return {
        "id": id, "url": url, "title": title,
        "summary": "Summary", "source": "TestSrc",
        "language": language, "published": published,
    }


class TestLoadPosts:
    def test_loads_existing_file(self, tmp_path):
        """load_posts reads storage/posts_YYYY-MM-DD.json."""
        storage = tmp_path / "storage"
        storage.mkdir()
        (storage / "posts_2026-06-29.json").write_text(json.dumps({
            "date": "2026-06-29",
            "posts": [
                make_post("p1", "https://ex.com/1"),
                make_post("p2", "https://ex.com/2"),
            ],
        }))

        # Also create archive dir with no full_text
        archive = tmp_path / "archive"
        archive.mkdir()
        (archive / "full_text").mkdir()

        with patch("clusterize.STORAGE_DIR", storage):
            with patch("clusterize.PROJECT_DIR", tmp_path):
                from clusterize import load_posts
                posts = load_posts(date_str="2026-06-29")

        assert len(posts) == 2
        ids = {p["id"] for p in posts}
        assert ids == {"p1", "p2"}

    def test_dedups_by_url(self, tmp_path):
        """Same URL should appear only once."""
        storage = tmp_path / "storage"
        storage.mkdir()
        (storage / "posts_2026-06-29.json").write_text(json.dumps({
            "posts": [
                make_post("p1", "https://ex.com/same"),
                make_post("p2", "https://ex.com/same"),
            ],
        }))

        archive = tmp_path / "archive"
        archive.mkdir()
        (archive / "full_text").mkdir()

        with patch("clusterize.STORAGE_DIR", storage):
            with patch("clusterize.PROJECT_DIR", tmp_path):
                from clusterize import load_posts
                posts = load_posts(date_str="2026-06-29")

        assert len(posts) == 1

    def test_enriches_full_text(self, tmp_path):
        """Posts get full_text from archive/full_text/<id>.txt."""
        storage = tmp_path / "storage"
        storage.mkdir()
        (storage / "posts_2026-06-29.json").write_text(json.dumps({
            "posts": [make_post("art123", "https://ex.com/article")],
        }))

        archive = tmp_path / "archive"
        archive.mkdir()
        ft_dir = archive / "full_text"
        ft_dir.mkdir()
        (ft_dir / "art123.txt").write_text("This is the full scraped text.")

        with patch("clusterize.STORAGE_DIR", storage):
            with patch("clusterize.PROJECT_DIR", tmp_path):
                from clusterize import load_posts
                posts = load_posts(date_str="2026-06-29")

        assert "full_text" in posts[0]
        assert posts[0]["full_text"] == "This is the full scraped text."

    def test_full_text_truncated_to_8000(self, tmp_path):
        """Full text is cut to 8000 chars."""
        storage = tmp_path / "storage"
        storage.mkdir()
        (storage / "posts_2026-06-29.json").write_text(json.dumps({
            "posts": [make_post("long1", "https://ex.com/long")],
        }))

        archive = tmp_path / "archive"
        archive.mkdir()
        ft_dir = archive / "full_text"
        ft_dir.mkdir()
        (ft_dir / "long1.txt").write_text("x" * 20000)

        with patch("clusterize.STORAGE_DIR", storage):
            with patch("clusterize.PROJECT_DIR", tmp_path):
                from clusterize import load_posts
                posts = load_posts(date_str="2026-06-29")

        assert len(posts[0]["full_text"]) == 8000

    def test_missing_date_returns_empty(self, tmp_path, capsys):
        """No file for that date → empty list + warning."""
        storage = tmp_path / "storage"
        storage.mkdir()

        archive = tmp_path / "archive"
        archive.mkdir()
        (archive / "full_text").mkdir()

        with patch("clusterize.STORAGE_DIR", storage):
            with patch("clusterize.PROJECT_DIR", tmp_path):
                from clusterize import load_posts
                posts = load_posts(date_str="2026-01-01")

        assert posts == []
