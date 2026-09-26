# -*- coding: utf-8 -*-
"""账套占用探测:写盘前先确认能独占,避免占用时白跑备份/写盘/回滚一整轮。

Express 用 FoxPro/DBF 多用户共享打开账套;而 companion 写完要重建 CDX 索引(REINDEX
必须独占)。只要别处(另一台电脑、服务器、或残留进程)还开着账套,独占就拿不到——此时
若照常 备份→写盘→重建索引→失败回滚,在老旧 SMB1 上一来一回几十次文件拷贝,既慢又会被
云端每分钟重试刷成风暴。

本模块提供一个极廉价(~0.1s)的预探测:用 Win32 CreateFile 以「不允许共享」打开账套里
的哨兵表(ISRUN.DBF)。能打开=当前无人占用,可安全写盘+重建索引;打开报「共享冲突」=
账套被占用,调用方据此弹窗交用户(重试/终止),完全跳过昂贵的写盘回滚。
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

# 写盘第一张表 = ISRUN(增号);Express 开账套时同样开着它 → 当占用哨兵。
SENTINEL_TABLE = "ISRUN.DBF"

# CreateFile 常量(独占打开 = dwShareMode 0;只读访问足够探测占用)。
_GENERIC_READ = 0x80000000
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x80
# 占用特征错误码:别处已开着该文件,独占打开被拒。
_ERROR_SHARING_VIOLATION = 32
_ERROR_LOCK_VIOLATION = 33


def busy_from_winerror(err: int) -> bool:
    """Win32 错误码 → 是否「账套被占用」。共享/锁冲突=占用;其余(不存在等)非占用。"""
    return err in (_ERROR_SHARING_VIOLATION, _ERROR_LOCK_VIOLATION)


def is_account_busy(account_dir: str | Path, sentinel: str = SENTINEL_TABLE) -> bool:
    """以独占方式试开哨兵表:打不开且是共享冲突 → 账套被占用(True)。

    非 Windows / 路径缺失 / 哨兵不存在 / 其它错误 → False(不拦,交正常写盘路径如实暴露)。
    成功打开立即关闭句柄(只探测,不持有)。
    """
    if sys.platform != "win32" or not account_dir:
        return False
    # 哨兵不存在 → CreateFile 返回 FILE/PATH_NOT_FOUND,busy_from_winerror 归 False,无需额外 stat。
    path = Path(account_dir) / sentinel
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel32.CreateFileW
    create.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create.restype = wintypes.HANDLE
    invalid = ctypes.cast(-1, wintypes.HANDLE).value
    handle = create(
        str(path), _GENERIC_READ, 0, None, _OPEN_EXISTING, _FILE_ATTRIBUTE_NORMAL, None
    )
    if handle == invalid:
        return busy_from_winerror(ctypes.get_last_error())
    kernel32.CloseHandle(handle)
    return False
