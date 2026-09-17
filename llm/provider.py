"""Minimal, replaceable LLM client. Credentials and model come from the environment."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Protocol

DEFAULT_TIMEOUT_SECONDS = 180
# Extra attempts after the first call. 3 → up to 4 tries for transient 500/1234.
DEFAULT_RETRIES = 3
DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
RETRYABLE_HTTP = {429, 500, 502, 503, 504}
# Cap sleep so a flaky upstream does not stall the whole Agent turn forever.
MAX_RETRY_SLEEP_SECONDS = 8.0

INVALID_JSON_MARKER = "LLM response is not valid JSON"


class InvalidLLMJsonError(RuntimeError):
    """Model returned text that could not be parsed as a JSON object."""

    def __init__(self, message: str, *, raw_text: str | None = None):
        super().__init__(message)
        self.raw_text = raw_text


class LLMProvider(Protocol):
    def complete_json(self, *, system: str, user: str) -> dict:
        """Return a JSON object produced by the model."""


def get_llm_provider() -> LLMProvider | None:
    """Build a provider from env. Return None when the LLM is not configured."""
    api_key = _first_env("LLM_API_KEY", "OPENAI_API_KEY")
    model = _first_env("LLM_MODEL", "OPENAI_MODEL")
    if not api_key or not model:
        return None
    base_url = _first_env("LLM_BASE_URL", "OPENAI_BASE_URL") or DEFAULT_BASE_URL
    return OpenAICompatibleProvider(
        api_key=api_key,
        model=model,
        base_url=base_url,
        timeout=_env_int("LLM_TIMEOUT", DEFAULT_TIMEOUT_SECONDS),
        retries=_env_int("LLM_RETRIES", DEFAULT_RETRIES),
    )


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return None


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        return default
    return value if value >= 0 else default


class OpenAICompatibleProvider:
    """Chat Completions client that only depends on the standard library."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        retries: int = DEFAULT_RETRIES,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries

    def _complete_json_once(self, *, system: str, user: str, use_json_object: bool = True) -> dict:
        url = f"{self.base_url}/chat/completions"
        payload: dict = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if use_json_object:
            # Ask OpenAI-compatible backends (incl. Zhipu) for a JSON object body.
            payload["response_format"] = {"type": "json_object"}
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = json.loads(response.read().decode("utf-8"))

        content = body.get("choices", [{}])[0].get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("LLM returned an empty response")
        return parse_json_object(content)

    def complete_json(self, *, system: str, user: str) -> dict:
        last_error: Exception | None = None
        attempts = max(1, self.retries + 1)
        use_json_object = True
        for attempt in range(attempts):
            try:
                rewrite = attempt > 0 and isinstance(last_error, InvalidLLMJsonError)
                return self._complete_json_once(
                    system=system,
                    user=_user_for_attempt(user, rewrite=rewrite, last_error=last_error),
                    use_json_object=use_json_object,
                )
            except urllib.error.HTTPError as exc:
                detail = _read_http_error_body(exc)
                # Some gateways reject response_format; drop it and retry once.
                if (
                    use_json_object
                    and exc.code in {400, 422}
                    and "response_format" in (detail or "").lower()
                ):
                    use_json_object = False
                    last_error = RuntimeError(f"LLM HTTP {exc.code}: {detail[:300]}")
                    continue
                last_error = RuntimeError(f"LLM HTTP {exc.code}: {detail[:300]}")
                if _is_retryable_http(exc.code, detail) and attempt + 1 < attempts:
                    time.sleep(_retry_sleep_seconds(attempt))
                    continue
                raise last_error from exc
            except InvalidLLMJsonError as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    time.sleep(_retry_sleep_seconds(attempt))
                    continue
                raise
            except Exception as exc:
                if _is_retryable_network_error(exc) and attempt + 1 < attempts:
                    last_error = exc
                    time.sleep(_retry_sleep_seconds(attempt))
                    continue
                if isinstance(exc, RuntimeError) and INVALID_JSON_MARKER in str(exc):
                    raise
                raise RuntimeError(_network_error_message(exc, timeout=self.timeout)) from exc
        if isinstance(last_error, InvalidLLMJsonError):
            raise last_error
        if isinstance(last_error, RuntimeError):
            raise last_error
        raise RuntimeError(_network_error_message(last_error, timeout=self.timeout))


def _user_for_attempt(user: str, *, rewrite: bool, last_error: Exception | None) -> str:
    if not rewrite:
        return user
    raw = ""
    if isinstance(last_error, InvalidLLMJsonError) and last_error.raw_text:
        raw = last_error.raw_text[:2000]
    return (
        user
        + "\n\n[JSON_REWRITE]\n"
        + "Previous model output was not valid JSON. "
        + "Return ONE valid JSON object only. No Markdown fences, no comments, no trailing commas.\n"
        + (f"Parse error: {last_error}\n" if last_error else "")
        + (f"Previous output:\n{raw}\n" if raw else "")
    )


def _retry_sleep_seconds(attempt: int) -> float:
    """Exponential backoff: ~0.8s, 1.6s, 3.2s, … capped."""
    return min(MAX_RETRY_SLEEP_SECONDS, 0.8 * (2**attempt))


def _read_http_error_body(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", errors="replace")
    except Exception:
        return str(exc.reason or exc)


def _is_retryable_http(status: int, detail: str) -> bool:
    if status in RETRYABLE_HTTP:
        return True
    # Zhipu sometimes surfaces transient faults in the body even on odd codes.
    text = (detail or "").lower()
    return (
        '"code":"1234"' in text.replace(" ", "")
        or '"code": 1234' in text
        or "网络错误" in (detail or "")
        or "please try again" in text
        or "请稍后重试" in (detail or "")
    )


def _is_retryable_network_error(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, urllib.error.URLError, ConnectionError)):
        return True
    text = str(exc).lower()
    return (
        "timed out" in text
        or "timeout" in text
        or "temporarily unavailable" in text
        or "网络错误" in str(exc)
        or '"code":"1234"' in str(exc).replace(" ", "")
    )


def _network_error_message(exc: BaseException | None, *, timeout: int) -> str:
    text = str(exc or "").strip()
    lowered = text.lower()
    if exc is None or isinstance(exc, TimeoutError) or "timed out" in lowered or "timeout" in lowered:
        return f"LLM request timed out after {timeout}s"
    if isinstance(exc, urllib.error.URLError):
        return f"LLM request failed: {exc.reason}"
    return f"LLM request failed: {text or 'unknown network error'}"


def parse_json_object(text: str) -> dict:
    """Parse a JSON object from model output, including common GLM malformations."""
    raw = text if isinstance(text, str) else ""
    last_error: Exception | None = None
    for candidate in _json_candidates(raw):
        for variant in _repair_variants(candidate):
            try:
                parsed = json.loads(variant)
            except json.JSONDecodeError as exc:
                last_error = exc
                continue
            if isinstance(parsed, dict):
                return parsed
            last_error = RuntimeError("LLM response JSON must be an object")
    detail = str(last_error) if last_error else "unparseable"
    raise InvalidLLMJsonError(f"{INVALID_JSON_MARKER}: {detail}", raw_text=raw)


def _json_candidates(text: str) -> list[str]:
    stripped = (text or "").strip()
    if not stripped:
        return []
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    items = [stripped]
    extracted = _extract_balanced_object(stripped)
    if extracted and extracted not in items:
        items.append(extracted)
    return items


def _extract_balanced_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        ch = text[index]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            depth += 1
            continue
        if ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _repair_variants(text: str) -> list[str]:
    base = (
        text.replace("\ufeff", "")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2018", "'")
        .replace("\u2019", "'")
    )
    variants = [base]
    # Trailing commas before } or ] — common GLM slip that yields "Expecting ',' delimiter".
    no_trailing = re.sub(r",(\s*[}\]])", r"\1", base)
    if no_trailing != base:
        variants.append(no_trailing)
    # Missing commas between values (require whitespace so "" empty strings stay intact).
    inserted = re.sub(r'"\s+"', '", "', no_trailing)
    inserted = re.sub(r"([}\]])\s*([{\[])", r"\1, \2", inserted)
    inserted = re.sub(r"(\d)\s+\"", r'\1, "', inserted)
    inserted = re.sub(r"(true|false|null)\s+\"", r'\1, "', inserted, flags=re.IGNORECASE)
    if inserted not in variants:
        variants.append(inserted)
    return variants
