"""Live: open one already-visible BOSS JD, extract actions, interpret. Never click apply/contact.

Optional. Not a CI gate. Live testing may open a JD as a workaround; that is not a product flow.
Run with: python tests/test_open_boss_job_live.py
Or: pytest tests/test_open_boss_job_live.py --run-live
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from playwright.sync_api import Error as PlaywrightError

from browser.edge_session import EdgeNotConnected, HumanVerificationStopped, connect_existing_edge, disconnect_edge
from tools.boss_job_search import open_boss_job, parse_job_id
from llm.provider import OpenAICompatibleProvider, get_llm_provider
from tools.interpret_job_actions import HeuristicActionInterpreter


def _print_analysis(job: dict) -> None:
    analysis = job.get("action_analysis") or {}
    inferred = analysis.get("inferred_context") or {}
    print()
    print("真实BOSS JD：")
    print(f"岗位：{job.get('job_title')}")
    print(f"公司：{job.get('company_name')}")
    print(f"URL：{job.get('job_url')}")
    print(f"JD读取：{'成功' if job.get('job_description') or job.get('requirements') else '未读到完整JD正文'}")
    print()
    print("发现操作：")
    actions = analysis.get("actions") or []
    if not actions:
        print("（未得到已分类操作。status=", analysis.get("analysis_status"), "error=", analysis.get("error"), "）")
    for index, item in enumerate(actions, start=1):
        print(f"{index}. raw_text={item.get('raw_text')}")
        print(f"   semantic_intent={item.get('semantic_intent')}")
        print(f"   confidence={item.get('confidence')}")
    print()
    print("最终：")
    print(f"analysis_status={analysis.get('analysis_status')}")
    print(f"application_evidence={inferred.get('application_evidence')}")
    print(f"evidence_source={inferred.get('evidence_source')}")
    print(f"confidence={inferred.get('confidence')}")
    print(f"notes={inferred.get('notes')}")
    print()
    print("是否点击过任何求职操作：否（本测试只打开JD、读取、分析）")


class _RecordingLLM:
    """Pass-through around OpenAICompatibleProvider so acceptance can print raw LLM JSON."""

    def __init__(self, inner: OpenAICompatibleProvider):
        self.inner = inner
        self.raw = None

    def complete_json(self, *, system: str, user: str) -> dict:
        self.raw = self.inner.complete_json(system=system, user=user)
        return self.raw


def main() -> None:
    print("=== open_boss_job 真实 JD 操作语义测试 ===")
    print("不会启动或关闭 Edge，不会点击投递/联系按钮。")
    print("请先暂时关掉 Cursor Playwright MCP 对该浏览器的占用，避免双 CDP 抢同一扇 Edge。")
    print()

    job_url = os.environ.get("BOSS_JOB_URL")
    playwright = None
    browser = None
    page = None
    try:
        playwright, browser, page = connect_existing_edge()
        current = page.url or ""
        if not job_url:
            if "zhipin.com" in current and "/job_detail/" in current:
                job_url = current.split("?")[0]
                print(f"使用当前标签已打开的 JD：{job_url}")
            else:
                raise RuntimeError(
                    "当前标签不是 BOSS 职位详情页，本测试不会自动搜索或跳转。"
                    "请先在 Agent Edge 打开一条 job_detail，或设置环境变量 BOSS_JOB_URL。"
                    f" current_url={current}"
                )
        if "bing.com" in current.lower() or current.lower().startswith("about:blank"):
            raise RuntimeError(
                f"当前标签不在 BOSS JD（疑似 CDP concurrency / browser session contention）。url={current}"
            )

        provider = get_llm_provider()
        if provider is None:
            raise RuntimeError(
                "llm_unavailable: no LLM configured. "
                "Set LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4 "
                "LLM_MODEL=glm-4-flash-250414 and LLM_API_KEY in the local environment"
            )
        if isinstance(provider, HeuristicActionInterpreter):
            raise RuntimeError("HeuristicActionInterpreter must not be used for live BOSS acceptance")
        if not isinstance(provider, OpenAICompatibleProvider):
            raise RuntimeError(
                f"Live acceptance requires OpenAICompatibleProvider, got {type(provider).__name__}"
            )
        recorder = _RecordingLLM(provider)
        print(
            f"LLM: {type(provider).__name__} model={provider.model} base_url={provider.base_url}"
        )
        print("semantic_intent 将只接受该 LLM 的 JSON（schema 校验），无 heuristic fallback。")

        job = open_boss_job(
            job_url=job_url,
            page=page,
            llm_provider=recorder,
        )
        _print_analysis(job)
        print()
        print("LLM raw JSON（GLM 返回、未经关键词分类）：")
        print(json.dumps(recorder.raw, ensure_ascii=False, indent=2))
        print()
        print("完整 action_analysis JSON：")
        print(json.dumps(job.get("action_analysis"), ensure_ascii=False, indent=2))
        analysis = job.get("action_analysis") or {}
        status = analysis.get("analysis_status")
        if status == "insufficient_data":
            raise RuntimeError(
                "JD page produced no extractable actions; cannot prove GLM semantic_intent path"
            )
        if status != "ok":
            raise RuntimeError(
                f"interpret_job_actions failed closed: status={status} error={analysis.get('error')}"
            )
        if recorder.raw is None:
            raise RuntimeError("LLM complete_json was not called; cannot prove GLM produced intents")
        raw_actions = (recorder.raw or {}).get("actions")
        if not isinstance(raw_actions, list):
            raise RuntimeError("LLM JSON has no actions list; cannot prove GLM produced intents")
        for item in analysis.get("actions") or []:
            intent = item.get("semantic_intent")
            if intent not in {
                "apply_resume",
                "start_contact",
                "continue_contact",
                "view_progress",
                "resume_management",
                "interest",
                "favorite",
                "share",
                "report",
                "other",
                "unknown",
            }:
                raise RuntimeError(f"illegal semantic_intent after merge: {intent!r}")
        print()
        print("验收证明：provider=OpenAICompatibleProvider，complete_json 已调用，")
        print("semantic_intent 来自上述 LLM JSON 并经 SEMANTIC_INTENTS 校验，未使用 HeuristicActionInterpreter。")
        if parse_job_id(job.get("job_url")) is None and not job.get("job_title"):
            raise SystemExit(1)
    except HumanVerificationStopped:
        raise SystemExit(1)
    except (EdgeNotConnected, PlaywrightError, RuntimeError, ValueError) as exc:
        message = str(exc)
        print("测试停止。")
        lowered = message.lower()
        if "bing.com" in lowered or "about:blank" in lowered or "没有读到职位" in message or "cdp" in lowered:
            print("阻塞原因：CDP concurrency / browser session contention")
            print("两个 CDP 客户端同时控制同一扇 Edge 时，标签可能跳到 Bing / about:blank。")
        print("原因：")
        print(message)
        raise SystemExit(1)
    finally:
        if playwright is not None:
            disconnect_edge(playwright, browser)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        print("测试停止。")
        print("原因：用户中断。")
        raise SystemExit(1)
