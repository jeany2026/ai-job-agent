"""Dump live ConversationStore + RunStore from a running uvicorn PID (Windows).

Uses Python's remote thread injection via the process's python312.dll PyRun_SimpleString.
Writes JSON to data/_live_run_dump.json.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "_live_run_dump.json"
PID = int(sys.argv[1]) if len(sys.argv) > 1 else 0
if not PID:
    raise SystemExit("usage: dump_live_conversation.py <pid>")

# Payload executed inside the target interpreter.
PAYLOAD = r"""
def __dump_ai_job_agent():
    import json, traceback
    from pathlib import Path
    out = Path(r""" + json.dumps(str(OUT)) + r""")
    try:
        import api.app as app
        cid = "df5c668c-bcca-41f7-85be-0f0949231c8b"
        rid = "3cad9ff0-3bec-462d-8387-b1d1021304b5"
        conv = app.conversation_store._items.get(cid)
        run = app.run_store.get(rid)
        payload = {
            "ok": True,
            "conversation_id": cid,
            "run_id": rid,
            "task_ids": list(conv.task_ids) if conv else [],
            "turns": list(conv.turns) if conv else [],
            "job_contexts": [dict(c) if isinstance(c, dict) else (c.to_dict() if hasattr(c, "to_dict") else c) for c in (conv.job_contexts if conv else [])],
            "run_stats": (run.result or {}).get("stats") if run and run.result else None,
            "run_status": run.status if run else None,
            "run_error_code": run.error_code if run else None,
            "run_message": run.message if run else None,
        }
        # Prefer raw dicts already stored
        contexts = []
        if conv:
            for item in conv.job_contexts:
                if isinstance(item, dict):
                    contexts.append(item)
                elif hasattr(item, "to_dict"):
                    contexts.append(item.to_dict())
                else:
                    contexts.append({"raw": repr(item)[:200]})
        payload["job_contexts"] = contexts
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        out.write_text(json.dumps({"ok": False, "error": str(exc), "trace": traceback.format_exc()}, ensure_ascii=False, indent=2), encoding="utf-8")
__dump_ai_job_agent()
"""

# Prefer a simpler cross-process approach: write payload file for manual exec.
# On Windows without a stable injector, fall back to documenting failure.
# Try `inject` package patterns via kernel32.

import ctypes
from ctypes import wintypes

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

PROCESS_ALL_ACCESS = 0x1F0FFF
MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000
PAGE_EXECUTE_READWRITE = 0x40
PAGE_READWRITE = 0x04

OpenProcess = kernel32.OpenProcess
OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
OpenProcess.restype = wintypes.HANDLE

VirtualAllocEx = kernel32.VirtualAllocEx
VirtualAllocEx.argtypes = [wintypes.HANDLE, wintypes.LPVOID, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD]
VirtualAllocEx.restype = wintypes.LPVOID

WriteProcessMemory = kernel32.WriteProcessMemory
WriteProcessMemory.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.LPCVOID, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
WriteProcessMemory.restype = wintypes.BOOL

CreateRemoteThread = kernel32.CreateRemoteThread
CreateRemoteThread.argtypes = [wintypes.HANDLE, wintypes.LPVOID, ctypes.c_size_t, wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD, wintypes.LPDWORD]
CreateRemoteThread.restype = wintypes.HANDLE

GetModuleHandleA = kernel32.GetModuleHandleA
GetModuleHandleA.argtypes = [wintypes.LPCSTR]
GetModuleHandleA.restype = wintypes.HMODULE

GetProcAddress = kernel32.GetProcAddress
GetProcAddress.argtypes = [wintypes.HMODULE, wintypes.LPCSTR]
GetProcAddress.restype = wintypes.LPVOID

CloseHandle = kernel32.CloseHandle

# This injector approach for PyRun_SimpleString across processes is fragile on Windows
# when ASLR differs. Instead: write a dump-request file and use a live HTTP monkey
# by starting a one-shot cooperative dump via existing server if we can register a route
# without restart — not possible.
#
# Cooperative dump: touch a file that we ask the user... 
# Better: use named pipe approach by patching through a polling thread we add AFTER restart.
#
# For this session: use `python -c` attaching via debugpy remote if enabled.
# Fallback: confirm from code + stats, and dump conversation by adding endpoint THEN
# calling it BEFORE kill — requires hot add.
#
# Hot-add routes on Starlette/FastAPI IS possible if we can execute code in-process.
# Try loading python312.dll from the SAME path as the target process.

print("Attempting cooperative dump via debug file poll is not in app.")
print("Writing payload for reference and exiting with guidance.")
(ROOT / "data" / "_dump_payload.py").write_text(PAYLOAD, encoding="utf-8")
print("wrote", ROOT / "data" / "_dump_payload.py")
print("NEED_INPROCESS_EXEC")
raise SystemExit(2)
