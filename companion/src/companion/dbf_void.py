# -*- coding: utf-8 -*-
"""作废 ERP 写进 Express 的库存单（领用 OU / 调整 JU / 盘点 TK；载荷 8，出入库第一期 B 段 ①，10-25；仓间调拨 RL，多仓闭环 P2）。

照 Express 原生作废的写法（服务器测试账套 ``70EXP\\test`` 实录 OU0000001，开发窗口逐字段比对副本 2b-iv → 2b-after）：在原单上作废，
不写冲销单——

- STTRN 单头留着：``DOCSTAT`` N → C、``TRNVAL`` → 0、``NXTSEQ`` → 空；
- STCRD 明细行打删除标记，``NETVAL`` 改成这一行的货值（Express 作废领用单时如此，SEQNUM 不变）；
- GLJNL 凭证头 ``DOCSTAT`` → C；GLJNLIT 各行 ``AMOUNT`` 改成空（null，不是 0）；
- STLOC 各仓结存、期桶按原单反向回滚（不动日期栏）；STMAS ``MREMBAL / MREMVAL / TOTBAL / TOTVAL`` 加回；
- ISRUN 计数器不回退（作废号不复用）；不要原因、不要密码。

调拨 RL 照原生作废（实录 RL0000003，副本 s12-rl3 → s12-rl3v）：单头 DOCSTAT C、NXTSEQ 空；那一对明细只打删除标记（字段不改）；
两仓 STLOC 按原单回滚（不动日期、金额栏）；STMAS 不动、没有凭证；ISRUN 不回退。

只作废 ERP 自己写进去、Express 里没被改过的单：写前按载荷带来的逐行（行号、存货码、仓库码、数量）核原单，对不上回
:data:`ERR_CHANGED` 转人工；原单已作废（DOCSTAT C）当作已完成；原单所在会计期间已关账回 ``PERIOD_LOCKED``。失败整体回滚。
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import dbf

from . import dbf_remarks
from . import dbf_schema as S
from . import fiscal_period
from . import stock_guard
from . import stock_location
from .consistency import account_company_matches
from .dbf_stock import COUNT, ISSUE, KINDS, _dec
from .dbf_transfer import TRANSFER
from .dbf_writer import (
    ERR_LOCATION,
    DbfWriteResult,
    StepClock,
    _WriteError,
    _backup,
    _discard_backup,
    _error_code,
    _open,
    _reindex,
    _restore,
)
from .logger import get_logger

log = get_logger(__name__)

DIRECTION = "stock_void"
ACTION = "void"
ERR_VALIDATION = "PAYLOAD_INVALID"
ERR_ACCOUNT_DIR = "ACCOUNT_DIR_NOT_ALLOWED"
ERR_COMPANY = "ACCOUNT_COMPANY_MISMATCH"
ERR_NOT_FOUND = "VOID_DOCUMENT_NOT_FOUND"
ERR_CHANGED = "VOID_DOCUMENT_CHANGED"
ERR_VERIFY = "READBACK_VERIFY_FAILED"
VOIDED = "C"
TABLES = ("STTRN", "STCRD", "STMAS", "GLJNL", "GLJNLIT")


def _text(rec, name: str) -> str:
    try:
        return str(rec[name] or "").strip()
    except (KeyError, dbf.FieldMissingError):
        return ""


def _validate(payload: Dict[str, Any]) -> Optional[str]:
    if str(payload.get("action") or "") != ACTION:
        return "action is not void"
    if str(payload.get("doctype") or "").strip().upper() not in (*KINDS, TRANSFER):
        return "doctype is not OU / JU / TK / RL"
    if not str(payload.get("docnum") or "").strip():
        return "docnum missing"
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return "items missing"
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict) or _dec(item.get("qty")) == 0:
            return f"line {index} is not valid"
    return None


def _head(account_dir: Path, docnum: str):
    table = _open(account_dir, "STTRN", dbf.READ_ONLY)
    try:
        rows = [{"DOCSTAT": _text(r, "DOCSTAT"), "DOCDAT": r["DOCDAT"], "POSOPR": _text(r, "POSOPR")}
                for r in S.live_rows(table) if _text(r, "DOCNUM") == docnum]
    finally:
        table.close()
    return rows


def _lines(account_dir: Path, docnum: str) -> List[dict]:
    table = _open(account_dir, "STCRD", dbf.READ_ONLY)
    try:
        rows = [{"SEQNUM": _text(r, "SEQNUM"), "STKCOD": _text(r, "STKCOD"), "LOCCOD": _text(r, "LOCCOD"),
                 "POSOPR": _text(r, "POSOPR"), "TRNQTY": _dec(r["TRNQTY"]), "XTRNQTY": _dec(r["XTRNQTY"])}
                for r in S.live_rows(table) if _text(r, "DOCNUM") == docnum]
    finally:
        table.close()
    return sorted(rows, key=lambda row: int(row["SEQNUM"]) if row["SEQNUM"].isdigit() else 0)


def _differences(lines: List[dict], items: List[dict]) -> List[str]:
    """原单明细与 ERP 写入时的逐行对不对得上：存货码 / 仓库码载荷里有才核，数量一定核（OU 正数、JU / TK 带正负号）。"""
    if len(lines) != len(items):
        return [f"STCRD {len(lines)} rows, expected {len(items)}"]
    out = []
    for seq, (row, item) in enumerate(zip(lines, items), start=1):
        code = str(item.get("code") or "").strip()
        loccod = str(item.get("loccod") or "").strip()
        if code and row["STKCOD"] != code:
            out.append(f"line {seq} STKCOD {row['STKCOD']!r} != {code!r}")
        if loccod and row["LOCCOD"] != loccod:
            out.append(f"line {seq} LOCCOD {row['LOCCOD']!r} != {loccod!r}")
        qty, factor = _dec(item.get("qty")), _dec(item.get("factor")) or Decimal(1)
        # 写入时认不出 Express 包装单位、按 ERP 换算率换成库存单位写的行，TRNQTY 是「数量 × 换算率」（stock_units）
        if row["TRNQTY"] not in (qty, qty * factor):
            out.append(f"line {seq} TRNQTY {row['TRNQTY']} != {item.get('qty')}")
    return out


def _transfer_differences(lines: List[dict], items: List[dict]) -> List[str]:
    """调拨 RL：每一行载荷对原单一对明细（调出 4 在 ``loccod``、调入 3 在另一个仓，同一件品、同数量）。"""
    if len(lines) != 2 * len(items):
        return [f"STCRD {len(lines)} rows, expected {2 * len(items)}"]
    out = []
    for seq, item in enumerate(items, start=1):
        pair = {row["POSOPR"]: row for row in lines if row["SEQNUM"] == str(seq)}
        source, target = pair.get(S.STCRD_POSOPR_TRANSFER_OUT), pair.get(S.STCRD_POSOPR_TRANSFER_IN)
        if not source or not target:
            out.append(f"line {seq} is not a transfer pair")
            continue
        code, loccod = str(item.get("code") or "").strip(), str(item.get("loccod") or "").strip()
        qty, factor = _dec(item.get("qty")), _dec(item.get("factor")) or Decimal(1)
        if code and {source["STKCOD"], target["STKCOD"]} != {code}:
            out.append(f"line {seq} STKCOD {source['STKCOD']!r} != {code!r}")
        if loccod and source["LOCCOD"] != loccod:
            out.append(f"line {seq} LOCCOD {source['LOCCOD']!r} != {loccod!r}")
        if any(row["TRNQTY"] not in (qty, qty * factor) for row in (source, target)):
            out.append(f"line {seq} TRNQTY != {item.get('qty')}")
    return out


def _shortages(account_dir: Path, lines: List[dict], kind: str) -> List[dict]:
    """作废入库 / 盘盈（JU、TK 的正数行）会把结存减回去：照写销货的负库存规则核（``stock_guard.short_of``，验收 10-25）。
    Express 原生作废 JU 时拦不拦还没实录（服务器测试账套第 8 条），先按写入同一口径拦下转人工，不写出 Express 不许的负数；领用 OU 作废是加回去，不核。"""
    if kind == ISSUE:
        return []
    need: Dict[Tuple[str, str], Decimal] = {}
    for row in lines:
        if kind == TRANSFER and row["POSOPR"] != S.STCRD_POSOPR_TRANSFER_IN:
            continue  # 作废调拨：调入仓那一行减回去要核，调出仓是加回去
        if row["XTRNQTY"] > 0:
            key = (row["STKCOD"], row["LOCCOD"] or stock_guard.ALL_LOCATIONS)
            need[key] = need.get(key, Decimal(0)) + row["XTRNQTY"]
    return stock_guard.short_of(account_dir, need, any(row["LOCCOD"] for row in lines))


def void_stock_entry(
    payload: Dict[str, Any],
    account_dir: str | Path,
    *,
    make_backup: bool = True,
    selected_dir: str = "",
    expected_company: str = "",
    **_ignored,
) -> DbfWriteResult:
    """作废一张 ERP 写进去的库存单（同 :func:`dbf_stock.write_stock_entry` 的签名与两道账套闸）。"""
    clock = StepClock(DIRECTION)
    problem = _validate(payload)
    if problem:
        return DbfWriteResult(False, error_code=ERR_VALIDATION, error=problem)
    account_dir = Path(account_dir)
    if S.path_ambiguity(account_dir) or S.path_ambiguity(selected_dir) or not S.account_dir_allowed(account_dir, selected_dir):
        return DbfWriteResult(False, error_code=ERR_ACCOUNT_DIR,
                              error=f"write target {account_dir} != selected account dir {selected_dir!r}")
    co_ok, co_reason = account_company_matches(account_dir, expected_company)
    if not co_ok:
        return DbfWriteResult(False, error_code=ERR_COMPANY, error=co_reason)
    docnum = str(payload["docnum"]).strip()
    doctype = str(payload["doctype"]).strip().upper()
    heads = _head(account_dir, docnum)
    if len(heads) != 1:
        return DbfWriteResult(False, error_code=ERR_NOT_FOUND, error=f"{docnum}: {len(heads)} STTRN rows")
    head = heads[0]
    if head["DOCSTAT"] == VOIDED:
        return DbfWriteResult(True, docnum=docnum, notes={"already_voided": True})
    if payload.get("respect_period_lock") and head["DOCDAT"] and fiscal_period.locked(account_dir, head["DOCDAT"]):
        return DbfWriteResult(False, error_code=fiscal_period.ERR_PERIOD_LOCKED,
                              error=f"accounting period of {head['DOCDAT']} is locked in Express")
    transfer = doctype == TRANSFER
    kind = TRANSFER if transfer else KINDS[doctype]
    wanted = (S.STCRD_POSOPR_TRANSFER_OUT if transfer else S.STCRD_POSOPR_ISSUE if kind == ISSUE
              else S.STCRD_POSOPR_COUNT if doctype == COUNT else S.STCRD_POSOPR_ADJUST)
    lines = _lines(account_dir, docnum)
    differ = (_transfer_differences if transfer else _differences)(lines, payload["items"])
    if head["POSOPR"] != wanted:
        differ.insert(0, f"STTRN POSOPR {head['POSOPR']!r} != {wanted!r}")
    if differ:
        return DbfWriteResult(False, error_code=ERR_CHANGED, error=f"{docnum} changed in Express: " + "; ".join(differ[:5]))
    short = _shortages(account_dir, lines, kind)
    if short:
        return DbfWriteResult(False, error_code=stock_guard.ERR_STOCK_INSUFFICIENT, error=stock_guard.message(short))
    has_stloc = stock_location._file(account_dir, stock_location.TABLE) is not None
    # 行附加说明（按台号码）：原生作废领用 / 调整等给行说明打删除标记，调拨 RL 保留（S 盘副本统计 157 留 : 5 删，11-02）
    remarks = () if transfer or not dbf_remarks.has_line_remarks(account_dir, docnum) else (dbf_remarks.TABLE,)
    clock.lap("checks")
    backup_dir = _backup(account_dir, TABLES + (S.LOCATION_TABLES if has_stloc else ()) + remarks) if make_backup else None
    clock.lap("backup")
    try:
        moves = _void_lines(account_dir, docnum, kind)
        if not transfer:  # 调拨不改全仓结存（STMAS 不动），也没有凭证
            _restore_masters(account_dir, moves)
        _void_head(account_dir, docnum)
        if not transfer:
            _void_journal(account_dir, docnum)
        if remarks:
            dbf_remarks.void(account_dir, docnum)
        expected = {}
        if has_stloc and moves:
            back = [_transfer_back(item) if transfer else stock_location.revert(item) for item in moves]
            try:
                expected, _notes = stock_location.apply(account_dir, back)
            except stock_location.LocationError as exc:
                raise _WriteError(str(exc), ERR_LOCATION) from exc
        clock.lap("write")
        _reindex(account_dir, TABLES + (S.LOCATION_TABLES if has_stloc else ()) + remarks)
        clock.lap("reindex")
        _verify(account_dir, docnum)
        if remarks and dbf_remarks.has_line_remarks(account_dir, docnum):
            raise _WriteError(f"{docnum}: line remarks still live after void", ERR_VERIFY)
        if expected:
            try:
                stock_location.verify(account_dir, expected)
            except stock_location.LocationVerifyError as exc:
                raise _WriteError(str(exc), ERR_VERIFY, exc.evidence) from exc
        clock.lap("verify")
        _discard_backup(backup_dir)
        clock.log(docnum, "voided")
        return DbfWriteResult(True, docnum=docnum)
    except Exception as exc:  # 任何失败 → 回滚还原
        if backup_dir:
            _restore(account_dir, backup_dir)
            _discard_backup(backup_dir)
        clock.log(docnum, "rolled_back")
        log.exception("stock entry void failed, rolled back")
        return DbfWriteResult(False, error_code=_error_code(exc), error=str(exc),
                              evidence=exc.evidence if isinstance(exc, _WriteError) else None)


def _transfer_back(item: stock_location.Move) -> stock_location.Move:
    """调拨那一行在 STLOC 上的反向（不动日期、金额栏，同原生作废 RL）。"""
    return stock_location.Move(item.stkcod, item.loccod, -item.qty, Decimal(0), item.docdat, stock_location.KIND_TRANSFER)


def _void_lines(account_dir: Path, docnum: str, kind: str) -> List[stock_location.Move]:
    """明细行打删除标记（NETVAL 记这一行的货值；调拨只打标记、字段不改），回原单的库存变动。"""
    move_kind = stock_location.KIND_ISSUE if kind == ISSUE else stock_location.KIND_ADJUST
    moves = []
    table = _open(account_dir, "STCRD", dbf.READ_WRITE)
    try:
        for rec in S.live_rows(table):
            if _text(rec, "DOCNUM") != docnum:
                continue
            row = {name: rec[name] for name in ("STKCOD", "LOCCOD", "XTRNQTY", "XTRNVAL", "TRNVAL", "DOCDAT")}
            if kind == TRANSFER:
                sign = -1 if _text(rec, "POSOPR") == S.STCRD_POSOPR_TRANSFER_OUT else 1
                moves.append(stock_location.Move(_text(rec, "STKCOD"), _text(rec, "LOCCOD"), sign * _dec(row["XTRNQTY"]),
                                                 Decimal(0), row["DOCDAT"], stock_location.KIND_TRANSFER))
                dbf.delete(rec)
                continue
            moves.append(stock_location.move(row, move_kind))
            with rec as fields:
                fields["NETVAL"] = float(_dec(row["TRNVAL"]))
            dbf.delete(rec)
    finally:
        table.close()
    return moves


def _restore_masters(account_dir: Path, moves: List[stock_location.Move]) -> None:
    """商品主档结存加回原单的变动（Express 作废时动 MREMBAL / MREMVAL / TOTBAL / TOTVAL 四栏）。"""
    back: Dict[str, Tuple[Decimal, Decimal]] = {}
    for item in moves:
        qty, value = back.get(item.stkcod, (Decimal(0), Decimal(0)))
        back[item.stkcod] = (qty - item.qty, value - item.value)
    table = _open(account_dir, "STMAS", dbf.READ_WRITE)
    try:
        for rec in S.live_rows(table):
            code = _text(rec, "STKCOD")
            if code not in back:
                continue
            qty, value = back.pop(code)
            with rec as fields:
                for name, delta in (("MREMBAL", qty), ("TOTBAL", qty), ("MREMVAL", value), ("TOTVAL", value)):
                    fields[name] = float(_dec(rec[name]) + delta)
    finally:
        table.close()
    if back:
        raise _WriteError(f"STMAS missing {sorted(back)}", ERR_CHANGED)


def _void_head(account_dir: Path, docnum: str) -> None:
    table = _open(account_dir, "STTRN", dbf.READ_WRITE)
    try:
        for rec in S.live_rows(table):
            if _text(rec, "DOCNUM") == docnum:
                with rec as fields:
                    fields["DOCSTAT"] = VOIDED
                    fields["TRNVAL"] = 0.0
                    fields["NXTSEQ"] = ""
    finally:
        table.close()


def _void_journal(account_dir: Path, docnum: str) -> None:
    """凭证头标 C、分录金额改成空（零金额 / 定期盘存账套没有凭证，什么都不动）。"""
    journal = _open(account_dir, "GLJNL", dbf.READ_WRITE)
    try:
        for rec in S.live_rows(journal):
            if _text(rec, "VOUCHER") == docnum:
                with rec as fields:
                    fields["DOCSTAT"] = VOIDED
    finally:
        journal.close()
    entries = _open(account_dir, "GLJNLIT", dbf.READ_WRITE)
    try:
        for rec in S.live_rows(entries):
            if _text(rec, "VOUCHER") == docnum:
                with rec as fields:
                    fields["AMOUNT"] = None
    finally:
        entries.close()


def _verify(account_dir: Path, docnum: str) -> None:
    def fail(what, table):
        raise _WriteError(f"void {docnum}: {what}", ERR_VERIFY, {"kind": "readback_verify", "table": table, "docnum": docnum})

    heads = _head(account_dir, docnum)
    if len(heads) != 1 or heads[0]["DOCSTAT"] != VOIDED:
        fail("header not voided", "STTRN")
    if _lines(account_dir, docnum):
        fail("detail rows still live", "STCRD")
    journal = _open(account_dir, "GLJNL", dbf.READ_ONLY)
    try:
        if any(_text(r, "DOCSTAT") != VOIDED for r in S.live_rows(journal) if _text(r, "VOUCHER") == docnum):
            fail("journal not voided", "GLJNL")
    finally:
        journal.close()
    entries = _open(account_dir, "GLJNLIT", dbf.READ_ONLY)
    try:
        if any(r["AMOUNT"] not in (None, 0) for r in S.live_rows(entries) if _text(r, "VOUCHER") == docnum):
            fail("journal amounts left", "GLJNLIT")
    finally:
        entries.close()


__all__ = ["ACTION", "ERR_CHANGED", "ERR_NOT_FOUND", "void_stock_entry"]
