# -*- coding: utf-8 -*-
"""客户地址/称谓的**实时级联校验** —— 建客户时不许猜、不许兜底。

## 为什么要有它（两个都是老产品踩过的真坑）

1. **新建客户会继承新表单里的默认地址。** `cus/form.php` 的新建表单自带一组省/县/区/
   邮编预选值。不显式清掉，客户就会**带着一个没人选过的地址**被存进 DMS —— 事后没人
   看得出来，对账/寄件才发现。所以 `clear_customer_select_defaults()` 在 create 之前
   把这些键清空。
2. **空值/被删值不能拿"第一项"补齐。** 老实现遇到过"空的必填 select 会让服务端报
   误导性的 `ข้อมูลถูกใช้งานในระบบ`"，于是用第一项兜底 —— 结果是客户被填上一个
   **凭空捏造的称谓**。线上 `b78697f3` 把这条禁掉了：要么给一个**显式**的值，
   要么拦下来让人去选。

所以这里的规矩是：**显式给值 + 与实时父级链核对通过**，两者缺一就拦。

照搬老产品 `services/erp/mrerp_dms_customer_geo.py`（线上基准 `501589f0`），
逻辑一字未改。
"""

from __future__ import annotations

import re

from pearnly_dms.dms_client_base import DmsClientError

_SELECTS = ("selprovinces", "seldistricts", "selsubdistricts", "selzipcodes")
_SUFFIXES = ("", "_ct", "_sd")
# 上一级 → 下一级的取数端点（顺序即层级）
_CHILDREN = (
    ("cus/component/listdistricts.php", "selprovinces"),
    ("cus/component/listsubdistricts.php", "seldistricts"),
    ("cus/component/listzipcodes.php", "selsubdistricts"),
)


def clear_customer_select_defaults(data: dict) -> None:
    """新建客户前，清掉新表单自带的任意地址默认值（不清 = 客户凭空多一个地址）。"""
    for key in ("selprefix", *(field + suffix for suffix in _SUFFIXES for field in _SELECTS)):
        if key in data:
            data[key] = ""


def _require_member(value: str, rows: list, field: str) -> None:
    if not value:
        raise DmsClientError(
            f"customer {field} requires an explicit selection", "ERR_DMS_MASTER_UNMATCHED"
        )
    if not rows:
        raise DmsClientError(f"DMS {field} options unavailable", "ERR_DMS_MASTER_UNAVAILABLE")
    if not any(str(row[0]) == str(value) for row in rows):
        raise DmsClientError(
            f"customer {field} no longer belongs to the selected parent", "ERR_DMS_MASTER_UNMATCHED"
        )


def validate_customer_selects(client, data: dict, form_html: str) -> None:
    """**不猜**：只有实时父级链认账的显式 id 才留下。

    三套地址（`""` / `_ct` / `_sd`）分别校验：

    · 表单里没有这一个 select 的表单块（`selprovinces_ct` 之类）→ 跳过，那是这套表单
      本来就不带的可选地址块；
    · 这一套地址**整块都空** → 跳过（没用上的可选地址就该留空）；
    · 用了就必须一级一级对得上，否则拦。
    """
    prefix = str(data.get("selprefix") or "")
    prefixes = client._select_options(form_html, "selprefix")
    if prefix and not any(str(row[0]) == prefix for row in prefixes):
        # 有些合法称谓只出现在订车快速建客表单里。保持"双来源"契约，
        # 但**绝不**凭空造一个不存在的称谓。
        prefixes = client.list_prefixes()
    _require_member(prefix, prefixes, "selprefix")

    # 父级链只在校验这一次读；OCR、下拉、用户、保存、DMS 登录会话之间都不复用。
    fetched = {}
    for suffix in _SUFFIXES:
        provinces = client._select_options(form_html, "selprovinces" + suffix)
        if suffix and not re.search(rf'<select\b[^>]*name="selprovinces{suffix}"', form_html, re.I):
            continue  # 这套表单没有暴露那个可选地址块
        values = [str(data.get(field + suffix) or "") for field in _SELECTS]
        if suffix and not any(values):
            continue  # 没用上的可选地址必须保持空
        _require_member(values[0], provinces, "selprovinces" + suffix)
        for index, (path, parent_field) in enumerate(_CHILDREN, 1):
            parent_id = values[index - 1]
            key = (path, parent_id)
            if key not in fetched:
                fetched[key] = client._fetch_options(path, {parent_field: parent_id})
            _require_member(values[index], fetched[key], _SELECTS[index] + suffix)
