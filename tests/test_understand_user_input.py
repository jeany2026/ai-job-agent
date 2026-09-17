"""understand_user_input: one LLM turn over message + attachments. No keyword split."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tests.mock_llm import MockLLMProvider, claim_verification_response, understanding_response
from tools.registry import build_registry
from understanding.schema import has_candidate_supplement, has_user_goal, validate_llm_payload
from understanding.understand_user_input import understand_user_input
from understanding.verify_candidate_claims import VERIFY_CANDIDATE_CLAIMS_MARKER

SOURCE_PATH = ROOT_DIR / "understanding" / "understand_user_input.py"


def _cap(name: str, quote: str, kind: str = "user_statement") -> dict:
    return {"name": name, "quote": quote, "source_kind": kind}


def _run(message, response=None, *, attachments=None, candidate_profile=None, session_context=None, provider=None):
    llm = provider or MockLLMProvider(response if response is not None else understanding_response())
    result = understand_user_input(
        message,
        attachments,
        candidate_profile=candidate_profile,
        session_context=session_context,
        llm_provider=llm,
    )
    return result, llm


def test_case1_pure_job_goal():
    message = "帮我找深圳高级产品经理，期望薪资35K左右。"
    result, llm = _run(
        message,
        understanding_response(
            user_goal={
                "target_roles": ["高级产品经理"],
                "cities": ["深圳"],
                "salary_min": 35000,
            }
        ),
    )
    assert result["analysis_status"] == "ok"
    assert result["user_goal"]["target_roles"] == ["高级产品经理"]
    assert result["user_goal"]["cities"] == ["深圳"]
    assert result["user_goal"]["salary_min"] == 35000
    assert has_user_goal(result)
    assert not has_candidate_supplement(result)
    assert result["persist_requested"] is False
    assert len(llm.calls) == 1
    assert "UserInputUnderstanding" in llm.calls[0]["system"]
    assert message in llm.calls[0]["user"]


def test_case2_pure_candidate_info_is_valid_without_job_goal():
    message = "我做了十几年金融产品，做过支付、清结算、对账和CRM。"
    result, llm = _run(
        message,
        understanding_response(
            candidate_supplement={
                "statements": ["十几年金融产品经验"],
                "claimed_capabilities": [
                    _cap("支付", "支付"),
                    _cap("清结算", "清结算"),
                    _cap("对账", "对账"),
                    _cap("CRM", "CRM"),
                ],
            }
        ),
    )
    assert result["analysis_status"] == "ok"
    assert result["user_goal"]["target_roles"] == []
    assert has_candidate_supplement(result)
    names = [item["name"] for item in result["candidate_supplement"]["claimed_capabilities"]]
    assert names == ["支付", "清结算", "对账", "CRM"]
    assert "支付" not in result["user_goal"]["focus_areas"]
    assert result["persist_requested"] is False
    assert llm.calls


def test_case3_goal_plus_candidate_info():
    message = "我做了十几年金融产品，做过支付、清结算和CRM。现在想找深圳高级产品经理。"
    result, _ = _run(
        message,
        understanding_response(
            user_goal={"target_roles": ["高级产品经理"], "cities": ["深圳"]},
            candidate_supplement={
                "claimed_capabilities": [
                    _cap("金融产品", "金融产品"),
                    _cap("支付", "支付"),
                    _cap("清结算", "清结算"),
                    _cap("CRM", "CRM"),
                ]
            },
        ),
    )
    assert result["analysis_status"] == "ok"
    assert result["user_goal"]["target_roles"] == ["高级产品经理"]
    assert result["user_goal"]["cities"] == ["深圳"]
    names = [item["name"] for item in result["candidate_supplement"]["claimed_capabilities"]]
    assert "支付" in names and "清结算" in names and "CRM" in names
    assert result["user_goal"]["focus_areas"] == []


def test_case4_candidate_plus_preferences():
    message = "我做过支付和清结算。这次优先考虑金融科技，不希望长期高强度加班。"
    result, _ = _run(
        message,
        understanding_response(
            candidate_supplement={
                "claimed_capabilities": [_cap("支付", "支付"), _cap("清结算", "清结算")]
            },
            preferences=["优先金融科技", "不希望长期高强度加班"],
        ),
    )
    assert result["analysis_status"] == "ok"
    assert has_candidate_supplement(result)
    assert "优先金融科技" in result["preferences"]
    assert "不希望长期高强度加班" in result["preferences"]
    assert "支付" not in result["user_goal"]["focus_areas"]
    assert "清结算" not in result["preferences"]


def test_case5_message_and_attachment_are_one_semantic_context():
    message = "这是我最近做的项目，也请考虑进去。"
    attachment_text = "AI Job Agent 是一个求职 Agent 项目，覆盖岗位搜索、JD 理解与匹配。"
    result, llm = _run(
        message,
        understanding_response(
            candidate_supplement={
                "statements": ["最近完成的项目请纳入本次匹配"],
                "claimed_projects": [
                    {
                        "name": "AI Job Agent",
                        "description": "求职 Agent 项目",
                        "quote": "AI Job Agent 是一个求职 Agent 项目",
                        "source_kind": "project_document",
                    }
                ],
            }
        ),
        attachments=[
            {
                "filename": "AI Job Agent 项目说明.pdf",
                "kind": "project_document",
                "text": attachment_text,
            }
        ],
    )
    assert result["analysis_status"] == "ok"
    user_prompt = llm.calls[0]["user"]
    assert message in user_prompt
    assert attachment_text in user_prompt
    assert "AI Job Agent 项目说明.pdf" in user_prompt
    assert "同一次" in llm.calls[0]["system"] or "一并理解" in user_prompt
    assert len(llm.calls) == 2
    assert VERIFY_CANDIDATE_CLAIMS_MARKER in llm.calls[1]["system"]
    assert attachment_text in llm.calls[1]["user"]
    project = result["candidate_supplement"]["claimed_projects"][0]
    assert project["name"] == "AI Job Agent"
    assert project["source_kind"] == "project_document"
    assert result["user_goal"]["target_roles"] == []
    assert "AI Job Agent" not in result["user_goal"]["focus_areas"]


def test_case6_mixed_goal_supplement_and_constraints():
    message = (
        "我做了十几年金融产品，做过支付、清结算，最近自己做了一个AI Job Agent。"
        "这次帮我找深圳高级产品经理，35K左右，不考虑之前已经排除的公司。"
    )
    result, _ = _run(
        message,
        understanding_response(
            user_goal={
                "target_roles": ["高级产品经理"],
                "cities": ["深圳"],
                "salary_min": 35000,
            },
            candidate_supplement={
                "claimed_capabilities": [_cap("支付", "支付"), _cap("清结算", "清结算")],
                "claimed_projects": [
                    {
                        "name": "AI Job Agent",
                        "quote": "AI Job Agent",
                        "source_kind": "user_statement",
                    }
                ],
            },
            constraints={"notes": ["不考虑之前已经排除的公司"]},
        ),
    )
    assert result["analysis_status"] == "ok"
    assert result["user_goal"]["target_roles"] == ["高级产品经理"]
    assert result["user_goal"]["salary_min"] == 35000
    names = [item["name"] for item in result["candidate_supplement"]["claimed_capabilities"]]
    assert "支付" in names
    assert result["candidate_supplement"]["claimed_projects"][0]["name"] == "AI Job Agent"
    assert "不考虑之前已经排除的公司" in result["constraints"]["notes"]
    assert "支付" not in result["user_goal"]["target_roles"]


def test_case7_transferable_matching_context_not_keyword_reject():
    message = (
        "我没有医疗行业经验，但做过支付、清结算、对账和复杂资金链路，"
        "希望你不要因为没有医疗行业经验直接排除。"
    )
    result, _ = _run(
        message,
        understanding_response(
            candidate_supplement={
                "claimed_capabilities": [
                    _cap("支付", "支付"),
                    _cap("清结算", "清结算"),
                    _cap("对账", "对账"),
                    _cap("复杂资金链路", "复杂资金链路"),
                ],
                "acknowledged_gaps": [{"gap": "没有医疗行业经验", "quote": "没有医疗行业经验"}],
                "transfer_claims": [
                    {
                        "claim": "支付与清结算能力可迁移",
                        "from_domain": "支付清结算",
                        "to_domain": "医疗",
                        "quote": "不要因为没有医疗行业经验直接排除",
                    }
                ],
            },
            matching_context="不要因为没有医疗行业经验直接排除，重点判断支付、清结算等可迁移能力。",
        ),
    )
    assert result["analysis_status"] == "ok"
    assert result["matching_context"]
    assert "不要因为没有医疗行业经验直接排除" in result["matching_context"]
    gaps = [item["gap"] for item in result["candidate_supplement"]["acknowledged_gaps"]]
    assert any("医疗" in item for item in gaps)
    assert result["user_goal"]["target_roles"] == []
    assert "医疗" not in result["user_goal"]["focus_areas"]


def test_case8_persist_requested_does_not_write_profile():
    message = "我最近新增了一个AI Agent项目，以后找工作都请把这个项目考虑进去。"
    result, llm = _run(
        message,
        understanding_response(
            candidate_supplement={
                "claimed_projects": [
                    {
                        "name": "AI Agent项目",
                        "quote": "AI Agent项目",
                        "source_kind": "user_statement",
                    }
                ],
            },
            persist_requested=True,
        ),
    )
    assert result["analysis_status"] == "ok"
    assert result["persist_requested"] is True
    assert result["candidate_supplement"]["persist_requested"] is True
    source = SOURCE_PATH.read_text(encoding="utf-8")
    assert "create_candidate_profile" not in source
    assert "update_candidate_profile" not in source
    assert "CandidateProfileStore" not in source
    assert llm.calls


def test_existing_profile_is_context_only():
    message = "这次重点看看医疗支付。"
    result, llm = _run(
        message,
        understanding_response(
            user_goal={"focus_areas": ["医疗支付"]},
        ),
        candidate_profile={
            "summary": "16年金融科技产品经验",
            "years_experience": "16年",
            "business_capabilities": [{"name": "支付"}],
        },
    )
    assert result["analysis_status"] == "ok"
    user_prompt = llm.calls[0]["user"]
    assert "16年金融科技产品经验" in user_prompt
    assert "不要把它写成本次 supplement" in user_prompt or "不要更新" in user_prompt


def test_empty_input_insufficient():
    result, llm = _run("  ", understanding_response())
    assert result["analysis_status"] == "insufficient_data"
    assert llm.calls == []


def test_llm_unavailable():
    import importlib

    mod = importlib.import_module("understanding.understand_user_input")
    original = mod.get_llm_provider
    mod.get_llm_provider = lambda: None
    try:
        result = understand_user_input("找深圳产品经理", llm_provider=None)
    finally:
        mod.get_llm_provider = original
    assert result["analysis_status"] == "llm_unavailable"


def test_illegal_json_is_llm_error():
    llm = MockLLMProvider(error=RuntimeError("LLM response is not valid JSON"))
    result = understand_user_input("找深圳产品经理", llm_provider=llm)
    assert result["analysis_status"] == "llm_error"


def test_paraphrased_quote_does_not_fail_understanding():
    message = "我做过支付清算和复杂对账。"
    result, llm = _run(
        message,
        understanding_response(
            user_goal={"target_roles": ["产品经理"], "cities": ["深圳"]},
            candidate_supplement={
                "claimed_capabilities": [_cap("支付清算", "支付与清结算相关工作")]
            },
        ),
    )
    assert result["analysis_status"] == "ok"
    assert result["user_goal"]["target_roles"] == ["产品经理"]
    assert result["user_goal"]["cities"] == ["深圳"]
    assert result["candidate_supplement"]["claimed_capabilities"][0]["evidence_status"] == "unverified"
    assert result["evidence_validation_status"] == "ok"
    assert result["verifications"] == []
    assert result["support_check"][0]["kind"] == "support_check"
    assert result["support_check"][0]["consistency_hint"] == "consistent"
    assert "quote not found" not in (result.get("error") or "")
    assert len(llm.calls) == 2


def test_unsupported_claim_does_not_drop_user_goal():
    result, llm = _run(
        "我需要在深圳找产品经理。附件是我的简历。",
        provider=MockLLMProvider(
            understanding_response(
                user_goal={"target_roles": ["产品经理"], "cities": ["深圳"]},
                candidate_supplement={
                    "claimed_projects": [
                        {
                            "name": "从未出现过的航天项目",
                            "description": "主导载人航天控制系统",
                            "quote": "这段原文根本没出现过",
                            "source_kind": "resume",
                        }
                    ]
                },
            ),
            verify_response=claim_verification_response(
                "claimed_projects[0]",
                status="unsupported",
                rationale="证据来源未支持该项目",
            ),
        ),
    )
    assert result["analysis_status"] == "ok"
    assert result["user_goal"]["target_roles"] == ["产品经理"]
    assert result["user_goal"]["cities"] == ["深圳"]
    project = result["candidate_supplement"]["claimed_projects"][0]
    assert project["name"] == "从未出现过的航天项目"
    assert project["evidence_status"] == "unverified"
    assert result["evidence_validation_status"] == "ok"
    assert result["verifications"] == []
    assert result["support_check"][0]["consistency_hint"] == "inconsistent"
    assert VERIFY_CANDIDATE_CLAIMS_MARKER in llm.calls[1]["system"]


def test_verify_llm_error_marks_unverified_and_keeps_goal():
    result, _ = _run(
        "帮我找深圳产品经理，我做过支付。",
        provider=MockLLMProvider(
            understanding_response(
                user_goal={"target_roles": ["产品经理"], "cities": ["深圳"]},
                candidate_supplement={"claimed_capabilities": [_cap("支付", "支付")]},
            ),
            verify_error=RuntimeError("LLM response is not valid JSON"),
        ),
    )
    assert result["analysis_status"] == "ok"
    assert result["user_goal"]["target_roles"] == ["产品经理"]
    assert result["evidence_validation_status"] == "llm_error"
    assert result["candidate_supplement"]["claimed_capabilities"][0]["evidence_status"] == "unverified"


def test_no_claims_skips_evidence_verification():
    result, llm = _run(
        "帮我找深圳高级产品经理。",
        understanding_response(user_goal={"target_roles": ["高级产品经理"], "cities": ["深圳"]}),
    )
    assert result["analysis_status"] == "ok"
    assert result["evidence_validation_status"] == "skipped"
    assert len(llm.calls) == 1
    assert "UserInputUnderstanding" in llm.calls[0]["system"]


def test_extractor_cannot_self_attest_evidence_status():
    fields = validate_llm_payload(
        understanding_response(
            candidate_supplement={
                "claimed_capabilities": [
                    {
                        "name": "支付",
                        "quote": "支付",
                        "source_kind": "user_statement",
                        "evidence_status": "supported",
                    }
                ]
            }
        )
    )
    assert "evidence_status" not in fields["candidate_supplement"]["claimed_capabilities"][0]


def test_source_has_no_quote_substring_gate():
    source = SOURCE_PATH.read_text(encoding="utf-8")
    assert "_assert_quotes_in_turn" not in source
    assert "quote not found" not in source
    assert "verify_candidate_claims" in source


def test_structured_understanding_skips_llm():
    llm = MockLLMProvider(understanding_response())
    result = understand_user_input(
        {
            "raw_text": "already structured",
            "user_goal": {
                "target_roles": ["高级产品经理"],
                "cities": ["深圳"],
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
        },
        llm_provider=llm,
    )
    assert result["analysis_status"] == "ok"
    assert result["user_goal"]["target_roles"] == ["高级产品经理"]
    assert llm.calls == []


def test_registry_dispatches_understand_user_input():
    registry = build_registry()
    result = registry.invoke(
        "understand_user_input",
        {"message": "帮我找深圳高级产品经理"},
        llm_provider=MockLLMProvider(
            understanding_response(user_goal={"target_roles": ["高级产品经理"], "cities": ["深圳"]})
        ),
    )
    assert result["analysis_status"] == "ok"
    assert result["user_goal"]["target_roles"] == ["高级产品经理"]
    assert registry.invocations == ["understand_user_input"]


def test_session_context_is_prompt_only_not_quote_source():
    result, llm = _run(
        "再重点看看医疗支付。",
        understanding_response(user_goal={"focus_areas": ["医疗支付"]}),
        session_context={
            "previous_goal": {
                "target_roles": ["高级产品经理"],
                "cities": ["深圳"],
                "focus_areas": ["金融科技"],
                "salary_min": None,
                "exclude_companies": [],
                "platforms": [],
            },
            "last_recommended": [
                {
                    "company_name": "支付科技有限公司",
                    "job_title": "高级产品经理",
                    "platform": "mock",
                }
            ],
        },
    )
    assert result["analysis_status"] == "ok"
    prompt = llm.calls[0]["user"]
    assert "再重点看看医疗支付。" in prompt
    assert "高级产品经理" in prompt
    assert "支付科技有限公司" in prompt
    assert "禁止从这里摘 quote" in prompt
    assert result["user_goal"]["focus_areas"] == ["医疗支付"]
    assert result["user_goal"]["target_roles"] == ["高级产品经理"]
    assert result["user_goal"]["cities"] == ["深圳"]


def test_session_context_does_not_fill_when_this_turn_has_roles():
    result, _ = _run(
        "改成找北京产品总监。",
        understanding_response(user_goal={"target_roles": ["产品总监"], "cities": ["北京"]}),
        session_context={"previous_goal": {"target_roles": ["高级产品经理"], "cities": ["深圳"]}},
    )
    assert result["user_goal"]["target_roles"] == ["产品总监"]
    assert result["user_goal"]["cities"] == ["北京"]
