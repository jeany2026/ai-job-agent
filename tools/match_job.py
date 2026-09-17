"""Compatibility re-export. Implementation lives in matching/match_job.py."""

from __future__ import annotations

from matching.match_job import MATCH_JOB_SPEC, match_job, match_job_with_llm
from matching.schema import empty_result

__all__ = [
    "MATCH_JOB_SPEC",
    "empty_result",
    "match_job",
    "match_job_with_llm",
]
