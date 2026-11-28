# -*- coding: utf-8 -*-
r"""段 ③ a：云端 CC 首次自动安装（用户 11-24「安装要自动——连接上、选公司即可用，不要人手动装」）。

桥连上云端、隧道盘 ``cc\`` 备好、拿到一次性配对令牌后，替人在远程会话里做原来要人做的那几步：
把连接窗口拉到前台、点进远程画面（远程桌面要先点进去才收键）→ Alt+Home（远程桌面里打开开始菜单的标准快捷键；
窗口模式下 Windows 键不转给远程）→ 输 ``powershell`` 回车 → 输安装命令（:func:`cloud_package.install_command`）回车。

字一律按 Unicode 发（``SendInput`` + ``KEYEVENTF_UNICODE``）：与远程键盘布局无关（云端默认泰文键盘）。11-28 真机 V1：
从桥这边按 Unicode 发「V1 abc 123 中文测试 ภาษาไทยทดสอบ ก่อน-หลัง」到云端记事本，存盘读回 86 字节一致。
按键序列只在这里（:func:`steps`），发送器可注入（测试用假的）；只发、不判成没成——成没成看配对回执（桥 ``pairing_status``）。
"""
from __future__ import annotations

import time
from typing import Callable, Iterable, Optional, Sequence, Tuple

from ..logger import get_logger

log = get_logger(__name__)

# 每一步之后等多久（秒）：开始菜单弹出、PowerShell 起来要时间（真机：搜索框约 1 秒，PowerShell 窗口约 3–5 秒）
AFTER_CLICK = 0.8
AFTER_START_MENU = 1.5
AFTER_SEARCH = 1.0
AFTER_SHELL = 5.0

Step = Tuple[str, object]


def steps(command: str) -> list:
    """要做的动作：``("focus", None)`` / ``("click", None)`` / ``("combo", ("ALT", "HOME"))`` / ``("text", 字)`` /
    ``("key", "ENTER")`` / ``("wait", 秒)``。"""
    return [
        ("focus", None), ("wait", AFTER_CLICK),
        ("click", None), ("wait", AFTER_CLICK),
        ("combo", ("ALT", "HOME")), ("wait", AFTER_START_MENU),
        ("text", "powershell"), ("wait", AFTER_SEARCH),
        ("key", "ENTER"), ("wait", AFTER_SHELL),
        ("text", command), ("key", "ENTER"),
    ]


class Sender:
    """真发送器（Windows）：把 ``hwnd`` 的窗口还原到前台、点它的正中、按键、按 Unicode 打字。"""

    VK = {"ALT": 0x12, "HOME": 0x24, "ENTER": 0x0D}

    def __init__(self, hwnd: int) -> None:
        import ctypes
        from ctypes import wintypes as W

        self._ctypes, self._W, self.hwnd = ctypes, W, int(hwnd)
        self._u32 = ctypes.WinDLL("user32", use_last_error=True)

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", W.WORD), ("wScan", W.WORD), ("dwFlags", W.DWORD), ("time", W.DWORD),
                        ("dwExtraInfo", ctypes.c_size_t)]

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", W.LONG), ("dy", W.LONG), ("mouseData", W.DWORD), ("dwFlags", W.DWORD),
                        ("time", W.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class _U(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", W.DWORD), ("u", _U)]

        self._INPUT, self._KEYBDINPUT = INPUT, KEYBDINPUT

    def _send(self, items: Sequence) -> None:
        array = (self._INPUT * len(items))(*items)
        sent = self._u32.SendInput(len(items), array, self._ctypes.sizeof(self._INPUT))
        if sent != len(items):
            raise OSError(self._ctypes.get_last_error(), "SendInput")

    def _key(self, vk: int = 0, scan: int = 0, flags: int = 0):
        item = self._INPUT(type=1)
        item.u.ki = self._KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
        return item

    def focus(self) -> None:
        self._u32.ShowWindow(self.hwnd, 9)   # SW_RESTORE
        self._u32.SetForegroundWindow(self.hwnd)

    def click(self) -> None:
        rect = self._W.RECT()
        self._u32.GetWindowRect(self.hwnd, self._ctypes.byref(rect))
        x, y = (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2
        self._u32.SetCursorPos(x, y)
        self._u32.mouse_event(0x0002, 0, 0, 0, 0)   # 左键按下
        self._u32.mouse_event(0x0004, 0, 0, 0, 0)   # 左键抬起

    def combo(self, names: Iterable[str]) -> None:
        codes = [self.VK[name] for name in names]
        self._send([self._key(vk) for vk in codes] + [self._key(vk, flags=0x0002) for vk in reversed(codes)])

    def key(self, name: str) -> None:
        self.combo([name])

    def text(self, value: str) -> None:
        for ch in str(value):   # KEYEVENTF_UNICODE = 4，抬起再加 KEYEVENTF_KEYUP = 2
            self._send([self._key(scan=ord(ch), flags=0x0004), self._key(scan=ord(ch), flags=0x0006)])
            time.sleep(0.01)


def run(command: str, sender, *, sleep: Callable[[float], None] = time.sleep,
        plan: Optional[list] = None) -> bool:
    """按 :func:`steps` 发一遍；发送出错回 False（不重试，由桥退回「云端安装命令」兜底）。命令里有令牌，不进日志。"""
    try:
        for action, value in plan if plan is not None else steps(command):
            if action == "wait":
                sleep(float(value))
            elif action in ("focus", "click"):
                getattr(sender, action)()
            else:
                getattr(sender, action)(value)
    except Exception as exc:  # noqa: BLE001 - 发不出去：退回兜底
        log.warning("云端自动安装发键失败：%s", type(exc).__name__)
        return False
    log.info("云端自动安装 · 已发安装命令")
    return True


__all__ = ["Sender", "run", "steps"]
