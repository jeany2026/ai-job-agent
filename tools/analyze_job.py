"""Compatibility re-export. Implementation lives in job/analyze_job.py."""

from __future__ import annotations

from job.analyze_job import ANALYZE_JOB_SPEC, analyze_job, analyze_job_with_llm
from job.profile_schema import empty_profile as empty_analysis

__all__ = [
    "ANALYZE_JOB_SPEC",
    "analyze_job",
    "analyze_job_with_llm",
    "empty_analysis",
]
