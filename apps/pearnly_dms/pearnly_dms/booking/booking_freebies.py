# -*- coding: utf-8 -*-
"""订车单赠品行写入 DMS：原生带出 → 合成最终行 → 表单数组（纯函数，零 IO）。

照 DMS 原生订车单（`drfcbc/component/js/form.js`，2026-11-23 DMS-TEST 只读核实）：

- 选车型时原生调 `drfcbc/component/cardffreebies.php`（`idcar`、`hfbp`），返回
  `[项数, 合计数量, 合计金额, 赠品行 HTML]`；行里是 7 个同名数组输入框（见 `ARRAYS`）。
- 用户能改：数量（空或 ≤0 当 1.00）、删行、从商品主档加行（同一商品不能重复）；
  单位只读；金额 = 数量 × 单价（两位小数、千分位，与原生 `calcfreebies` 同形）。
- 没有赠品也能提交（不发这几个字段，同原生空表单）。

取数与提交在 `client_ops`；这里只做解析与合成，金额一律 `Decimal`。
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from pearnly_dms.contracts.client_base import DmsClientError

#: 原生赠品行的数组字段（与原生 HTML 同序）。
ARRAYS = ("txtfbproduct[]", "txtfbquantity[]", "txtfbunits[]", "txtfbprice[]",
          "txtfbamount[]", "fbunitsval[]", "fbproductval[]")
_KEYS = ("name", "qty", "unit", "price", "amount", "unit_id", "product_id")
NATIVE_PATH = "drfcbc/component/cardffreebies.php"
PICKER = "txtfbproduct[]"


def _decimal(value: Any) -> Optional[Decimal]:
    text = str(value if value is not None else "").replace(",", "").strip()
    try:
        number = Decimal(text or "0")
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _fixed(value: Decimal, *, commas: bool) -> str:
    value = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{value:,.2f}" if commas else f"{value:.2f}"


def rows_from_arrays(arrays: Dict[str, List[str]]) -> List[Dict[str, str]]:
    """表单数组 → 行；各列长度不一致说明表单不完整，按读失败处理。"""
    columns = [list(arrays.get(name) or []) for name in ARRAYS]
    if len({len(column) for column in columns}) != 1:
        raise DmsClientError("Booking freebie columns are incomplete", "ERR_DMS_IMPORT")
    return [dict(zip(_KEYS, values)) for values in zip(*columns)]


def native_rows(body: str, parse_arrays: Callable[[str], Dict[str, List[str]]]) -> List[Dict[str, str]]:
    """`cardffreebies.php` 的返回 → 原生默认赠品行。原生报错（`err::`）或形状不符 → 抛，不建单。"""
    text = (body or "").strip()
    if not text or text.lower().startswith("err::"):
        raise DmsClientError("DMS car freebies unavailable", "ERR_DMS_MASTER_UNAVAILABLE")
    try:
        data = json.loads(text)
        count, html = int(data[0]), str(data[3] or "")
    except (ValueError, TypeError, IndexError, KeyError) as exc:
        raise DmsClientError("DMS car freebies response has an unknown shape",
                             "ERR_DMS_MASTER_UNAVAILABLE") from exc
    rows = rows_from_arrays(parse_arrays(html)) if html.strip() else []
    if len(rows) != count:
        raise DmsClientError("DMS car freebies count does not match its rows", "ERR_DMS_MASTER_UNAVAILABLE")
    return rows


def picker_row(row: Sequence[Any]) -> Dict[str, str]:
    """原生商品选择器（`bshsd` 元素 `txtfbproduct[]`）一行：id、代码、名称、单位 id、单位、单价。"""
    values = [str(value if value is not None else "") for value in row]
    values += [""] * (6 - len(values))
    return {"product_id": values[0], "name": values[2], "unit_id": values[3],
            "unit": values[4], "price": values[5]}


def final_rows(native: List[Dict[str, str]], wanted: Optional[Iterable[Tuple[str, str]]],
               picker: Callable[[], Optional[List[Sequence[Any]]]]) -> List[Dict[str, str]]:
    """最终要提交的行。

    `wanted` 为 `None`（用户没动过赠品）→ 原样用原生默认行，与网页选车后直接保存一致。
    否则按 `wanted` 的 `(商品 id, 数量)` 顺序合成：原生默认里有的商品沿用它的名称、单位、单价，
    没有的从原生商品选择器取（`picker()` 只在需要时调一次）；同一商品只留第一行（原生判重复）。
    """
    if wanted is None:
        rows = [dict(row) for row in native]
    else:
        by_id = {str(row.get("product_id")): row for row in native}
        catalogue: Optional[Dict[str, Dict[str, str]]] = None
        rows, seen = [], set()
        for product_id, quantity in wanted:
            product_id = str(product_id or "").strip()
            if not product_id or product_id in seen:
                continue
            seen.add(product_id)
            base = by_id.get(product_id)
            if base is None:
                if catalogue is None:
                    listed = picker()
                    if listed is None:
                        raise DmsClientError("DMS product picker unavailable", "ERR_DMS_MASTER_UNAVAILABLE")
                    catalogue = {item["product_id"]: item for item in map(picker_row, listed)}
                base = catalogue.get(product_id)
                if base is None:
                    raise DmsClientError("Freebie product is not in DMS", "ERR_DMS_MASTER_UNMATCHED")
            rows.append({**base, "qty": str(quantity)})
    out = []
    for row in rows:
        quantity, price = _decimal(row.get("qty")), _decimal(row.get("price"))
        if quantity is None or price is None:
            raise DmsClientError("Freebie quantity or price is not a number", "ERR_DMS_IMPORT")
        if quantity <= 0:
            quantity = Decimal("1")
        out.append({**row, "qty": _fixed(quantity, commas=False), "price": _fixed(price, commas=False),
                    "amount": _fixed(quantity * price, commas=True)})
    return out


def form_arrays(rows: List[Dict[str, str]]) -> Dict[str, List[str]]:
    """行 → 原生表单数组；没有行就不发这几个字段（同原生空表单）。"""
    if not rows:
        return {}
    return {name: [str(row.get(key) or "") for row in rows] for name, key in zip(ARRAYS, _KEYS)}


def signature(form: Dict[str, Any]) -> List[Tuple[str, Decimal]]:
    """回读比对口径：逐行（商品 id，数量）。名称、单价是 DMS 回显，不比。"""
    ids = form.get("fbproductval[]") or []
    quantities = form.get("txtfbquantity[]") or []
    if isinstance(ids, str):
        ids = [ids]
    if isinstance(quantities, str):
        quantities = [quantities]
    return [(str(pid).strip(), _decimal(qty) or Decimal("0")) for pid, qty in zip(ids, quantities)]


__all__ = ["ARRAYS", "NATIVE_PATH", "PICKER", "final_rows", "form_arrays", "native_rows",
           "picker_row", "rows_from_arrays", "signature"]
