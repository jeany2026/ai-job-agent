"""Read-only output assembly. Does not change Job stage or decide next action."""

from __future__ import annotations

from agent.state import DECISION_SURFACED, AgentState, JobRecord, job_has_decision
from rules.rank import rank_records


def build_report(state: AgentState) -> dict:
    recommended = []
    for record in rank_records(_surfaced_jobs(state)):
        recommended.append(_match_item(record))

    excluded = []
    for record in _blacklist_jobs(state):
        job = _job_fields(record)
        excluded.append(
            {
                "job_key": record.job_key,
                "platform": job.get("platform"),
                "job_id": job.get("job_id"),
                "job_title": job.get("job_title"),
                "company_name": job.get("company_name"),
                "reason": "blacklist",
                "recommendation": (record.match_result or {}).get("recommendation"),
            }
        )

    jobs = [_memory_job_item(record) for record in _all_jobs(state)]
    report = {
        "session_id": state.session.session_id,
        "status": "DONE",
        "goal": state.goal,
        "candidate_id": state.candidate.candidate_id,
        "platforms": _platforms_in_report(state, recommended, excluded, jobs),
        "recommended": recommended,
        "excluded": excluded,
        "jobs": jobs,
        "stats": dict(state.search.stats),
        "min_recommend": state.constraints.min_recommend,
        "stop_reason": state.search.stop_reason,
        "search_plans": [
            {
                "plan_id": plan.plan_id,
                "keyword": plan.keyword,
                "city": plan.city,
                "exhausted": plan.exhausted,
            }
            for plan in state.search.plans
        ],
        "exhausted_plan_ids": list(state.search.exhausted),
    }
    if state.follow_up_of:
        record = state.jobs.get(state.follow_up_of)
        resolved = state.resolved_job_context or {}
        report["follow_up"] = True
        report["resolved_job_key"] = state.follow_up_of
        report["original_job_profile"] = resolved.get("job_profile")
        report["original_match_result"] = resolved.get("match_result")
        report["job_profile"] = record.job_profile if record is not None else resolved.get("job_profile")
        report["match_result"] = record.match_result if record is not None else None
        report["candidate_context"] = state.candidate_context
        report["application_evidence"] = resolved.get("application_evidence")
    if state.reference_resolution:
        report["reference_resolution"] = state.reference_resolution
    return report


def build_waiting_user_report(state: AgentState, record: JobRecord, *, rationale: str | None = None) -> dict:
    """One job for Human Gate. Not a ranked batch of recommendations."""
    job = _job_fields(record)
    match = record.match_result if isinstance(record.match_result, dict) else {}
    assessments = match.get("capability_assessments") or []
    profile = record.job_profile if isinstance(record.job_profile, dict) else {}
    item = {
        "job_key": record.job_key,
        "platform": job.get("platform"),
        "job_id": job.get("job_id"),
        "job_title": job.get("job_title"),
        "company_name": job.get("company_name"),
        "job_url": job.get("job_url"),
        "city": job.get("city"),
        "salary": job.get("salary"),
        "recommendation": match.get("recommendation"),
        "overall_fit": match.get("overall_fit"),
        "hard_requirements_met": match.get("hard_requirements_met"),
        "capability_assessments": assessments,
        "rationale": match.get("rationale"),
        "risks": match.get("risks") or [],
        "knowledge_gaps": match.get("knowledge_gaps") or [],
        "matched_capabilities": [
            entry for entry in assessments if isinstance(entry, dict) and entry.get("outcome") == "direct"
        ],
        "transferable_capabilities": [
            entry for entry in assessments if isinstance(entry, dict) and entry.get("outcome") == "transferable"
        ],
        "job_summary": profile.get("job_summary"),
        "decision_rationale": rationale,
        "stage": record.stage,
    }
    return {
        "session_id": state.session.session_id,
        "status": "WAITING_USER",
        "needs_user_decision": True,
        "goal": state.goal,
        "candidate_id": state.candidate.candidate_id,
        "platforms": list(state.constraints.platforms or []),
        "recommended": [item],
        "waiting_job": item,
        "excluded": [],
        "stats": dict(state.search.stats),
        "message": "找到一个值得你决定的职位。",
        "task": dict(state.task_view) if state.task_view else None,
    }


def _surfaced_jobs(state: AgentState) -> list[JobRecord]:
    """Jobs the Reasoner explicitly surfaced. Match recommendation is Interpretation only."""
    records = [record for record in state.jobs.values() if job_has_decision(record, DECISION_SURFACED)]
    records.sort(key=lambda item: item.listed_order)
    return records


def _blacklist_jobs(state: AgentState) -> list[JobRecord]:
    records = [
        record
        for record in state.jobs.values()
        if "blacklist" in (record.constraint_flags or []) or record.exclude_reason == "blacklist"
    ]
    records.sort(key=lambda item: item.listed_order)
    return records


def _all_jobs(state: AgentState) -> list[JobRecord]:
    records = list(state.jobs.values())
    records.sort(key=lambda item: item.listed_order)
    return records


def _match_item(record: JobRecord) -> dict:
    job = _job_fields(record)
    match = record.match_result or {}
    return {
        "job_key": record.job_key,
        "platform": job.get("platform"),
        "job_id": job.get("job_id"),
        "job_title": job.get("job_title"),
        "company_name": job.get("company_name"),
        "recommendation": match.get("recommendation"),
        "overall_fit": match.get("overall_fit"),
        "hard_requirements_met": match.get("hard_requirements_met"),
        "capability_assessments": match.get("capability_assessments") or [],
        "rationale": match.get("rationale"),
        "stage": record.stage,
    }


def _memory_job_item(record: JobRecord) -> dict:
    job = _job_fields(record)
    match = record.match_result if isinstance(record.match_result, dict) else {}
    return {
        "job_key": record.job_key,
        "platform": job.get("platform"),
        "job_id": job.get("job_id"),
        "job_title": job.get("job_title"),
        "company_name": job.get("company_name"),
        "stage": record.stage,
        "constraint_flags": list(record.constraint_flags or []),
        "exclude_reason": record.exclude_reason,
        "recommendation": match.get("recommendation"),
        "decisions": list(record.decisions or []),
        "verifications": list(record.verifications or []),
    }


def _job_fields(record: JobRecord) -> dict:
    return record.opened or record.listed or {}


def _platforms_in_report(
    state: AgentState,
    recommended: list[dict],
    excluded: list[dict],
    jobs: list[dict] | None = None,
) -> list[str]:
    seen: list[str] = []
    folded: set[str] = set()
    for item in recommended + excluded + list(jobs or []):
        platform = item.get("platform")
        if not platform:
            continue
        key = str(platform).casefold()
        if key in folded:
            continue
        folded.add(key)
        seen.append(str(platform))
    for platform in state.constraints.platforms or []:
        key = str(platform).casefold()
        if key in folded:
            continue
        folded.add(key)
        seen.append(str(platform))
    return seen
