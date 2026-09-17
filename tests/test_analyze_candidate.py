"""Unit tests for analyze_candidate. Mock LLM only; no keyword extractor."""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from candidate.analyze_candidate import analyze_candidate
from candidate.schema import REQUIRED_OK_ARRAY_FIELDS, empty_profile
from tests.mock_llm import MockLLMProvider, candidate_profile_response

analyze_mod = importlib.import_module("candidate.analyze_candidate")

RESUME_PATH = ROOT_DIR / "tests" / "fixtures" / "resumes" / "sample_pm.txt"
SAMPLE_RESUME = RESUME_PATH.read_text(encoding="utf-8")
SOURCE_PATH = ROOT_DIR / "candidate" / "analyze_candidate.py"
SCHEMA_PATH = ROOT_DIR / "candidate" / "schema.py"


def _run(resume=SAMPLE_RESUME, response=None, *, provider=None):
    llm = provider or MockLLMProvider(response if response is not None else candidate_profile_response())
    return analyze_candidate(resume, llm_provider=llm), llm


def test_full_profile_ok():
    result, llm = _run()
    assert result["analysis_status"] == "ok"
    assert result["error"] is None
    assert result["summary"]
    assert len(result["summary"]) <= 200
    assert result["target_roles"] == ["高级产品经理"]
    assert result["years_experience"] == "8年"
    assert result["education"] == "本科"
    assert result["locations"] == ["深圳"]
    assert result["product_capabilities"]
    assert result["business_capabilities"]
    assert result["technical_capabilities"]
    assert result["industry_experience"]
    assert result["management_experience"]
    assert result["project_experience"]
    assert result["transferable_capabilities"]
    assert result["direct_capabilities"]
    assert result["product_capabilities"][0]["evidence"][0]["quote"]
    assert result["transferable_capabilities"][0]["transfer_rationale"]
    assert result["transferable_capabilities"][0]["from_domain"]
    for field in REQUIRED_OK_ARRAY_FIELDS:
        assert isinstance(result[field], list)
    assert len(llm.calls) == 1
    assert "张三" in llm.calls[0]["user"]
    assert "候选人事实抽取器" in llm.calls[0]["system"]
    assert result["memory"]["status"] in {"usable", "partial"}
    assert result["memory"]["evidence"]
    assert result["memory"]["claims"]
    assert all(item.get("evidence_status") == "unverified" for item in result["memory"]["claims"])
    assert all(item.get("derived_from") for item in result["memory"]["claims"])
    assert result["memory"]["verifications"] == []
    assert any(item.get("kind") == "analyze_candidate" for item in result["memory"]["interpretations"])


def test_resume_object_with_candidate_id():
    result, _ = _run({"candidate_id": "cand-1", "text": SAMPLE_RESUME})
    assert result["analysis_status"] == "ok"
    assert result["candidate_id"] == "cand-1"


def test_insufficient_data_empty_resume():
    llm = MockLLMProvider(candidate_profile_response())
    for resume in (None, "", "   ", {"candidate_id": "x", "text": ""}):
        result = analyze_candidate(resume, llm_provider=llm)
        assert result["analysis_status"] == "insufficient_data"
        assert result["product_capabilities"] == []
        assert result["error"]
    assert llm.calls == []


def test_llm_unavailable():
    original = analyze_mod.get_llm_provider
    analyze_mod.get_llm_provider = lambda: None
    try:
        result = analyze_candidate(SAMPLE_RESUME, llm_provider=None)
    finally:
        analyze_mod.get_llm_provider = original
    assert result["analysis_status"] == "llm_unavailable"
    assert result["product_capabilities"] == []
    assert "not configured" in (result.get("error") or "")


def test_invalid_json_is_llm_error():
    result, _ = _run(provider=MockLLMProvider(error=RuntimeError("LLM response is not valid JSON")))
    assert result["analysis_status"] == "llm_error"
    assert result["product_capabilities"] == []
    assert "JSON" in (result.get("error") or "")


def test_llm_timeout_is_llm_error():
    result, _ = _run(provider=MockLLMProvider(error=TimeoutError("LLM timed out")))
    assert result["analysis_status"] == "llm_error"
    assert "timed out" in (result.get("error") or "")


def test_non_object_json_is_llm_error():
    llm = MockLLMProvider(candidate_profile_response())

    def complete_json(*, system: str, user: str):
        llm.calls.append({"system": system, "user": user})
        return ["not", "an", "object"]

    llm.complete_json = complete_json
    result, _ = _run(provider=llm)
    assert result["analysis_status"] == "llm_error"
    assert result["industry_experience"] == []


def test_missing_evidence_does_not_drop_other_facts():
    payload = candidate_profile_response()
    payload["product_capabilities"][0]["evidence"] = []
    result, _ = _run(response=payload)
    assert result["analysis_status"] == "ok"
    assert result["memory"]["facts"]
    assert any(item.get("name") == "产品规划" for item in result["memory"]["facts"])


def test_quote_mismatch_does_not_fail_ingest():
    payload = candidate_profile_response()
    payload["product_capabilities"][0]["evidence"][0]["quote"] = "从未在简历出现的能力句子"
    result, _ = _run(response=payload)
    assert result["analysis_status"] == "ok"
    assert result["memory"]["status"] in {"usable", "partial"}
    assert "not found in resume" not in (result.get("error") or "")


def test_illegal_kind_is_normalized_not_fatal():
    payload = candidate_profile_response()
    payload["product_capabilities"][0]["kind"] = "maybe_skill"
    result, _ = _run(response=payload)
    assert result["analysis_status"] == "ok"
    assert any(item.get("name") == "产品规划" for item in result["memory"]["facts"])


def test_non_string_fields_are_skipped_not_fatal():
    payload = {
        "facts": [
            {"kind": "education", "name": "education", "value": ["本科", "硕士"]},
            {"kind": "career", "name": "years_experience", "value": 16},
            {"kind": "skill", "name": "产品规划", "value": "做过支付产品", "source_kind": "resume"},
        ]
    }
    result, _ = _run(response=payload)
    assert result["analysis_status"] == "ok"
    assert result["error"] is None
    names = {item.get("name") for item in result["memory"]["facts"]}
    assert "产品规划" in names
    assert "years_experience" in names


def test_numeric_years_are_normalized_not_failed():
    payload = candidate_profile_response()
    payload["years_experience"] = 16
    result, _ = _run(response=payload)
    assert result["analysis_status"] == "ok"
    years = next(item for item in result["memory"]["facts"] if item.get("name") == "years_experience")
    assert years["value"] == {"value": 16, "unit": "years"}
    assert years["normalization_status"] == "coerced"


def test_llm_analysis_status_ignored():
    payload = candidate_profile_response(analysis_status="ok", error="ignore me", candidate_id="from-llm")
    result, _ = _run({"candidate_id": "from-code", "text": SAMPLE_RESUME}, response=payload)
    assert result["analysis_status"] == "ok"
    assert result["error"] is None
    assert result["candidate_id"] == "from-code"


def test_empty_arrays_ok():
    payload = {
        "summary": None,
        "target_roles": [],
        "years_experience": None,
        "education": None,
        "locations": [],
        "product_capabilities": [],
        "business_capabilities": [],
        "technical_capabilities": [],
        "industry_experience": [],
        "management_experience": [],
        "project_experience": [],
        "transferable_capabilities": [],
        "knowledge_gaps": [],
        "direct_capabilities": [],
        "raw_evidence_notes": None,
    }
    result, _ = _run(response=payload)
    assert result["analysis_status"] == "ok"
    for field in REQUIRED_OK_ARRAY_FIELDS:
        assert result[field] == []


def test_registry_and_tools_export():
    from tools import analyze_candidate as exported
    from tools.registry import build_registry

    llm = MockLLMProvider(candidate_profile_response())
    direct = exported(SAMPLE_RESUME, llm_provider=llm)
    via_registry = build_registry().invoke(
        "analyze_candidate",
        {"resume": SAMPLE_RESUME},
        llm_provider=MockLLMProvider(candidate_profile_response()),
    )
    assert direct["analysis_status"] == via_registry["analysis_status"] == "ok"
    assert direct["education"] == via_registry["education"] == "本科"


def test_empty_profile_shape():
    blank = empty_profile(candidate_id="x")
    assert blank["candidate_id"] == "x"
    assert blank["analysis_status"] is None
    for field in REQUIRED_OK_ARRAY_FIELDS:
        assert blank[field] == []


def test_no_heuristic_in_production_path():
    source = SOURCE_PATH.read_text(encoding="utf-8")
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert "extract_skills" not in names
    assert "classify_action_semantically" not in names
    dumped = ast.dump(tree)
    assert "Heuristic" not in dumped
    for needle in (
        "CORE_ABILITY_TERMS",
        "KEYWORD_STOPWORDS",
        'if "简历" in',
        "if '学历' in",
        "synonym",
    ):
        assert needle not in source
        assert needle not in schema
    entry = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "analyze_candidate")
    dumped_entry = ast.dump(entry)
    assert "validate_llm_payload" not in dumped_entry
    assert "_assert_evidence_in_resume" not in source
    assert "analyze_candidate_with_llm" in dumped_entry
