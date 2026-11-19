# 来源：旧线上 ec2e5c957e6b · services/erp/mrerp_xlsx_fmt.py（只改导入路径与模板位置）
"""MR.ERP xlsx · 字段格式工具 + 服务端校验上限常量 · mrerp_xlsx_generator 拆分 leaf.

铁律 29 字段格式(日期/字符串/金额)+ MR.ERP 服务端长度/日期/税种上限。纯函数,0 逻辑改。
"""

import hashlib
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional, Tuple

from pearnly_core.tax_documents import amounts

# ============================================================
# 铁律 29 · 字段格式工具
# ============================================================
MAX_AMOUNT = Decimal("999999999.99")

# 服务端业务校验上限(2026-05-18 集成测试发现 · 见 mrerp-known-facts.md §7)
# xlsx schema 字段标 str(30) / str(50) 但 MR.ERP 服务端实际更严
# 校验顺序:服务端先做长度校验 → 长度过 → 才查主数据存在
# adapter 必须 client-side 拦截,避免 "ไม่พบ" 类错误被长度错遮盖
MRERP_INVOICE_NO_MAX = 18
MRERP_BILL_NO_MAX = 20
MRERP_CUSTOMER_CODE_MAX = 20
MRERP_CUSTOMER_BILL_MAX = 20

# sales_credit 允许的 Pearnly tax_kind 枚举(derive_tax_kind 的返回值之一)
# wht_* 是预扣税 · 销项发票不适用 · 仅 vat_* 或 non_vat 合法
MRERP_VALID_TAX_KINDS_SC = ("vat_7", "vat_0", "vat_exempt", "non_vat")

# 日期检查阈值(单位:天)
MRERP_DATE_FUTURE_HARD_REJECT_DAYS = 30  # > today + 30d → ERR_DATE_FUTURE
MRERP_DATE_FUTURE_WARN_DAYS = 7  # > today + 7d → 警告(不拒)
MRERP_DATE_PAST_WARN_DAYS = 730  # < today - 730d → 警告(2 年前)


def fmt_date(value: Any) -> str:
    """日期 → YYYY-MM-DD 字符串(cell 用 @ 文本格式)"""
    if not value:
        return ""
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return ""
        if len(s) >= 10 and s[4] == "-" and s[7] == "-":
            return s[:10]
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d")
        except Exception:
            return s[:10]
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    return str(value)[:10]


def supplier_code_from_name(name: Any) -> str:
    """卖方名 → 确定性供应商码(无税号零售卖家 · 幂等)。autocreate 与 purchase 生成器
    共用,两侧推导必须同源否则建了对不上。md5 仅派生短码非安全用途。"""
    s = str(name or "").strip().lower()
    if not s or s == "-":
        return ""
    digest = hashlib.md5(s.encode("utf-8"), usedforsecurity=False).hexdigest()
    return ("V" + digest)[:12].upper()


def fmt_str(value: Any, max_len: int = 50) -> str:
    """字符串 trim 到 max_len · None / NaN 返回空串"""
    if value is None:
        return ""
    s = str(value).strip()
    if not s or s.lower() in ("none", "null", "nan"):
        return ""
    if len(s) > max_len:
        return s[:max_len]
    return s


def fmt_number(value: Any) -> Optional[float]:
    """金额 → float · 超上限或非法返回 None · 负数静默 clamp 到 -MAX_AMOUNT
    (保留原行为:用于摘要/展示场景 · 不抛错)"""
    if value is None or value == "":
        return None
    # 识别读出的写法（「1,234.50」「฿100」「3%」…）按共用 `amounts.number` 读；读不懂回 None（调用方不得当 0，主会话 11-19）
    n = amounts.number(value)
    if n is None:
        return None
    if n > MAX_AMOUNT:
        return float(MAX_AMOUNT)
    if n < -MAX_AMOUNT:
        return float(-MAX_AMOUNT)
    return float(n)


def history_number(history: Any, *keys: str) -> Optional[float]:
    """按键序取金额:顶层优先,history['fields'] 兜底。

    真实推送流的 history 经 flatten_history_for_mrerp 产出:顶层只有 total_amount 等
    SELECT 列,OCR 的 vat/subtotal 只在 fields 嵌套里。闸/生成器读金额必须两层都看,
    只读顶层会对真实单据静默空转(2026-07-02 复测 BUG4 根因)。"""
    if not isinstance(history, dict):
        return None
    fields = history.get("fields")
    fields = fields if isinstance(fields, dict) else {}
    for key in keys:
        for src in (history, fields):
            n = fmt_number(src.get(key))
            if n is not None:
                return n
    return None


def _field_value(history: Any, *keys: str) -> Any:
    if not isinstance(history, dict):
        return None
    fields = history.get("fields")
    fields = fields if isinstance(fields, dict) else {}
    for key in keys:
        for source in (history, fields):
            if key in source and source.get(key) not in (None, ""):
                return source.get(key)
    return None


def _bool_value(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().casefold()
    if text in {"1", "true", "yes", "included", "inclusive", "รวม"}:
        return True
    if text in {"0", "false", "no", "excluded", "exclusive", "แยก"}:
        return False
    return None


def _items_gross(history: Any) -> Optional[float]:
    if not isinstance(history, dict):
        return None
    fields = history.get("fields")
    fields = fields if isinstance(fields, dict) else {}
    items = history.get("items") or fields.get("items")
    if not isinstance(items, list) or not items:
        return None
    total = Decimal("0")
    for item in items:
        if not isinstance(item, dict):
            return None
        amount = line_amount(item)
        if amount is None:
            qty = fmt_number(item.get("qty") or item.get("quantity"))
            price = fmt_number(item.get("unit_price") or item.get("price"))
            if qty is None or price is None:
                return None
            amount = qty * price
        total += Decimal(str(amount))
    return float(total)


LINE_AMOUNT_KEYS = ("amount", "subtotal", "total", "line_total")


def line_amount(item: Any) -> Optional[float]:
    """一行明细的金额（印在票上的那一格）：导入表与 ERP 载荷记 ``amount``，Cowork 识别记 ``subtotal``（同 Express 的读法，
    `express_rules.push.common` 明细行），再兜底 ``total`` / ``line_total``；都没有回 None，由调用方按「数量 × 单价」补。
    11-14 TEST2019 真写：只认 ``amount`` 时 Cowork 有行折扣的票被改成「数量 × 单价」（票 288.90 记成 321.00）。"""
    if not isinstance(item, dict):
        return None
    for key in LINE_AMOUNT_KEYS:
        found = fmt_number(item.get(key))
        if found is not None:
            return found
    return None


# 税率种类 → MR.ERP 税率表（`tbl_othvatrate`：名称、税率、价内外 vatrateinex 1 价外 / 2 价内、throwacvat=1 不进税报告）里的哪一行。
# 名称按这家公司读到的税率表取（`defaults.AccountDefaults.header` 读单据接口 `txtothvatrate` 放进 ``_mrerp_vat_rates``，导入按名称
# 去空格匹配，`imp*/component/importpc.php`）；没读到时用 MR.ERP 初始库的名称（stderp2020.sql:3100-3103）。
EXCLUSIVE, INCLUSIVE, ZERO, OUTSIDE = "exclusive", "inclusive", "zero", "outside"
STARTER_VAT_RATES = {ZERO: "0", EXCLUSIVE: "7 (แยก)", INCLUSIVE: "7 (รวม)", OUTSIDE: "นอกระบบ"}


def vat_rates(rows: Any) -> Dict[str, str]:
    """单据接口的 ``txtothvatrate`` 行 ``[id, 名称, 税率, 价内外, 不进税报告]`` → {种类: 名称}；认不出的种类不出现。"""
    found: Dict[str, str] = {}
    for row in rows or []:
        if not isinstance(row, list) or len(row) < 5 or not str(row[1] or "").strip():
            continue
        name, rate, inex, outside = str(row[1]).strip(), _decimal(row[2]), str(row[3] or ""), str(row[4] or "") == "1"
        kind = (OUTSIDE if outside else ZERO if rate == 0 and not inex
                else EXCLUSIVE if rate == 7 and inex == "1" else INCLUSIVE if rate == 7 and inex == "2" else "")
        if kind:
            found.setdefault(kind, name)
    return found


def vat_label(kind: str, mappings: Any = None) -> str:
    rates = (mappings or {}).get("_mrerp_vat_rates") if isinstance(mappings, dict) else None
    return (rates or {}).get(kind) or STARTER_VAT_RATES[kind]


def mrerp_tax_kind(history: Any, *, purchase: bool = False) -> str:
    """税率种类（销售与采购货品同一判定；11-14 起采购货品不再写死价外——无税卖家的票被多加 7%，票 500.00 记成 535.00）：
    人在复核里标过价内 / 价外照它；否则拿明细合计比总额 / 小计。税额≈0：销售是零税率 ``zero``；采购是无税卖家开的票、不是税务
    发票，按 MR.ERP 的「นอกระบบ」不进进项税报告 ``outside``（主会话 11-14；同 Express 税额 0 不写 ISVAT、不进 ภ.พ.30）。"""
    vat = history_number(history, "vat", "vat_amount") or 0
    if abs(vat) < 0.005:
        return OUTSIDE if purchase else ZERO

    explicit = _bool_value(
        _field_value(history, "price_includes_vat", "vat_included", "tax_inclusive")
    )
    if explicit is not None:
        return INCLUSIVE if explicit else EXCLUSIVE

    gross = _items_gross(history)
    total = history_number(history, "total_amount", "grand_total", "net_payable")
    subtotal = history_number(history, "subtotal", "amount_before_tax")
    discount = history_number(history, "discount", "discount_total") or 0
    if gross is not None:
        after_discount = gross - discount
        if total is not None and abs(after_discount - total) <= 0.10:
            return INCLUSIVE
        if subtotal is not None and abs(after_discount - subtotal) <= 0.10:
            return EXCLUSIVE
    return EXCLUSIVE


def printed_invoice_no(history: Any) -> str:
    """票面发票号（Cowork 识别 / ERP 单号），原样不截。"""
    fields = history.get("fields") if isinstance(history, dict) and isinstance(history.get("fields"), dict) else {}
    for value in (history.get("invoice_number"), history.get("invoice_no"), fields.get("invoice_number")):
        if str(value or "").strip():
            return str(value).strip()
    return ""


def mrerp_bill_no(history: Any, docnum: str, mappings: Any = None) -> str:
    """导入第 11 列「เลขที่บิล」= MR.ERP 的税务发票号（单据 vatnum、销项 / 进项税记录 svatno / pvatno，税报告「ใบกำกับภาษี
    เลขที่」取它，`impartran/component/importpc.php:417-424, 868, 978`）：票面发票号原样；票面没有时同 MR.ERP 网页不填的默认 =
    这次所选菜单的前缀 + 单号（``docnum`` = 第 1 列，前缀 ``_mrerp_menu_prefix`` 由准备时读那个菜单的新单表单定）。超过 20 字
    MR.ERP 拒收，不截，由预检拦下（`MRERP_BILL_NO_MAX`）。"""
    printed = printed_invoice_no(history)
    if printed:
        return printed
    prefix = str((mappings or {}).get("_mrerp_menu_prefix") or "") if isinstance(mappings, dict) else ""
    return prefix + str(docnum or "")


def mrerp_tax_label(history: Any, mappings: Any = None, *, purchase: bool = False) -> str:
    """导入表与网页表单「อัตราภาษี」写的名称：种类（`mrerp_tax_kind`）按这家公司的税率表取名。"""
    return vat_label(mrerp_tax_kind(history, purchase=purchase), mappings)


mrerp_sales_tax_label = mrerp_tax_label  # 旧名（销售先有这个判定）；新代码用 mrerp_tax_label


def _decimal(value: Any) -> Decimal:
    """空 = 0；写法按共用 `amounts.number`；读不懂的**报错**、不当 0（主会话 11-19：导入表的行金额、折扣读不懂时静默变 0，
    推出去的单金额就错了——停下比写错好）。"""
    if value in (None, ""):
        return Decimal("0")
    found = amounts.number(value)
    if found is None:
        raise ValueError(f"amount not parseable: {value!r}")
    return found


def allocate_discount(history: Any, rows: List[Dict[str, Any]], tax_kind: str) -> Tuple[Decimal, List[Dict[str, Any]]]:
    """整单折扣怎么进导入表（三种带明细的单同一做法：赊销、现销、采购货品）→ (表头「หักส่วนลด」, 明细行)。

    MR.ERP 导入的明细金额与数量、单价无关：对得上账（明细合计 − 票面折扣 = 税前或含税合计）时把折扣按行金额比例摊进各行
    （分到分、尾差进最后一行，印的单价不动），表头折扣写 0——现销导入表头折扣与已扣过的收款额同时给会被拒（原
    `sales_cash._cash_import_details`）。对不上时行照印的、折扣照写表头，交 MR.ERP 自己算，写后读回金额核对（`readback`）。
    11-14 TEST2019 真写：赊销表头折扣写死 0，票 963.00 记成 1,070.00。"""
    discount = _decimal(history_number(history, "discount", "discount_total"))
    if discount <= 0 or not rows:
        return max(discount, Decimal("0")), rows
    target = history_number(history, "total_amount", "grand_total", "net_payable")
    if tax_kind == EXCLUSIVE:
        target = history_number(history, "subtotal", "amount_before_tax")
    if target is None:
        return discount, rows
    target_amount = _decimal(target)
    line_amounts = [_decimal(row.get("amount")) for row in rows]
    gross = sum(line_amounts, Decimal("0"))
    if target_amount < 0 or gross <= 0 or abs((gross - target_amount) - discount) > Decimal("0.10"):
        return discount, rows
    positive = [index for index, amount in enumerate(line_amounts) if amount > 0]
    if not positive:
        return discount, rows
    adjusted = [dict(row) for row in rows]
    allocated = Decimal("0")
    cent = Decimal("0.01")
    for index in positive[:-1]:
        net = (line_amounts[index] * target_amount / gross).quantize(cent, rounding=ROUND_HALF_UP)
        adjusted[index]["amount"] = net
        allocated += net
    adjusted[positive[-1]]["amount"] = target_amount - allocated
    return Decimal("0"), adjusted


def fmt_number_strict(value: Any) -> float:
    """金额严格模式 → float · 负数 / 超 MAX_AMOUNT / 非法都 raise ValueError
    用于 sales_credit 上传前 preflight · 销项发票净额必须 > 0
    (P1-A §3.3 · 2026-05-18)"""
    if value is None or value == "":
        raise ValueError("amount is missing")
    try:
        n = Decimal(str(value))
    except Exception as e:
        raise ValueError(f"amount not parseable: {value!r}") from e
    if n < 0:
        raise ValueError(
            f"negative amount {n} not allowed for sales_credit upload "
            "(use a credit-note flow instead)"
        )
    if n > MAX_AMOUNT:
        raise ValueError(
            f"amount {n} exceeds MR.ERP ceiling {MAX_AMOUNT}; " "split the invoice or escalate"
        )
    return float(n)
