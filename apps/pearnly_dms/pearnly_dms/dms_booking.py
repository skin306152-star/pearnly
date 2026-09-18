# -*- coding: utf-8 -*-
"""建订车单：一次 DMS 会话里「复核主档 → 建单 → 挂附件 → 刷主档缓存」。

照搬老产品 `services/line_dms/booking_flow.py:_book_in_session`（那是它的建单执行体）。

几个必须保留的设计：

1. **先复核再建单**：拿确认瞬间的实时主档核对逐问选中的车/色/店/银行，对不上就
   不建单、退回重问（`master_contract.reconcile`）；
2. **账套级互斥只护「取号→提交」**：同一账套不同销售账号并发取号会撞单号；
   但客户写入/挂附件/刷主档这些慢步骤**不进共享锁**（否则一个慢附件卡住全店）；
3. **附件在同一会话挂**（少一次登录），且**建单成功但附件没挂全要如实说**，
   绝不谎报附件成功；
4. 建完顺手全量刷主档缓存（零额外登录）。
"""

from __future__ import annotations

import dataclasses
import logging
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from pearnly_core.session_lock import account_lock, dms_booking_key

logger = logging.getLogger(__name__)


@contextmanager
def booking_lock(endpoint: Any, timeout_sec: float = 180.0):
    """同一 **DMS 账套**的「取号 → 提交」临界区（按服务地址，不按账号）。

    为什么建单不按账号锁：同一账套下多个销售账号并发取号（autonum）会撞单号。
    """
    import frappe

    name = endpoint if isinstance(endpoint, str) else str(
        getattr(endpoint, "name", "") or (endpoint or {}).get("name") or ""
    )
    system_url = ""
    try:
        system_url = str(
            frappe.db.get_value("Pearnly ERP Endpoint", name, "system_url") or ""
        )
    except Exception:
        logger.warning("[dms booking] 读 system_url 失败，锁键会退化成空地址", exc_info=True)
    with account_lock(dms_booking_key(system_url)) as got:
        yield got


def card_payload(payload: dict):
    """从 `qa.draft` 重建 `ThaiIdCardPayload`（客户已建，建单表单要回显身份/地址）。"""
    from pearnly_dms.dms_models import ThaiAddress, ThaiIdCardPayload

    qa = payload.get("qa") or {}
    d = qa.get("draft") or {}
    name = str((qa.get("customer") or {}).get("name") or "").strip()
    address = ThaiAddress(
        house_no=str(d.get("house_no") or ""),
        province_id=str(d.get("province_id") or ""),
        province_name=str(d.get("province_name") or ""),
        district_id=str(d.get("district_id") or ""),
        district_name=str(d.get("district_name") or ""),
        subdistrict_id=str(d.get("subdistrict_id") or ""),
        subdistrict_name=str(d.get("subdistrict_name") or ""),
        zipcode_id=str(d.get("zipcode_id") or ""),
        zipcode=str(d.get("zipcode") or d.get("zipcode_name") or ""),
        building=str(d.get("building") or ""),
        floor=str(d.get("floor") or ""),
        room=str(d.get("room") or ""),
        village=str(d.get("village") or ""),
        moo=str(d.get("moo") or ""),
        soi=str(d.get("soi") or ""),
        road=str(d.get("road") or ""),
    )
    first, _, last = name.partition(" ")
    return ThaiIdCardPayload(
        people_id=str(d.get("people_id") or ""),
        first_name=first,
        last_name=last,
        birthday_be=str(d.get("birthday_be") or ""),
        address=address,
        prefix_id=str(d.get("prefix_id") or "17"),
        prefix_name=str(d.get("prefix_name") or ""),
        phone=str(d.get("phone") or "0800000000"),
    )


def book_in_session(
    endpoint: Any,
    payload: dict,
    attach_files: Optional[List[dict]] = None,
    attach_failed: Optional[List[dict]] = None,
) -> Dict[str, Any]:
    """一个 DMS 会话内：复核主档 → 建单 → 挂附件 → 顺手全量刷主档缓存（零额外登录）。

    逐问选的 `place/term/regis/car/paint` id 覆盖端点默认值；**顾问**在逐问开局就按操作员
    的 DMS 账号匹配好（或端点上钉死）存进 `qa["advisor"]` —— 建单层
    （`_advisor_ref_strict`）按该 id 严格校验，**匹配不上宁可报错也不落到别人头上**；
    交车日用逐问值覆盖（端点默认只给推算基线）。

    返回 `{ok, booking_no, booking_id, attach_ok, attached, attach_failed}`
    或 `run_logged_in` 的 err dict（`ok=False`）。
    """
    from pearnly_dms.dms_booking_customer import card_from_customer
    from pearnly_dms.dms_client_base import DmsClientError
    from pearnly_dms.dms_company_banks import fetch_company_banks, validate_company_bank_payments
    from pearnly_dms.dms_intake import run_logged_in
    from pearnly_dms.dms_master_contract import reconcile
    from pearnly_dms.dms_masters_cache import refresh_from_client
    from pearnly_dms.dms_models import BookingDefaults
    from pearnly_dms.dms_push import booking_defaults_from_endpoint
    from pearnly_dms.dms_qa_util import find_row

    qa = payload.get("qa") or {}
    answers = qa.get("answers") or {}
    adv = qa.get("advisor") or {}
    defaults = dataclasses.replace(
        booking_defaults_from_endpoint(endpoint),
        advisor_id=str(adv.get("id") or ""),
        # 主档列表抖动时建单层按名字降级落顾问，所以名字也要一路带到 defaults
        advisor_name=str(adv.get("name") or ""),
        car_id=str((answers.get("car") or {}).get("id") or ""),
        paint_id=str((answers.get("paint") or {}).get("id") or ""),
        place_book_id=str((answers.get("place") or {}).get("id") or ""),
        term_sale_id=str((answers.get("term") or {}).get("id") or ""),
        regis_behalf_id=str((answers.get("regis") or {}).get("id") or ""),
    )
    card = card_payload(payload)
    delivery_be = str(answers.get("delivery_date_be") or "")
    customer_id = str((qa.get("customer") or {}).get("id") or "")

    def _do(cl, adapter):
        live_masters = {
            **cl.fetch_masters(strict=True),
            "company_banks": fetch_company_banks(adapter),
        }
        selected_car_id = str((answers.get("car") or {}).get("id") or "")
        selected_car = find_row(live_masters.get("cars"), selected_car_id)
        live_paints: List[list] = []
        if selected_car is not None:
            live_paints = cl._bshsd_all("txtcarpaint", idcar=selected_car_id)
            if live_paints is None:
                raise DmsClientError(
                    f"DMS paint master unavailable for car {selected_car_id!r}",
                    "ERR_DMS_MASTER_UNAVAILABLE",
                )
        preflight = reconcile(qa, live_masters, live_paints)
        if preflight["status"] != "ok":
            return {
                "ok": False,
                "preflight": preflight["status"],
                "field": preflight.get("field", ""),
                "error_code": preflight.get("code", "ERR_DMS_MASTER_CHANGED"),
                "qa": preflight["qa"],
            }
        payload["qa"] = preflight["qa"]

        if qa.get("customer_dirty"):
            draft = dict(qa.get("draft") or {})
            draft["name"] = str((qa.get("customer") or {}).get("name") or "")
            cl.save_customer(fields=draft, mode="overwrite", customer_id=customer_id)
        master_card = card_from_customer(cl, customer_id=customer_id, people_id=card.people_id)
        booking = cl.resolve_booking_payload(defaults, master_card)
        if delivery_be:
            payments = validate_company_bank_payments(adapter, qa.get("payments") or [])
            booking = dataclasses.replace(
                booking,
                delivery_date_be=delivery_be,
                regis_name=str(answers.get("regis_name") or ""),
                payments=tuple(payments),
            )
        # 账套级互斥只护「取号 → 提交」：同账套不同销售账号并发时别撞单号。
        # 客户写入 / 附件挂载 / 主档刷新这些慢步骤不进共享锁。
        with booking_lock(endpoint):
            booking_id, booking_no = cl.create_booking_via_form(
                customer_id=customer_id, booking=booking, card=master_card
            )
        attached = 0
        failed = list(attach_failed or [])
        if attach_files:
            res = cl.attach_booking_files(booking_id=booking_id, files=attach_files)
            attached = int(res.get("attached") or 0)
            failed += list(res.get("failed") or [])
        refresh_from_client(endpoint, cl)
        return {
            "ok": True,
            "booking_id": booking_id,
            "booking_no": booking_no,
            "attach_ok": not failed,
            "attached": attached,
            "attach_failed": failed,
        }

    return run_logged_in(endpoint, _do)
