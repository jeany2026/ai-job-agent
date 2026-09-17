"""Cross-platform deterministic dedupe. Exact identity only; no title families."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from rules.dedupe import (
    cross_platform_identity,
    is_duplicate,
    remember_job,
    seen_keys,
)

FIXTURE_PATH = ROOT_DIR / "tests" / "fixtures" / "jobs" / "cross_platform_dupes.json"


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_same_platform_job_key_still_dedupes():
    existing = {"mock:mock-direct"}
    assert is_duplicate({"platform": "mock", "job_id": "mock-direct"}, existing)
    assert not is_duplicate({"platform": "mock", "job_id": "mock-transfer"}, existing)


def test_cross_platform_company_title_city_is_duplicate():
    data = _fixture()
    boss, liepin = data["duplicate_pair"]
    existing: set[str] = set()
    remember_job(existing, boss)
    assert is_duplicate(liepin, existing)
    assert cross_platform_identity(boss) == cross_platform_identity(liepin)


def test_title_family_is_not_a_duplicate():
    data = _fixture()
    senior, plain, other_city = data["not_duplicates"]
    existing: set[str] = set()
    remember_job(existing, senior)
    assert not is_duplicate(plain, existing)
    assert not is_duplicate(other_city, existing)


def test_same_job_id_on_other_platform_is_not_enough():
    existing = {"boss:shared-id"}
    other = {
        "platform": "liepin",
        "job_id": "shared-id",
        "job_title": "运营专员",
        "company_name": "另一家公司",
        "city": "北京",
    }
    assert not is_duplicate(other, existing)


def test_exact_url_dedupes_across_platforms():
    data = _fixture()
    first, second = data["same_url_pair"]
    existing: set[str] = set()
    remember_job(existing, first)
    assert is_duplicate(second, existing)


def test_incomplete_identity_does_not_guess():
    data = _fixture()
    first, second = data["incomplete"]
    existing: set[str] = set()
    remember_job(existing, first)
    assert cross_platform_identity(first) is None
    assert not is_duplicate(second, existing)


def test_seen_keys_includes_identities_from_records():
    from agent.state import JobRecord

    data = _fixture()
    boss, liepin = data["duplicate_pair"]
    records = {
        "boss:boss-pay-1": JobRecord(
            job_key="boss:boss-pay-1",
            stage="listed",
            listed_order=0,
            listed=boss,
        )
    }
    existing = seen_keys(records)
    assert "boss:boss-pay-1" in existing
    assert is_duplicate(liepin, existing)
