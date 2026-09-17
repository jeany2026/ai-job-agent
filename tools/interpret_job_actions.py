"""Interpret job-page action elements into stable business intents.

A browser adapter supplies observed action elements. Semantic intent comes only
from a real LLM. Deterministic code then combines those intents into
application_evidence. No keyword / word-family / regex semantic guessing.
"""

from __future__ import annotations

import json
import re
from typing import Any

from llm.provider import LLMProvider, get_llm_provider

INTERPRET_JOB_ACTIONS_SPEC = {
    "name": "interpret_job_actions",
    "description": (
        "Interpret observed job-detail page actions into stable business intents, "
        "then infer whether the page shows evidence of an existing application. "
        "Reasoner passes job_key; Binding loads raw_actions from World. "
        "This tool does not click, apply, chat, search, or query a recruiting API."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "job_key": {
                "type": "string",
                "description": "Identity of a live opened World Job. Binding loads raw_actions.",
            },
            "job_id": {"type": "string"},
            "job_url": {"type": "string"},
            "page_context": {
                "type": "object",
                "description": (
                    "Compatibility only. Loop Binding loads actions from World. "
                    "Typical fields: platform, job_id, job_url, actions[]."
                ),
            },
        },
        "required": ["job_key"],
    },
}

SEMANTIC_INTENTS = (
    "apply_resume",
    "start_contact",
    "continue_contact",
    "view_progress",
    "resume_management",
    "interest",
    "favorite",
    "share",
    "report",
    "other",
    "unknown",
)

APPLICATION_EVIDENCE = ("applied", "not_applied", "uncertain")
ACTION_FIELDS = (
    "text",
    "aria_label",
    "title",
    "role",
    "href",
    "tag",
    "surrounding_text",
    "dom_context",
)

# General semantic families. These are lemmas / concepts, not site button copy.
APPLY_FAMILY = {
    "apply",
    "application",
    "resume",
    "cv",
    "curriculum",
    "投递",
    "申请",
    "简历",
}
CONTACT_FAMILY = {
    "contact",
    "chat",
    "chatting",
    "message",
    "conversation",
    "recruiter",
    "hiring",
    "talk",
    "talking",
    "reach",
    "discussion",
    "hr",
    "沟通",
    "联系",
    "聊天",
    "会话",
    "招呼",
}
CONTINUE_FAMILY = {
    "continue",
    "existing",
    "ongoing",
    "already",
    "again",
    "resume",
    "keep",
    "继续",
    "已有",
    "再次",
    "还在",
    "进行中",
}
COMPLETE_FAMILY = {
    "already",
    "submitted",
    "applied",
    "sent",
    "done",
    "已经",
    "申请过",
    "投递过",
    "沟通过",
}
START_FAMILY = {
    "start",
    "begin",
    "initiate",
    "first",
    "new",
    "reach",
    "发起",
    "开始",
    "首次",
    "初次",
}
PROGRESS_FAMILY = {
    "progress",
    "status",
    "track",
    "history",
    "record",
    "进度",
    "进展",
    "状态",
    "记录",
}
FAVORITE_FAMILY = {
    "favorite",
    "favourite",
    "save",
    "star",
    "collect",
    "bookmark",
    "收藏",
    "关注",
}
SHARE_FAMILY = {
    "share",
    "forward",
    "分享",
    "转发",
}
REPORT_FAMILY = {
    "report",
    "complain",
    "abuse",
    "举报",
    "投诉",
}
RESUME_DOCUMENT_CUES = {
    "resume",
    "cv",
    "curriculum",
    "简历",
}
RESUME_CONTINUE_CUES = {
    "chat",
    "chatting",
    "conversation",
    "discussion",
    "contact",
    "talk",
    "沟通",
    "联系",
    "聊天",
    "会话",
}

INTERPRET_SYSTEM_PROMPT = """You interpret job-page UI actions into stable business intents.

Return exactly one JSON object. One output action per input action, same order.
Do not invent extra actions. Do not omit actions.

Allowed semantic_intent values only:
apply_resume, start_contact, continue_contact, view_progress,
resume_management, interest, favorite, share, report, other, unknown

Definitions:
- apply_resume: this control would submit / send / apply a resume or application TO THE CURRENT JOB.
- resume_management: manage the candidate's resume materials (edit, upload, add attachment, open resume center) WITHOUT applying to the current job. The word for resume in the label is not enough for apply_resume.
- start_contact: start a NEW conversation with the recruiter / hiring party for this job.
- continue_contact: continue an ALREADY EXISTING recruiting conversation or application relationship.
- view_progress: view status / progress of an existing application to this job.
- interest: express interest in the job without starting a chat and without applying.
- favorite: save / bookmark / follow the job.
- share: share the posting.
- report: report or complain.
- other: a real control that is none of the above (including site-wide navigation that is not a job action).
- unknown: not enough information. Do not guess.

Primary evidence is THIS action's own text, aria_label, title, role, href, tag.
surrounding_text and dom_context are auxiliary only. Do not assign a nearby control's intent to this action.
Site-wide navigation (home, messages, resume center, profile) is not apply_resume for the current job.
Do not decide applied / not_applied. Only output intents.
Return JSON only.

Output:
{
  "actions": [
    {
      "raw_text": "...",
      "semantic_intent": "apply_resume|start_contact|continue_contact|view_progress|resume_management|interest|favorite|share|report|other|unknown",
      "confidence": 0.0,
      "evidence": "which fields of THIS action you used",
      "reasoning": "short semantic reason"
    }
  ]
}
"""


def interpret_job_actions(
    page_context: dict | str | None = None,
    llm_provider: LLMProvider | None = None,
    **kwargs,
) -> dict:
    """Understand observed job-page actions, then infer application evidence."""
    context = _coerce_page_context(page_context, kwargs)
    if context.get("analysis_status") == "error":
        return context

    actions = [_normalize_action(item) for item in context.get("actions") or []]
    context = {**context, "actions": actions}

    if not actions:
        return _status_result(context, "insufficient_data", "no page actions were provided")

    provider = llm_provider if llm_provider is not None else get_llm_provider()
    if provider is None:
        return _status_result(context, "llm_unavailable", "LLM provider is not configured")

    return interpret_job_actions_with_llm(context, provider)


def interpret_job_actions_with_llm(page_context: dict, llm_provider: LLMProvider) -> dict:
    """LLM classifies each action; application evidence is computed deterministically."""
    user_prompt = (
        "Interpret these observed job-page actions. Return JSON only.\n"
        + json.dumps(_context_for_prompt(page_context), ensure_ascii=False, indent=2)
    )
    try:
        raw = llm_provider.complete_json(system=INTERPRET_SYSTEM_PROMPT, user=user_prompt)
    except Exception as exc:
        return _status_result(page_context, "error", str(exc))

    merged = _merge_classified_actions(page_context.get("actions") or [], raw)
    if merged.get("analysis_status") == "error":
        return _status_result(page_context, "error", merged.get("error") or "invalid LLM action payload")

    classified = merged["actions"]
    inferred = infer_application_evidence(classified)
    return {
        "analysis_status": "ok",
        "job_id": _null_if_blank(page_context.get("job_id")),
        "platform": _null_if_blank(page_context.get("platform")),
        "job_url": _null_if_blank(page_context.get("job_url")),
        "actions": classified,
        "inferred_context": inferred,
    }


def infer_application_evidence(actions: list[dict]) -> dict:
    """Deterministic second layer: intents → application evidence. No page copy here."""
    intents = [item.get("semantic_intent") for item in actions if isinstance(item, dict)]
    has_apply = "apply_resume" in intents
    has_start = "start_contact" in intents
    has_continue = "continue_contact" in intents
    has_progress = "view_progress" in intents
    has_existing = has_continue or has_progress

    if has_existing:
        confidence = 0.92 if has_continue and has_progress else 0.88 if has_continue else 0.84
        notes = (
            "Existing recruiting-relationship intent is present "
            "(continue_contact and/or view_progress), so the page is treated as applied evidence."
        )
        evidence = "applied"
    elif has_apply:
        confidence = 0.9 if has_start else 0.82
        notes = (
            "An initiate-application intent is present and no existing-relationship intent "
            "was observed, so the page is treated as not-yet-applied evidence."
        )
        evidence = "not_applied"
    elif has_start:
        confidence = 0.72
        notes = (
            "Only a first-time contact intent is present. That is not enough to prove "
            "an application already happened, and it is also not an apply control."
        )
        evidence = "uncertain"
    else:
        confidence = 0.4
        notes = "No apply, contact-start, or existing-relationship intent was strong enough to decide."
        evidence = "uncertain"

    return {
        "has_apply_intent": has_apply,
        "has_start_contact_intent": has_start,
        "has_continue_contact_intent": has_continue,
        "has_existing_relation_intent": has_existing,
        "application_evidence": evidence,
        "evidence_source": "page_action_semantics",
        "confidence": confidence,
        "notes": notes,
    }


def classify_action_semantically(action: dict) -> dict:
    """Historical keyword classifier. Not used by interpret_job_actions production path."""
    blob = _action_blob(action)
    tokens = _english_tokens(blob)
    raw_text = _display_text(action)

    if not blob.strip():
        return _action_result(raw_text, "unknown", 0.2, "no action text or context", "empty action")

    flags = {
        "apply": _family_hit(blob, tokens, APPLY_FAMILY),
        "contact": _family_hit(blob, tokens, CONTACT_FAMILY),
        "continue": _family_hit(blob, tokens, CONTINUE_FAMILY),
        "complete": _family_hit(blob, tokens, COMPLETE_FAMILY),
        "start": _family_hit(blob, tokens, START_FAMILY),
        "progress": _family_hit(blob, tokens, PROGRESS_FAMILY),
        "favorite": _family_hit(blob, tokens, FAVORITE_FAMILY),
        "share": _family_hit(blob, tokens, SHARE_FAMILY),
        "report": _family_hit(blob, tokens, REPORT_FAMILY),
        "resume_doc": _family_hit(blob, tokens, RESUME_DOCUMENT_CUES),
        "resume_chat": _family_hit(blob, tokens, RESUME_CONTINUE_CUES),
    }
    href = (_null_if_blank(action.get("href")) or "").lower()
    if re.search(r"(?:^|[/?_=-])(?:apply|application|resume|cv)(?:[/?_=-]|$)", href):
        flags["apply"] = True
        flags["resume_doc"] = True
    if re.search(r"(?:^|[/?_=-])(?:chat|message|im|talk)(?:[/?_=-]|$)", href):
        flags["contact"] = True

    resume_is_document = flags["resume_doc"] and not flags["resume_chat"]
    resume_is_continue = bool(flags["resume_chat"] and "resume" in tokens)

    if flags["continue"] and flags["contact"]:
        intent, confidence = "continue_contact", 0.9
        reason = "continue-aspect plus contact-family"
    elif resume_is_continue and (flags["continue"] or flags["contact"]):
        intent, confidence = "continue_contact", 0.86
        reason = "resume-conversation cue plus contact context"
    elif flags["progress"] or (flags["complete"] and flags["apply"]):
        intent, confidence = "view_progress", 0.88
        reason = "existing-application / progress cue"
    elif flags["complete"] and flags["contact"] and not flags["apply"]:
        intent, confidence = "continue_contact", 0.8
        reason = "completed-aspect plus contact-family"
    elif (flags["apply"] or resume_is_document) and not flags["complete"]:
        intent, confidence = "apply_resume", 0.9 if flags["apply"] else 0.8
        reason = "application / resume-document family without a completed-aspect"
    elif flags["contact"]:
        intent, confidence = "start_contact", 0.84 if flags["start"] else 0.78
        reason = "contact-family without continue/completed-aspect"
    elif flags["favorite"]:
        intent, confidence = "favorite", 0.86
        reason = "save / follow family"
    elif flags["share"]:
        intent, confidence = "share", 0.86
        reason = "share family"
    elif flags["report"]:
        intent, confidence = "report", 0.86
        reason = "report family"
    else:
        intent, confidence = "unknown", 0.35
        reason = "no semantic family matched; refusing to guess"

    used = [name for name, hit in flags.items() if hit]
    evidence = "fields=" + ",".join(_populated_fields(action)) + "; cues=" + ",".join(used or ["none"])
    return _action_result(raw_text, intent, confidence, evidence, reason)


class HeuristicActionInterpreter:
    """Historical keyword classifier. Not a production LLM and not a test double.

    complete_json is disabled so this class cannot replace a real LLM via llm_provider.
    """

    def complete_json(self, *, system: str, user: str) -> dict:
        raise RuntimeError(
            "HeuristicActionInterpreter is not a production LLM. "
            "Use get_llm_provider() / OpenAICompatibleProvider, or a MockLLMProvider in tests."
        )


def _merge_classified_actions(originals: list[dict], raw: Any) -> dict:
    """Accept LLM intents only. Never guess missing/illegal intents."""
    if not isinstance(raw, dict):
        return {"analysis_status": "error", "error": "LLM response JSON must be an object"}
    raw_actions = raw.get("actions")
    if not isinstance(raw_actions, list):
        return {"analysis_status": "error", "error": "LLM response must include an actions list"}
    if len(raw_actions) != len(originals):
        return {
            "analysis_status": "error",
            "error": "LLM action count does not match observed actions",
        }

    merged = []
    for index, original in enumerate(originals):
        predicted = raw_actions[index]
        if not isinstance(predicted, dict):
            return {"analysis_status": "error", "error": "LLM action item must be an object"}
        intent = predicted.get("semantic_intent")
        if intent not in SEMANTIC_INTENTS:
            return {
                "analysis_status": "error",
                "error": f"illegal semantic_intent: {intent!r}",
            }
        confidence = predicted.get("confidence")
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        merged.append(
            {
                "raw_text": _display_text(original) or _null_if_blank(predicted.get("raw_text")),
                "semantic_intent": intent,
                "confidence": confidence,
                "evidence": _null_if_blank(predicted.get("evidence")),
                "reasoning": _null_if_blank(predicted.get("reasoning")),
            }
        )
    return {"analysis_status": "ok", "actions": merged}


def _coerce_page_context(page_context: dict | str | None, kwargs: dict) -> dict:
    if page_context is None and "page_context" in kwargs:
        page_context = kwargs.get("page_context")
    if isinstance(page_context, str):
        try:
            page_context = json.loads(page_context)
        except json.JSONDecodeError:
            return {"analysis_status": "error", "error": "page_context must be an object"}
    if page_context is None:
        page_context = kwargs
    if not isinstance(page_context, dict):
        return {"analysis_status": "error", "error": "page_context must be an object"}
    if isinstance(page_context.get("page_context"), dict) and "actions" not in page_context:
        page_context = page_context["page_context"]
    actions = page_context.get("actions")
    if actions is None:
        actions = []
    if not isinstance(actions, list):
        return {"analysis_status": "error", "error": "actions must be a list"}
    return {
        "platform": _null_if_blank(page_context.get("platform")),
        "job_id": _null_if_blank(page_context.get("job_id")),
        "job_url": _null_if_blank(page_context.get("job_url")),
        "actions": actions,
    }


def _normalize_action(item: Any) -> dict:
    if isinstance(item, str):
        item = {"text": item}
    if not isinstance(item, dict):
        item = {}
    return {field: _null_if_blank(item.get(field)) for field in ACTION_FIELDS}


def _context_for_prompt(page_context: dict) -> dict:
    return {
        "platform": page_context.get("platform"),
        "job_id": page_context.get("job_id"),
        "job_url": page_context.get("job_url"),
        "actions": page_context.get("actions") or [],
    }


def _context_from_prompt(user: str) -> dict:
    start = user.find("{")
    end = user.rfind("}")
    if start < 0 or end <= start:
        return {"actions": []}
    parsed = json.loads(user[start : end + 1])
    if not isinstance(parsed, dict):
        return {"actions": []}
    return parsed


def _action_blob(action: dict) -> str:
    parts = []
    for field in ACTION_FIELDS:
        value = _null_if_blank(action.get(field))
        if value:
            parts.append(value)
    return " ".join(parts)


def _display_text(action: dict) -> str | None:
    for field in ("text", "aria_label", "title"):
        value = _null_if_blank(action.get(field))
        if value:
            return value
    return None


def _populated_fields(action: dict) -> list[str]:
    return [field for field in ACTION_FIELDS if _null_if_blank(action.get(field))]


def _english_tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _family_hit(blob: str, tokens: set[str], family: set[str]) -> bool:
    lower = blob.lower()
    for lemma in family:
        if lemma.isascii():
            if lemma.lower() in tokens:
                return True
        elif lemma in blob:
            return True
        elif lemma.lower() in lower and lemma.isascii():
            return True
    return False


def _action_result(raw_text: str | None, intent: str, confidence: float, evidence: str, reasoning: str) -> dict:
    return {
        "raw_text": raw_text,
        "semantic_intent": intent,
        "confidence": confidence,
        "evidence": evidence,
        "reasoning": reasoning,
    }


def _status_result(page_context: dict, status: str, error: str) -> dict:
    return {
        "analysis_status": status,
        "job_id": _null_if_blank(page_context.get("job_id")),
        "platform": _null_if_blank(page_context.get("platform")),
        "job_url": _null_if_blank(page_context.get("job_url")),
        "actions": [],
        "inferred_context": {
            "has_apply_intent": False,
            "has_start_contact_intent": False,
            "has_continue_contact_intent": False,
            "has_existing_relation_intent": False,
            "application_evidence": "uncertain",
            "evidence_source": "page_action_semantics",
            "confidence": 0.0,
            "notes": error,
        },
        "error": error,
    }


def _null_if_blank(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "n/a"}:
        return None
    return text
