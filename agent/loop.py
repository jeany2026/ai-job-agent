"""Observe → Decide → Act → Reduce. Not a fixed script. Not a bundled browse tool."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.bind_arguments import bind_tool_arguments
from agent.decide import Action, decide, is_llm_tool, tool_action
from agent.human_gate import apply_human_gate, classify_exception, result_human_gate
from agent.report import build_report
from agent.state import (
    DECISION_SKIPPED,
    DECISION_SURFACED,
    OPEN_TOOL_BY_SOURCE,
    SEARCH_TOOL_BY_SOURCE,
    TERMINAL_STATUSES,
    AgentState,
    JobRecord,
    SearchPlan,
    job_has_decision,
    job_record_key,
    active_plan,
    mark_active_plan_exhausted,
    note_job_decision,
    record_decision,
    record_error,
    used_plan_keywords,
)
from job.common_schema import merge_opened, normalize_common_job
from rules.dedupe import is_duplicate, remember_job, seen_keys
from rules.filters import list_constraint_flags, list_exclude_reason
from agent.loop_safety import (
    REPEATED_NO_PROGRESS_LIMIT,
    action_fingerprint,
    build_repeated_no_progress_observation,
    bump_no_progress_block,
    note_tool_world_outcome,
    should_block_repeated_no_progress,
    world_progress_fingerprint,
)
from rules.quota import note_llm_call, note_open, note_search, remaining_list_capacity
from rules.rank import rank_records
from rules.stop import STOP_NO_NEXT_PLAN, STOP_PLAN_SEARCH_FAILED
from tools.registry import FORBIDDEN_LOOP_TOOLS, ToolRegistry

PRE_STATUS = {
    "parse_user_goal": "PARSING_GOAL",
    "understand_user_input": "PARSING_GOAL",
    "analyze_candidate": "GATHERING_CANDIDATE",
    "plan_search": "PLANNING",
    "interpret_job_actions": "ENRICHING_JOBS",
    "analyze_job": "ANALYZING",
    "match_job": "MATCHING",
    "record_insufficient_match": "MATCHING",
    "decide_next_action": "DECIDING",
    "execute_job_action": "EXECUTING",
    "execute_action": "EXECUTING",
    "inspect_job": "ENRICHING_JOBS",
    "inspect_application_state": "ENRICHING_JOBS",
    "advance_after_filter": "FILTERING",
    "rank_jobs": "RANKING",
    "build_report": "REPORTING",
}
PRE_STATUS.update({name: "SEARCHING" for name in SEARCH_TOOL_BY_SOURCE.values()})
PRE_STATUS.update({name: "ENRICHING_JOBS" for name in OPEN_TOOL_BY_SOURCE.values()})
SEARCH_TOOLS = frozenset(
    {
        "search_jobs",
        "mock_search_jobs",
        "search_boss_jobs",
        "search_liepin_jobs",
        "search_51job_jobs",
    }
)
OPEN_TOOLS = frozenset(
    {
        "open_job",
        "mock_open_job",
        "open_boss_job",
        "open_liepin_job",
        "open_51job_job",
    }
)
INSPECT_JOB_TOOLS = frozenset({"inspect_job"})
EXECUTE_TOOLS = frozenset({"execute_action", "execute_job_action"})

MAX_STEPS = 120
MISSING_CANDIDATE_MATCH_REJECT_LIMIT = 2
REPEATED_ACTION_REJECT_LIMIT = 8
LLM_QUOTA_REJECT_LIMIT = 3
# After this many consecutive rejects, emit thrash_re_reason Observation (no Action).
THRASH_RECOVERY_AFTER = 2
MISSING_CANDIDATE_ERROR = (
    "insufficient_candidate_material for match_job; "
    "World has no usable candidate material"
)


@dataclass
class LoopContext:
    task_store: Any = None
    conversation_id: str | None = None


def observe(state: AgentState) -> dict:
    stages: dict[str, int] = {}
    for record in state.jobs.values():
        stages[record.stage] = stages.get(record.stage, 0) + 1
    return {
        "status": state.session.status,
        "stages": stages,
        "opened": state.search.stats.get("opened", 0),
        "llm_calls": state.search.stats.get("llm_calls", 0),
    }


def run_loop(
    state: AgentState,
    registry: ToolRegistry,
    *,
    llm_provider=None,
    max_steps: int = MAX_STEPS,
    on_status=None,
    task_store=None,
    conversation_id: str | None = None,
    world_restore: dict | None = None,
) -> AgentState:
    def notify() -> None:
        if on_status is not None:
            on_status(state)

    ctx = LoopContext(
        task_store=task_store,
        conversation_id=conversation_id or state.conversation_id,
    )
    if ctx.conversation_id and not state.conversation_id:
        state.conversation_id = ctx.conversation_id

    if state.session.status == "INIT":
        state.status = "RUNNING"
        if state.last_raw_observation is None:
            from agent.human_gate_continuity import build_initial_turn_observation

            state.last_raw_observation = build_initial_turn_observation(
                state,
                world_restore=world_restore,
            )
        notify()

    steps = 0
    while state.session.status not in TERMINAL_STATUSES:
        steps += 1
        if steps > max_steps:
            record_error(state, f"loop exceeded max_steps={max_steps}")
            state.status = "FAILED"
            notify()
            break

        observation = observe(state)
        action = decide(state, registry=registry, llm_provider=llm_provider)
        if action.tool_name in FORBIDDEN_LOOP_TOOLS:
            record_error(state, f"Loop attempted forbidden tool {action.tool_name}")
            state.status = "FAILED"
            notify()
            break

        record_decision(
            state,
            observation=observation,
            action_name=action.tool_name or action.action_type,
        )

        if action.action_type == "rejected":
            if _note_rejection_pressure(state, action):
                notify()
                break
            # Diagnostics only — never inject analyze_candidate / open_job / etc.
            _note_thrash_for_reasoner(state)
            if state.session.status not in TERMINAL_STATUSES:
                state.status = "RUNNING"
                state.progress = "DECIDING"
            notify()
            continue
        if action.action_type == "ask_user":
            _reset_reject_pressure(state)
            state = _apply_ask_user(state, action, ctx)
            notify()
            break
        if action.action_type == "finish":
            _reset_reject_pressure(state)
            state = _apply_finish(state, action, ctx)
            notify()
            break
        if action.action_type == "fail":
            state = reduce(state, action, {"ok": False}, ctx)
            notify()
            break
        if action.action_type != "tool" or not action.tool_name:
            record_error(state, f"unhandled action_type {action.action_type}", kind="reasoner")
            state.status = "FAILED"
            notify()
            break

        if should_block_repeated_no_progress(state, action):
            state.last_raw_observation = build_repeated_no_progress_observation(state, action)
            blocks = bump_no_progress_block(state)
            if blocks >= REPEATED_NO_PROGRESS_LIMIT:
                record_error(
                    state,
                    "identical action repeated without World progress",
                    kind="repeated_no_progress",
                    extra={
                        "tool_name": action.tool_name,
                        "blocks": blocks,
                        "fingerprint": action_fingerprint(action),
                    },
                )
                state.output = {
                    **(state.output if isinstance(state.output, dict) else {}),
                    "error_code": "repeated_no_progress",
                    "message": (
                        "同一动作反复执行且没有产生新的职位进展。"
                        "请换查询条件、打开/分析已有职位、说明更多要求，或结束本轮。"
                    ),
                }
                state.status = "FAILED"
                notify()
                break
            if state.session.status not in TERMINAL_STATUSES:
                state.status = "RUNNING"
                state.progress = "DECIDING"
            notify()
            continue

        if PRE_STATUS.get(action.tool_name):
            state.progress = PRE_STATUS[action.tool_name]
            notify()

        bound = bind_tool_arguments(state, action)
        if not bound.get("ok"):
            observation = dict(bound.get("observation") or {})
            # Bind failures are Observations (ambiguous/missing evidence, missing job…).
            # Do not rewrite them as action_rejected — Reasoner must see the real kind.
            state.last_raw_observation = observation
            if _fail_closed_missing_candidate(state, observation):
                notify()
                break
            error = str(observation.get("error") or observation.get("kind") or "bind_failed")
            if _bump_reject_and_maybe_fail(
                state,
                error=error,
                attempted={"tool_name": action.tool_name},
                overwrite_observation=False,
            ):
                notify()
                break
            if state.session.status not in TERMINAL_STATUSES:
                state.status = "RUNNING"
                state.progress = "DECIDING"
            notify()
            continue

        bound_arguments = bound.get("arguments") if isinstance(bound.get("arguments"), dict) else {}
        # Keep identity pointers on Action for Reduce admission; strip Tool bodies stay out.
        if action.tool_name in {"understand_user_input", "analyze_candidate"}:
            pointers = dict(action.arguments or {})
            for key in ("intake_ids", "evidence_ids"):
                if key in bound_arguments:
                    pointers[key] = bound_arguments[key]
            action.arguments = pointers

        before_world = world_progress_fingerprint(state)
        try:
            result = _act(
                action,
                registry,
                llm_provider=llm_provider,
                arguments=bound_arguments,
                search_session=getattr(state.search, "session", None),
            )
        except Exception as exc:
            gate = classify_exception(exc, source=action.tool_name)
            if gate:
                apply_human_gate(state, gate)
                notify()
                break
            if _bump_reject_and_maybe_fail(
                state,
                error=str(exc),
                attempted={"tool_name": action.tool_name},
            ):
                notify()
                break
            notify()
            continue

        if action.action_type == "tool" and is_llm_tool(action.tool_name):
            note_llm_call(state)

        _reset_reject_pressure(state)
        state = reduce(state, action, result, ctx)
        note_tool_world_outcome(state, action, before_fingerprint=before_world)
        if state.session.status not in TERMINAL_STATUSES:
            state.status = "RUNNING"
        _sync_task(state, ctx)
        notify()

    _sync_task(state, ctx)
    return state


def _remember_business_observation(state: AgentState, observation: dict) -> AgentState:
    state.last_raw_observation = dict(observation)
    if state.session.status not in TERMINAL_STATUSES:
        state.status = "RUNNING"
    return state


def _remember_observation(state: AgentState, action: Action, result: Any) -> None:
    name = action.tool_name or action.action_type
    state.last_raw_observation = {
        "kind": "tool_result",
        "tool_name": name,
        "result": _observation_view(name, result),
    }
    state.progress = PRE_STATUS.get(name) or state.progress


def _remember_rejection(state: AgentState, action: Action) -> None:
    attempted = (action.arguments or {}).get("attempted") or {}
    state.last_raw_observation = {
        "kind": "action_rejected",
        "error": (action.arguments or {}).get("error") or action.reason,
        "attempted": attempted,
    }
    state.progress = "DECIDING"


def _reset_reject_pressure(state: AgentState) -> None:
    stats = state.search.stats
    stats["consecutive_action_rejects"] = 0
    stats["consecutive_llm_quota_rejects"] = 0


def _is_missing_candidate_signal(payload: dict | None) -> bool:
    if not isinstance(payload, dict):
        return False
    kind = str(payload.get("kind") or payload.get("type") or "")
    error = str(payload.get("error") or "")
    if kind in {"missing_candidate_material", "insufficient_candidate_material"}:
        return True
    lowered = error.lower()
    return (
        "no candidate material" in lowered
        or "missing candidate" in lowered
        or "insufficient_candidate_material" in lowered
    )


def _note_rejection_pressure(state: AgentState, action: Action) -> bool:
    """Fail closed on missing-candidate thrash, LLM-quota thrash, or repeated rejects."""
    error = str((action.arguments or {}).get("error") or action.reason or "")
    attempted = (action.arguments or {}).get("attempted") if isinstance(action.arguments, dict) else {}
    tool_name = ""
    if isinstance(attempted, dict):
        tool_name = str(attempted.get("tool_name") or "")
    missing_signal = _is_missing_candidate_signal({"error": error, "kind": "action_rejected"})
    if missing_signal and (not tool_name or tool_name == "match_job" or "no candidate material" in error.lower()):
        _remember_rejection(state, action)
        return _bump_missing_candidate_and_maybe_fail(
            state,
            {
                "kind": "insufficient_candidate_material",
                "error": error or MISSING_CANDIDATE_ERROR,
                "attempted": attempted,
            },
        )
    return _bump_reject_and_maybe_fail(
        state,
        error=error,
        attempted=attempted if isinstance(attempted, dict) else {},
    )


def _note_thrash_for_reasoner(state: AgentState) -> None:
    """On repeated rejects, emit Observation + structural facts for Reasoner.

    Must never return or execute a business Action (analyze_candidate / open_job / …).
    Program may only re-enter Decide so Reasoner chooses the next step.
    """
    from agent.intake import list_intake, raw_candidate_intake_present
    from agent.validate_action import (
        _pending_candidate_supplement,
        _unexplored_listed_keys,
        _world_has_candidate_material,
    )
    from rules.quota import remaining_opens

    stats = state.search.stats
    consecutive = int(stats.get("consecutive_action_rejects") or 0)
    if consecutive < THRASH_RECOVERY_AFTER:
        return

    prior = state.last_raw_observation if isinstance(state.last_raw_observation, dict) else {}
    intake_ids = [
        str(item.get("id"))
        for item in list_intake(state)
        if isinstance(item, dict) and item.get("id")
    ]
    unexplored = _unexplored_listed_keys(state, limit=8)
    material = _world_has_candidate_material(state)
    pending = raw_candidate_intake_present(state) or _pending_candidate_supplement(state)

    observation = {
        "kind": "thrash_re_reason",
        "layer": "observation",
        "reason": "repeated_rejects_require_reasoner_redecide",
        "consecutive_action_rejects": consecutive,
        "prior_kind": prior.get("kind"),
        "prior_error": prior.get("error") or prior.get("kind"),
        "attempted": prior.get("attempted") if isinstance(prior.get("attempted"), dict) else {},
        "available_intake_ids": intake_ids[:8],
        "unexplored_listed_job_keys": list(unexplored),
        "unexplored_listed_count": len(unexplored),
        "candidate_material_present": bool(material),
        "raw_candidate_intake_present": bool(pending),
        "remaining_opens": remaining_opens(state),
        # Explicit: this Observation is not an Action and does not authorize one.
        "not_action": True,
        "program_must_not_select_tool": True,
    }
    if prior.get("error"):
        observation["error"] = prior.get("error")
    state.last_raw_observation = observation
    stats["thrash_re_reason_notes"] = int(stats.get("thrash_re_reason_notes") or 0) + 1


def _thrash_recovery_action(state: AgentState) -> Action | None:
    """Retired: previously injected analyze_candidate / open_job. Never returns an Action."""
    _note_thrash_for_reasoner(state)
    return None


def _bump_reject_and_maybe_fail(
    state: AgentState,
    *,
    error: str,
    attempted: dict | None = None,
    overwrite_observation: bool = True,
) -> bool:
    attempted = attempted if isinstance(attempted, dict) else {}
    if overwrite_observation:
        _remember_rejection(
            state,
            Action(
                action_type="rejected",
                arguments={"error": error, "attempted": attempted},
            ),
        )
    stats = state.search.stats
    stats["consecutive_action_rejects"] = int(stats.get("consecutive_action_rejects") or 0) + 1
    lowered = error.lower()
    if "quota exceeded: llm" in lowered:
        stats["consecutive_llm_quota_rejects"] = int(stats.get("consecutive_llm_quota_rejects") or 0) + 1
    else:
        stats["consecutive_llm_quota_rejects"] = 0

    opened = int(stats.get("opened") or 0)
    listed = int(stats.get("listed") or len(state.jobs or {}))
    if int(stats["consecutive_llm_quota_rejects"]) >= LLM_QUOTA_REJECT_LIMIT:
        record_error(
            state,
            "llm quota exhausted while tools kept being selected",
            kind="llm_quota_exhausted",
            extra={"opened": opened, "listed": listed, "last_error": error},
        )
        state.output = {
            **(state.output if isinstance(state.output, dict) else {}),
            "error_code": "llm_quota_exhausted",
            "message": (
                "模型调用配额已用尽，分析和匹配没法继续。"
                + (
                    f"当前还有未打开的职位（已打开 {opened}/{listed}），请重试让我继续打开详情。"
                    if listed > opened
                    else "请重试，或换一种方式说明下一步。"
                )
            ),
        }
        state.status = "FAILED"
        return True
    if int(stats["consecutive_action_rejects"]) >= REPEATED_ACTION_REJECT_LIMIT:
        record_error(
            state,
            "too many consecutive action rejects",
            kind="repeated_action_rejected",
            extra={"opened": opened, "listed": listed, "last_error": error},
        )
        state.output = {
            **(state.output if isinstance(state.output, dict) else {}),
            "error_code": "repeated_action_rejected",
            "message": (
                "连续多步动作都被拦住了，这一轮先停下来。"
                + (
                    f"列表里还有未打开的职位（已打开 {opened}/{listed}），请重试继续探索。"
                    if listed > opened
                    else "请重试，或告诉我你更想怎么继续。"
                )
            ),
        }
        state.status = "FAILED"
        return True
    return False


def _fail_closed_missing_candidate(state: AgentState, observation: dict) -> bool:
    if not _is_missing_candidate_signal(observation):
        return False
    return _bump_missing_candidate_and_maybe_fail(state, observation)


def _bump_missing_candidate_and_maybe_fail(state: AgentState, observation: dict) -> bool:
    stats = state.search.stats
    stats["missing_candidate_match_rejects"] = int(stats.get("missing_candidate_match_rejects") or 0) + 1
    if int(stats["missing_candidate_match_rejects"]) < MISSING_CANDIDATE_MATCH_REJECT_LIMIT:
        return False
    opened = int(stats.get("opened") or 0)
    listed = int(stats.get("listed") or len(state.jobs or {}))
    record_error(
        state,
        "missing candidate material after opening jobs; cannot match or recommend",
        kind="insufficient_candidate_material",
        extra={"opened": opened, "listed": listed},
    )
    state.output = {
        **(state.output if isinstance(state.output, dict) else {}),
        "error_code": "insufficient_candidate_material",
        "message": (
            f"已经打开了 {opened} 个职位详情，但还没有可用的候选人材料，无法完成匹配推荐。"
            "请补充经历，或对本轮已有材料进行分析后再继续。"
        ),
    }
    state.status = "FAILED"
    state.last_raw_observation = dict(observation)
    return True


def _observation_view(tool_name: str, result: Any) -> Any:
    if not isinstance(result, dict):
        return result
    if tool_name in SEARCH_TOOLS:
        jobs = []
        for item in result.get("jobs") or []:
            if not isinstance(item, dict):
                continue
            jobs.append(
                {
                    "job_id": item.get("job_id"),
                    "job_url": item.get("job_url"),
                    "job_title": item.get("job_title"),
                    "company_name": item.get("company_name"),
                    "city": item.get("city"),
                }
            )
        return {"jobs": jobs, "platform": result.get("platform"), "count": len(jobs)}
    if tool_name in OPEN_TOOLS or tool_name in INSPECT_JOB_TOOLS:
        return {
            "job_id": result.get("job_id"),
            "job_url": result.get("job_url"),
            "job_title": result.get("job_title"),
            "company_name": result.get("company_name"),
            "platform": result.get("platform"),
            "has_job_description": bool(result.get("job_description")),
            "raw_action_count": len(result.get("raw_actions") or []),
        }
    if tool_name == "inspect_application_state":
        return {
            "job_key": result.get("job_key"),
            "job_id": result.get("job_id"),
            "platform": result.get("platform"),
            "already_applied_matches": list(result.get("already_applied_matches") or []),
            "history_record_count": len(result.get("history_records") or []),
            "page_action_texts": list(result.get("page_action_texts") or []),
        }
    view = {
        key: result.get(key)
        for key in (
            "analysis_status",
            "error",
            "error_code",
            "next_action",
            "rationale",
            "recommendation",
            "hard_requirements_met",
            "overall_fit",
            "keyword",
            "city",
            "execution_status",
            "verification",
        )
        if key in result
    }
    inferred = result.get("inferred_context")
    if isinstance(inferred, dict) and inferred.get("application_evidence"):
        view["application_evidence"] = inferred.get("application_evidence")
    if result.get("application_evidence") and "application_evidence" not in view:
        view["application_evidence"] = result.get("application_evidence")
    if result.get("job_id"):
        view["job_id"] = result.get("job_id")
    if result.get("job_url"):
        view["job_url"] = result.get("job_url")
    if result.get("raw_actions") is not None:
        view["raw_action_count"] = len(result.get("raw_actions") or [])
    return view or result


def _apply_ask_user(state: AgentState, action: Action, ctx: LoopContext | None) -> AgentState:
    arguments = action.arguments if isinstance(action.arguments, dict) else {}
    question = action.question or arguments.get("question") or action.reason or "请再说明一下。"
    intent = action.intent or arguments.get("intent")
    # intent is the discriminator. Do not guess surface from question text or bare job_key.
    if intent == "surface":
        record = _explicit_ask_job(state, action, arguments)
        if record is not None:
            from task.lifecycle import load_bound_task, surface_job

            note_job_decision(
                record,
                kind=DECISION_SURFACED,
                source="reasoner",
                payload={"question": question, "intent": "surface"},
            )
            task = load_bound_task(state, _task_store(ctx))
            if task is not None:
                surface_job(task, record, rationale=action.reason)
                _persist_bound_task(state, task, ctx)
            return _pause_waiting_user(state, record, question)
    state.output = {
        "session_id": state.session.session_id,
        "status": "WAITING_USER",
        "goal": state.goal,
        "message": question,
        "clarification_needed": True,
        "ask_intent": intent or "clarification",
        "error_code": action.error_code or arguments.get("error_code"),
        "recommended": [],
        "excluded": [],
        "stats": dict(state.search.stats),
        "needs_user_decision": True,
    }
    state.status = "WAITING_USER"
    return state


def _apply_finish(state: AgentState, action: Action, ctx: LoopContext | None) -> AgentState:
    from candidate.errors import GOAL_INCOMPLETE

    if (action.error_code or (action.arguments or {}).get("error_code")) == GOAL_INCOMPLETE:
        return _finish_goal_incomplete(state)
    if not state.search.stop_reason:
        state.search.stop_reason = action.reason or "reasoner_finished"
    state.output = build_report(state)
    if state.task_view:
        state.output["task"] = dict(state.task_view)
    state.status = "DONE"
    return state


def _explicit_ask_job(
    state: AgentState,
    action: Action,
    arguments: dict,
) -> JobRecord | None:
    job_key = action.job_key or arguments.get("job_key")
    if not isinstance(job_key, str) or not job_key.strip():
        return None
    return state.jobs.get(job_key.strip())


def _act(
    action: Action,
    registry: ToolRegistry,
    *,
    llm_provider,
    arguments: dict | None = None,
    search_session=None,
) -> Any:
    payload = arguments if arguments is not None else action.arguments
    name = action.tool_name
    if action.action_type != "tool" or not name:
        raise ValueError("only action_type=tool can be executed")
    if not registry.has(name):
        raise ValueError(f"未知 Tool：{name}")
    extra: dict = {"llm_provider": llm_provider}
    if name == "search_jobs":
        extra["search_session"] = search_session
    return registry.invoke(name, payload, **extra)


def reduce(state: AgentState, action: Action, result: Any, ctx: LoopContext | None = None) -> AgentState:
    gate = result_human_gate(result)
    if gate:
        if not gate.get("source"):
            gate = {**gate, "source": action.tool_name or action.action_type}
        if action.tool_name in EXECUTE_TOOLS:
            _restore_waiting_after_blocked_execute(state, ctx)
        return apply_human_gate(state, gate)

    if action.action_type == "fail":
        extra = {}
        raw = action.raw_reasoner_output or (action.arguments or {}).get("raw_reasoner_output")
        if raw is not None:
            extra["raw_reasoner_output"] = raw
        error_code = action.error_code or (action.arguments or {}).get("error_code") or "reasoner_failed"
        message = str((action.arguments or {}).get("error") or "reasoner failed")
        record_error(
            state,
            message,
            kind="reasoner",
            extra={**(extra or {}), "error_code": error_code},
        )
        state.output = {
            **(state.output if isinstance(state.output, dict) else {}),
            "error_code": error_code,
            "message": message,
        }
        state.status = "FAILED"
        return state
    if action.action_type != "tool":
        record_error(state, f"unhandled action {action.action_type}", kind="reasoner")
        state.status = "FAILED"
        return state
    name = action.tool_name
    if name == "parse_user_goal":
        return _reduce_goal(state, result)
    if name == "understand_user_input":
        return _reduce_understanding(state, action, result, ctx)
    if name == "bind_task":
        return _reduce_bind_task(state, action, ctx)
    if name == "skip_job":
        return _reduce_task_control(state, "skip_job", action.arguments or {}, ctx)
    if name == "stop_task":
        return _reduce_task_control(state, "stop_task", action.arguments or {}, ctx)
    if name == "hydrate_job_reference":
        return _reduce_hydrate_job_reference(state, action, ctx)
    if name == "analyze_candidate":
        return _reduce_candidate(state, action, result, ctx)
    if name == "plan_search":
        return _reduce_plan(state, action, result)
    if name in SEARCH_TOOLS:
        return _reduce_search(state, action, result)
    if name in OPEN_TOOLS:
        return _reduce_open(state, action, result)
    if name in INSPECT_JOB_TOOLS:
        return _reduce_inspect_job(state, action, result)
    if name == "inspect_application_state":
        return _reduce_inspect_application_state(state, action, result)
    if name == "interpret_job_actions":
        return _reduce_interpret(state, action, result)
    if name == "analyze_job":
        return _reduce_analyze(state, action, result)
    if name == "match_job":
        return _reduce_match(state, action, result, ctx)
    if name == "record_insufficient_match":
        return _reduce_insufficient_match(state, action, ctx)
    if name == "decide_next_action":
        return _reduce_decide_next(state, action, result, ctx)
    if name in EXECUTE_TOOLS:
        return _reduce_execute(state, action, result, ctx)
    if name == "advance_after_filter":
        return _reduce_advance(state, ctx)
    if name == "rank_jobs":
        return _reduce_rank(state)
    if name == "build_report":
        return _reduce_report(state)
    record_error(state, f"unhandled action {name}")
    state.status = "FAILED"
    return state


def _reduce_understanding(
    state: AgentState,
    action: Action,
    result: dict,
    ctx: LoopContext | None = None,
) -> AgentState:
    if not isinstance(result, dict) or result.get("analysis_status") != "ok":
        incoming = result if isinstance(result, dict) else {}
        status = incoming.get("analysis_status") or "analysis_failed"
        record_error(
            state,
            incoming.get("error") or "understand_user_input failed",
            kind="understanding",
        )
        state.understanding = incoming or None
        state.understanding_status = str(status)
        return _remember_business_observation(
            state,
            {
                "kind": "semantic_understanding",
                "tool_name": "understand_user_input",
                "analysis_status": status,
                "uncertainty": True,
                "error": incoming.get("error"),
                "error_code": incoming.get("error_code") or status,
            },
        )

    from conversation.job_context import job_contexts_from_session
    from conversation.reference_resolver import RESOLUTION_RESOLVED, resolve_conversation_reference
    from tools.parse_user_goal import empty_goal
    from understanding.schema import has_candidate_supplement, has_user_goal

    state.understanding = result
    goal = empty_goal(raw_text=result.get("raw_text"))
    goal.update(result.get("user_goal") or {})
    goal["analysis_status"] = "ok"
    goal["error"] = None
    state.goal = goal
    state.preferences = list(result.get("preferences") or [])
    state.candidate.supplement = result.get("candidate_supplement") or {}
    _admit_action_intake(state, action)
    _ingest_understanding_into_memory(state)
    _refresh_candidate_context(state)

    store = ctx.task_store if ctx is not None else None
    resolution = resolve_conversation_reference(
        result.get("conversation_reference"),
        job_contexts_from_session(state.session_context),
    )
    state.reference_resolution = resolution.to_dict()
    state.task_intent = None
    state.active_tasks = _active_task_views(store, state.conversation_id)
    state.understanding_status = "ok"

    observation = {
        "kind": "user_turn_understood",
        "task_kind": result.get("task_kind"),
        "has_user_goal": has_user_goal(result),
        "has_candidate_supplement": has_candidate_supplement(result),
        "has_attachments": bool(state.attachments),
        "has_resume_text": bool(state.candidate.resume_ref),
        "conversation_reference": result.get("conversation_reference"),
        "reference_resolution": dict(state.reference_resolution),
        "active_tasks": list(state.active_tasks),
    }
    # Factual flags only. Program coaching belongs in program_hints (Context), not Observation.
    if result.get("task_kind") is None:
        observation["uncertainty"] = True
        observation["missing"] = ["task_kind"]
    if result.get("conversation_reference") and (
        resolution.status != RESOLUTION_RESOLVED or resolution.job_context is None
    ):
        observation.update(
            {
                "kind": "reference_unresolved",
                "error_code": "reference_unresolved",
                "message": "我不确定你指的是哪一个职位，请再说明一下。",
            }
        )
    return _remember_business_observation(state, observation)


def _reduce_goal(state: AgentState, result: dict) -> AgentState:
    if not isinstance(result, dict) or result.get("analysis_status") != "ok":
        incoming = result if isinstance(result, dict) else {}
        status = incoming.get("analysis_status") or "analysis_failed"
        record_error(state, incoming.get("error") or "parse_user_goal failed", kind="understanding")
        state.goal = incoming or None
        return _remember_business_observation(
            state,
            {
                "kind": "tool_result",
                "tool_name": "parse_user_goal",
                "analysis_status": status,
                "uncertainty": True,
                "error": incoming.get("error"),
                "error_code": incoming.get("error_code") or status,
            },
        )
    state.goal = result
    _remember_observation(state, tool_action("parse_user_goal"), result)
    state.status = "RUNNING"
    return state


def _reduce_candidate(
    state: AgentState,
    action: Action,
    result: dict,
    ctx: LoopContext | None = None,
) -> AgentState:
    from agent.state import bind_semantic_layers
    from candidate.memory import empty_memory, merge_memory, note_unresolved
    from storage.errors import StorageError

    incoming = result if isinstance(result, dict) else {}
    memory = incoming.get("memory") or empty_memory()
    bind_semantic_layers(state, merge_memory(state.candidate.memory or empty_memory(), memory))
    if incoming.get("analysis_status") == "ok":
        _admit_action_intake(state, action)
    ingest_status = incoming.get("analysis_status")
    if incoming.get("memory") or incoming.get("direct_capabilities") or incoming.get("project_experience"):
        state.candidate.profile = incoming
    if ingest_status == "ok":
        state.candidate.profile_status = "ok"
    if incoming.get("candidate_id"):
        state.candidate.candidate_id = incoming["candidate_id"]
    # Job-seeking agent: successful LLM ingest of usable candidate facts → default portrait.
    # Text input and attachments are both materials; persist is not gated on "记住".
    from candidate.memory import memory_is_usable

    should_persist = ingest_status == "ok" and memory_is_usable(state.candidate.memory)
    if should_persist:
        try:
            _persist_profile_if_needed(state)
            state.candidate.profile_persisted_this_turn = True
        except StorageError as exc:
            state.candidate.memory = note_unresolved(
                state.candidate.memory,
                code="TOOL_ERROR",
                message=f"profile persist skipped: {exc}",
                field=None,
            )
    _refresh_candidate_context(state)
    _remember_observation(state, tool_action("analyze_candidate"), incoming)
    state.status = "RUNNING"
    return state


def _admit_action_intake(state: AgentState, action: Action) -> None:
    """Admit raw intake used by this Action into Evidence. No carrier→type mapping."""
    from agent.intake import admit_intake_as_evidence, list_intake
    from agent.state import bind_semantic_layers
    from candidate.memory import empty_memory

    args = action.arguments if isinstance(action.arguments, dict) else {}
    requested = args.get("intake_ids")
    if not isinstance(requested, list) or not requested:
        return
    wanted = {str(item).strip() for item in requested if item is not None and str(item).strip()}
    if not wanted:
        return
    items = [item for item in list_intake(state) if str(item.get("id")) in wanted]
    if not items:
        return
    memory = admit_intake_as_evidence(state.candidate.memory or empty_memory(), items)
    bind_semantic_layers(state, memory)


def _reduce_plan(state: AgentState, action: Action, result: Any) -> AgentState:
    mark_active_plan_exhausted(state)
    keyword, city, fail_reason = _plan_fields(state, action, result)
    if fail_reason == "missing_keyword" or not keyword:
        if fail_reason == "missing_keyword":
            return _remember_business_observation(
                state,
                {
                    "kind": "search_plan_incomplete",
                    "missing": ["keyword"],
                    "tool_name": "plan_search",
                },
            )
        _remember_observation(state, action, result if isinstance(result, dict) else {"error": fail_reason})
        state.search.stop_reason = fail_reason or STOP_NO_NEXT_PLAN
        state.status = "RUNNING"
        return state
    used = {item.casefold() for item in used_plan_keywords(state)}
    if keyword.casefold() in used:
        _remember_observation(state, action, result)
        state.search.stop_reason = STOP_NO_NEXT_PLAN
        state.status = "RUNNING"
        return state
    state.search.stop_reason = None
    plan = SearchPlan(
        plan_id=f"plan-{len(state.search.plans) + 1}",
        keyword=keyword,
        city=city,
        platform=state.constraints.data_source,
    )
    state.search.plans.append(plan)
    state.search.active_plan_id = plan.plan_id
    _remember_observation(state, action, result)
    state.status = "RUNNING"
    return state


def _plan_fields(state: AgentState, action: Action, result: Any) -> tuple[str | None, str | None, str | None]:
    if not isinstance(result, dict) or result.get("analysis_status") != "ok":
        status = (result or {}).get("analysis_status") if isinstance(result, dict) else None
        error = (result or {}).get("error") if isinstance(result, dict) else "plan_search failed"
        record_error(state, str(error or "plan_search failed"), kind="plan_search")
        return None, None, STOP_PLAN_SEARCH_FAILED if status else STOP_NO_NEXT_PLAN

    keyword = result.get("keyword")
    city = result.get("city")
    text = str(keyword).strip() if keyword else None
    city_text = str(city).strip() if city else None
    if not text:
        return None, city_text or None, "missing_keyword"
    return text, city_text or None, None


def _reduce_search(state: AgentState, action: Action, result: Any) -> AgentState:
    from platforms.search_contract import session_from_dict

    jobs = _jobs_from_search(result)
    capacity = remaining_list_capacity(state)
    newly_ingested = 0
    duplicates = 0
    capacity_skipped = 0
    existing = seen_keys(state.jobs)
    next_order = 0 if not state.jobs else max(record.listed_order for record in state.jobs.values()) + 1
    for job in jobs:
        listed = normalize_common_job(job, platform=job.get("platform") or state.constraints.data_source)
        if is_duplicate(listed, existing):
            duplicates += 1
            continue
        if newly_ingested >= capacity:
            capacity_skipped += 1
            continue
        key = job_record_key(listed)
        if not key:
            record_error(state, "search result missing job_id and job_url", kind="search")
            continue
        reason = list_exclude_reason(listed, state.constraints)
        flags = list_constraint_flags(listed, state.constraints)
        if reason == "blacklist" and "blacklist" not in flags:
            flags = [*flags, "blacklist"]
        record = JobRecord(
            job_key=key,
            stage="listed",
            listed_order=next_order,
            listed=listed,
            exclude_reason=reason if reason == "blacklist" else None,
            constraint_flags=flags,
        )
        if "already_applied" in flags:
            _stamp_application_evidence(record, "already_applied")
        state.jobs[key] = record
        remember_job(existing, listed)
        next_order += 1
        newly_ingested += 1
    note_search(
        state,
        newly_ingested=newly_ingested,
        duplicates=duplicates,
        capacity_skipped=capacity_skipped,
    )
    args = action.arguments if isinstance(action.arguments, dict) else {}
    keyword = args.get("keyword")
    city = args.get("city")
    mode = args.get("mode") or "fresh"
    fetch_status = "ok"
    can_continue_flag = True
    session_proj = None
    if isinstance(result, dict):
        mode = result.get("mode") or mode
        fetch_status = str(result.get("fetch_status") or "ok")
        can_continue_flag = bool(result.get("can_continue"))
        session = session_from_dict(result.get("search_session"))
        if session is not None:
            state.search.session = session
            session_proj = {
                "session_id": session.session_id,
                "platform": session.platform,
                "keyword": session.keyword,
                "city": session.city,
                "status": session.status,
                "exposed_count": len(session.exposed_job_keys),
            }

    if keyword:
        plan = active_plan(state)
        same = (
            plan is not None
            and str(plan.keyword or "").strip().casefold() == str(keyword).strip().casefold()
        )
        if not same:
            plan = SearchPlan(
                plan_id=f"plan-{len(state.search.plans) + 1}",
                keyword=str(keyword),
                city=str(city) if city else None,
                platform=state.constraints.data_source,
            )
            state.search.plans.append(plan)
            state.search.active_plan_id = plan.plan_id
        # SearchPlan.exhausted == result-set exhausted, not "search was invoked".
        if fetch_status == "exhausted" and plan is not None:
            plan.exhausted = True
            if plan.plan_id not in state.search.exhausted:
                state.search.exhausted.append(plan.plan_id)

    progress = newly_ingested > 0
    stats = state.search.stats
    state.last_raw_observation = {
        "kind": "tool_result",
        "tool_name": action.tool_name or action.action_type,
        "layer": "observation",
        "search_executed": True,
        "query": {
            "keyword": keyword,
            "city": city,
            "limit": args.get("limit"),
            "mode": mode,
        },
        "mode": mode,
        "new_jobs": newly_ingested,
        "newly_ingested": newly_ingested,
        "duplicate_jobs": duplicates,
        "duplicates": duplicates,
        "capacity_skipped": capacity_skipped,
        "progress": progress,
        "fetch_status": fetch_status,
        "can_continue": can_continue_flag,
        "search_session": session_proj,
        "listed_count": int(stats.get("listed_count") or stats.get("listed") or 0),
        "search_executed_count": int(stats.get("search_executed_count") or stats.get("searches") or 0),
        "zero_increment_count": int(stats.get("zero_increment_count") or 0),
        "remaining_list_capacity": remaining_list_capacity(state),
        "result": _observation_view(action.tool_name or "search_jobs", result),
    }
    state.progress = PRE_STATUS.get(action.tool_name or "") or state.progress
    state.status = "RUNNING"
    return state


def _reduce_open(state: AgentState, action: Action, result: Any) -> AgentState:
    if not isinstance(result, dict):
        record_error(state, "open_* did not return a job object")
        state.status = "FAILED"
        return state
    record = _find_record(state, action, result)
    if record is None:
        return _missing_job_target(state, action, "open_* arguments did not name a listed job")
    opened = merge_opened(record.listed, result)
    record.opened = opened
    record.stage = "opened"
    note_open(state)
    _remember_observation(state, action, result)
    state.status = "RUNNING"
    return state


def _reduce_inspect_job(state: AgentState, action: Action, result: Any) -> AgentState:
    if not isinstance(result, dict):
        record_error(state, "inspect_job did not return a job object")
        state.status = "FAILED"
        return state
    record = _find_record(state, action, result)
    if record is None:
        return _missing_job_target(state, action, "inspect_job arguments did not name a listed job")
    opened = merge_opened(record.listed, result)
    record.opened = opened
    if record.stage == "listed":
        record.stage = "opened"
        note_open(state)
    _remember_observation(state, action, result)
    state.status = "RUNNING"
    return state


def _reduce_inspect_application_state(state: AgentState, action: Action, result: Any) -> AgentState:
    _remember_observation(state, action, result)
    state.status = "RUNNING"
    return state


def _reduce_interpret(state: AgentState, action: Action, result: Any) -> AgentState:
    record = _find_record(state, action, result)
    if record is None:
        return _missing_job_target(state, action, "interpret_job_actions arguments did not name a job")
    record.interpret_result = result if isinstance(result, dict) else None
    if isinstance(result, dict):
        inferred = result.get("inferred_context") if isinstance(result.get("inferred_context"), dict) else {}
        application_evidence = inferred.get("application_evidence") or result.get("application_evidence")
        if application_evidence:
            from candidate.semantic import make_interpretation

            record.interpretations.append(
                make_interpretation(
                    kind="application_evidence",
                    derived_from=[],
                    payload={
                        "application_evidence": application_evidence,
                        "job_key": record.job_key,
                    },
                    existing=record.interpretations,
                )
            )
    _remember_observation(state, action, result)
    state.status = "RUNNING"
    return state


def _reduce_analyze(state: AgentState, action: Action, result: Any) -> AgentState:
    record = _find_record(state, action, result)
    if record is None:
        return _missing_job_target(state, action, "analyze_job arguments did not name a job")
    record.job_profile = result if isinstance(result, dict) else None
    if isinstance(result, dict) and result.get("analysis_status") == "ok":
        record.stage = "analyzed"
    _remember_observation(state, action, result)
    state.status = "RUNNING"
    return state


def _reduce_insufficient_match(state: AgentState, action: Action, ctx: LoopContext | None = None) -> AgentState:
    from candidate.errors import INSUFFICIENT_INFORMATION

    record = _find_record(state, action, None)
    if record is None:
        return _missing_job_target(state, action, "record_insufficient_match arguments did not name a job")
    record.match_result = {
        "analysis_status": "insufficient_data",
        "error": INSUFFICIENT_INFORMATION,
        "error_code": INSUFFICIENT_INFORMATION,
        "recommendation": "insufficient_evidence",
        "hard_requirements_met": None,
        "rationale": "Candidate working memory does not yet have enough facts for matching.",
        "capability_assessments": [],
    }
    state.last_raw_observation = {
        "kind": "tool_result",
        "tool_name": "record_insufficient_match",
        "result": {"error_code": INSUFFICIENT_INFORMATION},
    }
    state.status = "RUNNING"
    return state


def _reduce_match(state: AgentState, action: Action, result: Any, ctx: LoopContext | None = None) -> AgentState:
    record = _find_record(state, action, result)
    if record is None:
        return _missing_job_target(state, action, "match_job arguments did not name a job")
    record.match_result = result if isinstance(result, dict) else None
    if isinstance(result, dict):
        record.stage = "matched"
    _remember_observation(state, action, result)
    state.status = "RUNNING"
    return state


def _reduce_advance(state: AgentState, ctx: LoopContext | None = None) -> AgentState:
    state.last_raw_observation = {
        "kind": "tool_result",
        "tool_name": "advance_after_filter",
        "result": {"follow_up_of": state.follow_up_of},
    }
    state.status = "RUNNING"
    return state


def _reduce_rank(state: AgentState) -> AgentState:
    matched = [record for record in state.jobs.values() if isinstance(record.match_result, dict)]
    matched.sort(key=lambda item: item.listed_order)
    ordered = rank_records(matched)
    ranked = []
    for record in ordered:
        ranked.append(
            {
                "job_key": record.job_key,
                "recommendation": (record.match_result or {}).get("recommendation"),
                "stage": record.stage,
            }
        )
    state.last_raw_observation = {
        "kind": "tool_result",
        "tool_name": "rank_jobs",
        "result": {"ranked": ranked, "count": len(ranked)},
    }
    state.status = "RUNNING"
    return state


def _reduce_report(state: AgentState) -> AgentState:
    if state.session.status == "WAITING_USER":
        return state
    state.output = build_report(state)
    if state.task_view:
        state.output["task"] = dict(state.task_view)
    state.last_raw_observation = {
        "kind": "tool_result",
        "tool_name": "build_report",
        "result": {"recommended_count": len((state.output or {}).get("recommended") or [])},
    }
    state.status = "RUNNING"
    return state


def _reduce_follow_up(state: AgentState, resolution) -> AgentState:
    from conversation.job_context import hydrate_job_record
    from conversation.reference_resolver import RESOLUTION_RESOLVED

    if resolution.status != RESOLUTION_RESOLVED or resolution.job_context is None:
        return _remember_business_observation(
            state,
            {
                "kind": "reference_unresolved",
                "error_code": "reference_unresolved",
                "message": "我不确定你指的是哪一个职位，请再说明一下。",
                "reference_resolution": resolution.to_dict(),
            },
        )

    ctx = resolution.job_context
    state.follow_up_of = ctx.job_key
    state.resolved_job_context = ctx.to_dict()
    hydrate_job_record(state, ctx)
    _refresh_candidate_context(state)
    has_profile = isinstance(ctx.job_profile, dict)
    return _remember_business_observation(
        state,
        {
            "kind": "follow_up_resolved",
            "job_key": ctx.job_key,
            "has_job_profile": has_profile,
            "missing_context": [] if has_profile else ["job_profile"],
        },
    )


def _reduce_hydrate_job_reference(state: AgentState, action: Action, ctx: LoopContext | None) -> AgentState:
    _bind_task_from_arguments(state, action.arguments or {}, ctx)
    resolution = _resolution_from_state(state, action.arguments or {})
    return _reduce_follow_up(state, resolution)


def _reduce_bind_task(state: AgentState, action: Action, ctx: LoopContext | None) -> AgentState:
    from task.lifecycle import bind_task, create_bound_task, hydrate_state_from_task

    arguments = action.arguments if isinstance(action.arguments, dict) else {}
    store = _task_store(ctx)
    task = None
    task_id = arguments.get("task_id")
    create_decision = arguments.get("create")
    if create_decision is True:
        task = create_bound_task(state, store)
    elif isinstance(task_id, str) and task_id.strip() and store is not None:
        task = store.get(task_id.strip())
    if task is None:
        task = _task_for_control(state, arguments, ctx)
    if task is None:
        if create_decision is None:
            return _remember_business_observation(
                state,
                {
                    "kind": "task_clarification",
                    "reason": "create_decision_missing",
                    "control": "bind_task",
                    "tool_name": "bind_task",
                },
            )
        return _remember_business_observation(
            state,
            {
                "kind": "task_clarification",
                "error_code": "clarification_needed",
                "message": "当前没有可以进行绑定的求职任务。",
                "control": "bind_task",
                "tool_name": "bind_task",
            },
        )
    bind_task(state, task)
    hydrate_state_from_task(state, task)
    return _remember_business_observation(
        state,
        {
            "kind": "task_bound",
            "task_id": task.task_id,
            "task_kind": (state.understanding or {}).get("task_kind"),
            "created": create_decision is True,
            "tool_name": "bind_task",
        },
    )


def _reduce_task_control(
    state: AgentState,
    control: str,
    arguments: dict,
    ctx: LoopContext | None,
) -> AgentState:
    from task.lifecycle import (
        bind_task,
        hydrate_state_from_task,
        skip_current_job,
        stop_task,
    )

    task = _task_for_control(state, arguments, ctx)
    if task is None:
        return _remember_business_observation(
            state,
            {
                "kind": "task_clarification",
                "error_code": "clarification_needed",
                "message": "当前没有进行中的求职任务可以执行这个操作。",
                "control": control,
                "tool_name": control,
            },
        )
    store = _task_store(ctx)
    bind_task(state, task)
    state.task_control = control

    if control == "stop_task":
        stop_task(task, reason="user_requested")
        if store is not None:
            store.save(task)
        bind_task(state, task)
        state.search.stop_reason = "user_requested"
        state.task_control = None
        return _remember_business_observation(
            state,
            {
                "kind": "task_stopped",
                "tool_name": "stop_task",
                "task_id": task.task_id,
            },
        )

    if control == "skip_job":
        if task.task_status != "WAITING_USER" or not task.current_job_context_id:
            return _remember_business_observation(
                state,
                {
                    "kind": "task_clarification",
                    "error_code": "clarification_needed",
                    "message": "当前没有等待你决定的职位可以跳过。",
                    "control": "skip_job",
                    "tool_name": "skip_job",
                },
            )
        skipped = skip_current_job(task)
        hydrate_state_from_task(state, task)
        if skipped is not None and skipped.job_key in state.jobs:
            note_job_decision(
                state.jobs[skipped.job_key],
                kind=DECISION_SKIPPED,
                source="user",
                payload={"via": "skip_job"},
            )
        if store is not None:
            store.save(task)
        bind_task(state, task)
        state.pending_job_key = None
        state.task_control = None
        return _remember_business_observation(
            state,
            {
                "kind": "job_skipped",
                "tool_name": "skip_job",
                "job_key": skipped.job_key if skipped is not None else None,
            },
        )

    return _remember_business_observation(
        state,
        {
            "kind": "task_clarification",
            "error_code": "clarification_needed",
            "message": "请再说明一下。",
            "control": control,
            "tool_name": control,
        },
    )


def _reduce_decide_next(state: AgentState, action: Action, result: Any, ctx: LoopContext | None) -> AgentState:
    # Retired second decision-maker. Mis-invocation is Observation only; no exclude / COMPLETE_TASK / pending_job.
    _remember_observation(state, action, result)
    state.status = "RUNNING"
    return state


def _reduce_execute(state: AgentState, action: Action, result: Any, ctx: LoopContext | None) -> AgentState:
    from task.lifecycle import bind_task, load_bound_task, record_application, resume_running

    _bind_task_from_arguments(state, action.arguments or {}, ctx)
    record = _find_record(state, action, result) or _pending_record(state)
    if record is None:
        return _missing_job_target(state, action, "execute_job_action arguments did not name a job")
    payload = result if isinstance(result, dict) else {}
    execution_status = payload.get("execution_status") or payload.get("result")
    result_name = payload.get("result")
    confirmed = (
        payload.get("ok") is True
        and result_name == "applied"
        and payload.get("verification") in {None, "confirmed"}
        and execution_status in {None, "success", "applied"}
    )
    if payload.get("verification") == "uncertain" or execution_status == "uncertain":
        confirmed = False
        result_name = "uncertain"

    evidence = payload.get("application_evidence")
    history_result: str | None
    history_evidence: str | None
    if confirmed:
        _stamp_application_evidence(record, evidence or "applied")
        _record_execute_verification(record, status="confirmed", evidence=evidence or "applied")
        history_result = "applied"
        history_evidence = evidence or "applied"
    elif execution_status == "already_applied" or result_name == "already_applied":
        _stamp_application_evidence(record, evidence or "applied")
        history_result = "already_applied"
        history_evidence = evidence or "already_applied"
    elif execution_status == "already_executed" or result_name == "already_executed":
        if payload.get("ok") is True or payload.get("prior_result") == "applied":
            _stamp_application_evidence(record, evidence or "applied")
        history_result = None
        history_evidence = None
    else:
        _stamp_application_evidence(record, payload.get("error") or evidence or "failed")
        history_result = result_name if result_name in {"uncertain", "failed"} else "failed"
        history_evidence = payload.get("error") or evidence

    task = load_bound_task(state, _task_store(ctx))
    if task is not None and history_result is not None:
        record_application(
            task,
            result=history_result,
            evidence=history_evidence,
            job_context_id=record.job_key,
        )
        store = _task_store(ctx)
        if store is not None:
            store.save(task)
        bind_task(state, task)
    elif task is not None:
        resume_running(task)
        store = _task_store(ctx)
        if store is not None:
            store.save(task)
        bind_task(state, task)
    state.pending_job_key = None
    state.task_control = None
    state.last_raw_observation = {
        "kind": "tool_result",
        "tool_name": action.tool_name,
        "result": {"execution_status": execution_status, "result": result_name},
    }
    state.status = "RUNNING"
    return state


def _restore_waiting_after_blocked_execute(state: AgentState, ctx: LoopContext | None) -> None:
    from task.lifecycle import bind_task, load_bound_task, restore_waiting_user

    record = _pending_record(state)
    task = load_bound_task(state, _task_store(ctx))
    if task is None:
        return
    restore_waiting_user(task, record.job_key if record is not None else task.current_job_context_id)
    store = _task_store(ctx)
    if store is not None:
        store.save(task)
    bind_task(state, task)


def _pause_waiting_user(state: AgentState, record: JobRecord, rationale: str | None) -> AgentState:
    from agent.report import build_waiting_user_report

    state.search.stats["recommended"] = len(
        [item for item in state.jobs.values() if job_has_decision(item, DECISION_SURFACED)]
    )
    state.output = build_waiting_user_report(state, record, rationale=rationale)
    if state.task_view:
        state.output["task"] = dict(state.task_view)
    state.status = "WAITING_USER"
    return state


def _complete_current_task(state: AgentState, ctx: LoopContext | None) -> AgentState:
    from task.lifecycle import bind_task, complete_task, load_bound_task

    mark_active_plan_exhausted(state)
    reason = state.search.stop_reason or "task_complete"
    state.search.stop_reason = reason
    task = load_bound_task(state, _task_store(ctx))
    if task is not None:
        complete_task(task, reason=reason)
        store = _task_store(ctx)
        if store is not None:
            store.save(task)
        bind_task(state, task)
    state.last_raw_observation = {
        "kind": "tool_result",
        "tool_name": "complete_current_task",
        "result": {"stop_reason": reason},
    }
    state.status = "RUNNING"
    return state


def _task_reject(state: AgentState, record: JobRecord, ctx: LoopContext | None, *, rationale: str) -> None:
    from task.lifecycle import load_bound_task, mark_job_rejected

    task = load_bound_task(state, _task_store(ctx))
    if task is None:
        return
    mark_job_rejected(task, record, rationale=rationale)
    _persist_bound_task(state, task, ctx)


def _persist_bound_task(state: AgentState, task, ctx: LoopContext | None) -> None:
    from task.lifecycle import bind_task

    store = _task_store(ctx)
    if store is not None:
        store.save(task)
    bind_task(state, task)


def _pending_record(state: AgentState) -> JobRecord | None:
    if state.pending_job_key and state.pending_job_key in state.jobs:
        return state.jobs[state.pending_job_key]
    return None


def _stamp_application_evidence(record: JobRecord, evidence: str) -> None:
    interpret = dict(record.interpret_result or {})
    inferred = dict(interpret.get("inferred_context") or {})
    inferred["application_evidence"] = evidence
    interpret["inferred_context"] = inferred
    record.interpret_result = interpret


def _record_execute_verification(record: JobRecord, *, status: str, evidence: str) -> None:
    from candidate.semantic import VERIFICATION_BASIS_TOOL, make_verification

    record.verifications.append(
        make_verification(
            status=status,
            basis=VERIFICATION_BASIS_TOOL,
            derived_from=[],
            payload={"application_evidence": evidence, "job_key": record.job_key},
            existing=record.verifications,
        )
    )


def _task_store(ctx: LoopContext | None):
    if ctx is None:
        return None
    if ctx.task_store is None:
        from task.store import JobSearchTaskStore

        ctx.task_store = JobSearchTaskStore()
    return ctx.task_store


def _task_for_control(state: AgentState, arguments: dict, ctx: LoopContext | None):
    from task.lifecycle import bind_task, hydrate_state_from_task

    store = _task_store(ctx)
    task_id = arguments.get("task_id") or (state.task_intent or {}).get("task_id")
    task = None
    if store is not None and isinstance(task_id, str) and task_id.strip():
        task = store.get(task_id.strip())
    if task is None and store is not None and state.job_search_task_id:
        task = store.get(state.job_search_task_id)
    if task is None:
        return None
    bind_task(state, task)
    hydrate_state_from_task(state, task)
    return task


def _bind_task_from_arguments(state: AgentState, arguments: dict, ctx: LoopContext | None) -> None:
    _task_for_control(state, arguments, ctx)


def _resolution_from_state(state: AgentState, arguments: dict):
    from conversation.job_context import get_job_context, job_context_from_dict, job_contexts_from_session
    from conversation.reference_resolver import (
        RESOLUTION_RESOLVED,
        RESOLUTION_UNRESOLVED,
        ReferenceResolution,
    )

    raw = state.reference_resolution if isinstance(state.reference_resolution, dict) else {}
    job_key = arguments.get("job_key") or raw.get("job_key") or raw.get("context_id")
    ctx = None
    blob = raw.get("job_context")
    if isinstance(blob, dict):
        ctx = job_context_from_dict(blob)
    if ctx is None and job_key:
        ctx = get_job_context(job_contexts_from_session(state.session_context), job_key)
    if ctx is None:
        return ReferenceResolution(
            status=RESOLUTION_UNRESOLVED,
            error="follow-up job is not resolved",
            candidates=list(raw.get("candidates") or []),
        )
    return ReferenceResolution(
        status=RESOLUTION_RESOLVED,
        job_context=ctx,
        candidates=[ctx.context_id],
    )


def _sync_task(state: AgentState, ctx: LoopContext | None) -> None:
    from task.lifecycle import sync_task_from_state

    store = _task_store(ctx) if state.job_search_task_id else None
    if store is None:
        return
    sync_task_from_state(state, store)


def _active_task_views(store, conversation_id: str | None) -> list[dict]:
    if store is None or not conversation_id:
        return []
    return [
        {
            "task_id": task.task_id,
            "task_status": task.task_status,
            "current_job_context_id": task.current_job_context_id,
        }
        for task in store.list_active(conversation_id)
    ]


def _jobs_from_search(result: Any) -> list[dict]:
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    if isinstance(result, dict):
        jobs = result.get("jobs")
        if isinstance(jobs, list):
            return [item for item in jobs if isinstance(item, dict)]
    return []


def _missing_job_target(state: AgentState, action: Action, message: str) -> AgentState:
    state.last_raw_observation = {
        "kind": "tool_result",
        "tool_name": action.tool_name,
        "result": {"error": message, "error_code": "missing_job_identity"},
    }
    state.status = "RUNNING"
    return state


def _find_record(state: AgentState, action: Action, result: Any) -> JobRecord | None:
    blobs: list[dict] = []
    if isinstance(action.arguments, dict):
        blobs.append(action.arguments)
        for nested_key in ("job", "page_context", "job_profile", "job_listing"):
            nested = action.arguments.get(nested_key)
            if isinstance(nested, dict):
                blobs.append(nested)

    for blob in blobs:
        explicit_key = blob.get("job_key")
        if isinstance(explicit_key, str) and explicit_key.strip() and explicit_key.strip() in state.jobs:
            return state.jobs[explicit_key.strip()]
        key = job_record_key(blob)
        if key and key in state.jobs:
            return state.jobs[key]
        job_id = blob.get("job_id")
        if job_id:
            for record in state.jobs.values():
                listed_id = (record.listed or {}).get("job_id")
                opened_id = (record.opened or {}).get("job_id") if record.opened else None
                if job_id in {listed_id, opened_id}:
                    return record
        job_url = blob.get("job_url")
        if job_url:
            for record in state.jobs.values():
                listed_url = (record.listed or {}).get("job_url")
                opened_url = (record.opened or {}).get("job_url") if record.opened else None
                if job_url in {listed_url, opened_url}:
                    return record
    return None


def _refresh_candidate_context(state: AgentState) -> None:
    from candidate.context import build_candidate_context

    understanding = state.understanding or {}
    profile = state.candidate.profile if isinstance(state.candidate.profile, dict) else None
    if isinstance(profile, dict):
        profile = dict(profile)
        profile["kind"] = "interpretation_projection"
    state.candidate_context = build_candidate_context(
        persistent_profile=profile,
        candidate_supplement=state.candidate.supplement or understanding.get("candidate_supplement"),
        matching_context=understanding.get("matching_context"),
        preferences=state.preferences,
        constraints=understanding.get("constraints"),
        profile_version=state.candidate.profile_version,
        candidate_memory=state.candidate.memory,
    )


def _ingest_understanding_into_memory(state: AgentState) -> None:
    from agent.state import bind_semantic_layers
    from candidate.memory import empty_memory, ingest_understanding

    understanding = state.understanding if isinstance(state.understanding, dict) else {}
    memory = ingest_understanding(
        state.candidate.memory or empty_memory(),
        state.candidate.supplement,
        support_check=understanding.get("support_check") or [],
    )
    bind_semantic_layers(state, memory)
    _refresh_candidate_context(state)


def _finish_goal_incomplete(state: AgentState) -> AgentState:
    from candidate.errors import GOAL_INCOMPLETE

    state.output = {
        "session_id": state.session.session_id,
        "status": "DONE",
        "goal": state.goal,
        "candidate_id": state.candidate.candidate_id,
        "platforms": list(state.constraints.platforms or []),
        "recommended": [],
        "excluded": [],
        "stats": dict(state.search.stats),
        "clarification_needed": True,
        "error_code": GOAL_INCOMPLETE,
        "message": "我已经记下你提供的经历。还需要你告诉我想找什么方向的工作，我才能开始搜索。",
        "candidate_memory": state.candidate.memory,
    }
    state.status = "DONE"
    return state


def _persist_profile_if_needed(state: AgentState) -> None:
    from storage.candidate_profile import CandidateProfileStore, compute_source_hash
    from storage.errors import StorageError

    if not state.profile_dir:
        return
    from candidate.memory import project_profile_view

    memory = state.candidate.memory if isinstance(state.candidate.memory, dict) else {}
    profile = project_profile_view(memory, candidate_id=state.candidate.candidate_id)
    profile["analysis_status"] = "ok"
    profile["error"] = None
    profile["kind"] = "interpretation_projection"
    state.candidate.profile = profile
    state.candidate.profile_status = "ok"
    store = CandidateProfileStore(state.profile_dir)
    cid = state.candidate.candidate_id or "default"
    resume_hash = None
    if state.candidate.resume_ref:
        try:
            resume_hash = compute_source_hash(state.candidate.resume_ref)
        except StorageError:
            resume_hash = None
    kinds = _source_kinds_for_persist(state)
    layers = {
        "evidence": list(memory.get("evidence") or []),
        "claims": list(memory.get("claims") or []),
        "interpretations": list(memory.get("interpretations") or []),
        "verifications": list(memory.get("verifications") or []),
    }
    if store.exists(cid):
        kwargs: dict = {"source_kinds": kinds, **layers}
        if resume_hash:
            kwargs["source_resume_hash"] = resume_hash
            kwargs["source_resume_name"] = "resume"
        updated = store.update(cid, state.candidate.profile, **kwargs)
        state.candidate.profile_version = updated["profile_version"]
        if updated.get("source_resume_hash"):
            state.candidate.source_resume_hash = updated.get("source_resume_hash")
        return
    created = store.create(
        state.candidate.profile,
        candidate_id=cid,
        source_resume_name="resume" if resume_hash else None,
        source_resume_hash=resume_hash,
        source_kinds=kinds,
        **layers,
    )
    state.candidate.profile_version = created["profile_version"]
    state.candidate.source_resume_hash = created.get("source_resume_hash")


def _source_kinds_for_persist(state: AgentState) -> list[str]:
    """Persist tags from semantic claims only — never from upload/filename/carrier."""
    allowed = {"resume", "user_statement", "project_document", "other_attachment"}
    kinds: list[str] = []
    memory = state.candidate.memory if isinstance(state.candidate.memory, dict) else {}
    for item in list(memory.get("claims") or []) + list(memory.get("facts") or []):
        if not isinstance(item, dict):
            continue
        kind = item.get("source_kind")
        if isinstance(kind, str) and kind in allowed and kind not in kinds:
            kinds.append(kind)
    # Default tag when LLM facts lack source_kind: user-provided materials (text or upload).
    if not kinds:
        kinds.append("user_statement")
    return kinds
