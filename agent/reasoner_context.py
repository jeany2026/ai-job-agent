"""Assemble a read-only World projection for the Reasoner.

Context is a view — not a second World, not a Pipeline stage, not a place to
invent Evidence or disguise Interpretation as Fact. Full raw bodies stay in
World; Binding loads them when the Reasoner selects an identity.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agent.bind_arguments import attachment_ref, evidence_ref, list_state_evidence
from agent.action_facts import build_action_state_facts, stamp_execution_outcome
from agent.epistemic import (
    LAYER_CLAIM,
    LAYER_FACT,
    LAYER_INTERPRETATION,
    LAYER_OBSERVATION,
    LAYER_PROGRAM_HINT,
    LAYER_VERIFICATION,
    PROVENANCE_LLM,
    PROVENANCE_OBSERVED,
    PROVENANCE_RESTORED,
    EVIDENCE_STATUS_UNVERIFIED,
    is_restored,
    program_hints_shell,
    stamp_llm_interpretation,
)
from agent.intake import intake_ref, list_intake
from candidate.memory import project_profile_view, refresh_memory_status
from rules.application import application_evidence_of
from tools.registry import available_tool_contracts

if TYPE_CHECKING:
    from agent.state import AgentState, JobRecord
    from tools.registry import ToolRegistry

PROGRAM_CONCLUSION_FIELDS = (
    "current_job",
    "next_job_to_open",
    "next_action",
    "should_continue_search",
    "should_stop_enriching",
    "recommended_count",
    "stop_reason",
)


def build_reasoner_payload(state: "AgentState", registry: "ToolRegistry") -> dict:
    from rules.quota import listed_count, remaining_llm_calls, remaining_list_capacity, remaining_opens

    memory = refresh_memory_status(getattr(state.candidate, "memory", None) or {})
    stored_evidence = list_state_evidence(state)
    evidence = [evidence_ref(item) for item in stored_evidence]
    intake = [intake_ref(item) for item in list_intake(state)]
    claims = [_claim_projection(item) for item in (state.claims or memory.get("claims") or [])]
    interpretations = [
        _interpretation_projection(item)
        for item in (state.interpretations or memory.get("interpretations") or [])
    ]
    verifications = [
        _verification_projection(item)
        for item in (state.verifications or memory.get("verifications") or [])
    ]
    jobs = _job_views(state)
    candidate_profile = _candidate_profile_projection(state, memory)
    understanding = state.understanding if isinstance(state.understanding, dict) else {}
    goal = _goal_projection(state.goal)
    attachments = _attachment_refs(state, intake)
    persistence = _persistence_view(state)
    context = {
        "understanding_status": getattr(state, "understanding_status", None) or "not_understood",
        # Index only — full text lives in Intake / Evidence; Binding loads bodies.
        "raw_user_message_present": bool(
            isinstance(state.pending_goal, str) and state.pending_goal.strip()
        ),
        "raw_user_message_excerpt": _excerpt(state.pending_goal),
        "intake": intake,
        "attachments": attachments,
        "persistence": persistence,
        "candidate_id": state.candidate.candidate_id,
        "data_source": state.constraints.data_source,
        "waiting_user_job_key": _waiting_user_job_key(state),
        "job_search_task_id": state.job_search_task_id,
        "pending_job_key": state.pending_job_key,
        "follow_up_of": state.follow_up_of,
        "candidate_material_present": _candidate_material_flag(state, memory),
        "raw_candidate_intake_present": _raw_candidate_intake_flag(state),
        "has_candidate_supplement": _has_candidate_supplement_flag(state),
        "unexplored_listed_count": _unexplored_listed_count(state),
        "unexplored_listed_job_keys": _unexplored_listed_job_keys(state),
        "already_analyzed_job_keys": _already_analyzed_job_keys(state),
        "already_matched_job_keys": _already_matched_job_keys(state),
        "task_intent": None,
        "task_kind": understanding.get("task_kind"),
        "active_tasks": list(state.active_tasks or []),
        "task_control": state.task_control,
        "preferences": list(state.preferences or []),
        "candidate_supplement": _supplement_projection(getattr(state.candidate, "supplement", None)),
        "candidate_context": _candidate_context_projection(state, evidence),
        "understanding": {
            "kind": "interpretation",
            "layer": LAYER_INTERPRETATION,
            "task_kind": understanding.get("task_kind"),
            "persist_requested": understanding.get("persist_requested"),
            "matching_context": understanding.get("matching_context"),
            "has_candidate_supplement": _has_candidate_supplement_flag(state),
        },
    }
    constraints = {
        "data_source": state.constraints.data_source,
        # List ingest capacity — NOT "how many searches are left".
        "remaining_list_capacity": remaining_list_capacity(state),
        "remaining_search_slots": remaining_list_capacity(state),  # compat alias
        "remaining_opens": remaining_opens(state),
        "remaining_llm_calls": remaining_llm_calls(state),
        "blacklist": list(state.constraints.blacklist or []),
        "already_applied": list(state.constraints.already_applied or []),
        "cities": list(state.constraints.cities or []),
        "salary_min": state.constraints.salary_min,
        "platforms": list(state.constraints.platforms or []),
        "min_recommend": state.constraints.min_recommend,
    }
    search_stats = state.search.stats if isinstance(state.search.stats, dict) else {}
    history = {
        "decisions": list(state.decisions[-12:]),
        "used_keywords": _used_keywords(state),
        "search_plans": [
            {
                "plan_id": plan.plan_id,
                "keyword": plan.keyword,
                "city": plan.city,
                "exhausted": plan.exhausted,
            }
            for plan in state.search.plans
        ],
        "recorded_stop_reason": state.search.stop_reason,
        "search_stats": {
            "search_executed_count": int(
                search_stats.get("search_executed_count") or search_stats.get("searches") or 0
            ),
            "listed_count": listed_count(state),
            "newly_ingested_total": int(search_stats.get("newly_ingested_total") or 0),
            "duplicate_total": int(search_stats.get("duplicate_total") or 0),
            "zero_increment_count": int(search_stats.get("zero_increment_count") or 0),
            "last_search_newly_ingested": search_stats.get("last_search_newly_ingested"),
            "last_search_duplicates": search_stats.get("last_search_duplicates"),
            "last_search_progress": search_stats.get("last_search_progress"),
            "opened": int(search_stats.get("opened") or 0),
        },
        "search_session": _search_session_projection(state),
    }
    uncertainty = {
        "understanding_status": context["understanding_status"],
        "uninterpreted_evidence_ids": list(memory.get("uninterpreted_evidence_ids") or []),
        "unresolved": list(memory.get("unresolved") or []),
        "errors": list(state.errors[-8:]),
    }
    observation, program_hints = _observation_and_hints(state)
    action_state_facts = build_action_state_facts(state)
    context["action_state_facts"] = action_state_facts
    return {
        "goal": goal,
        "context": context,
        "intake": intake,
        "evidence": evidence,
        "observations": [observation],
        "observation": observation,
        "action_state_facts": action_state_facts,
        "program_hints": program_hints,
        "claims": claims,
        "interpretations": interpretations,
        "verifications": verifications,
        "available_tools": available_tool_contracts(registry),
        "constraints": constraints,
        "history": history,
        "uncertainty": uncertainty,
        "jobs": jobs,
        "candidate_profile": candidate_profile,
        "memory": {
            "understanding_status": context["understanding_status"],
            "raw_user_message_present": context["raw_user_message_present"],
            "raw_user_message_excerpt": context["raw_user_message_excerpt"],
            "intake": intake,
            "attachments": context["attachments"],
            "persistence": persistence,
            "goal": goal,
            "preferences": context["preferences"],
            "candidate_memory": _memory_without_bodies(memory, evidence),
            "candidate_supplement": context["candidate_supplement"],
            "candidate_id": context["candidate_id"],
            "understanding": context["understanding"],
            "evidence": evidence,
            "claims": claims,
            "interpretations": interpretations,
            "verifications": verifications,
            "jobs": jobs,
            "candidate_profile": candidate_profile,
            "action_state_facts": action_state_facts,
        },
    }


def _persistence_view(state: "AgentState") -> dict[str, Any]:
    from agent.world_restore import persistence_view_for_reasoner

    return persistence_view_for_reasoner(state)


def jobs_available(payload: dict | None) -> list[dict[str, Any]]:
    extra = payload if isinstance(payload, dict) else {}
    jobs = extra.get("jobs")
    if isinstance(jobs, list) and jobs:
        return [item for item in jobs if isinstance(item, dict)]
    memory = extra.get("memory") if isinstance(extra.get("memory"), dict) else {}
    nested = memory.get("jobs")
    if isinstance(nested, list):
        return [item for item in nested if isinstance(item, dict)]
    return []


def _candidate_material_flag(state: "AgentState", memory: dict) -> bool:
    from agent.bind_arguments import _candidate_material_present

    del memory  # presence is decided from World candidate layers, not this projection
    candidate_context = state.candidate_context if isinstance(state.candidate_context, dict) else None
    candidate_profile = state.candidate.profile if isinstance(state.candidate.profile, dict) else None
    return _candidate_material_present(candidate_context, candidate_profile, state)


def _raw_candidate_intake_flag(state: "AgentState") -> bool:
    from agent.intake import raw_candidate_intake_present

    return raw_candidate_intake_present(state)


def _has_candidate_supplement_flag(state: "AgentState") -> bool:
    """True when Understanding already extracted candidate materials (text and/or attachments)."""
    from understanding.schema import has_candidate_supplement

    understanding = state.understanding if isinstance(state.understanding, dict) else {}
    if has_candidate_supplement(understanding):
        return True
    supplement = getattr(state.candidate, "supplement", None)
    return has_candidate_supplement({"candidate_supplement": supplement or {}})


def _unexplored_listed_count(state: "AgentState") -> int:
    from agent.validate_action import _unexplored_listed_keys

    return len(_unexplored_listed_keys(state, limit=10_000))


def _search_session_projection(state: "AgentState") -> dict[str, Any] | None:
    """Business projection of SearchSession. No URL / DOM / scroll."""
    session = getattr(state.search, "session", None)
    if session is None:
        return None
    if isinstance(session, dict):
        status = session.get("status")
        return {
            "session_id": session.get("session_id"),
            "platform": session.get("platform"),
            "keyword": session.get("keyword"),
            "city": session.get("city"),
            "status": status,
            "exposed_count": len(session.get("exposed_job_keys") or []),
            "can_continue": status in {"active", "resource_limited"},
        }
    status = getattr(session, "status", None)
    return {
        "session_id": getattr(session, "session_id", None),
        "platform": getattr(session, "platform", None),
        "keyword": getattr(session, "keyword", None),
        "city": getattr(session, "city", None),
        "status": status,
        "exposed_count": len(getattr(session, "exposed_job_keys", None) or []),
        "can_continue": status in {"active", "resource_limited"},
    }


def _unexplored_listed_job_keys(state: "AgentState") -> list[str]:
    from agent.validate_action import _unexplored_listed_keys

    return _unexplored_listed_keys(state, limit=8)


def _already_analyzed_job_keys(state: "AgentState") -> list[str]:
    from agent.validate_action import _job_already_analyzed

    keys: list[str] = []
    for key, record in (getattr(state, "jobs", None) or {}).items():
        if _job_already_analyzed(record):
            keys.append(str(key))
    return keys[:20]


def _already_matched_job_keys(state: "AgentState") -> list[str]:
    from agent.validate_action import _job_already_matched

    keys: list[str] = []
    for key, record in (getattr(state, "jobs", None) or {}).items():
        if _job_already_matched(record):
            keys.append(str(key))
    return keys[:20]


def _candidate_profile_projection(state: "AgentState", memory: dict) -> dict:
    view = project_profile_view(memory, candidate_id=state.candidate.candidate_id)
    stored = state.candidate.profile if isinstance(state.candidate.profile, dict) else None
    if stored and _projection_has_body(stored):
        view = dict(stored)
    view = stamp_llm_interpretation(view, source="analyze_candidate")
    view["kind"] = "interpretation_projection"
    view["layer"] = LAYER_INTERPRETATION
    # Honest provenance only — never invent derived_from from "all evidence".
    derived = view.get("derived_from")
    if not isinstance(derived, list):
        view["derived_from"] = []
    else:
        view["derived_from"] = [item for item in derived if item]
    if is_restored(stored or view):
        view["provenance"] = PROVENANCE_RESTORED
        view["evidence_status"] = "restored_unverified"
        view["epistemic"] = {
            **(view.get("epistemic") if isinstance(view.get("epistemic"), dict) else {}),
            "provenance": PROVENANCE_RESTORED,
            "restored": True,
            "verified": False,
            "scope": "historical_or_restored",
        }
    else:
        view.setdefault("evidence_status", view.get("evidence_status") or EVIDENCE_STATUS_UNVERIFIED)
    if not _projection_has_body(view):
        view["analysis_status"] = None
    return view


def _projection_has_body(profile: dict) -> bool:
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


def _used_keywords(state: "AgentState") -> list[str]:
    from agent.state import used_plan_keywords

    return used_plan_keywords(state)


def _waiting_user_job_key(state: "AgentState") -> str | None:
    """Job last named by ask_user (WAITING_USER Memory). Not a Program cursor."""
    current_id = (state.task_view or {}).get("current_job_context_id")
    if isinstance(current_id, str) and current_id.strip():
        return current_id.strip()
    return None


def _job_views(state: "AgentState") -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for record in state.jobs.values():
        items.append(_job_view(record))
    items.sort(key=lambda item: str(item.get("job_key") or ""))
    return items[:40]


def _job_view(record: "JobRecord") -> dict[str, Any]:
    """Project Job layers. No synthetic Evidence; no JD body (Binding loads that)."""
    source_blob = record.opened if isinstance(record.opened, dict) else None
    if source_blob is None:
        source_blob = record.listed if isinstance(record.listed, dict) else {}
    fact = _job_fact_projection(record, source_blob)
    listed_card_facts = _listed_card_facts(record, source_blob)
    job_profile = _job_profile_projection(record)
    match_result = _match_projection(record.match_result)
    interpret_result = _interpret_result_projection(record)
    restored_interp = any(
        is_restored(blob)
        for blob in (record.job_profile, record.match_result, record.interpret_result)
        if isinstance(blob, dict)
    )
    return {
        "job_key": record.job_key,
        "job_id": source_blob.get("job_id"),
        "job_url": source_blob.get("job_url"),
        "listed_order": record.listed_order,
        "stage": record.stage,
        "stage_semantics": {
            "layer": LAYER_FACT,
            "kind": "progress_marker",
            "note": (
                "stage reflects observed open/list progress in Live World; "
                "restored interpretations do not upgrade stage to analyzed/matched"
            ),
            "has_restored_interpretations": restored_interp,
        },
        "exclude_reason": record.exclude_reason,
        "constraint_flags": list(record.constraint_flags or []),
        "decisions": [
            _decision_projection(item) for item in list(record.decisions or []) if isinstance(item, dict)
        ],
        "listed_card_facts": listed_card_facts,
        "fact": fact,
        "has_interpret": record.interpret_result is not None,
        "has_job_profile": record.job_profile is not None,
        "has_match": record.match_result is not None,
        "job_profile": job_profile,
        "interpret_result": interpret_result,
        "match_result": match_result,
        "interpretations": [
            _interpretation_projection(item) for item in list(record.interpretations or [])
        ],
        "verifications": [
            _verification_projection(item) for item in list(record.verifications or [])
        ],
    }


def _listed_card_facts(record: "JobRecord", job: dict[str, Any]) -> dict[str, Any]:
    """External page/list observations. Not Interpretation. Not Evidence bodies."""
    return {
        "layer": LAYER_FACT,
        "kind": "listed_card_facts",
        "provenance": PROVENANCE_OBSERVED,
        "source": "opened" if record.opened else "listed",
        "job_title": job.get("job_title"),
        "company_name": job.get("company_name"),
        "city": job.get("city"),
        "salary": job.get("salary"),
        "experience": job.get("experience"),
        "education": job.get("education"),
        "company_industry": job.get("company_industry"),
        "company_size": job.get("company_size"),
    }


def _job_fact_projection(record: "JobRecord", job: dict[str, Any]) -> dict[str, Any]:
    """World Fact index for a Job. Not Evidence; not Interpretation."""
    jd = job.get("job_description")
    requirements = job.get("requirements")
    jd_text = jd if isinstance(jd, str) else ""
    req_text = requirements if isinstance(requirements, str) else ""
    opened = record.opened if isinstance(record.opened, dict) else {}
    return {
        "layer": LAYER_FACT,
        "kind": "job_fact_index",
        "provenance": PROVENANCE_OBSERVED,
        "source": "opened" if record.opened else "listed",
        "platform": job.get("platform"),
        "has_job_description": bool(jd_text.strip()),
        "has_requirements": bool(req_text.strip()),
        "has_raw_actions": isinstance(opened.get("raw_actions"), list),
        "jd_char_count": len(jd_text),
        "requirements_char_count": len(req_text),
    }


def _job_profile_projection(record: "JobRecord") -> dict[str, Any] | None:
    profile = record.job_profile
    if not isinstance(profile, dict):
        return None
    view = stamp_llm_interpretation(dict(profile), source="analyze_job")
    view["kind"] = "interpretation_projection"
    view["layer"] = LAYER_INTERPRETATION
    derived = view.get("derived_from")
    if not isinstance(derived, list):
        view["derived_from"] = []
    else:
        view["derived_from"] = [item for item in derived if item]
    if is_restored(profile):
        view["scope"] = "restored_historical"
    else:
        view["scope"] = "current_session"
    return view


def _match_projection(match_result: Any) -> dict[str, Any] | None:
    if not isinstance(match_result, dict):
        return None
    view = stamp_llm_interpretation(dict(match_result), source="match_job")
    view["kind"] = "interpretation"
    view["layer"] = LAYER_INTERPRETATION
    derived = view.get("derived_from")
    if not isinstance(derived, list):
        view["derived_from"] = []
    else:
        view["derived_from"] = [item for item in derived if item]
    if is_restored(match_result):
        view["scope"] = "restored_historical"
    else:
        view["scope"] = "current_session"
    return view


def _interpret_result_projection(record: "JobRecord") -> dict[str, Any] | None:
    result = record.interpret_result
    if not isinstance(result, dict):
        return None
    view = stamp_llm_interpretation(dict(result), source="interpret_job_actions")
    view.setdefault("kind", "interpretation")
    view.setdefault("layer", LAYER_INTERPRETATION)
    app_value = application_evidence_of(result)
    view["application_evidence"] = {
        "layer": LAYER_INTERPRETATION,
        "kind": "interpretation",
        "field": "application_evidence",
        "value": app_value,
        "source": "interpret_job_actions",
        "provenance": view.get("provenance") or PROVENANCE_LLM,
        "evidence_status": view.get("evidence_status") or EVIDENCE_STATUS_UNVERIFIED,
        "derived_from": list(view.get("derived_from") or []),
        "note": "LLM page-action semantics — not an external verified applied Fact",
    }
    if is_restored(result):
        view["scope"] = "restored_historical"
        view["application_evidence"]["provenance"] = PROVENANCE_RESTORED
        view["application_evidence"]["evidence_status"] = "restored_unverified"
    else:
        view["scope"] = "current_session"
    return view


def _decision_projection(item: dict[str, Any]) -> dict[str, Any]:
    view = dict(item)
    source = str(view.get("source") or "")
    if source == "world_restore" or is_restored(view):
        view["provenance"] = PROVENANCE_RESTORED
        view["evidence_status"] = "restored_unverified"
        view["layer"] = view.get("layer") or LAYER_INTERPRETATION
        view["note"] = "restored decision record — not a fresh Observation"
    else:
        view.setdefault("layer", "decision")
        view.setdefault("provenance", "session")
    return view


def _goal_projection(goal: Any) -> Any:
    if not isinstance(goal, dict):
        return goal
    view = stamp_llm_interpretation(dict(goal), source="understand_user_input")
    view.setdefault("kind", "interpretation")
    view.setdefault("layer", LAYER_INTERPRETATION)
    return view


def _supplement_projection(supplement: Any) -> Any:
    if not isinstance(supplement, dict):
        return supplement
    view = stamp_llm_interpretation(dict(supplement), source="understand_user_input")
    view.setdefault("kind", "interpretation")
    view.setdefault("layer", LAYER_INTERPRETATION)
    return view


def _claim_projection(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {"layer": LAYER_CLAIM, "value": item}
    view = dict(item)
    view.setdefault("layer", LAYER_CLAIM)
    if is_restored(view):
        view.setdefault("provenance", PROVENANCE_RESTORED)
    else:
        view.setdefault("evidence_status", view.get("evidence_status") or EVIDENCE_STATUS_UNVERIFIED)
    return view


def _interpretation_projection(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {"layer": LAYER_INTERPRETATION, "value": item}
    view = stamp_llm_interpretation(dict(item), source=str(item.get("kind") or "interpretation"))
    view.setdefault("layer", LAYER_INTERPRETATION)
    view.setdefault("kind", view.get("kind") or "interpretation")
    return view


def _verification_projection(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {"layer": LAYER_VERIFICATION, "value": item}
    view = dict(item)
    view.setdefault("layer", LAYER_VERIFICATION)
    return view


def _observation_and_hints(state: "AgentState") -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Split pure Observation from Program diagnostics."""
    raw = state.last_raw_observation
    hints: dict[str, Any] | None = None
    if isinstance(raw, dict):
        view = {
            key: value
            for key, value in raw.items()
            if key not in {"coach_zh", "recovery"}
        }
        view.setdefault("layer", LAYER_OBSERVATION)
        kind = view.get("kind")
        if kind == "tool_result" and view.get("search_executed") is True:
            view = _enrich_search_observation_world_facts(view, state)
        if kind == "action_rejected":
            recovery = _reject_recovery_card(state)
            hints = program_hints_shell(
                trigger="action_rejected",
                recovery=recovery,
                notes_zh=_coach_zh_from_recovery(view.get("error"), recovery),
            )
        elif kind == "repeated_no_progress":
            view = _enrich_no_progress_observation_world_facts(view, state)
            recovery = _no_progress_recovery_card(state)
            hints = program_hints_shell(
                trigger="repeated_no_progress",
                recovery=recovery,
                notes_zh=_coach_zh_from_no_progress(view, recovery),
            )
        elif kind == "tool_result" and view.get("search_executed") is True:
            note = _coach_zh_from_search_observation(view, state)
            if note:
                hints = program_hints_shell(
                    trigger="search_observation",
                    notes_zh=note,
                )
        elif kind == "user_turn_understood" and view.get("has_candidate_supplement"):
            hints = program_hints_shell(
                trigger="candidate_supplement_present",
                notes_zh=(
                    "本轮理解结果含候选人经历线索（has_candidate_supplement）。"
                    "这是事实提示，不是必须先 analyze_candidate 的流程指令。"
                ),
            )
        elif kind == "thrash_re_reason":
            hints = program_hints_shell(
                trigger="thrash_re_reason",
                recovery={
                    "do_not_retry_same_action": True,
                    "unexplored_listed_count": view.get("unexplored_listed_count") or 0,
                    "unexplored_listed_job_keys": list(view.get("unexplored_listed_job_keys") or []),
                    "available_intake_ids": list(view.get("available_intake_ids") or []),
                    "candidate_material_present": bool(view.get("candidate_material_present")),
                    "raw_candidate_intake_present": bool(view.get("raw_candidate_intake_present")),
                    "remaining_opens": view.get("remaining_opens"),
                    "focus": ["do_not_retry_identical_rejected_action", "reasoner_must_redecide"],
                },
                notes_zh=(
                    "连续动作被拒绝。程序不会替你选 Tool；"
                    "请根据 Observation 中的结构性事实自行决定下一步。"
                ),
            )
        view = stamp_execution_outcome(view)
        return view, hints
    pending = state.pending_goal
    return {
        "kind": "user_turn",
        "layer": LAYER_OBSERVATION,
        "understanding_status": getattr(state, "understanding_status", None) or "not_understood",
        "message_present": bool(isinstance(pending, str) and pending.strip()),
        "message_excerpt": _excerpt(pending),
    }, None


def _observation_projection(state: "AgentState") -> dict[str, Any]:
    """Compat: Observation only (no program_hints). Prefer _observation_and_hints."""
    observation, _hints = _observation_and_hints(state)
    return observation


def _enrich_search_observation_world_facts(
    obs: dict[str, Any],
    state: "AgentState",
) -> dict[str, Any]:
    """Attach World facts to Search Observation. Does not choose the next Tool."""
    from rules.quota import remaining_opens

    view = dict(obs)
    unexplored = _unexplored_listed_count(state)
    unexplored_keys = _unexplored_listed_job_keys(state)
    progress = view.get("progress")
    fetch_status = view.get("fetch_status")
    can_continue = view.get("can_continue")
    new_jobs = int(view.get("new_jobs") or view.get("newly_ingested") or 0)
    view["unexplored_listed_count"] = unexplored
    view["unexplored_listed_job_keys"] = unexplored_keys
    view["remaining_opens"] = remaining_opens(state)
    view["world_search_facts"] = {
        "layer": LAYER_FACT,
        "query_executed_this_turn": True,
        "new_jobs_ingested": new_jobs,
        "progress": bool(progress) if progress is not None else None,
        "fetch_status": fetch_status,
        "can_continue": can_continue,
        "result_set_exhausted": fetch_status == "exhausted",
        "no_new_jobs_this_turn": progress is False or new_jobs == 0,
        "unexplored_listed_count": unexplored,
        "unexplored_listed_job_keys": unexplored_keys,
        "remaining_opens": remaining_opens(state),
    }
    return view


def _enrich_no_progress_observation_world_facts(
    obs: dict[str, Any],
    state: "AgentState",
) -> dict[str, Any]:
    from rules.quota import remaining_opens

    view = dict(obs)
    if "unexplored_listed_count" not in view:
        view["unexplored_listed_count"] = _unexplored_listed_count(state)
    if "unexplored_listed_job_keys" not in view:
        view["unexplored_listed_job_keys"] = _unexplored_listed_job_keys(state)
    if "remaining_opens" not in view:
        view["remaining_opens"] = remaining_opens(state)
    return view


def _coach_zh_from_search_observation(obs: dict[str, Any], state: "AgentState") -> str | None:
    """Factual diagnostics only — never prescribe open / keyword / ask_user / finish."""
    progress = obs.get("progress")
    fetch_status = obs.get("fetch_status")
    unexplored = int(obs.get("unexplored_listed_count") or _unexplored_listed_count(state) or 0)
    opens_left = obs.get("remaining_opens")
    if opens_left is None:
        from rules.quota import remaining_opens

        opens_left = remaining_opens(state)
    new_jobs = int(obs.get("new_jobs") or 0)
    dups = int(obs.get("duplicate_jobs") or 0)
    can_continue = obs.get("can_continue")
    # Surface facts when this search did not advance inventory or result set is done.
    if progress is True and fetch_status != "exhausted" and unexplored == 0:
        return None
    bits = [
        "事实：",
        f"本次 search new_jobs={new_jobs}, duplicate_jobs={dups}, ",
        f"progress={progress}, fetch_status={fetch_status}, can_continue={can_continue}。",
        f" World unexplored_listed_count={unexplored}, remaining_opens={opens_left}。",
    ]
    if progress is False or new_jobs == 0:
        bits.append("本次该 query 未入库新职位。")
    if fetch_status == "exhausted":
        bits.append("当前 SearchSession 结果集已无更多未暴露岗位。")
    bits.append(
        "若根据已有 World/Observation，再执行同一 Action 已知不会产生新的业务进展，"
        "应重新评估其他合法 Action；具体选择仍由你决定。"
    )
    return "".join(bits)


def _no_progress_recovery_card(state: "AgentState") -> dict[str, Any]:
    """Structural hints after repeated_no_progress. Does not choose the next Tool."""
    from rules.quota import remaining_opens

    memory = refresh_memory_status(getattr(state.candidate, "memory", None) or {})
    return {
        "do_not_retry_same_action": True,
        "unexplored_listed_count": _unexplored_listed_count(state),
        "unexplored_listed_job_keys": _unexplored_listed_job_keys(state),
        "remaining_opens": remaining_opens(state),
        "candidate_material_present": _candidate_material_flag(state, memory),
        "raw_candidate_intake_present": _raw_candidate_intake_flag(state),
        "has_candidate_supplement": _has_candidate_supplement_flag(state),
        "focus": [
            "identical_no_progress_action_not_reexecuted",
            "reasoner_must_redecide_from_world",
        ],
    }


def _coach_zh_from_no_progress(obs: dict[str, Any], recovery: dict[str, Any]) -> str:
    attempted = obs.get("attempted") if isinstance(obs.get("attempted"), dict) else {}
    tool = attempted.get("tool_name") or "该动作"
    unexplored = recovery.get("unexplored_listed_count") or 0
    opens = recovery.get("remaining_opens")
    line = (
        f"事实：程序未再次执行 {tool}（与上次相同且 World 无进展）。"
        f" unexplored_listed_count={unexplored}, remaining_opens={opens}。"
        " 请根据当前 Observation 与 World 重新评估合法 Action；选择仍由你决定。"
    )
    return line


def _coach_zh_from_recovery(error: Any, recovery: dict[str, Any]) -> str | None:
    """Short Chinese diagnostics. Facts only — never prescribe a mandatory next Tool."""
    focus = list(recovery.get("focus") or [])
    err = str(error or "").lower()
    unexplored = recovery.get("unexplored_listed_count") or 0
    opens = recovery.get("remaining_opens")
    if "quota exceeded: open" in err or "open_quota_exhausted_do_not_open_job" in focus:
        return "打开名额已用尽，禁止再 open_job。请根据 World 事实自行选择其他 Action。"
    if "job already opened" in err:
        return "该职位已打开且 World 已有 JD，禁止再 open_job / inspect 重读。"
    if "insufficient_job_material" in err or "still listed" in err:
        return (
            f"当前职位缺少可执行的 JD 事实（insufficient_job_material）。"
            f" unexplored_listed_count={unexplored}, remaining_opens={opens}。"
        )
    if "already analyzed" in err or "already matched" in err:
        return "该职位已完成对应步骤，不要对同一职位重复同一 Tool。"
    if "inspect_job blocked" in err or "禁止导航重开" in err:
        return "World 已有 JD 文本，不要 inspect/重开同一职位。"
    if "insufficient_candidate_material" in err or "no candidate material" in err:
        return (
            "match 缺少可用候选人材料（insufficient_candidate_material）。"
            "Context 中的 raw_candidate_intake_present / candidate_material_present 供你参考；"
            "下一步由你自行选择。"
        )
    return None


def _reject_recovery_card(state: "AgentState") -> dict[str, Any]:
    """Structural facts after a rejected Action. Does not choose or name the next Tool."""
    from rules.quota import remaining_opens

    memory = refresh_memory_status(getattr(state.candidate, "memory", None) or {})
    material = _candidate_material_flag(state, memory)
    raw_intake = _raw_candidate_intake_flag(state)
    pending_supplement = _has_candidate_supplement_flag(state)
    unexplored = _unexplored_listed_count(state)
    opens_left = remaining_opens(state)
    focus: list[str] = ["do_not_retry_identical_rejected_action"]
    if opens_left <= 0:
        focus.append("open_quota_exhausted_do_not_open_job")
    return {
        "do_not_retry_same_action": True,
        "unexplored_listed_count": unexplored,
        "unexplored_listed_job_keys": _unexplored_listed_job_keys(state),
        "remaining_opens": opens_left,
        "candidate_material_present": material,
        "raw_candidate_intake_present": raw_intake,
        "has_candidate_supplement": pending_supplement,
        "focus": focus,
    }


def _candidate_context_projection(
    state: "AgentState",
    evidence_refs: list[dict[str, Any]],
) -> dict[str, Any] | None:
    ctx = state.candidate_context
    if not isinstance(ctx, dict):
        return None
    view = dict(ctx)
    mem = view.get("candidate_memory")
    if isinstance(mem, dict):
        view["candidate_memory"] = _memory_without_bodies(mem, evidence_refs)
    profile = view.get("persistent_profile")
    if isinstance(profile, dict):
        labeled = dict(profile)
        labeled.setdefault("kind", "interpretation_projection")
        labeled.setdefault("layer", LAYER_INTERPRETATION)
        view["persistent_profile"] = labeled
    return view


def _attachment_refs(state: "AgentState", intake: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Carrier upload refs for Reasoner. Not Evidence types."""
    refs: list[dict[str, Any]] = []
    for item in intake:
        if item.get("carrier") == "text" and not item.get("filename"):
            continue
        refs.append(
            attachment_ref(
                {
                    "filename": item.get("filename"),
                    "carrier": item.get("carrier"),
                    "text": "x" * int(item.get("char_count") or 0),
                },
                evidence_id=str(item.get("id") or "") or None,
            )
        )
    if refs:
        return refs
    return [
        attachment_ref(item)
        for item in list(state.attachments or [])
        if isinstance(item, dict)
    ]


def _memory_without_bodies(memory: dict, evidence_refs: list[dict[str, Any]]) -> dict[str, Any]:
    view = dict(memory)
    view["evidence"] = evidence_refs
    return view


def _excerpt(value: Any, *, limit: int = 200) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    return text[:limit]
