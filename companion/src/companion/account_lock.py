# -*- coding: utf-8 -*-
"""账套占用探测:写盘前先确认能独占,避免占用时白跑备份/写盘/回滚一整轮。

Express 用 FoxPro/DBF 多用户共享打开账套;而 companion 写完要重建 CDX 索引(REINDEX
必须独占)。只要别处(另一台电脑、服务器、或残留进程)还开着账套,独占就拿不到——此时
若照常 备份→写盘→重建索引→失败回滚,在老旧 SMB1 上一来一回几十次文件拷贝,既慢又会被
云端每分钟重试刷成风暴。

本模块提供一个极廉价(~0.1s)的预探测:用 Win32 CreateFile 以「不允许共享」打开账套里
的哨兵表(ISRUN.DBF)。能打开=当前无人占用,可安全写盘+重建索引;打开报「共享冲突」=
账套被占用,调用方据此弹窗交用户(重试/终止),完全跳过昂贵的写盘回滚。

写账前用 :func:`busy_tables`（11-12，正版 Express 18 种窗口实测，共享笔记「共享写入实验-11-12/b8_matrix.txt」）：主界面不占任何文件；
除「科目表窗口」外的窗口都开着 ISRUN；科目表窗口开着 ARTRNRM、GLACC、GLBAL、ISTAB——只试 ISRUN 会漏判「科目表窗口开着 + 单据带行说明」
以及没测到的窗口。所以写账前把本次要写的每张表的 DBF 与 CDX 都独占试开一遍，任一打不开 = 占用；范围就是之后写前备份 / 重建索引
要独占的那几张表（不多拦，也不少拦）。:func:`is_account_busy` 留给「Express 开着这本账套吗」的判断（代录、托盘询问框）。
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


_PROBE_EXTS = (".DBF", ".CDX")


def _exclusive_error(path) -> int:
    """以「只读 + 不共享」打开 ``path`` 后立即关闭：打开成功回 0，否则回 Win32 错误码。非 Windows 恒 0（不拦）。"""
    if sys.platform != "win32":
        return 0
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel32.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                       wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    invalid = ctypes.cast(-1, wintypes.HANDLE).value
    handle = create(str(path), _GENERIC_READ, 0, None, _OPEN_EXISTING, _FILE_ATTRIBUTE_NORMAL, None)
    if handle == invalid:
        return ctypes.get_last_error()
    kernel32.CloseHandle(handle)
    return 0


def busy_tables(account_dir: str | Path, tables) -> list:
    """这几张表里有哪些现在拿不到独占（别处开着）：逐张试开 ``<表>.DBF`` 与 ``<表>.CDX``，文件不存在的不试；共享 / 锁冲突算占用，
    别的错误不算（交正常写盘路径如实暴露）。返回被占的表名（按传入顺序，去重）；都能开回空列表。只探测，不持有句柄。

    文件名按账套目录里实际的大小写找（网络盘上 Express 的表多是大写，合成测试账套也是）。网络盘上每个文件一次打开约一次往返，
    一张单十来张表、二十来个文件，估计 1 秒左右（记录见 docs/work/2026-11-12-express-open-no-touch.md）。"""
    if not account_dir:
        return []
    folder = Path(account_dir)
    try:
        present = {path.name.upper(): path for path in folder.iterdir() if path.is_file()}
    except OSError:
        return []
    busy = []
    for table in dict.fromkeys(str(t).upper() for t in tables or ()):
        for ext in _PROBE_EXTS:
            path = present.get(table + ext)
            if path is not None and busy_from_winerror(_exclusive_error(path)):
                busy.append(table)
                break
    return busy


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
