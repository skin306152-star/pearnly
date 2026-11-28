"""收款的纯规则（不依赖 Frappe、IO）：收款类型给不给收款员选、金额规则、预留后的可收余额、放行版本、写账载荷 v20、回执。

金额一律 ``Decimal`` 两位小数；超过两位小数、负数、不是数字都拒（不四舍五入替人改钱）。

**收款类型只列「收钱」类**（主会话 11-11：靠 Express 自带属性判断，判不出的默认不列；11-12 按类放宽：以类型关联的科目是不是
银行存款为准，名称只作辅助）。一种 ZR 类型按下面的顺序判一次：

1. 名称（泰文 POSDES）含「ส่วนลดเงินสด」或英文名含 cash discount → **折扣**（不当收款方式列，只在「折扣」那一步用）；
2. 名称含「เช็ค」或英文名含 cheque → 支票，不列（本批不做支票；支票记进存款科目的也不列）；
3. 名称含「ภาษีถูกหัก」「หัก ณ ที่จ่าย」或英文名含 withholding → 预扣税，不列（预扣填 0）；
4. **转账**，列给收款员：
   - 新版 CC 报了科目类别（``account_class``，CC 按账套自身的科目表结构判，见 ``companion.payment_methods.account_classes``）：
     科目是银行存款类（``bank``）；或科目在现金与存款那一组里、不是现金科目也判不出存款（``cash_group``）而名称含「โอนเข้าบัญชี」。
     名称说转账、科目却在组外（例 GISP 的「เงินโอนเข้าบัญชี GIZE」记关联公司借款）→ 不列；
   - 老版 CC 没报类别（或这一种没有可用科目）：名称含「โอนเข้าบัญชี」，或关联了银行（SHORTNAM 指向 BKMAS）；
5. 名称含「เงินสด」且科目就是账套的现金科目（ISINFO.ACCNUM00）→ 现金，不另列（现金走「现金」那一格，写进抬头 CSHRCV）；
6. 其余（费用、坏账、借款、预收、礼券、信用卡、零用金、暂记……）→ 不列。

列给收款员的（转账、折扣）还要有科目（CC 按 ISRUN.ACCNUM01 / 关联银行 BKMAS.ACCNUM 解出，``account_issue`` 为空）；
没有科目或两处不一致 → 不列；前缀（PREFIX）重复的几种都不列（Express 里每种方式一行 ``CHQNUM = 前缀 + RE 号``，前缀就是
这种方式的身份；DOCCOD 不唯一，只作参考）；折扣类型多于一种 → 不给折扣。
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from pearnly_core.express_rules.contracts import codes

PAYLOAD_VERSION = 20
# 带抵减单的收款盖 23（CC 桌面版 1.12.26 起认 ``offsets``，旧版诚实拒收、派单只给认得的电脑）；不带的照旧 20
OFFSETS_PAYLOAD_VERSION = 23
DIRECTION = "receipt"
# 抵减单（退货 / 减项进收款，评估 docs/work/2026-11-28-credit-note-assessment.md）：快照里 ``rectyp`` 是这几种的，收款时从合计里扣——
# Express 减项（RECTYP 5，CN 簿 SR / SE / EC）与定金（RECTYP 0，AI）；MR.ERP 减债（arsc）读进来也记 5。其余（发票 3、MR.ERP 赊销 2 /
# 增债 3）是要收的
OFFSET_RECTYPS = ("5", "0")
CENT = Decimal("0.01")
ZERO = Decimal("0.00")
MAX_INVOICES = 50
MAX_METHODS = 10
REF_MAX = 30

# 记录状态
DRAFT, QUEUED, WRITING, WRITTEN, FAILED, WITHDRAWN = "draft", "queued", "writing", "written", "failed", "withdrawn"
STATUSES = (DRAFT, QUEUED, WRITING, WRITTEN, FAILED, WITHDRAWN)
# 结果不明（写没写进去不知道）单列「结果不明」，不并进失败（主会话 11-25，同 ERP 单据 `express.contracts.UNKNOWN_REASONS`）：
# 失败且原因是 CC 桌面版写到一半断了（`cc_desktop.jobs.LOST`）或 MR.ERP 导入发出后查不到 / 出错（``mrerp.result_unknown: <码>``）
RESULT_UNKNOWN = "Result unknown"
EXPRESS_UNKNOWN = "cc_desktop.result_unknown"
UNKNOWN_REASONS = (EXPRESS_UNKNOWN, "mrerp.result_unknown")
# 列表「状态」筛选里结果不明单列一档（筛选值，不落库）：失败 + 原因是上面两种（库里按 LIKE 认，两种都含这一段）
RESULT_UNKNOWN_STATUS = "result_unknown"
UNKNOWN_LIKE = "%.result_unknown%"

# 给人看的状态词（用户 11-22 统一成 录入中 / 成功 / 失败 / 已取消）：LINE 回执与网页「收款」页同一张表（`collections.js` 的
# ``STATUS_WORDS`` 契约测试比对）。草稿照旧。结果不明那一档只是筛选值（不落库），列在这里让筛选下拉与条件标签显示状态词
STATUS_WORDS = {DRAFT: "erp.collection.status.draft", QUEUED: "Entering", WRITING: "Entering", WRITTEN: "Succeeded",
                FAILED: "Failed", WITHDRAWN: "Cancelled", RESULT_UNKNOWN_STATUS: RESULT_UNKNOWN}


def result_unknown(status, reason="") -> bool:
    return status == FAILED and str(reason or "").startswith(UNKNOWN_REASONS)


def status_filters(filters) -> list:
    """列表「状态」筛选交给库的条件（服务端筛选，同 :func:`result_unknown` 的判据）：选「结果不明」= 失败且原因是结果不明；选「失败」=
    失败且原因不是结果不明（结果不明不并进失败，主会话 11-28）。其余条件原样。``filters`` 是 ``[字段, 操作, 值]`` 的表。"""
    out = []
    for item in filters:
        field, op, value = (list(item) + [None, None])[:3]
        if field != "status" or op not in ("=", "in"):
            out.append(item)
            continue
        values = [value] if op == "=" else list(value or [])
        unknown, failed = RESULT_UNKNOWN_STATUS in values, FAILED in values
        if unknown and not failed and len(values) == 1:
            out += [["status", "=", FAILED], ["reason", "like", UNKNOWN_LIKE]]
        elif failed and not unknown and len(values) == 1:
            out += [["status", "=", FAILED], ["reason", "not like", UNKNOWN_LIKE]]
        else:  # 几档一起选：结果不明算进失败那一档查（两档都选时就是全部失败）
            out.append(["status", "in", sorted({FAILED if v == RESULT_UNKNOWN_STATUS else v for v in values})])
    return out


def retryable(status, reason="") -> bool:
    """写进 Express 结果不明的：只给「重试」（共用写账队列 `cc_desktop.jobs.retry` 钉回原电脑再排，CC 桌面版按参照号查重，写过的
    回原号、一行不写），不给「撤回」（撤回后 Express 里可能已有，再录就重复；主会话 11-25 定 A）。"""
    return status == FAILED and str(reason or "") == EXPRESS_UNKNOWN


def status_word(status, reason="") -> str:
    """收款状态（与原因）→ 状态词词条（没在表里的原样）。"""
    if result_unknown(status, reason):
        return RESULT_UNKNOWN
    return STATUS_WORDS.get(status or "", status or "")


RESERVING = (QUEUED, WRITING)          # 占着发票余额的（已放行、还没写完）
WITHDRAWABLE = (DRAFT, QUEUED, FAILED)  # 没被电脑领走前可撤回（领走的另由写账队列拒）
SOURCES = ("line", "cc", "web")  # web：ERP 网页「新建」（11-14），提交即放行

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
# 新版 CC 报的科目类别（与 ``companion.payment_methods.ACCOUNT_CLASSES`` 同值，契约测试守住）：银行存款、现金与存款那一组里的其他科目、
# 现金科目、组外
ACCOUNT_BANK = "bank"
ACCOUNT_CASH_GROUP = "cash_group"
ACCOUNT_CASH = "cash"
ACCOUNT_OTHER = "other"
ACCOUNT_CLASSES = (ACCOUNT_BANK, ACCOUNT_CASH_GROUP, ACCOUNT_CASH, ACCOUNT_OTHER)

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
OFFSETS_EXCEED = "collection.offsets_exceed_invoices"   # 抵减大于发票（净额 < 0），等原生实录再定
VOUCHER_MISSING = "collection.voucher_missing"
CHANGED = "collection.changed"
CONSENT_MISSING = "collection.consent_missing"
NOT_DRAFT = "collection.not_draft"
NOT_WITHDRAWABLE = "collection.not_withdrawable"
NOT_RETRYABLE = "collection.not_retryable"
BUSY = "collection.busy"
SALESMAN_INVALID = "collection.salesman_invalid"
DEPARTMENT_INVALID = "collection.department_invalid"
# 电脑写入时说「这张不能收」的回执码（转人工，不重试）：已被收 / 不存在。服务品现金制的发票（SRV_VATTYP=2，CC 不列进未收清发票）
# 写入时遇到也回 INVOICE_NOT_FOUND（回执证据里 ``problem="cash_basis_vat"``，主会话 11-11 更正：没有新码）。带每张发票此刻的
# 余额的记下余额；都按客户再读一次未收清发票。码照 CC 桌面版原样小写（`dbf_receipt.ERR_*`，回执 ``[already_collected] …``）：
# 11-14 前这里写成大写，从没对上过（余额没记、快照没按客户重读），跨两边由 `test_companion_contract` 守住
UNCOLLECTABLE = ("already_collected", "invoice_not_found")
# 电脑「写前就拒、一行没写」的码（`dbf_receipt.write_receipt` 在写前备份之前抛的 Refused / 直接回的码，主会话 11-14 审定）：
# 这类失败把收款退回草稿、带原因还给录入的人改了再放行（卡片有效期规则 A9）；再放行换新请求号（`push.request_key`），
# YOUREF 仍是记录号——上次万一其实写成了，CC 按同客户同 YOUREF 回原号、一行不写。其余失败（写到一半回滚、结果不明、
# 没账套、撤掉）照旧记失败，不还回。
RETURNABLE = ("already_collected", "invoice_not_found", "receipt_type_invalid", "cash_account_missing",
              "deposit_account_missing", "JOURNAL_BOOK_NOT_FOUND", "CUSTOMER_NOT_IN_ARMAS", "ACCOUNT_NOT_POSTABLE")


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


def _transfer(row, name) -> bool:
    """是不是转账（规则第 4 条）：报了科目类别按类别（名称只作辅助），没报照名称与关联银行。"""
    klass = str(row.get("account_class") or "").strip()
    if klass in ACCOUNT_CLASSES:
        return klass == ACCOUNT_BANK or (klass == ACCOUNT_CASH_GROUP and _has(name, TRANSFER_WORDS))
    return _has(name, TRANSFER_WORDS) or bool(str(row.get("bank") or "").strip())


def classify(row, cash_account="") -> dict:
    """一种 ZR 收款类型 → ``{"role": "transfer" | "discount" | "", "reason": 不列的原因码}``（规则见模块说明）。"""
    name, name_en = row.get("name"), row.get("name_en")
    if _has(name, DISCOUNT_WORDS) or _has_en(name_en, DISCOUNT_WORDS_EN):
        role = DISCOUNT
    elif _has(name, CHEQUE_WORDS) or _has_en(name_en, CHEQUE_WORDS_EN):
        return {"role": "", "reason": CHEQUE}
    elif _has(name, WITHHOLDING_WORDS) or _has_en(name_en, WITHHOLDING_WORDS_EN):
        return {"role": "", "reason": WITHHOLDING}
    elif _transfer(row, name):
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
                        "account": str(row.get("account") or "").strip(),
                        "bank_account": row.get("bank_account") if isinstance(row.get("bank_account"), dict) else None,
                        "uses": _uses(row.get("uses"))}, verdict))
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


def _uses(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def account_label(item) -> str:
    """收款员选方式时看到的名字：方式进的银行户头「简称（没有用户头名称）·账号末 4 位数字」，例「กรุงศรี ·3016」；CC 桌面版没报户头
    （旧版、方式没指户头）的照旧用方式名。户头字段见 `tests/test_companion_contract.py`（receipt_types[].bank_account，11-13）。"""
    found = item.get("bank_account") if isinstance(item.get("bank_account"), dict) else None
    if not found:
        return item.get("name") or item.get("prefix") or ""
    bank = " ".join(str(found.get("short") or "").split()) or " ".join(str(found.get("bank") or "").split())
    digits = "".join(ch for ch in str(found.get("number") or "") if ch.isdigit())[-4:]
    if not bank:
        return item.get("name") or item.get("prefix") or ""
    return f"{bank} ·{digits}" if digits else bank


def listed(transfers) -> list:
    """列给收款员选的转账方式（每种带 ``label``，:func:`account_label`）：两种以上方式进同一个户头的只列一种——这本账套原生收款单用得多的
    那种（``uses``），平了取编号小的。没报户头的照列。只影响列表；草稿里用了被收起的那种照样能写（规则按 :func:`catalog` 判）。"""
    keep = {}
    for item in transfers or []:
        code = str((item.get("bank_account") or {}).get("code") or "") if isinstance(item.get("bank_account"), dict) else ""
        if not code:
            continue
        best = keep.get(code)
        if best is None or (item.get("uses") or 0, _code_key(best["code"])) > (best.get("uses") or 0, _code_key(item["code"])):
            keep[code] = item
    out = []
    for item in transfers or []:
        code = str((item.get("bank_account") or {}).get("code") or "") if isinstance(item.get("bank_account"), dict) else ""
        if code and keep[code] is not item:
            continue
        out.append({**item, "label": account_label(item)})
    return out


def _code_key(code):
    text = str(code or "")
    return (0, int(text), text) if text.isdigit() else (1, 0, text)


def method_of(catalog_value, prefix):
    """按前缀认一种可用的方式（转账或折扣）：``(那一种, 角色)``；认不出回 ``(None, "")``。"""
    discount = catalog_value.get("discount")
    prefix = codes.choose(prefix, [item["prefix"] for item in catalog_value.get("transfers") or []] +
                          ([discount["prefix"]] if discount else []))
    for item in catalog_value.get("transfers") or []:
        if item["prefix"] == prefix:
            return item, TRANSFER
    if discount and discount["prefix"] == prefix:
        return discount, DISCOUNT
    return None, ""


# ── 余额与分配 ───────────────────────────────────────────────────────────────


def is_offset(rectyp) -> bool:
    """这一行是抵减单（从合计里扣）。"""
    return str(rectyp or "").strip() in OFFSET_RECTYPS


def available(remaining, reserved) -> Decimal:
    """快照余额减去别的记录的预留，不小于 0。"""
    return max(ZERO, money(remaining) - money(reserved or 0))


def allocate(rows, amount) -> list:
    """收款员只给了一个总数：按发票日期、单号从早到晚分（先收满早的，最后一张可部分收）。``rows`` 每张带 ``available``。
    分不完（总数大于这些发票能收的合计）回 :data:`OVER_COLLECTED`，``detail`` 带能收的合计。
    勾了的抵减单不参与分配（主会话 11-28）：整张能抵的都抵上，发票按「总数 + 抵减」分。"""
    offsets = [row for row in rows if is_offset(row.get("rectyp"))]
    rows = [row for row in rows if not is_offset(row.get("rectyp"))]
    left = money(amount, allow_zero=False) + sum((money(row.get("available") or 0) for row in offsets), ZERO)
    out = [{"docnum": row["docnum"], "amount": money(row.get("available") or 0)} for row in offsets
           if money(row.get("available") or 0) > 0]
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
    不重复、每种 > 0；收现金要这本账套有现金科目；发票合计 − 抵减单合计 = 现金 + 各方式（含折扣）；只有折扣、没有收到钱的拒；
    抵减单（:func:`is_offset`）同发票逐张核、可部分抵，抵减大于发票拒（:data:`OFFSETS_EXCEED`）；正好抵平（净额 0）不要方式；
    ``require_vouchers``（放行时）每种转账都要有凭证。"""
    invoices = list(draft.get("invoices") or [])
    if not invoices:
        raise CollectionError(INVOICES_EMPTY)
    if len(invoices) > MAX_INVOICES:
        raise CollectionError(INVOICES_TOO_MANY)
    seen, out_invoices, over = set(), [], {}
    for row in invoices:
        docnum = codes.choose(row.get("docnum"), open_rows)  # 复述时空白变了的单号换回快照里的原样
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
                             "department": found.get("department") or "", "rectyp": str(found.get("rectyp") or "")})
    if over:
        raise CollectionError(OVER_COLLECTED, {"available": over})
    if all(is_offset(row["rectyp"]) for row in out_invoices):
        raise CollectionError(INVOICES_EMPTY)
    out_invoices.sort(key=lambda row: is_offset(row["rectyp"]))  # 发票在前（第一张发票定销售员 / 部门），抵减单在后
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
    offset = sum((row["amount"] for row in out_invoices if is_offset(row["rectyp"])), ZERO)
    total = sum((row["amount"] for row in out_invoices if not is_offset(row["rectyp"])), ZERO) - offset
    if total < 0:
        raise CollectionError(OFFSETS_EXCEED, {"invoices": text(total + offset), "offsets": text(offset)})
    discount = sum((row["amount"] for row in out_methods if row["role"] == DISCOUNT), ZERO)
    transfer = sum((row["amount"] for row in out_methods if row["role"] == TRANSFER), ZERO)
    paid = cash + transfer + discount
    if paid != total:
        raise CollectionError(AMOUNT_MISMATCH, {"invoices": text(total), "paid": text(paid)})
    if cash + transfer <= 0 and not (total == 0 and offset > 0 and not out_methods):
        raise CollectionError(DISCOUNT_ONLY)
    return {"invoices": out_invoices, "cash": cash, "methods": out_methods, "total": total, "transfer": transfer,
            "discount": discount, "offset": offset}


def summaries(checked) -> dict:
    """列表与导出用的几栏：发票与各收多少、各方式（不含现金）。"""
    return {"invoice_summary": "; ".join(f"{row['docnum']} {'-' if is_offset(row.get('rectyp')) else ''}{text(row['amount'])}"
                                         for row in checked["invoices"])[:1000],
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
    """放行只认「确认卡展示的那个版本」+ 明确同意（LINE【确认】/ 打「ยืนยัน / 确认 / ok」、CC「写入」）：回空串可以放行；
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
    ``{ref_no, doc_date, customer, salesman, department, cash, invoices: [{docnum, amount, rectyp?}], methods: [{code, prefix, amount}]}``；
    抵减单（:func:`is_offset`）另放 ``offsets: [{docnum, rectyp, amount}]``、载荷盖 :data:`OFFSETS_PAYLOAD_VERSION`；没有抵减单时与以前逐字节相同。
    ``book``：账套（`books.for_company` 那一项：path、root、company）；``company``：``{tax_id, branch}``。
    账套位置几栏与其他写账载荷同名（领单时换成领取电脑的写法，`express_rules.push.wire_location`）。"""
    path = str(book.get("path") or "").strip()
    offsets = [row for row in record["invoices"] if is_offset(row.get("rectyp"))]
    out = {
        "direction": DIRECTION, "payload_version": OFFSETS_PAYLOAD_VERSION if offsets else PAYLOAD_VERSION,
        "ref_no": str(record["ref_no"])[:REF_MAX],
        "company": {"tax_id": str(company.get("tax_id") or ""), "branch": str(company.get("branch") or "")},
        "doc_date": str(record["doc_date"])[:10],
        "customer": str(record["customer"] or ""), "salesman": str(record.get("salesman") or ""),
        "department": str(record.get("department") or ""),
        "invoices": [{"docnum": row["docnum"], "amount": text(row["amount"])} for row in record["invoices"]
                     if not is_offset(row.get("rectyp"))],
        "cash": text(record.get("cash") or 0),
        "methods": [{"code": row.get("code") or "", "prefix": row["prefix"], "amount": text(row["amount"])}
                    for row in record.get("methods") or []],
        "user": str(user or "")[:140],
        "account_root": str(book.get("root") or "").strip(), "account_dir": path,
        "account_set": path, "account_company": str(book.get("company") or "").strip(),
        "account_set_row": 0, "respect_period_lock": True,
    }
    if offsets:
        out["offsets"] = [{"docnum": row["docnum"], "rectyp": str(row["rectyp"]).strip(), "amount": text(row["amount"])}
                          for row in offsets]
    return out


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
