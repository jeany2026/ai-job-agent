"""Deterministic rules used by reduce(). No JD/resume semantic guessing."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.state import Constraints, JobRecord
from rules.blacklist import company_is_blacklisted
from rules.dedupe import is_duplicate
from rules.filters import list_constraint_flags, list_exclude_reason, match_exclude_reason, salary_range_upper_yuan
from rules.rank import rank_records


def test_dedupe_by_job_key():
    existing = {"mock:mock-direct"}
    assert is_duplicate({"platform": "mock", "job_id": "mock-direct"}, existing)
    assert not is_duplicate({"platform": "mock", "job_id": "mock-transfer"}, existing)


def test_blacklist_normalized_company():
    assert company_is_blacklisted("黑名单科技", ["黑名单科技"])
    assert company_is_blacklisted("黑名单科技有限公司", ["黑名单科技"])
    assert not company_is_blacklisted("支付科技有限公司", ["黑名单科技"])


def test_list_filter_blacklist_excludes_city_salary_are_flags():
    constraints = Constraints(blacklist=["黑名单科技"], salary_min=30000, cities=["深圳"])
    blocked = {
        "platform": "mock",
        "job_id": "x",
        "company_name": "黑名单科技",
        "city": "深圳",
        "salary": "40-50K",
    }
    assert list_exclude_reason(blocked, constraints) == "blacklist"
    assert "blacklist" in list_constraint_flags(blocked, constraints)
    low = {
        "platform": "mock",
        "job_id": "y",
        "company_name": "支付科技",
        "city": "深圳",
        "salary": "10-20K",
    }
    assert list_exclude_reason(low, constraints) is None
    assert "salary" in list_constraint_flags(low, constraints)
    ok = {
        "platform": "mock",
        "job_id": "z",
        "company_name": "支付科技",
        "city": "深圳",
        "salary": "25-40K",
    }
    assert list_exclude_reason(ok, constraints) is None
    assert list_constraint_flags(ok, constraints) == []
    other_city = {
        "platform": "mock",
        "job_id": "w",
        "company_name": "支付科技",
        "city": "北京",
        "salary": "40-50K",
    }
    assert list_exclude_reason(other_city, constraints) is None
    assert "city" in list_constraint_flags(other_city, constraints)


def test_unparseable_salary_does_not_exclude():
    constraints = Constraints(salary_min=30000)
    job = {"platform": "mock", "job_id": "n", "company_name": "A", "salary": "面议"}
    assert salary_range_upper_yuan("面议") is None
    assert list_exclude_reason(job, constraints) is None
    assert "salary" not in list_constraint_flags(job, constraints)


def test_hard_requirements_false_is_readable_match_material_not_exclude_instruction():
    label = match_exclude_reason({"analysis_status": "ok", "hard_requirements_met": False})
    assert label == "hard_requirements"
    assert match_exclude_reason({"analysis_status": "ok", "hard_requirements_met": True}) is None
    assert match_exclude_reason({"analysis_status": "llm_error"}) == "llm_error"


def test_rank_puts_yes_strong_first():
    weak = JobRecord(
        job_key="a",
        stage="matched",
        listed_order=0,
        listed={"job_id": "a"},
        match_result={"recommendation": "weak", "overall_fit": "moderate", "hard_requirements_met": True},
    )
    yes = JobRecord(
        job_key="b",
        stage="matched",
        listed_order=1,
        listed={"job_id": "b"},
        match_result={"recommendation": "yes", "overall_fit": "strong", "hard_requirements_met": True},
    )
    ordered = rank_records([weak, yes])
    assert ordered[0].job_key == "b"
    assert ordered[1].job_key == "a"
