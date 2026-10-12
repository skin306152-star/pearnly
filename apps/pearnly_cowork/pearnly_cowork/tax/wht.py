"""ภ.ง.ด.3 / ภ.ง.ด.53 草稿：从识别记录（印了预扣税的费用票、50 ทวิ）与 50 ทวิ 参考记录算出。纯函数：只用标准库与共用规则。

口径（税务局原件，核查记录见任务记录 L5 节）：
- 归哪张表：收款方 13 位税号首位是 0 → ภ.ง.ด.53（法人），但 0991 / 0992 开头（外籍个人、未分割遗产、
  คณะบุคคล / ห้างหุ้นส่วนสามัญ）按个人所得税 → ภ.ง.ด.3；其余 → ภ.ง.ด.3。会计可按收款方改一次并记住。
- 所得类型按收款方问一次并记住（`memory`，工作空间内）；收款方没记过时按识别出的分类（category）记的类型兜底；
  都没有就列进 questions，不猜。按税率推的类型只作建议。
- 申报期间按付款日：有 50 ทวิ 用凭证上的付款日；票据当场已付（现金 / 印了付款方式 / 收据、简易税票）用票据日；
  其余也先用票据日并标「付款日为推定」，会计可逐行改。
- 50 ทวิ 的付款方 / 收款方：本公司是其中一方，另一方为对方。对方是本公司费用票的卖方、或记过所得类型 → 本公司付款，
  进本表；对方是本公司销售票的买方 → 是客户扣了我们的，不进本表；都判不出 → 列出待会计确认，不计入。
- 一张票与一张 50 ทวิ 收款方相同、金额（或税额）差不超过 0.01、日期差不超过 45 天 → 同一笔付款，金额与日期取凭证。
- ใบแนบ：每页 6 名收款方，每名下最多 3 行付款明细（多出的另起一个序号）；ภ.ง.ด.3 分 ชื่อ / ชื่อสกุล，
  条件码 ภ.ง.ด.3 为 1/2/3、ภ.ง.ด.53 为 1/2。
- e-WHT：2569-01-01 至 2570-12-31 经电子预扣（e-Withholding Tax）付款的，多数所得税率为 1%，且由银行代缴、
  不进本表。票面印 1% 而该类型常用税率更高的行照常计入，但标出请会计确认。
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from pearnly_core.tax_documents import amounts as tax_amounts
from pearnly_core.tax_documents import rules as tax_rules
from pearnly_cowork.tax import periods, thai_words

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
HUNDRED = Decimal(100)
PND3, PND53 = "pnd3", "pnd53"
FORMS = (PND53, PND3)
BOTH = (PND3, PND53)
CERTIFICATE = "withholding_certificate"

# 码 → (常用税率 %, 能进的表, 适用 e-WHT 1%)。税率出自 ท.ป.4/2528 现行合并文本；只作核对，申报金额取单据上的数。
INCOME_TYPES = {
    "service": (Decimal(3), BOTH, True),
    "hire_of_work": (Decimal(3), BOTH, True),
    "professional": (Decimal(3), BOTH, True),
    "rent": (Decimal(5), BOTH, True),
    "advertising": (Decimal(2), BOTH, True),
    "promotion": (Decimal(3), BOTH, True),
    "prize": (Decimal(5), BOTH, True),
    "transport": (Decimal(1), BOTH, False),
    "insurance": (Decimal(1), (PND53,), False),
    "interest": (Decimal(1), (PND53,), False),
    "dividend": (Decimal(10), (PND53,), False),
    "commission": (Decimal(3), (PND53,), True),
    "other": (None, BOTH, False),
}
INCOME_CODES = tuple(INCOME_TYPES)
COMMON_RATES = tuple(Decimal(value) for value in ("1", "1.5", "2", "3", "5", "10", "15"))
RATE_SNAP = Decimal("0.05")
EWHT_RATE = Decimal(1)
EWHT_FROM, EWHT_TO = date(2026, 1, 1), date(2027, 12, 31)
CONDITIONS = {PND3: ("1", "2", "3"), PND53: ("1", "2")}
DEFAULT_CONDITION = "1"
LINES_PER_PAYEE = 3
PAYEES_PER_SHEET = 6
PAIR_DAYS = 45
LOOKBACK_DAYS = 90
THRESHOLD = Decimal(1000)
# 只作建议（questions 里的 suggested），不代替会计选：同一税率可能是几种所得
SUGGEST_BY_RATE = {Decimal(5): "rent", Decimal(2): "advertising", Decimal(1): "transport", Decimal(3): "service",
                   Decimal(10): "dividend"}

ISSUES = {
    "payee_tax_missing": "error",
    "payee_tax_invalid": "warn",
    "income_type_missing": "warn",
    "rate_mismatch": "warn",
    "rate_not_standard": "warn",
    "ewht_check": "warn",
    "below_threshold": "info",
    "condition_invalid": "warn",
    "form_type_mismatch": "warn",
    "form_name_mismatch": "info",
    "amount_unreadable": "error",
    "payment_date_assumed": "info",
    "certificate_direction_unclear": "warn",
    "certificate_received": "info",
    "certificate_parties_unreadable": "warn",
    "duplicate_document": "info",
}
# 这几项在会计改正之前不能计入（改了 include 也不行）
BLOCKING = ("payee_tax_missing", "amount_unreadable")
EDIT_KEYS = ("include", "payment_date", "income_type", "income_text", "rate", "base", "tax", "condition",
             "payee_tax_id", "form")


def money(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    return number.quantize(CENT, rounding=ROUND_HALF_UP) if number.is_finite() else None


def tax_id(value) -> str:
    """数字形式的税号（读丢开头 0 的 12 位按共用规则补回）；空回空串。不判校验位。"""
    return tax_amounts.digits(tax_amounts.repaired_tax_id(str(value or "").strip()))


def valid_tax_id(value) -> bool:
    digits = tax_id(value)
    return len(digits) == 13 and tax_amounts.valid_tax_id(digits)


def form_for(payee_tax_id, override="") -> str:
    """pnd53 / pnd3；没有 13 位税号回空串（不能申报）。"""
    if override in FORMS:
        return override
    digits = tax_id(payee_tax_id)
    if len(digits) != 13:
        return ""
    if digits.startswith("0") and not digits.startswith(("0991", "0992")):
        return PND53
    return PND3


def name_suggests_individual(name) -> bool:
    text = re.sub(r"\s+", "", str(name or ""))
    return text.startswith(thai_words.NON_JURISTIC_PREFIXES) and thai_words.JURISTIC_WORD not in text


def split_name(name) -> tuple[str, str, str]:
    """ภ.ง.ด.3 的 (称谓, ชื่อ, ชื่อสกุล)：去掉认得的称谓，第一个词是名、其余是姓；非个人整串放 ชื่อ。"""
    text = re.sub(r"\s+", " ", str(name or "")).strip()
    if not text or name_suggests_individual(text):
        return "", text, ""
    title = next((word for word in thai_words.PERSON_TITLES if text.startswith(word)), "")
    rest = text[len(title):].strip()
    first, _space, last = rest.partition(" ")
    return title, first, last.strip()


def category_key(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()[:100]


def snap_rate(rate):
    """算出的税率靠近常用档（±0.05）就取那一档。"""
    if rate is None:
        return None
    for common in COMMON_RATES:
        if abs(rate - common) <= RATE_SNAP:
            return common
    return rate.quantize(CENT, rounding=ROUND_HALF_UP)


def base_of(fields: Mapping):
    """付款金额（不含 VAT）：税前小计（有整单折扣且与合计对得上时扣掉）；没有小计时用含税合计 − VAT。"""
    subtotal, discount = money(fields.get("subtotal")), money(fields.get("discount")) or ZERO
    vat, total = money(fields.get("vat")) or ZERO, money(fields.get("total_amount"))
    if subtotal is not None and subtotal > 0:
        if discount and total is not None and abs(subtotal - discount + vat - total) <= Decimal("0.05"):
            return subtotal - discount
        if discount and (total is None or abs(subtotal + vat - total) > Decimal("0.05")):
            return subtotal - discount
        return subtotal
    if total is not None and total > 0:
        return total - vat
    return None


def certificate_base(fields: Mapping):
    """50 ทวิ 的「จำนวนเงินที่จ่าย」：识别落在小计或合计里。"""
    for key in ("subtotal", "total_amount"):
        value = money(fields.get(key))
        if value is not None and value > 0:
            return value
    return None


def withheld(fields: Mapping, base) -> tuple:
    """(税率 %, 税额)：优先票面印的税额，只印税率时按基数算；都没有回 (None, None)。"""
    amount, rate = money(fields.get("wht_amount")), money(fields.get("wht_rate"))
    if amount is not None and amount > 0:
        if rate is None or rate <= 0:
            rate = snap_rate(amount * HUNDRED / base) if base else None
        return rate, amount
    if rate is not None and rate > 0 and base:
        return rate, (base * rate / HUNDRED).quantize(CENT, rounding=ROUND_HALF_UP)
    return None, None


def paid_at_issue(fields: Mapping) -> bool:
    if str(fields.get("posting_payment_manual") or "") == "cash" or str(fields.get("payment_method") or "").strip():
        return True
    return tax_rules.paid_hint(fields.get("document_type")) is True


def _issue(code, **detail) -> dict:
    out = {"code": code, "level": ISSUES[code]}
    out.update({key: value for key, value in detail.items() if value not in (None, "")})
    return out


def _party(fields, side) -> dict:
    return {"payee_tax_id": tax_id(fields.get(side + "_tax")),
            "payee_branch": str(fields.get(side + "_branch") or "").strip()[:10],
            "payee_name": str(fields.get(side + "_name") or "").strip()[:200],
            "payee_address": str(fields.get(side + "_addr") or "").strip()[:300]}


def _payment(document, fields, source, side, base, rate, tax) -> dict:
    return {"row": document["ref"], "refs": [document["ref"]], "origin": document.get("origin") or "history",
            "source": source, **_party(fields, side),
            "date": periods.parse_date(fields.get("date")), "date_source": "certificate" if source == "certificate"
            else ("paid_at_issue" if paid_at_issue(fields) else "invoice_date"),
            "base": base, "rate": rate, "tax": tax, "invoice_number": str(fields.get("invoice_number") or "")[:60],
            "category": str(fields.get("category") or "").strip()[:100], "include": source != "suggested",
            "issues": []}


def _invoice_key(seller, fields) -> tuple:
    number = re.sub(r"\s+", "", str(fields.get("invoice_number") or "")).upper()
    return (seller, number, money(fields.get("total_amount"))) if number else None


def _near(first, second) -> bool:
    if first is None or second is None:
        return False
    return abs(first - second) <= CENT


def _pair(invoice_rows, certificate_rows) -> list:
    used, out = set(), []
    for row in invoice_rows:
        match = None
        for position, certificate in enumerate(certificate_rows):
            if position in used or certificate["payee_tax_id"] != row["payee_tax_id"] or not row["payee_tax_id"]:
                continue
            if not (_near(row["base"], certificate["base"]) or (row["tax"] and _near(row["tax"], certificate["tax"]))):
                continue
            if row["date"] and certificate["date"] and abs((certificate["date"] - row["date"]).days) > PAIR_DAYS:
                continue
            match = position
            break
        if match is None:
            out.append(row)
            continue
        used.add(match)
        certificate = certificate_rows[match]
        merged = {**row, "refs": row["refs"] + certificate["refs"], "source": "both", "include": True,
                  "date": certificate["date"] or row["date"], "date_source": "certificate",
                  "base": certificate["base"] or row["base"], "rate": certificate["rate"] or row["rate"],
                  "tax": certificate["tax"] or row["tax"]}
        # 票据证实了是本公司付的款；凭证上没读出的金额由票据补上
        resolved = {"certificate_direction_unclear"} | (
            {"amount_unreadable"} if merged["base"] is not None and merged["tax"] else set())
        merged["issues"] = [item for item in certificate["issues"] if item["code"] not in resolved]
        out.append(merged)
    out.extend(certificate for position, certificate in enumerate(certificate_rows) if position not in used)
    return out


def collect(documents, company_tax_id, remembered_payees=()) -> tuple[list, list]:
    """识别记录 → (付款行, 文档级说明)。

    ``documents``：[{ref, origin: history|reference, fields: 主页字段}]；同一张票（卖方 + 票号 + 合计）或同一张凭证
    （对方 + 日期 + 金额）重复上传只算一次；
    ``remembered_payees``：记过所得类型的收款方税号（判 50 ทวิ 方向与列出建议补扣的依据）。"""
    company = tax_id(company_tax_id)
    remembered = {tax_id(value) for value in remembered_payees or ()}
    invoices, certificates, notes = [], [], []
    purchase_sellers, sales_buyers, seen = set(), set(), {}
    for document in documents or []:
        fields = document.get("fields") or {}
        seller, buyer = tax_id(fields.get("seller_tax")), tax_id(fields.get("buyer_tax"))
        if str(fields.get("document_flag") or "") == CERTIFICATE:
            certificates.append((document, fields, seller, buyer))
        elif buyer == company and seller != company:
            if seller:
                purchase_sellers.add(seller)
            key = _invoice_key(seller, fields)
            if key and key in seen:
                notes.append(_issue("duplicate_document", ref=document["ref"], of=seen.get(key, "")))
                continue
            if key:
                seen[key] = document["ref"]
            invoices.append((document, fields, seller))
        elif seller == company and buyer and buyer != company:
            sales_buyers.add(buyer)
    invoice_rows = []
    for document, fields, seller in invoices:
        base = base_of(fields)
        rate, tax = withheld(fields, base)
        if tax and tax > 0:
            invoice_rows.append(_payment(document, fields, "invoice", "seller", base, rate, tax))
        elif seller in remembered and base is not None and base >= THRESHOLD:
            invoice_rows.append(_payment(document, fields, "suggested", "seller", base, None, None))
    certificate_rows, seen_certificates = [], {}
    for document, fields, seller, buyer in certificates:
        if company not in (seller, buyer):
            continue
        side = "buyer" if seller == company else "seller"
        counterparty = buyer if seller == company else seller
        if not counterparty or counterparty == company:
            notes.append(_issue("certificate_parties_unreadable", ref=document["ref"]))
            continue
        payer = counterparty in purchase_sellers or counterparty in remembered
        if counterparty in sales_buyers and not payer:
            notes.append(_issue("certificate_received", ref=document["ref"], counterparty=counterparty))
            continue
        base = certificate_base(fields)
        rate, tax = withheld(fields, base)
        row = _payment(document, fields, "certificate", side, base, rate, tax)
        key = (counterparty, row["date"], tax, base)
        if key in seen_certificates:
            notes.append(_issue("duplicate_document", ref=document["ref"], of=seen_certificates.get(key, "")))
            continue
        seen_certificates[key] = document["ref"]
        if base is None or not tax:
            row["issues"].append(_issue("amount_unreadable"))
        if not payer:
            row["include"] = False
            row["issues"].append(_issue("certificate_direction_unclear"))
        certificate_rows.append(row)
    return _pair(invoice_rows, certificate_rows), notes


def _edit_for(row, overrides: Mapping) -> dict:
    edit = {}
    for ref in row["refs"]:
        edit.update(overrides.get(ref) or {})
    return edit


def clean_edit(raw: Mapping) -> dict:
    """会计 / CC 的逐行改动 → 只留认得的键与合法值；不合法抛 ValueError("tax.edit_invalid")。"""
    edit = {}
    for key in EDIT_KEYS:
        if key not in raw or raw[key] in (None, ""):
            continue
        value = raw[key]
        if key == "include":
            if not isinstance(value, bool):
                raise ValueError("tax.edit_invalid")
        elif key == "payment_date":
            found = periods.parse_date(value)
            if not found:
                raise ValueError("tax.edit_invalid")
            value = found.isoformat()
        elif key == "income_type":
            if value not in INCOME_TYPES:
                raise ValueError("tax.edit_invalid")
        elif key in ("rate", "base", "tax"):
            number = money(value)
            if number is None or number < 0 or (key == "rate" and number > 100):
                raise ValueError("tax.edit_invalid")
            value = str(number)
        elif key == "condition":
            if str(value) not in CONDITIONS[PND3]:
                raise ValueError("tax.edit_invalid")
            value = str(value)
        elif key == "payee_tax_id":
            if len(tax_id(value)) != 13:
                raise ValueError("tax.edit_invalid")
            value = tax_id(value)
        elif key == "form":
            if value not in FORMS:
                raise ValueError("tax.edit_invalid")
        else:
            value = str(value).strip()[:140]
        edit[key] = value
    return edit


def _choice(memory: Mapping, row) -> tuple[dict, str]:
    choice = memory.get("payee:" + row["payee_tax_id"]) if row["payee_tax_id"] else None
    if choice:
        return choice, "payee"
    key = category_key(row.get("category"))
    choice = memory.get("category:" + key) if key else None
    return (choice, "category") if choice else ({}, "")


def _apply(row, edit, memory) -> dict:
    row = {**row, "issues": list(row["issues"])}
    for key in ("base", "tax", "rate"):
        if key in edit:
            row[key] = money(edit[key])
    if "rate" in edit and "tax" not in edit and row["base"] is not None:
        row["tax"] = (row["base"] * row["rate"] / HUNDRED).quantize(CENT, rounding=ROUND_HALF_UP)
    if "payment_date" in edit:
        row["date"], row["date_source"] = periods.parse_date(edit["payment_date"]), "edited"
    if "payee_tax_id" in edit:
        row["payee_tax_id"] = edit["payee_tax_id"]
    choice, found_by = _choice(memory, row)
    row["income_type"] = edit.get("income_type") or choice.get("income_type") or ""
    row["income_text"] = edit.get("income_text") or (choice.get("income_text") if not edit.get("income_type") else "") or ""
    row["type_source"] = "edited" if edit.get("income_type") else found_by
    row["form"] = form_for(row["payee_tax_id"], edit.get("form") or choice.get("form") or "")
    row["condition"] = str(edit.get("condition") or choice.get("condition") or DEFAULT_CONDITION)
    if row["source"] == "suggested" and row["income_type"] in INCOME_TYPES:
        standard = INCOME_TYPES[row["income_type"]][0]
        if row["rate"] is None and standard is not None:
            row["rate"] = standard
        if row["tax"] is None and row["rate"] is not None and row["base"] is not None:
            row["tax"] = (row["base"] * row["rate"] / HUNDRED).quantize(CENT, rounding=ROUND_HALF_UP)
    if "include" in edit:
        row["include"] = edit["include"]
    row["edited"] = sorted(edit)
    return row


def _checks(row) -> list:
    found = []
    if not row["payee_tax_id"] or len(row["payee_tax_id"]) != 13:
        found.append(_issue("payee_tax_missing"))
    elif not valid_tax_id(row["payee_tax_id"]):
        found.append(_issue("payee_tax_invalid"))
    if row["base"] is None or not row["tax"]:
        if not any(item["code"] == "amount_unreadable" for item in row["issues"]):
            found.append(_issue("amount_unreadable"))
    if row["date_source"] == "invoice_date":
        found.append(_issue("payment_date_assumed"))
    kind, rate = row["income_type"], row["rate"]
    if not kind:
        found.append(_issue("income_type_missing"))
    elif kind in INCOME_TYPES:
        standard, forms, ewht = INCOME_TYPES[kind]
        in_window = row["date"] is not None and EWHT_FROM <= row["date"] <= EWHT_TO
        if rate is not None and standard is not None:
            expected = {standard} | ({EWHT_RATE} if ewht and in_window else set())
            if rate not in expected:
                found.append(_issue("rate_mismatch", expected=str(standard)))
            elif rate == EWHT_RATE and standard > EWHT_RATE:
                found.append(_issue("ewht_check"))
        if row["form"] and row["form"] not in forms:
            found.append(_issue("form_type_mismatch"))
    if rate is not None and rate not in COMMON_RATES:
        found.append(_issue("rate_not_standard"))
    if row["base"] is not None and row["base"] < THRESHOLD:
        found.append(_issue("below_threshold"))
    if row["form"] and row["condition"] not in CONDITIONS[row["form"]]:
        found.append(_issue("condition_invalid"))
    if row["form"] == PND53 and name_suggests_individual(row["payee_name"]):
        found.append(_issue("form_name_mismatch"))
    return found


def _shown(row) -> dict:
    """JSON 能存的行：金额字符串、日期 ISO。"""
    out = {key: value for key, value in row.items() if key not in ("base", "rate", "tax", "date")}
    for key in ("base", "rate", "tax"):
        value = row.get(key)
        out[key] = "" if value is None else (str(value.normalize()) if key == "rate" else str(value))
    out["date"] = row["date"].isoformat() if row.get("date") else ""
    if row.get("form") == PND3:
        out["name_title"], out["name_first"], out["name_last"] = split_name(row["payee_name"])
    return out


def _order(row):
    return (row["payee_name"].casefold(), row["payee_tax_id"], row["payee_branch"], row["date"] or date.max,
            row["row"])


def sheets(lines) -> list:
    """ใบแนบ 分页：同一收款方（税号 + 分店）连续最多 3 行一个序号，每页 6 个序号；序号跨页连续。
    给每行写上 seq / sheet，回 [{sheet, of, entries: [{seq, lines}], base, tax}]。"""
    blocks, key, current = [], None, []
    for line in lines:
        line_key = (line["payee_tax_id"], line["payee_branch"])
        if line_key != key or len(current) == LINES_PER_PAYEE:
            if current:
                blocks.append(current)
            key, current = line_key, []
        current.append(line)
    if current:
        blocks.append(current)
    pages = []
    for start in range(0, len(blocks), PAYEES_PER_SHEET):
        entries = []
        for offset, block in enumerate(blocks[start:start + PAYEES_PER_SHEET]):
            for line in block:
                line["seq"], line["sheet"] = start + offset + 1, len(pages) + 1
            entries.append({"seq": start + offset + 1, "lines": block})
        rows = [line for entry in entries for line in entry["lines"]]
        pages.append({"sheet": len(pages) + 1, "entries": entries,
                      "base": sum((line["base"] for line in rows), ZERO),
                      "tax": sum((line["tax"] for line in rows), ZERO)})
    for page in pages:
        page["of"] = len(pages)
    return pages


def draft(payments, notes, year: int, month: int, memory=None, overrides=None) -> dict:
    """付款行 → 本月草稿：{forms: {pnd53, pnd3}, questions, review, suggestions, notes, issues_by_code, totals}。

    ``memory``：{"payee:<税号>" / "category:<分类>": {income_type, income_text, form, condition}}；
    ``overrides``：{识别记录名: 逐行改动（`clean_edit` 过的）}。"""
    memory, overrides = memory or {}, overrides or {}
    target = (year, month)
    lines, review, suggestions, questions, counted = [], [], [], {}, {}
    for payment in payments:
        row = _apply(payment, _edit_for(payment, overrides), memory)
        # 没有日期的记录本来就取不到（按票据日取数）；付款日不在本月的属于别的月
        if row["date"] is None or (row["date"].year, row["date"].month) != target:
            continue
        row["issues"] = row["issues"] + [item for item in _checks(row)
                                         if not any(old["code"] == item["code"] for old in row["issues"])]
        blocked = any(item["code"] in BLOCKING for item in row["issues"])
        if row["source"] == "suggested" and not row["include"]:
            suggestions.append(row)
            continue
        for item in row["issues"]:
            counted[item["code"]] = counted.get(item["code"], 0) + 1
        if not row["include"] or blocked or not row["form"]:
            row["include"] = False
            review.append(row)
            continue
        lines.append(row)
        if not row["income_type"]:
            question = questions.setdefault(row["payee_tax_id"], {
                "payee_tax_id": row["payee_tax_id"], "payee_name": row["payee_name"], "category": row["category"],
                "rows": 0, "base": ZERO, "tax": ZERO, "rates": set()})
            question["rows"] += 1
            question["base"] += row["base"] or ZERO
            question["tax"] += row["tax"] or ZERO
            if row["rate"] is not None:
                question["rates"].add(row["rate"])
    for note in notes or []:
        counted[note["code"]] = counted.get(note["code"], 0) + 1
    forms = {}
    for form in FORMS:
        chosen = sorted((line for line in lines if line["form"] == form), key=_order)
        pages = sheets(chosen)
        payees = {(line["payee_tax_id"], line["payee_branch"]) for line in chosen}
        forms[form] = {"payees": len(payees), "lines": [_shown(line) for line in chosen], "sheets": len(pages),
                       "sheet_totals": [{"sheet": page["sheet"], "base": str(page["base"]), "tax": str(page["tax"])}
                                        for page in pages],
                       "base": str(sum((line["base"] for line in chosen), ZERO)),
                       "tax": str(sum((line["tax"] for line in chosen), ZERO))}
    shown_questions = []
    for question in questions.values():
        rates = sorted(question.pop("rates"))
        suggested = SUGGEST_BY_RATE.get(rates[0]) if len(rates) == 1 else None
        shown_questions.append({**question, "base": str(question["base"]), "tax": str(question["tax"]),
                                "rates": [str(rate.normalize()) for rate in rates], "suggested": suggested or ""})
    return {"period": periods.text(year, month), "forms": forms, "questions": shown_questions,
            "review": [_shown(row) for row in sorted(review, key=_order)],
            "suggestions": [_shown(row) for row in sorted(suggestions, key=_order)],
            "notes": list(notes or []), "issues_by_code": counted,
            "totals": {"base": str(sum((Decimal(forms[form]["base"]) for form in FORMS), ZERO)),
                       "tax": str(sum((Decimal(forms[form]["tax"]) for form in FORMS), ZERO)),
                       "lines": len(lines), "review": len(review), "suggestions": len(suggestions)}}
