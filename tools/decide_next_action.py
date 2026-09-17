"""Agent decision: what to do next with the current job. Not a score threshold."""

from __future__ import annotations

import json
from typing import Any

from llm.provider import LLMProvider, get_llm_provider

DECIDE_NEXT_ACTION_SPEC = {
    "name": "decide_next_action",
    "description": (
        "Decide the Agent's next action for the current job inside a JobSearchTask. "
        "Uses already-structured UserGoal, CandidateContext, JobProfile, and MatchResult. "
        "Does not search, open, analyze, or match. Does not use a score threshold."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "user_goal": {"type": "object"},
            "candidate_context": {"type": "object"},
            "job_profile": {"type": "object"},
            "match_result": {"type": "object"},
            "job_listing": {"type": "object"},
            "preferences": {"type": "array", "items": {"type": "string"}},
            "constraints": {"type": "object"},
            "application_evidence": {"type": "string"},
            "task_status": {"type": "string"},
            "explored_summary": {"type": "object"},
        },
        "required": ["match_result"],
    },
}

ALLOWED_NEXT_ACTIONS = {
    "REJECT_JOB",
    "SURFACE_TO_USER",
    "CONTINUE_EXPLORING",
    "COMPLETE_TASK",
}

ALLOWED_STATUS = {
    "ok",
    "insufficient_data",
    "llm_unavailable",
    "llm_error",
    "analysis_failed",
}

DECIDE_NEXT_ACTION_SYSTEM_PROMPT = """你是求职 Agent 的下一步决策器。根据已经结构化的任务上下文，决定对当前职位下一步做什么。

你不是打分器。不要把 MatchResult.recommendation / overall_fit 当成唯一门槛。
也不要输出搜索关键词、DOM 操作或投递点击步骤。

必须综合考虑：
- UserGoal（目标职位、城市、薪资、排除公司）
- CandidateContext（长期画像 + 本次补充，不要改写长期画像）
- JobProfile（这个职位是什么）
- MatchResult（direct / transferable / missing / insufficient_evidence，以及 gaps、risks）
- preferences 与 constraints
- application_evidence（已经申请通常不应再打扰用户）
- 当前任务探索状态

next_action 只能是：
- REJECT_JOB：不值得占用用户注意力，Agent 内部淘汰后继续探索
- SURFACE_TO_USER：值得让用户决定（投递或跳过）
- CONTINUE_EXPLORING：证据不足或还不应打扰用户，继续探索其他职位
- COMPLETE_TASK：当前任务已经没有必要继续探索

示例（不是固定规则）：
- 匹配看起来高，但薪资/城市明显不符合目标 → 可以 REJECT_JOB
- 分数中等，但核心能力高度可迁移且目标契合 → 可以 SURFACE_TO_USER
- 已经申请 → 通常不要 SURFACE_TO_USER
- 明显不满足核心硬约束 → REJECT_JOB
- 证据不足 → CONTINUE_EXPLORING 或 REJECT_JOB

只返回一个 JSON 对象，不要 Markdown，不要解释。
不要输出 analysis_status、error（由程序填写）。

返回字段：
next_action, rationale
"""


class DecideNextActionSchemaError(ValueError):
    """Raised when JobSearchDecision JSON does not satisfy field rules."""


def empty_decision() -> dict:
    return {
        "analysis_status": None,
        "error": None,
        "next_action": None,
        "rationale": None,
    }


def status_decision(status: str, error: str | None) -> dict:
    if status not in ALLOWED_STATUS:
        raise DecideNextActionSchemaError(f"illegal analysis_status: {status}")
    result = empty_decision()
    result["analysis_status"] = status
    result["error"] = error
    return result


def decide_next_action(payload: Any, llm_provider: LLMProvider | None = None) -> dict:
    """LLM JSON → schema-checked next action. Fail closed. Never threshold on score."""
    if llm_provider is None:
        llm_provider = get_llm_provider()
    if llm_provider is None:
        return status_decision("llm_unavailable", "llm provider is not configured")

    try:
        user_payload = _user_payload(payload)
    except DecideNextActionSchemaError as exc:
        return status_decision("analysis_failed", str(exc))

    try:
        raw = llm_provider.complete_json(
            system=DECIDE_NEXT_ACTION_SYSTEM_PROMPT,
            user=json.dumps(user_payload, ensure_ascii=False),
        )
    except TimeoutError as exc:
        return status_decision("llm_error", str(exc) or "llm request timed out")
    except Exception as exc:
        message = str(exc) or "llm request failed"
        lowered = message.lower()
        if "timed out" in lowered or "timeout" in lowered:
            return status_decision("llm_error", message)
        if "not configured" in lowered:
            return status_decision("llm_unavailable", message)
        return status_decision("llm_error", message)

    try:
        fields = validate_decision_payload(raw)
    except DecideNextActionSchemaError as exc:
        return status_decision("analysis_failed", str(exc))

    result = empty_decision()
    result.update(fields)
    result["analysis_status"] = "ok"
    result["error"] = None
    return result


def validate_decision_payload(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise DecideNextActionSchemaError("LLM JSON must be an object")
    action = raw.get("next_action")
    if not isinstance(action, str) or action.strip() not in ALLOWED_NEXT_ACTIONS:
        raise DecideNextActionSchemaError(
            f"next_action must be one of {sorted(ALLOWED_NEXT_ACTIONS)}"
        )
    rationale = raw.get("rationale")
    if rationale is not None and not isinstance(rationale, str):
        raise DecideNextActionSchemaError("rationale must be a string or null")
    text = rationale.strip() if isinstance(rationale, str) else None
    return {
        "next_action": action.strip(),
        "rationale": text or None,
    }


def _user_payload(payload: Any) -> dict:
    if not isinstance(payload, dict):
        raise DecideNextActionSchemaError("decide_next_action payload must be an object")
    match_result = payload.get("match_result")
    if not isinstance(match_result, dict):
        raise DecideNextActionSchemaError("match_result is required")
    return {
        "user_goal": payload.get("user_goal") if isinstance(payload.get("user_goal"), dict) else {},
        "candidate_context": payload.get("candidate_context")
        if isinstance(payload.get("candidate_context"), dict)
        else None,
        "job_profile": payload.get("job_profile") if isinstance(payload.get("job_profile"), dict) else None,
        "match_result": match_result,
        "job_listing": payload.get("job_listing") if isinstance(payload.get("job_listing"), dict) else {},
        "preferences": payload.get("preferences") if isinstance(payload.get("preferences"), list) else [],
        "constraints": payload.get("constraints") if isinstance(payload.get("constraints"), dict) else {},
        "application_evidence": payload.get("application_evidence"),
        "task_status": payload.get("task_status"),
        "explored_summary": payload.get("explored_summary")
        if isinstance(payload.get("explored_summary"), dict)
        else {},
    }
