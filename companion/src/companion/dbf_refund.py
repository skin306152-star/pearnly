# -*- coding: utf-8 -*-
"""收银退货单 SR 写进 Express（载荷 10，销项 ``doctype = SR`` 带 ``refund`` 块；ERP B3b，由 ``dbf_sales.write_sale`` 分流过来）。

照 Express 原生「ใบลดหนี้/รับคืนสินค้า」退现金的写法（服务器测试账套实录 SR0000001：挂原单 HS0000039，退 1 个、100 含税，选「จ่ายคืนเป็นเงินสด」）：

- 单头 ARTRN：RECTYP 5、CNTYP 3（退现金）、SONUM = 原单号、FLGVAT 1、AMOUNT / AFTDISC / TOTAL / NETAMT = 含税、NETVAL = 税前、
  VATAMT、RCVAMT = 含税（退了的钱）、IVCAMT = 原单含税合计、CMPLAPP Y、DOCSTAT N、NXTSEQ = 行数；
- 明细 STCRD：POSOPR 2、FLAG 2、RETSTK Y、PEOPLE = 客户、RDOCNUM = 原单号左对齐补空格到 14 位 + 原单行号（实录「HS0000039     1」）、
  TRNQTY / UNITPR / TRNVAL 按交易单位记**含税**（SR 是 Express 原生单，照原生写；CC 写 HS 时这几栏记税前）、NETVAL / XSALVAL = 税前、
  XTRNQTY = 库存单位数量、XUNITPR / XTRNVAL = 原单那一行的成本（对应原生「按原批次」）、MREMBAL / MREMVAL = 写后结存（同 CC 现有库存
  写法；先进先出批次链 MLOTNUM / LOTBAL 不写，由 Express「计算成本」重排）；
- 主档 STMAS 结存加回、各仓 STLOC 退货回仓（只记 LMOVDAT）；
- 凭证 JNLTYP 03（单据簿 SR 那一行的，没有用 03）：借 退货科目（SEQIT 1，税前）、借 销项税（SEQIT 2）/ 贷 现金（SEQIT 3，含税，账套现金科目
  ACCNUM00），成本回冲 借 存货（SEQIT 8）/ 贷 销货成本（SEQIT 9）；摘要「ลดหนี้/รับคืน + 客户名」；
- 销项税 ISVAT：VATREC S、RECTYP 5、AMT01 / VAT01 正数、REFNUM = SR 自己的单号（混有免税商品时金额栏照载荷 ``vat_fields``，同 HS）；
- ISRUN SR 计数 +1；ARBAL 的 SR 月桶加、ARMAS.BALANCE 减（实录现象：退现金也冲减客户余额，照做不修正）；
- 退款里非现金的部分：同 HS 另记普通日记账凭证，方向相反「借 现金 / 贷 收款科目」（``pos_journal`` ``reverse``）。

单号：SR + 佛历年月日 + '-' + 当日序号（同 CC 写的其他单）。写前只读找原单：ARTRN 里这张现销 HS（RECTYP 1、没作废）找不到 →
``REFUND_ORIGINAL_NOT_FOUND``；含税合计与 ERP 记的对不上（在 Express 里被改过）→ ``REFUND_ORIGINAL_CHANGED``；每一行按存货码 + 仓库在
原单 STCRD 里找那一行，找不到 → ``REFUND_LINE_NOT_FOUND``；扣掉以前退货单（RDOCNUM 指向它）已退的数量后不够退 → ``REFUND_EXCEEDS_ORIGINAL``。
这些都整单拒、一行不写（转人工）。写入在一份写前备份里：任何一步失败整单回滚；写完读回单头、明细、凭证、销项税、各仓结存，不符同样回滚。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import dbf

from . import account_rules
from . import cdx_reindex
from . import dbf_detail
from . import dbf_schema as S
from . import fiscal_period
from . import master_create
from . import pos_journal
from . import stock_location
from . import stock_sale
from . import stock_units
from .dbf_writer import (
    ERR_FIELD_WIDTH,
    ERR_LOCATION,
    ERR_VERIFY,
    DbfWriteResult,
    StepClock,
    _WriteError,
    _backup,
    _bump_isrun,
    _department_accounts,
    _discard_backup,
    _error_code,
    _f,
    _gl_line,
    _next_docnum,
    _open,
    _reindex_or_defer,
    _restore,
    _verify_index,
    amount_fields,
    audit_fields,
    record_locations,
    stock_location_tables,
    verify_locations,
)
from .logger import get_logger

log = get_logger(__name__)

PREFIX = "SR"
RECTYP = "5"            # ARTRN / ISVAT：退货 / 减价
CNTYP_CASH = "3"        # 退现金（实录 SR0000001）
POSOPR = "2"            # STCRD：退货
FLAG = "2"
RETSTK = "Y"            # 货回仓
ORIGINAL_RECTYP = "1"   # 原单：现销 HS
RDOCNUM_PAD = 14        # RDOCNUM：原单号左对齐补空格到 14 位，后接原单行号
SEQIT = {"refund": "1", "output_vat": "2", "cash": "3"}
SEQIT_INVENTORY, SEQIT_COGS = "8", "9"
DESCRIPTION = "ลดหนี้/รับคืน"
ERR_ORIGINAL_NOT_FOUND = "REFUND_ORIGINAL_NOT_FOUND"
ERR_ORIGINAL_CHANGED = "REFUND_ORIGINAL_CHANGED"
ERR_LINE_NOT_FOUND = "REFUND_LINE_NOT_FOUND"
ERR_EXCEEDS = "REFUND_EXCEEDS_ORIGINAL"
ERR_STOCK_ITEM = "STOCK_ITEM_NOT_FOUND"
REFUSALS = (ERR_ORIGINAL_NOT_FOUND, ERR_ORIGINAL_CHANGED, ERR_LINE_NOT_FOUND, ERR_EXCEEDS)
_CENT = Decimal("0.01")
_TOLERANCE = Decimal("0.01")
_VERIFY_TABLES = ("ARTRN", "STCRD", "GLJNLIT", "ISVAT")


class Refused(Exception):
    """写前核原单不过：整单不写，``code`` 是 :data:`REFUSALS` 之一（或存货认不出）。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class _Origin:
    seq: str
    stkcod: str
    loccod: str
    qty: Decimal     # 原单这一行的库存单位数量（XTRNQTY）
    value: Decimal   # 原单这一行的成本（XTRNVAL）
    unit_cost: Decimal


@dataclass(frozen=True)
class _Line:
    seq: int
    item: Dict[str, Any]
    stkcod: str
    loccod: str
    units: stock_units.Units
    unit_cost: Decimal   # 每个库存单位的成本（原单那一行）
    cost: Decimal        # 本行回冲的成本
    gross: Decimal       # 本行含税额
    net: Decimal         # 本行税前额
    rdocnum: str
    accounts: Tuple[str, str]  # (存货科目, 销货成本科目)；成本为 0 时空


def _q2(value) -> Decimal:
    return Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_UP)


def _dec(value) -> Decimal:
    try:
        number = Decimal(str(value if value not in (None, "") else 0))
    except Exception:  # noqa: BLE001
        return Decimal(0)
    return number if number.is_finite() else Decimal(0)


def _text(rec, name: str) -> str:
    try:
        return str(rec[name] or "").strip()
    except (KeyError, dbf.FieldMissingError):
        return ""


def _fields(table) -> set:
    return {name.upper() for name in table.field_names}


def _filtered(table, row: Dict[str, Any]) -> Dict[str, Any]:
    """只写这张表有的列（老账套没有 CNTYP / SONUM / RDOCNUM / RETSTK / FLAG / BALANCE 时照旧写其余的）。"""
    fields = _fields(table)
    return {key: value for key, value in row.items() if key.upper() in fields}


def _fit(text: str, width: int) -> str:
    return pos_journal.fit(text, width).strip()


def rdocnum(against: str, seq) -> str:
    """原单号左对齐补空格到 14 位 + 原单行号（实录「HS0000039     1」；行号多位时照接在后面，待实录核对）。"""
    return f"{against.ljust(RDOCNUM_PAD)}{str(seq).strip()}"


# ── 写前只读：原单 ─────────────────────────────────────────────────────────────────────────────────────


def original(account_dir, against: str, against_total: Decimal) -> str:
    """ARTRN 里这张原单（现销 HS、没作废）→ 它的客户码；找不到 / 含税合计对不上抛 :class:`Refused`。"""
    table = _open(Path(account_dir), "ARTRN", dbf.READ_ONLY)
    try:
        rows = [r for r in S.live_rows(table) if _text(r, "DOCNUM") == against and _text(r, "RECTYP") == ORIGINAL_RECTYP
                and _text(r, "DOCSTAT") != "C"]
    finally:
        table.close()
    if not rows:
        raise Refused(ERR_ORIGINAL_NOT_FOUND, f"cash sale {against} is not in Express (or it was cancelled)")
    found = _dec(rows[0]["NETAMT"])
    if abs(found - against_total) > _TOLERANCE:
        raise Refused(ERR_ORIGINAL_CHANGED, f"cash sale {against} totals {found} in Express, ERP has {against_total}")
    return _text(rows[0], "CUSCOD")


def _origins(account_dir, against: str) -> List[_Origin]:
    table = _open(Path(account_dir), "STCRD", dbf.READ_ONLY)
    try:
        out = []
        for r in S.live_rows(table):
            if _text(r, "DOCNUM") != against:
                continue
            qty, value = _dec(r["XTRNQTY"]), _dec(r["XTRNVAL"])
            out.append(_Origin(_text(r, "SEQNUM"), _text(r, "STKCOD"), _text(r, "LOCCOD"), qty, value,
                               value / qty if qty else _dec(r["XUNITPR"])))
    finally:
        table.close()
    return sorted(out, key=lambda row: (int(row.seq) if row.seq.isdigit() else 0, row.seq))


def _returned(account_dir, against: str) -> Dict[str, Decimal]:
    """以前的退货单已经退了原单各行多少（RDOCNUM 指向原单的退货行，库存单位）。表没有 RDOCNUM 一栏时当没退过。"""
    table = _open(Path(account_dir), "STCRD", dbf.READ_ONLY)
    try:
        if "RDOCNUM" not in _fields(table):
            return {}
        head = against.ljust(RDOCNUM_PAD)
        out: Dict[str, Decimal] = {}
        for r in S.live_rows(table):
            link = str(r["RDOCNUM"] or "")
            if _text(r, "POSOPR") == POSOPR and link.startswith(head) and link[:RDOCNUM_PAD].strip() == against:
                seq = link[RDOCNUM_PAD:].strip()
                out[seq] = out.get(seq, Decimal(0)) + _dec(r["XTRNQTY"])
        return out
    finally:
        table.close()


def _gross(items: List[Dict[str, Any]], total: Decimal) -> List[Decimal]:
    """各行含税额：按税前额占比分摊单据含税合计，尾差落最后一行，合计恒等于含税合计（载荷只带各行税前额）。"""
    nets = [_dec(item.get("amount")) for item in items]
    whole = sum(nets, Decimal(0))
    if whole <= 0:
        return [Decimal(0)] * (len(items) - 1) + [total]
    out = [_q2(net * total / whole) for net in nets[:-1]]
    return out + [total - sum(out, Decimal(0))]


def plan(account_dir, entry, items: List[Dict[str, Any]], locations) -> List[_Line]:
    """每一行退货挂原单哪一行、成本多少（只读）。认不出 / 不够退抛 :class:`Refused`。"""
    against = entry.refund_against
    origins = _origins(account_dir, against)
    used = _returned(account_dir, against)
    main = account_rules.main_location(account_dir)
    grosses = _gross(items, Decimal(entry.total_amount))
    lines: List[_Line] = []
    for index, item in enumerate(items):
        code = str(item.get("code") or "").strip()
        loccod = stock_location.location_at(locations, index) or str(item.get("loccod") or "").strip() or main
        try:
            master = stock_sale.find_stock_item(account_dir, str(item.get("name") or ""), code)
        except stock_sale.StockItemNotFound as exc:
            raise Refused(ERR_STOCK_ITEM, f"line {index + 1}: {exc}") from exc
        if not master:
            raise Refused(ERR_STOCK_ITEM, f"line {index + 1}: {code!r} is not a stock item in Express")
        candidates = [row for row in origins if row.stkcod == code and row.loccod == loccod]
        if not candidates:
            raise Refused(ERR_LINE_NOT_FOUND, f"line {index + 1}: {code} at {loccod} is not on cash sale {against}")
        units = stock_units.resolve(account_dir, code, str(item.get("unit") or ""), _dec(item.get("qty")),
                                    item.get("factor"))
        pick = next((row for row in candidates if row.qty - used.get(row.seq, Decimal(0)) >= units.base_qty), None)
        if pick is None:
            left = sum((row.qty - used.get(row.seq, Decimal(0)) for row in candidates), Decimal(0))
            raise Refused(ERR_EXCEEDS, f"line {index + 1}: returning {units.base_qty} of {code} but only {left} "
                                       f"left to return on cash sale {against}")
        used[pick.seq] = used.get(pick.seq, Decimal(0)) + units.base_qty
        cost = _q2(units.base_qty * pick.unit_cost)
        accounts = ("", "")
        if cost:
            try:
                accounts = stock_sale.stock_accounts(account_dir, master.get("acccod") or "")
            except stock_sale.StockItemNotFound as exc:
                raise Refused(ERR_STOCK_ITEM, f"line {index + 1}: {exc}") from exc
        lines.append(_Line(index + 1, item, code, loccod, units, pick.unit_cost, cost, grosses[index],
                           _dec(item.get("amount")), rdocnum(against, pick.seq), accounts))
    return lines


# ── 写 ──────────────────────────────────────────────────────────────────────────────────────────────


def write_refund(payload: Dict[str, Any], account_dir, entry, *, userid: str = S.PEARNLY_USERID, depcod: str = "",
                 make_backup: bool = True, index_batch=None) -> DbfWriteResult:
    """把一张收银退货单直写进账套 DBF（``write_sale`` 已核过载荷、写盘目录、账套名与公司）。失败整体回滚，绝不留半张。"""
    from .dbf_sales import _field_overflow, _table_sizes, _update_arbal  # 同销项共用（避免循环导入）

    clock = StepClock("refund")
    account_dir = Path(account_dir)
    overflow = _field_overflow(entry)
    if overflow:
        return DbfWriteResult(False, error_code=ERR_FIELD_WIDTH, error=overflow)
    journal, journal_error, journal_reason = pos_journal.check(account_dir, entry.pos_transfers)
    if journal_error:
        return DbfWriteResult(False, error_code=journal_error, error=journal_reason)
    try:
        locations = stock_location.plan(account_dir, payload)
    except stock_location.LocationError as exc:
        return DbfWriteResult(False, error_code=ERR_LOCATION, error=str(exc))
    docdate_be = str(payload["docdate_be"])
    docdat = S.be_yymmdd_to_greg(docdate_be)
    if payload.get("respect_period_lock") and fiscal_period.locked(account_dir, docdat):
        return DbfWriteResult(False, error_code=fiscal_period.ERR_PERIOD_LOCKED,
                              error=f"accounting period of {docdat} is locked in Express")
    try:
        cuscod = original(account_dir, entry.refund_against, Decimal(entry.refund_against_total))
        lines = plan(account_dir, entry, list(entry.items), locations)
    except Refused as exc:
        return DbfWriteResult(False, error_code=exc.code, error=str(exc))
    clock.lap("checks")
    backup_dir = _backup(account_dir, S.SALES_BACKUP_TABLES + stock_location_tables(locations)) if make_backup else None
    clock.lap("backup")
    docnum = ""
    moves = [] if locations is not None else None
    try:
        since = _table_sizes(account_dir)
        journal_since = pos_journal.sizes(account_dir) if journal else {}
        vatprd = S.be_yymmdd_to_greg(payload.get("vat_period_be") or docdate_be)
        docnum, gl_rows, cash_acc, line_modes = _write_all(account_dir, entry, cuscod, lines, docdate_be, docdat,
                                                           vatprd, userid=userid, depcod=depcod, moves=moves)
        notes = _update_arbal(account_dir, cuscod, docdat, PREFIX, Decimal(entry.total_amount))
        _reduce_balance(account_dir, cuscod, Decimal(entry.total_amount))
        stloc_expected, notes = record_locations(account_dir, moves, notes)
        vouchers = []
        if journal:
            voucher_dep = depcod or account_rules.book(account_dir, journal).get("DEPCOD", "")
            vouchers = pos_journal.write(
                account_dir, journal, docnum=docnum, docdat=docdat, docdate_be=docdate_be, cash_acc=cash_acc,
                transfers=entry.pos_transfers, userid=userid, depcod=voucher_dep,
                departmental=_department_accounts(account_dir) if voucher_dep else frozenset(), reverse=True)
            notes = {**(notes or {}), "pos_vouchers": [item["voucher"] for item in vouchers]}
        clock.lap("write")
        _reindex_or_defer(account_dir, S.SALES_WRITE_TABLES, len(lines), "ARMAS", index_batch,
                          **({"more": S.LOCATION_TABLES} if stloc_expected else {}))
        clock.lap("reindex")
        _verify(account_dir, docnum, entry, len(lines), gl_rows, since)
        verify_locations(account_dir, stloc_expected)
        if vouchers:
            pos_journal.verify(account_dir, vouchers, cash_acc, journal_since, reverse=True)
        clock.lap("verify")
        _verify_index(account_dir, cdx_reindex.TAG_AR_DOCNUM, docnum)
        clock.lap("index_check")
        _discard_backup(backup_dir)
        clock.log(docnum, "ok")
        return DbfWriteResult(True, docnum=docnum, party_code=cuscod, line_modes=line_modes, notes=notes)
    except Exception as exc:  # 任何失败 → 回滚还原
        clock.lap("failed_step")
        if backup_dir:
            _restore(account_dir, backup_dir)
            _discard_backup(backup_dir)
        clock.log(docnum, "rolled_back")
        log.exception("refund write failed, rolled back")
        return DbfWriteResult(False, error_code=_error_code(exc), error=str(exc),
                              backup_dir=str(backup_dir) if backup_dir else None,
                              evidence=exc.evidence if isinstance(exc, _WriteError) else None)


def _write_all(account_dir, entry, cuscod: str, lines: List[_Line], docdate_be: str, docdat, vatprd, *, userid: str,
               depcod: str, moves) -> Tuple[str, List[Tuple[str, str, str, Decimal]], str, List[Dict]]:
    """单头、明细、主档结存、销项税、凭证、计数器 → (单号, 写下的分录 [(SEQIT, 科目, 借贷, 金额)], 现金科目, 逐行回执)。"""
    base, vat, total = Decimal(entry.base_amount), Decimal(entry.vat_amount), Decimal(entry.total_amount)
    has_vat = vat > 0
    book = account_rules.book(account_dir, PREFIX)
    jnltyp = book.get("JNLTYP") or S.JNLTYP_SALES
    depcod = depcod or book.get("DEPCOD", "")
    name = entry.customer_name or cuscod
    orgnum = master_create.party_orgnum(account_dir, "ARMAS", "CUSCOD", cuscod)
    flgvat, amount, aftdisc, total_, netval, netamt = amount_fields(True, has_vat, base, vat, total)

    head = _open(account_dir, "ARTRN", dbf.READ_WRITE)
    try:
        docnum = _next_docnum(head, PREFIX, docdate_be)
        head.append(_filtered(head, {
            "RECTYP": RECTYP, "DOCNUM": docnum, "DOCDAT": docdat, "YOUREF": entry.ref_no, "FLGVAT": flgvat,
            "CUSCOD": cuscod, "VATRAT": float(entry.vat_rate), "AMOUNT": amount, "AFTDISC": aftdisc, "TOTAL": total_,
            "NETVAL": netval, "VATAMT": _f(vat), "NETAMT": netamt, "RCVAMT": netamt, "REMAMT": 0.0, "CSHRCV": 0.0,
            "IVCAMT": _f(entry.refund_against_total), "DOCSTAT": "N", "USERID": userid,
            "CNTYP": CNTYP_CASH, "SONUM": entry.refund_against,
            **audit_fields(docdat, depcod, has_vat, orgnum), "NXTSEQ": str(len(lines))}))
    finally:
        head.close()

    line_modes: List[Dict] = []
    cogs: Dict[Tuple[str, str], Decimal] = {}
    balances: Dict[str, Tuple[Decimal, Decimal]] = {}
    table = _open(account_dir, "STCRD", dbf.READ_WRITE)
    try:
        for line in lines:
            item, units = line.item, line.units
            master = stock_sale.find_stock_item(account_dir, str(item.get("name") or ""), line.stkcod)
            bal, val = balances.get(line.stkcod, (_dec(master["mrembal"]), _dec(master["mremval"])))
            new_bal, new_val = bal + units.base_qty, _q2(val + line.cost)
            balances[line.stkcod] = (new_bal, new_val)
            unitpr = _q2(line.gross / units.qty) if units.qty else line.gross
            row = account_rules.localize_stcrd(account_dir, {
                "STKCOD": line.stkcod, "DOCNUM": docnum, "SEQNUM": str(line.seq), "DOCDAT": docdat, "REFNUM": "",
                "DEPCOD": depcod, "POSOPR": POSOPR, "PEOPLE": cuscod, "TRNQTY": float(units.qty),
                "UNITPR": float(unitpr), "DISCAMT": 0.0, "TRNVAL": float(line.gross), "NETVAL": float(line.net),
                "XUNITPR": float(_q2(line.unit_cost)), "XTRNVAL": float(line.cost), "XSALVAL": float(line.net),
                "STKDES": str(item.get("name") or "")[:50], "MREMBAL": float(new_bal), "MREMVAL": float(new_val),
                "RDOCNUM": line.rdocnum, "RETSTK": RETSTK, "FLAG": FLAG,
            }, units.unit, line.loccod)
            stock_units.stamp(row, units)
            table.append(_filtered(table, row))
            if moves is not None:
                moves.append(stock_location.move(row, stock_location.KIND_RETURN))
            stock_sale.write_stmas_fields(account_dir, line.stkcod, {"MREMBAL": float(new_bal), "MREMVAL": float(new_val),
                                                                     "TOTBAL": float(new_bal), "TOTVAL": float(new_val)})
            if line.cost:
                cogs[line.accounts] = cogs.get(line.accounts, Decimal(0)) + line.cost
            line_modes.append(dbf_detail.line_mode(line.seq, str(item.get("name") or ""), line.stkcod,
                                                   mode=stock_sale.ITEM_MODE))
    finally:
        table.close()

    if has_vat:
        tax = _open(account_dir, "ISVAT", dbf.READ_WRITE)
        try:
            columns = _fields(tax)
            given = {key: _f(value) for key, value in entry.vat_fields.items() if key in columns}
            tax.append(_filtered(tax, {
                "VATREC": S.ISVAT_VATREC_OUTPUT, "RECTYP": RECTYP, "VATPRD": vatprd, "VATDAT": docdat, "DOCDAT": docdat, "DOCNUM": docnum,
                "REFNUM": docnum, "DESCRP": _fit(name, 60), "AMT01": _f(base), "VAT01": _f(vat), "DOCSTAT": "N",
                "TAXID": entry.customer_tax_id, "PRENAM": entry.customer_prename[:15], "ORGNUM": int(orgnum or 0),
                **given}))
        finally:
            tax.close()

    cash_acc = _cash_account(account_dir, entry)
    gl_rows: List[Tuple[str, str, str, Decimal]] = []
    for ln in entry.lines:
        role = str(ln.get("role") or "")
        acc = cash_acc if role == "cash" else str(ln["acc"]).strip()
        side = S.TRNTYP_CREDIT if role == "cash" else S.TRNTYP_DEBIT
        gl_rows.append((SEQIT[role], acc, side, _q2(ln["amount"])))
    for (inventory_acc, cogs_acc), value in cogs.items():
        gl_rows.append((SEQIT_INVENTORY, inventory_acc, S.TRNTYP_DEBIT, _q2(value)))
        gl_rows.append((SEQIT_COGS, cogs_acc, S.TRNTYP_CREDIT, _q2(value)))
    descrp = _fit(f"{DESCRIPTION} {name}", 50)
    journal = _open(account_dir, "GLJNL", dbf.READ_WRITE)
    try:
        journal.append(_filtered(journal, {"JNLTYP": jnltyp, "VOUDAT": docdat, "VOUCHER": docnum, "DESCRP": descrp,
                                           "TRNSTAT": "P", "DOCSTAT": "N", "CREBY": userid}))
    finally:
        journal.close()
    departmental = _department_accounts(account_dir) if depcod else frozenset()
    entries = _open(account_dir, "GLJNLIT", dbf.READ_WRITE)
    try:
        for seqit, acc, side, value in gl_rows:
            entries.append(_filtered(entries, _gl_line(docnum, seqit, docdat, acc, descrp, side, value, depcod,
                                                       departmental)))
    finally:
        entries.close()
    _bump_isrun(account_dir, PREFIX, jnltyp)
    return docnum, gl_rows, cash_acc, line_modes


def _cash_account(account_dir, entry) -> str:
    """贷方现金科目：Express 原生记账套现金科目（ISINFO.ACCNUM00，可记账时）；否则用载荷给的（同 HS）。"""
    given = next((str(ln.get("acc") or "").strip() for ln in entry.lines if ln.get("role") == "cash"), "")
    cash = account_rules.default_account(account_dir, "ACCNUM00")
    return cash if cash and account_rules.postable_account(account_dir, cash) else given


def _reduce_balance(account_dir, cuscod: str, total: Decimal) -> None:
    """ARMAS.BALANCE 减退款额（实录 SR0000001：退现金也冲减客户余额）。老表没有这一列不动。"""
    table = _open(Path(account_dir), "ARMAS", dbf.READ_WRITE)
    try:
        if "BALANCE" not in _fields(table):
            return
        for r in S.live_rows(table):
            if _text(r, "CUSCOD") == cuscod:
                with r as rec:
                    rec["BALANCE"] = float(_dec(rec["BALANCE"]) - total)
                return
    finally:
        table.close()


def _verify(account_dir, docnum: str, entry, line_count: int, gl_rows, since: Dict[str, int]) -> None:
    """读回：单头一行（RECTYP 5、含税合计、行数）、明细行数（POSOPR 2）、凭证与要写的一致且借贷平衡、有税时销项税在。
    不符抛写盘错误（回滚）。只看这次追加的行。"""
    total = Decimal(entry.total_amount)

    def fail(what, table):
        raise _WriteError(f"refund {docnum}: {what}", ERR_VERIFY,
                          {"kind": "readback_verify", "table": table, "docnum": docnum})

    def appended(name):
        table = _open(Path(account_dir), name, dbf.READ_ONLY)
        try:
            return [{key: r[key] for key in table.field_names} for r in S.live_rows(table)
                    if dbf.recno(r) >= since.get(name, 0)]
        finally:
            table.close()

    heads = [r for r in appended("ARTRN") if str(r["DOCNUM"] or "").strip() == docnum]
    if (len(heads) != 1 or str(heads[0]["RECTYP"]).strip() != RECTYP or abs(_dec(heads[0]["NETAMT"]) - total) > _TOLERANCE
            or str(heads[0]["NXTSEQ"] or "").strip() != str(line_count)):
        fail(f"header rows {heads!r}", "ARTRN")
    rows = [r for r in appended("STCRD") if str(r["DOCNUM"] or "").strip() == docnum]
    if len(rows) != line_count or any(str(r["POSOPR"]).strip() != POSOPR for r in rows):
        fail(f"{len(rows)} detail rows, expected {line_count}", "STCRD")
    got = sorted((str(r["SEQIT"]).strip(), str(r["ACCNUM"]).strip(), str(r["TRNTYP"]).strip(), _q2(r["AMOUNT"]))
                 for r in appended("GLJNLIT") if str(r["VOUCHER"] or "").strip() == docnum)
    if got != sorted(gl_rows):
        fail(f"journal lines {got} differ from {sorted(gl_rows)}", "GLJNLIT")
    debit = sum((amount for _seq, _acc, side, amount in got if side == S.TRNTYP_DEBIT), Decimal(0))
    credit = sum((amount for _seq, _acc, side, amount in got if side != S.TRNTYP_DEBIT), Decimal(0))
    if debit != credit:
        fail(f"journal not balanced {debit} / {credit}", "GLJNLIT")
    if Decimal(entry.vat_amount) > 0 and not any(
            str(r["DOCNUM"] or "").strip() == docnum and str(r["VATREC"]).strip() == S.ISVAT_VATREC_OUTPUT
            for r in appended("ISVAT")):
        fail("output VAT row missing", "ISVAT")


__all__ = ["ERR_EXCEEDS", "ERR_LINE_NOT_FOUND", "ERR_ORIGINAL_CHANGED", "ERR_ORIGINAL_NOT_FOUND", "PREFIX", "REFUSALS",
           "Refused", "original", "plan", "rdocnum", "write_refund"]
