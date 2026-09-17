"""Real BOSS E2E regression: capture Reasoner LLM user JSON + action trace.

Uses the live Agent path (real LLM + real search_jobs/BOSS). Does not prescribe
next Tools. Stops cleanly on Human Gate (login/captcha). Writes a full report.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import agent.bind_arguments as bind_mod
import agent.decide as decide_mod
import agent.loop as loop_mod
from agent.orchestrator import run_agent
from llm import get_llm_provider
from tools.registry import build_registry

MESSAGE = (
    "我需要的工作是在深圳，最好福田区和南山区，职位产品经理/产品主管/需求工程师，附件简历。"
)
RESUME_PATH = ROOT / "tests" / "fixtures" / "resumes" / "sample_pm.txt"
OUT_PATH = ROOT / "data" / "_e2e_boss_program_hints_regression.json"

TRACE: list[dict] = []
LLM_CALLS: list[dict] = []
_round = {"n": 0}

_orig_decide = decide_mod.decide
_orig_bind = bind_mod.bind_tool_arguments
_orig_act = loop_mod._act
_orig_reduce = loop_mod.reduce


def _safe(obj: Any, limit: int = 2000) -> str:
    try:
        text = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        text = str(obj)
    return text if len(text) <= limit else text[:limit] + "…"


def _job_stages(state) -> dict:
    return {
        key: {
            "stage": rec.stage,
            "has_opened": isinstance(rec.opened, dict),
            "has_profile": isinstance(rec.job_profile, dict),
            "has_match": isinstance(rec.match_result, dict),
        }
        for key, rec in (state.jobs or {}).items()
    }


def _payload_checklist(user_obj: dict) -> dict:
    obs = user_obj.get("observation") if isinstance(user_obj.get("observation"), dict) else {}
    ctx = user_obj.get("context") if isinstance(user_obj.get("context"), dict) else {}
    hints = user_obj.get("program_hints")
    facts = obs.get("world_search_facts") if isinstance(obs.get("world_search_facts"), dict) else None
    return {
        "has_observation": "observation" in user_obj and user_obj.get("observation") is not None,
        "has_program_hints_key": "program_hints" in user_obj,
        "program_hints_non_null": hints is not None,
        "observation_unexplored_listed_count": obs.get("unexplored_listed_count"),
        "observation_unexplored_listed_job_keys": obs.get("unexplored_listed_job_keys"),
        "observation_remaining_opens": obs.get("remaining_opens"),
        "observation_has_world_search_facts": isinstance(facts, dict),
        "context_unexplored_listed_count": ctx.get("unexplored_listed_count"),
        "context_unexplored_listed_job_keys": ctx.get("unexplored_listed_job_keys"),
        "context_remaining_opens": (user_obj.get("constraints") or {}).get("remaining_opens")
        if isinstance(user_obj.get("constraints"), dict)
        else ctx.get("remaining_opens"),
        "obs_kind": obs.get("kind"),
        "obs_progress": obs.get("progress"),
        "obs_fetch_status": obs.get("fetch_status"),
        "search_executed": obs.get("search_executed"),
    }


class CapturingLLM:
    """Wrap real provider; record every Reasoner/tool complete_json user payload."""

    def __init__(self, inner):
        self.inner = inner
        self.calls: list[dict] = []

    def complete_json(self, *, system: str, user: str) -> dict:
        parsed_user = None
        parse_error = None
        try:
            parsed_user = json.loads(user) if isinstance(user, str) and user.strip().startswith("{") else None
        except Exception as exc:
            parse_error = str(exc)
        is_reasoner = "你是全局 Agent Reasoner" in (system or "")
        entry = {
            "n": len(self.calls) + 1,
            "is_reasoner": is_reasoner,
            "system_excerpt": (system or "")[:120],
            "user_parse_error": parse_error,
            "checklist": _payload_checklist(parsed_user) if isinstance(parsed_user, dict) else None,
            "observation_kind": (parsed_user or {}).get("observation", {}).get("kind")
            if isinstance(parsed_user, dict) and isinstance(parsed_user.get("observation"), dict)
            else None,
            "program_hints_trigger": (
                (parsed_user or {}).get("program_hints") or {}
            ).get("trigger")
            if isinstance(parsed_user, dict)
            else None,
            "user_keys": sorted(parsed_user.keys()) if isinstance(parsed_user, dict) else None,
            "raw_user_excerpt": (user or "")[:1500],
        }
        raw = self.inner.complete_json(system=system, user=user)
        entry["llm_raw_action"] = raw if isinstance(raw, dict) else {"non_dict": type(raw).__name__}
        self.calls.append(entry)
        LLM_CALLS.append(entry)
        print(
            f"[LLM#{entry['n']}] reasoner={is_reasoner} "
            f"obs={entry.get('observation_kind')} hints_trigger={entry.get('program_hints_trigger')} "
            f"action={_safe(entry.get('llm_raw_action'), 300)}"
        )
        return raw


def traced_decide(state, registry=None, llm_provider=None):
    _round["n"] += 1
    n = _round["n"]
    obs_before = deepcopy(state.last_raw_observation) if isinstance(state.last_raw_observation, dict) else None
    llm_before = len(LLM_CALLS)
    action = _orig_decide(state, registry=registry, llm_provider=llm_provider)
    llm_after = LLM_CALLS[llm_before:]
    reasoner_calls = [c for c in llm_after if c.get("is_reasoner")]
    entry = {
        "round": n,
        "phase": "reasoner_decision",
        "observation_before": {
            "kind": (obs_before or {}).get("kind") if isinstance(obs_before, dict) else None,
            "snippet": _safe(obs_before, 1200),
        },
        "reasoner_llm_calls": reasoner_calls,
        "action_returned_by_llm_path": {
            "action_type": action.action_type,
            "tool_name": action.tool_name,
            "arguments": dict(action.arguments or {}),
            "intent": action.intent,
            "job_key": action.job_key,
            "question": action.question,
            "reason": action.reason,
            "error_code": action.error_code,
        },
        "world": {
            "listed_count": int((state.search.stats or {}).get("listed_count") or (state.search.stats or {}).get("listed") or 0),
            "opened": int((state.search.stats or {}).get("opened") or 0),
            "job_stages": _job_stages(state),
            "stats_excerpt": {
                k: (state.search.stats or {}).get(k)
                for k in (
                    "search_executed_count",
                    "zero_increment_count",
                    "last_search_progress",
                    "consecutive_no_progress_blocks",
                    "last_action_fingerprint",
                    "last_action_progress",
                )
            },
        },
    }
    TRACE.append(entry)
    print(f"\n=== ROUND {n} REASONER ACTION === {_safe(entry['action_returned_by_llm_path'])}")
    return action


def traced_bind(state, action):
    result = _orig_bind(state, action)
    TRACE.append(
        {
            "round": _round["n"],
            "phase": "binding",
            "ok": result.get("ok"),
            "observation_kind": (result.get("observation") or {}).get("kind")
            if isinstance(result.get("observation"), dict)
            else None,
            "argument_keys": sorted((result.get("arguments") or {}).keys())
            if isinstance(result.get("arguments"), dict)
            else [],
            "changed_tool": False,
            "snippet": _safe(
                {
                    "ok": result.get("ok"),
                    "obs": result.get("observation"),
                    "args": result.get("arguments"),
                },
                1200,
            ),
        }
    )
    return result


def traced_act(action, registry, llm_provider=None, arguments=None, search_session=None):
    result = _orig_act(
        action,
        registry,
        llm_provider=llm_provider,
        arguments=arguments,
        search_session=search_session,
    )
    TRACE.append(
        {
            "round": _round["n"],
            "phase": "tool",
            "requested_tool": action.tool_name,
            "bound_arguments": dict(arguments or {}),
            "search_mode": (arguments or {}).get("mode") if isinstance(arguments, dict) else None,
            "result_snippet": _safe(result, 1500),
            "result_platform": (result or {}).get("platform") if isinstance(result, dict) else None,
            "result_fetch_status": (result or {}).get("fetch_status") if isinstance(result, dict) else None,
            "result_job_count": len((result or {}).get("jobs") or []) if isinstance(result, dict) else None,
        }
    )
    print(
        f"--- TOOL {action.tool_name} platform={(result or {}).get('platform') if isinstance(result, dict) else None} "
        f"fetch={(result or {}).get('fetch_status') if isinstance(result, dict) else None} ---"
    )
    return result


def traced_reduce(state, action, result, ctx=None):
    out = _orig_reduce(state, action, result, ctx)
    obs = out.last_raw_observation if isinstance(out.last_raw_observation, dict) else {}
    TRACE.append(
        {
            "round": _round["n"],
            "phase": "reduce_world",
            "observation_kind": obs.get("kind"),
            "observation_snippet": _safe(obs, 1500),
            "session_status": out.session.status,
            "listed_count": int((out.search.stats or {}).get("listed_count") or (out.search.stats or {}).get("listed") or 0),
            "opened": int((out.search.stats or {}).get("opened") or 0),
            "job_stages": _job_stages(out),
        }
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


def _identical_fresh_pm_search(action: dict) -> bool:
    if action.get("action_type") != "tool" or action.get("tool_name") != "search_jobs":
        return False
    args = action.get("arguments") or {}
    mode = args.get("mode") or "fresh"
    return (
        str(args.get("keyword") or "").strip() == "产品经理"
        and str(args.get("city") or "").strip() == "深圳"
        and str(mode).strip().casefold() == "fresh"
    )


def analyze(state) -> dict:
    decisions = [t for t in TRACE if t.get("phase") == "reasoner_decision"]
    tools = [t for t in TRACE if t.get("phase") == "tool"]
    reasoner_llms = [c for c in LLM_CALLS if c.get("is_reasoner")]

    # After listed>0, count repeated identical fresh PM searches proposed by Reasoner
    listed_seen = False
    fresh_pm_after_listed = []
    for d in decisions:
        listed = int((d.get("world") or {}).get("listed_count") or 0)
        if listed > 0:
            listed_seen = True
        act = d.get("action_returned_by_llm_path") or {}
        if listed_seen and _identical_fresh_pm_search(act):
            fresh_pm_after_listed.append(
                {
                    "round": d.get("round"),
                    "action": act,
                    "obs_before": (d.get("observation_before") or {}).get("kind"),
                    "llm_checklist": (d.get("reasoner_llm_calls") or [{}])[-1].get("checklist")
                    if d.get("reasoner_llm_calls")
                    else None,
                }
            )

    # Payload field coverage on reasoner calls after first search observation / no-progress
    post_search_payload_checks = []
    for c in reasoner_llms:
        kind = c.get("observation_kind")
        if kind in {"tool_result", "repeated_no_progress"} or (
            isinstance(c.get("checklist"), dict)
            and c["checklist"].get("search_executed") is True
        ):
            post_search_payload_checks.append(
                {
                    "n": c.get("n"),
                    "observation_kind": kind,
                    "program_hints_trigger": c.get("program_hints_trigger"),
                    "checklist": c.get("checklist"),
                    "llm_raw_action": c.get("llm_raw_action"),
                }
            )

    search_tools = [t for t in tools if t.get("requested_tool") == "search_jobs"]
    return {
        "final_status": getattr(getattr(state, "session", None), "status", None),
        "error_code": (state.output or {}).get("error_code") if state and isinstance(state.output, dict) else None,
        "output_message": (state.output or {}).get("message") if state and isinstance(state.output, dict) else None,
        "human_gate": getattr(state, "human_gate", None) if state else None,
        "stats": dict(state.search.stats or {}) if state else {},
        "listed_count": int((state.search.stats or {}).get("listed_count") or (state.search.stats or {}).get("listed") or 0)
        if state
        else 0,
        "opened": int((state.search.stats or {}).get("opened") or 0) if state else 0,
        "decision_order": [
            (
                d["action_returned_by_llm_path"].get("action_type"),
                d["action_returned_by_llm_path"].get("tool_name"),
                (d["action_returned_by_llm_path"].get("arguments") or {}).get("mode"),
                (d["action_returned_by_llm_path"].get("arguments") or {}).get("keyword"),
            )
            for d in decisions
        ],
        "tool_order": [t.get("requested_tool") for t in tools],
        "search_tool_platforms": [t.get("result_platform") for t in search_tools],
        "search_fetch_statuses": [t.get("result_fetch_status") for t in search_tools],
        "identical_fresh_pm_search_after_listed": fresh_pm_after_listed,
        "identical_fresh_pm_after_listed_count": len(fresh_pm_after_listed),
        "post_search_reasoner_payload_checks": post_search_payload_checks,
        "reasoner_llm_call_count": len(reasoner_llms),
        "job_keys": list((state.jobs or {}).keys())[:20] if state else [],
    }


def verdict(report: dict) -> dict:
    status = report.get("final_status")
    listed = int(report.get("listed_count") or 0)
    thrash = int(report.get("identical_fresh_pm_after_listed_count") or 0)
    checks = report.get("post_search_reasoner_payload_checks") or []
    payload_ok = False
    payload_gaps = []
    if checks:
        # Require at least one post-search reasoner call with full required fields when search obs present
        for c in checks:
            cl = c.get("checklist") or {}
            needed = []
            if not cl.get("has_observation"):
                needed.append("observation")
            if not cl.get("has_program_hints_key"):
                needed.append("program_hints")
            # world facts / unexplored may live on observation after search tool_result
            if c.get("observation_kind") == "tool_result" and cl.get("search_executed"):
                if not cl.get("observation_has_world_search_facts"):
                    needed.append("world_search_facts")
                if cl.get("observation_unexplored_listed_count") is None and cl.get("context_unexplored_listed_count") is None:
                    needed.append("unexplored_listed_count")
                if cl.get("observation_unexplored_listed_job_keys") is None and cl.get("context_unexplored_listed_job_keys") is None:
                    needed.append("unexplored_listed_job_keys")
                if cl.get("observation_remaining_opens") is None and cl.get("context_remaining_opens") is None:
                    needed.append("remaining_opens")
            if c.get("observation_kind") == "repeated_no_progress":
                if cl.get("observation_unexplored_listed_count") is None and cl.get("context_unexplored_listed_count") is None:
                    needed.append("unexplored_listed_count")
            if not needed:
                payload_ok = True
                break
            payload_gaps = needed
    else:
        payload_gaps = ["no_post_search_reasoner_call"]

    platforms = report.get("search_tool_platforms") or []
    real_boss_search = any(p == "boss" for p in platforms)

    stuck = None
    if status == "NEEDS_HUMAN":
        stuck = "human_gate"
        e2e_ok = True  # allowed
    elif status == "FAILED" and report.get("error_code") == "repeated_no_progress":
        stuck = "repeated_no_progress"
        e2e_ok = False
    elif status in {"DONE", "WAITING_USER"}:
        stuck = None
        e2e_ok = listed > 0 and thrash < 8 and payload_ok
    elif status == "FAILED":
        stuck = f"failed:{report.get('error_code')}"
        e2e_ok = False
    else:
        stuck = f"status:{status}"
        e2e_ok = False

    # Stricter: thrash of identical fresh after listed is a product regression even if not fail-closed yet
    if listed > 0 and thrash >= 3 and status != "NEEDS_HUMAN":
        e2e_ok = False
        stuck = stuck or "identical_fresh_search_thrash_after_listed"

    if not real_boss_search and status != "NEEDS_HUMAN":
        # Human gate may fire before a successful tool result is recorded
        if not any(t.get("requested_tool") == "search_jobs" for t in TRACE if t.get("phase") == "tool"):
            if status != "NEEDS_HUMAN":
                stuck = stuck or "no_search_jobs_executed"

    return {
        "e2e_complete_ok": bool(e2e_ok),
        "stuck_layer": stuck,
        "real_boss_search_seen": real_boss_search,
        "payload_fields_ok": payload_ok,
        "payload_gaps": payload_gaps,
        "listed_count": listed,
        "identical_fresh_thrash_after_listed": thrash,
        "final_status": status,
    }


def main() -> int:
    print("Loading LLM…")
    inner = get_llm_provider()
    if inner is None:
        print("FAIL: LLM provider unavailable (check LLM_API_KEY / env)")
        return 2
    print(f"provider={type(inner).__name__} model_env={os.getenv('LLM_MODEL')}")

    resume_text = RESUME_PATH.read_text(encoding="utf-8")
    llm = CapturingLLM(inner)
    install_hooks()
    state = None
    try:
        state = run_agent(
            message=MESSAGE,
            attachments=[
                {
                    "filename": "resume.txt",
                    "client_hint": "resume",
                    "text": resume_text,
                }
            ],
            data_source="boss",
            llm_provider=llm,
            conversation_id="e2e-boss-program-hints-1",
            max_steps=28,
            constraints={
                "max_open_jd": 4,
                "max_llm_calls": 22,
                "max_search_results": 40,
            },
        )
    except Exception as exc:
        print("RUN EXCEPTION:", exc)
        traceback.print_exc()
    finally:
        uninstall_hooks()

    report = analyze(state)
    v = verdict(report)
    payload = {
        "user_message": MESSAGE,
        "data_source": "boss",
        "verdict": v,
        "report": report,
        "trace": TRACE,
        "llm_calls": LLM_CALLS,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("\n===== VERDICT =====")
    print(json.dumps(v, ensure_ascii=False, indent=2))
    print(f"Wrote {OUT_PATH}")
    return 0 if v.get("e2e_complete_ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
