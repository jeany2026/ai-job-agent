"""JobContext: session-scoped job working memory. Not CandidateProfile.

Reuses JobRecord / JobProfile / MatchResult / application evidence.
Does not own JobSearchTask lifecycle (RUNNING / WAITING_USER / ...).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from apply.schema import structured_application_evidence
from job.common_schema import job_key as make_job_key

JOB_CONTEXT_KEEP_STAGES = {"analyzed", "matched"}


@dataclass
class JobContext:
    """One job's working context inside a Conversation. Not a long-term profile."""

    context_id: str
    job_key: str
    platform: str | None = None
    job_id: str | None = None
    job_url: str | None = None
    title: str | None = None
    company: str | None = None
    job_profile: dict | None = None
    job_listing: dict | None = None
    match_result: dict | None = None
    application_evidence: str | None = None
    interpret_result: dict | None = None
    source_run_id: str | None = None
    source_round: int | None = None
    listed_order: int | None = None
    round_ordinal: int | None = None
    recommendation_context: dict | None = None
    stage: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "context_id": self.context_id,
            "job_key": self.job_key,
            "platform": self.platform,
            "job_id": self.job_id,
            "job_url": self.job_url,
            "title": self.title,
            "company": self.company,
            "job_profile": copy.deepcopy(self.job_profile) if self.job_profile is not None else None,
            "job_listing": copy.deepcopy(self.job_listing) if self.job_listing is not None else None,
            "match_result": copy.deepcopy(self.match_result) if self.match_result is not None else None,
            "application_evidence": self.application_evidence,
            "interpret_result": copy.deepcopy(self.interpret_result)
            if self.interpret_result is not None
            else None,
            "source_run_id": self.source_run_id,
            "source_round": self.source_round,
            "listed_order": self.listed_order,
            "round_ordinal": self.round_ordinal,
            "recommendation_context": copy.deepcopy(self.recommendation_context)
            if self.recommendation_context is not None
            else None,
            "stage": self.stage,
        }


def job_context_from_dict(raw: Any) -> JobContext | None:
    if not isinstance(raw, dict):
        return None
    job_key = _optional_text(raw.get("job_key")) or _optional_text(raw.get("context_id"))
    if not job_key:
        listing = raw.get("job_listing") if isinstance(raw.get("job_listing"), dict) else {}
        job_key = make_job_key(listing) or make_job_key(raw)
    if not job_key:
        return None
    context_id = _optional_text(raw.get("context_id")) or job_key
    return JobContext(
        context_id=context_id,
        job_key=job_key,
        platform=_optional_text(raw.get("platform")),
        job_id=_optional_text(raw.get("job_id")),
        job_url=_optional_text(raw.get("job_url")),
        title=_optional_text(raw.get("title") or raw.get("job_title")),
        company=_optional_text(raw.get("company") or raw.get("company_name")),
        job_profile=_optional_dict(raw.get("job_profile")),
        job_listing=_optional_dict(raw.get("job_listing")),
        match_result=_optional_dict(raw.get("match_result")),
        application_evidence=_optional_text(raw.get("application_evidence")),
        interpret_result=_optional_dict(raw.get("interpret_result")),
        source_run_id=_optional_text(raw.get("source_run_id")),
        source_round=_optional_int(raw.get("source_round")),
        listed_order=_optional_int(raw.get("listed_order")),
        round_ordinal=_optional_int(raw.get("round_ordinal")),
        recommendation_context=_optional_dict(raw.get("recommendation_context")),
        stage=_optional_text(raw.get("stage")),
    )


def job_context_from_record(
    record,
    *,
    source_run_id: str | None,
    source_round: int,
    round_ordinal: int,
) -> JobContext:
    job = record.opened or record.listed or {}
    match = record.match_result if isinstance(record.match_result, dict) else {}
    listing = copy.deepcopy(job) if isinstance(job, dict) else {}
    return JobContext(
        context_id=record.job_key,
        job_key=record.job_key,
        platform=_optional_text(listing.get("platform")),
        job_id=_optional_text(listing.get("job_id")),
        job_url=_optional_text(listing.get("job_url")),
        title=_optional_text(listing.get("job_title")),
        company=_optional_text(listing.get("company_name")),
        job_profile=copy.deepcopy(record.job_profile) if isinstance(record.job_profile, dict) else None,
        job_listing=listing or None,
        match_result=copy.deepcopy(record.match_result) if isinstance(record.match_result, dict) else None,
        application_evidence=structured_application_evidence(record.interpret_result),
        interpret_result=copy.deepcopy(record.interpret_result)
        if isinstance(record.interpret_result, dict)
        else None,
        source_run_id=source_run_id,
        source_round=source_round,
        listed_order=int(record.listed_order) if record.listed_order is not None else None,
        round_ordinal=round_ordinal,
        recommendation_context=_recommendation_context(match, stage=record.stage),
        stage=_optional_text(record.stage),
    )


def should_persist_job_record(record) -> bool:
    if record is None:
        return False
    if isinstance(record.job_profile, dict) or isinstance(record.match_result, dict):
        return True
    if any(isinstance(item, dict) and item.get("kind") == "surfaced" for item in (getattr(record, "decisions", None) or [])):
        return True
    return record.stage in JOB_CONTEXT_KEEP_STAGES


def job_contexts_from_state(
    state,
    *,
    source_run_id: str | None = None,
    source_round: int = 1,
) -> list[JobContext]:
    records = [
        record
        for record in getattr(state, "jobs", {}).values()
        if should_persist_job_record(record)
    ]
    records.sort(key=lambda item: (item.listed_order, item.job_key))
    run_id = source_run_id or getattr(getattr(state, "session", None), "session_id", None)
    contexts: list[JobContext] = []
    for index, record in enumerate(records, start=1):
        contexts.append(
            job_context_from_record(
                record,
                source_run_id=run_id,
                source_round=source_round,
                round_ordinal=index,
            )
        )
    return contexts


def job_contexts_from_session(session_context: dict | None) -> list[JobContext]:
    if not isinstance(session_context, dict):
        return []
    raw_items = session_context.get("job_contexts") or []
    if not isinstance(raw_items, list):
        return []
    contexts: list[JobContext] = []
    seen: set[str] = set()
    for item in raw_items:
        ctx = item if isinstance(item, JobContext) else job_context_from_dict(item)
        if ctx is None or ctx.context_id in seen:
            continue
        seen.add(ctx.context_id)
        contexts.append(ctx)
    return contexts


def upsert_job_contexts(existing: list[dict], incoming: list[JobContext]) -> list[dict]:
    merged = [job_context_from_dict(item) for item in existing]
    merged = [item for item in merged if item is not None]
    by_id = {item.context_id: item for item in merged}
    for ctx in incoming:
        previous = by_id.get(ctx.context_id)
        if previous is None:
            by_id[ctx.context_id] = ctx
            merged.append(ctx)
            continue
        updated = job_context_from_dict(previous.to_dict())
        assert updated is not None
        _overlay_latest_analysis(updated, ctx)
        by_id[ctx.context_id] = updated
        for index, item in enumerate(merged):
            if item.context_id == ctx.context_id:
                merged[index] = updated
                break
    return [item.to_dict() for item in merged]


def catalog_entry(ctx: JobContext) -> dict[str, Any]:
    """Prompt-safe summary. No job_id / job_key / job_url for Understanding to copy."""
    match = ctx.match_result or {}
    rec = ctx.recommendation_context or {}
    gaps = []
    for item in rec.get("knowledge_gaps") or match.get("knowledge_gaps") or []:
        if isinstance(item, dict) and item.get("gap"):
            gaps.append(str(item["gap"]))
        elif isinstance(item, str) and item.strip():
            gaps.append(item.strip())
    return {
        "source_round": ctx.source_round,
        "round_ordinal": ctx.round_ordinal,
        "title": ctx.title,
        "company": ctx.company,
        "stage": ctx.stage,
        "recommendation": rec.get("recommendation") or match.get("recommendation"),
        "overall_fit": rec.get("overall_fit") or match.get("overall_fit"),
        "knowledge_gaps": gaps[:8],
    }


def job_context_catalog(contexts: list[JobContext]) -> list[dict[str, Any]]:
    return [catalog_entry(ctx) for ctx in contexts]


def get_job_context(contexts: list[dict] | list[JobContext], context_id: str | None) -> JobContext | None:
    wanted = (context_id or "").strip()
    if not wanted:
        return None
    for item in contexts:
        ctx = item if isinstance(item, JobContext) else job_context_from_dict(item)
        if ctx is None:
            continue
        if ctx.context_id == wanted or ctx.job_key == wanted:
            return ctx
    return None


def save_job_context(contexts: list[dict], incoming: dict | JobContext) -> list[dict]:
    ctx = incoming if isinstance(incoming, JobContext) else job_context_from_dict(incoming)
    if ctx is None:
        return list(contexts)
    return upsert_job_contexts(contexts, [ctx])


def hydrate_job_record(state, ctx: JobContext):
    """Install a JobRecord from Persistence. Stage follows observed listing/open only.

    Historical job_profile / interpret_result are stamped restored/unverified and
    do not upgrade Live stage to analyzed/matched.
    """
    from agent.epistemic import observed_stage_from_record_fields, stamp_restored
    from agent.state import JobRecord

    listing = copy.deepcopy(ctx.job_listing) if isinstance(ctx.job_listing, dict) else {}
    if not listing:
        listing = {
            "platform": ctx.platform,
            "job_id": ctx.job_id,
            "job_url": ctx.job_url,
            "job_title": ctx.title,
            "company_name": ctx.company,
        }
    interpret = copy.deepcopy(ctx.interpret_result) if isinstance(ctx.interpret_result, dict) else {}
    if interpret:
        interpret = stamp_restored(interpret, source="persistence")
    profile = copy.deepcopy(ctx.job_profile) if isinstance(ctx.job_profile, dict) else None
    if isinstance(profile, dict):
        profile = stamp_restored(profile, source="persistence")
    has_detail = bool(
        (isinstance(listing.get("job_description"), str) and listing["job_description"].strip())
        or (isinstance(listing.get("requirements"), str) and listing["requirements"].strip())
    )
    prior = str(ctx.stage or "").strip().casefold()
    if not prior:
        # Hydrate/follow-up without explicit stage: continuity job identity → opened.
        prior = "opened"
    # Continuity open: prior opened/analyzed/matched means the job was opened before.
    opened = listing if has_detail or prior in {"opened", "analyzed", "matched"} else None
    stage = observed_stage_from_record_fields(
        opened=opened,
        listed=listing,
        persisted_stage=prior,
    )
    record = JobRecord(
        job_key=ctx.job_key,
        stage=stage,
        listed_order=ctx.listed_order if ctx.listed_order is not None else 0,
        listed=listing,
        opened=opened,
        interpret_result=interpret or None,
        job_profile=profile,
        match_result=None,
    )
    state.jobs[ctx.job_key] = record
    return record


def _overlay_latest_analysis(target: JobContext, incoming: JobContext) -> None:
    if incoming.job_profile is not None:
        target.job_profile = copy.deepcopy(incoming.job_profile)
    if incoming.job_listing is not None:
        target.job_listing = copy.deepcopy(incoming.job_listing)
    if incoming.match_result is not None:
        target.match_result = copy.deepcopy(incoming.match_result)
    if incoming.interpret_result is not None:
        target.interpret_result = copy.deepcopy(incoming.interpret_result)
    if incoming.application_evidence is not None:
        target.application_evidence = incoming.application_evidence
    if incoming.recommendation_context is not None:
        target.recommendation_context = copy.deepcopy(incoming.recommendation_context)
    if incoming.stage is not None:
        target.stage = incoming.stage
    if incoming.title:
        target.title = incoming.title
    if incoming.company:
        target.company = incoming.company
    if incoming.job_url:
        target.job_url = incoming.job_url
    if incoming.platform:
        target.platform = incoming.platform
    if incoming.job_id:
        target.job_id = incoming.job_id


def _recommendation_context(match: dict, *, stage: str | None) -> dict[str, Any]:
    return {
        "recommendation": match.get("recommendation"),
        "overall_fit": match.get("overall_fit"),
        "rationale": match.get("rationale"),
        "capability_assessments": copy.deepcopy(match.get("capability_assessments") or []),
        "knowledge_gaps": copy.deepcopy(match.get("knowledge_gaps") or []),
        "risks": copy.deepcopy(match.get("risks") or []),
        "hard_requirements_met": match.get("hard_requirements_met"),
        "stage": stage,
    }


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_dict(value: Any) -> dict | None:
    return copy.deepcopy(value) if isinstance(value, dict) else None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None
