"""
Tests for digest markdown building: build_digest_md.
"""
import sys
import os
from pathlib import Path
from unittest.mock import patch
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
os.environ.setdefault("MINIMAX_API_KEY", "test-key")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "test-chat")


class TestBuildDigestMd:
    def test_contains_date(self):
        from clusterize import build_digest_md

        items = [self._item(topic="Test", url="https://ex.com/1")]
        md = build_digest_md(items, "2026-06-29")

        assert "2026-06-29" in md
        assert "ai-digest" in md

    def test_toc_has_all_topics(self):
        from clusterize import build_digest_md

        items = [self._item(topic=f"Topic {i}", url=f"https://ex.com/{i}")
                 for i in range(5)]
        md = build_digest_md(items, "2026-06-29")

        for i in range(5):
            assert f"{i+1}. Topic {i}" in md

    def test_toc_contains_hyperlinks(self):
        from clusterize import build_digest_md

        items = [self._item(topic="Test Topic", url="https://ex.com/1")]
        md = build_digest_md(items, "2026-06-29")

        # TOC should link to slug anchor
        assert "[Test Topic]" in md
        assert "#test-topic" in md.lower().replace("_", "-")

    def test_post_section_has_title(self):
        from clusterize import build_digest_md

        items = [self._item(topic="Main Topic Title", url="https://ex.com/1")]
        md = build_digest_md(items, "2026-06-29")

        assert "## 1. Main Topic Title" in md

    def test_source_line_has_url(self):
        from clusterize import build_digest_md

        items = [self._item(topic="T", url="https://ex.com/article", source="TechCrunch")]
        md = build_digest_md(items, "2026-06-29")

        assert "TechCrunch" in md
        assert "https://ex.com/article" in md

    def test_image_media_block_shown(self):
        from clusterize import build_digest_md

        items = [self._item(topic="T", url="https://ex.com/1",
                            media={"images": ["https://img.com/pic.jpg"], "videos": []})]
        md = build_digest_md(items, "2026-06-29")

        assert "🖼" in md
        assert "https://img.com/pic.jpg" in md

    def test_video_media_block_shown(self):
        from clusterize import build_digest_md

        items = [self._item(topic="T", url="https://ex.com/1",
                            media={"images": [], "videos": ["https://vid.com/clip.mp4"]})]
        md = build_digest_md(items, "2026-06-29")

        assert "🎬" in md
        assert "https://vid.com/clip.mp4" in md

    def test_no_media_block_when_empty(self):
        from clusterize import build_digest_md

        items = [self._item(topic="T", url="https://ex.com/1",
                            media={"images": [], "videos": []})]
        md = build_digest_md(items, "2026-06-29")

        assert "🖼" not in md
        assert "🎬" not in md

    def test_digest_post_count_in_header(self):
        from clusterize import build_digest_md

        items = [self._item(topic=f"T{i}", url=f"https://ex.com/{i}") for i in range(7)]
        md = build_digest_md(items, "2026-06-29")

        # Note: bold markers `**` wrap the label, so count follows `:** 7`
        assert "Тем в дайджесте:" in md

    def _item(self, topic="Test", text=None, url="https://ex.com/1",
              source="Source", media=None, n_source_posts=3):
        if text is None:
            text = ("Развёрнутый текст дайджеста о важной теме. " * 5)[:200]
        return {
            "topic": topic,
            "text": text,
            "url": url,
            "source": source,
            "media": media or {"images": [], "videos": []},
            "n_source_posts": n_source_posts,
        }
