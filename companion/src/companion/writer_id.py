# -*- coding: utf-8 -*-
"""直写的录入人：记这台电脑、这个 Windows 用户在 CC「授权登录」存的 Express 账户名（11-07，用户「录入人应该记 exp 的账户人吧」）。

- 来源：``express_operator.credentials`` 目标 ``Pearnly CC/Express/default`` 的用户名（一个 Windows 用户一份，代录也用它登录，所以同一台
  电脑上直写与代录记的是同一个人）。只读用户名，不读密码；不写日志、不上报（回执的 ``writer`` 只带最后记了谁）。
- 规整照原生（S 盘 95 本 120 万张单：录入人 100% 大写、最长 8 个字符，栏宽 C(8)）：去空白、转大写。
- 退回 ``PEARNLY``（不拦单）：没授权、读不到、空、超过 8 个字符（不截断：截断后可能是另一个真人）、cp874 写不出、
  不在这本账套所在年度的 Express 用户表 ``secure\\SCUSER.DBF``（RECTYP U 的 RECCOD）里；找不到用户表时照用这个名字。
  用户表只读 RECTYP / RECCOD 两栏（同一行还有密码栏，不碰）。
- 凭证头 ``GLJNL.CREBY`` 不用它，一律记 ``PEARNLY``（机器标记，见 ``dbf_schema.is_pearnly``）。
"""
from __future__ import annotations

import struct
from pathlib import Path
from typing import Callable, Optional

import dbf

from . import dbf_schema as S
from .logger import get_logger

log = get_logger(__name__)

WIDTH = 8
_USERS_TABLE = ("secure", "SCUSER.DBF")
_LEVELS = 4  # 账套目录往上找几层用户表（70EXP\TEST → 70EXP\secure；套了一层年度的 64EXP\63EXP 也找得到）


def _stored() -> str:
    from .express_operator import credentials

    try:
        return credentials.user_name(credentials.target("default"))
    except Exception:  # noqa: BLE001 — 读凭据出任何错都当没授权
        return ""


def users_file(account_dir) -> Optional[Path]:
    """这本账套所在年度的 Express 用户表；找不到回 None。"""
    here = Path(account_dir)
    for folder in [here, *list(here.parents)[:_LEVELS]]:
        path = folder.joinpath(*_USERS_TABLE)
        if path.is_file():
            return path
    return None


def _users(path: Path) -> Optional[set]:
    try:
        table = dbf.Table(str(path), codepage=S.CODEPAGE, unicode_errors=S.UNICODE_ERRORS)
        table.open(dbf.READ_ONLY)
    except Exception:  # noqa: BLE001 — 读不了用户表：当作找不到
        return None
    try:
        return {str(rec["RECCOD"] or "").strip().upper() for rec in S.live_rows(table)
                if str(rec["RECTYP"] or "").strip().upper() == "U"}
    except Exception:  # noqa: BLE001
        return None
    finally:
        table.close()


def resolve(account_dir, *, stored: Callable[[], str] = _stored) -> str:
    """这次直写记谁：Express 账户名，不合格回 ``PEARNLY``（原因记一条 info / warning，不带名字以外的东西）。"""
    try:
        name = str(stored() or "").strip().upper()
    except Exception:  # noqa: BLE001 — 读凭据出任何错都当没授权
        name = ""
    if not name:
        return S.PEARNLY_USERID
    try:
        name.encode(S.CODEPAGE)
    except (UnicodeEncodeError, LookupError):
        log.warning("录入人：授权的 Express 账户名写不进账套编码，记 %s", S.PEARNLY_USERID)
        return S.PEARNLY_USERID
    if len(name) > WIDTH:
        log.warning("录入人：授权的 Express 账户名超过 %d 个字符，记 %s", WIDTH, S.PEARNLY_USERID)
        return S.PEARNLY_USERID
    path = users_file(account_dir)
    known = _users(path) if path else None
    if known is not None and name not in known:
        log.warning("录入人：%s 不在这本账套的 Express 用户表里，记 %s", name, S.PEARNLY_USERID)
        return S.PEARNLY_USERID
    return name


def cc_vouchers(account_dir) -> set:
    """CC 写的单的凭证号：凭证头 ``GLJNL.CREBY`` 是机器标记（11-07 起单头录入人记 Express 账户名，只能这样认，``dbf_schema.is_pearnly``）。
    表不在、读不了回空集合。"""
    for cand in ("GLJNL.DBF", "gljnl.dbf"):
        path = Path(account_dir) / cand
        if path.is_file():
            break
    else:
        return set()
    out = set()
    try:
        with open(path, "rb") as f:
            count, hlen, rlen = struct.unpack("<IHH", f.read(32)[4:12])
            f.seek(32)
            raw, fields, off = f.read(max(hlen - 32, 0)), {}, 1
            for pos in range(0, len(raw), 32):
                if raw[pos] == 0x0D:
                    break
                name = raw[pos:pos + 11].split(b"\0")[0].decode("ascii", "replace").upper()
                fields[name] = (off, raw[pos + 16])
                off += raw[pos + 16]
            if "VOUCHER" not in fields or "CREBY" not in fields:
                return set()
            (voff, vsize), (coff, csize) = fields["VOUCHER"], fields["CREBY"]
            f.seek(hlen)
            for _ in range(count):
                rec = f.read(rlen)
                if len(rec) < rlen:
                    break
                if rec[:1] != b"*" and S.is_pearnly(rec[coff:coff + csize].decode("cp874", "replace")):
                    out.add(rec[voff:voff + vsize].decode("cp874", "replace").strip())
    except (OSError, struct.error, IndexError):
        return set()
    return out
