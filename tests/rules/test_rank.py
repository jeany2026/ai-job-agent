"""Unified rank is MatchResult-based and stable. Platform is not a quality signal."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.state import JobRecord
from rules.rank import rank_key, rank_records


def _record(
    job_key: str,
    *,
    listed_order: int,
    platform: str,
    recommendation: str,
    overall_fit: str = "strong",
    hard: bool = True,
) -> JobRecord:
    return JobRecord(
        job_key=job_key,
        stage="matched",
        listed_order=listed_order,
        listed={"platform": platform, "job_id": job_key.split(":", 1)[-1]},
        match_result={
            "recommendation": recommendation,
            "overall_fit": overall_fit,
            "hard_requirements_met": hard,
        },
    )


def test_yes_strong_ranks_above_weak_regardless_of_platform():
    liepin_weak = _record("liepin:w", listed_order=0, platform="liepin", recommendation="weak")
    boss_yes = _record("boss:y", listed_order=1, platform="boss", recommendation="yes")
    job51_yes = _record("job51:y", listed_order=2, platform="job51", recommendation="yes", overall_fit="moderate")
    ordered = rank_records([liepin_weak, job51_yes, boss_yes])
    assert [item.job_key for item in ordered] == ["boss:y", "job51:y", "liepin:w"]


def test_rank_is_stable_under_shuffle():
    records = [
        _record("job51:c", listed_order=2, platform="job51", recommendation="weak"),
        _record("boss:a", listed_order=0, platform="boss", recommendation="yes"),
        _record("liepin:b", listed_order=1, platform="liepin", recommendation="yes", overall_fit="moderate"),
    ]
    expected = ["boss:a", "liepin:b", "job51:c"]
    for permutation in (
        records,
        list(reversed(records)),
        [records[1], records[2], records[0]],
        [records[2], records[0], records[1]],
    ):
        ordered = rank_records(permutation)
        assert [item.job_key for item in ordered] == expected


def test_equal_match_uses_listed_order_not_platform_name():
    later_boss = _record("boss:z", listed_order=5, platform="boss", recommendation="yes")
    earlier_liepin = _record("liepin:a", listed_order=1, platform="liepin", recommendation="yes")
    ordered = rank_records([later_boss, earlier_liepin])
    assert [item.job_key for item in ordered] == ["liepin:a", "boss:z"]
    assert rank_key(earlier_liepin) < rank_key(later_boss)


def test_rank_records_stamps_rank_score():
    weak = _record("mock:w", listed_order=0, platform="mock", recommendation="weak")
    yes = _record("mock:y", listed_order=1, platform="mock", recommendation="yes")
    ordered = rank_records([weak, yes])
    assert ordered[0].rank_score == rank_key(yes)
    assert ordered[1].rank_score == rank_key(weak)
