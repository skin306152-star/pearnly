# -*- coding: utf-8 -*-
"""写一份账套期间，在账套目录里锁住一个锁文件（1.11.0，ADR-025）。

同一份 Express 账套可能同时有两个站点派单：事务所的 Cowork（会计推识别的票）和客户的 Pearnly ERP（外地员工开的单）。两个站点
各自的写者锁互不相识，所以互斥放在电脑这一侧：写之前独占锁住账套目录里的 ``PEARNLY.LCK``（网络盘上同样有效：Windows 的
字节锁经 SMB 传到文件服务器），锁不到就说明另一台电脑上的 CC 桌面版正在写这份账套 —— 按「等账套」回报（不算失败、不烧重试
次数），过一会儿服务端再派。同一台电脑本来就一件一件写（单线程），锁只挡别的电脑。

锁文件只是一个空文件，不是 Express 的表；Express 自己不看它。账套目录不可写、建不了锁文件时不挡（写 DBF 一样会失败，由写入
那边照常报错）。
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

from .logger import get_logger

log = get_logger(__name__)

LOCK_NAME = "PEARNLY.LCK"
ERR_WRITER_BUSY = "ACCOUNT_WRITER_BUSY"


class Busy(Exception):
    """别的电脑正在写这份账套。"""


def _lock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fd: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


@contextmanager
def held(account_dir: str):
    """``with held(账套目录):`` 期间别的电脑锁不到；锁不到抛 :class:`Busy`。"""
    path = Path(str(account_dir or "")) / LOCK_NAME
    try:
        fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o666)
    except OSError as error:
        log.info("建不了账套锁文件（目录不可写？），不挡写入: %s", error)
        yield
        return
    try:
        try:
            _lock(fd)
        except OSError:
            raise Busy(str(path)) from None
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)
