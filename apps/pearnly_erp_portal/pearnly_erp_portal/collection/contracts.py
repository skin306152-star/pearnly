"""收款的纯规则（不依赖 Frappe、IO）：收款类型给不给收款员选、金额规则、预留后的可收余额、放行版本、写账载荷 v20、回执。

金额一律 ``Decimal`` 两位小数；超过两位小数、负数、不是数字都拒（不四舍五入替人改钱）。

**收款类型只列「收钱」类**（主会话 11-11：靠 Express 自带属性判断，判不出的默认不列）。一种 ZR 类型按下面的顺序判一次：

1. 名称（泰文 POSDES）含「ส่วนลดเงินสด」或英文名含 cash discount → **折扣**（不当收款方式列，只在「折扣」那一步用）；
2. 名称含「เช็ค」或英文名含 cheque → 支票，不列（本批不做支票）；
3. 名称含「ภาษีถูกหัก」「หัก ณ ที่จ่าย」或英文名含 withholding → 预扣税，不列（预扣填 0）；
4. 名称含「โอนเข้าบัญชี」，或关联了银行（SHORTNAM 指向 BKMAS）→ **转账**，列给收款员；
5. 名称含「เงินสด」且科目就是账套的现金科目（ISINFO.ACCNUM00）→ 现金，不另列（现金走「现金」那一格，写进抬头 CSHRCV）；
6. 其余（费用、坏账、借款、预收、礼券、信用卡、零用金、暂记……）→ 不列。

列给收款员的（转账、折扣）还要有科目（CC 按 ISRUN.ACCNUM01 / 关联银行 BKMAS.ACCNUM 解出，``account_issue`` 为空）；
没有科目或两处不一致 → 不列；前缀（PREFIX）重复的几种都不列（Express 里每种方式一行 ``CHQNUM = 前缀 + RE 号``，前缀就是
这种方式的身份；DOCCOD 不唯一，只作参考）；折扣类型多于一种 → 不给折扣。
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

PAYLOAD_VERSION = 20
DIRECTION = "receipt"
CENT = Decimal("0.01")
ZERO = Decimal("0.00")
MAX_INVOICES = 50
MAX_METHODS = 10
REF_MAX = 30

# 记录状态
DRAFT, QUEUED, WRITING, WRITTEN, FAILED, WITHDRAWN = "draft", "queued", "writing", "written", "failed", "withdrawn"
STATUSES = (DRAFT, QUEUED, WRITING, WRITTEN, FAILED, WITHDRAWN)
RESERVING = (QUEUED, WRITING)          # 占着发票余额的（已放行、还没写完）
WITHDRAWABLE = (DRAFT, QUEUED, FAILED)  # 没被电脑领走前可撤回（领走的另由写账队列拒）
SOURCES = ("line", "cc")

# 收款类型的结论
TRANSFER, DISCOUNT = "transfer", "discount"
CHEQUE, WITHHOLDING, CASH_BOX, NOT_MONEY = "cheque", "withholding", "cash_box", "not_money"
NO_ACCOUNT, ACCOUNT_CONFLICT, DUPLICATE_PREFIX, DISCOUNT_SEVERAL = (
    "no_account", "account_conflict", "duplicate_prefix", "discount_several")

DISCOUNT_WORDS = ("ส่วนลดเงินสด",)
DISCOUNT_WORDS_EN = ("cash discount",)
CHEQUE_WORDS = ("เช็ค",)
CHEQUE_WORDS_EN = ("cheque",)
WITHHOLDING_WORDS = ("ภาษีถูกหัก", "หัก ณ ที่จ่าย")
WITHHOLDING_WORDS_EN = ("withholding",)
TRANSFER_WORDS = ("โอนเข้าบัญชี",)
CASH_WORDS = ("เงินสด",)

# 错误码（LINE / CC 按码出文案）
INVOICES_EMPTY = "collection.invoices_empty"
INVOICES_TOO_MANY = "collection.invoices_too_many"
INVOICE_DUPLICATE = "collection.invoice_duplicate"
INVOICE_NOT_FOUND = "collection.invoice_not_found"
OVER_COLLECTED = "collection.over_collected"
AMOUNT_INVALID = "collection.amount_invalid"
AMOUNT_MISMATCH = "collection.amount_mismatch"
METHOD_INVALID = "collection.receipt_type_invalid"
METHOD_DUPLICATE = "collection.method_duplicate"
METHODS_TOO_MANY = "collection.methods_too_many"
CASH_ACCOUNT_MISSING = "collection.cash_account_missing"
DISCOUNT_ONLY = "collection.discount_only"
VOUCHER_MISSING = "collection.voucher_missing"
CHANGED = "collection.changed"
CONSENT_MISSING = "collection.consent_missing"
NOT_DRAFT = "collection.not_draft"
NOT_WITHDRAWABLE = "collection.not_withdrawable"
BUSY = "collection.busy"
SALESMAN_INVALID = "collection.salesman_invalid"
DEPARTMENT_INVALID = "collection.department_invalid"
# 电脑写入时说「这张不能收」的回执码（转人工，不重试）：已被收 / 不存在 / 服务品现金制（SRV_VATTYP=2，CC 不列进未收清发票）。
# 前两种带每张发票此刻的余额；三种都按客户再读一次未收清发票
UNCOLLECTABLE = ("ALREADY_COLLECTED", "INVOICE_NOT_FOUND", "CASH_BASIS_VAT")


class CollectionError(ValueError):
    """规则不过：``code`` 是上面的错误码，``detail`` 是给人看的数（各张发票此刻能收多少、两边合计……）。"""

    def __init__(self, code, detail=None):
        super().__init__(code)
        self.code = code
        self.detail = detail or {}


# ── 金额 ─────────────────────────────────────────────────────────────────────


def money(value, *, allow_zero=True) -> Decimal:
    """收款员打的数 → 两位小数 Decimal。千分位逗号、空白照收；负数、超过两位小数、不是数字回 :data:`AMOUNT_INVALID`。"""
    if isinstance(value, Decimal):
        number = value
    else:
        text = str(value if value is not None else "").replace(",", "").strip()
        if not text:
            text = "0"
        try:
            number = Decimal(text)
        except InvalidOperation:
            raise CollectionError(AMOUNT_INVALID) from None
    try:
        exact = number.is_finite() and number >= 0 and number == number.quantize(CENT)
    except InvalidOperation:  # 大到两位小数放不下
        exact = False
    if not exact:
        raise CollectionError(AMOUNT_INVALID)
    if not allow_zero and number == 0:
        raise CollectionError(AMOUNT_INVALID)
    return number.quantize(CENT)


def text(value) -> str:
    return format(Decimal(value).quantize(CENT), "f")


# ── 收款类型 ─────────────────────────────────────────────────────────────────


def _has(value, words) -> bool:
    value = str(value or "")
    return any(word in value for word in words)


def _has_en(value, words) -> bool:
    value = str(value or "").lower()
    return any(word in value for word in words)


def classify(row, cash_account="") -> dict:
    """一种 ZR 收款类型 → ``{"role": "transfer" | "discount" | "", "reason": 不列的原因码}``（规则见模块说明）。"""
    name, name_en = row.get("name"), row.get("name_en")
    if _has(name, DISCOUNT_WORDS) or _has_en(name_en, DISCOUNT_WORDS_EN):
        role = DISCOUNT
    elif _has(name, CHEQUE_WORDS) or _has_en(name_en, CHEQUE_WORDS_EN):
        return {"role": "", "reason": CHEQUE}
    elif _has(name, WITHHOLDING_WORDS) or _has_en(name_en, WITHHOLDING_WORDS_EN):
        return {"role": "", "reason": WITHHOLDING}
    elif _has(name, TRANSFER_WORDS) or str(row.get("bank") or "").strip():
        role = TRANSFER
    elif _has(name, CASH_WORDS) and str(row.get("account") or "").strip() and \
            str(row.get("account") or "").strip() == str(cash_account or "").strip():
        return {"role": "", "reason": CASH_BOX}
    else:
        return {"role": "", "reason": NOT_MONEY}
    issue = str(row.get("account_issue") or "").strip()
    if issue == "conflict":
        return {"role": "", "reason": ACCOUNT_CONFLICT}
    if issue or not str(row.get("account") or "").strip():
        return {"role": "", "reason": NO_ACCOUNT}
    return {"role": role, "reason": ""}


def catalog(receipt_types, cash_account="") -> dict:
    """这本账套给收款员的收款方式：``{"cash": 能不能收现金, "transfers": [...], "discount": 折扣那种或 None,
    "excluded": [{prefix, code, name, reason}]}``。每种 ``{code, prefix, name, name_en, account}``。"""
    rows = [row for row in receipt_types or [] if isinstance(row, dict) and str(row.get("prefix") or "").strip()]
    judged = []
    for row in rows:
        verdict = classify(row, cash_account)
        judged.append(({"code": str(row.get("code") or "").strip(), "prefix": str(row.get("prefix") or "").strip(),
                        "name": str(row.get("name") or "").strip(), "name_en": str(row.get("name_en") or "").strip(),
                        "account": str(row.get("account") or "").strip()}, verdict))
    counts = {}
    for item, _verdict in judged:
        counts[item["prefix"]] = counts.get(item["prefix"], 0) + 1
    transfers, discounts, excluded = [], [], []
    for item, verdict in judged:
        reason = verdict["reason"] or (DUPLICATE_PREFIX if counts[item["prefix"]] > 1 else "")
        if reason:
            excluded.append({**item, "reason": reason})
        elif verdict["role"] == TRANSFER:
            transfers.append(item)
        else:
            discounts.append(item)
    if len(discounts) > 1:
        excluded.extend({**item, "reason": DISCOUNT_SEVERAL} for item in discounts)
        discounts = []
    return {"cash": bool(str(cash_account or "").strip()), "transfers": transfers,
            "discount": discounts[0] if discounts else None, "excluded": excluded}


def method_of(catalog_value, prefix):
    """按前缀认一种可用的方式（转账或折扣）：``(那一种, 角色)``；认不出回 ``(None, "")``。"""
    prefix = str(prefix or "").strip()
    for item in catalog_value.get("transfers") or []:
        if item["prefix"] == prefix:
            return item, TRANSFER
    discount = catalog_value.get("discount")
    if discount and discount["prefix"] == prefix:
        return discount, DISCOUNT
    return None, ""


# ── 余额与分配 ───────────────────────────────────────────────────────────────


def available(remaining, reserved) -> Decimal:
    """快照余额减去别的记录的预留，不小于 0。"""
    return max(ZERO, money(remaining) - money(reserved or 0))


def allocate(rows, amount) -> list:
    """收款员只给了一个总数：按发票日期、单号从早到晚分（先收满早的，最后一张可部分收）。``rows`` 每张带 ``available``。
    分不完（总数大于这些发票能收的合计）回 :data:`OVER_COLLECTED`，``detail`` 带能收的合计。"""
    left = money(amount, allow_zero=False)
    out = []
    for row in sorted(rows, key=lambda item: (str(item.get("doc_date") or ""), str(item.get("docnum") or ""))):
        if left <= 0:
            break
        take = min(left, money(row.get("available") or 0))
        if take > 0:
            out.append({"docnum": row["docnum"], "amount": take})
            left -= take
    if left > 0:
        total = sum((money(row.get("available") or 0) for row in rows), ZERO)
        raise CollectionError(OVER_COLLECTED, {"available": text(total)})
    return out


# ── 一笔收款的规则 ───────────────────────────────────────────────────────────


def check(draft, open_rows, catalog_value, *, require_vouchers=False) -> dict:
    """``draft``：``{"invoices": [{docnum, amount}], "cash", "methods": [{prefix, amount, voucher?}]}``；``open_rows``：这位客户
    此刻能收的发票 ``{docnum: {"available", "remaining", "doc_date", "salesman", "department"}}``（已扣别的记录的预留）。
    过了回规整后的一笔（金额 Decimal、方式带角色与名称、各合计）；不过抛 :class:`CollectionError`。

    规则：至少一张发票、最多 :data:`MAX_INVOICES` 张、不重复；每张收 > 0 且不超过此刻能收的（部分收可以）；方式按前缀认、
    不重复、每种 > 0；收现金要这本账套有现金科目；发票合计 = 现金 + 各方式（含折扣）；只有折扣、没有收到钱的拒；
    ``require_vouchers``（放行时）每种转账都要有凭证。"""
    invoices = list(draft.get("invoices") or [])
    if not invoices:
        raise CollectionError(INVOICES_EMPTY)
    if len(invoices) > MAX_INVOICES:
        raise CollectionError(INVOICES_TOO_MANY)
    seen, out_invoices, over = set(), [], {}
    for row in invoices:
        docnum = str(row.get("docnum") or "").strip()
        if not docnum or docnum in seen:
            raise CollectionError(INVOICE_DUPLICATE if docnum else INVOICE_NOT_FOUND, {"docnum": docnum})
        seen.add(docnum)
        found = open_rows.get(docnum)
        if found is None:
            raise CollectionError(INVOICE_NOT_FOUND, {"docnum": docnum})
        amount = money(row.get("amount"), allow_zero=False)
        can = money(found.get("available") or 0)
        if amount > can:
            over[docnum] = text(can)
        out_invoices.append({"docnum": docnum, "amount": amount, "remaining": money(found.get("remaining") or 0),
                             "doc_date": found.get("doc_date") or "", "salesman": found.get("salesman") or "",
                             "department": found.get("department") or ""})
    if over:
        raise CollectionError(OVER_COLLECTED, {"available": over})
    cash = money(draft.get("cash") or 0)
    if cash > 0 and not catalog_value.get("cash"):
        raise CollectionError(CASH_ACCOUNT_MISSING)
    methods = list(draft.get("methods") or [])
    if len(methods) > MAX_METHODS:
        raise CollectionError(METHODS_TOO_MANY)
    out_methods, prefixes = [], set()
    for row in methods:
        item, role = method_of(catalog_value, row.get("prefix"))
        if item is None:
            raise CollectionError(METHOD_INVALID, {"prefix": str(row.get("prefix") or "")})
        if item["prefix"] in prefixes:
            raise CollectionError(METHOD_DUPLICATE, {"prefix": item["prefix"]})
        prefixes.add(item["prefix"])
        voucher = str(row.get("voucher") or "").strip()
        if require_vouchers and role == TRANSFER and not voucher:
            raise CollectionError(VOUCHER_MISSING, {"prefix": item["prefix"]})
        out_methods.append({"code": item["code"], "prefix": item["prefix"], "name": item["name"], "role": role,
                            "amount": money(row.get("amount"), allow_zero=False), "voucher": voucher})
    total = sum((row["amount"] for row in out_invoices), ZERO)
    discount = sum((row["amount"] for row in out_methods if row["role"] == DISCOUNT), ZERO)
    transfer = sum((row["amount"] for row in out_methods if row["role"] == TRANSFER), ZERO)
    paid = cash + transfer + discount
    if paid != total:
        raise CollectionError(AMOUNT_MISMATCH, {"invoices": text(total), "paid": text(paid)})
    if cash + transfer <= 0:
        raise CollectionError(DISCOUNT_ONLY)
    return {"invoices": out_invoices, "cash": cash, "methods": out_methods, "total": total, "transfer": transfer,
            "discount": discount}


def summaries(checked) -> dict:
    """列表与导出用的几栏：发票与各收多少、各方式（不含现金）。"""
    return {"invoice_summary": "; ".join(f"{row['docnum']} {text(row['amount'])}" for row in checked["invoices"])[:1000],
            "method_summary": "; ".join(f"{row['prefix']} {text(row['amount'])}" for row in checked["methods"])[:1000]}


# ── 销售员与部门 ─────────────────────────────────────────────────────────────


def staff(first, run_department="", salesman=None, department=None, previous=None) -> dict:
    """写进 RE 抬头的销售员与部门（不留空交给 CC 回退单据簿，主会话 11-11）：收款员改过（给了值）就用改的；没给时默认第一张发票的
    销售员 / 部门，那张发票没有部门再用这本账套 RE 单据簿的默认部门（ISRUN ``DOCTYP=RE`` 的 ``DEPCOD``）。

    ``previous``（改草稿时）：``{"salesman", "department", "default_salesman", "default_department"}``——原来的值与按原来第一张发票
    算出的默认；原来的值与默认不同（收款员改过）就沿用，否则跟着新的第一张发票走。"""
    first = first or {}
    default = {"salesman": str(first.get("salesman") or "").strip(),
               "department": str(first.get("department") or "").strip() or str(run_department or "").strip()}
    out = {}
    for key, given in (("salesman", salesman), ("department", department)):
        if given is not None:
            out[key] = str(given or "").strip()
        elif previous and str(previous.get(key) or "") != str(previous.get("default_" + key) or ""):
            out[key] = str(previous.get(key) or "")
        else:
            out[key] = default[key]
    if not out["department"]:
        out["department"] = default["department"]
    return out


# ── 放行 ─────────────────────────────────────────────────────────────────────


def release_check(status, current_version, shown_version, consent) -> str:
    """放行只认「确认卡展示的那个版本」+ 明确同意（LINE【确认】/ 打「ยืนยัน / 确认 / ok」、CC「录吧」）：回空串可以放行；
    不是草稿回 :data:`NOT_DRAFT`；没有同意事件回 :data:`CONSENT_MISSING`；版本对不上（卡片之后又改过）回 :data:`CHANGED`。"""
    if status != DRAFT:
        return NOT_DRAFT
    if not str(consent or "").strip():
        return CONSENT_MISSING
    try:
        shown = int(shown_version)
    except (TypeError, ValueError):
        return CHANGED
    return "" if shown == int(current_version or 0) else CHANGED


# ── 写账载荷与回执 ───────────────────────────────────────────────────────────


def payload(record, book, company, user="") -> dict:
    """一笔已放行的收款 → 交给 CC 桌面版的载荷（方向 ``receipt``，载荷版本 20）。``record``：
    ``{ref_no, doc_date, customer, salesman, department, cash, invoices: [{docnum, amount}], methods: [{code, prefix, amount}]}``；
    ``book``：账套（`books.for_company` 那一项：path、root、company）；``company``：``{tax_id, branch}``。
    账套位置几栏与其他写账载荷同名（领单时换成领取电脑的写法，`express_rules.push.wire_location`）。"""
    path = str(book.get("path") or "").strip()
    return {
        "direction": DIRECTION, "payload_version": PAYLOAD_VERSION,
        "ref_no": str(record["ref_no"])[:REF_MAX],
        "company": {"tax_id": str(company.get("tax_id") or ""), "branch": str(company.get("branch") or "")},
        "doc_date": str(record["doc_date"])[:10],
        "customer": str(record["customer"] or ""), "salesman": str(record.get("salesman") or ""),
        "department": str(record.get("department") or ""),
        "invoices": [{"docnum": row["docnum"], "amount": text(row["amount"])} for row in record["invoices"]],
        "cash": text(record.get("cash") or 0),
        "methods": [{"code": row.get("code") or "", "prefix": row["prefix"], "amount": text(row["amount"])}
                    for row in record.get("methods") or []],
        "user": str(user or "")[:140],
        "account_root": str(book.get("root") or "").strip(), "account_dir": path,
        "account_set": path, "account_company": str(book.get("company") or "").strip(),
        "account_set_row": 0, "respect_period_lock": True,
    }


def balances(meta) -> dict:
    """回执里每张发票的当前余额：成功是写后的 ``remaining_after``，``already_collected`` 是电脑重读到的 ``remaining``。
    ``{docnum: Decimal}``；读不出的略过。"""
    out = {}
    rows = (meta or {}).get("invoices") if isinstance(meta, dict) else None
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        docnum = str(row.get("docnum") or "").strip()
        value = row.get("remaining_after", row.get("remaining"))
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, TypeError):
            continue
        if docnum and amount.is_finite() and amount >= 0:
            out[docnum] = amount.quantize(CENT)
    return out
