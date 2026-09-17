"""Live reject-trace for attachment + search. Does not print API keys."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import agent.loop as loop
from agent.orchestrator import run_agent
from api.serialize import serialize_agent_state
from llm.provider import get_llm_provider
from tools.registry import build_registry


def main() -> int:
    resume = (ROOT / "tests/fixtures/resumes/sample_pm.txt").read_text(encoding="utf-8")
    attachments = [
        {
            "filename": "resume.txt",
            "text": resume,
            "kind": "resume",
            "client_hint": "resume",
        }
    ]
    message = "我要深圳的产品经理的工作，简历见附件"

    rejects: list[dict] = []
    orig = loop._bump_reject_and_maybe_fail

    def wrapped(state, error, attempted=None):
        item = {
            "n": int((state.search.stats or {}).get("consecutive_action_rejects") or 0) + 1,
            "error": error,
            "attempted": attempted,
            "opened": int((state.search.stats or {}).get("opened") or 0),
            "listed": int((state.search.stats or {}).get("listed") or 0),
            "memory_status": (state.candidate.memory or {}).get("status"),
            "profile_status": state.candidate.profile_status,
            "persisted": bool(getattr(state.candidate, "profile_persisted_this_turn", False)),
        }
        rejects.append(item)
        print("REJECT", json.dumps(item, ensure_ascii=False), flush=True)
        return orig(state, error=error, attempted=attempted)

    loop._bump_reject_and_maybe_fail = wrapped

    provider = get_llm_provider()
    if provider is None:
        print("llm_unavailable", flush=True)
        return 2

    registry = build_registry(data_source="mock")
    with tempfile.TemporaryDirectory() as tmp:
        state = run_agent(
            message=message,
            attachments=attachments,
            profile_dir=tmp,
            candidate_id="default",
            llm_provider=provider,
            registry=registry,
            data_source="mock",
            constraints={
                "max_open_jd": 8,
                "max_search_results": 8,
                "max_llm_calls": 50,
                "min_recommend": 1,
            },
        )
        payload = serialize_agent_state(state, profile_dir=tmp)
        report = {
            "status": state.session.status,
            "invocations": registry.invocations,
            "error_code": payload.get("error_code"),
            "message": payload.get("message"),
            "stats": dict(state.search.stats or {}),
            "profile_updated": payload.get("profile_updated"),
            "profile": payload.get("profile"),
            "rejects": rejects,
            "memory_status": (state.candidate.memory or {}).get("status"),
            "persisted": state.candidate.profile_persisted_this_turn,
            "understanding_task_kind": (state.understanding or {}).get("task_kind"),
        }
        out = ROOT / "data" / "_reject_trace.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("---DONE---", flush=True)
        print(json.dumps({k: report[k] for k in [
            "status", "invocations", "error_code", "message", "stats",
            "profile_updated", "persisted", "memory_status",
        ]}, ensure_ascii=False, indent=2), flush=True)
        print("reject_n", len(rejects), "wrote", out, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
