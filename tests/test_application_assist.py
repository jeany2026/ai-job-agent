"""Phase 11: apply-assist reserve. Auto delivery must raise; production must not click apply."""

from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from apply.assist import (
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
from apply.platforms import apply_on_platform, contact_on_platform
from apply.schema import AUTO_APPLY_ENTRYPOINTS, SUPPORTED_APPLY_PLATFORMS
from tools.registry import SUPPORTED_DATA_SOURCES, build_registry

PRODUCTION_DIRS = (
    "agent",
    "apply",
    "browser",
    "candidate",
    "job",
    "llm",
    "matching",
    "platforms",
    "rules",
    "storage",
    "tools",
)

CLICK_ATTRS = {"click"}
TYPE_ATTRS = {"fill", "type", "press"}
CHAT_NAME_HINTS = ("send_message", "send_chat", "start_chat", "chat_recruiter")
ALLOWED_CLICK_FILES = {
    "apply/live_execute.py",
    "platforms/boss/execute.py",
}


def _done_request():
    return {
        "job": {
            "platform": "mock",
            "job_id": "direct",
            "job_url": "https://example.test/jobs/direct",
            "job_title": "高级产品经理",
            "company_name": "支付科技",
        },
        "interpret_result": {
            "actions": [
                {"semantic_intent": "apply_resume"},
                {"semantic_intent": "start_contact"},
                {"semantic_intent": "share"},
            ],
            "inferred_context": {"application_evidence": "not_applied"},
        },
        "match_result": {"recommendation": "yes"},
    }


def test_prepare_apply_assist_is_draft_only():
    plan = prepare_apply_assist(_done_request())
    assert plan["schema_version"] == 1
    assert plan["assist_status"] == "awaiting_human_confirmation"
    assert plan["confirmation_required"] is True
    assert plan["auto_execution"] == "not_implemented"
    assert plan["job_key"] == "mock:direct"
    assert plan["platform"] == "mock"
    assert plan["proposed_actions"] == [
        {"kind": "apply_resume", "status": "not_implemented"},
        {"kind": "start_contact", "status": "not_implemented"},
    ]
    assert plan["application_evidence"] == "not_applied"
    assert plan["match_recommendation"] == "yes"


def test_prepare_does_not_guess_from_button_text():
    plan = prepare_apply_assist(
        {
            "job": {"platform": "boss", "job_id": "x"},
            "interpret_result": {
                "actions": [{"text": "立即沟通", "semantic_intent": "unknown"}],
            },
        }
    )
    assert plan["proposed_actions"] == []
    assert plan["confirmation_required"] is True


def test_human_confirmation_does_not_enable_execution():
    plan = prepare_apply_assist(_done_request())
    confirmation = human_confirmation(confirmed=True, confirmed_by="user")
    assert confirmation["status"] == "confirmed"
    with pytest.raises(NotImplementedError, match="not implemented"):
        execute_apply_assist(plan, confirmation)


@pytest.mark.parametrize(
    "fn",
    [auto_apply, click_apply, execute_apply_assist],
)
def test_auto_apply_entrypoints_raise(fn):
    confirmation = human_confirmation(confirmed=True)
    with pytest.raises(NotImplementedError) as exc:
        fn({"job_key": "mock:direct"}, confirmation)
    assert "not implemented" in str(exc.value)


@pytest.mark.parametrize("fn", [auto_contact, send_communication])
def test_auto_contact_entrypoints_raise(fn):
    with pytest.raises(NotImplementedError) as exc:
        fn({"job_key": "mock:direct"}, human_confirmation(confirmed=True), message="你好")
    assert "not implemented" in str(exc.value)


@pytest.mark.parametrize("platform", list(SUPPORTED_APPLY_PLATFORMS))
def test_platform_auto_apply_raises(platform):
    with pytest.raises(NotImplementedError):
        apply_on_platform(platform, job={"job_url": "https://example.test/job"}, confirmation={"confirmed": True})
    with pytest.raises(NotImplementedError):
        contact_on_platform(platform, confirmation={"confirmed": True}, message="hi")


def test_unknown_platform_is_validation_not_guessing():
    with pytest.raises(ValueError, match="unsupported apply platform"):
        apply_on_platform("unknown_site")


def test_reserved_tools_are_not_on_the_loop_registry():
    reserved = {item["name"] for item in RESERVED_TOOL_SPECS}
    assert reserved == {AUTO_APPLY_SPEC["name"], SEND_COMMUNICATION_SPEC["name"]}
    for source in SUPPORTED_DATA_SOURCES:
        names = {item["name"] for item in build_registry(data_source=source).specs()}
        assert reserved.isdisjoint(names)
        for entry in AUTO_APPLY_ENTRYPOINTS:
            assert entry not in names


def test_auto_entrypoint_bodies_only_raise_not_implemented():
    from apply import assist, platforms

    mapping = {
        "auto_apply": assist.auto_apply,
        "auto_contact": assist.auto_contact,
        "click_apply": assist.click_apply,
        "send_communication": assist.send_communication,
        "execute_apply_assist": assist.execute_apply_assist,
        "apply_on_platform": platforms.apply_on_platform,
        "contact_on_platform": platforms.contact_on_platform,
    }
    assert set(mapping) == set(AUTO_APPLY_ENTRYPOINTS)
    for name, fn in mapping.items():
        source = inspect.getsource(fn)
        tree = ast.parse(source)
        func = tree.body[0]
        assert isinstance(func, ast.FunctionDef)
        assert func.name == name
        assert _raises_not_implemented(func), name


def test_production_has_no_click_apply_or_send_chat():
    for path in _production_python_files():
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        relative = path.relative_to(ROOT_DIR).as_posix()
        if path.parent.name != "apply" or path.name not in {"assist.py", "platforms.py"}:
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name in AUTO_APPLY_ENTRYPOINTS:
                    raise AssertionError(f"production auto-delivery impl outside reserve: {relative}:{node.name}")
                if isinstance(node, ast.FunctionDef) and node.name in CHAT_NAME_HINTS:
                    raise AssertionError(f"production chat impl: {relative}:{node.name}")
        allow_click = relative.replace("\\", "/") in ALLOWED_CLICK_FILES
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                attr = node.func.attr
                if attr in CLICK_ATTRS and not allow_click:
                    raise AssertionError(f"production click call: {relative}")
                if attr in TYPE_ATTRS and _looks_like_chat_type(source):
                    raise AssertionError(f"production chat typing: {relative}")
        lowered = source.lower()
        if "page.click" in lowered and not allow_click:
            raise AssertionError(f"page.click in production: {relative}")


def test_loop_does_not_call_auto_delivery():
    for name in ("decide.py", "loop.py", "orchestrator.py"):
        source = (ROOT_DIR / "agent" / name).read_text(encoding="utf-8")
        tree = ast.parse(source)
        dumped = ast.dump(tree)
        for entry in AUTO_APPLY_ENTRYPOINTS:
            assert entry not in dumped, f"{name} must not invoke {entry}"


def _raises_not_implemented(func: ast.FunctionDef) -> bool:
    stmts = [
        node
        for node in func.body
        if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))
    ]
    if not stmts or not _is_not_implemented_raise(stmts[-1]):
        return False
    for stmt in stmts[:-1]:
        if _contains_click_or_type(stmt):
            return False
    return True


def _contains_click_or_type(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
            if child.func.attr in CLICK_ATTRS or child.func.attr in TYPE_ATTRS:
                return True
    return False


def _is_not_implemented_raise(stmt: ast.stmt) -> bool:
    if not isinstance(stmt, ast.Raise) or stmt.exc is None:
        return False
    exc = stmt.exc
    if isinstance(exc, ast.Name):
        return exc.id == "NotImplementedError"
    if isinstance(exc, ast.Call):
        func = exc.func
        return isinstance(func, ast.Name) and func.id == "NotImplementedError"
    return False


def _looks_like_chat_type(source: str) -> bool:
    return "send_communication" in source or "打招呼" in source or "沟通消息" in source


def _production_python_files():
    for folder in PRODUCTION_DIRS:
        root = ROOT_DIR / folder
        if not root.exists():
            continue
        yield from root.rglob("*.py")
