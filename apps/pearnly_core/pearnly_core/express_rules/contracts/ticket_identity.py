"""同一张票的统一身份（复核 5 批之第 1 批；设计 ``docs/work/2026-11-09-cc-misuse-graceful-design.md`` 第 1 层 1.1、1.4）。

纯契约：只用标准库与同样纯的 ``targets.account_match``，不读库、不依赖框架。两产品（Cowork 识别单、ERP 收单记录与单据）、
两模式（直写、代录）、两账本（Express、MR.ERP）判「是不是同一张票」都调这一份，判重口径只有这一处。

- 身份键 =（方向，卖方税号，卖方分店，买方税号，买方分店，规范化单号，开票日期，含税总额）。
- 规范化单号：全角转半角（NFKC），去掉空白和 ``/ - . _``，转大写。``IV/69/00751``、``IV69-00751``、``iv69 00751`` 是同一个号。
- 分店用票面印的（``account_match.printed_branch``）：没印回空串，「总公司」是 00000，数字取 5 位。没印和总公司不当成同一个。
- 金额用 ``Decimal`` 到 0.01；日期是公历 ``YYYY-MM-DD``（佛历差 543 在识别侧已纠正）。
- 一方没有税号（个人、境外）时，这一方用规范化后的名称代替，身份降为「近似」（``NEAR``）。近似只标不跳，见 :func:`same`。
- 比的范围（同一公司、同一账本目标）由调用方定，不进身份键；不按人分（两个人各传一份也是同一张）。
- 单号、日期、金额缺任何一项：没有身份（回 None），调用方不把它当成同一张。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import re
import unicodedata

from pearnly_core.express_rules.targets.account_match import printed_branch

EXACT = "exact"  # 票面字段全对上：就是这一张
NEAR = "near"  # 一方没有税号、按名称对上的；或单号只差一两个字（`looks_alike`）：只标不跳
PURCHASE, SALES = "purchase", "sales"
KEY_LENGTH = 40
NEAR_EDITS = 2  # 单号差几个字以内算「像」

_DROP = re.compile(r"[\s/\-._]+")
_NAME_DROP = re.compile(r"[\s\W_]+", re.UNICODE)
_SEP = "\x1f"


@dataclass(frozen=True)
class Face:
    """一张票的票面（产品从自己的记录里取出来交给这里）。"""

    direction: str
    number: str
    date: object
    total: object
    seller_tax: str = ""
    seller_branch: str = ""
    buyer_tax: str = ""
    buyer_branch: str = ""
    seller_name: str = ""
    buyer_name: str = ""


@dataclass(frozen=True)
class Identity:
    key: str
    level: str  # EXACT / NEAR


def direction(value) -> str:
    text = str(value or "").strip().casefold()
    if text.startswith("purch") or text in ("buy", "in", "expense"):
        return PURCHASE
    if text.startswith("sale") or text in ("sell", "out", "income"):
        return SALES
    return text


def number(value) -> str:
    return _DROP.sub("", unicodedata.normalize("NFKC", str(value or ""))).upper()


def tax(value) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKC", str(value or "")) if ch.isdigit())


def name(value) -> str:
    return _NAME_DROP.sub("", unicodedata.normalize("NFKC", str(value or "")).casefold())


def money(value):
    """含税总额到 0.01；读不出回 None。"""
    if value is None or str(value).strip() == "":
        return None
    try:
        return Decimal(str(value).replace(",", "").strip()).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def day(value) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return ""


def _side(tax_value, branch_value, name_value) -> tuple[str, bool]:
    found = tax(tax_value)
    if found:
        return f"{found}:{printed_branch(branch_value)}", True
    return "name:" + name(name_value), False


def identity(face: Face) -> Identity | None:
    """这张票的身份；单号、日期、金额缺一项回 None（不能判，调用方不当成同一张）。"""
    numbered, dated, total = number(face.number), day(face.date), money(face.total)
    if not numbered or not dated or total is None:
        return None
    seller, seller_known = _side(face.seller_tax, face.seller_branch, face.seller_name)
    buyer, buyer_known = _side(face.buyer_tax, face.buyer_branch, face.buyer_name)
    if seller == "name:" or buyer == "name:":
        return None  # 一方既没税号也没名称：认不出是谁的票
    parts = (direction(face.direction), seller, buyer, numbered, dated, f"{total:.2f}")
    key = hashlib.sha256(_SEP.join(parts).encode("utf-8")).hexdigest()[:KEY_LENGTH]
    return Identity(key, EXACT if seller_known and buyer_known else NEAR)


def _edits(a: str, b: str, limit: int) -> int:
    """两个号差几个字（编辑距离），超过 ``limit`` 就提前停，回 limit + 1。"""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    previous = list(range(len(b) + 1))
    for i, left in enumerate(a, start=1):
        current = [i]
        for j, right in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (left != right)))
        if min(current) > limit:
            return limit + 1
        previous = current
    return previous[-1]


def looks_alike(first: Face, second: Face) -> bool:
    """近似：其余票面字段都对上、单号只差一两个字（截图把 IV69/00751 读成 IV/69/06751）。不自动跳过（连号同额同日的票很常见），
    只给复核卡标出来让人决定。"""
    a, b = identity(first), identity(second)
    if a is None or b is None or a.key == b.key:
        return False
    same_rest = (direction(first.direction), _side(first.seller_tax, first.seller_branch, first.seller_name),
                 _side(first.buyer_tax, first.buyer_branch, first.buyer_name), day(first.date), money(first.total)) == \
        (direction(second.direction), _side(second.seller_tax, second.seller_branch, second.seller_name),
         _side(second.buyer_tax, second.buyer_branch, second.buyer_name), day(second.date), money(second.total))
    return same_rest and _edits(number(first.number), number(second.number), NEAR_EDITS) <= NEAR_EDITS


def same(first: Face, second: Face) -> str:
    """两张票是不是同一张：``EXACT``（就是这一张，按已写过跳过）、``NEAR``（像，只标不跳）或空串（不是）。"""
    a, b = identity(first), identity(second)
    if a is not None and b is not None and a.key == b.key:
        return a.level
    return NEAR if looks_alike(first, second) else ""
