"""Durable follow-up for an already verified order. Never creates an order.

Detaching this evidence from the editable conversation allows a new draft even
when LINE is unavailable. Expired/changed-identity receipts remain in Review,
never silently marked Delivered and never retried with a fresh key.

A submission whose result is still unknown is detached the same way, as an
``Unverified`` record with no ``booking_id`` (``hold``): the conversation is free
at once and ``recovery.reconcile_record`` keeps reading it back. Only ``retain``
turns it into ``Pending`` (in place, same record) once the order is verified.
"""
import hashlib
import json
import logging
import uuid

import frappe

from pearnly_core import line
from pearnly_dms.booking import attachments, booking_ledger
from pearnly_dms.booking.booking_readback import docno_equal
from pearnly_dms.contracts.endpoint import endpoint_name
from pearnly_dms.adapter.operations import run_logged_in
from pearnly_dms.line import cards
from pearnly_dms.line.identity import matches
from pearnly_dms.ocr.ocr_service import resolve_dms_endpoint

DT = "DMS Booking Delivery"
WINDOW_SECONDS = 23 * 3600
#: 结果未知、正在后台核对的提交证据（`booking_id` 为空；`schedule_due` 只取 Pending，不碰它）。
UNVERIFIED = "Unverified"
#: 第一次后台核对：登记后 5 分钟（执行体里已经核过一轮；崩溃后才转存的，下一轮扫描就核）。
FIRST_CHECK_SECONDS = 300
logger = logging.getLogger(__name__)

def _state_store():
    """不淘汰的状态存储（``pearnly_core.execution.state``；用到时才导入，离线检查导入本模块不需要共用层）。"""
    from pearnly_core.execution import state as pearnly_state

    return pearnly_state.store()



def age(stamp):
    return (frappe.utils.get_datetime() - frappe.utils.get_datetime(stamp)).total_seconds()


def identity_fingerprint(endpoint):
    row = frappe.db.get_value("Pearnly ERP Endpoint", endpoint_name(endpoint),
                              ["system_url", "username"], as_dict=True)
    if not row or not row.get("system_url") or not row.get("username"):
        raise ValueError("DMS endpoint identity is unavailable")
    return hashlib.sha256(json.dumps(dict(row), sort_keys=True).encode()).hexdigest()


def name_for(line_user_id, nonce):
    return hashlib.sha256(f"{line_user_id}:{nonce}".encode()).hexdigest()


def hold(line_user_id, marker):
    """结果未知：把会话里的提交证据原样转存成 `Unverified` 记录并**先提交**。

    返回 `(记录名, 是否本次新建)`。记录名就是 `name_for(uid, nonce)`，重复调用只有一条；
    调用方必须在这之后才清会话（先落盘、后清会话，与 `recovery.finish` 同一顺序）。
    """
    name = name_for(line_user_id, marker["nonce"])
    if frappe.db.exists(DT, name):
        if frappe.db.get_value(DT, name, "booking_no") != marker["booking_no"]:
            raise ValueError("Unverified booking evidence conflicts with delivery record")
        return name, False
    first = frappe.utils.add_to_date(marker.get("recorded_at") or frappe.utils.now(),
                                     seconds=FIRST_CHECK_SECONDS)
    doc = frappe.get_doc({"doctype": DT, "name": name, "line_user_id": line_user_id,
        "nonce": marker["nonce"], "booking_no": marker["booking_no"], "booking_id": "",
        "status": UNVERIFIED, "attempt": json.dumps(marker, ensure_ascii=False),
        "next_attempt_at": first})
    doc.insert(ignore_permissions=True, set_name=name)
    frappe.db.commit()  # Must survive before the conversation may be cleared.
    return name, True


def retain(line_user_id, marker):
    name = name_for(line_user_id, marker["nonce"])
    if frappe.db.exists(DT, name):
        saved = frappe.get_doc(DT, name)
        booking_id = str(marker["result"]["booking_id"])
        if saved.booking_no == marker["booking_no"] and saved.status == UNVERIFIED and not saved.booking_id:
            # 后台核实了那张结果未知的单：就地补上单据 id、转待送达（一张单只有一条记录）。
            frappe.db.set_value(DT, name, {"booking_id": booking_id, "status": "Pending",
                "attempt": json.dumps(marker, ensure_ascii=False), "last_error": "",
                "next_attempt_at": frappe.utils.now()})
            frappe.db.commit()
            return name
        if saved.booking_no != marker["booking_no"] or saved.booking_id != booking_id:
            raise ValueError("Completed booking evidence conflicts with delivery record")
        return name
    doc = frappe.get_doc({"doctype": DT, "name": name, "line_user_id": line_user_id,
        "nonce": marker["nonce"], "booking_no": marker["booking_no"],
        "booking_id": str(marker["result"]["booking_id"]), "status": "Pending",
        "attempt": json.dumps(marker, ensure_ascii=False), "next_attempt_at": frappe.utils.now()})
    doc.insert(ignore_permissions=True, set_name=name)
    frappe.db.commit()  # Must survive before the conversation may be cleared.
    return name


def _save(doc, marker, *, status="Pending", error=""):
    tries = (marker.get("delivery") or {}).get("attempts", 0)
    frappe.db.set_value(DT, doc.name, {"attempt": json.dumps(marker, ensure_ascii=False),
        "status": status, "last_error": error,
        "next_attempt_at": frappe.utils.add_to_date(seconds=min(3600, 60 * 2 ** min(tries, 6)))})
    frappe.db.commit()


def _repair_files(doc, marker):
    context = marker["context"]
    binding = context["binding"]
    endpoint = resolve_dms_endpoint(binding["user_id"], context["endpoint"])
    if identity_fingerprint(endpoint) != context.get("endpoint_fingerprint"):
        raise ValueError("DMS identity changed")
    expected = attachments.expected_names(context.get("qa") or {})

    def repair(client, adapter):
        # Repair only the verified document, under the original sales identity.
        form = client._parse_form_defaults(client._post_text(
            "drfcbc/form.php", {"status": "e", "id": doc.booking_id}))
        if not docno_equal(doc.booking_no, form) or form.get("idsel") != doc.booking_id:
            raise ValueError("Attachment recovery document identity changed")
        names = {v for k, v in form.items() if k.startswith("fulcurrname")}
        missing = expected - names
        if not missing:
            return {"ok": True, "attached": len(expected), "failed": []}
        files, failed = attachments.download(context["qa"], missing=missing)
        # A rebind during download revokes the write as well as the receipt.
        frappe.db.commit()
        if not matches(binding, doc.line_user_id):
            raise ValueError("LINE binding changed")
        result = client.attach_booking_files(booking_id=doc.booking_id, files=files)
        failures = failed + result.get("failed", [])
        return {"ok": not failures, "attached": len(expected & names) + result.get("attached", 0),
                "failed": failures}

    # 后台任务（派进 long 队列），没人等着看结果 → 预算走共用口径的后台那一档（90 秒）。
    result = run_logged_in(endpoint, repair, background=True)
    marker["result"].update(attach_ok=bool(result.get("ok")), attached=result.get("attached", 0),
                            attach_failed=result.get("failed") or [])
    return bool(result.get("ok"))


def _receipt_text(doc, marker):
    qa = marker["context"].get("qa") or {}
    answers = qa.get("answers") or {}
    return cards.booking_receipt_text(doc.booking_no,
        str((answers.get("car") or {}).get("label") or ""),
        str(answers.get("delivery_date_be") or ""), str((qa.get("advisor") or {}).get("name") or ""))


def deliver(name):
    cache, owner = _state_store(), uuid.uuid4().hex
    key = cache.make_key("pearnly:dms:delivery:" + name)
    if not cache.set(key, owner, nx=True, ex=660):
        return False
    try:
        doc = frappe.get_doc(DT, name)
        if doc.status != "Pending":
            return doc.status == "Delivered"
        marker = json.loads(doc.attempt)
        binding = marker["context"]["binding"]
        if not matches(binding, doc.line_user_id):
            _save(doc, marker, status="Review", error="binding_changed")
            return False
        if age(marker.get("completed_at") or marker["recorded_at"]) >= WINDOW_SECONDS:
            _save(doc, marker, status="Review", error="delivery_window_expired")
            return False
        marker["delivery"] = {"attempts": (marker.get("delivery") or {}).get("attempts", 0) + 1}
        _save(doc, marker)  # Persist next retry before any IO.
        if not marker["result"].get("attach_ok"):
            if not _repair_files(doc, marker):
                _save(doc, marker, error="attachments_pending")
                return False
            booking_ledger.log_booking(endpoint=marker["context"]["endpoint"],
                payload={"qa": marker["context"].get("qa") or {}}, result=marker["result"],
                actor=binding["user_id"])
        receipt = marker.get("receipt")
        if not receipt:
            from pearnly_dms import i18n

            with i18n.line_language(doc.line_user_id):  # 回执按收件人自己的手机语言
                receipt_text = _receipt_text(doc, marker)
            receipt = {"key": str(uuid.uuid4()), "created_at": frappe.utils.now(), "text": receipt_text}
            marker["receipt"] = receipt
        _save(doc, marker)
        frappe.db.commit()
        if not matches(binding, doc.line_user_id):
            _save(doc, marker, status="Review", error="binding_changed")
            return False
        if age(receipt["created_at"]) >= WINDOW_SECONDS:
            _save(doc, marker, status="Review", error="receipt_key_expired")
            return False
        accepted = line.push(doc.line_user_id, [{"type": "text", "text": receipt["text"]}], retry_key=receipt["key"])
        _save(doc, marker, status="Delivered" if accepted else "Pending", error="" if accepted else "receipt_pending")
        return bool(accepted)
    except Exception:
        logger.warning("Completed order delivery retained for retry", exc_info=True)
        return False
    finally:
        cache.eval("if redis.call('get',KEYS[1]) == ARGV[1] then return redis.call('del',KEYS[1]) else return 0 end",
                   1, key, owner)


def schedule_due():
    for name in frappe.get_all(DT, filters={"status": "Pending", "next_attempt_at": ["<=", frappe.utils.now()]},
                               order_by="next_attempt_at asc", limit_page_length=50, pluck="name"):
        frappe.enqueue("pearnly_dms.booking.delivery.deliver", name=name, queue="long", timeout=600,
                       job_id="dms-delivery-" + name, deduplicate=True)
