# -*- coding: utf-8 -*-
"""实机看公司列表（开发工具，只读）：Express 停在公司列表时，把那个窗口的控件（类名、控件号、位置、文字）和列表里每一行
（每一列的字）记下来，给「按账套路径选公司」写配方用（评估 §16）。不打字、不点、不选中任何一行。

Express 是 32 位 ANSI 程序：文字用 A 版消息取、按 cp874 解；列表行用 32 位的 LVITEMA 结构、缓冲区借在 Express 自己的进程里
（读完马上释放，同 win32_desk.status_text）。

    python lab_company_dump.py --out C:\\XpPilot\\lab\\company-list.json
"""
from __future__ import annotations

import argparse
import ctypes
import json
import struct
import sys
from ctypes import wintypes as W

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
EnumProc = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
user32.EnumWindows.argtypes = [ctypes.c_void_p, W.LPARAM]
user32.EnumChildWindows.argtypes = [W.HWND, ctypes.c_void_p, W.LPARAM]
user32.SendMessageTimeoutA.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM, W.UINT, W.UINT, ctypes.POINTER(ctypes.c_size_t)]
user32.GetClassNameW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [W.HWND, ctypes.POINTER(W.RECT)]
user32.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
user32.IsWindowVisible.argtypes = [W.HWND]
user32.GetDlgCtrlID.argtypes = [W.HWND]
user32.GetWindowLongW.argtypes = [W.HWND, ctypes.c_int]
kernel32.OpenProcess.restype = W.HANDLE
kernel32.VirtualAllocEx.argtypes = [W.HANDLE, ctypes.c_void_p, ctypes.c_size_t, W.DWORD, W.DWORD]
kernel32.VirtualAllocEx.restype = ctypes.c_void_p
kernel32.VirtualFreeEx.argtypes = [W.HANDLE, ctypes.c_void_p, ctypes.c_size_t, W.DWORD]
kernel32.WriteProcessMemory.argtypes = [W.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
kernel32.ReadProcessMemory.argtypes = [W.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
kernel32.CloseHandle.argtypes = [W.HANDLE]

LVM_GETITEMCOUNT, LVM_GETITEMTEXTA, LVM_GETHEADER = 0x1004, 0x102D, 0x101F
HDM_GETITEMCOUNT = 0x1200
WM_GETTEXT = 0x000D
ROWS_MAX, COLUMNS_MAX = 300, 8


def send(hwnd, msg, wparam=0, lparam=0, timeout=1000) -> int:
    result = ctypes.c_size_t()
    user32.SendMessageTimeoutA(hwnd, msg, wparam, lparam, 0x2, timeout, ctypes.byref(result))
    return int(result.value)


def cls(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(hwnd, buf, 128)
    return buf.value


def text(hwnd) -> str:
    buf = ctypes.create_string_buffer(512)
    send(hwnd, WM_GETTEXT, 512, ctypes.addressof(buf))
    return buf.value.decode("cp874", "replace")


def rect(hwnd) -> list:
    r = W.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return [r.left, r.top, r.right, r.bottom]


def pid_of(hwnd) -> int:
    pid = W.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def children(hwnd) -> list:
    found = []
    proc = EnumProc(lambda h, _l: found.append(h) or True)
    user32.EnumChildWindows(hwnd, ctypes.cast(proc, ctypes.c_void_p), 0)
    return found


def express_windows() -> list:
    """可见的、属于 ExpressI 进程的顶层窗口。"""
    import subprocess

    listed = subprocess.run(["tasklist", "/FI", "IMAGENAME eq ExpressI.exe", "/FO", "CSV", "/NH"],
                            capture_output=True, text=True).stdout
    pids = {int(line.split(",")[1].strip('"')) for line in listed.splitlines() if line.startswith('"ExpressI')}
    found = []
    proc = EnumProc(lambda h, _l: (found.append(h) if user32.IsWindowVisible(h) and pid_of(h) in pids else None) or True)
    user32.EnumWindows(ctypes.cast(proc, ctypes.c_void_p), 0)
    return found


def list_rows(hwnd) -> dict:
    """SysListView32 的每一行每一列（32 位 LVITEMA，缓冲区在 Express 进程里）。"""
    count = min(send(hwnd, LVM_GETITEMCOUNT), ROWS_MAX)
    header = send(hwnd, LVM_GETHEADER)
    columns = min(send(header, HDM_GETITEMCOUNT) if header else 1, COLUMNS_MAX) or 1
    process = kernel32.OpenProcess(0x0008 | 0x0010 | 0x0020, False, pid_of(hwnd))
    if not process:
        return {"count": count, "error": "open process failed"}
    remote = kernel32.VirtualAllocEx(process, None, 4096, 0x1000 | 0x2000, 0x04)
    rows = []
    try:
        text_at = remote + 256
        for row in range(count):
            cells = []
            for column in range(columns):
                # LVITEMA（32 位）：mask, iItem, iSubItem, state, stateMask, pszText, cchTextMax, ...
                item = struct.pack("<IiiIIIi", 0x1, row, column, 0, 0, text_at, 512) + b"\0" * 24
                written = ctypes.c_size_t()
                kernel32.WriteProcessMemory(process, remote, item, len(item), ctypes.byref(written))
                send(hwnd, LVM_GETITEMTEXTA, row, remote)
                buf = ctypes.create_string_buffer(512)
                read = ctypes.c_size_t()
                kernel32.ReadProcessMemory(process, text_at, buf, 512, ctypes.byref(read))
                cells.append(buf.raw.split(b"\0", 1)[0].decode("cp874", "replace"))
            rows.append(cells)
    finally:
        kernel32.VirtualFreeEx(process, remote, 0, 0x8000)
        kernel32.CloseHandle(process)
    return {"count": count, "columns": columns, "rows": rows}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    out = []
    for hwnd in express_windows():
        controls = []
        for child in children(hwnd):
            if not user32.IsWindowVisible(child):
                continue
            kind = cls(child)
            password = kind == "Edit" and bool(user32.GetWindowLongW(child, -16) & 0x20)
            row = {"cls": kind, "id": int(user32.GetDlgCtrlID(child)), "rect": rect(child),
                   "text": "" if password else text(child)[:200]}
            if kind == "SysListView32":
                row["list"] = list_rows(child)
            controls.append(row)
        out.append({"title": text(hwnd), "cls": cls(hwnd), "rect": rect(hwnd), "controls": controls})
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(len(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
