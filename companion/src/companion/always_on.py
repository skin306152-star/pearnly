"""常驻模式：让一台办公室电脑当「CC 的电脑」，没人在也一直替 CC 把单据写进 Express（用户 2026-10-12 定方案 A）。

这台电脑开机自动登录一个专用 Windows 账号（在 Windows 里设一次），小助手随登录启动；打开常驻模式后：
- 不让电脑睡眠：保持「系统 + 屏幕」（`SetThreadExecutionState`，退出即自动失效，不改电源设置）。只保持系统不够：
  现代待机（S0）的电脑屏幕一关就进待机，锁屏约 1 分钟也会关屏。2026-10-12 在办公室测试机（现代待机）实测：
  只保持系统，关屏立即进入待机；保持系统 + 屏幕再锁屏，4 分钟以上一直在线、系统日志无待机记录。
  所以锁屏界面会一直亮着（显示器可以关掉电源；没接显示器的小主机无所谓）。按电源键 / 合盖仍会睡，那是人为操作。
- 开机自启时先锁屏：自动登录进来的桌面马上锁上，别人碰不到；小助手在锁屏后照常取任务、写账套。
- 不弹「账套被占用」的询问框：没人点，原先会卡住再被当成用户终止；改为按「等账套」回报，空出来自动写（gui_tray_workers）。

其余（取任务、心跳、文件夹定时任务、自动更新、自愈）与普通电脑完全相同，同一套代码。非 Windows（开发机）全部不做事。
"""

from __future__ import annotations

import sys
import threading

from .logger import get_logger

log = get_logger(__name__)

# winbase.h
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002
AWAKE = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED


class KeepAwake:
    """在一条专用守护线程上保持「系统需要」状态：Windows 按调用线程记这个状态，线程在、状态就在，
    所以不放在会退出 / 重启的工作线程上，也不依赖哪条线程来调开关。"""

    def __init__(self) -> None:
        self._stop: threading.Event | None = None
        self._thread: threading.Thread | None = None

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def set(self, on: bool) -> bool:
        if sys.platform != "win32":
            return False
        if on and not self.active:
            stop = threading.Event()
            ready = threading.Event()
            result = {"ok": False}

            def hold() -> None:
                import ctypes

                kernel32 = ctypes.windll.kernel32
                result["ok"] = bool(kernel32.SetThreadExecutionState(AWAKE))
                ready.set()
                stop.wait()
                kernel32.SetThreadExecutionState(ES_CONTINUOUS)

            self._stop, self._thread = stop, threading.Thread(target=hold, name="keep-awake", daemon=True)
            self._thread.start()
            ready.wait(5)
            if result["ok"]:
                log.info("常驻模式：已保持电脑不睡眠")
            else:
                log.warning("常驻模式：保持不睡眠失败")
            return result["ok"]
        if not on and self.active:
            assert self._stop is not None and self._thread is not None
            self._stop.set()
            self._thread.join(5)
            self._stop = self._thread = None
            log.info("常驻模式：已恢复系统睡眠设置")
        return True


def lock_screen() -> bool:
    """锁屏（同按 Win+L）。只在开机自启、常驻模式打开时调。"""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        ok = bool(ctypes.windll.user32.LockWorkStation())
        log.info("常驻模式：开机后已锁屏" if ok else "常驻模式：锁屏失败")
        return ok
    except Exception:
        log.exception("常驻模式：锁屏失败")
        return False
