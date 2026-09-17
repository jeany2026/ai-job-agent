"""Same-turn support_check is Interpretation, never Verification.supported."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tests.mock_llm import MockLLMProvider, claim_verification_response
from understanding.schema import grounded_candidate_supplement
from understanding.verify_candidate_claims import (
    VERIFY_CANDIDATE_CLAIMS_MARKER,
    list_claim_records,
    verify_candidate_claims,
)

SOURCE_PATH = ROOT_DIR / "understanding" / "verify_candidate_claims.py"


def _project(name: str, quote: str | None = None) -> dict:
    return {
        "name": name,
        "description": None,
        "quote": quote,
        "source_kind": "resume",
    }


def test_no_claims_skips_llm():
    llm = MockLLMProvider({"judgments": []})
    result = verify_candidate_claims(
        {"claimed_projects": []},
        raw_text="帮我找深圳产品经理",
        attachments=[],
        llm_provider=llm,
    )
    assert result["evidence_validation_status"] == "skipped"
    assert result["support_check"] == []
    assert result["verifications"] == []
    assert llm.calls == []


def test_same_turn_supported_judgment_is_not_verification():
    llm = MockLLMProvider(
        verify_response=claim_verification_response("claimed_projects[0]", status="supported")
    )
    result = verify_candidate_claims(
        {"claimed_projects": [_project("支付中台", "支付中台建设")]},
        raw_text="简历里写了支付中台建设",
        attachments=[],
        llm_provider=llm,
    )
    assert result["evidence_validation_status"] == "ok"
    item = result["candidate_supplement"]["claimed_projects"][0]
    assert item["evidence_status"] == "unverified"
    assert result["verifications"] == []
    assert not any(entry.get("status") == "supported" for entry in result["verifications"])
    hints = result["support_check"]
    assert hints
    assert hints[0]["kind"] == "support_check"
    assert hints[0]["layer"] == "interpretation"
    assert hints[0]["consistency_hint"] == "consistent"
    assert hints[0]["payload"]["llm_judgment"] == "supported"
    assert VERIFY_CANDIDATE_CLAIMS_MARKER in llm.calls[0]["system"]
    assert "支付中台建设" in llm.calls[0]["user"]


def test_same_turn_unsupported_stays_unverified_and_is_kept():
    llm = MockLLMProvider(
        verify_response=claim_verification_response("claimed_projects[0]", status="unsupported")
    )
    result = verify_candidate_claims(
        {
            "claimed_projects": [_project("编造项目")],
            "claimed_capabilities": [{"name": "支付", "quote": "支付", "source_kind": "user_statement"}],
        },
        raw_text="我做过支付",
        attachments=[],
        llm_provider=llm,
    )
    projects = result["candidate_supplement"]["claimed_projects"]
    assert projects[0]["evidence_status"] == "unverified"
    assert result["verifications"] == []
    project_hint = next(
        item for item in result["support_check"] if item["payload"].get("claim_id") == "claimed_projects[0]"
    )
    assert project_hint["consistency_hint"] == "inconsistent"
    grounded = grounded_candidate_supplement(result["candidate_supplement"])
    assert grounded["claimed_projects"][0]["name"] == "编造项目"
    assert grounded["claimed_capabilities"][0]["name"] == "支付"


def test_missing_judgment_is_unverified_not_guessed():
    llm = MockLLMProvider(verify_response={"judgments": []})
    result = verify_candidate_claims(
        {"claimed_capabilities": [{"name": "支付", "quote": None, "source_kind": "user_statement"}]},
        raw_text="我做过支付",
        attachments=[],
        llm_provider=llm,
    )
    assert result["evidence_validation_status"] == "ok"
    assert result["candidate_supplement"]["claimed_capabilities"][0]["evidence_status"] == "unverified"
    assert result["support_check"][0]["consistency_hint"] == "unclear"
    assert result["verifications"] == []


def test_verifier_unavailable_does_not_use_string_matching():
    result = verify_candidate_claims(
        {"claimed_capabilities": [{"name": "支付", "quote": "支付", "source_kind": "user_statement"}]},
        raw_text="我做过支付",
        attachments=[],
        llm_provider=None,
    )
    assert result["evidence_validation_status"] == "llm_unavailable"
    assert result["candidate_supplement"]["claimed_capabilities"][0]["evidence_status"] == "unverified"
    assert result["verifications"] == []
    source = SOURCE_PATH.read_text(encoding="utf-8")
    assert "not in haystack" not in source
    assert "_collapse" not in source


def test_illegal_verifier_json_is_llm_error():
    llm = MockLLMProvider(verify_error=RuntimeError("LLM response is not valid JSON"))
    result = verify_candidate_claims(
        {"claimed_capabilities": [{"name": "支付", "quote": "支付", "source_kind": "user_statement"}]},
        raw_text="我做过支付",
        attachments=[],
        llm_provider=llm,
    )
    assert result["evidence_validation_status"] == "llm_error"
    assert result["candidate_supplement"]["claimed_capabilities"][0]["evidence_status"] == "unverified"
    assert result["verifications"] == []


def test_illegal_status_fails_verification_only():
    llm = MockLLMProvider(
        verify_response={"judgments": [{"claim_id": "claimed_capabilities[0]", "evidence_status": "maybe"}]}
    )
    result = verify_candidate_claims(
        {"claimed_capabilities": [{"name": "支付", "quote": "支付", "source_kind": "user_statement"}]},
        raw_text="我做过支付",
        attachments=[],
        llm_provider=llm,
    )
    assert result["evidence_validation_status"] == "analysis_failed"
    assert result["candidate_supplement"]["claimed_capabilities"][0]["evidence_status"] == "unverified"
    assert result["verifications"] == []


def test_claim_records_are_structural_not_semantic():
    records = list_claim_records(
        {
            "claimed_projects": [_project("AI Job Agent", "求职 Agent")],
            "acknowledged_gaps": [{"gap": "没有医疗经验", "quote": None}],
        }
    )
    assert [item["claim_id"] for item in records] == [
        "claimed_projects[0]",
        "acknowledged_gaps[0]",
    ]
    assert records[0]["kind"] == "project"
    assert records[1]["kind"] == "gap"
