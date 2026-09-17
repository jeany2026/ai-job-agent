"""Minimal live check: OpenAICompatibleProvider can reach GLM-4-Flash.

Requires LLM_API_KEY in the environment. Does not write or print the key.
Does not call Ollama. Does not use heuristic classification.

Optional: not a CI gate. Run with `--run-live` or RUN_LIVE=1.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from llm.provider import OpenAICompatibleProvider, get_llm_provider

GLM_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
GLM_MODEL = "glm-4-flash-250414"

pytestmark = [pytest.mark.optional, pytest.mark.live]


def _must_not_be_ollama(base_url: str) -> None:
    lowered = base_url.lower()
    if "127.0.0.1:11434" in lowered or "localhost:11434" in lowered:
        raise RuntimeError("GLM connectivity test must not call Ollama at 127.0.0.1:11434")


def test_glm_connection() -> None:
    api_key = (os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError(
            "llm_unavailable: LLM_API_KEY is not set. "
            "Set it in the local environment; do not put the key in source files."
        )

    os.environ["LLM_BASE_URL"] = GLM_BASE_URL
    os.environ["LLM_MODEL"] = GLM_MODEL

    provider = get_llm_provider()
    if provider is None:
        raise RuntimeError("llm_unavailable: get_llm_provider() returned None")
    if not isinstance(provider, OpenAICompatibleProvider):
        raise RuntimeError(
            f"Must reuse OpenAICompatibleProvider, got {type(provider).__name__}"
        )

    _must_not_be_ollama(provider.base_url)
    if provider.model != GLM_MODEL:
        raise RuntimeError(f"unexpected model: {provider.model}")
    if provider.base_url.rstrip("/") != GLM_BASE_URL:
        raise RuntimeError(f"unexpected base URL: {provider.base_url}")

    parsed = provider.complete_json(
        system="You are a connectivity probe. Return JSON only. Do not add extra keys.",
        user='请只返回 JSON：\n{"status":"ok","message":"GLM connection successful"}',
    )
    if not isinstance(parsed, dict):
        raise RuntimeError("LLM response JSON must be an object")
    if parsed.get("status") != "ok":
        raise RuntimeError(f"unexpected status in LLM JSON: {parsed!r}")
    if parsed.get("message") != "GLM connection successful":
        raise RuntimeError(f"unexpected message in LLM JSON: {parsed!r}")

    print("GLM connectivity test passed")
    print(f"provider={type(provider).__name__}")
    print(f"model={provider.model}")
    print(f"base_url={provider.base_url}")
    print(f"endpoint={provider.base_url}/chat/completions")
    print("used_ollama=false")
    print("used_heuristic=false")


if __name__ == "__main__":
    test_glm_connection()
