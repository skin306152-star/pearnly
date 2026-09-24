# -*- coding: utf-8 -*-
"""**客户档**的浏览器编辑器（LINE 内同一个 LIFF 页，`&editor=customer`）后端。

照搬老产品 `services/line_dms/customer_edit.py`（线上基准 `501589f0`），async → 同步。

与订车编辑器的差别（三条，都是线上既有行为，不是新设计）：

  · 会话态是 **`reviewing`**（客户档确认卡那一态），不是 `booking_review`；
  · 只改**客户资料**，不动订车的答案/付款/附件；
  · 保存后**不写 DMS**：按本地完整客户快照再查重（`recognize_lookup`），把差异算出来，
    发一张新的确认卡/差异卡回 LINE —— 真正的写入仍然要用户在 LINE 里点确认。

`_prefix_id` 那两行是给"身份证识别出的称谓在主档里暂时没命中"兜底的：合并现值时必须
先用 OCR 称谓反查主档 id；查不到就把 id 清空，让用户自己选，**不能拿一个错的 id 顶上去**。
"""

from __future__ import annotations

import logging
import secrets
from typing import Any, Dict, List, Optional, Tuple

from pearnly_core import line_session as store
from pearnly_dms.approval import approval_flow as approval_flow
from pearnly_dms.editor import booking_edit as editor
from pearnly_dms.editor.edit_snapshot import GEO_FIELDS
from pearnly_dms.line import cards as cards
from pearnly_dms.intake import draft as draft
from pearnly_dms.menu import menu_flow as menu_flow
from pearnly_dms.line import out as _out
from pearnly_dms.intake.customer_diff import diff_customer_fields
from pearnly_dms.intake.service import geo_options as _geo_options
from pearnly_dms.intake.service import recognize_lookup
from pearnly_dms.ocr.ocr_service import resolve_dms_endpoint

logger = logging.getLogger(__name__)

STATE = "reviewing"


def _review(line_user_id: str, nonce: str) -> Tuple[dict, dict, dict]:
    """绑定 + `reviewing` 态 + nonce（只校验不消费）→ (binding, payload, endpoint)。"""
    binding = editor._binding(line_user_id)
    session = store.get_session(line_user_id)
    if not store.verify_nonce(session, nonce, STATE):
        raise editor.DmsBookingEditError("dms_booking.expired", 409)
    payload = (session or {}).get("payload") or {}
    endpoint = resolve_dms_endpoint(binding["user_id"], payload.get("endpoint_id"))
    if not endpoint:
        raise editor.DmsBookingEditError("dms_booking.no_endpoint", 409)
    return binding, payload, endpoint


def load(line_user_id: str, nonce: str) -> dict:
    """首屏补客户现值；OCR 未解析的地址保持空选，不能用不同旧地址掩盖。"""
    _, payload, endpoint = _review(line_user_id, nonce)
    customer = dict(payload.get("draft") or {})
    baseline = payload.get("customer_baseline")
    if baseline:
        customer = {**baseline, **customer}
    preserve_empty_geo = (
        bool(payload.get("id_card")) and not payload.get("customer_edited")
        and any(key in customer and not str(customer[key] or "").strip() for key, _ in GEO_FIELDS)
    )
    snapshot = editor._snapshot(
        endpoint, customer=customer,
        customer_id="" if baseline or payload.get("customer_edited") else payload.get("customer_id", ""),
        preserve_empty_geo=preserve_empty_geo,
    )
    # 先把 OCR 的称谓在主档里解出 id（解不出就留空，交给用户在下拉里选）。
    title = customer.get("prefix_name") or (payload.get("id_card") or {}).get("prefix_name")
    if title and not customer.get("prefix_id"):
        customer["prefix_id"] = draft._prefix_id(title, snapshot["prefixes"])
    # 草稿里有值的字段优先（用户/OCR 已经确认过的），其余用 DMS 现值补。
    merged = {**snapshot.get("resolved_customer", {}), **{k: v for k, v in customer.items() if v}}
    if preserve_empty_geo:
        # The snapshot cleared an unresolved level and its children before loading
        # options. Do not reintroduce stale child IDs/labels from the raw draft.
        for id_field, name_field in GEO_FIELDS:
            for key in (id_field, name_field):
                merged[key] = (snapshot.get("resolved_customer") or {}).get(key, "")
    if title and not customer.get("prefix_id"):
        merged["prefix_id"] = ""
    return {
        "form": {"customer": merged},
        "snapshot": snapshot.get("snapshot", {}),
        "masters": {"prefixes": editor._options(snapshot["prefixes"])},
        "geo": {k: editor._options(v) for k, v in snapshot["geo"].items()},
    }


def geo(line_user_id: str, nonce: str, level: str, parent_id: str = "") -> List[dict]:
    """地址级联代理（与订车编辑器同一份实现，只是会话态不同）。"""
    _, _, endpoint = _review(line_user_id, nonce)
    if level not in {"provinces", "districts", "subdistricts", "zipcodes"}:
        raise editor.DmsBookingEditError("dms_booking.invalid_geo")
    result = _geo_options(endpoint, level=level, parent_id=parent_id)
    if not result.get("ok"):
        raise editor._read_error(result, "dms_booking.geo_unavailable")
    return editor._options(result.get("options") or [])


def _card(
    binding: dict,
    payload: dict,
    customer: dict,
    lookup: dict,
    snapshot: dict,
    nonce: str,
    has_admin: bool,
) -> Tuple[dict, dict]:
    """按查重结果造新 payload + 对应的回 LINE 卡片（四分支与 LINE 对话里那条完全一致）。"""
    summary = draft.build_summary(customer, {**snapshot["geo"], "selected": customer})
    incoming = {
        **(payload.get("id_card") or {}),
        **{k: customer[k] for k in ("people_id", "name", "birthday_be", "prefix_name")},
        "address": {
            **customer,
            "province": customer["province_name"],
            "district": customer["district_name"],
            "subdistrict": customer["subdistrict_name"],
        },
    }
    # 上一次确认留下的派生态（候选/显式差异/审批）必须先清掉，否则新卡会带着旧结论。
    new = {
        k: v
        for k, v in payload.items()
        if k
        not in {
            "customer_id",
            "master_draft",
            "master_summary",
            "approval",
            "has_admin",
            "display_diffs",
            "write_fields",
            "write_addresses",
            "customer_baseline",
        }
    }
    new.update(
        customer_edited=True,
        draft=customer,
        summary=summary,
        id_card=incoming,
        phone=customer["phone"],
        nonce=nonce,
        field_diffs=[],
        scenario=lookup["scenario"],
    )
    if lookup["scenario"] == "exact":
        current = lookup["match"]["current_fields"]
        if (str(payload.get("customer_id")) == str(lookup["match"]["customer_id"])
                and payload.get("customer_baseline") is not None):
            current = payload["customer_baseline"]
        diffs = diff_customer_fields(current, customer)
        # 网页端可以**显式清空**可选地址栏（楼/层/房/村/หมู่/ซอย/ถนน）—— OCR 的空白不算，
        # 所以这里单独把"原本有值、现在被清掉"的字段补成差异，否则用户的清除动作会静默失效。
        for key in ("building", "floor", "room", "village", "moo", "soi", "road"):
            if current.get(key) and not customer.get(key):
                diffs.append({"field": key, "old": str(current[key]), "new": ""})
        new.update(
            customer_id=lookup["match"]["customer_id"], scenario="exact_diff", field_diffs=diffs
        )
        new["customer_baseline"] = dict(current)
        new["master_draft"], new["master_summary"] = draft.master_snapshot(current)
        if diffs:
            display = draft.display_diffs(
                diffs, {**snapshot["geo"], "prefixes": snapshot["prefixes"]}
            )
            # 端点写权限由**服务端**判：前端传来的任何东西都不作为依据。
            card, approval = approval_flow.exact_diff_card(
                binding["user_id"], display, has_admin, nonce, summary
            )
            new.update(display_diffs=display, has_admin=has_admin, approval=approval)
        else:
            card = (
                cards.same_customer_card
                if new.get("mode") == menu_flow.MODE_CUSTOMER
                else cards.booking_customer_card
            )(summary, nonce)
    elif lookup["scenario"] == "none":
        card = cards.new_customer_card(summary, nonce)
    else:
        new["candidates"] = lookup.get("candidates") or []
        card = cards.candidates_card(new["candidates"], nonce)
    return new, card


def save(line_user_id: str, nonce: str, submitted: dict) -> str:
    """保存客户档并**同步确认**新卡已送达 LINE；返回轮换后的新 nonce。**不写 DMS**。"""
    binding, payload, endpoint = _review(line_user_id, nonce)
    customer = editor._customer(dict(submitted.get("customer") or {}))
    snapshot = editor._snapshot(endpoint, customer=customer)
    labels = editor._customer_master_labels(snapshot, customer)
    if labels is None:
        raise editor.DmsBookingEditError("dms_booking.invalid_master")
    customer.update(labels)
    customer["tax_id"] = customer["people_id"]
    lookup = recognize_lookup(
        endpoint,
        people_id=customer["people_id"],
        name=customer["name"],
        ocr_address=customer,
        phone=customer["phone"],
    )
    if not lookup.get("ok"):
        raise editor._read_error(lookup, "dms_booking.master_unavailable")
    new_nonce = secrets.token_hex(8)
    new, card = _card(
        binding, payload, customer, lookup, snapshot, new_nonce, draft.has_admin_creds(endpoint)
    )
    if not store.replace_review_payload(line_user_id, nonce, new, state=STATE):
        raise editor.DmsBookingEditError("dms_booking.expired", 409)
    try:
        if not _out.send(line_user_id, card):
            raise RuntimeError("customer_preview_not_delivered")
    except Exception as exc:
        # 卡没发出去 → 恢复旧 payload/nonce，用户还能重试同一份草稿。
        store.replace_review_payload(line_user_id, new_nonce, payload, state=STATE)
        raise editor.DmsBookingEditError("dms_booking.preview_send_failed", 503) from exc
    return new_nonce


__all__ = ["STATE", "geo", "load", "save"]
