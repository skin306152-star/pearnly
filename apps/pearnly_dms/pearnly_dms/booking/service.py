# -*- coding: utf-8 -*-
"""建订车单：一次 DMS 会话里「复核主档 → 建单并回读 → 挂附件」。

照搬老产品 `services/line_dms/booking_flow.py:_book_in_session`（那是它的建单执行体）。

几个必须保留的设计：

1. **先复核再建单**：拿确认瞬间的实时主档核对逐问选中的车/色/店/银行，对不上就
   不建单、退回重问（`master_contract.reconcile`）；
2. **账套级互斥只护「取号→提交」**：同一账套不同销售账号并发取号会撞单号；
   但客户写入/挂附件/刷主档这些慢步骤**不进共享锁**（否则一个慢附件卡住全店）；
3. **附件在同一会话挂**（少一次登录）；**建单成功但附件没挂全**如实记 `attach_ok=False`，
   绝不谎报附件成功 —— 回执不等它，缺的附件由 `delivery` 后台补，补不上才说一次；
4. 成功不自动刷新员工销售全量；不把部分复核资料冒充全量刷新成功。
5. **老客户主档缺订车必填项**（生日、称谓、电话、门牌、四级地址）时，以前到建单前一步
   必然失败、重试也一样。现在：本人有改客户资料的权限（与差异卡给「更新」同一判据），
   就用本次草稿里的原值只补 DMS 空着的项，一次、单独登记，补齐后照常建单；补不齐不写，
   退回预览卡写明缺项；没有权限就列出缺项、给重试（管理员在 DMS 补好后重试会实时重读）。
   见 `_fill_customer_gaps`。资料齐全的单一步都不多走。
"""

from __future__ import annotations

import dataclasses
import logging
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from pearnly_core.session_lock import account_lock, dms_booking_key
from pearnly_dms.contracts.endpoint import endpoint_name

logger = logging.getLogger(__name__)


@contextmanager
def booking_lock(endpoint: Any, timeout_sec: float = 180.0):
    """同一 **DMS 账套**的「取号 → 提交」临界区（按服务地址，不按账号）。

    为什么建单不按账号锁：同一账套下多个销售账号并发取号（autonum）会撞单号。
    """
    import frappe

    name = endpoint_name(endpoint)
    if not name:
        raise ValueError("DMS endpoint is required")
    system_url = str(frappe.db.get_value("Pearnly ERP Endpoint", name, "system_url") or "").strip()
    if not system_url:
        raise ValueError("DMS endpoint system URL is required")
    with account_lock(dms_booking_key(system_url), timeout_sec=timeout_sec) as got:
        yield got


def card_payload(payload: dict):
    """从 `qa.draft` 重建 `ThaiIdCardPayload`（客户已建，建单表单要回显身份/地址）。"""
    from pearnly_dms.contracts.models import ThaiAddress, ThaiIdCardPayload

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


#: 客户主档缺订车必填项：本人能补（退回预览卡改）/ 只能由管理员在 DMS 补（给重试）。
CUSTOMER_INCOMPLETE = "ERR_DMS_CUSTOMER_INCOMPLETE"
CUSTOMER_INCOMPLETE_ADMIN = "ERR_DMS_CUSTOMER_INCOMPLETE_ADMIN"


def _incomplete(missing, code: str = CUSTOMER_INCOMPLETE) -> Dict[str, Any]:
    from pearnly_dms.adapter.operations import err

    return err(code, response_body={"submitted": False, "missing": list(missing)})


def _fill_customer_gaps(cl, endpoint: str, payload: dict, gap, *, guard, cache_scope) -> Optional[Dict[str, Any]]:
    """确认订车时客户主档缺必填项：只补空项、写一次。返回 None = 已补，调用方重读后建单；
    否则返回要交给 LINE 层的失败结果（订车单一律没开始建）。

    · 权限：与差异卡给「更新」同一判据（`intake.draft.may_update_customer`）；没有就不写，
      报 `ERR_DMS_CUSTOMER_INCOMPLETE_ADMIN`（列缺项、给重试）；
    · 取值：`booking_customer.gap_fill`（只补空项、地址只按整套），补不齐一项都不写；
    · 写法与改客户资料那一步相同：独立登记号 ``booking-customer-fill:<确认号>``、先登记后提交、
      提交前再核当前绑定、以最终那次权威读取为对照基准做三方合并、写后回读、写后更新本地快照；
      被一次结果未确认的写入挡着时只读核对它，绝不重发；
    · 提交前就被 DMS 表单的称谓/地址校验拒收（什么都没发出）按补不齐处理；读写之间被人改过
      → 现有的冲突复核卡；结果不明 → 现有的"未确认"重试卡。
    """
    from pearnly_dms.adapter import snapshot_writeback
    from pearnly_dms.adapter.operations import err
    from pearnly_dms.booking.booking_customer import gap_fill
    from pearnly_dms.contracts import customer_merge
    from pearnly_dms.intake.customer_attempt import Attempt
    from pearnly_dms.intake.draft import has_admin_creds, may_update_customer

    qa = payload.get("qa") or {}
    if not may_update_customer(qa.get("user_id"), has_admin_creds(endpoint)):
        return _incomplete(gap.missing, CUSTOMER_INCOMPLETE_ADMIN)
    values = gap_fill(gap.fields, qa.get("draft") or {})
    if not values:
        return _incomplete(gap.missing)
    customer_id = str((qa.get("customer") or {}).get("id") or "")
    fields, addresses = customer_merge.split(values)
    marker = Attempt(endpoint, "booking-customer-fill:" + str(payload.get("nonce") or ""),
                     qa.get("user_id"), fields=fields, mode="overwrite", customer_id=customer_id,
                     addresses=addresses, baseline=gap.fields)

    def begin_customer(verify):
        if callable(guard):
            import frappe
            frappe.db.commit()  # live form reads must not freeze authority in an old RR view
            guard()
        marker.begin(verify)

    try:
        try:
            done = marker.previous()
        except customer_merge.DmsClientError as exc:
            if exc.error_code != "ERR_DMS_CUSTOMER_UNCONFIRMED":
                raise
            marker.reconcile(cl)
            done = marker.previous()
        if not done:
            cl.save_customer(fields=fields, mode="overwrite", customer_id=customer_id,
                             addresses=addresses, baseline=gap.fields, on_attempt=begin_customer)
            marker.finish({"ok": True, "success": True, "customer_id": customer_id, "mode": "overwrite"})
    except customer_merge.DmsClientError as exc:
        if exc.error_code == "ERR_DMS_MASTER_UNMATCHED" and not marker.dispatched():
            return _incomplete(gap.missing)
        return marker.finish(err(exc.error_code, response_body=getattr(exc, "response_body", None)))
    snapshot_writeback.customer(endpoint, cl, customer_id, expected_scope=cache_scope)
    return None


def book_in_session(
    endpoint: Any,
    payload: dict,
    attach_files: Optional[List[dict]] = None,
    attach_failed: Optional[List[dict]] = None,
    *,
    on_attempt=None,
) -> Dict[str, Any]:
    """一个 DMS 会话内：复核主档 → 建单并回读 → 挂附件。

    逐问选的 `place/term/regis/car/paint` id 覆盖端点默认值；**顾问**在逐问开局就按操作员
    的 DMS 账号匹配好（或端点上钉死）存进 `qa["advisor"]` —— 建单层
    （`_advisor_ref_strict`）按该 id 严格校验，**匹配不上宁可报错也不落到别人头上**；
    交车日用逐问值覆盖（端点默认只给推算基线）。

　　返回 `{ok, booking_no, booking_id, attach_ok, attached, attach_failed}`
    或 `run_logged_in` 的 err dict（`ok=False`）。

    ⚠️ `on_attempt`（提交前登记，防重复建单）**必须原样透传**到建单调用：LINE 那条链
    只有这一个门进建单，半路丢了回调等于登记永远不生效 —— 而症状只在超时/重发时出现。
    真正的"登记先于写"判据在 `dms_booking_submit.submit_booking`（脚本 5.2 钉住）。
    """
    from pearnly_dms.adapter.push import booking_defaults_from_endpoint
    from pearnly_dms.booking.booking_customer import CustomerIncomplete, card_from_customer
    from pearnly_dms.booking import booking_review
    from pearnly_dms.booking.booking_org import resolve_booking_org
    from pearnly_dms.booking.booking_payload import build_booking_payload
    from pearnly_dms.booking.booking_preflight import local_preflight
    from pearnly_dms.contracts.client_base import DmsClientError
    from pearnly_dms.snapshots import store
    from pearnly_dms.snapshots.directory import Directory
    from pearnly_dms.contracts.payments import validate_payment_completeness
    from pearnly_dms.adapter.operations import err, run_logged_in
    from pearnly_dms.masters.company_banks import validate_company_bank_payments
    from pearnly_dms.adapter import snapshot_writeback

    # Resolver returns a mapping; Frappe treats mappings as SQL filters, not document names.
    endpoint = endpoint_name(endpoint)
    if not endpoint:
        return err("ERR_NO_CREDS")
    qa = payload.get("qa") or {}
    cache_scope = snapshot_writeback.capture_scope(endpoint)

    def _do(cl, adapter):
        nonlocal qa
        validate_payment_completeness(qa.get("payments") or [])
        # 提交前复核主档**对照本地完整快照**，不再借管理员去 DMS 实时读（ADR-014；用户
        # 2026-09-27："主档信息一般不会经常变来变去"）。快照由白天每分钟增量 + 凌晨全量
        # 保持新鲜。客户资料卡、顾问组织仍是销售会话实时读、读不到才借管理员（各自的回退）。
        directory = Directory(str(qa.get("endpoint_id") or endpoint))
        try:
            preflight = local_preflight(qa, directory)
        except (store.SnapshotUnavailable, DmsClientError) as exc:
            return err(getattr(exc, "error_code", "") or "ERR_DMS_MASTER_UNAVAILABLE", str(exc))
        if preflight["status"] != "ok":
            return {
                "ok": False,
                "preflight": preflight["status"],
                "field": preflight.get("field", ""),
                "error_code": preflight.get("code", "ERR_DMS_MASTER_CHANGED"),
                "qa": preflight["qa"],
            }
        qa = payload["qa"] = preflight["qa"]
        answers = qa.get("answers") or {}
        adv = qa.get("advisor") or {}
        defaults = dataclasses.replace(
            booking_defaults_from_endpoint(endpoint),
            advisor_id=str(adv.get("id") or ""),
            advisor_name=str(adv.get("name") or ""),
            car_id=str((answers.get("car") or {}).get("id") or ""),
            paint_id=str((answers.get("paint") or {}).get("id") or ""),
            place_book_id=str((answers.get("place") or {}).get("id") or ""),
            term_sale_id=str((answers.get("term") or {}).get("id") or ""),
            regis_behalf_id=str((answers.get("regis") or {}).get("id") or ""),
        )
        card = card_payload(payload)
        customer_id = str((qa.get("customer") or {}).get("id") or "")
        # Master/identity reads can be slow. A reset or a rebind must revoke
        # both the customer update and the later order submission.
        guard = getattr(on_attempt, "assert_current", None)
        if callable(guard):
            import frappe
            frappe.db.commit()
            guard()
        if qa.get("customer_dirty"):
            from pearnly_dms.contracts import customer_merge
            from pearnly_dms.intake.customer_attempt import Attempt
            draft = dict(qa.get("draft") or {})
            draft["name"] = str((qa.get("customer") or {}).get("name") or "")
            baseline = qa.get("customer_baseline")
            fields = {key: value for key, value in draft.items()
                      if key in customer_merge.IDENTITY + customer_merge.ADDRESS
                      and (baseline is None or str(value or "") != str(baseline.get(key) or ""))}
            fields, addresses = customer_merge.split(qa.get("customer_changes",
                                                     customer_merge.flatten(fields)))
            marker = Attempt(endpoint, "booking-customer:" + str(payload.get("nonce") or ""),
                qa.get("user_id"), fields=fields, mode="overwrite", customer_id=customer_id,
                addresses=addresses, baseline=baseline)
            # DmsClient.save_customer restores the original sales writer transport.
            def begin_customer(verify):
                if callable(guard):
                    import frappe
                    frappe.db.commit()  # live form reads must not freeze authority in an old RR view
                    guard()
                marker.begin(verify)
            try:
                try:
                    done = marker.previous()
                except customer_merge.DmsClientError as exc:
                    if exc.error_code != "ERR_DMS_CUSTOMER_UNCONFIRMED":
                        raise
                    # 被一次结果未确认的客户写入挡着：本会话里先只读核对它（管理员会话，绝不重发），
                    # 再看一次；仍然不明就照旧报"未确认"（订车单还没开始建，界面给重试卡）。
                    marker.reconcile(cl)
                    done = marker.previous()
                if not done:
                    cl.save_customer(fields=fields, mode="overwrite", customer_id=customer_id,
                                     addresses=addresses, baseline=baseline, on_attempt=begin_customer)
                    marker.finish({"ok": True, "success": True, "customer_id": customer_id,
                                   "mode": "overwrite"})
            except customer_merge.DmsClientError as exc:
                result = err(exc.error_code, response_body=getattr(exc, "response_body", None))
                return marker.finish(result)
            snapshot_writeback.customer(endpoint, cl, customer_id, expected_scope=cache_scope)
        try:
            master_card = card_from_customer(cl, customer_id=customer_id, people_id=card.people_id)
        except CustomerIncomplete as gap:
            failed = _fill_customer_gaps(cl, endpoint, payload, gap, guard=guard, cache_scope=cache_scope)
            if failed:
                return failed
            try:  # 补完再按同一判据读一次；仍缺就退回预览卡，不再写第二次
                master_card = card_from_customer(cl, customer_id=customer_id, people_id=card.people_id)
            except CustomerIncomplete as still:
                return _incomplete(still.missing)
        org = resolve_booking_org(cl, defaults.advisor_id, defaults)
        booking = build_booking_payload(
            defaults, masters=preflight["masters"], paints=preflight["paints"], org=org,
        )
        payments = validate_company_bank_payments(adapter, qa.get("payments") or [], client=cl,
                                                  catalogues=preflight["masters"])
        # Only submit on the original sales writer, using the single validated snapshot.
        booking = dataclasses.replace(
            booking,
            delivery_date_be=str(answers.get("delivery_date_be") or booking.delivery_date_be),
            regis_name=str(answers.get("regis_name") or ""),
            payments=tuple(payments),
        )
        readback: dict = {}
        with booking_lock(endpoint):
            booking_id, booking_no = cl.create_booking_via_form(
                customer_id=customer_id, booking=booking, card=master_card,
                on_attempt=on_attempt, readback_out=readback,
            )
        attached = 0
        failed = list(attach_failed or [])
        if attach_files:
            try:
                res = cl.attach_booking_files(booking_id=booking_id, files=attach_files)
                attached = int(res.get("attached") or 0)
                failed += list(res.get("failed") or [])
            except Exception:
                logger.warning("[dms booking] attachment failed after booking commit", exc_info=True)
                failed.append({"error_code": "ERR_DMS_ATTACHMENT"})
        return {
            "ok": True,
            "booking_id": booking_id,
            "booking_no": booking_no,
            "attach_ok": not failed,
            "attached": attached,
            "attach_failed": failed,
            # 回执逐项复核（核实那一轮读到的表单 vs 提交值）；只决定回执怎么写，不改结果。
            "review": booking_review.review(readback),
            "cache_updated": False,  # Compatibility: no complete sales read yet.
            "cache_refresh_requested": False,
        }

    # 操作员自己发起的一笔：做完顺手把 DMS 使用记录里的增量补回缓存。
    # `line_write`：登录页都没打开时同一任务自动再试一次；任务时限到点报可重试的网络异常。
    return run_logged_in(endpoint, _do, catch_up=True, line_write=True)
