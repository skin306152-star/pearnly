# -*- coding: utf-8 -*-
"""Windows 上的 ``Desk``：只用 ctypes（安装包排除了 pywinauto / comtypes / mss / cv2，见 companion.spec），由试点执行代理
（31516fff ``agent_win.py``）移植，去掉了 HTTP 服务、读账套和 pywinauto。

守卫写死在这里：每次按键 / 点击前核前台进程是 ExpressI.exe；点击还要核落点所在的顶层窗口也属于 ExpressI，否则拒绝，
不把键打进别的程序。工具栏用 ``TB_COMMANDTOINDEX`` / ``TB_ISBUTTONENABLED`` 按命令号问亮不亮（这两条消息不用跨进程内存），
命令给主窗口发 ``WM_COMMAND``（和点那个按钮走同一条路，不靠坐标：显示缩放 150% 时按坐标会点偏，试点实测）。

DPI：CC 的进程由 Qt 设成按显示器感知缩放，这里不再调 ``SetProcessDPIAware``；窗口位置都是物理像素，明细表格的偏移按
100% 写、乘系统缩放。非 Windows 上导入不报错，只是用不了（测试用假 Express）。
"""
from __future__ import annotations

import base64
import ctypes
import os
import struct
import sys
import time
import zlib
from typing import Dict, List, Optional, Sequence

from .desk import CC_INPUT_MARK, Refused, run

PROCESS = "expressi.exe"
IS_WIN = sys.platform == "win32"

if IS_WIN:
    from ctypes import wintypes as W

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    HWND, HDC, HANDLE = W.HWND, W.HDC, W.HANDLE
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
    # Express 的窗口是别的进程的：用 ShowWindowAsync（ShowWindow 同步等对方处理，对方停在模态框里会一直不回，同 bg_desk._send）
    user32.ShowWindowAsync.argtypes = [HWND, ctypes.c_int]
    user32.IsIconic.argtypes = [HWND]
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user32.GetCursorPos.argtypes = [ctypes.POINTER(W.POINT)]
    user32.VkKeyScanExW.argtypes = [W.WCHAR, W.HKL]
    user32.VkKeyScanExW.restype = ctypes.c_short
    user32.LoadKeyboardLayoutW.argtypes = [W.LPCWSTR, W.UINT]
    user32.LoadKeyboardLayoutW.restype = W.HKL
    user32.PostMessageW.argtypes = [HWND, W.UINT, W.WPARAM, W.LPARAM]
    user32.GetParent.argtypes, user32.GetParent.restype = [HWND], HWND
    user32.SendMessageTimeoutA.argtypes = [HWND, W.UINT, W.WPARAM, W.LPARAM, W.UINT, W.UINT,
                                           ctypes.POINTER(ctypes.c_size_t)]
    user32.GetDlgCtrlID.argtypes = [HWND]
    user32.SendMessageTimeoutW.argtypes = [HWND, W.UINT, W.WPARAM, W.LPARAM, W.UINT, W.UINT,
                                           ctypes.POINTER(ctypes.c_size_t)]
    user32.GetDC.argtypes = [HWND]
    user32.GetDC.restype = HDC
    user32.ReleaseDC.argtypes = [HWND, HDC]
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.OpenClipboard.argtypes = [HWND]
    user32.SetClipboardData.argtypes = [W.UINT, HANDLE]
    user32.SetClipboardData.restype = HANDLE
    user32.GetDpiForSystem.restype = W.UINT
    user32.MonitorFromWindow.argtypes = [HWND, W.DWORD]
    user32.MonitorFromWindow.restype = HANDLE
    user32.GetMonitorInfoW.argtypes = [HANDLE, ctypes.c_void_p]
    user32.SetWindowPos.argtypes = [HWND, HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.UINT]
    user32.IsZoomed.argtypes = [HWND]
    user32.GetWindowLongW.argtypes = [HWND, ctypes.c_int]
    kernel32.GlobalAlloc.argtypes = [W.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = HANDLE
    kernel32.GlobalLock.argtypes = [HANDLE]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [HANDLE]
    kernel32.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    kernel32.OpenProcess.restype = HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [HANDLE, W.DWORD, W.LPWSTR, ctypes.POINTER(W.DWORD)]
    kernel32.CloseHandle.argtypes = [HANDLE]
    kernel32.VirtualAllocEx.argtypes = [HANDLE, ctypes.c_void_p, ctypes.c_size_t, W.DWORD, W.DWORD]
    kernel32.VirtualAllocEx.restype = ctypes.c_void_p
    kernel32.VirtualFreeEx.argtypes = [HANDLE, ctypes.c_void_p, ctypes.c_size_t, W.DWORD]
    kernel32.ReadProcessMemory.argtypes = [HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                           ctypes.POINTER(ctypes.c_size_t)]
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
    # 菜单（只读，两种做法共用；后台做法按命令号发 WM_COMMAND）
    user32.GetMenu.argtypes = [HWND]
    user32.GetMenu.restype = W.HMENU
    user32.GetSubMenu.argtypes = [W.HMENU, ctypes.c_int]
    user32.GetSubMenu.restype = W.HMENU
    user32.GetMenuItemCount.argtypes = [W.HMENU]
    user32.GetMenuItemID.argtypes = [W.HMENU, ctypes.c_int]
    user32.GetMenuItemID.restype = W.UINT
    user32.GetMenuState.argtypes = [W.HMENU, W.UINT, W.UINT]
    user32.GetMenuState.restype = W.UINT
    user32.GetMenuStringW.argtypes = [W.HMENU, W.UINT, W.LPWSTR, ctypes.c_int, W.UINT]

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

    class GUITHREADINFO(ctypes.Structure):
        _fields_ = [("cbSize", W.DWORD), ("flags", W.DWORD), ("hwndActive", HWND), ("hwndFocus", HWND),
                    ("hwndCapture", HWND), ("hwndMenuOwner", HWND), ("hwndMoveSize", HWND), ("hwndCaret", HWND),
                    ("rcCaret", W.RECT)]

    user32.SendInput.argtypes = [W.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.GetGUIThreadInfo.argtypes = [W.DWORD, ctypes.c_void_p]

    EnumProc = ctypes.WINFUNCTYPE(W.BOOL, HWND, W.LPARAM)
    user32.EnumWindows.argtypes = [EnumProc, W.LPARAM]
    user32.EnumChildWindows.argtypes = [HWND, EnumProc, W.LPARAM]

KEYEVENTF_KEYUP = 0x2
MOUSE = {"left": (0x2, 0x4), "right": (0x8, 0x10), "middle": (0x20, 0x40)}
VK = {"ENTER": 0x0D, "RETURN": 0x0D, "TAB": 0x09, "ESC": 0x1B, "ESCAPE": 0x1B, "HOME": 0x24, "END": 0x23,
      "INS": 0x2D, "INSERT": 0x2D, "DEL": 0x2E, "DELETE": 0x2E, "BS": 0x08, "BACKSPACE": 0x08, "BKSP": 0x08,
      "UP": 0x26, "DOWN": 0x28, "LEFT": 0x25, "RIGHT": 0x27, "PGUP": 0x21, "PAGE_UP": 0x21, "PRIOR": 0x21,
      "PGDN": 0x22, "PAGE_DOWN": 0x22, "NEXT": 0x22, "SPACE": 0x20, "SHIFT": 0x10, "CTRL": 0x11, "CONTROL": 0x11,
      "ALT": 0x12}
VK.update({f"F{i}": 0x6F + i for i in range(1, 13)})
# 要带「扩展键」标记，否则 NumLock 开着时 Home 打出 7、Insert 打出 0（试点实测）
EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E}
US_LAYOUT, TH_LAYOUT = "00000409", "0000041E"
WM_GETTEXT, WM_COMMAND, WM_CLOSE, WM_INPUTLANGCHANGEREQUEST = 0x000D, 0x0111, 0x0010, 0x0050
BM_CLICK, WM_MDIGETACTIVE = 0x00F5, 0x0229
TB_ISBUTTONENABLED, TB_BUTTONCOUNT, TB_COMMANDTOINDEX = 0x0409, 0x0418, 0x0419
SB_GETTEXTA, SB_GETTEXTLENGTHW = 0x0402, 0x040C
SHOT_SIZE = (1280, 720)
MF_BYPOSITION, MF_GRAYED, MF_DISABLED, MF_SEPARATOR = 0x0400, 0x0001, 0x0002, 0x0800
MENU_DEPTH = 3


def button_command(button) -> tuple:
    """按一个对话框按钮的消息：给它所在的对话框发 ``WM_COMMAND``（按钮号 + BN_CLICKED），与点它等价，不要求窗口在前台、不要焦点。
    10-27 实机：给登录窗的「OK」发 ``BM_CLICK`` 毫无反应（前台也一样），改发这个当场生效。回 ``(对话框, wparam, lparam)``。"""
    dialog = user32.GetParent(button)
    ident = int(user32.GetDlgCtrlID(button)) & 0xFFFF  # BN_CLICKED = 0（高 16 位）
    return dialog, ident, int(button.value if hasattr(button, "value") else button)


def png(width: int, height: int, rgb_rows: Sequence[bytes]) -> bytes:
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"".join(b"\0" + row for row in rgb_rows)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


class WindowsDesk:
    """在当前 Windows 用户的交互会话里操作 Express。``express_exe`` 是配置里的服务器版 Express 程序（只认它的进程名）。"""

    def __init__(self, express_exe: str = ""):
        if not IS_WIN:
            raise RuntimeError("only on Windows")
        self.express_exe = express_exe
        self.pid: Optional[int] = None  # 绑定的那一个 ExpressI 进程（多个代录会话并行时各管各的，§15.1）；None = 认任何一个
        self._hkl: Dict[str, int] = {}
        self.last_input_tick = 0  # 最后一次本程序发出的按键 / 鼠标（presence 据此分辨「是不是有人在动」）

    # ── 窗口 ────────────────────────────────────────────────────────────────
    @staticmethod
    def _exe_of(hwnd) -> str:
        pid = W.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(520)
            size = W.DWORD(520)
            return buf.value if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)) else ""
        finally:
            kernel32.CloseHandle(handle)

    @staticmethod
    def _title(hwnd) -> str:
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        return buf.value

    @staticmethod
    def _cls(hwnd) -> str:
        buf = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(hwnd, buf, 128)
        return buf.value

    @staticmethod
    def _rect(hwnd) -> List[int]:
        r = W.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        return [r.left, r.top, r.right, r.bottom]

    @staticmethod
    def _send_message(hwnd, msg: int, wparam: int = 0, lparam: int = 0, timeout_ms: int = 200) -> int:
        res = ctypes.c_size_t()
        user32.SendMessageTimeoutW(hwnd, msg, wparam, lparam, 0x2, timeout_ms, ctypes.byref(res))  # SMTO_ABORTIFHUNG
        return res.value

    def _gettext(self, hwnd) -> str:
        """跨进程读控件文字要用 WM_GETTEXT（GetWindowText 读不到别的进程的子控件）。"""
        buf = ctypes.create_unicode_buffer(400)
        self._send_message(hwnd, WM_GETTEXT, 400, ctypes.addressof(buf))
        return buf.value

    def _children(self, parent) -> List:
        found: List = []

        def child(ch, _):
            found.append(ch)
            return True

        user32.EnumChildWindows(parent, EnumProc(child), 0)
        return found

    def status_text(self, hwnd) -> str:
        """状态栏第 0 格的字（「公司名 (账套路径) by 用户」）。Express 的状态栏不回 WM_GETTEXT，只认 SB_GETTEXT，
        而它的缓冲区要在 Express 自己的进程里（字节数按字数 × 2 留足）：在对方进程里借一块内存、读回来、马上释放（只读，不改 Express 的任何东西）。"""
        length = self._send_message(hwnd, SB_GETTEXTLENGTHW, 0, 0) & 0xFFFF
        if not length:
            return ""
        pid = W.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process = kernel32.OpenProcess(0x0008 | 0x0010 | 0x0020, False, pid.value)  # VM_OPERATION | VM_READ | VM_WRITE
        if not process:
            return ""
        size = (length + 1) * 2
        remote = kernel32.VirtualAllocEx(process, None, size, 0x1000 | 0x2000, 0x04)  # MEM_COMMIT|RESERVE, READWRITE
        try:
            if not remote:
                return ""
            # Express 不是 Unicode 程序，状态栏里存的是泰文 cp874 字节：用 A 版读回再按 cp874 解（W 版读出来泰文是乱码，10-26 实机）
            self._send_message(hwnd, SB_GETTEXTA, 0, remote, 500)
            buf = ctypes.create_string_buffer(size)
            read = ctypes.c_size_t()
            if not kernel32.ReadProcessMemory(process, remote, buf, size, ctypes.byref(read)):
                return ""
            return buf.raw[:read.value].split(b"\0", 1)[0].decode("cp874", "replace")
        finally:
            if remote:
                kernel32.VirtualFreeEx(process, remote, 0, 0x8000)  # MEM_RELEASE
            kernel32.CloseHandle(process)

    @staticmethod
    def _pid_of(hwnd) -> int:
        pid = W.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)

    def is_express(self, hwnd, any_instance: bool = False) -> bool:
        if not hwnd or os.path.basename(self._exe_of(hwnd)).lower() != PROCESS:
            return False
        return any_instance or self.pid is None or self._pid_of(hwnd) == self.pid

    def bind(self, pid: Optional[int]) -> None:
        """只认这一个 ExpressI 进程的窗口（启动时记下的，或按状态栏账套路径认出的）：别的会话的 Express 一概不碰。"""
        self.pid = int(pid) if pid else None

    def instances(self) -> List[Dict]:
        """这台电脑上所有 ExpressI 进程各自的主窗口（进程号、标题、文字，含状态栏）：开录前认哪个是这本账套的。"""
        found: Dict[int, Dict] = {}
        for w in self.windows(any_instance=True):
            if w.get("owned"):
                continue
            area = (w["rect"][2] - w["rect"][0]) * (w["rect"][3] - w["rect"][1])
            if w["pid"] not in found or area > found[w["pid"]]["area"]:
                found[w["pid"]] = dict(w, area=area)
        return list(found.values())

    def windows(self, any_instance: bool = False) -> List[Dict]:
        """ExpressI 的全部可见顶层窗口（标题、类名、位置、子控件文字前 40 个）：弹窗认不认得出就看这里有没有字。
        绑定了进程就只看那一个 Express 的（``any_instance`` 看全部，只给认实例用）。"""
        out: List[Dict] = []

        def top(hwnd, _):
            if user32.IsWindowVisible(hwnd) and self.is_express(hwnd, any_instance):
                texts, status, buttons = [], [], []
                for ch in self._children(hwnd):
                    if not user32.IsWindowVisible(ch):
                        continue
                    if self._cls(ch).startswith("msctls_statusbar"):
                        # 状态栏写着「公司名 (账套路径) by 用户」，核对公司用；放最前，不受 40 条上限影响
                        text = self.status_text(ch).strip()
                        if text:
                            status.append(text[:300])
                    elif len(texts) < 40:
                        text = self._gettext(ch).strip()
                        if text:
                            texts.append(text[:200])
                            if self._cls(ch) == "Button" and len(buttons) < 12:
                                buttons.append(text[:60])  # 按钮单列：认「业务选择框」（engine.is_choice）
                texts = status + texts
                out.append({"hwnd": int(hwnd), "pid": self._pid_of(hwnd), "exe": self._exe_of(hwnd),
                            "title": self._title(hwnd), "cls": self._cls(hwnd),
                            "rect": self._rect(hwnd), "owned": bool(user32.GetWindow(hwnd, 4)), "texts": texts,
                            "buttons": buttons})
            return True

        user32.EnumWindows(EnumProc(top), 0)
        return out

    def foreground(self) -> Dict:
        hwnd = user32.GetForegroundWindow()
        return {"hwnd": int(hwnd or 0), "exe": os.path.basename(self._exe_of(hwnd)) if hwnd else "",
                "title": self._title(hwnd) if hwnd else "", "pid": self._pid_of(hwnd) if hwnd else 0}

    def guard(self) -> None:
        fg = self.foreground()
        if fg["exe"].lower() != PROCESS:
            raise Refused(f"foreground is not Express ({fg['exe']})")

    def guard_point(self, x: int, y: int) -> None:
        self.guard()
        root = user32.GetAncestor(user32.WindowFromPoint(W.POINT(int(x), int(y))), 2)  # GA_ROOT
        if not self.is_express(root):
            raise Refused(f"point ({x},{y}) is not on Express")

    def focus_info(self) -> Dict:
        fg = user32.GetForegroundWindow()
        tid = user32.GetWindowThreadProcessId(fg, None)
        info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
        if not user32.GetGUIThreadInfo(tid, ctypes.byref(info)):
            return {}
        focus = info.hwndFocus
        return {"hwnd": int(focus or 0), "cls": self._cls(focus) if focus else "",
                "text": self._gettext(focus) if focus else "", "rect": self._rect(focus) if focus else None}

    def main_window(self) -> Optional[Dict]:
        best, area = None, -1
        for w in self.windows():
            left, top, right, bottom = w["rect"]
            size = (right - left) * (bottom - top)
            if not w["owned"] and size > area:
                best, area = w, size
        return best

    def activate(self) -> Dict:
        w = self.main_window()
        if not w:
            raise Refused("no Express window")
        hwnd = HWND(w["hwnd"])
        if self.foreground()["exe"].lower() == PROCESS and not user32.IsIconic(hwnd):
            return {"title": w["title"], "already": True}  # 已在前台：不按 Alt（Alt 会收起展开的菜单）
        if user32.IsIconic(hwnd):
            user32.ShowWindowAsync(hwnd, 9)
        self._key_event(VK["ALT"], False)  # 按一下 Alt 才允许本进程把别的窗口拉到前台
        self._key_event(VK["ALT"], True)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.25)
        return {"title": w["title"]}

    # ── 键盘 ────────────────────────────────────────────────────────────────
    def _send(self, inputs: List) -> None:
        for one in inputs:  # 打上 CC 的签名：「有人在用」的钩子据此认出是自己发的，不暂停
            if one.type == 1:
                one.u.ki.dwExtraInfo = CC_INPUT_MARK
            else:
                one.u.mi.dwExtraInfo = CC_INPUT_MARK
        arr = (INPUT * len(inputs))(*inputs)
        user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
        self.last_input_tick = kernel32.GetTickCount()

    def _key_event(self, vk: int, up: bool) -> None:
        flags = (KEYEVENTF_KEYUP if up else 0) | (0x1 if vk in EXTENDED else 0)
        self._send([INPUT(1, _U(ki=KEYBDINPUT(vk, 0, flags, 0, 0)))])

    def _tap(self, vk: int, mods=()) -> None:
        for m in mods:
            self._key_event(m, False)
        self._key_event(vk, False)
        self._key_event(vk, True)
        for m in reversed(mods):
            self._key_event(m, True)
        time.sleep(0.02)

    def _layout(self, layout: str):
        if layout not in self._hkl:
            self._hkl[layout] = user32.LoadKeyboardLayoutW(layout, 0)
        return self._hkl[layout]

    def _char(self, ch: str, mods=()) -> None:
        code = user32.VkKeyScanExW(ch, self._layout(US_LAYOUT))
        if code == -1:
            raise Refused(f"cannot type {ch!r}")
        vk, shift = code & 0xFF, (code >> 8) & 0xFF
        extra = [VK["SHIFT"]] if shift & 1 else []
        extra += [VK["CTRL"]] if shift & 2 else []
        extra += [VK["ALT"]] if shift & 4 else []
        self._tap(vk, list(mods) + extra)

    def type_ascii(self, text: str) -> None:
        for ch in text:
            if ord(ch) > 126:
                raise Refused("type takes ASCII only")
            if ch == "\n":
                self._tap(VK["ENTER"])
            elif ch == "\t":
                self._tap(VK["TAB"])
            else:
                self._char(ch)

    def sendkeys(self, spec: str) -> None:
        """SendKeys 子集：+ Shift、^ Ctrl、% Alt 作用于下一个键；{ENTER}、{TAB 3}。"""
        names = {"+": VK["SHIFT"], "^": VK["CTRL"], "%": VK["ALT"]}
        i, mods = 0, []
        while i < len(spec):
            ch = spec[i]
            if ch in names:
                mods.append(names[ch])
                i += 1
                continue
            if ch == "{":
                end = spec.index("}", i + 2) if spec[i + 1] == "}" else spec.index("}", i)
                parts = spec[i + 1:end].split(" ")
                i = end + 1
                name, times = parts[0], int(parts[1]) if len(parts) > 1 else 1
                for _ in range(times):
                    if name.upper() in VK:
                        self._tap(VK[name.upper()], mods)
                    elif len(name) == 1:
                        self._char(name, mods)
                    else:
                        raise Refused(f"unknown key {{{name}}}")
            else:
                self._char(ch, mods)
                i += 1
            mods = []

    def key_combo(self, text: str, repeat: int = 1) -> None:
        parts = [p for p in text.replace(" ", "+").split("+") if p]
        *mods, last = parts or [""]
        mod_vks = [VK[m.upper()] for m in mods]
        for _ in range(max(1, min(int(repeat), 100))):
            if last.upper() in VK:
                self._tap(VK[last.upper()], mod_vks)
            elif len(last) == 1:
                self._char(last.lower() if mod_vks else last, mod_vks)
            else:
                raise Refused(f"unknown key {text}")

    def _clip_set(self, text: str) -> None:
        for _ in range(10):
            if user32.OpenClipboard(None):
                break
            time.sleep(0.05)
        else:
            raise Refused("clipboard busy")
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

    def paste(self, text: str) -> None:
        """泰文：放剪贴板再按 Shift+Insert（Express 里 Ctrl+V 无效，直接发键会变问号）。"""
        user32.LoadKeyboardLayoutW(TH_LAYOUT, 1)
        self._clip_set(text)
        time.sleep(0.15)
        self._tap(VK["INSERT"], [VK["SHIFT"]])

    def type_any(self, text: str) -> None:
        if all(ord(c) <= 126 for c in text):
            self.type_ascii(text)
        else:
            self.paste(text)

    def us_layout(self) -> None:
        """Express 窗口的输入法切成英文再发英文 / 数字键（泰文键盘下数字行会打出泰文字母）。"""
        self.guard()
        user32.PostMessageW(user32.GetForegroundWindow(), WM_INPUTLANGCHANGEREQUEST, 0, self._layout(US_LAYOUT))
        time.sleep(0.1)

    # ── 鼠标（只给 AI 接手和明细表格入口用）────────────────────────────────────
    def _mouse(self, flags: int, data: int = 0) -> None:
        self._send([INPUT(0, _U(mi=MOUSEINPUT(0, 0, data & 0xFFFFFFFF, flags, 0, 0)))])

    def click(self, x: int, y: int, button: str = "left", count: int = 1) -> None:
        self.guard_point(x, y)
        user32.SetCursorPos(int(x), int(y))
        time.sleep(0.08)
        down, up = MOUSE[button]
        for _ in range(max(1, min(int(count), 3))):
            self._mouse(down)
            self._mouse(up)
            time.sleep(0.06)

    def drag(self, x0, y0, x1, y1) -> None:
        self.guard_point(x0, y0)
        self.guard_point(x1, y1)
        user32.SetCursorPos(int(x0), int(y0))
        self._mouse(0x2)
        time.sleep(0.1)
        user32.SetCursorPos(int(x1), int(y1))
        time.sleep(0.1)
        self._mouse(0x4)

    def scroll(self, x, y, direction: str, amount: int) -> None:
        self.guard_point(x, y)
        user32.SetCursorPos(int(x), int(y))
        notch = 120 * max(1, min(int(amount), 20))
        if direction in ("up", "down"):
            self._mouse(0x0800, notch if direction == "up" else -notch)
        else:
            self._mouse(0x1000, notch if direction == "right" else -notch)

    def cursor(self) -> List[int]:
        p = W.POINT()
        user32.GetCursorPos(ctypes.byref(p))
        return [p.x, p.y]

    # ── 截图（GDI 缩放后编成 PNG）────────────────────────────────────────────
    def screen_size(self) -> List[int]:
        return [user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)]

    def grab(self, region: List[int], out_w: int, out_h: int) -> bytes:
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
            info = ctypes.create_string_buffer(struct.pack("<IiiHHIIiiII", 40, out_w, -out_h, 1, 24, 0, 0, 0, 0, 0, 0), 44)
            pixels = ctypes.create_string_buffer(stride * out_h)
            gdi32.GetDIBits(mem, bmp, 0, out_h, pixels, info, 0)
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
        return png(out_w, out_h, rows)

    def screenshot(self, size=SHOT_SIZE) -> Dict:
        w, h = self.screen_size()
        out_w, out_h = (int(size[0]), int(size[1])) if size else (w, h)
        return {"png": base64.b64encode(self.grab([0, 0, w, h], out_w, out_h)).decode(), "screen": [w, h],
                "size": [out_w, out_h]}

    def zoom(self, region: List[int], long_edge: int = 1280) -> Dict:
        x0, y0, x1, y1 = [int(v) for v in region]
        if x1 <= x0 or y1 <= y0:
            raise Refused("bad zoom region")
        scale = min(1.0 * long_edge / max(x1 - x0, y1 - y0), 3.0)
        out_w, out_h = max(1, int((x1 - x0) * scale)), max(1, int((y1 - y0) * scale))
        return {"png": base64.b64encode(self.grab([x0, y0, x1, y1], out_w, out_h)).decode(), "size": [out_w, out_h]}

    # ── 单据窗口 ────────────────────────────────────────────────────────────
    def _toolbars(self) -> List:
        main = self.main_window()
        if not main:
            raise Refused("no Express window")
        return [ch for ch in self._children(HWND(main["hwnd"]))
                if self._cls(ch) == "ToolbarWindow32" and user32.IsWindowVisible(ch)]

    def _bar_for(self, cmd: int):
        """带这个命令的那条工具栏（开着几张单据窗口时有几条，取按钮最多的那条）。"""
        bars = [b for b in self._toolbars() if ctypes.c_ssize_t(self._send_message(b, TB_COMMANDTOINDEX, cmd)).value >= 0]
        if not bars:
            return None
        return max(bars, key=lambda b: self._send_message(b, TB_BUTTONCOUNT))

    def toolbar(self, cmds: Sequence[int]) -> Dict:
        out = []
        for cmd in cmds:
            bar = self._bar_for(int(cmd))
            out.append({"cmd": int(cmd), "enabled": bool(bar and self._send_message(bar, TB_ISBUTTONENABLED, int(cmd)))})
        return {"buttons": out}

    def command(self, cmd: int) -> Dict:
        """给 Express 主窗口发 WM_COMMAND（等同点那个按钮）。先核前台是 Express、这个命令现在亮着（灰的不发）。"""
        self.guard()
        bar = self._bar_for(int(cmd))
        if bar is not None and not self._send_message(bar, TB_ISBUTTONENABLED, int(cmd)):
            raise Refused(f"command {cmd} is disabled")
        user32.PostMessageW(HWND(self.main_window()["hwnd"]), WM_COMMAND, int(cmd), 0)
        return {"cmd": int(cmd)}

    # ── 菜单 ────────────────────────────────────────────────────────────────
    def _item_text(self, menu, index: int) -> str:
        buf = ctypes.create_unicode_buffer(128)
        user32.GetMenuStringW(menu, index, buf, 128, MF_BYPOSITION)
        return buf.value.replace("&", "").strip()

    def _menu_items(self, menu, depth: int) -> List[Dict]:
        items: List[Dict] = []
        count = int(user32.GetMenuItemCount(menu))
        for index in range(max(0, count)):
            sub = user32.GetSubMenu(menu, index)
            state = int(user32.GetMenuState(menu, index, MF_BYPOSITION))
            flags = 0 if state == 0xFFFFFFFF else (state & 0xFF if sub else state)  # 子菜单：高字节是项数
            if not sub and flags & MF_SEPARATOR:
                continue
            node = {"text": self._item_text(menu, index), "cmd": 0 if sub else int(user32.GetMenuItemID(menu, index)),
                    "sub": bool(sub), "grey": bool(flags & (MF_GRAYED | MF_DISABLED)), "items": []}
            if sub and depth > 1:
                node["items"] = self._menu_items(sub, depth - 1)
            items.append(node)
        return items

    def menu_tree(self, depth: int = MENU_DEPTH) -> List[Dict]:
        """Express 主窗口的菜单（只读，不展开、不点）：各层文字 / 命令号 / 是否子菜单 / 是否灰。读不到回空。"""
        main = self.main_window()
        if not main:
            raise Refused("no Express window")
        menu = user32.GetMenu(HWND(main["hwnd"]))
        return self._menu_items(menu, depth) if menu else []

    def menu_resolve(self, path: List[str], name: str = "") -> tuple:
        """回 (命令号, 是否原样按编号找到)；找法见 ``menu.resolve``。菜单读不到时抛 ``Refused``。"""
        from . import menu as M

        tree = self.menu_tree(max(MENU_DEPTH, len(path) + 1))
        if not tree:
            raise Refused("menu not found")
        return M.resolve(tree, path, name)

    def menu_command(self, path: List[str], name: str = "") -> int:
        return self.menu_resolve(path, name)[0]

    def active_form(self):
        """当前激活的单据窗口（MDI 子窗口）：开着几张单据窗口时只看这一张。"""
        main = self.main_window()
        if not main:
            raise Refused("no Express window")
        mdi = [ch for ch in self._children(HWND(main["hwnd"])) if self._cls(ch) == "MDIClient"]
        if mdi:
            active = self._send_message(mdi[0], WM_MDIGETACTIVE, 0, 0, 500)
            if active:
                return HWND(active)
        return HWND(main["hwnd"])

    def fields(self) -> Dict:
        """当前单据窗口里可见的输入格（Edit / ComboBox）：句柄、位置、内容，和左边同一行最近的标签文字。"""
        statics, inputs = [], []
        for ch in self._children(self.active_form()):
            if not user32.IsWindowVisible(ch):
                continue
            cls = self._cls(ch)
            if cls == "Static":
                text = self._gettext(ch).strip()
                if text:
                    statics.append((text, self._rect(ch)))
            elif cls in ("Edit", "ComboBox"):
                inputs.append({"hwnd": int(ch), "cls": cls, "rect": self._rect(ch), "text": self._gettext(ch)})
        for f in inputs:
            x0, y0, x1, y1 = f["rect"]
            best = None
            for text, (lx0, ly0, lx1, ly1) in statics:
                if abs((ly0 + ly1) / 2 - (y0 + y1) / 2) < 16 and lx1 <= x0 + 5:
                    if best is None or x0 - lx1 < best[0]:
                        best = (x0 - lx1, text)
            f["label"] = best[1] if best else ""
        return {"fields": inputs}

    def login_fill(self, a: Dict) -> Dict:
        """自动登录：在标题含 ``title`` 的登录窗里，按格子左边的标签找到用户名格和密码格，直接把字填进去（WM_SETTEXT，不经键盘、
        不经剪贴板），再按「OK」。用户名和密码只在这一次调用的内存里，不回、不记。"""
        dialogs = [w for w in self.windows() if a["title"] in w.get("title", "")]
        if len(dialogs) != 1:
            raise Refused("login window not found")
        hwnd = HWND(dialogs[0]["hwnd"])
        statics, edits = [], []
        for ch in self._children(hwnd):
            if not user32.IsWindowVisible(ch):
                continue
            cls = self._cls(ch)
            if cls == "Static":
                statics.append((self._gettext(ch).strip(), self._rect(ch)))
            elif cls == "Edit":
                edits.append((ch, self._rect(ch)))

        def field(label: str):
            spots = [r for text, r in statics if text == label]
            if len(spots) != 1:
                raise Refused(f"label {label!r} not found")
            lx0, ly0, lx1, ly1 = spots[0]
            near = [(ch, r) for ch, r in edits if abs((r[1] + r[3]) / 2 - (ly0 + ly1) / 2) < 16 and r[0] >= lx1 - 5]
            if not near:
                raise Refused(f"no field next to {label!r}")
            return min(near, key=lambda item: item[1][0] - lx1)[0]

        user_box, password_box = field(a["user_label"]), field(a["password_label"])
        for box, value in ((user_box, a["user"]), (password_box, a["password"])):
            buf = ctypes.create_unicode_buffer(value)
            user32.SendMessageTimeoutW(box, 0x000C, 0, ctypes.addressof(buf), 0x2, 1000, ctypes.byref(ctypes.c_size_t()))
            ctypes.memset(buf, 0, ctypes.sizeof(buf))
        buttons = [ch for ch in self._children(hwnd) if self._cls(ch) == "Button"
                   and self._gettext(ch).replace("&", "").strip() == a.get("button", "OK")]
        if len(buttons) != 1:
            raise Refused("login button not found")
        dialog, wparam, lparam = button_command(buttons[0])
        user32.PostMessageW(dialog, WM_COMMAND, wparam, lparam)
        return {"sent": True}

    def company_pick(self, a: Dict) -> Dict:
        """公司列表（#32770 里一个 MFCGridCtrl，10-27 实机）选这家公司，全程发消息、不碰真键盘鼠标：
        ① 先按登记表行号移过去（表按 COMPNAM 升序 = CC 的 account_set_row，实机核对一致）：默认选中的第一行常是别家真实公司，
           绝不在默认行上按确定；② 再用 Express 自带的「ค้นหา」按 COMPNAM 查找（主路径）；查不到（弹提示）就关掉提示、保留行号选中；
        ③ 按列表的「ตกลง」。进去后由会话核对状态栏的公司名与账套路径，对不上就退出（这里不判）。"""
        row, name = int(a.get("row") or 0), str(a.get("name") or "")
        lists = [w for w in self.windows() if w["cls"] == "#32770"
                 and any(self._cls(ch) == "MFCGridCtrl" for ch in self._children(HWND(w["hwnd"])))]
        if len(lists) != 1 or row < 1:
            raise Refused("company list not found" if len(lists) != 1 else "no registry row")
        dialog = HWND(lists[0]["hwnd"])
        grid = [ch for ch in self._children(dialog) if self._cls(ch) == "MFCGridCtrl"][0]
        for vk, times in ((VK["HOME"], 1), (VK["DOWN"], row - 1)):
            for _ in range(times):
                user32.PostMessageW(grid, 0x0100, vk, 0)
                user32.PostMessageW(grid, 0x0101, vk, 0xC0000000)
        time.sleep(0.3)
        searched = "skipped"
        search = [ch for ch in self._children(dialog) if self._cls(ch) == "Button"
                  and self._gettext(ch).replace("&", "").strip() == a.get("search_button", "ค้นหา")]
        if name and len(search) == 1:
            box = self._press_and_wait(dialog, search[0], lambda w: any(
                self._cls(ch) == "Edit" for ch in self._children(HWND(w["hwnd"]))))
            if box is None:
                searched = "no_search_box"
            else:
                edit = [ch for ch in self._children(HWND(box["hwnd"])) if self._cls(ch) == "Edit"][0]
                raw = ctypes.create_string_buffer(name.encode("cp874", "replace"))
                user32.SendMessageTimeoutA(edit, 0x000C, 0, ctypes.addressof(raw), 0x2, 1000, ctypes.byref(ctypes.c_size_t()))
                ok = [ch for ch in self._children(HWND(box["hwnd"])) if self._cls(ch) == "Button"
                      and int(user32.GetDlgCtrlID(ch)) == 1]
                said = ""
                if ok:
                    shown = self._press_and_wait(HWND(box["hwnd"]), ok[0], lambda w: not any(
                        self._cls(ch) in ("Edit", "MFCGridCtrl") for ch in self._children(HWND(w["hwnd"]))))
                    if shown is not None:  # 查不到：Express 弹了提示框，关掉它，保留行号选中的那一行
                        said = " ".join(t for t in shown.get("texts", []) if not t.startswith("&"))[:120]
                        user32.PostMessageW(HWND(shown["hwnd"]), WM_COMMAND, 1, 0)
                        time.sleep(0.3)
                searched = f"not_found: {said}" if said else "ok"
                for w in self.windows():  # 查找框没关的话按取消关掉
                    if w["hwnd"] == box["hwnd"]:
                        user32.PostMessageW(HWND(box["hwnd"]), WM_COMMAND, 2, 0)
        confirm = [ch for ch in self._children(dialog) if self._cls(ch) == "Button" and int(user32.GetDlgCtrlID(ch)) == 1]
        if len(confirm) != 1:
            raise Refused("company list OK not found")
        dlg, wparam, lparam = button_command(confirm[0])
        user32.PostMessageW(dlg, WM_COMMAND, wparam, lparam)
        return {"row": row, "search": searched}

    def _press_and_wait(self, dialog, button, wanted, seconds: float = 3.0) -> Optional[Dict]:
        """按对话框里的一个按钮，等一个新出现、符合 ``wanted`` 的 Express 对话框（查找框 / 提示框）；等不到回 None。"""
        before = {w["hwnd"] for w in self.windows()}
        dlg, wparam, lparam = button_command(button)
        user32.PostMessageW(dlg, WM_COMMAND, wparam, lparam)
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            time.sleep(0.2)
            for w in self.windows():
                if w["hwnd"] not in before and w["cls"] == "#32770" and wanted(w):
                    return w
        return None

    def dialog_button(self, text: str) -> Dict:
        """按 Express 弹窗里文字为 text 的按钮（`button_command`，不靠坐标、不靠默认按钮；作废确认框里回车按到的不是「ตกลง」）。"""
        self.guard()
        found = []
        for w in self.windows():
            if w["cls"] != "#32770":
                continue
            found += [ch for ch in self._children(HWND(w["hwnd"])) if self._cls(ch) == "Button"
                      and user32.IsWindowVisible(ch) and self._gettext(ch).replace("&", "").strip() == text]
        if len(found) > 1:  # 叠着几个弹窗：只按最上面（前台）那个里的
            fg = user32.GetForegroundWindow()
            found = [b for b in found if int(user32.GetAncestor(b, 2) or 0) == int(fg or 0)]
        if len(found) != 1:
            raise Refused(f"{len(found)} buttons named {text!r}")
        dialog, wparam, lparam = button_command(found[0])
        user32.PostMessageW(dialog, WM_COMMAND, wparam, lparam)
        return {"pressed": text}

    def grid_click(self, offset=(112, 30)) -> Dict:
        """点明细表格（MFCGridCtrl）的某一格：表格控件实际位置 + 偏移（按 100% 写，乘系统缩放）。回车进不去表格，只能这样进。"""
        self.guard()
        grids = [self._rect(ch) for ch in self._children(self.active_form())
                 if user32.IsWindowVisible(ch) and self._cls(ch) == "MFCGridCtrl"]
        if not grids:
            raise Refused("no grid")
        left, top, right, bottom = max(grids, key=lambda r: (r[2] - r[0]) * (r[3] - r[1]))
        scale = user32.GetDpiForSystem() / 96
        x, y = int(left + offset[0] * scale), int(top + offset[1] * scale)
        self.click(x, y)
        return {"xy": [x, y]}

    def show_main(self) -> bool:
        """把 Express 主窗口切到前面（人点「显示 Express 窗口」时用；录单本身从不抢前台）。"""
        main = self.main_window()
        if not main:
            return False
        hwnd = HWND(main["hwnd"])
        if user32.IsIconic(hwnd):
            user32.ShowWindowAsync(hwnd, 9)  # SW_RESTORE
        return bool(user32.SetForegroundWindow(hwnd))

    @staticmethod
    def _work_area(hwnd) -> List[int]:
        """窗口所在显示器的工作区（去掉任务栏）。"""

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", W.DWORD), ("rcMonitor", W.RECT), ("rcWork", W.RECT), ("dwFlags", W.DWORD)]

        info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return []
        r = info.rcWork
        return [r.left, r.top, r.right, r.bottom]

    def fit(self) -> Dict:
        """把超出屏幕的 Express 窗口挪回屏内（``desk.fit_rect``）：不激活、不改前后次序、异步（对方停在模态框里也不卡），
        后台做法照样能用（不碰真键鼠）。已最大化的主窗口不动。回挪过的窗口标题。"""
        from .desk import fit_rect

        main = self.main_window()
        moved = []
        for w in self.windows():
            hwnd = HWND(w["hwnd"])
            # 能拉大小的主窗口才铺满工作区；登录窗这种固定大小的（Express 还没进公司时它就是唯一的窗口）只挪不拉
            is_main = bool(main) and w["hwnd"] == main["hwnd"] and bool(user32.GetWindowLongW(hwnd, -16) & 0x00040000)
            if user32.IsIconic(hwnd) or (is_main and user32.IsZoomed(hwnd)):
                continue
            work = self._work_area(hwnd)
            new = fit_rect(w["rect"], work, main=is_main) if work else []
            if not new:
                continue
            flags = 0x0004 | 0x0010 | 0x4000  # SWP_NOZORDER | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS
            if [new[2] - new[0], new[3] - new[1]] == [w["rect"][2] - w["rect"][0], w["rect"][3] - w["rect"][1]]:
                flags |= 0x0001  # SWP_NOSIZE：只挪不改大小
            user32.SetWindowPos(hwnd, None, new[0], new[1], new[2] - new[0], new[3] - new[1], flags)
            moved.append(w["title"][:60])
        return {"moved": moved}

    def close_express(self) -> Dict:
        """正常退出：给主窗口发 WM_CLOSE（等同点右上角关闭；锁屏时键盘用不了也能发）。"""
        main = self.main_window()
        if not main:
            return {"closed": True}
        user32.PostMessageW(HWND(main["hwnd"]), WM_CLOSE, 0, 0)
        return {"sent": "WM_CLOSE"}

    # ── 动作分发 ────────────────────────────────────────────────────────────
    def do(self, a: Dict) -> Dict:
        op = a.get("do")
        simple = {"windows": lambda: {"windows": self.windows()},
                  "state": lambda: {"fg": self.foreground(), "windows": self.windows(), "focus": self.focus_info()},
                  "activate": self.activate,
                  "cursor": lambda: {"pos": self.cursor()},
                  "fields": self.fields,
                  "fit": self.fit,
                  "close_express": self.close_express}
        if op in simple:
            return simple[op]()
        if op == "maximize_main":
            self.guard()
            user32.ShowWindowAsync(HWND(self.main_window()["hwnd"]), 3)
            return {}
        if op == "us_layout":
            self.us_layout()
            return {}
        if op == "menu_tree":
            return {"tree": self.menu_tree()}
        if op == "menu":  # 真键盘：先单独按 Alt 激活菜单栏，再按数字（单据窗口开着时 Alt+数字 是单据窗口的快捷键）
            self.guard()
            path = [str(k) for k in a["path"]]
            main = self.main_window()
            if main and user32.GetMenu(HWND(main["hwnd"])):
                # 读得到菜单：编号原样对得上照旧按键；要按文字找、或落到子菜单里唯一的命令时按命令号发（同后台做法）
                cmd, literal = self.menu_resolve(path, str(a.get("name") or ""))
                if not literal:
                    user32.PostMessageW(HWND(main["hwnd"]), WM_COMMAND, int(cmd), 0)
                    return {"cmd": int(cmd)}
            self.us_layout()
            self.key_combo("alt")
            time.sleep(0.3)
            for k in path:
                self.sendkeys(k)
                time.sleep(0.4)
            return {}
        if op == "close_form":
            self.guard()
            self.key_combo("ctrl+F4")
            return {}
        if op in ("keys", "key", "type", "paste"):
            self.guard()
            {"keys": lambda: self.sendkeys(a["text"]), "key": lambda: self.key_combo(a["text"], a.get("repeat", 1)),
             "type": lambda: self.type_any(a["text"]), "paste": lambda: self.paste(a["text"])}[op]()
            return {}
        if op == "click":
            self.click(a["x"], a["y"], a.get("button", "left"), int(a.get("count", 1)))
            return {}
        if op == "drag":
            self.drag(a["x0"], a["y0"], a["x1"], a["y1"])
            return {}
        if op == "scroll":
            self.scroll(a["x"], a["y"], a["direction"], a.get("amount", 3))
            return {}
        if op == "move":
            self.guard()
            user32.SetCursorPos(int(a["x"]), int(a["y"]))
            return {}
        if op == "mouse":
            self.guard()
            self._mouse(0x2 if a["state"] == "down" else 0x4)
            return {}
        if op == "wait":
            time.sleep(min(int(a.get("ms", 500)), 30000) / 1000)
            return {}
        if op == "screenshot":
            return self.screenshot(a.get("size", SHOT_SIZE))
        if op == "zoom":
            return self.zoom(a["region"], a.get("long_edge", 1280))
        if op == "toolbar":
            return self.toolbar(a.get("cmds", ()))
        if op == "command":
            return self.command(a["cmd"])
        if op == "dialog_button":
            return self.dialog_button(a["text"])
        if op == "grid_click":
            return self.grid_click(a.get("offset", (112, 30)))
        if op == "login_fill":
            return self.login_fill(a)
        if op == "company_pick":
            return self.company_pick(a)
        raise Refused(f"unknown action {op}")

    def act(self, actions: List[Dict], windows: bool = True) -> Dict:
        out = {"results": run(self.do, actions)}
        if windows:
            out["windows"] = self.windows()
            out["fg"] = self.foreground()
        return out
