"""Raw User Information Intake.

Carrier metadata only. No business identity (Evidence / Candidate / Job).
Semantic Understanding (on demand) interprets content; Reduce may admit Evidence.
"""

from __future__ import annotations

from typing import Any

from candidate.semantic import CONTENT_TYPE_INTAKE_TEXT, fold_text, next_layer_id

CARRIER_TEXT = "text"
CARRIER_UPLOAD = "upload"
CARRIER_COMPAT = "compat_parameter"

# Neutral Evidence label after admission — not a carrier-derived business type.
ADMITTED_CONTENT_TYPE = CONTENT_TYPE_INTAKE_TEXT


def empty_intake() -> list[dict[str, Any]]:
    return []


def make_intake_item(
    *,
    text: str,
    carrier: str,
    filename: str | None = None,
    intake_id: str | None = None,
    existing: list[dict] | None = None,
    client_hint: str | None = None,
) -> dict[str, Any]:
    body = text if isinstance(text, str) else ""
    item: dict[str, Any] = {
        "id": intake_id or next_layer_id(existing, "in-"),
        "carrier": carrier,
        "text": body,
        "char_count": len(body),
        "filename": filename,
    }
    # Non-authoritative client label only; never used as World business identity.
    if isinstance(client_hint, str) and client_hint.strip():
        item["client_hint"] = client_hint.strip()
    return item


def seed_turn_intake(
    *,
    user_message: str | None,
    attachments: list[dict] | None = None,
    resume_text: str | None = None,
) -> list[dict[str, Any]]:
    """Build raw intake list. Does not create Evidence or assign business types."""
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(text: str, *, carrier: str, filename: str | None = None, client_hint: str | None = None) -> None:
        body = text.strip() if isinstance(text, str) else ""
        if not body:
            return
        folded = fold_text(body)
        if folded in seen:
            return
        seen.add(folded)
        items.append(
            make_intake_item(
                text=body,
                carrier=carrier,
                filename=filename,
                existing=items,
                client_hint=client_hint,
            )
        )

    if isinstance(user_message, str) and user_message.strip():
        _add(user_message, carrier=CARRIER_TEXT)

    # Compatibility: resume= is another text blob, not a pre-labeled resume Evidence.
    if isinstance(resume_text, str) and resume_text.strip():
        _add(resume_text, carrier=CARRIER_COMPAT)

    for raw in attachments or []:
        if not isinstance(raw, dict):
            continue
        text = raw.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        filename = raw.get("filename") or raw.get("name")
        hint = raw.get("client_hint") or raw.get("kind") or raw.get("content_type")
        _add(
            text,
            carrier=CARRIER_UPLOAD if filename or hint else CARRIER_TEXT,
            filename=str(filename).strip() if filename else None,
            client_hint=str(hint).strip() if hint else None,
        )
    return items


def list_intake(state: Any) -> list[dict[str, Any]]:
    items = list(getattr(state, "intake", None) or [])
    return [item for item in items if isinstance(item, dict) and item.get("id")]


def raw_candidate_intake_present(state: Any) -> bool:
    """True when this turn already has upload/compat text intake (not analyzed yet).

    Carrier/filename only — not business identity, not a substitute for Evidence.
    Plain user-message text alone does not count.
    """
    for item in list_intake(state):
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        carrier = str(item.get("carrier") or "")
        if carrier in {CARRIER_UPLOAD, CARRIER_COMPAT}:
            return True
        if item.get("filename"):
            return True
    for item in list(getattr(state, "attachments", None) or []):
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            return True
    return False


def intake_ref(item: dict[str, Any], *, excerpt_max: int = 200) -> dict[str, Any]:
    text = item.get("text") if isinstance(item.get("text"), str) else ""
    return {
        "id": item.get("id"),
        "carrier": item.get("carrier"),
        "filename": item.get("filename"),
        "char_count": item.get("char_count") if item.get("char_count") is not None else len(text),
        "short_excerpt": text[:excerpt_max],
        # client_hint intentionally omitted from Reasoner view — not business identity
    }


def admit_intake_as_evidence(
    memory: dict | None,
    intake_items: list[dict[str, Any]],
    *,
    content_type: str = ADMITTED_CONTENT_TYPE,
) -> dict:
    """Admit raw intake into Evidence after semantic work. No carrier→type mapping."""
    from candidate.memory import ensure_text_evidence, refresh_memory_status

    result = refresh_memory_status(memory or {})
    for item in intake_items:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        carrier = str(item.get("carrier") or CARRIER_TEXT)
        origin = f"intake.{carrier}"
        result, _ = ensure_text_evidence(
            result,
            text,
            content_type=content_type,
            origin=origin,
            source="intake",
            source_ref=item.get("filename") or item.get("id"),
        )
    return refresh_memory_status(result)
