"""Analyze one normalized job posting into JobProfile. Semantics come only from an LLM."""

from __future__ import annotations

import json
import re
from typing import Any

from job.profile_schema import (
    ANALYSIS_STATUS_FAILED,
    ANALYSIS_STATUS_INSUFFICIENT,
    ANALYSIS_STATUS_LLM_ERROR,
    ANALYSIS_STATUS_LLM_UNAVAILABLE,
    ANALYSIS_STATUS_OK,
    JOB_PASSTHROUGH_FIELDS,
    SchemaError,
    empty_profile,
    status_profile,
    validate_llm_payload,
)
from llm.provider import LLMProvider, get_llm_provider

ANALYZE_JOB_SPEC = {
    "name": "analyze_job",
    "description": (
        "Analyze a World Job's JD facts into a structured JobProfile. "
        "Reasoner passes job_key only; Binding loads JD from World. "
        "Semantic extraction is performed by an LLM; this tool only validates schema. "
        "This tool does not match a resume, score a candidate, or recommend apply."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "job_key": {
                "type": "string",
                "description": "Identity of a live World Job. Binding loads JD facts.",
            },
            "job_id": {"type": "string"},
            "job_url": {"type": "string"},
            "job": {
                "type": "object",
                "description": "Compatibility only. Loop Binding ignores this and loads from World.",
            },
        },
        "required": ["job_key"],
    },
}

ANALYZE_JOB_SYSTEM_PROMPT = """你是职位JD分析器。只根据给定职位 JSON / JD 提取结构化 JobProfile。

严格规则：
1. 只使用职位文本中已经写明的事实。不要猜测，不要补充未出现的要求。
2. 不要读取或假设任何候选人简历、用户能力、匹配结果。
3. JobProfile 禁止候选人/匹配用语：不要出现“适合”“匹配”“推荐”“评分”“分数”“候选人”。
4. 不要给匹配分，不要建议投递。
5. 区分职责和要求：职责句子放入 responsibilities；能力/资格句子放入对应要求列表。
6. hard_requirements 只收录 JD 明确表达为必须/硬性的条件，例如：「必须…」「须具备…」「要求：本科及以上」「N 年及以上」「持有 XX 证书」等明示硬约束。
7. 不得因「看起来重要」升为 hard。preferred / bonus / 加分 / 有则更好 / 更佳 只能进入 bonus_requirements，禁止进入 hard_requirements。
8. 模型推测的隐含要求、未在 JD 写明的条件不得进入 hard_requirements。hard 条目必须 explicit=true，并尽量提供 evidence_quote（JD 原文摘录）。
9. 产品/岗位核心能力放入 core_requirements；业务领域放入 business_requirements；技术栈放入 technical_requirements；行业背景放入 industry_requirements。
10. importance 只能是 high / medium / low。硬性要求用 high，普通能力用 medium，加分项用 low。
11. explicit=true 表示 JD 明确写出；不得把推断项标成 hard。
12. job_summary 不超过100字，只概括职位本身，不要复制整段JD。
13. keywords 只保留有业务意义的词。
14. 数组字段必须出现，没有内容时返回 []。
15. 只返回一个 JSON 对象，不要 Markdown，不要解释。
16. 不要输出 analysis_status、error、job_id、job_title、company_name（由程序填写/透传）。

返回字段：
job_summary, hard_requirements, core_requirements, business_requirements,
technical_requirements, industry_requirements, bonus_requirements,
responsibilities, experience_requirements, keywords

RequirementItem：
{"requirement":"...","category":"...","importance":"high|medium|low","explicit":true,"evidence_quote":"...或null"}

experience_requirements：
{"years":"...或null","education":"...或null","seniority":"...或null","other":[]}
"""

_WHITESPACE_RE = re.compile(r"\s+")


def analyze_job(job: dict | str | None, llm_provider: LLMProvider | None = None) -> dict:
    """Extract a JobProfile from one normalized job object via LLM JSON + schema checks."""
    parsed, coerce_error = _coerce_job(job)
    if coerce_error:
        status = ANALYSIS_STATUS_INSUFFICIENT if coerce_error == "empty" else ANALYSIS_STATUS_FAILED
        message = "job_description and requirements are empty" if coerce_error == "empty" else coerce_error
        return status_profile(status, message, job=parsed if isinstance(parsed, dict) else None)

    if not _has_job_text(parsed):
        return status_profile(
            ANALYSIS_STATUS_INSUFFICIENT,
            "job_description and requirements are empty",
            job=parsed,
        )

    provider = llm_provider if llm_provider is not None else get_llm_provider()
    if provider is None:
        return status_profile(
            ANALYSIS_STATUS_LLM_UNAVAILABLE,
            "LLM provider is not configured",
            job=parsed,
        )

    return analyze_job_with_llm(parsed, provider)


def analyze_job_with_llm(job: dict, llm_provider: LLMProvider) -> dict:
    user_prompt = "请分析以下职位 JSON，只返回结构化 JobProfile JSON：\n" + json.dumps(
        _job_for_prompt(job),
        ensure_ascii=False,
        indent=2,
    )
    try:
        raw = llm_provider.complete_json(system=ANALYZE_JOB_SYSTEM_PROMPT, user=user_prompt)
    except Exception as exc:
        return status_profile(ANALYSIS_STATUS_LLM_ERROR, str(exc), job=job)

    if not isinstance(raw, dict):
        return status_profile(
            ANALYSIS_STATUS_LLM_ERROR,
            "LLM response JSON must be an object",
            job=job,
        )

    try:
        fields = validate_llm_payload(raw)
        _assert_quotes_in_job(fields, job)
    except SchemaError as exc:
        return status_profile(ANALYSIS_STATUS_FAILED, str(exc), job=job)

    result = empty_profile(job)
    result.update(fields)
    result["analysis_status"] = ANALYSIS_STATUS_OK
    result["error"] = None
    for key in JOB_PASSTHROUGH_FIELDS:
        result[key] = _optional_passthrough(job.get(key))
    return result


def _coerce_job(job: dict | str | None) -> tuple[dict | None, str | None]:
    if job is None:
        return None, "empty"
    if isinstance(job, str):
        text = job.strip()
        if not text:
            return None, "empty"
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None, "job must be an object"
        if not isinstance(parsed, dict):
            return None, "job must be an object"
        return parsed, None
    if not isinstance(job, dict):
        return None, "job must be an object"
    return job, None


def _has_job_text(job: dict) -> bool:
    return bool(_optional_passthrough(job.get("job_description")) or _optional_passthrough(job.get("requirements")))


def _job_for_prompt(job: dict) -> dict:
    keys = (
        "platform",
        "job_id",
        "job_url",
        "job_title",
        "company_name",
        "city",
        "salary",
        "experience",
        "education",
        "company_industry",
        "company_size",
        "job_description",
        "requirements",
        "benefits",
    )
    return {key: job.get(key) for key in keys}


def _assert_quotes_in_job(fields: dict, job: dict) -> None:
    haystack = _collapse(_job_corpus(job))
    if not haystack:
        raise SchemaError("job text is empty")
    for quote, owner in _walk_quotes(fields):
        if _collapse(quote) not in haystack:
            raise SchemaError(f"{owner} evidence_quote not found in job text")


def _job_corpus(job: dict) -> str:
    parts = [
        _optional_passthrough(job.get("job_title")),
        _optional_passthrough(job.get("job_description")),
        _optional_passthrough(job.get("requirements")),
        _optional_passthrough(job.get("benefits")),
        _optional_passthrough(job.get("experience")),
        _optional_passthrough(job.get("education")),
    ]
    return "\n".join(part for part in parts if part)


def _walk_quotes(value: Any, owner: str = "profile"):
    if isinstance(value, dict):
        quote = value.get("evidence_quote")
        if isinstance(quote, str) and quote.strip():
            yield quote, owner
        for key, inner in value.items():
            yield from _walk_quotes(inner, f"{owner}.{key}")
    elif isinstance(value, list):
        for index, inner in enumerate(value):
            yield from _walk_quotes(inner, f"{owner}[{index}]")


def _collapse(text: str) -> str:
    return _WHITESPACE_RE.sub("", text)


def _optional_passthrough(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "n/a"}:
        return None
    return text
