"""Unified Tool dispatch. Registry does not orchestrate the Agent Loop."""

from __future__ import annotations

from typing import Any, Callable

from candidate.analyze_candidate import ANALYZE_CANDIDATE_SPEC, analyze_candidate
from job.analyze_job import ANALYZE_JOB_SPEC, analyze_job
from matching.match_job import MATCH_JOB_SPEC, match_job
from platforms.job_actions import (
    EXECUTE_ACTION_SPEC,
    INSPECT_APPLICATION_STATE_SPEC,
    INSPECT_JOB_SPEC,
    OPEN_JOB_SPEC,
    SEARCH_JOBS_SPEC,
    execute_action,
    inspect_application_state,
    inspect_job,
    open_job,
    search_jobs,
)
from tools.execute_job_action import EXECUTE_JOB_ACTION_SPEC, execute_job_action
from tools.reason_next_action import REASON_NEXT_ACTION_SPEC, reason_next_action
from tools.task_tools import (
    BIND_TASK_SPEC,
    HYDRATE_JOB_REFERENCE_SPEC,
    SKIP_JOB_SPEC,
    STOP_TASK_SPEC,
    bind_task_tool,
    hydrate_job_reference_tool,
    skip_job_tool,
    stop_task_tool,
)
from tools.interpret_job_actions import INTERPRET_JOB_ACTIONS_SPEC, interpret_job_actions
from tools.parse_user_goal import PARSE_USER_GOAL_SPEC, parse_user_goal
from tools.plan_search import PLAN_SEARCH_SPEC, plan_search
from understanding.understand_user_input import (
    UNDERSTAND_USER_INPUT_SPEC,
    understand_user_input,
)

Handler = Callable[..., Any]

FORBIDDEN_LOOP_TOOLS = {"browse_boss_jobs"}
REASONER_HIDDEN_TOOLS = {
    "reason_next_action",
    "parse_user_goal",
    "decide_next_action",
    "rank_jobs",
    "build_report",
    "execute_job_action",
}
PLATFORM_TOOL_ALIASES = {
    "mock_search_jobs",
    "mock_open_job",
    "search_boss_jobs",
    "open_boss_job",
    "search_liepin_jobs",
    "open_liepin_job",
    "search_51job_jobs",
    "open_51job_job",
}


class ToolRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}
        self._specs: dict[str, dict] = {}
        self.invocations: list[str] = []

    def register(self, spec: dict, handler: Handler) -> None:
        name = spec["name"]
        self._specs[name] = spec
        self._handlers[name] = handler

    def has(self, name: str) -> bool:
        return name in self._handlers

    def specs(self) -> list[dict]:
        return list(self._specs.values())

    def invoke(self, name: str, arguments: dict | None = None, **extra: Any) -> Any:
        if name not in self._handlers:
            raise ValueError(f"未知 Tool：{name}")
        self.invocations.append(name)
        arguments = dict(arguments or {})
        return self._handlers[name](arguments, **extra)


SUPPORTED_DATA_SOURCES = ("mock", "boss", "liepin", "job51")


def build_registry(*, data_source: str = "mock") -> ToolRegistry:
    """Agent Loop registry. Does not register browse_boss_jobs."""
    if data_source not in SUPPORTED_DATA_SOURCES:
        raise ValueError(f"unsupported data_source: {data_source}")
    registry = ToolRegistry()
    registry.register(PARSE_USER_GOAL_SPEC, _parse_user_goal)
    registry.register(REASON_NEXT_ACTION_SPEC, _reason_next_action)
    registry.register(UNDERSTAND_USER_INPUT_SPEC, _understand_user_input)
    registry.register(ANALYZE_CANDIDATE_SPEC, _analyze_candidate)
    registry.register(ANALYZE_JOB_SPEC, _analyze_job)
    registry.register(INTERPRET_JOB_ACTIONS_SPEC, _interpret_job_actions)
    registry.register(MATCH_JOB_SPEC, _match_job)
    registry.register(PLAN_SEARCH_SPEC, _plan_search)
    registry.register(SEARCH_JOBS_SPEC, _bound(search_jobs, data_source))
    registry.register(OPEN_JOB_SPEC, _bound(open_job, data_source))
    registry.register(INSPECT_JOB_SPEC, _bound(inspect_job, data_source))
    registry.register(INSPECT_APPLICATION_STATE_SPEC, _bound(inspect_application_state, data_source))
    registry.register(EXECUTE_ACTION_SPEC, _bound(execute_action, data_source))
    registry.register(EXECUTE_JOB_ACTION_SPEC, _execute_job_action)
    registry.register(BIND_TASK_SPEC, _passthrough(bind_task_tool))
    registry.register(SKIP_JOB_SPEC, _passthrough(skip_job_tool))
    registry.register(STOP_TASK_SPEC, _passthrough(stop_task_tool))
    registry.register(HYDRATE_JOB_REFERENCE_SPEC, _passthrough(hydrate_job_reference_tool))
    return registry


def available_tool_contracts(registry: ToolRegistry) -> list[dict]:
    """Tool contracts the Reasoner may choose. Hidden/forbidden tools are omitted."""
    contracts: list[dict] = []
    for spec in registry.specs():
        name = spec.get("name")
        if name in FORBIDDEN_LOOP_TOOLS or name in REASONER_HIDDEN_TOOLS or name in PLATFORM_TOOL_ALIASES:
            continue
        contracts.append(
            {
                "name": spec.get("name"),
                "description": spec.get("description"),
                "parameters": spec.get("parameters"),
                "output": spec.get("returns") or spec.get("output") or spec.get("output_description"),
                "allowed_in_loop": True,
            }
        )
    return contracts


def _llm(extra: dict) -> Any:
    return extra.get("llm_provider")


def _reason_next_action(arguments: dict, **extra: Any) -> dict:
    return reason_next_action(arguments, llm_provider=_llm(extra))


def _parse_user_goal(arguments: dict, **extra: Any) -> dict:
    goal = arguments.get("goal")
    if goal is None:
        goal = arguments.get("text")
    return parse_user_goal(goal, llm_provider=_llm(extra))


def _understand_user_input(arguments: dict, **extra: Any) -> dict:
    message = arguments.get("message")
    if message is None:
        message = arguments.get("text")
    if message is None:
        message = arguments.get("goal")
    return understand_user_input(
        message,
        arguments.get("attachments"),
        candidate_profile=arguments.get("candidate_profile"),
        session_context=arguments.get("session_context"),
        llm_provider=_llm(extra),
    )


def _analyze_candidate(arguments: dict, **extra: Any) -> dict:
    resume = arguments.get("resume")
    if resume is None:
        resume = arguments.get("text")
    return analyze_candidate(
        resume,
        llm_provider=_llm(extra),
        existing_profile=arguments.get("existing_profile"),
        existing_memory=arguments.get("existing_memory"),
    )


def _analyze_job(arguments: dict, **extra: Any) -> dict:
    return analyze_job(arguments.get("job"), llm_provider=_llm(extra))


def _interpret_job_actions(arguments: dict, **extra: Any) -> dict:
    page_context = arguments.get("page_context")
    if page_context is None:
        page_context = arguments
    return interpret_job_actions(page_context, llm_provider=_llm(extra))


def _match_job(arguments: dict, **extra: Any) -> dict:
    return match_job(
        arguments.get("candidate_profile"),
        arguments.get("job_profile"),
        llm_provider=_llm(extra),
        candidate_context=arguments.get("candidate_context"),
    )


def _plan_search(arguments: dict, **extra: Any) -> dict:
    return plan_search(arguments, llm_provider=_llm(extra))


def _passthrough(handler: Handler) -> Handler:
    def _call(arguments: dict, **extra: Any) -> Any:
        return handler(arguments, **extra)

    return _call


def _bound(handler: Handler, data_source: str) -> Handler:
    def _call(arguments: dict, **extra: Any) -> Any:
        extra.setdefault("data_source", data_source)
        return handler(arguments, **extra)

    return _call


def _execute_job_action(arguments: dict, **extra: Any) -> dict:
    return execute_job_action(arguments, **extra)
