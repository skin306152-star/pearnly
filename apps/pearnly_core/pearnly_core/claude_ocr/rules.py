"""硬性自检（ADR-022，10-17 评测定）：决定 Haiku 的结果能不能直接用，还是交 Opus 重读。不按模型自标的「不确定」升级。"""
from decimal import Decimal, InvalidOperation

from pearnly_core.tax_documents import printed


def digits(value):
    return "".join(c for c in str(value or "") if c.isdigit())


def thai_id_ok(value):
    """泰国税号 / 身份证号的校验位。"""
    d = digits(value)
    if len(d) != 13:
        return False
    return (11 - sum(int(d[i]) * (13 - i) for i in range(12)) % 11) % 10 == int(d[12])


def money(value):
    try:
        return Decimal(str(value).replace(",", "")) if value not in (None, "") else None
    except InvalidOperation:
        return None


def fix_own_tax(read, own_tax):
    """读出的税号与本公司税号只差 1–2 位就改成本公司的（本公司税号已知，读错一两位是看错不是另一家）。返回改了几处。"""
    own = digits(own_tax)
    if len(own) != 13 or own == "0" * 13 or not read:
        return 0
    fixed = 0
    for doc in read.get("documents") or []:
        for field in ("seller_tax", "buyer_tax"):
            value = digits(doc.get(field))
            if len(value) == 13 and value != own and sum(a != b for a, b in zip(value, own)) <= 2:
                doc[field] = own
                fixed += 1
    return fixed


def _taxes(doc):
    """这张单读出的 13 位税号（卖方、买方，读出几个算几个）。"""
    return [tax for tax in (digits(doc.get(field)) for field in ("seller_tax", "buyer_tax")) if len(tax) == 13]


def copy_page(text, documents, number, doc_kind=None):
    """第 ``number`` 页（从 1 数，这一页没有单据起始）是前面某张已读单的再印：副本（สำเนา）、随税票的收据联
    （ใบเสร็จรับเงิน / ใบแนบใบกำกับภาษี）。这一页自己的 PDF 文字层里要找得到那张单的单号（完整的号、至少 4 个字母数字）、
    含税额（完整的数、不为 0）和它读出的每个 13 位税号（至少一个），且不是减项 / 增项通知单（除非那张单本身是减项单）。
    与 Cowork 自动推前核对的 ``push.express.automation.same_document_page`` 同口径、同一组函数（``tax_documents.printed``）；
    那边核对方税号，这里不知道进销方向，读出的税号都要在（只会更严）。扫描件、隐形识别文字层（空串）、对不上的一律不算。"""
    text = str(text or "")
    if not text.strip():
        return False
    for doc in documents:
        total, taxes = money(doc.get("total")), _taxes(doc)
        if not (type(doc.get("page")) is int and doc["page"] < number) or total is None or total == 0 or not taxes:
            continue
        if not printed.number_in_text(doc.get("invoice_number"), text) or not printed.amount_in_text(total, text):
            continue
        if not all(printed.tax_in_text(tax, text) for tax in taxes):
            continue
        if printed.adjustment_note(text) and doc_kind != "credit_note":
            continue
        return True
    return False


def copies_only(read, pages, page_texts):
    """读出的单比页数少，是因为没有单据起始的页**全都**是前面已读单的再印（:func:`copy_page`）。``page_texts`` 要与页一一对应
    （``pages.page_texts``），给不出、对不上、有单据没记起始页的一律不算。"""
    documents = read.get("documents") or []
    if not documents or not isinstance(page_texts, (list, tuple)) or len(page_texts) != pages:
        return False
    starts = set()
    for doc in documents:
        page = doc.get("page")
        if type(page) is not int or not 1 <= page <= pages:
            return False
        starts.add(page)
    rest = [number for number in range(1, pages + 1) if number not in starts]
    return bool(rest) and all(copy_page(page_texts[number - 1], documents, number, read.get("doc_kind")) for number in rest)


def voucher_problems(read, pages, page_texts=None):
    """返回要交 Opus 的原因（空 = 直接用）。``page_texts``：每页的 PDF 文字层（``pages.page_texts``；不给 = 不看文字层，同以前）。"""
    if not read:
        return ["no_result"]
    if read.get("entry") != "enter":
        return []
    out = []
    documents = read.get("documents") or []
    for i, doc in enumerate(documents):
        for field in ("invoice_number", "date", "total"):
            if not doc.get(field):
                out.append(f"documents[{i}].{field} missing")
        for field in ("seller_tax", "buyer_tax"):
            if doc.get(field) and not thai_id_ok(doc[field]):
                out.append(f"documents[{i}].{field} fails the Thai tax ID check digit")
        sub, vat, total = money(doc.get("subtotal")), money(doc.get("vat")), money(doc.get("total"))
        if sub is not None and vat is not None and total is not None and abs(sub + vat - total) > Decimal("0.05") and sub != total:
            out.append(f"documents[{i}] subtotal + vat != total")  # 含税小票印的 Subtotal 就是含税合计，算正常
    # 电子税票常是「正本 + 副本 / 收据联」几页一份：后面几页都在文字层里重复了前面的单，就不是漏读（E11，10-20）
    if pages >= 3 and len(documents) < pages and not copies_only(read, pages, page_texts):
        out.append(f"{pages} pages but only {len(documents)} documents were read; check every page for further invoices")
    return out


def id_card_problems(read):
    if not read:
        return ["no_result"]
    if not read.get("is_id_card"):
        return []
    out = []
    if not thai_id_ok(read.get("people_id")):
        out.append("people_id fails the Thai ID check digit")
    for field in ("first_name_th", "last_name_th", "date_of_birth_be"):
        if not read.get(field):
            out.append(f"{field} missing")
    return out


# ── 对账三类文档（一次一页）：读出来的数自己对不上就交 Opus。跨页的核对（上一页结余接下一页）由产品拼页后照原样做。
_CENT = Decimal("0.01")


def _either(row, a, b):
    return money(row.get(a)) or Decimal(0), money(row.get(b)) or Decimal(0)


def _amount_problems(rows, a, b, names):
    out = []
    for i, row in enumerate(rows):
        first, second = money(row.get(a)), money(row.get(b))
        if first and second:
            out.append(f"entries[{i}] has both {a} and {b}")
        amount, printed = money(row.get("amount")), first or second
        if amount is not None and printed is not None and abs(amount - printed) > _CENT:
            out.append(f"entries[{i}].amount differs from the {a}/{b} column")
        direction = row.get("direction")
        if printed and direction and direction != names[0 if first else 1]:
            out.append(f"entries[{i}].direction does not match its column")
    return out


def _bank_breaks(rows, opening):
    """按印的先后（旧在前）核余额链：上一次印的余额 + 其间存入 − 取出 = 这次印的余额。返回断开的行号。"""
    breaks, previous, pending = [], opening, Decimal(0)
    for i, row in enumerate(rows):
        deposit, withdrawal = _either(row, "deposit", "withdrawal")
        pending += deposit - withdrawal  # 有的账单一天只在最后一行印余额：中间几行的金额累加到下一次印的余额再核
        balance = money(row.get("balance"))
        if balance is None:
            continue
        if previous is not None and abs(previous + pending - balance) > _CENT:
            breaks.append(i)
        previous, pending = balance, Decimal(0)
    return breaks


def bank_problems(read):
    """一页银行账单：相邻两行都印了余额时，上一行余额 + 存入 − 取出 = 本行余额（第一行接本页的期初）。网银导出常是新的在前
    （倒序，对账出行时 ``bank_intake.chronological`` 会排回来）：两种顺序都核，取断得少的那种。"""
    if not read:
        return ["no_result"]
    rows = read.get("entries") or []
    out = _amount_problems(rows, "deposit", "withdrawal", ("deposit", "withdrawal"))
    opening = money(read.get("opening_balance"))
    forward = _bank_breaks(rows, opening)
    # 倒序时期初接在最旧的那一行（印在最后）之前：反过来核，同样从期初接起
    backward = [len(rows) - 1 - i for i in _bank_breaks(list(reversed(rows)), opening)] if forward else []
    out += [f"entries[{i}].balance does not follow from the previous balance and the amounts since"
            for i in (sorted(backward) if forward and len(backward) < len(forward) else forward)]
    closing = money(read.get("closing_balance"))
    last = next((money(r.get("balance")) for r in reversed(rows) if money(r.get("balance")) is not None), None)
    if closing is not None and last is not None and abs(closing - last) > _CENT:
        out.append("closing_balance differs from the last row's balance")
    return out


def gl_problems(read):
    """一页总账：余额按借减贷（资产）或贷减借（负债 / 收入）走，同一页方向一致；按多数行定方向，对不上的行交 Opus。"""
    if not read:
        return ["no_result"]
    rows = read.get("entries") or []
    out = _amount_problems(rows, "debit", "credit", ("debit", "credit"))
    steps, previous, pending = [], money(read.get("opening_balance")), Decimal(0)
    for i, row in enumerate(rows):
        debit, credit = _either(row, "debit", "credit")
        pending += debit - credit
        balance = money(row.get("balance"))
        if balance is None:
            continue
        if previous is not None:
            steps.append((i, balance - previous, pending))
        previous, pending = balance, Decimal(0)
    plus = sum(1 for _i, moved, net in steps if abs(moved - net) <= _CENT)
    minus = sum(1 for _i, moved, net in steps if abs(moved + net) <= _CENT)
    sign = 1 if plus >= minus else -1
    out += [f"entries[{i}].balance does not follow from the previous balance and this row's debit/credit"
            for i, moved, net in steps if abs(moved - sign * net) > _CENT]
    return out


def vat_report_problems(read):
    """一页税报：每行未税额 + 税额 = 合计（印了合计的行）；买方税号 13 位且校验位对。"""
    if not read:
        return ["no_result"]
    out = []
    for i, row in enumerate(read.get("entries") or []):
        sub, vat, total = money(row.get("subtotal")), money(row.get("vat")), money(row.get("total"))
        if sub is not None and vat is not None and total is not None and abs(sub + vat - total) > Decimal("0.05"):
            out.append(f"entries[{i}] subtotal + vat != total")
        tax = digits(row.get("customer_tax"))
        if tax and tax != "0" * 13 and not thai_id_ok(tax):
            out.append(f"entries[{i}].customer_tax fails the Thai tax ID check digit")
    if read.get("seller_tax") and digits(read["seller_tax"]) != "0" * 13 and not thai_id_ok(read["seller_tax"]):
        out.append("seller_tax fails the Thai tax ID check digit")
    return out


PROBLEMS = {"id_card": id_card_problems, "bank": bank_problems, "gl": gl_problems, "vat_report": vat_report_problems}
