"""P1 采购载荷 → Express 进项录入字段(确定性映射 · 不连库 · 不调 LLM)。

接 pearnly-app 出的采购载荷(services/erp/express_push/mapper.build_express_payload):
  doctype RR/HP · account_set · docdate_be(YYMMDD 佛历)· vat_period_be ·
  ref_no(供应商票号)· supplier{code,name,tax_id,prename,supplier_new} ·
  vat_rate · base_amount/vat_amount/total_amount · lines[{acc,side,amount,desc}]。

映射到 Express 采购赊购/现购页(1.ซื้อ → 4.ซื้อเงินเชื่อ)可录入的进项字段:
  供应商代码 / 供应商票号 REFNUM / 佛历单据日 DD/MM/YY / 税前金额 / VAT 率。

分录(lines)由 Express 按供应商默认科目(APMAS.ACCNUM)自动过账,Companion 只录票头 +
金额 + VAT 率;lines/总额随载荷带下,供录入后 OCR 读回核对与诚实回报。

⚠️ 真 Express 进项页的模板/字段定位/校准需 Windows + Express(下一棒);本模块只做
确定性数据映射 + 本地守门,headless 可全测。账套白名单只 DATAT(red line · 端侧再钉)。
"""
from __future__ import annotations

import re

from dataclasses import dataclass, field as df
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

ALLOWED_ACCOUNT_SETS = ()  # fail-safe 默认:无所选账套 → 空 → 任何账套都拒(没选不能瞎写)
ALLOWED_DOCTYPES = ("RR", "HP")


def allowed_account_sets(selected: str = "") -> tuple:
    """端侧账套名期望值 = 客户所选账套(config.account_set)。

    所选非空 → 仅该账套放行;缺所选 → 空元组(fail-safe·任何账套都拒)。无 DATAT 写死。
    """
    s = (selected or "").strip()
    return (s,) if s else ()

# Express 采购页 Tab 录入顺序(票头字段;明细分录由 Express 自过账)
PURCHASE_FIELD_ORDER = ("supplier_code", "ref_no", "doc_date", "base_amount", "vat_rate")

REF_NO_BYTES = 15  # Express 的 REFNUM C(15)（APTRN / ISVAT），按账套编码 cp874 计


def fit_ref_no(value, limit: int = REF_NO_BYTES) -> str:
    """票号放进 Express 的 REFNUM：放得下原样；放不下先去掉分隔符（- / . 空格），还放不下保留后面 limit 字节
    （流水号在后）。会计手录超长票号也是这样缩（10-09 实测：简易税票号 G0012-69-0045871 共 16 位，停在「字段太长」）。
    只用于进项（供应商的票号）；销项是自家开的票，税表里的号要和印出来的一致，超长照旧拦下（`dbf_sales`）。"""
    text = str(value or "").strip()

    def size(item: str) -> int:
        return len(item.encode("cp874", "replace"))

    if size(text) <= limit:
        return text
    compact = re.sub(r"[\s\-/.]", "", text)
    while size(compact) > limit:
        compact = compact[1:]
    return compact

ERR_ACCOUNT_SET_NOT_ALLOWED = "ACCOUNT_SET_NOT_ALLOWED"
ERR_BAD_DOCTYPE = "BAD_DOCTYPE"
ERR_INVALID_DATE = "INVALID_DOC_DATE"
ERR_MISSING_REF_NO = "MISSING_REF_NO"
ERR_MISSING_SUPPLIER = "MISSING_SUPPLIER"
ERR_AMOUNTS = "AMOUNTS_NOT_CONSISTENT"
ERR_ENTRY_NOT_BALANCED = "ENTRY_NOT_BALANCED"
ERR_ITEMS_INVALID = "ITEMS_INVALID"

_CENT = Decimal("0.01")
_BALANCE_TOL = Decimal("0.02")


@dataclass
class PayloadValidationResult:
    ok: bool
    error_code: Optional[str] = None
    detail: str = ""


@dataclass
class PurchaseEntry:
    """录入 Express 采购页所需的归一化字段。"""

    doctype: str
    account_set: str
    supplier_code: str
    supplier_new: bool
    supplier_name: str
    supplier_tax_id: str
    supplier_prename: str
    ref_no: str
    doc_date: str  # DD/MM/YY(佛历 2 位年)
    vat_period: str  # MM/YY(佛历)
    vat_rate: str  # "7" | "0"
    base_amount: str
    vat_amount: str
    total_amount: str
    lines: List[Dict[str, Any]] = df(default_factory=list)
    # V1 安全明细:仅 items_status=='ok' 时落 STCRD;否则退回表头模式(诚实)。
    items: List[Dict[str, Any]] = df(default_factory=list)
    items_status: str = "empty"
    items_account: str = ""
    # 自建供应商落档地址(与真账录入一致;空=不填)。
    supplier_address: str = ""
    # 分店号(5 位,如 00018;空=未提供)。连锁供应商同税号多档时的匹配判据 + 新建档落 ORGNUM。
    supplier_branch: str = ""
    # 存货科目组(ISACC ACCCOD)· 与销项同名同义:账套零库存品时建 STKTYP=0 全靠它。
    stock_acccod: str = ""
    # 会计在复核时选了「新建供应商档」(载荷 supplier.confirmed_new):不再按税号 / 名称找旧档、
    # 不按「疑似重复」拦,照票面新建(同税号几个档里没有这一家时由人拍板 · 2026-09-28 压测)。
    supplier_confirmed_new: bool = False


def _d(v: Any) -> Optional[Decimal]:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v).replace(",", "").strip())
    except (InvalidOperation, ValueError, TypeError):
        return None


def _q(v: Decimal) -> Decimal:
    return v.quantize(_CENT, rounding=ROUND_HALF_EVEN)


def _valid_be_yymmdd(s: str) -> bool:
    if not (len(s) == 6 and s.isdigit()):
        return False
    mm, dd = int(s[2:4]), int(s[4:6])
    return 1 <= mm <= 12 and 1 <= dd <= 31


def be_date_to_express(docdate_be: str) -> str:
    """佛历 YYMMDD → Express 日期 DD/MM/YY,例 '581231' → '31/12/58'。"""
    s = str(docdate_be or "").strip()
    if not _valid_be_yymmdd(s):
        raise ValueError(f"bad docdate_be: {docdate_be!r}")
    return f"{s[4:6]}/{s[2:4]}/{s[0:2]}"


def be_period_to_express(vat_period_be: str) -> str:
    """佛历税期 YYMMDD → MM/YY,例 '581201' → '12/58'。空/非法返回 ''。"""
    s = str(vat_period_be or "").strip()
    if not _valid_be_yymmdd(s):
        return ""
    return f"{s[2:4]}/{s[0:2]}"


def _vat_rate_str(payload: Dict[str, Any]) -> str:
    rate = _d(payload.get("vat_rate"))
    if rate is None or rate <= 0:
        return "0"
    return format(rate.normalize(), "f")


def validate_purchase_payload(
    payload: Dict[str, Any], allowed: tuple = ALLOWED_ACCOUNT_SETS
) -> PayloadValidationResult:
    """录入 Express 前的端侧守门:白名单 + 必填 + 金额/分录自洽。

    后端 mapper 已做主校验;这里是 Companion 真正点击/输入前最后一道保护,防止
    缺映射或坏数据时乱写客户账套。任一不过 → 调用方落 manual/ack_failure,绝不录入。
    `allowed` = 端侧账套名期望值(客户所选账套 · 见 allowed_account_sets)。缺所选 → 空 → 必拒。
    """
    account_set = str(payload.get("account_set") or "").strip()
    if account_set not in allowed:
        return PayloadValidationResult(
            False, ERR_ACCOUNT_SET_NOT_ALLOWED,
            f"account_set {account_set!r} not in whitelist {allowed}",
        )

    doctype = str(payload.get("doctype") or "").strip().upper()
    if doctype not in ALLOWED_DOCTYPES:
        return PayloadValidationResult(
            False, ERR_BAD_DOCTYPE, f"doctype must be one of {ALLOWED_DOCTYPES}"
        )

    if not _valid_be_yymmdd(str(payload.get("docdate_be") or "").strip()):
        return PayloadValidationResult(
            False, ERR_INVALID_DATE, "docdate_be must be Buddhist YYMMDD"
        )

    if not str(payload.get("ref_no") or "").strip():
        return PayloadValidationResult(
            False, ERR_MISSING_REF_NO, "missing supplier ref_no (REFNUM)"
        )

    supplier = payload.get("supplier") or {}
    if not (str(supplier.get("code") or "").strip()
            or str(supplier.get("name") or "").strip()):
        return PayloadValidationResult(
            False, ERR_MISSING_SUPPLIER, "supplier needs a code or a name"
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

    bal = _lines_balance(payload.get("lines") or [])
    if bal is None:
        return PayloadValidationResult(
            False, ERR_ENTRY_NOT_BALANCED, "GL lines do not balance (ΣD != ΣC)"
        )

    item_err = _validate_items(payload)
    if item_err is not None:
        return item_err

    return PayloadValidationResult(True)


def _validate_items(payload: Dict[str, Any]) -> Optional[PayloadValidationResult]:
    """明细守门(镜像销项):仅 items_status=='ok' 严校,要求行非空+挂账科目+每行品名/金额。
    其它状态=云端已判不可信 → 放行退回表头模式。"""
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


def _lines_balance(lines: List[Dict[str, Any]]) -> Optional[tuple[Decimal, Decimal]]:
    """返回 (ΣD, ΣC) 当借贷平且非空;否则 None。"""
    if not lines:
        return None
    dr = Decimal("0")
    cr = Decimal("0")
    for ln in lines:
        amt = _d(ln.get("amount"))
        if amt is None:
            return None
        side = str(ln.get("side") or "").strip().upper()
        if side == "D":
            dr += amt
        elif side == "C":
            cr += amt
        else:
            return None
    if _q(dr) != _q(cr) or dr <= 0:
        return None
    return dr, cr


def build_purchase_entry(payload: Dict[str, Any]) -> PurchaseEntry:
    """P1 采购载荷 → PurchaseEntry。需先 validate_purchase_payload 通过。"""
    supplier = payload.get("supplier") or {}
    code = str(supplier.get("code") or "").strip()
    return PurchaseEntry(
        doctype=str(payload.get("doctype") or "").strip().upper(),
        account_set=str(payload.get("account_set") or "").strip(),
        supplier_code=code,
        stock_acccod=str(payload.get("stock_acccod") or "").strip(),
        # 无代码 → 必判新供应商(不能把空代码当老供应商录入),旗标只能再加严不放松。
        supplier_new=bool(supplier.get("supplier_new")) or not code,
        supplier_name=str(supplier.get("name") or "").strip(),
        supplier_tax_id=str(supplier.get("tax_id") or "").strip(),
        supplier_prename=str(supplier.get("prename") or "").strip(),
        supplier_address=str(supplier.get("address") or "").strip(),
        supplier_branch=str(supplier.get("branch") or "").strip(),
        supplier_confirmed_new=supplier.get("confirmed_new") is True,
        ref_no=fit_ref_no(payload.get("ref_no")),
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
    )


def field_bindings(entry: PurchaseEntry) -> List[tuple[str, str]]:
    """按 PURCHASE_FIELD_ORDER 产出 (字段名, 值) 录入序列。

    新供应商(supplier_new·APMAS 无档)无代码可录 → 跳过 supplier_code,由上游先建档;
    其余票头字段照序产出。
    """
    values = {
        "supplier_code": entry.supplier_code,
        "ref_no": entry.ref_no,
        "doc_date": entry.doc_date,
        "base_amount": entry.base_amount,
        "vat_rate": entry.vat_rate,
    }
    out: List[tuple[str, str]] = []
    for name in PURCHASE_FIELD_ORDER:
        val = values.get(name, "")
        if name == "supplier_code" and not val:
            continue  # 新供应商:代码留待建档,不录空
        out.append((name, val))
    return out
