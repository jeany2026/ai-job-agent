"""Loop must not call browse_boss_jobs or implement keyword-based open ordering."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

AGENT_DIR = ROOT_DIR / "agent"
RULES_DIR = ROOT_DIR / "rules"
REGISTRY = ROOT_DIR / "tools" / "registry.py"


def _python_files():
    yield from AGENT_DIR.glob("*.py")
    yield from RULES_DIR.glob("*.py")
    yield REGISTRY
    yield ROOT_DIR / "platforms" / "mock" / "jobs.py"
    yield ROOT_DIR / "platforms" / "boss" / "jobs.py"
    yield ROOT_DIR / "platforms" / "liepin" / "jobs.py"
    yield ROOT_DIR / "platforms" / "job51" / "jobs.py"


def test_no_enrich_priority_symbol():
    for path in _python_files():
        source = path.read_text(encoding="utf-8")
        assert "enrich_priority" not in source, path


def test_loop_forbids_browse_boss_jobs():
    loop_src = (AGENT_DIR / "loop.py").read_text(encoding="utf-8")
    decide_src = (AGENT_DIR / "decide.py").read_text(encoding="utf-8")
    orch_src = (AGENT_DIR / "orchestrator.py").read_text(encoding="utf-8")
    assert "FORBIDDEN_LOOP_TOOLS" in loop_src
    tree = ast.parse(decide_src)
    dumped = ast.dump(tree)
    assert "browse_boss_jobs" not in dumped
    assert "browse_boss_jobs" not in orch_src
    registry_src = REGISTRY.read_text(encoding="utf-8")
    assert "browse_boss_jobs" in registry_src
    assert "FORBIDDEN_LOOP_TOOLS" in registry_src
    assert "register" in registry_src
    assert "SEARCH_JOBS_SPEC" in registry_src
    assert "OPEN_JOB_SPEC" in registry_src
    assert "INSPECT_JOB_SPEC" in registry_src
    assert "INSPECT_APPLICATION_STATE_SPEC" in registry_src
    assert "EXECUTE_ACTION_SPEC" in registry_src
    actions_src = (ROOT_DIR / "platforms" / "job_actions.py").read_text(encoding="utf-8")
    assert "search_boss_jobs" in actions_src
    assert "open_boss_job" in actions_src
    assert "search_liepin_jobs" in actions_src
    assert "open_liepin_job" in actions_src
    assert "search_51job_jobs" in actions_src
    assert "open_51job_job" in actions_src


def test_no_heuristic_fallback_in_agent():
    for path in AGENT_DIR.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        dumped = ast.dump(tree)
        assert "Heuristic" not in dumped
        assert "classify_action_semantically" not in source
