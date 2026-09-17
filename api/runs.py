"""In-memory background wrapper around run_agent. No queue, no database."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from conversation.job_context import (
    get_job_context,
    job_context_catalog,
    job_context_from_dict,
    job_contexts_from_state,
    save_job_context,
    upsert_job_contexts,
)
from agent.state import RUNTIME_STATUSES, AgentState
from api.serialize import progress_message, serialize_agent_state
from storage.candidate_profile import DEFAULT_CANDIDATE_ID


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class AgentRun:
    run_id: str
    run_phase: str
    created_at: str
    status: str | None = None
    result: dict[str, Any] | None = None
    error_code: str | None = None
    message: str | None = None
    conversation_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Conversation:
    conversation_id: str
    previous_goal: dict | None = None
    previous_preferences: list[str] = field(default_factory=list)
    previous_matching_context: str | None = None
    last_recommended: list[dict] = field(default_factory=list)
    job_contexts: list[dict] = field(default_factory=list)
    turns: list[dict] = field(default_factory=list)
    source_round: int = 0
    task_ids: list[str] = field(default_factory=list)
    pending_human_gate: dict | None = None
    human_gate_world_snapshot: dict | None = None


class ConversationStore:
    """Sticky user session across Agent runs. Not CandidateProfile storage."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, Conversation] = {}

    def ensure(self, conversation_id: str | None) -> Conversation:
        cid = (conversation_id or "").strip()
        with self._lock:
            if cid and cid in self._items:
                return self._items[cid]
            item = Conversation(conversation_id=str(uuid.uuid4()))
            self._items[item.conversation_id] = item
            return item

    def session_context(self, conversation_id: str | None) -> dict | None:
        cid = (conversation_id or "").strip()
        if not cid:
            return None
        with self._lock:
            item = self._items.get(cid)
        if item is None:
            return None
        if (
            not item.previous_goal
            and not item.last_recommended
            and not item.job_contexts
            and not item.task_ids
            and not item.pending_human_gate
        ):
            return None
        contexts = [dict(ctx) for ctx in item.job_contexts]
        parsed = [ctx for ctx in (job_context_from_dict(raw) for raw in contexts) if ctx is not None]
        payload = {
            "previous_goal": item.previous_goal,
            "previous_preferences": list(item.previous_preferences),
            "previous_matching_context": item.previous_matching_context,
            "last_recommended": list(item.last_recommended),
            "job_contexts": contexts,
            "job_context_catalog": job_context_catalog(parsed),
            "task_ids": list(item.task_ids),
        }
        if isinstance(item.pending_human_gate, dict):
            payload["pending_human_gate"] = dict(item.pending_human_gate)
        if isinstance(item.human_gate_world_snapshot, dict):
            payload["human_gate_world_snapshot"] = dict(item.human_gate_world_snapshot)
        return payload

    def get_job_contexts(self, conversation_id: str | None) -> list[dict]:
        cid = (conversation_id or "").strip()
        if not cid:
            return []
        with self._lock:
            item = self._items.get(cid)
            if item is None:
                return []
            return [dict(ctx) for ctx in item.job_contexts]

    def get_job_context(self, conversation_id: str | None, context_id: str | None) -> dict | None:
        ctx = get_job_context(self.get_job_contexts(conversation_id), context_id)
        return ctx.to_dict() if ctx is not None else None

    def save_job_context(self, conversation_id: str | None, context: dict) -> dict | None:
        cid = (conversation_id or "").strip()
        if not cid or not isinstance(context, dict):
            return None
        with self._lock:
            item = self._items.get(cid)
            if item is None:
                item = Conversation(conversation_id=cid)
                self._items[cid] = item
            item.job_contexts = save_job_context(item.job_contexts, context)
        return self.get_job_context(cid, context.get("context_id") or context.get("job_key"))

    def remember(self, conversation_id: str | None, state: AgentState, *, run_id: str | None = None) -> None:
        cid = (conversation_id or "").strip()
        if not cid:
            return
        goal = state.goal if isinstance(state.goal, dict) else None
        output = state.output if isinstance(state.output, dict) else {}
        recommended = list(output.get("recommended") or [])
        preferences = list(state.preferences or [])
        matching_context = None
        if isinstance(state.understanding, dict):
            matching_context = state.understanding.get("matching_context")
        with self._lock:
            item = self._items.get(cid)
            if item is None:
                item = Conversation(conversation_id=cid)
                self._items[cid] = item
            if goal and goal.get("analysis_status") == "ok":
                item.previous_goal = dict(goal)
            if preferences:
                item.previous_preferences = preferences
            if matching_context:
                item.previous_matching_context = matching_context
            if recommended:
                item.last_recommended = recommended
            item.source_round = int(item.source_round or 0) + 1
            incoming = job_contexts_from_state(
                state,
                source_run_id=run_id or state.session.session_id,
                source_round=item.source_round,
            )
            if incoming:
                item.job_contexts = upsert_job_contexts(item.job_contexts, incoming)
            task_id = state.job_search_task_id
            if task_id and task_id not in item.task_ids:
                item.task_ids.append(task_id)
            if state.session.status == "NEEDS_HUMAN" and isinstance(state.human_gate, dict):
                from agent.human_gate_continuity import snapshot_world_for_human_gate

                item.pending_human_gate = dict(state.human_gate)
                item.human_gate_world_snapshot = snapshot_world_for_human_gate(state)
            else:
                # Gate cleared when the turn leaves NEEDS_HUMAN (resolved, failed, done, waiting).
                item.pending_human_gate = None
                item.human_gate_world_snapshot = None
            item.turns.append(
                {
                    "source_run_id": run_id or state.session.session_id,
                    "source_round": item.source_round,
                    "task_kind": (state.understanding or {}).get("task_kind")
                    if isinstance(state.understanding, dict)
                    else None,
                    "status": state.session.status,
                    "human_gate_reason": (state.human_gate or {}).get("reason")
                    if isinstance(state.human_gate, dict)
                    else None,
                }
            )


class RunStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, AgentRun] = {}

    def create(self, *, conversation_id: str | None = None) -> AgentRun:
        run = AgentRun(
            run_id=str(uuid.uuid4()),
            run_phase="queued",
            created_at=utc_now(),
            conversation_id=conversation_id,
        )
        with self._lock:
            self._runs[run.run_id] = run
        return run

    def get(self, run_id: str) -> AgentRun | None:
        with self._lock:
            return self._runs.get(run_id)

    def update(self, run_id: str, **changes: Any) -> AgentRun | None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return None
            for key, value in changes.items():
                setattr(run, key, value)
            return run

    def snapshot(self, run_id: str) -> dict[str, Any] | None:
        run = self.get(run_id)
        if run is None:
            return None
        payload = {
            "run_id": run.run_id,
            "conversation_id": run.conversation_id,
            "run_phase": run.run_phase,
            "status": run.status,
            "created_at": run.created_at,
            "ok": False,
            "error_code": run.error_code,
            "message": run.message,
            "recommended": [],
            "excluded": [],
            "human_gate": None,
            "errors": [],
            "profile_updated": False,
        }
        if run.result:
            payload.update(run.result)
            payload["run_id"] = run.run_id
            payload["run_phase"] = run.run_phase
            payload["conversation_id"] = run.conversation_id
        elif run.run_phase == "running":
            payload["message"] = run.message or progress_message(run.status)
        return payload


def start_agent_run(
    store: RunStore,
    *,
    message: str,
    data_source: str,
    run_agent_fn: Callable[..., AgentState],
    attachments: list[dict] | None = None,
    resume_text: str | None = None,
    llm_provider=None,
    profile_dir: str | None = None,
    candidate_id: str = DEFAULT_CANDIDATE_ID,
    session_context: dict | None = None,
    conversation_id: str | None = None,
    conversation_store: ConversationStore | None = None,
    task_store=None,
) -> AgentRun:
    run = store.create(conversation_id=conversation_id)
    thread = threading.Thread(
        target=_execute,
        kwargs={
            "store": store,
            "run_id": run.run_id,
            "message": message,
            "attachments": attachments or [],
            "resume_text": resume_text,
            "data_source": data_source,
            "run_agent_fn": run_agent_fn,
            "llm_provider": llm_provider,
            "profile_dir": profile_dir,
            "candidate_id": candidate_id,
            "session_context": session_context,
            "conversation_id": conversation_id,
            "conversation_store": conversation_store,
            "task_store": task_store,
        },
        daemon=True,
        name=f"agent-run-{run.run_id[:8]}",
    )
    store.update(
        run.run_id,
        run_phase="running",
        status="PARSING_GOAL",
        message=progress_message("PARSING_GOAL"),
    )
    thread.start()
    return run


def _execute(
    *,
    store: RunStore,
    run_id: str,
    message: str,
    attachments: list[dict],
    resume_text: str | None,
    data_source: str,
    run_agent_fn: Callable[..., AgentState],
    llm_provider,
    profile_dir: str | None,
    candidate_id: str,
    session_context: dict | None,
    conversation_id: str | None,
    conversation_store: ConversationStore | None,
    task_store=None,
) -> None:
    def on_status(state: AgentState) -> None:
        display = state.progress if state.status == "RUNNING" and state.progress else state.status
        store.update(
            run_id,
            status=state.status,
            message=progress_message(display),
        )

    try:
        kwargs: dict[str, Any] = {
            "goal": message,
            "message": message,
            "resume": resume_text,
            "attachments": attachments,
            "data_source": data_source,
            "profile_dir": profile_dir,
            "candidate_id": candidate_id,
            "session_context": session_context,
            "on_status": on_status,
            "conversation_id": conversation_id,
            "task_store": task_store,
        }
        if llm_provider is not None:
            kwargs["llm_provider"] = llm_provider
        state = run_agent_fn(**kwargs)
        if not isinstance(state, AgentState):
            store.update(
                run_id,
                run_phase="finished",
                status="FAILED",
                error_code="agent_failed",
                message="Agent 未返回有效状态。",
                result={
                    "ok": False,
                    "status": "FAILED",
                    "error_code": "agent_failed",
                    "message": "Agent 未返回有效状态。",
                    "conversation_id": conversation_id,
                },
            )
            return
        if conversation_store is not None:
            conversation_store.remember(conversation_id, state, run_id=run_id)
        result = serialize_agent_state(state, profile_dir=profile_dir)
        result["conversation_id"] = conversation_id
        status = result.get("status")
        if status not in RUNTIME_STATUSES:
            status = state.session.status
        store.update(
            run_id,
            run_phase="finished",
            status=status,
            error_code=result.get("error_code"),
            message=result.get("message"),
            result=result,
        )
    except Exception as exc:
        _fail(store, run_id, exc, conversation_id)


def _fail(store: RunStore, run_id: str, exc: BaseException, conversation_id: str | None) -> None:
    message = str(exc) or "Agent 运行失败。"
    store.update(
        run_id,
        run_phase="finished",
        status="FAILED",
        error_code="agent_failed",
        message=message,
        result={
            "ok": False,
            "status": "FAILED",
            "error_code": "agent_failed",
            "message": message,
            "conversation_id": conversation_id,
            "errors": [{"kind": "entry", "message": message}],
            "recommended": [],
            "excluded": [],
        },
    )
