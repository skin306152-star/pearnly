# -*- coding: utf-8 -*-
"""仓间调拨写进 Express：原生 RL「โอนย้ายระหว่างคลัง」（``direction = stock``、``doctype = RL``；ERP 多仓闭环 P2，10-30）。

写法照服务器测试账套 ``70EXP\\test`` 原生录的 RL0000001–0000003（副本 ``_srvtest_record\\s12-before → s12-rl2 → s12-rl3``，
`docs/work/2026-10-25-express-native-rules.md` 4.6 / 4.6b，10-30 拷回本机逐栏核过）：

- 单头 STTRN 一行：POSOPR 4、LOCCOD 调出仓、PEOPLE 调入仓、REMARK 单头说明、NXTSEQ 行数（右对齐 3 位）、DOCSTAT N、
  没有金额（TRNVAL 0）、没有参考号与科目；
- 明细 STCRD 每件一对，同单号、同行号：调出那行 POSOPR 4、LOCCOD 调出仓、PEOPLE 调入仓；调入那行 POSOPR 3、LOCCOD 调入仓、
  PEOPLE 调出仓；数量 TRNQTY / XTRNQTY、单位 TQUCOD / TFACTOR，**单价金额全空**；STKDES 是单头说明（没写说明时是品名）；
  REIMBURSE ``\\x00``；MREMBAL / MREMVAL 记这件品的主档结存（调拨不改全仓结存）；
- 各仓结存 STLOC：调出仓 LOCBAL 与当期数量减、调入仓加；调入仓没有这件品的行就新建（只有品码、仓、数量、STATUS A）；
  **不动日期栏、不动金额栏**（原生如此）；
- 商品主档 STMAS 不动；不写凭证；ISRUN 的 RL 号 +1（单据簿 DOCTYP / PREFIX 都是 RL，没有科目、日记账空）。
- 单号同 CC 写的其他库存单：``RL`` + 佛历年月日 + ``-`` + 当日序号。
- 批次链（MLOTNUM）不写：原生 RL 的 MLOTNUM 指 Express 自己的先进先出批次，录别的单时 Express 还会重排旧行（4.6b），
  同 CC 写的其他库存单交给 Express「计算成本」排。

认不出的行整单停 ``STOCK_ITEM_NOT_FOUND``，人在 ERP 里选了新建的行（``new_ok``，载荷 15）在写前备份里建 0 余额存货再调（同出入库单）；两个仓都必须在这套账的仓库代码表里、且不是同一个仓；账套不许负库存时按
调出仓结存核（``stock_guard``）。全程在一份写前备份里，任何一步失败整单回滚；写完读回单头、成对明细、各仓结存、确认没有凭证。
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

import dbf

from . import account_rules
from . import cdx_reindex
from . import dbf_detail
from . import dbf_remarks
from . import dbf_schema as S
from . import fiscal_period
from . import stock_chain
from . import stock_guard
from . import stock_location
from . import stock_sale
from . import stock_units
from .consistency import account_company_matches
from .dbf_stock import (ERR_BOOK, ERR_STOCK_ITEM, _creatable, _dec, _filtered, _fit, _next_docnum, _validate_common,
                        native_docnum, set_running,
                        create_new_items, new_item_shortages)
from .dbf_writer import (
    ERR_ACCOUNT_DIR,
    ERR_COMPANY,
    ERR_LOCATION,
    ERR_VALIDATION,
    ERR_VERIFY,
    StepClock,
    _WriteError,
    _backup,
    _bump_isrun,
    _discard_backup,
    _error_code,
    _open,
    _reindex,
    _restore,
    _verify_index,
    check_remarks,
)
from .logger import get_logger
from .write_result import DbfWriteResult

log = get_logger(__name__)

DIRECTION = "stock_transfer"
TRANSFER = "RL"
TABLES = ("STTRN", "STCRD", "ISRUN") + S.LOCATION_TABLES


def validate(payload: Dict[str, Any]) -> Optional[str]:
    """载荷形状：调出 / 调入仓码都要有、不同；每行数量为正。"""
    problem = _validate_common(payload)
    if problem:
        return problem
    source = str(payload.get("loccod") or "").strip()
    target = str(payload.get("to_loccod") or "").strip()
    if not source or not target:
        return "RL needs both loccod and to_loccod"
    if source == target:
        return "RL loccod and to_loccod are the same location"
    for index, item in enumerate(payload["items"], start=1):
        if _dec(item.get("qty")) <= 0:
            return f"line {index} qty {item.get('qty')!r} is not valid for RL"
        if str(item.get("loccod") or source).strip() != source:
            return f"line {index} loccod differs from the header source location"
    return None


def book_row(account_dir) -> Optional[Dict[str, str]]:
    """ISRUN 里 RL 那一行（DOCTYP RL、PREFIX RL）：``{prefix, jnltyp}``；没有或有几行不同 → None。"""
    table = account_rules._open(account_dir, "ISRUN")
    if table is None:
        return None
    try:
        found = {account_rules._gv(r, "JNLTYP") for r in S.live_rows(table)
                 if account_rules._gv(r, "DOCTYP") == TRANSFER and account_rules._gv(r, "PREFIX") == TRANSFER}
    finally:
        table.close()
    if len(found) != 1:
        return None
    return {"prefix": TRANSFER, "jnltyp": found.pop() or ""}


def write_transfer(
    payload: Dict[str, Any],
    account_dir: str | Path,
    *,
    userid: str = S.PEARNLY_USERID,
    make_backup: bool = True,
    selected_dir: str = "",
    expected_company: str = "",
    today: Optional[_dt.date] = None,
    **_ignored,
) -> DbfWriteResult:
    """把一张调拨直写进账套 DBF（同 :func:`dbf_stock.write_stock_entry` 的签名与两道账套闸）。失败整体回滚，绝不留半截单。"""
    clock = StepClock(DIRECTION)
    problem = validate(payload)
    if problem:
        return DbfWriteResult(False, error_code=ERR_VALIDATION, error=problem)
    remarks_error = dbf_remarks.problem(payload["items"])
    if remarks_error:
        return DbfWriteResult(False, error_code=remarks_error[0], error=remarks_error[1])
    account_dir = Path(account_dir)
    if S.path_ambiguity(account_dir) or S.path_ambiguity(selected_dir) or not S.account_dir_allowed(account_dir, selected_dir):
        return DbfWriteResult(False, error_code=ERR_ACCOUNT_DIR,
                              error=f"write target {account_dir} != selected account dir {selected_dir!r}")
    co_ok, co_reason = account_company_matches(account_dir, expected_company)
    if not co_ok:
        return DbfWriteResult(False, error_code=ERR_COMPANY, error=co_reason)
    docdate_be = str(payload["docdate_be"]).strip()
    docdat = S.be_yymmdd_to_greg(docdate_be)
    if payload.get("respect_period_lock") and fiscal_period.locked(account_dir, docdat):
        return DbfWriteResult(False, error_code=fiscal_period.ERR_PERIOD_LOCKED,
                              error=f"accounting period of {docdat} is locked in Express")
    book = book_row(account_dir)
    if book is None:
        return DbfWriteResult(False, error_code=ERR_BOOK, error="no single RL book (DOCTYP RL) in ISRUN")
    source, target = str(payload["loccod"]).strip(), str(payload["to_loccod"]).strip()
    known = stock_location.warehouses(account_dir)
    missing = sorted(code for code in (source, target) if code not in known)
    if missing:
        return DbfWriteResult(False, error_code=ERR_LOCATION,
                              error=f"location {', '.join(missing)} is not in this account set's location table (ISTAB 21)")
    if stock_location._file(account_dir, stock_location.TABLE) is None:
        return DbfWriteResult(False, error_code=ERR_LOCATION, error="this account set has no STLOC table")
    items = payload["items"]
    found: List[Dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        try:
            match = stock_sale.find_stock_item(account_dir, str(item.get("name") or ""), str(item.get("code") or ""))
        except stock_sale.StockItemNotFound as exc:
            return DbfWriteResult(False, error_code=ERR_STOCK_ITEM, error=f"line {index}: {exc}")
        if not match and _creatable(item):
            found.append(None)  # 人在 ERP 里选了新建：写前备份里再建（`create_new_items`）
            continue
        if not match:
            return DbfWriteResult(False, error_code=ERR_STOCK_ITEM,
                                  error=f"line {index}: {item.get('name') or item.get('code')!r} is not a stock item in Express")
        found.append(match)
    # 不许负库存的账套：调出仓按销货同一个规则核（调入那边是加，不核）
    short = stock_guard.shortages(account_dir, [{**item, "item_mode": "stock_sale"} for item in items], [source] * len(items),
                                  new_rows=False) \
        or new_item_shortages(account_dir, items, found, lambda item: True, source)  # 要新建的存货调出仓结存是 0
    if short:
        return stock_guard.refusal(account_dir, short)
    remark = _fit(payload.get("remark") or "", 50)
    clock.lap("checks")
    # 要建新存货时主档也进写前备份（调拨本身不动 STMAS）
    by_place = stock_chain.applies(account_dir)  # 分仓计价账套：两个仓的批次链都在 STLOC，主档抄调入仓的链（11-19）
    backup_dir = _backup(account_dir, TABLES + (("STMAS",) if None in found or by_place else ()) + dbf_remarks.tables(items)) \
        if make_backup else None
    clock.lap("backup")
    docnum = ""
    try:
        today = today or _dt.date.today()
        create_new_items(account_dir, items, found, userid=userid, acccod=str(payload.get("stock_acccod") or "").strip())
        docnum, following = native_docnum(account_dir, TRANSFER, TRANSFER)  # 原生流水号（用户 11-18 定 D5）
        chain_out: dict = {}
        docnum, lines, moves = _write(account_dir, docdate_be, docdat, remark, items, found, source, target,
                                      userid=userid, today=today, docnum=docnum, chain_out=chain_out)
        set_running(account_dir, TRANSFER, TRANSFER, following)
        try:
            expected, notes = stock_location.apply(account_dir, moves)
        except stock_location.LocationError as exc:
            raise _WriteError(str(exc), ERR_LOCATION) from exc
        chain_expected = stock_chain.write(account_dir, chain_out.get("after", {}), chain_out.get("values", {}))
        remarks = dbf_remarks.write(account_dir, docnum, enumerate(items, start=1))  # 按台号码：一对明细同一个行号，说明挂一次
        clock.lap("write")
        _reindex(account_dir, ("STTRN", "STCRD", "ISRUN") + S.LOCATION_TABLES + dbf_remarks.tables(items))
        clock.lap("reindex")
        _verify(account_dir, docnum, found, source, target)
        check_remarks(account_dir, docnum, remarks)
        try:
            stock_location.verify(account_dir, expected)
        except stock_location.LocationVerifyError as exc:
            raise _WriteError(str(exc), ERR_VERIFY, exc.evidence) from exc
        chain_problems = stock_chain.verify(account_dir, chain_expected)
        if chain_problems:
            raise _WriteError("; ".join(chain_problems[:5]), ERR_VERIFY)
        clock.lap("verify")
        _verify_index(account_dir, cdx_reindex.TAG_STCRD_DOC, docnum)
        clock.lap("index_check")
        _discard_backup(backup_dir)
        clock.log(docnum, "ok")
        return DbfWriteResult(True, docnum=docnum, line_modes=lines, notes=notes)
    except Exception as exc:  # 任何失败 → 回滚还原
        clock.lap("failed_step")
        if backup_dir:
            _restore(account_dir, backup_dir)
            _discard_backup(backup_dir)
        clock.log(docnum, "rolled_back")
        log.exception("stock transfer write failed, rolled back")
        return DbfWriteResult(False, error_code=_error_code(exc), error=str(exc),
                              backup_dir=str(backup_dir) if backup_dir else None,
                              evidence=exc.evidence if isinstance(exc, _WriteError) else None)


def _write(account_dir, docdate_be, docdat, remark, items, found, source, target, *, userid, today, docnum="", chain_out=None):
    """单头 + 成对明细 → (单号, 逐行结果, 各仓结存变动)。``chain_out``：分仓计价账套写后两个仓的批次链（``stock_chain``）。"""
    if not docnum:
        head = _open(account_dir, "STTRN", dbf.READ_WRITE)
        try:
            docnum = _next_docnum(head, TRANSFER, docdate_be)
        finally:
            head.close()
    lines: List[Dict] = []
    moves: List[stock_location.Move] = []
    # 原生调拨两行都带批次指针（主档当前那一批，11-18 实录 RL0000004）；Express 之后重算批次链时把调拨算进链里
    from .stock_costing import average_cost as _cost, lot_heads as _lot_heads  # 与库存单、销货、进货共用一套取价
    heads = _lot_heads(account_dir, {match["stkcod"] for match in found if match})
    # 分仓计价的账套（ISINFO.LOC_COST = Y）原生调拨带金额：两行按出库成本计价，调入那一行成为调入仓的新一批（11-18 先进先出、分仓计价测试账套
    # 实录 RL2300610；不分仓计价的 70EXP 实录 RL0000004 两行都不带金额）
    loc_cost = str((account_rules._first(account_dir, "ISINFO") or {}).get("LOC_COST") or "").strip().upper() == "Y"
    # 分仓计价且有 STLOC：两个仓各自的批次链（11-19 实录）——调出行记调出仓的链写前结存与指针、按调出仓的批取价（先进先出），
    # 调入那一批成为调入仓链上最新的一批；写后两个仓的链记进 STLOC，主档抄调入仓的链
    by_place = loc_cost and stock_chain.applies(account_dir)
    codes = {match["stkcod"] for match in found if match}
    chains = stock_chain.read(account_dir, {(code, loc) for code in codes for loc in (source, target)}) if by_place else {}
    fifo = by_place and str((account_rules._first(account_dir, "ISINFO") or {}).get("COSMTD") or "").strip().upper() == "F"
    place_lots = stock_chain.fifo_lots(account_dir, {key: chains[key] for key in chains if key[1] == source}) if fifo else {}
    # 原生存单即重算当天（``stock_chain`` 11-20 实录）：调出行（4）排到调出仓当天已有行之前时记开日状态、按开日的批取价
    firsts = stock_chain.day_firsts(account_dir, {(code, source) for code in codes}, docdat) if fifo else {}
    early_lots: dict = {}
    early_state: dict = {}
    place_values: dict = {}
    total = Decimal(0)
    table = _open(account_dir, "STCRD", dbf.READ_WRITE)
    try:
        for seq, (item, match) in enumerate(zip(items, found), start=1):
            stkcod = match["stkcod"]
            units = stock_units.resolve(account_dir, stkcod, str(item.get("unit") or ""), _dec(item.get("qty")),
                                        item.get("factor"))
            unit_cost = _cost(account_dir, match) if loc_cost else Decimal(0)
            value = (abs(units.base_qty) * unit_cost).quantize(Decimal("0.01")) if loc_cost else Decimal(0)
            out_key, in_key = (stkcod, source), (stkcod, target)
            # 两个仓都记着链才按仓走（老账套有的存货链在主档上，``stock_chain.Chain.kept``）
            by_wh = by_place and chains[out_key].kept and chains[in_key].kept
            early = fifo and by_wh and (out_key in early_lots or stock_chain.ahead(
                firsts.get(out_key), stock_chain.order_of(S.STCRD_POSOPR_TRANSFER_OUT, docnum, seq)))
            if early and out_key not in early_lots:
                start = stock_chain.opening(firsts[out_key])
                early_lots[out_key] = stock_chain.fifo_lots(account_dir, {out_key: start}, before=docdat)[out_key]
                early_state[out_key] = (firsts[out_key].head, start.bal, start.val)
            out_before = early_state[out_key] if early else None
            if fifo and by_wh:
                from .stock_costing import fifo_cost as _fifo_cost, fifo_take as _fifo_take
                pool = early_lots if early else place_lots
                lots_left = pool.get(out_key, [])
                unit_cost, value = _fifo_cost(lots_left, abs(units.base_qty), Decimal(0))  # 出超的部分按 0（同出库，原生）
                pool[out_key] = _fifo_take(lots_left, abs(units.base_qty))
            if early:
                after = stock_chain.moved(stock_chain.opening(stock_chain.DayFirst((), *out_before)), -abs(units.base_qty), -value)
                early_state[out_key] = (stock_chain.stored_head(after), after.bal, after.val)
            total += value * 2 if loc_cost else value  # 原生单头货值 = 两行（调出 + 调入）之和（11-20 实录 RL2300610：2 × 712.8 = 1,425.6）
            for posopr, here, other, sign in ((S.STCRD_POSOPR_TRANSFER_OUT, source, target, -1),
                                              (S.STCRD_POSOPR_TRANSFER_IN, target, source, 1)):
                row = account_rules.localize_stcrd(account_dir, {
                    "STKCOD": stkcod, "DOCNUM": docnum, "SEQNUM": S.seqnum_text(seq), "DOCDAT": docdat,
                    "POSOPR": posopr, "PEOPLE": other, "TRNQTY": float(units.qty), "TFACTOR": 1.0,
                    "UNITPR": 0.0, "TRNVAL": 0.0, "XTRNQTY": float(units.base_qty),
                    "MREMBAL": float(_dec(match["mrembal"])), "MREMVAL": float(_dec(match["mremval"])),
                    "STKDES": remark or _fit(str(item.get("name") or ""), 50), "REIMBURSE": "\x00",
                    "MLOTNUM": stock_chain.row_head(heads.get(stkcod, ""), match["mrembal"]),
                }, units.unit, here)
                if out_before and sign < 0:  # 排在当天最前：记开日状态（原样）
                    row.update({"MREMBAL": float(out_before[1]), "MREMVAL": float(out_before[2]),
                                "MLOTNUM": stock_chain.row_head(out_before[0], out_before[1])})
                elif by_wh and sign < 0:  # 调出行：调出仓的链写前结存与指针
                    row.update({"MREMBAL": float(chains[out_key].bal), "MREMVAL": float(chains[out_key].val),
                                "MLOTNUM": stock_chain.row_head(chains[out_key].head, chains[out_key].bal)})
                stock_units.stamp(row, units)
                if loc_cost:
                    priced = {"UNITPR": float(unit_cost.quantize(Decimal("0.01"))), "TRNVAL": float(value),
                              "XUNITPR": float(unit_cost.quantize(Decimal("0.0001"))), "XTRNVAL": float(value),
                              "NETVAL": float(value) if sign > 0 else 0.0}
                    if sign > 0:  # 调入那一行成为新的一批（批号：日期 + 3 + 单号行号）
                        priced.update({"MLOTNUM": f"{docdat:%Y%m%d}{S.STCRD_POSOPR_TRANSFER_IN}{docnum:<12}{S.seqnum_text(seq)}",
                                       "MREMBAL": float(units.base_qty), "MREMVAL": float(value),
                                       "LOTBAL": float(units.base_qty), "LOTVAL": float(value),
                                       "LUNITPR": float(unit_cost.quantize(Decimal("0.01")))})
                    row.update(priced)
                if by_wh:
                    key = out_key if sign < 0 else in_key
                    chains[key] = stock_chain.moved(chains[key], sign * abs(units.base_qty), sign * value,
                                                    row["MLOTNUM"] if sign > 0 else "")
                    place_values[key] = place_values.get(key, Decimal(0)) + sign * value
                S.append_stcrd(table, _filtered(table, row))
                # 分仓计价：两个仓的期桶金额随货值增减、调入仓记最后变动日（11-20 实录）；不分仓计价照旧不动金额、日期栏
                moves.append(stock_location.Move(stkcod, here, sign * units.base_qty, sign * value if loc_cost else Decimal(0),
                                                 docdat, stock_location.KIND_TRANSFER_COSTED if loc_cost
                                                 else stock_location.KIND_TRANSFER))
            if by_wh:
                stock_sale.move_stmas(account_dir, stkcod, 0, 0, fields={"MLOTNUM": stock_chain.stored_head(chains[in_key])},
                                      lots=(chains[in_key].bal, chains[in_key].val))
            lines.append(dbf_detail.line_mode(seq, str(item.get("name") or ""), stkcod, mode=stock_sale.ITEM_MODE))
    finally:
        table.close()
    if by_place and chain_out is not None:
        chain_out.update({"after": {key: chains[key] for key in place_values}, "values": place_values})
    head = _open(account_dir, "STTRN", dbf.READ_WRITE)
    try:
        S.append_native(head, _filtered(head, {
            "DOCNUM": docnum, "DOCDAT": docdat, "POSOPR": S.STCRD_POSOPR_TRANSFER_OUT, "PEOPLE": target, "REMARK": remark,
            "LOCCOD": source, "TRNVAL": float(total), "NXTSEQ": str(len(items)), "DOCSTAT": "N", "USERID": userid, "CHGDAT": today}))
    finally:
        head.close()
    return docnum, lines, moves


def _verify(account_dir, docnum: str, found, source: str, target: str) -> None:
    """读回：单头一行（调出 / 调入仓对）；每个行号恰好一对（调出 4 在调出仓、调入 3 在调入仓、同一件品、同数量）；没有凭证。"""
    def fail(what, table):
        raise _WriteError(f"stock transfer {docnum}: {what}", ERR_VERIFY,
                          {"kind": "readback_verify", "table": table, "docnum": docnum})

    head = _open(account_dir, "STTRN", dbf.READ_ONLY)
    try:
        heads = [r for r in S.live_rows(head) if (r.DOCNUM or "").strip() == docnum]
        if len(heads) != 1 or (heads[0].LOCCOD or "").strip() != source or (heads[0].PEOPLE or "").strip() != target:
            fail(f"{len(heads)} header rows or wrong locations", "STTRN")
    finally:
        head.close()
    table = _open(account_dir, "STCRD", dbf.READ_ONLY)
    try:
        pairs: Dict[str, list] = {}
        for r in S.live_rows(table):
            if (r.DOCNUM or "").strip() == docnum:
                pairs.setdefault(str(r.SEQNUM or "").strip(), []).append(
                    ((r.POSOPR or "").strip(), (r.LOCCOD or "").strip(), (r.STKCOD or "").strip(), _dec(r.XTRNQTY)))
    finally:
        table.close()
    if len(pairs) != len(found):
        fail(f"{len(pairs)} line pairs, expected {len(found)}", "STCRD")
    for seq, match in enumerate(found, start=1):
        rows = sorted(pairs.get(str(seq), []))
        code = str(match["stkcod"]).strip()
        if len(rows) != 2 or [(row[0], row[1], row[2]) for row in rows] != [
                (S.STCRD_POSOPR_TRANSFER_IN, target, code), (S.STCRD_POSOPR_TRANSFER_OUT, source, code)] \
                or rows[0][3] != rows[1][3]:
            fail(f"line {seq} is not a matching transfer pair: {rows}", "STCRD")
    journal = _open(account_dir, "GLJNL", dbf.READ_ONLY)
    try:
        if any((r.VOUCHER or "").strip() == docnum for r in S.live_rows(journal)):
            fail("a journal voucher exists", "GLJNL")
    finally:
        journal.close()


__all__ = ["DIRECTION", "TRANSFER", "book_row", "validate", "write_transfer"]
