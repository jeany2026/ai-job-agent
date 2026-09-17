"""Evidence / Claim / Interpretation / Verification constructors.

Code only records structure and provenance. It does not guess meaning.
Same-turn LLM output is never a Verification.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

CONTENT_TYPE_RESUME = "resume"
CONTENT_TYPE_USER_MESSAGE = "user_message"
CONTENT_TYPE_USER_STATEMENT = "user_statement"
CONTENT_TYPE_PROJECT_DOCUMENT = "project_document"
CONTENT_TYPE_OTHER_ATTACHMENT = "other_attachment"
# Neutral Evidence label after Intake admission — not a carrier-derived business type.
CONTENT_TYPE_INTAKE_TEXT = "intake_text"

SOURCE_KIND_TO_CONTENT_TYPE = {
    "resume": CONTENT_TYPE_RESUME,
    "user_statement": CONTENT_TYPE_USER_MESSAGE,
    "user_message": CONTENT_TYPE_USER_MESSAGE,
    "project_document": CONTENT_TYPE_PROJECT_DOCUMENT,
    "other_attachment": CONTENT_TYPE_OTHER_ATTACHMENT,
}

VERIFICATION_BASIS_USER = "user_confirmation"
VERIFICATION_BASIS_TOOL = "tool_mechanical_result"
ALLOWED_VERIFICATION_BASIS = {VERIFICATION_BASIS_USER, VERIFICATION_BASIS_TOOL}

LAYER_CLAIM = "claim"
LAYER_INTERPRETATION = "interpretation"
LAYER_VERIFICATION = "verification"

CONSISTENCY_CONSISTENT = "consistent"
CONSISTENCY_INCONSISTENT = "inconsistent"
CONSISTENCY_UNCLEAR = "unclear"

_LLM_STATUS_TO_HINT = {
    "supported": CONSISTENCY_CONSISTENT,
    "unsupported": CONSISTENCY_INCONSISTENT,
    "unverified": CONSISTENCY_UNCLEAR,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def fold_text(text: str | None) -> str:
    if not isinstance(text, str):
        return ""
    return "".join(text.split()).casefold()


def next_layer_id(items: list[dict] | None, prefix: str) -> str:
    max_n = 0
    for item in items or []:
        if not isinstance(item, dict):
            continue
        ident = str(item.get("id") or "")
        if not ident.startswith(prefix):
            continue
        tail = ident[len(prefix) :].lstrip("-")
        if tail.isdigit():
            max_n = max(max_n, int(tail))
    return f"{prefix}{max_n + 1}"


def id_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item).strip() if item is not None else ""
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def make_evidence(
    *,
    content: str,
    content_type: str,
    origin: str,
    source: str | None = None,
    source_ref: str | None = None,
    evidence_id: str | None = None,
    existing: list[dict] | None = None,
    timestamp: str | None = None,
) -> dict:
    return {
        "id": evidence_id or next_layer_id(existing, "ev-"),
        "source": source or origin,
        "provenance": origin,
        "timestamp": timestamp or utc_now(),
        "content_type": content_type,
        "origin": origin,
        "content": content,
        "source_ref": source_ref,
    }


def make_claim(
    *,
    kind: str,
    statement: str,
    derived_from: list[str] | None = None,
    name: str | None = None,
    value: Any = None,
    source_kind: str | None = None,
    claim_id: str | None = None,
    existing: list[dict] | None = None,
    quote: str | None = None,
) -> dict:
    return {
        "id": claim_id or next_layer_id(existing, "cl-"),
        "kind": kind,
        "name": name or kind,
        "statement": statement,
        "value": value,
        "derived_from": id_list(derived_from),
        "evidence_status": "unverified",
        "source_kind": source_kind,
        "quote": quote,
        "layer": LAYER_CLAIM,
    }


def make_interpretation(
    *,
    kind: str,
    derived_from: list[str] | None = None,
    payload: dict | None = None,
    consistency_hint: str | None = None,
    interpretation_id: str | None = None,
    existing: list[dict] | None = None,
) -> dict:
    return {
        "id": interpretation_id or next_layer_id(existing, "int-"),
        "kind": kind,
        "derived_from": id_list(derived_from),
        "payload": dict(payload or {}),
        "consistency_hint": consistency_hint,
        "layer": LAYER_INTERPRETATION,
    }


def make_verification(
    *,
    status: str,
    basis: str,
    derived_from: list[str] | None = None,
    payload: dict | None = None,
    verification_id: str | None = None,
    existing: list[dict] | None = None,
) -> dict:
    if basis not in ALLOWED_VERIFICATION_BASIS:
        raise ValueError(f"verification basis must be independent: {sorted(ALLOWED_VERIFICATION_BASIS)}")
    return {
        "id": verification_id or next_layer_id(existing, "ver-"),
        "status": status,
        "basis": basis,
        "derived_from": id_list(derived_from),
        "payload": dict(payload or {}),
        "layer": LAYER_VERIFICATION,
    }


def consistency_hint_for_llm_status(status: str | None) -> str:
    if not isinstance(status, str):
        return CONSISTENCY_UNCLEAR
    return _LLM_STATUS_TO_HINT.get(status.strip(), CONSISTENCY_UNCLEAR)


def content_type_for_source_kind(source_kind: str | None) -> str | None:
    if not isinstance(source_kind, str) or not source_kind.strip():
        return None
    return SOURCE_KIND_TO_CONTENT_TYPE.get(source_kind.strip())


def referenced_ids(*layers: list[dict] | None) -> set[str]:
    found: set[str] = set()
    for layer in layers:
        for item in layer or []:
            if not isinstance(item, dict):
                continue
            for ref in id_list(item.get("derived_from")):
                found.add(ref)
    return found


def uninterpreted_evidence_ids(memory: dict | None) -> list[str]:
    """Evidence already cited by a Claim or Interpretation has entered the chain."""
    extra = memory if isinstance(memory, dict) else {}
    referenced = referenced_ids(extra.get("claims"), extra.get("interpretations"))
    leftover: list[str] = []
    for item in extra.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        ident = item.get("id")
        if isinstance(ident, str) and ident and ident not in referenced:
            leftover.append(ident)
    return leftover


def has_provenance(item: dict | None) -> bool:
    if not isinstance(item, dict):
        return False
    if id_list(item.get("derived_from")):
        return True
    source_type = item.get("source_type") or item.get("source_kind")
    if isinstance(source_type, str) and source_type.strip():
        return True
    source_reference = item.get("source_reference") or item.get("quote")
    return isinstance(source_reference, str) and bool(source_reference.strip())
