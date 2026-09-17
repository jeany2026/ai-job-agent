"""Runtime CandidateContext. Not a persistent CandidateProfile."""

from __future__ import annotations

from typing import Any

from candidate.memory import memory_is_usable


def build_candidate_context(
    *,
    persistent_profile: dict | None,
    candidate_supplement: dict | None = None,
    matching_context: str | None = None,
    preferences: list[str] | None = None,
    constraints: dict | None = None,
    profile_version: int | None = None,
    candidate_memory: dict | None = None,
) -> dict:
    from understanding.schema import empty_constraints, empty_supplement

    return {
        "persistent_profile": persistent_profile,
        "candidate_supplement": candidate_supplement or empty_supplement(),
        "matching_context": matching_context,
        "preferences": list(preferences or []),
        "constraints": constraints or empty_constraints(),
        "profile_version": profile_version,
        "candidate_memory": candidate_memory,
    }


def context_has_candidate_evidence(context: dict | None) -> bool:
    from understanding.schema import has_candidate_supplement

    if not isinstance(context, dict):
        return False
    if memory_is_usable(context.get("candidate_memory")):
        return True
    profile = context.get("persistent_profile")
    if _profile_is_usable(profile):
        return True
    return has_candidate_supplement({"candidate_supplement": context.get("candidate_supplement") or {}})


def persistent_profile_is_usable(profile: dict | None) -> bool:
    return _profile_is_usable(profile)


def compose_profile_evidence(
    *,
    resume_text: str | None,
    attachments: list[dict] | None,
    user_message: str | None,
    include_user_message: bool,
) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    if resume_text and resume_text.strip():
        block = resume_text.strip()
        parts.append("## Resume\n" + block)
        seen.add(_fold(block))
    for item in attachments or []:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        folded = _fold(text)
        if folded in seen:
            continue
        seen.add(folded)
        name = item.get("filename") or item.get("kind") or "attachment"
        parts.append(f"## Attachment {name}\n{text.strip()}")
    if include_user_message and user_message and _fold(user_message) not in seen:
        parts.append("## User statement\n" + user_message.strip())
    return "\n\n".join(parts).strip()


def _profile_is_usable(profile: dict | None) -> bool:
    if not isinstance(profile, dict):
        return False
    status = profile.get("analysis_status")
    if status in {None, "ok"}:
        return status == "ok" or _has_profile_body(profile)
    return False


def _has_profile_body(profile: dict) -> bool:
    if profile.get("summary"):
        return True
    for key in (
        "product_capabilities",
        "business_capabilities",
        "technical_capabilities",
        "project_experience",
        "direct_capabilities",
        "industry_experience",
    ):
        if profile.get(key):
            return True
    return False


def _fold(text: str) -> str:
    return "".join(text.split()).casefold()
