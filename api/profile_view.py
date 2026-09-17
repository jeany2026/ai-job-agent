"""Map a stored CandidateProfile record into a display payload. No new profile model."""

from __future__ import annotations

from typing import Any

from storage.candidate_profile import CandidateProfileStore, DEFAULT_CANDIDATE_ID
from storage.errors import MissingStorageError


def empty_profile_view(*, candidate_id: str = DEFAULT_CANDIDATE_ID) -> dict[str, Any]:
    return {
        "ok": True,
        "exists": False,
        "candidate_id": candidate_id,
        "profile_version": None,
        "updated_at": None,
        "summary": None,
        "years_experience": None,
        "core_experience": [],
        "core_capabilities": [],
        "project_experience": [],
        "industry_experience": [],
        "transferable_capabilities": [],
        "career_direction": [],
    }


def load_profile_view(directory: str, candidate_id: str = DEFAULT_CANDIDATE_ID) -> dict[str, Any]:
    store = CandidateProfileStore(directory)
    cid = (candidate_id or DEFAULT_CANDIDATE_ID).strip() or DEFAULT_CANDIDATE_ID
    if not store.exists(cid):
        return empty_profile_view(candidate_id=cid)
    try:
        record = store.load(cid)
    except MissingStorageError:
        return empty_profile_view(candidate_id=cid)
    return serialize_profile_record(record)


def serialize_profile_record(record: dict | None) -> dict[str, Any]:
    if not isinstance(record, dict):
        return empty_profile_view()
    profile = record.get("profile") if isinstance(record.get("profile"), dict) else {}
    candidate_id = str(record.get("candidate_id") or DEFAULT_CANDIDATE_ID)
    view = empty_profile_view(candidate_id=candidate_id)
    view["exists"] = True
    view["profile_version"] = record.get("profile_version")
    view["updated_at"] = record.get("updated_at")
    view["summary"] = _optional_text(profile.get("summary"))
    view["years_experience"] = _optional_text(profile.get("years_experience"))
    view["core_experience"] = _core_experience(profile)
    view["core_capabilities"] = _capability_names(profile)
    view["project_experience"] = _project_names(profile)
    view["industry_experience"] = _industry_names(profile)
    view["transferable_capabilities"] = _transferable_names(profile)
    view["career_direction"] = _career_direction(profile)
    return view


def _core_experience(profile: dict) -> list[str]:
    items: list[str] = []
    years = _optional_text(profile.get("years_experience"))
    if years:
        items.append(years if "年" in years else f"{years}年经验")
    for industry in _industry_names(profile):
        if industry not in items:
            items.append(industry)
    for name in _project_names(profile)[:4]:
        if name not in items:
            items.append(name)
    return items


def _capability_names(profile: dict) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for field in (
        "product_capabilities",
        "business_capabilities",
        "technical_capabilities",
        "direct_capabilities",
        "management_experience",
    ):
        for item in profile.get(field) or []:
            name = _item_name(item)
            if not name:
                continue
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            names.append(name)
    return names


def _project_names(profile: dict) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for item in profile.get("project_experience") or []:
        name = _item_name(item)
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def _industry_names(profile: dict) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for item in profile.get("industry_experience") or []:
        if not isinstance(item, dict):
            continue
        name = _optional_text(item.get("industry"))
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def _transferable_names(profile: dict) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for item in profile.get("transferable_capabilities") or []:
        name = _item_name(item)
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def _career_direction(profile: dict) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for value in list(profile.get("target_roles") or []) + list(profile.get("locations") or []):
        text = _optional_text(value)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(text)
    return names


def _item_name(item: Any) -> str | None:
    if isinstance(item, str):
        return _optional_text(item)
    if not isinstance(item, dict):
        return None
    return _optional_text(item.get("name"))


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None
