"""Unit tests for interpret_job_actions. Mock LLM only; no keyword classifier."""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tests.mock_llm import MockLLMProvider, intents_response
from tools.interpret_job_actions import infer_application_evidence, interpret_job_actions

interpret_mod = importlib.import_module("tools.interpret_job_actions")

TOOL_SOURCE = ROOT_DIR / "tools" / "interpret_job_actions.py"


def _action(text=None, **fields):
    item = {
        "text": text,
        "aria_label": None,
        "title": None,
        "role": None,
        "href": None,
        "tag": None,
        "surrounding_text": None,
        "dom_context": None,
    }
    item.update(fields)
    return item


def _run(actions, *intents, provider=None):
    context = {"platform": "boss", "job_id": "job-1", "actions": actions}
    llm = provider or MockLLMProvider(intents_response(*intents))
    return interpret_job_actions(context, llm_provider=llm), llm


def test_resume_management_online():
    result, _ = _run([_action("完善在线简历")], "resume_management")
    assert result["analysis_status"] == "ok"
    assert result["actions"][0]["semantic_intent"] == "resume_management"
    assert result["inferred_context"]["application_evidence"] == "uncertain"


def test_resume_management_attachment():
    result, _ = _run([_action("新增附件简历")], "resume_management")
    assert result["actions"][0]["semantic_intent"] == "resume_management"
    assert result["inferred_context"]["application_evidence"] == "uncertain"


def test_resume_center_nav_not_apply():
    result, _ = _run([_action("简历 new")], "resume_management")
    assert result["actions"][0]["semantic_intent"] in {"resume_management", "other", "unknown"}
    assert result["actions"][0]["semantic_intent"] != "apply_resume"


def test_start_contact_from_llm():
    result, _ = _run([_action("Start a new recruiter conversation")], "start_contact")
    assert result["actions"][0]["semantic_intent"] == "start_contact"
    assert result["inferred_context"]["application_evidence"] == "uncertain"


def test_interest_not_start_contact():
    result, _ = _run([_action("感兴趣")], "interest")
    assert result["actions"][0]["semantic_intent"] == "interest"
    assert result["actions"][0]["semantic_intent"] != "start_contact"
    assert result["inferred_context"]["application_evidence"] == "uncertain"


def test_apply_resume_paraphrases_from_llm():
    for text, intent in (
        ("申请该职位", "apply_resume"),
        ("提交申请", "apply_resume"),
        ("投递", "apply_resume"),
        ("发送简历申请", "apply_resume"),
    ):
        result, _ = _run([_action(text)], intent)
        assert result["actions"][0]["semantic_intent"] == "apply_resume"


def test_resume_management_plus_start_is_uncertain():
    result, _ = _run(
        [_action("完善在线简历"), _action("立即沟通")],
        "resume_management",
        "start_contact",
    )
    assert result["inferred_context"]["application_evidence"] == "uncertain"
    assert result["inferred_context"]["has_apply_intent"] is False


def test_apply_plus_start_is_not_applied():
    result, _ = _run(
        [_action("提交申请"), _action("Start a new recruiter conversation")],
        "apply_resume",
        "start_contact",
    )
    assert result["analysis_status"] == "ok"
    assert result["inferred_context"]["application_evidence"] == "not_applied"


def test_continue_contact_is_applied():
    result, _ = _run([_action("Continue existing recruiter conversation")], "continue_contact")
    assert result["inferred_context"]["application_evidence"] == "applied"


def test_view_progress_is_applied():
    result, _ = _run([_action("View application progress")], "view_progress")
    assert result["inferred_context"]["application_evidence"] == "applied"


def test_interest_is_uncertain():
    result, _ = _run([_action("感兴趣")], "interest")
    assert result["inferred_context"]["application_evidence"] == "uncertain"


def test_missing_or_null_intent_fails():
    for payload in (
        MockLLMProvider({"actions": [{"confidence": 0.9}]}),
        MockLLMProvider({"actions": [{"semantic_intent": None}]}),
    ):
        result, _ = _run([_action("提交申请")], provider=payload)
        assert result["analysis_status"] == "error"
        assert "illegal semantic_intent" in (result.get("error") or "")


def test_heuristic_interpreter_cannot_replace_llm():
    from tools.interpret_job_actions import HeuristicActionInterpreter

    result, _ = _run(
        [_action("提交申请")],
        provider=HeuristicActionInterpreter(),
    )
    assert result["analysis_status"] == "error"
    assert "not a production LLM" in (result.get("error") or "")


def test_illegal_intent_fails_without_heuristic():
    called = {"heuristic": False}

    def boom(action):
        called["heuristic"] = True
        raise AssertionError("classify_action_semantically must not run")

    original = interpret_mod.classify_action_semantically
    interpret_mod.classify_action_semantically = boom
    try:
        result, _ = _run([_action("提交申请")], "maybe_apply")
    finally:
        interpret_mod.classify_action_semantically = original

    assert called["heuristic"] is False
    assert result["analysis_status"] == "error"
    assert result["actions"] == []
    assert "illegal semantic_intent" in (result.get("error") or "")


def test_llm_exception_fails_without_heuristic():
    called = {"heuristic": False}

    def boom(action):
        called["heuristic"] = True
        raise AssertionError("classify_action_semantically must not run")

    original = interpret_mod.classify_action_semantically
    interpret_mod.classify_action_semantically = boom
    try:
        result, _ = _run(
            [_action("提交申请")],
            provider=MockLLMProvider(error=RuntimeError("ollama down")),
        )
    finally:
        interpret_mod.classify_action_semantically = original

    assert called["heuristic"] is False
    assert result["analysis_status"] == "error"
    assert result["actions"] == []


def test_llm_unavailable():
    original = interpret_mod.get_llm_provider
    interpret_mod.get_llm_provider = lambda: None
    try:
        result = interpret_job_actions(
            {"job_id": "y", "actions": [_action("提交申请")]},
            llm_provider=None,
        )
    finally:
        interpret_mod.get_llm_provider = original
    assert result["analysis_status"] == "llm_unavailable"
    assert result["actions"] == []


def test_llm_called_with_action_context():
    result, llm = _run([_action("完善在线简历")], "resume_management")
    assert result["analysis_status"] == "ok"
    assert len(llm.calls) == 1
    assert "完善在线简历" in llm.calls[0]["user"]
    assert "semantic_intent" in llm.calls[0]["system"]


def test_action_count_mismatch_fails():
    result, _ = _run(
        [_action("a"), _action("b")],
        provider=MockLLMProvider(intents_response("apply_resume")),
    )
    assert result["analysis_status"] == "error"
    assert "count" in (result.get("error") or "").lower()


def test_interpret_with_mock_llm():
    payload = {
        "page_context": {
            "job_id": "dispatch-1",
            "actions": [_action("提交申请"), _action("Start a new recruiter conversation")],
        }
    }
    result = interpret_job_actions(
        payload["page_context"],
        llm_provider=MockLLMProvider(intents_response("apply_resume", "start_contact")),
    )
    assert result["analysis_status"] == "ok"
    assert result["inferred_context"]["application_evidence"] == "not_applied"


def test_combiner_resume_management_not_apply():
    uncertain = infer_application_evidence(
        [{"semantic_intent": "resume_management"}, {"semantic_intent": "start_contact"}]
    )
    assert uncertain["application_evidence"] == "uncertain"
    assert uncertain["has_apply_intent"] is False


def test_insufficient_data():
    empty = interpret_job_actions({"job_id": "x", "actions": []})
    assert empty["analysis_status"] == "insufficient_data"


def test_no_heuristic_in_merge_source():
    source = TOOL_SOURCE.read_text(encoding="utf-8")
    merge_start = source.index("def _merge_classified_actions")
    merge_end = source.index("def _coerce_page_context")
    merge_src = source[merge_start:merge_end]
    assert "classify_action_semantically" not in merge_src
    tree = ast.parse(source)
    # Production entry interpret_job_actions_with_llm must not name the heuristic function.
    fn = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "interpret_job_actions_with_llm"
    )
    dumped = ast.dump(fn)
    assert "classify_action_semantically" not in dumped
    entry = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "interpret_job_actions"
    )
    assert "classify_action_semantically" not in ast.dump(entry)
    assert "HeuristicActionInterpreter" not in ast.dump(entry)
    assert "HeuristicActionInterpreter" not in dumped


if __name__ == "__main__":
    test_resume_management_online()
    test_resume_management_attachment()
    test_resume_center_nav_not_apply()
    test_start_contact_from_llm()
    test_interest_not_start_contact()
    test_apply_resume_paraphrases_from_llm()
    test_resume_management_plus_start_is_uncertain()
    test_apply_plus_start_is_not_applied()
    test_continue_contact_is_applied()
    test_view_progress_is_applied()
    test_interest_is_uncertain()
    test_missing_or_null_intent_fails()
    test_heuristic_interpreter_cannot_replace_llm()
    test_illegal_intent_fails_without_heuristic()
    test_llm_exception_fails_without_heuristic()
    test_llm_unavailable()
    test_llm_called_with_action_context()
    test_action_count_mismatch_fails()
    test_interpret_with_mock_llm()
    test_combiner_resume_management_not_apply()
    test_insufficient_data()
    test_no_heuristic_in_merge_source()
    print("interpret_job_actions tests passed")
