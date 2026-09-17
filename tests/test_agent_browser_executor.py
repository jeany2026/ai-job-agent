"""Agent browser executor: Tool infra ensures Agent Edge; not a Reasoner Action."""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from browser.edge_launcher import AgentEdgeNotReady
from browser.edge_session import EdgeNotConnected, connect_existing_edge, get_reusable_page


def test_connect_calls_ensure_before_cdp():
    fake_page = MagicMock()
    fake_browser = MagicMock()
    fake_browser.contexts = []
    fake_pw = MagicMock()
    fake_pw.chromium.connect_over_cdp.return_value = fake_browser

    with (
        patch("browser.edge_session.ensure_agent_edge", return_value={"cdp_url": "http://127.0.0.1:9222", "status": "started"}) as ensure,
        patch("browser.edge_session.sync_playwright") as sp,
        patch("browser.edge_session.get_reusable_page", return_value=fake_page) as get_page,
    ):
        sp.return_value.start.return_value = fake_pw
        playwright, browser, page = connect_existing_edge()

    ensure.assert_called_once()
    fake_pw.chromium.connect_over_cdp.assert_called_once_with("http://127.0.0.1:9222")
    get_page.assert_called_once_with(fake_browser)
    assert page is fake_page
    assert browser is fake_browser
    assert playwright is fake_pw


def test_connect_skips_ensure_when_endpoint_explicit():
    fake_page = MagicMock()
    fake_browser = MagicMock()
    fake_pw = MagicMock()
    fake_pw.chromium.connect_over_cdp.return_value = fake_browser

    with (
        patch("browser.edge_session.ensure_agent_edge") as ensure,
        patch("browser.edge_session.sync_playwright") as sp,
        patch("browser.edge_session.get_reusable_page", return_value=fake_page),
    ):
        sp.return_value.start.return_value = fake_pw
        connect_existing_edge("http://127.0.0.1:9333")

    ensure.assert_not_called()
    fake_pw.chromium.connect_over_cdp.assert_called_once_with("http://127.0.0.1:9333")


def test_ensure_failure_propagates_as_agent_edge_not_ready():
    with patch(
        "browser.edge_session.ensure_agent_edge",
        side_effect=AgentEdgeNotReady("port busy"),
    ):
        try:
            connect_existing_edge()
        except AgentEdgeNotReady as exc:
            assert "port busy" in str(exc)
            return
    raise AssertionError("expected AgentEdgeNotReady")


def test_cdp_failure_is_edge_not_connected():
    fake_pw = MagicMock()
    fake_pw.chromium.connect_over_cdp.side_effect = RuntimeError("ECONNREFUSED")

    with (
        patch("browser.edge_session.ensure_agent_edge", return_value={"cdp_url": "http://127.0.0.1:9222"}),
        patch("browser.edge_session.sync_playwright") as sp,
    ):
        sp.return_value.start.return_value = fake_pw
        try:
            connect_existing_edge()
        except EdgeNotConnected as exc:
            assert "CDP" in str(exc) or "调试连接" in str(exc)
            fake_pw.stop.assert_called()
            return
    raise AssertionError("expected EdgeNotConnected")


def test_get_reusable_page_accepts_about_blank():
    blank = MagicMock()
    blank.url = "about:blank"
    context = MagicMock()
    context.pages = [blank]
    browser = MagicMock()
    browser.contexts = [context]
    page = get_reusable_page(browser)
    assert page is blank


def test_get_reusable_page_skips_unrelated_sites_like_baidu():
    baidu = MagicMock()
    baidu.url = "https://www.baidu.com/?tn=15007414_21_dg"
    blank = MagicMock()
    blank.url = "about:blank"
    context = MagicMock()
    context.pages = [baidu, blank]
    browser = MagicMock()
    browser.contexts = [context]
    page = get_reusable_page(browser)
    assert page is blank


def test_get_reusable_page_opens_new_tab_instead_of_baidu():
    baidu = MagicMock()
    baidu.url = "https://www.baidu.com/"
    fresh = MagicMock()
    fresh.url = "about:blank"
    context = MagicMock()
    context.pages = [baidu]
    context.new_page.return_value = fresh
    browser = MagicMock()
    browser.contexts = [context]
    page = get_reusable_page(browser)
    context.new_page.assert_called_once()
    assert page is fresh


def test_get_reusable_page_prefers_zhipin_over_blank():
    blank = MagicMock()
    blank.url = "about:blank"
    boss = MagicMock()
    boss.url = "https://www.zhipin.com/web/geek/jobs"
    context = MagicMock()
    context.pages = [blank, boss]
    browser = MagicMock()
    browser.contexts = [context]
    page = get_reusable_page(browser)
    assert page is boss


def test_get_reusable_page_prefers_geek_jobs_over_job_detail():
    geek = MagicMock()
    geek.url = "https://www.zhipin.com/web/geek/jobs?city=101280600"
    detail = MagicMock()
    detail.url = "https://www.zhipin.com/job_detail/83e043bce53ea3730nV72Nq5EFZT.html"
    context = MagicMock()
    context.pages = [geek, detail]
    browser = MagicMock()
    browser.contexts = [context]
    page = get_reusable_page(browser)
    assert page is geek


def test_no_launch_edge_reasoner_tool_in_registry():
    from tools.registry import available_tool_contracts, build_registry

    names = {item["name"] for item in available_tool_contracts(build_registry(data_source="boss"))}
    assert "launch_edge" not in names
    assert "connect_edge" not in names
    assert "open_browser" not in names
    assert "ensure_agent_edge" not in names


def test_connect_existing_edge_source_mentions_ensure():
    source = (ROOT_DIR / "browser" / "edge_session.py").read_text(encoding="utf-8")
    assert "ensure_agent_edge" in source
    tree = ast.parse(source)
    assert tree is not None
