"""Durable follow-up for an already verified order. Never creates an order.

Detaching this evidence from the editable conversation allows a new draft even
when LINE is unavailable. Expired/changed-identity receipts remain in Review,
never silently marked Delivered and never retried with a fresh key.

A submission whose result is still unknown is detached the same way, as an
``Unverified`` record with no ``booking_id`` (``hold``): the conversation is free
at once and ``recovery.reconcile_record`` keeps reading it back. Only ``retain``
turns it into ``Pending`` (in place, same record) once the order is verified.

回执从不等附件（2026-10-13 起，有意变化）：缺附件的单和附件齐全的单一样，当场发同一句回执；
发出那一次不补附件，之后每一轮只把缺的附件补进同一张单（补齐不发任何消息）。
补到第 ``REPAIR_LIMIT`` 次仍不行就停，先定好"单号 + 现有的附件失败提示"再发一次，
LINE 接受了才转 Review；改绑一律直接转 Review、不发消息。
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
#: 回执送达后，缺的附件最多后台补这么多次（每次一整次 DMS 登录）；再不行只告诉操作员一次。
REPAIR_LIMIT = 6
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


def _receipt_message(doc, marker):
    """回执（一条 LINE 消息）：带上开单后的逐项复核结论（`result.review`，没有就照旧）。

    有差异时登记一张差异卡（`review_fix.open_card`），卡上的三个按钮认这张登记。回执只定一次
    （定好后存在记录里、重发用同一条），所以只登记一次。
    """
    qa = marker["context"].get("qa") or {}
    answers = qa.get("answers") or {}
    result = marker.get("result") or {}
    review = result.get("review")
    record = nonce = ""
    if (review or {}).get("diffs"):
        from pearnly_dms.booking.conversation import review_fix

        record = doc.name
        nonce = review_fix.open_card(record, doc.line_user_id, doc.booking_no, doc.booking_id,
                                     review["diffs"])
    return cards.booking_receipt_message(doc.booking_no,
        str((answers.get("car") or {}).get("label") or ""),
        str(answers.get("delivery_date_be") or ""), str((qa.get("advisor") or {}).get("name") or ""),
        review=review, attach_ok=bool(result.get("attach_ok", True)), record=record, nonce=nonce)


def record(name):
    """一条回执投递记录（差异卡按钮要它的提交上下文）；没有就 `None`。"""
    return frappe.get_doc(DT, name) if name and frappe.db.exists(DT, name) else None


def endpoint_of(name):
    """这张单当初提交到的端点名（台账用）。"""
    doc = record(name)
    return ((json.loads(doc.attempt or "{}").get("context") or {}).get("endpoint") or "") if doc else ""


def sales_endpoint(name):
    """改这张单要用的端点：原提交销售的端点，DMS 身份（地址 + 账号）没变、LINE 绑定还是他；否则 `None`。

    与补附件（`_repair_files`）同一判据：只用原来那个销售的身份改原来那张单。
    """
    doc = record(name)
    if not doc:
        return None
    context = (json.loads(doc.attempt or "{}").get("context") or {})
    binding = context.get("binding") or {}
    if not binding or not matches(binding, doc.line_user_id):
        return None
    endpoint = resolve_dms_endpoint(binding.get("user_id"), context.get("endpoint"))
    if not endpoint or identity_fingerprint(endpoint) != context.get("endpoint_fingerprint"):
        return None
    return endpoint


def _attachment_notice(doc):
    """补不上时那一条：单号 + 现有的"部分附件上传失败，请在 DMS 中补充上传"（收件人的手机语言）。"""
    from pearnly_dms import i18n

    with i18n.line_language(doc.line_user_id):
        lang = i18n.line_lang()
        return (i18n.t("dms.card.TXT_BOOKING_REF", lang, booking_no=doc.booking_no) + "\n"
                + i18n.t("dms.card.qa.TXT_ATTACH_FAIL", lang))


def _follow_up_files(doc, marker, binding):
    """回执已经送达：只把缺的附件补进同一张单，绝不再发回执。"""
    if not marker.get("notice"):
        state = marker["delivery"]
        state["repairs"] = state.get("repairs", 0) + 1  # 与回执重试分开计数
        _save(doc, marker)  # Count this repair before the DMS login.
        try:
            repaired = _repair_files(doc, marker)
        except Exception:
            logger.warning("Attachment repair failed; counted towards the limit", exc_info=True)
            repaired = False
        if repaired:
            booking_ledger.log_booking(endpoint=marker["context"]["endpoint"],
                payload={"qa": marker["context"].get("qa") or {}}, result=marker["result"],
                actor=binding["user_id"])
            _save(doc, marker, status="Delivered")
            return True
        if not matches(binding, doc.line_user_id):
            _save(doc, marker, status="Review", error="binding_changed")
            return False
        if state["repairs"] < REPAIR_LIMIT:
            _save(doc, marker, error="attachments_pending")
            return False
        # 从这一刻起不再补（免得操作员手工补了系统又补一份）：先定好 key 和文字再发。
        marker["notice"] = {"key": str(uuid.uuid4()), "created_at": frappe.utils.now(),
                            "text": _attachment_notice(doc)}
        _save(doc, marker, error="attachments_failed")
    if not matches(binding, doc.line_user_id):
        _save(doc, marker, status="Review", error="binding_changed")
        return False
    notice = marker["notice"]
    if not line.push(doc.line_user_id, [{"type": "text", "text": notice["text"]}], retry_key=notice["key"]):
        _save(doc, marker, error="notice_pending")  # Same key next round, never another repair.
        return False
    _save(doc, marker, status="Review", error="attachments_failed")
    return False


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
        state = marker.get("delivery") or {}
        marker["delivery"] = {**state, "attempts": state.get("attempts", 0) + 1}
        _save(doc, marker)  # Persist next retry before any IO.
        receipt = marker.get("receipt")
        if (receipt or {}).get("accepted_at"):
            return _follow_up_files(doc, marker, binding)
        if not receipt:
            from pearnly_dms import i18n

            with i18n.line_language(doc.line_user_id):  # 回执按收件人自己的手机语言
                message = _receipt_message(doc, marker)
            receipt = {"key": str(uuid.uuid4()), "created_at": frappe.utils.now(), "message": message}
            marker["receipt"] = receipt
        _save(doc, marker)
        frappe.db.commit()
        if not matches(binding, doc.line_user_id):
            _save(doc, marker, status="Review", error="binding_changed")
            return False
        if age(receipt["created_at"]) >= WINDOW_SECONDS:
            _save(doc, marker, status="Review", error="receipt_key_expired")
            return False
        # 新记录存整条消息（可能是差异卡）；升级前定好的旧记录只有 `text`，照原样重发。
        message = receipt.get("message") or {"type": "text", "text": receipt["text"]}
        accepted = line.push(doc.line_user_id, [message], retry_key=receipt["key"])
        if accepted and not marker["result"].get("attach_ok"):
            # 回执不等附件：和正常成功同一刻、同一句送到；这一轮不补，缺的附件交给后面几轮。
            receipt["accepted_at"] = frappe.utils.now()
            _save(doc, marker, error="attachments_pending")
            return True
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
