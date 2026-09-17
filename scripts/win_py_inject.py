"""Execute a small Python snippet inside a target CPython process (Windows).

Finds python3xx.dll in the remote process, resolves PyGILState_Ensure /
PyRun_SimpleString / PyGILState_Release, then CreateRemoteThread.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import sys
import time
from ctypes import wintypes
from pathlib import Path

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)

PROCESS_ALL_ACCESS = 0x1F0FFF
MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000
PAGE_READWRITE = 0x04
PAGE_EXECUTE_READWRITE = 0x40
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
INFINITE = 0xFFFFFFFF


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("th32ModuleID", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("GlcntUsage", wintypes.DWORD),
        ("ProccntUsage", wintypes.DWORD),
        ("modBaseAddr", ctypes.c_void_p),
        ("modBaseSize", wintypes.DWORD),
        ("hModule", wintypes.HMODULE),
        ("szModule", wintypes.WCHAR * 256),
        ("szExePath", wintypes.WCHAR * 260),
    ]


kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.Module32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
kernel32.Module32FirstW.restype = wintypes.BOOL
kernel32.Module32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
kernel32.Module32NextW.restype = wintypes.BOOL
kernel32.VirtualAllocEx.argtypes = [wintypes.HANDLE, wintypes.LPVOID, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD]
kernel32.VirtualAllocEx.restype = ctypes.c_void_p
kernel32.WriteProcessMemory.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    wintypes.LPCVOID,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel32.WriteProcessMemory.restype = wintypes.BOOL
kernel32.CreateRemoteThread.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    ctypes.c_size_t,
    ctypes.c_void_p,
    wintypes.LPVOID,
    wintypes.DWORD,
    wintypes.LPDWORD,
]
kernel32.CreateRemoteThread.restype = wintypes.HANDLE
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD


def _err(msg: str) -> None:
    raise OSError(f"{msg}: {ctypes.get_last_error()}")


def open_process(pid: int):
    handle = kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, pid)
    if not handle:
        _err("OpenProcess")
    return handle


def find_python_module(pid: int) -> tuple[str, int]:
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
    if snap in (0, wintypes.HANDLE(-1).value):
        _err("CreateToolhelp32Snapshot")
    entry = MODULEENTRY32W()
    entry.dwSize = ctypes.sizeof(MODULEENTRY32W)
    if not kernel32.Module32FirstW(snap, ctypes.byref(entry)):
        kernel32.CloseHandle(snap)
        _err("Module32FirstW")
    found = None
    while True:
        name = entry.szModule.lower()
        if name.startswith("python3") and name.endswith(".dll") and "python3.dll" != name:
            found = (entry.szExePath, int(entry.modBaseAddr))
            break
        if not kernel32.Module32NextW(snap, ctypes.byref(entry)):
            break
    kernel32.CloseHandle(snap)
    if not found:
        raise RuntimeError("python3xx.dll not found in target")
    return found


def local_proc_rva(symbol: str) -> int:
    GetProcAddress = kernel32.GetProcAddress
    GetProcAddress.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    GetProcAddress.restype = ctypes.c_void_p
    import sys

    base = sys.dllhandle
    addr = GetProcAddress(base, symbol.encode("ascii"))
    if not addr:
        _err(f"GetProcAddress {symbol}")
    return int(addr) - int(base)


def write_remote(handle, data: bytes) -> int:
    remote = kernel32.VirtualAllocEx(handle, None, len(data) + 16, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE)
    if not remote:
        _err("VirtualAllocEx")
    written = ctypes.c_size_t(0)
    ok = kernel32.WriteProcessMemory(handle, remote, data, len(data), ctypes.byref(written))
    if not ok:
        _err("WriteProcessMemory")
    return int(remote)

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pid", type=int)
    ap.add_argument("--py", required=True, help="UTF-8 python source to run remotely")
    args = ap.parse_args()

    dll_path, remote_base = find_python_module(args.pid)
    print("dll", dll_path)
    print("remote_base", hex(remote_base))

    # Resolve absolute addresses in remote process
    symbols = {}
    for name in ("PyGILState_Ensure", "PyRun_SimpleString", "PyGILState_Release"):
        rva = local_proc_rva(name)
        symbols[name] = remote_base + rva
        print(name, hex(symbols[name]), "rva", hex(rva))
    src = Path(args.py).read_text(encoding="utf-8").encode("utf-8") + b"\0"
    handle = open_process(args.pid)
    try:
        remote_src = write_remote(handle, src)

        # Shellcode: x64 Microsoft ABI
        # rcx/rdx/r8/... 
        # thread start: LPVOID param unused
        ensure = symbols["PyGILState_Ensure"]
        run = symbols["PyRun_SimpleString"]
        release = symbols["PyGILState_Release"]

        # Build a small c helper in memory instead of raw shellcode:
        # Use VirtualAllocEx + write a ctypes callback locally won't run remotely.
        # Use documented approach: CreateRemoteThread(PyRun_SimpleString, remote_src)
        # WARNING: must hold GIL. So call Ensure first via a tiny codecave.

        # Codecave assembly (x64):
        #   sub rsp, 0x28
        #   mov rax, ensure
        #   call rax          ; eax = state
        #   mov ebx, eax
        #   mov rcx, remote_src
        #   mov rax, run
        #   call rax
        #   mov ecx, ebx
        #   mov rax, release
        #   call rax
        #   xor eax,eax
        #   add rsp, 0x28
        #   ret

        def imm64(addr: int) -> bytes:
            return addr.to_bytes(8, "little")

        code = bytearray()
        code += b"\x48\x83\xEC\x28"  # sub rsp, 0x28
        code += b"\x48\xB8" + imm64(ensure)  # mov rax, ensure
        code += b"\xFF\xD0"  # call rax
        code += b"\x89\xC3"  # mov ebx, eax
        code += b"\x48\xB9" + imm64(remote_src)  # mov rcx, src
        code += b"\x48\xB8" + imm64(run)  # mov rax, run
        code += b"\xFF\xD0"  # call rax
        code += b"\x89\xD9"  # mov ecx, ebx
        code += b"\x48\xB8" + imm64(release)  # mov rax, release
        code += b"\xFF\xD0"  # call rax
        code += b"\x33\xC0"  # xor eax,eax
        code += b"\x48\x83\xC4\x28"  # add rsp, 0x28
        code += b"\xC3"  # ret

        remote_code = kernel32.VirtualAllocEx(
            handle, None, len(code), MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE
        )
        if not remote_code:
            _err("VirtualAllocEx code")
        written = ctypes.c_size_t(0)
        if not kernel32.WriteProcessMemory(handle, remote_code, bytes(code), len(code), ctypes.byref(written)):
            _err("WriteProcessMemory code")

        thread = kernel32.CreateRemoteThread(handle, None, 0, remote_code, None, 0, None)
        if not thread:
            _err("CreateRemoteThread")
        kernel32.WaitForSingleObject(thread, 15000)
        kernel32.CloseHandle(thread)
        print("remote exec done")
    finally:
        kernel32.CloseHandle(handle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
