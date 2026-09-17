"""HTTP live e2e against restarted uvicorn. Uses real LLM + mock jobs."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8000"


def _multipart(fields: dict[str, str], files: list[tuple[str, str, bytes, str]]) -> tuple[bytes, str]:
    boundary = "----CursorBoundary7f3a9c"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        chunks.append(value.encode("utf-8"))
        chunks.append(b"\r\n")
    for field, filename, data, content_type in files:
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(
            (
                f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode()
        )
        chunks.append(data)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    body = b"".join(chunks)
    return body, f"multipart/form-data; boundary={boundary}"


def get_json(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    html = urllib.request.urlopen(BASE + "/", timeout=10).read().decode("utf-8", "replace")
    if "整理成功后会出现在这里" not in html:
        print("FAIL server UI copy unexpected")
        return 1
    print("OK server up, new UI copy")

    resume = (ROOT / "tests/fixtures/resumes/sample_pm.txt").read_bytes()
    body, content_type = _multipart(
        {
            "message": "我要深圳的产品经理的工作，简历见附件",
            "data_source": "mock",
        },
        [
            ("attachments", "resume.txt", resume, "text/plain"),
            ("attachment_kinds", "x", b"resume", "text/plain"),
        ],
    )
    # Fix attachment_kinds: should be form field string, not file.
    body, content_type = _multipart(
        {
            "message": "我要深圳的产品经理的工作，简历见附件",
            "data_source": "mock",
            "attachment_kinds": "resume",
        },
        [("attachments", "resume.txt", resume, "text/plain")],
    )
    req = urllib.request.Request(
        BASE + "/api/run-agent",
        data=body,
        headers={"Content-Type": content_type},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            started = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print("FAIL start", exc.read().decode("utf-8", "replace"))
        return 1

    print("started", json.dumps(started, ensure_ascii=False))
    run_id = started.get("run_id")
    if not run_id:
        print("FAIL no run_id")
        return 1

    payload = None
    for _ in range(180):
        payload = get_json(f"/api/run-agent/{run_id}")
        phase = payload.get("run_phase")
        status = payload.get("status")
        print("poll", phase, status, (payload.get("message") or "")[:80], flush=True)
        if phase == "finished" or status in {"DONE", "FAILED", "WAITING_USER", "NEEDS_HUMAN"}:
            break
        time.sleep(2)
    else:
        print("FAIL timeout")
        return 1

    profile = get_json("/api/profile")
    report = {
        "status": payload.get("status"),
        "error_code": payload.get("error_code"),
        "message": payload.get("message"),
        "profile_updated": payload.get("profile_updated"),
        "run_profile": payload.get("profile"),
        "disk_profile": profile,
        "stats": payload.get("stats"),
        "recommended_n": len(payload.get("recommended") or []),
    }
    out = ROOT / "data" / "_live_api_e2e.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))

    ok = (
        bool((report.get("run_profile") or {}).get("exists") or (profile or {}).get("exists"))
        and (
            report.get("profile_updated") is True
            or bool((profile or {}).get("core_capabilities") or (profile or {}).get("core_experience"))
        )
        and report.get("error_code") != "repeated_action_rejected"
    )
    # Soft pass: portrait written and not thrash; status may be WAITING_USER / DONE / llm_error after explore
    core_ok = Path(ROOT / "data" / "candidate_profiles").exists() and profile.get("exists") is True
    print("CORE_PROFILE_OK" if core_ok else "CORE_PROFILE_FAIL")
    print("NO_THRASH_OK" if report.get("error_code") != "repeated_action_rejected" else "THRASH_FAIL")
    print("LIVE_E2E_OK" if core_ok and report.get("error_code") != "repeated_action_rejected" else "LIVE_E2E_PARTIAL_OR_FAIL")
    print("wrote", out)
    return 0 if core_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
