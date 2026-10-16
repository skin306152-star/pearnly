# -*- coding: utf-8 -*-
"""开单回执的逐项复核 · 零 IO 纯函数叶子。

## 为什么要有它（2026-10-16 用户要求）

订车单写进 DMS 之后，按单号把这张单读回来，和确认前那张汇总卡**逐项**对一遍：
全部一致，回执末尾加一行"ผลการตรวจสอบกับ DMS: ถูกต้อง"；有不一致，回执换成差异卡，
逐条列出「确认时的值 → DMS 里的值」。

## 它不做什么

- **不参与任何判定**：单据是不是我们的、能不能报成功、要不要按"结果未知"处理，
  仍由 `booking_readback.evaluate_candidate` 决定，这里一个字都不改。走到这里的单已经核实成功。
- **不多读 DMS**：比对用的是回读核实那一轮已经读到的表单（`submit_booking(readback_out=…)`）。

## 比对口径（避免 2026-09-12 那类假阴性）

- 有编号的项比编号（顾问、地点、车、颜色、付款方式、上牌方式、省县区、邮编、称谓、银行），
  不比 DMS 回显的名称文字。
- 金额按 `Decimal`：`1000` 与 `1,000.00` 相同。
- 身份证号去噪后比；电话、账号只比数字。
- 手填的文字（姓名、门牌、巷、路、上牌人……）折叠空白后比。
- 附件不在这里比：附件是建单之后另挂的，结果看 `attach_ok`（见 `delivery`）。

每一项的展示值两边用同一组字段拼，所以差异卡上看到的就是"我们提交的"和"DMS 存下的"。
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from pearnly_dms.contracts.id_validate import normalize_thai_id
from pearnly_dms.contracts.payments import _PAYMENT_MONEY_FIELD, _PAYMENT_TEXT_FIELD

logger = logging.getLogger(__name__)

_TEXT, _ID, _DIGITS, _MONEY, _PEOPLE_ID = "text", "id", "digits", "money", "people_id"

_ADDRESS_TEXT = ("txthousenum", "txtbuilding", "txtfloor", "txtroom", "txtvillage",
                 "txtmoo", "txtsoi", "txtroad")
_ADDRESS_SHOWN = (*_ADDRESS_TEXT, "txtsubdistricts", "txtdistricts", "txtprovinces")


def _text(value: object) -> str:
    return " ".join(str(value if value is not None else "").split())


def _money(value: object) -> Optional[Decimal]:
    try:
        return Decimal(_text(value).replace(",", "") or "0")
    except InvalidOperation:
        return None


def _norm(kind: str, value: object):
    if kind == _MONEY:
        return _money(value)
    if kind == _DIGITS:
        return re.sub(r"\D", "", str(value or ""))
    if kind == _PEOPLE_ID:
        return normalize_thai_id(str(value or "")) or _text(value)
    return _text(value)


def _join(form: dict, fields: Sequence[str], sep: str = " ") -> str:
    return sep.join(v for v in (_text(form.get(f)) for f in fields) if v)


def _amount(form: dict, field: str) -> str:
    value = _money(form.get(field))
    return f"{value:,.2f}" if value is not None else _text(form.get(field))


# 汇总卡上的一项 → (标签键, [(字段, 比法)…], 展示函数)。
# 标签键就是汇总卡用的那一条（`dms.card.<键>`），差异卡与汇总卡叫法完全一致。
_Item = Tuple[str, Tuple[Tuple[str, str], ...], Callable[[dict], str]]
_ITEMS: Tuple[_Item, ...] = (
    ("sum.people_id", (("txtpeopleid", _PEOPLE_ID),), lambda f: _text(f.get("txtpeopleid"))),
    ("sum.name", (("prefixval", _ID), ("txtcus", _TEXT)), lambda f: _join(f, ("txtprefix", "txtcus"))),
    ("sum.birthday_be", (("txtbirthday", _TEXT),), lambda f: _text(f.get("txtbirthday"))),
    ("sum.address",
     (*((field, _TEXT) for field in _ADDRESS_TEXT),
      ("provincesval", _ID), ("districtsval", _ID), ("subdistrictsval", _ID)),
     lambda f: _join(f, _ADDRESS_SHOWN)),
    ("qa.LBL_POSTCODE", (("zipcodesval", _ID),), lambda f: _text(f.get("txtzipcodes"))),
    ("sum.phone", (("txttel", _DIGITS),), lambda f: _text(f.get("txttel"))),
    ("qa.LBL_ADVISOR", (("usersval", _ID),), lambda f: _text(f.get("txtusers"))),
    ("qa.LBL_PLACE", (("placebookval", _ID),), lambda f: _text(f.get("txtplacebook"))),
    ("qa.LBL_CAR", (("carval", _ID), ("carpaintval", _ID)),
     lambda f: _join(f, ("txtcar", "carpaintname"), " · ")),
    ("qa.LBL_DELIVERY", (("txtcardeliverydate", _TEXT),), lambda f: _text(f.get("txtcardeliverydate"))),
    ("qa.LBL_TERM", (("termsaleval", _ID),), lambda f: _text(f.get("txttermsale"))),
    ("qa.LBL_REGIS", (("regisbehalfval", _ID), ("txtregisname", _TEXT)),
     lambda f: _join(f, ("txtregisbehalf", "txtregisname"), " · ")),
)


def _payment_items(submitted: dict, form: dict) -> List[_Item]:
    """有金额的付款渠道（任一边非零）各成一项：金额 + 编号类字段 + 账号/票号（只比数字）。"""
    items: List[_Item] = []
    for channel, money_field in _PAYMENT_MONEY_FIELD.items():
        if not any(_money(side.get(money_field)) for side in (submitted, form)):
            continue
        text_fields = _PAYMENT_TEXT_FIELD.get(channel, {})
        checks = [(money_field, _MONEY)]
        checks += [(field, _ID) for field in text_fields.values() if field.endswith("val")]
        checks += [(field, _DIGITS) for slot, field in text_fields.items()
                   if slot.endswith("_no")]
        bank_name = next((field for slot, field in text_fields.items()
                          if slot in ("dst_bank_name", "bank_name")), "")

        def shown(f, money_field=money_field, bank_name=bank_name):
            return " · ".join(v for v in (_amount(f, money_field), _text(f.get(bank_name)) if bank_name else "") if v)

        items.append((f"qa.pay.{channel}", tuple(checks), shown))
    return items


def compare(submitted: dict, form: dict) -> List[Dict[str, str]]:
    """逐项比对。返回不一致的项（按汇总卡顺序）：`{"item": 标签键, "sent": 提交值, "dms": DMS 值}`。"""
    submitted, form = submitted or {}, form or {}
    items = [*_ITEMS, *_payment_items(submitted, form),
             ("qa.LBL_TOTAL", (("txtearnestmoney", _MONEY),), lambda f: _amount(f, "txtearnestmoney"))]
    diffs: List[Dict[str, str]] = []
    for label, checks, shown in items:
        if all(_norm(kind, submitted.get(field)) == _norm(kind, form.get(field)) for field, kind in checks):
            continue
        diffs.append({"item": label, "sent": shown(submitted), "dms": shown(form)})
    return diffs


def review(readback_out: Optional[dict]) -> Optional[Dict[str, list]]:
    """回读转交的结果 → 回执要的复核结论；没拿到表单就返回 `None`，回执照旧、不加那一行。

    调用时单已经建成并核实：这里**绝不能抛异常**（抛出去会让一张建成的单落进"意外失败"），
    比对本身出任何错都当"没复核"处理。
    """
    if not readback_out or not readback_out.get("form"):
        return None
    try:
        return {"diffs": compare(readback_out.get("submitted") or {}, readback_out["form"])}
    except Exception:  # noqa: BLE001
        logger.warning("[dms] 开单回执复核失败，回执照旧发出", exc_info=True)
        return None


__all__ = ["compare", "review"]
