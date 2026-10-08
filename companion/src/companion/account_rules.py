# -*- coding: utf-8 -*-
"""写入时按这套账自己的数据定的值（只读账套，不写）：主仓、单据簿、对方档的科目与付款期、最常用的类型码、单位码。

为什么（2026-10-08 用测试机 62 套真实账套核对）：以前这些都写死或用账套默认值，换一家公司就会写出账套里
不存在的码（仓库 01、类型 00、单位 ชิ），或与 Express 自己的记法不一致（应收 / 应付不用对方档上的科目，
控制科目与明细账对不上；到期日恒等于单据日期）。规则：能从这套账自己的设置读到的就照它写，读不到才用调用方给的值。

读档失败一律回调用方给的值（写入照常，不比以前差）；不猜、不造码。
"""
from __future__ import annotations

import datetime as _dt
from collections import Counter
from pathlib import Path
from typing import Dict, Optional, Tuple

import dbf

from . import dbf_schema as S
from . import unit_code


def _open(account_dir, name: str):
    for cand in (f"{name}.DBF", f"{name.lower()}.dbf"):
        path = Path(account_dir) / cand
        if path.is_file():
            t = dbf.Table(str(path), codepage=S.CODEPAGE)
            t.open(dbf.READ_ONLY)
            return t
    return None


def _gv(rec, field: str, default=""):
    try:
        value = getattr(rec, field)
    except Exception:  # noqa: BLE001 — 字段不存在 / 坏值：按没有
        return default
    if isinstance(value, str):
        return value.strip()
    return default if value is None else value


def _first(account_dir, name: str):
    t = _open(account_dir, name)
    if t is None:
        return None
    try:
        for r in S.live_rows(t):
            return {f: _gv(r, f) for f in t.field_names}
    finally:
        t.close()
    return None


# ── 账套设置 ────────────────────────────────────────────────────────────────


def main_location(account_dir, fallback: str = S.STCRD_DEFAULT_LOCCOD) -> str:
    """主仓 ISINFO.MAINLOC（62 套全有；有一套是 1001，没有 01 仓）。"""
    try:
        info = _first(account_dir, "ISINFO") or {}
    except Exception:  # noqa: BLE001
        return fallback
    return str(info.get("MAINLOC") or "").strip() or fallback


def default_account(account_dir, slot: str) -> str:
    """账套默认科目（ISINFO.ACCNUMxx，Express 自动过账用的那排：00 现金、02 应收、07 应付、18 购货…）。"""
    try:
        info = _first(account_dir, "ISINFO") or {}
    except Exception:  # noqa: BLE001
        return ""
    return str(info.get(slot) or "").strip()


def periodic_inventory(account_dir) -> bool:
    """期末盘存制(ISINFO.ISPERPETUA = 'N';62 套里 7 套):采购记「购货」科目,存货等期末盘点再调。"""
    try:
        info = _first(account_dir, "ISINFO") or {}
    except Exception:  # noqa: BLE001
        return False
    return str(info.get("ISPERPETUA") or "").strip().upper() == "N"


def book(account_dir, prefix: str) -> Dict[str, str]:
    """单据簿（ISRUN 里 PREFIX = 单别的那一行）：{JNLTYP, DEPCOD}；找不到是空字典。
    6 套的现购簿挂在 01 号日记账（不是 04），部分簿自带默认部门。"""
    try:
        t = _open(account_dir, "ISRUN")
    except Exception:  # noqa: BLE001
        return {}
    if t is None:
        return {}
    try:
        for r in S.live_rows(t):
            if str(_gv(r, "PREFIX")) == prefix:
                return {"JNLTYP": str(_gv(r, "JNLTYP")), "DEPCOD": str(_gv(r, "DEPCOD"))}
    finally:
        t.close()
    return {}


def postable_account(account_dir, acc: str) -> bool:
    """科目在这套账的科目表里、可记账（ACCTYP='0'）。"""
    acc = str(acc or "").strip()
    if not acc:
        return False
    try:
        t = _open(account_dir, "GLACC")
    except Exception:  # noqa: BLE001
        return False
    if t is None:
        return False
    try:
        for r in S.live_rows(t):
            if str(_gv(r, "ACCNUM")) == acc:
                return str(_gv(r, "ACCTYP")) == "0"
    finally:
        t.close()
    return False


# ── 对方档 ──────────────────────────────────────────────────────────────────

_PARTY = {"APMAS": "SUPCOD", "ARMAS": "CUSCOD"}


def party_terms(account_dir, table: str, code: str) -> Tuple[str, int]:
    """对方档（APMAS / ARMAS）上的 (科目 ACCNUM, 付款期天数 PAYTRM)；查不到是 ('', 0)。"""
    code = str(code or "").strip()
    if not code:
        return "", 0
    try:
        t = _open(account_dir, table)
    except Exception:  # noqa: BLE001
        return "", 0
    if t is None:
        return "", 0
    try:
        for r in S.live_rows(t):
            if str(_gv(r, _PARTY[table])) == code:
                try:
                    days = int(float(_gv(r, "PAYTRM", 0) or 0))
                except (TypeError, ValueError):
                    days = 0
                return str(_gv(r, "ACCNUM")), max(days, 0)
    finally:
        t.close()
    return "", 0


def control_account(account_dir, table: str, code: str, fallback: str) -> str:
    """应收 / 应付行记哪个科目：对方档上挂的（Express 自己过账也用它），不在科目表里或没挂才用调用方给的。
    62 套里 44% 的客户、16% 的供应商挂的不是账套默认的应收 / 应付科目。"""
    acc, _days = party_terms(account_dir, table, code)
    return acc if acc and acc != fallback and postable_account(account_dir, acc) else fallback


def due_date(docdat: _dt.date, days: int) -> _dt.date:
    return docdat + _dt.timedelta(days=int(days or 0)) if docdat else docdat


def common_type(account_dir, table: str, field: str, fallback: str = "00") -> str:
    """新建对方档的类型码：这套账现有档里最常用的那个（62 套里「00」有 2 套没有、4 套是别的意思）。"""
    try:
        t = _open(account_dir, table)
    except Exception:  # noqa: BLE001
        return fallback
    if t is None:
        return fallback
    try:
        counts = Counter(str(_gv(r, field)) for r in S.live_rows(t) if str(_gv(r, field)))
    finally:
        t.close()
    return counts.most_common(1)[0][0] if counts else fallback


# ── 明细行 ──────────────────────────────────────────────────────────────────


def localize_stcrd(account_dir, row: dict, unit: str, location: Optional[str] = None) -> dict:
    """存货卡片行：单位码查这套账自己的单位表（解析不出留空），仓库用主仓。"""
    row["TQUCOD"] = unit_code.resolve(account_dir, unit) if unit else ""
    row["LOCCOD"] = location if location is not None else main_location(account_dir)
    return row
