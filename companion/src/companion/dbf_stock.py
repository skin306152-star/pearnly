# -*- coding: utf-8 -*-
"""库存单写进 Express（载荷 6，``direction = stock``；出入库第一期 B 段，10-25）：ERP 的出库单 → 领用 OU、入库单 → 调整 JU（正）、
盘点调整 → TK（JU 类，正负都有）。写法照 S 盘真账样本（方案九.6 / 九.7b，6907GIZ、68SKCHE、69SKCHE）：

- 单头 STTRN：单号、日期、POSOPR（OU 6、JU 8、TK ':'）、备注、仓、货值合计（调整带正负号）、REFNUM（ERP 单号）、行数、
  借方科目 ACCNUMDR（领用）或对方科目 ACCNUMCR（调整）、部门、DOCSTAT N；盘点调整 TK 照 Express 原生（实录第 9 条 TK0000001）：
  货值 0、没有参考号与对方科目，明细 FLAG 5、MREMBAL / MREMVAL 记写前结存、PHYBAL 记写后这个仓的结存，凭证日记账取 ISRUN 那一行、
  摘要「ตรวจนับสินค้า + 单号」；
- 明细 STCRD（``dbf_schema.stcrd_stock_entry_row``）+ 主档 STMAS 结存 + 各仓 STLOC（只记 LMOVDAT）；
- 编号：ISRUN 里 DOCTYP OU / JU、单别 = 载荷的 ``doctype`` 那一行（没有就停下，不猜）；单号同其他 CC 写的单：
  单别 + 佛历年月日 + '-' + 当日序号；科目没给就用那一行的默认科目（领用 ACCNUM01、调整 ACCNUM02），必须可记账；
- 过账：一张 JNLTYP 00 普通日记账，VOUCHER = 单号，摘要 = 备注（没写是「จ่ายสินค้าภายใน / ปรับปรุงสินค้า + 单号」），每行
  SEQIT '1'；领用借科目、贷各物料的存货科目，调整为正借存货、贷对方科目，为负反过来。**定期盘存账套（ISINFO.ISPERPETUA ≠ Y）
  与零金额的单不过账**（样本里一律如此）。
- 主档结存按增减改（``stock_sale.move_stmas``：总结存 TOTBAL / TOTVAL ± 本单、UNITPR 随之重算、批次链 MREMBAL / MREMVAL 同样增减，11-07 照原生）；
- 成本：领用与减少的调整按移动均价（总结存 TOTVAL / TOTBAL；没货按标准成本 STNPR，再没有记 0），与 CC 写销货相同（金样本
  HS580101-001）；增加的调整用载荷给的单位成本，没给同领用。盘点调整 TK 的盘盈不用载荷的单价（Express 原生不让填）：挂这个仓最近一次
  进货那一批（RDOCNUM / MLOTNUM）、取那一批的成本（S 盘先进先出 2,836 行、平均成本 96 行；实录 TK0000001 在平均成本账套也是批成本）；
  挂不上批次的先进先出账套记 0（同原生），平均成本账套按商品均价（待核）。主档 LASUPD 记盘点日、盘盈那一行成最新一批（MLOTNUM），
  STLOC.TAKDAT 记盘点日。先进先出的批次（MLOTNUM / LOTBAL）不写，由 Express「计算成本」
  重排（方案九.7 第 3 条核实）。
- 新存货：行上的码 / 品名认不出 Express 的库存品就停下（``STOCK_ITEM_NOT_FOUND``）；人在 ERP 里选了新建的行（``new_ok``，载荷 15，
  多仓闭环 P3x）在写前备份里建 0 余额存货再写（``create_new_items``，与采购 / 销售同一个建档）。账套不允许负库存时减少的行先核结存
  （``stock_guard``，口径同销货）；要新建的行在减少（领用、盘亏）而账套不许负库存，建档前就停（``new_item_shortages``，主会话 11-01 定）。

全程在一份写前备份里：任何一步失败整单回滚；写完读回单头、明细、凭证、各仓结存，不符同样回滚。
"""

from __future__ import annotations

import datetime as _dt
import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import dbf

from . import account_rules
from . import cdx_reindex
from . import dbf_detail
from . import dbf_remarks
from . import dbf_schema as S
from . import fiscal_period
from . import product_master
from . import stock_guard
from . import stock_location
from . import stock_template
from . import stock_sale
from . import stock_units
from .consistency import account_company_matches
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
    _department_accounts,
    _discard_backup,
    _error_code,
    _gl_line,
    _open,
    _reindex,
    _restore,
    _verify_index,
    check_remarks,
    record_locations,
    stock_location_tables,
    verify_locations,
)
from .logger import get_logger
from .write_result import DbfWriteResult

log = get_logger(__name__)

DIRECTION = "stock"
ISSUE, ADJUST, COUNT = "OU", "JU", "TK"
KINDS = {ISSUE: ISSUE, ADJUST: ADJUST, COUNT: ADJUST}  # 载荷的单别 → ISRUN.DOCTYP
GENERAL_JOURNAL = "00"
SEQIT = "1"
ERR_BOOK = "JOURNAL_BOOK_NOT_FOUND"  # 账套 ISRUN 没有这个单别（同 POS 日汇总找不到单据簿）
ERR_ACCOUNT = "ACCOUNT_NOT_POSTABLE"
ERR_STOCK_ITEM = "STOCK_ITEM_NOT_FOUND"
TABLES = ("STTRN", "STCRD", "STMAS", "GLJNL", "GLJNLIT", "ISRUN")
_DESCRIPTION = {ISSUE: "จ่ายสินค้าภายใน", ADJUST: "ปรับปรุงสินค้า", COUNT: "ตรวจนับสินค้า"}
_CENT = Decimal("0.01")


def _q2(value) -> Decimal:
    return Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_UP)


def _dec(value) -> Decimal:
    try:
        return Decimal(str(value if value not in (None, "") else 0))
    except Exception:  # noqa: BLE001
        return Decimal(0)


def _fit(text: str, width: int) -> str:
    raw = str(text or "").encode("cp874", "replace")[:width]
    return raw.decode("cp874", "replace").strip()


def _validate_common(payload: Dict[str, Any]) -> Optional[str]:
    """库存单（含调拨 RL，`dbf_transfer`）共同的形状：单据日期、明细行都有码或品名、成本不为负。"""
    if not str(payload.get("docdate_be") or "").strip().isdigit():
        return "docdate_be missing"
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return "items missing"
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            return f"line {index} is not an object"
        if not (str(item.get("code") or "").strip() or str(item.get("name") or "").strip()):
            return f"line {index} has neither code nor name"
        if _dec(item.get("unit_cost")) < 0:
            return f"line {index} unit_cost is negative"
    return None


def _validate(payload: Dict[str, Any]) -> Optional[str]:
    doctype = str(payload.get("doctype") or "").strip().upper()
    if doctype not in KINDS:
        return f"doctype {doctype!r} is not OU / JU / TK"
    problem = _validate_common(payload)
    if problem:
        return problem
    for index, item in enumerate(payload["items"], start=1):
        qty = _dec(item.get("qty"))
        if qty == 0 or (doctype == ISSUE and qty < 0):
            return f"line {index} qty {item.get('qty')!r} is not valid for {doctype}"
    return None


def book_row(account_dir, doctype: str) -> Optional[Dict[str, str]]:
    """ISRUN 里这个单别那一行（DOCTYP OU / JU、PREFIX = 单别）：``{prefix, jnltyp, account}``；没有或有几行不同 → None。"""
    table = account_rules._open(account_dir, "ISRUN")
    if table is None:
        return None
    try:
        rows = [r for r in S.live_rows(table)
                if account_rules._gv(r, "DOCTYP") == KINDS[doctype] and account_rules._gv(r, "PREFIX") == doctype]
        field = "ACCNUM01" if KINDS[doctype] == ISSUE else "ACCNUM02"
        found = {(account_rules._gv(r, "JNLTYP"), account_rules._gv(r, field)) for r in rows}
    finally:
        table.close()
    if len(found) != 1:
        return None
    jnltyp, account = found.pop()
    return {"prefix": doctype, "jnltyp": jnltyp or GENERAL_JOURNAL, "account": account}


def _perpetual(account_dir) -> bool:
    info = account_rules._first(account_dir, "ISINFO") or {}
    return str(info.get("ISPERPETUA") or "Y").strip().upper() != "N"


def _next_docnum(table, prefix: str, docdate_be: str) -> str:
    """单别 + 佛历年月日 + '-' + 当日序号（3 位）；连软删行一起数（删掉的号在会计手上仍是用过的号）。"""
    head = f"{prefix}{docdate_be}-"
    seq = 0
    for r in table:
        docnum = (r.DOCNUM or "").strip()
        if docnum.startswith(head):
            try:
                seq = max(seq, int(docnum[len(head):]))
            except ValueError:
                continue
    return f"{head}{seq + 1:03d}"


def _fifo(account_dir) -> bool:
    """成本法先进先出（ISINFO.COSMTD = F）。"""
    return str((account_rules._first(account_dir, "ISINFO") or {}).get("COSMTD") or "").strip().upper() == "F"


def _lots(account_dir, keys, docdat=None) -> Dict[Tuple[str, str], Tuple[Decimal, str, str, Any]]:
    """盘盈挂的那一批（实录第 9 条）：各 (商品, 仓) 最近一次进货的那一行（STCRD POSOPR 0、数量为正，按日期取最后的）
    → (每个库存单位的成本, 原单号, 原单行号（存的原样 3 位）, 日期)。仓库码空的旧行算主仓。``docdat`` 给了只认不晚于盘点那天的进货
    （复核 10-26：任务积压后再写，不挂到盘点之后才进的那一批）。"""
    out: Dict[Tuple[str, str], Tuple[Decimal, str, str, Any]] = {}
    if not keys:
        return out
    main = account_rules.main_location(account_dir)
    table = _open(account_dir, "STCRD", dbf.READ_ONLY)
    try:
        for rec in S.live_rows(table):
            if (rec.POSOPR or "").strip() != S.STCRD_POSOPR_PURCHASE:
                continue
            key = ((rec.STKCOD or "").strip(), (rec.LOCCOD or "").strip() or main)
            qty = _dec(rec.XTRNQTY)
            if key not in keys or qty <= 0 or not rec.DOCDAT or (docdat is not None and rec.DOCDAT > docdat):
                continue
            if key not in out or rec.DOCDAT >= out[key][3]:
                # 行号照存的原样（Express 拼 RDOCNUM 是「单号 C(12) + SEQNUM 原样 C(3)」，存量 CC 单是左对齐）
                out[key] = (_dec(rec.XTRNVAL) / qty, (rec.DOCNUM or "").strip(), str(rec.SEQNUM or "").ljust(3)[:3], rec.DOCDAT)
    finally:
        table.close()
    return out


def _location_states(account_dir, keys) -> Dict[Tuple[str, str], Tuple[Decimal, Decimal]]:
    """各 (商品, 仓) 写前的结存与货值：STLOC 的 LOCBAL 与 BEGVAL + 各期 VAL（同 ``stock_location`` 的口径）。没有这张表 / 这一行的不给。"""
    out: Dict[Tuple[str, str], Tuple[Decimal, Decimal]] = {}
    if not keys or stock_location._file(account_dir, stock_location.TABLE) is None:
        return out
    table = stock_location._open(account_dir, stock_location.TABLE, dbf.READ_ONLY)
    try:
        values = [name for name in (field.upper() for field in table.field_names)
                  if name == "BEGVAL" or re.fullmatch(r"VAL\d+(NY)?", name)]
        for rec in S.live_rows(table):
            key = ((rec.STKCOD or "").strip(), (rec.LOCCOD or "").strip())
            if key in keys:
                out[key] = (_dec(rec.LOCBAL), sum((_dec(rec[name]) for name in values), Decimal(0)))
    finally:
        table.close()
    return out


def _stamp_take_dates(account_dir, places, docdat) -> None:
    """原生盘点：各 (商品, 仓) 的 STLOC.TAKDAT 记盘点那天（实录第 9 条）。老账套没有这一栏不写。"""
    table = stock_location._open(account_dir, stock_location.TABLE, dbf.READ_WRITE)
    try:
        if "TAKDAT" not in {name.upper() for name in table.field_names}:
            return
        for rec in S.live_rows(table):
            if ((rec.STKCOD or "").strip(), (rec.LOCCOD or "").strip()) in places:
                with rec as fields:
                    fields["TAKDAT"] = docdat
    finally:
        table.close()


def _cost(account_dir, item: Dict[str, Any]) -> Decimal:
    """移动均价（总结存为正时，TOTVAL / TOTBAL，= Express 的 UNITPR）→ 标准成本 → 0。"""
    if item["totbal"] > 0:
        return _dec(item["totval"]) / _dec(item["totbal"])
    return _dec(stock_sale.standard_cost(account_dir, item["stkcod"]))


def _guard_items(doctype: str, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """减少库存的行交给 ``stock_guard``（它只核 ``item_mode = stock_sale`` 的行）；增加的行不核。"""
    out = []
    for item in items:
        qty = _dec(item.get("qty"))
        decrease = doctype == ISSUE or qty < 0
        out.append({**item, "qty": str(abs(qty)), "item_mode": "stock_sale" if decrease else "stock_item"})
    return out


def _filtered(table, row: Dict[str, Any]) -> Dict[str, Any]:
    fields = {name.upper() for name in table.field_names}
    return {key: value for key, value in row.items() if key.upper() in fields}


def write_stock_entry(
    payload: Dict[str, Any],
    account_dir: str | Path,
    *,
    userid: str = S.PEARNLY_USERID,
    depcod: str = "",
    make_backup: bool = True,
    allowed_sets: tuple = (),
    selected_dir: str = "",
    expected_company: str = "",
    index_batch=None,
    today: Optional[_dt.date] = None,
) -> DbfWriteResult:
    """把一张库存单直写进账套 DBF。失败整体回滚，绝不留半截单。``allowed_sets`` / ``index_batch`` 与进项、销项同签名（库存单
    不按账套名白名单核：单据里没有账套名，账套由 ``selected_dir`` 与 ``expected_company`` 两道闸锁定；主档索引逐张重建）。"""
    clock = StepClock(DIRECTION)
    problem = _validate(payload)
    if problem:
        return DbfWriteResult(False, error_code=ERR_VALIDATION, error=problem)
    remarks_error = dbf_remarks.problem(payload["items"])
    if remarks_error:
        return DbfWriteResult(False, error_code=remarks_error[0], error=remarks_error[1])
    doctype = str(payload["doctype"]).strip().upper()
    kind = KINDS[doctype]
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
    book = book_row(account_dir, doctype)
    if book is None:
        return DbfWriteResult(False, error_code=ERR_BOOK, error=f"no single {doctype} book (DOCTYP {kind}) in ISRUN")
    account = str(payload.get("account") or "").strip() or book["account"]
    if not account_rules.postable_account(account_dir, account):
        return DbfWriteResult(False, error_code=ERR_ACCOUNT,
                              error=f"{doctype} account {account!r} is not a postable account")
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
    try:
        locations = stock_location.plan(account_dir, payload)
    except stock_location.LocationError as exc:
        return DbfWriteResult(False, error_code=ERR_LOCATION, error=str(exc))
    short = stock_guard.shortages(account_dir, _guard_items(doctype, items), locations) or new_item_shortages(
        account_dir, items, found, lambda item: kind == ISSUE or _dec(item.get("qty")) < 0, str(payload.get("loccod") or ""))
    if short:
        return DbfWriteResult(False, error_code=stock_guard.ERR_STOCK_INSUFFICIENT, error=stock_guard.message(short))
    remark = _fit(payload.get("remark") or "", 50)
    clock.lap("checks")
    backup_dir = _backup(account_dir, TABLES + stock_location_tables(locations) + dbf_remarks.tables(items)) \
        if make_backup else None
    clock.lap("backup")
    docnum = ""
    moves = [] if locations is not None else None
    try:
        today = today or _dt.date.today()
        create_new_items(account_dir, items, found, userid=userid, acccod=str(payload.get("stock_acccod") or "").strip())
        docnum, lines, total, inventory = _write(account_dir, doctype, kind, docdate_be, docdat, account, remark,
                                                 items, found, locations, moves, depcod=depcod, userid=userid,
                                                 ref_no=str(payload.get("ref_no") or ""), today=today,
                                                 header_loc=str(payload.get("loccod") or "").strip())
        posted = _post(account_dir, kind, docnum, docdat, account, remark, total, inventory, depcod=depcod,
                       userid=userid, today=today, doctype=doctype,
                       jnltyp=book["jnltyp"] if doctype == COUNT else "")  # 领用 / 调整照旧 00（已验收），盘点照原生取 ISRUN 的
        _bump_isrun(account_dir, book["prefix"], book["jnltyp"])
        stloc_expected, notes = record_locations(account_dir, moves, None)
        remarks = dbf_remarks.write(account_dir, docnum, enumerate(items, start=1))  # 按台号码：明细行号 = 载荷顺序
        if doctype == COUNT and moves is not None:
            main = account_rules.main_location(account_dir)
            _stamp_take_dates(account_dir, {(match["stkcod"], stock_location.location_at(locations, index) or main)
                                            for index, match in enumerate(found)}, docdat)
        clock.lap("write")
        _reindex(account_dir, TABLES + (S.LOCATION_TABLES if stloc_expected else ()) + dbf_remarks.tables(items))
        clock.lap("reindex")
        _verify(account_dir, docnum, len(lines), total, posted)
        verify_locations(account_dir, stloc_expected)
        check_remarks(account_dir, docnum, remarks)
        clock.lap("verify")
        # 同进货 / 销货：按 Express 自己的明细索引（DOCNUM+SEQNUM）回查到这张单，Express 才看得到
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
        log.exception("stock entry write failed, rolled back")
        return DbfWriteResult(False, error_code=_error_code(exc), error=str(exc),
                              backup_dir=str(backup_dir) if backup_dir else None,
                              evidence=exc.evidence if isinstance(exc, _WriteError) else None)


def _creatable(item) -> bool:
    return item.get("new_ok") is True and not str(item.get("code") or "").strip()


def new_item_shortages(account_dir, items, found, reduces, header_loc="") -> List[Dict[str, Any]]:
    """要新建的行（``found`` 里留空）里减少库存的（``reduces(item)``）：账套不许负库存时，新建的存货结存是 0，写下去必成负数，建档前
    就停，原因同结存核对（``stock_guard.message``）；允许负库存的账套照建照写（主会话 11-01：账套设了不许负库存，就不能替它写成负数）。
    （建档本身照采购 / 销售记 NEGALLOW = Y；商品级标记管的是以后的单，这里按账套的设置判。）"""
    pending = [item for item, match in zip(items, found) if match is None and reduces(item)]
    if not pending or stock_guard.negative_allowed(account_dir):
        return []
    return [{"stkcod": str(item.get("name") or "").strip(), "loccod": str(item.get("loccod") or header_loc or "").strip(),
             "need": abs(_dec(item.get("qty"))), "have": Decimal(0)} for item in pending]


def create_new_items(account_dir, items, found, *, userid, acccod="") -> None:
    """``found`` 里留空（``None``）的行 = Express 里没有、人选了新建的商品：建 0 余额库存品（`product_master.ensure_stock_item`：
    克隆账套结构、单位取票面或账套众数、记真实建档日，同采购 / 销售），按建出的码认回来填进 ``found``。同一张单同名的两行只建一次
    （建档先按品名找）。``acccod``：人在 ERP 里为这份账套选的存货科目组（载荷 ``stock_acccod``）；没选、账套里用得最多的那组又判不出
    是存货科目时报 ``STOCK_ACC_GROUP_UNFIT``，同采购，ERP 让人选一次。调用方已在写前备份里，失败整单回滚。"""
    for index, item in enumerate(items):
        if found[index] is None:
            try:
                made = product_master.ensure_stock_item(account_dir, str(item.get("name") or ""), userid=userid,
                                                        unit=str(item.get("unit") or ""), acccod=acccod)
            except stock_template.InventoryAccountUnfit as exc:
                raise _WriteError(str(exc), stock_template.ERR_STOCK_GROUP) from exc
            found[index] = stock_sale.find_stock_item(account_dir, "", made.stkcod)


def _write(account_dir, doctype, kind, docdate_be, docdat, account, remark, items, found, locations, moves, *,
           depcod, userid, ref_no, today, header_loc) -> Tuple[str, List[Dict], Decimal, Dict[str, Decimal]]:
    """单头 + 明细 + 主档结存 → (单号, 逐行结果, 货值合计（调整带正负号）, 各存货科目的货值（调整带正负号）)。"""
    count = doctype == COUNT
    posopr = S.STCRD_POSOPR_ISSUE if kind == ISSUE else S.STCRD_POSOPR_COUNT if count else S.STCRD_POSOPR_ADJUST
    move_kind = stock_location.KIND_ISSUE if kind == ISSUE else stock_location.KIND_ADJUST
    head = _open(account_dir, "STTRN", dbf.READ_WRITE)
    try:
        docnum = _next_docnum(head, doctype, docdate_be)
    finally:
        head.close()
    total = Decimal(0)
    inventory: Dict[str, Decimal] = {}
    lines: List[Dict] = []
    perpetual = _perpetual(account_dir)  # 定期盘存账套不过账，也就不查存货科目组（验收 10-25：缺组时原来报笼统的写盘失败）
    balances: Dict[str, Tuple[Decimal, Decimal]] = {}  # 批次链结存（STCRD 行上的结存栏，既有写法）
    totals: Dict[str, Tuple[Decimal, Decimal]] = {}  # 总结存（判成本、PHYBAL；主档按增减改，``stock_sale.move_stmas``）
    # 盘点调整 TK 照 Express 原生（实录第 9 条 TK0000001）：单价不取载荷的，盘盈挂这个仓最近一次进货的那一批、取那一批的成本
    # （S 盘先进先出 2,836 行、平均成本 96 行如此；实录 TK0000001 在平均成本账套也是批成本 45，不是这个仓的均价 50）；挂不上批次的
    # 先进先出账套记 0（同原生），平均成本账套按商品均价（待核）。明细带 PHYBAL（写后这个仓的结存）
    main = account_rules.main_location(account_dir)
    places = [(match["stkcod"], stock_location.location_at(locations, index) or main) for index, match in enumerate(found)]
    gains = {place for place, item in zip(places, items) if _dec(item.get("qty")) > 0}
    lots = _lots(account_dir, gains, docdat) if count else {}
    has_stloc = stock_location._file(account_dir, stock_location.TABLE) is not None
    located = _location_states(account_dir, set(places)) if count else {}
    fifo = count and _fifo(account_dir)
    table = _open(account_dir, "STCRD", dbf.READ_WRITE)
    try:
        for seq, (item, match) in enumerate(zip(items, found), start=1):
            stkcod = match["stkcod"]
            # 包装单位按 Express 主档系数换成库存单位动结存（stock_units；验收 10-25 实测「1 กล่อง = 25 เส้น」按 1 写）
            units = stock_units.resolve(account_dir, stkcod, str(item.get("unit") or ""), _dec(item.get("qty")),
                                        item.get("factor"))
            qty = units.base_qty
            signed = -qty if kind == ISSUE else qty
            bal, val = balances.get(stkcod, (_dec(match["mrembal"]), _dec(match["mremval"])))
            tot_bal, tot_val = totals.get(stkcod, (_dec(match["totbal"]), _dec(match["totval"])))
            now = {**match, "totbal": tot_bal, "totval": tot_val}
            given = _dec(item.get("unit_cost")) / (units.base_qty / units.qty if units.qty else 1)  # 每包成本 → 每个库存单位
            unit_cost = given if (signed > 0 and given > 0) else _cost(account_dir, now)
            place = places[seq - 1]
            lot = lots.get(place) if count and signed > 0 else None
            if count and signed > 0:
                unit_cost = lot[0] if lot else Decimal(0) if fifo else _cost(account_dir, now)
            value = _q2(abs(qty) * unit_cost)
            signed_value = -value if signed < 0 else value
            new_bal, new_val = bal + signed, _q2(val + signed_value)
            balances[stkcod] = (new_bal, new_val)
            totals[stkcod] = (tot_bal + signed, _q2(tot_val + signed_value))
            unit = units.unit
            trn = units.qty if kind == ISSUE else (units.qty if signed >= 0 else -abs(units.qty))
            row = account_rules.localize_stcrd(account_dir, S.stcrd_stock_entry_row(
                docnum, seq, docdat, stkcod, str(item.get("name") or ""), float(trn),
                float(_q2(unit_cost) if unit_cost else 0), val=float(value if kind == ISSUE else signed_value),
                rem_bal=float(new_bal), rem_val=float(new_val), posopr=posopr, unit=unit, depcod=depcod,
                accnumdr=account if kind == ISSUE else "", accnumcr=account if kind != ISSUE else ""),
                unit, stock_location.location_at(locations, seq - 1))
            stock_units.stamp(row, units if kind != ISSUE else stock_units.Units(units.unit, units.factor, units.qty,
                                                                                  abs(units.base_qty)))
            if units.factor != 1:
                row["UNITPR"] = float(_q2(unit_cost * units.factor))  # 交易单位（每包）的单价，同 Express 原生
            if count:
                place_qty, place_val = located.get(place, (Decimal(0), Decimal(0)))
                located[place] = (place_qty + signed, place_val + signed_value)
                # 原生：MREMBAL / MREMVAL 是写前结存，FLAG 5，PHYBAL 写后这个仓的结存，没有对方科目与品名
                # PHYBAL：这个仓写后的结存；账套没有 STLOC（不分仓）时就是商品主档写后的结存（复核 10-26）
                row.update({"FLAG": S.STCRD_FLAG_COUNT, "PHYBAL": float(place_qty + signed if has_stloc else tot_bal + signed),
                            "MREMBAL": float(bal),
                            "MREMVAL": float(val), "ACCNUMCR": "", "STKDES": "", "NETVAL": float(signed_value)})
                if lot:
                    link = f"{lot[1]:<12}{lot[2]}"
                    row.update({"RDOCNUM": link, "MLOTNUM": f"{lot[3]:%Y%m%d}{S.STCRD_POSOPR_PURCHASE}{link}",
                                "LOTBAL": float(qty), "LOTVAL": float(value), "LUNITPR": float(_q2(unit_cost))})
            S.append_stcrd(table, _filtered(table, row))
            if moves is not None:
                moves.append(stock_location.move(row, move_kind))
            master = {}
            if count:
                # 原生盘点：主档的最后变动日记这一天，盘盈那一行成了这件品最新的一批（MLOTNUM = 日期 + ':' + 单号行号）
                master["LASUPD"] = docdat
                if signed > 0:
                    master["MLOTNUM"] = f"{docdat:%Y%m%d}{S.STCRD_POSOPR_COUNT}{docnum:<12}{S.seqnum_text(seq)}"
            stock_sale.move_stmas(account_dir, stkcod, signed, signed_value, fields=master)
            total += signed_value
            if value and perpetual:
                inventory_account = stock_sale.stock_accounts(account_dir, match.get("acccod") or "")[0]
                inventory[inventory_account] = inventory.get(inventory_account, Decimal(0)) + signed_value
            lines.append(dbf_detail.line_mode(seq, str(item.get("name") or ""), stkcod, mode=stock_sale.ITEM_MODE))
    finally:
        table.close()
    head = _open(account_dir, "STTRN", dbf.READ_WRITE)
    try:
        header_value = -total if kind == ISSUE else total
        S.append_native(head, _filtered(head, {
            "DOCNUM": docnum, "DOCDAT": docdat, "POSOPR": posopr, "REMARK": remark,
            # 盘点调整 TK 原生单头：货值 0、没有参考号与对方科目（实录第 9 条 TK0000001）
            "LOCCOD": header_loc or main, "TRNVAL": 0.0 if count else float(header_value),
            "REFNUM": "" if count else _fit(ref_no, 15), "NXTSEQ": str(len(items)), "ACCNUMDR": account if kind == ISSUE else "",
            "ACCNUMCR": account if kind != ISSUE and not count else "", "DEPCOD": depcod, "POSTGL": "", "DOCSTAT": "N",
            "USERID": userid, "CHGDAT": today}))
    finally:
        head.close()
    return docnum, lines, total, inventory


def _post(account_dir, kind, docnum, docdat, account, remark, total, inventory, *, depcod, userid, today, doctype="",
          jnltyp="") -> List[Tuple]:
    """凭证（日记账取 ISRUN 那一行的，没有是 00）。定期盘存账套、零金额不过账 → []；否则回写下的分录 ``[(借贷, 科目, 金额)]``。
    盘点调整 TK 的摘要照原生「ตรวจนับสินค้า + 单号」（实录第 9 条），不用备注。"""
    if not _perpetual(account_dir) or not any(inventory.values()):
        return []
    descrp = _fit(f"{_DESCRIPTION[COUNT]} {docnum}" if doctype == COUNT else remark or f"{_DESCRIPTION[kind]} {docnum}", 50)
    rows: List[Tuple[str, str, Decimal]] = []
    if kind == ISSUE:
        rows.append((S.TRNTYP_DEBIT, account, -total))
        rows += [(S.TRNTYP_CREDIT, acc, -amount) for acc, amount in inventory.items() if amount]
    else:
        rows += [(S.TRNTYP_DEBIT if amount > 0 else S.TRNTYP_CREDIT, acc, abs(amount))
                 for acc, amount in inventory.items() if amount]
        if total:
            rows.append((S.TRNTYP_CREDIT if total > 0 else S.TRNTYP_DEBIT, account, abs(total)))
    departmental = _department_accounts(account_dir)
    journal = _open(account_dir, "GLJNL", dbf.READ_WRITE)
    try:
        journal.append(_filtered(journal, {"JNLTYP": jnltyp or GENERAL_JOURNAL, "VOUDAT": docdat, "VOUCHER": docnum,
                                           "DESCRP": descrp, "TRNSTAT": "P", "DOCSTAT": "N", "CREBY": S.PEARNLY_USERID,
                                           "CREDAT": today, "USERID": userid, "CHGDAT": today}))
    finally:
        journal.close()
    entries = _open(account_dir, "GLJNLIT", dbf.READ_WRITE)
    try:
        for side, acc, amount in rows:
            S.append_native(entries, _filtered(entries, _gl_line(docnum, SEQIT, docdat, acc, descrp, side, _q2(amount), depcod,
                                                       departmental)))
    finally:
        entries.close()
    return [(side, acc, _q2(amount)) for side, acc, amount in rows]


def _verify(account_dir, docnum: str, line_count: int, total: Decimal, posted: List[Tuple]) -> None:
    """读回：单头恰好一行、明细行数与货值合计、凭证分录与要写的一致且借贷平衡。不符抛写盘错误（回滚）。"""
    def fail(what, table):
        raise _WriteError(f"stock entry {docnum}: {what}", ERR_VERIFY,
                          {"kind": "readback_verify", "table": table, "docnum": docnum})

    head = _open(account_dir, "STTRN", dbf.READ_ONLY)
    try:
        heads = [r for r in S.live_rows(head) if (r.DOCNUM or "").strip() == docnum]
    finally:
        head.close()
    if len(heads) != 1:
        fail(f"{len(heads)} header rows", "STTRN")
    table = _open(account_dir, "STCRD", dbf.READ_ONLY)
    try:
        rows = [r for r in S.live_rows(table) if (r.DOCNUM or "").strip() == docnum]
        signed = sum((_dec(r.XTRNVAL) * (-1 if (r.POSOPR or "").strip() == S.STCRD_POSOPR_ISSUE else 1) for r in rows),
                     Decimal(0))
    finally:
        table.close()
    if len(rows) != line_count or _q2(signed) != _q2(total):
        fail(f"{len(rows)} detail rows worth {signed}, expected {line_count} worth {total}", "STCRD")
    entries = _open(account_dir, "GLJNLIT", dbf.READ_ONLY)
    try:
        got = sorted(((r.TRNTYP or "").strip(), (r.ACCNUM or "").strip(), _q2(r.AMOUNT))
                     for r in S.live_rows(entries) if (r.VOUCHER or "").strip() == docnum)
    finally:
        entries.close()
    if got != sorted(posted):
        fail(f"journal lines {got} differ from {sorted(posted)}", "GLJNLIT")
    debit = sum((amount for side, _acc, amount in got if side == S.TRNTYP_DEBIT), Decimal(0))
    credit = sum((amount for side, _acc, amount in got if side != S.TRNTYP_DEBIT), Decimal(0))
    if debit != credit:
        fail(f"journal not balanced {debit} / {credit}", "GLJNLIT")


__all__ = ["DIRECTION", "ERR_ACCOUNT", "ERR_BOOK", "ERR_STOCK_ITEM", "KINDS", "book_row", "create_new_items",
           "new_item_shortages", "write_stock_entry"]
