"""UTF-8 JSON file helpers. Parse/schema failures become StorageError, not silent defaults."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from storage.errors import CorruptStorageError, MissingStorageError, StorageError


def read_json_object(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        raise MissingStorageError(f"storage file not found: {target}", path=str(target))
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise StorageError(f"cannot read storage file: {target}: {exc}", path=str(target)) from exc
    if not raw.strip():
        raise CorruptStorageError(f"storage file is empty: {target}", path=str(target))
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CorruptStorageError(
            f"storage file is not valid JSON: {target}: {exc}",
            path=str(target),
        ) from exc
    if not isinstance(payload, dict):
        raise CorruptStorageError(
            f"storage file must be a JSON object: {target}",
            path=str(target),
        )
    return payload


def write_json_object(path: str | Path, payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise StorageError("storage payload must be a JSON object")
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(text + "\n", encoding="utf-8")
        tmp.replace(target)
    except OSError as exc:
        raise StorageError(f"cannot write storage file: {target}: {exc}", path=str(target)) from exc
