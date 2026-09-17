"""In-process live e2e with real GLM + mock jobs; bounded quotas so it can finish."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import agent.loop as loop
from agent.orchestrator import run_agent
from api.serialize import serialize_agent_state
from llm.provider import get_llm_provider
from storage.candidate_profile import CandidateProfileStore
from tools.registry import build_registry


def main() -> int:
    rejects: list[dict] = []
    orig = loop._bump_reject_and_maybe_fail

    def wrapped(state, error, attempted=None):
        item = {
            "n": int((state.search.stats or {}).get("consecutive_action_rejects") or 0) + 1,
            "error": str(error)[:160],
            "tool": (attempted or {}).get("tool_name") if isinstance(attempted, dict) else None,
            "opened": int((state.search.stats or {}).get("opened") or 0),
            "listed": int((state.search.stats or {}).get("listed") or 0),
        }
        rejects.append(item)
        print("REJECT", json.dumps(item, ensure_ascii=False), flush=True)
        return orig(state, error=error, attempted=attempted)

    loop._bump_reject_and_maybe_fail = wrapped

    resume = (ROOT / "tests/fixtures/resumes/sample_pm.txt").read_text(encoding="utf-8")
    attachments = [{"filename": "resume.txt", "text": resume, "kind": "resume", "client_hint": "resume"}]
    profile_dir = str(ROOT / "data" / "candidate_profiles")
    Path(profile_dir).mkdir(parents=True, exist_ok=True)

    provider = get_llm_provider()
    assert provider is not None
    registry = build_registry(data_source="mock")
    state = run_agent(
        message="我要深圳的产品经理的工作，简历见附件",
        attachments=attachments,
        profile_dir=profile_dir,
        candidate_id="default",
        llm_provider=provider,
        registry=registry,
        data_source="mock",
        constraints={
            "max_open_jd": 2,
            "max_search_results": 5,
            "max_llm_calls": 25,
            "min_recommend": 1,
        },
        max_steps=40,
    )
    payload = serialize_agent_state(state, profile_dir=profile_dir)
    store = CandidateProfileStore(profile_dir)
    disk = store.load("default") if store.exists("default") else None
    report = {
        "status": state.session.status,
        "invocations": registry.invocations,
        "error_code": payload.get("error_code"),
        "message": payload.get("message"),
        "stats": dict(state.search.stats or {}),
        "profile_updated": payload.get("profile_updated"),
        "persisted_flag": state.candidate.profile_persisted_this_turn,
        "disk_exists": bool(disk),
        "disk_capabilities": (
            serialize_agent_state(state, profile_dir=profile_dir).get("profile") or {}
        ).get("core_capabilities"),
        "ui_profile": payload.get("profile"),
        "rejects": rejects[:12],
        "reject_n": len(rejects),
        "recommended_n": len(payload.get("recommended") or []),
    }
    out = ROOT / "data" / "_live_restart_e2e.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in [
        "status", "invocations", "error_code", "message", "stats",
        "profile_updated", "persisted_flag", "disk_exists", "disk_capabilities",
        "reject_n", "recommended_n",
    ]}, ensure_ascii=False, indent=2), flush=True)

    core = (
        "analyze_candidate" in registry.invocations
        and report["disk_exists"]
        and report["error_code"] != "repeated_action_rejected"
    )
    print("CORE_OK" if core else "CORE_FAIL", flush=True)
    print("wrote", out, flush=True)
    return 0 if core else 1


if __name__ == "__main__":
    raise SystemExit(main())
