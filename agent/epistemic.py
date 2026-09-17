"""Epistemic layer labels and restore provenance helpers.

Does not decide business next steps. Does not invent Evidence or Facts.
"""

from __future__ import annotations

from typing import Any

LAYER_FACT = "fact"
LAYER_CLAIM = "claim"
LAYER_INTERPRETATION = "interpretation"
LAYER_VERIFICATION = "verification"
LAYER_OBSERVATION = "observation"
LAYER_PROGRAM_HINT = "program_hint"

PROVENANCE_OBSERVED = "observed"
PROVENANCE_LLM = "llm_interpretation"
PROVENANCE_RESTORED = "restored"
PROVENANCE_UNVERIFIED = "unverified"

EVIDENCE_STATUS_UNVERIFIED = "unverified"
EVIDENCE_STATUS_RESTORED = "restored_unverified"


def is_restored(blob: Any) -> bool:
    if not isinstance(blob, dict):
        return False
    epistemic = blob.get("epistemic") if isinstance(blob.get("epistemic"), dict) else {}
    provenance = blob.get("provenance") or epistemic.get("provenance")
    status = blob.get("evidence_status") or epistemic.get("evidence_status")
    return provenance == PROVENANCE_RESTORED or status == EVIDENCE_STATUS_RESTORED


def stamp_restored(blob: Any, *, source: str = "persistence") -> Any:
    """Mark an Interpretation blob as restored/unverified. No-op for non-dicts."""
    if not isinstance(blob, dict):
        return blob
    view = dict(blob)
    epistemic = dict(view.get("epistemic") or {}) if isinstance(view.get("epistemic"), dict) else {}
    epistemic.update(
        {
            "provenance": PROVENANCE_RESTORED,
            "evidence_status": EVIDENCE_STATUS_RESTORED,
            "restored": True,
            "verified": False,
            "source": epistemic.get("source") or source,
        }
    )
    view["epistemic"] = epistemic
    view["provenance"] = PROVENANCE_RESTORED
    view["evidence_status"] = EVIDENCE_STATUS_RESTORED
    view.setdefault("layer", LAYER_INTERPRETATION)
    if view.get("kind") in (None, ""):
        view["kind"] = "interpretation"
    derived = view.get("derived_from")
    if not isinstance(derived, list):
        view["derived_from"] = []
    return view


def stamp_llm_interpretation(blob: Any, *, source: str) -> Any:
    if not isinstance(blob, dict):
        return blob
    view = dict(blob)
    if is_restored(view):
        return view
    epistemic = dict(view.get("epistemic") or {}) if isinstance(view.get("epistemic"), dict) else {}
    epistemic.setdefault("provenance", PROVENANCE_LLM)
    epistemic.setdefault("evidence_status", EVIDENCE_STATUS_UNVERIFIED)
    epistemic.setdefault("restored", False)
    epistemic.setdefault("verified", False)
    epistemic.setdefault("source", source)
    view["epistemic"] = epistemic
    view.setdefault("provenance", epistemic["provenance"])
    view.setdefault("evidence_status", epistemic["evidence_status"])
    view.setdefault("layer", LAYER_INTERPRETATION)
    if not isinstance(view.get("derived_from"), list):
        view["derived_from"] = list(view.get("derived_from") or []) if view.get("derived_from") else []
    return view


def observed_stage_from_record_fields(
    *,
    opened: dict | None,
    listed: dict | None,
    persisted_stage: str | None = None,
) -> str:
    """Stage from observed open/list material — never analyzed/matched from interpretations.

    Persistence labels analyzed/matched are downgraded to opened when open material
    (or continuity stage beyond listed) exists; they never become Live analyzed/matched.
    """
    if isinstance(opened, dict) and opened:
        return "opened"
    prior = str(persisted_stage or "").strip().casefold()
    if prior in {"opened", "analyzed", "matched", "surfaced", "waiting_user"}:
        return "opened"
    if isinstance(listed, dict) and listed:
        return "listed"
    return "listed"


def program_hints_shell(**fields: Any) -> dict[str, Any]:
    """Non-Observation diagnostics. Never a business next_action."""
    payload = {
        "layer": LAYER_PROGRAM_HINT,
        "kind": "diagnostics",
        "not_observation": True,
        "not_fact": True,
        "not_instruction": True,
        "not_next_action": True,
    }
    payload.update(fields)
    return payload
