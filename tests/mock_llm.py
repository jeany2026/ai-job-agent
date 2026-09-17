"""Test double for LLMProvider. Returns predetermined JSON. Not a keyword classifier.

AgentRoutingLLM / default_reasoner_decision encode one legal exploration strategy
for tests. They are not a production Pipeline, and Program must not execute this
order on its own.
"""

from __future__ import annotations

VERIFY_CANDIDATE_CLAIMS_MARKER = "你是候选人事实证据验证器"


def _is_claim_verifier(system: str) -> bool:
    return VERIFY_CANDIDATE_CLAIMS_MARKER in (system or "")


def default_claim_verification(user: str) -> dict:
    """Stamp predetermined supported judgments from already-listed claim_ids."""
    from json import loads

    start = user.find("[")
    end = user.rfind("]")
    claims = []
    if start >= 0 and end > start:
        try:
            parsed = loads(user[start : end + 1])
        except Exception:
            parsed = []
        if isinstance(parsed, list):
            claims = parsed
    return claim_verification_response(
        *[
            item.get("claim_id")
            for item in claims
            if isinstance(item, dict) and item.get("claim_id")
        ]
    )


def claim_verification_response(*claim_ids: str, status: str = "supported", rationale: str | None = None) -> dict:
    return {
        "judgments": [
            {
                "claim_id": claim_id,
                "evidence_status": status,
                "rationale": rationale or "predetermined test double",
            }
            for claim_id in claim_ids
        ]
    }


class MockLLMProvider:
    def __init__(
        self,
        response: dict | None = None,
        *,
        error: Exception | None = None,
        verify_response: dict | None = None,
        verify_error: Exception | None = None,
    ):
        self.response = response
        self.error = error
        self.verify_response = verify_response
        self.verify_error = verify_error
        self.calls: list[dict] = []

    def complete_json(self, *, system: str, user: str) -> dict:
        self.calls.append({"system": system, "user": user})
        if _is_claim_verifier(system):
            if self.verify_error is not None:
                raise self.verify_error
            if isinstance(self.verify_response, dict):
                return self.verify_response
            return default_claim_verification(user)
        if self.error is not None:
            raise self.error
        if not isinstance(self.response, dict):
            raise RuntimeError("MockLLMProvider has no response")
        return self.response


KNOWN_TEST_INTENTS = {
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
}


def intents_response(*intents: str) -> dict:
    return {
        "actions": [
            {
                "raw_text": None,
                "semantic_intent": intent,
                "confidence": 0.9,
                "evidence": "mock llm",
                "reasoning": "predetermined test double",
            }
            for intent in intents
        ]
    }


def _interpret_from_page_context(user: str) -> dict:
    """Test double: honor fixture aria_label when it is already a semantic_intent."""
    from json import loads

    actions: list = []
    start = user.find("{")
    end = user.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = loads(user[start : end + 1])
        except Exception:
            parsed = {}
        if isinstance(parsed, dict) and isinstance(parsed.get("actions"), list):
            actions = parsed["actions"]
    labeled = []
    for item in actions:
        if not isinstance(item, dict):
            labeled.append(None)
            continue
        aria = item.get("aria_label")
        labeled.append(aria if aria in KNOWN_TEST_INTENTS else None)
    if actions and all(item is not None for item in labeled):
        return intents_response(*labeled)
    if "mock-applied" in user:
        n = len(actions) or 2
        seq = ["continue_contact", "view_progress"]
        return intents_response(*[seq[i] if i < len(seq) else "other" for i in range(n)])
    n = len(actions) or 2
    defaults = []
    for index in range(n):
        if index == 0:
            defaults.append("apply_resume")
        elif index == 1:
            defaults.append("start_contact")
        else:
            defaults.append("other")
    return intents_response(*defaults)


def _evidence(quote: str, location_hint: str | None = None, field: str | None = None) -> dict:
    return {"quote": quote, "location_hint": location_hint, "field": field}


def candidate_profile_response(**overrides) -> dict:
    """Predetermined CandidateProfile JSON. Tests supply evidence quotes from fixtures."""
    payload = {
        "summary": "八年支付产品经验的高级产品经理，覆盖清分结算与商户平台。",
        "target_roles": ["高级产品经理"],
        "years_experience": "8年",
        "education": "本科",
        "locations": ["深圳"],
        "product_capabilities": [
            {
                "name": "产品规划",
                "kind": "direct",
                "category": "product",
                "description": "支付与清分结算产品规划",
                "explicit": True,
                "evidence": [_evidence("负责支付、清分、结算产品规划与平台建设", "工作经历", "product_capabilities")],
                "confidence": 0.93,
            }
        ],
        "business_capabilities": [
            {
                "name": "支付业务",
                "kind": "direct",
                "category": "business",
                "description": None,
                "explicit": True,
                "evidence": [_evidence("熟悉支付业务", "技能", "business_capabilities")],
                "confidence": 0.9,
            }
        ],
        "technical_capabilities": [
            {
                "name": "SQL",
                "kind": "direct",
                "category": "technical",
                "description": None,
                "explicit": True,
                "evidence": [_evidence("数据分析、SQL", "技能", "technical_capabilities")],
                "confidence": 0.84,
            }
        ],
        "industry_experience": [
            {
                "industry": "支付",
                "role": "高级产品经理",
                "years_or_duration": "2018-2024",
                "description": "某支付公司",
                "evidence": [_evidence("2018-2024 某支付公司 高级产品经理", "工作经历", "industry_experience")],
                "explicit": True,
                "confidence": 0.95,
            }
        ],
        "management_experience": [
            {
                "name": "带产品小组",
                "kind": "direct",
                "category": "management",
                "description": "5人小组",
                "explicit": True,
                "evidence": [_evidence("带过5人产品小组", "工作经历", "management_experience")],
                "confidence": 0.88,
            }
        ],
        "project_experience": [
            {
                "name": "清分结算中台",
                "role": "产品设计",
                "description": "从0到1建设相关平台能力",
                "outcomes": "上线后日均交易量提升30%",
                "capabilities_demonstrated": ["产品设计"],
                "evidence": [
                    _evidence("清分结算中台：负责产品设计，上线后日均交易量提升30%。", "项目经验", "project_experience")
                ],
                "explicit": True,
                "confidence": 0.91,
            }
        ],
        "transferable_capabilities": [
            {
                "name": "复杂业务系统设计",
                "from_domain": "支付清分结算",
                "to_domain_hint": "金融科技平台",
                "transfer_rationale": "已主导清分结算中台与商户平台建设，可迁移到同类复杂业务系统。",
                "evidence": [_evidence("主导从0到1建设商户平台", "工作经历", "transferable_capabilities")],
                "confidence": 0.8,
            }
        ],
        "knowledge_gaps": [
            {
                "gap": "简历未写明具体技术架构深度",
                "severity": "low",
                "evidence": [],
                "notes": "未出现架构职称或底层系统设计原文",
            }
        ],
        "direct_capabilities": [
            {
                "name": "产品规划",
                "kind": "direct",
                "category": "product",
                "description": None,
                "explicit": True,
                "evidence": [_evidence("负责支付、清分、结算产品规划与平台建设", "工作经历", "direct_capabilities")],
                "confidence": 0.93,
            }
        ],
        "raw_evidence_notes": None,
    }
    payload.update(overrides)
    return payload


def job_profile_response(**overrides) -> dict:
    """Predetermined JobProfile JSON. Tests supply evidence quotes from job fixtures."""
    payload = {
        "job_summary": "该岗位负责平台产品规划，涉及支付清分结算，硬性要求本科与五年经验。",
        "hard_requirements": [
            {
                "requirement": "本科及以上学历",
                "category": "education",
                "importance": "high",
                "explicit": True,
                "evidence_quote": "本科及以上学历",
            },
            {
                "requirement": "5年以上产品经验",
                "category": "experience",
                "importance": "high",
                "explicit": True,
                "evidence_quote": "5年以上产品经验",
            },
            {
                "requirement": "必须具备产品规划能力",
                "category": "product_ability",
                "importance": "high",
                "explicit": True,
                "evidence_quote": "必须具备产品规划能力",
            },
        ],
        "core_requirements": [
            {
                "requirement": "产品规划",
                "category": "product_ability",
                "importance": "high",
                "explicit": True,
                "evidence_quote": "负责平台产品规划",
            }
        ],
        "business_requirements": [
            {
                "requirement": "支付业务",
                "category": "business",
                "importance": "medium",
                "explicit": True,
                "evidence_quote": "熟悉支付业务",
            }
        ],
        "technical_requirements": [],
        "industry_requirements": [],
        "bonus_requirements": [
            {
                "requirement": "CRM经验",
                "category": "business",
                "importance": "low",
                "explicit": True,
                "evidence_quote": "有CRM经验优先",
            },
            {
                "requirement": "医疗行业经验",
                "category": "industry",
                "importance": "low",
                "explicit": True,
                "evidence_quote": "有医疗行业经验优先",
            },
        ],
        "responsibilities": [
            "负责平台产品规划，推动支付、清分、结算等业务建设。",
        ],
        "experience_requirements": {
            "years": "5年以上产品经验",
            "education": "本科及以上学历",
            "seniority": "高级",
            "other": [],
        },
        "keywords": ["产品规划", "支付", "清分", "结算", "CRM"],
    }
    payload.update(overrides)
    return payload


def match_result_response(**overrides) -> dict:
    """Predetermined MatchResult JSON. Evidence quotes must appear in input profiles."""
    payload = {
        "hard_requirements_met": True,
        "hard_requirement_gaps": [],
        "capability_assessments": [
            {
                "dimension": "产品规划",
                "outcome": "direct",
                "job_requirement_ref": "必须具备产品规划能力",
                "candidate_capability_ref": "产品规划",
                "transfer_rationale": None,
                "evidence": [
                    _evidence("必须具备产品规划能力", "hard_requirements", "capability_assessments"),
                    _evidence("负责支付、清分、结算产品规划与平台建设", "product_capabilities", "capability_assessments"),
                ],
            }
        ],
        "business_fit": "strong",
        "technical_fit": "moderate",
        "industry_fit": "strong",
        "overall_fit": "strong",
        "risks": [],
        "knowledge_gaps": [
            {
                "gap": "CRM经验未在候选人画像中出现",
                "severity": "low",
                "evidence": [_evidence("有CRM经验优先", "bonus_requirements", "knowledge_gaps")],
                "notes": "JD加分项，非硬性",
            }
        ],
        "recommendation": "yes",
        "rationale": "候选人具备明示的产品规划与支付业务经验，学历与年限满足硬性要求，因此适合该岗位。",
        "evidence_summary": [
            _evidence("本科及以上学历", "hard_requirements", "evidence_summary"),
            _evidence("负责支付、清分、结算产品规划与平台建设", "product_capabilities", "evidence_summary"),
        ],
    }
    payload.update(overrides)
    return payload


def goal_response(**overrides) -> dict:
    payload = {
        "target_roles": ["高级产品经理"],
        "cities": ["深圳"],
        "salary_min": None,
        "focus_areas": ["金融科技", "支付", "复杂业务系统"],
        "exclude_companies": [],
        "platforms": [],
    }
    payload.update(overrides)
    return payload


def understanding_response(**overrides) -> dict:
    """Predetermined UserInputUnderstanding JSON. Not a keyword classifier."""
    payload = {
        "user_goal": {
            "target_roles": [],
            "cities": [],
            "salary_min": None,
            "focus_areas": [],
            "exclude_companies": [],
            "platforms": [],
        },
        "candidate_supplement": {
            "statements": [],
            "claimed_capabilities": [],
            "claimed_projects": [],
            "acknowledged_gaps": [],
            "transfer_claims": [],
            "persist_requested": False,
        },
        "preferences": [],
        "constraints": {
            "salary_min": None,
            "cities": [],
            "exclude_companies": [],
            "notes": [],
        },
        "matching_context": None,
        "persist_requested": False,
        "conversation_reference": None,
        "task_kind": None,
    }
    for key, value in overrides.items():
        if (
            key in {"user_goal", "candidate_supplement", "constraints"}
            and isinstance(value, dict)
            and isinstance(payload.get(key), dict)
        ):
            merged = dict(payload[key])
            merged.update(value)
            payload[key] = merged
        else:
            payload[key] = value
    if "persist_requested" in overrides:
        supplement = dict(payload["candidate_supplement"])
        supplement["persist_requested"] = bool(overrides["persist_requested"])
        payload["candidate_supplement"] = supplement
    return payload


def conversation_reference(**overrides) -> dict:
    hint_overrides = overrides.pop("resolution_hint", None)
    payload = {
        "type": "conversation_reference",
        "target_kind": "job",
        "reference_text": None,
        "resolution_hint": {
            "recency": None,
            "ordinal": None,
            "round_offset": None,
            "recommended_only": False,
            "highest_match": False,
            "semantic_filters": [],
        },
    }
    payload.update(overrides)
    if isinstance(hint_overrides, dict):
        hint = dict(payload["resolution_hint"])
        hint.update(hint_overrides)
        payload["resolution_hint"] = hint
    return payload


def plan_search_response(**overrides) -> dict:
    payload = {
        "keyword": "产品总监",
        "city": "深圳",
        "rationale": "UserGoal 仍需要覆盖产品管理方向以补足推荐数量。",
    }
    payload.update(overrides)
    return payload


def transferable_job_profile_response(**overrides) -> dict:
    payload = {
        "job_summary": "该岗位负责保险产品规划与复杂业务系统设计。",
        "hard_requirements": [
            {
                "requirement": "本科及以上学历",
                "category": "education",
                "importance": "high",
                "explicit": True,
                "evidence_quote": "本科及以上学历",
            },
            {
                "requirement": "5年以上产品经验",
                "category": "experience",
                "importance": "high",
                "explicit": True,
                "evidence_quote": "5年以上产品经验",
            },
            {
                "requirement": "必须具备产品规划能力",
                "category": "product_ability",
                "importance": "high",
                "explicit": True,
                "evidence_quote": "必须具备产品规划能力",
            },
        ],
        "core_requirements": [
            {
                "requirement": "产品规划",
                "category": "product_ability",
                "importance": "high",
                "explicit": True,
                "evidence_quote": "负责保险产品规划与复杂业务系统设计。",
            },
            {
                "requirement": "复杂业务系统设计",
                "category": "product_ability",
                "importance": "high",
                "explicit": True,
                "evidence_quote": "复杂业务系统设计",
            },
        ],
        "business_requirements": [],
        "technical_requirements": [],
        "industry_requirements": [
            {
                "requirement": "保险行业产品经验",
                "category": "industry",
                "importance": "medium",
                "explicit": True,
                "evidence_quote": "保险行业产品经验",
            }
        ],
        "bonus_requirements": [],
        "responsibilities": ["负责保险产品规划与复杂业务系统设计。"],
        "experience_requirements": {
            "years": "5年以上产品经验",
            "education": "本科及以上学历",
            "seniority": None,
            "other": [],
        },
        "keywords": ["保险", "产品规划", "复杂业务系统"],
    }
    payload.update(overrides)
    return payload


def hard_job_profile_response(**overrides) -> dict:
    payload = {
        "job_summary": "该岗位负责药品质量管理，须持有执业药师资格证。",
        "hard_requirements": [
            {
                "requirement": "必须持有执业药师资格证",
                "category": "certification",
                "importance": "high",
                "explicit": True,
                "evidence_quote": "必须持有执业药师资格证",
            },
            {
                "requirement": "本科及以上学历",
                "category": "education",
                "importance": "high",
                "explicit": True,
                "evidence_quote": "本科及以上学历",
            },
        ],
        "core_requirements": [],
        "business_requirements": [],
        "technical_requirements": [],
        "industry_requirements": [],
        "bonus_requirements": [],
        "responsibilities": ["负责药品质量管理。"],
        "experience_requirements": {
            "years": None,
            "education": "本科及以上学历",
            "seniority": None,
            "other": [],
        },
        "keywords": ["执业药师"],
    }
    payload.update(overrides)
    return payload


class AgentRoutingLLM:
    """Test double that returns predetermined JSON per tool prompt.

    One legal exploration strategy for tests. Not production scheduling, not a
    Program pipeline, not a keyword classifier.
    """

    def __init__(self):
        self.calls: list[dict] = []

    def complete_json(self, *, system: str, user: str) -> dict:
        self.calls.append({"system": system, "user": user})
        if _is_claim_verifier(system):
            return default_claim_verification(user)
        if "你是全局 Agent Reasoner" in system:
            return default_reasoner_decision(user)
        if "UserInputUnderstanding" in system:
            return understanding_response(user_goal=goal_response())
        if "JobSearchDecision" in system or "下一步决策器" in system:
            return _job_search_decision(user)
        if "SearchPlan" in system:
            return plan_search_response()
        if "UserGoal" in system:
            return goal_response()
        if "MatchResult" in system:
            from json import loads
            from pathlib import Path

            fixture_dir = Path(__file__).resolve().parent / "fixtures" / "matching"
            if "执业药师" in user:
                return loads((fixture_dir / "hard_missing.json").read_text(encoding="utf-8"))
            if "保险产品经理" in user:
                return loads((fixture_dir / "transferable.json").read_text(encoding="utf-8"))
            return match_result_response()
        if "候选人事实抽取器" in system or "候选人事实增量更新器" in system or "CandidateProfile" in system:
            return candidate_profile_response()
        if "JobProfile" in system:
            if "mock-hard" in user or "执业药师" in user:
                return hard_job_profile_response()
            if "mock-transfer" in user or "保险产品经理" in user:
                return transferable_job_profile_response()
            return job_profile_response()
        if "semantic_intent" in system:
            return _interpret_from_page_context(user)
        raise RuntimeError("AgentRoutingLLM has no predetermined JSON for this prompt")


def _job_search_decision(user: str) -> dict:
    """Predetermined JobSearchDecision JSON for tests. Not a production score threshold."""
    from json import loads

    try:
        payload = loads(user)
    except Exception:
        payload = {}
    match = payload.get("match_result") if isinstance(payload, dict) else {}
    if not isinstance(match, dict):
        match = {}
    listing = payload.get("job_listing") if isinstance(payload, dict) else {}
    title = str((listing or {}).get("job_title") or "")
    if match.get("hard_requirements_met") is False or "执业药师" in title or "执业药师" in user:
        return {
            "next_action": "REJECT_JOB",
            "rationale": "核心硬约束不满足，不值得占用用户注意力。",
        }
    if match.get("recommendation") == "no":
        return {
            "next_action": "REJECT_JOB",
            "rationale": "匹配结果不支持把这个职位交给用户决定。",
        }
    evidence = payload.get("application_evidence") if isinstance(payload, dict) else None
    if evidence == "applied":
        return {
            "next_action": "REJECT_JOB",
            "rationale": "已经申请过，不再占用用户注意力。",
        }
    return {
        "next_action": "SURFACE_TO_USER",
        "rationale": "综合目标、匹配评估与约束，这个职位值得用户决定。",
    }


def default_reasoner_decision(user: str, *, after_match: str | None = None) -> dict:
    """One legal test-double exploration strategy. Not a production Pipeline.

    Program must not execute this order. after_match may be ask_user / continue /
    finish so other doubles can vary the post-match choice; the default is also
    only a test strategy, not 'match 后必须 ask_user'.
    """
    from json import loads

    try:
        ctx = loads(user)
    except Exception:
        ctx = {}
    memory = ctx.get("memory") if isinstance(ctx.get("memory"), dict) else {}
    observation = ctx.get("observation") if isinstance(ctx.get("observation"), dict) else {}
    constraints = ctx.get("constraints") if isinstance(ctx.get("constraints"), dict) else {}
    history = ctx.get("history") if isinstance(ctx.get("history"), dict) else {}
    context = ctx.get("context") if isinstance(ctx.get("context"), dict) else {}
    goal = ctx.get("goal") if isinstance(ctx.get("goal"), dict) else {}
    if not goal:
        goal = memory.get("goal") if isinstance(memory.get("goal"), dict) else {}
    understanding = memory.get("understanding") if isinstance(memory.get("understanding"), dict) else {}
    if not understanding and isinstance(context.get("understanding"), dict):
        understanding = context["understanding"]
    jobs = [item for item in (ctx.get("jobs") or memory.get("jobs") or []) if isinstance(item, dict)]
    data_source = constraints.get("data_source") or context.get("data_source") or "mock"
    task = {
        "task_control": context.get("task_control") or constraints.get("task_control"),
        "task_intent": context.get("task_intent") or constraints.get("task_intent"),
        "job_search_task_id": context.get("job_search_task_id") or constraints.get("job_search_task_id"),
        "waiting_user_job_key": context.get("waiting_user_job_key") or constraints.get("waiting_user_job_key"),
        "pending_job_key": context.get("pending_job_key") or constraints.get("pending_job_key"),
        "follow_up_of": context.get("follow_up_of") or constraints.get("follow_up_of"),
        "active_tasks": context.get("active_tasks") or observation.get("active_tasks") or [],
    }
    candidate_profile = (
        ctx.get("candidate_profile")
        or memory.get("candidate_profile")
        or (context.get("candidate_context") or {}).get("persistent_profile")
        or (constraints.get("candidate_context") or {}).get("persistent_profile")
    )
    candidate_context = context.get("candidate_context") or constraints.get("candidate_context") or {
        "persistent_profile": candidate_profile,
        "candidate_memory": memory.get("candidate_memory"),
    }
    search_name = "search_jobs"
    open_name = "open_job"

    def _job_blob(item: dict) -> dict:
        job = item.get("job")
        base = job if isinstance(job, dict) else {}
        card = item.get("listed_card_facts") if isinstance(item.get("listed_card_facts"), dict) else {}
        # Prefer card facts for title/company; keep identity from item/base.
        merged = {**card, **base, **{k: item.get(k) for k in ("job_key", "job_id", "job_url", "stage") if item.get(k)}}
        if not merged.get("job_title") and card.get("job_title"):
            merged["job_title"] = card.get("job_title")
        if not merged.get("company_name") and card.get("company_name"):
            merged["company_name"] = card.get("company_name")
        return merged

    def _identity_args(item: dict) -> dict:
        job = _job_blob(item)
        arguments: dict = {}
        for field in ("job_key", "job_id", "job_url"):
            value = item.get(field) or job.get(field)
            if value:
                arguments[field] = value
        return arguments

    def _named_job(*want_keys: str) -> dict:
        wanted = [key for key in want_keys if key]
        result = observation.get("result") if isinstance(observation.get("result"), dict) else {}
        for field in ("job_key", "job_id", "job_url"):
            value = result.get(field) or observation.get(field)
            if not value:
                continue
            for item in jobs:
                job = _job_blob(item)
                if item.get(field) == value or job.get(field) == value:
                    return item
        for key in wanted:
            for item in jobs:
                if item.get("job_key") == key:
                    return item
        return {}

    def _job_needing(*, stage: str | None = None, has_interpret=None, has_job_profile=None, has_match=None) -> dict:
        # Test-double choice: skip user-explicit blacklist flags. Program still leaves those jobs in Memory.
        matched = []
        for item in jobs:
            flags = item.get("constraint_flags") or []
            if "blacklist" in flags:
                continue
            if stage is not None and item.get("stage") != stage:
                continue
            if has_interpret is not None and bool(item.get("has_interpret")) != has_interpret:
                continue
            if has_job_profile is not None and bool(item.get("has_job_profile")) != has_job_profile:
                continue
            if has_match is not None and bool(item.get("has_match")) != has_match:
                continue
            matched.append(item)
        matched.sort(key=lambda item: item.get("listed_order") if isinstance(item.get("listed_order"), int) else 10**9)
        return matched[0] if matched else {}

    def has_goal() -> bool:
        return bool(
            goal.get("target_roles")
            or goal.get("cities")
            or goal.get("focus_areas")
            or goal.get("salary_min") is not None
        )

    def search_action(keyword: str | None = None, city: str | None = None) -> dict:
        roles = goal.get("target_roles") or []
        cities = goal.get("cities") or []
        text = keyword or (str(roles[0]) if roles else "产品经理")
        arguments: dict = {"keyword": text, "limit": 8, "mode": "fresh"}
        place = city or (str(cities[0]) if cities else None)
        if place:
            arguments["city"] = place
        elif data_source == "boss":
            arguments["city"] = "深圳"
            place = "深圳"
        sess = history.get("search_session") if isinstance(history.get("search_session"), dict) else {}
        same_keyword = str(sess.get("keyword") or "").casefold() == str(text).casefold()
        same_city = True
        if place and sess.get("city") is not None:
            same_city = str(sess.get("city") or "").casefold() == str(place).casefold()
        if sess.get("can_continue") and same_keyword and same_city:
            arguments["mode"] = "continue"
        return {
            "action_type": "tool",
            "tool_name": search_name,
            "arguments": arguments,
            "reason": "search from understood goal",
        }

    def unused_role() -> str | None:
        used = {str(item).casefold() for item in (history.get("used_keywords") or constraints.get("used_keywords") or [])}
        for role in goal.get("target_roles") or []:
            text = str(role).strip()
            if text and text.casefold() not in used:
                return text
        return None

    def continue_exploring() -> dict:
        recorded = history.get("recorded_stop_reason") or constraints.get("stop_reason")
        if recorded in {"plan_search_failed", "no_next_plan"}:
            return {"action_type": "finish", "reason": recorded}
        opens = int(constraints.get("remaining_opens") or 0)
        if opens <= 0:
            return {"action_type": "finish", "reason": "open_quota"}
        nxt = open_listed()
        if nxt is not None:
            return nxt
        hydrated = hydrate_history_job()
        if hydrated is not None:
            return hydrated
        searches = int(constraints.get("remaining_search_slots") or 0)
        if searches <= 0:
            return {"action_type": "finish", "reason": "no listed jobs"}
        next_role = unused_role()
        if next_role:
            cities = goal.get("cities") or []
            return search_action(next_role, str(cities[0]) if cities else None)
        return {
            "action_type": "tool",
            "tool_name": "plan_search",
            "arguments": {
                "goal": goal,
                "used_keywords": list(history.get("used_keywords") or constraints.get("used_keywords") or []),
                "cities": list(goal.get("cities") or []),
                "platform": data_source,
            },
            "reason": "need another search plan",
        }

    def open_listed() -> dict | None:
        target = _job_needing(stage="listed")
        if not target:
            return None
        arguments = _identity_args(target)
        if not arguments:
            return None
        return {"action_type": "tool", "tool_name": open_name, "arguments": arguments, "reason": "open a listed job"}

    def hydrate_history_job() -> dict | None:
        """Test-double: pull one dormant history job into Live World. Not a Program pipeline."""
        persistence = context.get("persistence") if isinstance(context.get("persistence"), dict) else {}
        history_jobs = persistence.get("history_jobs") or []
        live_keys = {str(item.get("job_key")) for item in jobs if item.get("job_key")}
        for item in history_jobs:
            if not isinstance(item, dict):
                continue
            key = item.get("job_key")
            if not key or key in live_keys or item.get("live"):
                continue
            return {
                "action_type": "tool",
                "tool_name": "hydrate_job_reference",
                "arguments": {"job_key": key, "task_id": _task_id()},
                "reason": "test-double: hydrate dormant history job",
            }
        return None

    def has_match_evidence() -> bool:
        profile = candidate_profile
        mem = memory.get("candidate_memory")
        if isinstance(profile, dict) and (
            profile.get("analysis_status") == "ok"
            or profile.get("direct_capabilities")
            or profile.get("project_experience")
        ):
            return True
        if isinstance(mem, dict) and (mem.get("facts") or mem.get("status") in {"usable", "partial"}):
            return True
        return False

    def _ctx_intake() -> list[dict]:
        items = [item for item in (ctx.get("intake") or []) if isinstance(item, dict) and item.get("id")]
        if items:
            return items
        nested = memory.get("intake") if isinstance(memory.get("intake"), list) else []
        if nested:
            return [item for item in nested if isinstance(item, dict) and item.get("id")]
        context_intake = context.get("intake") if isinstance(context.get("intake"), list) else []
        return [item for item in context_intake if isinstance(item, dict) and item.get("id")]

    def _ctx_evidence() -> list[dict]:
        items = [item for item in (ctx.get("evidence") or []) if isinstance(item, dict) and item.get("id")]
        if items:
            return items
        nested = memory.get("evidence") if isinstance(memory.get("evidence"), list) else []
        return [item for item in nested if isinstance(item, dict) and item.get("id")]

    def _intake_ids() -> list[str]:
        ids: list[str] = []
        for item in _ctx_intake():
            ident = str(item.get("id"))
            if ident and ident not in ids:
                ids.append(ident)
        return ids

    def _evidence_ids(*content_types: str) -> list[str]:
        ids: list[str] = []
        for item in _ctx_evidence():
            if content_types and item.get("content_type") not in content_types:
                continue
            ident = str(item.get("id"))
            if ident and ident not in ids:
                ids.append(ident)
        return ids

    def _has_non_message_intake() -> bool:
        """Upload / resume= carriers only — not every text message."""
        for item in _ctx_intake():
            carrier = item.get("carrier")
            if carrier in {"upload", "compat_parameter"}:
                return True
            if item.get("filename"):
                return True
        return False

    def _should_ingest_candidate() -> bool:
        """Text and attachments are both materials; ingest when Understanding extracted supplement."""
        from understanding.schema import has_candidate_supplement

        if _has_non_message_intake():
            return True
        if understanding.get("has_candidate_supplement"):
            return True
        if has_candidate_supplement(understanding):
            return True
        supplement = context.get("candidate_supplement")
        if has_candidate_supplement({"candidate_supplement": supplement or {}}):
            return True
        return bool(understanding.get("persist_requested"))

    def analyze_candidate_action() -> dict:
        intake = _intake_ids()
        if intake:
            return {
                "action_type": "tool",
                "tool_name": "analyze_candidate",
                "arguments": {"intake_ids": intake},
                "reason": "optional candidate ingest",
            }
        ids = _evidence_ids() or _evidence_ids("intake_text", "resume", "user_message", "user_statement", "project_document")
        return {
            "action_type": "tool",
            "tool_name": "analyze_candidate",
            "arguments": {"evidence_ids": ids},
            "reason": "optional candidate ingest",
        }

    kind = observation.get("kind")
    last_tool = observation.get("tool_name")
    control = task.get("task_control")
    result = observation.get("result") if isinstance(observation.get("result"), dict) else {}

    def understand_action() -> dict:
        intake = _intake_ids()
        if intake:
            return {
                "action_type": "tool",
                "tool_name": "understand_user_input",
                "arguments": {"intake_ids": intake},
                "reason": "need to understand this user turn",
            }
        return {
            "action_type": "tool",
            "tool_name": "understand_user_input",
            "arguments": {"evidence_ids": _evidence_ids()},
            "reason": "need to understand this user turn",
        }

    if kind in {"ambiguous_evidence", "missing_evidence"}:
        available_intake = observation.get("available_intake") or []
        intake_ids = [
            str(item.get("id"))
            for item in available_intake
            if isinstance(item, dict) and item.get("id")
        ]
        available = observation.get("available_evidence") or []
        evidence_ids = [
            str(item.get("id"))
            for item in available
            if isinstance(item, dict) and item.get("id")
        ]
        target = observation.get("tool_name")
        if intake_ids and target == "analyze_candidate":
            return {
                "action_type": "tool",
                "tool_name": "analyze_candidate",
                "arguments": {"intake_ids": intake_ids},
                "reason": "retry with explicit intake ids",
            }
        if intake_ids and target == "understand_user_input":
            return {
                "action_type": "tool",
                "tool_name": "understand_user_input",
                "arguments": {"intake_ids": intake_ids},
                "reason": "retry with explicit intake ids",
            }
        if evidence_ids and target == "analyze_candidate":
            return {
                "action_type": "tool",
                "tool_name": "analyze_candidate",
                "arguments": {"evidence_ids": evidence_ids},
                "reason": "retry with explicit evidence ids",
            }
        if evidence_ids and target == "understand_user_input":
            return {
                "action_type": "tool",
                "tool_name": "understand_user_input",
                "arguments": {"evidence_ids": evidence_ids},
                "reason": "retry with explicit evidence ids",
            }
        return {
            "action_type": "ask_user",
            "intent": "clarification",
            "question": "需要你指定要处理的材料。",
            "reason": "evidence pointer missing",
        }

    if memory.get("understanding_status") != "ok" and last_tool != "understand_user_input":
        return understand_action()

    if kind in {"task_clarification", "reference_unresolved"}:
        return {
            "action_type": "ask_user",
            "intent": "clarification",
            "question": observation.get("message") or "请再说明一下。",
            "error_code": observation.get("error_code"),
            "reason": "need user clarification",
        }

    def _intent() -> dict:
        raw = observation.get("task_intent") or task.get("task_intent") or understanding.get("task_intent")
        return raw if isinstance(raw, dict) else {}

    def _task_id() -> str | None:
        if _intent().get("task_id"):
            return _intent().get("task_id")
        if task.get("job_search_task_id"):
            return task.get("job_search_task_id")
        active = observation.get("active_tasks") or task.get("active_tasks") or []
        if isinstance(active, list) and len(active) == 1 and isinstance(active[0], dict):
            return active[0].get("task_id")
        return None

    def _task_kind() -> str | None:
        return (
            observation.get("task_kind")
            or understanding.get("task_kind")
            or _intent().get("control")
        )

    def bind_action(*, create: bool = False) -> dict:
        arguments: dict = {}
        task_id = _task_id()
        if task_id:
            arguments["task_id"] = task_id
        if create:
            arguments["create"] = True
        return {
            "action_type": "tool",
            "tool_name": "bind_task",
            "arguments": arguments,
            "reason": "bind job search task",
        }

    def skip_action() -> dict:
        arguments: dict = {}
        if _task_id():
            arguments["task_id"] = _task_id()
        return {
            "action_type": "tool",
            "tool_name": "skip_job",
            "arguments": arguments,
            "reason": "user skip",
        }

    def stop_action() -> dict:
        arguments: dict = {}
        if _task_id():
            arguments["task_id"] = _task_id()
        return {
            "action_type": "tool",
            "tool_name": "stop_task",
            "arguments": arguments,
            "reason": "user stop",
        }

    def _waiting_key() -> str | None:
        if task.get("waiting_user_job_key"):
            return task.get("waiting_user_job_key")
        if task.get("pending_job_key"):
            return task.get("pending_job_key")
        if observation.get("job_key"):
            return observation.get("job_key")
        active = task.get("active_tasks") or []
        if isinstance(active, list) and len(active) == 1 and isinstance(active[0], dict):
            return active[0].get("current_job_context_id")
        return None

    def apply_action() -> dict:
        waiting = _waiting_key()
        target = _named_job(waiting, task.get("pending_job_key"), task.get("waiting_user_job_key"))
        arguments = {
            "user_authorization": "apply_job",
            "job_context_id": target.get("job_key") or waiting,
            "task_id": task.get("job_search_task_id") or _task_id(),
        }
        arguments.update(_identity_args(target))
        if waiting and "job_key" not in arguments:
            arguments["job_key"] = waiting
        return {
            "action_type": "tool",
            "tool_name": "execute_action",
            "arguments": arguments,
            "reason": "user authorized apply",
        }

    def follow_up_action() -> dict:
        arguments: dict = {}
        if _task_id():
            arguments["task_id"] = _task_id()
        ref = observation.get("reference_resolution") if isinstance(observation.get("reference_resolution"), dict) else {}
        if ref.get("job_key"):
            arguments["job_key"] = ref.get("job_key")
        return {
            "action_type": "tool",
            "tool_name": "hydrate_job_reference",
            "arguments": arguments,
            "reason": "hydrate referenced job",
        }

    def understood_task_action() -> dict | None:
        task_kind = _task_kind()
        if task_kind == "skip_job":
            if not task.get("job_search_task_id") and _task_id():
                return bind_action()
            return skip_action()
        if task_kind == "apply_job":
            if not task.get("job_search_task_id") and _task_id():
                return bind_action()
            return apply_action()
        if task_kind == "stop_task":
            if not task.get("job_search_task_id") and _task_id():
                return bind_action()
            return stop_action()
        if task_kind == "continue_task":
            active = observation.get("active_tasks") or task.get("active_tasks") or []
            if isinstance(active, list) and len(active) > 1:
                return {
                    "action_type": "ask_user",
                    "intent": "clarification",
                    "question": "目前有不止一个进行中的求职任务，请说明你指的是哪一次。",
                    "error_code": "clarification_needed",
                    "reason": "test-double exploration: multiple active tasks, ask which one",
                }
            if task.get("waiting_user_job_key"):
                return skip_action()
            if not task.get("job_search_task_id") and _task_id():
                return bind_action()
            return continue_exploring()
        if task_kind in {"follow_up_job", "job_reference"}:
            ref = observation.get("reference_resolution") if isinstance(observation.get("reference_resolution"), dict) else {}
            if ref.get("status") == "resolved":
                return follow_up_action()
        if not task.get("job_search_task_id") and (
            has_goal() or task_kind in {"new_job_search", "update_goal"}
        ):
            return bind_action(create=True)
        return None

    if last_tool in {"execute_action", "execute_job_action"}:
        return {"action_type": "finish", "reason": "apply finished"}

    if kind in {"action_rejected", "thrash_re_reason"}:
        recovery = observation.get("recovery") if isinstance(observation.get("recovery"), dict) else {}
        hints = ctx.get("program_hints") if isinstance(ctx.get("program_hints"), dict) else {}
        if isinstance(hints.get("recovery"), dict):
            recovery = hints.get("recovery") or recovery
        # thrash_re_reason already carries structural facts on the Observation itself.
        if kind == "thrash_re_reason":
            recovery = {
                **recovery,
                "unexplored_listed_count": observation.get("unexplored_listed_count")
                or recovery.get("unexplored_listed_count")
                or 0,
                "unexplored_listed_job_keys": observation.get("unexplored_listed_job_keys")
                or recovery.get("unexplored_listed_job_keys")
                or [],
                "remaining_opens": observation.get("remaining_opens")
                if observation.get("remaining_opens") is not None
                else recovery.get("remaining_opens"),
                "raw_candidate_intake_present": observation.get("raw_candidate_intake_present"),
                "candidate_material_present": observation.get("candidate_material_present"),
                "available_intake_ids": observation.get("available_intake_ids") or [],
            }
        if int(recovery.get("remaining_opens") or 0) <= 0:
            if (
                recovery.get("raw_candidate_intake_present")
                or recovery.get("has_candidate_supplement")
            ) and not recovery.get("candidate_material_present"):
                return analyze_candidate_action()
            return continue_exploring() or {
                "action_type": "finish",
                "reason": str(observation.get("error") or observation.get("reason") or "action rejected"),
            }
        if int(recovery.get("unexplored_listed_count") or 0) > 0 and int(
            recovery.get("remaining_opens") or 0
        ) > 0:
            nxt = open_listed()
            if nxt:
                return nxt
        if (
            recovery.get("raw_candidate_intake_present")
            or recovery.get("has_candidate_supplement")
        ) and not recovery.get("candidate_material_present"):
            return analyze_candidate_action()
        return continue_exploring() or {
            "action_type": "finish",
            "reason": str(observation.get("error") or observation.get("reason") or "action rejected"),
        }

    if kind in {"job_skipped", "task_stopped"}:
        if kind == "task_stopped":
            return {"action_type": "finish", "reason": "user stopped"}
        nxt = open_listed() or hydrate_history_job()
        return nxt or {"action_type": "finish", "reason": "no listed job after skip"}

    if kind == "task_control":
        if control == "apply_job" or observation.get("control") == "apply_job":
            target = _named_job(
                task.get("pending_job_key"),
                task.get("waiting_user_job_key"),
                observation.get("job_key"),
            )
            arguments = {
                "user_authorization": "apply_job",
                "job_context_id": target.get("job_key") or task.get("pending_job_key"),
                "task_id": task.get("job_search_task_id"),
            }
            arguments.update(_identity_args(target))
            if task.get("pending_job_key") and "job_key" not in arguments:
                arguments["job_key"] = task.get("pending_job_key")
            return {
                "action_type": "tool",
                "tool_name": "execute_action",
                "arguments": arguments,
                "reason": "user authorized apply",
            }
        if control == "stop_task" or observation.get("control") == "stop_task":
            return {"action_type": "finish", "reason": "user stopped"}
        nxt = open_listed()
        return nxt or {"action_type": "finish", "reason": "no listed job after task control"}

    if kind == "follow_up_resolved":
        target = _named_job(observation.get("job_key"), task.get("follow_up_of"))
        if not target:
            target = _job_needing(has_job_profile=True)
        if target.get("job_profile") or target.get("has_job_profile"):
            arguments = _identity_args(target)
            return {
                "action_type": "tool",
                "tool_name": "match_job",
                "arguments": arguments,
                "reason": "follow-up match",
            }
        return open_listed() or {"action_type": "finish", "reason": "follow-up has no job"}

    if kind == "task_bound":
        task_kind = observation.get("task_kind") or understanding.get("task_kind")
        if task_kind == "apply_job":
            return apply_action()
        if task_kind == "skip_job":
            return skip_action()
        if task_kind == "stop_task":
            return stop_action()
        if task_kind == "continue_task":
            return continue_exploring()
        if task_kind in {"follow_up_job", "job_reference"}:
            return follow_up_action()
        persist = bool(understanding.get("persist_requested"))
        if persist or _should_ingest_candidate():
            return analyze_candidate_action()
        if has_goal():
            return search_action()
        return {"action_type": "finish", "error_code": "GOAL_INCOMPLETE", "reason": "need a job-search goal"}

    if kind in {"user_turn_understood", "user_turn"}:
        handled = understood_task_action()
        if handled is not None:
            return handled
        if _should_ingest_candidate():
            return analyze_candidate_action()
        if has_goal():
            return search_action()
        return {"action_type": "finish", "error_code": "GOAL_INCOMPLETE", "reason": "need a job-search goal"}

    if last_tool == "analyze_candidate":
        if has_goal():
            return search_action()
        return {"action_type": "finish", "error_code": "GOAL_INCOMPLETE", "reason": "need a job-search goal"}

    if last_tool in {"search_jobs", "mock_search_jobs", "search_boss_jobs", "search_liepin_jobs", "search_51job_jobs"}:
        return continue_exploring()

    if last_tool in {"open_job", "inspect_job", "mock_open_job", "open_boss_job", "open_liepin_job", "open_51job_job"}:
        if not has_match_evidence():
            return continue_exploring()
        target = _named_job() or _job_needing(stage="opened", has_interpret=False)
        arguments = _identity_args(target)
        return {
            "action_type": "tool",
            "tool_name": "interpret_job_actions",
            "arguments": arguments,
            "reason": "check page actions",
        }

    if last_tool == "interpret_job_actions":
        target = _named_job() or _job_needing(has_interpret=True, has_job_profile=False)
        arguments = _identity_args(target)
        return {
            "action_type": "tool",
            "tool_name": "analyze_job",
            "arguments": arguments,
            "reason": "analyze opened JD",
        }

    if last_tool == "analyze_job":
        if not has_match_evidence():
            return continue_exploring()
        target = _named_job() or _job_needing(has_job_profile=True, has_match=False)
        arguments = _identity_args(target)
        return {
            "action_type": "tool",
            "tool_name": "match_job",
            "arguments": arguments,
            "reason": "match a named job",
        }

    if last_tool == "match_job":
        if task.get("follow_up_of"):
            return {"action_type": "finish", "reason": "follow-up answered"}
        if after_match == "finish":
            return {"action_type": "finish", "reason": "test-double finish after match"}
        if after_match == "continue":
            return continue_exploring()
        target = _named_job() or _job_needing(has_match=True)
        match = target.get("match_result") or result
        usable = (
            after_match == "ask_user"
            or (
                after_match is None
                and isinstance(match, dict)
                and match.get("analysis_status") == "ok"
                and match.get("hard_requirements_met") is not False
            )
        )
        if usable:
            listing = _job_blob(target)
            title = target.get("job_title") or listing.get("job_title") or "这个职位"
            job_key = target.get("job_key")
            if not job_key:
                return continue_exploring()
            return {
                "action_type": "ask_user",
                "intent": "surface",
                "job_key": job_key,
                "question": f"{title} 值得你决定要不要投递。",
                "arguments": {"job_key": job_key, "intent": "surface"},
                "reason": "test-double exploration: surface after a usable match",
            }
        return continue_exploring()

    if last_tool == "plan_search":
        if result.get("analysis_status") and result.get("analysis_status") != "ok":
            return {"action_type": "finish", "reason": history.get("recorded_stop_reason") or "plan_search_failed"}
        keyword = result.get("keyword")
        if not keyword:
            return {"action_type": "finish", "reason": history.get("recorded_stop_reason") or "plan_search_failed"}
        return search_action(str(keyword), result.get("city"))

    if has_goal():
        return search_action()
    return {"action_type": "finish", "reason": "no further action"}


class ScriptedReasonerLLM(AgentRoutingLLM):
    """Returns queued Reasoner decisions. Other prompts use AgentRoutingLLM."""

    def __init__(self, actions: list[dict]):
        super().__init__()
        self.reasoner_actions = list(actions)

    def complete_json(self, *, system: str, user: str) -> dict:
        if "你是全局 Agent Reasoner" in system:
            self.calls.append({"system": system, "user": user})
            if not self.reasoner_actions:
                return {"action_type": "finish", "reason": "reasoner queue empty"}
            item = self.reasoner_actions.pop(0)
            if callable(item):
                from json import loads

                return item(loads(user))
            return item
        return super().complete_json(system=system, user=user)


