"""测试机上的执行代理：在已登录用户（skin3）的交互会话里用 pythonw 隐藏运行，只听 127.0.0.1。

本机执行器经 ssh 端口转发连过来，一次发一批动作（POST /act），代理按顺序做、遇错即停，回每个动作的结果和当前 Express 窗口清单。
只用标准库。规矩（写死在代码里，不靠调用方自觉）：
- 每次按键 / 点击前核前台进程是 ExpressI.exe；点击还要核落点所在的顶层窗口也属于 ExpressI，否则拒绝（不把键打进别的程序）；
- 读账套只许读白名单（试点账套 \\\\accserver\\d$\\account\\69EXP\\test，和工作目录 copies 下的副本），先拷到临时目录再读；
- 启动程序只许白名单里的服务器版 Express；
- 不收、不存、不记密码：自动登录只调 cc_login（从 Windows 凭据管理器取、直接填进 Express 登录框），回结果只有成败。

    pythonw -m pilot.agent_win            （在工作目录下跑，如 C:\\XpPilot；令牌在 agent.token）
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import shutil
import struct
import sys
import tempfile
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Dict, List, Optional

from . import dbfread

WORK = Path(__file__).resolve().parent.parent  # 代码放在 <工作目录>\pilot\，令牌、日志、副本都在工作目录
PORT = int(os.environ.get("XP_PILOT_PORT", "18765"))
PROCESS = "expressi.exe"
# 试点账套（主会话 10-26 指定），只读。同一个目录两种写法：普通共享 ACCOUNT（rec-laptop 的 aksar 映射的就是它）和 d$ 管理共享
BOOKS = [r"\\accserver\account\69exp\test", r"\\accserver\d$\account\69exp\test"]
COPY_ROOT = str(WORK / "copies").lower()  # 第 ④ 种方式的本机副本
# 服务器版 Express。公司列表（secure\SCCOMP）按程序目录走，试点公司「9.บริษัท Pearnly จำกัด」在 69EXP 那套的列表里（路径 TEST = 69EXP\test），
# 70EXP 那套列表里的 TEST 是验收在用的 70EXP\test，不能开。两套是同一个程序文件（10-26 核对）。
EXES = [r"\\accserver\account\69exp\expressi.exe", r"\\accserver\d$\account\69exp\expressi.exe"]
BOOK_TABLES = ("APTRN", "ARTRN", "STCRD", "ISVAT", "GLJNL", "GLJNLIT", "ISRUN", "APMAS", "ARMAS", "STMAS", "STLOC")
SHOT_SIZE = (1280, 720)  # 截图缩到这么大再给模型（1920×1080 屏），坐标由调用方按比例换回

IS_WIN = sys.platform == "win32"
LOG = WORK / "agent-log.txt"


def log(message: str) -> None:
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {message}\n")
    except OSError:
        pass


class Refused(Exception):
    """守卫拒绝（前台不是 Express、落点不在 Express、路径不在白名单等）。"""


# ── Win32 ───────────────────────────────────────────────────────────────────
if IS_WIN:
    from ctypes import wintypes as W

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    HWND, HDC, HANDLE = W.HWND, W.HDC, W.HANDLE
    user32.SetProcessDPIAware.restype = W.BOOL
    user32.GetForegroundWindow.restype = HWND
    user32.GetWindowThreadProcessId.argtypes = [HWND, ctypes.POINTER(W.DWORD)]
    user32.GetWindowThreadProcessId.restype = W.DWORD
    user32.IsWindowVisible.argtypes = [HWND]
    user32.GetWindowTextW.argtypes = [HWND, W.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.argtypes = [HWND, W.LPWSTR, ctypes.c_int]
    user32.GetWindowRect.argtypes = [HWND, ctypes.POINTER(W.RECT)]
    user32.GetWindow.argtypes = [HWND, W.UINT]
    user32.GetWindow.restype = HWND
    user32.WindowFromPoint.argtypes = [W.POINT]
    user32.WindowFromPoint.restype = HWND
    user32.GetAncestor.argtypes = [HWND, W.UINT]
    user32.GetAncestor.restype = HWND
    user32.SetForegroundWindow.argtypes = [HWND]
    user32.ShowWindow.argtypes = [HWND, ctypes.c_int]
    user32.IsIconic.argtypes = [HWND]
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user32.GetCursorPos.argtypes = [ctypes.POINTER(W.POINT)]
    user32.VkKeyScanExW.argtypes = [W.WCHAR, W.HKL]
    user32.VkKeyScanExW.restype = ctypes.c_short
    user32.LoadKeyboardLayoutW.argtypes = [W.LPCWSTR, W.UINT]
    user32.LoadKeyboardLayoutW.restype = W.HKL
    user32.PostMessageW.argtypes = [HWND, W.UINT, W.WPARAM, W.LPARAM]
    user32.SendMessageTimeoutW.argtypes = [HWND, W.UINT, W.WPARAM, W.LPARAM, W.UINT, W.UINT,
                                           ctypes.POINTER(ctypes.c_size_t)]
    user32.GetDC.argtypes = [HWND]
    user32.GetDC.restype = HDC
    user32.ReleaseDC.argtypes = [HWND, HDC]
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.OpenClipboard.argtypes = [HWND]
    user32.SetClipboardData.argtypes = [W.UINT, HANDLE]
    user32.SetClipboardData.restype = HANDLE
    kernel32.GlobalAlloc.argtypes = [W.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = HANDLE
    kernel32.GlobalLock.argtypes = [HANDLE]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [HANDLE]
    kernel32.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    kernel32.OpenProcess.restype = HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [HANDLE, W.DWORD, W.LPWSTR, ctypes.POINTER(W.DWORD)]
    kernel32.CloseHandle.argtypes = [HANDLE]
    gdi32.CreateCompatibleDC.argtypes = [HDC]
    gdi32.CreateCompatibleDC.restype = HDC
    gdi32.CreateCompatibleBitmap.argtypes = [HDC, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = HANDLE
    gdi32.SelectObject.argtypes = [HDC, HANDLE]
    gdi32.SelectObject.restype = HANDLE
    gdi32.SetStretchBltMode.argtypes = [HDC, ctypes.c_int]
    gdi32.SetBrushOrgEx.argtypes = [HDC, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    gdi32.StretchBlt.argtypes = [HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, HDC, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, ctypes.c_int, W.DWORD]
    gdi32.GetDIBits.argtypes = [HDC, HANDLE, W.UINT, W.UINT, ctypes.c_void_p, ctypes.c_void_p, W.UINT]
    gdi32.DeleteObject.argtypes = [HANDLE]
    gdi32.DeleteDC.argtypes = [HDC]

    ULONG_PTR = ctypes.c_size_t

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", W.LONG), ("dy", W.LONG), ("mouseData", W.DWORD), ("dwFlags", W.DWORD),
                    ("time", W.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", W.WORD), ("wScan", W.WORD), ("dwFlags", W.DWORD), ("time", W.DWORD),
                    ("dwExtraInfo", ULONG_PTR)]

    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", W.DWORD), ("u", _U)]

    user32.SendInput.argtypes = [W.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.GetGUIThreadInfo.argtypes = [W.DWORD, ctypes.c_void_p]

    EnumProc = ctypes.WINFUNCTYPE(W.BOOL, HWND, W.LPARAM)
    user32.EnumWindows.argtypes = [EnumProc, W.LPARAM]
    user32.EnumChildWindows.argtypes = [HWND, EnumProc, W.LPARAM]

KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x2, 0x4
MOUSE = {"left": (0x2, 0x4), "right": (0x8, 0x10), "middle": (0x20, 0x40)}
VK = {"ENTER": 0x0D, "RETURN": 0x0D, "TAB": 0x09, "ESC": 0x1B, "ESCAPE": 0x1B, "HOME": 0x24, "END": 0x23,
      "INS": 0x2D, "INSERT": 0x2D, "DEL": 0x2E, "DELETE": 0x2E, "BS": 0x08, "BACKSPACE": 0x08, "BKSP": 0x08,
      "UP": 0x26, "DOWN": 0x28, "LEFT": 0x25, "RIGHT": 0x27, "PGUP": 0x21, "PAGE_UP": 0x21, "PRIOR": 0x21,
      "PGDN": 0x22, "PAGE_DOWN": 0x22, "NEXT": 0x22, "SPACE": 0x20, "SHIFT": 0x10, "CTRL": 0x11, "CONTROL": 0x11,
      "ALT": 0x12, "SUPER": 0x5B, "WIN": 0x5B, "CMD": 0x5B}
VK.update({f"F{i}": 0x6F + i for i in range(1, 13)})
US_LAYOUT, TH_LAYOUT = "00000409", "0000041E"


def _exe_of(hwnd) -> str:
    pid = W.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(520)
        size = W.DWORD(520)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _title(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


def _cls(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(hwnd, buf, 128)
    return buf.value


def _rect(hwnd) -> List[int]:
    r = W.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return [r.left, r.top, r.right, r.bottom]


def _gettext(hwnd) -> str:
    """跨进程读控件文字要用 WM_GETTEXT（GetWindowText 读不到别的进程的子控件）。"""
    buf = ctypes.create_unicode_buffer(400)
    res = ctypes.c_size_t()
    user32.SendMessageTimeoutW(hwnd, 0x000D, 400, ctypes.addressof(buf), 0x2, 200, ctypes.byref(res))
    return buf.value


def is_express(hwnd) -> bool:
    return bool(hwnd) and os.path.basename(_exe_of(hwnd)).lower() == PROCESS


def windows() -> List[Dict]:
    """ExpressI 的全部可见顶层窗口（标题、类名、位置、子控件文字前 40 个）。弹窗认得出认不出，就看这里有没有字。"""
    out: List[Dict] = []

    def top(hwnd, _):
        if user32.IsWindowVisible(hwnd) and is_express(hwnd):
            texts: List[str] = []

            def child(ch, _):
                if len(texts) < 40 and user32.IsWindowVisible(ch):
                    text = _gettext(ch).strip()
                    if text:
                        texts.append(text[:200])
                return True

            user32.EnumChildWindows(hwnd, EnumProc(child), 0)
            owner = user32.GetWindow(hwnd, 4)  # GW_OWNER
            out.append({"hwnd": int(hwnd), "title": _title(hwnd), "cls": _cls(hwnd), "rect": _rect(hwnd),
                        "owned": bool(owner), "texts": texts})
        return True

    user32.EnumWindows(EnumProc(top), 0)
    return out


def foreground() -> Dict:
    hwnd = user32.GetForegroundWindow()
    return {"hwnd": int(hwnd or 0), "exe": os.path.basename(_exe_of(hwnd)) if hwnd else "", "title": _title(hwnd)}


def guard() -> None:
    fg = foreground()
    if fg["exe"].lower() != PROCESS:
        raise Refused(f"前台不是 Express（是 {fg['exe']} 「{fg['title']}」），已拒绝按键 / 点击")


def guard_point(x: int, y: int) -> None:
    guard()
    root = user32.GetAncestor(user32.WindowFromPoint(W.POINT(int(x), int(y))), 2)  # GA_ROOT
    if not is_express(root):
        raise Refused(f"落点 ({x},{y}) 不在 Express 窗口上，已拒绝")


def _focus_info() -> Dict:
    """第 2 层（Win32，不截图）：前台线程的焦点控件、光标位置。VFP 表单里的格子多半不是独立窗口，读不到就回空。"""
    class GUITHREADINFO(ctypes.Structure):
        _fields_ = [("cbSize", W.DWORD), ("flags", W.DWORD), ("hwndActive", HWND), ("hwndFocus", HWND),
                    ("hwndCapture", HWND), ("hwndMenuOwner", HWND), ("hwndMoveSize", HWND), ("hwndCaret", HWND),
                    ("rcCaret", W.RECT)]

    fg = user32.GetForegroundWindow()
    tid = user32.GetWindowThreadProcessId(fg, None)
    info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
    if not user32.GetGUIThreadInfo(tid, ctypes.byref(info)):
        return {}
    focus = info.hwndFocus
    out = {"hwnd": int(focus or 0), "cls": _cls(focus) if focus else "", "text": _gettext(focus) if focus else "",
           "rect": _rect(focus) if focus else None,
           "caret": [info.rcCaret.left, info.rcCaret.top] if info.hwndCaret else None,
           "is_fg": bool(focus) and int(focus) == int(fg or 0)}
    return out


def _uia_focus() -> Dict:
    """第 2 层（UI Automation，经 pywinauto uia 后端）：焦点元素的名称 / 值 / 类型，另读 MSAA 旧属性（VFP 只可能从这里露值）。
    没装 pywinauto 就回空。"""
    try:
        from pywinauto.controls.uiawrapper import UIAWrapper
        from pywinauto.uia_defines import IUIA
        from pywinauto.uia_element_info import UIAElementInfo
    except Exception:  # noqa: BLE001
        return {}
    try:
        info = UIAElementInfo(IUIA().iuia.GetFocusedElement())
        out = {"name": info.name or "", "type": info.control_type or "", "cls": info.class_name or "",
               "pid": info.process_id}
        try:
            legacy = UIAWrapper(info).legacy_properties()
            out.update({"value": str(legacy.get("Value") or ""), "legacy_name": str(legacy.get("Name") or ""),
                        "role": legacy.get("Role")})
        except Exception:  # noqa: BLE001
            out["value"] = ""
        return out
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"[:200]}


def state() -> Dict:
    """一次读全第 2 层能读到的：前台、Express 顶层窗口和文字、焦点控件（Win32 与 UIA）。"""
    return {"fg": foreground(), "windows": windows(), "focus": _focus_info(), "uia": _uia_focus()}


def main_window():
    best, area = None, -1
    for w in windows():
        left, top_, right, bottom = w["rect"]
        a = (right - left) * (bottom - top_)
        if not w["owned"] and a > area:
            best, area = w, a
    return best


def activate() -> Dict:
    w = main_window()
    if not w:
        raise Refused("没有 Express 窗口")
    hwnd = HWND(w["hwnd"])
    if foreground()["exe"].lower() == PROCESS and not user32.IsIconic(hwnd):
        return {"title": w["title"], "fg": foreground(), "already": True}  # 已在前台：不按 Alt（Alt 会收起展开的菜单）
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)
    _key_event(VK["ALT"], False)  # 按一下 Alt 才允许本进程把别的窗口拉到前台
    _key_event(VK["ALT"], True)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.25)
    return {"title": w["title"], "fg": foreground()}


def maximize_main() -> Dict:
    w = main_window()
    if not w:
        raise Refused("没有 Express 窗口")
    user32.ShowWindow(HWND(w["hwnd"]), 3)  # SW_MAXIMIZE
    return {"rect": _rect(HWND(w["hwnd"]))}


# ── 键盘 ────────────────────────────────────────────────────────────────────
def _send(inputs: List) -> None:
    arr = (INPUT * len(inputs))(*inputs)
    user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


# 这些键要带「扩展键」标记，否则 Windows 当成小键盘键：NumLock 开着时 Home 打出 7、Insert 打出 0（10-26 实测踩到）
EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E}


def _key_event(vk: int, up: bool) -> None:
    flags = (KEYEVENTF_KEYUP if up else 0) | (0x1 if vk in EXTENDED else 0)
    _send([INPUT(1, _U(ki=KEYBDINPUT(vk, 0, flags, 0, 0)))])


def _tap(vk: int, mods=()) -> None:
    for m in mods:
        _key_event(m, False)
    _key_event(vk, False)
    _key_event(vk, True)
    for m in reversed(mods):
        _key_event(m, True)
    time.sleep(0.02)


_HKL: Dict[str, int] = {}


def _hkl(layout: str):
    if layout not in _HKL:
        _HKL[layout] = user32.LoadKeyboardLayoutW(layout, 0)
    return _HKL[layout]


def _char(ch: str, mods=()) -> None:
    code = user32.VkKeyScanExW(ch, _hkl(US_LAYOUT))
    if code == -1:
        raise Refused(f"打不出字符 {ch!r}（非英文请走 paste）")
    vk, shift = code & 0xFF, (code >> 8) & 0xFF
    extra = [VK["SHIFT"]] if shift & 1 else []
    extra += [VK["CTRL"]] if shift & 2 else []
    extra += [VK["ALT"]] if shift & 4 else []
    _tap(vk, list(mods) + extra)


def type_ascii(text: str) -> None:
    for ch in text:
        if ord(ch) > 126:
            raise Refused("type 只收英文 / 数字，泰文走 paste")
        if ch == "\n":
            _tap(VK["ENTER"])
        elif ch == "\t":
            _tap(VK["TAB"])
        else:
            _char(ch)


def sendkeys(spec: str) -> None:
    """.NET SendKeys 的子集：+ Shift、^ Ctrl、% Alt 作用于下一个键；{ENTER}、{TAB 3}、{F7}；{+} {%} {^} {{} {}} 原样字符。"""
    i, mods = 0, []
    names = {"+": VK["SHIFT"], "^": VK["CTRL"], "%": VK["ALT"]}
    while i < len(spec):
        ch = spec[i]
        if ch in names:
            mods.append(names[ch])
            i += 1
            continue
        if ch == "{":
            end = spec.index("}", i + 2) if spec[i + 1] == "}" else spec.index("}", i)
            body = spec[i + 1:end]
            i = end + 1
            parts = body.split(" ")
            name, times = parts[0], int(parts[1]) if len(parts) > 1 else 1
            for _ in range(times):
                if name.upper() in VK:
                    _tap(VK[name.upper()], mods)
                elif len(name) == 1:
                    _char(name, mods)
                else:
                    raise Refused(f"不认识的键 {{{name}}}")
        else:
            _char(ch, mods)
            i += 1
        mods = []


def key_combo(text: str, repeat: int = 1) -> None:
    """电脑操作工具集的 key 写法：Return、Tab、alt+1、ctrl+shift+End、F7……"""
    parts = [p for p in text.replace(" ", "+").split("+") if p]
    *mods, last = parts or [""]
    mod_vks = [VK[m.upper()] for m in mods]
    for _ in range(max(1, min(int(repeat), 100))):
        if last.upper() in VK:
            _tap(VK[last.upper()], mod_vks)
        elif len(last) == 1:
            _char(last.lower() if mod_vks else last, mod_vks)
        else:
            raise Refused(f"不认识的键 {text}")


def _clip_set(text: str) -> None:
    for _ in range(10):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        raise Refused("剪贴板被占用")
    try:
        user32.EmptyClipboard()
        for fmt, data in ((13, text.encode("utf-16-le") + b"\0\0"),  # CF_UNICODETEXT
                          (1, text.encode("cp874", "replace") + b"\0"),  # CF_TEXT：Express 不是 Unicode 程序
                          (16, struct.pack("<I", 0x041E))):  # CF_LOCALE 泰文
            handle = kernel32.GlobalAlloc(0x0042, len(data))
            ptr = kernel32.GlobalLock(handle)
            ctypes.memmove(ptr, data, len(data))
            kernel32.GlobalUnlock(handle)
            user32.SetClipboardData(fmt, handle)
    finally:
        user32.CloseClipboard()


def paste(text: str) -> None:
    """泰文：放剪贴板再按 Shift+Insert（Express 里 Ctrl+V 无效，直接发键会变问号）。"""
    user32.LoadKeyboardLayoutW(TH_LAYOUT, 1)
    _clip_set(text)
    time.sleep(0.15)
    _tap(VK["INSERT"], [VK["SHIFT"]])


def type_any(text: str) -> None:
    if all(ord(c) <= 126 for c in text):
        type_ascii(text)
    else:
        paste(text)


def ensure_us_layout() -> None:
    """把 Express 窗口的输入法切成英文，再发英文 / 数字键（泰文键盘下数字行会打出泰文字母）。"""
    guard()
    user32.PostMessageW(user32.GetForegroundWindow(), 0x0050, 0, _hkl(US_LAYOUT))
    time.sleep(0.1)


# ── 鼠标 ────────────────────────────────────────────────────────────────────
def _mouse(flags: int, data: int = 0) -> None:
    _send([INPUT(0, _U(mi=MOUSEINPUT(0, 0, data & 0xFFFFFFFF, flags, 0, 0)))])


def click(x: int, y: int, button: str = "left", count: int = 1, mods: str = "") -> None:
    guard_point(x, y)
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.08)
    mod_vks = [VK[m.upper()] for m in mods.split("+") if m]
    for m in mod_vks:
        _key_event(m, False)
    down, up = MOUSE[button]
    for _ in range(count):
        _mouse(down)
        _mouse(up)
        time.sleep(0.06)
    for m in reversed(mod_vks):
        _key_event(m, True)


def drag(x0, y0, x1, y1) -> None:
    guard_point(x0, y0)
    guard_point(x1, y1)
    user32.SetCursorPos(int(x0), int(y0))
    _mouse(0x2)
    time.sleep(0.1)
    user32.SetCursorPos(int(x1), int(y1))
    time.sleep(0.1)
    _mouse(0x4)


def scroll(x, y, direction: str, amount: int) -> None:
    guard_point(x, y)
    user32.SetCursorPos(int(x), int(y))
    notch = 120 * max(1, int(amount))
    if direction in ("up", "down"):
        _mouse(0x0800, notch if direction == "up" else -notch)
    else:
        _mouse(0x1000, notch if direction == "right" else -notch)


def cursor() -> List[int]:
    p = W.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return [p.x, p.y]


# ── 截图（GDI 缩放后编成 PNG，纯标准库）────────────────────────────────────
def screen_size() -> List[int]:
    return [user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)]


def _png(width: int, height: int, rgb_rows: List[bytes]) -> bytes:
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"".join(b"\0" + row for row in rgb_rows)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def grab(region: List[int], out_w: int, out_h: int) -> bytes:
    x0, y0, x1, y1 = region
    src = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(src)
    bmp = gdi32.CreateCompatibleBitmap(src, out_w, out_h)
    old = gdi32.SelectObject(mem, bmp)
    try:
        gdi32.SetStretchBltMode(mem, 4)  # HALFTONE，缩小后字还看得清
        gdi32.SetBrushOrgEx(mem, 0, 0, None)
        gdi32.StretchBlt(mem, 0, 0, out_w, out_h, src, x0, y0, x1 - x0, y1 - y0, 0x00CC0020)
        stride = (out_w * 3 + 3) & ~3
        info = struct.pack("<IiiHHIIiiII", 40, out_w, -out_h, 1, 24, 0, 0, 0, 0, 0, 0)  # 自上而下
        info_buf = ctypes.create_string_buffer(info, 44)
        pixels = ctypes.create_string_buffer(stride * out_h)
        gdi32.GetDIBits(mem, bmp, 0, out_h, pixels, info_buf, 0)
        data = pixels.raw
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(None, src)
    rows = []
    for y in range(out_h):
        bgr = data[y * stride: y * stride + out_w * 3]
        rgb = bytearray(len(bgr))
        rgb[0::3], rgb[1::3], rgb[2::3] = bgr[2::3], bgr[1::3], bgr[0::3]
        rows.append(bytes(rgb))
    return _png(out_w, out_h, rows)


def screenshot(size=SHOT_SIZE) -> Dict:
    w, h = screen_size()
    out_w, out_h = (int(size[0]), int(size[1])) if size else (w, h)
    png = grab([0, 0, w, h], out_w, out_h)
    return {"png": base64.b64encode(png).decode(), "screen": [w, h], "size": [out_w, out_h]}


def zoom(region: List[int], long_edge: int = 1280) -> Dict:
    x0, y0, x1, y1 = [int(v) for v in region]
    if x1 <= x0 or y1 <= y0:
        raise Refused("zoom 区域不对")
    scale = min(1.0 * long_edge / max(x1 - x0, y1 - y0), 3.0)
    out_w, out_h = max(1, int((x1 - x0) * scale)), max(1, int((y1 - y0) * scale))
    return {"png": base64.b64encode(grab([x0, y0, x1, y1], out_w, out_h)).decode(), "size": [out_w, out_h]}


# ── 账套（只读）──────────────────────────────────────────────────────────────
def _book(path: str) -> Path:
    norm = os.path.normpath(path).lower().rstrip("\\")
    if norm in BOOKS or norm.startswith(COPY_ROOT + "\\"):
        return Path(path)
    raise Refused(f"账套 {path} 不在白名单")


def book_counts(book: str, tables=BOOK_TABLES) -> Dict[str, int]:
    root = _book(book)
    out = {}
    for name in tables:
        p = root / f"{name}.DBF"
        if p.exists():
            with p.open("rb") as f:  # 只读头 8 字节拿行数，不拷整表
                out[name] = dbfread.count(f.read(8))
    return out


def _json_safe(row: Dict) -> Dict:
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()}


def book_rows(book: str, since: Dict[str, int], tables=None) -> Dict[str, List[Dict]]:
    root = _book(book)
    tmp = Path(tempfile.mkdtemp(prefix="xp_pilot_"))
    out: Dict[str, List[Dict]] = {}
    try:
        for name in tables or list(since):
            p = root / f"{name}.DBF"
            if not p.exists():
                continue
            q = tmp / p.name
            shutil.copyfile(p, q)  # 共享读，Express 开着也能拷
            out[name] = [_json_safe(r) for r in dbfread.rows(q.read_bytes(), since.get(name, 0))]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def _toolbar():
    from pywinauto import Desktop
    main = main_window()
    if not main:
        raise Refused("没有 Express 窗口")
    bars = [b for b in Desktop(backend="win32").window(handle=main["hwnd"]).wrapper_object().descendants(
        class_name="ToolbarWindow32") if b.is_visible()]
    if not bars:
        raise Refused("看不到工具栏")
    return max(bars, key=lambda b: b.button_count())  # 开着几张单据窗口时有几条，取看得见、按钮最多的那条


def _tb_screen_rect(tb, b) -> List[int]:
    r = b.rectangle()  # 工具栏内部坐标 → 屏幕坐标
    pt = W.POINT(0, 0)
    user32.ClientToScreen(HWND(tb.handle), ctypes.byref(pt))
    return [r.left + pt.x, r.top + pt.y, r.right + pt.x, r.bottom + pt.y]


def toolbar() -> Dict:
    """工具栏按钮清单（第 2 层：按钮位置、能不能点、提示文字都由系统接口现取，不写死坐标）。"""
    tb = _toolbar()
    tips = []
    try:
        tips = tb.tip_texts()
    except Exception:  # noqa: BLE001
        pass
    out = []
    for i in range(tb.button_count()):
        b = tb.button(i)
        r = _tb_screen_rect(tb, b)
        out.append({"i": i, "rect": r, "enabled": bool(b.is_enabled()),
                    "text": b.info.text or "", "cmd": int(b.info.idCommand),
                    "sep": bool(b.info.fsStyle & 1)})
    return {"buttons": out, "tips": tips}


def command(cmd: int) -> Dict:
    """工具栏 / 菜单命令：给 Express 主窗口发 WM_COMMAND（和点那个按钮一样走 Express 自己的命令处理，不改程序、不靠坐标）。
    先核前台是 Express，且这个命令在工具栏上现在是可点的（灰的不发）。"""
    guard()
    tb = _toolbar()
    hits = [tb.button(i) for i in range(tb.button_count()) if int(tb.button(i).info.idCommand) == int(cmd)]
    if hits and not hits[0].is_enabled():
        raise Refused(f"命令 {cmd} 现在是灰的")
    main = main_window()
    user32.PostMessageW(HWND(main["hwnd"]), 0x0111, int(cmd), 0)
    return {"cmd": int(cmd)}


def toolbar_click(index: Optional[int] = None, cmd: Optional[int] = None) -> Dict:
    """按坐标点工具栏按钮。注意：显示缩放不是 100% 时坐标会偏（10-26 rec-laptop 150% 实测点偏），优先用 command。"""
    tb = _toolbar()
    if cmd is not None:
        found = [i for i in range(tb.button_count()) if int(tb.button(i).info.idCommand) == int(cmd)]
        if not found:
            raise Refused(f"工具栏上没有命令 {cmd}")
        index = found[0]
    b = tb.button(int(index))
    if not b.is_enabled():
        raise Refused(f"工具栏第 {index} 个按钮现在点不了")
    left, top, right, bottom = _tb_screen_rect(_toolbar(), b)
    x, y = (left + right) // 2, (top + bottom) // 2
    click(x, y)
    return {"xy": [x, y]}


def active_form():
    """当前激活的单据窗口（MDI 子窗口）：开着几张单据窗口时只看这一张。"""
    main = main_window()
    if not main:
        raise Refused("没有 Express 窗口")
    found = []

    def child(ch, _):
        if _cls(ch) == "MDIClient":
            found.append(ch)
        return True

    user32.EnumChildWindows(HWND(main["hwnd"]), EnumProc(child), 0)
    if found:
        res = ctypes.c_size_t()
        user32.SendMessageTimeoutW(found[0], 0x0229, 0, 0, 0x2, 500, ctypes.byref(res))  # WM_MDIGETACTIVE
        if res.value:
            return HWND(res.value)
    return HWND(main["hwnd"])


def fields() -> Dict:
    """当前单据窗口里所有可见输入格（Edit / ComboBox）：句柄、位置、内容，和左边同一行最近的标签文字。
    执行器用它把「焦点在哪个句柄」翻成「焦点在哪一格」，也用它读底部合计。"""
    form = active_form()
    statics, inputs = [], []

    def child(ch, _):
        if not user32.IsWindowVisible(ch):
            return True
        c = _cls(ch)
        if c == "Static":
            t = _gettext(ch).strip()
            if t:
                statics.append((t, _rect(ch)))
        elif c in ("Edit", "ComboBox"):
            inputs.append({"hwnd": int(ch), "cls": c, "rect": _rect(ch), "text": _gettext(ch)})
        return True

    user32.EnumChildWindows(form, EnumProc(child), 0)
    for f in inputs:
        x0, y0, x1, y1 = f["rect"]
        best = None
        for t, (lx0, ly0, lx1, ly1) in statics:
            if abs((ly0 + ly1) / 2 - (y0 + y1) / 2) < 16 and lx1 <= x0 + 5:
                if best is None or x0 - lx1 < best[0]:
                    best = (x0 - lx1, t)
        f["label"] = best[1] if best else ""
    return {"fields": inputs}


def dialog_button(text: str) -> Dict:
    """按 Express 弹窗里文字为 text 的按钮（去掉 & 后全等）：找可见的 Express 对话框，给那个按钮发 BM_CLICK。
    按钮文字从系统接口读，不靠坐标，也不靠默认按钮是哪个（10-26 实测：作废确认框里回车按到的不是「ตกลง」）。"""
    guard()
    found = []
    for w in windows():
        if w["cls"] != "#32770":
            continue

        def child(ch, _):
            if _cls(ch) == "Button" and user32.IsWindowVisible(ch) and _gettext(ch).replace("&", "").strip() == text:
                found.append(ch)
            return True

        user32.EnumChildWindows(HWND(w["hwnd"]), EnumProc(child), 0)
    if len(found) > 1:  # 叠着几个弹窗：只按最上面（前台）那个弹窗里的
        fg = user32.GetForegroundWindow()
        top = [b for b in found if int(user32.GetAncestor(b, 2) or 0) == int(fg or 0)]
        found = top
    if len(found) != 1:
        raise Refused(f"弹窗里找到 {len(found)} 个「{text}」按钮")
    user32.PostMessageW(found[0], 0x00F5, 0, 0)  # BM_CLICK
    return {"pressed": text}


def grid_click(offset=(112, 30)) -> Dict:
    """点当前单据窗口里可见的明细表格（MFCGridCtrl）的某一格：位置 = 表格控件实际位置 + 格子在表格里的偏移
    （偏移按 100% 缩放写，乘系统缩放比例；默认是第一行的品码格）。表格本身不是一格一个窗口，只能这样进。"""
    guard()
    grids = []

    def child(ch, _):
        if user32.IsWindowVisible(ch) and _cls(ch) == "MFCGridCtrl":
            grids.append(_rect(ch))
        return True

    user32.EnumChildWindows(active_form(), EnumProc(child), 0)
    if not grids:
        raise Refused("没有可见的明细表格")
    left, top, right, bottom = max(grids, key=lambda r: (r[2] - r[0]) * (r[3] - r[1]))
    scale = user32.GetDpiForSystem() / 96
    x, y = int(left + offset[0] * scale), int(top + offset[1] * scale)
    click(x, y)
    return {"xy": [x, y], "grid": [left, top, right, bottom], "scale": scale}


def copy_book(book: str, name: str) -> Dict:
    """把试点账套整本只读拷到 <工作目录>\\copies\\<name>（给第 ④ 种直接写入当基准用）。"""
    root = _book(book)
    if not name.replace("-", "").replace("_", "").isalnum():
        raise Refused("副本名只许字母数字和 - _")
    target = Path(COPY_ROOT) / name
    if target.exists():
        raise Refused(f"{target} 已存在")
    target.mkdir(parents=True)
    n = 0
    for p in root.iterdir():
        if p.is_file():
            shutil.copyfile(p, target / p.name)
            n += 1
    return {"copied": n, "to": str(target)}


def launch(exe: str) -> Dict:
    if os.path.normpath(exe).lower() not in EXES:
        raise Refused(f"不在白名单：{exe}")
    import subprocess
    subprocess.Popen([exe], cwd=os.path.dirname(exe))
    return {"launched": exe}


# ── 动作分发 ────────────────────────────────────────────────────────────────
def do(a: Dict) -> Dict:
    op = a.get("do")
    if op == "ping":
        return {"pong": True, "screen": screen_size(), "fg": foreground()}
    if op == "windows":
        return {"windows": windows(), "fg": foreground()}
    if op == "state":
        return state()
    if op == "probe":
        from . import probe_ui
        return probe_ui.dump(a.get("depth", 6), a.get("limit", 400))
    if op == "activate":
        return activate()
    if op == "maximize_main":
        guard()
        return maximize_main()
    if op == "us_layout":
        ensure_us_layout()
        return {}
    if op == "keys":
        guard()
        sendkeys(a["text"])
        return {}
    if op == "key":
        guard()
        key_combo(a["text"], a.get("repeat", 1))
        return {}
    if op == "type":
        guard()
        type_any(a["text"])
        return {}
    if op == "paste":
        guard()
        paste(a["text"])
        return {}
    if op == "click":
        click(a["x"], a["y"], a.get("button", "left"), int(a.get("count", 1)), a.get("mods", ""))
        return {}
    if op == "move":
        guard()
        user32.SetCursorPos(int(a["x"]), int(a["y"]))
        return {}
    if op == "mouse":
        guard()
        _mouse(0x2 if a["state"] == "down" else 0x4)
        return {}
    if op == "drag":
        drag(a["x0"], a["y0"], a["x1"], a["y1"])
        return {}
    if op == "scroll":
        scroll(a["x"], a["y"], a["direction"], a.get("amount", 3))
        return {}
    if op == "hold":
        guard()
        vks = [VK[k.upper()] if k.upper() in VK else (user32.VkKeyScanExW(k, _hkl(US_LAYOUT)) & 0xFF)
               for k in a["text"].split("+")]
        for vk in vks:
            _key_event(vk, False)
        time.sleep(min(float(a.get("seconds", 1)), 30))
        for vk in reversed(vks):
            _key_event(vk, True)
        return {}
    if op == "cursor":
        return {"pos": cursor()}
    if op == "wait":
        time.sleep(min(int(a.get("ms", 500)), 30000) / 1000)
        return {}
    if op == "screenshot":
        return screenshot(a.get("size", SHOT_SIZE))
    if op == "zoom":
        return zoom(a["region"], a.get("long_edge", 1280))
    if op == "book_counts":
        return {"counts": book_counts(a["book"])}
    if op == "book_rows":
        return {"rows": book_rows(a["book"], a.get("since", {}), a.get("tables"))}
    if op == "toolbar":
        return toolbar()
    if op == "fields":
        return fields()
    if op == "dialog_button":
        return dialog_button(a["text"])
    if op == "grid_click":
        return grid_click(a.get("offset", (112, 30)))
    if op == "command":
        return command(a["cmd"])
    if op == "toolbar_click":
        return toolbar_click(a.get("index"), a.get("cmd"))
    if op == "copy_book":
        return copy_book(a["book"], a["name"])
    if op == "launch":
        return launch(a["exe"])
    if op == "login":
        from . import cc_login
        return cc_login.fill(a.get("target", cc_login.DEFAULT_TARGET))
    raise Refused(f"不认识的动作 {op}")


def act(actions: List[Dict], with_windows: bool = True) -> Dict:
    started = time.time()
    results, failed = [], False
    for a in actions:
        if failed:
            results.append({"ok": False, "skipped": True})
            continue
        try:
            res = do(a)
            results.append({"ok": True, **res})
            log(f"{a.get('do')} ok" + (f" {a.get('text')!r}" if a.get("do") in ("keys", "key") else ""))
        except Refused as e:
            failed = True
            results.append({"ok": False, "error": str(e), "refused": True})
            log(f"{a.get('do')} REFUSED {e}")
        except Exception as e:  # noqa: BLE001 — 回给执行器，由它决定停不停
            failed = True
            results.append({"ok": False, "error": f"{type(e).__name__}: {e}"})
            log(f"{a.get('do')} ERROR {type(e).__name__}: {e}")
    out = {"results": results, "ms": int((time.time() - started) * 1000)}
    if with_windows:
        out["windows"] = windows()
        out["fg"] = foreground()
    return out


class Handler(BaseHTTPRequestHandler):
    token = ""
    lock = threading.Lock()

    def log_message(self, *args):  # 不往 stderr 打（pythonw 没有控制台）
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if body.get("token") != self.token:
            self.send_response(403)
            self.end_headers()
            return
        if self.path == "/stop":
            self._reply({"stopping": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        with self.lock:
            reply = act(body.get("actions", []), body.get("windows", True))
        self._reply(reply)

    def _reply(self, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> int:
    if not IS_WIN:
        print("只在 Windows 测试机上跑")
        return 2
    user32.SetProcessDPIAware()
    Handler.token = (WORK / "agent.token").read_text(encoding="ascii").strip()
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    log(f"agent start pid={os.getpid()} port={PORT}")
    server.serve_forever()
    log("agent stop")
    return 0


if __name__ == "__main__":
    sys.exit(main())
