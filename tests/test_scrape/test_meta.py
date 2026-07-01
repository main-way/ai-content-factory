"""
Tests for scrape.py: load_meta, save_meta, scrape_one.
"""
import json
import sys
import os
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestLoadMeta:
    def test_loads_valid_json(self, tmp_path):
        idx = tmp_path / "_index.json"
        idx.write_text(json.dumps({
            "post1": {"url": "https://a.com/1", "length": 500, "scraped_at": "2026-06-29"},
            "post2": {"url": "https://b.com/2", "length": 300, "scraped_at": "2026-06-29"},
        }))

        import scrape
        orig_base = scrape.BASE
        orig_meta = scrape.META_FILE
        scrape.BASE = tmp_path
        scrape.META_FILE = idx

        result = scrape.load_meta()

        assert result["post1"]["url"] == "https://a.com/1"
        assert result["post2"]["length"] == 300

        scrape.BASE = orig_base
        scrape.META_FILE = orig_meta

    def test_missing_file_returns_empty_dict(self, tmp_path):
        """No _index.json → empty dict."""
        import scrape
        orig_base = scrape.BASE
        orig_meta = scrape.META_FILE
        scrape.BASE = tmp_path
        scrape.META_FILE = tmp_path / "_index.json"

        result = scrape.load_meta()

        assert result == {}
        scrape.BASE = orig_base
        scrape.META_FILE = orig_meta

    def test_corrupted_json_returns_empty_dict(self, tmp_path):
        """Corrupted JSON → empty dict, no exception."""
        idx = tmp_path / "_index.json"
        idx.write_text("{not json at all")

        import scrape
        orig_base = scrape.BASE
        orig_meta = scrape.META_FILE
        scrape.BASE = tmp_path
        scrape.META_FILE = idx

        result = scrape.load_meta()

        assert result == {}
        scrape.BASE = orig_base
        scrape.META_FILE = orig_meta


class TestSaveMeta:
    def test_writes_json_file(self, tmp_path):
        import scrape
        orig_base = scrape.BASE
        orig_meta = scrape.META_FILE
        idx = tmp_path / "_index.json"
        scrape.BASE = tmp_path
        scrape.META_FILE = idx

        scrape.save_meta({"key": {"status": "ok", "length": 123}})

        loaded = json.loads(idx.read_text())
        assert loaded["key"]["status"] == "ok"

        scrape.BASE = orig_base
        scrape.META_FILE = orig_meta


class TestScrapeOne:
    def _make_post(self, id="p1", url="https://example.com/article", title="Article"):
        return {"id": id, "url": url, "title": title, "source": "TestSrc"}

    def test_extracts_and_saves_text(self, tmp_path, mocker):
        """Successful scrape → saves file and returns ok status."""
        mocker.patch("scrape.requests.get", return_value=MagicMock(
            status_code=200,
            text="<html><body><p>Article main content here.</p></body></html>",
        ))
        mocker.patch("scrape.trafilatura.extract",
                     return_value=("Article main content here with enough characters "
                                   "to exceed the 200 character minimum threshold required "
                                   "for a valid scrape result. " * 3))

        import scrape
        orig_base = scrape.BASE
        orig_ft = scrape.FULL_TEXT_DIR
        scrape.BASE = tmp_path
        scrape.FULL_TEXT_DIR = tmp_path / "full_text"
        scrape.FULL_TEXT_DIR.mkdir(parents=True)

        pid, result = scrape.scrape_one(self._make_post())

        assert pid == "p1"
        assert result["status"] == "ok"
        assert result["length"] > 200
        assert (scrape.FULL_TEXT_DIR / "p1.txt").exists()

        scrape.BASE = orig_base
        scrape.FULL_TEXT_DIR = orig_ft

    def test_returns_error_on_bad_url(self, tmp_path):
        import scrape
        orig_base = scrape.BASE
        orig_ft = scrape.FULL_TEXT_DIR
        scrape.BASE = tmp_path
        scrape.FULL_TEXT_DIR = tmp_path / "full_text"

        pid, result = scrape.scrape_one({"id": "x", "url": "", "title": "T"})

        assert result["status"] == "error"
        scrape.BASE = orig_base
        scrape.FULL_TEXT_DIR = orig_ft

    def test_returns_too_short_when_content_small(self, tmp_path, mocker):
        """Extracted text < 200 chars → too_short status."""
        mocker.patch("scrape.requests.get", return_value=MagicMock(
            status_code=200, text="<html><body><p>Short</p></body></html>",
        ))
        mocker.patch("scrape.trafilatura.extract", return_value="Short text.")

        import scrape
        orig_base = scrape.BASE
        orig_ft = scrape.FULL_TEXT_DIR
        scrape.BASE = tmp_path
        scrape.FULL_TEXT_DIR = tmp_path / "full_text"
        scrape.FULL_TEXT_DIR.mkdir(parents=True)

        pid, result = scrape.scrape_one(self._make_post())

        assert result["status"] == "too_short"
        scrape.BASE = orig_base
        scrape.FULL_TEXT_DIR = orig_ft

    def test_timeout_returns_timeout_status(self, tmp_path, mocker):
        """requests.exceptions.Timeout → timeout status."""
        import requests.exceptions
        mocker.patch("scrape.requests.get", side_effect=requests.exceptions.Timeout())

        import scrape
        orig_base = scrape.BASE
        orig_ft = scrape.FULL_TEXT_DIR
        scrape.BASE = tmp_path
        scrape.FULL_TEXT_DIR = tmp_path / "full_text"
        scrape.FULL_TEXT_DIR.mkdir(parents=True)

        pid, result = scrape.scrape_one(self._make_post())

        assert result["status"] == "timeout"
        scrape.BASE = orig_base
        scrape.FULL_TEXT_DIR = orig_ft

    def test_non_200_http_code_returns_error(self, tmp_path, mocker):
        """HTTP 404 → error status."""
        mocker.patch("scrape.requests.get", return_value=MagicMock(status_code=404))

        import scrape
        orig_base = scrape.BASE
        orig_ft = scrape.FULL_TEXT_DIR
        scrape.BASE = tmp_path
        scrape.FULL_TEXT_DIR = tmp_path / "full_text"
        scrape.FULL_TEXT_DIR.mkdir(parents=True)

        pid, result = scrape.scrape_one(self._make_post())

        assert result["status"] == "error"
        assert "404" in result["error"]
        scrape.BASE = orig_base
        scrape.FULL_TEXT_DIR = orig_ft
