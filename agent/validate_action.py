"""Program contract / safety / quota checks. Does not choose the next Tool."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tools.registry import FORBIDDEN_LOOP_TOOLS

if TYPE_CHECKING:
    from agent.state import AgentState
    from tools.registry import ToolRegistry

SEARCH_TOOLS = {
    "search_jobs",
    "mock_search_jobs",
    "search_boss_jobs",
    "search_liepin_jobs",
    "search_51job_jobs",
}
OPEN_TOOLS = {
    "open_job",
    "mock_open_job",
    "open_boss_job",
    "open_liepin_job",
    "open_51job_job",
}

REQUIRES_USER_AUTH = {"execute_action", "execute_job_action"}
ALLOWED_ACTION_TYPES = {"tool", "ask_user", "finish"}
ASK_USER_INTENTS = {"clarification", "surface"}


def validate_reasoner_action(
    state: "AgentState",
    decision: dict,
    registry: "ToolRegistry",
) -> dict:
    """Return {ok, error, action}. Never substitutes a different Tool."""
    action_type = decision.get("action_type")
    if action_type == "ask_user":
        return _validate_ask_user(state, decision)
    if action_type == "finish":
        return _validate_finish(state, decision)
    if action_type != "tool":
        return {"ok": False, "error": "action_type must be tool, ask_user, or finish", "action": decision}

    name = decision.get("tool_name")
    if not isinstance(name, str) or not name.strip():
        return {"ok": False, "error": "tool_name is required", "action": decision}
    name = name.strip()
    if name in FORBIDDEN_LOOP_TOOLS:
        return {"ok": False, "error": f"tool is forbidden: {name}", "action": decision}
    if name == "reason_next_action":
        return {"ok": False, "error": "reason_next_action cannot invoke itself", "action": decision}
    if not registry.has(name):
        return {"ok": False, "error": f"unknown tool: {name}", "action": decision}

    arguments = decision.get("arguments") if isinstance(decision.get("arguments"), dict) else {}
    spec = _spec(registry, name)
    missing = _missing_required(spec, arguments)
    if missing:
        return {"ok": False, "error": f"missing required argument: {missing}", "action": decision}

    from rules.quota import can_open, remaining_llm_calls, remaining_opens

    # Search execution is not gated by listed_count / list capacity. Capacity only
    # limits ingest inside Reduce; zero-increment and identical repeats are
    # Observation + loop-safety concerns for the Reasoner.
    if name in OPEN_TOOLS and not can_open(state):
        return {"ok": False, "error": "quota exceeded: open", "action": decision}
    if name in OPEN_TOOLS:
        record = _job_record_for_decision(state, decision, arguments)
        if record is not None and (
            getattr(record, "stage", None) in {"opened", "analyzed", "matched"}
            or isinstance(getattr(record, "opened", None), dict)
        ):
            # Structural exception: empty JD fact may re-open once to fetch page text.
            # Not a semantic “look again”; do not navigate when World already has JD text.
            if not _opened_jd_missing(record):
                return {
                    "ok": False,
                    "error": (
                        "job already opened with JD text in World; "
                        "do not open_job or inspect_job to re-browse the same JD"
                    ),
                    "action": decision,
                }
    if remaining_llm_calls(state) <= 0 and name in {
        "understand_user_input",
        "analyze_candidate",
        "analyze_job",
        "interpret_job_actions",
        "match_job",
        "plan_search",
    }:
        return {"ok": False, "error": "quota exceeded: llm", "action": decision}

    if name in SEARCH_TOOLS:
        raw_goal = _pending_goal_text(state)
        keyword = arguments.get("keyword")
        if (
            getattr(state, "understanding_status", None) != "ok"
            and raw_goal
            and isinstance(keyword, str)
            and keyword.strip() == raw_goal
        ):
            return {
                "ok": False,
                "error": "search keyword cannot be the ununderstood pending_goal text",
                "action": decision,
            }
        # Same-query / unexplored-listed preference is Reasoner + Observation
        # (progress / new_jobs) + generic loop safety — not a Program forbid rule.

    # match_job material sufficiency is Binding's job (Observation), not a
    # "must call analyze_candidate first" workflow gate.

    explore_gate = _explore_first_gate(state, name, decision, arguments)
    if explore_gate is not None:
        return explore_gate

    if name in REQUIRES_USER_AUTH and arguments.get("user_authorization") != "apply_job":
        return {"ok": False, "error": "execute_action requires user_authorization=apply_job", "action": decision}

    return {"ok": True, "error": None, "action": decision}


def _spec(registry: Any, name: str) -> dict:
    for item in registry.specs():
        if item.get("name") == name:
            return item
    return {}


def _missing_required(spec: dict, arguments: dict) -> str | None:
    required = ((spec.get("parameters") or {}).get("required")) or []
    for key in required:
        if key not in arguments or arguments[key] in (None, ""):
            return str(key)
    return None


def _validate_ask_user(state: "AgentState", decision: dict) -> dict:
    """Structural ask_user contract only. Does not decide whether to surface from match/score."""
    arguments = decision.get("arguments") if isinstance(decision.get("arguments"), dict) else {}
    intent = decision.get("intent") or arguments.get("intent")
    if intent not in ASK_USER_INTENTS:
        return {
            "ok": False,
            "error": "ask_user intent must be clarification or surface",
            "action": decision,
        }
    # Unread upload / pending supplement are Context facts for Reasoner.
    # Program must not force analyze_candidate before clarification.
    if intent == "surface":
        job_key = decision.get("job_key") or arguments.get("job_key")
        if not isinstance(job_key, str) or not job_key.strip():
            return {
                "ok": False,
                "error": "ask_user surface requires job_key",
                "action": decision,
            }
        job_key = job_key.strip()
        jobs = getattr(state, "jobs", None) or {}
        if job_key not in jobs:
            return {
                "ok": False,
                "error": "ask_user surface job_key not in jobs",
                "action": decision,
            }
    return {"ok": True, "error": None, "action": decision}


PLACEHOLDER_FINISH_REASONS = frozenset(
    {
        "",
        "...",
        "…",
        "reason",
        "n/a",
        "na",
        "none",
        "null",
        "todo",
        "placeholder",
    }
)


def _validate_finish(state: "AgentState", decision: dict) -> dict:
    """Contract only: reject placeholder finish. Does not require search or pick the next Tool."""
    arguments = decision.get("arguments") if isinstance(decision.get("arguments"), dict) else {}
    reason = decision.get("reason")
    if reason is None:
        reason = arguments.get("reason")
    reason_text = str(reason).strip() if reason is not None else ""
    if reason_text.casefold() in PLACEHOLDER_FINISH_REASONS or set(reason_text) <= {".", "。", "…", "·"}:
        return {
            "ok": False,
            "error": "finish reason must be a concrete sentence, not a placeholder like '...'",
            "action": decision,
        }
    # Whether to search before finishing is Reasoner's business decision — not a Program duty.
    return {"ok": True, "error": None, "action": decision}


def _pending_goal_text(state: Any) -> str:
    pending = getattr(state, "pending_goal", None)
    if not isinstance(pending, str):
        return ""
    return pending.strip()


def _unexplored_listed_keys(state: "AgentState", *, limit: int = 8) -> list[str]:
    jobs = getattr(state, "jobs", None) or {}
    ranked = sorted(
        jobs.values(),
        key=lambda record: (
            int(getattr(record, "listed_order", 0) or 0),
            str(getattr(record, "job_key", "") or ""),
        ),
    )
    keys: list[str] = []
    for record in ranked:
        if getattr(record, "stage", None) != "listed":
            continue
        if getattr(record, "exclude_reason", None):
            continue
        key = str(getattr(record, "job_key", "") or "").strip()
        if not key:
            continue
        keys.append(key)
        if len(keys) >= limit:
            break
    return keys


def _job_already_analyzed(record: Any) -> bool:
    from agent.epistemic import is_restored

    profile = getattr(record, "job_profile", None)
    # Restored historical profiles are not "already analyzed this Live session".
    if isinstance(profile, dict) and is_restored(profile):
        return False
    if getattr(record, "stage", None) in {"analyzed", "matched"}:
        return True
    return isinstance(profile, dict) and profile.get("analysis_status") == "ok"


def _job_already_matched(record: Any) -> bool:
    from agent.epistemic import is_restored

    match = getattr(record, "match_result", None)
    if isinstance(match, dict) and is_restored(match):
        return False
    if getattr(record, "stage", None) == "matched":
        return True
    return isinstance(match, dict) and bool(match)


def _opened_jd_missing(record: Any) -> bool:
    """True when open ran but World has no JD text fact yet (retry open is structural)."""
    opened = getattr(record, "opened", None)
    if not isinstance(opened, dict):
        return True
    jd = opened.get("job_description")
    return not (isinstance(jd, str) and jd.strip())


def _explore_first_gate(
    state: "AgentState",
    name: str,
    decision: dict,
    arguments: dict,
) -> dict | None:
    """Material / idempotency gates only. Does not prescribe the next Tool.

    - analyze/match on listed without JD facts → insufficient job material
    - successful analyze/match → do not redo the same job (anti-thrash)
    - Never: "unexplored listed ⇒ must open_job next"
    """
    if name == "inspect_job":
        record = _job_record_for_decision(state, decision, arguments)
        if record is not None and not _opened_jd_missing(record):
            return {
                "ok": False,
                "error": (
                    "inspect_job blocked: World already has JD text for this job; "
                    "do not re-browse"
                ),
                "action": decision,
            }
        return None

    if name not in {"analyze_job", "match_job"}:
        return None
    record = _job_record_for_decision(state, decision, arguments)
    if record is None:
        return None
    stage = getattr(record, "stage", None)
    if stage == "listed":
        # True precondition: listed card has no opened JD fact yet.
        return {
            "ok": False,
            "error": (
                f"insufficient_job_material: job is still listed with no opened JD fact for {name}"
            ),
            "action": decision,
        }
    if name == "analyze_job" and _job_already_analyzed(record):
        return {
            "ok": False,
            "error": "job already analyzed; do not re-analyze the same job",
            "action": decision,
        }
    if name == "match_job" and _job_already_matched(record):
        return {
            "ok": False,
            "error": "job already matched; do not re-match the same job",
            "action": decision,
        }
    return None


def _same_search_as_active_plan(state: "AgentState", arguments: dict) -> bool:
    """True when this search repeats the active plan (or any prior pool if plan missing)."""
    from agent.state import active_plan

    keyword = str(arguments.get("keyword") or "").strip().casefold()
    city = str(arguments.get("city") or "").strip().casefold()
    plan = active_plan(state)
    if plan is None:
        return bool(getattr(state, "jobs", None))
    plan_kw = str(plan.keyword or "").strip().casefold()
    plan_city = str(plan.city or "").strip().casefold()
    if keyword != plan_kw:
        return False
    if not city or not plan_city:
        return True
    return city == plan_city


def _job_record_for_decision(state: "AgentState", decision: dict, arguments: dict):
    jobs = getattr(state, "jobs", None) or {}
    if not jobs:
        return None
    for key in (
        decision.get("job_key"),
        arguments.get("job_key"),
        arguments.get("job_context_id"),
    ):
        if isinstance(key, str) and key.strip() and key.strip() in jobs:
            return jobs[key.strip()]
    job_id = arguments.get("job_id")
    job_url = arguments.get("job_url")
    if isinstance(job_id, str) and job_id.strip():
        needle = job_id.strip()
        for record in jobs.values():
            listed = record.listed if isinstance(record.listed, dict) else {}
            opened = record.opened if isinstance(record.opened, dict) else {}
            if needle in {listed.get("job_id"), opened.get("job_id")}:
                return record
    if isinstance(job_url, str) and job_url.strip():
        needle = job_url.strip()
        for record in jobs.values():
            listed = record.listed if isinstance(record.listed, dict) else {}
            opened = record.opened if isinstance(record.opened, dict) else {}
            if needle in {listed.get("job_url"), opened.get("job_url")}:
                return record
    return None


def _world_has_candidate_material(state: "AgentState") -> bool:
    from agent.bind_arguments import _candidate_material_present

    candidate_context = state.candidate_context if isinstance(state.candidate_context, dict) else None
    candidate_profile = state.candidate.profile if isinstance(state.candidate.profile, dict) else None
    return _candidate_material_present(candidate_context, candidate_profile, state)


def _pending_candidate_supplement(state: "AgentState") -> bool:
    """True when Understanding already extracted candidate materials (LLM structured output)."""
    from understanding.schema import has_candidate_supplement

    understanding = getattr(state, "understanding", None)
    if has_candidate_supplement(understanding if isinstance(understanding, dict) else {}):
        return True
    candidate = getattr(state, "candidate", None)
    supplement = getattr(candidate, "supplement", None) if candidate is not None else None
    return has_candidate_supplement({"candidate_supplement": supplement or {}})
