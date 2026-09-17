"""Conversation-scoped job working memory and reference resolution."""

from __future__ import annotations

from conversation.job_context import (
    JobContext,
    catalog_entry,
    get_job_context,
    hydrate_job_record,
    job_context_catalog,
    job_context_from_dict,
    job_context_from_record,
    job_contexts_from_session,
    job_contexts_from_state,
    save_job_context,
    upsert_job_contexts,
)
from conversation.reference_resolver import (
    RESOLUTION_NONE,
    RESOLUTION_RESOLVED,
    RESOLUTION_UNRESOLVED,
    ReferenceResolution,
    resolve_conversation_reference,
)

__all__ = [
    "JobContext",
    "RESOLUTION_NONE",
    "RESOLUTION_RESOLVED",
    "RESOLUTION_UNRESOLVED",
    "ReferenceResolution",
    "catalog_entry",
    "get_job_context",
    "hydrate_job_record",
    "job_context_catalog",
    "job_context_from_dict",
    "job_context_from_record",
    "job_contexts_from_session",
    "job_contexts_from_state",
    "resolve_conversation_reference",
    "save_job_context",
    "upsert_job_contexts",
]
