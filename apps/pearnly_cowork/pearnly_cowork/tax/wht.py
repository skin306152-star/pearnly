"""ภ.ง.ด.3 / ภ.ง.ด.53 草稿：从识别记录（印了预扣税的费用票、50 ทวิ）与 50 ทวิ 参考记录算出。纯函数：只用标准库与共用规则。

哪条记录是 50 ทวิ 与识别存档、推送门槛用同一条规则（`contracts.document_policy.document_class`：原文标题打的类别，
或读数本身就是凭证的样子），与租户选的录不录无关：草稿只读，不看推送的选择。凭证读数里付款方、收款方落在哪一栏不假定
（真凭证常是付款公司在卖方一栏、收款方在买方一栏），本公司在哪一边，对方就在另一边。

口径（税务局原件，核查记录见任务记录 L5 节）：
- 归哪张表：收款方 13 位税号首位是 0 → ภ.ง.ด.53（法人），但 0991 / 0992 开头（外籍个人、未分割遗产、
  คณะบุคคล / ห้างหุ้นส่วนสามัญ）按个人所得税 → ภ.ง.ด.3；其余 → ภ.ง.ด.3。会计可按收款方改一次并记住。
- 所得类型按收款方问一次并记住（`memory`，工作空间内）；收款方没记过时按识别出的分类（category）记的类型兜底；
  都没有就列进 questions，不猜。按税率推的类型只作建议。
- 申报期间按付款日：有 50 ทวิ 用凭证上的付款日；票据当场已付（录入选了现金 / 印了付款方式 / 收据、简易税票，
  录入选了赊账的除外）用票据日；其余也先用票据日并标「付款日为推定」，会计可逐行改。
- 50 ทวิ 的付款方 / 收款方：本公司是其中一方，另一方为对方。对方只是本公司费用票的卖方、或记过所得类型 → 本公司付款，
  进本表；只是本公司销售票的买方 → 是客户扣了我们的，不进本表；两边都是或都判不出 → 列出待会计确认，不计入
  （与本公司的费用票配上了即认定本公司付款）。
- 同一收款方的票与 50 ทวิ 配成同一笔付款（日期差不超过 45 天；付款日只是推定的票——赊账、没当场付——凭证在票据日
  之后 90 天内也算，常见 60 天账期或凭证晚开；金额或税额每张容差 0.01）：一对一；几张票合计等于一张凭证；一张票等于
  几张凭证合计。金额与日期取凭证。取数时票与凭证都往月末后多看 90 天：票配上了以后几个月的凭证就按凭证日落月，不再按
  票据日计入本月；以后的票也一起配，别的月配给它的凭证这个月也配给它。配不上的票，前后 45 天内有同一收款方的凭证、
  或（推定付款日的）90 天内有金额相同的凭证（配上了别的票的也算）就放进待核，配不上的那些凭证也一起放进待核，
  都不进合计，会计确认后计入。
- 票面没有本公司税号、但归在本公司名下（客户档案或单一公司的工作空间）、或买方名称就是本公司名称的费用票：列出待会计
  确认，不计入（与本公司付款的 50 ทวิ 配上了即确认）。
- 读到了却不计入本月的本月单据逐张列在 ``excluded``（销售单、没有预扣、凭证不是本公司的、付款落在别的月），不悄悄丢。
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
from pearnly_cowork.contracts import document_policy
from pearnly_cowork.tax import periods, thai_words

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
HUNDRED = Decimal(100)
PND3, PND53 = "pnd3", "pnd53"
FORMS = (PND53, PND3)
BOTH = (PND3, PND53)
CERTIFICATE = document_policy.CERTIFICATE

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
CREDIT_DAYS = LOOKBACK_DAYS  # 付款日只是推定的票（赊账）：凭证在票据日之后这么多天内也能配（不超过 LOOKBACK_DAYS）
FORWARD_DAYS = CREDIT_DAYS  # 往月末后多看几天：本月的票可能以后几个月才付款开凭证（不少于 CREDIT_DAYS，否则两个月各算一次）
SUBSET_CANDIDATES = 12    # 合计配对时每笔最多看日期最近的几张
SUBSET_STEPS = 20000      # 合计配对的搜索步数上限（找不到就当配不上，进待核）
MATCH_CLIENT, MATCH_WORKSPACE = "client", "workspace"  # 票面没有本公司税号、按归属取到的记录
MATCH_NAME = "name"  # 买方税号没读出、买方名称是本公司的名称（`sources._company_names`）
UNCONFIRMED_MATCHES = (MATCH_CLIENT, MATCH_WORKSPACE, MATCH_NAME)
# 读到了、不计入本月的本月单据的原因（``excluded``），按会计最可能要问的排前
EXCLUDED_REASONS = ("paid_in_other_month", "payment_date_missing", "certificate_other_company", "nothing_withheld",
                    "not_this_company", "sales_document")
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
    "pairing_unclear": "warn",
    "buyer_unconfirmed": "warn",
    "duplicate_document": "info",
}
# 这几项在会计改正之前不能计入（改了 include 也不行）
BLOCKING = ("payee_tax_missing", "amount_unreadable")
# 这几项让行先不计入；会计逐行设 include = true 即确认，提示随之去掉
CONFIRMED_BY_INCLUDE = ("certificate_direction_unclear", "pairing_unclear", "buyer_unconfirmed")
# 取数时判的、改了金额后要按改后的值重判的
RECHECKED = ("amount_unreadable",)
# 只从行本身的记录名取的改动（合并进来的其他记录上的日期、金额改动不套到合并后的这一笔）
ROW_ONLY_KEYS = ("payment_date", "base", "tax", "rate")
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


def rate_text(value) -> str:
    """税率写成「3」「1.5」「10」：去掉尾零、不用指数（Decimal("10").normalize() 是 1E+1）。"""
    return "" if value is None else format(value.normalize(), "f")


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
    """票据日就是付款日吗：录入时人工选的现金 / 赊账优先（`contracts.push` 的人工裁决层），其次票面印的付款方式、票种。"""
    manual = str(fields.get("posting_payment_manual") or "").strip().lower()
    if manual in ("cash", "credit"):
        return manual == "cash"
    if str(fields.get("payment_method") or "").strip():
        return True
    return tax_rules.paid_hint(fields.get("document_type")) is True


def is_certificate(fields: Mapping) -> bool:
    """这条记录（主页字段、凭证页或参考记录）是 50 ทวิ：同识别存档的那条规则（`contracts.document_policy`）。"""
    return document_policy.document_class(fields) == CERTIFICATE


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


def _within(invoice_date, certificate_date) -> bool:
    if invoice_date is None or certificate_date is None:
        return True
    return abs((certificate_date - invoice_date).days) <= PAIR_DAYS


def _credit(row, certificate_date) -> bool:
    """付款日只是推定的票（赊账、没当场付）与票据日之后 CREDIT_DAYS 天内的凭证（45 天以外的那一段）。"""
    if row["date_source"] != "invoice_date" or row["date"] is None or certificate_date is None:
        return False
    return PAIR_DAYS < (certificate_date - row["date"]).days <= CREDIT_DAYS


def _reach(row, certificate) -> bool:
    """这张票与这张凭证日期上可能是同一笔付款：相差不超过 45 天，赊账票的凭证在票据日之后 90 天内也算。"""
    return _within(row["date"], certificate["date"]) or _credit(row, certificate["date"])


def _same_amount(row, certificate) -> bool:
    return _near(row["base"], certificate["base"]) or (bool(row["tax"]) and _near(row["tax"], certificate["tax"]))


def _distance(invoice_date, certificate_date) -> tuple:
    """配对时谁更近：凭证日在票据日当天或之后的优先，再按相差天数；缺日期的排最后。"""
    if invoice_date is None or certificate_date is None:
        return (2, 0)
    gap = (certificate_date - invoice_date).days
    return (0 if gap >= 0 else 1, abs(gap))


def _total(values):
    values = list(values)
    return None if not values or any(value is None for value in values) else sum(values, ZERO)


def _subset(rows, key, target) -> list:
    """rows 里至少两张、``key`` 合计等于 ``target``（每张容差 0.01）的一组，回位置；找不到或超过搜索步数回 []。
    金额从大到小深搜：加上超出目标的跳过，剩下的全加上也够不着就停。"""
    if target is None or target <= 0:
        return []
    items = sorted(((row[key], position) for position, row in enumerate(rows)
                    if row[key] is not None and row[key] > 0), key=lambda item: (-item[0], item[1]))
    rest = [ZERO] * (len(items) + 1)
    for index in range(len(items) - 1, -1, -1):
        rest[index] = rest[index + 1] + items[index][0]
    chosen, steps = [], [SUBSET_STEPS]

    def search(start, total) -> bool:
        steps[0] -= 1
        if steps[0] < 0:
            return False
        if len(chosen) >= 2 and abs(total - target) <= CENT * len(chosen):
            return True
        for index in range(start, len(items)):
            if total + rest[index] + CENT * len(items) < target:
                return False
            value = items[index][0]
            if total + value > target + CENT * (len(chosen) + 1):
                continue
            chosen.append(items[index][1])
            if search(index + 1, total + value):
                return True
            chosen.pop()
        return False

    return sorted(chosen) if search(0, ZERO) else []


def _sum_match(rows, target) -> list:
    """几张合计等于 target 的一组（先按税额，再按金额）。"""
    for key in ("tax", "base"):
        found = _subset(rows, key, target[key])
        if found:
            return found
    return []


def _merged(invoices, certificate, split=False) -> dict:
    """票与 50 ทวิ 是同一笔付款：金额、日期取凭证（凭证没读出的由票据合计补上），收款方名址缺的用票据补。
    一张票配一张凭证：这一行仍以票的记录名为准；几张票一张凭证、或一张票分几张凭证（``split``）：每张凭证一行。"""
    first = invoices[0]
    own = len(invoices) == 1 and not split
    base, tax, rate = certificate["base"], certificate["tax"], certificate["rate"]
    if not split:
        if base is None:
            base = _total(row["base"] for row in invoices)
        if not tax:
            tax = _total(row["tax"] for row in invoices)
        if rate is None:
            rate = first["rate"] if len(invoices) == 1 else (snap_rate(tax * HUNDRED / base) if base and tax else None)
    numbers = [row["invoice_number"] for row in invoices if row["invoice_number"]]
    merged = {**(first if own else certificate),
              **{key: first[key] or certificate[key] for key in ("payee_branch", "payee_name", "payee_address")},
              "refs": first["refs"] + certificate["refs"] if own
              else certificate["refs"] + [ref for row in invoices for ref in row["refs"]],
              "source": "both", "include": True, "date": certificate["date"] or first["date"],
              "date_source": "certificate", "base": base, "rate": rate, "tax": tax,
              "invoice_number": ", ".join(dict.fromkeys(numbers))[:60],
              "category": first["category"] or certificate["category"]}
    # 票据证实了是本公司付的款；凭证上没读出的金额由票据补上
    resolved = {"certificate_direction_unclear"} | ({"amount_unreadable"} if base is not None and tax else set())
    merged["issues"] = [item for item in certificate["issues"] if item["code"] not in resolved]
    return merged


def _unclear(row, others):
    """配不上而日期相近：先不计入，列出可能是同一笔的记录名，会计确认（include）后计入。"""
    row["include"] = False
    refs = [ref for other in others for ref in other["refs"]]
    for item in row["issues"]:
        if item["code"] == "pairing_unclear":
            item["candidates"] = list(dict.fromkeys(item["candidates"] + refs))
            return
    row["issues"].append(_issue("pairing_unclear", candidates=refs))


def _pair_payee(invoices, certificates) -> list:
    """同一收款方：一对一 → 几张票一张凭证 → 一张票几张凭证（赊账票的凭证可在票据日之后 90 天内）；剩下的票（印了
    预扣税的）日期前后 45 天内有这个收款方的凭证、或赊账票 90 天内有金额相同的凭证（配上了别的票的也算）就标「对不上」
    先不计入：那张凭证可能就付了这张票，各月取到的单据不同、配法不同时，这样一张票不会这个月按票据日计入、另一个月又
    随凭证计入。"""
    free_invoices, free_certificates, out = set(range(len(invoices))), set(range(len(certificates))), []

    def nearest(row, pool):
        return sorted(pool, key=lambda c: (_distance(row["date"], certificates[c]["date"]), c))

    # 一对一：金额与税额都相同的先配，再是印了税额的票、凭证日在票据日之后、相差天数少的
    pairs = []
    for i, row in enumerate(invoices):
        for c, certificate in enumerate(certificates):
            base_ok = _near(row["base"], certificate["base"])
            tax_ok = bool(row["tax"]) and _near(row["tax"], certificate["tax"])
            if (base_ok or tax_ok) and _reach(row, certificate):
                pairs.append(((not (base_ok and tax_ok), row["source"] != "invoice",
                               *_distance(row["date"], certificate["date"]), i, c), i, c))
    for _rank, i, c in sorted(pairs):
        if i in free_invoices and c in free_certificates:
            free_invoices.discard(i)
            free_certificates.discard(c)
            out.append(_merged([invoices[i]], certificates[c]))
    for c in sorted(free_certificates):
        certificate = certificates[c]
        pool = sorted((i for i in free_invoices if _reach(invoices[i], certificate)),
                      key=lambda i: (_distance(invoices[i]["date"], certificate["date"]), i))[:SUBSET_CANDIDATES]
        picked = sorted(pool[position] for position in _sum_match([invoices[i] for i in pool], certificate))
        if picked:
            free_invoices.difference_update(picked)
            free_certificates.discard(c)
            out.append(_merged([invoices[i] for i in picked], certificate))
    for i in sorted(free_invoices):
        row = invoices[i]
        pool = nearest(row, [c for c in free_certificates if _reach(row, certificates[c])])
        pool = pool[:SUBSET_CANDIDATES]
        picked = sorted(pool[position] for position in _sum_match([certificates[c] for c in pool], row))
        if picked:
            free_invoices.discard(i)
            free_certificates.difference_update(picked)
            out.extend(_merged([row], certificates[c], split=True) for c in picked)
    left_invoices = [invoices[i] for i in sorted(free_invoices)]
    left_certificates = [certificates[c] for c in sorted(free_certificates)]
    for row in left_invoices:
        if row["source"] != "invoice":
            continue
        # 45 天以内的不论金额；赊账票 45 天以外的只看金额相同的（那张凭证配给了别的票，可能本来付的是这张）
        near = [certificate for certificate in certificates if _within(row["date"], certificate["date"])
                or (_credit(row, certificate["date"]) and _same_amount(row, certificate))]
        if near:
            _unclear(row, near)
            for certificate in near:
                if any(certificate is left for left in left_certificates):
                    _unclear(certificate, [row])
    return out + left_invoices + left_certificates


def _pair(invoice_rows, certificate_rows) -> list:
    """按收款方税号分组配对；没有税号的原样留着（进待核）。"""
    groups, out = {}, []
    for kind, rows in ((0, invoice_rows), (1, certificate_rows)):
        for row in rows:
            if row["payee_tax_id"]:
                groups.setdefault(row["payee_tax_id"], ([], []))[kind].append(row)
            else:
                out.append(row)
    for invoices, certificates in groups.values():
        out.extend(_pair_payee(invoices, certificates))
    return out


def collect(documents, company_tax_id, remembered_payees=(), skipped=None) -> tuple[list, list]:
    """识别记录 → (付款行, 文档级说明)。

    ``documents``：[{ref, origin: history|reference, fields: 主页字段, match?}]；``match`` 是 client / workspace / name
    的，是票面没有本公司税号、按归属或买方名称取到的记录（`sources.wht_documents`），先不计入、待会计确认；
    同一张票（卖方 + 票号 + 合计）或同一张凭证（对方 + 日期 + 金额）重复上传只算一次；
    ``remembered_payees``：记过所得类型的收款方税号（判 50 ทวิ 方向与列出建议补扣的依据）；
    ``skipped``：给了就填上不成付款行、也不进说明的记录 {记录名: 原因}（``EXCLUDED_REASONS``，草稿据此列 ``excluded``）。"""
    skipped = {} if skipped is None else skipped
    company = tax_id(company_tax_id)
    remembered = {tax_id(value) for value in remembered_payees or ()}
    invoices, certificates, notes = [], [], []
    purchase_sellers, sales_buyers, seen = set(), set(), {}
    for document in documents or []:
        fields = document.get("fields") or {}
        seller, buyer = tax_id(fields.get("seller_tax")), tax_id(fields.get("buyer_tax"))
        if is_certificate(fields):
            certificates.append((document, fields, seller, buyer))
            continue
        if buyer == company and seller != company:
            unconfirmed = False
            if seller:
                purchase_sellers.add(seller)
        elif (document.get("match") in UNCONFIRMED_MATCHES and company not in (seller, buyer)
              and str(fields.get("direction") or "") != "sales"):
            unconfirmed = True
        else:
            if seller == company and buyer and buyer != company:
                sales_buyers.add(buyer)
            sale = seller == company or str(fields.get("direction") or "") == "sales"
            skipped[document["ref"]] = "sales_document" if sale else "not_this_company"
            continue
        key = _invoice_key(seller, fields)
        if key and key in seen:
            notes.append(_issue("duplicate_document", ref=document["ref"], of=seen.get(key, "")))
            continue
        if key:
            seen[key] = document["ref"]
        invoices.append((document, fields, seller, unconfirmed))
    invoice_rows = []
    for document, fields, seller, unconfirmed in invoices:
        base = base_of(fields)
        rate, tax = withheld(fields, base)
        if tax and tax > 0:
            row = _payment(document, fields, "invoice", "seller", base, rate, tax)
        elif seller in remembered and base is not None and base >= THRESHOLD:
            row = _payment(document, fields, "suggested", "seller", base, None, None)
        else:
            skipped[document["ref"]] = "nothing_withheld"
            continue
        if unconfirmed:
            row["include"] = False
            row["issues"].append(_issue("buyer_unconfirmed"))
        invoice_rows.append(row)
    certificate_rows, seen_certificates = [], {}
    for document, fields, seller, buyer in certificates:
        if company not in (seller, buyer):
            skipped[document["ref"]] = "certificate_other_company"
            continue
        side = "buyer" if seller == company else "seller"
        counterparty = buyer if seller == company else seller
        if not counterparty or counterparty == company:
            notes.append(_issue("certificate_parties_unreadable", ref=document["ref"]))
            continue
        # 对方只是供应商（或记过所得类型）才认定本公司付款；也是客户的，交会计确认（与费用票配上了即认定）
        bought = counterparty in purchase_sellers or counterparty in remembered
        sold = counterparty in sales_buyers
        if sold and not bought:
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
        if sold or not bought:
            row["include"] = False
            row["issues"].append(_issue("certificate_direction_unclear"))
        certificate_rows.append(row)
    return _pair(invoice_rows, certificate_rows), notes


def _edit_for(row, overrides: Mapping) -> dict:
    """这一行的改动：合并进来的其他记录上的先套（日期、金额除外），行本身记录名上的最后套。"""
    edit = {}
    for ref in row["refs"]:
        if ref != row["row"]:
            edit.update({key: value for key, value in (overrides.get(ref) or {}).items() if key not in ROW_ONLY_KEYS})
    edit.update(overrides.get(row["row"]) or {})
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
    # 读不出金额由 `_checks` 按改后的值重判
    row = {**row, "issues": [item for item in row["issues"] if item["code"] not in RECHECKED]}
    for key in ("base", "tax", "rate"):
        if key in edit:
            row[key] = money(edit[key])
    if "rate" in edit and "tax" not in edit and row["base"] is not None:
        row["tax"] = (row["base"] * row["rate"] / HUNDRED).quantize(CENT, rounding=ROUND_HALF_UP)
    if row["rate"] is None and row["base"] and row["tax"]:  # 读不出金额、会计补了金额与税额的
        row["rate"] = snap_rate(row["tax"] * HUNDRED / row["base"])
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
        if edit["include"]:
            row["issues"] = [item for item in row["issues"] if item["code"] not in CONFIRMED_BY_INCLUDE]
    row["edited"] = sorted(edit)
    return row


def _checks(row) -> list:
    found = []
    if not row["payee_tax_id"] or len(row["payee_tax_id"]) != 13:
        found.append(_issue("payee_tax_missing"))
    elif not valid_tax_id(row["payee_tax_id"]):
        found.append(_issue("payee_tax_invalid"))
    if row["base"] is None or not row["tax"]:
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
        out[key] = rate_text(value) if key == "rate" else ("" if value is None else str(value))
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


def _excluded(documents, skipped, elsewhere, result, target) -> list:
    """本月的单据（票据日 / 凭证日在本月）读到了，却不在两张表、待核、建议与说明里的：逐张给原因（``EXCLUDED_REASONS``），
    CC 能告诉会计为什么没算（2026-10-13 测试机：一张收据悄悄没了）。"""
    shown = {ref for form in result["forms"].values() for line in form["lines"] for ref in line["refs"]}
    shown |= {ref for key in ("review", "suggestions") for row in result[key] for ref in row["refs"]}
    shown |= {note.get("ref") for note in result["notes"]}
    out = []
    for document in documents or []:
        ref, fields = document.get("ref"), document.get("fields") or {}
        day = periods.parse_date(fields.get("date"))
        if not ref or ref in shown or day is None or (day.year, day.month) != target:
            continue
        entry = {"ref": ref, "reason": skipped.get(ref) or "not_this_company", "date": day.isoformat()}
        if ref in elsewhere:
            paid = elsewhere[ref]
            entry.update({"reason": "paid_in_other_month", "payment_date": paid.isoformat()} if paid
                         else {"reason": "payment_date_missing"})
        for key in ("document_type", "invoice_number", "seller_name", "seller_tax", "buyer_name", "buyer_tax",
                    "total_amount", "wht_amount"):
            value = str(fields.get(key) or "").strip()
            if value:
                entry[key] = value[:200]
        if is_certificate(fields):
            entry["document_type"] = CERTIFICATE
        out.append(entry)
    return sorted(out, key=lambda item: (EXCLUDED_REASONS.index(item["reason"]), item["date"], item["ref"]))


def draft(payments, notes, year: int, month: int, memory=None, overrides=None, documents=None,
          skipped=None) -> dict:
    """付款行 → 本月草稿：{forms: {pnd53, pnd3}, questions, review, suggestions, notes, excluded, issues_by_code, totals}。

    ``memory``：{"payee:<税号>" / "category:<分类>": {income_type, income_text, form, condition}}；
    ``overrides``：{识别记录名: 逐行改动（`clean_edit` 过的）}；``documents`` / ``skipped``：交给 `collect` 的记录与它
    填的原因，用来列 ``excluded``（不给就是空表）。"""
    memory, overrides = memory or {}, overrides or {}
    target = (year, month)
    lines, review, suggestions, questions, counted, elsewhere = [], [], [], {}, {}, {}
    for payment in payments:
        row = _apply(payment, _edit_for(payment, overrides), memory)
        # 没有日期的记录本来就取不到（按票据日取数）；付款日不在本月的属于别的月
        if row["date"] is None or (row["date"].year, row["date"].month) != target:
            for ref in row["refs"]:
                elsewhere.setdefault(ref, row["date"])
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
                                "rates": [rate_text(rate) for rate in rates], "suggested": suggested or ""})
    result = {"period": periods.text(year, month), "forms": forms, "questions": shown_questions,
              "review": [_shown(row) for row in sorted(review, key=_order)],
              "suggestions": [_shown(row) for row in sorted(suggestions, key=_order)],
              "notes": list(notes or []), "issues_by_code": counted,
              "totals": {"base": str(sum((Decimal(forms[form]["base"]) for form in FORMS), ZERO)),
                         "tax": str(sum((Decimal(forms[form]["tax"]) for form in FORMS), ZERO)),
                         "lines": len(lines), "review": len(review), "suggestions": len(suggestions)}}
    result["excluded"] = _excluded(documents, skipped or {}, elsewhere, result, target)
    return result
