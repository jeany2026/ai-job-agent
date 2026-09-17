"""Local JSON JobSearchTask persistence. No database, Redis, or vector store."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from storage.errors import CorruptStorageError, MissingStorageError, StorageError
from storage.jsonfile import read_json_object, write_json_object
from task.schema import (
    ACTIVE_TASK_STATUSES,
    SCHEMA_VERSION,
    JobSearchTask,
    job_search_task_from_dict,
)

REQUIRED_FIELDS = ("schema_version", "task_id", "task_status")


class JobSearchTaskStore:
    """In-memory store with optional JSON directory. Lives longer than one AgentState."""

    def __init__(self, directory: str | Path | None = None) -> None:
        self.directory = Path(directory) if directory else None
        self._lock = threading.Lock()
        self._items: dict[str, dict[str, Any]] = {}
        if self.directory is not None:
            self._load_directory()

    def save(self, task: JobSearchTask) -> JobSearchTask:
        payload = task.to_dict()
        self._validate(payload)
        with self._lock:
            self._items[task.task_id] = payload
        if self.directory is not None:
            write_json_object(self._path(task.task_id), payload)
        return task

    def load(self, task_id: str) -> JobSearchTask:
        text = (task_id or "").strip()
        if not text:
            raise StorageError("task_id is required to load JobSearchTask")
        with self._lock:
            payload = self._items.get(text)
        if payload is None and self.directory is not None:
            payload = self._validate(read_json_object(self._path(text)))
            with self._lock:
                self._items[text] = payload
        if payload is None:
            raise MissingStorageError(f"JobSearchTask not found: {text}")
        task = job_search_task_from_dict(payload)
        if task is None:
            raise CorruptStorageError(f"JobSearchTask is not valid: {text}")
        return task

    def get(self, task_id: str | None) -> JobSearchTask | None:
        text = (task_id or "").strip()
        if not text:
            return None
        try:
            return self.load(text)
        except (MissingStorageError, CorruptStorageError, StorageError):
            return None

    def list_all(self) -> list[JobSearchTask]:
        with self._lock:
            ids = list(self._items.keys())
        tasks: list[JobSearchTask] = []
        for task_id in ids:
            task = self.get(task_id)
            if task is not None:
                tasks.append(task)
        tasks.sort(key=lambda item: (item.created_at, item.task_id))
        return tasks

    def list_by_conversation(self, conversation_id: str | None) -> list[JobSearchTask]:
        cid = (conversation_id or "").strip()
        if not cid:
            return []
        return [task for task in self.list_all() if task.conversation_id == cid]

    def list_active(self, conversation_id: str | None = None) -> list[JobSearchTask]:
        if conversation_id:
            tasks = self.list_by_conversation(conversation_id)
        else:
            tasks = []
        return [task for task in tasks if task.task_status in ACTIVE_TASK_STATUSES]

    def _path(self, task_id: str) -> Path:
        assert self.directory is not None
        safe = task_id.replace("/", "_").replace("\\", "_")
        return self.directory / f"{safe}.json"

    def _load_directory(self) -> None:
        assert self.directory is not None
        if not self.directory.exists():
            return
        for path in sorted(self.directory.glob("*.json")):
            if path.name.endswith(".tmp"):
                continue
            payload = self._validate(read_json_object(path))
            task_id = payload["task_id"]
            with self._lock:
                self._items[task_id] = payload

    def _validate(self, payload: dict[str, Any]) -> dict[str, Any]:
        for field in REQUIRED_FIELDS:
            if field not in payload:
                raise CorruptStorageError(f"JobSearchTask missing {field}")
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise CorruptStorageError(
                f"unsupported JobSearchTask schema_version={payload.get('schema_version')}"
            )
        task_id = payload.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip():
            raise CorruptStorageError("JobSearchTask task_id must be a non-empty string")
        status = payload.get("task_status")
        if not isinstance(status, str) or not status.strip():
            raise CorruptStorageError("JobSearchTask task_status must be a non-empty string")
        return payload
