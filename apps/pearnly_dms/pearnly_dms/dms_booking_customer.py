# -*- coding: utf-8 -*-
"""订车单的客户主档快照。

**为什么建单前要重查一次客户**：订车单表单上的身份/地址字段是从客户档回显的，
所以要用**主档里的权威值**，不是 OCR 出来的原始值（操作员可能在面板上改过）。
顺带校验客户号对得上 —— 对不上就报错，绝不把单建到别人名下。

照搬老产品 `services/erp/mrerp_dms_booking_customer.py`。
"""

from __future__ import annotations

from typing import Any

from pearnly_dms.dms_client_base import DmsClientError
from pearnly_dms.dms_models import ThaiAddress, ThaiIdCardPayload


def card_from_customer(client: Any, *, customer_id: str, people_id: str) -> ThaiIdCardPayload:
    """按身份证号重查客户，核对客户号后返回主档快照。"""
    match = client.lookup_customer(people_id)
    matched_id = str(match.get("customer_id") or "")
    if not match.get("found") or matched_id != str(customer_id):
        raise DmsClientError(
            f"booking customer lookup mismatch: expected={customer_id!r} found={matched_id!r}",
            "ERR_DMS_CUSTOMER_SAVE",
        )

    fields = match.get("fields") or {}
    address = ThaiAddress(
        house_no=str(fields.get("house_no") or ""),
        building=str(fields.get("building") or ""),
        floor=str(fields.get("floor") or ""),
        room=str(fields.get("room") or ""),
        village=str(fields.get("village") or ""),
        moo=str(fields.get("moo") or ""),
        soi=str(fields.get("soi") or ""),
        road=str(fields.get("road") or ""),
        province_id=str(fields.get("province_id") or ""),
        province_name=str(fields.get("province_name") or ""),
        district_id=str(fields.get("district_id") or ""),
        district_name=str(fields.get("district_name") or ""),
        subdistrict_id=str(fields.get("subdistrict_id") or ""),
        subdistrict_name=str(fields.get("subdistrict_name") or ""),
        zipcode_id=str(fields.get("zipcode_id") or ""),
        zipcode=str(fields.get("zipcode_name") or ""),
    )
    # 建单表单要回显这一整串 —— 少任何一个，DMS 都可能收下一张"缺字段"的单，
    # 所以要**在本地就拦住**，别指望 DMS 拒。
    required = (
        fields.get("name"),
        fields.get("people_id"),
        fields.get("birthday_be"),
        fields.get("prefix_id"),
        fields.get("prefix_name"),
        fields.get("phone"),
        address.house_no,
        address.province_id,
        address.province_name,
        address.district_id,
        address.district_name,
        address.subdistrict_id,
        address.subdistrict_name,
        address.zipcode_id,
        address.zipcode,
    )
    if not all(required):
        raise DmsClientError(
            f"booking customer {customer_id!r} has incomplete master data",
            "ERR_DMS_CUSTOMER_SAVE",
        )

    return ThaiIdCardPayload(
        people_id=str(fields["people_id"]),
        first_name=str(fields["name"]),
        last_name="",
        birthday_be=str(fields["birthday_be"]),
        address=address,
        prefix_id=str(fields["prefix_id"]),
        prefix_name=str(fields.get("prefix_name") or ""),
        phone=str(fields["phone"]),
    )
