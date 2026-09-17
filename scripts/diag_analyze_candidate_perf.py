"""One-off performance probe for analyze_candidate. Not part of the Agent Loop.

Does not print resume text or API keys.
Uses the same provider credentials/model/URL as production, but retries=0
so timings are a single HTTP round-trip.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.resume_extract import extract_resume_text
from candidate.analyze_candidate import (
    ANALYZE_CANDIDATE_SPEC,
    ANALYZE_CANDIDATE_SYSTEM_PROMPT,
    _assert_evidence_in_resume,
    _coerce_resume,
)
from candidate.schema import SchemaError, validate_llm_payload
from llm.provider import get_llm_provider, parse_json_object

RESUME_PATH = Path(r"D:\download\鲍玲俐_简历 (2).docx")
HTTP_TIMEOUT = 180

MINIMAL_SYSTEM = """你是简历分析器。只根据简历原文提取最少结构化字段。
只返回一个 JSON 对象，不要 Markdown。
字段：summary, target_roles, years_experience, core_capabilities。
core_capabilities 最多 5 条，每条只要 name。
不要编造。"""


def estimate_tokens(text: str) -> int:
    """Rough token estimate when the API does not return usage. Not a tokenizer."""
    if not text:
        return 0
    return max(1, round(len(text) / 1.8))


def instrumented_complete(*, provider, system: str, user: str) -> dict:
    url = f"{provider.base_url}/chat/completions"
    t_payload0 = time.perf_counter()
    payload = {
        "model": provider.model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    raw_payload = json.dumps(payload).encode("utf-8")
    t_payload1 = time.perf_counter()
    request = urllib.request.Request(
        url,
        data=raw_payload,
        headers={
            "Authorization": f"Bearer {provider.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    t_send = time.perf_counter()
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
        t_headers = time.perf_counter()
        body_bytes = response.read()
        t_body = time.perf_counter()
        status = getattr(response, "status", None)
    t_json0 = time.perf_counter()
    body = json.loads(body_bytes.decode("utf-8"))
    t_json1 = time.perf_counter()
    content = body.get("choices", [{}])[0].get("message", {}).get("content")
    t_parse0 = time.perf_counter()
    parsed = parse_json_object(content) if isinstance(content, str) else None
    t_parse1 = time.perf_counter()
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    return {
        "http_status": status,
        "payload_bytes": len(raw_payload),
        "payload_build_s": round(t_payload1 - t_payload0, 4),
        "ttfb_s": round(t_headers - t_send, 3),
        "body_read_s": round(t_body - t_headers, 3),
        "http_total_s": round(t_body - t_send, 3),
        "raw_json_load_s": round(t_json1 - t_json0, 4),
        "content_parse_s": round(t_parse1 - t_parse0, 4),
        "response_bytes": len(body_bytes),
        "content_chars": len(content) if isinstance(content, str) else 0,
        "usage": {
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
        },
        "parsed": parsed,
        "endpoint": url,
        "model": provider.model,
    }


def run_case(name: str, *, provider, system: str, user: str, extra: dict | None = None) -> dict:
    print(f"\n===== {name} start =====")
    row = instrumented_complete(provider=provider, system=system, user=user)
    extra = extra or {}
    row.update(extra)
    row["name"] = name
    row["system_chars"] = len(system)
    row["user_chars"] = len(user)
    row["system_tokens_est"] = estimate_tokens(system)
    row["user_tokens_est"] = estimate_tokens(user)
    print(
        json.dumps(
            {
                "name": name,
                "http_total_s": row["http_total_s"],
                "ttfb_s": row["ttfb_s"],
                "body_read_s": row["body_read_s"],
                "payload_bytes": row["payload_bytes"],
                "response_bytes": row["response_bytes"],
                "content_chars": row["content_chars"],
                "usage": row["usage"],
                "system_chars": row["system_chars"],
                "user_chars": row["user_chars"],
            },
            ensure_ascii=False,
        )
    )
    print(f"===== {name} done =====")
    return row


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    provider = get_llm_provider()
    if provider is None:
        raise SystemExit("LLM provider is not configured")

    print("provider_class", type(provider).__name__)
    print("model", provider.model)
    print("base_url", provider.base_url)
    print("endpoint", f"{provider.base_url}/chat/completions")
    print("http_client", "urllib.request")
    print("response_format", None)
    print("json_mode", False)
    print("provider_timeout", getattr(provider, "timeout", None))
    print("provider_retries", getattr(provider, "retries", None))
    print("probe_http_timeout", HTTP_TIMEOUT)
    print("probe_retries", 0)

    t0 = time.perf_counter()
    data = RESUME_PATH.read_bytes()
    resume_text = extract_resume_text(filename=RESUME_PATH.name, data=data)
    extract_s = time.perf_counter() - t0

    t1 = time.perf_counter()
    coerced, _, coerce_error = _coerce_resume(resume_text)
    coerce_s = time.perf_counter() - t1
    if coerce_error or not coerced:
        raise SystemExit(f"resume coerce failed: {coerce_error}")

    t2 = time.perf_counter()
    system_c = ANALYZE_CANDIDATE_SYSTEM_PROMPT
    _ = len(system_c)
    system_build_s = time.perf_counter() - t2

    t3 = time.perf_counter()
    user_c = "请分析以下简历，只返回结构化 CandidateProfile JSON：\n" + coerced
    user_build_s = time.perf_counter() - t3

    spec_chars = len(json.dumps(ANALYZE_CANDIDATE_SPEC, ensure_ascii=False))

    print(
        json.dumps(
            {
                "resume_file": RESUME_PATH.name,
                "resume_file_bytes": len(data),
                "resume_chars": len(coerced),
                "resume_tokens_est": estimate_tokens(coerced),
                "extract_s": round(extract_s, 4),
                "coerce_s": round(coerce_s, 4),
                "system_prompt_build_s": round(system_build_s, 6),
                "user_prompt_build_s": round(user_build_s, 6),
                "official_system_chars": len(system_c),
                "official_user_chars": len(user_c),
                "tool_spec_chars": spec_chars,
                "schema_in_http_payload": False,
                "note": "ANALYZE_CANDIDATE_SPEC is not sent to the LLM; field rules live in the system prompt.",
            },
            ensure_ascii=False,
        )
    )

    case_a = run_case(
        "A_one_sentence",
        provider=provider,
        system="只根据简历原文回答。只返回 JSON。",
        user="请用一句话概括这份简历，只返回 JSON：{\"summary\":\"...\"}\n简历：\n" + coerced,
    )

    case_b = run_case(
        "B_minimal_profile",
        provider=provider,
        system=MINIMAL_SYSTEM,
        user="请分析以下简历，只返回最小 JSON：\n" + coerced,
    )

    t_c0 = time.perf_counter()
    case_c = run_case(
        "C_official_analyze_candidate",
        provider=provider,
        system=system_c,
        user=user_c,
        extra={
            "extract_s": round(extract_s, 4),
            "coerce_s": round(coerce_s, 4),
            "system_prompt_build_s": round(system_build_s, 6),
            "user_prompt_build_s": round(user_build_s, 6),
        },
    )

    parsed = case_c.get("parsed")
    t_val0 = time.perf_counter()
    validate_error = None
    evidence_error = None
    validated = None
    if isinstance(parsed, dict):
        try:
            validated = validate_llm_payload(parsed)
        except SchemaError as exc:
            validate_error = str(exc)
        if validated is not None:
            try:
                _assert_evidence_in_resume(validated, coerced)
            except SchemaError as exc:
                evidence_error = str(exc)
    t_val1 = time.perf_counter()
    case_c["schema_validate_s"] = round(t_val1 - t_val0, 4)
    case_c["validate_error"] = validate_error
    case_c["evidence_error"] = evidence_error
    case_c["analyze_candidate_like_total_s"] = round(time.perf_counter() - t_c0 + extract_s + coerce_s, 3)

    if isinstance(parsed, dict):
        array_counts = {}
        for key, value in parsed.items():
            if isinstance(value, list):
                array_counts[key] = len(value)
        case_c["returned_array_counts"] = array_counts

    summary = {
        "A_http_total_s": case_a["http_total_s"],
        "A_ttfb_s": case_a["ttfb_s"],
        "A_body_read_s": case_a["body_read_s"],
        "A_usage": case_a["usage"],
        "A_content_chars": case_a["content_chars"],
        "B_http_total_s": case_b["http_total_s"],
        "B_ttfb_s": case_b["ttfb_s"],
        "B_body_read_s": case_b["body_read_s"],
        "B_usage": case_b["usage"],
        "B_content_chars": case_b["content_chars"],
        "C_http_total_s": case_c["http_total_s"],
        "C_ttfb_s": case_c["ttfb_s"],
        "C_body_read_s": case_c["body_read_s"],
        "C_usage": case_c["usage"],
        "C_content_chars": case_c["content_chars"],
        "C_schema_validate_s": case_c["schema_validate_s"],
        "C_validate_error": case_c["validate_error"],
        "C_evidence_error": case_c["evidence_error"],
        "C_returned_array_counts": case_c.get("returned_array_counts"),
        "local_extract_s": round(extract_s, 4),
        "local_coerce_s": round(coerce_s, 4),
        "local_prompt_build_s": round(system_build_s + user_build_s, 6),
    }
    print("\n===== SUMMARY =====")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
