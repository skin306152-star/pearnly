# -*- coding: utf-8 -*-
"""把映射好的车型规格显示在订车确认卡旁边（"你选的是哪台车"的当场对账）。

为什么要有它：**车是主档行里的 ID**，销售在 LINE 里只看到自己选的那个名字。
把 DMS 那一行的规格摊开给他看，选错车（同名前驱/四驱、不同年款）才能当场发现，
而不是等交车。

⚠️ 标签是**卡片文案**，按本项目规矩进语言包（`dms.card.car.*`），不在代码里写死 ——
见 `dms_cards.py` 顶部那段"文案不进代码"。

照搬老产品 `services/line_dms/booking_car_details.py`（线上基准 `501589f0`）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from pearnly_dms.line.cards import _kv_row, _t

# 车型主档行的列号 → 语言包里的标签名（列号照老产品，真机勘察来的）。
_FIELD_NAMES: Tuple[Tuple[int, str], ...] = (
    (4, "brand"),
    (6, "type"),
    (8, "desc"),
    (10, "grade"),
    (12, "gear"),
    (13, "year"),
    (15, "engine"),
    (16, "price"),
)


def rows(qa: Dict[str, Any]) -> List[Dict[str, Any]]:
    """逐问状态 → 规格卡的行。**取不到的列直接不出**，不留空槽。"""
    car_id = str(((qa.get("answers") or {}).get("car") or {}).get("id") or "")
    cars = ((qa.get("master_snapshot") or {}).get("rows") or {}).get("cars") or []
    car = next((row for row in cars if str(row[0]) == car_id), [])
    return [
        _kv_row(_t(f"car.{name}"), str(car[index]))
        for index, name in _FIELD_NAMES
        if len(car) > index and car[index] not in (None, "")
    ]
