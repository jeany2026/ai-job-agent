"""Bind Reasoner Action pointers to World. Does not choose the next Tool.

Action carries identities / decision params. Binding loads authoritative material
from World. Binding never picks another entity or invents a business next step.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agent.intake import CARRIER_TEXT, intake_ref, list_intake
from job.common_schema import job_key as make_job_key

if TYPE_CHECKING:
    from agent.decide import Action
    from agent.state import AgentState, JobRecord

EXCERPT_MAX = 200
EVIDENCE_CONSUMING_TOOLS = frozenset({"understand_user_input", "analyze_candidate"})
JOB_MATERIAL_TOOLS = frozenset(
    {
        "analyze_job",
        "match_job",
        "interpret_job_actions",
        "execute_action",
        "execute_job_action",
        "inspect_job",
        "inspect_application_state",
        "open_job",
        "mock_open_job",
        "open_boss_job",
        "open_liepin_job",
        "open_51job_job",
    }
)
ACTION_BODY_KEYS = frozenset(
    {
        "resume",
        "text",
        "attachments",
        "existing_memory",
        "existing_profile",
        "candidate_profile",
        "candidate_memory",
        "candidate_context",
        "message",
        "job",
        "job_profile",
        "job_listing",
        "page_context",
        "interpret_result",
    }
)

KIND_AMBIGUOUS = "ambiguous_evidence"
KIND_MISSING = "missing_evidence"
KIND_MISSING_JOB = "missing_job"
KIND_MISSING_JOB_MATERIAL = "missing_job_material"
KIND_MISSING_JOB_PROFILE = "missing_job_profile"
KIND_MISSING_CANDIDATE_MATERIAL = "insufficient_candidate_material"


def list_state_evidence(state: "AgentState") -> list[dict[str, Any]]:
    memory = getattr(getattr(state, "candidate", None), "memory", None)
    memory = memory if isinstance(memory, dict) else {}
    items = list(getattr(state, "evidence", None) or memory.get("evidence") or [])
    return [item for item in items if isinstance(item, dict) and item.get("id")]


def evidence_ref(item: dict[str, Any]) -> dict[str, Any]:
    content = item.get("content")
    text = content if isinstance(content, str) else ""
    return {
        "id": item.get("id"),
        "content_type": item.get("content_type"),
        "origin": item.get("origin"),
        "source": item.get("source"),
        "source_ref": item.get("source_ref"),
        "short_excerpt": text[:EXCERPT_MAX],
        "char_count": len(text),
    }


def attachment_ref(item: dict[str, Any], *, evidence_id: str | None = None) -> dict[str, Any]:
    text = item.get("text")
    body = text if isinstance(text, str) else ""
    return {
        "id": evidence_id,
        "filename": item.get("filename") or item.get("name") or item.get("source_ref"),
        "carrier": item.get("carrier"),
        "has_body": bool(body.strip()),
        "length": len(body),
    }


def bind_tool_arguments(state: "AgentState", action: "Action") -> dict[str, Any]:
    """Resolve intake_ids / evidence_ids / job_key into Tool input. Never selects a Tool."""
    name = action.tool_name
    raw = dict(action.arguments or {})
    if name in EVIDENCE_CONSUMING_TOOLS:
        return _bind_evidence_tool(state, name, raw)
    if name in JOB_MATERIAL_TOOLS:
        return _bind_job_tool(state, name, raw)
    return {"ok": True, "arguments": raw}


def _bind_evidence_tool(state: "AgentState", name: str, raw: dict[str, Any]) -> dict[str, Any]:
    requested_intake = _id_list(raw.get("intake_ids"))
    requested_evidence = _id_list(raw.get("evidence_ids"))
    intake_items = list_intake(state)
    evidence_items = list_state_evidence(state)
    intake_catalog = [intake_ref(item, excerpt_max=EXCERPT_MAX) for item in intake_items]
    evidence_catalog = [evidence_ref(item) for item in evidence_items]
    stripped = {key: value for key, value in raw.items() if key not in ACTION_BODY_KEYS}

    if not requested_intake and not requested_evidence:
        has_material = bool(intake_items or evidence_items)
        kind = KIND_AMBIGUOUS if has_material else KIND_MISSING
        return {
            "ok": False,
            "observation": _bind_observation(
                kind,
                tool_name=name,
                requested_intake=[],
                requested_evidence=[],
                available_intake=intake_catalog,
                available_evidence=evidence_catalog,
                missing_intake=[],
                missing_evidence=[],
            ),
        }

    by_intake = {str(item.get("id")): item for item in intake_items}
    by_evidence = {str(item.get("id")): item for item in evidence_items}
    resolved_intake: list[dict[str, Any]] = []
    resolved_evidence: list[dict[str, Any]] = []
    missing_intake: list[str] = []
    missing_evidence: list[str] = []
    for ident in requested_intake:
        item = by_intake.get(ident)
        if item is None:
            missing_intake.append(ident)
        else:
            resolved_intake.append(item)
    for ident in requested_evidence:
        item = by_evidence.get(ident)
        if item is None:
            missing_evidence.append(ident)
        else:
            resolved_evidence.append(item)
    if missing_intake or missing_evidence:
        return {
            "ok": False,
            "observation": _bind_observation(
                KIND_MISSING,
                tool_name=name,
                requested_intake=requested_intake,
                requested_evidence=requested_evidence,
                available_intake=intake_catalog,
                available_evidence=evidence_catalog,
                missing_intake=missing_intake,
                missing_evidence=missing_evidence,
            ),
        }

    texts_ok = any(
        (item.get("text") or "").strip()
        for item in resolved_intake
        if isinstance(item.get("text"), str)
    ) or any(
        (item.get("content") or "").strip()
        for item in resolved_evidence
        if isinstance(item.get("content"), str)
    )
    if not texts_ok:
        return {
            "ok": False,
            "observation": _bind_observation(
                KIND_MISSING,
                tool_name=name,
                requested_intake=requested_intake,
                requested_evidence=requested_evidence,
                available_intake=intake_catalog,
                available_evidence=evidence_catalog,
                missing_intake=requested_intake,
                missing_evidence=requested_evidence,
            ),
        }

    if name == "understand_user_input":
        arguments = _understand_input(state, resolved_intake, resolved_evidence)
    else:
        arguments = _analyze_candidate_input(state, resolved_intake, resolved_evidence)
    arguments.update(stripped)
    arguments["intake_ids"] = requested_intake
    arguments["evidence_ids"] = requested_evidence
    return {"ok": True, "arguments": arguments}


def _bind_job_tool(state: "AgentState", name: str, raw: dict[str, Any]) -> dict[str, Any]:
    stripped = {key: value for key, value in raw.items() if key not in ACTION_BODY_KEYS}
    record = _resolve_job_record(state, raw)
    if record is None:
        return {
            "ok": False,
            "observation": {
                "kind": KIND_MISSING_JOB,
                "type": KIND_MISSING_JOB,
                "tool_name": name,
                "requested_job_key": _requested_job_key(raw),
                "available_job_keys": sorted(state.jobs.keys()),
                "error": "job identity not found in World",
            },
        }

    identity = _job_identity(record)
    if name in {
        "open_job",
        "mock_open_job",
        "open_boss_job",
        "open_liepin_job",
        "open_51job_job",
    }:
        arguments = {**stripped, **identity}
        return {"ok": True, "arguments": arguments}

    if name in {"inspect_job", "inspect_application_state"}:
        arguments = {**stripped, **identity, "job": dict(record.opened or record.listed or {})}
        if name == "inspect_application_state":
            arguments.setdefault("already_applied", list(state.constraints.already_applied or []))
            arguments.setdefault("application_history", list(state.application_history or []))
            opened = record.opened if isinstance(record.opened, dict) else {}
            if opened.get("raw_actions") is not None:
                arguments.setdefault("raw_actions", list(opened.get("raw_actions") or []))
        return {"ok": True, "arguments": arguments}

    if name == "analyze_job":
        job = dict(record.opened or record.listed or {})
        if not _job_has_analyzable_fact(job):
            return {
                "ok": False,
                "observation": {
                    "kind": KIND_MISSING_JOB_MATERIAL,
                    "type": KIND_MISSING_JOB_MATERIAL,
                    "tool_name": name,
                    "job_key": record.job_key,
                    "missing": ["job_description"],
                    "error": "World job has no JD fact to analyze",
                },
            }
        return {"ok": True, "arguments": {**stripped, **identity, "job": job}}

    if name == "match_job":
        profile = record.job_profile if isinstance(record.job_profile, dict) else None
        if profile is None:
            return {
                "ok": False,
                "observation": {
                    "kind": KIND_MISSING_JOB_PROFILE,
                    "type": KIND_MISSING_JOB_PROFILE,
                    "tool_name": name,
                    "job_key": record.job_key,
                    "missing": ["job_profile"],
                    "error": "World job has no job_profile interpretation",
                },
            }
        candidate_context = state.candidate_context if isinstance(state.candidate_context, dict) else None
        candidate_profile = state.candidate.profile if isinstance(state.candidate.profile, dict) else None
        if not _candidate_material_present(candidate_context, candidate_profile, state):
            from agent.intake import raw_candidate_intake_present

            return {
                "ok": False,
                "observation": {
                    "kind": KIND_MISSING_CANDIDATE_MATERIAL,
                    "type": KIND_MISSING_CANDIDATE_MATERIAL,
                    "tool_name": name,
                    "job_key": record.job_key,
                    "missing": ["candidate_material"],
                    "error": "insufficient_candidate_material: World has no usable candidate material for matching",
                    # Facts for Reasoner — not a prescribed next Tool.
                    "raw_candidate_intake_present": raw_candidate_intake_present(state),
                    "candidate_material_present": False,
                },
            }
        arguments = {
            **stripped,
            **identity,
            "job_profile": profile,
            "candidate_context": candidate_context,
            "candidate_profile": candidate_profile,
        }
        return {"ok": True, "arguments": arguments}

    if name == "interpret_job_actions":
        opened = record.opened if isinstance(record.opened, dict) else {}
        actions = opened.get("raw_actions")
        if not isinstance(actions, list):
            return {
                "ok": False,
                "observation": {
                    "kind": KIND_MISSING_JOB_MATERIAL,
                    "type": KIND_MISSING_JOB_MATERIAL,
                    "tool_name": name,
                    "job_key": record.job_key,
                    "missing": ["raw_actions"],
                    "error": "World job has no raw_actions to interpret",
                },
            }
        page_context = {
            "platform": opened.get("platform") or (record.listed or {}).get("platform"),
            "job_id": opened.get("job_id") or (record.listed or {}).get("job_id"),
            "job_url": opened.get("job_url") or (record.listed or {}).get("job_url"),
            "actions": list(actions),
        }
        return {
            "ok": True,
            "arguments": {**stripped, **identity, "page_context": page_context},
        }

    if name in {"execute_action", "execute_job_action"}:
        job = dict(record.opened or record.listed or {})
        arguments = {
            **stripped,
            **identity,
            "job": job,
        }
        if isinstance(record.interpret_result, dict):
            arguments["interpret_result"] = record.interpret_result
        return {"ok": True, "arguments": arguments}

    return {"ok": True, "arguments": {**stripped, **identity}}


def _resolve_job_record(state: "AgentState", raw: dict[str, Any]) -> "JobRecord | None":
    """Map Action identity fields to live World JobRecord. No fallback to 'best' job."""
    blobs: list[dict[str, Any]] = [raw]
    for nested_key in ("job", "page_context", "job_profile", "job_listing"):
        nested = raw.get(nested_key)
        if isinstance(nested, dict):
            blobs.append(nested)

    for blob in blobs:
        explicit = blob.get("job_key")
        if isinstance(explicit, str) and explicit.strip():
            key = explicit.strip()
            if key in state.jobs:
                return state.jobs[key]
            return None
        key = make_job_key(blob)
        if key and key in state.jobs:
            return state.jobs[key]
        job_id = blob.get("job_id")
        if job_id:
            matches = [
                record
                for record in state.jobs.values()
                if job_id in {(record.listed or {}).get("job_id"), (record.opened or {}).get("job_id") if record.opened else None}
            ]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                return None
        job_url = blob.get("job_url")
        if job_url:
            matches = [
                record
                for record in state.jobs.values()
                if job_url
                in {(record.listed or {}).get("job_url"), (record.opened or {}).get("job_url") if record.opened else None}
            ]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                return None
    return None


def _requested_job_key(raw: dict[str, Any]) -> str | None:
    for blob in (raw, raw.get("job") if isinstance(raw.get("job"), dict) else None):
        if not isinstance(blob, dict):
            continue
        key = blob.get("job_key")
        if isinstance(key, str) and key.strip():
            return key.strip()
        derived = make_job_key(blob)
        if derived:
            return derived
    return None


def _job_identity(record: "JobRecord") -> dict[str, Any]:
    job = record.opened or record.listed or {}
    identity: dict[str, Any] = {"job_key": record.job_key}
    for field in ("job_id", "job_url", "platform"):
        value = job.get(field)
        if value:
            identity[field] = value
    return identity


def _job_has_analyzable_fact(job: dict[str, Any]) -> bool:
    for field in ("job_description", "requirements", "job_title"):
        value = job.get(field)
        if isinstance(value, str) and value.strip():
            return True
    return False


def _candidate_material_present(
    candidate_context: dict | None,
    candidate_profile: dict | None,
    state: "AgentState",
) -> bool:
    from candidate.context import context_has_candidate_evidence

    if context_has_candidate_evidence(candidate_context):
        return True
    if isinstance(candidate_profile, dict) and (
        candidate_profile.get("analysis_status") == "ok"
        or candidate_profile.get("summary")
        or candidate_profile.get("direct_capabilities")
        or candidate_profile.get("project_experience")
    ):
        return True
    memory = getattr(state.candidate, "memory", None)
    from candidate.memory import memory_is_usable

    return memory_is_usable(memory)


def _id_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        text = raw.strip()
        return [text] if text else []
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = str(item).strip() if item is not None else ""
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _understand_input(
    state: "AgentState",
    intake_items: list[dict[str, Any]],
    evidence_items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Load raw text for Understanding. No carrier→business-kind mapping."""
    messages: list[str] = []
    attachments: list[dict[str, Any]] = []
    for item in intake_items:
        text = item.get("text") if isinstance(item.get("text"), str) else ""
        if not text.strip():
            continue
        carrier = str(item.get("carrier") or CARRIER_TEXT)
        if carrier == CARRIER_TEXT and not item.get("filename"):
            messages.append(text)
            continue
        record: dict[str, Any] = {"text": text}
        if item.get("filename"):
            record["filename"] = item.get("filename")
        attachments.append(record)
    for item in evidence_items:
        content = item.get("content") if isinstance(item.get("content"), str) else ""
        if not content.strip():
            continue
        # Admitted Evidence is already World material; still no business remapping.
        attachments.append(
            {
                "filename": item.get("source_ref"),
                "text": content,
            }
        )
    arguments: dict[str, Any] = {"message": "\n".join(messages) if messages else None}
    if attachments:
        arguments["attachments"] = attachments
    if not arguments.get("message") and attachments:
        # Pure uploads / evidence: fold first body into message so coerce is not empty.
        first = attachments[0]
        arguments["message"] = first.get("text")
        arguments["attachments"] = attachments[1:] or None
        if arguments["attachments"] is None:
            arguments.pop("attachments", None)
    session = getattr(state, "session_context", None)
    if isinstance(session, dict):
        arguments["session_context"] = session
    return arguments


def _analyze_candidate_input(
    state: "AgentState",
    intake_items: list[dict[str, Any]],
    evidence_items: list[dict[str, Any]],
) -> dict[str, Any]:
    texts: list[str] = []
    for item in intake_items:
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            texts.append(text)
    for item in evidence_items:
        content = item.get("content")
        if isinstance(content, str) and content.strip():
            texts.append(content)
    candidate = getattr(state, "candidate", None)
    arguments: dict[str, Any] = {
        "resume": {
            "text": "\n\n".join(texts),
            "candidate_id": getattr(candidate, "candidate_id", None),
            "intake_ids": [item.get("id") for item in intake_items if item.get("id")],
            "evidence_ids": [item.get("id") for item in evidence_items if item.get("id")],
        }
    }
    memory = getattr(candidate, "memory", None)
    if isinstance(memory, dict):
        arguments["existing_memory"] = memory
    profile = getattr(candidate, "profile", None)
    if isinstance(profile, dict):
        arguments["existing_profile"] = profile
    return arguments


def _bind_observation(
    kind: str,
    *,
    tool_name: str,
    requested_intake: list[str],
    requested_evidence: list[str],
    available_intake: list[dict[str, Any]],
    available_evidence: list[dict[str, Any]],
    missing_intake: list[str],
    missing_evidence: list[str],
) -> dict[str, Any]:
    return {
        "kind": kind,
        "type": kind,
        "tool_name": tool_name,
        "requested_intake_ids": list(requested_intake),
        "requested_evidence_ids": list(requested_evidence),
        "available_intake": available_intake,
        "available_evidence": available_evidence,
        "missing_intake_ids": list(missing_intake),
        "missing_evidence_ids": list(missing_evidence),
    }
