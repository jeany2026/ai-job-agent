"""Parse free-text or structured input into UserGoal. Semantics from LLM only.

Loop still calls this goal-only extractor. Unified turn understanding is
`understand_user_input`. Do not expand this tool into a catch-all parser.
"""

from __future__ import annotations

from typing import Any

from llm.provider import LLMProvider, get_llm_provider

PARSE_USER_GOAL_SPEC = {
    "name": "parse_user_goal",
    "description": (
        "Parse a user's job-search goal into a structured UserGoal. "
        "Free text is interpreted by an LLM; already-structured objects are schema-checked only. "
        "This tool does not search jobs or match a resume."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "goal": {
                "description": "Free-text goal, or a structured UserGoal object.",
            },
        },
        "required": ["goal"],
    },
}

ALLOWED_GOAL_STATUS = {
    "ok",
    "insufficient_data",
    "llm_unavailable",
    "llm_error",
    "analysis_failed",
}

PARSE_USER_GOAL_SYSTEM_PROMPT = """你是求职目标解析器。只根据用户输入提取结构化 UserGoal。

严格规则：
1. 只使用用户已经写明的事实。不要编造未出现的城市、职位、薪资数字、公司名。
2. 用户说「薪资符合目标」但没有给出数字时，salary_min 必须为 null，不要猜测。
3. target_roles 是用户想找的职位名称列表，不要改写成技能。
4. cities 是意向城市。没有写城市时返回 []。
5. focus_areas 是用户强调的业务/行业重点，没有则 []。
6. exclude_companies 只收录用户明确要排除的公司，没有则 []。
7. platforms 仅当用户点名招聘网站时填写；否则 []。
8. salary_min 是月薪下限整数（人民币元）。无法从原文得到数字则为 null。
9. 只返回一个 JSON 对象，不要 Markdown，不要解释。
10. 不要输出 analysis_status、error、raw_text（由程序填写）。

返回字段：
target_roles, cities, salary_min, focus_areas, exclude_companies, platforms
"""


class GoalSchemaError(ValueError):
    """Raised when UserGoal JSON does not satisfy field rules."""


def empty_goal(*, raw_text: str | None = None) -> dict:
    return {
        "analysis_status": None,
        "error": None,
        "raw_text": raw_text,
        "target_roles": [],
        "cities": [],
        "salary_min": None,
        "focus_areas": [],
        "exclude_companies": [],
        "platforms": [],
    }


def status_goal(status: str, error: str | None, *, raw_text: str | None = None) -> dict:
    if status not in ALLOWED_GOAL_STATUS:
        raise GoalSchemaError(f"illegal analysis_status: {status}")
    result = empty_goal(raw_text=raw_text)
    result["analysis_status"] = status
    result["error"] = error
    return result


def parse_user_goal(goal: Any, llm_provider: LLMProvider | None = None) -> dict:
    """Validate a structured UserGoal or parse free text via LLM JSON + schema checks."""
    structured, raw_text, coerce_error = _coerce_goal(goal)
    if coerce_error == "empty":
        return status_goal("insufficient_data", "goal is empty", raw_text=raw_text)
    if coerce_error:
        return status_goal("analysis_failed", coerce_error, raw_text=raw_text)

    if structured is not None:
        try:
            fields = validate_goal_payload(structured)
        except GoalSchemaError as exc:
            return status_goal("analysis_failed", str(exc), raw_text=raw_text)
        if not fields["target_roles"]:
            return status_goal(
                "insufficient_data",
                "target_roles is empty",
                raw_text=raw_text,
            )
        result = empty_goal(raw_text=raw_text)
        result.update(fields)
        result["analysis_status"] = "ok"
        result["error"] = None
        return result

    provider = llm_provider if llm_provider is not None else get_llm_provider()
    if provider is None:
        return status_goal("llm_unavailable", "LLM provider is not configured", raw_text=raw_text)

    user_prompt = "请解析以下求职目标，只返回结构化 UserGoal JSON：\n" + (raw_text or "")
    try:
        raw = provider.complete_json(system=PARSE_USER_GOAL_SYSTEM_PROMPT, user=user_prompt)
    except Exception as exc:
        return status_goal("llm_error", str(exc), raw_text=raw_text)

    if not isinstance(raw, dict):
        return status_goal("llm_error", "LLM response JSON must be an object", raw_text=raw_text)

    try:
        fields = validate_goal_payload(raw)
    except GoalSchemaError as exc:
        return status_goal("analysis_failed", str(exc), raw_text=raw_text)

    if not fields["target_roles"]:
        return status_goal("insufficient_data", "target_roles is empty", raw_text=raw_text)

    result = empty_goal(raw_text=raw_text)
    result.update(fields)
    result["analysis_status"] = "ok"
    result["error"] = None
    return result


def validate_goal_payload(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise GoalSchemaError("UserGoal JSON must be an object")
    return {
        "target_roles": _string_list(raw.get("target_roles"), "target_roles"),
        "cities": _string_list(raw.get("cities"), "cities"),
        "salary_min": _optional_salary(raw.get("salary_min")),
        "focus_areas": _string_list(raw.get("focus_areas"), "focus_areas"),
        "exclude_companies": _string_list(raw.get("exclude_companies"), "exclude_companies"),
        "platforms": _string_list(raw.get("platforms"), "platforms"),
    }


def is_structured_goal(value: Any) -> bool:
    return isinstance(value, dict) and "target_roles" in value


def _coerce_goal(goal: Any) -> tuple[dict | None, str | None, str | None]:
    if goal is None:
        return None, None, "empty"
    if isinstance(goal, str):
        text = goal.strip()
        if not text:
            return None, None, "empty"
        return None, text, None
    if not isinstance(goal, dict):
        return None, None, "goal must be a string or object"

    raw_text = _first_text(goal, "raw_text", "text", "goal", "query")
    if is_structured_goal(goal):
        return goal, raw_text, None
    if raw_text:
        return None, raw_text, None
    return None, None, "empty"


def _first_text(payload: dict, *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _string_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise GoalSchemaError(f"{field} must be an array")
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise GoalSchemaError(f"{field} items must be strings")
        text = item.strip()
        if not text:
            raise GoalSchemaError(f"{field} items must be non-empty strings")
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _optional_salary(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise GoalSchemaError("salary_min must be an integer or null")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    raise GoalSchemaError("salary_min must be an integer or null")
