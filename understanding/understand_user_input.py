"""Understand one user turn: message + attachments as one semantic context.

Extraction and evidence validation are separate LLM responsibilities.
This layer does not search, match, or persist CandidateProfile.
"""

from __future__ import annotations

from typing import Any

from llm.provider import LLMProvider, get_llm_provider
from understanding.schema import (
    ANALYSIS_STATUS_FAILED,
    ANALYSIS_STATUS_INSUFFICIENT,
    ANALYSIS_STATUS_LLM_ERROR,
    ANALYSIS_STATUS_LLM_UNAVAILABLE,
    ALLOWED_SOURCE_KINDS,
    CONTROL_TASK_KINDS,
    EVIDENCE_VALIDATION_SKIPPED,
    FOLLOW_UP_TASK_KINDS,
    SchemaError,
    empty_understanding,
    has_conversation_reference,
    has_user_goal,
    is_empty_understanding_fields,
    status_understanding,
    validate_llm_payload,
)
from understanding.verify_candidate_claims import verify_candidate_claims

UNDERSTAND_USER_INPUT_SPEC = {
    "name": "understand_user_input",
    "description": (
        "Understand one user turn of natural language plus optional attachments. "
        "Splits the same semantic context into UserGoal, CandidateSupplement, "
        "Preferences, Constraints, and MatchingContext. Does not search jobs, "
        "match a JD, or update CandidateProfile."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "intake_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Ids of this-turn Intake items to read. Prefer these for raw "
                    "user text/uploads. Do not send message or attachment bodies."
                ),
            },
            "evidence_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Ids of already-admitted Evidence this Tool should read. "
                    "Do not send message or attachment bodies."
                ),
            },
            "message": {
                "type": "string",
                "description": "Direct Python API only. Reasoner Actions must not send this body.",
            },
            "attachments": {
                "type": "array",
                "description": (
                    "Direct Python API only. Reasoner Actions must not send attachment text."
                ),
            },
            "candidate_profile": {
                "type": "object",
                "description": "Optional existing CandidateProfile context. Not updated.",
            },
            "session_context": {
                "type": "object",
                "description": (
                    "Optional previous-turn structured context: previous_goal, "
                    "previous_preferences, last_recommended, job_contexts. "
                    "For references like prior jobs; Understanding must not output job_id."
                ),
            },
        },
    },
}

UNDERSTAND_USER_INPUT_SYSTEM_PROMPT = """你是用户输入理解器。用户的自然语言和附件属于同一次表达，必须在同一个语义上下文中理解。

任务：判断用户这一次到底告诉了 Agent 什么，并拆成结构化 UserInputUnderstanding。
不要把输入转换成招聘网站搜索关键词。不要做岗位匹配，不要更新长期画像。

分类规则：
1. user_goal：本次想让 Agent 完成的求职任务。target_roles 只放职位名称（如高级产品经理），不要把个人技能改写成职位。cities 是意向城市。salary_min 是月薪下限整数（人民币元）；「35K左右」→ 35000；说了薪资但无数字则为 null。focus_areas 只放本次想看的岗位/行业方向（如金融科技、医疗支付），禁止把「我做过支付」这类个人经历放进 focus_areas。exclude_companies / platforms 仅收录用户明确提到的排除公司或招聘网站。
2. candidate_supplement：本次提供的候选人自身信息（经历、能力、项目、缺口、可迁移主张）。没有简历也可以成立。quote 只是可选提示，允许概括，不要为了逐字摘录而编造。不要输出 evidence_status。source_kind 只能是 resume / user_statement / project_document / other_attachment。
3. preferences：软性偏好，如「优先金融科技」「不希望长期高强度加班」。不要把个人能力写进这里。
4. constraints：更硬的限制。notes 放「不考虑已排除的公司」等；salary_min / cities / exclude_companies 仅在用户把它们当限制说出时填写，可与 user_goal 对应字段重复。
5. matching_context：本次匹配时必须考虑的上下文，例如「不要因为没有医疗行业经验直接排除，重点判断支付/清结算等可迁移能力」。这是本次任务上下文，不是长期画像字段。
6. persist_requested：保留字段作元数据。用户明确说「记住/以后都要考虑」时可为 true；否则 false。程序默认会在 analyze_candidate 成功抽出可用候选人事实后写入画像，不依赖本字段。文字输入与附件同属本轮资料。
7. 一次输入可以同时包含以上多类。纯候选人信息也是有效输入：user_goal 可以为空数组，不要因此判定失败。
8. 附件不是独立任务。用户说「这个项目请考虑进去」并上传文档时，应把附件证据归入 candidate_supplement，而不是当成搜索词。
9. 只使用已经写明的事实。不要编造未出现的公司、职位、薪资、项目名。
10. 数组字段必须出现，没有内容时返回 []。只返回一个 JSON 对象，不要 Markdown，不要解释。
11. 不要输出 analysis_status、error、raw_text（由程序填写）。
12. conversation_reference：仅当用户在指代本会话中已经讨论过的职位时填写，否则为 null。只描述指代，禁止输出 job_id / job_key / job_url / context_id。resolution_hint 只能用结构化字段：recency（last / previous_round / this_round / any / null）、ordinal（从1开始的序号或 null）、round_offset（0=本轮，-1=上一轮，或 null）、recommended_only、highest_match、semantic_filters。semantic_filters 项为 {kind, value}，kind 只能是 knowledge_gap / missing_experience / industry / capability / title / company / recommendation / overall_fit。
13. task_kind：new_job_search=新的找工作任务（即使会话里已有职位/任务也要重新搜，例如「重新帮我找广州的产品经理」）；follow_up_job=对已讨论职位继续分析；update_goal=用新的目标开启新的搜索任务；job_reference=引用历史职位；apply_job=对当前等待决定的职位授权投递/申请；skip_job=跳过当前等待决定的职位；stop_task=停止这次求职任务；continue_task=继续当前求职任务。不要把新的求职目标当成对旧职位的追问，也不要把「投递/跳过/停止」当成新的搜索。
14. 用户只是在问已经出现过的职位时，user_goal 可以保持空数组，不要因此判定失败。
15. apply_job / skip_job / stop_task / continue_task 是对已有 JobSearchTask 的控制，不是新的搜索。用户说「投递」「跳过」「停止这次求职」「继续」时应使用这些 task_kind。用户说「重新帮我找…」必须是 new_job_search。

返回字段：
user_goal, candidate_supplement, preferences, constraints, matching_context, persist_requested, conversation_reference, task_kind

user_goal：
{"target_roles":[],"cities":[],"salary_min":null,"focus_areas":[],"exclude_companies":[],"platforms":[]}

candidate_supplement：
{"statements":[],"claimed_capabilities":[{"name":"...","quote":"...或null","source_kind":"user_statement"}],"claimed_projects":[{"name":"...","description":"...或null","quote":"...或null","source_kind":"project_document"}],"acknowledged_gaps":[{"gap":"...","quote":"...或null"}],"transfer_claims":[{"claim":"...","from_domain":"...或null","to_domain":"...或null","quote":"...或null"}],"persist_requested":false}

constraints：
{"salary_min":null,"cities":[],"exclude_companies":[],"notes":[]}

matching_context 为字符串或 null。persist_requested 为布尔值。
conversation_reference 为 null，或：
{"type":"conversation_reference","target_kind":"job","reference_text":"...或null","resolution_hint":{"recency":"last或previous_round或this_round或any或null","ordinal":null,"round_offset":null,"recommended_only":false,"highest_match":false,"semantic_filters":[]}}
task_kind 为 new_job_search / follow_up_job / update_goal / job_reference / apply_job / skip_job / stop_task / continue_task 或 null。
"""


def understand_user_input(
    message: Any = None,
    attachments: Any = None,
    *,
    candidate_profile: dict | None = None,
    session_context: dict | None = None,
    llm_provider: LLMProvider | None = None,
) -> dict:
    """Extract this turn, then semantically verify candidate claims. Does not persist CandidateProfile."""
    structured, raw_text, attachment_records, nested_profile, coerce_error = _coerce_input(
        message, attachments
    )
    if candidate_profile is None:
        candidate_profile = nested_profile
    if coerce_error == "empty":
        return status_understanding(
            ANALYSIS_STATUS_INSUFFICIENT,
            "user input is empty",
            raw_text=raw_text,
        )
    if coerce_error:
        return status_understanding(
            ANALYSIS_STATUS_FAILED,
            coerce_error,
            raw_text=raw_text,
        )

    if structured is not None:
        try:
            fields = validate_llm_payload(structured)
            this_turn_has_goal = has_user_goal(fields)
            fields = _fill_goal_from_session(fields, session_context)
            fields = _finalize_task_fields(fields, this_turn_has_goal=this_turn_has_goal)
        except SchemaError as exc:
            return status_understanding(ANALYSIS_STATUS_FAILED, str(exc), raw_text=raw_text)
        if is_empty_understanding_fields(fields):
            return status_understanding(
                ANALYSIS_STATUS_INSUFFICIENT,
                "understood fields are empty",
                raw_text=raw_text,
            )
        return _ok_result(
            fields,
            raw_text=raw_text,
            evidence_validation_status=EVIDENCE_VALIDATION_SKIPPED,
        )

    provider = llm_provider if llm_provider is not None else get_llm_provider()
    if provider is None:
        return status_understanding(
            ANALYSIS_STATUS_LLM_UNAVAILABLE,
            "LLM provider is not configured",
            raw_text=raw_text,
        )

    user_prompt = _build_user_prompt(
        raw_text=raw_text,
        attachments=attachment_records,
        candidate_profile=candidate_profile if isinstance(candidate_profile, dict) else None,
        session_context=session_context if isinstance(session_context, dict) else None,
    )
    try:
        raw = provider.complete_json(system=UNDERSTAND_USER_INPUT_SYSTEM_PROMPT, user=user_prompt)
    except Exception as exc:
        return status_understanding(ANALYSIS_STATUS_LLM_ERROR, str(exc), raw_text=raw_text)

    if not isinstance(raw, dict):
        return status_understanding(
            ANALYSIS_STATUS_LLM_ERROR,
            "LLM response JSON must be an object",
            raw_text=raw_text,
        )

    try:
        fields = validate_llm_payload(raw)
        this_turn_has_goal = has_user_goal(fields)
        fields = _fill_goal_from_session(fields, session_context)
        fields = _finalize_task_fields(fields, this_turn_has_goal=this_turn_has_goal)
    except SchemaError as exc:
        return status_understanding(ANALYSIS_STATUS_FAILED, str(exc), raw_text=raw_text)

    if is_empty_understanding_fields(fields):
        return status_understanding(
            ANALYSIS_STATUS_INSUFFICIENT,
            "understood fields are empty",
            raw_text=raw_text,
        )
    verified = verify_candidate_claims(
        fields.get("candidate_supplement"),
        raw_text=raw_text,
        attachments=attachment_records,
        llm_provider=provider,
    )
    fields = dict(fields)
    fields["candidate_supplement"] = verified["candidate_supplement"]
    return _ok_result(
        fields,
        raw_text=raw_text,
        evidence_validation_status=verified["evidence_validation_status"],
        support_check=verified.get("support_check") or [],
        error=None,
    )


def _ok_result(
    fields: dict,
    *,
    raw_text: str | None,
    evidence_validation_status: str | None = None,
    support_check: list[dict] | None = None,
    error: str | None = None,
) -> dict:
    result = empty_understanding(raw_text=raw_text)
    result.update(fields)
    result["analysis_status"] = "ok"
    result["error"] = error
    result["evidence_validation_status"] = evidence_validation_status
    result["support_check"] = list(support_check or [])
    result["verifications"] = []
    return result


def is_structured_understanding(value: Any) -> bool:
    return isinstance(value, dict) and "user_goal" in value and "candidate_supplement" in value


def _coerce_input(
    message: Any,
    attachments: Any,
) -> tuple[dict | None, str | None, list[dict], dict | None, str | None]:
    from tools.parse_user_goal import GoalSchemaError, is_structured_goal, validate_goal_payload
    from understanding.schema import empty_constraints, empty_supplement

    payload_attachments = attachments
    nested_profile: dict | None = None
    if isinstance(message, dict):
        profile = message.get("candidate_profile")
        if isinstance(profile, dict):
            nested_profile = profile
        if is_structured_understanding(message):
            pass
        elif is_structured_goal(message):
            try:
                goal_fields = validate_goal_payload(message)
            except GoalSchemaError as exc:
                return None, _first_text(message, "raw_text", "text"), [], nested_profile, str(exc)
            raw_text = _first_text(message, "raw_text", "text", "goal", "query")
            message = {
                "raw_text": raw_text,
                "user_goal": goal_fields,
                "candidate_supplement": empty_supplement(),
                "preferences": [],
                "constraints": empty_constraints(),
                "matching_context": None,
                "persist_requested": False,
                "conversation_reference": None,
                "task_kind": None,
            }
        else:
            if payload_attachments is None:
                payload_attachments = message.get("attachments")
            message = _first_text(message, "message", "raw_text", "text", "goal", "query")

    if is_structured_understanding(message):
        raw_text = _first_text(message, "raw_text", "message", "text")
        records, attach_error = _coerce_attachments(payload_attachments)
        if attach_error:
            return None, raw_text, [], nested_profile, attach_error
        return message, raw_text, records, nested_profile, None

    if message is not None and not isinstance(message, str):
        return None, None, [], nested_profile, "message must be a string or UserInputUnderstanding object"

    raw_text = message.strip() if isinstance(message, str) else None
    if raw_text == "":
        raw_text = None

    records, attach_error = _coerce_attachments(payload_attachments)
    if attach_error:
        return None, raw_text, [], nested_profile, attach_error
    if not raw_text and not records:
        return None, raw_text, [], nested_profile, "empty"
    return None, raw_text, records, nested_profile, None


def _coerce_attachments(attachments: Any) -> tuple[list[dict], str | None]:
    if attachments is None:
        return [], None
    if not isinstance(attachments, list):
        return [], "attachments must be an array"
    records: list[dict] = []
    for index, item in enumerate(attachments):
        record, error = _coerce_attachment(item, index)
        if error:
            return [], error
        if record:
            records.append(record)
    return records, None


def _coerce_attachment(item: Any, index: int) -> tuple[dict | None, str | None]:
    if item is None:
        return None, None
    if isinstance(item, str):
        text = item.strip()
        if not text:
            return None, None
        return {
            "filename": None,
            "text": text,
        }, None
    if not isinstance(item, dict):
        return None, f"attachments[{index}] must be an object or string"

    filename = _optional_name(item.get("filename") or item.get("name"))
    kind = item.get("kind") or item.get("source_kind")
    kind_text = str(kind).strip() if kind is not None else None
    # kind optional — Binding must not invent business identity from carriers.
    if kind_text and kind_text not in ALLOWED_SOURCE_KINDS:
        return None, f"attachments[{index}].kind must be one of {sorted(ALLOWED_SOURCE_KINDS)}"

    text = _first_text(item, "text", "content", "resume_text")
    if not text:
        data = item.get("data")
        if data is not None:
            try:
                from api.resume_extract import ResumeExtractError, extract_resume_text

                text = extract_resume_text(filename=filename, data=data)
            except ResumeExtractError as exc:
                return None, f"attachments[{index}] {exc.message}"
            except Exception as exc:
                return None, f"attachments[{index}] unreadable: {exc}"
    if not text:
        return None, f"attachments[{index}] has no text"
    record: dict = {"filename": filename, "text": text}
    if kind_text:
        record["kind"] = kind_text
    return record, None


def _build_user_prompt(
    *,
    raw_text: str | None,
    attachments: list[dict],
    candidate_profile: dict | None,
    session_context: dict | None = None,
) -> str:
    parts = [
        "请理解以下同一次用户表达，只返回结构化 UserInputUnderstanding JSON。",
        "",
        "## 用户输入",
        raw_text or "（无文字）",
    ]
    if attachments:
        parts.append("")
        parts.append("## 附件（与用户输入属于同一次表达，请一并理解，不要单独当成搜索条件）")
        for index, item in enumerate(attachments, start=1):
            name = item.get("filename") or f"attachment-{index}"
            kind = item.get("kind")
            if kind:
                parts.append(f"### 附件{index} filename={name} kind={kind}")
            else:
                parts.append(f"### 附件{index} filename={name}")
            parts.append(item.get("text") or "")
    session_text = _session_context_text(session_context)
    if session_text:
        parts.append("")
        parts.append(
            "## 本次会话上下文（上一轮已结构化的任务，以及已讨论职位的目录；"
            "仅用于理解「再」「刚才」以及对历史职位的指代；"
            "不是本次新证据，禁止从这里摘 quote，也不要写成新的 candidate_supplement；"
            "禁止输出 job_id / job_key / job_url）"
        )
        parts.append(session_text)
    context = _profile_context(candidate_profile)
    if context:
        parts.append("")
        parts.append("## 已有长期 CandidateProfile 摘要（仅供区分新旧信息；不要把它写成本次 supplement，也不要更新它）")
        parts.append(context)
    return "\n".join(parts)


def _session_context_text(session_context: dict | None) -> str | None:
    if not isinstance(session_context, dict):
        return None
    parts: list[str] = []
    previous_goal = session_context.get("previous_goal") or session_context.get("goal")
    if isinstance(previous_goal, dict):
        roles = [item for item in (previous_goal.get("target_roles") or []) if item]
        cities = [item for item in (previous_goal.get("cities") or []) if item]
        focus = [item for item in (previous_goal.get("focus_areas") or []) if item]
        if roles:
            parts.append("上一轮目标职位: " + "、".join(str(item) for item in roles))
        if cities:
            parts.append("上一轮城市: " + "、".join(str(item) for item in cities))
        if focus:
            parts.append("上一轮方向: " + "、".join(str(item) for item in focus))
        if previous_goal.get("salary_min") is not None:
            parts.append(f"上一轮薪资下限: {previous_goal.get('salary_min')}")
    preferences = session_context.get("previous_preferences") or []
    if preferences:
        parts.append("上一轮偏好: " + "、".join(str(item) for item in preferences if item))
    catalog = _job_catalog_entries(session_context)
    if catalog:
        parts.append("本会话已讨论职位（按轮次与序号；不要输出 job_id）：")
        for item in catalog:
            round_no = item.get("source_round")
            ordinal = item.get("round_ordinal")
            company = item.get("company") or "未知公司"
            title = item.get("title") or "未命名岗位"
            rec = item.get("recommendation") or item.get("stage") or ""
            fit = item.get("overall_fit") or ""
            extra = []
            if rec:
                extra.append(f"推荐={rec}")
            if fit:
                extra.append(f"匹配={fit}")
            gaps = [gap for gap in (item.get("knowledge_gaps") or []) if gap]
            if gaps:
                extra.append("缺口=" + "、".join(str(gap) for gap in gaps[:3]))
            prefix = []
            if round_no is not None:
                prefix.append(f"第{round_no}轮")
            if ordinal is not None:
                prefix.append(f"第{ordinal}个")
            label = " ".join(prefix) + "：" if prefix else ""
            suffix = f"（{'；'.join(extra)}）" if extra else ""
            parts.append(f"- {label}{company} / {title}{suffix}")
    else:
        recommended = session_context.get("last_recommended") or []
        if recommended:
            parts.append("上一轮推荐岗位:")
            for item in recommended[:5]:
                if not isinstance(item, dict):
                    continue
                company = item.get("company_name") or "未知公司"
                title = item.get("job_title") or "未命名岗位"
                platform = item.get("platform") or ""
                extra = f"（{platform}）" if platform else ""
                parts.append(f"- {company} / {title}{extra}")
    return "\n".join(parts) or None


def _fill_goal_from_session(fields: dict, session_context: dict | None) -> dict:
    """Compose empty structured UserGoal fields from the previous turn. Not semantic guessing."""
    if not isinstance(fields, dict) or not isinstance(session_context, dict):
        return fields
    if _should_skip_goal_fill(fields):
        return fields
    previous = session_context.get("previous_goal") or session_context.get("goal")
    if not isinstance(previous, dict):
        return fields
    goal = dict(fields.get("user_goal") or {})
    for key in ("target_roles", "cities", "focus_areas", "exclude_companies", "platforms"):
        if not goal.get(key) and previous.get(key):
            goal[key] = list(previous[key])
    if goal.get("salary_min") is None and previous.get("salary_min") is not None:
        goal["salary_min"] = previous["salary_min"]
    fields = dict(fields)
    fields["user_goal"] = goal
    if not fields.get("preferences") and session_context.get("previous_preferences"):
        fields["preferences"] = list(session_context["previous_preferences"])
    return fields


def _should_skip_goal_fill(fields: dict) -> bool:
    """Follow-up / task control must not be rewritten into a new search goal."""
    kind = fields.get("task_kind")
    if kind in FOLLOW_UP_TASK_KINDS or kind in CONTROL_TASK_KINDS:
        return True
    if kind in {"new_job_search", "update_goal"}:
        return False
    return has_conversation_reference(fields) and not has_user_goal(fields)


def _finalize_task_fields(fields: dict, *, this_turn_has_goal: bool) -> dict:
    del this_turn_has_goal
    return dict(fields)


def _job_catalog_entries(session_context: dict) -> list[dict]:
    from conversation.job_context import job_context_catalog, job_contexts_from_session

    catalog = session_context.get("job_context_catalog")
    if isinstance(catalog, list) and catalog:
        return [item for item in catalog if isinstance(item, dict)]
    return job_context_catalog(job_contexts_from_session(session_context))


def _profile_context(profile: dict | None) -> str | None:
    if not isinstance(profile, dict):
        return None
    parts: list[str] = []
    summary = profile.get("summary")
    if isinstance(summary, str) and summary.strip():
        parts.append(summary.strip())
    years = profile.get("years_experience")
    if isinstance(years, str) and years.strip():
        parts.append(f"年限: {years.strip()}")
    names: list[str] = []
    for field in (
        "product_capabilities",
        "business_capabilities",
        "direct_capabilities",
    ):
        for item in profile.get(field) or []:
            if isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"].strip():
                names.append(item["name"].strip())
            if len(names) >= 8:
                break
        if len(names) >= 8:
            break
    if names:
        parts.append("已知能力: " + "、".join(names))
    return "\n".join(parts) or None


def _first_text(payload: dict, *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _optional_name(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
