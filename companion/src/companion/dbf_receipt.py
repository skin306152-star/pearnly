# -*- coding: utf-8 -*-
"""收款单 RE（รับชำระหนี้）直写进 Express（载荷 20，``direction = receipt``；收款员在 LINE / CC 录的收款，评估
``docs/work/2026-11-11-collection-receipt-assessment.md`` §4.2、第 0 步与「接口契约」）。

照 NLTTEST 2920 张 Express 原生完成的 RE（1.0.1.94，例 RE2403079 BB 转账、RE2402952 纯现金、RE2402778 TB + DC 折扣、RE2401367.1 部分收款、
RE2401052 多张发票）：

- 单号：ISRUN ``DOCTYP = RE`` 那一行的 ``PREFIX`` + ``DOCNUM``（``DOCNUM`` 存的是下一个号，写完 +1）；账套里已有这个号（含软删行）就往后跳。
- 单头 ARTRN：RECTYP 9、DOCDAT / DUEDAT / CMPLDAT = 收款日、DEPCOD、SLMCOD、CUSCOD、YOUREF = 收款记录号（幂等键）、BILNUM ``~``、
  IVCAMT = NETAMT = Σ发票本次收、CSHRCV = 现金、CHQRCV = CHQPAS = Σ其他方式行（含折扣行）、INTRCV 0、CMPLAPP Y、DOCSTAT M、
  SRV_VATTYP ``-``、ORGNUM = 第一张发票的分店号（原生 3301 / 3301 张相同）、USERID。
- ARRCPIT：每张发票一行（RECTYP 3、RCVAMT 本次收、VATAMT 0）；ARRCPCQ：每种非现金方式一行（CHQNUM = 前缀 + 单号截 12 字），现金只进单头。
- 被收的发票：RCVAMT += 本次、REMAMT -= 本次；收清时 CMPLAPP Y、CMPLDAT = 收款日，没收清的不动这两栏。
- 凭证 GLJNL（单据簿 RE 行的 JNLTYP，原生 02；摘要按它的 JNLEXP 算，``payment_methods.journal_text``）、GLJNLIT：SEQIT 2 借现金
  （ISINFO.ACCNUM00）与各方式科目（``payment_methods``：SHORTNAM→BKMAS，否则 ACCNUM01；同科目合并、按科目排，负的方式行记贷方，
  :func:`journal_rows`），SEQIT 6 贷应收（客户档 ACCNUM，没有用 ISINFO.ACCNUM02）合计；部门按科目「使用部门」记（同其他单）。
- ARBAL 客户行 ``RE{期}`` 加 IVCAMT（``fiscal_period``），ARMAS.BALANCE 减 IVCAMT。
- 银行流水 BKTRN（11-13）：方式经 ``SHORTNAM`` 指到银行户头（BKMAS 有这个 BNKACC）的，每种方式另记一行（原生 NLTTEST 2247 / 2247 张这类
  方式行都有、没指户头的一行都没有，:func:`bank_row`）；只是科目号与户头科目相同、不经 SHORTNAM 的不记。

管钱底线（写前，持 ``PEARNLY.LCK``，worker 已拿着）：同客户、同 YOUREF、没作废的 RE 已在 → 回原号，一行不写（幂等）；逐张重读发票，
任一张不在 / 不是这个客户 / 已作废 / 服务品现金制增值税（:data:`ERR_INVOICE_NOT_FOUND`，本批不收）或当前余额不够本次收（:data:`ERR_ALREADY_COLLECTED`，含已被别人收清）
→ **整单不写**，证据带每张的当前余额；不自动缩小金额。方式不在 / 没科目 / 科目冲突 → :data:`ERR_RECEIPT_TYPE`；有现金但账套没设现金科目
→ :data:`ERR_CASH_ACCOUNT`。写入在一份写前备份里，任何一步失败整单回滚；写完读回单头、两张明细、凭证（借贷平）与发票余额，不符同样回滚。
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import dbf

from . import account_rules
from . import cdx_reindex
from . import dbf_schema as S
from . import fiscal_period
from . import glbal_mark
from . import master_create
from . import payment_methods
from .consistency import account_company_matches
from .dbf_writer import (
    ERR_ACCOUNT_DIR,
    ERR_COMPANY,
    ERR_VALIDATION,
    ERR_VERIFY,
    DbfWriteResult,
    StepClock,
    _WriteError,
    _backup,
    _department_accounts,
    _discard_backup,
    _error_code,
    _gl_line,
    _open,
    _reindex_or_defer,
    _restore,
    _verify_index,
)
from .logger import get_logger
from .purchase_adapter import ALLOWED_ACCOUNT_SETS

log = get_logger(__name__)

DIRECTION = "receipt"
RUN_DOCTYP = "RE"          # ISRUN 单据簿行
RECTYP = "9"               # ARTRN：收款单
INVOICE_RECTYP = "3"       # ARTRN / ARRCPIT：发票（赊销 IV）
DOCSTAT = "M"              # 原生完成的 RE（非支票）
CANCELLED = "C"
BILNUM = "~"
SRV_VATTYP = "-"
JNLTYP = "02"              # 单据簿没写时（原生 RE 行 JNLTYP 02、ISINFO.ARRCPJNL 02）
SEQIT_RECEIVED = "2"       # 借：现金 / 各方式
SEQIT_RECEIVABLE = "6"     # 贷：应收
CASH_SLOT, RECEIVABLE_SLOT = "ACCNUM00", "ACCNUM02"
BALANCE_BUCKET = "RE"
# 服务品现金制增值税的发票（ARTRN.SRV_VATTYP 2）：原生收款时单头另记 FLGVAT / VATDAT（NLTTEST 15 张），本批不收（主会话 11-11，
# 需求用不到），写前遇到整单不写、按 invoice_not_found 转人工，problem cash_basis_vat；``express_ar_open`` 也不列
CASH_BASIS_VAT = "2"

# 失败码（契约原样；ERP 按它显示原因）
ERR_ALREADY_COLLECTED = "already_collected"
ERR_INVOICE_NOT_FOUND = "invoice_not_found"
ERR_RECEIPT_TYPE = "receipt_type_invalid"
ERR_CASH_ACCOUNT = "cash_account_missing"
ERR_BOOK = "JOURNAL_BOOK_NOT_FOUND"            # 账套没有 RE 单据簿 / 收款明细表（同库存单的码）
ERR_CUSTOMER = "CUSTOMER_NOT_IN_ARMAS"
ERR_NOT_POSTABLE = "ACCOUNT_NOT_POSTABLE"
REFUSALS = (ERR_ALREADY_COLLECTED, ERR_INVOICE_NOT_FOUND, ERR_RECEIPT_TYPE, ERR_CASH_ACCOUNT, ERR_BOOK, ERR_CUSTOMER,
            ERR_NOT_POSTABLE)

WRITE_TABLES = ("ISRUN", "ARTRN", "ARRCPIT", "ARRCPCQ", "GLJNL", "GLJNLIT", "ARBAL")
BACKUP_TABLES = WRITE_TABLES + ("ARMAS", "GLBAL", "BKTRN")  # GLBAL：写完分录标待重算（glbal_mark），不重建索引；BKTRN：银行流水（BANK_TABLE）
REQUIRED_TABLES = ("ISRUN", "ARTRN", "ARRCPIT", "ARRCPCQ", "GLJNL", "GLJNLIT", "ARMAS")
# 银行流水：只在有方式指到户头时写（写前备份与占用探测照 BACKUP_TABLES 带上，文件不存在的两处都跳过；重建索引只在真写了时带上）
BANK_TABLE = "BKTRN"
BANK_TRNTYP = "bR"          # 收款进户头（付款是 bP、收支票 QR）
BANK_CHQSTAT = "19"         # 原生 2265 / 2265 行同值，含义没查到，照原生
BANK_JNLTRNTYP = "0"
BANK_REFDOC = "~"
BANK_NAME_WIDTH = 50        # BKTRN.NAME C(50)
_VERIFY_TABLES = ("ARTRN", "ARRCPIT", "ARRCPCQ", "GLJNLIT")

_WIDTH = {"ref_no": 30, "customer": 10, "salesman": 10, "department": 4, "docnum": 12, "prefix": 2, "user": 8}
MAX_INVOICES = 300
MAX_METHODS = 20
_CENT = Decimal("0.01")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class Refused(Exception):
    """写前核不过：整单不写。``evidence`` 随回执 meta.evidence 交回。"""

    def __init__(self, code: str, message: str, evidence: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.evidence = evidence


@dataclass(frozen=True)
class Method:
    prefix: str
    amount: Decimal
    code: str = ""


@dataclass
class Receipt:
    ref_no: str
    doc_date: _dt.date
    customer: str
    salesman: str
    department: str
    invoices: List[Tuple[str, Decimal]]
    cash: Decimal
    methods: List[Method]
    user: str
    total: Decimal = field(default=Decimal(0))


def _bytes(text: str) -> int:
    return len(str(text or "").encode("cp874", "replace"))


def _q2(value) -> Decimal:
    return Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_UP)


def _dec(value) -> Decimal:
    try:
        number = Decimal(str(value if value not in (None, "") else 0))
    except (InvalidOperation, ValueError):
        return Decimal(0)
    return number if number.is_finite() else Decimal(0)


def _money(value, label: str, *, positive: bool, signed: bool = False) -> Decimal:
    """载荷金额：字符串或数字、至多两位小数；``positive`` 时必须 > 0，否则 >= 0；``signed``：可负、不可为 0。"""
    if isinstance(value, bool) or value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError(f"{label} missing")
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        raise ValueError(f"{label} is not a number") from None
    if not number.is_finite() or number != number.quantize(_CENT):
        raise ValueError(f"{label} must have at most two decimals")
    if signed:
        if number == 0:
            raise ValueError(f"{label} out of range")
    elif number < 0 or (positive and number == 0):
        raise ValueError(f"{label} out of range")
    return number.quantize(_CENT)


def _text(value) -> str:
    return str(value or "").strip() if isinstance(value, (str, int)) and not isinstance(value, bool) else ""


def _date(payload: Dict[str, Any]) -> _dt.date:
    raw = payload.get("doc_date")
    if isinstance(raw, str) and _DATE.fullmatch(raw.strip()):
        return _dt.date.fromisoformat(raw.strip())
    be = payload.get("docdate_be")  # 与其他写账载荷同一种写法也认（佛历 YYMMDD）
    if isinstance(be, str) and be.strip():
        return S.be_yymmdd_to_greg(be.strip())
    raise ValueError("doc_date must be YYYY-MM-DD")


def parse(payload: Dict[str, Any], allowed: tuple = ALLOWED_ACCOUNT_SETS) -> Receipt:
    """载荷 → :class:`Receipt`；不合格抛 ``ValueError``（调用方回 ``PAYLOAD_INVALID``）。"""
    if str(payload.get("direction") or "").strip().lower() != DIRECTION:
        raise ValueError("direction must be receipt")
    account_set = str(payload.get("account_set") or "").strip()
    if account_set not in allowed:
        raise ValueError(f"account_set {account_set!r} not in whitelist {allowed}")
    values = {key: _text(payload.get(key)) for key in ("ref_no", "customer", "salesman", "department", "user")}
    for key in ("ref_no", "customer"):
        if not values[key]:
            raise ValueError(f"missing {key}")
    for key in ("ref_no", "customer", "salesman", "department"):
        if _bytes(values[key]) > _WIDTH[key]:
            raise ValueError(f"{key} exceeds {_WIDTH[key]} bytes: {values[key]!r}")
    doc_date = _date(payload)
    rows = payload.get("invoices")
    if not isinstance(rows, list) or not rows or len(rows) > MAX_INVOICES:
        raise ValueError("invoices must be a non-empty list")
    invoices: List[Tuple[str, Decimal]] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"invoice {index} is not an object")
        docnum = _text(row.get("docnum"))
        if not docnum or _bytes(docnum) > _WIDTH["docnum"]:
            raise ValueError(f"invoice {index} docnum invalid: {docnum!r}")
        if any(docnum == seen for seen, _amount in invoices):
            raise ValueError(f"invoice {docnum} listed twice")
        invoices.append((docnum, _money(row.get("amount"), f"invoice {docnum} amount", positive=True)))
    cash = _money(payload.get("cash") if payload.get("cash") not in (None, "") else "0.00", "cash", positive=False)
    given = payload.get("methods") or []
    if not isinstance(given, list) or len(given) > MAX_METHODS:
        raise ValueError("methods must be a list")
    methods: List[Method] = []
    for index, row in enumerate(given, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"method {index} is not an object")
        prefix = _text(row.get("prefix"))
        if not prefix or _bytes(prefix) > _WIDTH["prefix"]:
            raise ValueError(f"method {index} prefix invalid: {prefix!r}")
        if any(prefix == seen.prefix for seen in methods):
            raise ValueError(f"method {prefix} listed twice")  # 同一前缀两行会撞方式行编号（前缀 + 单号）
        # 方式行可为负（原生 RE2402899：客户多转 0.55，「25 รายได้อื่นๆ」-0.55，凭证 SEQIT 2 贷 0.55），不可为 0
        methods.append(Method(prefix, _money(row.get("amount"), f"method {prefix} amount", positive=True, signed=True),
                              _text(row.get("code"))))
    total = sum((amount for _docnum, amount in invoices), Decimal(0))
    paid = cash + sum((method.amount for method in methods), Decimal(0))
    if total != paid:
        raise ValueError(f"invoices {total} != cash + methods {paid}")
    if paid <= 0:
        raise ValueError("nothing received")
    return Receipt(values["ref_no"], doc_date, values["customer"], values["salesman"], values["department"], invoices,
                   cash, methods, values["user"], total)


# ── 写前只读 ─────────────────────────────────────────────────────────────────────────────────────


def _str(rec, name: str) -> str:
    try:
        return str(rec[name] or "").strip()
    except (KeyError, dbf.FieldMissingError):
        return ""


def _fields(table) -> set:
    return {name.upper() for name in table.field_names}


def _filtered(table, row: Dict[str, Any]) -> Dict[str, Any]:
    fields = _fields(table)
    return {key: value for key, value in row.items() if key.upper() in fields}


def run_row(account_dir) -> Optional[Dict[str, str]]:
    """ISRUN 里 RE 单据簿那一行（未删）→ ``{prefix, next, department, jnltyp, jnlexp}``；没有 None。"""
    table = _open(Path(account_dir), "ISRUN", dbf.READ_ONLY)
    try:
        for r in S.live_rows(table):
            if _str(r, "DOCTYP") == RUN_DOCTYP:
                try:
                    jnlexp = str(r["JNLEXP"] or "").rstrip()
                except (KeyError, dbf.FieldMissingError):
                    jnlexp = ""
                return {"prefix": _str(r, "PREFIX"), "next": _str(r, "DOCNUM"), "department": _str(r, "DEPCOD"),
                        "jnltyp": _str(r, "JNLTYP"), "jnlexp": jnlexp}
    finally:
        table.close()
    return None


def existing(account_dir, receipt: Receipt) -> Optional[str]:
    """同客户、同 YOUREF、没作废的 RE 已写过 → 它的单号（幂等：换电脑重写、回执丢失后重发、重领任务都不写第二张）。"""
    table = _open(Path(account_dir), "ARTRN", dbf.READ_ONLY)
    try:
        for r in S.live_rows(table):
            if (_str(r, "RECTYP") == RECTYP and _str(r, "CUSCOD") == receipt.customer and _str(r, "YOUREF") == receipt.ref_no
                    and _str(r, "DOCSTAT") != CANCELLED):
                return _str(r, "DOCNUM")
    finally:
        table.close()
    return None


def invoice_state(account_dir, customer: str, docnums) -> Dict[str, dict]:
    """这几张发票此刻在账套里的样子：docnum → ``{found, customer, cancelled, rows, remaining, received, orgnum, slmcod,
    depcod}``（只看未删、RECTYP 3 的行）。"""
    wanted = set(docnums)
    out: Dict[str, dict] = {docnum: {"found": False, "rows": 0} for docnum in wanted}
    table = _open(Path(account_dir), "ARTRN", dbf.READ_ONLY)
    try:
        has_orgnum = "ORGNUM" in _fields(table)
        for r in S.live_rows(table):
            docnum = _str(r, "DOCNUM")
            if docnum not in wanted or _str(r, "RECTYP") != INVOICE_RECTYP:
                continue
            state = out[docnum]
            state["rows"] += 1
            mine = _str(r, "CUSCOD") == customer and _str(r, "DOCSTAT") != CANCELLED
            if state["found"] and not mine:
                continue
            state.update(found=True, customer=_str(r, "CUSCOD"), cancelled=_str(r, "DOCSTAT") == CANCELLED,
                         cash_basis=_str(r, "SRV_VATTYP") == CASH_BASIS_VAT,
                         remaining=_q2(_dec(r["REMAMT"])), received=_q2(_dec(r["RCVAMT"])),
                         orgnum=int(_dec(r["ORGNUM"])) if has_orgnum else 0,
                         slmcod=_str(r, "SLMCOD"), depcod=_str(r, "DEPCOD"))
            if mine:
                state["mine"] = state.get("mine", 0) + 1
    finally:
        table.close()
    return out


def check_invoices(account_dir, receipt: Receipt) -> Dict[str, dict]:
    """逐张核：在、是这个客户的、没作废、只有一张、当前余额够本次收。任一张不过抛 :class:`Refused`（整单不写），证据带每张的当前余额。"""
    states = invoice_state(account_dir, receipt.customer, [docnum for docnum, _amount in receipt.invoices])
    report, worst = [], ""
    for docnum, amount in receipt.invoices:
        state = states[docnum]
        problem = ""
        if not state["found"]:
            problem = "not_found"
        elif state["customer"] != receipt.customer:
            problem = "other_customer"
        elif state["cancelled"]:
            problem = "cancelled"
        elif state.get("mine", 0) > 1:
            problem = "duplicate"
        elif state.get("cash_basis"):
            problem = "cash_basis_vat"
        elif state["remaining"] < amount:
            problem = "short"
        report.append({"docnum": docnum, "amount": f"{amount:.2f}",
                       "remaining": f"{state['remaining']:.2f}" if state["found"] else "", "problem": problem})
        if problem and (worst != ERR_INVOICE_NOT_FOUND):
            worst = ERR_ALREADY_COLLECTED if problem == "short" else ERR_INVOICE_NOT_FOUND
    if worst:
        bad = ", ".join(f"{row['docnum']} {row['problem']} (remaining {row['remaining'] or '-'})"
                        for row in report if row["problem"])
        raise Refused(worst, f"receipt {receipt.ref_no} not written: {bad}",
                      {"kind": "receipt_invoices", "invoices": report})
    return states


def plan_methods(account_dir, receipt: Receipt) -> List[Tuple[Method, str]]:
    """每种方式 → 科目（按前缀认，``payment_methods``）；不在 / 没科目 / 冲突 / 科目不能记账抛 :class:`Refused`。"""
    rows = payment_methods.types(account_dir, payment_methods.RECEIPT_TYPES)
    out = []
    for method in receipt.methods:
        account, issue = payment_methods.account_of(rows, method.prefix)
        if not issue and account_rules.chart_account(account_dir, account) is not None \
                and not account_rules.postable_account(account_dir, account):
            issue = "not_postable"
        if issue:
            raise Refused(ERR_RECEIPT_TYPE, f"receipt type {method.prefix!r}: {issue}",
                          {"kind": "receipt_type", "prefix": method.prefix, "code": method.code, "problem": issue,
                           "account": account})
        out.append((method, account))
    return out


def plan_bank(account_dir, methods: List[Tuple[Method, str]]) -> List[Tuple[Method, str]]:
    """要记银行流水的方式 → [(方式, 户头 BNKACC)]：这种方式在 ISRUN 里经 ``SHORTNAM`` 指到 BKMAS 里有的户头（同前缀几行取第一行指到户头的）。
    有要记的而账套没有 BKTRN 表 → :class:`Refused`（整单不写，不留缺流水的单）。"""
    if not methods:
        return []
    accounts = payment_methods.banks(account_dir)
    rows = payment_methods.types(account_dir, payment_methods.RECEIPT_TYPES)
    out = []
    for method, _account in methods:
        bank = next((row["bank"] for row in rows if row["prefix"] == method.prefix and row["bank"] in accounts), "")
        if bank:
            out.append((method, bank))
    if out and not any(path.stem.upper() == BANK_TABLE and path.suffix.upper() == ".DBF"
                       for path in Path(account_dir).iterdir()):
        raise Refused(ERR_BOOK, f"receipt types {[m.prefix for m, _b in out]} go to a bank account but the account set has no "
                                f"{BANK_TABLE}")
    return out


def cash_account(account_dir, receipt: Receipt) -> str:
    if receipt.cash <= 0:
        return ""
    account = account_rules.default_account(account_dir, CASH_SLOT)
    if not account or (account_rules.chart_account(account_dir, account) is not None
                       and not account_rules.postable_account(account_dir, account)):
        raise Refused(ERR_CASH_ACCOUNT, f"cash {receipt.cash} received but the account set has no usable cash account "
                                        f"(ISINFO.{CASH_SLOT} = {account!r})")
    return account


def receivable_account(account_dir, customer: str) -> str:
    account = account_rules.control_account(account_dir, "ARMAS", customer,
                                            account_rules.default_account(account_dir, RECEIVABLE_SLOT))
    if not account:
        raise Refused(ERR_NOT_POSTABLE, f"customer {customer!r} has no receivable account and ISINFO.{RECEIVABLE_SLOT} is empty")
    return account


def _missing_tables(account_dir) -> List[str]:
    present = {path.stem.upper() for path in Path(account_dir).iterdir() if path.suffix.upper() == ".DBF"}
    return [name for name in REQUIRED_TABLES if name not in present]


def _next_docnum(account_dir, run: Dict[str, str]) -> Tuple[str, str]:
    """(本张单号, 写完后 ISRUN.DOCNUM 该记的下一个号)。单号 = 前缀 + ISRUN 的下一个号；账套里已有（含软删行，号用过就是用过了）就往后跳。"""
    prefix, current = run["prefix"], run["next"]
    if not prefix or not current.isdigit():
        raise Refused(ERR_BOOK, f"ISRUN RE row has no usable running number (prefix {prefix!r}, next {current!r})")
    width = len(current)
    table = _open(Path(account_dir), "ARTRN", dbf.READ_ONLY)
    try:
        used = {_str(r, "DOCNUM") for r in table}
    finally:
        table.close()
    number = int(current)
    for _ in range(10000):
        docnum = f"{prefix}{str(number).zfill(width)}"
        if _bytes(docnum) > _WIDTH["docnum"]:
            break
        if docnum not in used:
            return docnum, str(number + 1).zfill(width)
        number += 1
    raise Refused(ERR_BOOK, f"no free RE number after {prefix}{current}")


# ── 写 ──────────────────────────────────────────────────────────────────────────────────────────


@dataclass
class _Plan:
    receipt: Receipt
    run: Dict[str, str]
    methods: List[Tuple[Method, str]]
    cash_acc: str
    ar_acc: str
    states: Dict[str, dict]
    salesman: str
    department: str
    userid: str
    bank: List[Tuple[Method, str]] = field(default_factory=list)   # 要记银行流水的方式与户头（plan_bank）


def write_receipt(payload: Dict[str, Any], account_dir, *, userid: str = S.PEARNLY_USERID, depcod: str = "",
                  make_backup: bool = True, allowed_sets: tuple = ALLOWED_ACCOUNT_SETS, selected_dir: str = "",
                  expected_company: str = "", index_batch=None) -> DbfWriteResult:
    """把一张收款单直写进账套 DBF。失败整体回滚，绝不留半张；写前核不过整单不写。调用方（worker）已持 ``PEARNLY.LCK``。"""
    clock = StepClock("receipt")
    try:
        receipt = parse(payload, allowed=allowed_sets)
    except ValueError as exc:
        return DbfWriteResult(False, error_code=ERR_VALIDATION, error=f"PAYLOAD_INVALID: {exc}")
    account_dir = Path(account_dir)
    ambiguous = S.path_ambiguity(account_dir) or S.path_ambiguity(selected_dir)
    if ambiguous:
        return DbfWriteResult(False, error_code=ERR_ACCOUNT_DIR,
                              error=f"账套路径不完整,系统会把落点改写成 {ambiguous!r}(原值 {str(account_dir)!r})")
    if not S.account_dir_allowed(account_dir, selected_dir):
        return DbfWriteResult(False, error_code=ERR_ACCOUNT_DIR,
                              error=f"write target {account_dir} != selected account dir {selected_dir!r}")
    co_ok, co_reason = account_company_matches(account_dir, expected_company)
    if not co_ok:
        return DbfWriteResult(False, error_code=ERR_COMPANY, error=co_reason)
    missing = _missing_tables(account_dir)
    if missing:
        return DbfWriteResult(False, error_code=ERR_BOOK, error=f"account set has no {', '.join(missing)}")
    if payload.get("respect_period_lock") and fiscal_period.locked(account_dir, receipt.doc_date):
        return DbfWriteResult(False, error_code=fiscal_period.ERR_PERIOD_LOCKED,
                              error=f"accounting period of {receipt.doc_date} is locked in Express")
    # 写过了：回原号，一行不写。放在核发票之前：已写的那张已经把余额收掉，再核会误报「已被收清」
    done = existing(account_dir, receipt)
    if done:
        log.info("receipt %s already written as %s, not written again", receipt.ref_no, done)
        states = invoice_state(account_dir, receipt.customer, [docnum for docnum, _amount in receipt.invoices])
        return DbfWriteResult(True, docnum=done, skipped=True, party_code=receipt.customer,
                              receipt=_receipt_result(done, receipt, states, after=False))
    try:
        if not master_create._code_exists(account_dir, "ARMAS", "CUSCOD", receipt.customer):
            raise Refused(ERR_CUSTOMER, f"customer {receipt.customer!r} is not in Express")
        run = run_row(account_dir)
        if run is None:
            raise Refused(ERR_BOOK, "the account set has no receipt (RE) book in ISRUN")
        methods = plan_methods(account_dir, receipt)
        bank = plan_bank(account_dir, methods)
        cash_acc = cash_account(account_dir, receipt)
        ar_acc = receivable_account(account_dir, receipt.customer)
        states = check_invoices(account_dir, receipt)
    except Refused as exc:
        return DbfWriteResult(False, error_code=exc.code, error=str(exc), evidence=exc.evidence)
    first = states[receipt.invoices[0][0]]
    plan = _Plan(receipt, run, methods, cash_acc, ar_acc, states,
                 salesman=receipt.salesman or first.get("slmcod", ""),
                 department=receipt.department or run.get("department", "") or depcod,
                 userid=_userid(receipt.user, userid), bank=bank)
    clock.lap("checks")
    backup_dir = _backup(account_dir, BACKUP_TABLES) if make_backup else None
    clock.lap("backup")
    docnum = ""
    try:
        banked = (BANK_TABLE,) if plan.bank else ()
        since = _table_sizes(account_dir, _VERIFY_TABLES + banked)
        docnum, gl_rows = _write_all(account_dir, plan)
        _write_bank(account_dir, plan, docnum)
        notes = _update_arbal(account_dir, receipt.customer, receipt.doc_date, receipt.total)
        master_create.move_party_balance(account_dir, "ARMAS", receipt.customer, -receipt.total)
        clock.lap("write")
        _reindex_or_defer(account_dir, WRITE_TABLES + banked, 0, "", index_batch)
        clock.lap("reindex")
        _verify(account_dir, docnum, plan, gl_rows, since)
        clock.lap("verify")
        _verify_index(account_dir, cdx_reindex.TAG_AR_DOCNUM, docnum)
        clock.lap("index_check")
        _discard_backup(backup_dir)
        clock.log(docnum, "ok")
        return DbfWriteResult(True, docnum=docnum, party_code=receipt.customer, notes=notes or None,
                              receipt=_receipt_result(docnum, receipt, states, after=True))
    except Exception as exc:  # 任何失败 → 回滚还原
        clock.lap("failed_step")
        if backup_dir:
            _restore(account_dir, backup_dir)
            _discard_backup(backup_dir)
        clock.log(docnum, "rolled_back")
        log.exception("receipt write failed, rolled back")
        return DbfWriteResult(False, error_code=_error_code(exc), error=str(exc),
                              backup_dir=str(backup_dir) if backup_dir else None,
                              evidence=exc.evidence if isinstance(exc, _WriteError) else None)


def _userid(user: str, fallback: str) -> str:
    """单头录入人：载荷 ``user``（录这笔收款的人）按 cp874 截 8 字节；没给用这台电脑的录入人（``writer_id``）。"""
    raw = str(user or "").strip().encode("cp874", "replace")[:_WIDTH["user"]]
    text = raw.decode("cp874", "replace").strip()
    return text or fallback


def _receipt_result(docnum: str, receipt: Receipt, states: Dict[str, dict], *, after: bool) -> dict:
    """回执 ``meta.receipt``：``{docnum, invoices: [{docnum, remaining_after}]}``（``after``：这次刚收过，余额扣掉本次）。"""
    rows = []
    for invoice, amount in receipt.invoices:
        remaining = states.get(invoice, {}).get("remaining", Decimal(0))
        rows.append({"docnum": invoice, "remaining_after": f"{_q2(remaining - amount) if after else remaining:.2f}"})
    return {"docnum": docnum, "invoices": rows}


def _write_all(account_dir: Path, plan: _Plan) -> Tuple[str, List[Tuple[str, str, str, Decimal]]]:
    receipt, run = plan.receipt, plan.run
    docdat = receipt.doc_date
    docnum, following = _next_docnum(account_dir, run)
    received = sum((method.amount for method, _acc in plan.methods), Decimal(0))
    first = plan.states[receipt.invoices[0][0]]

    head = _open(account_dir, "ARTRN", dbf.READ_WRITE)
    try:
        S.append_native(head, _filtered(head, {
            "RECTYP": RECTYP, "DOCNUM": docnum, "DOCDAT": docdat, "DEPCOD": plan.department, "SLMCOD": plan.salesman,
            "CUSCOD": receipt.customer, "YOUREF": receipt.ref_no, "DUEDAT": docdat, "BILNUM": BILNUM,
            "NETAMT": float(receipt.total), "IVCAMT": float(receipt.total), "CSHRCV": float(receipt.cash),
            "CHQRCV": float(received), "CHQPAS": float(received), "INTRCV": 0.0, "RCVAMT": 0.0, "REMAMT": 0.0,
            "CMPLAPP": "Y", "CMPLDAT": docdat, "DOCSTAT": DOCSTAT, "SRV_VATTYP": SRV_VATTYP,
            "ORGNUM": int(first.get("orgnum") or 0), "USERID": plan.userid, "CHGDAT": _dt.date.today(),
        }))
        # 被收的发票：余额减本次；收清的标完成（原生 CMPLAPP Y + CMPLDAT 收款日），没收清的不动这两栏
        amounts = dict(receipt.invoices)
        for rec in S.live_rows(head):
            docno = _str(rec, "DOCNUM")
            if docno not in amounts or _str(rec, "RECTYP") != INVOICE_RECTYP or _str(rec, "CUSCOD") != receipt.customer \
                    or _str(rec, "DOCSTAT") == CANCELLED:
                continue
            amount = amounts.pop(docno)
            with rec as row:
                remaining = _q2(_dec(row["REMAMT"]) - amount)
                row["RCVAMT"] = float(_q2(_dec(row["RCVAMT"]) + amount))
                row["REMAMT"] = float(remaining)
                if remaining == 0:
                    row["CMPLAPP"] = "Y"
                    row["CMPLDAT"] = docdat
        if amounts:
            raise _WriteError(f"invoices vanished while writing: {sorted(amounts)}", ERR_VERIFY,
                              {"kind": "receipt_invoices", "missing": sorted(amounts)})
    finally:
        head.close()

    items = _open(account_dir, "ARRCPIT", dbf.READ_WRITE)
    try:
        for invoice, amount in receipt.invoices:
            items.append(_filtered(items, {"RCPNUM": docnum, "DOCNUM": invoice, "RECTYP": INVOICE_RECTYP,
                                           "RCVAMT": float(amount), "VATAMT": 0.0}))
    finally:
        items.close()
    lines = _open(account_dir, "ARRCPCQ", dbf.READ_WRITE)
    try:
        for method, _acc in plan.methods:
            lines.append(_filtered(lines, {"RCPNUM": docnum, "CHQNUM": payment_methods.line_number(method.prefix, docnum),
                                           "RCVAMT": float(method.amount)}))
    finally:
        lines.close()

    gl_rows = journal_rows(plan)
    descrp = description(account_dir, run.get("jnlexp", ""), receipt.customer)
    journal = _open(account_dir, "GLJNL", dbf.READ_WRITE)
    try:
        journal.append(S.journal_stamp(journal, _filtered(journal, {
            "JNLTYP": run.get("jnltyp") or JNLTYP, "VOUDAT": docdat, "VOUCHER": docnum, "DESCRP": descrp, "TRNSTAT": "P",
            "DOCSTAT": "N", "CREBY": S.PEARNLY_USERID}), userid=plan.userid))  # 稽核栏照原生（``dbf_schema.journal_stamp``）
    finally:
        journal.close()
    departmental = _department_accounts(account_dir) if plan.department else frozenset()
    entries = _open(account_dir, "GLJNLIT", dbf.READ_WRITE)
    written = [_gl_line(docnum, seqit, docdat, acc, descrp, side, amount, plan.department, departmental)
               for seqit, acc, side, amount in gl_rows]
    try:
        for line in written:
            S.append_native(entries, S.journal_stamp(entries, _filtered(entries, line)))
    finally:
        entries.close()
    glbal_mark.mark(account_dir, written)  # 科目余额对应格标待重算（照 Express 原生，11-12）
    _bump_run(account_dir, following)
    return docnum, gl_rows


def journal_rows(plan: _Plan) -> List[Tuple[str, str, str, Decimal]]:
    """凭证分录 [(SEQIT, 科目, 借贷, 金额)]：SEQIT 2 现金与各方式按（科目, 借贷）合并、按科目排（原生多方式的 RE 分录行按科目升序，
    与方式行的先后无关：RE2402979 TX 行在前、分录 1112-01 在前；RE2402889 DC / AP / TF 三行分录 4100-04、5220-02、5390-01），
    负的方式行（多收的零头记其他收入等，原生 RE2402899）记贷方；最后 SEQIT 6 贷应收合计。"""
    merged: Dict[Tuple[str, str], Decimal] = {}
    if plan.receipt.cash > 0:
        merged[(plan.cash_acc, S.TRNTYP_DEBIT)] = plan.receipt.cash
    for method, acc in plan.methods:
        side = S.TRNTYP_DEBIT if method.amount > 0 else S.TRNTYP_CREDIT
        merged[(acc, side)] = merged.get((acc, side), Decimal(0)) + abs(method.amount)
    rows = [(SEQIT_RECEIVED, acc, side, amount) for (acc, side), amount in sorted(merged.items())]
    return rows + [(SEQIT_RECEIVABLE, plan.ar_acc, S.TRNTYP_CREDIT, plan.receipt.total)]


def description(account_dir, jnlexp: str, customer: str) -> str:
    """凭证摘要：单据簿 RE 行的 ``JNLEXP`` 按这位客户算出、截到 50 字节（GLJNL / GLJNLIT.DESCRP C(50)）。"""
    from .pos_journal import fit

    armas = payment_methods.lookup_padded(account_dir, "ARMAS", "CUSCOD", customer,
                                          ("CUSCOD", "PRENAM", "CUSNAM", "CUSNAM2")) or {}
    text = payment_methods.journal_text(jnlexp, {"ARMAS": armas})
    return fit(text, 50).rstrip(" ")


def _bump_run(account_dir: Path, following: str) -> None:
    table = _open(account_dir, "ISRUN", dbf.READ_WRITE)
    try:
        for r in S.live_rows(table):
            if _str(r, "DOCTYP") == RUN_DOCTYP:
                with r as rec:
                    rec["DOCNUM"] = following
                return
    finally:
        table.close()


def bank_name(account_dir, customer: str) -> str:
    """银行流水的户名：客户档 PRENAM 与 CUSNAM 去掉两头空格、有前缀名时中间用不换行空格（\xa0）连，截到 50 字节
    （原生 NLTTEST：没有前缀名的 2208 行 = CUSNAM，有前缀名的 13 行 = PRENAM + \xa0 + CUSNAM；其余 44 行是客户后来改了名）。"""
    from .pos_journal import fit

    armas = payment_methods.lookup_padded(account_dir, "ARMAS", "CUSCOD", customer, ("PRENAM", "CUSNAM")) or {}
    text = "\xa0".join(part for part in (armas.get("PRENAM", "").strip(), armas.get("CUSNAM", "").strip()) if part)
    return fit(text, BANK_NAME_WIDTH).rstrip(" ")


def bank_row(*, docnum: str, docdat, prefix: str, bank: str, amount: Decimal, customer: str, name: str, department: str,
             userid: str, chgdat) -> Dict[str, Any]:
    """一行银行流水（BKTRN），逐字段照原生（NLTTEST 2265 行 bR）：类型 bR、户头 = 方式的 SHORTNAM、CHQNUM = 方式行编号（同 ARRCPCQ）、
    TRNDAT = CHQDAT = GETDAT = 收款日、客户与户名、部门同单头、AMOUNT = NETAMT = 这种方式的金额、CHQSTAT 19、JNLTRNTYP 0、REFDOC ``~``、
    VOUCHER = 收款单号、USERID / CHGDAT 同单头；其余栏（银行、分行、手续费、税、存入日、备注……）原生都空，不写。"""
    return {"BKTRNTYP": BANK_TRNTYP, "TRNDAT": docdat, "CHQNUM": payment_methods.line_number(prefix, docnum), "CHQDAT": docdat,
            "CUSCOD": customer, "NAME": name, "DEPCOD": department, "GETDAT": docdat, "AMOUNT": float(amount),
            "NETAMT": float(amount), "CHQSTAT": BANK_CHQSTAT, "BNKACC": bank, "JNLTRNTYP": BANK_JNLTRNTYP,
            "REFDOC": BANK_REFDOC, "VOUCHER": docnum, "USERID": userid, "CHGDAT": chgdat}


def _write_bank(account_dir: Path, plan: _Plan, docnum: str) -> None:
    if not plan.bank:
        return
    receipt = plan.receipt
    name = bank_name(account_dir, receipt.customer)
    table = _open(account_dir, BANK_TABLE, dbf.READ_WRITE)
    try:
        for method, bank in plan.bank:
            S.append_native(table, _filtered(table, bank_row(
                docnum=docnum, docdat=receipt.doc_date, prefix=method.prefix, bank=bank, amount=method.amount,
                customer=receipt.customer, name=name, department=plan.department, userid=plan.userid,
                chgdat=_dt.date.today())))
    finally:
        table.close()


def _update_arbal(account_dir: Path, customer: str, docdat, total: Decimal) -> dict:
    """客户应收 ``RE{期}`` 加本次 IVCAMT（判不出期就不写、把原因报回，同销项）。"""
    from .dbf_sales import _update_arbal as update

    return update(account_dir, customer, docdat, BALANCE_BUCKET, total)


def _table_sizes(account_dir: Path, names=_VERIFY_TABLES) -> Dict[str, int]:
    sizes = {}
    for name in names:
        table = _open(account_dir, name, dbf.READ_ONLY)
        try:
            sizes[name] = len(table)
        finally:
            table.close()
    return sizes


def _verify(account_dir: Path, docnum: str, plan: _Plan, gl_rows, since: Dict[str, int]) -> None:
    """读回：单头一行（RECTYP 9、金额栏）、发票行与方式行与要写的一致、凭证与要写的一致且借贷平、发票余额扣对了。不符抛写盘错误（回滚）。"""
    receipt = plan.receipt

    def fail(what, table):
        raise _WriteError(f"receipt {docnum}: {what}", ERR_VERIFY,
                          {"kind": "readback_verify", "table": table, "docnum": docnum})

    def appended(name, key):
        table = _open(account_dir, name, dbf.READ_ONLY)
        try:
            return [{f: r[f] for f in table.field_names} for r in S.live_rows(table)
                    if dbf.recno(r) >= since.get(name, 0) and str(r[key] or "").strip() == docnum]
        finally:
            table.close()

    heads = appended("ARTRN", "DOCNUM")
    received = sum((method.amount for method, _acc in plan.methods), Decimal(0))
    if (len(heads) != 1 or str(heads[0]["RECTYP"]).strip() != RECTYP or _q2(heads[0]["IVCAMT"]) != receipt.total
            or _q2(heads[0]["CSHRCV"]) != receipt.cash or _q2(heads[0]["CHQRCV"]) != received
            or str(heads[0]["CUSCOD"]).strip() != receipt.customer):
        fail(f"header rows {heads!r}", "ARTRN")
    got = sorted((str(r["DOCNUM"]).strip(), _q2(r["RCVAMT"])) for r in appended("ARRCPIT", "RCPNUM"))
    if got != sorted((invoice, amount) for invoice, amount in receipt.invoices):
        fail(f"invoice lines {got}", "ARRCPIT")
    got = sorted((str(r["CHQNUM"]).strip(), _q2(r["RCVAMT"])) for r in appended("ARRCPCQ", "RCPNUM"))
    if got != sorted((payment_methods.line_number(method.prefix, docnum), method.amount) for method, _acc in plan.methods):
        fail(f"method lines {got}", "ARRCPCQ")
    got = sorted((str(r["SEQIT"]).strip(), str(r["ACCNUM"]).strip(), str(r["TRNTYP"]).strip(), _q2(r["AMOUNT"]))
                 for r in appended("GLJNLIT", "VOUCHER"))
    if got != sorted(gl_rows):
        fail(f"journal lines {got} differ from {sorted(gl_rows)}", "GLJNLIT")
    debit = sum((amount for _s, _a, side, amount in got if side == S.TRNTYP_DEBIT), Decimal(0))
    credit = sum((amount for _s, _a, side, amount in got if side != S.TRNTYP_DEBIT), Decimal(0))
    receivable = sum((amount for seqit, _a, side, amount in got if seqit == SEQIT_RECEIVABLE and side != S.TRNTYP_DEBIT),
                     Decimal(0))
    if debit != credit or receivable != receipt.total:
        fail(f"journal not balanced {debit} / {credit} (receivable {receivable})", "GLJNLIT")
    if plan.bank:
        got = sorted((str(r["BKTRNTYP"]).strip(), str(r["BNKACC"]).strip(), str(r["CHQNUM"]).strip(), _q2(r["AMOUNT"]))
                     for r in appended(BANK_TABLE, "VOUCHER"))
        if got != sorted((BANK_TRNTYP, bank, payment_methods.line_number(method.prefix, docnum), method.amount)
                         for method, bank in plan.bank):
            fail(f"bank lines {got}", BANK_TABLE)
    now = invoice_state(account_dir, receipt.customer, [invoice for invoice, _amount in receipt.invoices])
    for invoice, amount in receipt.invoices:
        if now[invoice].get("remaining") != _q2(plan.states[invoice]["remaining"] - amount):
            fail(f"invoice {invoice} remaining {now[invoice].get('remaining')}", "ARTRN")


__all__ = ["DIRECTION", "ERR_ALREADY_COLLECTED", "ERR_CASH_ACCOUNT", "ERR_INVOICE_NOT_FOUND", "ERR_RECEIPT_TYPE",
           "REFUSALS", "Receipt", "Refused", "bank_name", "bank_row", "check_invoices", "existing", "parse", "plan_bank", "run_row", "write_receipt"]
