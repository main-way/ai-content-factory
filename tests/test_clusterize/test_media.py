"""
Tests for media extraction: fetch_media.
"""
import sys
import os
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
os.environ.setdefault("MINIMAX_API_KEY", "test-key")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "test-chat")


class TestFetchMedia:
    def _make_response(self, html):
        mock = MagicMock()
        mock.read.return_value = html.encode("utf-8", errors="ignore")
        mock.__enter__ = MagicMock(return_value=mock)
        mock.__exit__ = MagicMock(return_value=False)
        return mock

    def test_extracts_og_image(self, mocker):
        """og:image meta tag → added to images list."""
        html = '<meta property="og:image" content="https://example.com/img.jpg"/>'
        mocker.patch("urllib.request.urlopen", return_value=self._make_response(html))

        from clusterize import fetch_media
        result = fetch_media("https://example.com/article", timeout=5)

        assert "https://example.com/img.jpg" in result["images"]

    def test_extracts_twitter_image_fallback(self, mocker):
        """twitter:image used when og:image absent."""
        html = '<meta name="twitter:image" content="https://cdn.com/twitter.jpg"/>'
        mocker.patch("urllib.request.urlopen", return_value=self._make_response(html))

        from clusterize import fetch_media
        result = fetch_media("https://example.com/article")

        assert "https://cdn.com/twitter.jpg" in result["images"]

    def test_extracts_og_video(self, mocker):
        """og:video meta tag → added to videos list."""
        html = '<meta property="og:video" content="https://video.com/clip.mp4"/>'
        mocker.patch("urllib.request.urlopen", return_value=self._make_response(html))

        from clusterize import fetch_media
        result = fetch_media("https://example.com/article")

        assert "https://video.com/clip.mp4" in result["videos"]

    def test_no_media_returns_empty_lists(self, mocker):
        """Page with no og tags → both lists empty."""
        html = "<html><head><title>No media</title></head><body>Nothing</body></html>"
        mocker.patch("urllib.request.urlopen", return_value=self._make_response(html))

        from clusterize import fetch_media
        result = fetch_media("https://example.com/nomedia")

        assert result["images"] == []
        assert result["videos"] == []

    def test_timeout_returns_empty(self, mocker):
        """Fetch timeout → empty result, no exception raised."""
        mocker.patch("urllib.request.urlopen", side_effect=TimeoutError("timeout"))

        from clusterize import fetch_media
        result = fetch_media("https://example.com/slow")

        assert result["images"] == []
        assert result["videos"] == []

    def test_connection_error_returns_empty(self, mocker):
        """Connection error → empty result, no exception."""
        mocker.patch("urllib.request.urlopen", side_effect=Exception("connection refused"))

        from clusterize import fetch_media
        result = fetch_media("https://example.com/dead")

        assert result["images"] == []
        assert result["videos"] == []

    def test_deduplicates_images(self, mocker):
        """Same image URL mentioned twice → only one entry."""
        html = (
            '<meta property="og:image" content="https://ex.com/img.jpg"/>'
            '<meta property="og:image" content="https://ex.com/img.jpg"/>'
        )
        mocker.patch("urllib.request.urlopen", return_value=self._make_response(html))

        from clusterize import fetch_media
        result = fetch_media("https://example.com/article")

        assert result["images"].count("https://ex.com/img.jpg") == 1
