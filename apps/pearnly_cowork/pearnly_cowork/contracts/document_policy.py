"""哪些单据录不录由这个租户自己选（用户 2026-10-07：「规则不应该写死，无法判断有争议的能让用户做选择」）。纯函数，不连库。

口径类（各家记账习惯不同）：转账凭条、预扣税凭证 50 ทวิ。识别只认类别；这个租户选一次「录入 / 不录入」，之后同类
自动套用。选「不录入」的，识别存档时直接按非发票处理；没选过的推送前停下来等人选。

类别来自识别读数，不为它多调模型或加长提示词（用户 2026-10-07 选零成本）：转账凭条是票种 ``payment_evidence``；
预扣税凭证是原文标题认出的 ``document_flag``（`ocr.engine.runner.mark_title`），或读数本身就是凭证的样子
（:func:`certificate_shape`：没有原文、标题规则没机会跑的页，2026-10-13 测试机）。

认类别只在这里一处（:func:`document_class`）：识别存档（`ocr.results.prepare_history_batch` 调 :func:`apply_to_pages`，
真识别、Cloud Run 回来的、回放压测取回的旧结果都走这一步）、推送前的门槛、复核页的选择、CC 与 ภ.ง.ด.3 / 53 草稿
（`tax.wht`）都用它，同一张单据不因读法或入口不同而结果不同。

同一份存储另记**按公司**选一次的商品单入账方式（数字会计员第 3 期，用户 2026-10-16「按公司问一次记住」）：永续 / 混合库存账套的
商品单（没声明入账类型时推送交人，`push.express.posting_profile`）这家公司选「走库存 / 记费用」，键 ``goods_posting|<公司身份>``
（公司身份同 `companies.identity.company_identity` 的 ``key``），值 ``stock`` / ``service``。没选过的照旧交人；怎么用见
`push.express.goods_posting`。

同一份存储还记**真有两份同期账套时哪份是现用的**（数字会计员第 3 期，用户 2026-10-17「按公司问一次」）：按日期选账套
（`targets.account_match.for_day`）选出多份时，这家公司、这组分店、本年或次年段、这一组候选问一次，键
``current_book|<公司身份>|<分店>|<cy 或 ny>|<候选账套键排序后的 sha1 前 12 位>``，值是选中的规范账套键。候选组合在键里：
新建了账套、组合变了就是另一个键，重新问，旧选择不会把新一年的单推进旧账套。没选过的照旧停下；怎么用见 `companies.current_book`。

记住回答时另记一项「当时各候选的最新录入」（用户 2026-10-18 同意加重问：另一份候选账套在上次回答之后有了更新录入就再问一次，
仍不自动挑最近有录入的那份）：键 ``current_book_seen|…``（同一组的 ``current_book|…`` 换前缀），值 ``<候选键 sha1 前 8 位>=<标记>``
按位排序、逗号隔开（标记是 ISO 时刻，没有为空，:func:`book_marks`）。之后**未被选中**的某份此刻的标记晚于记下的
（:func:`current_book_newer`）就当没选过、照旧停下再问；选中的那份自己有新录入不算。旧存储没有这一项（或认不得）的回答照旧可用、
不会因此重问。标记取什么见 `companies.current_book.marks`。

标记后面可以再带一段 **录入标记**（第 3 期 E3，CC 桌面版 1.9.1 起）：``<ISO 时刻>~<YYYY-MM-DD>/<记录数>``，是 CC 桌面版在账套目录
每一行另报的 Express ``GLJNL.DBF`` 表头（最后更新日期只到天、记录数，:func:`entry_mark`），会计不经 Pearnly 直接在 Express 里录的
单也看得到。比较时这一段「记录数变了或日期更晚」就算有新录入；回答时或此刻有一边没有这一段（旧版 CC 桌面版、回答记在 1.9.1 之前）
就只比前面的时刻，不因升级多问一次。
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from typing import Any, Dict, Iterable, Mapping, Optional

from pearnly_core.tax_documents.amounts import digits, money, repaired_tax_id, valid_tax_id

CERTIFICATE = "withholding_certificate"
POLICY_CLASSES = ("payment_evidence", CERTIFICATE)
ENTER, SKIP = "enter", "skip"
VALUES = (ENTER, SKIP)
NEEDED_PREFIX = "document_policy_needed_"
SKIPPED_PREFIX = "document_policy_skip_"
WARNING_PREFIX = "document_policy:"
# 按公司记的商品单入账方式（见文件头）：键 ``goods_posting|<公司身份>``，值同推送的入账类型 `contracts.push.VALID_POSTING_KINDS`
GOODS_POSTING = "goods_posting"
GOODS_POSTING_VALUES = ("stock", "service")
GOODS_POSTING_KEY_MAX = 240
# 两份同期时现用的那份（见文件头）：键 ``current_book|<公司身份>|<分店>|<cy / ny>|<候选摘要>``，值是规范账套键
CURRENT_BOOK = "current_book"
CURRENT_BOOK_PARTS = ("cy", "ny")
BOOK_KEY_MAX = 500  # 同 `targets.identity.normalize_express_account_key` 的上限
# 记回答时各候选的最新录入标记（见文件头）：键 ``current_book_seen|…``，值 ``<8 位>=<ISO 时刻或空>,…``
CURRENT_BOOK_SEEN = "current_book_seen"
SEEN_ID = 8
_MARK = r"[0-9T:.\-]{0,32}(?:~\d{4}-\d{2}-\d{2}/\d{1,10})?"
_SEEN_VALUE = re.compile(rf"[0-9a-f]{{8}}={_MARK}(?:,[0-9a-f]{{8}}={_MARK})*")
# 录入标记（见文件头）：Express GLJNL.DBF 表头的最后更新日期与记录数，CC 桌面版账套目录每行的 ``entry_mark``
ENTRY_SEP = "~"
_ENTRY = re.compile(r"(\d{4}-\d{2}-\d{2})/(\d{1,10})")


def _not_invoice(fields: Mapping[str, Any]) -> bool:
    value = fields.get("is_not_invoice")
    return value.strip().lower() == "true" if isinstance(value, str) else value is True


def _tax_id(value) -> str:
    """有效的 13 位泰国税号（读丢开头 0 的按共用规则补回，MOD-11 校验位要对）；不是回空串。
    个人的 13 位身份证号与法人税号是同一种号码、同一种校验位。"""
    found = digits(repaired_tax_id(str(value or "").strip()))
    return found if len(found) == 13 and valid_tax_id(found) else ""


def certificate_shape(fields: Mapping[str, Any]) -> bool:
    """读数本身就是 50 ทวิ 的样子：已判「不是发票」、印了预扣税额（大于 0），付款方与收款方两边都有各自有效的 13 位
    税号（收款方是个人时是他的 13 位身份证号）。

    2026-10-13 测试机两张真凭证（扫描 PDF、手机照片）：结果里没有原文，标题规则（`ocr.engine.runner.mark_title`）没机会
    跑，回来的是不是发票、类别空着、读出预扣税额，付款公司在卖方一栏、收款方（公司或个人）在买方一栏。只认已判「不是发票」
    的页，按票读的页（印了预扣税额的费用票）从不因此改成凭证；只有一边有税号的信函、没有预扣税额的转账凭条都不算。"""
    fields = fields if isinstance(fields, Mapping) else {}
    if not _not_invoice(fields) or (money(fields.get("wht_amount")) or 0) <= 0:
        return False
    payer, payee = _tax_id(fields.get("seller_tax")), _tax_id(fields.get("buyer_tax"))
    return bool(payer and payee and payer != payee)


def document_class(fields: Mapping[str, Any]) -> str:
    """口径类类别；不是口径类回空。先认原文标题打的类别，再认票种（转账凭条），最后认读数的样子（:func:`certificate_shape`）。"""
    fields = fields if isinstance(fields, Mapping) else {}
    flag = str(fields.get("document_flag") or "")
    if flag in POLICY_CLASSES:
        return flag
    if fields.get("document_type") == "payment_evidence":
        return "payment_evidence"
    return CERTIFICATE if certificate_shape(fields) else ""


def goods_posting_key(company: Any) -> str:
    """一家公司商品单入账方式的键；公司身份为空或太长回空串（不记）。"""
    company = str(company or "").strip()
    key = f"{GOODS_POSTING}|{company}" if company else ""
    return key if len(key) <= GOODS_POSTING_KEY_MAX else ""


def goods_posting(policy: Mapping[str, str], company: Any) -> Optional[str]:
    """这家公司选过的商品单入账方式（``stock`` / ``service``）；没选过回 None。"""
    key = goods_posting_key(company)
    value = (policy or {}).get(key) if key else None
    return value if value in GOODS_POSTING_VALUES else None


def _book_keys(candidates: Iterable[Any]) -> list:
    return sorted({str(item or "").strip() for item in candidates or () if str(item or "").strip()})


def current_book_key(company: Any, branch: Any, part: Any, candidates: Iterable[Any]) -> str:
    """一组同期候选账套（调用方给规范账套键）问「哪份是现用的」的键；公司身份为空、候选不到两份、段不是本年 / 次年、
    键太长回空串（不记，照旧停下）。"""
    company, branch, keys = str(company or "").strip(), str(branch or "").strip(), _book_keys(candidates)
    if not company or part not in CURRENT_BOOK_PARTS or len(keys) < 2 or "|" in branch:
        return ""
    digest = hashlib.sha1("\n".join(keys).encode("utf-8"), usedforsecurity=False).hexdigest()[:12]
    key = f"{CURRENT_BOOK}|{company}|{branch}|{part}|{digest}"
    return key if len(key) <= GOODS_POSTING_KEY_MAX else ""


def current_book(policy: Mapping[str, str], key: Any, candidates: Iterable[Any]) -> Optional[str]:
    """这一组候选里选过的现用账套（规范账套键）；没选过、或选的已不在候选里回 None。"""
    value = (policy or {}).get(key) if key else None
    return value if isinstance(value, str) and value in _book_keys(candidates) else None


def current_book_seen_key(key: Any) -> str:
    """一组候选的 ``current_book|…`` 键 → 记回答时各候选最新录入标记的键（换前缀）；不是这种键、换了太长回空串（不记标记）。"""
    if not isinstance(key, str) or not key.startswith(CURRENT_BOOK + "|"):
        return ""
    seen = CURRENT_BOOK_SEEN + key[len(CURRENT_BOOK):]
    return seen if len(seen) <= GOODS_POSTING_KEY_MAX else ""


def entry_mark(value: Any) -> str:
    """CC 桌面版报的录入标记（``YYYY-MM-DD/记录数``，GLJNL.DBF 表头）→ 规范写法；空、认不出回空串（当作没报）。"""
    found = _ENTRY.fullmatch(str(value or "").strip())
    if not found:
        return ""
    try:
        day = date.fromisoformat(found.group(1))
    except ValueError:
        return ""
    return f"{day.isoformat()}/{int(found.group(2))}"


def _split_mark(value: Any) -> tuple:
    """存的标记 → (时刻部分, 录入标记)；不是字符串的（datetime / date）整个当时刻。"""
    if isinstance(value, str) and ENTRY_SEP in value:
        written, _sep, entry = value.partition(ENTRY_SEP)
        return written, entry_mark(entry)
    return value, ""


def book_mark(value: Any, entry: Any = None) -> str:
    """一份账套的「最新录入」标记 → 存的写法（ISO 时刻到微秒；日期按当天零点；有录入标记接在 ``~`` 后面，见文件头）；
    时刻空、认不出为空（当作没有经 Pearnly 的录入），两样都没有回空串。``value`` 也可以是已存的写法。"""
    value, stored_entry = _split_mark(value)
    entry = entry_mark(entry) if entry is not None else stored_entry
    written = _written_mark(value)
    return f"{written}{ENTRY_SEP}{entry}" if entry else written


def _written_mark(value: Any) -> str:
    if isinstance(value, datetime):
        found = value.replace(tzinfo=None)
    elif isinstance(value, date):
        found = datetime(value.year, value.month, value.day)
    else:
        text = str(value or "").strip()
        try:
            found = datetime.fromisoformat(text).replace(tzinfo=None) if text else None
        except ValueError:
            found = None
    return found.isoformat(timespec="microseconds") if found else ""


def _seen_id(book_key: str) -> str:
    return hashlib.sha1(book_key.encode("utf-8"), usedforsecurity=False).hexdigest()[:SEEN_ID]


def book_marks(marks: Mapping[str, Any]) -> str:
    """回答时各候选（``{规范账套键: 最新录入}``）→ 存的值（`book_mark` 写法，按位排序）；没有候选、位撞了、太长回空串（不记标记，
    这一组的回答照旧可用、不因新录入重问）。"""
    pairs = {}
    for key in _book_keys(marks or {}):
        ident = _seen_id(key)
        if ident in pairs:
            return ""
        pairs[ident] = book_mark((marks or {}).get(key))
    value = ",".join(f"{ident}={pairs[ident]}" for ident in sorted(pairs))
    return value if value and len(value) <= BOOK_KEY_MAX and _SEEN_VALUE.fullmatch(value) else ""


def current_book_seen(policy: Mapping[str, str], key: Any, candidates: Iterable[Any]) -> Optional[Dict[str, str]]:
    """这一组回答时记下的各候选最新录入（``{规范账套键: 标记}``，没有录入为空串）；旧存储没有这一项、认不得、记的不是这一组
    候选回 None（照旧用记住的回答、不重问）。"""
    seen_key = current_book_seen_key(key)
    raw = (policy or {}).get(seen_key) if seen_key else None
    if not isinstance(raw, str) or not _SEEN_VALUE.fullmatch(raw):
        return None
    stored = dict(item.split("=", 1) for item in raw.split(","))
    keys = {_seen_id(book): book for book in _book_keys(candidates)}
    if set(stored) != set(keys):
        return None
    return {keys[ident]: book_mark(mark) for ident, mark in stored.items()}


def _entry_newer(current: str, before: str) -> bool:
    """录入标记（``YYYY-MM-DD/记录数``）：记录数变了（多录、删了又整理）或日期更晚算有新录入；有一边没有不算。"""
    if not current or not before:
        return False
    (day, count), (old_day, old_count) = current.split("/"), before.split("/")
    return count != old_count or day > old_day


def current_book_newer(seen: Mapping[str, Any], now: Mapping[str, Any], chosen: Any) -> bool:
    """记住的回答还算不算数（用户 2026-10-18）：**未被选中**的候选里有一份此刻的最新录入（``now``）晚于回答时记下的（``seen``，
    当时没有录入的按最早算）→ True，要重问；选中的那份自己有新录入不算，此刻没有标记的不算。两边都带录入标记（E3）时，
    它的记录数变了或日期更晚也算（:func:`_entry_newer`）。"""
    for key, mark in (now or {}).items():
        if key == chosen or key not in (seen or {}):
            continue
        current, before = _split_mark(book_mark(mark)), _split_mark(book_mark(seen[key]))
        if current[0] and (not before[0] or datetime.fromisoformat(current[0]) > datetime.fromisoformat(before[0])):
            return True
        if _entry_newer(current[1], before[1]):
            return True
    return False


def current_book_parts(key: Any, company: Any) -> Optional[tuple]:
    """``current_book|…`` 键是不是这家公司的：是 → ``(分店, "cy" / "ny")``（主档页「记住的选择」显示与删用）；不是回 None。"""
    company = str(company or "").strip()
    prefix = f"{CURRENT_BOOK}|{company}|"
    if not company or not isinstance(key, str) or not key.startswith(prefix):
        return None
    parts = key[len(prefix):].split("|")
    if len(parts) != 3 or parts[1] not in CURRENT_BOOK_PARTS or len(parts[2]) != 12:
        return None
    return parts[0], parts[1]


def company_keys(policy: Mapping[str, str], company: Any) -> Dict[str, str]:
    """这家公司在口径里按公司记的几项（商品单入账方式、两份同期时现用的账套）：``{键: 值}``，按键排序。"""
    goods = goods_posting_key(company)
    return {key: value for key, value in sorted((policy or {}).items())
            if (goods and key == goods) or current_book_parts(key, company)}


def _known(key: Any, value: Any) -> bool:
    if key in POLICY_CLASSES:
        return value in VALUES
    if not isinstance(key, str) or len(key) > GOODS_POSTING_KEY_MAX:
        return False
    if key.startswith(GOODS_POSTING + "|"):
        return value in GOODS_POSTING_VALUES
    if key.startswith(CURRENT_BOOK_SEEN + "|"):
        return isinstance(value, str) and len(value) <= BOOK_KEY_MAX and bool(_SEEN_VALUE.fullmatch(value))
    return (key.startswith(CURRENT_BOOK + "|") and isinstance(value, str)
            and 0 < len(value) <= BOOK_KEY_MAX and value == value.strip())


def normalize(raw: Any) -> Dict[str, str]:
    """存着的口径 → 只留认得的类别与取值（含按公司记的商品单入账方式、两份同期时现用的账套与回答时各候选的最新录入）。"""
    return {key: value for key, value in (raw if isinstance(raw, Mapping) else {}).items() if _known(key, value)}


def push_block(fields: Mapping[str, Any], policy: Mapping[str, str]) -> Optional[str]:
    """推送前的门槛 → 原因码（复核页按码显示文案与按钮），能推回 ``None``。"""
    kind = document_class(fields)
    if kind and policy.get(kind) == SKIP:
        return SKIPPED_PREFIX + kind
    if kind and policy.get(kind) != ENTER:
        return NEEDED_PREFIX + kind
    return None


def apply_to_pages(pages, policy: Mapping[str, str]) -> None:
    """识别存档前（逐页展开的读数，就地改）：口径类按这个租户的选择定这一页算不算票。

    选了「不录入」→ 记成非发票；选了「录入」或还没选 → 算票（还没选的推送前会停下来问）。原文标题认出的预扣税凭证
    在引擎里已判成非发票，这里同样按选择来，同一类单据不因读法不同而结果不同。

    按读数样子认出的凭证（:func:`certificate_shape`）先把类别写进 ``document_flag``：之后算票的页不再是「不是发票」，
    类别靠这个标记留着，推送门槛、参考记录与报税草稿看到的和原文标题认出的完全一样。"""
    for page in pages:
        fields = page.get("fields") if isinstance(page, dict) else None
        if not isinstance(fields, dict):
            continue
        kind = document_class(fields)
        if not kind:
            continue
        if kind == CERTIFICATE and fields.get("document_flag") != CERTIFICATE:
            fields["document_flag"] = CERTIFICATE
            page.setdefault("_validation_warnings", []).append(f"{WARNING_PREFIX} shape {kind}")
        skip = policy.get(kind) == SKIP
        if bool(fields.get("is_not_invoice")) != skip:
            fields["is_not_invoice"] = skip
            page.setdefault("_validation_warnings", []).append(f"{WARNING_PREFIX} {policy.get(kind) or 'ask'} {kind}")


def skipped_certificates(pages, policy: Mapping[str, str]) -> list:
    """这个租户选了「不录入」的预扣税凭证页：[(在读数里的序号, 页)]。只存一条参考记录给报税草稿读
    （`history.certificates`，用户 2026-10-13），不成票、不进录入待办、不推送、不扣费。"""
    if policy.get(CERTIFICATE) != SKIP:
        return []
    found = []
    for position, page in enumerate(pages or []):
        fields = page.get("fields") if isinstance(page, dict) else None
        if isinstance(fields, dict) and document_class(fields) == CERTIFICATE:
            found.append((position, page))
    return found


__all__ = ["CERTIFICATE", "CURRENT_BOOK", "CURRENT_BOOK_PARTS", "CURRENT_BOOK_SEEN", "ENTER", "GOODS_POSTING",
           "GOODS_POSTING_VALUES", "NEEDED_PREFIX", "POLICY_CLASSES", "SKIP", "SKIPPED_PREFIX", "VALUES", "apply_to_pages",
           "book_mark", "book_marks", "entry_mark", "certificate_shape", "company_keys", "current_book", "current_book_key",
           "current_book_newer", "current_book_parts", "current_book_seen", "current_book_seen_key", "document_class",
           "goods_posting", "goods_posting_key", "normalize", "push_block", "skipped_certificates"]
