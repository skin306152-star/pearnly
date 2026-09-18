# -*- coding: utf-8 -*-
"""DMS 订车逐问要的纯函数工具：文本归一 / 金额解析 / 主档行取标签。

无状态叶子：零 IO、零会话，也不反向依赖状态机本体（状态机单向 import 这里）。
**金额一律 `Decimal`，不用 float** —— 订金金额不能有浮点误差。

照搬老产品 `services/line_dms/qa_util.py`（线上基准 `501589f0`），整份搬。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

MAX_AMOUNT = Decimal("99999999")
THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")

# 支付渠道补充信息的形态（**单一事实源**：逐问步进与卡片文案共用）。
#
# ⚠️ 这里的值描述的是「**要问几步、问什么**」，不是 DMS 的字段名：
#   destination = 转账只问**到账方银行/账户**；ref = 单条补充文本（票据号等）；
#   detail = 自由描述；None = 金额即完结。
# 把那句自由文本拆成 DMS 的原生槽位（`cheque_no` / `card_type` / `dst_bank_id`…）
# 是 `booking_payments` 那一层的事，**不在这里、也不在 `dms_payments`**。
CHANNEL_EXTRA_SHAPE = {
    "cash": None,
    "transfer": "destination",
    "cheque": "ref",
    "cashier_cheque": "ref",
    "card": "ref",
    "other": "detail",
}


def parse_amount(text: Optional[str]) -> Optional[Decimal]:
    """泰文数字→阿拉伯、去逗号/空白后转 `Decimal`；非法/越界/超两位小数 → None。"""
    s = (text or "").translate(THAI_DIGITS).replace(",", "")
    s = "".join(s.split())
    if not s:
        return None
    try:
        value = Decimal(s)
    except InvalidOperation:
        return None
    if not value.is_finite() or value <= 0 or value > MAX_AMOUNT:
        return None
    if value != value.quantize(Decimal("0.01")):
        return None
    return value


def complete_channel(qa: Dict[str, Any]) -> None:
    """支付渠道收尾：追加进 payments、清 pending、回到 pay_more。"""
    qa.setdefault("payments", []).append(qa["pending_channel"])
    qa["pending_channel"] = {}
    qa["step"] = "pay_more"


def norm(text: Optional[str]) -> str:
    """搜索归一：casefold + 只留字母数字。**去连字符**，这样 `D-Max` 能被 `dmax` 命中。"""
    return "".join(ch for ch in (text or "").casefold() if ch.isalnum())


def norm_row(row) -> str:
    """主档行可比串：名称 + 代码（同一个归一）。"""
    name = str(row[2]) if len(row) > 2 else ""
    code = str(row[1]) if len(row) > 1 else ""
    return norm(f"{name} {code}")


def row_name(row) -> str:
    """主档行 `[id, code, name, ...]` 的名称；缺名回退代码/ID。"""
    if len(row) > 2 and row[2]:
        return str(row[2])
    return str(row[1]) if len(row) > 1 else str(row[0])


def car_label(row) -> str:
    """车型行标签：代码 + 名称。"""
    code = str(row[1]) if len(row) > 1 else ""
    name = str(row[2]) if len(row) > 2 else ""
    return (code + " " + name).strip()


def car_label_of(qa: Dict[str, Any]) -> str:
    return str((qa.get("answers") or {}).get("car", {}).get("label") or "")


def find_row(rows: Optional[List[list]], rid: Optional[str]) -> Optional[list]:
    for r in rows or []:
        if r and str(r[0]) == str(rid):
            return r
    return None


def deposit_total(payments: List[Dict[str, Any]]) -> Decimal:
    """各渠道订金金额求和（金额一律 `Decimal`，不用 float）。"""
    return sum((Decimal(str(p.get("amount") or "0")) for p in payments or []), Decimal("0"))


def qat(name: str, **kw) -> str:
    """取一条**订车逐问**的文案（`dms.card.qa.*`），默认泰文（LINE 通道的语言）。

    放这个纯函数叶子里，是为了让 `dms_qa_cards` 与 `dms_qa_payment_cards`
    都能取词而**不必互相 import**（那两份本来就是互相引用的，见各自顶部）。
    """
    from pearnly_dms import i18n

    return i18n.t(f"dms.card.qa.{name}", lang=kw.pop("lang", "th"), **kw)
