"""Match a CandidateProfile against a JobProfile. Semantics come only from an LLM."""

from __future__ import annotations

import json
import re
from typing import Any

from llm.provider import LLMProvider, get_llm_provider
from matching.schema import (
    ANALYSIS_STATUS_FAILED,
    ANALYSIS_STATUS_INSUFFICIENT,
    ANALYSIS_STATUS_LLM_ERROR,
    ANALYSIS_STATUS_LLM_UNAVAILABLE,
    ANALYSIS_STATUS_OK,
    ID_PASSTHROUGH_FIELDS,
    SchemaError,
    empty_result,
    status_result,
    validate_llm_payload,
)

MATCH_JOB_SPEC = {
    "name": "match_job",
    "description": (
        "Match Candidate materials in World against a Job's JobProfile interpretation. "
        "Reasoner passes job_key only; Binding loads job_profile and candidate materials from World. "
        "Capability outcomes and recommendation come only from an LLM; this tool "
        "validates schema. This tool does not search jobs, open pages, or apply."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "job_key": {
                "type": "string",
                "description": "Identity of a live World Job that already has a job_profile.",
            },
            "job_id": {"type": "string"},
            "job_url": {"type": "string"},
            "candidate_profile": {
                "type": "object",
                "description": "Compatibility only. Loop Binding loads from World.",
            },
            "candidate_context": {
                "type": "object",
                "description": "Compatibility only. Loop Binding loads from World.",
            },
            "job_profile": {
                "type": "object",
                "description": "Compatibility only. Loop Binding loads from World.",
            },
        },
        "required": ["job_key"],
    },
}

MATCH_JOB_SYSTEM_PROMPT = """你是岗位匹配评估器。根据 CandidateContext（长期 CandidateProfile + 本次 CandidateSupplement + MatchingContext）与 JobProfile 输出结构化 MatchResult。

严格规则：
1. 只使用 CandidateContext 与 JobProfile 中已经结构化的事实。不要编造未出现的能力、要求、证书、年限。
2. 禁止用关键词字面相等代替判断。无行业直接经验 ≠ 无岗位能力。
3. 本次 CandidateSupplement 与 MatchingContext 是当前任务上下文，必须纳入判断，但它们不是长期画像字段。
4. 每项能力必须给出四态之一 outcome：
   - direct：候选人有可直接对应的能力/经历
   - transferable：没有同行直接经验，但有可迁移能力；必须填写 transfer_rationale
   - missing：该要求缺乏可迁移支撑，确认不具备
   - insufficient_evidence：上下文都不足以判断
5. B（transferable）与 C（missing）只能根据语义判断，不要因为行业词不同就判 missing。
6. hard_requirements_met：true=硬性要求均满足；false=至少一项硬性不满足；null=无法判断。
7. hard_requirements_met=false 时，hard_requirement_gaps 不能为空；status 只能是 fail 或 unknown。
8. hard_requirements_met=true 时，不得出现 status=fail 的 hard gap。
9. FitLevel 只能是 strong / moderate / weak / none / unknown。
10. recommendation 只能是 yes / weak / no / insufficient_evidence。
11. rationale 必须回答「为什么适合/不适合」，基于上述评估，不要空话。
12. knowledge_gaps 只写相对本 JD 的缺口。
13. 数组字段必须出现，没有内容时返回 []。
14. 只返回一个 JSON 对象，不要 Markdown，不要解释。
15. 不要输出 analysis_status、error、job_id、candidate_id（由程序填写）。

返回字段：
hard_requirements_met, hard_requirement_gaps, capability_assessments,
business_fit, technical_fit, industry_fit, overall_fit,
risks, knowledge_gaps, recommendation, rationale, evidence_summary

CapAssessment：
{"dimension":"...","outcome":"direct|transferable|missing|insufficient_evidence","job_requirement_ref":"...或null","candidate_capability_ref":"...或null","transfer_rationale":"transferable时必填否则null","evidence":[{"quote":"来自Context或Profile的原文","location_hint":"...或null","field":"...或null"}]}

HardGap：
{"requirement":"...","status":"fail|unknown","notes":"...或null","evidence":[]}

RiskItem：
{"risk":"...","severity":"high|medium|low","notes":"...或null","evidence":[]}

GapItem：
{"gap":"...","severity":"high|medium|low","evidence":[],"notes":"...或null"}
"""

_WHITESPACE_RE = re.compile(r"\s+")
_FAILED_PROFILE_STATUSES = {
    ANALYSIS_STATUS_INSUFFICIENT,
    ANALYSIS_STATUS_LLM_UNAVAILABLE,
    ANALYSIS_STATUS_LLM_ERROR,
    ANALYSIS_STATUS_FAILED,
}


def match_job(
    candidate_profile: dict | None,
    job_profile: dict | None = None,
    llm_provider: LLMProvider | None = None,
    *,
    candidate_context: dict | None = None,
) -> dict:
    """Match CandidateContext × JobProfile via LLM JSON + schema checks. Fail closed."""
    context = candidate_context if isinstance(candidate_context, dict) else None
    profile = candidate_profile
    if context and profile is None:
        profile = context.get("persistent_profile")
    candidate, job, coerce_error, coerce_status = _coerce_profiles(
        profile,
        job_profile,
        candidate_context=context,
    )
    candidate_id = _profile_id(candidate, "candidate_id")
    job_id = _profile_id(job, "job_id")
    if coerce_error:
        return status_result(
            coerce_status,
            coerce_error,
            job_id=job_id,
            candidate_id=candidate_id,
        )

    provider = llm_provider if llm_provider is not None else get_llm_provider()
    if provider is None:
        return status_result(
            ANALYSIS_STATUS_LLM_UNAVAILABLE,
            "LLM provider is not configured",
            job_id=job_id,
            candidate_id=candidate_id,
        )

    return match_job_with_llm(candidate, job, provider, candidate_context=context)


def match_job_with_llm(
    candidate_profile: dict,
    job_profile: dict,
    llm_provider: LLMProvider,
    *,
    candidate_context: dict | None = None,
) -> dict:
    candidate_id = _profile_id(candidate_profile, "candidate_id")
    job_id = _profile_id(job_profile, "job_id")
    user_prompt = _match_user_prompt(candidate_profile, job_profile, candidate_context)
    try:
        raw = llm_provider.complete_json(system=MATCH_JOB_SYSTEM_PROMPT, user=user_prompt)
    except Exception as exc:
        return status_result(
            ANALYSIS_STATUS_LLM_ERROR,
            str(exc),
            job_id=job_id,
            candidate_id=candidate_id,
        )

    if not isinstance(raw, dict):
        return status_result(
            ANALYSIS_STATUS_LLM_ERROR,
            "LLM response JSON must be an object",
            job_id=job_id,
            candidate_id=candidate_id,
        )

    try:
        fields = validate_llm_payload(raw)
        _assert_quotes_in_profiles(fields, candidate_profile, job_profile, candidate_context=candidate_context)
    except SchemaError as exc:
        return status_result(
            ANALYSIS_STATUS_FAILED,
            str(exc),
            job_id=job_id,
            candidate_id=candidate_id,
        )

    result = empty_result(job_id=job_id, candidate_id=candidate_id)
    result.update(fields)
    result["analysis_status"] = ANALYSIS_STATUS_OK
    result["error"] = None
    result["job_id"] = job_id
    result["candidate_id"] = candidate_id
    return result


def _coerce_profiles(
    candidate_profile: dict | None,
    job_profile: dict | None,
    *,
    candidate_context: dict | None = None,
) -> tuple[dict | None, dict | None, str | None, str]:
    from candidate.context import context_has_candidate_evidence
    from candidate.schema import empty_profile

    if job_profile is None:
        return candidate_profile, None, "job_profile is required", ANALYSIS_STATUS_INSUFFICIENT
    if not isinstance(job_profile, dict):
        return candidate_profile, None, "job_profile must be an object", ANALYSIS_STATUS_FAILED

    profile = candidate_profile
    if profile is None and context_has_candidate_evidence(candidate_context):
        profile = empty_profile()
        profile["analysis_status"] = ANALYSIS_STATUS_OK
    if profile is None:
        return None, job_profile, "candidate_profile and job_profile are required", ANALYSIS_STATUS_INSUFFICIENT
    if not isinstance(profile, dict):
        return None, job_profile, "candidate_profile must be an object", ANALYSIS_STATUS_FAILED

    candidate_status = _optional_status(profile.get("analysis_status"))
    job_status = _optional_status(job_profile.get("analysis_status"))
    if candidate_status in _FAILED_PROFILE_STATUSES and not context_has_candidate_evidence(candidate_context):
        return (
            profile,
            job_profile,
            f"candidate profile analysis_status is {candidate_status}",
            ANALYSIS_STATUS_INSUFFICIENT,
        )
    if job_status in _FAILED_PROFILE_STATUSES:
        return (
            profile,
            job_profile,
            f"job profile analysis_status is {job_status}",
            ANALYSIS_STATUS_INSUFFICIENT,
        )
    return profile, job_profile, None, ANALYSIS_STATUS_OK


def _profile_for_prompt(profile: dict) -> dict:
    skip = {"analysis_status", "error", *ID_PASSTHROUGH_FIELDS}
    return {key: value for key, value in profile.items() if key not in skip}


def _profile_id(profile: dict | None, key: str) -> str | None:
    if not isinstance(profile, dict):
        return None
    value = profile.get(key)
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "n/a"}:
        return None
    return text


def _optional_status(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _match_user_prompt(candidate_profile: dict, job_profile: dict, candidate_context: dict | None) -> str:
    parts = [
        "请匹配以下 CandidateContext 与 JobProfile，只返回结构化 MatchResult JSON。",
        "长期 CandidateProfile 不是岗位专用画像。本次 Supplement / MatchingContext 只用于这一次匹配。",
    ]
    if candidate_context:
        parts.append("CandidateContext.persistent_profile:")
        profile = candidate_context.get("persistent_profile") or candidate_profile
        parts.append(json.dumps(_profile_for_prompt(profile or {}), ensure_ascii=False, indent=2))
        from understanding.schema import grounded_candidate_supplement

        parts.append("CandidateContext.candidate_supplement:")
        parts.append(
            "这些是 Claim，默认 unverified。同轮 support_check 只是 Interpretation，"
            "不要当成已验证事实。冲突主张与简历 Evidence 同时保留。"
        )
        parts.append(
            json.dumps(
                grounded_candidate_supplement(candidate_context.get("candidate_supplement") or {}),
                ensure_ascii=False,
                indent=2,
            )
        )
        parts.append("CandidateContext.matching_context:")
        parts.append(json.dumps(candidate_context.get("matching_context"), ensure_ascii=False))
        parts.append("CandidateContext.preferences:")
        parts.append(json.dumps(candidate_context.get("preferences") or [], ensure_ascii=False))
        if candidate_context.get("candidate_memory"):
            parts.append("CandidateContext.candidate_memory:")
            parts.append(
                json.dumps(candidate_context.get("candidate_memory") or {}, ensure_ascii=False, indent=2)
            )
    else:
        parts.append("CandidateProfile:")
        parts.append(json.dumps(_profile_for_prompt(candidate_profile), ensure_ascii=False, indent=2))
    parts.append("JobProfile:")
    parts.append(json.dumps(_profile_for_prompt(job_profile), ensure_ascii=False, indent=2))
    return "\n".join(parts)


def _assert_quotes_in_profiles(
    fields: dict,
    candidate_profile: dict,
    job_profile: dict,
    *,
    candidate_context: dict | None = None,
) -> None:
    haystack = _collapse(
        json.dumps(_profile_for_prompt(candidate_profile), ensure_ascii=False)
        + json.dumps(_profile_for_prompt(job_profile), ensure_ascii=False)
    )
    if candidate_context:
        haystack += _collapse(json.dumps(candidate_context, ensure_ascii=False))
    if not haystack:
        raise SchemaError("profiles are empty")
    for quote, owner in _walk_quotes(fields):
        if _collapse(quote) not in haystack:
            raise SchemaError(f"{owner} evidence quote not found in profiles")


def _walk_quotes(value: Any, owner: str = "match"):
    if isinstance(value, dict):
        if "quote" in value and isinstance(value.get("quote"), str):
            yield value["quote"], owner
        for key, inner in value.items():
            yield from _walk_quotes(inner, f"{owner}.{key}")
    elif isinstance(value, list):
        for index, inner in enumerate(value):
            yield from _walk_quotes(inner, f"{owner}[{index}]")


def _collapse(text: str) -> str:
    return _WHITESPACE_RE.sub("", text)
