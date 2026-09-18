# -*- coding: utf-8 -*-
"""订车单的客户主档选择与快照。

**为什么建单前要按客户号直读一次**：订车单表单上的身份/地址字段是从客户档回显的，
所以要用**主档里的权威值**，不是 OCR 出来的原始值（操作员可能在面板上改过）。

三条别改坏的规矩：

1. **直读，不搜索**（`read_customer`）：搜索有"新客隐身期"，刚建的客户可能搜不到，
   于是把"就在眼前"的客户误判成不存在；直读不受列表缓存影响。
2. **身份以归一后的 13 位证号比对**，不是比字符串。证号**矛盾**（我们选的客户号指向
   另一个人）是**硬冲突**，到此为止 —— 绝不再去问别的账号，否则别人的客户资料会漏出来。
3. 销售看不到客户档时，**只读**借本端点的管理员；借不到就明确失败关闭
   （见 `dms_admin_read.py`），退回销售的不完整视图不算选项。

照搬老产品 `services/erp/mrerp_dms_booking_customer.py`（线上基准 `501589f0`），逻辑未改。
"""

from __future__ import annotations

from typing import Any

from pearnly_dms.dms_id_validate import normalize_thai_id
from pearnly_dms.dms_client_base import DmsClientError
from pearnly_dms.dms_models import ThaiAddress, ThaiIdCardPayload


def card_from_customer(client: Any, *, customer_id: str, people_id: str) -> ThaiIdCardPayload:
    """直读已选客户并核验完整证号；销售不可读时只读借用本端点管理员。"""
    expected = normalize_thai_id(people_id)
    if (
        not str(customer_id or "").strip()
        or len(expected) != 13
        or not expected.isascii()
        or not expected.isdigit()
    ):
        raise DmsClientError("booking customer identity missing", "ERR_DMS_CUSTOMER_LOOKUP")
    try:
        fields = client.read_customer(str(customer_id))
    except DmsClientError:
        fields = {}
    actual = normalize_thai_id(str((fields or {}).get("people_id") or ""))
    if actual and actual != expected:
        # 身份矛盾**不是**可见性问题。在去问别的账号之前就停手，
        # 免得把别人的客户资料带出来。
        raise DmsClientError("selected customer identity mismatch", "ERR_DMS_CUSTOMER_LOOKUP")
    try:
        return _card_from_fields(fields or {}, customer_id=customer_id, expected=expected)
    except DmsClientError:
        # 统一走权威只读层判定（未配管理员 → 原样抛本会话的错；配了但登录失败 → 明确失败关闭）。
        from pearnly_dms.dms_admin_read import authoritative_read_client

        reader = authoritative_read_client(client)
        if reader is client:
            raise
        # 同一个配置的 base_url、同一个确切的客户号；不做广搜、不换来源、
        # 不用缓存字段、也不给订车写入方提权。
        try:
            fields = reader.read_customer(str(customer_id))
        except DmsClientError as exc:
            raise DmsClientError(
                "configured admin could not read selected customer", "ERR_DMS_CUSTOMER_LOOKUP"
            ) from exc
        return _card_from_fields(fields, customer_id=customer_id, expected=expected)


def _card_from_fields(fields: dict, *, customer_id: str, expected: str) -> ThaiIdCardPayload:
    """两个主体（销售 / 管理员）必须过**同一套**身份与完整性判据。"""
    if normalize_thai_id(str(fields.get("people_id") or "")) != expected:
        raise DmsClientError(
            f"booking customer {customer_id!r} identity could not be verified",
            "ERR_DMS_CUSTOMER_LOOKUP",
        )

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
    if not all(str(value or "").strip() for value in required):
        raise DmsClientError(
            f"booking customer {customer_id!r} has incomplete master data",
            "ERR_DMS_CUSTOMER_LOOKUP",
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
