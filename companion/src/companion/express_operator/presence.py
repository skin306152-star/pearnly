# -*- coding: utf-8 -*-
"""录的时候会不会和人冲突（评估 §14，级别「自动」）：真冲突才停在原地等，人走开再接着录，每次都留证据。

- 前台做法（真键盘鼠标）：锁屏（``OpenInputDesktop`` 拿不到输入桌面）或本程序没发键时出现新的键盘鼠标动作 → 停；
- 后台做法（直接给 Express 发消息，``bg_desk``）：人用别的程序、锁屏都不冲突；只有人把 Express 切到最前面又在动才停。
开局时的旧输入（登录、解锁）不算；恢复条件是连续 ``idle_s`` 秒（默认 30 秒）没人动，或人在 CC 里点「继续」（``resume_now``）。
「有人在动」= 除了 CC 自己发的输入以外的一切（主会话 10-30）：批次在跑时挂低级键鼠钩子，只忽略 dwExtraInfo 带 CC 签名
（``CC_INPUT_MARK``）的事件；本机键鼠、经 Deskflow / AnyDesk / TeamViewer 等远程软件来的、别的程序注入的都算有人。
钩子没挂上或被系统摘掉时才退回 ``GetLastInputInfo``（分不出是谁发的，只能按时间排除本程序自己发的键）。
每次要发键 / 发消息前调用一次（引擎 ``act``）；等待期间人点停止就抛 ``Stopped``，这张留在「正在录」，续录时先读账套判断。
"""
from __future__ import annotations

import ctypes
import sys
import threading
import time
from typing import Callable, Dict, List, Optional, Protocol

from .desk import CC_INPUT_MARK
from .engine import Stopped


class Signals(Protocol):
    def locked(self) -> bool: ...

    def last_input_tick(self) -> int: ...

    def now_tick(self) -> int: ...


WH_KEYBOARD_LL, WH_MOUSE_LL, WM_QUIT, WM_APP = 13, 14, 0x0012, 0x8000
KEY_DOWNS = (0x0100, 0x0104)  # WM_KEYDOWN、WM_SYSKEYDOWN
HOOK_LOST_MS = 1000  # 系统记到的输入比钩子看到的最后一个事件还晚这么多：钩子被摘了（超时会被系统悄悄摘掉）


class HardwareInput:
    """批次在跑时挂的低级键鼠钩子（``WH_KEYBOARD_LL`` / ``WH_MOUSE_LL``）：只记时刻，不记按了什么；dwExtraInfo 带 CC
    签名的（CC 自己 ``SendInput`` 发的）只记进 ``last_any``，其余都算有人。钩子在自己的线程里收消息（低级钩子要求装它的
    线程有消息循环），``stop`` 时发 ``WM_QUIT`` 退出并摘掉。"""

    def __init__(self, user32, kernel32):
        self.user32, self.kernel32 = user32, kernel32
        self.last_human = 0  # 最后一次不是 CC 自己发的输入（GetTickCount 毫秒）
        self.last_any = 0  # 最后一次任何输入（含 CC 自己的），用来看钩子是否还活着
        self.active = False
        self._thread: Optional[threading.Thread] = None
        self._tid = 0
        self._procs: List = []

    def start(self) -> bool:
        if self._thread is not None and self._thread.is_alive():
            return self.active
        ready = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(ready,), name="presence-hooks", daemon=True)
        self._thread.start()
        ready.wait(5)
        time.sleep(0.1)  # 10-30 实机：挂上后马上来的输入系统还不送给钩子，隔 50 毫秒起才送
        return self.active

    def stop(self) -> None:
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive() and self._tid:
            self.user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
            thread.join(5)
        self.active = False

    def _run(self, ready: threading.Event) -> None:
        from ctypes import wintypes as W

        lresult = ctypes.c_ssize_t
        proc_type = ctypes.WINFUNCTYPE(lresult, ctypes.c_int, W.WPARAM, W.LPARAM)

        class KBD(ctypes.Structure):
            _fields_ = [("vkCode", W.DWORD), ("scanCode", W.DWORD), ("flags", W.DWORD), ("time", W.DWORD),
                        ("dwExtraInfo", ctypes.c_size_t)]

        class MOUSE(ctypes.Structure):
            _fields_ = [("pt", W.POINT), ("mouseData", W.DWORD), ("flags", W.DWORD), ("time", W.DWORD),
                        ("dwExtraInfo", ctypes.c_size_t)]

        u, k = self.user32, self.kernel32
        u.SetWindowsHookExW.restype = W.HHOOK
        u.SetWindowsHookExW.argtypes = [ctypes.c_int, proc_type, W.HINSTANCE, W.DWORD]
        u.CallNextHookEx.restype = lresult
        u.CallNextHookEx.argtypes = [W.HHOOK, ctypes.c_int, W.WPARAM, W.LPARAM]
        u.UnhookWindowsHookEx.argtypes = [W.HHOOK]
        u.PostThreadMessageW.argtypes = [W.DWORD, W.UINT, W.WPARAM, W.LPARAM]
        k.GetModuleHandleW.restype = W.HMODULE
        k.GetModuleHandleW.argtypes = [W.LPCWSTR]

        def handler(struct, counts):
            def proc(code, wparam, lparam):
                if code >= 0:
                    now = int(k.GetTickCount())
                    self.last_any = now
                    if counts(int(wparam)) and \
                            ctypes.cast(lparam, ctypes.POINTER(struct)).contents.dwExtraInfo != CC_INPUT_MARK:
                        self.last_human = now
                return u.CallNextHookEx(None, code, wparam, lparam)
            return proc_type(proc)

        # 键盘只算按下：单独的抬起不是人在按（10-30 录制机：有程序每 0.3 秒补发一次 Ctrl 抬起，批次停了就恢复不了）
        self._procs = [handler(KBD, lambda msg: msg in KEY_DOWNS), handler(MOUSE, lambda _msg: True)]
        msg = W.MSG()
        u.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # 先建好本线程的消息队列，stop 发的 WM_QUIT 才收得到
        self._tid = int(k.GetCurrentThreadId())
        module = k.GetModuleHandleW(None)
        hooks = [u.SetWindowsHookExW(kind, proc, module, 0) for kind, proc in zip((WH_KEYBOARD_LL, WH_MOUSE_LL), self._procs)]
        self.last_human = self.last_any = int(k.GetTickCount())
        self.active = all(hooks)
        if not self.active:
            ready.set()
        # 线程进了消息循环才算挂好
        u.PostThreadMessageW(self._tid, WM_APP, 0, 0)
        try:
            if self.active:
                while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                    if msg.message == WM_APP:
                        ready.set()
                        continue
                    u.TranslateMessage(ctypes.byref(msg))
                    u.DispatchMessageW(ctypes.byref(msg))
        finally:
            ready.set()
            for hook in hooks:
                if hook:
                    u.UnhookWindowsHookEx(hook)
            self.active = False


class WindowsSignals:
    def __init__(self):
        from ctypes import wintypes as W

        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user32.OpenInputDesktop.restype = W.HANDLE
        self.user32.OpenInputDesktop.argtypes = [W.DWORD, W.BOOL, W.DWORD]
        self.user32.CloseDesktop.argtypes = [W.HANDLE]

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", W.UINT), ("dwTime", W.DWORD)]

        self._info_type = LASTINPUTINFO
        self.hooks = HardwareInput(self.user32, self.kernel32)

    def start(self) -> None:
        self.hooks.start()

    def stop(self) -> None:
        self.hooks.stop()

    @property
    def hardware_only(self) -> bool:
        """钩子还在：``last_input_tick`` 不含 CC 自己发的输入。系统记到的输入比钩子看到的还新（钩子超时被摘了）就当钩子没了，
        重挂一次，这一刻先退回系统最后输入时间。"""
        if not self.hooks.active:
            return False
        if self._system_tick() - self.hooks.last_any > HOOK_LOST_MS:
            self.hooks.stop()
            self.hooks.start()
            return False
        return True

    def locked(self) -> bool:
        desk = self.user32.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_SWITCHDESKTOP
        if not desk:
            return True
        self.user32.CloseDesktop(desk)
        return False

    def last_input_tick(self) -> int:
        if self.hooks.active:
            return self.hooks.last_human
        return self._system_tick()

    def _system_tick(self) -> int:
        info = self._info_type(cbSize=ctypes.sizeof(self._info_type))
        self.user32.GetLastInputInfo(ctypes.byref(info))
        return int(info.dwTime)

    def now_tick(self) -> int:
        return int(self.kernel32.GetTickCount())


class Presence:
    """什么时候停下来让人。两种做法口径不同（10-26 实机：旧口径把登录时的按键当成「有人在用」，每张白等 2 分钟）：

    - 前台做法（真键盘鼠标，``background=False``）：锁屏时发键到不了 Express → 停；在本程序没发键的空档里出现了新的键盘
      鼠标动作 → 有人在用，停；
    - 后台做法（发消息，``background=True``）：不用真键盘鼠标，人用别的程序、锁屏都不冲突，照常录；只有人把本批在用的
      那个 Express 切到最前面、又在动键盘鼠标（在 Express 里点、打字）才算真冲突，停。

    开局先记下当时的「最后输入时间」（登录、解锁时的按键不算）；每次判定都留证据（时刻、原因、系统最后输入时间、本程序
    最后发键时间、当时最前面的程序和窗口标题），没有证据不停；恢复条件是连续 ``idle_s`` 秒没人动，或人在 CC 里点「继续」。"""

    def __init__(self, signals: Signals, own_tick: Callable[[], int], *, stop_requested: Callable[[], bool],
                 on_pause: Callable[[str], None] = lambda _r: None, on_resume: Callable[[], None] = lambda: None,
                 idle_s: float = 30, poll_s: float = 3, sleep: Callable[[float], None] = time.sleep,
                 background: bool = False, foreground: Callable[[], Dict] = lambda: {},
                 clock: Callable[[], float] = time.time):
        self.signals, self.own_tick = signals, own_tick
        self.stop_requested, self.on_pause, self.on_resume = stop_requested, on_pause, on_resume
        self.idle_ms, self.poll_s, self.sleep = int(idle_s * 1000), poll_s, sleep
        self.background, self.foreground, self.clock = background, foreground, clock
        self._resume_now = False
        self._seen: Optional[int] = None  # 已经处理过的输入（开局、恢复时记下，之后只看新的）
        self._source = "last_input"  # 这次判定用的是钩子（不含 CC 自己发的）还是系统最后输入时间，进证据
        self.evidence: List[Dict] = []  # 每次暂停的证据，进汇报

    def resume_now(self) -> None:
        self._resume_now = True

    def _new_input(self) -> Optional[int]:
        """开局以来（或上次恢复以来）出现、且不是本程序发的输入：回它的系统时刻，没有回 None。"""
        if self._seen is None:
            getattr(self.signals, "start", lambda: None)()  # 开局才挂钩子
        hardware = bool(getattr(self.signals, "hardware_only", False))
        self._source = "hook" if hardware else "last_input"
        last = self.signals.last_input_tick()
        if self._seen is None:
            self._seen = last  # 开局：之前的输入（登录、解锁）一律不算
            return None
        if last > (self._seen if hardware else max(self.own_tick() + 500, self._seen)):
            return last
        return None

    def close(self) -> None:
        """批次录完：摘钩子。下一批开局再挂。"""
        getattr(self.signals, "stop", lambda: None)()
        self._seen = None

    def reason(self) -> Optional[Dict]:
        fg = self.foreground() or {}
        # 只认本批在用的那个 Express（多批并行时人切到别的 Express 不算这一批的冲突）；``mine`` 由宿主按绑定的进程号给
        express_front = str(fg.get("exe", "")).lower() == "expressi.exe" and fg.get("mine", True)
        if not self.background and self.signals.locked():
            return self._evidence("screen_locked", None, fg)
        new = self._new_input()
        if new is None:
            return None
        if self.background and not express_front:
            self._seen = new  # 人在用别的程序：不冲突，照常录，只是不再重复看这次输入
            return None
        return self._evidence("user_active", new, fg)

    def _evidence(self, reason: str, input_tick: Optional[int], fg: Dict) -> Dict:
        return {"reason": reason, "at": round(self.clock(), 1), "input_tick": input_tick, "own_tick": self.own_tick(),
                "now_tick": self.signals.now_tick(), "foreground": str(fg.get("exe", "")),
                "source": self._source,
                "title": str(fg.get("title", ""))[:120]}

    def __call__(self) -> None:
        found = self.reason()
        if found is None:
            return
        self.evidence.append(found)
        self.on_pause(found["reason"])
        self._resume_now = False
        while True:
            if self.stop_requested():
                raise Stopped()
            if self.background or not self.signals.locked():
                idle = self.signals.now_tick() - self.signals.last_input_tick()
                if self._resume_now or idle >= self.idle_ms:
                    self._seen = self.signals.last_input_tick()
                    found["paused_s"] = round(self.clock() - found["at"], 1)
                    self.on_resume()
                    return
            self.sleep(self.poll_s)


def windows_signals() -> Optional[WindowsSignals]:
    return WindowsSignals() if sys.platform == "win32" else None
