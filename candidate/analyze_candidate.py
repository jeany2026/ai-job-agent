"""Ingest candidate facts into working memory. Not a one-shot CandidateProfile gate."""

from __future__ import annotations

from typing import Any

from candidate.errors import LLM_ERROR, NO_EVIDENCE, SCHEMA_ERROR
from candidate.schema import SchemaError
from candidate.memory import (
    claim_from_fact,
    empty_memory,
    ensure_text_evidence,
    ingest_from_llm_payload,
    merge_facts,
    merge_layer,
    note_unresolved,
    project_profile_view,
    refresh_memory_status,
)
from candidate.semantic import CONTENT_TYPE_INTAKE_TEXT, make_interpretation
from candidate.schema import (
    ANALYSIS_STATUS_INSUFFICIENT,
    ANALYSIS_STATUS_LLM_ERROR,
    ANALYSIS_STATUS_LLM_UNAVAILABLE,
    ANALYSIS_STATUS_OK,
)
from llm.provider import LLMProvider, get_llm_provider

ANALYZE_CANDIDATE_SPEC = {
    "name": "analyze_candidate",
    "description": (
        "Incrementally extract candidate facts from this-turn evidence into "
        "working memory. Does not gate job search. Does not match a JD."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "intake_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Ids of this-turn Intake items to read. Prefer these for raw "
                    "user text/uploads. Do not send bodies in Action."
                ),
            },
            "evidence_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Ids of already-admitted Evidence this Tool should read. "
                    "Do not send resume or attachment bodies."
                ),
            },
            "resume": {
                "type": "string",
                "description": (
                    "Direct Python API only. Reasoner Actions must not send this body. "
                    "Accepts text or an object with text and optional candidate_id."
                ),
            },
            "existing_profile": {
                "type": "object",
                "description": "Optional prior profile view. Merged as existing facts, not a schema gate.",
            },
            "existing_memory": {
                "type": "object",
                "description": "Optional CandidateWorkingMemory to extend.",
            },
        },
    },
}

ANALYZE_CANDIDATE_MARKER = "你是候选人事实抽取器"

ANALYZE_CANDIDATE_SYSTEM_PROMPT = """你是候选人事实抽取器。从本次证据中增量抽出已经写明的候选人事实，写入 Working Memory。

这不是一次性完整人物画像。不要为了凑齐 CandidateProfile 字段而编造。
不要做岗位匹配，不要输出推荐或适合某岗位的结论。

规则：
1. 只使用本次证据里已经写明的事实。用户自然语言和附件属于同一份证据。
2. 一条事实格式异常不要影响其他事实。value 可以是数字、字符串或小对象（如 {"years":16}）。
3. 不要要求 quote 与原文逐字相同。quote 只是可选提示。
4. 不要输出 analysis_status、error、candidate_id。
5. 只返回一个 JSON 对象。

返回：
{"facts":[{"kind":"identity或career或education或project或skill或domain或transfer或other","name":"...","value":任意JSON值,"source_kind":"resume或user_statement或project_document或other_attachment或null","quote":"...或null"}],"notes":null}
"""

ANALYZE_CANDIDATE_UPDATE_SYSTEM_PROMPT = """你是候选人事实增量更新器。根据已有事实和新证据，只抽出新证据里新增或更正的事实。

不要删除旧事实。不要生成完整 CandidateProfile。不要做岗位匹配。
value 可以是数字、字符串或小对象。一条格式异常不要影响其他事实。
只返回 {"facts":[...],"notes":null}。
"""


def analyze_candidate(
    resume: dict | str | None,
    llm_provider: LLMProvider | None = None,
    *,
    existing_profile: dict | None = None,
    existing_memory: dict | None = None,
) -> dict:
    """Ingest facts from this-turn evidence. Field errors do not invalidate the candidate."""
    text, candidate_id, coerce_error = _coerce_resume(resume)
    memory = refresh_memory_status(existing_memory or empty_memory())
    if existing_profile and not memory.get("facts"):
        prior, prior_unresolved = ingest_from_llm_payload(existing_profile)
        memory = merge_facts(memory, prior)
        for item in prior_unresolved:
            memory = note_unresolved(
                memory, code=item["code"], message=item["message"], field=item.get("field")
            )
    if coerce_error:
        memory = note_unresolved(memory, code=NO_EVIDENCE, message="resume is empty", field="resume")
        memory["ingest_status"] = ANALYSIS_STATUS_INSUFFICIENT
        memory["ingest_error_code"] = NO_EVIDENCE
        memory["ingest_error"] = "resume is empty" if coerce_error == "empty" else coerce_error
        return _result(
            memory,
            candidate_id=candidate_id,
            analysis_status=ANALYSIS_STATUS_INSUFFICIENT,
            error=memory["ingest_error"],
            error_code=NO_EVIDENCE,
        )

    provider = llm_provider if llm_provider is not None else get_llm_provider()
    if provider is None:
        memory = note_unresolved(
            memory, code=LLM_ERROR, message="LLM provider is not configured", field=None
        )
        memory["ingest_status"] = ANALYSIS_STATUS_LLM_UNAVAILABLE
        memory["ingest_error_code"] = LLM_ERROR
        memory["ingest_error"] = "LLM provider is not configured"
        return _result(
            memory,
            candidate_id=candidate_id,
            analysis_status=ANALYSIS_STATUS_LLM_UNAVAILABLE,
            error=memory["ingest_error"],
            error_code=LLM_ERROR,
        )

    return analyze_candidate_with_llm(
        text,
        provider,
        candidate_id=candidate_id,
        existing_profile=existing_profile if isinstance(existing_profile, dict) else None,
        existing_memory=memory,
    )


def analyze_candidate_with_llm(
    resume_text: str,
    llm_provider: LLMProvider,
    *,
    candidate_id: str | None = None,
    existing_profile: dict | None = None,
    existing_memory: dict | None = None,
) -> dict:
    memory = refresh_memory_status(existing_memory or empty_memory())
    if existing_profile:
        system = ANALYZE_CANDIDATE_UPDATE_SYSTEM_PROMPT
        user_prompt = (
            "请根据已有事实与新证据，只返回新增或更正的 facts JSON。\n"
            "Existing facts:\n"
            + str(memory.get("facts") or existing_profile)
            + "\nNew evidence:\n"
            + resume_text
        )
    else:
        system = ANALYZE_CANDIDATE_SYSTEM_PROMPT
        user_prompt = "请从以下证据抽出候选人事实，只返回 facts JSON：\n" + resume_text
    try:
        raw = llm_provider.complete_json(system=system, user=user_prompt)
    except Exception as exc:
        memory = note_unresolved(memory, code=LLM_ERROR, message=str(exc), field=None)
        memory["ingest_status"] = ANALYSIS_STATUS_LLM_ERROR
        memory["ingest_error_code"] = LLM_ERROR
        memory["ingest_error"] = str(exc)
        return _result(
            memory,
            candidate_id=candidate_id,
            analysis_status=ANALYSIS_STATUS_LLM_ERROR,
            error=str(exc),
            error_code=LLM_ERROR,
        )

    if not isinstance(raw, dict):
        memory = note_unresolved(
            memory, code=SCHEMA_ERROR, message="LLM response JSON must be an object", field=None
        )
        memory["ingest_status"] = ANALYSIS_STATUS_LLM_ERROR
        memory["ingest_error_code"] = SCHEMA_ERROR
        memory["ingest_error"] = "LLM response JSON must be an object"
        return _result(
            memory,
            candidate_id=candidate_id,
            analysis_status=ANALYSIS_STATUS_LLM_ERROR,
            error=memory["ingest_error"],
            error_code=SCHEMA_ERROR,
        )

    memory, evidence_id = ensure_text_evidence(
        memory,
        resume_text,
        content_type=CONTENT_TYPE_INTAKE_TEXT,
        origin="analyze_candidate",
        source="intake",
    )
    derived_from = [evidence_id] if evidence_id else []
    try:
        facts, unresolved = ingest_from_llm_payload(raw, derived_from=derived_from)
    except SchemaError as exc:
        memory = note_unresolved(memory, code=SCHEMA_ERROR, message=str(exc), field=None)
        memory["ingest_status"] = ANALYSIS_STATUS_OK
        memory["ingest_error_code"] = SCHEMA_ERROR
        memory["ingest_error"] = str(exc)
        return _result(
            memory,
            candidate_id=candidate_id,
            analysis_status=ANALYSIS_STATUS_OK,
            error=None,
            error_code=None,
        )
    memory = merge_facts(memory, facts)
    claims = []
    for fact in facts:
        claims.append(claim_from_fact(fact, existing=list(memory.get("claims") or []) + claims))
    memory = merge_layer(memory, "claims", claims)
    memory = merge_layer(
        memory,
        "interpretations",
        [
            make_interpretation(
                kind="analyze_candidate",
                derived_from=derived_from,
                payload={"analysis_status": ANALYSIS_STATUS_OK, "claim_count": len(claims)},
                existing=memory.get("interpretations"),
            )
        ],
    )
    for item in unresolved:
        memory = note_unresolved(
            memory, code=item["code"], message=item["message"], field=item.get("field")
        )
    memory["ingest_status"] = ANALYSIS_STATUS_OK
    memory["ingest_error_code"] = SCHEMA_ERROR if unresolved else None
    memory["ingest_error"] = unresolved[0]["message"] if unresolved else None
    return _result(
        memory,
        candidate_id=candidate_id,
        analysis_status=ANALYSIS_STATUS_OK,
        error=None,
        error_code=None,
    )


def _result(
    memory: dict,
    *,
    candidate_id: str | None,
    analysis_status: str,
    error: str | None,
    error_code: str | None,
) -> dict:
    view = project_profile_view(memory, candidate_id=candidate_id)
    view["analysis_status"] = analysis_status
    view["error"] = error
    view["error_code"] = error_code
    view["candidate_id"] = candidate_id
    view["memory"] = refresh_memory_status(memory)
    return view


def _coerce_resume(resume: dict | str | None) -> tuple[str | None, str | None, str | None]:
    import json

    if resume is None:
        return None, None, "empty"
    if isinstance(resume, str):
        text = resume.strip()
        return (text, None, None) if text else (None, None, "empty")
    if not isinstance(resume, dict):
        return None, None, "resume must be a string or object"

    candidate_id = _optional_id(resume.get("candidate_id"))
    text = _first_text(resume, "text", "resume_text", "resume", "content")
    if not text:
        leftover = {
            key: value
            for key, value in resume.items()
            if key not in {"candidate_id", "text", "resume_text", "resume", "content"}
            and value not in (None, "", [])
        }
        if leftover:
            text = json.dumps(leftover, ensure_ascii=False)
    if not text or not str(text).strip():
        return None, candidate_id, "empty"
    return str(text).strip(), candidate_id, None


def _first_text(payload: dict, *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _optional_id(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
