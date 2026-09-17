"""One-off E2E architecture behavior tracer. Not a product pipeline."""

from __future__ import annotations

import json
import sys
import traceback
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import agent.bind_arguments as bind_mod
import agent.decide as decide_mod
import agent.loop as loop_mod
from agent.human_gate_continuity import (
    OBS_HUMAN_GATE_RESOLVED,
    snapshot_world_for_human_gate,
)
from agent.orchestrator import run_agent
from agent.state import JobRecord, new_agent_state
from llm import get_llm_provider
from tools.registry import build_registry

MESSAGE = (
    "我需要的工作是在深圳，最好福田区和南山区，职位产品经理/产品主管/需求工程师，附件简历。"
)
RESUME_TEXT = (
    "张三｜产品经理｜5年经验｜深圳\n"
    "负责过支付中台、清结算与对账产品；熟悉需求拆解、PRD、跨团队协作。\n"
    "期望：深圳福田/南山 产品经理或产品主管。\n"
)

TRACE: list[dict] = []
_orig_decide = decide_mod.decide
_orig_bind = bind_mod.bind_tool_arguments
_orig_act = loop_mod._act
_orig_reduce = loop_mod.reduce
_round = {"n": 0}


def _safe(obj, limit=1200):
    try:
        text = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        text = str(obj)
    if len(text) > limit:
        return text[:limit] + "…"
    return text


def _intake_view(state):
    items = []
    for item in state.intake or []:
        if not isinstance(item, dict):
            continue
        items.append(
            {
                "id": item.get("id"),
                "carrier": item.get("carrier"),
                "filename": item.get("filename"),
                "char_count": item.get("char_count") or len(str(item.get("text") or "")),
                "has_kind_resume": item.get("kind") == "resume",
                "content_type": item.get("content_type"),
                "client_hint": item.get("client_hint"),
            }
        )
    return items


def _job_stages(state):
    return {
        key: {
            "stage": rec.stage,
            "has_profile": isinstance(rec.job_profile, dict),
            "has_match": isinstance(rec.match_result, dict),
            "has_opened": isinstance(rec.opened, dict),
        }
        for key, rec in (state.jobs or {}).items()
    }


def traced_decide(state, registry=None, llm_provider=None):
    _round["n"] += 1
    n = _round["n"]
    obs_before = deepcopy(state.last_raw_observation) if isinstance(state.last_raw_observation, dict) else state.last_raw_observation
    action = _orig_decide(state, registry=registry, llm_provider=llm_provider)
    entry = {
        "round": n,
        "phase": "reasoner_decision",
        "producer": "Reasoner(decide→reason_next_action→LLM)",
        "observation_before": {
            "kind": (obs_before or {}).get("kind") if isinstance(obs_before, dict) else None,
            "keys": sorted((obs_before or {}).keys()) if isinstance(obs_before, dict) else [],
            "has_next_action": isinstance(obs_before, dict) and "next_action" in obs_before,
            "has_coach_zh": isinstance(obs_before, dict) and "coach_zh" in obs_before,
            "snippet": _safe(obs_before, 800),
        },
        "action": {
            "action_type": action.action_type,
            "tool_name": action.tool_name,
            "arguments": dict(action.arguments or {}),
            "intent": action.intent,
            "job_key": action.job_key,
            "question": action.question,
            "reason": action.reason,
            "error_code": action.error_code,
        },
        "intake_at_decision": _intake_view(state),
        "job_stages_at_decision": _job_stages(state),
        "search_session_exposed": list(
            getattr(getattr(state.search, "session", None), "exposed_job_keys", None) or []
        ),
    }
    TRACE.append(entry)
    print(f"\n=== ROUND {n} REASONER ===")
    print(_safe(entry["action"]))
    return action


def traced_bind(state, action):
    result = _orig_bind(state, action)
    TRACE.append(
        {
            "round": _round["n"],
            "phase": "binding",
            "producer": "Binding(bind_tool_arguments)",
            "ok": result.get("ok"),
            "observation_kind": (result.get("observation") or {}).get("kind")
            if isinstance(result.get("observation"), dict)
            else None,
            "argument_keys": sorted((result.get("arguments") or {}).keys())
            if isinstance(result.get("arguments"), dict)
            else [],
            "prescribed_next_tool": None,
            "snippet": _safe(
                {
                    "ok": result.get("ok"),
                    "observation": result.get("observation"),
                    "arg_keys": sorted((result.get("arguments") or {}).keys())
                    if isinstance(result.get("arguments"), dict)
                    else [],
                },
                1000,
            ),
        }
    )
    print(f"--- ROUND {_round['n']} BINDING ok={result.get('ok')} ---")
    return result


def traced_act(action, registry, llm_provider=None, arguments=None, search_session=None):
    before_inv = list(getattr(registry, "invocations", []) or [])
    result = _orig_act(
        action,
        registry,
        llm_provider=llm_provider,
        arguments=arguments,
        search_session=search_session,
    )
    after_inv = list(getattr(registry, "invocations", []) or [])
    new_inv = after_inv[len(before_inv) :]
    TRACE.append(
        {
            "round": _round["n"],
            "phase": "tool",
            "producer": "Tool(_act/registry)",
            "tool_name": action.tool_name,
            "registry_invocations_delta": new_inv,
            "result_kind_hint": (result.get("kind") if isinstance(result, dict) else type(result).__name__),
            "search_mode": (arguments or {}).get("mode") if isinstance(arguments, dict) else None,
            "result_snippet": _safe(result, 900),
        }
    )
    print(f"--- ROUND {_round['n']} TOOL {action.tool_name} inv={new_inv} ---")
    return result


def traced_reduce(state, action, result, ctx=None):
    before_stages = _job_stages(state)
    before_exposed = list(getattr(getattr(state.search, "session", None), "exposed_job_keys", None) or [])
    out = _orig_reduce(state, action, result, ctx)
    after_obs = out.last_raw_observation if isinstance(out.last_raw_observation, dict) else {}
    TRACE.append(
        {
            "round": _round["n"],
            "phase": "reduce_world",
            "producer": "Program(reduce)",
            "stages_before": before_stages,
            "stages_after": _job_stages(out),
            "exposed_before": before_exposed,
            "exposed_after": list(
                getattr(getattr(out.search, "session", None), "exposed_job_keys", None) or []
            ),
            "observation_kind": after_obs.get("kind"),
            "observation_has_next_action": "next_action" in after_obs,
            "observation_has_coach_zh": "coach_zh" in after_obs,
            "session_status": out.session.status,
            "observation_snippet": _safe(after_obs, 900),
        }
    )
    print(
        f"--- ROUND {_round['n']} REDUCE status={out.session.status} obs={after_obs.get('kind')} ---"
    )
    return out


def install_hooks():
    decide_mod.decide = traced_decide
    loop_mod.decide = traced_decide
    bind_mod.bind_tool_arguments = traced_bind
    loop_mod.bind_tool_arguments = traced_bind
    loop_mod._act = traced_act
    loop_mod.reduce = traced_reduce


def uninstall_hooks():
    decide_mod.decide = _orig_decide
    loop_mod.decide = _orig_decide
    bind_mod.bind_tool_arguments = _orig_bind
    loop_mod.bind_tool_arguments = _orig_bind
    loop_mod._act = _orig_act
    loop_mod.reduce = _orig_reduce


def analyze_trace(trace: list[dict]) -> dict:
    decisions = [t for t in trace if t.get("phase") == "reasoner_decision"]
    tools = [t for t in trace if t.get("phase") == "tool"]
    binds = [t for t in trace if t.get("phase") == "binding"]
    reduces = [t for t in trace if t.get("phase") == "reduce_world"]

    tool_order = [t.get("tool_name") for t in tools]
    decision_order = [
        (d["action"].get("action_type"), d["action"].get("tool_name")) for d in decisions
    ]

    # Auto-trigger detection: after a tool reduce, next decision must come from Reasoner
    # (already true by loop). Detect if any reduce/tool invoked extra business tools.
    extra_tool_invocations = []
    for t in tools:
        delta = t.get("registry_invocations_delta") or []
        # Expected: at most the chosen tool (aliases possible)
        if len(delta) > 1:
            extra_tool_invocations.append({"tool": t.get("tool_name"), "delta": delta})

    obs_with_next = [r for r in reduces if r.get("observation_has_next_action")]
    obs_with_coach = [r for r in reduces if r.get("observation_has_coach_zh")]

    # SearchSession: continue modes and exposure growth
    search_rounds = [t for t in tools if t.get("tool_name") in {"search_jobs", "mock_search_jobs"}]
    exposure_events = []
    for r in reduces:
        before = set(r.get("exposed_before") or [])
        after = set(r.get("exposed_after") or [])
        if after != before:
            exposure_events.append(
                {
                    "round": r["round"],
                    "new": sorted(after - before),
                    "removed": sorted(before - after),
                    "total_after": len(after),
                }
            )

    return {
        "decision_order": decision_order,
        "tool_order": tool_order,
        "extra_tool_invocations_in_single_act": extra_tool_invocations,
        "observations_with_next_action": len(obs_with_next),
        "observations_with_coach_zh": len(obs_with_coach),
        "search_rounds": [
            {"round": t["round"], "mode": t.get("search_mode")} for t in search_rounds
        ],
        "exposure_events": exposure_events,
        "bind_failures": [b for b in binds if not b.get("ok")],
    }


def run_main_turn(llm):
    install_hooks()
    try:
        state = run_agent(
            message=MESSAGE,
            attachments=[
                {
                    "filename": "resume.txt",
                    "client_hint": "resume",
                    "text": RESUME_TEXT,
                }
            ],
            data_source="mock",
            llm_provider=llm,
            conversation_id="e2e-arch-trace-1",
            max_steps=24,
            constraints={"max_open_jd": 3, "max_llm_calls": 18, "max_searches": 4},
        )
        return state
    finally:
        uninstall_hooks()


def run_human_gate_continuation(llm, prior_state):
    """Simulate NEEDS_HUMAN snapshot restore + user says logged in."""
    # Build a world as if search hit login gate mid-flight.
    snap_state = deepcopy(prior_state) if prior_state is not None else new_agent_state(
        goal_input=MESSAGE, data_source="mock", conversation_id="e2e-arch-trace-hg"
    )
    if not snap_state.jobs:
        snap_state.jobs["mock:demo"] = JobRecord(
            job_key="mock:demo",
            stage="listed",
            listed_order=0,
            listed={"platform": "mock", "job_id": "demo", "job_title": "产品经理", "city": "深圳"},
        )
    snap_state.human_gate = {
        "reason": "login_required",
        "message": "未检测到登录",
        "source": "search_jobs",
    }
    snap_state.status = "NEEDS_HUMAN"
    snapshot = snapshot_world_for_human_gate(snap_state)

    session_context = {
        "pending_human_gate": dict(snap_state.human_gate),
        "human_gate_world_snapshot": snapshot,
    }

    global TRACE
    hg_trace_start = len(TRACE)
    install_hooks()
    try:
        state = run_agent(
            message="我已经登录了",
            attachments=None,
            data_source="mock",
            llm_provider=llm,
            conversation_id="e2e-arch-trace-hg",
            session_context=session_context,
            max_steps=8,
            constraints={"max_open_jd": 3, "max_llm_calls": 10, "max_searches": 4},
        )
        hg_trace = TRACE[hg_trace_start:]
        return state, hg_trace
    finally:
        uninstall_hooks()


def main():
    out_path = ROOT / "tmp_e2e_arch_trace.json"
    print("Loading LLM provider…")
    llm = get_llm_provider()
    print(f"provider={type(llm).__name__}")

    # Seed intake inspection before loop
    seed = new_agent_state(
        goal_input=MESSAGE,
        attachments=[{"filename": "resume.txt", "client_hint": "resume", "text": RESUME_TEXT}],
        data_source="mock",
    )
    seed_report = {
        "intake": _intake_view(seed),
        "evidence_count": len(seed.evidence or []),
        "any_intake_labeled_resume_kind": any(
            i.get("has_kind_resume") or i.get("content_type") == "resume" for i in _intake_view(seed)
        ),
    }
    print("SEED INTAKE:", _safe(seed_report))

    print("\n===== MAIN TURN (real LLM + mock tools) =====")
    try:
        state = run_main_turn(llm)
    except Exception as exc:
        print("MAIN TURN FAILED:", exc)
        traceback.print_exc()
        state = None

    summary = analyze_trace(TRACE)
    print("\n===== MAIN SUMMARY =====")
    print(_safe(summary, 2000))
    if state is not None:
        print(
            "final_status=",
            state.session.status,
            "invocations=",
            getattr(build_registry(data_source="mock"), "invocations", None),
        )
        # registry on state path: get from last tools — re-read from TRACE
        print("decisions=", len(state.decisions or []))
        print("jobs=", list((state.jobs or {}).keys())[:10])

    print("\n===== HUMAN GATE CONTINUATION =====")
    try:
        hg_state, hg_trace = run_human_gate_continuation(llm, state)
        hg_decisions = [t for t in hg_trace if t.get("phase") == "reasoner_decision"]
        first = hg_decisions[0] if hg_decisions else None
        print("HG first observation kind:", (hg_state.last_raw_observation or {}) if False else None)
        # Initial observation is consumed into first decide's observation_before
        print("HG first decision:", _safe(first["action"] if first else None))
        print(
            "HG first obs_before kind:",
            (first or {}).get("observation_before", {}).get("kind"),
        )
        hg_tools = [t.get("tool_name") for t in hg_trace if t.get("phase") == "tool"]
        print("HG tool order:", hg_tools)
        print("HG final status:", hg_state.session.status)
        # Did Program auto-search before first Reasoner decision?
        auto_before_reasoner = False
        if hg_trace:
            first_phase = hg_trace[0].get("phase")
            auto_before_reasoner = first_phase == "tool"
        print("HG auto_tool_before_reasoner=", auto_before_reasoner)
        print(
            "HG obs is human_gate_resolved=",
            (first or {}).get("observation_before", {}).get("kind") == OBS_HUMAN_GATE_RESOLVED
            or (first or {}).get("observation_before", {}).get("kind") == "human_gate_resolved",
        )
    except Exception as exc:
        print("HG TURN FAILED:", exc)
        traceback.print_exc()
        hg_trace = []

    payload = {
        "user_message": MESSAGE,
        "seed_intake": seed_report,
        "main_trace": TRACE,
        "main_summary": summary,
        "final_status": getattr(getattr(state, "session", None), "status", None),
        "hg_trace_len": len(hg_trace) if "hg_trace" in dir() else 0,
    }
    # hg_trace is appended into TRACE already; split for report
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
