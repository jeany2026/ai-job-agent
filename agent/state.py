"""Runtime AgentState. Development Phase ≠ runtime status."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from job.common_schema import job_key as make_job_key

RUNTIME_STATUSES = (
    "INIT",
    "RUNNING",
    "PARSING_GOAL",
    "GATHERING_CANDIDATE",
    "PLANNING",
    "SEARCHING",
    "ENRICHING_JOBS",
    "ANALYZING",
    "MATCHING",
    "DECIDING",
    "FILTERING",
    "RANKING",
    "REPORTING",
    "EXECUTING",
    "WAITING_USER",
    "NEEDS_HUMAN",
    "DONE",
    "FAILED",
)

# Ends the current Loop/HTTP run. NEEDS_HUMAN / WAITING_USER are pauses for the
# user, not proof the JobSearchTask is business-complete — resume via a new turn
# on the same Conversation (Observation → World → Reasoner), not a new Task.
TERMINAL_STATUSES = {"DONE", "NEEDS_HUMAN", "FAILED", "WAITING_USER"}

# World progress only. Reasoner/user decisions and Verifications live on
# JobRecord.decisions / verifications. excluded/recommended are legacy labels
# that production must not write.
WORLD_JOB_STAGES = (
    "listed",
    "opened",
    "analyzed",
    "matched",
)
JOB_STAGES = WORLD_JOB_STAGES + (
    "excluded",
    "recommended",
)
DECISION_SURFACED = "surfaced"
DECISION_SKIPPED = "skipped"

PROFILE_PENDING = "pending"
PROFILE_OK = "ok"
PROFILE_FAILED = "failed"

DEFAULT_CONSTRAINTS = {
    "blacklist": [],
    "already_applied": [],
    "salary_min": None,
    "cities": [],
    "platforms": ["mock"],
    "max_search_results": 30,
    "max_open_jd": 10,
    "max_llm_calls": 80,
    "min_recommend": 1,
}

SEARCH_TOOL_BY_SOURCE = {
    "mock": "search_jobs",
    "boss": "search_jobs",
    "liepin": "search_jobs",
    "job51": "search_jobs",
}

OPEN_TOOL_BY_SOURCE = {
    "mock": "open_job",
    "boss": "open_job",
    "liepin": "open_job",
    "job51": "open_job",
}

SUPPORTED_DATA_SOURCES = tuple(SEARCH_TOOL_BY_SOURCE.keys())

LLM_TOOLS = {
    "parse_user_goal",
    "understand_user_input",
    "analyze_candidate",
    "interpret_job_actions",
    "analyze_job",
    "match_job",
    "plan_search",
    "reason_next_action",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class Session:
    session_id: str
    status: str
    created_at: str


@dataclass
class CandidateState:
    resume_ref: str | None = None
    profile: dict | None = None
    profile_status: str = PROFILE_PENDING
    candidate_id: str | None = None
    profile_version: int | None = None
    source_resume_hash: str | None = None
    supplement: dict | None = None
    memory: dict | None = None
    # Set when this turn successfully wrote CandidateProfileStore (not a semantic flag).
    profile_persisted_this_turn: bool = False


@dataclass
class SearchPlan:
    plan_id: str
    keyword: str | None = None
    city: str | None = None
    platform: str = "mock"
    exhausted: bool = False


@dataclass
class SearchState:
    plans: list[SearchPlan] = field(default_factory=list)
    active_plan_id: str | None = None
    exhausted: list[str] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)
    stop_reason: str | None = None
    # Execution continuity for search_jobs fresh|continue. Not a Reasoner Action.
    session: Any = None


@dataclass
class Constraints:
    blacklist: list[str] = field(default_factory=list)
    already_applied: list[str] = field(default_factory=list)
    salary_min: int | None = None
    cities: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=lambda: ["mock"])
    max_search_results: int = 30
    max_open_jd: int = 10
    max_llm_calls: int = 50
    min_recommend: int = 1
    data_source: str = "mock"


@dataclass
class JobRecord:
    job_key: str
    stage: str
    listed_order: int
    listed: dict
    opened: dict | None = None
    interpret_result: dict | None = None
    job_profile: dict | None = None
    match_result: dict | None = None
    exclude_reason: str | None = None
    constraint_flags: list[str] = field(default_factory=list)
    rank_score: tuple | None = None
    interpretations: list[dict] = field(default_factory=list)
    verifications: list[dict] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)


@dataclass
class AgentState:
    session: Session
    pending_goal: Any = None
    goal: dict | None = None
    understanding: dict | None = None
    candidate_context: dict | None = None
    attachments: list[dict] = field(default_factory=list)
    preferences: list[str] = field(default_factory=list)
    profile_dir: str | None = None
    session_context: dict | None = None
    follow_up_of: str | None = None
    resolved_job_context: dict | None = None
    reference_resolution: dict | None = None
    task_intent: dict | None = None
    active_tasks: list[dict] = field(default_factory=list)
    conversation_id: str | None = None
    job_search_task_id: str | None = None
    task_control: str | None = None
    task_view: dict | None = None
    application_history: list[dict] = field(default_factory=list)
    pending_job_key: str | None = None
    candidate: CandidateState = field(default_factory=CandidateState)
    search: SearchState = field(default_factory=SearchState)
    constraints: Constraints = field(default_factory=Constraints)
    jobs: dict[str, JobRecord] = field(default_factory=dict)
    decisions: list[dict] = field(default_factory=list)
    human_gate: dict | None = None
    errors: list[dict] = field(default_factory=list)
    output: dict | None = None
    last_raw_observation: dict | None = None
    progress: str | None = None
    understanding_status: str = "not_understood"
    intake: list[dict] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    claims: list[dict] = field(default_factory=list)
    interpretations: list[dict] = field(default_factory=list)
    verifications: list[dict] = field(default_factory=list)

    @property
    def status(self) -> str:
        return self.session.status

    @status.setter
    def status(self, value: str) -> None:
        if value not in RUNTIME_STATUSES:
            raise ValueError(f"illegal runtime status: {value}")
        self.session.status = value


def new_agent_state(
    *,
    resume: str | dict | None = None,
    goal_input: Any,
    constraints: dict | None = None,
    data_source: str = "mock",
    candidate_id: str = "cand-1",
    attachments: list[dict] | None = None,
    candidate_profile: dict | None = None,
    profile_record: dict | None = None,
    profile_dir: str | None = None,
    session_context: dict | None = None,
    conversation_id: str | None = None,
) -> AgentState:
    user_constraints = constraints or {}
    merged = {**DEFAULT_CONSTRAINTS, **user_constraints}
    merged["data_source"] = data_source
    if data_source not in SEARCH_TOOL_BY_SOURCE:
        raise ValueError(f"unsupported data_source: {data_source}")
    if "platforms" not in user_constraints:
        merged["platforms"] = [data_source]

    resume_text, resume_id = _coerce_resume_ref(resume, candidate_id)
    attachment_records = _normalize_attachment_records(attachments)
    record = profile_record if isinstance(profile_record, dict) else None
    profile = candidate_profile
    version = None
    source_hash = None
    if record:
        profile = record.get("profile") if profile is None else profile
        version = record.get("profile_version")
        source_hash = record.get("source_resume_hash")
        if record.get("candidate_id"):
            resume_id = str(record["candidate_id"])
    profile_status = PROFILE_PENDING
    if isinstance(profile, dict) and profile.get("analysis_status") == "ok":
        profile_status = PROFILE_OK
    constraint_obj = Constraints(
        blacklist=_string_list(merged.get("blacklist")),
        already_applied=_string_list(merged.get("already_applied")),
        salary_min=_optional_int(merged.get("salary_min")),
        cities=_string_list(merged.get("cities")),
        platforms=_string_list(merged.get("platforms")) or [data_source],
        max_search_results=int(merged.get("max_search_results") or 30),
        max_open_jd=int(merged.get("max_open_jd") or 10),
        max_llm_calls=int(merged.get("max_llm_calls") or 50),
        min_recommend=int(merged.get("min_recommend") or 1),
        data_source=data_source,
    )
    stats = {
        "listed": 0,
        "listed_count": 0,
        "opened": 0,
        "llm_calls": 0,
        "searches": 0,
        "search_executed_count": 0,
        "newly_ingested_total": 0,
        "duplicate_total": 0,
        "zero_increment_count": 0,
        "recommended": 0,
        "excluded": 0,
    }
    from agent.intake import seed_turn_intake
    from candidate.memory import empty_memory, merge_memory

    # Intake only — no Evidence / business type from carrier.
    intake = seed_turn_intake(
        user_message=_user_message_text(goal_input),
        attachments=attachment_records,
        resume_text=resume_text,
    )
    memory = empty_memory()
    if record:
        memory = merge_memory(
            memory,
            {
                "evidence": record.get("evidence") or [],
                "claims": record.get("claims") or [],
                "interpretations": record.get("interpretations") or [],
                "verifications": record.get("verifications") or [],
            },
        )
        if isinstance(profile, dict):
            profile = dict(profile)
            profile["kind"] = "interpretation_projection"
            profile.setdefault("derived_from", [item.get("id") for item in memory.get("evidence") or [] if item.get("id")])
    state = AgentState(
        session=Session(session_id=str(uuid.uuid4()), status="INIT", created_at=utc_now()),
        pending_goal=goal_input,
        goal=None,
        attachments=attachment_records,
        intake=intake,
        profile_dir=profile_dir,
        session_context=session_context if isinstance(session_context, dict) else None,
        conversation_id=(conversation_id or "").strip() or None,
        candidate=CandidateState(
            resume_ref=resume_text,
            profile=profile if isinstance(profile, dict) else None,
            profile_status=profile_status,
            candidate_id=resume_id or candidate_id,
            profile_version=int(version) if isinstance(version, int) and not isinstance(version, bool) else None,
            source_resume_hash=str(source_hash).strip() if source_hash else None,
            memory=memory,
        ),
        search=SearchState(stats=stats),
        constraints=constraint_obj,
    )
    bind_semantic_layers(state, memory)
    return state


def bind_semantic_layers(state: AgentState, memory: dict | None) -> None:
    extra = memory if isinstance(memory, dict) else {}
    state.candidate.memory = extra
    state.evidence = list(extra.get("evidence") or [])
    state.claims = list(extra.get("claims") or [])
    state.interpretations = list(extra.get("interpretations") or [])
    state.verifications = list(extra.get("verifications") or [])


def search_tool_name(state: AgentState) -> str:
    return SEARCH_TOOL_BY_SOURCE[state.constraints.data_source]


def open_tool_name(state: AgentState) -> str:
    return OPEN_TOOL_BY_SOURCE[state.constraints.data_source]


def jobs_in_stage(state: AgentState, *stages: str) -> list[JobRecord]:
    wanted = set(stages)
    records = [record for record in state.jobs.values() if record.stage in wanted]
    records.sort(key=lambda item: item.listed_order)
    return records


def in_flight_job(state: AgentState) -> JobRecord | None:
    """Quota/stat helper. Loop / reduce / payload must not use this to pick a target."""
    for record in jobs_in_stage(state, "opened", "analyzed"):
        if record.stage == "excluded":
            continue
        if record.opened and record.interpret_result is None:
            return record
        if record.interpret_result is not None and record.job_profile is None:
            return record
        if record.job_profile is not None and record.match_result is None:
            return record
    return None


def next_job_to_open(state: AgentState) -> JobRecord | None:
    """Quota/stat helper. Loop / reduce / payload must not use this to pick a target."""
    for record in jobs_in_stage(state, "listed"):
        return record
    return None


def note_job_decision(
    record: JobRecord,
    *,
    kind: str,
    source: str,
    payload: dict | None = None,
) -> None:
    """Record a Reasoner or user decision. Does not change world stage."""
    item = {
        "kind": kind,
        "source": source,
        "layer": "decision",
        "at": utc_now(),
    }
    if payload:
        item.update(payload)
    record.decisions.append(item)


def job_has_decision(record: JobRecord, kind: str) -> bool:
    return any(item.get("kind") == kind for item in (record.decisions or []))


def record_decision(state: AgentState, *, observation: dict, action_name: str, extra: dict | None = None) -> None:
    item = {
        "at": utc_now(),
        "status": state.session.status,
        "action": action_name,
        "observation": observation,
    }
    if extra:
        item.update(extra)
    state.decisions.append(item)


def record_error(state: AgentState, message: str, *, kind: str = "error", extra: dict | None = None) -> None:
    item = {"at": utc_now(), "kind": kind, "message": message}
    if extra:
        item.update(extra)
    state.errors.append(item)


def job_record_key(job: dict) -> str | None:
    return make_job_key(job)


def active_plan(state: AgentState) -> SearchPlan | None:
    if not state.search.active_plan_id:
        return None
    for plan in state.search.plans:
        if plan.plan_id == state.search.active_plan_id:
            return plan
    return None


def used_plan_keywords(state: AgentState) -> list[str]:
    seen: list[str] = []
    folded: set[str] = set()
    for plan in state.search.plans:
        keyword = (plan.keyword or "").strip()
        if not keyword:
            continue
        key = keyword.casefold()
        if key in folded:
            continue
        folded.add(key)
        seen.append(keyword)
    return seen


def next_goal_plan_fields(state: AgentState) -> tuple[str | None, str | None]:
    """Next SearchPlan keyword/city from already-structured UserGoal fields. Not a synonym list."""
    used = {item.casefold() for item in used_plan_keywords(state)}
    goal = state.goal or {}
    roles = goal.get("target_roles") or []
    cities = goal.get("cities") or state.constraints.cities or []
    city = cities[0] if cities else None
    for role in roles:
        text = str(role).strip() if role is not None else ""
        if not text:
            continue
        if text.casefold() in used:
            continue
        return text, city
    return None, city


def mark_active_plan_exhausted(state: AgentState) -> None:
    plan = active_plan(state)
    if plan is None:
        return
    plan.exhausted = True
    if plan.plan_id not in state.search.exhausted:
        state.search.exhausted.append(plan.plan_id)


def _coerce_resume_ref(resume: str | dict | None, default_id: str) -> tuple[str | None, str | None]:
    if resume is None:
        return None, default_id
    if isinstance(resume, str):
        text = resume.strip()
        return (text or None), default_id
    if not isinstance(resume, dict):
        return None, default_id
    candidate_id = resume.get("candidate_id") or default_id
    for key in ("text", "resume_text", "resume", "content"):
        value = resume.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip(), str(candidate_id)
    return None, str(candidate_id) if candidate_id else default_id


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item).strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip()
    if not text.isdigit():
        return None
    return int(text)


def _user_message_text(goal_input: Any) -> str | None:
    if isinstance(goal_input, str) and goal_input.strip():
        return goal_input.strip()
    if not isinstance(goal_input, dict):
        return None
    for key in ("raw_text", "message", "text", "goal", "query"):
        value = goal_input.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _normalize_attachment_records(attachments: list[dict] | None) -> list[dict]:
    """Keep upload blobs as raw records. Do not assign business content_type."""
    records: list[dict] = []
    seen: set[str] = set()
    for item in attachments or []:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            folded = "".join(text.split()).casefold()
            if folded in seen:
                continue
            seen.add(folded)
        hint = item.get("client_hint") or item.get("kind") or item.get("content_type")
        record: dict = {
            "filename": item.get("filename") or item.get("name"),
            "text": text.strip() if isinstance(text, str) and text.strip() else item.get("text"),
        }
        if isinstance(hint, str) and hint.strip():
            record["client_hint"] = hint.strip()
        records.append(record)
    return records
