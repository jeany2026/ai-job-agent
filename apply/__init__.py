"""Human-confirmed apply assist (architecture reserve). Auto delivery is not implemented."""

from __future__ import annotations

from .assist import (
    AUTO_APPLY_SPEC,
    RESERVED_TOOL_SPECS,
    SEND_COMMUNICATION_SPEC,
    auto_apply,
    auto_contact,
    click_apply,
    execute_apply_assist,
    human_confirmation,
    prepare_apply_assist,
    send_communication,
)
from .platforms import apply_on_platform, contact_on_platform
from .schema import AUTO_APPLY_ENTRYPOINTS, SUPPORTED_APPLY_PLATFORMS

__all__ = [
    "AUTO_APPLY_ENTRYPOINTS",
    "AUTO_APPLY_SPEC",
    "RESERVED_TOOL_SPECS",
    "SEND_COMMUNICATION_SPEC",
    "SUPPORTED_APPLY_PLATFORMS",
    "apply_on_platform",
    "auto_apply",
    "auto_contact",
    "click_apply",
    "contact_on_platform",
    "execute_apply_assist",
    "human_confirmation",
    "prepare_apply_assist",
    "send_communication",
]
