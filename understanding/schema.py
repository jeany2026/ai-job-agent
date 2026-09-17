"""UserInputUnderstanding field-level schema. Validation only; no semantic guessing."""

from __future__ import annotations

from typing import Any

ALLOWED_SOURCE_KINDS = {
    "resume",
    "user_statement",
    "project_document",
    "other_attachment",
}

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

MATCHING_CONTEXT_MAX_CHARS = 500

ALLOWED_TASK_KINDS = {
    "new_job_search",
    "follow_up_job",
    "update_goal",
    "job_reference",
    "apply_job",
    "skip_job",
    "stop_task",
    "continue_task",
}

FOLLOW_UP_TASK_KINDS = {"follow_up_job", "job_reference"}
CONTROL_TASK_KINDS = {"apply_job", "skip_job", "stop_task", "continue_task"}

CONVERSATION_REFERENCE_TYPE = "conversation_reference"
ALLOWED_REFERENCE_TARGET_KINDS = {"job"}
ALLOWED_RECENCY = {"last", "previous_round", "this_round", "any"}
ALLOWED_SEMANTIC_FILTER_KINDS = {
    "knowledge_gap",
    "missing_experience",
    "industry",
    "capability",
    "title",
    "company",
    "recommendation",
    "overall_fit",
}

REFERENCE_IDENTITY_FIELDS = ("job_id", "job_key", "job_url", "context_id")

EVIDENCE_STATUS_SUPPORTED = "supported"
EVIDENCE_STATUS_UNSUPPORTED = "unsupported"
EVIDENCE_STATUS_UNVERIFIED = "unverified"
ALLOWED_EVIDENCE_STATUS = {
    EVIDENCE_STATUS_SUPPORTED,
    EVIDENCE_STATUS_UNSUPPORTED,
    EVIDENCE_STATUS_UNVERIFIED,
}

EVIDENCE_VALIDATION_SKIPPED = "skipped"
EVIDENCE_VALIDATION_OK = "ok"
ALLOWED_EVIDENCE_VALIDATION_STATUS = {
    EVIDENCE_VALIDATION_SKIPPED,
    EVIDENCE_VALIDATION_OK,
    ANALYSIS_STATUS_LLM_UNAVAILABLE,
    ANALYSIS_STATUS_LLM_ERROR,
    ANALYSIS_STATUS_FAILED,
}

CLAIM_COLLECTION_KEYS = (
    "claimed_capabilities",
    "claimed_projects",
    "acknowledged_gaps",
    "transfer_claims",
)


class SchemaError(ValueError):
    """Raised when LLM JSON does not satisfy UserInputUnderstanding field rules."""


def empty_user_goal() -> dict:
    return {
        "target_roles": [],
        "cities": [],
        "salary_min": None,
        "focus_areas": [],
        "exclude_companies": [],
        "platforms": [],
    }


def empty_supplement() -> dict:
    return {
        "statements": [],
        "claimed_capabilities": [],
        "claimed_projects": [],
        "acknowledged_gaps": [],
        "transfer_claims": [],
        "persist_requested": False,
    }


def empty_constraints() -> dict:
    return {
        "salary_min": None,
        "cities": [],
        "exclude_companies": [],
        "notes": [],
    }


def empty_understanding(*, raw_text: str | None = None) -> dict:
    return {
        "analysis_status": None,
        "error": None,
        "raw_text": raw_text,
        "user_goal": empty_user_goal(),
        "candidate_supplement": empty_supplement(),
        "preferences": [],
        "constraints": empty_constraints(),
        "matching_context": None,
        "persist_requested": False,
        "conversation_reference": None,
        "task_kind": None,
        "evidence_validation_status": None,
        "support_check": [],
        "verifications": [],
    }


def status_understanding(
    status: str,
    error: str | None,
    *,
    raw_text: str | None = None,
) -> dict:
    if status not in ALLOWED_ANALYSIS_STATUS:
        raise SchemaError(f"illegal analysis_status: {status}")
    result = empty_understanding(raw_text=raw_text)
    result["analysis_status"] = status
    result["error"] = error
    return result


def validate_llm_payload(raw: Any) -> dict:
    """Validate LLM JSON into UserInputUnderstanding semantic fields.

    Does not set analysis_status / error / raw_text (code-owned).
    """
    from tools.parse_user_goal import GoalSchemaError, validate_goal_payload

    if not isinstance(raw, dict):
        raise SchemaError("LLM JSON must be an object")

    try:
        user_goal = validate_goal_payload(raw.get("user_goal") or {})
    except GoalSchemaError as exc:
        raise SchemaError(str(exc)) from exc

    supplement = validate_supplement(raw.get("candidate_supplement") or {})
    persist_requested = _optional_bool(raw.get("persist_requested"), "persist_requested")
    if persist_requested is None:
        persist_requested = bool(supplement.get("persist_requested"))
    supplement["persist_requested"] = persist_requested

    return {
        "user_goal": user_goal,
        "candidate_supplement": supplement,
        "preferences": _string_list(raw.get("preferences"), "preferences"),
        "constraints": validate_constraints(raw.get("constraints") or {}),
        "matching_context": _optional_matching_context(raw.get("matching_context")),
        "persist_requested": persist_requested,
        "conversation_reference": validate_conversation_reference(raw.get("conversation_reference")),
        "task_kind": _optional_task_kind(raw.get("task_kind")),
    }


def validate_supplement(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise SchemaError("candidate_supplement must be an object")
    persist = _optional_bool(raw.get("persist_requested"), "candidate_supplement.persist_requested")
    return {
        "statements": _string_list(raw.get("statements"), "candidate_supplement.statements"),
        "claimed_capabilities": [
            validate_named_item(item, "candidate_supplement.claimed_capabilities", name_key="name")
            for item in _require_list(raw.get("claimed_capabilities"), "candidate_supplement.claimed_capabilities")
        ],
        "claimed_projects": [
            validate_project_item(item, "candidate_supplement.claimed_projects")
            for item in _require_list(raw.get("claimed_projects"), "candidate_supplement.claimed_projects")
        ],
        "acknowledged_gaps": [
            validate_gap_item(item, "candidate_supplement.acknowledged_gaps")
            for item in _require_list(raw.get("acknowledged_gaps"), "candidate_supplement.acknowledged_gaps")
        ],
        "transfer_claims": [
            validate_transfer_item(item, "candidate_supplement.transfer_claims")
            for item in _require_list(raw.get("transfer_claims"), "candidate_supplement.transfer_claims")
        ],
        "persist_requested": bool(persist),
    }


def validate_constraints(raw: Any) -> dict:
    from tools.parse_user_goal import GoalSchemaError

    if not isinstance(raw, dict):
        raise SchemaError("constraints must be an object")
    try:
        salary_min = _optional_salary(raw.get("salary_min"))
    except GoalSchemaError as exc:
        raise SchemaError(str(exc)) from exc
    return {
        "salary_min": salary_min,
        "cities": _string_list(raw.get("cities"), "constraints.cities"),
        "exclude_companies": _string_list(
            raw.get("exclude_companies"), "constraints.exclude_companies"
        ),
        "notes": _string_list(raw.get("notes"), "constraints.notes"),
    }


def validate_named_item(item: Any, owner: str, *, name_key: str) -> dict:
    if not isinstance(item, dict):
        raise SchemaError(f"{owner} item must be an object")
    return {
        name_key: _required_string(item.get(name_key), f"{owner}.{name_key}"),
        "quote": _optional_string(item.get("quote")),
        "source_kind": _optional_source_kind(item.get("source_kind"), f"{owner}.source_kind"),
    }


def validate_project_item(item: Any, owner: str) -> dict:
    if not isinstance(item, dict):
        raise SchemaError(f"{owner} item must be an object")
    return {
        "name": _required_string(item.get("name"), f"{owner}.name"),
        "description": _optional_string(item.get("description")),
        "quote": _optional_string(item.get("quote")),
        "source_kind": _optional_source_kind(item.get("source_kind"), f"{owner}.source_kind"),
    }


def validate_gap_item(item: Any, owner: str) -> dict:
    if not isinstance(item, dict):
        raise SchemaError(f"{owner} item must be an object")
    return {
        "gap": _required_string(item.get("gap"), f"{owner}.gap"),
        "quote": _optional_string(item.get("quote")),
    }


def validate_transfer_item(item: Any, owner: str) -> dict:
    if not isinstance(item, dict):
        raise SchemaError(f"{owner} item must be an object")
    return {
        "claim": _required_string(item.get("claim"), f"{owner}.claim"),
        "from_domain": _optional_string(item.get("from_domain")),
        "to_domain": _optional_string(item.get("to_domain")),
        "quote": _optional_string(item.get("quote")),
    }


def validate_conversation_reference(raw: Any) -> dict | None:
    """Structured semantic reference. Never keeps job_id / job_key for Understanding."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise SchemaError("conversation_reference must be an object or null")
    type_text = _optional_string(raw.get("type")) or CONVERSATION_REFERENCE_TYPE
    if type_text != CONVERSATION_REFERENCE_TYPE:
        raise SchemaError("conversation_reference.type must be conversation_reference")
    target_kind = _optional_string(raw.get("target_kind"))
    if target_kind is None:
        raise SchemaError("conversation_reference.target_kind is required")
    if target_kind not in ALLOWED_REFERENCE_TARGET_KINDS:
        raise SchemaError(
            "conversation_reference.target_kind must be one of "
            f"{sorted(ALLOWED_REFERENCE_TARGET_KINDS)}"
        )
    for key in REFERENCE_IDENTITY_FIELDS:
        raw.get(key)  # discarded: Understanding must not choose job identity
    return {
        "type": CONVERSATION_REFERENCE_TYPE,
        "target_kind": target_kind,
        "reference_text": _optional_string(raw.get("reference_text")),
        "resolution_hint": validate_resolution_hint(raw.get("resolution_hint")),
    }


def validate_resolution_hint(raw: Any) -> dict:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise SchemaError("conversation_reference.resolution_hint must be an object or null")
    recency = _optional_string(raw.get("recency"))
    if recency is not None and recency not in ALLOWED_RECENCY:
        raise SchemaError(
            "conversation_reference.resolution_hint.recency must be one of "
            f"{sorted(ALLOWED_RECENCY)}"
        )
    return {
        "recency": recency,
        "ordinal": _optional_positive_int(raw.get("ordinal"), "conversation_reference.resolution_hint.ordinal"),
        "round_offset": _optional_int(
            raw.get("round_offset"), "conversation_reference.resolution_hint.round_offset"
        ),
        "recommended_only": bool(_optional_bool(raw.get("recommended_only"), "recommended_only")),
        "highest_match": bool(_optional_bool(raw.get("highest_match"), "highest_match")),
        "semantic_filters": [
            validate_semantic_filter(item, "conversation_reference.resolution_hint.semantic_filters")
            for item in _require_list(raw.get("semantic_filters"), "conversation_reference.resolution_hint.semantic_filters")
        ],
    }


def validate_semantic_filter(item: Any, owner: str) -> dict:
    if not isinstance(item, dict):
        raise SchemaError(f"{owner} item must be an object")
    kind = _required_string(item.get("kind"), f"{owner}.kind")
    if kind not in ALLOWED_SEMANTIC_FILTER_KINDS:
        raise SchemaError(f"{owner}.kind must be one of {sorted(ALLOWED_SEMANTIC_FILTER_KINDS)}")
    return {
        "kind": kind,
        "value": _required_string(item.get("value"), f"{owner}.value"),
    }


def empty_resolution_hint() -> dict:
    return {
        "recency": None,
        "ordinal": None,
        "round_offset": None,
        "recommended_only": False,
        "highest_match": False,
        "semantic_filters": [],
    }


def has_conversation_reference(understanding: dict) -> bool:
    ref = understanding.get("conversation_reference")
    return isinstance(ref, dict) and ref.get("type") == CONVERSATION_REFERENCE_TYPE


def is_follow_up_task(understanding: dict | None) -> bool:
    if not isinstance(understanding, dict):
        return False
    kind = understanding.get("task_kind")
    if kind in CONTROL_TASK_KINDS:
        return False
    if kind in FOLLOW_UP_TASK_KINDS:
        return True
    return kind is None and has_conversation_reference(understanding)


def is_control_task(understanding: dict | None) -> bool:
    if not isinstance(understanding, dict):
        return False
    return understanding.get("task_kind") in CONTROL_TASK_KINDS


def has_user_goal(understanding: dict) -> bool:
    goal = understanding.get("user_goal") or {}
    return bool(
        goal.get("target_roles")
        or goal.get("cities")
        or goal.get("salary_min") is not None
        or goal.get("focus_areas")
        or goal.get("exclude_companies")
        or goal.get("platforms")
    )


def iter_candidate_claims(supplement: dict | None):
    """Yield (collection, index, item) for extracted candidate facts. Code only walks structure."""
    extra = supplement if isinstance(supplement, dict) else {}
    for key in CLAIM_COLLECTION_KEYS:
        items = extra.get(key) or []
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items):
            if isinstance(item, dict):
                yield key, index, item


def claim_id_for(collection: str, index: int) -> str:
    return f"{collection}[{index}]"


def stamp_evidence_status(item: dict, status: str, *, rationale: str | None = None) -> dict:
    if status not in ALLOWED_EVIDENCE_STATUS:
        raise SchemaError(f"illegal evidence_status: {status}")
    stamped = dict(item)
    stamped["evidence_status"] = status
    if rationale:
        stamped["evidence_rationale"] = rationale
    else:
        stamped.pop("evidence_rationale", None)
    return stamped


def mark_claims_unverified(supplement: dict | None, *, rationale: str | None = None) -> dict:
    extra = dict(supplement or empty_supplement())
    for key in CLAIM_COLLECTION_KEYS:
        extra[key] = [
            stamp_evidence_status(item, EVIDENCE_STATUS_UNVERIFIED, rationale=rationale)
            if isinstance(item, dict)
            else item
            for item in (extra.get(key) or [])
        ]
    return extra


def grounded_candidate_supplement(supplement: dict | None) -> dict:
    """Pass claims through. Same-turn support_check does not drop or upgrade them.

    Only an independent Verification may later mark unsupported. Code does not
    infer support from text or from same-turn LLM consistency hints.
    """
    extra = dict(supplement or empty_supplement())
    for key in CLAIM_COLLECTION_KEYS:
        kept = []
        for item in extra.get(key) or []:
            if not isinstance(item, dict):
                continue
            if item.get("evidence_status") == EVIDENCE_STATUS_UNSUPPORTED:
                continue
            kept.append(item)
        extra[key] = kept
    return extra


def has_candidate_supplement(understanding: dict) -> bool:
    extra = understanding.get("candidate_supplement") or {}
    return bool(
        extra.get("statements")
        or extra.get("claimed_capabilities")
        or extra.get("claimed_projects")
        or extra.get("acknowledged_gaps")
        or extra.get("transfer_claims")
    )


def has_task_context(understanding: dict) -> bool:
    constraints = understanding.get("constraints") or {}
    return bool(
        understanding.get("preferences")
        or understanding.get("matching_context")
        or constraints.get("salary_min") is not None
        or constraints.get("cities")
        or constraints.get("exclude_companies")
        or constraints.get("notes")
        or extra_persist(understanding)
    )


def extra_persist(understanding: dict) -> bool:
    return bool(understanding.get("persist_requested"))


def is_empty_understanding_fields(fields: dict) -> bool:
    if fields.get("task_kind") in CONTROL_TASK_KINDS:
        return False
    return not (
        has_user_goal(fields)
        or has_candidate_supplement(fields)
        or has_task_context(fields)
        or has_conversation_reference(fields)
    )


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
        if not isinstance(item, str):
            raise SchemaError(f"{field} items must be strings")
        text = item.strip()
        if not text:
            raise SchemaError(f"{field} items must be non-empty strings")
        key = text.casefold()
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


def _optional_matching_context(value: Any) -> str | None:
    text = _optional_string(value)
    if text is None:
        return None
    if len(text) > MATCHING_CONTEXT_MAX_CHARS:
        return text[:MATCHING_CONTEXT_MAX_CHARS]
    return text


def _optional_task_kind(value: Any) -> str | None:
    text = _optional_string(value)
    if text is None:
        return None
    if text not in ALLOWED_TASK_KINDS:
        raise SchemaError(f"task_kind must be one of {sorted(ALLOWED_TASK_KINDS)}")
    return text


def _optional_positive_int(value: Any, field: str) -> int | None:
    number = _optional_int(value, field)
    if number is None:
        return None
    if number < 1:
        raise SchemaError(f"{field} must be a positive integer or null")
    return number


def _optional_int(value: Any, field: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise SchemaError(f"{field} must be an integer or null")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    raise SchemaError(f"{field} must be an integer or null")


def _optional_bool(value: Any, field: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise SchemaError(f"{field} must be a boolean")
    return value


def _optional_source_kind(value: Any, field: str) -> str | None:
    text = _optional_string(value)
    if text is None:
        return None
    if text not in ALLOWED_SOURCE_KINDS:
        raise SchemaError(f"{field} must be one of {sorted(ALLOWED_SOURCE_KINDS)}")
    return text


def _optional_salary(value: Any) -> int | None:
    from tools.parse_user_goal import GoalSchemaError

    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise GoalSchemaError("salary_min must be an integer or null")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    raise GoalSchemaError("salary_min must be an integer or null")
