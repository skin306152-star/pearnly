"""Express 账套 DBF 销项直写器(镜像 dbf_writer 采购)。

一张赊销(IV)/现销(HS)写 6 表:ISRUN(增号)→ ARTRN(单头)→ ISVAT('S')(销项税)
→ GLJNL('03')+GLJNLIT(分录 Dr 应收/现金 = Cr 销售收入 + Cr 销项税)→ ARBAL(客户应收
按月桶)。STCRD(存货出)本期非存货不碰(同采购延后)。

字段语义逆向真 DATAT gold(现销 HL580301-001·3 行非库存):ARTRN.AMOUNT/TOTAL/NETAMT=
含税、NETVAL=税前、VATAMT=税额、FLGVAT='1'(价含税);ISVAT('S').REFNUM=本单 DOCNUM;
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
from . import account_rules
from . import dbf_schema as S
from . import fiscal_period
from . import master_create
from . import pos_journal
from . import write_verify
from . import stock_sale
from . import cdx_reindex
from .consistency import account_company_matches
from .dbf_writer import (
    ERR_ACCOUNT_DIR,
    ERR_COMPANY,
    ERR_FIELD_WIDTH,
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
    _restore,
    _verify_index,
    _WriteError,
    amount_fields,
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
    """
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
    if not (entry.customer_code or entry.customer_name):
        return DbfWriteResult(
            False,
            error_code=ERR_NEW_CUSTOMER,
            error="customer has neither code nor name",
        )
    overflow = _field_overflow(entry)
    if overflow:
        return DbfWriteResult(False, error_code=ERR_FIELD_WIDTH, error=overflow)
    # POS 日汇总票的付款方式转账凭证:写前先定普通日记账单据簿、核收款科目可记账,定不下来就不动账套。
    journal, journal_error, journal_reason = pos_journal.check(account_dir, entry.pos_transfers)
    if journal_error:
        return DbfWriteResult(False, error_code=journal_error, error=journal_reason)

    docdate_be = str(payload["docdate_be"])
    docdat = S.be_yymmdd_to_greg(docdate_be)
    vatprd = S.be_yymmdd_to_greg(payload.get("vat_period_be") or docdate_be)
    base = Decimal(entry.base_amount)
    vat = Decimal(entry.vat_amount)
    total = Decimal(entry.total_amount)
    has_vat = vat > 0

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
    backup_dir = _backup(account_dir, S.SALES_BACKUP_TABLES) if make_backup else None
    clock.lap("backup")
    docnum = ""
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
        )
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
        )
        clock.lap("reindex")
        _verify(
            account_dir,
            docnum,
            total,
            has_vat,
            base=base,
            detail_n=detail_n,
            has_stock=has_stock,
            since=since,
        )
        pos_differ = (pos_journal.verify(account_dir, vouchers, cash_acc, journal_since, docdat=docdat)
                      if vouchers else [])
        clock.lap("verify")
        _verify_index(account_dir, cdx_reindex.TAG_AR_DOCNUM, docnum)
        clock.lap("index_check")
        # 写完顺手核对(第 3 期第 6 条):只报不回滚,随回执 meta.verify 交回
        verify = write_verify.check(account_dir, write_verify.Expected(
            side="sales", docnum=docnum, party_code=entry.customer_code, docdat=docdat,
            rectyp=S.SALES_RECTYP[entry.doctype], refnum=entry.ref_no, lines=gl_rows,
            tax_id=entry.customer_tax_id, branch=entry.customer_branch, has_vat=has_vat, since=since),
            extra=pos_differ)
        clock.lap("write_check")
        _discard_backup(backup_dir)  # 校验通过(已提交)→ 删快照,磁盘恒定
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
) -> tuple:
    """写完销项六表,返回 (凭证号, 逐行 mode, 有无库存行, 诚实标注, 实际写的分录 [(SEQIT, ACCNUM, 借贷)])。"""
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
        cash_acc = account_rules.default_account(account_dir, "ACCNUM00")
        if debits and cash_acc and account_rules.postable_account(account_dir, cash_acc):
            debits[0]["acc"] = cash_acc
    else:
        if debits:
            debits[0]["acc"] = account_rules.control_account(
                account_dir, "ARMAS", entry.customer_code, str(debits[0].get("acc") or "")
            )
        payterm = account_rules.party_terms(account_dir, "ARMAS", entry.customer_code)[1]
        duedat = account_rules.due_date(docdat, payterm)
    name = entry.customer_name
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
        ar.append(
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
                "RCVAMT": netamt if cash else 0.0,
                "REMAMT": 0.0 if cash else netamt,
                "CSHRCV": netamt if cash else 0.0,
                "IVCAMT": netamt if cash else 0.0,
                "DOCSTAT": "N",
                "USERID": userid,
                **audit_fields(docdat, depcod, has_vat, orgnum, duedat),
                **({"PAYTRM": payterm} if "PAYTRM" in ar.field_names else {}),
                **nxtseq_override,
            }
        )
    finally:
        ar.close()

    line_modes = []
    cogs_rows = []  # V2-b:卖库存品的 COGS 分录(借成本/贷存货),接在 asset/rev/vat 之后
    if write_detail:
        stock_items = [
            it for it in entry.items if str(it.get("item_mode") or "") == "stock_sale"
        ]
        if stock_items and len(stock_items) != len(entry.items):
            # 一单里库存品与非库存混行:SEQNUM 会撞 + 部分扣库存,V1 不静默处理 → 交会计。
            raise _WriteError("混合库存+非库存明细行(V1 不支持)", ERR_STOCK_NOT_FOUND)
        if stock_items:
            line_modes, cogs_rows = stock_sale.write_stock_lines(
                account_dir,
                docnum,
                docdat,
                entry.items,
                entry.customer_code,
                depcod=depcod,
                userid=userid,
                acccod=getattr(entry, "stock_acccod", ""),
            )
        else:
            line_modes = _write_stcrd_detail(
                account_dir, docnum, docdat, entry, depcod, userid=userid
            )

    if has_vat:
        iv = _open(account_dir, "ISVAT", dbf.READ_WRITE)
        try:
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
                    **({"ORGNUM": orgnum} if "ORGNUM" in {f.upper() for f in iv.field_names} else {}),
                }
            )
        finally:
            iv.close()

    descrp = (f"ขายเชื่อให้ {name}" if prefix == "IV" else f"ขายสดให้ {name}")[:50]
    if entry.is_pos:
        descrp = pos_journal.sale_description(entry.pos_first, entry.pos_last)
    gj = _open(account_dir, "GLJNL", dbf.READ_WRITE)
    try:
        gj.append(
            {
                "JNLTYP": jnltyp,
                "VOUDAT": docdat,
                "VOUCHER": docnum,
                "DESCRP": descrp,
                "TRNSTAT": "P",
                "DOCSTAT": "N",
                "CREBY": userid,
            }
        )
    finally:
        gj.close()

    departmental = _department_accounts(account_dir) if depcod else frozenset()
    gl_rows = _gl_rows(entry) + cogs_rows
    gi = _open(account_dir, "GLJNLIT", dbf.READ_WRITE)
    try:
        for seqit, accnum, trntyp, amt in gl_rows:
            gi.append(
                _gl_line(docnum, seqit, docdat, accnum, descrp, trntyp, amt, depcod, departmental)
            )
    finally:
        gi.close()

    _bump_isrun(account_dir, prefix, jnltyp)
    notes = _update_arbal(account_dir, entry.customer_code, docdat, prefix, total)
    return docnum, line_modes, stock_sale.has_stock_lines(line_modes), notes, [row[:3] for row in gl_rows]


def _write_stcrd_detail(
    account_dir, docnum: str, docdat, entry, depcod: str = "", userid: str = S.PEARNLY_USERID
):
    """逐行写 STCRD(委托共用 dbf_detail·PEOPLE=客户码)。返回逐行 mode 结果。"""
    return dbf_detail.write_stcrd_detail(
        account_dir,
        docnum,
        docdat,
        entry.items,
        entry.customer_code,
        entry.items_account,
        depcod=depcod,
        userid=userid,
    )


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
                       since_recno=since.get("STCRD", 0))

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
) -> None:
    """读回明细(委托共用 dbf_detail);不符 → 抛错触发回滚。V2-b 卖库存放行库存影响行。"""
    try:
        err = dbf_detail.verify_detail(
            account_dir, docnum, base, detail_n, allow_stock_impact=has_stock,
            since_recno=since_recno,
        )
    except dbf_detail.DetailVerifyError as e:
        # 明细读回不符:message 一字不改(既有按文本 match 的断言依赖),证据随第三参带走。
        raise _WriteError(e.message, ERR_VERIFY, evidence=e.evidence) from e
    if err:
        raise _WriteError(err, ERR_VERIFY)
