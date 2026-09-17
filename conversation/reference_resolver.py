"""Resolve ConversationReference against Conversation JobContexts.

Understanding supplies structured hints. This module never reads user text,
never defaults to the last job, and never guesses when more than one job fits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from conversation.job_context import JobContext, job_context_from_dict

RESOLUTION_RESOLVED = "resolved"
RESOLUTION_UNRESOLVED = "reference_unresolved"
RESOLUTION_NONE = "no_reference"

FIT_RANK = {
    "strong": 4,
    "moderate": 3,
    "weak": 2,
    "none": 1,
    "unknown": 0,
}
RECOMMENDATION_RANK = {
    "yes": 3,
    "weak": 2,
    "insufficient_evidence": 1,
    "no": 0,
}
RECOMMENDED_VALUES = {"yes", "weak"}


@dataclass
class ReferenceResolution:
    status: str
    job_context: JobContext | None = None
    candidates: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "job_context": self.job_context.to_dict() if self.job_context is not None else None,
            "context_id": self.job_context.context_id if self.job_context is not None else None,
            "job_key": self.job_context.job_key if self.job_context is not None else None,
            "candidates": list(self.candidates),
            "error": self.error,
            "clarification_needed": self.status == RESOLUTION_UNRESOLVED,
        }


def resolve_conversation_reference(
    reference: dict | None,
    job_contexts: list[JobContext] | list[dict] | None,
) -> ReferenceResolution:
    contexts = _coerce_contexts(job_contexts)
    if not isinstance(reference, dict):
        return ReferenceResolution(status=RESOLUTION_NONE, error="no conversation_reference")
    if reference.get("type") != "conversation_reference":
        return _unresolved(contexts, "conversation_reference.type is not conversation_reference")
    if reference.get("target_kind") != "job":
        return _unresolved(contexts, "conversation_reference.target_kind is not job")

    hint = reference.get("resolution_hint") or {}
    if hint is None:
        hint = {}
    if not isinstance(hint, dict):
        return _unresolved(contexts, "resolution_hint must be an object")

    remaining = list(contexts)
    remaining = _filter_round(remaining, hint)
    remaining = _filter_recommended(remaining, hint)
    remaining = _filter_semantic(remaining, hint.get("semantic_filters") or [])
    remaining = _filter_highest_match(remaining, hint)
    remaining = _filter_ordinal(remaining, hint)
    remaining = _apply_recency_uniqueness(remaining, hint)

    if len(remaining) == 1:
        return ReferenceResolution(
            status=RESOLUTION_RESOLVED,
            job_context=remaining[0],
            candidates=[remaining[0].context_id],
        )
    if not remaining:
        return _unresolved(contexts, "no JobContext matched the structured reference")
    return _unresolved(remaining, "multiple JobContexts matched; clarification needed")


def _coerce_contexts(job_contexts: list[JobContext] | list[dict] | None) -> list[JobContext]:
    result: list[JobContext] = []
    for item in job_contexts or []:
        ctx = item if isinstance(item, JobContext) else job_context_from_dict(item)
        if ctx is not None:
            result.append(ctx)
    return result


def _unresolved(contexts: list[JobContext], error: str) -> ReferenceResolution:
    return ReferenceResolution(
        status=RESOLUTION_UNRESOLVED,
        candidates=[ctx.context_id for ctx in contexts],
        error=error,
    )


def _filter_round(contexts: list[JobContext], hint: dict) -> list[JobContext]:
    if not contexts:
        return contexts
    rounds = [ctx.source_round for ctx in contexts if ctx.source_round is not None]
    max_round = max(rounds) if rounds else None
    wanted: int | None = None
    recency = hint.get("recency")
    offset = hint.get("round_offset")
    if isinstance(offset, int) and not isinstance(offset, bool):
        if max_round is None:
            return []
        if offset > 0:
            wanted = offset
        elif offset == 0:
            wanted = max_round + 1
        else:
            wanted = max_round + offset + 1
    elif recency == "previous_round":
        wanted = max_round
    elif recency == "this_round":
        wanted = max_round + 1 if max_round is not None else 1
    if wanted is None:
        return contexts
    matched = [ctx for ctx in contexts if ctx.source_round == wanted]
    return matched


def _filter_recommended(contexts: list[JobContext], hint: dict) -> list[JobContext]:
    if not hint.get("recommended_only"):
        return contexts
    return [ctx for ctx in contexts if _is_recommended(ctx)]


def _filter_semantic(contexts: list[JobContext], filters: Any) -> list[JobContext]:
    if not isinstance(filters, list) or not filters:
        return contexts
    remaining = contexts
    for item in filters:
        if not isinstance(item, dict):
            return []
        remaining = [ctx for ctx in remaining if _semantic_match(ctx, item)]
        if not remaining:
            return remaining
    return remaining


def _filter_highest_match(contexts: list[JobContext], hint: dict) -> list[JobContext]:
    if not hint.get("highest_match") or not contexts:
        return contexts
    scored = [(_match_score(ctx), ctx) for ctx in contexts]
    best = max(item[0] for item in scored)
    winners = [ctx for score, ctx in scored if score == best]
    if len(winners) == 1:
        return winners
    return []


def _filter_ordinal(contexts: list[JobContext], hint: dict) -> list[JobContext]:
    ordinal = hint.get("ordinal")
    if ordinal is None:
        return contexts
    if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 1:
        return []
    ordered = _sorted(contexts)
    index = ordinal - 1
    if index >= len(ordered):
        return []
    return [ordered[index]]


def _apply_recency_uniqueness(contexts: list[JobContext], hint: dict) -> list[JobContext]:
    """`last` means unique-in-context, not 'pick the most recent job'."""
    if hint.get("recency") != "last":
        return contexts
    if len(contexts) <= 1:
        return contexts
    return contexts


def _sorted(contexts: list[JobContext]) -> list[JobContext]:
    return sorted(
        contexts,
        key=lambda ctx: (
            ctx.source_round or 0,
            ctx.round_ordinal or 0,
            ctx.listed_order if ctx.listed_order is not None else 0,
            ctx.job_key,
        ),
    )


def _is_recommended(ctx: JobContext) -> bool:
    if ctx.stage == "recommended":
        return True
    rec = (ctx.recommendation_context or {}).get("recommendation")
    if rec in RECOMMENDED_VALUES:
        return True
    match = ctx.match_result or {}
    return match.get("recommendation") in RECOMMENDED_VALUES


def _match_score(ctx: JobContext) -> tuple[int, int]:
    rec = ctx.recommendation_context or {}
    match = ctx.match_result or {}
    fit = rec.get("overall_fit") or match.get("overall_fit")
    recommendation = rec.get("recommendation") or match.get("recommendation")
    return (
        FIT_RANK.get(str(fit), 0) if fit is not None else 0,
        RECOMMENDATION_RANK.get(str(recommendation), 0) if recommendation is not None else 0,
    )


def _semantic_match(ctx: JobContext, filt: dict) -> bool:
    kind = str(filt.get("kind") or "").strip()
    value = _fold(filt.get("value"))
    if not kind or not value:
        return False
    texts = _structured_texts(ctx, kind)
    return any(_contains(value, _fold(text)) for text in texts if text)


def _structured_texts(ctx: JobContext, kind: str) -> list[str]:
    match = ctx.match_result or {}
    rec = ctx.recommendation_context or {}
    profile = ctx.job_profile or {}
    listing = ctx.job_listing or {}
    if kind == "title":
        return [ctx.title or "", listing.get("job_title") or ""]
    if kind == "company":
        return [ctx.company or "", listing.get("company_name") or ""]
    if kind == "recommendation":
        return [
            str(rec.get("recommendation") or ""),
            str(match.get("recommendation") or ""),
            str(ctx.stage or ""),
        ]
    if kind == "overall_fit":
        return [str(rec.get("overall_fit") or ""), str(match.get("overall_fit") or "")]
    if kind in {"knowledge_gap", "missing_experience"}:
        texts = _gap_texts(rec.get("knowledge_gaps") or match.get("knowledge_gaps") or [])
        texts.extend(_requirement_texts(profile.get("industry_requirements") or []))
        texts.extend(_requirement_texts(profile.get("bonus_requirements") or []))
        texts.extend(_requirement_texts(profile.get("hard_requirements") or []))
        return texts
    if kind == "industry":
        texts = _requirement_texts(profile.get("industry_requirements") or [])
        texts.extend(_gap_texts(rec.get("knowledge_gaps") or match.get("knowledge_gaps") or []))
        texts.append(str(listing.get("company_industry") or ""))
        return texts
    if kind == "capability":
        texts = []
        for item in rec.get("capability_assessments") or match.get("capability_assessments") or []:
            if isinstance(item, dict):
                texts.extend(
                    [
                        str(item.get("dimension") or ""),
                        str(item.get("job_requirement_ref") or ""),
                        str(item.get("candidate_capability_ref") or ""),
                    ]
                )
        for field in (
            "core_requirements",
            "business_requirements",
            "technical_requirements",
            "hard_requirements",
        ):
            texts.extend(_requirement_texts(profile.get(field) or []))
        return texts
    return []


def _gap_texts(items: Any) -> list[str]:
    texts: list[str] = []
    for item in items or []:
        if isinstance(item, dict):
            texts.append(str(item.get("gap") or ""))
            texts.append(str(item.get("notes") or ""))
        elif isinstance(item, str):
            texts.append(item)
    return texts


def _requirement_texts(items: Any) -> list[str]:
    texts: list[str] = []
    for item in items or []:
        if isinstance(item, dict):
            texts.append(str(item.get("requirement") or ""))
        elif isinstance(item, str):
            texts.append(item)
    return texts


def _fold(value: Any) -> str:
    if value is None:
        return ""
    return "".join(str(value).split()).casefold()


def _contains(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return left in right or right in left
