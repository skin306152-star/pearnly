# -*- coding: utf-8 -*-
"""按载荷写指定仓库（载荷 4，2026-10 ERP 前台）：明细行 / 表头带 Express 仓库码 ``loccod`` 的单，库存行写进那个仓，并照
Express 自己的记法维护各仓结存 STLOC。

**契约**：载荷 ``items[]`` 每行可带 ``loccod``（≤4 字的 Express 仓库码），表头 ``loccod`` 是行的默认；服务端给带了任意
``loccod`` 的载荷盖 ``payload_version = 4``。仓库码必须在代码表 ISTAB（TABTYP = 21）里，否则整单不写、回 ``ERR_LOCATION``，
不回落主仓。行与表头都没带的行照旧写主仓 ISINFO.MAINLOC。**一个 ``loccod`` 都没带的载荷与以前逐字节相同**：不碰 STLOC、
不多备份、不多重建索引（``requested`` 为假时本模块什么都不做）。

**STLOC 怎么记**（2026-10-22 用事务所 5 套真账只读核对：6902ASC、69SPP206（02 / 30 两仓都在动）、68BRTKCC、69ASIM、
69MANAC7；不带符号未知的单别时 LOCBAL、QTY{期}、VAL{期} 三项与 STCRD 逐仓累计一致率 99.9%～100%，差的几行是人工改过的）：

- 一行 = 一件库存品 × 一个仓（只有 STKTYP = 0 的品有行）；
- ``LOCBAL = BEGBAL + Σ QTY{期}``（5 套 100%），``QTY{期}`` / ``VAL{期}`` = 该期该仓 STCRD 的带符号 XTRNQTY / XTRNVAL
  （进为正、出为负），期号是 ISPRD 的财年期号与 CY / NY 年槽（同余额表月桶，``fiscal_period``）；
- ``LPURDAT`` / ``LSELDAT`` / ``LMOVDAT`` = 该仓最后一次进货 / 销货 / 任何变动的单据日期（取较晚的那个）；
- ``LOCVAL``、``MREMBAL`` / ``MREMVAL`` Express 不维护（几乎恒 0），这里也不碰；
- 新行照真表的样子：品码、仓码、STKCLASS 抄商品主档、STATUS = 'A'，其余从 0 / 空起。

单据日期不在 ISPRD 两个年槽里：LOCBAL 与日期照记，期桶不写，原因随回执 ``notes.stloc_bucket_not_written`` 上报（同
APBAL / ARBAL 月桶的做法）。STCRD 行上的结存列（MREMBAL 等）与成本口径保持原样（全仓），本模块只管各仓结存。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import dbf

from . import dbf_schema as S
from . import fiscal_period
from .logger import get_logger

log = get_logger(__name__)

ERR_LOCATION = "ERR_LOCATION"
TABLE = "STLOC"
WAREHOUSE_TABTYP = "21"
LOCCOD_MAX = 4
KIND_PURCHASE = "purchase"
KIND_SALE = "sale"
_DATE_FIELD = {KIND_PURCHASE: "LPURDAT", KIND_SALE: "LSELDAT"}
_TOLERANCE = Decimal("0.000001")


class LocationError(Exception):
    """仓库码不合格 / 不在这套账的仓库代码表里 / 账套没有 STLOC：整单不写，回 ``ERR_LOCATION``。"""

    code = ERR_LOCATION


class LocationVerifyError(Exception):
    """写完读回 STLOC 与要写的不符：整单回滚（写路按读回失败回报）。"""

    def __init__(self, message: str, evidence: Optional[dict] = None) -> None:
        super().__init__(message)
        self.evidence = evidence


@dataclass(frozen=True)
class Move:
    """一行库存变动：哪件品、哪个仓、带符号的数量与货值（进为正）、单据日期、进货 / 销货。"""

    stkcod: str
    loccod: str
    qty: Decimal
    value: Decimal
    docdat: object
    kind: str


# ── 载荷 ────────────────────────────────────────────────────────────────────


def _given(value) -> bool:
    return value is not None and value != ""


def requested(payload: dict) -> bool:
    """载荷带没带仓库码（表头或任何一行）。没带的载荷整条写路与以前逐字节相同。"""
    payload = payload or {}
    if _given(payload.get("loccod")):
        return True
    return any(isinstance(item, dict) and _given(item.get("loccod")) for item in payload.get("items") or [])


def _code(value) -> Optional[str]:
    if not _given(value):
        return None
    if not isinstance(value, str):
        raise LocationError(f"loccod must be text: {value!r}")
    code = value.strip()
    if not code or len(code.encode(S.CODEPAGE, "replace")) > LOCCOD_MAX:
        raise LocationError(f"loccod {value!r} is not an Express location code")
    return code


def warehouses(account_dir) -> Dict[str, str]:
    """这套账的仓库代码表（ISTAB TABTYP = 21，不算软删的行）：{仓库码: 名称}。表缺是空字典。"""
    table = _open(account_dir, "ISTAB", dbf.READ_ONLY)
    if table is None:
        return {}
    try:
        out = {}
        for rec in S.live_rows(table):
            if _text(rec, "TABTYP") == WAREHOUSE_TABTYP and _text(rec, "TYPCOD"):
                out[_text(rec, "TYPCOD")] = _text(rec, "TYPDES") or _text(rec, "SHORTNAM")
        return out
    finally:
        table.close()


def plan(account_dir, payload: dict) -> Optional[List[Optional[str]]]:
    """每一行明细写进哪个仓（行的 ``loccod``，没有用表头的；都没有是 None = 主仓，同以前）。载荷一个仓库码都没带回 None
    （整条写路照旧）。仓库码不合格或不在这套账的仓库代码表里抛 :class:`LocationError`（写前、动账套之前）。"""
    if not requested(payload):
        return None
    header = _code(payload.get("loccod"))
    items = [item if isinstance(item, dict) else {} for item in payload.get("items") or []]
    locations = [_code(item.get("loccod")) or header for item in items]
    wanted = {code for code in locations if code} | ({header} if header else set())
    known = warehouses(account_dir)
    missing = sorted(code for code in wanted if code not in known)
    if missing:
        raise LocationError(f"location {', '.join(missing)} is not in this account set's location table (ISTAB 21)")
    if not _file(account_dir, TABLE):
        raise LocationError("this account set has no STLOC table")
    return locations


def location_at(locations: Optional[List[Optional[str]]], index: int) -> Optional[str]:
    """第 ``index`` 行（从 0 起）的仓库码；没有计划或这一行没带是 None（写主仓）。"""
    if not locations or index >= len(locations):
        return None
    return locations[index]


def move(row: dict, kind: str) -> Move:
    """刚写的一条 STCRD 库存行 → 它在 STLOC 上的变动（进货为正、销货为负，数量 XTRNQTY、货值 XTRNVAL）。"""
    sign = Decimal(-1) if kind == KIND_SALE else Decimal(1)
    return Move(str(row["STKCOD"]).strip(), str(row["LOCCOD"]).strip(), sign * _dec(row.get("XTRNQTY")),
                sign * _dec(row.get("XTRNVAL")), row.get("DOCDAT"), kind)


# ── 写 ──────────────────────────────────────────────────────────────────────


def apply(account_dir, moves: Iterable[Move]) -> Tuple[Dict[tuple, dict], dict]:
    """把这张单的库存变动记进 STLOC（在写路的备份-回滚事务内调用）→ (读回要核的各行终值, 回执 notes)。"""
    moves = list(moves)
    if not moves:
        return {}, {}
    calendar = fiscal_period.load(account_dir)
    totals: Dict[tuple, dict] = {}
    skipped = []
    for item in moves:
        slot = totals.setdefault((item.stkcod, item.loccod), {"qty": Decimal(0), "buckets": {}, "dates": {}})
        slot["qty"] += item.qty
        period = calendar.locate(item.docdat) if (calendar and item.docdat) else None
        if period is None:
            skipped.append(str(item.docdat))
        else:
            qty_field, val_field = f"QTY{period.suffix}", f"VAL{period.suffix}"
            buckets = slot["buckets"]
            buckets[qty_field] = buckets.get(qty_field, Decimal(0)) + item.qty
            buckets[val_field] = buckets.get(val_field, Decimal(0)) + item.value
        for field in ("LMOVDAT", _DATE_FIELD.get(item.kind)):
            if field and item.docdat and (slot["dates"].get(field) is None or item.docdat > slot["dates"][field]):
                slot["dates"][field] = item.docdat
    classes = _stock_classes(account_dir, {key[0] for key in totals})
    expected: Dict[tuple, dict] = {}
    table = _open(account_dir, TABLE, dbf.READ_WRITE)
    if table is None:
        raise LocationError("this account set has no STLOC table")
    try:
        fields = {name.upper() for name in table.field_names}
        seen = set()
        for rec in S.live_rows(table):
            key = (_text(rec, "STKCOD"), _text(rec, "LOCCOD"))
            if key not in totals or key in seen:
                continue
            seen.add(key)
            values = _updated(rec, totals[key], fields)
            with rec as row:
                for name, value in values.items():
                    row[name] = value
            expected[key] = values
        for key in [key for key in totals if key not in seen]:
            values = _updated(None, totals[key], fields)
            table.append({"STKCOD": key[0], "LOCCOD": key[1], "STKCLASS": classes.get(key[0], ""), "STATUS": "A",
                          **values})
            expected[key] = values
    finally:
        table.close()
    notes = {}
    if skipped:
        why = f"document date {', '.join(sorted(set(skipped)))} is outside the fiscal calendar (ISPRD)"
        log.warning("STLOC 期桶未写（会计需人工补记）：%s", why)
        notes["stloc_bucket_not_written"] = why
    return expected, notes


def _updated(rec, total: dict, fields: set) -> dict:
    """这一行要写的新值：LOCBAL 与期桶加上本单的变动，日期取较晚的。"""
    values = {"LOCBAL": float(_dec(_value(rec, "LOCBAL")) + total["qty"])}
    for name, delta in total["buckets"].items():
        if name in fields:
            values[name] = float(_dec(_value(rec, name)) + delta)
    for name, day in total["dates"].items():
        old = _value(rec, name)
        if name in fields:
            values[name] = day if old is None or day > old else old
    return values


def verify(account_dir, expected: Dict[tuple, dict]) -> None:
    """写完读回：每个动过的品 × 仓都在，LOCBAL、期桶、日期与要写的一致；不符抛 :class:`LocationVerifyError`。"""
    if not expected:
        return
    table = _open(account_dir, TABLE, dbf.READ_ONLY)
    if table is None:
        raise LocationVerifyError("STLOC missing after write", {"kind": "readback_verify", "table": TABLE})
    try:
        found = {}
        for rec in S.live_rows(table):
            key = (_text(rec, "STKCOD"), _text(rec, "LOCCOD"))
            if key in expected and key not in found:
                found[key] = {name: _value(rec, name) for name in expected[key]}
    finally:
        table.close()
    for key, values in expected.items():
        got = found.get(key)
        for name, want in values.items():
            have = None if got is None else got.get(name)
            same = have == want if not isinstance(want, float) else (
                have is not None and abs(_dec(have) - _dec(want)) <= _TOLERANCE)
            if not same:
                raise LocationVerifyError(
                    f"STLOC {key[0]}@{key[1]} {name} {have!r} != {want!r}",
                    {"kind": "readback_verify", "table": TABLE, "stkcod": key[0], "loccod": key[1], "field": name})


# ── 读表小工具 ──────────────────────────────────────────────────────────────


def _file(account_dir, name: str) -> Optional[Path]:
    base = Path(account_dir)
    for cand in (f"{name}.DBF", f"{name.lower()}.dbf", f"{name.lower()}.DBF"):
        path = base / cand
        if path.is_file():
            return path
    return None


def _open(account_dir, name: str, mode):
    path = _file(account_dir, name)
    if path is None:
        return None
    table = dbf.Table(str(path), codepage=S.CODEPAGE, unicode_errors=S.UNICODE_ERRORS)
    table.open(mode)
    return table


def _stock_classes(account_dir, codes: set) -> Dict[str, str]:
    """新建 STLOC 行要抄的商品主档 STKCLASS（真账 STLOC.STKCLASS 与主档 100% 一致）。"""
    table = _open(account_dir, "STMAS", dbf.READ_ONLY)
    if table is None:
        return {}
    try:
        out = {}
        for rec in S.live_rows(table):
            code = _text(rec, "STKCOD")
            if code in codes and code not in out:
                out[code] = _text(rec, "STKCLASS")
        return out
    finally:
        table.close()


def _value(rec, name: str):
    if rec is None:
        return None
    try:
        return rec[name]
    except Exception:  # noqa: BLE001 — 老表没有这一列
        return None


def _text(rec, name: str) -> str:
    value = _value(rec, name)
    return "" if value is None else str(value).strip()


def _dec(value) -> Decimal:
    if value in (None, ""):
        return Decimal(0)
    return Decimal(repr(value)) if isinstance(value, float) else Decimal(str(value))
