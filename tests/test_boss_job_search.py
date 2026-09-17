"""Call the BOSS job tools against the already-logged-in Edge.

Optional live script / test. Not a CI gate.
Run with: python tests/test_boss_job_search.py
Or: pytest tests/test_boss_job_search.py --run-live
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from playwright.sync_api import Error as PlaywrightError

from browser.edge_session import EdgeNotConnected, HumanVerificationStopped
from tools.boss_job_search import jobs_to_json, search_boss_jobs


def main() -> None:
    print("=== search_boss_jobs Tool 测试 ===")
    print("前提：Edge 已打开、已登录 BOSS，并已开启 edge://inspect 远程调试。")
    print("请先暂时关掉 Cursor 里的 Playwright MCP 对该浏览器的占用，避免两个 CDP 客户端抢同一扇 Edge。")
    print("本测试不会启动或关闭 Edge。")
    print()

    try:
        result = search_boss_jobs(
            keyword="高级产品经理",
            city="深圳",
            limit=30,
            mode="fresh",
        )
    except (EdgeNotConnected, HumanVerificationStopped, PlaywrightError, RuntimeError, ValueError) as exc:
        if isinstance(exc, HumanVerificationStopped):
            raise SystemExit(1)
        print("测试停止。")
        print("原因：")
        print(str(exc))
        raise SystemExit(1)

    print()
    print("=== 结构化结果 ===")
    jobs = result.get("jobs") if isinstance(result, dict) else result
    print(jobs_to_json(jobs))
    if isinstance(result, dict):
        print("fetch_status=", result.get("fetch_status"), "can_continue=", result.get("can_continue"))
    print()
    print(f"返回条数：{len(jobs)}")
    with_jd = sum(1 for job in jobs if job.get("job_description"))
    print(f"含完整 JD 的条数：{with_jd}")
    if len(jobs) >= 16:
        print("第 2 批：已返回超过 15 条，视为加载更多成功。")
    else:
        print("第 2 批：返回不足 16 条，加载更多未达到预期。")
    ids = [job.get("job_id") for job in jobs if job.get("job_id")]
    print(f"唯一 job_id 数量：{len(set(ids))}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        print("测试停止。")
        print("原因：用户中断。")
        raise SystemExit(1)
