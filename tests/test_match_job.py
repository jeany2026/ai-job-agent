"""Unit tests for match_job. Mock LLM only; no keyword matcher."""

from __future__ import annotations

import ast
import importlib
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from candidate.schema import empty_profile as empty_candidate
from job.profile_schema import empty_profile as empty_job
from job.profile_schema import validate_llm_payload as validate_job_payload
from matching.match_job import match_job
from matching.schema import REQUIRED_OK_ARRAY_FIELDS, empty_result
from tests.mock_llm import (
    MockLLMProvider,
    candidate_profile_response,
    job_profile_response,
    match_result_response,
)

match_mod = importlib.import_module("matching.match_job")

FIXTURE_DIR = ROOT_DIR / "tests" / "fixtures" / "matching"
SOURCE_PATH = ROOT_DIR / "matching" / "match_job.py"
SCHEMA_PATH = ROOT_DIR / "matching" / "schema.py"
TOOLS_REEXPORT_PATH = ROOT_DIR / "tools" / "match_job.py"

OUTCOMES = ("direct", "transferable", "missing", "insufficient_evidence")


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _ok_candidate(**overrides) -> dict:
    profile = empty_candidate(candidate_id=overrides.pop("candidate_id", "cand-1"))
    payload = candidate_profile_response()
    payload.update(overrides)
    from candidate.schema import validate_llm_payload

    profile.update(validate_llm_payload(payload))
    profile["analysis_status"] = "ok"
    profile["error"] = None
    return profile


def _ok_job(payload: dict | None = None, **passthrough) -> dict:
    job_meta = {
        "job_id": passthrough.get("job_id", "123456"),
        "job_title": passthrough.get("job_title", "高级产品经理"),
        "company_name": passthrough.get("company_name", "某科技公司"),
    }
    profile = empty_job(job_meta)
    profile.update(validate_job_payload(payload or job_profile_response()))
    profile["analysis_status"] = "ok"
    profile["error"] = None
    return profile


def _insurance_job() -> dict:
    payload = job_profile_response()
    payload["core_requirements"] = list(payload["core_requirements"]) + [
        {
            "requirement": "复杂业务系统设计",
            "category": "product_ability",
            "importance": "high",
            "explicit": True,
            "evidence_quote": "复杂业务系统设计",
        }
    ]
    payload["industry_requirements"] = [
        {
            "requirement": "保险行业产品经验",
            "category": "industry",
            "importance": "medium",
            "explicit": True,
            "evidence_quote": "保险行业产品经验",
        }
    ]
    return _ok_job(payload, job_id="ins-1", job_title="保险产品经理")


def _pharmacist_job() -> dict:
    payload = job_profile_response()
    payload["hard_requirements"] = list(payload["hard_requirements"]) + [
        {
            "requirement": "必须持有执业药师资格证",
            "category": "certification",
            "importance": "high",
            "explicit": True,
            "evidence_quote": "必须持有执业药师资格证",
        }
    ]
    return _ok_job(payload, job_id="pharm-1", job_title="执业药师")


def _run(candidate=None, job=None, response=None, *, provider=None):
    llm = provider or MockLLMProvider(response if response is not None else match_result_response())
    return match_job(
        candidate if candidate is not None else _ok_candidate(),
        job if job is not None else _ok_job(),
        llm_provider=llm,
    ), llm


def test_direct_match_ok():
    result, llm = _run(response=_load_fixture("direct.json"))
    assert result["analysis_status"] == "ok"
    assert result["error"] is None
    assert result["candidate_id"] == "cand-1"
    assert result["job_id"] == "123456"
    assert result["hard_requirements_met"] is True
    assert result["hard_requirement_gaps"] == []
    assert result["capability_assessments"][0]["outcome"] == "direct"
    assert result["business_fit"] == "strong"
    assert result["technical_fit"] == "moderate"
    assert result["industry_fit"] == "strong"
    assert result["overall_fit"] == "strong"
    assert result["recommendation"] == "yes"
    assert result["rationale"]
    assert "适合" in result["rationale"]
    for field in REQUIRED_OK_ARRAY_FIELDS:
        assert isinstance(result[field], list)
    assert len(llm.calls) == 1
    assert "MatchResult" in llm.calls[0]["system"]
    assert "CandidateProfile" in llm.calls[0]["user"]
    assert "JobProfile" in llm.calls[0]["user"]
    assert "产品规划" in llm.calls[0]["user"]


def test_transferable_outcome():
    result, _ = _run(
        candidate=_ok_candidate(),
        job=_insurance_job(),
        response=_load_fixture("transferable.json"),
    )
    assert result["analysis_status"] == "ok"
    assessment = result["capability_assessments"][0]
    assert assessment["outcome"] == "transferable"
    assert assessment["transfer_rationale"]
    assert "迁移" in assessment["transfer_rationale"]
    assert result["hard_requirements_met"] is True
    assert result["recommendation"] == "weak"
    assert result["industry_fit"] == "weak"
    assert result["overall_fit"] == "moderate"


def test_hard_missing_outcome():
    result, _ = _run(
        candidate=_ok_candidate(),
        job=_pharmacist_job(),
        response=_load_fixture("hard_missing.json"),
    )
    assert result["analysis_status"] == "ok"
    assert result["hard_requirements_met"] is False
    assert result["hard_requirement_gaps"]
    assert result["hard_requirement_gaps"][0]["status"] == "fail"
    assert "执业药师" in result["hard_requirement_gaps"][0]["requirement"]
    assert result["capability_assessments"][0]["outcome"] == "missing"
    assert result["recommendation"] == "no"
    assert result["overall_fit"] == "weak"


def test_insufficient_evidence_outcome():
    sparse_candidate = empty_candidate(candidate_id="sparse-c")
    sparse_candidate["analysis_status"] = "ok"
    sparse_job = empty_job({"job_id": "sparse-j"})
    sparse_job["analysis_status"] = "ok"
    result, _ = _run(
        candidate=sparse_candidate,
        job=sparse_job,
        response=_load_fixture("insufficient_evidence.json"),
    )
    assert result["analysis_status"] == "ok"
    assert result["hard_requirements_met"] is None
    assert result["capability_assessments"][0]["outcome"] == "insufficient_evidence"
    assert result["recommendation"] == "insufficient_evidence"
    assert result["overall_fit"] == "unknown"
    assert result["rationale"]


def test_four_outcomes_supported():
    seen = set()
    for name, job in (
        ("direct.json", _ok_job()),
        ("transferable.json", _insurance_job()),
        ("hard_missing.json", _pharmacist_job()),
        ("insufficient_evidence.json", empty_job({"job_id": "sparse-j"})),
    ):
        payload = _load_fixture(name)
        candidate = _ok_candidate() if name != "insufficient_evidence.json" else empty_candidate(candidate_id="sparse-c")
        if name == "insufficient_evidence.json":
            candidate["analysis_status"] = "ok"
            job["analysis_status"] = "ok"
        result, _ = _run(candidate=candidate, job=job, response=payload)
        assert result["analysis_status"] == "ok"
        seen.add(result["capability_assessments"][0]["outcome"])
    assert seen == set(OUTCOMES)


def test_insufficient_data_missing_profiles():
    llm = MockLLMProvider(match_result_response())
    for candidate, job in (
        (None, _ok_job()),
        (_ok_candidate(), None),
        (None, None),
    ):
        result = match_job(candidate, job, llm_provider=llm)
        assert result["analysis_status"] == "insufficient_data"
        assert result["capability_assessments"] == []
        assert result["recommendation"] is None
        assert result["hard_requirements_met"] is None
        assert result["error"]
    assert llm.calls == []


def test_failed_input_profile_is_insufficient():
    llm = MockLLMProvider(match_result_response())
    failed = empty_candidate(candidate_id="bad")
    failed["analysis_status"] = "analysis_failed"
    failed["error"] = "no evidence"
    result = match_job(failed, _ok_job(), llm_provider=llm)
    assert result["analysis_status"] == "insufficient_data"
    assert result["candidate_id"] == "bad"
    assert llm.calls == []


def test_non_object_profile_is_analysis_failed():
    llm = MockLLMProvider(match_result_response())
    result = match_job("not-a-profile", _ok_job(), llm_provider=llm)
    assert result["analysis_status"] == "analysis_failed"
    assert "object" in (result.get("error") or "")
    assert llm.calls == []


def test_llm_unavailable():
    original = match_mod.get_llm_provider
    match_mod.get_llm_provider = lambda: None
    try:
        result = match_job(_ok_candidate(), _ok_job(), llm_provider=None)
    finally:
        match_mod.get_llm_provider = original
    assert result["analysis_status"] == "llm_unavailable"
    assert result["capability_assessments"] == []
    assert result["recommendation"] is None
    assert "not configured" in (result.get("error") or "")


def test_invalid_json_is_llm_error():
    result, _ = _run(provider=MockLLMProvider(error=RuntimeError("LLM response is not valid JSON")))
    assert result["analysis_status"] == "llm_error"
    assert result["capability_assessments"] == []
    assert "JSON" in (result.get("error") or "")


def test_llm_timeout_is_llm_error():
    result, _ = _run(provider=MockLLMProvider(error=TimeoutError("LLM timed out")))
    assert result["analysis_status"] == "llm_error"
    assert "timed out" in (result.get("error") or "")


def test_non_object_json_is_llm_error():
    llm = MockLLMProvider(match_result_response())

    def complete_json(*, system: str, user: str):
        llm.calls.append({"system": system, "user": user})
        return ["not", "an", "object"]

    llm.complete_json = complete_json
    result, _ = _run(provider=llm)
    assert result["analysis_status"] == "llm_error"
    assert result["hard_requirement_gaps"] == []


def test_illegal_outcome_fails_without_guessing():
    payload = match_result_response()
    payload["capability_assessments"][0]["outcome"] = "partial_match"
    result, _ = _run(response=payload)
    assert result["analysis_status"] == "analysis_failed"
    assert result["capability_assessments"] == []
    assert result["recommendation"] is None
    assert "outcome" in (result.get("error") or "")


def test_transferable_without_rationale_is_analysis_failed():
    payload = _load_fixture("transferable.json")
    payload["capability_assessments"][0]["transfer_rationale"] = None
    result, _ = _run(job=_insurance_job(), response=payload)
    assert result["analysis_status"] == "analysis_failed"
    assert result["capability_assessments"] == []
    assert "transfer_rationale" in (result.get("error") or "")


def test_illegal_recommendation_fails_without_guessing():
    payload = match_result_response()
    payload["recommendation"] = "maybe"
    result, _ = _run(response=payload)
    assert result["analysis_status"] == "analysis_failed"
    assert result["recommendation"] is None
    assert "recommendation" in (result.get("error") or "")


def test_illegal_fit_fails_without_guessing():
    payload = match_result_response()
    payload["overall_fit"] = "excellent"
    result, _ = _run(response=payload)
    assert result["analysis_status"] == "analysis_failed"
    assert result["overall_fit"] is None
    assert "overall_fit" in (result.get("error") or "")


def test_true_met_with_fail_gap_is_analysis_failed():
    payload = match_result_response()
    payload["hard_requirement_gaps"] = [
        {
            "requirement": "本科及以上学历",
            "status": "fail",
            "notes": "inconsistent",
            "evidence": [],
        }
    ]
    result, _ = _run(response=payload)
    assert result["analysis_status"] == "analysis_failed"
    assert result["hard_requirements_met"] is None
    assert "hard_requirements_met" in (result.get("error") or "")


def test_false_met_without_gaps_is_analysis_failed():
    payload = match_result_response()
    payload["hard_requirements_met"] = False
    payload["hard_requirement_gaps"] = []
    result, _ = _run(response=payload)
    assert result["analysis_status"] == "analysis_failed"
    assert "hard_requirement_gaps" in (result.get("error") or "")


def test_quote_not_in_profiles_is_analysis_failed():
    payload = match_result_response()
    payload["capability_assessments"][0]["evidence"][0]["quote"] = "从未在画像出现的句子"
    result, _ = _run(response=payload)
    assert result["analysis_status"] == "analysis_failed"
    assert result["capability_assessments"] == []
    assert "not found in profiles" in (result.get("error") or "")


def test_llm_analysis_status_ignored():
    payload = match_result_response(
        analysis_status="ok",
        error="ignore me",
        job_id="from-llm",
        candidate_id="from-llm",
    )
    result, _ = _run(
        candidate=_ok_candidate(candidate_id="from-code"),
        job=_ok_job(job_id="job-code"),
        response=payload,
    )
    assert result["analysis_status"] == "ok"
    assert result["error"] is None
    assert result["candidate_id"] == "from-code"
    assert result["job_id"] == "job-code"


def test_empty_arrays_ok():
    payload = {
        "hard_requirements_met": None,
        "hard_requirement_gaps": [],
        "capability_assessments": [],
        "business_fit": "unknown",
        "technical_fit": "unknown",
        "industry_fit": "unknown",
        "overall_fit": "unknown",
        "risks": [],
        "knowledge_gaps": [],
        "recommendation": "insufficient_evidence",
        "rationale": "双方画像数组为空，无法给出匹配结论。",
        "evidence_summary": [],
    }
    result, _ = _run(response=payload)
    assert result["analysis_status"] == "ok"
    for field in REQUIRED_OK_ARRAY_FIELDS:
        assert result[field] == []
    assert result["hard_requirements_met"] is None


def test_ids_passthrough():
    result, _ = _run(candidate=_ok_candidate(candidate_id="c-9"), job=_ok_job(job_id="j-9"))
    assert result["candidate_id"] == "c-9"
    assert result["job_id"] == "j-9"


def test_tools_export():
    from tools import match_job as exported
    from tools.match_job import match_job as imported

    llm = MockLLMProvider(match_result_response())
    direct = imported(_ok_candidate(), _ok_job(), llm_provider=llm)
    exported_result = exported(
        _ok_candidate(),
        _ok_job(),
        llm_provider=MockLLMProvider(match_result_response()),
    )
    assert direct["analysis_status"] == exported_result["analysis_status"] == "ok"
    assert direct["recommendation"] == exported_result["recommendation"] == "yes"


def test_empty_result_shape():
    blank = empty_result(job_id="x", candidate_id="y")
    assert blank["job_id"] == "x"
    assert blank["candidate_id"] == "y"
    assert blank["analysis_status"] is None
    assert blank["hard_requirements_met"] is None
    assert blank["recommendation"] is None
    for field in REQUIRED_OK_ARRAY_FIELDS:
        assert blank[field] == []


def test_no_heuristic_in_production_path():
    source = SOURCE_PATH.read_text(encoding="utf-8")
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    reexport = TOOLS_REEXPORT_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert "classify_action_semantically" not in names
    classes = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    assert "HeuristicJobAnalyzer" not in classes
    dumped = ast.dump(tree)
    assert "Heuristic" not in dumped
    for needle in (
        "extract_skills",
        "extract_job_requirements",
        "HeuristicJobAnalyzer",
        "CORE_ABILITY_TERMS",
        "KEYWORD_STOPWORDS",
        "synonym",
        "enrich_priority",
        'if "本科" in',
        "if '学历' in",
        'if "简历" in',
    ):
        assert needle not in source
        assert needle not in schema
        assert needle not in reexport
    entry = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "match_job")
    assert "validate_llm_payload" in ast.dump(entry) or "match_job_with_llm" in ast.dump(entry)
