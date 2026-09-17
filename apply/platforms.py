"""Per-platform auto apply/contact stubs. Search/open stay in platforms/*.

These entry points exist so a later version can bind BOSS / Liepin / 51Job
without teaching the Agent Loop to click. Baseline v1 raises NotImplementedError.
"""

from __future__ import annotations

from typing import Any

from apply.assist import NOT_IMPLEMENTED_APPLY, NOT_IMPLEMENTED_CONTACT
from apply.schema import SUPPORTED_APPLY_PLATFORMS


def apply_on_platform(
    platform: str,
    *,
    job: dict | None = None,
    plan: dict | None = None,
    confirmation: dict | None = None,
    **_: Any,
) -> dict:
    _require_platform(platform)
    raise NotImplementedError(f"{NOT_IMPLEMENTED_APPLY}: platform={platform}")


def contact_on_platform(
    platform: str,
    *,
    job: dict | None = None,
    plan: dict | None = None,
    confirmation: dict | None = None,
    message: str | None = None,
    **_: Any,
) -> dict:
    _require_platform(platform)
    raise NotImplementedError(f"{NOT_IMPLEMENTED_CONTACT}: platform={platform}")


def _require_platform(platform: str) -> None:
    if platform not in SUPPORTED_APPLY_PLATFORMS:
        raise ValueError(f"unsupported apply platform: {platform}")
