"""CandidateProfile field-level schema. Validation only; no semantic guessing."""

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

ALLOWED_CAPABILITY_KINDS = {"direct", "transferable", "inferred"}
ALLOWED_CAPABILITY_CATEGORIES = {
    "product",
    "business",
    "technical",
    "industry",
    "management",
    "other",
}
ALLOWED_GAP_SEVERITY = {"high", "medium", "low"}

CAPABILITY_ARRAY_FIELDS = (
    "product_capabilities",
    "business_capabilities",
    "technical_capabilities",
    "management_experience",
    "direct_capabilities",
)
EXPERIENCE_ARRAY_FIELDS = ("industry_experience",)
PROJECT_ARRAY_FIELDS = ("project_experience",)
TRANSFERABLE_ARRAY_FIELDS = ("transferable_capabilities",)
GAP_ARRAY_FIELDS = ("knowledge_gaps",)
STRING_ARRAY_FIELDS = ("target_roles", "locations")

REQUIRED_OK_ARRAY_FIELDS = (
    *CAPABILITY_ARRAY_FIELDS,
    *EXPERIENCE_ARRAY_FIELDS,
    *PROJECT_ARRAY_FIELDS,
    *TRANSFERABLE_ARRAY_FIELDS,
    *GAP_ARRAY_FIELDS,
)

SUMMARY_MAX_CHARS = 200


class SchemaError(ValueError):
    """Raised when LLM JSON does not satisfy CandidateProfile field rules."""


def empty_profile(*, candidate_id: str | None = None) -> dict:
    return {
        "analysis_status": None,
        "error": None,
        "candidate_id": candidate_id,
        "summary": None,
        "target_roles": [],
        "years_experience": None,
        "education": None,
        "locations": [],
        "product_capabilities": [],
        "business_capabilities": [],
        "technical_capabilities": [],
        "industry_experience": [],
        "management_experience": [],
        "project_experience": [],
        "transferable_capabilities": [],
        "knowledge_gaps": [],
        "direct_capabilities": [],
        "raw_evidence_notes": None,
    }


def status_profile(
    status: str,
    error: str | None,
    *,
    candidate_id: str | None = None,
) -> dict:
    if status not in ALLOWED_ANALYSIS_STATUS:
        raise SchemaError(f"illegal analysis_status: {status}")
    result = empty_profile(candidate_id=candidate_id)
    result["analysis_status"] = status
    result["error"] = error
    return result


def validate_llm_payload(raw: Any) -> dict:
    """Deprecated legacy CandidateProfile validator.

    Production analyze_candidate / Agent Loop must not call this.
    Kept for tests and leftover store-view helpers only.
    """
    if not isinstance(raw, dict):
        raise SchemaError("LLM JSON must be an object")

    profile = empty_profile()
    profile["summary"] = _optional_summary(raw.get("summary"))
    profile["years_experience"] = _optional_string(raw.get("years_experience"))
    profile["education"] = _optional_string(raw.get("education"))
    profile["raw_evidence_notes"] = _optional_string(raw.get("raw_evidence_notes"))

    for field in STRING_ARRAY_FIELDS:
        profile[field] = _string_list(raw.get(field), field)

    for field in CAPABILITY_ARRAY_FIELDS:
        profile[field] = [
            validate_capability_item(item, field) for item in _require_list(raw.get(field), field)
        ]
    for field in EXPERIENCE_ARRAY_FIELDS:
        profile[field] = [
            validate_experience_item(item, field) for item in _require_list(raw.get(field), field)
        ]
    for field in PROJECT_ARRAY_FIELDS:
        profile[field] = [
            validate_project_item(item, field) for item in _require_list(raw.get(field), field)
        ]
    for field in TRANSFERABLE_ARRAY_FIELDS:
        profile[field] = [
            validate_transferable_item(item, field) for item in _require_list(raw.get(field), field)
        ]
    for field in GAP_ARRAY_FIELDS:
        profile[field] = [
            validate_gap_item(item, field) for item in _require_list(raw.get(field), field)
        ]

    return {
        key: value
        for key, value in profile.items()
        if key not in {"analysis_status", "error", "candidate_id"}
    }


def validate_evidence(item: Any, *, owner: str) -> dict:
    if not isinstance(item, dict):
        raise SchemaError(f"{owner} evidence item must be an object")
    quote = _required_string(item.get("quote"), f"{owner}.evidence.quote")
    return {
        "quote": quote,
        "location_hint": _optional_string(item.get("location_hint")),
        "field": _optional_string(item.get("field")),
    }


def validate_evidence_list(value: Any, *, owner: str, required: bool) -> list[dict]:
    items = _require_list(value, f"{owner}.evidence")
    evidence = [validate_evidence(item, owner=owner) for item in items]
    if required and not evidence:
        raise SchemaError(f"{owner} evidence must contain at least 1 item")
    return evidence


def validate_capability_item(item: Any, owner: str) -> dict:
    if not isinstance(item, dict):
        raise SchemaError(f"{owner} item must be an object")
    kind = _required_enum(item.get("kind"), ALLOWED_CAPABILITY_KINDS, f"{owner}.kind")
    category = _required_enum(item.get("category"), ALLOWED_CAPABILITY_CATEGORIES, f"{owner}.category")
    return {
        "name": _required_string(item.get("name"), f"{owner}.name"),
        "kind": kind,
        "category": category,
        "description": _optional_string(item.get("description")),
        "explicit": _required_bool(item.get("explicit"), f"{owner}.explicit"),
        "evidence": validate_evidence_list(item.get("evidence"), owner=owner, required=True),
        "confidence": _required_confidence(item.get("confidence"), f"{owner}.confidence"),
    }


def validate_transferable_item(item: Any, owner: str) -> dict:
    if not isinstance(item, dict):
        raise SchemaError(f"{owner} item must be an object")
    return {
        "name": _required_string(item.get("name"), f"{owner}.name"),
        "from_domain": _required_string(item.get("from_domain"), f"{owner}.from_domain"),
        "to_domain_hint": _optional_string(item.get("to_domain_hint")),
        "transfer_rationale": _required_string(
            item.get("transfer_rationale"), f"{owner}.transfer_rationale"
        ),
        "evidence": validate_evidence_list(item.get("evidence"), owner=owner, required=True),
        "confidence": _required_confidence(item.get("confidence"), f"{owner}.confidence"),
    }


def validate_experience_item(item: Any, owner: str) -> dict:
    if not isinstance(item, dict):
        raise SchemaError(f"{owner} item must be an object")
    return {
        "industry": _required_string(item.get("industry"), f"{owner}.industry"),
        "role": _optional_string(item.get("role")),
        "years_or_duration": _optional_string(item.get("years_or_duration")),
        "description": _optional_string(item.get("description")),
        "evidence": validate_evidence_list(item.get("evidence"), owner=owner, required=True),
        "explicit": _required_bool(item.get("explicit"), f"{owner}.explicit"),
        "confidence": _required_confidence(item.get("confidence"), f"{owner}.confidence"),
    }


def validate_project_item(item: Any, owner: str) -> dict:
    if not isinstance(item, dict):
        raise SchemaError(f"{owner} item must be an object")
    return {
        "name": _required_string(item.get("name"), f"{owner}.name"),
        "role": _optional_string(item.get("role")),
        "description": _optional_string(item.get("description")),
        "outcomes": _optional_string_or_joined(item.get("outcomes")),
        "capabilities_demonstrated": _string_list(
            item.get("capabilities_demonstrated"), f"{owner}.capabilities_demonstrated"
        ),
        "evidence": validate_evidence_list(item.get("evidence"), owner=owner, required=True),
        "explicit": _required_bool(item.get("explicit"), f"{owner}.explicit"),
        "confidence": _required_confidence(item.get("confidence"), f"{owner}.confidence"),
    }


def validate_gap_item(item: Any, owner: str) -> dict:
    if not isinstance(item, dict):
        raise SchemaError(f"{owner} item must be an object")
    return {
        "gap": _required_string(item.get("gap"), f"{owner}.gap"),
        "severity": _required_enum(item.get("severity"), ALLOWED_GAP_SEVERITY, f"{owner}.severity"),
        "evidence": validate_evidence_list(item.get("evidence"), owner=owner, required=False),
        "notes": _optional_string(item.get("notes")),
    }


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


def _optional_string_or_joined(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        parts = [_optional_string(item) for item in value]
        joined = "；".join(part for part in parts if part)
        return joined or None
    return _optional_string(value)


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


def _required_confidence(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SchemaError(f"{field} must be a number")
    number = float(value)
    if number < 0 or number > 1:
        raise SchemaError(f"{field} must be between 0 and 1")
    return number


def _required_enum(value: Any, allowed: set[str], field: str) -> str:
    text = _optional_string(value)
    if text is None or text not in allowed:
        raise SchemaError(f"{field} must be one of {sorted(allowed)}")
    return text
