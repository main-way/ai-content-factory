"""
Tests for clusterize.py helper functions: prepare_text, lang_ok.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Patch clusterize constants before import to avoid file-not-found errors
# for Obsidian dirs that don't exist in test env
import os
os.environ.setdefault("MINIMAX_API_KEY", "test-key")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "test-chat")

from clusterize import prepare_text, lang_ok


class TestPrepareText:
    def test_basic(self):
        post = {"title": "GPT-5 Released", "summary": "OpenAI announced new model", "source": "TechCrunch"}
        result = prepare_text(post)
        assert "GPT-5 Released" in result
        assert "TechCrunch" in result
        assert "OpenAI announced new model" in result

    def test_missing_title(self):
        post = {"summary": "Some summary", "source": "BBC"}
        result = prepare_text(post)
        assert "BBC" in result

    def test_missing_source(self):
        post = {"title": "Title Only", "summary": "Summary text"}
        result = prepare_text(post)
        assert "Title Only" in result

    def test_empty_post(self):
        result = prepare_text({})
        # Empty title+summary but source="" still renders the template format
        # This documents current behaviour: f"{title} [source: {source}] {summary}"
        assert result == "[source: ]"

    def test_strips_whitespace(self):
        post = {"title": "  Spaced Title  ", "summary": "  ", "source": ""}
        result = prepare_text(post)
        assert result.strip() == result
        assert "Spaced Title" in result


class TestLangOk:
    def test_english(self):
        assert lang_ok({"language": "en"}) is True

    def test_russian(self):
        assert lang_ok({"language": "ru"}) is True

    def test_empty(self):
        assert lang_ok({"language": ""}) is True

    def test_no_language_key(self):
        assert lang_ok({}) is True

    def test_chinese_rejected(self):
        assert lang_ok({"language": "zh"}) is False

    def test_japanese_rejected(self):
        assert lang_ok({"language": "ja"}) is False

    def test_other_languages_rejected(self):
        """Languages other than en/ru are filtered out."""
        for lang in ("fr", "de", "es", "pt", "it", "zh", "ja", "ko", "ar"):
            assert lang_ok({"language": lang}) is False, f"{lang} should be rejected"
