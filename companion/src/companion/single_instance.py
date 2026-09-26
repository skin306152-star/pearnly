# -*- coding: utf-8 -*-
"""单实例守卫:同机只允许一个小助手在跑。

两个实例会争抢同一账套的 DBF/CDX 文件(独占打开)、重复跑夜间 PACK、心跳互相顶,
属浪费且脆弱(数据靠云端租约 + 独占锁兜住不会写坏,但不该出现)。标准做法 = 命名互斥量:
进程存活期间持有,第二个实例创建同名互斥量时拿到 ERROR_ALREADY_EXISTS → 判定已在运行。
互斥量随进程退出由 OS 自动释放,崩溃不留残锁(优于 lockfile)。

非 Windows(测试/开发)回落 fcntl.flock 文件锁,语义一致(随进程退出释放)。
"""
from __future__ import annotations

import sys
from typing import Optional

_DEFAULT_NAME = "PearnlyCompanionSingleInstance"
_ERROR_ALREADY_EXISTS = 183


class InstanceGuard:
    """持有单实例锁。进程退出或显式 release() 时释放。"""

    def __init__(self, win_handle=None, posix_fd=None, posix_path: str = "") -> None:
        self._win_handle = win_handle
        self._posix_fd = posix_fd
        self._posix_path = posix_path

    def release(self) -> None:
        if self._win_handle is not None:
            import ctypes

            ctypes.windll.kernel32.CloseHandle(self._win_handle)
            self._win_handle = None
        if self._posix_fd is not None:
            import os

            os.close(self._posix_fd)
            self._posix_fd = None


def acquire(name: str = _DEFAULT_NAME) -> Optional[InstanceGuard]:
    """成功返回守卫(持有锁);已有实例在跑返回 None。无法判定时 fail-open(返回守卫,不挡启动)。"""
    if sys.platform == "win32":
        return _acquire_win(name)
    return _acquire_posix(name)


def _acquire_win(name: str) -> Optional[InstanceGuard]:
    import ctypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, name)
    last_err = kernel32.GetLastError()
    if not handle:
        return InstanceGuard()  # 创建失败 → 不阻断启动(宁可放过也不误杀唯一实例)
    if last_err == _ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return None
    return InstanceGuard(win_handle=handle)


def _acquire_posix(name: str) -> Optional[InstanceGuard]:
    import os
    import tempfile

    path = os.path.join(tempfile.gettempdir(), name + ".lock")
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    except ImportError:
        return InstanceGuard(posix_fd=fd, posix_path=path)  # 无 fcntl → fail-open
    return InstanceGuard(posix_fd=fd, posix_path=path)
