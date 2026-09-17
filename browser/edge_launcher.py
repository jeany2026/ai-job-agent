"""Prepare a dedicated Agent Edge with CDP on 127.0.0.1.

Does not launch or close the user's daily Edge. Does not attach to the
default Edge profile. Does not kill processes if the CDP port is busy.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_CDP_PORT = 9222
WAIT_SECONDS = 30


class AgentEdgeNotReady(RuntimeError):
    """Agent Edge CDP is not available and could not be started."""


def cdp_port() -> int:
    raw = (os.environ.get("EDGE_CDP_PORT") or str(DEFAULT_CDP_PORT)).strip()
    try:
        port = int(raw)
    except ValueError as exc:
        raise AgentEdgeNotReady(f"EDGE_CDP_PORT is not an integer: {raw}") from exc
    if not (1 <= port <= 65535):
        raise AgentEdgeNotReady(f"EDGE_CDP_PORT out of range: {port}")
    return port


def cdp_url(port: int | None = None) -> str:
    return f"http://127.0.0.1:{port if port is not None else cdp_port()}"


def agent_profile_dir() -> Path:
    override = (os.environ.get("EDGE_USER_DATA_DIR") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    # Project lives on D:; keep Agent Edge cookies next to the repo, not on C:.
    return Path(__file__).resolve().parents[1] / "browser_profile"


def cdp_ready(port: int | None = None) -> dict | None:
    """Return /json/version payload if Agent CDP is serving, else None."""
    url = cdp_url(port) + "/json/version"
    try:
        with urllib.request.urlopen(url, timeout=1.5) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(body, dict):
        return None
    if not (body.get("webSocketDebuggerUrl") or body.get("Browser")):
        return None
    return body


def port_in_use(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        return sock.connect_ex(("127.0.0.1", port)) == 0
    finally:
        sock.close()


def edge_executable() -> Path:
    extra = (os.environ.get("EDGE_EXECUTABLE") or "").strip()
    candidates = []
    if extra:
        candidates.append(Path(extra))
    x86 = os.environ.get("PROGRAMFILES(X86)") or r"C:\Program Files (x86)"
    pf = os.environ.get("PROGRAMFILES") or r"C:\Program Files"
    candidates.extend(
        [
            Path(x86) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            Path(pf) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        ]
    )
    for path in candidates:
        if path.is_file():
            return path
    raise AgentEdgeNotReady("找不到 Microsoft Edge（msedge.exe）")


def ensure_agent_edge(*, wait_seconds: int = WAIT_SECONDS) -> dict:
    """Make sure Agent Edge CDP is listening. Never kills other processes.

    Uses EDGE_CDP_PORT when free/ready. If that port is occupied by a non-CDP
    process (e.g. daily Edge), tries nearby ports so the user never has to
    pick or free a port manually. Daily Edge profile is never used.
    """
    preferred = cdp_port()
    candidates = [preferred] + [p for p in range(preferred + 1, preferred + 9) if p != preferred]

    for port in candidates:
        existing = cdp_ready(port)
        if existing is not None:
            return {
                "status": "already_running",
                "port": port,
                "cdp_url": cdp_url(port),
                "profile": str(agent_profile_dir()),
                "browser": existing.get("Browser"),
            }

        if port_in_use(port):
            # Occupied but not Agent CDP — skip; do not attach to daily Edge.
            continue

        profile = agent_profile_dir()
        profile.mkdir(parents=True, exist_ok=True)
        exe = edge_executable()
        args = [
            str(exe),
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=flags,
        )

        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            ready = cdp_ready(port)
            if ready is not None:
                return {
                    "status": "started",
                    "port": port,
                    "cdp_url": cdp_url(port),
                    "profile": str(profile),
                    "browser": ready.get("Browser"),
                }
            time.sleep(0.4)

        raise AgentEdgeNotReady(
            f"已启动 Agent Edge，但在 {wait_seconds}s 内 {cdp_url(port)} 仍不可用。"
            f" profile={profile}"
        )

    raise AgentEdgeNotReady(
        f"无法为 Agent 招聘浏览器找到可用 CDP 端口（尝试 {candidates[0]}–{candidates[-1]}）。"
        "不会动用你的日常 Edge。请稍后重试。"
    )


def main() -> int:
    try:
        info = ensure_agent_edge()
    except AgentEdgeNotReady as exc:
        print("Agent Edge 未就绪。")
        print(str(exc))
        return 1
    print("Agent Edge 已就绪。")
    print(f"status={info['status']}")
    print(f"cdp_url={info['cdp_url']}")
    print(f"port={info['port']}")
    print(f"profile={info['profile']}")
    if info.get("browser"):
        print(f"browser={info['browser']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
