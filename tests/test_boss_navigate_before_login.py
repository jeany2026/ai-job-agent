"""BOSS search must open zhipin.com before login_required checks."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.boss_job_search import ensure_logged_in, navigate_to_boss_search, open_search


def test_navigate_to_boss_search_raises_if_still_on_baidu():
    page = MagicMock()
    page.url = "https://www.baidu.com/?tn=15007414_21_dg"
    page.goto = MagicMock(side_effect=lambda *a, **k: None)
    # After goto, still baidu — simulates navigation failure.
    try:
        navigate_to_boss_search(page, "产品经理", "深圳")
    except RuntimeError as exc:
        assert "未能打开 BOSS" in str(exc)
        assert "baidu.com" in str(exc)
        return
    raise AssertionError("expected navigation failure")


def test_navigate_to_boss_search_accepts_zhipin_url():
    page = MagicMock()
    page.url = "https://www.baidu.com/"

    def fake_goto(url, **_kwargs):
        page.url = url

    page.goto.side_effect = fake_goto
    page.wait_for_url = MagicMock()
    navigate_to_boss_search(page, "产品经理", "深圳")
    assert "zhipin.com" in page.url
    assert "产品经理" in page.url or "%E4%BA%A7%E5%93%81%E7%BB%8F%E7%90%86" in page.url


def test_ensure_logged_in_rejects_non_zhipin_url():
    page = MagicMock()
    page.url = "https://www.baidu.com/"
    page.frames = []
    page.evaluate = MagicMock(return_value=False)
    try:
        ensure_logged_in({"loggedIn": False}, page)
    except RuntimeError as exc:
        assert "尚未打开 BOSS" in str(exc)
        return
    raise AssertionError("expected non-zhipin guard")


def test_ensure_logged_in_on_zhipin_without_session():
    page = MagicMock()
    page.url = "https://www.zhipin.com/web/geek/jobs?query=pm"
    page.frames = []
    page.evaluate = MagicMock(
        return_value={
            "loggedIn": False,
            "jobs": [],
            "vueCount": 0,
            "domCount": 0,
            "url": page.url,
        }
    )
    try:
        ensure_logged_in({"loggedIn": False, "jobs": [], "vueCount": 0, "domCount": 0}, page)
    except RuntimeError as exc:
        assert "未检测到 BOSS 登录态" in str(exc)
        assert "zhipin.com" in str(exc)
        assert "baidu.com" not in str(exc)
        return
    raise AssertionError("expected login_required-style error on zhipin")


def test_ensure_logged_in_accepts_geek_job_list_without_recommend_link():
    """Logged-in geek page often shows 消息/简历/职位卡; recommend href alone is brittle."""
    page = MagicMock()
    page.url = "https://www.zhipin.com/web/geek/jobs?query=%E4%BA%A7%E5%93%81%E7%BB%8F%E7%90%86&city=101280600"
    page.frames = []
    snapshot = {
        "loggedIn": False,
        "jobs": [{"job_id": "x", "job_title": "产品经理"}],
        "vueCount": 1,
        "domCount": 1,
        "url": page.url,
    }
    ensure_logged_in(snapshot, page)


def test_snapshot_indicates_boss_login_from_chat_signal_flag():
    from tools.boss_job_search import snapshot_indicates_boss_login

    assert snapshot_indicates_boss_login(
        {"loggedIn": True, "jobs": [], "url": "https://www.zhipin.com/web/geek/jobs"}
    )
    assert snapshot_indicates_boss_login(
        {
            "loggedIn": False,
            "jobs": [{"job_id": "1"}],
            "domCount": 1,
            "url": "https://www.zhipin.com/web/geek/jobs?query=pm",
        }
    )
    assert not snapshot_indicates_boss_login(
        {"loggedIn": False, "jobs": [], "vueCount": 0, "domCount": 0, "url": "https://www.zhipin.com/web/geek/jobs"}
    )


def test_resolve_boss_content_target_prefers_frame_with_geek_nav():
    from tools.boss_job_search import resolve_boss_content_target

    main = MagicMock()
    main.evaluate = MagicMock(return_value=0)
    geek = MagicMock()
    geek.evaluate = MagicMock(return_value=8)
    page = MagicMock()
    page.frames = [main, geek]
    assert resolve_boss_content_target(page) is geek


def test_open_search_navigates_before_reading_snapshot():
    page = MagicMock()
    page.url = "about:blank"
    page.frames = []
    calls = []

    def fake_goto(url, **_kwargs):
        calls.append(url)
        page.url = url

    page.goto.side_effect = fake_goto
    page.wait_for_url = MagicMock()
    page.evaluate = MagicMock(
        return_value={
            "jobs": [],
            "loggedIn": False,
            "url": "https://www.zhipin.com/web/geek/jobs",
            "vueCount": 0,
            "domCount": 0,
        }
    )
    # evaluate_retry may call page.evaluate with JS string
    from unittest.mock import patch

    with patch("tools.boss_job_search.evaluate_retry", return_value={"jobs": [], "loggedIn": False, "url": page.url}):
        with patch("tools.boss_job_search.check_human_verification"):
            snapshot = open_search(page, "产品经理", "深圳")
    assert calls and "zhipin.com" in calls[0]
    assert snapshot.get("loggedIn") is False
