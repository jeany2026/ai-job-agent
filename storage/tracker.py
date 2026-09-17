"""Local already-applied tracker. Rules consume structured keys; no JD/resume guessing."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from storage.errors import CorruptStorageError, MissingStorageError
from storage.jsonfile import read_json_object, write_json_object

TRACKER_SCHEMA_VERSION = 1


class AppliedTracker:
    """JSON file of already-applied job_keys for rules to read."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._keys: list[str] | None = None

    def applied_keys(self) -> list[str]:
        if self._keys is None:
            self._keys = list(self._load_keys())
        return list(self._keys)

    def contains(self, job_key: str | None) -> bool:
        if not job_key:
            return False
        return job_key in set(self.applied_keys())

    def mark_applied(self, job_key: str | None) -> None:
        text = (job_key or "").strip()
        if not text:
            return
        keys = self.applied_keys()
        if text in set(keys):
            return
        keys.append(text)
        self._persist(keys)

    def record_applied(self, job_keys: Iterable[str] | None) -> None:
        keys = self.applied_keys()
        seen = set(keys)
        changed = False
        for item in job_keys or []:
            text = str(item).strip()
            if not text or text in seen:
                continue
            keys.append(text)
            seen.add(text)
            changed = True
        if changed:
            self._persist(keys)

    def _load_keys(self) -> list[str]:
        try:
            payload = read_json_object(self.path)
        except MissingStorageError:
            return []
        version = payload.get("schema_version", TRACKER_SCHEMA_VERSION)
        if version != TRACKER_SCHEMA_VERSION:
            raise CorruptStorageError(
                f"unsupported tracker schema_version={version}: {self.path}",
                path=str(self.path),
            )
        if "already_applied" not in payload:
            return []
        values = payload.get("already_applied")
        if not isinstance(values, list):
            raise CorruptStorageError(
                f"tracker already_applied must be a list: {self.path}",
                path=str(self.path),
            )
        return _dedupe_keys(values)

    def _persist(self, keys: list[str]) -> None:
        clean = _dedupe_keys(keys)
        write_json_object(
            self.path,
            {
                "schema_version": TRACKER_SCHEMA_VERSION,
                "already_applied": clean,
            },
        )
        self._keys = clean


def merge_already_applied(
    existing: Iterable[str] | None,
    tracker: AppliedTracker | None,
) -> list[str]:
    keys = _dedupe_keys(existing or [])
    if tracker is None:
        return keys
    return _dedupe_keys([*keys, *tracker.applied_keys()])


def _dedupe_keys(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
