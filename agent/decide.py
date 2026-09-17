"""Ask the Agent Reasoner for the next action. No status→tool table."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.reasoner_context import build_reasoner_payload
from agent.state import LLM_TOOLS, TERMINAL_STATUSES, AgentState
from agent.validate_action import validate_reasoner_action
from rules.quota import note_llm_call
from tools.reason_next_action import reason_next_action
from tools.registry import ToolRegistry

REASONER_ACTION_TYPES = {"tool", "ask_user", "finish"}
PROGRAM_ACTION_TYPES = {"rejected", "fail"}


@dataclass
class Action:
    """Reasoner or Program step. action_type is the discriminator; tool_name is never reused as one."""

    action_type: str
    tool_name: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    question: str | None = None
    intent: str | None = None
    job_key: str | None = None
    reason: str | None = None
    error_code: str | None = None
    raw_reasoner_output: Any = None


def tool_action(
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    *,
    reason: str | None = None,
    error_code: str | None = None,
    raw_reasoner_output: Any = None,
) -> Action:
    return Action(
        action_type="tool",
        tool_name=tool_name,
        arguments=arguments or {},
        reason=reason,
        error_code=error_code,
        raw_reasoner_output=raw_reasoner_output,
    )


def decide(state: AgentState, registry: ToolRegistry | None = None, llm_provider=None) -> Action:
    if state.session.status in TERMINAL_STATUSES:
        raise RuntimeError(f"decide called on terminal status {state.session.status}")
    if registry is None:
        return Action(
            action_type="fail",
            arguments={"error": "tool registry is required"},
            error_code="reasoner_failed",
        )

    payload = build_reasoner_payload(state, registry)
    decision = reason_next_action(payload, llm_provider=llm_provider)
    calls = int((decision or {}).get("llm_calls") or 1)
    for _ in range(max(calls, 1)):
        note_llm_call(state)
    raw = (decision or {}).get("raw_reasoner_output") if isinstance(decision, dict) else None
    if not isinstance(decision, dict) or decision.get("analysis_status") != "ok":
        status = str((decision or {}).get("analysis_status") or "reasoner_failed")
        error_code = (
            status
            if status
            in {
                "llm_unavailable",
                "llm_error",
                "llm_invalid_json",
                "analysis_failed",
                "insufficient_data",
            }
            else "reasoner_failed"
        )
        return Action(
            action_type="fail",
            arguments={
                "error": (decision or {}).get("error") or "reason_next_action failed",
                "error_code": error_code,
                "raw_reasoner_output": raw,
            },
            error_code=error_code,
            raw_reasoner_output=raw,
        )

    checked = validate_reasoner_action(state, decision, registry)
    if not checked.get("ok"):
        return Action(
            action_type="rejected",
            arguments={
                "error": checked.get("error"),
                "attempted": {
                    "action_type": decision.get("action_type"),
                    "tool_name": decision.get("tool_name"),
                    "arguments": decision.get("arguments") or {},
                    "intent": decision.get("intent"),
                    "job_key": decision.get("job_key"),
                    "question": decision.get("question"),
                },
            },
            reason=str(checked.get("error") or "action rejected"),
            raw_reasoner_output=decision,
        )

    action_type = decision.get("action_type")
    if action_type == "ask_user":
        arguments = dict(decision.get("arguments") or {})
        question = decision.get("question")
        intent = decision.get("intent") or arguments.get("intent")
        job_key = decision.get("job_key") or arguments.get("job_key")
        arguments["question"] = question
        if intent:
            arguments["intent"] = intent
        if job_key:
            arguments["job_key"] = job_key
        if decision.get("error_code"):
            arguments["error_code"] = decision.get("error_code")
        return Action(
            action_type="ask_user",
            arguments=arguments,
            question=question,
            intent=intent if isinstance(intent, str) else None,
            job_key=job_key if isinstance(job_key, str) else None,
            reason=decision.get("reason"),
            error_code=decision.get("error_code"),
            raw_reasoner_output=decision,
        )
    if action_type == "finish":
        return Action(
            action_type="finish",
            arguments={"reason": decision.get("reason"), "error_code": decision.get("error_code")},
            reason=decision.get("reason"),
            error_code=decision.get("error_code"),
        )
    return Action(
        action_type="tool",
        tool_name=str(decision.get("tool_name")),
        arguments=dict(decision.get("arguments") or {}),
        reason=decision.get("reason"),
    )


def is_llm_tool(name: str | None) -> bool:
    return bool(name) and name in LLM_TOOLS
