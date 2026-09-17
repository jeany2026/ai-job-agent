"""Generate the next SearchPlan keyword via LLM JSON + schema checks. No synonym tables."""

from __future__ import annotations

from typing import Any

from llm.provider import LLMProvider, get_llm_provider

PLAN_SEARCH_SPEC = {
    "name": "plan_search",
    "description": (
        "Generate the next job-search keyword as a SearchPlan. "
        "Semantics come from an LLM; this tool only validates schema. "
        "It does not search, open, match, or apply."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "goal": {"type": "object", "description": "Structured UserGoal."},
            "used_keywords": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Keywords already used by SearchPlan queue.",
            },
            "cities": {"type": "array", "items": {"type": "string"}},
            "platform": {"type": "string"},
        },
        "required": ["goal"],
    },
}

ALLOWED_PLAN_STATUS = {
    "ok",
    "insufficient_data",
    "llm_unavailable",
    "llm_error",
    "analysis_failed",
}

PLAN_SEARCH_SYSTEM_PROMPT = """你是求职搜索规划器。只根据已结构化的 UserGoal 与已用关键词，生成下一条 SearchPlan。

严格规则：
1. 只使用 UserGoal 中已经出现的目标职位、城市、业务重点。不要编造用户未提到的行业词、技能词、同义词表。
2. keyword 必须是可直接用于招聘站搜索的职位关键词，且不能与 used_keywords 重复（大小写不敏感）。
3. 不要输出 Playwright、选择器、浏览器步骤、投递动作。
4. city 仅当 UserGoal.cities 或给定 cities 非空时填写其中一个；否则为 null。
5. rationale 用一句话说明为什么这条计划能补足当前推荐不足，必须能从 UserGoal 原文/字段得到依据。
6. 只返回一个 JSON 对象，不要 Markdown，不要解释。
7. 不要输出 analysis_status、error（由程序填写）。

返回字段：
keyword, city, rationale
"""


class PlanSearchSchemaError(ValueError):
    """Raised when SearchPlan JSON does not satisfy field rules."""


def empty_plan() -> dict:
    return {
        "analysis_status": None,
        "error": None,
        "keyword": None,
        "city": None,
        "rationale": None,
    }


def status_plan(status: str, error: str | None) -> dict:
    if status not in ALLOWED_PLAN_STATUS:
        raise PlanSearchSchemaError(f"illegal analysis_status: {status}")
    result = empty_plan()
    result["analysis_status"] = status
    result["error"] = error
    return result


def plan_search(payload: Any, llm_provider: LLMProvider | None = None) -> dict:
    """LLM JSON → schema-checked SearchPlan. Fail closed. Never invent a keyword in code."""
    arguments = payload if isinstance(payload, dict) else {}
    used = _used_keywords(arguments.get("used_keywords"))
    goal = arguments.get("goal") if isinstance(arguments.get("goal"), dict) else {}
    cities = arguments.get("cities") if isinstance(arguments.get("cities"), list) else []

    provider = llm_provider if llm_provider is not None else get_llm_provider()
    if provider is None:
        return status_plan("llm_unavailable", "LLM provider is not configured")

    user_prompt = (
        "请生成下一条 SearchPlan JSON。\n"
        "UserGoal:\n"
        + _safe_goal_text(goal)
        + "\nused_keywords:\n"
        + _safe_list_text(used)
        + "\ncities:\n"
        + _safe_list_text([str(item) for item in cities if isinstance(item, str)])
    )
    try:
        raw = provider.complete_json(system=PLAN_SEARCH_SYSTEM_PROMPT, user=user_prompt)
    except Exception as exc:
        return status_plan("llm_error", str(exc))

    if not isinstance(raw, dict):
        return status_plan("llm_error", "LLM response JSON must be an object")

    try:
        fields = validate_plan_payload(raw, used_keywords=used)
    except PlanSearchSchemaError as exc:
        return status_plan("analysis_failed", str(exc))

    result = empty_plan()
    result.update(fields)
    result["analysis_status"] = "ok"
    result["error"] = None
    return result


def validate_plan_payload(raw: Any, *, used_keywords: list[str] | None = None) -> dict:
    if not isinstance(raw, dict):
        raise PlanSearchSchemaError("SearchPlan JSON must be an object")
    keyword = _required_string(raw.get("keyword"), "keyword")
    city = _optional_string(raw.get("city"), "city")
    rationale = _optional_string(raw.get("rationale"), "rationale")
    used = {item.casefold() for item in (used_keywords or []) if item}
    if keyword.casefold() in used:
        raise PlanSearchSchemaError("keyword must not repeat a used SearchPlan keyword")
    return {"keyword": keyword, "city": city, "rationale": rationale}


def _required_string(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise PlanSearchSchemaError(f"{field} must be a string")
    text = value.strip()
    if not text:
        raise PlanSearchSchemaError(f"{field} must be a non-empty string")
    return text


def _optional_string(value: Any, field: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise PlanSearchSchemaError(f"{field} must be a string or null")
    text = value.strip()
    return text or None


def _used_keywords(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _safe_goal_text(goal: dict) -> str:
    keys = ("target_roles", "cities", "focus_areas", "exclude_companies", "salary_min", "raw_text")
    lines = []
    for key in keys:
        if key in goal:
            lines.append(f"{key}: {goal.get(key)!r}")
    return "\n".join(lines) if lines else "{}"


def _safe_list_text(items: list[str]) -> str:
    return repr(items)
