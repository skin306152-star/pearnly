"""P1 销项载荷 → Express 销售录入字段(确定性映射 · 镜像 purchase_adapter)。

接 P1 销项载荷(direction='sales'):doctype IV/HS · account_set · docdate_be(佛历
YYMMDD)· vat_period_be · ref_no(销售参考/客户 PO,作幂等锚)· customer{code,name,
tax_id,prename,customer_new} · vat_rate · base/vat/total · lines:借应收/现金=贷收入
+贷销项税(与采购相反)。

映射到 Express 销售页(2.ขาย → 4.ขายเงินเชื่อ)。复用 purchase_adapter 的通用助手
(佛历转换、金额解析、借贷平衡),只换 supplier→customer、AP→AR、单别 RR/HP→IV/HS。
"""
from __future__ import annotations

from dataclasses import dataclass, field as df
import re
from decimal import Decimal
from typing import Any, Dict, List

from . import dbf_schema as S
from .purchase_adapter import (
    ALLOWED_ACCOUNT_SETS,
    PayloadValidationResult,
    _BALANCE_TOL,
    _d,
    _lines_balance,
    _q,
    _valid_be_yymmdd,
    _vat_rate_str,
    be_date_to_express,
    be_period_to_express,
)

PROVIDE_DOCTYPES = S.SALES_DOCTYPES  # ("IV", "HS")
REFUND_DOCTYPE = "SR"  # 收银退货单(载荷 10):只在带 ``refund`` 块时认

ERR_ACCOUNT_SET_NOT_ALLOWED = "ACCOUNT_SET_NOT_ALLOWED"
ERR_BAD_DOCTYPE = "BAD_SALES_DOCTYPE"
ERR_INVALID_DATE = "INVALID_DOC_DATE"
ERR_MISSING_REF_NO = "MISSING_REF_NO"
ERR_MISSING_CUSTOMER = "MISSING_CUSTOMER"
ERR_AMOUNTS = "AMOUNTS_NOT_CONSISTENT"
ERR_ENTRY_NOT_BALANCED = "ENTRY_NOT_BALANCED"
ERR_ITEMS_INVALID = "ITEMS_INVALID"
ERR_POS_INVALID = "POS_INVALID"
ERR_REFUND_INVALID = "REFUND_INVALID"

# POS 日汇总票(载荷 3 的 `pos` 块):票号起止各最长这么多字(云端 contracts/pos.NUMBER_MAX 按同一上限截)、
# 最多这么多张付款方式转账凭证。
_POS_NUMBER_MAX = 40
_POS_TRANSFERS_MAX = 20
_POS_LABEL_MAX = 50
# 收银退货单(载荷 10 的 ``refund`` 块):原单号是 CC 写的现销 HS(ARTRN.SONUM 与 DOCNUM 同宽 C(12));退现金、货回仓。
_REFUND_AGAINST_MAX = 12
_REFUND_CNTYP = 3
_REFUND_RETSTK = "Y"
_REFUND_ROLES = ("refund", "output_vat", "cash")


@dataclass
class SalesEntry:
    doctype: str  # IV(赊销)| HS(现销)
    account_set: str
    customer_code: str
    customer_new: bool
    customer_name: str
    customer_tax_id: str
    customer_prename: str
    ref_no: str
    doc_date: str  # DD/MM/YY(佛历)
    vat_period: str  # MM/YY
    vat_rate: str
    base_amount: str
    vat_amount: str
    total_amount: str
    lines: List[Dict[str, Any]] = df(default_factory=list)
    # V1 安全明细:仅 items_status=='ok' 时落 STCRD;否则退回表头模式(诚实)。
    items: List[Dict[str, Any]] = df(default_factory=list)
    items_status: str = "empty"
    items_account: str = ""
    # 自建客户落档地址(与真账录入一致;空=不填,现金客户恒空)。
    customer_address: str = ""
    # 分店号(5 位,如 00018;空=未提供)。连锁客户同税号多档时的匹配判据 + 新建档落 ORGNUM。
    customer_branch: str = ""
    # 存货科目组(ISACC ACCCOD)· 账套一件库存品都没有时,建 STKTYP=0 全靠它定存货/COGS 科目。
    # 云端端点配了才带;空=没配,建档路会抛 StockAccGroupRequired 让会计选一次。
    stock_acccod: str = ""
    # 会计在复核时选了「新建客户档」(载荷 customer.confirmed_new):不再按税号 / 名称找旧档、
    # 不按「疑似重复」拦,照票面新建(同税号几个档里没有这一家时由人拍板 · 2026-09-28 压测)。
    customer_confirmed_new: bool = False
    # POS 导出的日汇总票(载荷 3):现销 HS 单号自编、票号起止写进销项税摘要;每种非现金付款方式另记一张
    # 日记账凭证「借 收款科目 / 贷 现金」,与本单同一次写入(见 pos_journal)。
    is_pos: bool = False
    pos_first: str = ""
    pos_last: str = ""
    pos_count: int = 0
    pos_transfers: List[Dict[str, Any]] = df(default_factory=list)
    # 载荷 9:销项税记录(ISVAT)要照写的金额栏(来源按规则算好,如混有免税商品时 AMT01 只记应税部分);空 = AMT01 记整张税前。
    vat_fields: Dict[str, str] = df(default_factory=dict)
    # 载荷 10:收银退货单 SR(``refund`` 块)· 挂原 HS 单号(SONUM)与原单含税合计(IVCAMT)。见 dbf_refund。
    is_refund: bool = False
    refund_against: str = ""
    refund_against_total: str = ""


def validate_sales_payload(
    payload: Dict[str, Any], allowed: tuple = ALLOWED_ACCOUNT_SETS
) -> PayloadValidationResult:
    """录入 Express 前端侧守门(镜像采购):账套名匹配 + 必填 + 金额/分录自洽。

    `allowed` = 端侧账套名期望值(客户所选账套 · 缺所选 → 空 → 必拒)。
    """
    account_set = str(payload.get("account_set") or "").strip()
    if account_set not in allowed:
        return PayloadValidationResult(
            False, ERR_ACCOUNT_SET_NOT_ALLOWED,
            f"account_set {account_set!r} not in whitelist {allowed}",
        )

    doctype = str(payload.get("doctype") or "").strip().upper()
    refund = "refund" in payload
    if doctype not in PROVIDE_DOCTYPES and not (refund and doctype == REFUND_DOCTYPE):
        return PayloadValidationResult(
            False, ERR_BAD_DOCTYPE, f"sales doctype must be one of {PROVIDE_DOCTYPES}"
        )
    if refund and doctype != REFUND_DOCTYPE:
        return PayloadValidationResult(False, ERR_REFUND_INVALID, "a refund must be an SR document")

    if not _valid_be_yymmdd(str(payload.get("docdate_be") or "").strip()):
        return PayloadValidationResult(
            False, ERR_INVALID_DATE, "docdate_be must be Buddhist YYMMDD"
        )

    if not str(payload.get("ref_no") or "").strip():
        return PayloadValidationResult(
            False, ERR_MISSING_REF_NO, "missing sales ref_no (idempotency anchor)"
        )

    cust = payload.get("customer") or {}
    if not (str(cust.get("code") or "").strip() or str(cust.get("name") or "").strip()):
        return PayloadValidationResult(
            False, ERR_MISSING_CUSTOMER, "customer needs a code or a name"
        )

    base = _d(payload.get("base_amount"))
    vat = _d(payload.get("vat_amount"))
    total = _d(payload.get("total_amount"))
    if base is None or vat is None or total is None:
        return PayloadValidationResult(False, ERR_AMOUNTS, "non-numeric amount")
    if base <= 0 or vat < 0 or total <= 0:
        return PayloadValidationResult(False, ERR_AMOUNTS, "amount out of range")
    if abs(_q(base + vat) - _q(total)) > _BALANCE_TOL:
        return PayloadValidationResult(
            False, ERR_AMOUNTS, "base + vat != total beyond tolerance"
        )

    if _lines_balance(payload.get("lines") or []) is None:
        return PayloadValidationResult(
            False, ERR_ENTRY_NOT_BALANCED, "GL lines do not balance (ΣD != ΣC)"
        )

    item_err = _validate_items(payload)
    if item_err is not None:
        return item_err

    if "vat_fields" in payload:
        fields_err = _validate_vat_fields(payload.get("vat_fields"), base)
        if fields_err is not None:
            return fields_err

    if "pos" in payload:
        pos_err = _validate_pos(payload, doctype, total)
        if pos_err is not None:
            return pos_err

    if refund:
        refund_err = _validate_refund(payload, base, vat, total)
        if refund_err is not None:
            return refund_err

    return PayloadValidationResult(True)


def _validate_refund(payload: Dict[str, Any], base: Decimal, vat: Decimal, total: Decimal) -> PayloadValidationResult | None:
    """收银退货单 SR 的 ``refund`` 块与它要求的单子样子:挂一张 CC 写的现销 HS(原单号 HS 开头、≤12 字节、原单合计为正)、
    退现金(cntyp 3)、货回仓(retstk Y);同时是 POS 日汇总(``pos`` 块,退给客户的现金不为负);明细全是带存货码的库存行;
    分录按角色恰好是 借 退货科目(税前)[+ 借 销项税] / 贷 现金(含税)。不达标 → 拒,一行不写。"""
    block = payload.get("refund")

    def bad(detail):
        return PayloadValidationResult(False, ERR_REFUND_INVALID, detail)

    if not isinstance(block, dict):
        return bad("refund must be an object")
    against = block.get("against")
    if (not isinstance(against, str) or not against.strip().upper().startswith("HS")
            or len(against.strip().encode("cp874", "replace")) > _REFUND_AGAINST_MAX):
        return bad("refund against must be an HS document number of at most 12 bytes")
    against_total = _d(block.get("against_total"))
    if against_total is None or not against_total.is_finite() or against_total <= 0:
        return bad("refund against_total invalid")
    if block.get("cntyp") != _REFUND_CNTYP or block.get("retstk") != _REFUND_RETSTK:
        return bad("only cash refunds with the goods back in stock are supported")
    if "pos" not in payload:
        return bad("a refund needs its pos block")
    cash = _d((payload.get("pos") or {}).get("cash"))
    if cash is None or cash < 0:
        return bad("refund cash cannot be negative")
    items = payload.get("items") or []
    if str(payload.get("items_status") or "") != "ok" or not items:
        return bad("a refund needs its stock lines")
    for it in items:
        qty, amount = _d((it or {}).get("qty")), _d((it or {}).get("amount"))
        if (not str((it or {}).get("code") or "").strip() or str(it.get("item_mode") or "") != "stock_sale"
                or qty is None or qty <= 0 or amount is None or amount < 0):
            return bad("every refund line must be a stock line with an Express code")
    lines = payload.get("lines") or []
    roles = [str((ln or {}).get("role") or "") for ln in lines]
    want = ["refund", "output_vat", "cash"] if vat > 0 else ["refund", "cash"]
    if roles != want:
        return bad(f"refund journal roles {roles} != {want}")
    sides = {"refund": ("D", base), "output_vat": ("D", vat), "cash": ("C", total)}
    for ln in lines:
        side, amount = sides[str(ln["role"])]
        if str(ln.get("side") or "").upper() != side or _d(ln.get("amount")) != amount or not str(ln.get("acc") or "").strip():
            return bad(f"refund journal line {ln.get('role')} invalid")
    return None


_VAT_FIELD = re.compile(r"AMT\d{2}|AMTRAT\d")


def _validate_vat_fields(fields, base: Decimal) -> PayloadValidationResult | None:
    """``vat_fields``:只认 ISVAT 的金额栏(AMTnn / AMTRATn,税额栏照 vat_amount 写、不许改),金额 0 ≤ x ≤ 整张税前。"""
    if not isinstance(fields, dict) or not fields:
        return PayloadValidationResult(False, ERR_AMOUNTS, "vat_fields must be a non-empty object")
    for name, value in fields.items():
        amount = _d(value)
        if not isinstance(name, str) or not _VAT_FIELD.fullmatch(name):
            return PayloadValidationResult(False, ERR_AMOUNTS, f"vat_fields {name!r} is not an ISVAT amount column")
        if amount is None or not amount.is_finite() or amount < 0 or amount > base:
            return PayloadValidationResult(False, ERR_AMOUNTS, f"vat_fields {name} out of range")
    return None


def _validate_pos(payload: Dict[str, Any], doctype: str, total: Decimal) -> PayloadValidationResult | None:
    """POS 日汇总票的 `pos` 块:现销、单号自编、票号起止与张数在、每张转账凭证科目非空、金额为正,
    各转账 + 当天现金净额(`cash`,可为负:现金退款多过现金收款)恰好等于本单含税额。
    不达标 → 拒(不写出半张对不上的日汇总)。"""
    pos = payload.get("pos")

    def bad(detail):
        return PayloadValidationResult(False, ERR_POS_INVALID, detail)

    if not isinstance(pos, dict):
        return bad("pos must be an object")
    if doctype != (REFUND_DOCTYPE if "refund" in payload else "HS"):
        return bad("pos sales must be HS (SR for a refund)")
    if pos.get("docnum") != "auto":
        return bad("pos docnum must be auto")
    for key in ("first", "last"):
        value = pos.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > _POS_NUMBER_MAX:
            return bad(f"pos {key} invalid")
    if type(pos.get("count")) is not int or pos["count"] < 1:
        return bad("pos count invalid")
    transfers = pos.get("transfers")
    if not isinstance(transfers, list) or len(transfers) > _POS_TRANSFERS_MAX:
        return bad("pos transfers invalid")
    moved = Decimal("0")
    for row in transfers:
        if not isinstance(row, dict) or not str(row.get("acc") or "").strip():
            return bad("pos transfer account missing")
        amount = _d(row.get("amount"))
        if amount is None or not amount.is_finite() or amount <= 0:
            return bad("pos transfer amount invalid")
        if len(str(row.get("label") or "")) > _POS_LABEL_MAX:
            return bad("pos transfer label too long")
        moved += amount
    cash = _d(pos.get("cash"))
    if cash is None or not cash.is_finite() or _q(moved + cash) != _q(total):
        return bad("pos transfers and cash do not add up to the total")
    return None


def _validate_items(payload: Dict[str, Any]) -> PayloadValidationResult | None:
    """明细守门:仅当 items_status=='ok' 才会落 STCRD,故只严校此态。
    要求 items 非空、挂账科目在、每行有品名+可解析金额;不达标 → 拒(不静默写坏明细)。
    其它状态(empty/incomplete/mismatch)= 云端已判明细不可信 → 放行(退回表头模式)。
    """
    if str(payload.get("items_status") or "").strip() != "ok":
        return None
    items = payload.get("items") or []
    if not items:
        return PayloadValidationResult(False, ERR_ITEMS_INVALID, "items_status=ok but empty")
    if not str(payload.get("items_account") or "").strip():
        return PayloadValidationResult(False, ERR_ITEMS_INVALID, "items_account missing")
    for it in items:
        if not str((it or {}).get("name") or "").strip():
            return PayloadValidationResult(False, ERR_ITEMS_INVALID, "item missing name")
        if _d(it.get("amount")) is None:
            return PayloadValidationResult(False, ERR_ITEMS_INVALID, "item amount not numeric")
    return None


def build_sales_entry(payload: Dict[str, Any]) -> SalesEntry:
    """P1 销项载荷 → SalesEntry。需先 validate_sales_payload 通过。"""
    cust = payload.get("customer") or {}
    code = str(cust.get("code") or "").strip()
    return SalesEntry(
        doctype=str(payload.get("doctype") or "").strip().upper(),
        account_set=str(payload.get("account_set") or "").strip(),
        customer_code=code,
        # 无代码 → 必判新客户(不能把空码当老客户录入)。
        customer_new=bool(cust.get("customer_new")) or not code,
        customer_name=str(cust.get("name") or "").strip(),
        customer_tax_id=str(cust.get("tax_id") or "").strip(),
        customer_prename=str(cust.get("prename") or "").strip(),
        customer_address=str(cust.get("address") or "").strip(),
        customer_branch=str(cust.get("branch") or "").strip(),
        customer_confirmed_new=cust.get("confirmed_new") is True,
        ref_no=str(payload.get("ref_no") or "").strip(),
        doc_date=be_date_to_express(payload.get("docdate_be")),
        vat_period=be_period_to_express(payload.get("vat_period_be")),
        vat_rate=_vat_rate_str(payload),
        base_amount=format(_q(_d(payload.get("base_amount")) or Decimal("0")), "f"),
        vat_amount=format(_q(_d(payload.get("vat_amount")) or Decimal("0")), "f"),
        total_amount=format(_q(_d(payload.get("total_amount")) or Decimal("0")), "f"),
        lines=list(payload.get("lines") or []),
        items=list(payload.get("items") or []),
        items_status=str(payload.get("items_status") or "empty").strip(),
        items_account=str(payload.get("items_account") or "").strip(),
        stock_acccod=str(payload.get("stock_acccod") or "").strip(),
        vat_fields={name: format(_q(_d(value)), "f") for name, value in (payload.get("vat_fields") or {}).items()},
        **_pos_fields(payload.get("pos")),
        **_refund_fields(payload.get("refund")),
    )


def _refund_fields(refund) -> Dict[str, Any]:
    if not isinstance(refund, dict):
        return {}
    return {
        "is_refund": True,
        "refund_against": str(refund.get("against") or "").strip(),
        "refund_against_total": format(_q(_d(refund.get("against_total")) or Decimal("0")), "f"),
    }


def _pos_fields(pos) -> Dict[str, Any]:
    if not isinstance(pos, dict):
        return {}
    return {
        "is_pos": True,
        "pos_first": str(pos.get("first") or "").strip(),
        "pos_last": str(pos.get("last") or "").strip(),
        "pos_count": int(pos.get("count") or 0),
        "pos_transfers": [
            {"acc": str(row.get("acc") or "").strip(),
             "amount": format(_q(_d(row.get("amount")) or Decimal("0")), "f"),
             "label": str(row.get("label") or "").strip()}
            for row in pos.get("transfers") or [] if isinstance(row, dict)
        ],
    }
