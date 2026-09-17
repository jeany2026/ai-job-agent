"""Map platform/browser failures to AgentState.human_gate. Not JD semantics."""

from __future__ import annotations

from typing import Any

from agent.state import AgentState, record_error, utc_now
from browser.edge_launcher import AgentEdgeNotReady
from browser.edge_session import EdgeNotConnected, HumanVerificationStopped

# Product Human Gate / wait reasons. Never include "please open the JD".
# browser_unavailable / cdp_unavailable = infrastructure (Agent Edge).
# login_required / captcha / access_* = user action in the Agent browser.
HUMAN_GATE_REASONS = (
    "login_required",
    "captcha",
    "access_blocked",
    "browser_unavailable",
    "cdp_unavailable",
    "site_blocked_client",
)

FORBIDDEN_HUMAN_GATE_REASONS = (
    "open_jd_manually",
    "manual_open_jd",
    "user_opens_jd",
    "please_open_jd",
)

CAPTCHA_SIGNALS = (
    "请完成安全验证",
    "拖动滑块",
    "滑动验证",
    "人机验证",
    "请进行验证",
    "verify you are human",
)

ACCESS_BLOCK_SIGNALS = (
    "访问异常",
    "检测到异常访问",
    "访问存在风险",
    "access denied",
)

SITE_BLOCK_SIGNALS = (
    "网络环境异常",
    "当前访问行为异常",
    "site_blocked_client",
)

LOGIN_SIGNALS = (
    "未检测到 BOSS 登录态",
    "请先在 Edge 中手动登录",
    "login_required",
)

# User-facing actions only for these; infra faults still pause but ask retry, not "configure Edge".
USER_ACTION_GATE_REASONS = frozenset({"login_required", "captcha", "access_blocked", "site_blocked_client"})
INFRA_GATE_REASONS = frozenset({"browser_unavailable", "cdp_unavailable"})


class HumanGateError(RuntimeError):
    """Raised by platform Tools so the Loop can pause for a human."""

    def __init__(self, reason: str, message: str, *, source: str | None = None):
        if reason not in HUMAN_GATE_REASONS:
            raise ValueError(f"illegal human_gate reason: {reason}")
        super().__init__(message)
        self.reason = reason
        self.source = source


def human_gate_payload(
    reason: str,
    message: str,
    *,
    source: str | None = None,
    extra: dict | None = None,
) -> dict:
    if reason in FORBIDDEN_HUMAN_GATE_REASONS:
        raise ValueError("Human Gate must not ask the user to open a JD manually")
    if reason not in HUMAN_GATE_REASONS:
        raise ValueError(f"illegal human_gate reason: {reason}")
    payload = {
        "reason": reason,
        "message": message,
        "source": source,
        "at": utc_now(),
    }
    if extra:
        payload.update(extra)
    return payload


def apply_human_gate(state: AgentState, gate: dict) -> AgentState:
    """Pause this run for human intervention. Not business-task completion.

    Keeps Live World (jobs/plans/decisions). Writes an auditable Observation.
    Cross-turn resume is Conversation continuity + Reasoner — not a fixed Action.
    """
    from agent.human_gate_continuity import human_gate_observation

    reason = gate.get("reason")
    message = str(gate.get("message") or "needs human")
    payload = human_gate_payload(
        str(reason),
        message,
        source=gate.get("source"),
        extra={key: value for key, value in gate.items() if key not in {"reason", "message", "source", "at"}},
    )
    state.human_gate = payload
    state.last_raw_observation = human_gate_observation(payload)
    record_error(state, message, kind="human_gate")
    state.status = "NEEDS_HUMAN"
    return state


def result_human_gate(result: Any) -> dict | None:
    if not isinstance(result, dict):
        return None
    gate = result.get("human_gate")
    if isinstance(gate, dict) and gate.get("reason"):
        return gate
    if result.get("ok") is False and result.get("reason") in HUMAN_GATE_REASONS:
        return {
            "reason": result["reason"],
            "message": result.get("message") or result.get("error") or result["reason"],
            "source": result.get("source"),
        }
    return None


def classify_exception(exc: BaseException, *, source: str | None = None) -> dict | None:
    """Return a human_gate dict, or None if this is a normal tool failure."""
    if isinstance(exc, HumanGateError):
        return human_gate_payload(exc.reason, str(exc), source=exc.source or source)

    message = str(exc)
    if isinstance(exc, AgentEdgeNotReady) or type(exc).__name__ == "AgentEdgeNotReady":
        return human_gate_payload(
            "browser_unavailable",
            message or "Agent 招聘浏览器未能启动。",
            source=source,
            extra={"fault": "agent_edge_not_ready"},
        )
    if isinstance(exc, EdgeNotConnected) or type(exc).__name__ == "EdgeNotConnected":
        return human_gate_payload(
            "cdp_unavailable",
            message or "无法建立 Agent 招聘浏览器的调试连接。",
            source=source,
            extra={"fault": "cdp_connect_failed"},
        )
    if isinstance(exc, HumanVerificationStopped) or type(exc).__name__ == "HumanVerificationStopped":
        return human_gate_payload(_reason_from_message(message, default="captcha"), message, source=source)

    reason = _reason_from_message(message)
    if reason:
        return human_gate_payload(reason, message, source=source)
    return None


def reraise_as_human_gate(exc: BaseException, *, source: str) -> None:
    """Re-raise HumanGateError when the exception is a product pause; otherwise re-raise as-is."""
    gate = classify_exception(exc, source=source)
    if gate is None:
        raise exc
    raise HumanGateError(gate["reason"], gate["message"], source=source) from exc


def _reason_from_message(message: str, *, default: str | None = None) -> str | None:
    haystack = message or ""
    lowered = haystack.lower()
    for signal in LOGIN_SIGNALS:
        if signal.lower() in lowered or signal in haystack:
            return "login_required"
    for signal in SITE_BLOCK_SIGNALS:
        if signal.lower() in lowered or signal in haystack:
            return "site_blocked_client"
    for signal in ACCESS_BLOCK_SIGNALS:
        if signal.lower() in lowered or signal in haystack:
            return "access_blocked"
    for signal in CAPTCHA_SIGNALS:
        if signal.lower() in lowered or signal in haystack:
            return "captcha"
    return default
