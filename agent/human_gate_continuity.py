"""Human Gate continuity: pause Observation + World snapshot resume.

NEEDS_HUMAN ends the current HTTP run, not the business Task.
Resume path: User feedback → Observation → restore World → Reasoner → next Action.
Never hardcodes search_jobs / browser login success.
"""

from __future__ import annotations

import copy
from typing import Any

from agent.state import AgentState, JobRecord, SearchPlan, SearchState, utc_now


RESOLUTION_USER_CONFIRMED = "user_confirmed"
OBS_HUMAN_GATE = "human_gate"
OBS_HUMAN_GATE_RESOLVED = "human_gate_resolved"


def _search_session_snapshot(state: AgentState) -> dict[str, Any] | None:
    from platforms.search_contract import session_to_dict

    session = getattr(state.search, "session", None)
    return session_to_dict(session)


def human_gate_observation(gate: dict) -> dict[str, Any]:
    """Auditable Observation when the Loop pauses for a human."""
    payload = dict(gate) if isinstance(gate, dict) else {}
    return {
        "kind": OBS_HUMAN_GATE,
        "layer": "observation",
        "awaiting_human": True,
        "reason": payload.get("reason"),
        "message": payload.get("message"),
        "source": payload.get("source"),
        "gate": {
            "reason": payload.get("reason"),
            "message": payload.get("message"),
            "source": payload.get("source"),
            "at": payload.get("at"),
            **{
                key: value
                for key, value in payload.items()
                if key not in {"reason", "message", "source", "at"}
            },
        },
        "at": payload.get("at") or utc_now(),
    }


def user_confirmed_observation(
    *,
    user_message: Any,
    prior_gate: dict | None,
    world_restore: dict | None = None,
) -> dict[str, Any]:
    """User says the gate action is done. Not a verified browser login assertion."""
    gate = dict(prior_gate) if isinstance(prior_gate, dict) else {}
    observation: dict[str, Any] = {
        "kind": OBS_HUMAN_GATE_RESOLVED,
        "layer": "observation",
        "resolution": RESOLUTION_USER_CONFIRMED,
        "meaning": "user_states_human_action_done",
        "browser_login_verified": False,
        "message": user_message if isinstance(user_message, str) else None,
        "message_present": bool(isinstance(user_message, str) and user_message.strip()),
        "prior_gate": {
            "reason": gate.get("reason"),
            "source": gate.get("source"),
            "message": gate.get("message"),
            "fault": gate.get("fault"),
        },
        "at": utc_now(),
    }
    if isinstance(world_restore, dict):
        observation["world_restore"] = dict(world_restore)
    return observation


def snapshot_world_for_human_gate(state: AgentState) -> dict[str, Any]:
    """Capture Live World needed to resume the same Task after a Human Gate."""
    return {
        "goal": copy.deepcopy(state.goal) if isinstance(state.goal, dict) else None,
        "understanding": copy.deepcopy(state.understanding)
        if isinstance(state.understanding, dict)
        else None,
        "understanding_status": getattr(state, "understanding_status", None) or "not_understood",
        "preferences": list(state.preferences or []),
        "job_search_task_id": state.job_search_task_id,
        "pending_job_key": state.pending_job_key,
        "task_control": state.task_control,
        "follow_up_of": state.follow_up_of,
        "search": {
            "plans": [
                {
                    "plan_id": plan.plan_id,
                    "keyword": plan.keyword,
                    "city": plan.city,
                    "platform": plan.platform,
                    "exhausted": bool(plan.exhausted),
                }
                for plan in state.search.plans
            ],
            "active_plan_id": state.search.active_plan_id,
            "exhausted": list(state.search.exhausted or []),
            "stats": dict(state.search.stats or {}),
            "stop_reason": state.search.stop_reason,
            "session": _search_session_snapshot(state),
        },
        "jobs": {key: _job_to_dict(record) for key, record in state.jobs.items()},
        "decisions": copy.deepcopy(list(state.decisions or [])[-32:]),
        "candidate": {
            "profile": copy.deepcopy(state.candidate.profile)
            if isinstance(state.candidate.profile, dict)
            else None,
            "profile_status": state.candidate.profile_status,
            "candidate_id": state.candidate.candidate_id,
            "profile_version": state.candidate.profile_version,
            "supplement": copy.deepcopy(state.candidate.supplement)
            if isinstance(state.candidate.supplement, dict)
            else None,
            "memory": copy.deepcopy(state.candidate.memory)
            if isinstance(state.candidate.memory, dict)
            else None,
        },
        "human_gate": copy.deepcopy(state.human_gate) if isinstance(state.human_gate, dict) else None,
        "last_raw_observation": copy.deepcopy(state.last_raw_observation)
        if isinstance(state.last_raw_observation, dict)
        else None,
        "data_source": state.constraints.data_source,
    }


def restore_world_from_human_gate_snapshot(state: AgentState, snapshot: dict[str, Any]) -> dict[str, Any]:
    """Install paused World into a fresh AgentState. Does not choose the next Action."""
    if not isinstance(snapshot, dict):
        return {"reason": "invalid_snapshot", "restored_job_keys": []}

    if isinstance(snapshot.get("goal"), dict):
        state.goal = copy.deepcopy(snapshot["goal"])
    if isinstance(snapshot.get("understanding"), dict):
        state.understanding = copy.deepcopy(snapshot["understanding"])
    status = snapshot.get("understanding_status")
    if isinstance(status, str) and status.strip():
        state.understanding_status = status
    prefs = snapshot.get("preferences")
    if isinstance(prefs, list):
        state.preferences = [str(item) for item in prefs if item is not None]
    if snapshot.get("job_search_task_id"):
        state.job_search_task_id = str(snapshot["job_search_task_id"])
    if snapshot.get("pending_job_key"):
        state.pending_job_key = str(snapshot["pending_job_key"])
    if snapshot.get("task_control"):
        state.task_control = str(snapshot["task_control"])
    if snapshot.get("follow_up_of"):
        state.follow_up_of = str(snapshot["follow_up_of"])

    search_raw = snapshot.get("search") if isinstance(snapshot.get("search"), dict) else {}
    plans: list[SearchPlan] = []
    for item in search_raw.get("plans") or []:
        if not isinstance(item, dict) or not item.get("plan_id"):
            continue
        plans.append(
            SearchPlan(
                plan_id=str(item["plan_id"]),
                keyword=item.get("keyword"),
                city=item.get("city"),
                platform=str(item.get("platform") or state.constraints.data_source or "mock"),
                exhausted=bool(item.get("exhausted")),
            )
        )
    state.search = SearchState(
        plans=plans,
        active_plan_id=search_raw.get("active_plan_id"),
        exhausted=[str(item) for item in (search_raw.get("exhausted") or [])],
        stats=dict(search_raw.get("stats") or {}),
        stop_reason=search_raw.get("stop_reason"),
    )
    from platforms.search_contract import session_from_dict

    state.search.session = session_from_dict(search_raw.get("session"))

    jobs_raw = snapshot.get("jobs") if isinstance(snapshot.get("jobs"), dict) else {}
    restored_keys: list[str] = []
    for key, raw in jobs_raw.items():
        record = _job_from_dict(raw)
        if record is None:
            continue
        state.jobs[record.job_key] = record
        restored_keys.append(record.job_key)

    decisions = snapshot.get("decisions")
    if isinstance(decisions, list):
        state.decisions = [item for item in copy.deepcopy(decisions) if isinstance(item, dict)]

    cand = snapshot.get("candidate") if isinstance(snapshot.get("candidate"), dict) else {}
    if isinstance(cand.get("profile"), dict):
        from agent.epistemic import stamp_restored

        state.candidate.profile = stamp_restored(copy.deepcopy(cand["profile"]), source="human_gate_snapshot")
    if cand.get("profile_status"):
        state.candidate.profile_status = str(cand["profile_status"])
    if cand.get("candidate_id"):
        state.candidate.candidate_id = str(cand["candidate_id"])
    if cand.get("profile_version") is not None:
        state.candidate.profile_version = cand["profile_version"]
    if isinstance(cand.get("supplement"), dict):
        state.candidate.supplement = copy.deepcopy(cand["supplement"])
    if isinstance(cand.get("memory"), dict):
        state.candidate.memory = copy.deepcopy(cand["memory"])

    # Fresh turn is not paused; prior gate lives on Observation / session_context.
    state.human_gate = None

    return {
        "reason": "human_gate_continuity",
        "restored_job_keys": restored_keys,
        "bound_task_id": None,
        "continuity_task_id": state.job_search_task_id,
        "prior_gate_reason": (snapshot.get("human_gate") or {}).get("reason")
        if isinstance(snapshot.get("human_gate"), dict)
        else None,
        "prior_gate_source": (snapshot.get("human_gate") or {}).get("source")
        if isinstance(snapshot.get("human_gate"), dict)
        else None,
    }


def pending_human_gate_from_session(session_context: dict | None) -> dict | None:
    if not isinstance(session_context, dict):
        return None
    gate = session_context.get("pending_human_gate")
    if isinstance(gate, dict) and gate.get("reason"):
        return dict(gate)
    return None


def human_gate_snapshot_from_session(session_context: dict | None) -> dict | None:
    if not isinstance(session_context, dict):
        return None
    snapshot = session_context.get("human_gate_world_snapshot")
    return dict(snapshot) if isinstance(snapshot, dict) else None


def build_initial_turn_observation(
    state: AgentState,
    *,
    world_restore: dict | None = None,
) -> dict[str, Any]:
    """First Observation of a turn. Gate resume uses user_confirmed, not a new cold goal."""
    pending = pending_human_gate_from_session(state.session_context)
    if pending is not None:
        return user_confirmed_observation(
            user_message=state.pending_goal,
            prior_gate=pending,
            world_restore=world_restore,
        )
    observation: dict[str, Any] = {
        "kind": "user_turn",
        "understanding_status": getattr(state, "understanding_status", None) or "not_understood",
        "message": state.pending_goal,
        "has_attachments": bool(state.attachments),
    }
    if isinstance(world_restore, dict) and world_restore.get("reason") not in {None, "no_restore"}:
        observation["world_restore"] = {
            "reason": world_restore.get("reason"),
            "restored_job_keys": list(world_restore.get("restored_job_keys") or []),
            "continuity_task_id": world_restore.get("continuity_task_id"),
            "bound_task_id": world_restore.get("bound_task_id"),
        }
    return observation


def _job_to_dict(record: JobRecord) -> dict[str, Any]:
    return {
        "job_key": record.job_key,
        "stage": record.stage,
        "listed_order": record.listed_order,
        "listed": copy.deepcopy(record.listed) if isinstance(record.listed, dict) else {},
        "opened": copy.deepcopy(record.opened) if isinstance(record.opened, dict) else None,
        "interpret_result": copy.deepcopy(record.interpret_result)
        if isinstance(record.interpret_result, dict)
        else None,
        "job_profile": copy.deepcopy(record.job_profile) if isinstance(record.job_profile, dict) else None,
        "match_result": copy.deepcopy(record.match_result) if isinstance(record.match_result, dict) else None,
        "exclude_reason": record.exclude_reason,
        "constraint_flags": list(record.constraint_flags or []),
        "rank_score": list(record.rank_score) if isinstance(record.rank_score, tuple) else record.rank_score,
        "interpretations": copy.deepcopy(list(record.interpretations or [])),
        "verifications": copy.deepcopy(list(record.verifications or [])),
        "decisions": copy.deepcopy(list(record.decisions or [])),
    }


def _job_from_dict(raw: Any) -> JobRecord | None:
    if not isinstance(raw, dict) or not raw.get("job_key"):
        return None
    from agent.epistemic import observed_stage_from_record_fields, stamp_restored

    rank = raw.get("rank_score")
    if isinstance(rank, list):
        rank = tuple(rank)
    listed = copy.deepcopy(raw.get("listed")) if isinstance(raw.get("listed"), dict) else {}
    opened = copy.deepcopy(raw.get("opened")) if isinstance(raw.get("opened"), dict) else None
    interpret = (
        stamp_restored(copy.deepcopy(raw.get("interpret_result")), source="human_gate_snapshot")
        if isinstance(raw.get("interpret_result"), dict)
        else None
    )
    profile = (
        stamp_restored(copy.deepcopy(raw.get("job_profile")), source="human_gate_snapshot")
        if isinstance(raw.get("job_profile"), dict)
        else None
    )
    match = (
        stamp_restored(copy.deepcopy(raw.get("match_result")), source="human_gate_snapshot")
        if isinstance(raw.get("match_result"), dict)
        else None
    )
    stage = observed_stage_from_record_fields(
        opened=opened,
        listed=listed,
        persisted_stage=str(raw.get("stage") or ""),
    )
    decisions = []
    for item in copy.deepcopy(raw.get("decisions") or []):
        if not isinstance(item, dict):
            continue
        stamped = dict(item)
        stamped.setdefault("source", "world_restore")
        stamped["provenance"] = "restored"
        stamped["verified"] = False
        decisions.append(stamped)
    return JobRecord(
        job_key=str(raw["job_key"]),
        stage=stage,
        listed_order=int(raw.get("listed_order") or 0),
        listed=listed,
        opened=opened,
        interpret_result=interpret,
        job_profile=profile,
        match_result=match,
        exclude_reason=raw.get("exclude_reason"),
        constraint_flags=[str(item) for item in (raw.get("constraint_flags") or [])],
        rank_score=rank if isinstance(rank, tuple) else None,
        interpretations=[
            stamp_restored(item, source="human_gate_snapshot")
            for item in copy.deepcopy(raw.get("interpretations") or [])
            if isinstance(item, dict)
        ],
        verifications=[item for item in copy.deepcopy(raw.get("verifications") or []) if isinstance(item, dict)],
        decisions=decisions,
    )
