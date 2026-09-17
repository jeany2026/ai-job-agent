"""Map existing AgentState / report fields for the web UI. No new matching."""

from __future__ import annotations

from typing import Any

from agent.state import AgentState, JobRecord
from api.profile_view import empty_profile_view, load_profile_view, serialize_profile_record

HUMAN_GATE_LABELS = {
    "login_required": "请在 Agent 打开的浏览器窗口中登录招聘网站，然后再继续。",
    "captcha": "招聘网站出现验证码或人机验证，请在 Agent 打开的浏览器窗口中完成后继续。",
    "access_blocked": "访问被阻断，请在 Agent 打开的浏览器窗口中按页面提示处理后继续。",
    "browser_unavailable": "Agent 招聘浏览器未能启动或未就绪。请重试；你的日常 Edge 不受影响，也无需手动开调试。",
    "cdp_unavailable": "Agent 招聘浏览器已启动，但调试连接失败。请重试；无需手动开日常 Edge 或勾选 inspect。",
    "site_blocked_client": "当前访问环境被网站拦截，请在 Agent 打开的浏览器窗口中按提示处理后继续。",
}

PROGRESS_MESSAGES = {
    "PARSING_GOAL": "正在理解你的需求……",
    "GATHERING_CANDIDATE": "正在整理候选人背景……",
    "PLANNING": "正在搜索职位……",
    "SEARCHING": "正在搜索职位……",
    "ENRICHING_JOBS": "正在打开职位详情……",
    "ANALYZING": "正在分析职位要求……",
    "MATCHING": "正在分析匹配度……",
    "FILTERING": "正在整理结果……",
    "RANKING": "正在整理结果……",
    "REPORTING": "正在整理结果……",
    "DECIDING": "正在判断下一步……",
    "EXECUTING": "正在执行你授权的动作……",
    "WAITING_USER": "找到一个值得你决定的职位。",
    "DONE": "已经整理好这次的结果。",
    "NEEDS_HUMAN": "需要你处理一下，我才能继续。",
    "FAILED": "这次没有完成。",
}

INSUFFICIENT_CANDIDATE_MESSAGE = (
    "当前这次匹配还缺少能判断你与职位关系的经历信息。"
    "搜索可以先按你的目标进行；你也可以补充经历、简历或项目资料，方便后续匹配。"
)
MISSING_CANDIDATE_AFTER_OPEN_MESSAGE = (
    "已经打开了职位详情，但还没有你的简历或经历信息，无法完成匹配推荐。"
    "请补充简历、项目说明或核心经历后再继续。"
)
MAX_STEPS_MESSAGE = (
    "这一轮步骤用尽，还没有完成推荐。"
    "请重试继续探索职位，或告诉我你更关注的方向。"
)
LLM_QUOTA_EXHAUSTED_MESSAGE = (
    "模型调用配额已用尽，分析和匹配没法继续。"
    "若还有未打开的职位，请重试让我继续打开详情。"
)
REPEATED_ACTION_REJECTED_MESSAGE = (
    "连续多步动作都被拦住了，这一轮先停下来。"
    "请重试继续探索，或告诉我你更想怎么继续。"
)
GOAL_INCOMPLETE_MESSAGE = "我已经记下你提供的经历。还需要你告诉我想找什么方向的工作，我才能开始搜索。"
SCHEMA_ERROR_MESSAGE = "有一条候选人信息没能规范化，已跳过该字段，不影响这次求职任务。"
PARSER_ERROR_MESSAGE = "附件没能正确读出文字，请换一份可读的简历或直接描述经历。"
LLM_UNAVAILABLE_MESSAGE = (
    "模型服务暂时不可用，请稍后点「重试」。这不是登录或人工验证问题。"
)
LLM_ERROR_MESSAGE = (
    "模型服务暂时出错，请稍后点「重试」。这不是登录或人工验证问题。"
)
LLM_TIMEOUT_MESSAGE = "大模型调用超时，请稍后点「重试」。这不是登录或人工验证问题。"
LLM_INVALID_JSON_MESSAGE = (
    "模型返回内容格式无效（不是可用的 JSON），请点「重试」。服务本身可达，这不是登录问题。"
)


def progress_message(status: str | None) -> str:
    if not status:
        return "正在理解你的需求……"
    return PROGRESS_MESSAGES.get(status, "正在处理……")


def _done_outcome_view(state: AgentState, *, recommended: list) -> dict[str, Any]:
    """Honest DONE copy for the UI. Does not invent recommendations."""
    stats = dict(state.search.stats or {})
    searches = int(stats.get("searches") or 0)
    listed = int(stats.get("listed") or len(state.jobs or {}))
    opened = int(stats.get("opened") or 0)
    recommended_count = len(recommended or [])
    if recommended_count > 0:
        return {
            "message": progress_message("DONE"),
            "outcome_kind": "has_recommendations",
            "result_title": "推荐结果",
        }
    if searches == 0 and listed == 0:
        return {
            "message": "这一轮还没有开始搜索职位，所以没有推荐结果。可以再说一次目标，让我重新搜索。",
            "outcome_kind": "no_search",
            "result_title": "还没开始搜索",
        }
    if listed > 0 and opened == 0:
        return {
            "message": f"这一轮搜到约 {listed} 个职位，但还没有打开详情并选出推荐。可以说「继续找」让我接着看。",
            "outcome_kind": "listed_not_opened",
            "result_title": "已搜索，尚未推荐",
        }
    return {
        "message": (
            f"这一轮处理过约 {listed} 个职位（打开 {opened} 个），"
            "但还没有选出值得推荐给你的。可以说「继续找」。"
        ),
        "outcome_kind": "searched_no_recommendation",
        "result_title": "暂无推荐职位",
    }


def serialize_agent_state(state: AgentState, *, profile_dir: str | None = None) -> dict[str, Any]:
    status = state.session.status
    output = state.output if isinstance(state.output, dict) else {}
    profile_view = _profile_view_for(state, profile_dir=profile_dir)
    persisted = bool(
        state.candidate and getattr(state.candidate, "profile_persisted_this_turn", False)
    )
    payload: dict[str, Any] = {
        "ok": status == "DONE",
        "status": status,
        "session_id": state.session.session_id,
        "human_gate": state.human_gate,
        "errors": list(state.errors or []),
        "recommended": [_recommend_item(state, item) for item in output.get("recommended") or []],
        "excluded": list(output.get("excluded") or []),
        "stats": dict(state.search.stats or {}),
        "goal": state.goal,
        "candidate_id": state.candidate.candidate_id if state.candidate else None,
        "profile_version": state.candidate.profile_version if state.candidate else None,
        "profile": profile_view,
        "profile_updated": persisted,
        "stop_reason": state.search.stop_reason,
        "error_code": None,
        "message": None,
        "clarification_needed": False,
        "follow_up": bool(output.get("follow_up")),
        "reference_resolution": output.get("reference_resolution") or state.reference_resolution,
        "resolved_job_key": output.get("resolved_job_key") or state.follow_up_of,
        "needs_user_decision": bool(output.get("needs_user_decision") or status == "WAITING_USER"),
        "waiting_job": output.get("waiting_job"),
        "task": output.get("task") or state.task_view,
        "task_id": (output.get("task") or state.task_view or {}).get("task_id") if isinstance(output.get("task") or state.task_view, dict) else state.job_search_task_id,
        "task_status": (output.get("task") or state.task_view or {}).get("task_status") if isinstance(output.get("task") or state.task_view, dict) else None,
    }
    if status == "RUNNING":
        payload["ok"] = True
        payload["message"] = progress_message(getattr(state, "progress", None))
    if status == "DONE":
        done_view = _done_outcome_view(state, recommended=payload["recommended"])
        payload.update(done_view)
    if status == "WAITING_USER":
        payload["ok"] = True
        payload["message"] = output.get("message") or progress_message("WAITING_USER")
        if not payload.get("waiting_job") and payload.get("recommended"):
            payload["waiting_job"] = payload["recommended"][0]
        payload["needs_user_decision"] = True
    payload["candidate_memory"] = getattr(state.candidate, "memory", None)
    if output.get("clarification_needed") or output.get("error_code") in {
        "reference_unresolved",
        "clarification_needed",
        "GOAL_INCOMPLETE",
    }:
        payload["ok"] = False
        payload["error_code"] = output.get("error_code") or "clarification_needed"
        payload["clarification_needed"] = True
        payload["message"] = output.get("message") or "请再说明一下。"
    if status == "NEEDS_HUMAN":
        payload["ok"] = False
        payload["error_code"] = (state.human_gate or {}).get("reason") or "needs_human"
        payload["message"] = _human_gate_message(state.human_gate)
    elif status == "FAILED":
        payload["ok"] = False
        payload["error_code"], payload["message"] = _failed_error(state)
    return payload


def _profile_view_for(state: AgentState, *, profile_dir: str | None) -> dict[str, Any]:
    candidate_id = (state.candidate.candidate_id if state.candidate else None) or "default"
    if profile_dir:
        view = load_profile_view(profile_dir, candidate_id)
        if view.get("exists"):
            return view
    if state.candidate and isinstance(state.candidate.profile, dict) and state.candidate.profile_status == "ok":
        return serialize_profile_record(
            {
                "candidate_id": candidate_id,
                "profile_version": state.candidate.profile_version,
                "updated_at": None,
                "profile": state.candidate.profile,
            }
        )
    return empty_profile_view(candidate_id=candidate_id)


def _recommend_item(state: AgentState, item: dict) -> dict:
    record = _record_for(state, item)
    job = _job_fields(record)
    match = (record.match_result if record else None) or {}
    assessments = item.get("capability_assessments")
    if not assessments:
        assessments = match.get("capability_assessments") or []
    return {
        "job_key": item.get("job_key"),
        "platform": item.get("platform") or job.get("platform"),
        "job_id": item.get("job_id") or job.get("job_id"),
        "job_title": item.get("job_title") or job.get("job_title"),
        "company_name": item.get("company_name") or job.get("company_name"),
        "job_url": item.get("job_url") or job.get("job_url"),
        "recommendation": item.get("recommendation") if "recommendation" in item else match.get("recommendation"),
        "overall_fit": item.get("overall_fit") if "overall_fit" in item else match.get("overall_fit"),
        "hard_requirements_met": item.get("hard_requirements_met")
        if "hard_requirements_met" in item
        else match.get("hard_requirements_met"),
        "rationale": item.get("rationale") if "rationale" in item else match.get("rationale"),
        "capability_assessments": assessments,
        "risks": match.get("risks") or [],
        "knowledge_gaps": match.get("knowledge_gaps") or [],
        "matched_capabilities": [
            entry for entry in assessments if isinstance(entry, dict) and entry.get("outcome") == "direct"
        ],
        "transferable_capabilities": [
            entry for entry in assessments if isinstance(entry, dict) and entry.get("outcome") == "transferable"
        ],
    }


def _record_for(state: AgentState, item: dict) -> JobRecord | None:
    key = item.get("job_key")
    if key and key in state.jobs:
        return state.jobs[key]
    job_id = item.get("job_id")
    if not job_id:
        return None
    for record in state.jobs.values():
        job = _job_fields(record)
        if job.get("job_id") == job_id:
            return record
    return None


def _job_fields(record: JobRecord | None) -> dict:
    if record is None:
        return {}
    return record.opened or record.listed or {}


def _human_gate_message(gate: dict | None) -> str:
    from agent.human_gate import INFRA_GATE_REASONS, USER_ACTION_GATE_REASONS

    if not gate:
        return "需要人工处理后才能继续。"
    reason = str(gate.get("reason") or "")
    detail = HUMAN_GATE_LABELS.get(reason) if reason else None
    existing = str(gate.get("message") or "").strip()
    if reason in INFRA_GATE_REASONS:
        prefix = "招聘浏览环境暂不可用。"
    elif reason in USER_ACTION_GATE_REASONS:
        prefix = "需要你在 Agent 浏览器中处理一下才能继续。"
    else:
        prefix = "需要人工处理后才能继续。"
    if detail and existing and existing != detail and reason not in INFRA_GATE_REASONS:
        # Prefer stable product copy for infra; keep tool detail for user-action gates when useful.
        if len(existing) < 180 and existing not in detail:
            return f"{prefix} {detail}（{existing}）"
        return f"{prefix} {detail}"
    if detail:
        return f"{prefix} {detail}"
    if existing:
        return f"{prefix} {existing}"
    return prefix


def _failed_error(state: AgentState) -> tuple[str, str]:
    errors = list(state.errors or [])
    kinds = [str(item.get("kind") or "") for item in errors]
    messages = [str(item.get("message") or "") for item in errors]
    joined = " ".join(messages)
    lowered = joined.lower()
    profile = state.candidate.profile if state.candidate else None
    profile_status = ""
    if isinstance(profile, dict):
        profile_status = str(profile.get("analysis_status") or "")
    output = state.output if isinstance(state.output, dict) else {}
    output_code = str(output.get("error_code") or "")
    output_message = str(output.get("message") or "").strip()
    stats = dict(state.search.stats or {})
    opened = int(stats.get("opened") or 0)

    if (
        output_code in {"missing_candidate_material", "insufficient_candidate_material"}
        or "missing_candidate_material" in kinds
        or "insufficient_candidate_material" in kinds
        or "missing candidate material" in lowered
        or "insufficient_candidate_material" in lowered
    ):
        return (
            "insufficient_candidate_material",
            output_message or MISSING_CANDIDATE_AFTER_OPEN_MESSAGE,
        )

    if output_code == "llm_quota_exhausted" or "llm_quota_exhausted" in kinds:
        return "llm_quota_exhausted", output_message or LLM_QUOTA_EXHAUSTED_MESSAGE
    if output_code == "repeated_action_rejected" or "repeated_action_rejected" in kinds:
        return "repeated_action_rejected", output_message or REPEATED_ACTION_REJECTED_MESSAGE
    if output_code == "repeated_no_progress" or "repeated_no_progress" in kinds:
        return (
            "repeated_no_progress",
            output_message
            or (
                "同一动作反复执行且没有产生新的职位进展。"
                "请换查询条件、打开/分析已有职位、说明更多要求，或结束本轮。"
            ),
        )

    if profile_status == "llm_unavailable" or "llm provider is not configured" in lowered:
        return "llm_unavailable", LLM_UNAVAILABLE_MESSAGE
    if (
        output_code == "llm_invalid_json"
        or profile_status == "llm_invalid_json"
        or "llm_invalid_json" in kinds
        or "llm response is not valid json" in lowered
        or ("expecting" in lowered and "delimiter" in lowered)
        or ("jsondecodeerror" in lowered)
        or ("not valid json" in lowered)
    ):
        return "llm_invalid_json", LLM_INVALID_JSON_MESSAGE
    if (
        output_code == "llm_error"
        or profile_status == "llm_error"
        or "llm_error" in lowered
        or "llm http" in lowered
        or "timed out" in lowered
        or "timeout" in lowered
        or "llm request failed" in lowered
        or "llm request timed out" in lowered
    ):
        if "timed out" in lowered or "timeout" in lowered:
            return "llm_error", LLM_TIMEOUT_MESSAGE
        # Profile/tool paths historically stash parse failures under llm_error —
        # still tell the truth when the message is clearly invalid JSON.
        if "not valid json" in lowered or ("expecting" in lowered and "delimiter" in lowered):
            return "llm_invalid_json", LLM_INVALID_JSON_MESSAGE
        return "llm_error", LLM_ERROR_MESSAGE
    if "goal_incomplete" in lowered or output_code == "GOAL_INCOMPLETE":
        return "GOAL_INCOMPLETE", GOAL_INCOMPLETE_MESSAGE
    if "attachment_processing_failed" in lowered or "PARSER_ERROR" in joined:
        return "PARSER_ERROR", PARSER_ERROR_MESSAGE
    if "SCHEMA_ERROR" in joined or "schema_error" in kinds:
        return "SCHEMA_ERROR", SCHEMA_ERROR_MESSAGE
    if "TOOL_ERROR" in joined or "tool_error" in kinds:
        return "TOOL_ERROR", "有一个工具没有执行成功，请稍后再试。"
    if "insufficient_candidate" in lowered or "INSUFFICIENT_INFORMATION" in joined:
        return "INSUFFICIENT_INFORMATION", INSUFFICIENT_CANDIDATE_MESSAGE
    if any(kind == "candidate" for kind in kinds):
        return "TOOL_ERROR", "整理候选人事实时出错，已跳过异常字段。这不代表你没有提供候选人信息。"
    if "max_steps" in lowered or "loop exceeded" in lowered:
        from agent.validate_action import _world_has_candidate_material

        if opened > 0 and not _world_has_candidate_material(state):
            return "missing_candidate_material", MISSING_CANDIDATE_AFTER_OPEN_MESSAGE
        return "max_steps", output_message or MAX_STEPS_MESSAGE
    return "agent_failed", "这次没有完成，请稍后再试，或换一种方式告诉我你的目标。"
