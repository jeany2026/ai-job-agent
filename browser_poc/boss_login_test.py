"""BOSS 直聘登录 POC：可见 Chromium + 用户手动登录 + 基础登录态检测。"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

SCRIPT_DIR = Path(__file__).resolve().parent
PROFILE_DIR = SCRIPT_DIR / "browser_profile"
BOSS_HOME_URL = "https://www.zhipin.com/"
PAGE_TEXT_SUMMARY_LIMIT = 800

# 人机验证 / 风控类页面信号。不含登录流程里用户自己填写的短信验证码。
BLOCKER_KEYWORDS = (
    "请完成安全验证",
    "拖动滑块",
    "滑动验证",
    "人机验证",
    "访问异常",
    "网络环境异常",
    "当前访问行为异常",
    "检测到异常访问",
    "访问存在风险",
    "请进行验证",
    "verify you are human",
    "access denied",
)

LOGGED_IN_HINTS = (
    "沟通",
    "简历",
    "退出登录",
    "我的简历",
    "账号设置",
)

NOT_LOGGED_IN_HINTS = (
    "登录/注册",
    "立即登录",
    "短信登录",
    "微信登录",
    "扫码登录",
)


def stop_test(reason: str, step: str) -> None:
    print("测试停止。")
    print("原因：")
    print(reason)
    print("发生在哪一步：")
    print(step)
    raise SystemExit(1)


def visible_text(page) -> str:
    try:
        return page.inner_text("body") or ""
    except Exception:
        return ""


def summarize_text(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= PAGE_TEXT_SUMMARY_LIMIT:
        return collapsed
    return collapsed[:PAGE_TEXT_SUMMARY_LIMIT] + "..."


def find_blocker(text: str, url: str) -> str | None:
    haystack = f"{url}\n{text}".lower()
    for keyword in BLOCKER_KEYWORDS:
        if keyword.lower() in haystack:
            return keyword
    return None


def detect_login_state(url: str, title: str, text: str) -> tuple[str, str]:
    """返回 (LOGIN_STATE, 检测依据)。无法可靠判断时返回 UNKNOWN。"""
    compact = " ".join(text.split())
    not_logged_hits = [hint for hint in NOT_LOGGED_IN_HINTS if hint in compact]
    logged_in_hits = [hint for hint in LOGGED_IN_HINTS if hint in compact]

    looks_like_login_page = any(
        token in url.lower()
        for token in ("/login", "login.html", "sign")
    )

    if not_logged_hits and not logged_in_hits:
        return (
            "NOT_LOGGED_IN",
            f"页面可见文本包含未登录提示：{', '.join(not_logged_hits)}",
        )

    if looks_like_login_page and not_logged_hits:
        return (
            "NOT_LOGGED_IN",
            f"当前 URL 像登录页，且页面包含：{', '.join(not_logged_hits)}",
        )

    # 同时出现两边的信号时，不假装已经登录。
    if logged_in_hits and not_logged_hits:
        return (
            "UNKNOWN",
            "同时看到登录入口和登录后导航文案，当前无法可靠判断。"
            f" 未登录提示={not_logged_hits}；疑似已登录提示={logged_in_hits}。",
        )

    if logged_in_hits and "登录/注册" not in compact:
        return (
            "LOGGED_IN",
            "页面未见「登录/注册」，且可见文本包含登录后常见入口："
            f"{', '.join(logged_in_hits)}",
        )

    return (
        "UNKNOWN",
        "仅根据当前 URL、标题和可见文本，无法可靠判断是否已登录。"
        f" URL={url}；标题={title or '(空)'}。",
    )


def current_page(context):
    pages = [p for p in context.pages if p.url and p.url != "about:blank"]
    if pages:
        return pages[-1]
    if context.pages:
        return context.pages[0]
    return context.new_page()


def snapshot(page) -> tuple[str, str, str]:
    url = page.url
    try:
        title = page.title()
    except Exception:
        title = ""
    text = visible_text(page)
    return url, title, text


def print_detection(url: str, title: str, text: str, state: str, reason: str) -> None:
    print()
    print("当前 URL：")
    print(url or "(空)")
    print()
    print("页面标题：")
    print(title or "(空)")
    print()
    print("页面文本摘要：")
    print(summarize_text(text) or "(空)")
    print()
    print("检测结果：")
    print(f"LOGIN_STATE = {state}")
    print()
    print("检测依据：")
    print(reason)


def wait_for_manual_login() -> None:
    print("请在打开的浏览器中手动完成 BOSS 直聘登录。")
    print("不要在终端输入账号、密码或验证码。")
    print()
    print("登录完成后，请回到终端按 Enter。")
    try:
        input()
    except EOFError:
        stop_test(
            "终端无法接收 Enter（没有交互式输入）。请在本机终端直接运行本脚本。",
            "等待用户按 Enter 确认已手动登录。",
        )


def main() -> None:
    print("正在启动可见 Chromium（persistent context）...")
    print(f"浏览器 profile 目录：{PROFILE_DIR}")

    try:
        playwright = sync_playwright().start()
    except Exception as exc:
        stop_test(f"Playwright 启动失败：{exc}", "启动 Playwright。")

    try:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1280, "height": 800},
        )
    except Exception as exc:
        playwright.stop()
        stop_test(f"Chromium persistent context 启动失败：{exc}", "启动可见 Chromium。")

    try:
        page = current_page(context)
        print("浏览器已启动，正在打开 BOSS 直聘...")
        try:
            page.goto(BOSS_HOME_URL, wait_until="domcontentloaded", timeout=60_000)
        except PlaywrightTimeoutError:
            stop_test(
                "打开 BOSS 直聘超时。未尝试任何绕过，测试停止。",
                "打开 BOSS 直聘官方网站。",
            )
        except Exception as exc:
            stop_test(
                f"打开 BOSS 直聘失败：{exc}",
                "打开 BOSS 直聘官方网站。",
            )

        page.wait_for_timeout(1500)
        url, title, text = snapshot(page)
        blocker = find_blocker(text, url)
        if blocker:
            print_detection(url, title, text, "BLOCKED", f"页面出现限制信号：{blocker}")
            stop_test(
                f"页面出现访问限制或人机验证（命中：「{blocker}」）。本 POC 不绕过。",
                "打开 BOSS 直聘后检查页面状态。",
            )

        print("已打开页面。")
        print(f"当前 URL：{url}")
        print(f"页面标题：{title or '(空)'}")
        print()

        state, reason = detect_login_state(url, title, text)
        if state != "LOGGED_IN":
            wait_for_manual_login()
            page = current_page(context)
            try:
                page.wait_for_load_state("domcontentloaded", timeout=15_000)
            except PlaywrightTimeoutError:
                pass
            page.wait_for_timeout(1000)
            url, title, text = snapshot(page)
            blocker = find_blocker(text, url)
            if blocker:
                print_detection(url, title, text, "BLOCKED", f"页面出现限制信号：{blocker}")
                stop_test(
                    f"登录过程后页面出现访问限制或人机验证（命中：「{blocker}」）。本 POC 不绕过。",
                    "用户按 Enter 后检测登录状态。",
                )
            state, reason = detect_login_state(url, title, text)
        else:
            print("根据当前页面文本，可能已经处于登录后状态，跳过手动登录等待。")

        print_detection(url, title, text, state, reason)
        print()
        print("按 Enter 关闭浏览器。")
        try:
            input()
        except EOFError:
            pass
    finally:
        context.close()
        playwright.stop()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print()
        stop_test("用户中断。", "脚本运行过程中。")
    except Exception as exc:
        stop_test(f"未预期错误：{exc}", "脚本运行过程中。")
        sys.exit(1)
