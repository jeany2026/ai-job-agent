"""Optional live BOSS execute check. Not a CI gate. Does not search."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from browser.edge_session import EdgeNotConnected, connect_existing_edge, disconnect_edge

pytestmark = pytest.mark.live


def test_live_boss_execute_optional():
    """Real Edge/CDP apply is skipped unless D3_LIVE_APPLY=1 and a job URL is provided."""
    job_url = (os.environ.get("D3_LIVE_JOB_URL") or "").strip()
    allow_apply = os.environ.get("D3_LIVE_APPLY") == "1"
    if not job_url or not allow_apply:
        pytest.skip(
            "real BOSS E2E not executed because no logged-in Edge/CDP apply session was requested "
            "(set D3_LIVE_APPLY=1 and D3_LIVE_JOB_URL)"
        )
    try:
        playwright, browser, page = connect_existing_edge()
    except EdgeNotConnected as exc:
        pytest.skip(f"real BOSS E2E not executed: {exc}")
    disconnect_edge(playwright, browser)
    pytest.skip("connected Edge, but live apply is opt-in and must be run with a dedicated job URL")
