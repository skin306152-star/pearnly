"""Express 账套 DBF 销项直写器(镜像 dbf_writer 采购)。

一张赊销(IV)/现销(HS)写 6 表(收银退货单 SR 由 write_sale 分流给 dbf_refund):ISRUN(增号)→ ARTRN(单头)→ ISVAT('S')(销项税)
→ GLJNL('03')+GLJNLIT(分录 Dr 应收/现金 = Cr 销售收入 + Cr 销项税)→ ARBAL(客户应收
按月桶)。STCRD(存货出)本期非存货不碰(同采购延后)。

字段语义逆向真 DATAT gold(现销 HL580301-001·3 行非库存):ARTRN.AMOUNT/TOTAL/NETAMT=
含税、NETVAL=税前、VATAMT=税额、FLGVAT='1'(价含税);ISVAT('S').REFNUM=票面号(gold 里与 DOCNUM
相同;票号超出 DOCNUM 宽度时单号退回自编号,REFNUM 与 ARTRN.YOUREF 仍是票面号,见 _sales_docnum);
GLJNLIT SEQIT 借资产=2/贷收入=5/贷销项税=6。⚠️ DATAT 无赊销样本 → IV(Dr 应收)按现销
gold 类推,真机再证(见报告)。护栏沿用采购:DATAT 白名单/写前备份/失败回滚/读回校验/
ref_no+客户 幂等/CDX 重建。
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import dbf

from . import dbf_detail
from . import dbf_remarks
from . import product_master
from . import account_rules
from . import dbf_schema as S
from . import fiscal_period
from . import glbal_mark
from . import guided
from . import master_create
from . import pos_journal
from . import service_vat
from . import write_verify
from . import stock_guard, stock_location
from . import stock_sale
from . import stock_template
from . import cdx_reindex
from .consistency import account_company_matches
from .dbf_writer import (
    ERR_DEFERRED_VAT,
    ERR_ACCOUNT_DIR,
    ERR_COMPANY,
    ERR_FIELD_WIDTH,
    ERR_LOCATION,
    ERR_VALIDATION,
    ERR_VERIFY,
    ERR_WRITE,
    DbfWriteResult,
    StepClock,
    _backup,
    _bump_isrun,
    _department_accounts,
    _discard_backup,
    _f,
    _gl_line,
    _next_docnum,
    _open,
    _reindex_or_defer,
    _located,
    _restore,
    record_chains,
    record_locations,
    stock_location_tables,
    verify_chains,
    verify_locations,
    _verify_index,
    _WriteError,
    amount_fields,
    check_remarks,
    remarks_problem,
    inclusive_items,
    audit_fields,
)
from .logger import get_logger
from .sales_adapter import (
    ALLOWED_ACCOUNT_SETS,
    ERR_POS_INVALID,
    SalesEntry,
    build_sales_entry,
    validate_sales_payload,
)

log = get_logger(__name__)

ERR_NEW_CUSTOMER = "CUSTOMER_NOT_IN_ARMAS"
ERR_CUSTOMER_DUP = "CUSTOMER_DUP_SUSPECTED"  # 疑似重复客户 → 不建,转人工确认
ERR_CUSTOMER_AMBIGUOUS = "CUSTOMER_BRANCH_AMBIGUOUS"  # 同税号多分店档定不出唯一 → 转人工
ERR_STOCK_NOT_FOUND = (
    "STOCK_ITEM_NOT_FOUND"  # 选库存但客户账里无此库存品/混合行 → 交会计
)

_MAX_YOUREF = 30
_MAX_REFNUM = 15  # ISVAT.REFNUM C(15) · 票号同时进 YOUREF(30) 与 REFNUM,取严的那个
_MAX_CUSCOD = 10
_MAX_TAXID = 15
_MAX_ACCNUM = 15


def _field_overflow(entry: SalesEntry) -> Optional[str]:
    checks = [
        ("ref_no (YOUREF/REFNUM)", entry.ref_no, min(_MAX_YOUREF, _MAX_REFNUM)),
        ("customer_code (CUSCOD)", entry.customer_code, _MAX_CUSCOD),
        ("tax_id (TAXID)", entry.customer_tax_id, _MAX_TAXID),
    ]
    for label, val, limit in checks:
        if len((val or "").encode("cp874", "replace")) > limit:
            return f"{label} exceeds {limit} bytes: {val!r}"
    for ln in [*entry.lines, *entry.pos_transfers]:
        if len(str(ln.get("acc") or "").encode("cp874", "replace")) > _MAX_ACCNUM:
            return f"ACCNUM exceeds {_MAX_ACCNUM} bytes: {ln.get('acc')!r}"
    return None


def _gl_rows(entry: SalesEntry) -> List[Tuple[str, str, str, Decimal]]:
    """entry.lines → [(SEQIT, ACCNUM, TRNTYP, amount)]。

    lines 顺序 [借 资产(应收/现金), 贷 收入, 贷 销项税(若有)];借资产=2/贷收入=5/贷税=6。
    带 ``role`` 的载荷(ERP 前台的明细按行科目拆,收入可以有几条,10-24)按角色定;没有 role 的旧载荷照旧按位置。
    """
    if any(ln.get("role") for ln in entry.lines):
        seqit = {"asset": S.SALES_SEQIT["asset"], "revenue": S.SALES_SEQIT["revenue"],
                 "output_vat": S.SALES_SEQIT["output_vat"]}
        out: List[Tuple[str, str, str, Decimal]] = []
        for ln in entry.lines:
            role = str(ln.get("role") or "")
            if role in seqit:
                side = S.TRNTYP_DEBIT if role == "asset" else S.TRNTYP_CREDIT
                out.append((seqit[role], str(ln["acc"]), side, Decimal(str(ln["amount"]))))
        return out
    debits = [ln for ln in entry.lines if str(ln.get("side", "")).upper() == "D"]
    credits = [ln for ln in entry.lines if str(ln.get("side", "")).upper() == "C"]
    rows: List[Tuple[str, str, str, Decimal]] = []
    if debits:
        a = debits[0]
        rows.append(
            (
                S.SALES_SEQIT["asset"],
                str(a["acc"]),
                S.TRNTYP_DEBIT,
                Decimal(str(a["amount"])),
            )
        )
    if credits:
        rev = credits[0]
        rows.append(
            (
                S.SALES_SEQIT["revenue"],
                str(rev["acc"]),
                S.TRNTYP_CREDIT,
                Decimal(str(rev["amount"])),
            )
        )
    if len(credits) > 1:
        vat = credits[1]
        rows.append(
            (
                S.SALES_SEQIT["output_vat"],
                str(vat["acc"]),
                S.TRNTYP_CREDIT,
                Decimal(str(vat["amount"])),
            )
        )
    return rows


_MAX_DOCNUM = 12  # ARTRN/ISVAT/STCRD 的 DOCNUM C(12)


def _sales_docnum(artrn, prefix: str, docdate_be: str, ref_no: str) -> str:
    """销项凭证号 = 票面发票号。

    销项票是本方开的,票号(IV69/00475)印在实体票上、给了客户、要进 ภ.พ.30 让税局
    连号核对 —— Express 里的 เลขที่เอกสาร 就该是它,而不是我们另编一套。这与采购
    相反:采购票是对方开的,凭证号本就该由本方账套自己排。

    两个约束下的退让:
      · DOCNUM 定宽 C(12),票号超宽 → 退回自编号(ISVAT.REFNUM 仍是真实票号,报表不受影响);
      · 账套里已有同号的单(会计手录过)照写,不查重(用户 2026-09-30)。
    回退开关:PEARNLY_SALES_DOCNUM=seq → 整体退回旧的自编号行为。
    """
    if os.environ.get("PEARNLY_SALES_DOCNUM", "").strip().lower() == "seq":
        return _next_docnum(artrn, prefix, docdate_be)
    printed = (ref_no or "").strip()
    if printed and len(printed.encode("cp874", "replace")) <= _MAX_DOCNUM:
        return printed
    log.warning(
        "sales docnum: ref_no %r 超 DOCNUM C(%d) 或为空 · 退回自编号",
        printed,
        _MAX_DOCNUM,
    )
    return _next_docnum(artrn, prefix, docdate_be)


def write_sale(
    payload: Dict[str, Any],
    account_dir: str | Path,
    *,
    userid: str = S.PEARNLY_USERID,
    depcod: str = "",
    make_backup: bool = True,
    allowed_sets: tuple = ALLOWED_ACCOUNT_SETS,
    selected_dir: str = "",
    expected_company: str = "",
    index_batch=None,
    opening_stock=None,
) -> DbfWriteResult:
    """把一张销售票直写进账套 DBF(AR 表族)。失败整体回滚。

    opening_stock(会计补期初 [{name,qty,unit_cost,date}])非空 → 备份事务内先垫库存,让本票
    的库存卖行可复用真库存 + 结转 COGS(见 stock_sale.set_opening_stock)。期初与销售原子成败。

    三重一致性闸(镜像采购 · 缺所选 → fail-safe 拒):account_set 名(allowed_sets)+
    写盘目录(selected_dir)+ 目标 ISINFO 公司(expected_company)三证齐过才落盘。
    """
    clock = StepClock("sales")
    v = validate_sales_payload(payload, allowed=allowed_sets)
    if not v.ok:
        # POS 日汇总票的 `pos` 块不合格单独回报(云端按码出「重新导入 POS 导出」),其余照旧 PAYLOAD_INVALID
        return DbfWriteResult(
            False, error_code=ERR_POS_INVALID if v.error_code == ERR_POS_INVALID else ERR_VALIDATION,
            error=f"{v.error_code}: {v.detail}"
        )
    entry = build_sales_entry(payload)

    account_dir = Path(account_dir)
    ambiguous = S.path_ambiguity(account_dir) or S.path_ambiguity(selected_dir)
    if ambiguous:  # 路径会被系统改写落点 → 宁可不写,也不能静默写进本机影子目录
        return DbfWriteResult(
            False,
            error_code=ERR_ACCOUNT_DIR,
            error=(
                f"账套路径不完整,系统会把落点改写成 {ambiguous!r}"
                f"(原值 {str(account_dir)!r} · UNC 是不是少写了一个反斜杠?)"
            ),
        )
    if not S.account_dir_allowed(account_dir, selected_dir):
        return DbfWriteResult(
            False,
            error_code=ERR_ACCOUNT_DIR,
            error=f"write target {account_dir} != selected account dir {selected_dir!r}",
        )
    co_ok, co_reason = account_company_matches(account_dir, expected_company)
    if not co_ok:
        return DbfWriteResult(False, error_code=ERR_COMPANY, error=co_reason)
    if entry.is_refund:
        # 收银退货单 SR(载荷 10):挂原 HS、退现金、货回仓,另一条写路(dbf_refund);没有 refund 块的载荷不经过这里
        from .dbf_refund import write_refund

        return write_refund(payload, account_dir, entry, userid=userid, depcod=depcod, make_backup=make_backup,
                            index_batch=index_batch)
    if not (entry.customer_code or entry.customer_name):
        return DbfWriteResult(
            False,
            error_code=ERR_NEW_CUSTOMER,
            error="customer has neither code nor name",
        )
    overflow = _field_overflow(entry)
    if overflow:
        return DbfWriteResult(False, error_code=ERR_FIELD_WIDTH, error=overflow)
    remarks_error = remarks_problem(entry)
    if remarks_error:
        return DbfWriteResult(False, error_code=remarks_error[0], error=remarks_error[1])
    # POS 日汇总票的付款方式转账凭证:写前先定普通日记账单据簿、核收款科目可记账,定不下来就不动账套。
    journal, journal_error, journal_reason = pos_journal.check(account_dir, entry.pos_transfers)
    if journal_error:
        return DbfWriteResult(False, error_code=journal_error, error=journal_reason)
    # 逐行写进哪个仓、维护 STLOC(没带仓库码:账套有 STLOC 就按主仓记,没有 STLOC → None,整条写路不碰它)
    try:
        locations = stock_location.plan(account_dir, payload)
    except stock_location.LocationError as exc:
        return DbfWriteResult(False, error_code=ERR_LOCATION, error=str(exc))

    docdate_be = str(payload["docdate_be"])
    docdat = S.be_yymmdd_to_greg(docdate_be)
    # ERP 的单（载荷带 respect_period_lock，1.12.0）：单据日期所在的会计期在 Express 里已关账（上一年度账套常全年锁）→ 不写，
    # 回 PERIOD_LOCKED 转人工，ERP 显示原因。Cowork 的推送不带这个标记，照旧写。
    if payload.get("respect_period_lock") and fiscal_period.locked(account_dir, docdat):
        return DbfWriteResult(False, error_code=fiscal_period.ERR_PERIOD_LOCKED,
                              error=f"accounting period of {docdat} is locked in Express")
    # 载荷 21（引导录入，guided）的新键：值不在这本账套里（单位码、分类、科目组、对方类型、现金科目）整单拒收，不回落默认
    refusal = guided.invalid_values(account_dir, payload, guided.STOCK_OUT, "customer")
    if refusal:
        return DbfWriteResult(False, error_code=refusal.code, error=refusal.detail)
    # 账套不允许负库存(ISINFO.NEGALLOW = N):库存不够就整单不写,转人工(出入库第一期,10-25;stock_guard)。
    # 要随单新建的物料按结存 0 一起核(覆盖表 G-B6,11-12:对所有入口生效)
    short = stock_guard.shortages(account_dir, entry.items, locations,
                                  new_rows=entry.items_status == "ok" and bool(entry.items))
    if short:
        return stock_guard.refusal(account_dir, short)
    # 引导模式：原来「缺了就用默认值写」的情形（单位对不上、出库没有成本基础、现金科目不能记账）整单不写，带候选回去出题。
    # 负库存先核（上面），过了才问单位与成本
    refusal = guided.choices_needed(account_dir, payload, guided.STOCK_OUT)
    if refusal:
        return DbfWriteResult(False, error_code=refusal.code, error=refusal.detail, choice=refusal.choice)
    vatprd = S.be_yymmdd_to_greg(payload.get("vat_period_be") or docdate_be)
    base = Decimal(entry.base_amount)
    vat = Decimal(entry.vat_amount)
    total = Decimal(entry.total_amount)
    # 整张免税的单（ERP 增值税登记户、全是免税商品）带 `vat_fields`（AMT01 0、AMTRAT0 全额）：照原生也写销项税记录（实录第 10 条）
    has_vat = vat > 0 or bool(entry.vat_fields)
    # 服务品销项税口径（ARTRN.SRV_VATTYP，11-12 晚）：载荷带 srv_vattyp = 2 且有服务品 → 照原生收付实现记：税贷「未到期销项税」
    # （ISINFO.ACCNUM27）、开单不写销项税记录、单头 VATDAT 空；科目没设或不能记账 → 不写、转人工（同进货的口径 2）
    write_items = entry.items_status == "ok" and bool(entry.items)
    srv = service_vat.for_sale(payload, has_vat=vat > 0, service=not write_items or any(
        str(it.get("item_mode") or "").strip() != stock_sale.ITEM_MODE for it in entry.items))
    deferred_vat = ""
    if srv == service_vat.CASH_BASIS:
        deferred_vat = account_rules.default_account(account_dir, service_vat.DEFERRED_OUTPUT_VAT_SLOT)
        if not (deferred_vat and account_rules.postable_account(account_dir, deferred_vat)):
            return DbfWriteResult(False, error_code=ERR_DEFERRED_VAT,
                                  error=f"deferred output VAT account {deferred_vat!r} (ISINFO."
                                        f"{service_vat.DEFERRED_OUTPUT_VAT_SLOT}) is not postable")
    records_vat = has_vat and not deferred_vat

    # 不查重:账套里已有同号 / 同票的单照写(用户 2026-09-30、2026-10-08:Express 手工录入也不拦重复)。
    try:
        existing_code = entry.customer_code or (
            ""
            if entry.customer_confirmed_new
            else master_create.find_customer_code(
                account_dir,
                entry.customer_tax_id,
                entry.customer_name,
                branch=entry.customer_branch,
            )
        )
    except master_create.AmbiguousPartyMatch as exc:
        # 连锁同税号多档且分店号/名字定不出唯一 → 转人工,绝不静默记到总店头上。
        return DbfWriteResult(False, error_code=ERR_CUSTOMER_AMBIGUOUS, error=str(exc))
    if existing_code:
        entry.customer_code = existing_code
    elif not entry.customer_confirmed_new and master_create.suspected_customer_dup(account_dir, entry):
        # 新客户疑似重复(核心名相似·税号不同)→ 不静默建重复户,转人工确认(镜像采购)。
        return DbfWriteResult(False, error_code=ERR_CUSTOMER_DUP, error="dup")

    clock.lap("checks")
    backup_dir = _backup(account_dir, S.SALES_BACKUP_TABLES + stock_location_tables(locations)
                         + dbf_remarks.tables(entry.items)) if make_backup else None
    clock.lap("backup")
    docnum = ""
    moves = [] if locations is not None else None
    costing: dict = {}  # 库存行共用的取价状态(``stock_costing``),写完把分仓链记进 STLOC
    try:
        since = _table_sizes(account_dir)  # 读回只核对这之后追加的行(见 _VERIFY_TABLES)
        journal_since = pos_journal.sizes(account_dir) if journal else {}
        # 新客户 → 先在 ARMAS 建档(备份-回滚事务内)拿到码
        # 云端给了现金客户码、账套里却没有时 ensure_customer 也会新建:同样要重建 ARMAS 索引、回执标「新建主档」
        cash_missing = existing_code == master_create.CASH_CUSTOMER and not master_create._code_exists(
            account_dir, "ARMAS", "CUSCOD", master_create.CASH_CUSTOMER
        )
        entry.customer_code = master_create.ensure_customer(
            account_dir, entry, created_date=docdat, userid=userid
        )
        created_customer = bool(not existing_code) or (
            cash_missing and entry.customer_code == master_create.CASH_CUSTOMER
        )
        # 补期初:同一备份-回滚事务内先垫库存(STMAS/STCRD 已在 BACKUP_TABLES),_write_all 的
        # 库存卖行随即复用①真扣库存 + 移动均价 COGS。期初或后续过账任一失败 → 一并回滚,不留半吊子。
        if opening_stock:
            stock_sale.set_opening_stock(account_dir, opening_stock, docdat)
        docnum, line_modes, has_stock, notes, gl_rows = _write_all(
            account_dir,
            entry,
            docdate_be,
            docdat,
            vatprd,
            base,
            vat,
            total,
            has_vat,
            userid,
            depcod,
            **_located(locations, moves, costing),
            srv=srv,
            deferred_vat=deferred_vat,
        )
        stloc_expected, notes = record_locations(account_dir, moves, notes)
        chain_expected = record_chains(account_dir, costing)
        # 按台号码:明细行号 = 载荷顺序(库存 / 非库存两条路都从 1 起,混行转人工)
        remarks = dbf_remarks.write(account_dir, docnum, enumerate(entry.items, start=1))
        vouchers = []
        if journal:
            # 现金科目 = 本单借方实际记的那个(_write_all 已按账套换成 ACCNUM00;账套没设就是云端给的那个)
            cash_acc = next(str(ln.get("acc") or "") for ln in entry.lines
                            if str(ln.get("side", "")).upper() == "D")
            voucher_dep = depcod or account_rules.book(account_dir, journal).get("DEPCOD", "")
            vouchers = pos_journal.write(
                account_dir, journal, docnum=docnum, docdat=docdat, docdate_be=docdate_be, cash_acc=cash_acc,
                transfers=entry.pos_transfers, userid=userid, depcod=voucher_dep,
                departmental=_department_accounts(account_dir) if voucher_dep else frozenset(),
            )
            notes = {**(notes or {}), "pos_vouchers": [item["voucher"] for item in vouchers]}
        clock.lap("write")
        detail_n = (
            len(entry.items) if (entry.items_status == "ok" and entry.items) else 0
        )
        _reindex_or_defer(
            account_dir,
            S.SALES_WRITE_TABLES,
            detail_n,
            "ARMAS" if created_customer else "",
            index_batch,
            more=(S.LOCATION_TABLES if stloc_expected else ()) + dbf_remarks.tables(entry.items),
        )
        clock.lap("reindex")
        _verify(
            account_dir,
            docnum,
            total,
            records_vat,  # 口径 2 开单不写销项税记录
            base=base,
            detail_n=detail_n,
            has_stock=has_stock,
            since=since,
            stock_seqs=dbf_detail.stock_line_numbers(line_modes),
        )
        verify_locations(account_dir, stloc_expected)
        verify_chains(account_dir, chain_expected)
        check_remarks(account_dir, docnum, remarks)
        pos_differ = (pos_journal.verify(account_dir, vouchers, cash_acc, journal_since, docdat=docdat)
                      if vouchers else [])
        clock.lap("verify")
        _verify_index(account_dir, cdx_reindex.TAG_AR_DOCNUM, docnum)
        clock.lap("index_check")
        # 写完顺手核对(第 3 期第 6 条):只报不回滚,随回执 meta.verify 交回
        verify = write_verify.check(account_dir, write_verify.Expected(
            side="sales", docnum=docnum, party_code=entry.customer_code, docdat=docdat,
            rectyp=S.SALES_RECTYP[entry.doctype], refnum=entry.ref_no, lines=[row[:3] for row in gl_rows],
            tax_id=entry.customer_tax_id, branch=entry.customer_branch, has_vat=records_vat, since=since),
            extra=pos_differ)
        clock.lap("write_check")
        _discard_backup(backup_dir)  # 校验通过(已提交)→ 删快照,磁盘恒定
        product_master.backfill_committed(account_dir)  # 这本账套的直接科目行商品补过了（只在这张单里补过时记）
        clock.lap("cleanup")
        clock.log(docnum, "ok")
        return DbfWriteResult(
            True,
            docnum=docnum,
            backup_dir=None,
            party_code=entry.customer_code,
            created_party=created_customer,
            line_modes=line_modes,
            notes=notes,
            verify=verify,
            revenue=revenue_entries(gl_rows),
        )
    except Exception as e:
        clock.lap("failed_step")
        if backup_dir:
            _restore(account_dir, backup_dir)
            _discard_backup(
                backup_dir
            )  # 已回滚 → 删快照(保留路径串作 rolled_back 标记)
        clock.lap("rollback")
        clock.log(docnum, "rolled_back")
        code = (
            ERR_STOCK_NOT_FOUND
            if isinstance(e, stock_sale.StockItemNotFound)
            else stock_template.ERR_STOCK_GROUP
            if isinstance(e, stock_template.InventoryAccountUnfit)
            else e.code  # 直接记科目要的商品码已是库存品:交人(1.12.11)
            if isinstance(e, product_master.AccountItemIsStock)
            else (
                e.args[1]
                if (isinstance(e, _WriteError) and len(e.args) > 1)
                else ERR_WRITE
            )
        )
        log.exception("dbf sale write failed, rolled back")
        return DbfWriteResult(
            False,
            error_code=code,
            error=str(e),
            backup_dir=str(backup_dir) if backup_dir else None,
            evidence=e.evidence if isinstance(e, _WriteError) else None,
        )


def _write_all(
    account_dir,
    entry,
    docdate_be,
    docdat,
    vatprd,
    base,
    vat,
    total,
    has_vat,
    userid,
    depcod="",
    *,
    locations=None,
    moves=None,
    costing=None,
    srv=None,
    deferred_vat="",
) -> tuple:
    """写完销项六表,返回 (凭证号, 逐行 mode, 有无库存行, 诚实标注, 实际写的分录 [(SEQIT, ACCNUM, 借贷, 金额)])。
    ``srv`` / ``deferred_vat``:服务品销项税口径与口径 2 的「未到期销项税」科目(``service_vat.for_sale``);不给同以前。"""
    prefix = entry.doctype  # IV / HS
    cash = prefix == "HS"
    # 按这套账自己的设置(2026-10-08):单据簿的日记账 / 默认部门;赊销的应收行记客户档上的科目、到期日按客户档
    # 付款期(以前恒用账套默认应收科目,62 套里 44% 的客户挂的不是它,控制科目与明细账对不上)。
    book = account_rules.book(account_dir, prefix)
    jnltyp = book.get("JNLTYP") or S.JNLTYP_SALES
    depcod = depcod or book.get("DEPCOD", "")
    duedat = None
    debits = [ln for ln in entry.lines if str(ln.get("side", "")).upper() == "D"]
    payterm = 0
    if cash:
        # 现销:Express 原生借现金(ISINFO.ACCNUM00),完全不碰应收(真实账套 1.4 万张 HS 无一例外;2026-10-08 核对)。
        # 载荷 21:人在引导卡上选了收款科目(``cash_account``,现金或银行,写前已核可记账)就借它
        cash_acc = entry.cash_account or account_rules.default_account(account_dir, "ACCNUM00")
        if debits and cash_acc and account_rules.postable_account(account_dir, cash_acc):
            debits[0]["acc"] = cash_acc
    else:
        if debits:
            debits[0]["acc"] = account_rules.control_account(
                account_dir, "ARMAS", entry.customer_code, str(debits[0].get("acc") or "")
            )
        payterm = account_rules.party_terms(account_dir, "ARMAS", entry.customer_code)[1]
        duedat = account_rules.due_date(docdat, payterm)
        if entry.due_date_be:
            # 载荷 21:复核卡上的到期日(票面写的 / 人改的)盖过客户档账期;账期天数 = 到期日 − 单据日(同原生:到期日 = 单据日 + 账期)
            duedat = S.be_yymmdd_to_greg(entry.due_date_be)
            payterm = (duedat - docdat).days
    name = entry.customer_name
    salesman = account_rules.party_salesman(account_dir, entry.customer_code)
    # 凭证表头与销项税记录的分店号 = 这家客户档的分店号(税表分店一栏;真账一致 99%+)。
    orgnum = master_create.party_orgnum(account_dir, "ARMAS", "CUSCOD", entry.customer_code)
    flgvat, amount, aftdisc, total_, netval, netamt = amount_fields(
        cash, has_vat, base, vat, total
    )

    # V1 安全明细:仅 items_status==ok 落 STCRD。NXTSEQ 必须=明细行数(否则 Express 视作
    # 0 行清掉孤儿明细);表头模式(无明细)保持 audit_fields 默认,零回归。
    write_detail = entry.items_status == "ok" and bool(entry.items)
    nxtseq_override = {"NXTSEQ": str(len(entry.items))} if write_detail else {}

    ar = _open(account_dir, "ARTRN", dbf.READ_WRITE)
    try:
        # POS 日汇总票单号自编(HS + 佛历年月日 + 序号);票号起止写进销项税摘要与参考号
        docnum = (_next_docnum(ar, prefix, docdate_be) if entry.is_pos
                  else _sales_docnum(ar, prefix, docdate_be, entry.ref_no))
        # 销项 ARTRN.VATPRD 真单恒空(税期记在 ISVAT);CSHRCV/IVCAMT 现销=含税·赊销=0。
        S.append_native(ar, 
            {
                "RECTYP": S.SALES_RECTYP[prefix],
                "DOCNUM": docnum,
                "DOCDAT": docdat,
                "YOUREF": entry.ref_no,
                "FLGVAT": flgvat,
                "CUSCOD": entry.customer_code,
                "VATRAT": float(entry.vat_rate),
                "AMOUNT": amount,
                "AFTDISC": aftdisc,
                "TOTAL": total_,
                "NETVAL": netval,
                "VATAMT": _f(vat),
                "NETAMT": netamt,
                # 免税部分（载荷 `vat_fields` 的 AMTRAT0）：单头同销项税记录也记一份（Express 原生，实录第 10 条）；老账套没有这一栏不写
                **({"AMTRAT0": _f(Decimal(entry.vat_fields["AMTRAT0"]))}
                   if "AMTRAT0" in entry.vat_fields and "AMTRAT0" in {f.upper() for f in ar.field_names} else {}),
                "RCVAMT": netamt if cash else 0.0,
                "REMAMT": 0.0 if cash else netamt,
                "CSHRCV": netamt if cash else 0.0,
                "IVCAMT": netamt if cash else 0.0,
                "DOCSTAT": "N",
                "USERID": userid,
                **audit_fields(docdat, depcod, has_vat, orgnum, duedat, srv=srv),
                **({"VATDAT": None} if deferred_vat else {}),  # 口径 2:收款时 Express 才记税期(原生单头 VATDAT 空)
                **({"PAYTRM": payterm} if "PAYTRM" in ar.field_names else {}),
                # 销售员照 Express 取客户档上的（G-A15，11-12）；客户档没有就不写这一栏（同以前）
                **({"SLMCOD": salesman} if salesman and "SLMCOD" in {f.upper() for f in ar.field_names} else {}),
                **nxtseq_override,
            }
        )
    finally:
        ar.close()

    line_modes = []
    cogs_rows = []  # V2-b:卖库存品的 COGS 分录(借成本/贷存货),接在 asset/rev/vat 之后
    if write_detail:
        items = inclusive_items(entry.items, flgvat, total)  # 现销价含税：明细 UNITPR / TRNVAL 记含税（同原生）
        # 混行(库存品 + 非库存,载荷 19,ERP 按行选出库 / 只记账,11-06):照载荷顺序分段写,每行 SEQNUM = 整张单的行号
        # (以前两条路各从 1 起会撞,V1 整单拒收);只有库存行扣库存、结转成本。只有一种行时调用与以前一字不差。
        segments = dbf_detail.runs(items, lambda it: str(it.get("item_mode") or "") == stock_sale.ITEM_MODE)
        whole = len(segments) == 1
        for stock, seqs, part in segments:
            numbered = {} if whole else {"seqs": seqs}
            if stock:
                modes, rows = stock_sale.write_stock_lines(
                    account_dir,
                    docnum,
                    docdat,
                    part,
                    entry.customer_code,
                    depcod=depcod,
                    userid=userid,
                    acccod=getattr(entry, "stock_acccod", ""),
                    slmcod=salesman,
                    **_located(locations, moves, costing),
                    **numbered,
                )
                line_modes += modes
                cogs_rows = merge_gl_rows(cogs_rows + rows)
            else:
                line_modes += _write_stcrd_detail(
                    account_dir, docnum, docdat, entry, depcod, userid=userid, items=part, **_located(locations),
                    slmcod=salesman, **numbered,
                )

    if has_vat and not deferred_vat:  # 口径 2 开单不写销项税记录(收款时 Express 转)
        iv = _open(account_dir, "ISVAT", dbf.READ_WRITE)
        try:
            columns = {f.upper() for f in iv.field_names}
            # 载荷 9:来源按规则算好的金额栏(混有免税商品时 AMT01 只记应税部分);账套里没有的栏不写、记一笔
            given = {name: _f(Decimal(value)) for name, value in entry.vat_fields.items() if name in columns}
            if len(given) != len(entry.vat_fields):
                log.warning("ISVAT 没有这些栏,不写: %s", sorted(set(entry.vat_fields) - set(given)))
            iv.append(
                {
                    "VATREC": S.ISVAT_VATREC_OUTPUT,
                    "RECTYP": S.SALES_RECTYP[prefix],
                    "VATPRD": vatprd,
                    "VATDAT": docdat,
                    "DOCDAT": docdat,
                    "DOCNUM": docnum,
                    # 报税单号必须是票面那张的号,不是我们生成的凭证号 —— ภ.พ.30 销项税
                    # 报告出自本表,填 docnum 会让报告与客户手上的实体票对不上(税局按票号
                    # 核)。采购侧一直是 entry.ref_no,销项"镜像采购"时这一处抄反了。
                    "REFNUM": entry.ref_no,
                    "DESCRP": (pos_journal.vat_description(entry.pos_first, entry.pos_last) if entry.is_pos
                               else name[:60]),
                    "AMT01": _f(base),
                    "VAT01": _f(vat),
                    "DOCSTAT": "N",
                    "TAXID": entry.customer_tax_id,
                    "PRENAM": entry.customer_prename[:15],
                    **({"ORGNUM": orgnum} if "ORGNUM" in columns else {}),
                    **given,
                }
            )
        finally:
            iv.close()

    descrp = (f"ขายเชื่อให้ {name}" if prefix == "IV" else f"ขายสดให้ {name}")[:50]
    if entry.is_pos:
        descrp = pos_journal.sale_description(entry.pos_first, entry.pos_last)
    gj = _open(account_dir, "GLJNL", dbf.READ_WRITE)
    try:
        gj.append(S.journal_stamp(gj, {  # 稽核栏照原生（录入人 / 当天，``dbf_schema.journal_stamp``）
                "JNLTYP": jnltyp,
                "VOUDAT": docdat,
                "VOUCHER": docnum,
                "DESCRP": descrp,
                "TRNSTAT": "P",
                "DOCSTAT": "N",
                "CREBY": S.PEARNLY_USERID,  # 凭证建立人一律记机器标记（``dbf_schema.is_pearnly``）
            }, userid=userid))
    finally:
        gj.close()

    departmental = _department_accounts(account_dir) if depcod else frozenset()
    gl_rows = revenue_journal(account_dir, _gl_rows(entry), entry.items if write_detail else [], line_modes,
                              entry.items_account) + cogs_rows
    if deferred_vat:  # 口径 2:税那一行贷「未到期销项税」(原生 4124 / 4127 张)
        gl_rows = [(seq, deferred_vat if seq == S.SALES_SEQIT["output_vat"] else acc, side, amt)
                   for seq, acc, side, amt in gl_rows]
    gi = _open(account_dir, "GLJNLIT", dbf.READ_WRITE)
    written = [_gl_line(docnum, seqit, docdat, accnum, descrp, trntyp, amt, depcod, departmental)
               for seqit, accnum, trntyp, amt in gl_rows]
    try:
        for line in written:
            S.append_native(gi, S.journal_stamp(gi, line))
    finally:
        gi.close()
    glbal_mark.mark(account_dir, written)  # 科目余额对应格标待重算（照 Express 原生，11-12）

    _bump_isrun(account_dir, prefix, jnltyp)
    notes = _update_arbal(account_dir, entry.customer_code, docdat, prefix, total)
    if prefix == "IV":
        # 赊销加客户余额（原生；现销 HS 不动，``master_create.move_party_balance``）
        master_create.move_party_balance(account_dir, "ARMAS", entry.customer_code, total)
    return docnum, line_modes, stock_sale.has_stock_lines(line_modes), notes, gl_rows


def _write_stcrd_detail(
    account_dir, docnum: str, docdat, entry, depcod: str = "", userid: str = S.PEARNLY_USERID, locations=None,
    items=None, seqs=None, slmcod: str = "",
):
    """逐行写 STCRD(委托共用 dbf_detail·PEOPLE=客户码)。返回逐行 mode 结果。``items`` 不给用 ``entry.items``;
    ``seqs``:混行单分段写时这几行在整张单里的行号。"""
    return dbf_detail.write_stcrd_detail(
        account_dir,
        docnum,
        docdat,
        entry.items if items is None else items,
        entry.customer_code,
        entry.items_account,
        depcod=depcod,
        userid=userid,
        slmcod=slmcod,
        **_located(locations),
        **({"seqs": seqs} if seqs is not None else {}),
    )


EXPENSE_GROUP = "5"  # GLACC.GROUP:1 资产、2 负债、3 权益、4 收入、5 费用


def revenue_journal(account_dir, gl_rows, items, line_modes, items_account: str = ""):
    """收入照 Express 原生按物料科目组记(11-08,S 盘 95 本取证,``docs/work/2026-11-07-cc-sales-revenue-native-评估.md``):
    明细行的物料科目组配了收入科目 ``ISACC.ACCNUM03`` 且能记账 → 这一行的税前额从它原来那条收入贷方里扣出、改记 ACCNUM03
    (SEQIT 5,同科目合成一条);科目组没配 / 不能记账 / 是费用类照载荷(原生库存品几乎都没配,记账套默认收入 ACCNUM11,载荷本来就是它)。

    - 一行原来记在哪条收入:载荷收入有几条(ERP 按行科目拆)时找科目 = 这一行 ``account`` 的那条,再找 ``items_account`` 的那条,
      都没有用第一条;
    - 扣出的合计超过原来那条收入(行额与收入对不上,如整单折扣)→ 那一条整条照载荷,不改、不报错(以前就是照载荷写的)。
    """
    revenue_seq = S.SALES_SEQIT["revenue"]
    revenue = [i for i, row in enumerate(gl_rows) if row[0] == revenue_seq and row[2] == S.TRNTYP_CREDIT]
    lines = [(m.get("seq"), str(m.get("stkcod") or "").strip()) for m in line_modes or [] if isinstance(m, dict)]
    lines = [(seq, code) for seq, code in lines if code and isinstance(seq, int) and 1 <= seq <= len(items)]
    if not revenue or not lines:
        return gl_rows
    groups = account_rules.item_group_accounts(account_dir, [code for _seq, code in lines], "ACCNUM03")
    postable: Dict[str, bool] = {}
    moves: Dict[int, Dict[str, Decimal]] = {}
    for seq, code in lines:
        target = groups.get(code, "")
        item = items[seq - 1]
        amount = Decimal(str(item.get("amount") or "0"))
        if not target or amount <= 0:
            continue
        source = _revenue_row(gl_rows, revenue, str(item.get("account") or ""), items_account)
        if gl_rows[source][1] == target:
            continue
        if target not in postable:
            # 能记账、且不是费用类(GLACC.GROUP 5):S 盘 95 本原生非库存行贷科目组收入科目的,收入类 8.9 万行、负债类(预收定金)5,184、
            # 资产类 1,528、费用类只有 51 行——按品名配到采购侧建的费用品时照载荷记收入,不贷进费用科目(主会话 11-08)
            postable[target] = account_rules.postable_account(account_dir, target) \
                and account_rules.account_group(account_dir, target) != EXPENSE_GROUP
        if not postable[target]:
            continue
        bucket = moves.setdefault(source, {})
        bucket[target] = bucket.get(target, Decimal("0")) + amount
    out = []
    for i, row in enumerate(gl_rows):
        moved = sum(moves.get(i, {}).values(), Decimal("0"))
        if not moved:
            out.append(row)
            continue
        seqit, acc, side, amount = row
        rest = Decimal(str(amount)) - moved
        if rest < 0:
            log.warning("收入科目组改记跳过:行额 %s 超出收入 %s(%s)", moved, amount, acc)
            out.append(row)
            continue
        out += [(seqit, target, side, value) for target, value in moves[i].items()]
        if rest:
            out.append((seqit, acc, side, rest))
    return merge_gl_rows(out)


def revenue_entries(gl_rows) -> list:
    """实际记的收入贷方(SEQIT 5)[{acc, amount}]:随回执 ``meta.revenue`` 交回服务端,读回比分录按它(收入按科目组拆了科目,载荷里没有)。"""
    return [{"acc": str(acc).strip(), "amount": f"{Decimal(str(amount)):.2f}"} for seqit, acc, side, amount in gl_rows
            if seqit == S.SALES_SEQIT["revenue"] and side == S.TRNTYP_CREDIT]


def _revenue_row(gl_rows, revenue, account: str, items_account: str) -> int:
    """这一行原来记在哪条收入(下标):科目 = 行科目的那条 → 科目 = ``items_account`` 的那条 → 第一条。"""
    for wanted in (account.strip(), str(items_account or "").strip()):
        hit = next((i for i in revenue if wanted and str(gl_rows[i][1]).strip() == wanted), None)
        if hit is not None:
            return hit
    return revenue[0]


def merge_gl_rows(rows):
    """同一 (SEQIT, 科目, 借贷) 的分录合并成一条、按第一次出现的顺序(混行单几段库存行各自算出的成本分录,合起来与一次写完相同)。"""
    merged = {}
    for seqit, accnum, trntyp, amount in rows:
        key = (seqit, accnum, trntyp)
        merged[key] = merged.get(key, Decimal("0")) + Decimal(str(amount))
    return [(seqit, accnum, trntyp, amount) for (seqit, accnum, trntyp), amount in merged.items()]


def _update_arbal(
    account_dir: Path, cuscod: str, docdat, doctype: str, total: Decimal
) -> dict:
    """销项应收按单别 × 会计期分桶累加。IV(赊销)→ 'IV{期}',HS(现销)→ 'HS{期}'。

    期号是 ISPRD 财年期号 + 年槽,不是单号里的日历月(见 fiscal_period)。判不出就不写桶,
    把原因回给调用方上报 —— 猜一个 CY 出来是最坏的选项:客户对账单错了不报错,试算表照样平。
    """
    field, why = fiscal_period.resolve_month_field(account_dir, docdat, doctype.upper())
    if field is None:
        log.warning("ARBAL 月桶未写(会计需人工补记):%s", why)
        return {"arbal_not_written": why}
    t = _open(account_dir, "ARBAL", dbf.READ_WRITE)
    try:
        # 只往还算数的余额行上加:命中软删行 = 这笔应收加进了 Express 看不见的行,客户
        # 余额凭空少一截。找不到活行就 append 一条新的(下方),这才是真的"没有就建"。
        for r in S.live_rows(t):
            if (r.CUSCOD or "").strip() == cuscod:
                with r as rec:
                    rec[field] = float(Decimal(str(rec[field] or 0)) + total)
                return {}
        t.append({"CUSCOD": cuscod, field: _f(total)})
    finally:
        t.close()
    return {}


# 写后读回只核对**本次追加**的行:写入前记下这几张表的行数,之后追加的行记录号都 >= 它。
_VERIFY_TABLES = ("ARTRN", "STCRD", "GLJNLIT", "ISVAT")


def _table_sizes(account_dir: Path) -> Dict[str, int]:
    sizes = {}
    for name in _VERIFY_TABLES:
        t = _open(account_dir, name, dbf.READ_ONLY)
        try:
            sizes[name] = len(t)
        finally:
            t.close()
    return sizes


def _appended(table, since: int):
    return (r for r in S.live_rows(table) if dbf.recno(r) >= since)


def _verify(
    account_dir: Path,
    docnum: str,
    total: Decimal,
    has_vat: bool,
    *,
    base: Optional[Decimal] = None,
    detail_n: int = 0,
    has_stock: bool = False,
    since: Optional[Dict[str, int]] = None,
    stock_seqs=None,
) -> None:
    since = since or {}
    # 读回一律只看还算数的行。销项 DOCNUM 就是票面发票号,删单重推时新单与那条软删的旧单
    # 同号:连软删行一起读,ARTRN 会先命中旧行(拿旧 NETAMT/NXTSEQ 判新单),GLJNLIT 会把
    # 旧分录金额一起累加而判成借贷不平 —— 幂等闸放行之后,单据就改在这里被判失败回滚。
    ar = _open(account_dir, "ARTRN", dbf.READ_ONLY)
    try:
        hit = next(
            (r for r in _appended(ar, since.get("ARTRN", 0))
             if (r.DOCNUM or "").strip() == docnum), None
        )
        if hit is None or abs(Decimal(str(hit.NETAMT)) - total) > Decimal("0.02"):
            ev = {
                "kind": "readback_verify",
                "table": "ARTRN",
                "docnum": docnum,
                "expected_total": float(total),
            }
            if hit is not None:
                ev["found_total"] = float(hit.NETAMT)
            raise _WriteError("ARTRN row missing or NETAMT mismatch", ERR_VERIFY, ev)
        if detail_n and (hit.NXTSEQ or "").strip() != str(detail_n):
            nxt = (hit.NXTSEQ or "").strip()
            raise _WriteError(
                f"ARTRN.NXTSEQ {hit.NXTSEQ!r} != line count {detail_n}",
                ERR_VERIFY,
                {
                    "kind": "readback_verify",
                    "table": "ARTRN",
                    "docnum": docnum,
                    "expected_lines": detail_n,
                    "found_lines": int(nxt) if nxt.isdigit() else None,
                },
            )
    finally:
        ar.close()

    if detail_n:
        _verify_detail(account_dir, docnum, base, detail_n, has_stock=has_stock,
                       since_recno=since.get("STCRD", 0), stock_seqs=stock_seqs)

    gi = _open(account_dir, "GLJNLIT", dbf.READ_ONLY)
    try:
        dr = cr = asset_dr = Decimal("0")
        rows = 0
        for r in _appended(gi, since.get("GLJNLIT", 0)):
            if (r.VOUCHER or "").strip() == docnum:
                rows += 1
                amt = Decimal(str(r.AMOUNT))
                if (r.TRNTYP or "").strip() == S.TRNTYP_DEBIT:
                    dr += amt
                    if (r.SEQIT or "").strip() == S.SALES_SEQIT["asset"]:
                        asset_dr += amt
                else:
                    cr += amt
        # V2-b:卖库存品多一对 COGS 分录(借成本/贷存货),dr/cr 各 +COGS 仍平。故校验
        # 「借贷平 + 资产借方(应收/现金)== 销售总额」,不再要求总借方==总额(否则 COGS 误判不平)。
        if rows < 2 or dr != cr or asset_dr != total:
            raise _WriteError(
                f"GL not balanced (dr={dr} cr={cr} asset={asset_dr} total={total} rows={rows})",
                ERR_VERIFY,
                {
                    "kind": "readback_verify",
                    "table": "GLJNLIT",
                    "docnum": docnum,
                    "expected_total": float(total),
                    "found_total": float(asset_dr),
                    "dr": float(dr),
                    "cr": float(cr),
                    "rows": rows,
                },
            )
    finally:
        gi.close()

    if has_vat:
        iv = _open(account_dir, "ISVAT", dbf.READ_ONLY)
        try:
            if not any(
                (r.DOCNUM or "").strip() == docnum
                and (r.VATREC or "").strip() == S.ISVAT_VATREC_OUTPUT
                for r in _appended(iv, since.get("ISVAT", 0))
            ):
                raise _WriteError(
                    "ISVAT('S') row missing",
                    ERR_VERIFY,
                    {"kind": "readback_verify", "table": "ISVAT", "docnum": docnum},
                )
        finally:
            iv.close()


def _verify_detail(
    account_dir: Path,
    docnum: str,
    base: Optional[Decimal],
    detail_n: int,
    *,
    has_stock: bool = False,
    since_recno: int = 0,
    stock_seqs=None,
) -> None:
    """读回明细(委托共用 dbf_detail);不符 → 抛错触发回滚。V2-b 卖库存放行库存影响行(混行单只放行 ``stock_seqs`` 那几行)。"""
    try:
        err = dbf_detail.verify_detail(
            account_dir, docnum, base, detail_n, allow_stock_impact=has_stock,
            since_recno=since_recno, stock_seqs=stock_seqs,
        )
    except dbf_detail.DetailVerifyError as e:
        # 明细读回不符:message 一字不改(既有按文本 match 的断言依赖),证据随第三参带走。
        raise _WriteError(e.message, ERR_VERIFY, evidence=e.evidence) from e
    if err:
        raise _WriteError(err, ERR_VERIFY)
