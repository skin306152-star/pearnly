# -*- coding: utf-8 -*-
"""隧道目录有变化才叫醒中转（CC 桌面版要轻量：空闲时 CPU 近 0，不再每 0.5 秒扫一遍）。

接口只有四个：``wait(超时秒) -> bool``（有变化回 True，超时回 False；只给中转线程自己调）、``poke()``（任何线程调，叫醒一次、
当作有变化，例如刚生成配对令牌要重算等多久）、``interrupt()``（任何线程调，叫醒正在等的 ``wait`` 让它回 False，之后的 ``wait``
也立刻回）、``close()``（中转线程退出时自己调，释放句柄）。

- :class:`WindowsWatcher`：``ReadDirectoryChangesW``（ctypes，重叠 IO + 事件，``FILE_NOTIFY_CHANGE_FILE_NAME``，含子目录）
  监视隧道根目录；``WaitForMultipleObjects`` 同时等 IO 完成与停止事件，停止时 ``CancelIoEx`` 并等取消完成后才关句柄、放缓冲。
  只当「有变化」的信号用，不解析通知内容（中转每次都把 ``in\\`` / ``ctl\\`` 整个看一遍）；缓冲溢出（回 0 字节）也算有变化。
  远程桌面重定向盘 / subst 盘写入时通知是否每次都到要在 Windows 真机实测，所以宿主另留每 60 秒一次兜底扫描。
- :class:`ManualWatcher`：没有系统通知的等待（非 Windows、测试）；``notify()`` 模拟一次变化。
- :class:`PollingWatcher`：Windows 上打不开目录监视时的退路：每 ``interval`` 秒回一次「有变化」（同旧的 0.5 秒轮询）。

:func:`open_watcher` 按系统选：Windows 用 :class:`WindowsWatcher`，打不开退到 :class:`PollingWatcher`；其余用 :class:`ManualWatcher`
（只有宿主的兜底扫描）。
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

from ..logger import get_logger

log = get_logger(__name__)

POLL_FALLBACK = 0.5
_BUFFER_DWORDS = 16 * 1024   # 64 KB，DWORD 对齐


class ManualWatcher:
    """没有系统通知：``wait`` 等到 ``notify()``（回 True）、``interrupt()`` 或超时（回 False）。"""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._pending = False
        self._stopped = False

    def notify(self) -> None:
        with self._cond:
            self._pending = True
            self._cond.notify_all()

    poke = notify

    def wait(self, timeout: float) -> bool:
        with self._cond:
            if not self._pending and not self._stopped:
                self._cond.wait(max(0.0, float(timeout)))
            if self._stopped:
                return False
            changed, self._pending = self._pending, False
            return changed

    def interrupt(self) -> None:
        with self._cond:
            self._stopped = True
            self._cond.notify_all()

    def close(self) -> None:
        self.interrupt()


class PollingWatcher(ManualWatcher):
    """退路：每 ``interval`` 秒当作有变化（等的超时更短时按超时回 False）。"""

    def __init__(self, interval: float = POLL_FALLBACK) -> None:
        super().__init__()
        self.interval = float(interval)

    def wait(self, timeout: float) -> bool:
        if timeout < self.interval:
            return super().wait(timeout)
        super().wait(self.interval)
        with self._cond:
            return not self._stopped


class WindowsWatcher:
    """``ReadDirectoryChangesW`` 监视一个目录（含子目录）的文件名变化（建、删、改名）。打不开目录抛 ``OSError``。"""

    FILE_LIST_DIRECTORY = 0x0001
    FILE_SHARE_ALL = 0x0001 | 0x0002 | 0x0004
    OPEN_EXISTING = 3
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    FILE_FLAG_OVERLAPPED = 0x40000000
    FILE_NOTIFY_CHANGE_FILE_NAME = 0x00000001
    WAIT_OBJECT_0 = 0
    WAIT_TIMEOUT = 0x102
    ERROR_IO_INCOMPLETE = 996
    ERROR_OPERATION_ABORTED = 995
    INFINITE_CAP_MS = 0x7FFFFFFE

    def __init__(self, root: Path) -> None:
        import ctypes
        from ctypes import wintypes as W

        self._ctypes = ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._k32 = k32

        class OVERLAPPED(ctypes.Structure):
            _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t), ("Offset", W.DWORD),
                        ("OffsetHigh", W.DWORD), ("hEvent", W.HANDLE)]

        k32.CreateFileW.restype = W.HANDLE
        k32.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, ctypes.c_void_p, W.DWORD, W.DWORD, W.HANDLE]
        k32.CreateEventW.restype = W.HANDLE
        k32.CreateEventW.argtypes = [ctypes.c_void_p, W.BOOL, W.BOOL, W.LPCWSTR]
        k32.ReadDirectoryChangesW.restype = W.BOOL
        k32.ReadDirectoryChangesW.argtypes = [W.HANDLE, ctypes.c_void_p, W.DWORD, W.BOOL, W.DWORD,
                                              ctypes.POINTER(W.DWORD), ctypes.POINTER(OVERLAPPED), ctypes.c_void_p]
        k32.GetOverlappedResult.restype = W.BOOL
        k32.GetOverlappedResult.argtypes = [W.HANDLE, ctypes.POINTER(OVERLAPPED), ctypes.POINTER(W.DWORD), W.BOOL]
        k32.WaitForMultipleObjects.restype = W.DWORD
        k32.WaitForMultipleObjects.argtypes = [W.DWORD, ctypes.POINTER(W.HANDLE), W.BOOL, W.DWORD]
        k32.CancelIoEx.restype = W.BOOL
        k32.CancelIoEx.argtypes = [W.HANDLE, ctypes.POINTER(OVERLAPPED)]
        k32.SetEvent.argtypes = [W.HANDLE]
        k32.ResetEvent.argtypes = [W.HANDLE]
        k32.CloseHandle.argtypes = [W.HANDLE]

        self._W = W
        invalid = W.HANDLE(-1).value
        handle = k32.CreateFileW(str(root), self.FILE_LIST_DIRECTORY, self.FILE_SHARE_ALL, None, self.OPEN_EXISTING,
                                 self.FILE_FLAG_BACKUP_SEMANTICS | self.FILE_FLAG_OVERLAPPED, None)
        if not handle or handle == invalid:
            raise OSError(ctypes.get_last_error(), "CreateFileW")
        self._dir = handle
        self._io_event = k32.CreateEventW(None, True, False, None)
        self._stop_event = k32.CreateEventW(None, True, False, None)
        self._poke_event = k32.CreateEventW(None, False, False, None)   # 自动复位
        if not self._io_event or not self._stop_event or not self._poke_event:
            code = ctypes.get_last_error()
            self._release()
            raise OSError(code, "CreateEventW")
        self._overlapped = OVERLAPPED()
        self._overlapped.hEvent = self._io_event
        self._buffer = (ctypes.c_uint32 * _BUFFER_DWORDS)()
        self._pending = False
        self._closed = False
        self._lock = threading.Lock()   # interrupt（别的线程）与 close（中转线程）不同时碰停止事件
        self._handles = (W.HANDLE * 3)(self._io_event, self._stop_event, self._poke_event)

    def _issue(self) -> None:
        k32, ctypes = self._k32, self._ctypes
        k32.ResetEvent(self._io_event)
        ok = k32.ReadDirectoryChangesW(self._dir, ctypes.byref(self._buffer), ctypes.sizeof(self._buffer), True,
                                       self.FILE_NOTIFY_CHANGE_FILE_NAME, None, ctypes.byref(self._overlapped), None)
        if not ok:
            raise OSError(ctypes.get_last_error(), "ReadDirectoryChangesW")
        self._pending = True

    def wait(self, timeout: float) -> bool:
        if self._closed:
            return False
        if not self._pending:
            self._issue()
        ms = int(min(max(0.0, float(timeout)) * 1000, self.INFINITE_CAP_MS))
        result = self._k32.WaitForMultipleObjects(3, self._handles, False, ms)
        if result == self.WAIT_OBJECT_0:
            count = self._W.DWORD(0)
            self._k32.GetOverlappedResult(self._dir, self._ctypes.byref(self._overlapped), self._ctypes.byref(count),
                                          False)
            self._pending = False   # 下次 wait 再发一次（不解析内容；0 字节 = 缓冲溢出，也算有变化）
            return True
        if result == self.WAIT_OBJECT_0 + 1 or result == self.WAIT_TIMEOUT:
            return False
        if result == self.WAIT_OBJECT_0 + 2:   # poke：读还挂着，下次 wait 接着等
            return True
        raise OSError(self._ctypes.get_last_error(), "WaitForMultipleObjects")

    def interrupt(self) -> None:
        with self._lock:
            if not self._closed and self._stop_event:
                self._k32.SetEvent(self._stop_event)

    def poke(self) -> None:
        with self._lock:
            if not self._closed and self._poke_event:
                self._k32.SetEvent(self._poke_event)

    def close(self) -> None:
        """（中转线程自己调）取消还在等的读并等它真的结束，再关句柄。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        if self._pending:
            count = self._W.DWORD(0)
            self._k32.CancelIoEx(self._dir, self._ctypes.byref(self._overlapped))
            self._k32.GetOverlappedResult(self._dir, self._ctypes.byref(self._overlapped), self._ctypes.byref(count),
                                          True)
            self._pending = False
        with self._lock:
            self._release()

    def _release(self) -> None:
        for name in ("_dir", "_io_event", "_stop_event", "_poke_event"):
            handle = getattr(self, name, None)
            if handle:
                self._k32.CloseHandle(handle)
            setattr(self, name, None)


def open_watcher(root: Path):
    """按系统选监视方式（见模块说明）。"""
    if sys.platform != "win32":
        return ManualWatcher()
    try:
        return WindowsWatcher(Path(root))
    except Exception as exc:  # noqa: BLE001 - 打不开目录监视：退回 0.5 秒轮询
        log.warning("隧道目录监视打不开，改为定时查看：%s", type(exc).__name__)
        return PollingWatcher()


__all__ = ["ManualWatcher", "POLL_FALLBACK", "PollingWatcher", "WindowsWatcher", "open_watcher"]
