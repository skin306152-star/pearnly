"""票面自洽表（Claude 路线的升级门，2026-10-27）：发票类读法逐份核对会计恒等式，不过的交 Opus 重读。

起因：mrerp LINE 录入 LI-00029（นิวเบสท์ 税票 HS002600133）Haiku 把整箱价读成单价、两款香皂的行金额读错、含税价当未税价，
旧自检只看必填、税号校验位与「小计 + 税额 = 合计」，没有升级；产品复核（``tax_documents.amounts``）事后才标出来。

规矩（用户 2026-10-27：「要有架构性框架性的做好，不能修修又补补」「用生产的规则是不是会让质量回退」）：

- 每条检查是一个**会计恒等式**，有代码、只在它用到的字段都印了时才查、带给 Opus 的具体提示（哪一行、差多少）。不收按长相猜的启发式。
- 金额解析与容差和产品复核共用 ``tax_documents.amounts``（同一条算术只写一次）；但哪几条触发升级（``POLICY``）只属于 Claude
  路线，只按 Claude 金标回放的数据改（``scripts/claude-ocr/gate.py --replay``），不跟着产品复核或谷歌路线改。
- 改这张表的顺序：先在 ``tests/fixtures/claude-ocr/consistency-cases.json`` 加真实读法样本，再改代码，测试与回放都过了才算。

纯计算：只依赖标准库与共用层的纯契约，不导入 Frappe。
"""
from decimal import Decimal

from pearnly_core.tax_documents.amounts import RELATIVE, TOLERANCE, VAT_RATE, money

# 触发升级的检查（回放定的；不在这里的检查照样算出来记进结果，只是不升级）。``price_mode`` 不升级：数字能定，``settle`` 直接定。
POLICY = ("line_math", "lines_vs_document", "total_below_line")
CHECKS = ("line_math", "lines_vs_document", "price_mode", "total_below_line", "vat_rate")
HUNDRED = Decimal(100)


def _near(a, b, scale=None) -> bool:
    return abs(a - b) <= max(TOLERANCE, abs(scale if scale is not None else b) * RELATIVE)


def _fmt(value) -> str:
    return f"{value:.2f}"


def _line_expectations(qty, price, discount, percent):
    """这一行可能的印法：行金额 = 数量 × 单价，减行折扣（总额或每件），或打折扣百分比；单价未税、行金额含税（或反过来）也认。"""
    gross = qty * price
    nets = {gross}
    if discount:
        nets |= {gross - discount, gross - discount * qty}
    if percent:
        nets.add(gross * (HUNDRED - percent) / HUNDRED)
    return nets | {n * (1 + VAT_RATE) for n in nets} | {n / (1 + VAT_RATE) for n in nets}


def _line_math(i, lines):
    out = []
    for j, line in enumerate(lines):
        qty, price, amount = money(line.get("qty")), money(line.get("unit_price")), money(line.get("amount"))
        if qty is None or price is None or amount is None or qty <= 0 or price <= 0 or amount <= 0:
            continue  # 没印齐、赠品（0 元）或折扣行（负数）不查
        discount, percent = money(line.get("discount")) or Decimal(0), money(line.get("discount_percent")) or Decimal(0)
        if not any(_near(net, amount) for net in _line_expectations(qty, price, discount, percent)):
            shown = f"{qty} × {_fmt(price)}" + (f" − {_fmt(discount)}" if discount else "") + (f" − {percent}%" if percent else "")
            out.append(f"documents[{i}].lines[{j}] line_math: {shown} does not give the printed line amount {_fmt(amount)}; "
                       "re-read qty, unit price (the price of the unit the qty counts), discount and amount on this line")
    return out


def _sides(doc):
    sub, vat, total = money(doc.get("subtotal")), money(doc.get("vat")), money(doc.get("total"))
    discount = money(doc.get("discount")) or Decimal(0)
    exclusive = {v for v in (sub, sub + discount if sub is not None else None) if v is not None}
    inclusive = {v for v in (total, total + discount if total is not None else None) if v is not None}
    return sub, vat, total, discount, exclusive, inclusive


def _lines_vs_document(i, doc, lines):
    """各行金额之和对上未税小计（未税价票面）或含税合计（含税价票面），任一边对上就平。返回（问题，对上的是哪一边）。"""
    rows = [(money(line.get("amount")), money(line.get("unit_price"))) for line in lines]
    priced = [(amount, price) for amount, price in rows if amount is not None or price]
    if not priced or any(amount is None for amount, _price in priced):
        return [], None  # 明细没读出、或有行印了单价却没读出金额：不查（赠品行没单价没金额，不算缺）
    line_sum = sum(amount for amount, _price in priced)
    sub, _vat, total, _discount, exclusive, inclusive = _sides(doc)
    if not exclusive and not inclusive:
        return [], None
    on_ex = any(_near(line_sum, v) for v in exclusive)
    on_in = any(_near(line_sum, v) for v in inclusive)
    if on_ex or on_in:
        return [], ("both" if on_ex and on_in else "exclusive" if on_ex else "inclusive")
    printed = ", ".join(f"{k} {_fmt(v)}" for k, v in (("subtotal", sub), ("total", total)) if v is not None)
    return [f"documents[{i}] lines_vs_document: the line amounts add up to {_fmt(line_sum)} but the document prints {printed}; "
            "re-read every line (qty, unit price, discount, amount) and the totals"], None


def _price_mode(i, doc, side):
    mode = str(doc.get("price_mode") or "").strip().lower()
    vat = money(doc.get("vat"))
    if side not in ("exclusive", "inclusive") or mode not in ("exclusive", "inclusive") or not vat or vat <= 0 or mode == side:
        return []
    return [f"documents[{i}] price_mode: price_mode is \"{mode}\" but the line amounts add up to the "
            f"{'VAT-included total' if side == 'inclusive' else 'pre-VAT subtotal'}; check whether the printed prices include VAT"]


def _total_below_line(i, doc, lines):
    total, discount = money(doc.get("total")), money(doc.get("discount")) or Decimal(0)
    biggest = max((amount for amount in (money(line.get("amount")) for line in lines) if amount is not None), default=None)
    if total is None or total <= 0 or biggest is None or discount or total >= biggest - TOLERANCE:
        return []
    return [f"documents[{i}] total_below_line: the total {_fmt(total)} is less than a single line amount {_fmt(biggest)}; "
            "re-read the totals and that line"]


def _vat_rate(i, doc):
    sub, vat, total, discount, _ex, _in = _sides(doc)
    if vat is None or vat <= 0:
        return []
    bases = [b for b in (sub, sub - discount if sub is not None else None) if b is not None and b > 0]
    expected = [b * VAT_RATE for b in bases] + ([total * VAT_RATE / (1 + VAT_RATE)] if total is not None and total > 0 else [])
    if not expected or any(_near(vat, e, e) for e in expected):
        return []
    return [f"documents[{i}] vat_rate: VAT {_fmt(vat)} is not 7% of the printed amounts; re-read subtotal, discount, VAT and total"]


def findings(read):
    """全部检查的结果（不分是否升级）：``[(代码, 给 Opus 的一句话)]``。"""
    if not read or read.get("entry") != "enter":
        return []
    out = []
    for i, doc in enumerate(read.get("documents") or []):
        lines = [line for line in doc.get("lines") or [] if isinstance(line, dict)]
        out += _line_math(i, lines)
        problems, side = _lines_vs_document(i, doc, lines)
        out += problems
        out += _price_mode(i, doc, side)
        out += _total_below_line(i, doc, lines)
        out += _vat_rate(i, doc)
    return [(text.split(": ", 1)[0].rsplit(" ", 1)[-1], text) for text in out]


def problems(read, policy=POLICY):
    """要交 Opus 的那几条（按 ``policy``）。"""
    return [text for code, text in findings(read) if code in policy]


def settle(read):
    """数字唯一能定的字段由数字定，不问模型（两家模型都读不准含税 / 未税；LI-00029 Haiku 读空、Opus 读成未税）。

    目前一项：各行金额之和只对上含税合计（或只对上未税小计）、且印了税额时，``price_mode`` 就是那一边。两边都对上
    （税额为 0、小计 = 合计）或都对不上时不动。返回改了什么（记进识别结果，便于追溯）。"""
    changed = []
    if not read or read.get("entry") != "enter":
        return changed
    for i, doc in enumerate(read.get("documents") or []):
        lines = [line for line in doc.get("lines") or [] if isinstance(line, dict)]
        _problems, side = _lines_vs_document(i, doc, lines)
        vat = money(doc.get("vat"))
        if side in ("exclusive", "inclusive") and vat and vat > 0 and doc.get("price_mode") != side:
            changed.append(f"documents[{i}].price_mode {doc.get('price_mode')} -> {side}")
            doc["price_mode"] = side
    return changed
