"""一张票「需要人看一眼」的原因（2026-09-28 Express 压测：错的 101 张只标出 16 张）。

原来的「识别完整度」（`contracts/history.list_status`，与旧线上一致）只看字段填没填，不看对不对；识别时做过的
客观检查只留在页上，列表和复核页都不显示。这里按票汇总成一份原因清单（服务端单一来源），随每次写入重算，
存进记录的 `review_reasons`，列表与复核页都读它。

两类原因：
- **按当前票面重算的**（改对了自然消失）：税号位数 / 校验位、本方税号与所选账套不符、金额算不平、明细合计对不上、
  进项单号像会计盖的 Express 凭证号、单号写法与这个对方以前的票不同；
- **识别时留下的**（人看过才算数）：单号两种读法、可能漏识别、这一页没读出、识别不确定、疑似重复。
  复核页上点选（单号选哪一种）或点掉之后记进票面 `review_resolved`，推送成功后也不再算。

只给提示，不拦保存和提交（没有确认步骤，用户 2026-09-26）。规则不针对任何公司、商品或单据。

2026-10-02 起录入口不再显示这些原因（用户：识别后的提示容易漏，只提升识别质量、把结果显示出来）：照样算、照样存，
作内部质量信号；有确定答案的在入库时直接改（`repair_tax_ids`、`settle_number`），不交人选。
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from pearnly_core.tax_documents.amounts import valid_tax_id

from pearnly_cowork.contracts.history import primary_page_fields
from pearnly_cowork.contracts.line_items import review_status
from pearnly_cowork.ocr.validation.accounting import number_alternative

RESOLVED = "review_resolved"
# 人看过才算数的原因（点选 / 点掉后记进 `review_resolved`）。
ACKNOWLEDGED = frozenset({"number_choice", "number_unseen", "missed_invoice", "page_unread", "uncertain", "duplicate"})
_AMOUNT_TOLERANCE = Decimal("1.00")
# Express 凭证号：两位单别 + 佛历年月日 + '-' + 三位序号（小助手 `dbf_writer._next_docnum` 同一格式），
# 会计记账后盖在原件上，识别常把它当成票号。
_VOUCHER = re.compile(r"^[A-Z]{2}(\d{2})(\d{2})(\d{2})-\d{3}$")


def _money(value):
    if value in (None, ""):
        return None
    try:
        amount = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    return amount if amount.is_finite() else None


def _digits(value) -> str:
    return re.sub(r"\D", "", str(value or ""))


def shape(number) -> str:
    """单号的写法：数字都当成 9，字母与分隔符原样（IV69/06-017 → IV99/99-999）。跨年只换数字，写法不变。"""
    return re.sub(r"\d", "9", str(number or "").strip().upper())


def _tax_reason(field, value):
    digits = _digits(value)
    if not digits:
        return None
    if len(digits) == 13:
        return None if valid_tax_id(digits) else {"code": "tax_checksum", "field": field}
    reason = {"code": "tax_length", "field": field}
    # 掉了开头的 0（压测 8 处）：补上后校验位对才给建议，不自动改。
    if len(digits) == 12 and valid_tax_id("0" + digits):
        reason["suggest"] = "0" + digits
    return reason


def history_tax(value) -> str:
    """取这家票号历史用的税号：13 位原样；掉了开头 0 的 12 位按复核建议补上（校验位对才补），其余不取（回空）。

    2026-09-28 复测：15 页销售 PDF 有 6 页本公司税号读成 12 位，查不到历史写法，单号两种读法只好都问人。"""
    digits = _digits(value)
    if len(digits) == 12 and valid_tax_id("0" + digits):
        return "0" + digits
    return digits if len(digits) == 13 else ""


def _amount_reason(fields):
    sub, vat, total = (_money(fields.get(key)) for key in ("subtotal", "vat", "total_amount"))
    if sub is None or total is None:
        return None
    discount = _money(fields.get("discount")) or Decimal(0)
    withheld = _money(fields.get("wht_amount")) or Decimal(0)
    vat = vat or Decimal(0)
    gaps = [abs(sub + vat - total), abs(sub - discount + vat - total), abs(sub - discount + vat - withheld - total)]
    return None if min(gaps) <= _AMOUNT_TOLERANCE else {"code": "amount_math"}


def _voucher_reason(fields, direction):
    """进项单号像会计盖的 Express 凭证号，而且年月日就是票面日期前后 → 提醒核对（压测 15 处）。销项不查（本方自己的号）。"""
    if direction != "purchase":
        return None
    match = _VOUCHER.match(str(fields.get("invoice_number") or "").strip().upper())
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    date = str(fields.get("date") or "")[:10]
    try:
        invoice_year, invoice_month = int(date[:4]) + 543, int(date[5:7])
    except ValueError:
        return None
    if 1 <= month <= 12 and 1 <= day <= 31 and year == invoice_year % 100 and abs(month - invoice_month) <= 1:
        return {"code": "voucher_number", "field": "invoice_number"}
    return None


def _usual_shapes(party_numbers, *, exclude=()) -> set:
    """这家以前至少两张票用过的写法。"""
    counts = {}
    for other in party_numbers or ():
        text = str(other or "").strip()
        if text and text not in exclude:
            counts[shape(text)] = counts.get(shape(text), 0) + 1
    return {value for value, count in counts.items() if count >= 2}


def _pattern_reason(fields, party_numbers):
    """同一对方以前至少两张票都是同一种写法、这张不一样 → 提醒核对（IV69 读成 IV60 写法不变不会报；
    读成 IU69、多一个前缀、少一位会报）。"""
    number = str(fields.get("invoice_number") or "").strip()
    if not number:
        return None
    shapes = {}
    for other in party_numbers or ():
        if str(other or "").strip() and str(other).strip() != number:
            shapes[shape(other)] = shapes.get(shape(other), 0) + 1
    if not shapes or shape(number) in shapes:
        return None
    usual, count = max(shapes.items(), key=lambda item: item[1])
    return {"code": "number_pattern", "field": "invoice_number", "usual": usual} if count >= 2 else None


def reasons(pages, *, direction=None, own_tax=None, party_numbers=(), duplicate=False, edited=False) -> list[dict]:
    """→ 原因清单 `[{code, field?, ...}]`，没有就是空列表。

    `direction`：进项 / 销项（票面没写时用记录的）；`own_tax`：所选账套（或公司资料）的本方税号，没有就不比；
    `party_numbers`：同一开票方（卖方）以前人核过的票号；`duplicate`：识别入库时查到疑似重复；`edited`：人改过票面。
    """
    pages = pages if isinstance(pages, list) else []
    fields = primary_page_fields(pages)
    if not fields or fields.get("is_not_invoice") in (True, "true", "True"):
        return []
    direction = fields.get("direction") or direction or "purchase"
    party, own = ("buyer", "seller") if direction == "sales" else ("seller", "buyer")
    resolved = set(fields.get(RESOLVED) or []) if isinstance(fields.get(RESOLVED), list) else set()
    found = []

    primary = next((page for page in pages if (page or {}).get("fields") is fields), {}) or {}
    notes = _notes(pages)
    settled = False  # 单号两种读法已由这家历史写法裁决（转复核就是因为它，不再笼统提醒）
    for note in notes:
        choice = number_alternative(note)
        if choice and str(fields.get("invoice_number") or "").strip() == choice[0]:
            # 这家以前人核过的票号写法能裁决：模型读法对得上、原文读法对不上 → 不用再问（压测 27 张这样问多了）。
            usual = _usual_shapes(party_numbers, exclude=choice)
            if usual and shape(choice[0]) in usual and shape(choice[1]) not in usual:
                settled = True
                break
            found.append({"code": "number_choice", "field": "invoice_number", "choices": list(choice)})
            break
    unseen = {note.split(":", 1)[1].strip() for note in notes if note.startswith("invoice_number_unseen:")}
    if str(fields.get("invoice_number") or "").strip() in unseen - {""} and not found:
        found.append({"code": "number_unseen", "field": "invoice_number"})
    if any(note.startswith("possible_missed_invoice") for note in notes):
        found.append({"code": "missed_invoice"})
    if any(note.startswith("page_unread") for note in notes):
        found.append({"code": "page_unread"})

    for field in (f"{party}_tax", f"{own}_tax"):
        reason = _tax_reason(field, fields.get(field))
        if reason:
            found.append(reason)
    own_digits, book = _digits(fields.get(f"{own}_tax")), _digits(own_tax)
    if len(own_digits) == 13 and valid_tax_id(own_digits) and len(book) == 13 and own_digits != book:
        found.append({"code": "own_tax", "field": f"{own}_tax"})
    for reason in (_amount_reason(fields), _voucher_reason(fields, direction),
                   _pattern_reason(fields, party_numbers)):
        if reason:
            found.append(reason)
    items = review_status(fields)
    if items:
        found.append({"code": f"items_{items}", "field": "items"})
    if duplicate:
        found.append({"code": "duplicate"})
    # 识别自己说拿不准、上面又没说出具体哪里 → 笼统提醒一次；人改过票面就不再提。
    if not found and not edited and not settled and primary.get("_needs_manual_review"):
        found.append({"code": "uncertain"})
    return [reason for reason in found if reason["code"] not in resolved]


def _notes(pages) -> list[str]:
    return [str(note) for page in pages if isinstance(page, dict)
            for note in [*(page.get("_validation_warnings") or []), *(page.get("_trigger_reasons") or [])]]


def repair_tax_ids(pages) -> list[str]:
    """读成 12 位、补上开头的 0 校验位就对的税号直接补上（原先只给复核建议，用户 2026-10-02：有确定答案的不问人）。
    就地改 `pages`，返回改过的字段。补了校验位仍不对的不动。"""
    changed = []
    for page in pages if isinstance(pages, list) else []:
        fields = (page or {}).get("fields") if isinstance(page, dict) else None
        if not isinstance(fields, dict):
            continue
        for field in ("seller_tax", "buyer_tax"):
            digits = _digits(fields.get(field))
            if len(digits) == 12 and valid_tax_id("0" + digits):
                fields[field] = "0" + digits
                changed.append(field)
    return changed


def settle_number(pages, *, direction=None, party_numbers=()) -> str | None:
    """单号有确定答案时直接定下来（用户 2026-10-02：不再交人选），就地改主页面，返回新单号；没改回 None。

    - 两种读法（`invoice_number_alternative`）：这家以前人核过的票号写法只认原文读法 → 换成原文读法；
      认模型读法、或没有历史 → 保留识别的主值。
    - 进项单号像 Express 凭证号（`_voucher_reason`）：识别时另有一个候选号（两种读法里的另一个）就换成它，没有就保留。
    """
    pages = pages if isinstance(pages, list) else []
    fields = primary_page_fields(pages)
    if not fields or fields.get("is_not_invoice") in (True, "true", "True"):
        return None
    current = str(fields.get("invoice_number") or "").strip()
    choices = [choice for choice in map(number_alternative, _notes(pages)) if choice and choice[0] == current]
    if choices:
        usual = _usual_shapes(party_numbers, exclude=choices[0])
        if usual and shape(choices[0][1]) in usual and shape(choices[0][0]) not in usual:
            fields["invoice_number"] = choices[0][1]
            return choices[0][1]
    if _voucher_reason(fields, fields.get("direction") or direction or "purchase"):
        others = {other for _, other in choices if other.strip() and not _VOUCHER.match(other.strip().upper())}
        if len(others) == 1:
            fields["invoice_number"] = others.pop()
            return fields["invoice_number"]
    return None


def codes(found) -> str:
    """存进记录的简表：原因码逗号相连（列表只要知道有没有、有哪些）。"""
    return ",".join(dict.fromkeys(reason["code"] for reason in found or []))


__all__ = ["ACKNOWLEDGED", "RESOLVED", "codes", "history_tax", "reasons", "repair_tax_ids", "settle_number", "shape"]
