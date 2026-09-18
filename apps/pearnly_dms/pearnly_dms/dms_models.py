# -*- coding: utf-8 -*-
"""DMS（卖车系统）用的纯数据类型：身份证 → 客户 → 订车单。

**这些类型不碰网络、不碰 Playwright**，就是数据。所以它们能单独单测，
业务客户端和适配器都只是消费它们。

照搬老产品 `services/erp/mrerp_dms_models.py`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass(frozen=True)
class ThaiAddress:
    """归一后的泰国地址，并解析成 DMS 的主档 id。

    `*_id` 是 DMS 的府/县/区/邮编 id，`*_name` 是 DMS 回显给人看的名字。
    **id 为空就表示"没映射上"** —— 那时候绝不能自动推送。
    """

    house_no: str = ""
    province_id: str = ""
    province_name: str = ""
    district_id: str = ""
    district_name: str = ""
    subdistrict_id: str = ""
    subdistrict_name: str = ""
    zipcode_id: str = ""
    zipcode: str = ""
    building: str = ""
    floor: str = ""
    room: str = ""
    village: str = ""
    moo: str = ""
    soi: str = ""
    road: str = ""


@dataclass(frozen=True)
class ThaiIdCardPayload:
    """从泰国身份证抽出来的身份字段，用来建客户。"""

    people_id: str
    first_name: str
    last_name: str
    birthday_be: str  # 佛历 dd/mm/yyyy，例如 "01/01/2530"
    address: ThaiAddress = field(default_factory=ThaiAddress)
    prefix_id: str = "17"
    prefix_name: str = ""
    phone: str = "0800000000"

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


@dataclass(frozen=True)
class DMSMasterRef:
    """DMS 主档的一行（顾问 / 车型 / 颜色 / 分店 …）。

    `extra` 装 DMS 为车型返回的尾列（品牌 id/名、类型、级别、变速箱、年份、
    马力、价格 …）—— 建单时**必须原样回显**，所以整段带着走。
    """

    id: str
    code: str
    name: str
    extra: tuple = ()


@dataclass(frozen=True)
class DMSBookingPayload:
    """一张订车单草稿需要的全部内容（身份证之外的部分）。"""

    doc_date_be: str
    delivery_date_be: str
    advisor: DMSMasterRef
    car: DMSMasterRef
    paint: DMSMasterRef
    place_book: DMSMasterRef
    term_sale: DMSMasterRef
    branch: DMSMasterRef
    team: DMSMasterRef
    regis_behalf: DMSMasterRef
    # 登记人姓名；空 = 沿用身份证姓名，保持既有调用方行为不变
    regis_name: str = ""
    # 订金支付渠道明细，元素 {"channel", "amount", "extra"}；由
    # `dms_payments.payment_form_fields` 聚合进表单，空 = 现状（订金 0.00）
    payments: tuple = ()
    # 顾问归属带出来的**原生组织字段**（branch/team + 四级主管，订车与销售两套）。
    # 由 `dms_booking_org.resolve_booking_org` 按顾问实时解出，整包进表单；
    # 空 = 这条路径还没解组织（不能拿分店列表首行硬填，见 booking_org 顶部）。
    organization_fields: tuple = ()


# booking_defaults 里属于「个人」的键：顾问 = 销售提成归属，是个人身份不是租户策略，
# **绝不随租户默认值继承下去**（否则全店的单都算到老板头上）。
PERSONAL_BOOKING_DEFAULT_KEYS = ("advisor_id", "advisor_code", "advisor_name")


@dataclass(frozen=True)
class BookingDefaults:
    """单个端点的建单默认值（存在 endpoint 配置的 `booking_defaults` 里）。

    存的是连接向导里选一次的 DMS 主档 id/code；缺的 id 在推送时从实时主档解析。
    """

    advisor_id: str = ""
    advisor_code: str = ""
    advisor_name: str = ""
    car_id: str = ""
    car_code: str = ""
    paint_id: str = ""
    paint_code: str = ""
    place_book_id: str = ""
    term_sale_id: str = ""
    branch_id: str = ""
    team_id: str = ""
    regis_behalf_id: str = ""
    delivery_days: int = 15

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "BookingDefaults":
        d = dict((config or {}).get("booking_defaults") or {})
        try:
            days = int(d.get("delivery_days") or 15)
        except (TypeError, ValueError):
            days = 15
        return cls(
            advisor_id=str(d.get("advisor_id") or "").strip(),
            advisor_code=str(d.get("advisor_code") or "").strip(),
            advisor_name=str(d.get("advisor_name") or "").strip(),
            car_id=str(d.get("car_id") or "").strip(),
            car_code=str(d.get("car_code") or "").strip(),
            paint_id=str(d.get("paint_id") or "").strip(),
            paint_code=str(d.get("paint_code") or "").strip(),
            place_book_id=str(d.get("place_book_id") or "").strip(),
            term_sale_id=str(d.get("term_sale_id") or "").strip(),
            branch_id=str(d.get("branch_id") or "").strip(),
            team_id=str(d.get("team_id") or "").strip(),
            regis_behalf_id=str(d.get("regis_behalf_id") or "").strip(),
            delivery_days=days,
        )
