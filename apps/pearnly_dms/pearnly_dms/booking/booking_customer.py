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

照搬老产品 `services/erp/mrerp_dms_booking_customer.py`（线上基准 `501589f0`）。
2026-10-13 起身份已核实、但最终那次权威读取缺订车必填项时，单独报
`ERR_DMS_CUSTOMER_INCOMPLETE`（`CustomerIncomplete` 带上读到的资料与缺项），不再和
"读不到客户"共用 `ERR_DMS_CUSTOMER_LOOKUP`；必填判据本身一字未改（`missing_required`）。
订车时只补空项的取值规则见 `gap_fill`（纯计算，写入在 `booking.service`）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pearnly_dms.contracts.id_validate import normalize_thai_id
from pearnly_dms.contracts.client_base import DmsClientError, parse_be_date
from pearnly_dms.contracts.customer_merge import ADDRESS
from pearnly_dms.contracts.models import ThaiAddress, ThaiIdCardPayload

#: 建单表单要回显的客户主档字段（`read_customer` 的键）。少任何一个，DMS 都可能收下一张
#: "缺字段"的单，所以要**在本地就拦住**，别指望 DMS 拒。
REQUIRED = (
    "name", "people_id", "birthday_be", "prefix_id", "prefix_name", "phone",
    "house_no", "province_id", "province_name", "district_id", "district_name",
    "subdistrict_id", "subdistrict_name", "zipcode_id", "zipcode_name",
)
#: 缺项按卡片上的字段名报（`dms.card.diff.<键>`）：编号与名称是同一项。
_LABEL_FIELD = {"prefix_name": "prefix_id", "province_name": "province_id",
                "district_name": "district_id", "subdistrict_name": "subdistrict_id",
                "zipcode_name": "zipcode_id"}
_GEO_IDS = ("province_id", "district_id", "subdistrict_id", "zipcode_id")


class CustomerIncomplete(DmsClientError):
    """身份已核实，但最终那次权威读取里缺订车必填项。

    ``fields`` 是那次读取的原样结果（补空写入拿它当对照基准），``missing`` 是缺的卡片字段名。
    """

    def __init__(self, customer_id: str, fields: dict, missing: List[str]):
        super().__init__(f"booking customer {customer_id!r} has incomplete master data",
                         "ERR_DMS_CUSTOMER_INCOMPLETE")
        self.fields = dict(fields)
        self.missing = list(missing)


def _text(value: Any) -> str:
    return str(value or "").strip()


def missing_required(fields: dict) -> List[str]:
    """``REQUIRED`` 里为空的键（判据与原来逐项相同：``str(值 or "").strip()`` 为空即缺）。"""
    return [key for key in REQUIRED if not _text(fields.get(key))]


def missing_labels(fields: dict) -> List[str]:
    """缺项对应的卡片字段名，按 ``REQUIRED`` 顺序去重。"""
    out: List[str] = []
    for key in missing_required(fields):
        label = _LABEL_FIELD.get(key, key)
        if label not in out:
            out.append(label)
    return out


def gap_fill(current: dict, draft: dict) -> Optional[Dict[str, str]]:
    """订车确认时，DMS 主档里空着的必填项能不能用本次草稿的原值补齐（纯计算，不连任何系统）。

    返回要写的字段（只含 DMS 里空着的项，已有的值一个都不带）；``{}`` = 本来就不缺；
    ``None`` = 补不齐，**一项都不写**（不做部分写入）。

    · 取值只来自 ``qa["draft"]`` 的原值（识别结果、操作员手输或编辑页填的），绝不用
      ``booking.service.card_payload`` 那种占位（称谓 "17"、电话 "0800000000"）；
    · 生日必须是合法的佛历 dd/mm/yyyy；称谓只写编号，名称由 DMS 按编号回显；
      DMS 里有编号却没有名称（编号已失效）补不了；
    · 户籍地址只按整套补：DMS 整块（门牌、楼、层、室、村、หมู่、巷、路、四级编号）都空 →
      用草稿整套；DMS 四级编号与草稿完全相同、只缺门牌号 → 只补门牌号；其余一律不补，
      免得拼出"甲府的门牌加乙府的区"。
    """
    current, draft = current or {}, draft or {}
    missing = set(missing_required(current))
    if not missing:
        return {}
    if "people_id" in missing:
        return None
    out: Dict[str, str] = {}
    for key in ("name", "phone"):
        if key in missing:
            if not _text(draft.get(key)):
                return None
            out[key] = _text(draft.get(key))
    if "birthday_be" in missing:
        birthday = _text(draft.get("birthday_be"))
        try:
            parse_be_date(birthday)
        except ValueError:
            return None
        out["birthday_be"] = birthday
    if missing & {"prefix_id", "prefix_name"}:
        if _text(current.get("prefix_id")) or not _text(draft.get("prefix_id")):
            return None
        out["prefix_id"] = _text(draft.get("prefix_id"))
    address_missing = missing - {"name", "people_id", "birthday_be", "prefix_id", "prefix_name", "phone"}
    if address_missing:
        if not all(_text(draft.get(key)) for key in ("house_no",) + _GEO_IDS):
            return None
        if not any(_text(current.get(key)) for key in ADDRESS):
            out.update({key: _text(draft.get(key)) for key in ADDRESS if _text(draft.get(key))})
        elif address_missing == {"house_no"} and all(
                _text(current.get(key)) == _text(draft.get(key)) for key in _GEO_IDS):
            out["house_no"] = _text(draft.get("house_no"))
        else:
            return None
    return out


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
        from pearnly_dms.adapter.admin_read import authoritative_read_client

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
    # 建单表单要回显这一整串（`REQUIRED`）—— 缺项单独报，带上这次读到的资料，
    # 订车层据此决定能不能只补空项（`gap_fill`）。
    if missing_required(fields):
        raise CustomerIncomplete(customer_id, fields, missing_labels(fields))

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
