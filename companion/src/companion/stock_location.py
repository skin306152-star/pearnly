# -*- coding: utf-8 -*-
"""按载荷写指定仓库（载荷 4，2026-10 ERP 前台）：明细行 / 表头带 Express 仓库码 ``loccod`` 的单，库存行写进那个仓，并照
Express 自己的记法维护各仓结存 STLOC。

**契约**：载荷 ``items[]`` 每行可带 ``loccod``（≤4 字的 Express 仓库码），表头 ``loccod`` 是行的默认；服务端给带了任意
``loccod`` 的载荷盖 ``payload_version = 4``。仓库码必须在代码表 ISTAB（TABTYP = 21）里，否则整单不写、回 ``ERR_LOCATION``，
不回落主仓。行与表头都没带的行照旧写主仓 ISINFO.MAINLOC。

**不论带没带仓库码，账套有 STLOC 就按实际写入的仓维护**（出入库第一期，10-25）：原来一个 ``loccod`` 都没带的载荷（Cowork
的推送全是）不碰 STLOC，于是 Express 的各仓结存与 STCRD 对不上（Express 自己录单时两边一起记）。现在照 Express 的记法记进
主仓那一行，随单新建的物料补 STLOC 行。账套没有 STLOC 表（很老的账套）时没带仓库码的单照旧不碰；带了仓库码的照旧整单拒写。

**STLOC 怎么记**（2026-10-22 用事务所 5 套真账只读核对：6902ASC、69SPP206（02 / 30 两仓都在动）、68BRTKCC、69ASIM、
69MANAC7；不带符号未知的单别时 LOCBAL、QTY{期}、VAL{期} 三项与 STCRD 逐仓累计一致率 99.9%～100%，差的几行是人工改过的）：

- 一行 = 一件库存品 × 一个仓（只有 STKTYP = 0 的品有行）；
- ``LOCBAL = BEGBAL + Σ QTY{期}``（5 套 100%），``QTY{期}`` / ``VAL{期}`` = 该期该仓 STCRD 的带符号 XTRNQTY / XTRNVAL
  （进为正、出为负），期号是 ISPRD 的财年期号与 CY / NY 年槽（同余额表月桶，``fiscal_period``）；
- ``LPURDAT`` / ``LSELDAT`` / ``LMOVDAT`` = 该仓最后一次进货 / 销货 / 任何变动的单据日期（取较晚的那个）；
- ``LOCVAL``、``MLOTNUM``、``MREMBAL`` / ``MREMVAL``：不分仓计价的账套 Express 不维护（几乎恒 0），这里也不碰；分仓计价的账套
  （ISINFO.LOC_COST = Y）这几栏是这个仓的批次链与货值，库存单 / 调拨由 ``stock_chain`` 维护（11-19 实录）；
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

from . import account_rules
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
KIND_ISSUE = "issue"    # 库存单领用（OU 类）：减，只记 LMOVDAT
KIND_ADJUST = "adjust"  # 库存单调整（JU 类）：行上数量自带正负，只记 LMOVDAT
# 增减调整 JU / 生产完工收货 PI 照原生（11-18 实录 JU0000001 / PI0000001）：结存与期桶照动，不改 LMOVDAT
KIND_ADJUST_NATIVE = "adjust_native"
KIND_VOID = "void"      # 作废原单（dbf_void）：反向回滚，不动日期栏
KIND_TRANSFER = "transfer"  # 仓间调拨 RL（dbf_transfer）：数量自带正负，原生不动日期栏、不动金额栏，新建的行不带分类（实录 RL0000002）
KIND_HEAL = "heal"  # STLOC 缺行的存货补主仓那一行（``_heal``）：只补结存，不进期桶、不动日期栏
KIND_RETURN = "return"  # 收银退货单 SR 货回仓（dbf_refund）：增，只记 LMOVDAT（实录 SR0000001 与原单同一天，LSELDAT 动没动看不出，不碰）
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
    """每一行明细写进哪个仓（行的 ``loccod``，没有用表头的；都没有是 None = 主仓，同以前）。载荷一个仓库码都没带时，账套有
    STLOC 就每行都是 None（写主仓、照样维护 STLOC），没有 STLOC 回 None（这一套账不维护各仓结存）。仓库码不合格或不在这套账的
    仓库代码表里抛 :class:`LocationError`（写前、动账套之前）。"""
    if not requested(payload):
        if not _file(account_dir, TABLE):
            return None
        return [None for _item in (payload or {}).get("items") or []]
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


def revert(item: Move) -> Move:
    """作废原单（``dbf_void``）：同一品、同一仓、同一期桶的反向变动；Express 作废时不动日期栏（LMOVDAT 等）。"""
    return Move(item.stkcod, item.loccod, -item.qty, -item.value, item.docdat, KIND_VOID)


def move(row: dict, kind: str) -> Move:
    """刚写的一条 STCRD 库存行 → 它在 STLOC 上的变动（进货为正、销货为负，数量 XTRNQTY、货值 XTRNVAL）。"""
    sign = Decimal(-1) if kind in (KIND_SALE, KIND_ISSUE) else Decimal(1)
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
        slot = totals.setdefault((item.stkcod, item.loccod), {"qty": Decimal(0), "buckets": {}, "dates": {}, "kinds": set()})
        slot["qty"] += item.qty
        slot["kinds"].add(item.kind)
        period = calendar.locate(item.docdat) if (calendar and item.docdat) else None
        if period is None:
            skipped.append(str(item.docdat))
        else:
            qty_field, val_field = f"QTY{period.suffix}", f"VAL{period.suffix}"
            buckets = slot["buckets"]
            buckets[qty_field] = buckets.get(qty_field, Decimal(0)) + item.qty
            if item.kind != KIND_TRANSFER:
                buckets[val_field] = buckets.get(val_field, Decimal(0)) + item.value
        for field in (() if item.kind in (KIND_VOID, KIND_TRANSFER, KIND_ADJUST_NATIVE)
                      else ("LMOVDAT", _DATE_FIELD.get(item.kind))):
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
        stocked = set()  # STLOC 有行（任何仓）的存货
        for rec in S.live_rows(table):
            stocked.add(_text(rec, "STKCOD"))
        _heal(account_dir, totals, stocked)
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
            transfer_only = totals[key]["kinds"] == {KIND_TRANSFER}
            table.append({"STKCOD": key[0], "LOCCOD": key[1], "STATUS": "A",
                          **({} if transfer_only else {"STKCLASS": classes.get(key[0], "")}), **values})
            expected[key] = values
    finally:
        table.close()
    notes = {}
    if skipped:
        why = f"document date {', '.join(sorted(set(skipped)))} is outside the fiscal calendar (ISPRD)"
        log.warning("STLOC 期桶未写（会计需人工补记）：%s", why)
        notes["stloc_bucket_not_written"] = why
    return expected, notes


def _heal(account_dir, totals: Dict[tuple, dict], stocked: set) -> None:
    """存货在 STLOC 一行都没有、主档却有结存（10-22 前的 CC 写库存不维护 STLOC 留下的，11-20 实录 70EXP 测试账套 PN00063：
    原生录单时选不到它）：把本单之前的主档结存记进主仓那一行（随本单新建），之后原生就选得到、各仓结存也加得上主档。
    各写路都是先改主档、再记 STLOC，所以本单之前的结存 = 主档现结存 − 本单的变动。"""
    missing = {key[0] for key in totals} - stocked
    if not missing:
        return
    main = account_rules.main_location(account_dir)
    if not main:
        return
    table = _open(account_dir, "STMAS", dbf.READ_ONLY)
    if table is None:
        return
    try:
        balances = {}
        for rec in S.live_rows(table):
            code = _text(rec, "STKCOD")
            if code in missing and code not in balances and _text(rec, "STKTYP") in ("", "0"):
                balances[code] = _dec(_value(rec, "TOTBAL"))
    finally:
        table.close()
    for code, now in balances.items():
        moved = sum((total["qty"] for key, total in totals.items() if key[0] == code), Decimal(0))
        before = now - moved
        if before == 0:
            continue
        slot = totals.setdefault((code, main), {"qty": Decimal(0), "buckets": {}, "dates": {}, "kinds": set()})
        slot["qty"] += before
        slot["kinds"].add(KIND_HEAL)
        log.info("STLOC 补行：%s 主仓 %s 补本单之前的主档结存 %s", code, main, before)


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
