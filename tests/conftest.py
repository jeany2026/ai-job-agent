"""Put the project root first so tests/agent does not shadow the agent package."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
root = str(ROOT_DIR)
if root in sys.path:
    sys.path.remove(root)
sys.path.insert(0, root)

# If pytest inserted tests/agent onto sys.path, drop a shadowed top-level `agent`.
shadowed = sys.modules.get("agent")
if shadowed is not None:
    origin = getattr(shadowed, "__file__", "") or ""
    if "tests" in Path(origin).parts and "agent" in Path(origin).parts:
        del sys.modules["agent"]
        for name in list(sys.modules):
            if name.startswith("agent."):
                del sys.modules[name]

LIVE_OPTIONAL_FILENAMES = {
    "test_glm_connection.py",
    "test_boss_job_search.py",
    "test_open_boss_job_live.py",
    "test_execute_boss_live.py",
}


def pytest_addoption(parser):
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="run optional live tests (BOSS/LLM); not a CI gate",
    )


def pytest_collection_modifyitems(config, items):
    run_live = bool(
        config.getoption("--run-live")
        or os.environ.get("RUN_LIVE") == "1"
        or os.environ.get("RUN_LIVE_BOSS") == "1"
    )
    if run_live:
        return
    skip = pytest.mark.skip(reason="optional live test; pass --run-live (not a CI gate)")
    for item in items:
        path_name = Path(str(item.fspath)).name
        if "optional" in item.keywords or "live" in item.keywords or path_name in LIVE_OPTIONAL_FILENAMES:
            item.add_marker(skip)
