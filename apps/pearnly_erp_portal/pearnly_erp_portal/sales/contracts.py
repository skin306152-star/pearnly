"""销售单据的纯规则：类型、金额、确认前的闸、识别结果归一化。

旧线上这一层拆在 `services/sales/{totals,wht,numbering,issue_gates,baht_text}.py`；本仓
只留"记录一次销售"要用的规则。

**本仓不做税务发票**（用户 2026-09-23：开票系统与开税务发票都不要）：没有税票类型、
没有税票连号、没有预扣税（WHT）、没有票面与泰文金额大写、没有红冲补开、没有报价转开票。
销售单只是记录，它确认之后唯一的自动下游是出库。

这一层**不连库、不读配置、不发消息**：金额规则错了月底对不出账，而且错在外面看不出来，
所以它必须能在没有 Site 的地方被断言。对外只有一条依赖：买方类型的取值域在
`masters.contracts`（同 App 的纯规则）。
"""

from __future__ import annotations

from decimal import Decimal

from pearnly_erp_portal.masters.contracts import PARTY_TYPES
from pearnly_erp_portal.recognition import checks

_CENT = Decimal("0.01")
_HUNDRED = Decimal("100")

# ── 单据类型与状态 ──────────────────────────────────────────────────────────

# 只剩两种**非税务**单据：报价（还没成交）与收据（钱货两清的记录）。
QUOTATION = "quotation"
RECEIPT = "receipt"
DOC_KINDS = (QUOTATION, RECEIPT)

DRAFT = "draft"
SUBMITTED = "submitted"
VOID = "void"
STATUSES = (DRAFT, SUBMITTED, VOID)

# 确认之后就进销项、不能回头的状态。
SETTLED_STATUSES = (SUBMITTED, VOID)
# 只有草稿能被改；确认之后改金额等于改历史。
EDITABLE_STATUSES = (DRAFT,)

# ── 金额 ────────────────────────────────────────────────────────────────────


def _d(value) -> Decimal:
    return Decimal(str(value if value is not None else 0))


def _has_value(value) -> bool:
    """折扣百分比是否**真的**给了值（0 / None / 空串都不算给了）。"""
    return value not in (None, "", 0, "0", "0.0", "0.00")


def _line_discount(gross: Decimal, line: dict) -> tuple[Decimal, Decimal | None]:
    """行折扣：给了百分比走百分比，否则用绝对额；折后不为负。"""
    percentage = line.get("discount_pct")
    if _has_value(percentage):
        rate = _d(percentage)
        disc = (gross * rate / _HUNDRED).quantize(_CENT)
    else:
        rate = None
        disc = _d(line.get("discount", 0)).quantize(_CENT)
    return (disc if disc <= gross else gross), rate


def _resolve_header_discount(base: Decimal, amount, percentage) -> Decimal:
    """整单折扣：给了百分比走百分比，否则绝对额；夹在 [0, base]。"""
    head = ((base * _d(percentage) / _HUNDRED).quantize(_CENT) if _has_value(percentage)
            else _d(amount).quantize(_CENT))
    if head < 0:
        return Decimal("0.00")
    return head if head <= base else base


def _taxable_vat_base(lines: list, subtotal: Decimal, header_discount: Decimal) -> Decimal:
    """整单折扣按行金额比例摊到应税净额上，让 VAT 落在实际成交价上。"""
    taxable = sum((line["line_total"] for line in lines if line["vat_applicable"]),
                  Decimal("0"))
    if header_discount == 0 or subtotal == 0:
        return taxable.quantize(_CENT)
    share = (header_discount * taxable / subtotal).quantize(_CENT)
    base = (taxable - share).quantize(_CENT)
    return base if base > 0 else Decimal("0.00")


def compute_totals(lines: list, *, vat_rate, header_discount_amount=0,
                   header_discount_pct=0, price_includes_vat=False) -> dict:
    """从明细行算金额。

    行级折扣 + 整单折扣；VAT 落在**折后**应税净额上。
    `price_includes_vat=True` 表示行金额已含税（门市标价），此时反算 VAT 并把净额退成
    不含税；开关是**单据级**的，一张单里不能一半含税一半不含税。
    """
    vat = _d(vat_rate)
    subtotal = Decimal("0")
    discount_total = Decimal("0")
    normalized = []
    for index, line in enumerate(lines, start=1):
        qty = _d(line.get("qty", 1))
        price = _d(line.get("unit_price", 0))
        gross = (qty * price).quantize(_CENT)
        disc, percentage = _line_discount(gross, line)
        line_total = (gross - disc).quantize(_CENT)
        subtotal += line_total
        discount_total += disc
        normalized.append({
            "line_no": index,
            "item_type": line.get("item_type") or "goods",
            "product": line.get("product"),
            "description": (line.get("description") or "").strip(),
            "qty": qty,
            "unit": line.get("unit"),
            "unit_price": price,
            "discount": disc,
            "discount_pct": percentage,
            "line_total": line_total,
            "vat_applicable": line.get("vat_applicable", True) is not False,
        })

    header_discount = _resolve_header_discount(subtotal, header_discount_amount,
                                               header_discount_pct)
    discount_total += header_discount
    net_before_vat = (subtotal - header_discount).quantize(_CENT)
    taxable = _taxable_vat_base(normalized, subtotal, header_discount)

    if price_includes_vat and vat > 0:
        # 价内：行金额已含税 → VAT 反算，净额退成不含税。
        vat_amount = (taxable * vat / (_HUNDRED + vat)).quantize(_CENT)
        net_after_vat = (net_before_vat - vat_amount).quantize(_CENT)
        grand_total = net_before_vat
    else:
        vat_amount = (taxable * vat / _HUNDRED).quantize(_CENT)
        net_after_vat = net_before_vat
        grand_total = (net_before_vat + vat_amount).quantize(_CENT)

    return {
        "lines": normalized,
        "subtotal": subtotal.quantize(_CENT),
        "discount_total": discount_total.quantize(_CENT),
        "header_discount": header_discount,
        "net_before_vat": net_before_vat,
        "vat_base": taxable,
        "vat_amount": vat_amount,
        "net_after_vat": net_after_vat,
        "grand_total": grand_total,
    }


# ── 收款 ────────────────────────────────────────────────────────────────────
#
# 收款按旧线上的**表头口径**：钱记在单据上（`paid_amount` + 状态 + 方式 + 日期），
# 不另开流水表 —— 旧线上 `sales_documents` 就是这么存的。状态由"收了多少 / 该收多少"
# 算出来，不接受前端传来与金额矛盾的组合。

PAYMENT_STATUSES = ("unpaid", "partial", "paid")
# 旧线上收款方式是自由字符串（少数几家这么用）；这里给常用的四个，够覆盖门店实际。
PAYMENT_METHODS = ("cash", "transfer", "promptpay", "other")


def payment_status_for(paid, total) -> str:
    """收款状态：没收 = unpaid，收满 = paid，中间 = partial。"""
    paid_amount = _d(paid)
    total_amount = _d(total)
    if paid_amount <= 0:
        return "unpaid"
    if total_amount > 0 and paid_amount < total_amount:
        return "partial"
    return "paid"


# ── 确认前的闸 ──────────────────────────────────────────────────────────────
#
# 都在**确认之前**跑：不过就不写成已确认，也就不会动货。
#
# 收据**不要求已收款**（用户 2026-09-23 去掉开票侧之后，收据同时承担"现金卖货"和
# "赊账卖出"两种情形；税务发票那种"钱已收的凭证"口径已不在本仓）。收到钱用「收款」
# 动作记上去，状态由金额算。

# 面额 ≤ 0 的收据不确认：那要么是某行没设价（把"没设价"当 0 发过来了），要么走错了单据。
# 报价单不在内 —— ฿0 的报价顶多是没写完。
REQUIRE_AMOUNT = (RECEIPT,)


def amount_gate(row: dict) -> str | None:
    """合计 ≤ 0 的收据不许确认。合计读不出来也拦住。"""
    if row.get("doc_kind") not in REQUIRE_AMOUNT:
        return None
    try:
        total = Decimal(str(row.get("grand_total") or 0))
    except (ArithmeticError, ValueError):
        return "zero_amount"
    return None if total > 0 else "zero_amount"


def confirm_gates(row: dict) -> list[str]:
    """确认前的全部拒因（空 = 可以确认）。顺序固定，方便调用方按第一个拒因回话。"""
    reasons = []
    for reason in (amount_gate(row),):
        if reason:
            reasons.append(reason)
    return reasons


# ── 状态 × 动作 ─────────────────────────────────────────────────────────────
#
# 这张表就是"什么状态能做什么"，服务端按它拒绝，前端按它显示按钮（前端不自己算）。
# 确认（submitted）是分水岭：确认之后不能改金额，只能作废。

EDIT = "edit"
CONFIRM = "confirm"
VOID_ACTION = "void"

ACTIONS_BY_STATUS = {
    DRAFT: (EDIT, CONFIRM),
    SUBMITTED: (VOID_ACTION,),
    VOID: (),
}


def allowed_actions(status: str) -> tuple:
    """该状态允许的动作。未知状态一律空（fail-closed，不靠前端记）。"""
    return ACTIONS_BY_STATUS.get(status, ())


def can(status: str, action: str) -> bool:
    return action in allowed_actions(status)


# ── 销售单的来源与下游 ──────────────────────────────────────────────────────
#
# 用户 2026-09-21 对齐：销售记录的进出只有两条 —— **进**：POS 收银、上传单据识别、
# 手工录入；**出**：商品/库存/收发存报表。没有会计凭证、没有报税、没有对外推送、
# 没有账套。2026-09-23 再去掉开票侧（税票类型、税票连号、票面、红冲补开、报价转开票）。
#
# 所以一张销售单落定后**唯一的自动下游是出库**：确认了就是货出门。报价和草稿都不动货。

SOURCE_POS = "pos"
SOURCE_OCR = "ocr"
SOURCE_MANUAL = "manual"
SOURCES = (SOURCE_POS, SOURCE_OCR, SOURCE_MANUAL)


def should_post_stock(doc_kind: str, status: str) -> bool:
    """这张单要不要过账成出库。只有**已确认**的销售凭证动货。"""
    if status != SUBMITTED:
        return False
    return doc_kind in (RECEIPT,)


# ── 识别结果的归一化（纯函数）──────────────────────────────────────────────
#
# 上传单据 → 识别 → 归一成建单要的字段。**归一在这一层**：模型返回什么形状都能在离线
# 断言里看见，不必花钱调模型。金额不在这里算 —— 识别结果只描述票面，不替我们算数。


def unit_price_of(row: dict):
    """票面单价；只印了行金额（门市小票常见：数量 + 金额两列）就用 金额 ÷ 数量。

    09-26 真机：小票只有 Amount 一列，识别按「不许自己算」把单价全给了 0，整张单金额为 0。
    除法不是替模型算账：行金额是票面上印的，这里只是换成本单要的「单价」口径。"""
    for key in ("unit_price", "amount"):
        try:
            value = _d(str(row.get(key) or 0).replace(",", ""))
        except ArithmeticError:
            continue
        if value > 0:
            if key == "unit_price":
                return float(value)
            try:
                qty = _d(str(row.get("qty") or 1).replace(",", ""))
            except ArithmeticError:
                qty = _d(1)
            return float((value / (qty if qty > 0 else 1)).quantize(_CENT))
    return 0


def _header_discount(value):
    try:
        amount = _d(str(value or 0).replace(",", ""))
    except ArithmeticError:
        return 0
    return float(amount.quantize(_CENT)) if amount > 0 else 0


def _merged_discount(header, discount_lines):
    """模型已经把同一笔折扣读进了整单折扣就不重复加。"""
    if not discount_lines or abs(_d(header) - discount_lines) <= Decimal("0.005"):
        return header
    return float((_d(header) + discount_lines).quantize(_CENT))


def map_recognised(payload: dict, *, source_file: str = "", image_sha256: str = "") -> dict:
    """把识别结果归一成 `create_document` 的入参形状。缺什么就留空，不猜。"""
    payload = payload or {}
    lines, discount_lines = [], Decimal(0)
    for row in payload.get("lines") or []:
        description = str(row.get("description") or "").strip()
        if not description:
            continue
        value = abs(checks.money(row.get("amount")) or checks.money(row.get("unit_price"))
                    or checks.money(row.get("discount")) or 0)
        if value and checks.is_discount_line(description, row.get("amount"), row.get("unit_price")):
            # 折扣 / 优惠券行不是商品：并进整单折扣（`recognition.checks.is_discount_line`）；0 元的照常留作一行。
            discount_lines += value
            continue
        lines.append({
            "description": description[:300],
            "qty": row.get("qty") or 1,
            "unit": (str(row.get("unit") or "").strip() or None),
            "unit_price": unit_price_of(row),
            "discount": row.get("discount") or 0,
            "discount_pct": row.get("discount_percent") or 0,
        })
    buyer_type = str(payload.get("buyer_type") or "company").strip().lower()
    if buyer_type not in PARTY_TYPES:
        buyer_type = "company"
    return {
        "doc_kind": RECEIPT,
        # 票面收据号：本单没有外部单号字段（系统号另发），只给 LINE 卡片显示。
        "doc_no": (str(payload.get("doc_no") or "").strip()[:140] or None),
        "doc_date": (str(payload.get("doc_date") or "").strip() or None),
        "buyer_name": (str(payload.get("buyer_name") or "").strip()[:200]
                       or str(payload.get("seller_name") or "").strip()[:200]),
        "party_type": buyer_type,
        "address": (str(payload.get("buyer_address") or "").strip() or None),
        "has_vat": payload.get("has_vat") is not False,
        "vat_rate": payload.get("vat_rate") if payload.get("vat_rate") is not None else 7,
        # 门市小票（ABB）通常是含税价：票面写着含税就按含税算，不在上面再加一次 VAT。
        "price_includes_vat": payload.get("price_includes_vat") is True,
        # 票面整单折扣（金额）：以前没读，有整单折扣的小票入账会多记。
        "header_discount": _merged_discount(_header_discount(payload.get("discount_total")), discount_lines),
        "lines": lines,
        "source_file": source_file or None,
        "image_sha256": image_sha256 or None,
    }
