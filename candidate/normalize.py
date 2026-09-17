"""Type/shape normalization for candidate facts. No semantic guessing."""

from __future__ import annotations

from typing import Any

FACT_KINDS = {
    "identity",
    "career",
    "education",
    "project",
    "skill",
    "domain",
    "transfer",
    "preference",
    "constraint",
    "other",
}

NORMALIZATION_OK = "ok"
NORMALIZATION_COERCED = "coerced"
NORMALIZATION_UNCERTAIN = "uncertain"
NORMALIZATION_SKIPPED = "skipped"


def normalize_candidate_fact(raw: Any, *, default_kind: str = "other") -> dict | None:
    """Turn one extracted item into an internal fact. Never raises for type mismatch."""
    if raw is None:
        return None
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        raw = {"name": text, "value": text, "kind": default_kind}
    if not isinstance(raw, dict):
        return {
            "kind": default_kind if default_kind in FACT_KINDS else "other",
            "name": "value",
            "value": None,
            "display": str(raw),
            "extracted_value": raw,
            "normalization_status": NORMALIZATION_UNCERTAIN,
        }

    kind = raw.get("kind") or raw.get("category") or default_kind
    if not isinstance(kind, str) or kind.strip() not in FACT_KINDS:
        kind = default_kind if default_kind in FACT_KINDS else "other"
    else:
        kind = kind.strip()

    name = _as_label(raw.get("name") or raw.get("fact") or raw.get("gap") or raw.get("claim"))
    value, status = normalize_fact_value(raw.get("value"), name=name)
    if name is None and value is None and raw.get("extracted_value") is None:
        return None
    if name is None:
        name = kind
    display = _as_label(raw.get("display")) or _display_for(value) or name
    derived = raw.get("derived_from") if isinstance(raw.get("derived_from"), list) else []
    return {
        "kind": kind,
        "name": name,
        "value": value,
        "display": display,
        "extracted_value": raw.get("value") if "value" in raw else raw.get("extracted_value"),
        "source_type": _as_label(raw.get("source_type") or raw.get("source_kind")),
        "source_reference": _as_label(raw.get("source_reference") or raw.get("quote")),
        "normalization_status": status,
        "evidence_status": "unverified",
        "derived_from": [str(item).strip() for item in derived if item is not None and str(item).strip()],
    }


def normalize_fact_value(value: Any, *, name: str | None = None) -> tuple[Any, str]:
    if value is None:
        return None, NORMALIZATION_OK
    if isinstance(value, bool):
        return value, NORMALIZATION_OK
    if isinstance(value, int):
        if name == "years_experience":
            return {"value": value, "unit": "years"}, NORMALIZATION_COERCED
        return value, NORMALIZATION_OK
    if isinstance(value, float) and value.is_integer():
        return normalize_fact_value(int(value), name=name)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None, NORMALIZATION_OK
        if name == "years_experience" and text.isdigit():
            return {"value": int(text), "unit": "years"}, NORMALIZATION_COERCED
        if name == "years_experience" and text.endswith("年") and text[:-1].strip().isdigit():
            return {"value": int(text[:-1].strip()), "unit": "years"}, NORMALIZATION_COERCED
        return text, NORMALIZATION_OK
    if isinstance(value, dict):
        if "years" in value:
            years, status = normalize_fact_value(value.get("years"), name="years_experience")
            return years, status if years is not None else NORMALIZATION_UNCERTAIN
        if "value" in value and ("unit" in value or name == "years_experience"):
            inner, status = normalize_fact_value(value.get("value"), name=name)
            unit = value.get("unit") or ("years" if name == "years_experience" else None)
            if isinstance(inner, dict):
                return inner, status
            if inner is None:
                return None, NORMALIZATION_UNCERTAIN
            return {"value": inner, "unit": unit}, NORMALIZATION_COERCED
        return value, NORMALIZATION_OK
    if isinstance(value, list):
        items = []
        for item in value:
            if isinstance(item, str) and item.strip():
                items.append(item.strip())
            elif isinstance(item, (int, float)) and not isinstance(item, bool):
                items.append(item)
            else:
                return value, NORMALIZATION_UNCERTAIN
        return items, NORMALIZATION_OK
    return str(value), NORMALIZATION_UNCERTAIN


def _as_label(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    return None


def _display_for(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict) and value.get("value") is not None:
        unit = value.get("unit")
        if unit == "years":
            return f"{value['value']}年"
        return str(value.get("value"))
    if isinstance(value, list):
        return "、".join(str(item) for item in value)
    return str(value)
