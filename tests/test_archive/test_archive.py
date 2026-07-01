"""
Tests for archive.py: normalize_url and Archive class.
"""
import json
import sys
import os
from pathlib import Path
from unittest.mock import patch
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("MINIMAX_API_KEY", "test-key")


class TestNormalizeUrl:
    def test_strips_utm_params(self):
        from archive import normalize_url
        url = "https://example.com/article?utm_source=twitter&fbclid=abc&utm_medium=social"
        result = normalize_url(url)
        assert "utm_" not in result
        assert "fbclid" not in result
        assert result == "https://example.com/article"

    def test_strips_from_rss(self):
        from archive import normalize_url
        url = "https://example.com/feed?from=rss&utm_campaign=xyz"
        result = normalize_url(url)
        assert "from=rss" not in result

    def test_keeps_non_tracking_params(self):
        from archive import normalize_url
        url = "https://example.com/article?id=123&sort=newest"
        result = normalize_url(url)
        assert "id=123" in result

    def test_strips_whitespace(self):
        from archive import normalize_url
        result = normalize_url("  https://example.com/  ")
        assert result == "https://example.com/"


class TestArchive:
    @pytest.fixture
    def arch_dirs(self, tmp_path):
        """Provides archive dirs and patches archive module constants."""
        arch_dir = tmp_path / "archive"
        by_date = arch_dir / "posts_by_date"
        arch_dir.mkdir()
        by_date.mkdir()
        posts_file = arch_dir / "posts.json"
        stats_file = arch_dir / "stats.json"
        return {
            "arch_dir": arch_dir,
            "by_date": by_date,
            "posts_file": posts_file,
            "stats_file": stats_file,
        }

    def test_add_deduplicates_same_url(self, arch_dirs):
        """Same URL → only one post added."""
        from archive import Archive, ARCHIVE_DIR, BY_DATE_DIR, POSTS_FILE, STATS_FILE

        with patch("archive.ARCHIVE_DIR", arch_dirs["arch_dir"]), \
             patch("archive.BY_DATE_DIR", arch_dirs["by_date"]), \
             patch("archive.POSTS_FILE", arch_dirs["posts_file"]), \
             patch("archive.STATS_FILE", arch_dirs["stats_file"]):

            a = Archive(lazy=True)
            a._index = {}
            a.posts = []

            result = a.add([
                {"url": "https://ex.com/1", "title": "First"},
                {"url": "https://ex.com/1", "title": "Second"},
            ])

            # Second post with same URL is merged (updated), not skipped.
            # The update only fills in empty fields of the existing post.
            assert result["added"] == 1
            assert result["updated"] == 1
            assert result["skipped"] == 0
            assert len(a.posts) == 1

    def test_add_skips_empty_url(self, arch_dirs):
        """Post without URL → skipped."""
        from archive import Archive, ARCHIVE_DIR, BY_DATE_DIR, POSTS_FILE, STATS_FILE

        with patch("archive.ARCHIVE_DIR", arch_dirs["arch_dir"]), \
             patch("archive.BY_DATE_DIR", arch_dirs["by_date"]), \
             patch("archive.POSTS_FILE", arch_dirs["posts_file"]), \
             patch("archive.STATS_FILE", arch_dirs["stats_file"]):

            a = Archive(lazy=True)
            a._index = {}
            a.posts = []

            result = a.add([{"title": "No URL"}])

            assert result["skipped"] == 1
            assert len(a.posts) == 0

    def test_add_merges_fields(self, arch_dirs):
        """Existing post → new non-empty fields merged."""
        from archive import Archive, ARCHIVE_DIR, BY_DATE_DIR, POSTS_FILE, STATS_FILE

        with patch("archive.ARCHIVE_DIR", arch_dirs["arch_dir"]), \
             patch("archive.BY_DATE_DIR", arch_dirs["by_date"]), \
             patch("archive.POSTS_FILE", arch_dirs["posts_file"]), \
             patch("archive.STATS_FILE", arch_dirs["stats_file"]):

            a = Archive(lazy=True)
            a._index = {}
            a.posts = []

            a.add([{"url": "https://ex.com/1", "title": "T1", "author": "Alice", "summary": ""}])
            a.add([{"url": "https://ex.com/1", "title": "T1", "author": "", "summary": "New"}])

            assert a.posts[0]["author"] == "Alice"
            assert a.posts[0]["summary"] == "New"

    def test_save_writes_posts_json(self, arch_dirs):
        """save() creates posts.json."""
        from archive import Archive, ARCHIVE_DIR, BY_DATE_DIR, POSTS_FILE, STATS_FILE

        with patch("archive.ARCHIVE_DIR", arch_dirs["arch_dir"]), \
             patch("archive.BY_DATE_DIR", arch_dirs["by_date"]), \
             patch("archive.POSTS_FILE", arch_dirs["posts_file"]), \
             patch("archive.STATS_FILE", arch_dirs["stats_file"]):

            a = Archive(lazy=True)
            a._index = {}
            a.posts = [{"url": "https://ex.com/1", "title": "T1",
                         "published": "2026-06-29T10:00:00Z"}]
            a.save()

            assert (arch_dirs["arch_dir"] / "posts.json").exists()

    def test_save_writes_by_date_file(self, arch_dirs):
        """save() creates by-date JSON."""
        from archive import Archive, ARCHIVE_DIR, BY_DATE_DIR, POSTS_FILE, STATS_FILE

        with patch("archive.ARCHIVE_DIR", arch_dirs["arch_dir"]), \
             patch("archive.BY_DATE_DIR", arch_dirs["by_date"]), \
             patch("archive.POSTS_FILE", arch_dirs["posts_file"]), \
             patch("archive.STATS_FILE", arch_dirs["stats_file"]):

            a = Archive(lazy=True)
            a._index = {}
            a.posts = [{"url": "https://ex.com/1", "title": "T1",
                         "published": "2026-06-29T10:00:00Z"}]
            a.save()

            assert (arch_dirs["by_date"] / "2026-06-29.json").exists()

    def test_search_finds_title_keyword(self, arch_dirs):
        """search() finds posts by title."""
        from archive import Archive, ARCHIVE_DIR, BY_DATE_DIR, POSTS_FILE, STATS_FILE

        with patch("archive.ARCHIVE_DIR", arch_dirs["arch_dir"]), \
             patch("archive.BY_DATE_DIR", arch_dirs["by_date"]), \
             patch("archive.POSTS_FILE", arch_dirs["posts_file"]), \
             patch("archive.STATS_FILE", arch_dirs["stats_file"]):

            a = Archive(lazy=True)
            a._index = {}
            a.posts = [
                {"url": "https://ex.com/1", "title": "GPT-5 announced",
                 "summary": "OpenAI news"},
            ]

            results = a.search("GPT")
            assert len(results) >= 1
            assert any("GPT" in p.get("title", "") for p in results)

    def test_by_date_returns_correct_posts(self, arch_dirs):
        """by_date() returns posts from that day only."""
        from archive import Archive, ARCHIVE_DIR, BY_DATE_DIR, POSTS_FILE, STATS_FILE

        with patch("archive.ARCHIVE_DIR", arch_dirs["arch_dir"]), \
             patch("archive.BY_DATE_DIR", arch_dirs["by_date"]), \
             patch("archive.POSTS_FILE", arch_dirs["posts_file"]), \
             patch("archive.STATS_FILE", arch_dirs["stats_file"]):

            a = Archive(lazy=True)
            a._index = {}
            a.posts = [
                {"url": "https://ex.com/1", "title": "T1",
                 "published": "2026-06-29T10:00:00Z"},
                {"url": "https://ex.com/2", "title": "T2",
                 "published": "2026-06-28T10:00:00Z"},
            ]

            june29 = a.by_date("2026-06-29")
            assert len(june29) == 1
            assert june29[0]["title"] == "T1"

    def test_by_date_unknown_date_empty(self, arch_dirs):
        """by_date() for date with no posts → empty list."""
        from archive import Archive, ARCHIVE_DIR, BY_DATE_DIR, POSTS_FILE, STATS_FILE

        with patch("archive.ARCHIVE_DIR", arch_dirs["arch_dir"]), \
             patch("archive.BY_DATE_DIR", arch_dirs["by_date"]), \
             patch("archive.POSTS_FILE", arch_dirs["posts_file"]), \
             patch("archive.STATS_FILE", arch_dirs["stats_file"]):

            a = Archive(lazy=True)
            a._index = {}
            a.posts = [{"url": "https://ex.com/1", "title": "T1",
                         "published": "2026-06-29T10:00:00Z"}]

            assert a.by_date("2025-01-01") == []
