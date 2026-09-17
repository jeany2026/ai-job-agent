"""JobProfile field-level schema. Validation only; no semantic guessing."""

from __future__ import annotations

from typing import Any

ANALYSIS_STATUS_OK = "ok"
ANALYSIS_STATUS_INSUFFICIENT = "insufficient_data"
ANALYSIS_STATUS_LLM_UNAVAILABLE = "llm_unavailable"
ANALYSIS_STATUS_LLM_ERROR = "llm_error"
ANALYSIS_STATUS_FAILED = "analysis_failed"

ALLOWED_ANALYSIS_STATUS = {
    ANALYSIS_STATUS_OK,
    ANALYSIS_STATUS_INSUFFICIENT,
    ANALYSIS_STATUS_LLM_UNAVAILABLE,
    ANALYSIS_STATUS_LLM_ERROR,
    ANALYSIS_STATUS_FAILED,
}

ALLOWED_IMPORTANCE = {"high", "medium", "low"}

REQUIREMENT_ARRAY_FIELDS = (
    "hard_requirements",
    "core_requirements",
    "business_requirements",
    "technical_requirements",
    "industry_requirements",
    "bonus_requirements",
)

REQUIRED_OK_ARRAY_FIELDS = (
    *REQUIREMENT_ARRAY_FIELDS,
    "responsibilities",
    "keywords",
)

JOB_PASSTHROUGH_FIELDS = ("job_id", "job_title", "company_name")

SUMMARY_MAX_CHARS = 100

# Policy markers from baseline §8: these must never appear in hard_requirements.
HARD_BONUS_MARKERS = ("优先", "加分", "有则更好", "preferred", "bonus", "更佳")

# JobProfile must not contain candidate/matching language.
FORBIDDEN_PROFILE_TERMS = ("适合", "匹配", "推荐", "评分", "分数", "score", "候选人")


class SchemaError(ValueError):
    """Raised when LLM JSON does not satisfy JobProfile field rules."""


def empty_profile(job: dict | None = None) -> dict:
    job = job or {}
    return {
        "analysis_status": None,
        "error": None,
        "job_id": _passthrough_string(job.get("job_id")),
        "job_title": _passthrough_string(job.get("job_title")),
        "company_name": _passthrough_string(job.get("company_name")),
        "job_summary": None,
        "hard_requirements": [],
        "core_requirements": [],
        "business_requirements": [],
        "technical_requirements": [],
        "industry_requirements": [],
        "bonus_requirements": [],
        "responsibilities": [],
        "experience_requirements": {
            "years": None,
            "education": None,
            "seniority": None,
            "other": [],
        },
        "keywords": [],
    }


def status_profile(status: str, error: str | None, job: dict | None = None) -> dict:
    if status not in ALLOWED_ANALYSIS_STATUS:
        raise SchemaError(f"illegal analysis_status: {status}")
    result = empty_profile(job)
    result["analysis_status"] = status
    result["error"] = error
    return result


def validate_llm_payload(raw: Any) -> dict:
    """Validate LLM JSON into JobProfile semantic fields.

    Does not set analysis_status / error / job_id / job_title / company_name (code-owned).
    Raises SchemaError on illegal types, enums, or hard/bonus policy violations.
    """
    if not isinstance(raw, dict):
        raise SchemaError("LLM JSON must be an object")

    profile = empty_profile()
    profile["job_summary"] = _optional_summary(raw.get("job_summary"))

    for field in REQUIREMENT_ARRAY_FIELDS:
        profile[field] = [
            validate_requirement_item(item, field) for item in _require_list(raw.get(field), field)
        ]

    profile["responsibilities"] = _string_list(raw.get("responsibilities"), "responsibilities")
    profile["keywords"] = _string_list(raw.get("keywords"), "keywords")
    profile["experience_requirements"] = validate_experience_requirements(
        raw.get("experience_requirements")
    )

    _assert_hard_policy(profile)
    _assert_no_forbidden_language(profile)

    return {
        key: value
        for key, value in profile.items()
        if key not in {"analysis_status", "error", *JOB_PASSTHROUGH_FIELDS}
    }


def validate_requirement_item(item: Any, owner: str) -> dict:
    if not isinstance(item, dict):
        raise SchemaError(f"{owner} item must be an object")
    requirement = _required_string(item.get("requirement"), f"{owner}.requirement")
    category = _required_string(item.get("category"), f"{owner}.category")
    importance = _required_enum(item.get("importance"), ALLOWED_IMPORTANCE, f"{owner}.importance")
    explicit = _required_bool(item.get("explicit"), f"{owner}.explicit")
    if owner == "hard_requirements" and explicit is not True:
        raise SchemaError("hard_requirements items must be explicit=true")
    if owner == "hard_requirements" and _has_bonus_marker(requirement):
        raise SchemaError("hard_requirements must not contain preferred/bonus conditions")
    return {
        "requirement": requirement,
        "category": category,
        "importance": importance,
        "explicit": explicit,
        "evidence_quote": _optional_string(item.get("evidence_quote")),
    }


def validate_experience_requirements(value: Any) -> dict:
    if value is None:
        return {"years": None, "education": None, "seniority": None, "other": []}
    if not isinstance(value, dict):
        raise SchemaError("experience_requirements must be an object")
    return {
        "years": _optional_string(value.get("years")),
        "education": _optional_string(value.get("education")),
        "seniority": _optional_string(value.get("seniority")),
        "other": _string_list(value.get("other"), "experience_requirements.other"),
    }


def _assert_hard_policy(profile: dict) -> None:
    hard_texts = [item["requirement"] for item in profile["hard_requirements"]]
    bonus_texts = [item["requirement"] for item in profile["bonus_requirements"]]
    bonus_set = {text.lower() for text in bonus_texts}
    for text in hard_texts:
        if text.lower() in bonus_set:
            raise SchemaError("hard_requirements must not duplicate bonus_requirements")


def _assert_no_forbidden_language(profile: dict) -> None:
    for text in _walk_strings(profile):
        if _text_has_forbidden(text):
            raise SchemaError("JobProfile must not contain candidate/matching language")


def _has_bonus_marker(text: str) -> bool:
    lowered = text.lower()
    return any(marker.lower() in lowered for marker in HARD_BONUS_MARKERS)


def _text_has_forbidden(text: str | None) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return any(term.lower() in lowered for term in FORBIDDEN_PROFILE_TERMS)


def _walk_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for inner in value.values():
            yield from _walk_strings(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _walk_strings(inner)


def _require_list(value: Any, field: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SchemaError(f"{field} must be an array")
    return value


def _string_list(value: Any, field: str) -> list[str]:
    items = _require_list(value, field)
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = _optional_string(item)
        if not text:
            raise SchemaError(f"{field} items must be non-empty strings")
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _required_string(value: Any, field: str) -> str:
    text = _optional_string(value)
    if not text:
        raise SchemaError(f"{field} is required")
    return text


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SchemaError("string field must be a string")
    text = value.strip()
    if not text or text.lower() in {"none", "null", "n/a"}:
        return None
    return text


def _passthrough_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "n/a"}:
        return None
    return text


def _optional_summary(value: Any) -> str | None:
    text = _optional_string(value)
    if text is None:
        return None
    if len(text) > SUMMARY_MAX_CHARS:
        return text[:SUMMARY_MAX_CHARS]
    return text


def _required_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise SchemaError(f"{field} must be a boolean")
    return value


def _required_enum(value: Any, allowed: set[str], field: str) -> str:
    text = _optional_string(value)
    if text is None or text not in allowed:
        raise SchemaError(f"{field} must be one of {sorted(allowed)}")
    return text
