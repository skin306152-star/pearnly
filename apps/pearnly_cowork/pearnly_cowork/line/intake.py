"""LINE 收单：按选好的口径进**网页录入同一条链**，识别完推一张卡，点开在网页复核、提交。

链条（用户 2026-09-26：照 ERP 的新做法）：`flow` 问完目标 ERP / 公司 / 年度 / 进项销项 / 入账方式 →
用户发 PDF 或图片 → webhook 只登记、排队（回调里同步下载，LINE 会先断开，ERP 09-25 踩过）→
后台任务从 LINE 取原件，按本人身份走网页上传同一个入口（`files.service.create`）和识别受理
（`ocr.execution.service.submit`，目标记在票上）→ 识别完（`workflow` 的 `cowork_ocr_recognized` 钩子）
每张票推一张卡（版式照 ERP `integrations/line_cards.py`，见 `cards.py`），按钮打开网页复核页。
复核、提交推送都在网页做，与网页上传的票完全一样（列表里看得到，没有另一套草稿）。

识别不成（额度不足、看不出发票、任务失败）也回一句话，请本人处理或重发。
原件与票的幂等键都是 `line:<LINE 消息号>`：LINE 重投同一条消息不会多建、多扣费。
"""
import frappe
from frappe import _
from pearnly_core.execution import state as pearnly_state

from pearnly_core import line, line_binding

from pearnly_cowork.access import scope
from pearnly_cowork.line import flow

MEDIA_SUFFIX = {"image": ".jpg", "file": ".pdf"}
_PENDING = "pearnly:cowork:line-intake:"
PENDING_SECONDS = 2 * 86400


def receive(*, line_user_id, bound, message, reply_token, choices):
    """webhook 里：只排队，不下载。调用方已把会话用户切成绑定的这个账号。"""
    message_id = message.get("id") or ""
    filename = message.get("fileName") or f"line-{message_id or 'document'}{MEDIA_SUFFIX.get(message.get('type'), '.jpg')}"
    flow.keep(line_user_id, choices)
    line.start_loading(line_user_id, 60)
    frappe.enqueue("pearnly_cowork.line.intake.store", queue="short", enqueue_after_commit=True,
                   deduplicate=True, job_id="cowork-line-intake-" + message_id,
                   line_user_id=line_user_id, user=bound, message_id=message_id, filename=filename,
                   choices={key: value for key, value in choices.items() if key != "offered"},
                   language=getattr(frappe.local, "lang", None) or "th", reply_token=reply_token)


def store(line_user_id, user, message_id, filename, choices, language, reply_token=""):
    """后台任务：取原件 → 上传 → 受理识别。换绑 / 停用时不做、不扣费。"""
    if not message_id or line_binding.user_of(line_user_id) != user:
        return
    previous, previous_lang = frappe.session.user, getattr(frappe.local, "lang", None)
    frappe.set_user(user)
    frappe.local.lang = language
    try:
        if not scope.allowed("intake", user):
            return _say(line_user_id, _("This account cannot send documents"), reply_token)
        _admit(line_user_id, message_id, filename, choices, language, reply_token)
    finally:
        frappe.set_user(previous)
        frappe.local.lang = previous_lang


def _admit(line_user_id, message_id, filename, choices, language, reply_token):
    from pearnly_core.execution.errors import Deferred, QueueFull

    from pearnly_cowork.files import service as originals
    from pearnly_cowork.ocr.execution import service as recognition

    key = "line:" + message_id
    try:
        content = line.download_message_content(message_id)
        source = originals.create(scope.default_workspace(), filename, content, key)
        frappe.db.commit()
    except frappe.ValidationError as exc:
        frappe.db.rollback()
        return _say(line_user_id, str(exc), reply_token)
    except Exception:
        frappe.db.rollback()
        frappe.log_error(title="Cowork LINE document download failed", message=frappe.get_traceback()[-1500:])
        return _say(line_user_id, _("Could not store this document"), reply_token)
    target = choices.get("target")
    push = target not in (None, flow.RECOGNIZE_ONLY) and choices.get("connection")
    # 识别完的通知按原件找回这个人（先记下再受理：识别可能比这里返回得还快）。
    _remember(source["name"], line_user_id, language, reply_token, choices)
    try:
        batch = recognition.submit(source["name"], key, direction=choices.get("direction"),
                                   posting_kind=choices.get("posting_kind"), payment=choices.get("payment"),
                                   connection=choices.get("connection") if push else None,
                                   account_set_key=choices.get("account_set_key") if push else None,
                                   target=target)
        frappe.db.commit()
    except (Deferred, QueueFull) as exc:
        frappe.db.rollback()
        _forget(source["name"])
        if isinstance(exc, Deferred) and exc.reason == "insufficient_balance":
            return _say(line_user_id, _("There is not enough recognition balance. Top up and send the document again."),
                        reply_token)
        return _say(line_user_id, _("intake.error.queue_full"), reply_token)
    except frappe.ValidationError as exc:
        frappe.db.rollback()
        _forget(source["name"])
        return _say(line_user_id, str(exc), reply_token)
    if batch.get("state") in ("saved", "not_invoice"):
        # 同一条消息重投、票早已识别好：直接回结果。
        announce(source["name"], batch.get("state"), batch.get("history_records") or [])


def _remember(source_name, line_user_id, language, reply_token, choices):
    # 卡片上要摆这次的选择（目标、方向、入账方式；账套取票上记的另起一行）：趁本人会话还在时算好一起记下。
    pearnly_state.store().set_value(_PENDING + source_name, {
        "line_user_id": line_user_id, "language": language, "reply_token": reply_token,
        "direction": choices.get("direction"), "target": flow.target_name(choices),
        "mode": flow.mode_label(choices)}, expires_in_sec=PENDING_SECONDS)


def _forget(source_name):
    pearnly_state.store().delete_value(_PENDING + source_name)


def _take(source_name):
    """只通知一次：识别任务可能因扣费确认被推迟重跑，重跑时不再发。"""
    key = _PENDING + source_name
    pending = pearnly_state.store().get_value(key)
    if pending:
        pearnly_state.store().delete_value(key)
    return pending


def _say(line_user_id, text, reply_token=""):
    line.reply_or_push(line_user_id, [{"type": "text", "text": text}], reply_token)


# ── 识别完 / 失败（识别任务里调用）──────────────────────────────────────────


def recognized(batch):
    """`cowork_ocr_recognized` 钩子：识别结果已落成票。不是从 LINE 来的原件什么都不做。"""
    import json

    records = batch.get("history_records") or "[]"
    announce(batch.source_file, batch.phase, json.loads(records) if isinstance(records, str) else records)


def ocr_failed(arguments=None, code=""):
    """`pearnly_ocr_failure_handlers`：识别任务最终失败。"""
    batch = (arguments or {}).get("batch_name")
    source = frappe.db.get_value("Cowork OCR Batch", batch, "source_file") if batch else None
    pending = _take(source) if source else None
    if pending:
        _with_language(pending, lambda: _say(pending["line_user_id"],
                       _("Could not read this document. Please send it again."), pending.get("reply_token")))


def announce(source_name, phase, records):
    pending = _take(source_name)
    if not pending:
        return
    if phase != "saved" or not records:
        return _with_language(pending, lambda: _say(pending["line_user_id"],
                              _("No invoice was found in this document."), pending.get("reply_token")))

    def send():
        from pearnly_cowork.line import cards

        messages = cards.previews(records, pending)
        # 一次回复最多 5 条（LINE）：第一批用回复令牌，其余推送（ERP 同法）。
        line.reply_or_push(pending["line_user_id"], messages[:5], pending.get("reply_token"))
        for start in range(5, len(messages), 5):
            line.push(pending["line_user_id"], messages[start:start + 5])

    _with_language(pending, send)


def _with_language(pending, send):
    previous = getattr(frappe.local, "lang", None)
    frappe.local.lang = pending.get("language") or previous
    try:
        send()
    finally:
        frappe.local.lang = previous
