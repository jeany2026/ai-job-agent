"""Same-turn support_check. LLM consistency hint is Interpretation, never Verification.

This is not extraction and not string matching. The checker sees the same
turn corpus (message + attachments) and already-extracted claims, then
records a consistency_hint. UserGoal is out of scope.
Same-turn LLM judgment is not an independent Verification and must not
write evidence_status=supported as a fact.
"""

from __future__ import annotations

import json

from candidate.semantic import (
    consistency_hint_for_llm_status,
    make_interpretation,
)
from llm.provider import LLMProvider
from understanding.schema import (
    ANALYSIS_STATUS_FAILED,
    ANALYSIS_STATUS_LLM_ERROR,
    ANALYSIS_STATUS_LLM_UNAVAILABLE,
    ALLOWED_EVIDENCE_STATUS,
    EVIDENCE_STATUS_UNVERIFIED,
    EVIDENCE_VALIDATION_OK,
    EVIDENCE_VALIDATION_SKIPPED,
    SchemaError,
    claim_id_for,
    empty_supplement,
    iter_candidate_claims,
    mark_claims_unverified,
)

VERIFY_CANDIDATE_CLAIMS_MARKER = "你是候选人事实证据验证器"

VERIFY_CANDIDATE_CLAIMS_SYSTEM_PROMPT = """你是候选人事实证据验证器。你不抽取新事实，不改写求职目标，不搜索职位。

你会收到两份材料：
1. 证据来源：用户这一次的自然语言和附件正文。它们属于同一次表达，是唯一对照材料。
2. 待核验主张：上一层已经抽出的候选人主张（能力、项目、缺口、可迁移主张）。

任务：对每一条已抽出的主张，判断对照材料是否在语义上一致。
这是 consistency_hint / support_check，不是独立 Verification，不能把主张写成已验证事实。
这不是字符串匹配，不是关键词、同义词表或原文逐字比对。

判断规则：
1. supported：对照材料能够支持该主张。允许空格、换行、标点、语序和同义表达差异；允许概括原文已经写出的事实。程序只会把它记成 consistency_hint=consistent。
2. unsupported：对照材料无法支持该主张，或主张明显超出原文。程序只会把它记成 consistency_hint=inconsistent，主张仍保持 unverified。
3. 不要因为 quote 与原文不完全一致就判 unsupported。quote 只是抽取层的提示，不是判定标准。
4. 不要因为附件是 PDF/DOCX 抽出的凌乱文本、缺标点或断行就判 unsupported。
5. 不要从对照材料之外补事实。不要引入未出现的经历。
6. 不要判断 UserGoal、偏好或约束。那些不是候选人事实。
7. 不要新增、删除或改写 claim_id。只评估已经给出的条目。
8. 只返回一个 JSON 对象，不要 Markdown，不要解释。

返回：
{"judgments":[{"claim_id":"claimed_projects[0]","evidence_status":"supported或unsupported或unverified","rationale":"一句依据或null"}]}
"""


def list_claim_records(supplement: dict | None) -> list[dict]:
    """Deterministic claim envelopes for the support_check. No semantic guess."""
    records: list[dict] = []
    extra = supplement if isinstance(supplement, dict) else {}
    for collection, index, item in iter_candidate_claims(extra):
        records.append(
            {
                "claim_id": claim_id_for(collection, index),
                "kind": _kind_for(collection),
                "statement": _statement_for(collection, item),
                "quote": item.get("quote"),
                "source_kind": item.get("source_kind"),
            }
        )
    return records


def verify_candidate_claims(
    supplement: dict | None,
    *,
    raw_text: str | None,
    attachments: list[dict] | None,
    llm_provider: LLMProvider | None,
) -> dict:
    """Return supplement + support_check Interpretations. Never writes Verification.supported."""
    extra = dict(supplement or empty_supplement())
    records = list_claim_records(extra)
    if not records:
        return {
            "candidate_supplement": extra,
            "evidence_validation_status": EVIDENCE_VALIDATION_SKIPPED,
            "support_check": [],
            "verifications": [],
            "error": None,
        }
    if llm_provider is None:
        return {
            "candidate_supplement": mark_claims_unverified(
                extra, rationale="support_check is not configured"
            ),
            "evidence_validation_status": ANALYSIS_STATUS_LLM_UNAVAILABLE,
            "support_check": [],
            "verifications": [],
            "error": "LLM provider is not configured",
        }

    user_prompt = _build_verify_prompt(
        raw_text=raw_text,
        attachments=attachments or [],
        claims=records,
    )
    try:
        raw = llm_provider.complete_json(
            system=VERIFY_CANDIDATE_CLAIMS_SYSTEM_PROMPT,
            user=user_prompt,
        )
    except Exception as exc:
        return {
            "candidate_supplement": mark_claims_unverified(extra, rationale=str(exc)),
            "evidence_validation_status": ANALYSIS_STATUS_LLM_ERROR,
            "support_check": [],
            "verifications": [],
            "error": str(exc),
        }

    if not isinstance(raw, dict):
        return {
            "candidate_supplement": mark_claims_unverified(
                extra, rationale="support_check JSON must be an object"
            ),
            "evidence_validation_status": ANALYSIS_STATUS_LLM_ERROR,
            "support_check": [],
            "verifications": [],
            "error": "LLM response JSON must be an object",
        }

    try:
        judgments = _validate_judgments(raw, expected_ids={item["claim_id"] for item in records})
    except SchemaError as exc:
        return {
            "candidate_supplement": mark_claims_unverified(extra, rationale=str(exc)),
            "evidence_validation_status": ANALYSIS_STATUS_FAILED,
            "support_check": [],
            "verifications": [],
            "error": str(exc),
        }

    return {
        "candidate_supplement": mark_claims_unverified(extra, rationale="same-turn support_check is not verification"),
        "evidence_validation_status": EVIDENCE_VALIDATION_OK,
        "support_check": _support_check_interpretations(records, judgments),
        "verifications": [],
        "error": None,
    }


def _kind_for(collection: str) -> str:
    return {
        "claimed_capabilities": "capability",
        "claimed_projects": "project",
        "acknowledged_gaps": "gap",
        "transfer_claims": "transfer",
    }.get(collection, collection)


def _statement_for(collection: str, item: dict) -> str:
    if collection == "claimed_projects":
        parts = [item.get("name"), item.get("description")]
        return "：".join(str(part) for part in parts if part)
    if collection == "acknowledged_gaps":
        return str(item.get("gap") or "")
    if collection == "transfer_claims":
        return str(item.get("claim") or "")
    return str(item.get("name") or "")


def _build_verify_prompt(
    *,
    raw_text: str | None,
    attachments: list[dict],
    claims: list[dict],
) -> str:
    parts = [
        "请判断下列已抽出的候选人主张是否与本次对照材料语义一致。只返回 judgments JSON。",
        "这是 support_check，不是独立 Verification。",
        "",
        "## 证据来源",
        raw_text or "（无文字）",
    ]
    if attachments:
        parts.append("")
        parts.append("## 附件（与用户输入属于同一次表达，是同一份对照材料）")
        for index, item in enumerate(attachments, start=1):
            name = item.get("filename") or f"attachment-{index}"
            kind = item.get("kind") or item.get("content_type") or "other_attachment"
            parts.append(f"### 附件{index} filename={name} kind={kind}")
            parts.append(item.get("text") or "")
    parts.append("")
    parts.append("## 待核验主张")
    parts.append(json.dumps(claims, ensure_ascii=False, indent=2))
    return "\n".join(parts)


def _validate_judgments(raw: dict, *, expected_ids: set[str]) -> dict[str, dict]:
    items = raw.get("judgments")
    if items is None:
        items = []
    if not isinstance(items, list):
        raise SchemaError("judgments must be an array")
    judged: dict[str, dict] = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise SchemaError(f"judgments[{index}] must be an object")
        claim_id = item.get("claim_id")
        if not isinstance(claim_id, str) or not claim_id.strip():
            raise SchemaError(f"judgments[{index}].claim_id is required")
        claim_id = claim_id.strip()
        if claim_id not in expected_ids:
            continue
        status = item.get("evidence_status")
        if not isinstance(status, str) or status.strip() not in ALLOWED_EVIDENCE_STATUS:
            raise SchemaError(
                f"judgments[{index}].evidence_status must be one of "
                f"{sorted(ALLOWED_EVIDENCE_STATUS)}"
            )
        rationale = item.get("rationale")
        if rationale is not None and not isinstance(rationale, str):
            raise SchemaError(f"judgments[{index}].rationale must be a string or null")
        judged[claim_id] = {
            "evidence_status": status.strip(),
            "rationale": rationale.strip() if isinstance(rationale, str) and rationale.strip() else None,
        }
    return judged


def _support_check_interpretations(records: list[dict], judgments: dict[str, dict]) -> list[dict]:
    interpretations: list[dict] = []
    for record in records:
        claim_id = record["claim_id"]
        judgment = judgments.get(claim_id)
        llm_status = judgment.get("evidence_status") if judgment else None
        interpretations.append(
            make_interpretation(
                kind="support_check",
                derived_from=[claim_id],
                payload={
                    "claim_id": claim_id,
                    "llm_judgment": llm_status,
                    "rationale": judgment.get("rationale") if judgment else "support_check did not return this claim",
                },
                consistency_hint=consistency_hint_for_llm_status(llm_status),
                existing=interpretations,
            )
        )
    return interpretations
