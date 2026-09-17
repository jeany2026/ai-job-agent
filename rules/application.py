"""Read already-applied keys and interpret application_evidence.

User/tracker keys are mechanical annotations. application_evidence is
Interpretation for Reasoner — not a Program exclude instruction.
"""

from __future__ import annotations


def application_exclude_reason(
    *,
    job_key: str | None,
    already_applied_keys: list[str] | None,
    interpret_result: dict | None,
    tracker=None,
) -> str | None:
    """Annotate user/tracker already-applied keys. Ignore application_evidence."""
    if job_key and job_key in applied_key_set(already_applied_keys, tracker):
        return "already_applied"
    return None


def application_evidence_of(interpret_result: dict | None) -> str | None:
    """Read LLM interpretation. Not a Program exclude instruction."""
    return _application_evidence(interpret_result)


def applied_key_set(already_applied_keys: list[str] | None, tracker=None) -> set[str]:
    """Union of State keys and tracker keys. Tracker is structured IO, not semantics."""
    keys = {str(item).strip() for item in (already_applied_keys or []) if str(item).strip()}
    if tracker is None:
        return keys
    for item in tracker.applied_keys() or []:
        text = str(item).strip()
        if text:
            keys.add(text)
    return keys


def _application_evidence(interpret_result: dict | None) -> str | None:
    if not isinstance(interpret_result, dict):
        return None
    inferred = interpret_result.get("inferred_context")
    if not isinstance(inferred, dict):
        return None
    evidence = inferred.get("application_evidence")
    if isinstance(evidence, str) and evidence.strip():
        return evidence.strip()
    return None
