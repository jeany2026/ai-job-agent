"""Candidate working memory: incremental facts, not a one-shot profile gate."""

from __future__ import annotations

from typing import Any

from candidate.normalize import normalize_candidate_fact
from candidate.semantic import (
    CONTENT_TYPE_INTAKE_TEXT,
    CONTENT_TYPE_OTHER_ATTACHMENT,
    CONTENT_TYPE_PROJECT_DOCUMENT,
    CONTENT_TYPE_RESUME,
    CONTENT_TYPE_USER_MESSAGE,
    content_type_for_source_kind,
    fold_text,
    has_provenance,
    id_list,
    make_claim,
    make_evidence,
    make_interpretation,
    next_layer_id,
    uninterpreted_evidence_ids,
)

MEMORY_EMPTY = "empty"
MEMORY_PARTIAL = "partial"
MEMORY_USABLE = "usable"
MEMORY_INSUFFICIENT = "insufficient"

USABLE_KINDS = {
    "career",
    "education",
    "project",
    "skill",
    "domain",
    "transfer",
    "identity",
}


def empty_memory() -> dict:
    return {
        "status": MEMORY_EMPTY,
        "facts": [],
        "evidence": [],
        "claims": [],
        "interpretations": [],
        "verifications": [],
        "uninterpreted_evidence_ids": [],
        "unresolved": [],
        "ingest_status": None,
        "ingest_error_code": None,
        "ingest_error": None,
    }


def memory_is_usable(memory: dict | None) -> bool:
    if not isinstance(memory, dict):
        return False
    return refresh_memory_status(dict(memory))["status"] in {MEMORY_USABLE, MEMORY_PARTIAL}


def refresh_memory_status(memory: dict) -> dict:
    result = dict(memory)
    facts = [item for item in (result.get("facts") or []) if isinstance(item, dict)]
    usable = [
        item
        for item in facts
        if item.get("kind") in USABLE_KINDS and item.get("evidence_status") != "unsupported"
    ]
    if not facts:
        result["status"] = MEMORY_EMPTY
    elif usable:
        result["status"] = MEMORY_USABLE if len(usable) >= 1 else MEMORY_PARTIAL
    else:
        result["status"] = MEMORY_INSUFFICIENT
    result["facts"] = facts
    result["evidence"] = [item for item in (result.get("evidence") or []) if isinstance(item, dict)]
    result["claims"] = [item for item in (result.get("claims") or []) if isinstance(item, dict)]
    result["interpretations"] = [
        item for item in (result.get("interpretations") or []) if isinstance(item, dict)
    ]
    result["verifications"] = [
        item for item in (result.get("verifications") or []) if isinstance(item, dict)
    ]
    result["uninterpreted_evidence_ids"] = uninterpreted_evidence_ids(result)
    result.setdefault("unresolved", [])
    return result


def note_unresolved(memory: dict, *, code: str, message: str, field: str | None = None) -> dict:
    result = dict(memory)
    items = list(result.get("unresolved") or [])
    items.append({"code": code, "message": message, "field": field})
    result["unresolved"] = items
    return refresh_memory_status(result)


def merge_facts(memory: dict | None, facts: list[dict]) -> dict:
    result = refresh_memory_status(memory or empty_memory())
    existing = list(result.get("facts") or [])
    seen = {_fact_key(item) for item in existing}
    for item in facts:
        key = _fact_key(item)
        if key in seen:
            continue
        seen.add(key)
        existing.append(item)
    result["facts"] = existing
    return refresh_memory_status(result)


def merge_layer(memory: dict | None, key: str, items: list[dict]) -> dict:
    result = refresh_memory_status(memory or empty_memory())
    existing = list(result.get(key) or [])
    seen = {str(item.get("id") or "") for item in existing if item.get("id")}
    seen_payload = {_layer_key(item) for item in existing}
    for item in items:
        if not isinstance(item, dict):
            continue
        ident = str(item.get("id") or "")
        if ident and ident in seen:
            continue
        payload_key = _layer_key(item)
        if payload_key in seen_payload:
            continue
        if ident:
            seen.add(ident)
        seen_payload.add(payload_key)
        existing.append(item)
    result[key] = existing
    return refresh_memory_status(result)


def merge_memory(base: dict | None, incoming: dict | None) -> dict:
    result = refresh_memory_status(base or empty_memory())
    extra = incoming if isinstance(incoming, dict) else {}
    result = merge_facts(result, extra.get("facts") or [])
    for key in ("evidence", "claims", "interpretations", "verifications"):
        result = merge_layer(result, key, extra.get(key) or [])
    for item in extra.get("unresolved") or []:
        if isinstance(item, dict):
            result = note_unresolved(
                result,
                code=str(item.get("code") or "SCHEMA_ERROR"),
                message=str(item.get("message") or "field skipped"),
                field=item.get("field"),
            )
    if extra.get("ingest_status") is not None:
        result["ingest_status"] = extra.get("ingest_status")
        result["ingest_error_code"] = extra.get("ingest_error_code")
        result["ingest_error"] = extra.get("ingest_error")
    return refresh_memory_status(result)


def append_evidence(memory: dict | None, evidence: dict) -> dict:
    result = refresh_memory_status(memory or empty_memory())
    folded = fold_text(evidence.get("content"))
    if folded:
        for existing in result.get("evidence") or []:
            if fold_text(existing.get("content")) == folded:
                return result
    if not evidence.get("id"):
        evidence = dict(evidence)
        evidence["id"] = next_layer_id(result.get("evidence"), "ev-")
    return merge_layer(result, "evidence", [evidence])


def ensure_text_evidence(
    memory: dict | None,
    text: str,
    *,
    content_type: str,
    origin: str,
    source: str | None = None,
    source_ref: str | None = None,
) -> tuple[dict, str | None]:
    if not isinstance(text, str) or not text.strip():
        return refresh_memory_status(memory or empty_memory()), None
    cleaned = text.strip()
    result = refresh_memory_status(memory or empty_memory())
    folded = fold_text(cleaned)
    for existing in result.get("evidence") or []:
        if fold_text(existing.get("content")) == folded:
            return result, existing.get("id")
    evidence = make_evidence(
        content=cleaned,
        content_type=content_type,
        origin=origin,
        source=source or origin,
        source_ref=source_ref,
        existing=result.get("evidence"),
    )
    result = append_evidence(result, evidence)
    return result, evidence["id"]


def seed_turn_evidence(
    memory: dict | None,
    *,
    user_message: str | None,
    attachments: list[dict] | None = None,
) -> dict:
    """Resume / user_message / attachments enter the same Evidence list. Tag only."""
    result = refresh_memory_status(memory or empty_memory())
    if isinstance(user_message, str) and user_message.strip():
        result, _ = ensure_text_evidence(
            result,
            user_message,
            content_type=CONTENT_TYPE_USER_MESSAGE,
            origin="run_agent.message",
            source="user_input",
        )
    for item in attachments or []:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        content_type = (
            item.get("content_type") or item.get("kind") or item.get("source_kind") or CONTENT_TYPE_OTHER_ATTACHMENT
        )
        origin = "run_agent.resume" if content_type == CONTENT_TYPE_RESUME else "run_agent.attachment"
        source = "resume" if content_type == CONTENT_TYPE_RESUME else "attachment"
        result, _ = ensure_text_evidence(
            result,
            text,
            content_type=str(content_type),
            origin=origin,
            source=source,
            source_ref=item.get("filename") or item.get("name"),
        )
    return refresh_memory_status(result)


def evidence_ids_for_source_kind(memory: dict | None, source_kind: str | None) -> list[str]:
    from candidate.semantic import CONTENT_TYPE_INTAKE_TEXT

    content_type = content_type_for_source_kind(source_kind)
    extra = refresh_memory_status(memory or empty_memory())
    all_ids = [item["id"] for item in extra.get("evidence") or [] if item.get("id")]
    if content_type is None:
        return all_ids
    found = [
        item["id"]
        for item in extra.get("evidence") or []
        if item.get("id") and item.get("content_type") == content_type
    ]
    if found:
        return found
    if content_type == CONTENT_TYPE_USER_MESSAGE:
        found = [
            item["id"]
            for item in extra.get("evidence") or []
            if item.get("id") and item.get("content_type") in {CONTENT_TYPE_USER_MESSAGE, "user_statement"}
        ]
        if found:
            return found
    if content_type == CONTENT_TYPE_PROJECT_DOCUMENT:
        found = [
            item["id"]
            for item in extra.get("evidence") or []
            if item.get("id")
            and item.get("content_type") in {CONTENT_TYPE_PROJECT_DOCUMENT, CONTENT_TYPE_OTHER_ATTACHMENT}
        ]
        if found:
            return found
    # Neutral admitted intake may support any LLM-assigned source_kind.
    neutral = [
        item["id"]
        for item in extra.get("evidence") or []
        if item.get("id") and item.get("content_type") == CONTENT_TYPE_INTAKE_TEXT
    ]
    return neutral or all_ids


def claim_from_fact(fact: dict, *, existing: list[dict] | None = None) -> dict:
    display = fact.get("display") or fact.get("name") or fact.get("kind") or "claim"
    return make_claim(
        kind=str(fact.get("kind") or "other"),
        statement=str(display),
        derived_from=id_list(fact.get("derived_from")),
        name=fact.get("name"),
        value=fact.get("value"),
        source_kind=fact.get("source_type") or fact.get("source_kind"),
        existing=existing,
        quote=fact.get("source_reference") or fact.get("quote"),
    )


def ingest_from_supplement(supplement: dict | None, *, derived_from: list[str] | None = None) -> list[dict]:
    """Compose facts from already-structured Understanding claims. No new semantics."""
    extra = supplement if isinstance(supplement, dict) else {}
    facts: list[dict] = []
    shared = id_list(derived_from)
    for item in extra.get("statements") or []:
        fact = normalize_candidate_fact(
            {
                "kind": "identity",
                "name": "statement",
                "value": item,
                "source_kind": "user_statement",
                "derived_from": shared,
            },
            default_kind="identity",
        )
        if fact:
            facts.append(fact)
    for item in extra.get("claimed_capabilities") or []:
        if not isinstance(item, dict):
            continue
        fact = normalize_candidate_fact(
            {
                "kind": "skill",
                "name": item.get("name"),
                "value": item.get("name"),
                "source_kind": item.get("source_kind") or "user_statement",
                "quote": item.get("quote"),
                "derived_from": shared or id_list(item.get("derived_from")),
            },
            default_kind="skill",
        )
        if fact:
            facts.append(fact)
    for item in extra.get("claimed_projects") or []:
        if not isinstance(item, dict):
            continue
        fact = normalize_candidate_fact(
            {
                "kind": "project",
                "name": item.get("name"),
                "value": item.get("description") or item.get("name"),
                "source_kind": item.get("source_kind") or "project_document",
                "quote": item.get("quote"),
                "derived_from": shared or id_list(item.get("derived_from")),
            },
            default_kind="project",
        )
        if fact:
            facts.append(fact)
    for item in extra.get("acknowledged_gaps") or []:
        if not isinstance(item, dict):
            continue
        fact = normalize_candidate_fact(
            {
                "kind": "other",
                "name": "gap",
                "value": item.get("gap"),
                "quote": item.get("quote"),
                "source_kind": "user_statement",
                "derived_from": shared or id_list(item.get("derived_from")),
            },
            default_kind="other",
        )
        if fact:
            facts.append(fact)
    for item in extra.get("transfer_claims") or []:
        if not isinstance(item, dict):
            continue
        fact = normalize_candidate_fact(
            {
                "kind": "transfer",
                "name": item.get("claim") or "transfer",
                "value": {
                    "claim": item.get("claim"),
                    "from_domain": item.get("from_domain"),
                    "to_domain": item.get("to_domain"),
                },
                "quote": item.get("quote"),
                "source_kind": "user_statement",
                "derived_from": shared or id_list(item.get("derived_from")),
            },
            default_kind="transfer",
        )
        if fact:
            facts.append(fact)
    return facts


def ingest_understanding(memory: dict | None, supplement: dict | None, *, support_check: list[dict] | None = None) -> dict:
    """Write Claim + Interpretation. Same-turn support_check is never Verification."""
    result = refresh_memory_status(memory or empty_memory())
    extra = supplement if isinstance(supplement, dict) else {}
    facts: list[dict] = []
    claims: list[dict] = []
    for item in extra.get("statements") or []:
        derived = evidence_ids_for_source_kind(result, "user_statement")
        fact = normalize_candidate_fact(
            {
                "kind": "identity",
                "name": "statement",
                "value": item,
                "source_kind": "user_statement",
                "derived_from": derived,
            },
            default_kind="identity",
        )
        if fact:
            facts.append(fact)
            claims.append(claim_from_fact(fact, existing=list(result.get("claims") or []) + claims))
    for raw, kind, name, value, default_source in (
        *(
            (item, "skill", item.get("name"), item.get("name"), item.get("source_kind") or "user_statement")
            for item in extra.get("claimed_capabilities") or []
            if isinstance(item, dict)
        ),
        *(
            (
                item,
                "project",
                item.get("name"),
                item.get("description") or item.get("name"),
                item.get("source_kind") or "project_document",
            )
            for item in extra.get("claimed_projects") or []
            if isinstance(item, dict)
        ),
        *(
            (item, "other", "gap", item.get("gap"), "user_statement")
            for item in extra.get("acknowledged_gaps") or []
            if isinstance(item, dict)
        ),
        *(
            (
                item,
                "transfer",
                item.get("claim") or "transfer",
                {
                    "claim": item.get("claim"),
                    "from_domain": item.get("from_domain"),
                    "to_domain": item.get("to_domain"),
                },
                "user_statement",
            )
            for item in extra.get("transfer_claims") or []
            if isinstance(item, dict)
        ),
    ):
        derived = evidence_ids_for_source_kind(result, default_source)
        fact = normalize_candidate_fact(
            {
                "kind": kind,
                "name": name,
                "value": value,
                "source_kind": default_source,
                "quote": raw.get("quote") if isinstance(raw, dict) else None,
                "derived_from": derived,
            },
            default_kind=kind,
        )
        if fact:
            facts.append(fact)
            claims.append(claim_from_fact(fact, existing=list(result.get("claims") or []) + claims))
    result = merge_facts(result, facts)
    result = merge_layer(result, "claims", claims)
    if support_check:
        result = merge_layer(result, "interpretations", [item for item in support_check if isinstance(item, dict)])
    evidence_ids = [item["id"] for item in result.get("evidence") or [] if item.get("id")]
    result = merge_layer(
        result,
        "interpretations",
        [
            make_interpretation(
                kind="understand_user_input",
                derived_from=evidence_ids,
                payload={"source": "understand_user_input"},
                existing=result.get("interpretations"),
            )
        ],
    )
    return refresh_memory_status(result)


def ingest_from_llm_payload(raw: Any, *, derived_from: list[str] | None = None) -> tuple[list[dict], list[dict]]:
    """Accept facts[] or a loose CandidateProfile-shaped object. No fail-closed schema."""
    unresolved: list[dict] = []
    refs = id_list(derived_from)
    if not isinstance(raw, dict):
        return [], [{"code": "SCHEMA_ERROR", "message": "ingest payload is not an object", "field": None}]
    if isinstance(raw.get("facts"), list):
        facts = []
        for index, item in enumerate(raw["facts"]):
            payload = dict(item) if isinstance(item, dict) else item
            if isinstance(payload, dict) and refs:
                payload = dict(payload)
                payload["derived_from"] = refs
            fact = normalize_candidate_fact(payload)
            if fact is None:
                unresolved.append(
                    {
                        "code": "SCHEMA_ERROR",
                        "message": "skipped one ingest item after normalization",
                        "field": f"facts[{index}]",
                    }
                )
                continue
            facts.append(fact)
        return facts, unresolved
    return _facts_from_profile_shape(raw, derived_from=refs)


def _facts_from_profile_shape(raw: dict, *, derived_from: list[str] | None = None) -> tuple[list[dict], list[dict]]:
    facts: list[dict] = []
    unresolved: list[dict] = []
    _add(facts, raw, kind="identity", name="summary", value=raw.get("summary"), derived_from=derived_from)
    _add(facts, raw, kind="career", name="years_experience", value=raw.get("years_experience"), derived_from=derived_from)
    _add(facts, raw, kind="education", name="education", value=raw.get("education"), derived_from=derived_from)
    for role in raw.get("target_roles") or []:
        _add(facts, raw, kind="career", name="target_role", value=role, derived_from=derived_from)
    for city in raw.get("locations") or []:
        _add(facts, raw, kind="identity", name="location", value=city, derived_from=derived_from)
    for field, kind in (
        ("product_capabilities", "skill"),
        ("business_capabilities", "skill"),
        ("technical_capabilities", "skill"),
        ("management_experience", "skill"),
        ("direct_capabilities", "skill"),
    ):
        for item in raw.get(field) or []:
            if isinstance(item, dict):
                _add(
                    facts,
                    item,
                    kind=kind,
                    name=item.get("name"),
                    value=item.get("description") or item.get("name"),
                    quote=_first_quote(item),
                    view_field=field,
                    category=item.get("category"),
                    derived_from=derived_from,
                )
            else:
                unresolved.append(
                    {"code": "SCHEMA_ERROR", "message": "skipped non-object capability", "field": field}
                )
    for item in raw.get("project_experience") or []:
        if isinstance(item, dict):
            _add(
                facts,
                item,
                kind="project",
                name=item.get("name"),
                value=item.get("description") or item.get("name"),
                quote=_first_quote(item),
                derived_from=derived_from,
            )
    for item in raw.get("industry_experience") or []:
        if isinstance(item, dict):
            _add(
                facts,
                item,
                kind="domain",
                name=item.get("industry") or "industry",
                value=item.get("description") or item.get("industry"),
                quote=_first_quote(item),
                derived_from=derived_from,
            )
    for item in raw.get("transferable_capabilities") or []:
        if isinstance(item, dict):
            _add(
                facts,
                item,
                kind="transfer",
                name=item.get("name"),
                value={
                    "from_domain": item.get("from_domain"),
                    "to_domain": item.get("to_domain_hint"),
                    "rationale": item.get("transfer_rationale"),
                },
                quote=_first_quote(item),
                derived_from=derived_from,
            )
    return facts, unresolved


def project_profile_view(memory: dict | None, *, candidate_id: str | None = None) -> dict:
    """Read-only projection. Unverified stays unverified. Sourceless skills are omitted."""
    from candidate.schema import empty_profile

    view = empty_profile(candidate_id=candidate_id)
    view["analysis_status"] = "ok"
    view["error"] = None
    view["kind"] = "interpretation_projection"
    memory = refresh_memory_status(memory or empty_memory())
    for item in memory.get("facts") or []:
        kind = item.get("kind")
        name = item.get("name")
        display = item.get("display") or _stringify(item.get("value"))
        derived = id_list(item.get("derived_from"))
        if kind == "skill" and not has_provenance(item):
            continue
        if kind == "identity" and name == "summary" and display:
            view["summary"] = (display or "")[:200]
            view["summary_derived_from"] = derived
        elif name == "years_experience" and display:
            view["years_experience"] = display
            view["years_experience_derived_from"] = derived
        elif kind == "education" and display:
            view["education"] = display
            view["education_derived_from"] = derived
        elif name == "target_role" and display:
            view["target_roles"] = _append_unique(view["target_roles"], display)
        elif name == "location" and display:
            view["locations"] = _append_unique(view["locations"], display)
        elif kind == "skill" and (item.get("name") or display):
            bucket = item.get("view_field") if item.get("view_field") in _CAPABILITY_VIEW_FIELDS else "direct_capabilities"
            view[bucket].append(_projected_item(item, display, extra={"kind": "direct", "category": item.get("category") or "other", "explicit": True}))
        elif kind == "project" and (item.get("name") or display):
            view["project_experience"].append(
                _projected_item(
                    item,
                    display,
                    extra={"role": None, "outcomes": None, "capabilities_demonstrated": [], "explicit": True},
                )
            )
        elif kind == "domain" and display:
            view["industry_experience"].append(
                _projected_item(
                    item,
                    display,
                    name_key="industry",
                    extra={"role": None, "years_or_duration": None, "explicit": True},
                )
            )
        elif kind == "transfer" and (item.get("name") or display):
            value = item.get("value") if isinstance(item.get("value"), dict) else {}
            view["transferable_capabilities"].append(
                _projected_item(
                    item,
                    display,
                    extra={
                        "from_domain": value.get("from_domain") or "unknown",
                        "to_domain_hint": value.get("to_domain"),
                        "transfer_rationale": value.get("rationale") or display,
                    },
                    confidence=0.6,
                )
            )
    return view


_CAPABILITY_VIEW_FIELDS = {
    "product_capabilities",
    "business_capabilities",
    "technical_capabilities",
    "management_experience",
    "direct_capabilities",
}


def _add(
    facts: list[dict],
    raw: dict,
    *,
    kind: str,
    name: Any,
    value: Any,
    quote: Any = None,
    view_field: str | None = None,
    category: Any = None,
    derived_from: list[str] | None = None,
) -> None:
    if value is None:
        return
    fact = normalize_candidate_fact(
        {
            "kind": kind,
            "name": name,
            "value": value,
            "source_kind": raw.get("source_kind"),
            "quote": quote or _first_quote(raw),
            "derived_from": derived_from or raw.get("derived_from"),
        },
        default_kind=kind,
    )
    if not fact:
        return
    if view_field in _CAPABILITY_VIEW_FIELDS:
        fact["view_field"] = view_field
    if isinstance(category, str) and category.strip():
        fact["category"] = category.strip()
    facts.append(fact)


def _first_quote(item: dict) -> str | None:
    for evidence in item.get("evidence") or []:
        if isinstance(evidence, dict) and isinstance(evidence.get("quote"), str):
            return evidence["quote"]
    quote = item.get("quote")
    return quote if isinstance(quote, str) else None


def _view_evidence(item: dict) -> list[dict]:
    quote = item.get("source_reference")
    if isinstance(quote, str) and quote.strip():
        return [{"quote": quote, "location_hint": None, "field": item.get("name")}]
    display = item.get("display")
    if isinstance(display, str) and display.strip():
        return [{"quote": display, "location_hint": None, "field": item.get("name")}]
    return []


def _projected_item(
    item: dict,
    display: str | None,
    *,
    extra: dict | None = None,
    name_key: str = "name",
    confidence: float = 0.7,
) -> dict:
    projected = {
        name_key: item.get("name") or display,
        "description": display,
        "evidence": _view_evidence(item),
        "confidence": confidence,
        "derived_from": id_list(item.get("derived_from")),
        "evidence_status": item.get("evidence_status") or "unverified",
    }
    if extra:
        projected.update(extra)
    return projected


def _fact_key(item: dict) -> tuple:
    return (
        str(item.get("kind") or ""),
        str(item.get("name") or ""),
        str(item.get("display") or ""),
        str(item.get("source_type") or item.get("source_kind") or ""),
        tuple(id_list(item.get("derived_from"))),
    )


def _layer_key(item: dict) -> tuple:
    return (
        str(item.get("kind") or ""),
        str(item.get("statement") or item.get("content") or item.get("name") or ""),
        tuple(id_list(item.get("derived_from"))),
        str((item.get("payload") or {}).get("claim_id") or ""),
        str(item.get("consistency_hint") or item.get("status") or ""),
    )


def _append_unique(items: list, value: str) -> list:
    result = list(items)
    if value not in result:
        result.append(value)
    return result


def _stringify(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict) and value.get("value") is not None:
        if value.get("unit") == "years":
            return f"{value['value']}年"
        return str(value["value"])
    return str(value)
