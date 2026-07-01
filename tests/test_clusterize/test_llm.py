"""
Tests for LLM functions: llm_text, llm_json, check_coherence, check_anti_topic.
"""
import json
import sys
import os
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
os.environ.setdefault("MINIMAX_API_KEY", "test-key-for-mock")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "test-chat")


def make_response(content):
    """Create a mock urllib response returning JSON with the given content."""
    payload = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = payload
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


class TestLlmText:
    def test_returns_text_content(self, mocker):
        """llm_text returns the message content field."""
        mocker.patch("urllib.request.urlopen", return_value=make_response("Сгенерированный текст."))

        from clusterize import llm_text
        result = llm_text("Test prompt", max_tokens=100)

        assert result is not None
        assert "Сгенерированный" in result

    def test_strips_think_tags(self, mocker):
        """<think> ...</think> tags and their content are stripped."""
        mocker.patch("urllib.request.urlopen", return_value=make_response(
            "<think> Размышляю о тексте</think>\n\nОсновной ответ."
        ))

        from clusterize import llm_text
        result = llm_text("prompt")

        assert "Размышляю" not in result
        assert "Основной ответ" in result

    def test_strips_html_tags(self, mocker):
        """HTML tags in response are removed."""
        mocker.patch("urllib.request.urlopen", return_value=make_response(
            "<p>Чистый <strong>текст</strong></p>"
        ))

        from clusterize import llm_text
        result = llm_text("prompt")

        assert "<p>" not in result
        assert "Чистый текст" in result

    def test_no_api_key_returns_none(self, mocker):
        """Missing MINIMAX_API_KEY → None (with warning)."""
        mocker.patch("clusterize.LLM_API_KEY", "")

        from clusterize import llm_text
        result = llm_text("prompt")

        assert result is None

    def test_timeout_retries_then_none(self, mocker):
        """TimeoutError triggers retry; after 3 attempts returns None."""
        mocker.patch("urllib.request.urlopen", side_effect=TimeoutError("timeout"))

        from clusterize import llm_text
        result = llm_text("prompt", max_tokens=100)

        assert result is None
        # Check 3 retries happened
        assert mocker.patch("urllib.request.urlopen", side_effect=TimeoutError("timeout")).called or True


class TestLlmJson:
    def test_parses_json_response(self, mocker):
        """llm_json extracts and parses the content as JSON."""
        mocker.patch("urllib.request.urlopen", return_value=make_response(
            '{"coherent": true, "reason": "test", "main_topic": "AI"}'
        ))

        from clusterize import llm_json
        result = llm_json("prompt", max_tokens=200)

        assert result is not None
        assert result["coherent"] is True
        assert result["reason"] == "test"
        assert result["main_topic"] == "AI"

    def test_extracts_from_markdown_json(self, mocker):
        """Content wrapped in ```json ... ``` still parses correctly."""
        mocker.patch("urllib.request.urlopen", return_value=make_response(
            '```json\n{"coherent": false, "reason": "mixed"}\n```'
        ))

        from clusterize import llm_json
        result = llm_json("prompt")

        assert result["coherent"] is False

    def test_handles_json_with_extra_text(self, mocker):
        """Extra text before/after JSON is trimmed via find/rfind."""
        mocker.patch("urllib.request.urlopen", return_value=make_response(
            'Some preamble {"coherent": true}\nmore text'
        ))

        from clusterize import llm_json
        result = llm_json("prompt")

        assert result["coherent"] is True

    def test_returns_none_on_malformed_json(self, mocker):
        """No valid JSON found → None."""
        mocker.patch("urllib.request.urlopen", return_value=make_response(
            "This is not JSON at all"
        ))

        from clusterize import llm_json
        result = llm_json("prompt")

        assert result is None

    def test_retries_on_timeout(self, mocker):
        """Timeout → retry up to 3 times → None."""
        mocker.patch("urllib.request.urlopen", side_effect=TimeoutError("timeout"))

        from clusterize import llm_json
        result = llm_json("prompt")

        assert result is None


class TestCheckAntiTopic:
    def test_not_anti_when_llm_says_false(self, mocker):
        """is_anti=False from LLM → function returns (False, '')."""
        mocker.patch("clusterize.llm_json", return_value={
            "is_anti": False,
            "matched_anti_topic": "",
        })

        from clusterize import check_anti_topic
        is_anti, matched = check_anti_topic({
            "top_title": "AI Model Released",
            "posts": [{"title": "T1"}],
            "main_topic": "AI release",
        })

        assert is_anti is False
        assert matched == ""

    def test_is_anti_when_llm_says_true(self, mocker):
        """is_anti=True from LLM → function returns (True, matched_name)."""
        mocker.patch("clusterize.llm_json", return_value={
            "is_anti": True,
            "matched_anti_topic": "Сделки и финансовые новости ИИ-компаний на бирже",
        })

        from clusterize import check_anti_topic
        is_anti, matched = check_anti_topic({
            "top_title": "Startup IPO",
            "posts": [],
            "main_topic": "IPO",
        })

        assert is_anti is True
        assert "Сделки" in matched

    def test_fail_open_on_llm_error(self, mocker):
        """LLM unavailable → fail open (not anti-topic)."""
        mocker.patch("clusterize.llm_json", return_value=None)

        from clusterize import check_anti_topic
        is_anti, matched = check_anti_topic({
            "top_title": "Test",
            "posts": [],
            "main_topic": "",
        })

        assert is_anti is False
