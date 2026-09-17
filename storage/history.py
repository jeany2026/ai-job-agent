"""Local run/recommendation history. One JSON file per session; no cloud, no DB."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.state import AgentState, utc_now
from storage.errors import CorruptStorageError, MissingStorageError, StorageError
from storage.jsonfile import read_json_object, write_json_object

HISTORY_SCHEMA_VERSION = 1
REQUIRED_RUN_FIELDS = ("schema_version", "session_id", "status")


class HistoryStore:
    """Save and load Agent run snapshots as local JSON files."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def save_run(self, state: AgentState) -> dict[str, Any]:
        record = snapshot_run(state)
        write_json_object(self._run_path(record["session_id"]), record)
        return record

    def load_run(self, session_id: str) -> dict[str, Any]:
        text = (session_id or "").strip()
        if not text:
            raise StorageError("session_id is required to load history")
        return self._validate(read_json_object(self._run_path(text)))

    def list_runs(self) -> list[dict[str, Any]]:
        if not self.directory.exists():
            return []
        runs: list[dict[str, Any]] = []
        for path in sorted(self.directory.glob("*.json")):
            if path.name.endswith(".tmp"):
                continue
            try:
                record = self._validate(read_json_object(path))
            except (CorruptStorageError, MissingStorageError, StorageError) as exc:
                raise CorruptStorageError(
                    f"history directory contains a corrupt run file: {path}: {exc}",
                    path=str(path),
                ) from exc
            runs.append(record)
        runs.sort(key=lambda item: (item.get("created_at") or "", item.get("session_id") or ""))
        return runs

    def _run_path(self, session_id: str) -> Path:
        safe = session_id.replace("/", "_").replace("\\", "_")
        return self.directory / f"{safe}.json"

    def _validate(self, payload: dict[str, Any]) -> dict[str, Any]:
        for field in REQUIRED_RUN_FIELDS:
            if field not in payload:
                raise CorruptStorageError(
                    f"history record missing {field}",
                    path=str(self.directory),
                )
        if payload.get("schema_version") != HISTORY_SCHEMA_VERSION:
            raise CorruptStorageError(
                f"unsupported history schema_version={payload.get('schema_version')}",
                path=str(self.directory),
            )
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            raise CorruptStorageError("history session_id must be a non-empty string")
        status = payload.get("status")
        if not isinstance(status, str) or not status.strip():
            raise CorruptStorageError("history status must be a non-empty string")
        if "recommended" in payload and not isinstance(payload.get("recommended"), list):
            raise CorruptStorageError("history recommended must be a list")
        if "excluded" in payload and not isinstance(payload.get("excluded"), list):
            raise CorruptStorageError("history excluded must be a list")
        return payload


def snapshot_run(state: AgentState) -> dict[str, Any]:
    output = state.output if isinstance(state.output, dict) else None
    jobs = sorted(state.jobs.values(), key=lambda item: item.listed_order)
    return {
        "schema_version": HISTORY_SCHEMA_VERSION,
        "session_id": state.session.session_id,
        "status": state.session.status,
        "created_at": state.session.created_at,
        "saved_at": utc_now(),
        "candidate_id": state.candidate.candidate_id,
        "goal": state.goal,
        "platforms": list(state.constraints.platforms or []),
        "data_source": state.constraints.data_source,
        "recommended": list((output or {}).get("recommended") or []),
        "excluded": list((output or {}).get("excluded") or []),
        "stats": dict(state.search.stats),
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
        "jobs": [
            {
                "job_key": record.job_key,
                "stage": record.stage,
                "exclude_reason": record.exclude_reason,
                "platform": (record.opened or record.listed or {}).get("platform"),
                "job_id": (record.opened or record.listed or {}).get("job_id"),
                "job_title": (record.opened or record.listed or {}).get("job_title"),
                "company_name": (record.opened or record.listed or {}).get("company_name"),
            }
            for record in jobs
        ],
        "errors": list(state.errors),
        "output": output,
    }
