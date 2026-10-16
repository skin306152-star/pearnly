"""每张单分三类，决定自动推送能不能推它（数字会计员第 3 期第 1、2 条，设计见 docs/work/2026-10-16-da-phase3-design.md）。

纯函数，不连库、不依赖框架：推送服务（`push.application.service.assess`，后续阶段）与离线回放（`scripts/da-replay.py`）共用。
取数由调用方做好放进 `Facts`；这里只判：

- **A 规则定**：每一项都由人或规则定（票面写明、账套记忆（科目要最近连续 3 张有证据的同科目）、公司规则、任务默认、v2 历史已定、
  按方向推断抵进项（原文核得到票种）……），且第 2 条核对全过；
- **B CC 判断**：核对全过，但至少一项是 CC 按数据推断的（分类映射、默认科目、供应商档案、银行佐证、还没攒够连续 3 张的记忆科目、
  按公司选的商品单入账方式……）；
- **C 要人**：有要人选的项、任一核对不过、或被硬闸拦下。

第 2 条的核对全部确定性、零费用，**只作内部信号**：原因码记在票上给 CC 与月结台账用，不出提示文案、不拦人推
（用户 10-07「录入口不加识别后提示」，「推送不查重不拦」只对人推）。图片单一律 C 是写在代码里的常量
（`IMAGES_ALLOWED`），随发布改，不是租户开关。
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from pearnly_core.tax_documents.amounts import valid_tax_id
from pearnly_core.tax_documents.codes import RECEIPT, SIMPLIFIED_TAX_INVOICE, TAX_INVOICE
from pearnly_core.tax_documents.rules import from_title

from pearnly_cowork.push.express import choices as choice_rules
from pearnly_cowork.push.express import company_choices
from pearnly_cowork.push.express.pushed import fit_ref_no
from pearnly_cowork.targets import account_match
from pearnly_cowork.targets.identity import normalize_express_account_key

A, B, C = "A", "B", "C"
CLASSES = (A, B, C)
# 图片单（拍照、扫描件、图片转的 PDF）一律 C，直到回放证明图片单单独过第 15 条达标线（照片扫描件已测错误约 6%）。
IMAGES_ALLOWED = False
# 金额勾稽的容差：同识别校验 `ocr.validation.math.check_amount_math`（站点 OCR_PIPELINE_AMOUNT_TOL 默认 .5）。
AMOUNT_TOLERANCE = Decimal("0.5")
# 单据日期不早于上传前这么多天（读错年月的票常落在很久以前）
EARLIEST_DAYS = 60
# 各层说法不一时，Express 历史只认这么多天内读的（旧的历史不拿来比）
HISTORY_FRESH_DAYS = 31
# 文字层里有这些字样：作废 / 退货单，交人
VOID_WORDS = ("ยกเลิก", "ใบรับคืน")
_VOID_LATIN = re.compile(r"\bvoid\b", re.IGNORECASE)
# 文字层 PDF 的页：识别链第一段是 PDF 文字层（`ocr.engine.runner.pipeline_page` 的 ``["text", "L2"]``）
TEXT_STAGE = "text"
# 文字层 PDF 的原件：每页平均至少这么多字（同识别取文字层的门槛 `ocr.inputs_runtime.source._text_pages`）。
# 生产的文字层快路关着（10-02 eval8：泰文字体乱码），电子 PDF 照图片直读，所以只看识别链判不出原件是不是电子 PDF。
TEXT_CHARS_PER_PAGE = 200
# 账套记忆定的科目（按供应商记一个）：这家供应商在这份账套里最近连续至少这么多张同科目才算规则定（A），否则 CC 判断（B）。
# 第 15 条第一轮回放：记忆定的科目 7.6% 与会计记的不同（一家供应商不止一个科目），开发窗口按数据定（10-16）
MEMORY_STREAK = 3
# 连续张数只数有证据的单（`memory_evidence`）：推的时候科目由人或规则定的（这张单上传后有人选的、任务默认、公司规则、Express
# 历史已定）；凭账套记忆推出去的单要被合格回读覆盖、没被改才算（CC 自己推的单不能攒成「连续 3 张」，审查处理 10-16）
EVIDENCE_SOURCES = frozenset({"chosen", "task_default", "company_rule", "express_history"})
CONFIRMED, CHANGED = "confirmed", "changed"
# 录入时由识别后处理推出、不是读出来的金额：简式税票按 7/107 拆的（`ocr.pages.INCLUDED_VAT_AUDIT`）、补出的折扣
INCLUDED_VAT_AUDIT = "vat_included"
DISCOUNT_INFERRED = "discount_inferred:"
ATTACHMENT_PAGE = "attachment_page:"

# 「人或规则定」的来源（`company_choices.decided_by` 的词；mixed = 同一类几项都来自这几层）
RULE_SOURCES = frozenset({"task_default", "remembered", "company_rule", "mixed"})
# 按对方作键记下的来源：对方没有 13 位税号时键是名字，通用店名会串到别家，最高算 B
KEYED_SOURCES = frozenset({"remembered", "company_rule", "express_history", "chosen", "mixed"})
# 现 / 赊哪层定的（载荷 doctype_src，`payment.payment_verdict` 六级漏斗与 mapper 的记过 / 任务默认）。票种口径 ``doc_type``
# 只有「收据即已付」是规则（A）；「完整税票多为赊」是推断（B，`_payment_tier`）：第 15 条回放销项按票种判赊 93% 与会计不符
# （回放造票种的口径另有偏差，见回放报告），没有能核实它的正确口径前不算人或规则定（审查处理，10-16）
PAYMENT_TIERS = {"manual": A, "explicit": A, "doc_type": A, "remembered": A, "task_default": A, "company_rule": A,
                 "profile": B, "bank": B, "config_default": B}
CASH_DOCTYPES = ("HP", "HS")
# 记的科目哪层定的（载荷 account_source）。express_history 另看版本（v2 才算 A）。
PURCHASE_ACCOUNT_TIERS = {"task_default": A, "remembered": A, "chosen": A, "company_rule": A,
                          "category_map": B, "config_default": B}
# 销项的收入科目：连接上的默认收入科目（config_default）同进项连接默认科目一样是兜底、不是为这张单定的，算 B（第 15 条回放
# 44% 与会计记的收入科目不符：有几个收入科目的公司，审查处理 10-16）
SALES_ACCOUNT_TIERS = {"config_default": B, "task_default": A, "remembered": A, "chosen": A, "company_rule": A,
                       "category_map": B}
HISTORY_VERSION = 2
STOCK_MODES = ("stock_item", "stock_sale")
_THAI_MONTHS = (("มกราคม", "ม.ค."), ("กุมภาพันธ์", "ก.พ."), ("มีนาคม", "มี.ค."), ("เมษายน", "เม.ย."),
                ("พฤษภาคม", "พ.ค."), ("มิถุนายน", "มิ.ย."), ("กรกฎาคม", "ก.ค."), ("สิงหาคม", "ส.ค."),
                ("กันยายน", "ก.ย."), ("ตุลาคม", "ต.ค."), ("พฤศจิกายน", "พ.ย."), ("ธันวาคม", "ธ.ค."))
_EN_MONTHS = (("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may", "may"),
              ("jun", "june"), ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"), ("oct", "october"),
              ("nov", "november"), ("dec", "december"))


@dataclass(frozen=True)
class Facts:
    """判一张单要的全部事实（调用方取好）。

    ``decision``：`push.express.decide.decide` 的结果（applied / needed / decided_by / history）；``payload``：预检通过时的
    Express 载荷（没出载荷为 None）；``config``：这份账套的配置（含 CC 桌面版上报的 ``reported_*`` 主档）；
    ``ready`` / ``blocked``：硬闸（`account_match`、`doc_sanity`、`preflight_express`、录不录口径）全过 / 拦下的原因；
    ``pages`` / ``review_reasons``：票上的识别页与复核原因；``text``：原件的 PDF 文字层（全部页连起来，没有为 None）；
    ``page_texts``：原件这几页各自的文字层（`history.text_layer.pages`，隐形识别文字层的页为空串；没给时 ``text`` 只当一页）；
    ``account`` / ``catalog``：推往账套在连接目录里的那一行 / 整个目录；``day_books``：按这张单日期选出的账套键
    （定时任务跟随公司当年账套 T1 同一判据，判不出为 None）；``uploaded``：上传日；``source_total``：同一原件拆出几张票；
    ``prior_pushes``：这份账套里成功推送或还在途中（准备好、排队、在写、结果不明）的单（`prior_entry` 的形状）；``layers`` / ``history_read``：各层对同一项给的值
    （`layers`）与 Express 历史读到的日子；``posting_from``：商品单的入账类型哪来的（`goods_posting.resolve`：``declared`` /
    ``company_choice``，空 = 没用上）；``account_recent``：这家供应商在这份账套里最近几张单记的科目（新的在前，不含这张；
    `memory_settled` 用，没给按 0 张算）。"""

    direction: str
    fields: Mapping[str, Any]
    decision: Mapping[str, Any]
    payload: Optional[Mapping[str, Any]]
    config: Mapping[str, Any]
    today: dt.date
    ready: bool = False
    blocked: str = ""
    pages: Sequence[Mapping[str, Any]] = ()
    review_reasons: Sequence[Any] = ()
    text: Optional[str] = None
    account: Optional[Mapping[str, Any]] = None
    catalog: Sequence[Mapping[str, Any]] = ()
    day_books: Optional[Sequence[str]] = None
    uploaded: Optional[dt.date] = None
    source_total: int = 1
    prior_pushes: Sequence[Mapping[str, Any]] = ()
    page_texts: Optional[Sequence[str]] = None
    layers: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    history_read: Optional[dt.date] = None
    posting_from: str = ""
    account_recent: Optional[Sequence[str]] = None


@dataclass(frozen=True)
class Verdict:
    """``cls``：A / B / C；``reasons``：C 的原因码（第 2 条核对、``item:<类>``、``conflict:<类>``），B 列 CC 判断的项
    ``judged:<类>``，A 为空；``items``：每一项的档 ``{类: A|B|C}``。"""

    cls: str
    reasons: Tuple[str, ...]
    items: Mapping[str, str]


# ── 小工具 ──────────────────────────────────────────────────────────────────────


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _money(value: Any) -> Optional[Decimal]:
    text = str(value if value is not None else "").replace(",", "").strip()
    if not text:
        return None
    try:
        amount = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return amount if amount.is_finite() else None


def _day(value: Any) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _branch5(value: Any) -> str:
    number = _digits(value)
    return (number[-5:] if number else "0").zfill(5)


def _party_side(direction: str) -> str:
    return "buyer" if direction == "sales" else "seller"


def _own_side(direction: str) -> str:
    return "seller" if direction == "sales" else "buyer"


def _attributes(row: Any) -> Mapping[str, Any]:
    found = row.get("attributes") if isinstance(row, Mapping) else None
    return found if isinstance(found, Mapping) else {}


def _payload(facts: Facts) -> Mapping[str, Any]:
    return facts.payload if isinstance(facts.payload, Mapping) else {}


def _invoice_date(facts: Facts) -> Optional[dt.date]:
    return _day(facts.fields.get("date") or facts.fields.get("invoice_date"))


# ── 第 2 条：原文对得上 ──────────────────────────────────────────────────────────


# 一个完整的数：前后不连着数字（逗号后跟数字也算连着），千分位逗号可有可无
_NUMBER = re.compile(r"(?<![\d.])(?<!\d,)(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?!\d|,\d|\.\d)")


def amount_in_text(amount: Any, text: Optional[str]) -> bool:
    """金额在文字层里作为一个完整的数出现（千分位逗号可有可无，小数位数不拘：1,070.00 / 1070 / 1070.0 都算）。"""
    wanted = _money(amount)
    if wanted is None or not text:
        return False
    for token in _NUMBER.findall(str(text)):
        found = _money(token)
        if found is not None and found == wanted:
            return True
    return False


def _years(text: str) -> List[int]:
    """票面年份的公历候选：4 位佛历（>2400）减 543；2 位按佛历与公历各算一次。"""
    year = int(text)
    if len(text) >= 4:
        return [year - 543 if year > 2400 else year]
    return [2500 + year - 543, 2000 + year]


def _dates(text: str) -> set:
    found = set()

    def add(year_text, month, day):
        for year in _years(year_text):
            try:
                found.add(dt.date(year, int(month), int(day)))
            except ValueError:
                pass

    for day, month, year in re.findall(r"(?<!\d)(\d{1,2})\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(\d{4}|\d{2})(?!\d)", text):
        add(year, month, day)  # 泰国票面写法：日 / 月 / 年
    for year, month, day in re.findall(r"(?<!\d)(\d{4})\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(\d{1,2})(?!\d)", text):
        add(year, month, day)
    folded = text.casefold()
    for index, names in enumerate(_THAI_MONTHS, 1):
        for name in names:
            pattern = rf"(?<!\d)(\d{{1,2}})\s*{re.escape(name)}\s*(\d{{4}}|\d{{2}})(?!\d)"
            for day, year in re.findall(pattern, folded):
                add(year, index, day)
    for index, names in enumerate(_EN_MONTHS, 1):
        name = "(?:" + "|".join(sorted(names, key=len, reverse=True)) + ")"
        for day, year in re.findall(rf"(?<!\d)(\d{{1,2}})[\s\-]*{name}\b\.?[\s\-,]*(\d{{4}}|\d{{2}})(?!\d)", folded):
            add(year, index, day)
        for day, year in re.findall(rf"\b{name}\b\.?\s*(\d{{1,2}}),?\s*(\d{{4}})(?!\d)", folded):
            add(year, index, day)
    return found


def date_in_text(day: Optional[dt.date], text: Optional[str]) -> bool:
    """单据日期在文字层里能找到原文（日 / 月 / 年数字写法、泰文或英文月名，佛历或公历）。"""
    return bool(day and text) and day in _dates(str(text))


# ── 第 2 条：自动推前的核对 ──────────────────────────────────────────────────────


def text_layer(pages: Sequence[Mapping[str, Any]], page_texts: Any = None) -> bool:
    """这张票的原件是文字层 PDF（不是拍照、扫描件、图片转的 PDF）：每一页都是从 PDF 文字层读的；或者原件这几页**每一页**的
    文字层（``page_texts``，`history.text_layer.pages`）都够 `TEXT_CHARS_PER_PAGE` 字 —— 生产里电子 PDF 也照图片直读，
    识别链第一段不是 ``text``（第 15 条回放查出）。逐页判：扫描发票页 + 长文字附页的混合 PDF、带隐形识别文字层的扫描件
    （那几页给空串，`history.pdf_text`）都不算（审查处理 10-16）。给一段字符串时只当一页。"""
    pages = [page for page in pages or () if isinstance(page, Mapping)]
    if not pages:
        return False
    if all(list(page.get("_layer_chain") or [])[:1] == [TEXT_STAGE] for page in pages):
        return True
    if isinstance(page_texts, str):
        page_texts = [page_texts]
    texts = [str(item or "") for item in page_texts or ()]
    return len(texts) >= len(pages) and all(len(item.strip()) >= TEXT_CHARS_PER_PAGE for item in texts)


def tax_in_text(tax: Any, text: Optional[str]) -> bool:
    """13 位税号在文字层里能找到原文（数字之间可夹空格、连字号：0-1055-12345-67-8 也算）。"""
    number = _digits(tax)
    if len(number) != 13 or not text:
        return False
    pattern = r"(?<!\d)" + r"[\s\-]?".join(number) + r"(?!\d)"
    return re.search(pattern, str(text)) is not None


def _notes(pages) -> List[str]:
    return [str(note) for page in pages or () if isinstance(page, Mapping)
            for note in [*(page.get("_validation_warnings") or []), *(page.get("_trigger_reasons") or [])]]


def _inferred(facts: Facts) -> bool:
    """金额是推出来的：未税、税额都没读到（`common.amounts` 按 7% 反推）、识别后按 7/107 拆的、补出的折扣。"""
    fields = facts.fields
    if _money(fields.get("subtotal")) is None and _money(fields.get("vat")) is None:
        return True
    for page in facts.pages or ():
        audit = page.get("_extraction_audit") if isinstance(page, Mapping) else None
        if isinstance(audit, Mapping) and INCLUDED_VAT_AUDIT in audit:
            return True
    return any(note.startswith(DISCOUNT_INFERRED) for note in _notes(facts.pages))


def _amounts_hold(fields: Mapping[str, Any]) -> bool:
    """未税 + 税额 ≈ 含税（容差同识别校验；票面整单折扣、预扣税的两种写法也算对得上）。"""
    sub, vat, total = (_money(fields.get(key)) for key in ("subtotal", "vat", "total_amount"))
    if sub is None or vat is None or total is None:
        return False
    discount = _money(fields.get("discount")) or Decimal(0)
    withheld = _money(fields.get("wht_amount")) or Decimal(0)
    gaps = [abs(sub + vat - total), abs(sub - discount + vat - total)]
    if withheld > 0:
        gaps += [abs(sub + vat - withheld - total), abs(sub - discount + vat - withheld - total)]
    return min(gaps) <= AMOUNT_TOLERANCE


def _period(account: Optional[Mapping[str, Any]]) -> Tuple[Optional[dt.date], Optional[dt.date]]:
    """账套整个会计期间的起止：本年起 ～ 次年段止（``next_end``，没报取本年止）。次年段的单只在新年度账套还没建时才推进旧账套：
    由「推往的须正好是按日期选出的那份」（``day_books``，`targets.account_match.for_day` 同一规则）把关（用户 2026-10-16）。"""
    attributes = _attributes(account)
    end = _day(attributes.get("next_end")) or _day(attributes.get("period_end"))
    return _day(attributes.get("period_start")), end if _day(attributes.get("period_end")) else None


def _target_key(facts: Facts) -> str:
    return normalize_express_account_key((facts.account or {}).get("source_id") or _payload(facts).get("account_set"))


def _swapped(day: dt.date) -> Optional[dt.date]:
    if day.day > 12 or day.day == day.month:
        return None
    try:
        return dt.date(day.year, day.day, day.month)
    except ValueError:
        return None


def _wrong_branch(facts: Facts, day: Optional[dt.date]) -> bool:
    """同一法人有 2 份以上分店账套时，票面须印本方分店号且与这份账套的分店一致（人推照旧按 `account_match`，
    票面没印分店时放行；自动推不放行）。"""
    attributes = _attributes(facts.account)
    tax = _digits(attributes.get("tax_id"))
    if len(tax) != 13 or not _digits(attributes.get("branch")):
        return False
    branches = account_match.company_branches(facts.catalog, tax, day=day, period=_period(facts.account))
    if len(branches) < 2:
        return False
    printed = account_match.printed_branch(facts.fields.get(f"{_own_side(facts.direction)}_branch"))
    return not printed or printed != _branch5(attributes.get("branch"))


def _evidence(pages) -> bool:
    """这张票挂了证据页（非发票页挂到相邻单据上、或识别丢下的附属页）。"""
    pages = [page for page in pages or () if isinstance(page, Mapping)]
    for index, page in enumerate(pages):
        fields = page.get("fields") if isinstance(page.get("fields"), Mapping) else {}
        flag = fields.get("is_not_invoice")
        if index and (flag is True or str(flag).strip().lower() == "true"):
            return True
    return any(note.startswith(ATTACHMENT_PAGE) for note in _notes(pages))


def prior_entry(payload: Any) -> Optional[Dict[str, str]]:
    """一条推送记录冻结的载荷 → 重复分流比的几项 ``{direction, party_tax_id, ref_no, total}``（不要 Express 单号：还在途中的
    记录也算）；不是 Express 进 / 销项载荷回 None。"""
    payload = payload if isinstance(payload, Mapping) else {}
    direction = payload.get("direction")
    if direction not in ("purchase", "sales"):
        return None
    side = "customer" if direction == "sales" else "supplier"
    party = payload.get(side) if isinstance(payload.get(side), Mapping) else {}
    return {"direction": direction, "party_tax_id": _digits(party.get("tax_id")),
            "ref_no": str(payload.get("ref_no") or "").strip(), "total": str(payload.get("total_amount") or "")}


def _same_push(facts: Facts) -> bool:
    """这份账套已推过（成功或还在途中）同对方税号、同单号、同含税额的单（自动推专用的重复分流，用户 10-16 定；人推照旧不查
    不拦）。同一轮扫描里先判的那张刚排进队列、还没写，也算（审查处理 10-16）。"""
    payload = _payload(facts)
    side = "customer" if facts.direction == "sales" else "supplier"
    party = payload.get(side) if isinstance(payload.get(side), Mapping) else {}
    tax = _digits(party.get("tax_id") or facts.fields.get(f"{_party_side(facts.direction)}_tax"))
    ref = fit_ref_no(payload.get("ref_no") or facts.fields.get("invoice_number")).upper()
    total = _money(payload.get("total_amount") or facts.fields.get("total_amount"))
    if len(tax) != 13 or not ref or total is None:
        return False
    for prior in facts.prior_pushes or ():
        if not isinstance(prior, Mapping) or prior.get("direction") not in (None, "", facts.direction):
            continue
        if (_digits(prior.get("party_tax_id")) == tax and fit_ref_no(prior.get("ref_no")).upper() == ref
                and _money(prior.get("total")) == total):
            return True
    return False


def _own(facts: Facts) -> Tuple[str, str]:
    """(推往账套的 13 位税号或空串, 票面印的本方税号数字)。"""
    account = _digits(_attributes(facts.account).get("tax_id"))
    return (account if len(account) == 13 else ""), _digits(facts.fields.get(f"{_own_side(facts.direction)}_tax"))


def _own_mismatch(facts: Facts, tax_invoice: bool) -> bool:
    account, printed = _own(facts)
    if not account:
        return False
    if printed:
        return printed != account
    return tax_invoice


def checks(facts: Facts) -> List[str]:
    """第 2 条：自动推前的核对 → 不过的原因码（全过回空表）。全部确定性、零费用、只作内部信号。"""
    found: List[str] = []
    fields, payload = facts.fields, _payload(facts)
    # 硬闸照旧：account_match、doc_sanity、preflight_express 全部 ready；有要人选的项
    if facts.blocked:
        found.append("blocked")
    if not facts.ready or not payload:
        found.append("not_ready")
    if (facts.decision or {}).get("needed"):
        found.append("needed")
    # 识别侧：识别自己说拿不准、或票上有复核原因（不拿分档 `_confidence_band` 当闸）
    if any(isinstance(page, Mapping) and page.get("_needs_manual_review") for page in facts.pages or ()):
        found.append("review_flag")
    if facts.review_reasons:
        found.append("review_reasons")
    # 票种：文字层 PDF 才能进 A / B
    text = facts.text if isinstance(facts.text, str) else ""
    is_text = text_layer(facts.pages, facts.page_texts if facts.page_texts is not None else text)
    if not is_text and not IMAGES_ALLOWED:
        found.append("image")
    day = _invoice_date(facts)
    if is_text:
        # 原文对得上：含税额与单据日期能在文字层里找到原文
        if not text.strip():
            found.append("text_missing")
        else:
            if not amount_in_text(payload.get("total_amount") or fields.get("total_amount"), text):
                found.append("text_total")
            if not date_in_text(day, text):
                found.append("text_date")
    # 金额是读出来的、不是推出来的；正式税票未税、税额都读到且勾稽成立（免税单税额为 0）
    tax_invoice = str(fields.get("document_type") or "").strip() == "tax_invoice"
    if is_text and text.strip() and tax_invoice and not tax_in_text(
            fields.get(f"{_party_side(facts.direction)}_tax"), text):
        found.append("text_tax")  # 正式税票印着对方税号：文字层里要找得到原文
    if _inferred(facts):
        found.append("amount_inferred")
    elif tax_invoice and not _amounts_hold(fields):
        found.append("amount_math")
    # 对方税号：正式税票 13 位、校验位对
    party_tax = _digits(fields.get(f"{_party_side(facts.direction)}_tax"))
    if tax_invoice and not (len(party_tax) == 13 and valid_tax_id(party_tax)):
        found.append("party_tax")
    # 本方：推往账套有 13 位税号时，票面印的本方税号要正好是它；正式税票必须印（`account_match` 只在本方读得出且校验位对时才拦，
    # 读错一位、没印都放行 —— 人推照旧，自动推不放行，审查处理 10-16）
    if _own_mismatch(facts, tax_invoice):
        found.append("own_tax")
    # 日期：在推往账套的会计期间内（含次年段）、正是按日期选出的那份（新年度账套已建就是新的那份）、不晚于今天、
    # 不早于上传前 60 天、日月对调不会两可
    if day is None:
        found.append("date_missing")
    else:
        start, end = _period(facts.account)
        if not start or not end:
            found.append("period_unknown")
        elif not start <= day <= end:
            found.append("period")
        books = None if facts.day_books is None else {normalize_express_account_key(key) for key in facts.day_books}
        if books is None or books != {_target_key(facts)}:
            found.append("book")
        earliest = (facts.uploaded or facts.today) - dt.timedelta(days=EARLIEST_DAYS)
        if day > facts.today:
            found.append("date_future")
        if day < earliest:
            found.append("date_stale")
        # 日、月都 ≤12 且对调后的日子也说得通（同样在期间内、不晚于今天、不早于上传前 60 天）：读法两可
        swapped = _swapped(day)
        if swapped and start and end and start <= swapped <= end and earliest <= swapped <= facts.today:
            found.append("day_month")
    if _wrong_branch(facts, day):
        found.append("branch")
    # 作废 / 退货、一拆多、挂证据页
    if any(word in text for word in VOID_WORDS) or _VOID_LATIN.search(text):
        found.append("void")
    if int(facts.source_total or 1) >= 2:
        found.append("split")
    if _evidence(facts.pages):
        found.append("evidence")
    if _same_push(facts):
        found.append("duplicate_number")
    return found


# ── 第 1 条：每一项按什么定 ──────────────────────────────────────────────────────


def _party_tier(facts: Facts, source: Optional[str]) -> str:
    if source in RULE_SOURCES:
        return A
    side = _party_side(facts.direction)
    tax = _digits(facts.fields.get(f"{side}_tax"))
    if len(tax) != 13:
        return C  # 不列对方、交 CC 桌面版按名字找
    rows = facts.config.get("reported_customers" if facts.direction == "sales" else "reported_suppliers")
    rows = [row for row in rows if isinstance(row, Mapping)] if isinstance(rows, list) else []
    if not rows:
        return C  # 这份账套的主档没报上来
    same = [row for row in rows if _digits(row.get("tax_id")) == tax]
    if not same:
        return B  # 没有同税号的档：照票面新建
    branch = _branch5(facts.fields.get(f"{side}_branch"))
    exact = [row for row in same if _branch5(row.get("branch")) == branch]
    return A if len(same) == 1 and exact else C


def _payment_tier(source: str, payload: Mapping[str, Any]) -> str:
    """现 / 赊的档：票种口径只有判成现购 / 现销（收据即已付）算 A，判成赊（完整税票多为赊）是推断，算 B。"""
    if source == "doc_type":
        return A if str(payload.get("doctype") or "").upper() in CASH_DOCTYPES else B
    return PAYMENT_TIERS.get(source, C)


def _stock_lines(payload: Mapping[str, Any]) -> bool:
    return any(isinstance(item, Mapping) and str(item.get("item_mode") or "") in STOCK_MODES
               for item in payload.get("items") or [])


def _items_known(facts: Facts) -> bool:
    """每一行库存行都有着落：这份账套记过 / 公司规则 / 任务默认给的存货码（`choices.resolve` 的 ``applied["items"]``，按归一品名），
    或 Express 有同名存货（归一后全等，同 `choices.resolve` 不问的那种）。只看「账套里有没有存货主档」不够：选「走库存」的
    公司里一张服务费也会出库存行（审查处理 10-16）。"""
    config = facts.config if isinstance(facts.config, Mapping) else {}
    rows = config.get("reported_products") if isinstance(config.get("reported_products"), list) else []
    names = {choice_rules.norm_name(row.get("name")) for row in rows
             if isinstance(row, Mapping) and str(row.get("kind") or "stock") == "stock"}
    applied = (facts.decision or {}).get("applied") if isinstance((facts.decision or {}).get("applied"), Mapping) else {}
    mapped = applied.get("items") if isinstance(applied.get("items"), Mapping) else {}
    lines = [item for item in _payload(facts).get("items") or []
             if isinstance(item, Mapping) and str(item.get("item_mode") or "") in STOCK_MODES]
    for item in lines:
        name = choice_rules.norm_name(str(item.get("name") or "")[:50])
        if not name or (name not in mapped and name not in names):
            return False
    return bool(lines)


def claim_in_text(direction: str, fields: Mapping[str, Any], text: Optional[str]) -> bool:
    """原件文字层里核得到「可抵进项的完整税票」：标题是完整税票（`rules.from_title`，「อย่างย่อ」简式税票、减项通知单都不算），
    买方（本方）13 位税号印在上面。识别把简式税票 / 收据读成完整税票（或反过来）时这一步拦得住（审查处理 10-16）。
    推送判档与回放识别闸层共用。"""
    text = text if isinstance(text, str) else ""
    return bool(text.strip()) and from_title(text) == TAX_INVOICE and \
        tax_in_text((fields or {}).get(f"{_own_side(direction)}_tax"), text)


def no_claim_in_text(text: Optional[str]) -> bool:
    """原件文字层的标题正是不能抵进项的票种（简式税票、收据；`rules.from_title` 先认「อย่างย่อ」、再认完整税票、最后才认收据，
    「ใบเสร็จรับเงิน/ใบกำกับภาษี」合一单算完整税票）。"""
    text = text if isinstance(text, str) else ""
    return bool(text.strip()) and from_title(text) in (SIMPLIFIED_TAX_INVOICE, RECEIPT)


def _claim_printed(facts: Facts) -> bool:
    return claim_in_text(facts.direction, facts.fields, facts.text)


def _no_claim_printed(facts: Facts) -> bool:
    return no_claim_in_text(facts.text)


def memory_evidence(entries: Iterable[Mapping[str, Any]]) -> List[str]:
    """这家供应商在这份账套里最近推成功的单（新的在前，``{account, source, readback}``：载荷记的科目与来源 ``account_source``、
    合格回读里的结果 `CONFIRMED` 覆盖且没被改 / `CHANGED` 被改 / 空 = 还没被合格回读覆盖）→ `memory_streak` 用的科目序列。
    回读里被改的记空串（断开连续）；科目由人或规则定的（`EVIDENCE_SOURCES`）照记；凭账套记忆推出去、还没回读核过的不算证据，
    跳过。推送服务（`push.application.assess._recent_accounts`）与回放共用。"""
    out: List[str] = []
    for entry in entries or ():
        if not isinstance(entry, Mapping):
            continue
        account = str(entry.get("account") or "").strip()
        if entry.get("readback") == CHANGED:
            out.append("")
        elif str(entry.get("source") or "") in EVIDENCE_SOURCES or entry.get("readback") == CONFIRMED:
            out.append(account)
    return out


def memory_streak(recent: Optional[Sequence[Any]], value: Any) -> int:
    """最近几张单（新的在前）连续记成 ``value`` 的张数；遇到别的科目（含一单多科目、没记科目）就断。"""
    value = str(value or "").strip()
    count = 0
    for item in recent or ():
        if not value or str(item or "").strip() != value:
            break
        count += 1
    return count


def memory_settled(recent: Optional[Sequence[Any]], value: Any) -> bool:
    """账套记忆定的科目算规则定：这家供应商在这份账套里最近连续至少 `MEMORY_STREAK` 张都记这个科目（推送服务与回放共用）。"""
    return memory_streak(recent, value) >= MEMORY_STREAK


def _account_tier(facts: Facts, source: str) -> str:
    if source == "remembered":
        # 账套记忆按供应商只记一个科目：最近连续 3 张同科目才算 A，否则 B（第一轮回放后定）
        return A if memory_settled(facts.account_recent, _payload(facts).get("items_account")) else B
    if source == "express_history":
        version = (facts.decision.get("history") or {}).get("version")
        return A if isinstance(version, int) and version >= HISTORY_VERSION else B
    table = SALES_ACCOUNT_TIERS if facts.direction == "sales" else PURCHASE_ACCOUNT_TIERS
    return table.get(source, C)


def _vat_applies(payload: Mapping[str, Any]) -> bool:
    return any((_money(payload.get(key)) or Decimal(0)) > 0 for key in ("vat_amount", "vat_capitalized"))


def tiers(facts: Facts) -> Dict[str, str]:
    """第 1 条每一项的档 ``{类: A|B|C}``：party 对方档、item 存货、account 费用 / 收入科目、payment 现 / 赊、vat 抵不抵进项、
    department 部门、receipt POS 收款科目、own 本方（票面印的本方税号对上推往账套）、posting 商品单入账方式（这家公司选过的，B）。
    用不上的项不列。没出载荷时只列对方档。"""
    payload, decided = _payload(facts), dict((facts.decision or {}).get("decided_by") or {})
    out: Dict[str, str] = {"party": _party_tier(facts, decided.get("party"))}
    if not payload:
        return out
    keyed: Dict[str, str] = {"party": decided.get("party") or ""}
    if _stock_lines(payload):
        out["item"] = A if _items_known(facts) else C
        keyed["item"] = decided.get("item") or ""
    if str(payload.get("items_account") or "").strip():
        source = str(payload.get("account_source") or "").strip()
        out["account"], keyed["account"] = _account_tier(facts, source), source
    if isinstance(payload.get("pos"), Mapping):
        out["payment"] = A  # POS 日汇总：按导出里写明的付款方式拆（`contracts.pos`）
        if payload["pos"].get("transfers"):
            out["receipt"] = A if decided.get("receipt") in RULE_SOURCES else C
    else:
        source = str(payload.get("doctype_src") or "").strip()
        out["payment"] = _payment_tier(source, payload)
        keyed["payment"] = source
    if facts.direction == "purchase" and _vat_applies(payload):
        source = str(payload.get("item_src") or "").strip()
        # 按方向推断（`field_rules.judge_direction`，全平台共用的法定口径 `purchase_treatment`）也算规则定：第 15 条第一轮回放
        # 13,878 张 0 错，开发窗口按数据从 B 改 A（10-16）。但回放的票种是按答案造的，量不到识别把简式税票读成完整税票（或反过来）
        # 的错，所以还要原文对得上（审查处理 10-16）：判成抵扣的，文字层里核得到完整税票标题与买方税号才算 A；判成不抵（记费用）的，
        # 文字层里却是印着买方税号的完整税票（识别读错了票种），算 B。核不到原文（泰文字体乱码等）时判成不抵的照 A（不多抵税）
        if source in ("manual", "choice"):
            out["vat"] = A
        elif source == "judge_direction=purchase_invoice":
            out["vat"] = A if _claim_printed(facts) else B
        elif source.startswith("judge_direction="):
            # 判不抵（记费用）：文字层标题要正是简式税票 / 收据才算 A；核不到（乱码、标题印成别的）算 B —— 第四轮回放闸层
            # 判不抵且核不到原文的 10 张里 9 张会计抵了进项（审查处理 10-16）
            out["vat"] = A if _no_claim_printed(facts) else B
        else:
            out["vat"] = C
        # 本票人工裁决（manual）不按对方作键；choice 来自按对方记下的那一层
        keyed["vat"] = (decided.get("vat") or "chosen") if source == "choice" else ""
    if facts.posting_from == "company_choice":
        # 商品单入账方式按公司选过的（`goods_posting`）：会交人的这类单不只是商品（进项所有可抵进项的完整税票、销项全部单），
        # 公司选的值只是这家多数单的做法。第四轮回放：按公司多数选，各家仍有 1%～6% 的单与会计的记法（有没有存货明细）不同，
        # 所以算 CC 判断（B），不算规则定（审查处理 10-16）
        out["posting"] = B
    out["department"] = A  # 记过的照用，没记过取单据簿默认部门或不记（不再问，用户 10-08）
    # 本方：票面印的本方税号正是推往账套的（A）；票面没印本方的收据 / 简式税票（B，去向是上传时选的，不是票面说的）。
    # 印了却对不上、正式税票没印由第 2 条 ``own_tax`` 归 C；账套没有 13 位税号核不了，不列
    account, printed = _own(facts)
    if account and (printed == account or not printed):
        out["own"] = A if printed else B
    # 按名字作键的来源最高算 B：对方没有 13 位税号时，账套记忆、公司规则、Express 历史都以名字为键
    if len(_digits(facts.fields.get(f"{_party_side(facts.direction)}_tax"))) != 13:
        for kind, source in keyed.items():
            if out.get(kind) == A and source in KEYED_SOURCES:
                out[kind] = B
    return out


# ── 各层说法不一 ────────────────────────────────────────────────────────────────


def layers(fields: Mapping[str, Any], *, direction: str, fixed: Optional[Mapping[str, str]] = None,
           remembered: Optional[Mapping[str, Mapping[str, str]]] = None, rules: Iterable[Mapping[str, Any]] = (),
           branch: str = "", history: Optional[str] = None) -> Dict[str, Dict[str, str]]:
    """各层对这张单同一项给的值 ``{类: {来源: 值}}``：任务默认（``fixed``）、账套记忆（``remembered``，叠公司规则之前的）、
    公司规则（这家公司这个分店适用的）、Express 历史已定的科目（``history``）。只看按对方记的几类与部门。"""
    side = _party_side(direction)
    name, tax, party_branch = fields.get(f"{side}_name"), fields.get(f"{side}_tax"), fields.get(f"{side}_branch")
    supplier = choice_rules.supplier_subject(direction, tax, name)
    subjects = {"party": choice_rules.party_subject(direction, tax, party_branch, name), "account": supplier,
                "payment": supplier, "vat": supplier, "department": "*"}
    picked = company_choices.pick(rules, branch)
    out: Dict[str, Dict[str, str]] = {}
    for kind, subject in subjects.items():
        found = {}
        task = str((fixed or {}).get(kind) or "").strip() if kind != "party" else ""
        if task:
            found["task_default"] = task
        mine = str(((remembered or {}).get(kind) or {}).get(subject) or "").strip()
        if mine:
            found["remembered"] = mine
        rule = (picked.get(kind) or {}).get(subject)
        value = str((rule or {}).get("value") or "").strip()
        if value:
            found["company_rule"] = value
        if kind == "account" and str(history or "").strip():
            found["express_history"] = str(history).strip()
        if found:
            out[kind] = found
    return out


def conflicts(facts: Facts) -> List[str]:
    """各层对同一项给的值不同（哪怕优先级能裁出一个）的类：只看这张单真用了这几层定的类（``decided_by`` 里有的）。
    Express 历史只认 ``HISTORY_FRESH_DAYS`` 天内读的。只作内部分流；提示与确认是第 4 期 `rule_conflict`。"""
    decided = (facts.decision or {}).get("decided_by") or {}
    fresh = facts.history_read is not None and (facts.today - facts.history_read).days <= HISTORY_FRESH_DAYS
    out = []
    for kind, found in (facts.layers or {}).items():
        if kind not in decided:
            continue
        values = {str(value).strip() for source, value in (found or {}).items()
                  if str(value or "").strip() and (source != "express_history" or fresh)}
        if len(values) >= 2:
            out.append(kind)
    return out


# ── 分类 ────────────────────────────────────────────────────────────────────────


def classify(facts: Facts) -> Verdict:
    """第 1 条：每张单分 A / B / C（见文件头）。"""
    items = tiers(facts)
    reasons = checks(facts)
    reasons += [f"item:{kind}" for kind, tier in items.items() if tier == C]
    reasons += [f"conflict:{kind}" for kind in conflicts(facts)]
    if reasons:
        return Verdict(C, tuple(dict.fromkeys(reasons)), items)
    judged = tuple(f"judged:{kind}" for kind, tier in items.items() if tier == B)
    return Verdict(B if judged else A, judged, items)


def reason_codes(verdict: Verdict, limit: int = 140) -> str:
    """存进票上 ``auto_reasons`` 的简表：原因码逗号相连，截到 ``limit`` 字（第 9 条，≤140 字）。"""
    text = ""
    for code in verdict.reasons:
        candidate = f"{text},{code}" if text else code
        if len(candidate) > limit:
            break
        text = candidate
    return text


__all__ = ["A", "amount_in_text", "AMOUNT_TOLERANCE", "B", "C", "checks", "claim_in_text", "CLASSES", "classify",
           "conflicts", "date_in_text", "EARLIEST_DAYS", "EVIDENCE_SOURCES", "Facts", "HISTORY_FRESH_DAYS",
           "IMAGES_ALLOWED", "layers", "memory_evidence", "memory_settled", "MEMORY_STREAK", "memory_streak",
           "no_claim_in_text", "prior_entry", "reason_codes", "tax_in_text", "TEXT_CHARS_PER_PAGE", "text_layer",
           "tiers", "Verdict"]
