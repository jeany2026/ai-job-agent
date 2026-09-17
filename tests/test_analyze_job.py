"""Unit tests for analyze_job. Mock LLM only; no keyword extractor."""

from __future__ import annotations

import ast
import importlib
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from job.analyze_job import analyze_job
from job.profile_schema import REQUIRED_OK_ARRAY_FIELDS, empty_profile
from tests.mock_llm import MockLLMProvider, job_profile_response

analyze_mod = importlib.import_module("job.analyze_job")

JOB_PATH = ROOT_DIR / "tests" / "fixtures" / "jobs" / "sample_pm.json"
SAMPLE_JOB = json.loads(JOB_PATH.read_text(encoding="utf-8"))
SOURCE_PATH = ROOT_DIR / "job" / "analyze_job.py"
SCHEMA_PATH = ROOT_DIR / "job" / "profile_schema.py"
TOOLS_REEXPORT_PATH = ROOT_DIR / "tools" / "analyze_job.py"

FORBIDDEN = ("适合", "匹配", "推荐", "评分", "分数", "score", "候选人")


def _run(job=None, response=None, *, provider=None):
    llm = provider or MockLLMProvider(response if response is not None else job_profile_response())
    return analyze_job(job if job is not None else SAMPLE_JOB, llm_provider=llm), llm


def _has_requirement(items: list[dict], fragment: str) -> bool:
    return any(fragment in item.get("requirement", "") for item in items)


def test_full_jd_ok():
    result, llm = _run()
    assert result["analysis_status"] == "ok"
    assert result["error"] is None
    assert result["job_id"] == "123456"
    assert result["job_title"] == "高级产品经理"
    assert result["company_name"] == "某科技公司"
    assert result["hard_requirements"]
    assert result["core_requirements"]
    assert result["bonus_requirements"]
    assert result["responsibilities"]
    assert result["keywords"]
    assert result["job_summary"]
    assert len(result["job_summary"]) <= 100
    assert _has_requirement(result["hard_requirements"], "本科")
    assert _has_requirement(result["hard_requirements"], "5年")
    assert any("规划" in item["requirement"] for item in result["core_requirements"])
    assert any("CRM" in item["requirement"].upper() for item in result["bonus_requirements"])
    assert any("负责" in line for line in result["responsibilities"])
    for field in REQUIRED_OK_ARRAY_FIELDS:
        assert isinstance(result[field], list)
    assert len(llm.calls) == 1
    assert "JobProfile" in llm.calls[0]["system"]
    assert "123456" in llm.calls[0]["user"]


def test_insufficient_data_no_jd():
    llm = MockLLMProvider(job_profile_response())
    job = {
        "job_title": "高级产品经理",
        "company_name": "测试公司",
        "job_description": None,
        "requirements": None,
    }
    result = analyze_job(job, llm_provider=llm)
    assert result["analysis_status"] == "insufficient_data"
    assert result["hard_requirements"] == []
    assert result["core_requirements"] == []
    assert result["business_requirements"] == []
    assert result["technical_requirements"] == []
    assert result["industry_requirements"] == []
    assert result["bonus_requirements"] == []
    assert result["responsibilities"] == []
    assert result["keywords"] == []
    assert result["job_summary"] is None
    assert result["experience_requirements"] == {
        "years": None,
        "education": None,
        "seniority": None,
        "other": [],
    }
    assert llm.calls == []


def test_bonus_not_hard():
    result, _ = _run()
    assert result["analysis_status"] == "ok"
    assert result["bonus_requirements"]
    assert any("医疗" in item["requirement"] for item in result["bonus_requirements"])
    assert not any("医疗" in item["requirement"] for item in result["hard_requirements"])
    assert any("CRM" in item["requirement"].upper() for item in result["bonus_requirements"])
    assert not any("CRM" in item["requirement"].upper() for item in result["hard_requirements"])
    assert not any("优先" in item["requirement"] for item in result["hard_requirements"])
    assert not any("加分" in item["requirement"] for item in result["hard_requirements"])


def test_hard_education_and_years():
    result, _ = _run()
    assert result["analysis_status"] == "ok"
    assert _has_requirement(result["hard_requirements"], "本科")
    assert _has_requirement(result["hard_requirements"], "5年")
    assert all(item["explicit"] is True for item in result["hard_requirements"])
    assert all(item["importance"] == "high" for item in result["hard_requirements"])


def test_bonus_markers_in_hard_is_analysis_failed():
    payload = job_profile_response()
    payload["hard_requirements"].append(
        {
            "requirement": "有医疗行业经验优先",
            "category": "industry",
            "importance": "high",
            "explicit": True,
            "evidence_quote": "有医疗行业经验优先",
        }
    )
    result, _ = _run(response=payload)
    assert result["analysis_status"] == "analysis_failed"
    assert result["hard_requirements"] == []
    assert "bonus" in (result.get("error") or "").lower() or "优先" in (result.get("error") or "")


def test_hard_duplicating_bonus_is_analysis_failed():
    payload = job_profile_response()
    payload["hard_requirements"].append(
        {
            "requirement": "CRM经验",
            "category": "business",
            "importance": "high",
            "explicit": True,
            "evidence_quote": "有CRM经验优先",
        }
    )
    result, _ = _run(response=payload)
    assert result["analysis_status"] == "analysis_failed"
    assert result["hard_requirements"] == []
    assert "bonus" in (result.get("error") or "").lower()


def test_inferred_hard_is_analysis_failed():
    payload = job_profile_response()
    payload["hard_requirements"][0]["explicit"] = False
    result, _ = _run(response=payload)
    assert result["analysis_status"] == "analysis_failed"
    assert result["hard_requirements"] == []
    assert "explicit" in (result.get("error") or "")


def test_no_candidate_language():
    result, _ = _run()
    assert result["analysis_status"] == "ok"
    blob = _flatten(result)
    for term in FORBIDDEN:
        assert term not in blob, f"analysis leaked forbidden term: {term}"


def test_forbidden_language_is_analysis_failed():
    payload = job_profile_response()
    payload["job_summary"] = "该岗位适合候选人，建议匹配后推荐。"
    result, _ = _run(response=payload)
    assert result["analysis_status"] == "analysis_failed"
    assert result["job_summary"] is None
    assert "matching" in (result.get("error") or "").lower() or "候选人" in (result.get("error") or "")


def test_llm_unavailable():
    original = analyze_mod.get_llm_provider
    analyze_mod.get_llm_provider = lambda: None
    try:
        result = analyze_job(SAMPLE_JOB, llm_provider=None)
    finally:
        analyze_mod.get_llm_provider = original
    assert result["analysis_status"] == "llm_unavailable"
    assert result["hard_requirements"] == []
    assert "not configured" in (result.get("error") or "")


def test_invalid_json_is_llm_error():
    result, _ = _run(provider=MockLLMProvider(error=RuntimeError("LLM response is not valid JSON")))
    assert result["analysis_status"] == "llm_error"
    assert result["hard_requirements"] == []
    assert "JSON" in (result.get("error") or "")


def test_llm_timeout_is_llm_error():
    result, _ = _run(provider=MockLLMProvider(error=TimeoutError("LLM timed out")))
    assert result["analysis_status"] == "llm_error"
    assert "timed out" in (result.get("error") or "")


def test_non_object_json_is_llm_error():
    llm = MockLLMProvider(job_profile_response())

    def complete_json(*, system: str, user: str):
        llm.calls.append({"system": system, "user": user})
        return ["not", "an", "object"]

    llm.complete_json = complete_json
    result, _ = _run(provider=llm)
    assert result["analysis_status"] == "llm_error"
    assert result["hard_requirements"] == []


def test_illegal_importance_fails_without_guessing():
    payload = job_profile_response()
    payload["hard_requirements"][0]["importance"] = "must"
    result, _ = _run(response=payload)
    assert result["analysis_status"] == "analysis_failed"
    assert result["hard_requirements"] == []
    assert "importance" in (result.get("error") or "")


def test_quote_not_in_job_is_analysis_failed():
    payload = job_profile_response()
    payload["hard_requirements"][0]["evidence_quote"] = "从未在JD出现的硬性句子"
    result, _ = _run(response=payload)
    assert result["analysis_status"] == "analysis_failed"
    assert result["hard_requirements"] == []
    assert "not found in job text" in (result.get("error") or "")


def test_llm_analysis_status_ignored():
    payload = job_profile_response(
        analysis_status="ok",
        error="ignore me",
        job_id="from-llm",
        job_title="LLM职称",
        company_name="LLM公司",
    )
    result, _ = _run(response=payload)
    assert result["analysis_status"] == "ok"
    assert result["error"] is None
    assert result["job_id"] == "123456"
    assert result["job_title"] == "高级产品经理"
    assert result["company_name"] == "某科技公司"


def test_empty_arrays_ok():
    payload = {
        "job_summary": None,
        "hard_requirements": [],
        "core_requirements": [],
        "business_requirements": [],
        "technical_requirements": [],
        "industry_requirements": [],
        "bonus_requirements": [],
        "responsibilities": [],
        "experience_requirements": {
            "years": None,
            "education": None,
            "seniority": None,
            "other": [],
        },
        "keywords": [],
    }
    result, _ = _run(response=payload)
    assert result["analysis_status"] == "ok"
    for field in REQUIRED_OK_ARRAY_FIELDS:
        assert result[field] == []


def test_registry_and_tools_export():
    from tools import analyze_job as exported
    from tools.analyze_job import analyze_job as imported
    from tools.registry import build_registry

    llm = MockLLMProvider(job_profile_response())
    direct = imported(SAMPLE_JOB, llm_provider=llm)
    via_registry = build_registry().invoke(
        "analyze_job",
        {"job": SAMPLE_JOB},
        llm_provider=MockLLMProvider(job_profile_response()),
    )
    exported_result = exported(SAMPLE_JOB, llm_provider=MockLLMProvider(job_profile_response()))
    assert direct["analysis_status"] == via_registry["analysis_status"] == exported_result["analysis_status"] == "ok"
    assert direct["job_title"] == via_registry["job_title"] == "高级产品经理"


def test_empty_profile_shape():
    blank = empty_profile({"job_id": "x", "job_title": "t", "company_name": "c"})
    assert blank["job_id"] == "x"
    assert blank["analysis_status"] is None
    assert blank["error"] is None
    assert blank["hard_requirements"] == []
    for field in REQUIRED_OK_ARRAY_FIELDS:
        assert blank[field] == []


def test_no_heuristic_in_production_path():
    source = SOURCE_PATH.read_text(encoding="utf-8")
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    reexport = TOOLS_REEXPORT_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert "extract_job_requirements" not in names
    classes = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    assert "HeuristicJobAnalyzer" not in classes
    dumped = ast.dump(tree)
    assert "Heuristic" not in dumped
    for needle in (
        "extract_job_requirements",
        "HeuristicJobAnalyzer",
        "CORE_ABILITY_TERMS",
        "KEYWORD_STOPWORDS",
        "BUSINESS_TERMS",
        "TECHNICAL_TERMS",
        "INDUSTRY_TERMS",
        "classify_action_semantically",
    ):
        assert needle not in source
        assert needle not in schema
        assert needle not in reexport
    entry = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "analyze_job")
    assert "validate_llm_payload" in ast.dump(entry) or "analyze_job_with_llm" in ast.dump(entry)


def _flatten(value) -> str:
    if isinstance(value, dict):
        return " ".join(_flatten(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(_flatten(item) for item in value)
    return str(value)
