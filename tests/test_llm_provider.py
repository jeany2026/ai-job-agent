"""OpenAICompatibleProvider retries and timeout wrapping. No live LLM."""

from __future__ import annotations

import io
import json
from urllib.error import HTTPError, URLError

from llm.provider import OpenAICompatibleProvider, get_llm_provider


class _FakeResponse:
    def __init__(self, payload: dict):
        self._raw = json.dumps(
            {"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]}
        ).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_retries_timeout_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("The read operation timed out")
        return _FakeResponse({"ok": True})

    monkeypatch.setattr("llm.provider.urllib.request.urlopen", fake_urlopen)
    provider = OpenAICompatibleProvider(
        api_key="test",
        model="glm-4-flash-250414",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        timeout=5,
        retries=1,
    )
    parsed = provider.complete_json(system="s", user="u")
    assert parsed == {"ok": True}
    assert calls["n"] == 2


def test_timeout_becomes_runtime_error(monkeypatch):
    def fake_urlopen(*_args, **_kwargs):
        raise TimeoutError("The read operation timed out")

    monkeypatch.setattr("llm.provider.urllib.request.urlopen", fake_urlopen)
    provider = OpenAICompatibleProvider(
        api_key="test",
        model="glm-4-flash-250414",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        timeout=8,
        retries=0,
    )
    try:
        provider.complete_json(system="s", user="u")
    except RuntimeError as exc:
        assert "timed out after 8s" in str(exc)
        return
    raise AssertionError("timeout must become RuntimeError")


def test_http_error_is_not_swallowed(monkeypatch):
    def fake_urlopen(*_args, **_kwargs):
        raise HTTPError("https://example.test", 400, "bad", hdrs=None, fp=io.BytesIO(b'{"error":"no"}'))

    monkeypatch.setattr("llm.provider.urllib.request.urlopen", fake_urlopen)
    provider = OpenAICompatibleProvider(
        api_key="test",
        model="glm-4-flash-250414",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        retries=2,
    )
    try:
        provider.complete_json(system="s", user="u")
    except RuntimeError as exc:
        assert "LLM HTTP 400" in str(exc)
        return
    raise AssertionError("HTTP 400 must not be retried as success")


def test_urlerror_is_wrapped(monkeypatch):
    def fake_urlopen(*_args, **_kwargs):
        raise URLError("connection refused")

    monkeypatch.setattr("llm.provider.urllib.request.urlopen", fake_urlopen)
    provider = OpenAICompatibleProvider(
        api_key="test",
        model="glm-4-flash-250414",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        retries=0,
    )
    try:
        provider.complete_json(system="s", user="u")
    except RuntimeError as exc:
        assert "LLM request failed" in str(exc)
        return
    raise AssertionError("URLError must become RuntimeError")


def test_retries_http_500_then_succeeds(monkeypatch):
    calls = {"n": 0}
    sleeps: list[float] = []

    def fake_urlopen(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] <= 2:
            body = '{"error":{"code":"1234","message":"网络错误，请稍后重试。"}}'.encode("utf-8")
            raise HTTPError("https://example.test", 500, "err", hdrs=None, fp=io.BytesIO(body))
        return _FakeResponse({"ok": True})

    monkeypatch.setattr("llm.provider.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("llm.provider.time.sleep", sleeps.append)
    provider = OpenAICompatibleProvider(
        api_key="test",
        model="glm-4-flash-250414",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        retries=3,
    )
    parsed = provider.complete_json(system="s", user="u")
    assert parsed == {"ok": True}
    assert calls["n"] == 3
    assert sleeps == [0.8, 1.6]


def test_http_500_exhausted_still_raises(monkeypatch):
    def fake_urlopen(*_args, **_kwargs):
        body = '{"error":{"code":"1234","message":"网络错误，请稍后重试。"}}'.encode("utf-8")
        raise HTTPError("https://example.test", 500, "err", hdrs=None, fp=io.BytesIO(body))

    monkeypatch.setattr("llm.provider.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("llm.provider.time.sleep", lambda *_args: None)
    provider = OpenAICompatibleProvider(
        api_key="test",
        model="glm-4-flash-250414",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        retries=2,
    )
    try:
        provider.complete_json(system="s", user="u")
    except RuntimeError as exc:
        assert "LLM HTTP 500" in str(exc)
        assert "1234" in str(exc)
        return
    raise AssertionError("exhausted 500 retries must raise")


def test_parse_json_object_repairs_trailing_comma():
    from llm.provider import parse_json_object

    assert parse_json_object('{"a": 1, "b": 2,}') == {"a": 1, "b": 2}


def test_parse_json_object_repairs_missing_comma_between_keys():
    from llm.provider import parse_json_object

    assert parse_json_object('{"a": 1 "b": 2}') == {"a": 1, "b": 2}
    assert parse_json_object('{"a": "x" "b": "y"}') == {"a": "x", "b": "y"}


def test_parse_json_object_keeps_empty_string():
    from llm.provider import parse_json_object

    assert parse_json_object('{"a": "", "b": 1}') == {"a": "", "b": 1}


def test_parse_json_object_extracts_fenced_and_balanced():
    from llm.provider import parse_json_object

    text = 'Sure.\n```json\n{"ok": true}\n```\n'
    assert parse_json_object(text) == {"ok": True}


def test_parse_json_object_raises_invalid_llm_json():
    from llm.provider import InvalidLLMJsonError, parse_json_object

    try:
        parse_json_object("not json at all")
    except InvalidLLMJsonError as exc:
        assert "not valid JSON" in str(exc)
        assert exc.raw_text == "not json at all"
        return
    raise AssertionError("must raise InvalidLLMJsonError")


def test_invalid_json_is_retried_then_succeeds(monkeypatch):
    calls = {"n": 0}
    sleeps: list[float] = []

    def fake_urlopen(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResponseRaw('{"a": 1,')
        return _FakeResponse({"ok": True})

    monkeypatch.setattr("llm.provider.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("llm.provider.time.sleep", sleeps.append)
    provider = OpenAICompatibleProvider(
        api_key="test",
        model="glm-4-flash-250414",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        retries=2,
    )
    parsed = provider.complete_json(system="s", user="u")
    assert parsed == {"ok": True}
    assert calls["n"] == 2
    assert sleeps


class _FakeResponseRaw:
    def __init__(self, content: str):
        self._raw = json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_complete_json_sends_response_format_json_object(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _FakeResponse({"ok": True})

    monkeypatch.setattr("llm.provider.urllib.request.urlopen", fake_urlopen)
    provider = OpenAICompatibleProvider(
        api_key="test",
        model="glm-4-flash-250414",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        retries=0,
    )
    assert provider.complete_json(system="s", user="u") == {"ok": True}
    assert captured["body"]["response_format"] == {"type": "json_object"}


def test_get_llm_provider_defaults_to_zhipu_url(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "glm-4-flash-250414")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_RETRIES", raising=False)
    provider = get_llm_provider()
    assert provider is not None
    assert provider.base_url == "https://open.bigmodel.cn/api/paas/v4"
    assert provider.timeout == 180
    assert provider.retries == 3
