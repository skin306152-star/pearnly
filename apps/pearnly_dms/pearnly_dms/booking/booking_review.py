# -*- coding: utf-8 -*-
"""开单回执的逐项复核 · 零 IO 纯函数叶子。

## 为什么要有它（2026-10-16 用户要求）

订车单写进 DMS 之后，按单号把这张单读回来，和确认前那张汇总卡**逐项**对一遍：
全部一致，回执末尾加一行"ผลการตรวจสอบกับ DMS: ถูกต้อง"；有不一致，回执换成差异卡，
逐条列出「确认时的值 → DMS 里的值」，并给三个按钮（更新资料 / 使用原资料 / 修改，见 `review_fix`）。

## 它不做什么

- **不参与任何判定**：单据是不是我们的、能不能报成功、要不要按"结果未知"处理，
  仍由 `booking_readback.evaluate_candidate` 决定，这里一个字都不改。走到这里的单已经核实成功。
- **不多读 DMS**：比对用的是回读核实那一轮已经读到的表单（`submit_booking(readback_out=…)`）。

## 比对口径（避免 2026-09-12 那类假阴性）

- 有编号的项比编号（顾问、地点、车、颜色、销售条件、上牌方式、省县区、邮编、称谓、银行），
  不比 DMS 回显的名称文字。
- 金额按 `Decimal`：`1000` 与 `1,000.00` 相同。
- 身份证号去噪后比；电话、账号只比数字。
- 手填的文字（姓名、门牌、巷、路、上牌人……）折叠空白后比。
- 附件不在这里比：附件是建单之后另挂的，结果看 `attach_ok`（见 `delivery`）。

每一项的展示值两边用同一组字段拼，所以差异卡上看到的就是"我们提交的"和"DMS 存下的"。
差异项同时带上**这一项全部表单字段在提交时的值**（`write`，改单时写回同一张单），
以及编辑页上可以手改的文字栏（`inputs`：`[字段, 标签键]`；编号类的栏只能二选一）。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Sequence, Tuple

from pearnly_dms.contracts.id_validate import normalize_thai_id
from pearnly_dms.contracts.payments import _PAYMENT_MONEY_FIELD, _PAYMENT_TEXT_FIELD

logger = logging.getLogger(__name__)

_TEXT, _ID, _DIGITS, _MONEY, _PEOPLE_ID = "text", "id", "digits", "money", "people_id"

_ADDRESS_TEXT = ("txthousenum", "txtbuilding", "txtfloor", "txtroom", "txtvillage",
                 "txtmoo", "txtsoi", "txtroad")
# 地址文字栏 → 编辑页标签（与客户档差异卡同一套 `dms.card.diff.*`）。
_ADDRESS_LABELS = ("diff.house_no", "diff.building", "diff.floor", "diff.room", "diff.village",
                   "diff.moo", "diff.soi", "diff.road")


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


def _amount(value: object) -> str:
    money = _money(value)
    return f"{money:,.2f}" if money is not None else _text(value)


@dataclass(frozen=True)
class _Item:
    """汇总卡上的一项：标签键（`dms.card.<键>`）、比对字段与比法、展示字段、写回时连带的字段、可手改的文字栏。"""

    label: str
    checks: Tuple[Tuple[str, str], ...]
    shown: Tuple[str, ...] = ()
    sep: str = " "
    money: str = ""  # 有值 = 展示时先放这一栏的金额
    extra: Tuple[str, ...] = ()  # 不比、不上卡，只随写回（付款户名、支行……）
    inputs: Tuple[Tuple[str, str], ...] = ()

    def show(self, form: dict) -> str:
        parts = [_amount(form.get(self.money))] if self.money else []
        parts += [_text(form.get(f)) for f in self.shown]
        return self.sep.join(p for p in parts if p)

    def fields(self) -> Tuple[str, ...]:
        """改单时要写回的字段：比对字段 + 展示镜像 + 连带字段，去重保序。"""
        out: List[str] = []
        for name in (*(f for f, _ in self.checks), *self.shown, self.money, *self.extra):
            if name and name not in out:
                out.append(name)
        return tuple(out)

    def matches(self, expected: dict, form: dict) -> bool:
        return all(_norm(kind, expected.get(f)) == _norm(kind, form.get(f)) for f, kind in self.checks)


_ITEMS: Tuple[_Item, ...] = (
    _Item("sum.people_id", (("txtpeopleid", _PEOPLE_ID),), ("txtpeopleid",)),
    _Item("sum.name", (("prefixval", _ID), ("txtcus", _TEXT)), ("txtprefix", "txtcus"),
          inputs=(("txtcus", "diff.name"),)),
    _Item("sum.birthday_be", (("txtbirthday", _TEXT),), ("txtbirthday",),
          inputs=(("txtbirthday", "diff.birthday_be"),)),
    _Item("sum.address",
          (*((f, _TEXT) for f in _ADDRESS_TEXT),
           ("provincesval", _ID), ("districtsval", _ID), ("subdistrictsval", _ID)),
          (*_ADDRESS_TEXT, "txtsubdistricts", "txtdistricts", "txtprovinces"),
          inputs=tuple(zip(_ADDRESS_TEXT, _ADDRESS_LABELS))),
    _Item("qa.LBL_POSTCODE", (("zipcodesval", _ID),), ("txtzipcodes",)),
    _Item("sum.phone", (("txttel", _DIGITS),), ("txttel",), inputs=(("txttel", "diff.phone"),)),
    _Item("qa.LBL_ADVISOR", (("usersval", _ID),), ("txtusers",)),
    _Item("qa.LBL_PLACE", (("placebookval", _ID),), ("txtplacebook",)),
    _Item("qa.LBL_CAR", (("carval", _ID), ("carpaintval", _ID)), ("txtcar", "carpaintname"), " · "),
    _Item("qa.LBL_DELIVERY", (("txtcardeliverydate", _TEXT),), ("txtcardeliverydate",)),
    _Item("qa.LBL_TERM", (("termsaleval", _ID),), ("txttermsale",)),
    _Item("qa.LBL_REGIS", (("regisbehalfval", _ID), ("txtregisname", _TEXT)),
          ("txtregisbehalf", "txtregisname"), " · ", inputs=(("txtregisname", "qa.LBL_REGIS"),)),
)
_TOTAL = _Item("qa.LBL_TOTAL", (("txtearnestmoney", _MONEY),), money="txtearnestmoney")


def _payment_item(channel: str) -> _Item:
    """一个付款渠道：金额 + 编号类字段 + 账号/票号（只比数字）；卡上显示金额 · 银行。"""
    slots = _PAYMENT_TEXT_FIELD.get(channel, {})
    checks = [(_PAYMENT_MONEY_FIELD[channel], _MONEY)]
    checks += [(f, _ID) for f in slots.values() if f.endswith("val")]
    checks += [(f, _DIGITS) for slot, f in slots.items() if slot.endswith("_no")]
    bank = tuple(f for slot, f in slots.items() if slot in ("dst_bank_name", "bank_name"))
    return _Item(f"qa.pay.{channel}", tuple(checks), bank, " · ", money=_PAYMENT_MONEY_FIELD[channel],
                 extra=tuple(slots.values()))


_PAYMENTS = {channel: _payment_item(channel) for channel in _PAYMENT_MONEY_FIELD}
_BY_LABEL = {item.label: item for item in (*_ITEMS, *_PAYMENTS.values(), _TOTAL)}


def _items(submitted: dict, form: dict) -> List[_Item]:
    """有金额的付款渠道（任一边非零）才成一项。"""
    paid = [item for item in _PAYMENTS.values()
            if any(_money(side.get(item.money)) for side in (submitted, form))]
    return [*_ITEMS, *paid, _TOTAL]


def compare(submitted: dict, form: dict) -> List[Dict[str, object]]:
    """逐项比对，返回不一致的项（按汇总卡顺序）。

    每项：`item` 标签键、`sent` 提交值、`dms` DMS 值（都是展示文字），
    `write` 这一项全部字段的提交值（改单时写回），`inputs` 编辑页可手改的 `[字段, 标签键]`。
    """
    submitted, form = submitted or {}, form or {}
    diffs: List[Dict[str, object]] = []
    for item in _items(submitted, form):
        if item.matches(submitted, form):
            continue
        diffs.append({"item": item.label, "sent": item.show(submitted), "dms": item.show(form),
                      "write": {f: str(submitted.get(f) or "") for f in item.fields()},
                      "inputs": [list(pair) for pair in item.inputs]})
    return diffs


def unresolved(targets: Sequence[Dict[str, object]], form: dict) -> List[Dict[str, object]]:
    """改单之后仍与要写的值不一致的项（`targets` 是写回的差异项，`write` 是实际写的值）。

    返回与 `compare` 同形：`sent` 是这次要写的值、`dms` 是 DMS 现在的值。
    """
    out: List[Dict[str, object]] = []
    for target in targets or []:
        item = _BY_LABEL.get(str(target.get("item") or ""))
        want = dict(target.get("write") or {})
        if item is None or item.matches(want, form or {}):
            continue
        out.append({**target, "sent": item.show(want), "dms": item.show(form or {})})
    return out


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


__all__ = ["compare", "review", "unresolved"]
