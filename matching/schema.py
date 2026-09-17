"""MatchResult field-level schema. Validation only; no semantic guessing."""

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

ALLOWED_FIT_LEVEL = {"strong", "moderate", "weak", "none", "unknown"}
ALLOWED_CAPABILITY_OUTCOME = {"direct", "transferable", "missing", "insufficient_evidence"}
ALLOWED_RECOMMENDATION = {"yes", "weak", "no", "insufficient_evidence"}
ALLOWED_HARD_GAP_STATUS = {"fail", "unknown"}
ALLOWED_GAP_SEVERITY = {"high", "medium", "low"}

FIT_FIELDS = ("business_fit", "technical_fit", "industry_fit", "overall_fit")

REQUIRED_OK_ARRAY_FIELDS = (
    "hard_requirement_gaps",
    "capability_assessments",
    "risks",
    "knowledge_gaps",
)

ID_PASSTHROUGH_FIELDS = ("job_id", "candidate_id")


class SchemaError(ValueError):
    """Raised when LLM JSON does not satisfy MatchResult field rules."""


def empty_result(*, job_id: str | None = None, candidate_id: str | None = None) -> dict:
    return {
        "analysis_status": None,
        "error": None,
        "job_id": job_id,
        "candidate_id": candidate_id,
        "hard_requirements_met": None,
        "hard_requirement_gaps": [],
        "capability_assessments": [],
        "business_fit": None,
        "technical_fit": None,
        "industry_fit": None,
        "overall_fit": None,
        "risks": [],
        "knowledge_gaps": [],
        "recommendation": None,
        "rationale": None,
        "evidence_summary": [],
    }


def status_result(
    status: str,
    error: str | None,
    *,
    job_id: str | None = None,
    candidate_id: str | None = None,
) -> dict:
    if status not in ALLOWED_ANALYSIS_STATUS:
        raise SchemaError(f"illegal analysis_status: {status}")
    result = empty_result(job_id=job_id, candidate_id=candidate_id)
    result["analysis_status"] = status
    result["error"] = error
    return result


def validate_llm_payload(raw: Any) -> dict:
    """Validate LLM JSON into MatchResult semantic fields.

    Does not set analysis_status / error / job_id / candidate_id (code-owned).
    Raises SchemaError on illegal types or enums. Never maps unknown values.
    """
    if not isinstance(raw, dict):
        raise SchemaError("LLM JSON must be an object")

    result = empty_result()
    result["hard_requirements_met"] = _optional_bool(
        raw.get("hard_requirements_met"), "hard_requirements_met"
    )
    result["hard_requirement_gaps"] = [
        validate_hard_gap(item, "hard_requirement_gaps")
        for item in _require_list(raw.get("hard_requirement_gaps"), "hard_requirement_gaps")
    ]
    result["capability_assessments"] = [
        validate_capability_assessment(item, "capability_assessments")
        for item in _require_list(raw.get("capability_assessments"), "capability_assessments")
    ]
    for field in FIT_FIELDS:
        result[field] = _required_enum(raw.get(field), ALLOWED_FIT_LEVEL, field)
    result["risks"] = [
        validate_risk_item(item, "risks") for item in _require_list(raw.get("risks"), "risks")
    ]
    result["knowledge_gaps"] = [
        validate_gap_item(item, "knowledge_gaps")
        for item in _require_list(raw.get("knowledge_gaps"), "knowledge_gaps")
    ]
    result["recommendation"] = _required_enum(
        raw.get("recommendation"), ALLOWED_RECOMMENDATION, "recommendation"
    )
    result["rationale"] = _required_string(raw.get("rationale"), "rationale")
    result["evidence_summary"] = validate_evidence_list(
        raw.get("evidence_summary"), owner="evidence_summary", required=False
    )

    _assert_hard_consistency(result)

    return {
        key: value
        for key, value in result.items()
        if key not in {"analysis_status", "error", *ID_PASSTHROUGH_FIELDS}
    }


def validate_capability_assessment(item: Any, owner: str) -> dict:
    if not isinstance(item, dict):
        raise SchemaError(f"{owner} item must be an object")
    outcome = _required_enum(item.get("outcome"), ALLOWED_CAPABILITY_OUTCOME, f"{owner}.outcome")
    transfer_rationale = _optional_string(item.get("transfer_rationale"))
    if outcome == "transferable" and not transfer_rationale:
        raise SchemaError(f"{owner}.transfer_rationale is required when outcome is transferable")
    if outcome != "transferable":
        transfer_rationale = transfer_rationale if transfer_rationale else None
    return {
        "dimension": _required_string(item.get("dimension"), f"{owner}.dimension"),
        "outcome": outcome,
        "job_requirement_ref": _optional_string(item.get("job_requirement_ref")),
        "candidate_capability_ref": _optional_string(item.get("candidate_capability_ref")),
        "transfer_rationale": transfer_rationale,
        "evidence": validate_evidence_list(item.get("evidence"), owner=owner, required=False),
    }


def validate_hard_gap(item: Any, owner: str) -> dict:
    if not isinstance(item, dict):
        raise SchemaError(f"{owner} item must be an object")
    return {
        "requirement": _required_string(item.get("requirement"), f"{owner}.requirement"),
        "status": _required_enum(item.get("status"), ALLOWED_HARD_GAP_STATUS, f"{owner}.status"),
        "notes": _optional_string(item.get("notes")),
        "evidence": validate_evidence_list(item.get("evidence"), owner=owner, required=False),
    }


def validate_risk_item(item: Any, owner: str) -> dict:
    if not isinstance(item, dict):
        raise SchemaError(f"{owner} item must be an object")
    return {
        "risk": _required_string(item.get("risk"), f"{owner}.risk"),
        "severity": _required_enum(item.get("severity"), ALLOWED_GAP_SEVERITY, f"{owner}.severity"),
        "notes": _optional_string(item.get("notes")),
        "evidence": validate_evidence_list(item.get("evidence"), owner=owner, required=False),
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
    items = _require_list(value, f"{owner}.evidence" if not owner.endswith("evidence_summary") else owner)
    evidence = [validate_evidence(item, owner=owner) for item in items]
    if required and not evidence:
        raise SchemaError(f"{owner} evidence must contain at least 1 item")
    return evidence


def _assert_hard_consistency(result: dict) -> None:
    fail_gaps = [item for item in result["hard_requirement_gaps"] if item["status"] == "fail"]
    if result["hard_requirements_met"] is True and fail_gaps:
        raise SchemaError("hard_requirements_met cannot be true when a hard gap status is fail")
    if result["hard_requirements_met"] is False and not result["hard_requirement_gaps"]:
        raise SchemaError("hard_requirements_met is false but hard_requirement_gaps is empty")


def _require_list(value: Any, field: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SchemaError(f"{field} must be an array")
    return value


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


def _optional_bool(value: Any, field: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise SchemaError(f"{field} must be a boolean or null")
    return value


def _required_enum(value: Any, allowed: set[str], field: str) -> str:
    if not isinstance(value, str):
        raise SchemaError(f"{field} must be one of {sorted(allowed)}")
    text = value.strip()
    if text not in allowed:
        raise SchemaError(f"{field} must be one of {sorted(allowed)}")
    return text
