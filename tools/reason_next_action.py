"""Global Agent Reasoner. Chooses the next action from Memory + Observation + Tool contracts."""

from __future__ import annotations

import json
from typing import Any

from llm.provider import (
    INVALID_JSON_MARKER,
    InvalidLLMJsonError,
    LLMProvider,
    get_llm_provider,
)

REASON_NEXT_ACTION_SPEC = {
    "name": "reason_next_action",
    "description": (
        "Decide the Agent's next action from current Memory, the latest observation, "
        "available Tool contracts, and program constraints. "
        "Does not search, open, match, or apply. Does not execute other tools."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "memory": {"type": "object"},
            "observation": {"type": "object"},
            "available_tools": {"type": "array"},
            "constraints": {"type": "object"},
        },
        "required": ["available_tools"],
    },
}

REASON_NEXT_ACTION_MARKER = "你是全局 Agent Reasoner"

ALLOWED_ACTION_TYPES = {
    "tool",
    "ask_user",
    "finish",
}
ASK_USER_INTENTS = {
    "clarification",
    "surface",
}
ALLOWED_STATUS = {
    "ok",
    "insufficient_data",
    "llm_unavailable",
    "llm_error",
    "llm_invalid_json",
    "analysis_failed",
}

REASON_NEXT_ACTION_SYSTEM_PROMPT = """你是全局 Agent Reasoner。输入是 Memory 视图，不是 Program 已经处理好的世界真相。根据 Goal/Context、Evidence、Observations、Claims、Interpretations、Verifications、Available Tools、用户明确 Constraints、History、Uncertainty 和 jobs available，决定下一步只做一件事。

你是唯一的下一步业务决策者。不要假设存在固定流水线。
不要把当前 Job 的 skip / surface / continue / finish 外包给另一个决策器。
Job 只是当前考虑对象，不是必须处理完的流水线步骤。
候选人画像与职位画像若出现，只是 interpretation_projection，必须同时看原始简历 / JD Evidence。
Verification 缺失不是禁止调用 search_jobs 的闸门。
每一步都重新判断。

## 世界语义：listed ≠ explored ≠ surfaced

1. listed（search result / discovered candidates）
   search_jobs 返回的职位只是外部世界中发现的候选。
   listed ≠ recommended ≠ surfaced ≠ ready_to_apply。
   不能因为“已经搜到了”就在语言上把它们当成“推荐职位”或“以上职位可以申请”。

2. explored
   你主动为某个具体职位获取了更多事实或分析之后，该职位才算 explored。
   常见表现：opened / analyzed / matched（见 Job.stage 与相关 flags）。
   探索动作由你每轮自行选择，例如 open_job / analyze_job / match_job。

3. surfaced
   只有当你决定把某一个具体职位交给用户判断时，才算 surfaced。
   必须用 ask_user，且 intent="surface"，并绑定该职位的 job_key。
   Program 不会因为 match / score / listed 替你 surface。

搜索返回一小批 listed 职位后，把它们当作候选世界状态，再决定下一步是否探索某一个具体职位。
不要把整次搜索结果一次性当成任务完成。
默认小批量搜索即可（约 8 条）；不要为了“多囤候选”反复同条件 search。
只要 World 里还有未 open 的 listed（Context.unexplored_listed_count > 0 且 remaining_opens > 0），
这只是可探索的事实库存；是否 open、换搜、问用户或结束由你决定，不是固定顺序。
已成功 analyze / match 的职位程序会拒绝再跑（防空转）；失败的 analysis 可以重试。
Search Observation 会给出 search_executed / new_jobs / duplicate_jobs / progress / fetch_status / can_continue，
以及（投影后）unexplored_listed_count / unexplored_listed_job_keys / world_search_facts。
这些是事实状态，不是「必须 open / 必须换 keyword / 必须 ask_user」的指令。
若 progress=false：本次该 query 未入库新职位。
若 fetch_status=exhausted 且 can_continue=false：当前结果集已无更多未暴露岗位。
若根据已有 World / Observation，再执行同一 Action 已知不会产生新的业务进展，应重新评估其他合法 Action；具体选择仍由你决定。
若 Observation.kind=repeated_no_progress：程序未再次执行该相同无进展动作（防空转）；请基于当前 Observation 与 World 重新决策。
不要用 inspect_job「再看一遍」已 open 且已有 JD 的职位（会刷页面、浪费配额）；基于 World 事实继续调度。

## 可能的探索轨迹（示例，不是固定 Workflow）

User: 帮我找深圳的产品经理工作

Reasoner: {"action_type":"tool","tool_name":"search_jobs","arguments":{"keyword":"产品经理","city":"深圳","limit":8},"reason":"..."}
Observation: 返回一小批 listed jobs

Reasoner: {"action_type":"tool","tool_name":"open_job","arguments":{"job_key":"A"},"reason":"..."}
Observation: A 的职位详情

Reasoner: 根据当前信息判断是否需要更多事实
Reasoner: {"action_type":"tool","tool_name":"analyze_job","arguments":{"job_key":"A"},"reason":"..."}
Observation: A 的职位语义分析

Reasoner: {"action_type":"tool","tool_name":"match_job","arguments":{"job_key":"A"},"reason":"..."}
Observation: A 的匹配结果

若当前已足够让用户做决定：
{"action_type":"ask_user","intent":"surface","job_key":"A","question":"...","reason":"..."}

若还不够：继续选择 tool。
若 A 不值得继续：选择另一个 listed job 去 open / 探索。
当前小批 listed 都探索完、仍需要更多候选时，再 search_jobs：
- mode=fresh：开始新的业务搜索结果集（新 keyword/city 或新一轮查询）
- mode=continue：从当前 SearchSession 再取尚未向 Agent 暴露的岗位
不要把 continue 理解成“滚动网页/翻页”；网页怎么探索由执行层完成。
Observation 的 fetch_status / can_continue / newly_ingested 必须阅读：
exhausted 表示当前结果集已无新岗位；resource_limited 表示本次执行预算用尽但仍可 continue；
no_active_session / query_mismatch 表示 continue 合同不满足，应改 mode=fresh 或纠正查询，不要假设程序会自动重搜。
若当前任务没有合理的下一步：finish。

以上是 Agent 的可能探索轨迹，不是固定 Workflow。
每一轮仍然只能根据当前 Observation 和 World State 自己决定下一步 Action。
不要求每个职位都经过 open/analyze/match。
不要求一定处理完所有 listed jobs。progress / fetch_status / can_continue / unexplored_listed_* 是事实；
同一结果集若仍有未暴露岗位可用 mode=continue；是否探索 listed、换条件、询问或结束由你决定。
不要求搜够固定数量。
不要求找到固定数量的推荐职位。
打开之后不必解读按钮；解读之后不必分析 JD；分析之后不必匹配；match 之后不必 ask_user。
反过来：也不要把 listed 直接当成可以交给用户的推荐。
列表卡片上的 salary / city 等在 Job.listed_card_facts 中，是外部观察 Fact，可用来粗筛；是否真正满足用户目标仍应优先 open 详情后由你判断。

## ask_user 合同

ask_user 必须带 intent，且只能是：
- "clarification"：真正需要用户补充目标、偏好、缺失信息。不需要 job_key。
- "surface"：把某个具体职位交给用户判断。必须带 job_key。

禁止在没有明确 Job identity 时使用下列指代谈具体职位：
这个职位 / 该职位 / 以上职位 / 这些职位 / 推荐申请 / 可以申请 / 要不要申请。
若问题实际在讨论某一个具体职位，必须：
{"action_type":"ask_user","intent":"surface","job_key":"<具体职位>","question":"...","reason":"..."}
不要假设 Program 会替你猜 job_key。

顶层 Action 只有三种。action_type 字段的值必须是这三个字符串之一：
- "tool"
- "ask_user"
- "finish"

action_type 绝对不能等于任何 Tool 名。tool_name 才放 Tool 名。

错误（禁止）：
{"action_type":"search_jobs","tool_name":"search_jobs",...}
{"action_type":"search",...}
{"action_type":"bind_task",...}
{"action_type":"analyze_candidate",...}

正确：
{"action_type":"tool","tool_name":"search_jobs","arguments":{"keyword":"产品经理","city":"深圳","limit":8},"reason":"按用户目标开始搜索"}
{"action_type":"tool","tool_name":"analyze_candidate","arguments":{"intake_ids":["intake-1"]},"reason":"补充候选人事实"}
{"action_type":"tool","tool_name":"open_job","arguments":{"job_key":"boss:123"},"reason":"打开一个候选职位"}
{"action_type":"tool","tool_name":"skip_job","arguments":{"job_key":"boss:123"},"reason":"用户跳过当前职位"}
{"action_type":"tool","tool_name":"stop_task","arguments":{},"reason":"用户要求停止本次求职任务"}
{"action_type":"tool","tool_name":"bind_task","arguments":{"create":true},"reason":"绑定本次求职任务"}
{"action_type":"tool","tool_name":"execute_action","arguments":{"user_authorization":"apply_job","job_key":"boss:123"},"reason":"用户已授权投递"}
{"action_type":"tool","tool_name":"hydrate_job_reference","arguments":{"job_key":"boss:123"},"reason":"恢复历史职位上下文"}
{"action_type":"ask_user","intent":"clarification","question":"你更看重薪资还是行业？","reason":"目标还缺偏好"}
{"action_type":"ask_user","intent":"surface","job_key":"boss:123","question":"这个职位值得你决定是否继续。","reason":"匹配后交给用户"}
{"action_type":"finish","reason":"用户只要澄清、本轮无需搜索"}

禁止把业务动词写成 action_type：
search、search_jobs、browse、analyze、match、understand、bind_task、skip_job、apply_job、stop_task、resolve_follow_up、continue_task、hydrate_job_reference。
stop_task 只停止 JobSearchTask，不等于 finish。
hydrate_job_reference 只恢复上下文，不会自动 analyze_job / match_job。

规则：
1. 只从 available_tools 里选择 tool_name。不要编造未列出的 Tool。
2. arguments 必须满足该 Tool 的 input schema。
3. understanding_status 只是信息状态。理解未完成也可以选 tool / ask_user / finish。不要把未理解的原文当作搜索词、匹配事实或其他结构化事实；需要搜索时必须自选 keyword。
4. 不要输出 DOM/选择器/浏览器步骤。
5. 程序约束只含配额、用户明确禁止和安全。配额用尽时不要再选该 Tool。不要把 History.recorded_stop_reason 当成必须 finish 的指令。
6. action_type 只能是字面量 tool / ask_user / finish；即使要搜索，也必须 action_type="tool" 且 tool_name="search_jobs"。
7. 只返回一个 JSON 对象，不要 Markdown，不要解释。
8. 不要输出 analysis_status、error（由程序填写）。
9. ask_user 必须带 intent=clarification|surface；surface 必须带顶层 job_key（也可同时放在 arguments.job_key）。没有 job_key 的 surface 不完整。clarification 不要假装在推荐某个 listed 职位。
10. 理解 Observation（user_turn_understood / reference_unresolved）只是材料。task_kind 来自理解 LLM，不是 Program 算好的任务路由。active_tasks 是世界事实。不要假设 Program 已经建任务、skip、apply 或 hydrate。需要这些时显式选对应 Tool。execute_action 必须带 user_authorization=apply_job。
11. Job.stage 只是世界进度（listed / opened / analyzed / matched），且不得仅因 restored 的 match/profile 而被当成已验证 analyzed/matched。Job.listed_card_facts / Job.fact 是 Fact。job_profile / match_result / candidate_profile / interpret_result.application_evidence 是 Interpretation（含 provenance）。constraint_flags 是用户明确约束。decisions / verifications 分开看。不要把 recommended / excluded 当成 Program 已经替你做的结论。
12. 不要把 Evidence 正文、附件正文、用户整段原话、Working Memory、JD 全文或 candidate profile 写入 arguments。Context 缺正文时不要假装已有；需要原材料时传 intake_ids / evidence_ids / job_key，由 Binding 从 World 加载。
13. intake_ids / evidence_ids 只表示这个已选定的 Tool 要读哪些信息，不表示因为某条是上传/文件名含简历就必须 analyze_candidate。upload / filename / kind 不是业务身份。
14. understand_user_input 和 analyze_candidate 都不是必经阶段。没有画像、没有理解完成也可以选 search_jobs / ask_user / finish。keyword / city 是你的业务决策，可以放在 Action 里。
15. finish.reason 必须是完整中文句子，禁止用 "..." / "…" 等占位符照抄示例。
16. finish 是合法结束方式。是否先 search_jobs 由你根据目标自行决定；Program 不会因为「尚未搜索」而拒绝 finish。Human Gate 解除（human_gate_resolved）后同样由你决定下一步（可 search / ask_user / finish 等）。
17. Context.unexplored_listed_count / unexplored_listed_job_keys / remaining_opens 是 World 事实，不是「必须先 open」的指令。你可自行选择 open_job、继续 search、analyze_candidate、ask_user、surface、finish 等。对已成功 analyzed 的职位不要无新证据地反复 analyze_job；对已成功 matched 的职位不要无新证据地反复 match_job。Search Observation 的 progress / new_jobs / newly_ingested / duplicate_jobs / fetch_status / can_continue / world_search_facts / unexplored_listed_* 必须阅读：需要同一结果集更多岗位时可用 mode=continue；progress=false 且 fetch_status=exhausted 时 continue 不会再暴露新岗位；no_active_session / query_mismatch 时改 fresh 或纠正查询。若同一 Action 根据已有事实已知不会带来新进展，重新评估其他合法 Action。search 默认小批量即可。
18. 不要对同一个已 opened 且已有 JD 的职位反复 open_job / inspect_job（程序会拒绝）。World 已有 JD 文本时禁止为「重读」去刷浏览器。若 analyze_job / match_job 因缺少 JD / job_profile / candidate material 被拒，阅读 Observation，自行选择下一步——不要假设必须先调用某个固定 Tool。
19. candidate_material_present 表示 World 中已有可用的候选人材料（profile / usable memory / evidence），不是「必须先跑过 analyze_candidate」的流水线标记。raw_candidate_intake_present / has_candidate_supplement 只说明本轮还有未消化的输入线索。
    - match_job 的前置是 Binding 能装载足够的 Candidate Material + Job Material；analyze_candidate 只是形成候选人 Interpretation 的一种方式，不是 match 的固定前置步骤。
    - 若 Observation 为 insufficient_candidate_material / missing job_profile / insufficient_job_material：该 Action 当前不可执行；根据 Observation 自行选择下一步（可 analyze_candidate、ask_user、search、open、finish 等）。
    - 已有上传/补充材料时，不要假装 World 里没有；但允许 ask_user 澄清城市/薪资等与材料无关的问题。
20. Observation 含 action_rejected / repeated_no_progress / thrash_re_reason 时：阅读同级 program_hints（不是 Observation）。program_hints.layer=program_hint，不是 Fact、不是 Instruction、不是 next_action。program_hints 不指定下一 Tool。若 remaining_opens=0，再 open_job 会被配额拒绝。最终业务下一步仍由你自己选择。
21. remaining_llm_calls=0 时禁止再选 analyze_job / match_job / analyze_candidate 等 LLM Tool（会被拒绝）。可 ask_user、finish，或在 remaining_opens>0 时 open_job。remaining_list_capacity / remaining_search_slots 表示 World 还能再入库多少新职位，不是「还能 search 几次」。
22. Observation.kind=repeated_no_progress：程序已拒绝再次执行该相同无进展 Action。progress=false / fetch_status / unexplored_listed_* 是事实。若同一 Action 已知不会带来新进展，重新评估其他合法 Action；选择仍由你决定。
23. program_hints.notes_zh 只是纠偏诊断，不是强制业务指令；不要把它当成 Observation 或 Fact。
24. Job.listed_card_facts 是外部列表观察 Fact；Job.fact 是 JD 索引 Fact；job_profile / match_result / interpret_result.application_evidence 是 Interpretation。restored/unverified 的 Interpretation 不是本轮已验证 Fact。
25. analyze_candidate 成功抽出的可用候选人事实会默认写入该候选人画像（文字与附件同等）；不需要用户说「记住」。persist_requested 仅作元数据，不是落盘开关。
26. reason 只解释已经选定的 Action；不能用 reason 声称 arguments 中未写出的业务变化（例如换 keyword / mode / job_key）。Binding 与 Tool 执行只消费 tool_name + arguments；reason 不参与业务执行语义。

返回：
{"action_type":"tool","tool_name":"search_jobs","arguments":{"keyword":"产品经理","city":"深圳","limit":8},"reason":"开始搜索","confidence":0.8}
或 {"action_type":"tool","tool_name":"analyze_candidate","arguments":{"intake_ids":["in-1"]},"reason":"形成候选人 Interpretation"}
或 {"action_type":"ask_user","intent":"clarification","question":"你更看重薪资还是行业？","reason":"还缺偏好","error_code":null}
或 {"action_type":"ask_user","intent":"surface","job_key":"boss:123","question":"这个职位值得你决定是否继续。","reason":"交给用户","error_code":null}
或 {"action_type":"finish","reason":"本轮只需记录经历、暂不搜索","error_code":null}
"""

CONTRACT_RETRY_USER_PREFIX = (
    "上一轮输出违反 Reasoner 合同，请原样重写合法 JSON。"
    "不要解释。不要把 Tool 名写进 action_type。"
    "action_type 只能是 tool、ask_user 或 finish。"
    "若要调用某个 Tool，必须 action_type=tool 且 tool_name=该 Tool。"
    "若 ask_user，必须带 intent=clarification 或 intent=surface；"
    "intent=surface 时必须带 job_key。"
    "finish.reason 禁止使用 '...' 占位。"
    "合同错误："
)


class ReasonNextActionSchemaError(ValueError):
    """Raised when Reasoner JSON does not satisfy the action contract."""


def empty_decision() -> dict:
    return {
        "analysis_status": None,
        "error": None,
        "action_type": None,
        "tool_name": None,
        "arguments": {},
        "question": None,
        "intent": None,
        "job_key": None,
        "reason": None,
        "confidence": None,
        "error_code": None,
        "raw_reasoner_output": None,
    }


def status_decision(status: str, error: str | None, raw: Any = None) -> dict:
    if status not in ALLOWED_STATUS:
        raise ReasonNextActionSchemaError(f"illegal analysis_status: {status}")
    result = empty_decision()
    result["analysis_status"] = status
    result["error"] = error
    result["raw_reasoner_output"] = raw
    return result


def reason_next_action(payload: Any, llm_provider: LLMProvider | None = None) -> dict:
    """LLM JSON → schema-checked next action. Fail closed. No keyword scheduling."""
    context = payload if isinstance(payload, dict) else {}
    allowed_names = _tool_names(context.get("available_tools"))
    if llm_provider is None:
        llm_provider = get_llm_provider()
    if llm_provider is None:
        return status_decision("llm_unavailable", "llm provider is not configured")

    user_prompt = json.dumps(
        {
            # Lead with current Observation / diagnostics so the model cannot miss them.
            "observation": context.get("observation"),
            "observations": context.get("observations") or [context.get("observation")],
            # Sibling of Observation — diagnostics only; not a next_action prescription.
            "program_hints": context.get("program_hints"),
            "goal": context.get("goal"),
            "context": context.get("context"),
            "constraints": context.get("constraints") or {},
            "history": context.get("history") or {},
            "jobs": context.get("jobs") or [],
            "intake": context.get("intake") or [],
            "evidence": context.get("evidence") or [],
            "claims": context.get("claims") or [],
            "interpretations": context.get("interpretations") or [],
            "verifications": context.get("verifications") or [],
            "available_tools": context.get("available_tools") or [],
            "uncertainty": context.get("uncertainty") or {},
            "candidate_profile": context.get("candidate_profile"),
            "memory": context.get("memory"),
        },
        ensure_ascii=False,
    )
    llm_calls = 0
    try:
        raw = llm_provider.complete_json(system=REASON_NEXT_ACTION_SYSTEM_PROMPT, user=user_prompt)
        llm_calls += 1
    except InvalidLLMJsonError as exc:
        # Provider already retried parse/rewrite; one Reasoner-level rewrite remains.
        llm_calls += 1
        try:
            raw = llm_provider.complete_json(
                system=REASON_NEXT_ACTION_SYSTEM_PROMPT,
                user=(
                    CONTRACT_RETRY_USER_PREFIX
                    + str(exc)
                    + "\nReturn one valid JSON Action object only.\n"
                    + user_prompt
                ),
            )
            llm_calls += 1
        except InvalidLLMJsonError as retry_exc:
            failed = status_decision("llm_invalid_json", str(retry_exc))
            failed["llm_calls"] = llm_calls
            return failed
        except Exception as retry_exc:
            failed = status_decision(_status_for_llm_exception(retry_exc), str(retry_exc))
            failed["llm_calls"] = llm_calls
            return failed
    except Exception as exc:
        failed = status_decision(_status_for_llm_exception(exc), str(exc))
        failed["llm_calls"] = llm_calls
        return failed

    if not isinstance(raw, dict):
        failed = status_decision("llm_invalid_json", "LLM response JSON must be an object", raw=raw)
        failed["llm_calls"] = llm_calls
        return failed

    try:
        checked = _validate_decision(raw, allowed_names=allowed_names)
    except ReasonNextActionSchemaError as first_exc:
        # One contract retry: ask the model to rewrite. Never remap illegal action_type.
        retry_user = json.dumps(
            {
                "contract_error": str(first_exc),
                "previous_illegal_output": raw,
                "memory_view": json.loads(user_prompt),
                "instruction": (
                    "Rewrite one valid JSON object. "
                    "action_type must be exactly tool, ask_user, or finish. "
                    "Tool names belong only in tool_name. "
                    "ask_user requires intent=clarification|surface; "
                    "surface requires job_key."
                ),
            },
            ensure_ascii=False,
        )
        try:
            raw_retry = llm_provider.complete_json(
                system=REASON_NEXT_ACTION_SYSTEM_PROMPT,
                user=CONTRACT_RETRY_USER_PREFIX + str(first_exc) + "\n" + retry_user,
            )
            llm_calls += 1
        except InvalidLLMJsonError as exc:
            failed = status_decision("llm_invalid_json", str(exc), raw=raw)
            failed["llm_calls"] = llm_calls
            return failed
        except Exception as exc:
            failed = status_decision(_status_for_llm_exception(exc), str(exc), raw=raw)
            failed["llm_calls"] = llm_calls
            return failed
        if not isinstance(raw_retry, dict):
            failed = status_decision(
                "analysis_failed",
                str(first_exc),
                raw={"first": raw, "retry": raw_retry},
            )
            failed["llm_calls"] = llm_calls
            return failed
        try:
            checked = _validate_decision(raw_retry, allowed_names=allowed_names)
        except ReasonNextActionSchemaError as second_exc:
            failed = status_decision(
                "analysis_failed",
                str(second_exc),
                raw={"first": raw, "retry": raw_retry},
            )
            failed["llm_calls"] = llm_calls
            return failed
        checked["raw_reasoner_output"] = {"first": raw, "retry": raw_retry}
        checked["llm_calls"] = llm_calls
        return checked

    checked["raw_reasoner_output"] = raw
    checked["llm_calls"] = llm_calls
    return checked


def _status_for_llm_exception(exc: BaseException) -> str:
    text = str(exc or "")
    lowered = text.lower()
    if isinstance(exc, InvalidLLMJsonError) or INVALID_JSON_MARKER in text:
        return "llm_invalid_json"
    if "timed out" in lowered or "timeout" in lowered:
        return "llm_error"
    if "llm http" in lowered or "llm request failed" in lowered:
        return "llm_error"
    if ("expecting" in lowered and "delimiter" in lowered) or "jsondecodeerror" in lowered:
        return "llm_invalid_json"
    if "json" in lowered and ("valid" in lowered or "decode" in lowered or "parse" in lowered):
        return "llm_invalid_json"
    return "llm_error"


def _validate_decision(raw: dict, *, allowed_names: set[str]) -> dict:
    action_type = raw.get("action_type")
    if action_type not in ALLOWED_ACTION_TYPES:
        raise ReasonNextActionSchemaError("action_type must be tool, ask_user, or finish")
    result = empty_decision()
    result["analysis_status"] = "ok"
    result["error"] = None
    result["action_type"] = action_type
    result["reason"] = _optional_string(raw.get("reason"))
    result["confidence"] = _optional_confidence(raw.get("confidence"))
    result["error_code"] = _optional_string(raw.get("error_code"))
    if action_type == "tool":
        name = raw.get("tool_name")
        if not isinstance(name, str) or not name.strip():
            raise ReasonNextActionSchemaError("tool_name is required when action_type is tool")
        name = name.strip()
        if name not in allowed_names:
            raise ReasonNextActionSchemaError(f"tool_name is not in available_tools: {name}")
        arguments = raw.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise ReasonNextActionSchemaError("arguments must be an object")
        result["tool_name"] = name
        result["arguments"] = arguments
        return result
    if action_type == "ask_user":
        question = _optional_string(raw.get("question"))
        if not question:
            raise ReasonNextActionSchemaError("question is required when action_type is ask_user")
        result["question"] = question
        arguments = raw.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise ReasonNextActionSchemaError("arguments must be an object")
        # Parse intent/job_key but do not hard-fail incompleteness here.
        # Incomplete ask_user is rejected by Program validate → Observation, not Agent FAILED.
        intent = _optional_string(raw.get("intent")) or _optional_string(arguments.get("intent"))
        job_key = _optional_string(raw.get("job_key")) or _optional_string(arguments.get("job_key"))
        if intent:
            arguments = {**arguments, "intent": intent}
        if job_key:
            arguments = {**arguments, "job_key": job_key}
        result["intent"] = intent
        result["job_key"] = job_key
        result["arguments"] = arguments
        return result
    arguments = raw.get("arguments")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise ReasonNextActionSchemaError("arguments must be an object")
    result["arguments"] = arguments
    return result


def _tool_names(tools: Any) -> set[str]:
    names: set[str] = set()
    for item in tools or []:
        if isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"].strip():
            names.add(item["name"].strip())
        elif isinstance(item, str) and item.strip():
            names.add(item.strip())
    return names


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _optional_confidence(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if 0.0 <= number <= 1.0:
            return number
    return None
