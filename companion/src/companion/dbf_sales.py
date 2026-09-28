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
from . import dbf_schema as S
from . import fiscal_period
from . import master_create
from . import stock_sale
from . import cdx_reindex
from . import duplicate_gate as D
from . import write_gates as G
from .consistency import account_company_matches
from .dbf_writer import (
    ERR_ACCOUNT_DIR,
    ERR_PRIOR_DOC_EXISTS,  # noqa: F401  re-export · 云端与测试从本模块取
    ERR_COMPANY,
    ERR_FIELD_WIDTH,
    ERR_VALIDATION,
    ERR_VERIFY,
    ERR_WRITE,
    DbfWriteResult,
    _backup,
    _bump_isrun,
    _discard_backup,
    _f,
    _next_docnum,
    _open,
    _reindex_or_defer,
    _restore,
    _verify_index,
    _WriteError,
    _docnum_exists,  # noqa: F401  re-export · 防重单判读与采购共用同一份
    amount_fields,
    audit_fields,
)
from .logger import get_logger
from .sales_adapter import (
    ALLOWED_ACCOUNT_SETS,
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
    for ln in entry.lines:
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
      · 幂等靠 YOUREF+CUSCOD(见 _find_existing),与本函数选什么号无关。
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


def _find_existing(artrn, ref_no: str, cuscod: str) -> Optional[str]:
    """幂等:同 YOUREF+CUSCOD 的销售单已存在 → 返回**最后**那张的 DOCNUM。

    只认还算数的行(S.live_rows)。会计在 Express 里删掉单据后行仍软删躺在 ARTRN 里,连
    软删行一起认就是「删了也再也导不进来,且每次都报成功」——2026-07-25 现场那张
    IV690531-001 正是死在这里。

    取最后一张而不是第一张:理由与采购侧同(见 dbf_writer._find_existing)。销项这边号还会撞
    —— DOCNUM 取的就是票面票号,确认后落的第二张与第一张同号,单靠本函数分不开,故 head_row
    也取最后一张,两处合起来才让重投比得上刚写的那张。
    """
    sales_rectyp = set(S.SALES_RECTYP.values())  # {'3','1'}
    latest = None
    for r in S.live_rows(artrn):
        if (
            (r.YOUREF or "").strip() == ref_no
            and (r.CUSCOD or "").strip() == cuscod
            and (r.RECTYP or "").strip() in sales_rectyp
        ):
            latest = (r.DOCNUM or "").strip()
    return latest


def write_sale(
    payload: Dict[str, Any],
    account_dir: str | Path,
    *,
    userid: str = "BIT9",
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
    v = validate_sales_payload(payload, allowed=allowed_sets)
    if not v.ok:
        return DbfWriteResult(
            False, error_code=ERR_VALIDATION, error=f"{v.error_code}: {v.detail}"
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

    docdate_be = str(payload["docdate_be"])
    docdat = S.be_yymmdd_to_greg(docdate_be)
    vatprd = S.be_yymmdd_to_greg(payload.get("vat_period_be") or docdate_be)
    base = Decimal(entry.base_amount)
    vat = Decimal(entry.vat_amount)
    total = Decimal(entry.total_amount)
    has_vat = vat > 0

    # 闸序 = 查重 → 防重单(与桥骨架一字对齐 · 理由见 write_gates 模块头)。
    # 解析已有客户码(只读)→ 查重。无码新客户留待备份后建档。
    def _open_ar():
        return _open(account_dir, "ARTRN", dbf.READ_ONLY)

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

    dup, early = G.check_duplicate(
        _open_ar,
        _find_existing,
        entry.ref_no,
        existing_code or "",
        payload,
        base=base,
        vat=vat,
        total=total,
        docdat=docdat,
        label="销售单",
    )
    if early is not None:
        return early
    blocked_prior, waved_prior = G.check_prior_doc(
        _open_ar, payload, dup, label="销售单"
    )
    if blocked_prior is not None:
        return blocked_prior

    backup_dir = _backup(account_dir, S.BACKUP_TABLES) if make_backup else None
    try:
        # 新客户 → 先在 ARMAS 建档(备份-回滚事务内)拿到码
        entry.customer_code = master_create.ensure_customer(
            account_dir, entry, created_date=docdat, userid=userid
        )
        created_customer = bool(not existing_code)
        # 补期初:同一备份-回滚事务内先垫库存(STMAS/STCRD 已在 BACKUP_TABLES),_write_all 的
        # 库存卖行随即复用①真扣库存 + 移动均价 COGS。期初或后续过账任一失败 → 一并回滚,不留半吊子。
        if opening_stock:
            stock_sale.set_opening_stock(account_dir, opening_stock, docdat)
        docnum, line_modes, has_stock, notes = _write_all(
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
        _verify(
            account_dir,
            docnum,
            total,
            has_vat,
            base=base,
            detail_n=detail_n,
            has_stock=has_stock,
        )
        _verify_index(account_dir, cdx_reindex.TAG_AR_DOCNUM, docnum)
        _discard_backup(backup_dir)  # 校验通过(已提交)→ 删快照,磁盘恒定
        return DbfWriteResult(
            True,
            docnum=docnum,
            backup_dir=None,
            party_code=entry.customer_code,
            created_party=created_customer,
            line_modes=line_modes,
            notes={**notes, **D.confirmed_note(dup or waved_prior)},
        )
    except Exception as e:
        if backup_dir:
            _restore(account_dir, backup_dir)
            _discard_backup(
                backup_dir
            )  # 已回滚 → 删快照(保留路径串作 rolled_back 标记)
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
    """写完销项六表,返回 (凭证号, 逐行 mode, 有无库存行, 诚实标注)。"""
    prefix = entry.doctype  # IV / HS
    cash = prefix == "HS"
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
        docnum = _sales_docnum(ar, prefix, docdate_be, entry.ref_no)
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
                **audit_fields(docdat, depcod, has_vat, orgnum),
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
                    "DESCRP": name[:60],
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
    gj = _open(account_dir, "GLJNL", dbf.READ_WRITE)
    try:
        gj.append(
            {
                "JNLTYP": S.JNLTYP_SALES,
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

    gi = _open(account_dir, "GLJNLIT", dbf.READ_WRITE)
    try:
        for seqit, accnum, trntyp, amt in _gl_rows(entry) + cogs_rows:
            gi.append(
                {
                    "VOUCHER": docnum,
                    "SEQIT": seqit,
                    "VOUDAT": docdat,
                    "ACCNUM": accnum,
                    "DESCRP": descrp,
                    "TRNTYP": trntyp,
                    "AMOUNT": float(amt),
                }
            )
    finally:
        gi.close()

    _bump_isrun(account_dir, prefix, S.JNLTYP_SALES)
    notes = _update_arbal(account_dir, entry.customer_code, docdat, prefix, total)
    return docnum, line_modes, stock_sale.has_stock_lines(line_modes), notes


def _write_stcrd_detail(
    account_dir, docnum: str, docdat, entry, depcod: str = "", userid: str = "BIT9"
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


def _verify(
    account_dir: Path,
    docnum: str,
    total: Decimal,
    has_vat: bool,
    *,
    base: Optional[Decimal] = None,
    detail_n: int = 0,
    has_stock: bool = False,
) -> None:
    # 读回一律只看还算数的行。销项 DOCNUM 就是票面发票号,删单重推时新单与那条软删的旧单
    # 同号:连软删行一起读,ARTRN 会先命中旧行(拿旧 NETAMT/NXTSEQ 判新单),GLJNLIT 会把
    # 旧分录金额一起累加而判成借贷不平 —— 幂等闸放行之后,单据就改在这里被判失败回滚。
    ar = _open(account_dir, "ARTRN", dbf.READ_ONLY)
    try:
        hit = next(
            (r for r in S.live_rows(ar) if (r.DOCNUM or "").strip() == docnum), None
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
        _verify_detail(account_dir, docnum, base, detail_n, has_stock=has_stock)

    gi = _open(account_dir, "GLJNLIT", dbf.READ_ONLY)
    try:
        dr = cr = asset_dr = Decimal("0")
        rows = 0
        for r in S.live_rows(gi):
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
                for r in S.live_rows(iv)
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
) -> None:
    """读回明细(委托共用 dbf_detail);不符 → 抛错触发回滚。V2-b 卖库存放行库存影响行。"""
    try:
        err = dbf_detail.verify_detail(
            account_dir, docnum, base, detail_n, allow_stock_impact=has_stock
        )
    except dbf_detail.DetailVerifyError as e:
        # 明细读回不符:message 一字不改(既有按文本 match 的断言依赖),证据随第三参带走。
        raise _WriteError(e.message, ERR_VERIFY, evidence=e.evidence) from e
    if err:
        raise _WriteError(err, ERR_VERIFY)
