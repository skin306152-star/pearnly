"""硬性自检（ADR-022，10-17 评测定）：决定 Haiku 的结果能不能直接用，还是交 Opus 重读。不按模型自标的「不确定」升级。"""
from decimal import Decimal, InvalidOperation


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


def voucher_problems(read, pages):
    """返回要交 Opus 的原因（空 = 直接用）。"""
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
    if pages >= 3 and len(documents) < pages:
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
