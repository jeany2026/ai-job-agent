"""Compatibility re-export. Implementation lives in understanding/understand_user_input.py."""

from __future__ import annotations

from understanding.schema import empty_understanding
from understanding.understand_user_input import (
    UNDERSTAND_USER_INPUT_SPEC,
    understand_user_input,
)

__all__ = [
    "UNDERSTAND_USER_INPUT_SPEC",
    "empty_understanding",
    "understand_user_input",
]
