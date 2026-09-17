"""Controlled failures for local JSON storage. Do not guess file contents."""

from __future__ import annotations


class StorageError(Exception):
    """Local JSON storage failed. Callers must not invent a substitute record."""

    def __init__(self, message: str, *, path: str | None = None) -> None:
        super().__init__(message)
        self.path = path


class MissingStorageError(StorageError):
    """Requested storage file does not exist."""


class CorruptStorageError(StorageError):
    """File exists but is not a valid store record. Fail closed."""
