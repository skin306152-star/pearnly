"""销售单据的纯规则：类型、金额、连号、开出前的闸、泰文金额大写。

口径逐条照旧线上 `services/sales/*`（`totals.py` / `wht.py` / `numbering.py` /
`issue_gates.py` / `baht_text.py`）。这一层**不连库、不读配置、不发消息**：金额规则
错了，月底对不出账，而且错在外面看不出来 —— 所以它必须能在没有 Site 的地方被断言。

本模块只有一条对外依赖：买方完整性的判定在 `masters.contracts`（同 App 的纯规则）。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from pearnly_erp_portal.masters.contracts import missing_for_tax_invoice

_CENT = Decimal("0.01")
_HUNDRED = Decimal("100")

# ── 单据类型与状态 ──────────────────────────────────────────────────────────

# 报价不是税务凭证；三种票（全式税票 / 简易税票 / 税票式收据）与收据是钱与货的凭证；
# 借贷记是冲正。
QUOTATION = "quotation"
TAX_INVOICE = "tax_invoice"
TAX_INVOICE_SIMPLE = "tax_invoice_simple"
TAX_INVOICE_RECEIPT = "tax_invoice_receipt"
RECEIPT = "receipt"
CREDIT_NOTE = "credit_note"
DEBIT_NOTE = "debit_note"

DOC_KINDS = (QUOTATION, TAX_INVOICE, TAX_INVOICE_SIMPLE, TAX_INVOICE_RECEIPT, RECEIPT,
             CREDIT_NOTE, DEBIT_NOTE)

# 走完整买方块（抬头/税号/分店）才能开出的单据：税局要看买方是谁。
NEEDS_FULL_BUYER = (TAX_INVOICE, TAX_INVOICE_RECEIPT)

DRAFT = "draft"
PENDING = "pending_approval"
ISSUED = "issued"
VOID = "void"
STATUSES = (DRAFT, PENDING, ISSUED, VOID)

# 开出去就进销项、不能回头的状态。
SETTLED_STATUSES = (ISSUED, VOID)
# 只有草稿与"待审批"能被改；开出之后改票面等于改税表。
EDITABLE_STATUSES = (DRAFT, PENDING)

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


def compute_totals(lines: list, *, vat_rate, wht_rate=0, header_discount_amount=0,
                   header_discount_pct=0, price_includes_vat=False) -> dict:
    """从明细行算金额。

    行级折扣 + 整单折扣；VAT 落在**折后**应税净额上；WHT 同样按折后净额。
    `price_includes_vat=True` 表示行金额已含税（门市标价），此时反算 VAT 并把净额退成
    不含税；开关是**单据级**的，一张票里不能一半含税一半不含税。
    """
    vat = _d(vat_rate)
    wht = _d(wht_rate)
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

    wht_amount = (net_after_vat * wht / _HUNDRED).quantize(_CENT)
    net_payable = (grand_total - wht_amount).quantize(_CENT)
    return {
        "lines": normalized,
        "subtotal": subtotal.quantize(_CENT),
        "discount_total": discount_total.quantize(_CENT),
        "header_discount": header_discount,
        "net_before_vat": net_before_vat,
        "vat_base": taxable,
        "vat_amount": vat_amount,
        "net_after_vat": net_after_vat,
        "wht_amount": wht_amount,
        "grand_total": grand_total,
        "net_payable": net_payable,
    }


# ── 预扣税档位（票面标签）─────────────────────────────────────────────────

# 泰国 WHT 按服务类型分档。金额仍由 compute_totals 按档率算；这里只给档位与票面标签。
WHT_PRESETS = (
    ("0", "ไม่หัก / None"),
    ("1", "ค่าขนส่ง / Transport"),
    ("2", "ค่าโฆษณา / Advertising"),
    ("3", "ค่าบริการ·วิชาชีพ / Service & Professional"),
    ("5", "ค่าเช่า / Rental"),
)


def _format_rate(rate) -> str:
    """档率去尾零：3.00 → 3，1.50 → 1.5。"""
    value = Decimal(str(rate if rate not in (None, "") else 0))
    return format(value.normalize(), "f")


def wht_label(rate, lang: str = "th_en") -> str:
    """票面 WHT 标签带档率；泰文以外补次语。

    次语现在只有英文（`WHT`）。旧线上还有一个"泰+中"档 —— 中文那档等语言包批一起做，
    免得在这里写死一句中文（`check-i18n` 的存量清单不再增长）。
    """
    base = f"หัก ณ ที่จ่าย {_format_rate(rate)}%"
    return base if lang == "th" else f"{base} / WHT"


# ── 泰文金额大写 ────────────────────────────────────────────────────────────
#
# 泰国收据/税票的标配（จำนวนเงินตัวอักษร）。规则：整数部分逐段读（สิบ/ร้อย/พัน/หมื่น/
# แสน，超六位递归 ล้าน），个位 1 读 เอ็ด、十位 2 读 ยี่สิบ、十位 1 读 สิบ；无 สตางค์
# 收 บาทถ้วน。**字符串里出现的泰文是票面文字**，不是给人看的界面文案。

_THAI_DIGITS = ["ศูนย์", "หนึ่ง", "สอง", "สาม", "สี่", "ห้า", "หก", "เจ็ด", "แปด", "เก้า"]
_THAI_POSITIONS = ["", "สิบ", "ร้อย", "พัน", "หมื่น", "แสน"]


def _read_group(digits: str) -> str:
    digits = digits.lstrip("0")
    if not digits:
        return ""
    text = ""
    length = len(digits)
    for index, char in enumerate(digits):
        digit = int(char)
        position = length - 1 - index
        if digit == 0:
            continue
        if position == 0 and digit == 1 and length > 1:
            text += "เอ็ด"
        elif position == 1 and digit == 2:
            text += "ยี่สิบ"
        elif position == 1 and digit == 1:
            text += "สิบ"
        else:
            text += _THAI_DIGITS[digit] + _THAI_POSITIONS[position]
    return text


def _read_int(value: int) -> str:
    if value == 0:
        return "ศูนย์"
    digits = str(value)
    if len(digits) > 6:  # ล้าน 递归（支持 ล้านล้าน）
        return _read_int(int(digits[:-6])) + "ล้าน" + _read_group(digits[-6:])
    return _read_group(digits)


def baht_text(amount) -> str:
    """泰文金额大写：整数 + บาท +（ถ้วน 或两位 สตางค์）。"""
    quantized = Decimal(str(amount)).quantize(_CENT, rounding=ROUND_HALF_UP)
    baht = int(quantized)
    satang = int((quantized - baht) * 100)
    text = _read_int(baht) + "บาท"
    return text + ("ถ้วน" if satang == 0 else _read_group(str(satang)) + "สตางค์")


# ── 连号 ────────────────────────────────────────────────────────────────────

RESET_YEARLY = "yearly"
RESET_MONTHLY = "monthly"
RESET_NEVER = "never"

# 类型默认前缀（公司资料里的 doc_prefix 优先）。
DEFAULT_PREFIXES = {
    QUOTATION: "QT",
    TAX_INVOICE: "INV",
    TAX_INVOICE_SIMPLE: "ABB",
    TAX_INVOICE_RECEIPT: "TIR",
    RECEIPT: "RE",
    CREDIT_NOTE: "CN",
    DEBIT_NOTE: "DN",
}


def resolve_prefix(explicit, company_prefix, default: str) -> str:
    """前缀优先级：本次显式 > 公司资料的 doc_prefix > 类型默认。"""
    return (explicit or "").strip() or (company_prefix or "").strip() or default


def period_key(reset: str, on: date) -> str:
    """号码桶键 —— 决定什么时候归 1。"""
    if reset == RESET_MONTHLY:
        return on.strftime("%Y-%m")
    if reset == RESET_NEVER:
        return "ALL"
    return on.strftime("%Y")


def format_number(prefix: str, reset: str, on: date, number: int, width: int = 5) -> str:
    """展示号：前缀 + 期间 + 流水。"""
    sequence = f"{number:0{width}d}"
    if reset == RESET_MONTHLY:
        return f"{prefix}{on.strftime('%Y%m')}-{sequence}"
    if reset == RESET_NEVER:
        return f"{prefix}-{sequence}"
    return f"{prefix}{on.strftime('%Y')}-{sequence}"


def fiscal_year_start(company_month) -> int:
    """公司资料的财年起始月；缺省 1 月（泰国多数按 1 月）。"""
    try:
        month = int(company_month)
    except (TypeError, ValueError):
        return 1
    return month if 1 <= month <= 12 else 1


# ── 开出前的闸 ──────────────────────────────────────────────────────────────
#
# 都在**取号之前**跑：不过就不占连号 —— 占了号再退回就是跳号，税局那边要解释。

# 收据/合并单是"钱已收"的凭证：没款不开。
REQUIRE_PAYMENT = (RECEIPT, TAX_INVOICE_RECEIPT)
# 面额 ≤ 0 的税票/收据不开：那要么是某行没设价（把"没设价"当 0 发过来了），要么走错了单据。
# 报价单不在内 —— 它不是税务凭证，฿0 的报价顶多是没写完。
REQUIRE_AMOUNT = (TAX_INVOICE, TAX_INVOICE_SIMPLE, TAX_INVOICE_RECEIPT, RECEIPT)
PAYMENT_STATUSES = ("unpaid", "partial", "paid")


def payment_gate(row: dict) -> str | None:
    """收据/合并单开出时必须已收款（状态非 unpaid，且方式与日期齐）。"""
    if row.get("doc_kind") not in REQUIRE_PAYMENT:
        return None
    if (row.get("payment_status") or "unpaid") == "unpaid":
        return "payment_required"
    if not row.get("payment_method") or not row.get("payment_date"):
        return "payment_required"
    return None


def amount_gate(row: dict) -> str | None:
    """合计 ≤ 0 的税票/收据不许开出。合计读不出来也拦住。"""
    if row.get("doc_kind") not in REQUIRE_AMOUNT:
        return None
    try:
        total = Decimal(str(row.get("grand_total") or 0))
    except (ArithmeticError, ValueError):
        return "zero_amount"
    return None if total > 0 else "zero_amount"


def buyer_gate(row: dict) -> list[str]:
    """开出税票前买方还缺什么。非"完整买方"的单据不查。"""
    if row.get("doc_kind") not in NEEDS_FULL_BUYER:
        return []
    return missing_for_tax_invoice(row.get("buyer") or {})


def issue_gates(row: dict) -> list[str]:
    """开出前的全部拒因（空 = 可以开）。顺序固定，方便调用方按第一个拒因回话。"""
    reasons = []
    if buyer := buyer_gate(row):
        reasons.append("buyer_incomplete:" + ",".join(buyer))
    for reason in (amount_gate(row), payment_gate(row)):
        if reason:
            reasons.append(reason)
    return reasons

# ── 状态 × 动作 ─────────────────────────────────────────────────────────────
#
# 这张表就是"什么状态能做什么"，服务端按它拒绝，前端按它显示按钮（前端不自己算）。
# 开出（issued）是分水岭：进了销项就不能改票面，只能作废或红冲。

EDIT = "edit"
DELETE = "delete"
SUBMIT = "submit"
APPROVE = "approve"
REJECT = "reject"
ISSUE = "issue"
VOID_ACTION = "void"
CREDIT = "credit_note"
DEBIT = "debit_note"
CONVERT = "convert"
PDF = "pdf"

ACTIONS_BY_STATUS = {
    DRAFT: (EDIT, DELETE, SUBMIT, ISSUE, PDF, CONVERT),
    PENDING: (EDIT, APPROVE, REJECT, ISSUE, PDF),
    ISSUED: (VOID_ACTION, CREDIT, DEBIT, PDF),
    VOID: (PDF,),
}


def allowed_actions(status: str) -> tuple:
    """该状态允许的动作。未知状态一律空（fail-closed，不靠前端记）。"""
    return ACTIONS_BY_STATUS.get(status, ())


def can(status: str, action: str) -> bool:
    return action in allowed_actions(status)


# ── 红冲 / 补开 ─────────────────────────────────────────────────────────────

# 调减（退货/折让/开错调低）→ 贷项（红冲）；调增（补收/开错调高）→ 借项（补开）。
# 两者都引用一张**已开出**的原单，自身也是正式单据、走独立连号。
NOTE_TYPES = (CREDIT_NOTE, DEBIT_NOTE)
NOTE_PREFIXES = {CREDIT_NOTE: "CN", DEBIT_NOTE: "DN"}


def note_gate(original: dict) -> str | None:
    """能不能对这张原单出冲正单。原单不存在 / 不是已开出，都不行。"""
    if not original:
        return "original_not_found"
    if original.get("status") != ISSUED:
        return "original_not_issued"
    return None


def note_prefix(note_kind: str) -> str:
    return NOTE_PREFIXES.get(note_kind, "")


# ── 报价 → 开票 ─────────────────────────────────────────────────────────────

CONVERT_TARGETS = (TAX_INVOICE, TAX_INVOICE_RECEIPT, RECEIPT)


def convert_gate(original: dict, target_doc_kind: str) -> str | None:
    """能不能把这张单转成目标类型。报价不占发票号：转出的新单是草稿，开出时才取号。"""
    if target_doc_kind not in CONVERT_TARGETS:
        return "bad_target_type"
    if not original:
        return "original_not_found"
    if original.get("doc_kind") != QUOTATION:
        return "not_a_quotation"
    return None

# ── 销售单的来源与下游 ──────────────────────────────────────────────────────
#
# 用户 2026-09-21 对齐：销售记录的进出只有两条 —— **进**：POS 收银、上传发票识别、
# 手工录入；**出**：商品/库存/收发存报表。没有会计凭证、没有报税、没有对外推送、
# 没有账套。
#
# 所以一张销售单落定后**唯一的自动下游是出库**：开了票就是货出门。报价单与草稿
# 都不动货。

SOURCE_POS = "pos"
SOURCE_OCR = "ocr"
SOURCE_MANUAL = "manual"
SOURCES = (SOURCE_POS, SOURCE_OCR, SOURCE_MANUAL)


def should_post_stock(doc_kind: str, status: str) -> bool:
    """这张单要不要过账成出库。只有**已开出**的销售凭证动货。"""
    if status != ISSUED:
        return False
    return doc_kind in (TAX_INVOICE, TAX_INVOICE_SIMPLE, TAX_INVOICE_RECEIPT, RECEIPT)

