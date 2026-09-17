"""Persistent candidate Memory store. Local JSON; no semantic merge, no Agent Loop.

Cross-run records keep Evidence / Claim / Interpretation / Verification.
CandidateProfile is an interpretation_projection, not the sole stored truth.

create / update persist already-structured layers. This module does not
analyze resumes, guess capabilities, or write session supplements.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from agent.state import utc_now
from storage.errors import CorruptStorageError, MissingStorageError, StorageError
from storage.jsonfile import read_json_object, write_json_object

PROFILE_STORE_SCHEMA_VERSION = 1
DEFAULT_CANDIDATE_ID = "default"

ALLOWED_SOURCE_KINDS = {
    "resume",
    "user_statement",
    "project_document",
    "other_attachment",
}

REQUIRED_RECORD_FIELDS = (
    "schema_version",
    "candidate_id",
    "profile_version",
    "profile",
    "created_at",
    "updated_at",
)

SEMANTIC_LAYER_FIELDS = (
    "evidence",
    "claims",
    "interpretations",
    "verifications",
)

_ARCHIVE_NAME = re.compile(r"^(?P<id>.+)\.v(?P<version>\d+)\.json$")
_UNSET = object()


def compute_source_hash(text: str | None) -> str:
    """Stable hash of extracted source text. Empty text is not a resume identity."""
    if text is None or not str(text).strip():
        raise StorageError("cannot hash empty source text")
    normalized = "".join(str(text).split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def create_candidate_profile(
    directory: str | Path,
    profile: dict,
    *,
    candidate_id: str | None = None,
    source_resume_name: str | None = None,
    source_resume_hash: str | None = None,
    source_kinds: list[str] | None = None,
    evidence: list[dict] | None = None,
    claims: list[dict] | None = None,
    interpretations: list[dict] | None = None,
    verifications: list[dict] | None = None,
) -> dict:
    return CandidateProfileStore(directory).create(
        profile,
        candidate_id=candidate_id,
        source_resume_name=source_resume_name,
        source_resume_hash=source_resume_hash,
        source_kinds=source_kinds,
        evidence=evidence,
        claims=claims,
        interpretations=interpretations,
        verifications=verifications,
    )


def load_candidate_profile(directory: str | Path, candidate_id: str = DEFAULT_CANDIDATE_ID) -> dict:
    return CandidateProfileStore(directory).load(candidate_id)


def update_candidate_profile(
    directory: str | Path,
    candidate_id: str,
    profile: dict,
    *,
    source_resume_name: Any = _UNSET,
    source_resume_hash: Any = _UNSET,
    source_kinds: Any = _UNSET,
    evidence: Any = _UNSET,
    claims: Any = _UNSET,
    interpretations: Any = _UNSET,
    verifications: Any = _UNSET,
) -> dict:
    return CandidateProfileStore(directory).update(
        candidate_id,
        profile,
        source_resume_name=source_resume_name,
        source_resume_hash=source_resume_hash,
        source_kinds=source_kinds,
        evidence=evidence,
        claims=claims,
        interpretations=interpretations,
        verifications=verifications,
    )


class CandidateProfileStore:
    """One current JSON record per candidate_id, plus archived profile versions."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def create(
        self,
        profile: dict,
        *,
        candidate_id: str | None = None,
        source_resume_name: str | None = None,
        source_resume_hash: str | None = None,
        source_kinds: list[str] | None = None,
        evidence: list[dict] | None = None,
        claims: list[dict] | None = None,
        interpretations: list[dict] | None = None,
        verifications: list[dict] | None = None,
    ) -> dict:
        cid = _resolve_candidate_id(candidate_id, profile)
        if self._current_path(cid).exists():
            raise StorageError(
                f"candidate profile already exists: {cid}; use update_candidate_profile",
                path=str(self._current_path(cid)),
            )
        now = utc_now()
        record = {
            "schema_version": PROFILE_STORE_SCHEMA_VERSION,
            "candidate_id": cid,
            "profile_version": 1,
            "profile": _require_persistable_profile(profile),
            "source_resume_name": _optional_string(source_resume_name),
            "source_resume_hash": _optional_string(source_resume_hash),
            "source_kinds": _source_kinds(source_kinds),
            "evidence": _semantic_layer(evidence),
            "claims": _semantic_layer(claims),
            "interpretations": _semantic_layer(interpretations),
            "verifications": _semantic_layer(verifications),
            "created_at": now,
            "updated_at": now,
        }
        return self._write_current(record)

    def load(self, candidate_id: str = DEFAULT_CANDIDATE_ID) -> dict:
        cid = _require_candidate_id(candidate_id)
        path = self._current_path(cid)
        if not path.exists():
            raise MissingStorageError(f"candidate profile not found: {cid}", path=str(path))
        return self._validate(read_json_object(path), path=path)

    def update(
        self,
        candidate_id: str,
        profile: dict,
        *,
        source_resume_name: Any = _UNSET,
        source_resume_hash: Any = _UNSET,
        source_kinds: Any = _UNSET,
        evidence: Any = _UNSET,
        claims: Any = _UNSET,
        interpretations: Any = _UNSET,
        verifications: Any = _UNSET,
    ) -> dict:
        existing = self.load(candidate_id)
        previous_version = int(existing["profile_version"])
        self._archive_current(existing)

        name = (
            existing.get("source_resume_name")
            if source_resume_name is _UNSET
            else _optional_string(source_resume_name)
        )
        digest = (
            existing.get("source_resume_hash")
            if source_resume_hash is _UNSET
            else _optional_string(source_resume_hash)
        )
        kinds = (
            list(existing.get("source_kinds") or [])
            if source_kinds is _UNSET
            else _source_kinds(source_kinds)
        )
        layers = {
            key: (
                list(existing.get(key) or [])
                if value is _UNSET
                else _semantic_layer(value)
            )
            for key, value in (
                ("evidence", evidence),
                ("claims", claims),
                ("interpretations", interpretations),
                ("verifications", verifications),
            )
        }

        record = {
            "schema_version": PROFILE_STORE_SCHEMA_VERSION,
            "candidate_id": existing["candidate_id"],
            "profile_version": previous_version + 1,
            "profile": _require_persistable_profile(profile),
            "source_resume_name": name,
            "source_resume_hash": digest,
            "source_kinds": kinds,
            **layers,
            "created_at": existing["created_at"],
            "updated_at": utc_now(),
        }
        return self._write_current(record)

    def exists(self, candidate_id: str = DEFAULT_CANDIDATE_ID) -> bool:
        cid = (candidate_id or "").strip()
        if not cid:
            return False
        return self._current_path(cid).exists()

    def find_by_resume_hash(self, source_resume_hash: str) -> dict | None:
        digest = _optional_string(source_resume_hash)
        if not digest:
            return None
        for record in self._iter_current_records():
            if record.get("source_resume_hash") == digest:
                return record
        return None

    def load_version(self, candidate_id: str, profile_version: int) -> dict:
        cid = _require_candidate_id(candidate_id)
        if not isinstance(profile_version, int) or isinstance(profile_version, bool) or profile_version < 1:
            raise StorageError("profile_version must be a positive integer")
        current = self.load(cid)
        if current["profile_version"] == profile_version:
            return current
        path = self._version_path(cid, profile_version)
        if not path.exists():
            raise MissingStorageError(
                f"candidate profile version not found: {cid} v{profile_version}",
                path=str(path),
            )
        record = self._validate(read_json_object(path), path=path)
        if record["profile_version"] != profile_version:
            raise CorruptStorageError(
                f"archived profile_version mismatch: expected {profile_version}",
                path=str(path),
            )
        return record

    def list_versions(self, candidate_id: str = DEFAULT_CANDIDATE_ID) -> list[int]:
        cid = _require_candidate_id(candidate_id)
        versions: set[int] = set()
        if self.exists(cid):
            versions.add(int(self.load(cid)["profile_version"]))
        prefix = f"{_safe_id(cid)}.v"
        if self.directory.exists():
            for path in self.directory.glob(f"{prefix}*.json"):
                match = _ARCHIVE_NAME.match(path.name)
                if match and match.group("id") == _safe_id(cid):
                    versions.add(int(match.group("version")))
        return sorted(versions)

    def _archive_current(self, record: dict) -> None:
        version = int(record["profile_version"])
        path = self._version_path(record["candidate_id"], version)
        write_json_object(path, record)

    def _write_current(self, record: dict) -> dict:
        path = self._current_path(record["candidate_id"])
        write_json_object(path, record)
        return self._validate(record, path=path)

    def _iter_current_records(self) -> list[dict]:
        if not self.directory.exists():
            return []
        records: list[dict] = []
        for path in sorted(self.directory.glob("*.json")):
            if path.name.endswith(".tmp") or _ARCHIVE_NAME.match(path.name):
                continue
            records.append(self._validate(read_json_object(path), path=path))
        return records

    def _current_path(self, candidate_id: str) -> Path:
        return self.directory / f"{_safe_id(candidate_id)}.json"

    def _version_path(self, candidate_id: str, profile_version: int) -> Path:
        return self.directory / f"{_safe_id(candidate_id)}.v{profile_version}.json"

    def _validate(self, payload: dict[str, Any], *, path: Path) -> dict[str, Any]:
        for field in REQUIRED_RECORD_FIELDS:
            if field not in payload:
                raise CorruptStorageError(
                    f"candidate profile record missing {field}",
                    path=str(path),
                )
        if payload.get("schema_version") != PROFILE_STORE_SCHEMA_VERSION:
            raise CorruptStorageError(
                f"unsupported candidate profile schema_version={payload.get('schema_version')}",
                path=str(path),
            )
        candidate_id = payload.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            raise CorruptStorageError("candidate_id must be a non-empty string", path=str(path))
        version = payload.get("profile_version")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise CorruptStorageError("profile_version must be a positive integer", path=str(path))
        profile = payload.get("profile")
        if not isinstance(profile, dict):
            raise CorruptStorageError("profile must be an object", path=str(path))
        for stamp in ("created_at", "updated_at"):
            value = payload.get(stamp)
            if not isinstance(value, str) or not value.strip():
                raise CorruptStorageError(f"{stamp} must be a non-empty string", path=str(path))
        kinds = payload.get("source_kinds", [])
        if kinds is None:
            kinds = []
        if not isinstance(kinds, list):
            raise CorruptStorageError("source_kinds must be an array", path=str(path))
        try:
            payload["source_kinds"] = _source_kinds(kinds)
            payload["source_resume_name"] = _optional_string(payload.get("source_resume_name"))
            payload["source_resume_hash"] = _optional_string(payload.get("source_resume_hash"))
            for field in SEMANTIC_LAYER_FIELDS:
                payload[field] = _semantic_layer(payload.get(field))
            payload["profile"] = _mark_projection(payload.get("profile"))
        except StorageError as exc:
            raise CorruptStorageError(str(exc), path=str(path)) from exc
        return payload


def _resolve_candidate_id(candidate_id: str | None, profile: dict) -> str:
    if candidate_id and str(candidate_id).strip():
        return str(candidate_id).strip()
    if isinstance(profile, dict):
        nested = profile.get("candidate_id")
        if nested and str(nested).strip():
            return str(nested).strip()
    return DEFAULT_CANDIDATE_ID


def _require_candidate_id(candidate_id: str | None) -> str:
    text = (candidate_id or "").strip()
    if not text:
        raise StorageError("candidate_id is required")
    return text


def _safe_id(candidate_id: str) -> str:
    return candidate_id.replace("/", "_").replace("\\", "_")


def _require_persistable_profile(profile: Any) -> dict:
    if not isinstance(profile, dict):
        raise StorageError("profile must be an object")
    status = profile.get("analysis_status")
    if status is not None and status != "ok":
        raise StorageError(f"refusing to persist profile with analysis_status={status}")
    return _mark_projection(profile)


def _mark_projection(profile: Any) -> dict:
    if not isinstance(profile, dict):
        raise StorageError("profile must be an object")
    view = dict(profile)
    view["kind"] = "interpretation_projection"
    if "derived_from" not in view:
        view["derived_from"] = []
    return view


def _semantic_layer(value: Any) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise StorageError("semantic layer must be an array")
    result: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            raise StorageError("semantic layer items must be objects")
        result.append(dict(item))
    return result


def _source_kinds(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise StorageError("source_kinds must be an array")
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise StorageError("source_kinds items must be strings")
        kind = item.strip()
        if not kind:
            raise StorageError("source_kinds items must be non-empty strings")
        if kind not in ALLOWED_SOURCE_KINDS:
            raise StorageError(f"illegal source kind: {kind}")
        if kind in seen:
            continue
        seen.add(kind)
        result.append(kind)
    return result


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise StorageError("string field must be a string")
    text = value.strip()
    return text or None
