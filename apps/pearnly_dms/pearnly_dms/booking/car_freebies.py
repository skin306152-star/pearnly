# -*- coding: utf-8 -*-
"""车型主档里的「รายการของแถม」赠品：订车预览卡与编辑页**只读显示**（纯函数，零 IO）。

数据来自已发布的车辆快照（`catalogue_schema.FREEBIES` 那 6 列，与 DMS 车型表单同一份），
不另读 DMS；赠品不随单提交（2026-11-23 定：只显示）。金额一律 `Decimal`，
合计按全部赠品算，卡片只列前 `CARD_LIMIT` 项。

单价、金额、合计照 DMS 原生订车单：公司设置「隐藏赠品价格」（`setcbc.cbhidefreebiesprice`，
原生表单里的 `hfbp=h`）开着时只显示名称、数量、单位，价格照样随单提交（2026-11-23 主会话定）。
设置读不到时按隐藏处理，不冒险露价格。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from pearnly_dms.contracts.client_base import DmsClientError

COLUMNS = ("txtfbproduct[]", "txtfbunits[]", "txtfbquantity[]", "txtfbprice[]", "txtfbamount[]",
           "fbproductval[]")
CARD_LIMIT = 15


def _number(value: Any) -> Optional[Decimal]:
    text = str(value or "").replace(",", "").strip()
    if not text:
        return Decimal("0")
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def money(value: Decimal) -> str:
    return f"{value:,.2f}"


def quantity(value: Decimal) -> str:
    return f"{value.normalize():f}" if value == value.to_integral_value() else f"{value:,}"


def from_form(car_id: str, fields: Optional[Dict[str, Any]], *, hide_price: bool = True) -> Dict[str, Any]:
    """车型表单字段 → `{"car_id", "rows", "total", "hide_price"}`；没有赠品时 rows 为空。

    列长度对不上或金额解析不了 → 视为读不到，返回空（显示层不出这一块，不猜）。
    """
    fields = fields or {}
    columns = [list(fields.get(name) or []) for name in COLUMNS]
    empty = {"car_id": str(car_id), "rows": [], "total": "", "hide_price": bool(hide_price)}
    if len({len(column) for column in columns}) != 1:
        return empty
    rows: List[Dict[str, str]] = []
    total = Decimal("0")
    for name, unit, qty, price, amount, product_id in zip(*columns):
        numbers = (_number(qty), _number(price), _number(amount))
        if None in numbers or not str(name or "").strip():
            return empty
        total += numbers[2]
        rows.append({"product_id": str(product_id or "").strip(),
                     "name": str(name).strip(), "unit": str(unit or "").strip(),
                     "qty": quantity(numbers[0]), "price": money(numbers[1]),
                     "amount": money(numbers[2])})
    return {**empty, "rows": rows, "total": money(total) if rows else ""}


def edited(car_id: str, default: Dict[str, Any], submitted: List[Dict[str, Any]],
           products: List[Dict[str, str]]) -> Dict[str, Any]:
    """编辑页提交的赠品行（`[{product_id, qty}]`）→ 会话里存的赠品（显示行 + 合计 + 是否改过）。

    照原生：数量空或 ≤0 当 1；同一商品只留第一行；商品须是这台车的默认赠品或商品主档里的商品。
    返回 `None` = 有不认识的商品或数量不是数字（调用方按无效主档拒绝保存）。
    """
    known = {str(row.get("product_id")): row for row in products}
    known.update({str(row.get("product_id")): row for row in default.get("rows") or []})
    rows, seen, total = [], set(), Decimal("0")
    for item in submitted:
        product_id = str((item or {}).get("product_id") or "").strip()
        if not product_id or product_id in seen:
            continue
        seen.add(product_id)
        base, qty = known.get(product_id), _number((item or {}).get("qty"))
        price = _number((base or {}).get("price"))
        if base is None or qty is None or price is None:
            return None
        qty = qty if qty > 0 else Decimal("1")
        amount = qty * price
        total += amount
        rows.append({"product_id": product_id, "name": str(base.get("name") or ""),
                     "unit": str(base.get("unit") or ""), "qty": quantity(qty),
                     "price": money(price), "amount": money(amount)})
    same = [(r["product_id"], _number(r["qty"])) for r in rows] == [
        (str(r.get("product_id")), _number(r.get("qty"))) for r in default.get("rows") or []]
    return {"car_id": str(car_id), "rows": rows, "total": money(total) if rows else "", "edited": not same,
            "hide_price": bool(default.get("hide_price", True))}


def wanted(qa: Dict[str, Any]) -> Optional[tuple]:
    """写入 DMS 用：用户在编辑页动过这台车的赠品 → `((商品 id, 数量), …)`；没动过 → `None`
    （照原生选车带出的默认行提交）。换了车、旧会话没有这份 → `None`。"""
    saved = (qa or {}).get("car_freebies") or {}
    car_id = str(((qa or {}).get("answers") or {}).get("car", {}).get("id") or "")
    if not saved.get("edited") or not car_id or str(saved.get("car_id") or "") != car_id:
        return None
    return tuple((str(row.get("product_id") or ""), str(row.get("qty") or "")) for row in saved.get("rows") or [])


def for_car(qa: Dict[str, Any]) -> Dict[str, Any]:
    """会话里存的赠品只在**仍是这台车**时有效（换车后旧赠品不显示）。"""
    saved = (qa or {}).get("car_freebies") or {}
    car_id = str(((qa or {}).get("answers") or {}).get("car", {}).get("id") or "")
    return saved if car_id and str(saved.get("car_id") or "") == car_id and saved.get("rows") else {}


def from_view(view: Any, car_id: str) -> Dict[str, Any]:
    """从快照视图（`snapshots.directory.Directory`）取：车型表单读不到 → 空，不挡订车；
    隐藏价格设置读不到 → 按隐藏处理。"""
    try:
        fields = view.car_form(car_id)
    except DmsClientError:
        fields = {}
    try:
        hide = view.hide_freebies_price()
    except DmsClientError:
        hide = True
    return from_form(car_id, fields, hide_price=hide)
