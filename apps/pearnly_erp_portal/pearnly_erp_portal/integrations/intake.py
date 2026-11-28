"""LINE 收单：按选好的选项识别 → 一张只有「编辑」的卡 → 网页端原生采购 / 销售页提交入账。

链条（用户 2026-09-25 定稿）：`line_flow` 问完公司、供应商/客户、已付/未付、付款方式
→ 用户发 PDF/图片 → 这里留底（私有 File）并落一条内部记录 →
交共用 OCR 队列识别 → 推一张卡，**只有「编辑」**（不过期、不回执，省官方号消息额度）→
点「编辑」在 LINE 里打开网页端**原生**的采购 / 销售新建页，识别结果与选择已填好 →
点「提交」才入账过账（与手工录入同一个入账函数，收发存按现有功能关联）→ 之后再点
「编辑」打开的是那张单的查看页，不能再改。

- 不是草稿：这条内部记录网页端列表里看不到，只能从卡片进来。
- 重复单据放行，同一个文件也放行（用户 2026-09-25）；同一张卡只入一次（`line-intake:<记录名>`）。
- 每一行对哪件物料、入不入库按行预选（`line_items`，11-06）：入库行采购入库、出库行销售出库
  （认不到物料自动新建，销售允许负库存），只记账的行不动库存。
- 要人定的题（定不了的行、仓库、写账前置判定的项）在出卡前一题一题问完，卡片最后才发（`line_questions`，用户 11-19）。
"""
import hashlib
import json

import frappe
from frappe import _
from frappe.utils import add_to_date, now, now_datetime

from pearnly_core import line, line_binding
from pearnly_core.originals.rules import UPLOAD_MAX_BYTES

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.integrations import line_cards, line_flow, line_items, line_out

DOCTYPE = "ERP LINE Intake"
QUEUED, READY, FAILED, CONFIRMED = "queued", "ready", "failed", "confirmed"
CANCELLED = "cancelled"  # LINE 卡片「取消」（用户 11-19）：这一张什么都不做，记录作废、源文件删掉
OCR_KIND = "erp.line_intake"
MEDIA_TYPES = {"image": "image", "file": "pdf"}
DIRECTION_CAPABILITY = {"purchase": "purchase.write", "sales": "sales.write"}
# 同 CC 对话附件（共用层一个数）：原来 20MB，对话里收下的 20–30MB 文件交识别时被拒（11-10）；LINE 下载另有自己的上限
MAX_BYTES = UPLOAD_MAX_BYTES
# 从 LINE 取文件超过 MAX_BYTES（`_fetch` 的返回值，与取到 True / 取不到 False 分开）
TOO_LARGE = "too_large"


# ── 收件 ────────────────────────────────────────────────────────────────────


def receive(*, line_user_id, sent_by, choices, media_type, content, filename,
            line_message_id="", reply_token="") -> dict:
    """留底并排队识别。调用方已把会话用户切成绑定的这个账号。"""
    direction = choices.get("direction")
    if direction not in DIRECTION_CAPABILITY or not line_flow.ready(choices):
        frappe.throw(_("Choose a type first"))
    if sent_by != frappe.session.user or line_binding.user_of(line_user_id) != sent_by:
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    guard.require(DIRECTION_CAPABILITY[direction])
    from pearnly_erp_portal.masters import company

    company.document_company(choices.get("company"), sent_by)  # still permitted?
    # `content` 为 None：webhook 不下载，识别任务里再取（见 `_fetch`）。
    file = _store(content, filename) if content is not None else None
    doc = frappe.get_doc({
        "doctype": DOCTYPE, "status": QUEUED, "direction": direction, "owner": sent_by,
        "sent_by": sent_by, "line_user_id": line_user_id,
        # 收到时这个人手机上 LINE 的语言（webhook 已按它设好）：识别在队列里跑、可能被限流后
        # 重试，重试时不能再指望缓存或临时去问 LINE（09-26 一次重试回成了泰文）。
        "language": getattr(frappe.local, "lang", None) or channel_language(),
        "media_type": MEDIA_TYPES.get(media_type, media_type or ""),
        "line_message_id": line_message_id,
        "file_hash": hashlib.sha256(content).hexdigest() if file else None,
        "source_file": file.file_url if file else None, "source_file_name": file.name if file else None,
        "received_at": now(),
        "company": choices.get("company"), "party": choices.get("party") or "",
        "is_paid": int(bool(choices.get("is_paid"))), "payment_method": choices.get("payment_method") or "",
        "item_kind": choices.get("item_kind"), "warehouse": choices.get("warehouse") or None,
    }).insert(ignore_permissions=True)
    frappe.db.commit()
    from pearnly_core.execution.errors import Deferred, QueueFull
    from pearnly_core.execution.work import submit

    line.start_loading(line_user_id, 60)
    try:
        submit(OCR_KIND, doc.name, {"intake": doc.name, "line_user_id": line_user_id,
                                    "reply_token": reply_token, "filename": filename}, sent_by)
    except (QueueFull, Deferred):
        _drop(doc, FAILED, "queue_busy")
        line_out.reply(reply_token, [{"type": "text", "text": _(
            "The recognition queue is busy. Please send the document again shortly.")}])
    return {"name": doc.name}


def _store(content, filename):
    """私有、归本人（当前会话用户）、先不挂任何单据：识别与入账都按「本人上传的未挂文件」来认它。
    所以记录上的源文件字段是 Data 不是 Attach —— Attach 会让 Frappe 保存时把这份文件
    自动挂到收单记录上，识别就会被判「不是本人未挂出的上传」而拒绝（2026-09-26 真机）。"""
    if not content:
        frappe.throw(_("This document has no content"))
    if len(content) > MAX_BYTES:
        frappe.throw(_("This document is too large"))
    # 统一原件层（ADR-024）：站点配了就交原件层存（选了 Claude → 甲骨文、谷歌租户 → 谷歌存储），本机只留登记；存放处一时
    # 连不上抛 Deferred（识别任务按队列重试，不落本机）。原件层不收的（坏图、超页数）与没配的站点照旧存本机。
    import uuid

    from pearnly_core.originals import registered

    stored = registered.put("ERO-" + (uuid.uuid4().hex + uuid.uuid4().hex)[:48], filename, content)
    if stored is not None and not isinstance(stored, dict):
        return stored
    from pearnly_core.originals import local_file

    return frappe.get_doc({"doctype": "File", "file_name": filename, "is_private": 1,
                           "content": local_file.as_bytes(content)}).insert(ignore_permissions=True)


def _sha256(content) -> str:
    """bytes 或磁盘上的一份（CC 附件、代录资料区交来的，`local_file.LocalFile`，按块算）。"""
    from pearnly_core.originals import local_file

    return local_file.digest(content)


def _fetch(doc, filename):
    """识别任务里从 LINE 取这张图 / PDF，存成本人的私有文件，先提交 —— 识别若被限流推迟重跑，
    不会再下载一次。取不到（过期、网络、过大）就落为失败，告诉本人重发。"""
    from pearnly_core.execution.errors import Deferred

    try:
        # 与网页、CC 对话、CC 桌面版文件夹同一个上限（11-11；原来是下载函数默认的 20MB）
        content = line.download_message_content(doc.line_message_id or "", max_bytes=MAX_BYTES)
        file = _store(content, filename or f"line-{doc.line_message_id or 'document'}.jpg")
    except Deferred:
        frappe.db.rollback()
        raise  # 原件层一时连不上：识别任务按队列稍后再来（LINE 的内容还能再取），不算失败
    except line.MediaTooLarge:
        frappe.db.rollback()
        return TOO_LARGE
    except Exception:
        frappe.db.rollback()
        frappe.log_error(title="ERP LINE document download failed", message=frappe.get_traceback()[-1500:])
        return False
    # 先把这份 File 自己提交（④ #12）：下面「记到收单记录上」撞冲突要整笔回滚重来，File 若和它在同一笔里，回滚连 File 一起没了、
    # 存放处那份成了没人认的；重来的那次也不能再下载、再上传一次（外部动作不进会被整笔重来的事务）。
    frappe.db.commit()
    values = {"file_hash": hashlib.sha256(content).hexdigest(), "source_file": file.file_url, "source_file_name": file.name}
    from pearnly_core.transaction import conflicts

    try:
        kept = conflicts.retried(_keep_source, doc.name, values)
    except (conflicts.Exhausted, conflicts.RollbackFailed):
        # 用尽：刚上传的这份登记删掉（提交后才删盘上那份、排任务删存放处那份，`originals.registered.RegisteredFile`），
        # 按队列稍后再来（还没付费；LINE 的内容还能再取）
        _forget(file.name)
        raise Deferred("conflict") from None
    except Exception:
        # 别的错（校验失败、等锁超时 1205 等）：File 上面已单独提交，原样抛之前同样删掉这份登记，不留没人认的原件（11-28 ④ code-review）
        frappe.db.rollback()
        _forget(file.name)
        raise
    if not kept:
        _forget(file.name)  # 取件这段时间里这张已不在排队（取消 / 失败落定）：这份没人要了
        return NOT_QUEUED
    doc.update(values)
    return True


# `_fetch` 取到了，但这张已不在排队（取件期间被取消 / 落定）：run 照「不在排队」收尾
NOT_QUEUED = "not_queued"


def _keep_source(name, values):
    """把取到的原件记到收单记录上（`conflicts.retried` 包着，撞冲突整笔重来只重来这一段）：加锁读、仍在排队才写。"""
    doc = frappe.get_doc(DOCTYPE, name, for_update=True)
    if doc.status != QUEUED:
        return False
    doc.update(values)
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return True


def _forget(file_name):
    """删掉一份没记上的原件登记并提交；删不掉只记日志（不挡收尾）。"""
    try:
        if frappe.db.exists("File", file_name):
            frappe.delete_doc("File", file_name, ignore_permissions=True, force=True)
        frappe.db.commit()
    except Exception:  # noqa: BLE001 — 收尾删不掉不改变结论；留日志查
        frappe.db.rollback()
        frappe.log_error(title="ERP LINE document cleanup failed", message=frappe.get_traceback()[-1500:])


# 同一文件拆出的另一张（:func:`_sibling`）：哪些栏随第一条、哪些每条自己的。收单记录每一栏都要归到其中一类
# （`test_cc_intake.Siblings` 按 DocType 定义核，新加栏没归类就红；11-10 拆出的记录漏了来源与对话编号，只看到第 1 张）
SIBLING_SHARED = ("direction", "direction_guessed", "sent_by", "line_user_id", "language", "media_type", "line_message_id",
                  "file_hash", "company", "party", "is_paid", "payment_method", "item_kind", "warehouse", "source",
                  "session_id", "received_at")
SIBLING_OWN = ("status", "request_key", "source_file", "source_file_name", "result", "line_choices", "decided_at", "purchase",
               "sales_document", "discard_reason", "note", "error")


def _sibling(doc, result):
    """同一文件里的另一张单（多页 / 一张图几张票）：同样的选择，自己一份源文件（内容相同；入账时
    各挂各的，不抢同一个 File），自己一张卡、自己入账。来源与对话编号随第一条（CC 对话来的拆出来仍是 CC 的，对话资料列表
    `chat_records` 只认 cc；A 线 11-10 实验栈：拆出的 23 条落成默认的 LINE、对话里只看到第 1 张）；请求键只归第一条（同一附件
    再交回原来那条，`receive_chat`）。"""
    from pearnly_core.originals import registered

    file = registered.copy(_file_name(doc))  # 原件在原件层的复制登记（同一份原件，不再存一次）
    return frappe.get_doc({"doctype": DOCTYPE, "status": READY, "owner": doc.sent_by,
                           **{key: doc.get(key) for key in SIBLING_SHARED},
                           "source_file": file.file_url, "source_file_name": file.name,
                           "result": json.dumps(result, ensure_ascii=False, default=str)}).insert(ignore_permissions=True)


def _file_name(doc):
    """这张单自己那份 File。按记录编号取，不按地址找：同一张照片再发一次时，Frappe 会让新旧
    两份 File 共用同一个文件地址，按地址找可能拿到另一张单的那份。"""
    return doc.get("source_file_name") or ""


def _drop(doc, status, reason=""):
    """识别没成：状态落定，源文件删掉（不留件）。"""
    name = _file_name(doc)
    if name and not frappe.db.get_value("File", name, "attached_to_name"):
        frappe.delete_doc("File", name, ignore_permissions=True, force=True)
    frappe.db.set_value(DOCTYPE, doc.name, {"status": status, "decided_at": now(),
                                            "error": reason[:500] or None})
    frappe.db.commit()


# ── 识别（共用 OCR 队列里跑）──────────────────────────────────────────────────


def _recogniser(direction):
    if direction == "purchase":
        from pearnly_erp_portal.purchasing import recognition
    else:
        from pearnly_erp_portal.sales import recognition
    return recognition


# 识别结果的检查点（执行器 `execution.work.checkpoint`，按行存、自己提交）：记结果用尽、进程中途没了，重跑从这里取回，不再付费识别。
# 状态用产品自己的「terminal」（同 Cowork `cowork.terminal:`）：不是一次付费调用，不算进调用计数（`done_calls` 只数 done）。
RESULT_CHECKPOINT = "erp.line_intake.result"
RESULT_STATE = "terminal"


def _recognised(doc):
    """LINE 这张的识别结果（扣费参考号固定 `erp.line_intake:<名>`），经 :func:`_checkpointed`。"""
    return _checkpointed(lambda: _recogniser(doc.direction).recognise(
        [{"file": _file_name(doc), "kind": "bill"}], billing_reference="erp.line_intake:" + doc.name,
        company=doc.company, prefill=False))


def _checkpointed(read):
    """本次任务已有结果检查点就取回；没有才调 ``read()`` 付费识别，成功先写检查点（自己提交）。LINE 与 CC 对话收单共用。"""
    from pearnly_core.execution import work

    saved = work.checkpoint(RESULT_CHECKPOINT)
    if saved and saved.get("state") == RESULT_STATE and isinstance(saved.get("body"), dict):
        return dict(saved["body"])
    result = read()
    work.checkpoint(RESULT_CHECKPOINT, RESULT_STATE, json.loads(json.dumps(result, ensure_ascii=False, default=str)))
    return result


def _keep_result(name, result, extras, line_user_id=None):
    """记结果（`conflicts.retried` 包着，撞冲突整笔重来只重来这一段，不再调识别）：加锁读这张，仍在排队才写成待提交、拆出同一文件里
    的其余几张、每行预选，然后提交。已不在排队回 ``None``（不改回）。CC 对话收单（:func:`run_chat`）同用，没有 LINE 用户。"""
    doc = frappe.get_doc(DOCTYPE, name, for_update=True)
    if doc.status != QUEUED:
        return None
    doc.result = json.dumps(result, ensure_ascii=False, default=str)
    doc.status = READY
    doc.save(ignore_permissions=True)
    cards = [(doc, result)] + [(_sibling(doc, extra), extra) for extra in extras]
    for card, data in cards:
        # 每一行预选（按收单前选的对方，`line_items.prepare`）；定不了的在出卡前问
        line_items.prepare(card, data, line_user_id)
    frappe.db.commit()
    return cards


def run(intake, line_user_id, reply_token="", filename=""):
    """登记在 `pearnly_ocr_handlers` 的识别入口。换绑 / 停用时不识别、不扣费。

    外部动作（LINE 取件、原件上传、付费识别、发 LINE）都不进会被整笔重来的事务（④ #12）：取件后先把 File 提交，记到记录上那段
    （`_keep_source`）撞冲突只重来那一段；识别结果先写进执行器检查点（自己提交），再进「记结果」（`_keep_result`，加锁读、仍在排队
    才写，撞冲突只重来这一段、不再调识别）；用尽按队列稍后再来，重跑从检查点取回结果、不再付费。发 LINE 在记结果提交之后。"""
    # 加锁读（④ #12）：关快照隔离后普通读可能停在更早的读视图，取消 / 落定了还当成在排队
    doc = frappe.get_doc(DOCTYPE, intake, for_update=True)
    if doc.status != QUEUED:
        frappe.db.commit()
        return {"cancelled": "not_queued"}
    if line_binding.user_of(line_user_id) != doc.sent_by or doc.line_user_id != line_user_id:
        _drop(doc, FAILED, "binding_changed")
        return {"cancelled": "binding_changed"}
    frappe.db.commit()  # 放掉行锁：下面去 LINE 取件、交识别，不攥着这一行
    previous, previous_lang = frappe.session.user, getattr(frappe.local, "lang", None)
    frappe.set_user(doc.sent_by)
    # 卡片与 webhook 回复同一口径：用这个人手机上 LINE 的语言（用户 2026-09-26），
    # 不随这个账号在网页端的界面语言走 —— 否则同一段对话里前面泰文、卡片突然变中文。
    frappe.local.lang = doc.get("language") or line.user_language(line_user_id, default=channel_language())
    try:
        fetched = _file_name(doc) or _fetch(doc, filename)
        if fetched == NOT_QUEUED:
            return {"cancelled": "not_queued"}
        if not fetched or fetched == TOO_LARGE:
            from pearnly_core.originals import upload_limits

            code = "too_large" if fetched == TOO_LARGE else "download_failed"
            text = upload_limits.too_large() if fetched == TOO_LARGE else _("Could not store this document")
            _drop(doc, FAILED, code)
            line_out.reply_or_push(line_user_id, [{"type": "text", "text": text}], reply_token)
            return {"failed": code, "notified": True}
        try:
            result = _recognised(doc)
        except frappe.ValidationError as exc:
            from pearnly_erp_portal.recognition import billing, policy

            _drop(doc, FAILED, str(exc))
            # 余额不足、不能按本单录入（不是单据 / 贷项通知单）要原话告诉本人；其余识别失败统一请他重发。
            text = str(exc) if isinstance(exc, (billing.InsufficientBalance, policy.UnsupportedDocument)) else _(
                "Could not read this document. Please send it again.")
            line_out.reply_or_push(line_user_id, [{"type": "text", "text": text}], reply_token)
            return {"failed": "recognition_failed", "notified": True}
        extras = result.pop("additional", None) or []
        from pearnly_core.execution.errors import Deferred
        from pearnly_core.transaction import conflicts

        try:
            cards = conflicts.retried(_keep_result, doc.name, result, extras, line_user_id)
        except (conflicts.Exhausted, conflicts.RollbackFailed):
            # 用尽：结果在检查点里，按队列稍后再来，重跑直接取回记上（不再识别、不再扣费）
            raise Deferred("conflict") from None
        if cards is None:
            return {"cancelled": "not_queued"}  # 识别期间这张已不在排队（取消 / 落定）：不改回、不出卡
        from pearnly_erp_portal.integrations import line_questions

        try:
            # 用户 11-19「所有问题问完 … 一气呵成」：每张要人定的题（物料、仓库、写账前置判定）问完才出卡，逐张；
            # 正在问别的就排在后面（回「排队中」，不出卡）
            messages = line_questions.begin(line_user_id, [card.name for card, _data in cards])
        except Exception:
            frappe.db.rollback()
            frappe.clear_messages()
            frappe.log_error(title="ERP LINE item question failed", message=frappe.get_traceback()[-1500:])
            messages = None  # 提问出错不挡卡片：照出卡（卡片「保存」按判定兜底）
        if messages is None:
            # 会话已过期（或已换成收款）：问不了，照旧直接出卡
            messages = [message for card, data in cards for message in line_questions.cards_of(card, data)]
        # 一次回复最多 5 条（LINE）：第一批用回复令牌，其余推送。
        line_questions.send(line_user_id, messages, reply_token)
        return {}
    finally:
        frappe.set_user(previous)
        frappe.local.lang = previous_lang


def channel_language():
    return frappe.db.get_single_value("System Settings", "language") or "th"


def ocr_failed(arguments, code):
    """队列本身失败（排队超时、结果不确定）：告诉本人，记录落定。"""
    doc = frappe.db.get_value(DOCTYPE, arguments.get("intake"), ["name", "status", "source_file_name", "language"],
                              as_dict=True)
    if not doc or doc.status != QUEUED:
        return
    _drop(doc, FAILED, code)
    frappe.local.lang = doc.language or line.user_language(arguments.get("line_user_id"), default=channel_language())
    text = (_("The recognition result needs checking. Please do not resend this document yet.")
            if code == "provider_outcome_unknown" else _("Could not read this document. Please send it again."))
    line_out.push(arguments.get("line_user_id"), [{"type": "text", "text": text}])


# ── 预览卡 → 入账 ────────────────────────────────────────────────────────────


def _fallback_name(direction):
    """「一般供应商 / 散客」落进单据和主档的名字用官方号语言（站点系统语言），不随打开网页的
    那个人的界面语言变 —— 否则同一个兜底供应商会按不同语言各建一个（09-26 实测记成了中文「散客」）。"""
    return _(line_flow.FALLBACK_LABELS[direction], lang=channel_language())


def build_payload(doc, result=None) -> dict:
    """把「选好的主档 + 识别字段」拼成网页端入账函数要的入参（同一个形状）。"""
    result = result if result is not None else json.loads(doc.result or "{}")
    # 识别出口已规整成纯数字（`purchasing.recognition.tidy_numbers`、`sales.contracts.map_recognised`）；规整之前存下的记录
    # 在这里再过一次（主会话 11-19：「1,234.50」入账报值无效、「฿100」静默变 0）
    result = _tidy_numbers(doc.direction, result)
    chosen = line_items.choices_of(doc)  # 每行的选择（改版前识别的票没有：照旧按整批 `item_kind`）
    goods = doc.item_kind == "goods"
    attachments = [{"file": _file_name(doc), "kind": "bill"}]
    party = doc.party if doc.party not in ("", None, line_flow.FROM_DOCUMENT, line_flow.FALLBACK) else None
    fallback = doc.party == line_flow.FALLBACK
    if doc.direction == "purchase":
        from pearnly_erp_portal.purchasing import recognition

        payload = {key: result[key] for key in recognition.HEADER_FIELDS
                   if result.get(key) not in (None, "")}
        if payload.get("due_date"):
            # 修正前识别的卡片，到期日可能还是票面原样（「19/05/69」），网页日期框会报格式错误。
            from pearnly_erp_portal.recognition import dates

            payload["due_date"] = dates.due(payload["due_date"], payload.get("doc_date"))
            if not payload["due_date"]:
                payload.pop("due_date")
        if chosen:
            lines = _chosen_lines(result, chosen)
            stock = _place(chosen, lines)
        else:
            lines = []
            for row in result.get("lines") or []:
                # 改版前识别的票：识别补的「不可抵扣的增值税」行永远是服务，不按 LINE 里选的「库存商品」入库。
                moved = goods and not row.get(recognition.NON_CLAIMABLE_VAT)
                row = {**row, "item_type": "goods" if moved else "service"}
                if moved and doc.warehouse:
                    row["warehouse"] = doc.warehouse
                lines.append(row)
            stock = {"update_stock": int(goods), "auto_create_product": int(goods)}
        payload.update(company=doc.company, lines=lines, is_paid=doc.is_paid,
                       payment_method=doc.payment_method or "cash", source="line", attachments=attachments, **stock,
                       **_recogniser(doc.direction).marks(result))  # 票面合计与识别标记（写账前核对，`express.contracts.printed_hold`）
        if party:
            payload["supplier"] = party
        elif fallback:
            # 一般供应商：票面上的供应商信息不用，统一记到同名的那一个（第一次按名字新建，之后复用）。
            for key in ("supplier_tax_id", "supplier_branch_type", "supplier_branch_no",
                        "supplier_branch_name", "supplier_address"):
                payload.pop(key, None)
            payload["supplier_name"] = _fallback_name("purchase")
        return payload
    payload = {key: result.get(key) for key in ("doc_kind", "doc_date", "buyer_name", "party_type",
                                                  "address", "has_vat", "vat_rate", "price_includes_vat",
                                                  "header_discount")}
    payload = {key: value for key, value in payload.items() if value not in (None, "")}
    lines = []
    for row in result.get("lines") or []:
        # 百分比折扣（discount_pct）一起带上（主会话 11-19：识别出的百分比折扣拼单据时被丢掉）
        row = {key: row.get(key) for key in ("product", "description", "qty", "unit", "unit_price",
                                             "discount", "discount_pct") if row.get(key) not in (None, "")}
        if not chosen:
            # 改版前识别的票：整批「库存商品」才出库（以前名字刚好对上已有物料的行也照样出库，现在按行）
            row["item_type"] = "goods" if goods else "service"
            if goods and doc.warehouse:
                row["warehouse"] = doc.warehouse
        lines.append(row)
    if chosen:
        lines = _chosen_lines({"lines": lines}, chosen)
    payload.update(company=doc.company, lines=lines, is_paid=doc.is_paid,
                   payment_method=doc.payment_method or None, source="line", attachments=attachments,
                   **(_place(chosen, lines) if chosen else {}), **_recogniser(doc.direction).marks(result))
    if not doc.is_paid:
        payload["paid_amount"] = 0
    elif payload.get("doc_date"):
        # 定·09-25：已付的付款日期取单据日期（网页端表单默认是今天，这里预先填好）。
        payload["payment_date"] = payload["doc_date"]
    if party:
        payload["customer"] = party
    elif fallback:
        # 散客：不建客户档案，不用票面买方信息。
        payload.pop("address", None)
        payload.update(buyer_name=_fallback_name("sales"), party_type="anonymous")
    else:
        if not payload.get("buyer_name"):
            payload["buyer_name"] = _("Unknown buyer")
        # 票面读出的买方税号 / 分店：按它预选客户（同网页识别 `sales.recognition._customer`），提交时再按它认或记进新客户
        from pearnly_erp_portal.sales import contracts as sales_contracts
        from pearnly_erp_portal.sales import recognition as sales_recognition

        tax = sales_contracts.buyer_tax(result.get("buyer_tax_id"))
        if tax:
            payload.update(buyer_tax_id=tax, buyer_branch_no=result.get("buyer_branch_no") or "")
            customer = sales_recognition._customer(result, doc.company)
            if customer:
                payload["customer"] = customer
    return payload


def _tidy_numbers(direction, result):
    """一份识别结果的数字规整成纯数字（共用 `amounts.tidy`，与识别出口同一份字段）：金额读不懂原样留着让入账报错，
    税率读不懂去掉按默认算。"""
    if not isinstance(result, dict):
        return result
    if direction == "purchase":
        from pearnly_erp_portal.purchasing import recognition

        return recognition.tidy_numbers(result)
    from pearnly_core.tax_documents import amounts

    out = amounts.tidy(result, ("header_discount",), ("vat_rate",))
    if isinstance(out.get("lines"), list):
        out["lines"] = [amounts.tidy(row, ("qty", "unit_price", "discount"), ("discount_pct",)) if isinstance(row, dict) else row
                        for row in out["lines"]]
    return out


def _chosen_lines(result, chosen):
    """识别的行 + 这张票上每行的选择（`line_items`）：入库与否、物料、新建。"""
    lines = []
    for index, row in enumerate(result.get("lines") or []):
        choice = chosen["lines"][index] if index < len(chosen["lines"]) else None
        row = dict(row)
        if choice:
            row["item_type"] = choice.get("item_type") or "goods"
            if choice.get("product") and not row.get("product"):
                row["product"] = choice["product"]
            if choice.get("new") and not row.get("product"):
                row["new_item"] = 1
        lines.append(row)
    return lines


def _place(chosen, lines):
    """LINE 问的那一个仓（一张票问一次，各行同仓；要逐行不同仓在编辑页改）：有入库行才给，作各行的默认仓。"""
    if chosen.get("warehouse") and any(row.get("item_type") == "goods" for row in lines):
        return {"warehouse": chosen["warehouse"]}
    return {}


def totals(doc, result=None) -> dict:
    """卡上显示的金额用网页端同一套算法，算不出来（字段缺）就不显示金额。"""
    try:
        payload = build_payload(doc, result)
        if doc.direction == "purchase":
            from pearnly_erp_portal.purchasing import entry
        else:
            from pearnly_erp_portal.sales import entry
        return entry.preview(payload)
    except Exception:
        frappe.clear_messages()
        return {}


# ── 网页端：从卡片打开 → 提交 ────────────────────────────────────────────────


def _owned(name, *, lock=False):
    """只有发这张单据的本人能打开 / 提交（服务端判，不靠页面）。``lock``：先加锁、再做任何普通读（11-28 B ②）——关快照隔离后
    先普通读（exists）再锁，后面的 get_doc 读到的是旧副本，状态判断会按旧的走。"""
    if lock:
        # 加锁读整份：调用方（CC 对话、LINE 回调）的读视图可能早于别人刚提交的那次，加锁读不走读视图，状态是最新的
        if not name or not frappe.db.get_value(DOCTYPE, name, "name", for_update=True):
            frappe.throw(_("This document is no longer available"), frappe.DoesNotExistError)
        doc = frappe.get_doc(DOCTYPE, name, for_update=True)
    else:
        if not name or not frappe.db.exists(DOCTYPE, name):
            frappe.throw(_("This document is no longer available"), frappe.DoesNotExistError)
        doc = frappe.get_doc(DOCTYPE, name)
    if doc.sent_by != frappe.session.user:
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    guard.require(DIRECTION_CAPABILITY[doc.direction])
    return doc


def open_for_edit(name) -> dict:
    """卡片「编辑」打开的数据：已入账就给单号（页面转去查看页），否则给预填好的表单数据。"""
    doc = _owned(name)
    recorded = doc.purchase if doc.direction == "purchase" else doc.sales_document
    from pearnly_erp_portal.masters import company

    if doc.status == CONFIRMED and recorded:
        return {"direction": doc.direction, "recorded": recorded, "company": doc.company,
                "company_name": frappe.db.get_value("ERP Company", doc.company, "company_name") or doc.company}
    if doc.status != READY:
        frappe.throw(_("This document is no longer available"), frappe.DoesNotExistError)

    # 这张单在 LINE 里选的是哪家公司，页面就按哪家打开（仓库、商品选择都跟这家走）：只在这一页（页面级当前公司
    # `company.page_company`），不改本人网页的当前公司（线上 6，11-15：原来写用户默认值，点完「编辑」网页别处也换了公司）。
    if doc.company not in company.allowed_companies():
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    from pearnly_erp_portal.recognition import background

    # 网页编辑页：读不懂的数空着、原生必填拦着，不显示成 0（主会话 11-19；`recognition.background.web_numbers`）
    form = background.web_numbers(doc.direction, build_payload(doc))
    file = frappe.db.get_value("File", _file_name(doc), ["name", "file_name", "file_url"], as_dict=True)
    from pearnly_core.originals import registered

    if file and registered.NAME.fullmatch(file.file_name or ""):
        file.file_name = registered.filename(file.name)  # 原件在原件层：给页面原文件名
    form["attachments"] = [{"file": file.name, "file_name": file.file_name, "file_url": file.file_url,
                            "kind": "bill", "intake": True}] if file else []
    if doc.direction == "purchase" and form.get("supplier"):
        form["supplier_name"] = frappe.db.get_value("ERP Supplier", form["supplier"], "supplier_name")
    if doc.direction == "sales" and form.get("customer"):
        form["buyer_name"] = frappe.db.get_value("ERP Customer", form["customer"], "customer_name")
    form.pop("company", None)
    form.pop("ocr_review", None)  # 识别标记提交时由服务端按这张卡补上（:func:`submit`）
    # 识别检查没过的原因不给页面（用户 2026-10-02：录入侧不出识别后的错误提示，只靠识别质量）。
    return {"direction": doc.direction, "intake": doc.name, "doc": form, "company": doc.company,
            "company_name": frappe.db.get_value("ERP Company", doc.company, "company_name") or doc.company}


def submit(name, payload) -> dict:
    """页面点「提交」：用页面上的内容入账（公司与源文件以这张卡为准），不回 LINE 回执。"""
    doc = _owned(name, lock=True)
    recorded = doc.purchase if doc.direction == "purchase" else doc.sales_document
    if doc.status == CONFIRMED and recorded:
        return {"name": recorded, "duplicate": True}
    if doc.status != READY:
        frappe.throw(_("This document is no longer available"), frappe.DoesNotExistError)
    payload = dict(payload or {})
    payload["company"] = doc.company
    payload["source"] = "line"
    # 识别标记按这张卡的识别结果补上（页面不带）；票面合计用页面上的（人可以改）
    payload["ocr_review"] = _recogniser(doc.direction).marks(json.loads(doc.result or "{}"))["ocr_review"]
    source = {"file": _file_name(doc), "kind": "bill"}
    others = [row for row in payload.get("attachments") or [] if row.get("file") != source["file"]]
    payload["attachments"] = [source, *others]

    def finish(created):
        link = "purchase" if doc.direction == "purchase" else "sales_document"
        frappe.db.set_value(DOCTYPE, doc.name, {link: created["name"], "status": CONFIRMED,
                                                "decided_at": now()})

    if doc.direction == "purchase":
        from pearnly_erp_portal.purchasing import entry
    else:
        from pearnly_erp_portal.sales import entry
    return entry.post(payload, request_id="line-intake:" + doc.name, finalize=finish, allow_duplicate=True)


EXPRESS_DOCTYPES = {"purchase": "Purchase Document", "sales": "ERP Sales Document"}


def save_card(name) -> dict:
    """LINE 卡片「保存」（用户 11-18）：不进编辑页，按卡片上的选择与识别字段（``build_payload``，同 CC 收单）走编辑页同一个
    提交（:func:`submit`，请求号同一个：编辑页提交过的再点只回原单）。提交前先按 LINE 里出题的同一份写账前置判定核一遍
    （:func:`unanswered`，11-19）：还有没答的题（旧卡、LINE 里问不了的题）不提交；再同编辑页一样按写账规则判一遍
    （``express.api.preflight``，只对接着 Express 的公司）。有要人定的项就不提交，回 ``{"needed": [...]}``，由卡片一侧给「编辑」；
    其余缺项照提交时原样报错。"""
    doc = _owned(name)
    recorded = doc.purchase if doc.direction == "purchase" else doc.sales_document
    if doc.status == CONFIRMED and recorded:
        return {"name": recorded, "duplicate": True}
    if doc.status == CANCELLED:
        return {"cancelled": True}  # 点过「取消」：保存什么都不做
    if doc.status != READY:
        frappe.throw(_("This document is no longer available"), frappe.DoesNotExistError)
    found = judged(doc)
    needed = unanswered(doc, found)
    if needed:
        return {"needed": needed}
    stop = stopped_reason(doc, found)
    if stop:
        frappe.throw(stop)  # 账套里没有合格的存货科目组：不入账，回原因 + 「编辑」（旧卡）
    payload = build_payload(doc)
    from pearnly_erp_portal.express import api as express_api, push as express_push

    if express_push.linked(doc.company):
        needed = express_api.preflight(EXPRESS_DOCTYPES[doc.direction], payload).get("needed") or []
        if needed:
            return {"needed": needed}
    payload.pop("ocr_review", None)  # 由 :func:`submit` 按这张卡补上
    return submit(name, payload)


def judged(doc) -> dict:
    """这张此刻的写账前置判定（同 LINE 出卡前问的那一份，`line_questions`：``I:<记录>``，共用层合成的卡片）。"""
    from pearnly_core.write_precheck import api as precheck_api
    from pearnly_erp_portal.express import precheck

    return precheck_api.card(doc.company, [f"{precheck.INTAKE_PREFIX}:{doc.name}"])


def unanswered(doc, found=None) -> list:
    """这张此刻写账前置判定里还没定的项 → ``[{kind, label}]``。推荐值没点过的同样算没定（同 CC 引导卡：卡上没点过的预选不生效）；
    条件不成立的不算。"""
    from pearnly_core.write_precheck import contracts as card, guide

    items = card.items_of(found if found is not None else judged(doc))
    by_key = {item["key"]: item for item in items}
    return [{"kind": item["kind"], "label": item.get("label") or ""} for item in items
            if guide.tier_of(item) != guide.DECIDED and card.applies(item, by_key, [])]


# 判定判出写不进 Express、又没有可选的（只能会计在 Express 里改）：出卡前说一句事实、卡上不放「保存」（11-19）。
# 原因 → 现有原因词条：账套里没有合格的存货科目组（同 CC 桌面版写账停下时的那句）
STOP_TEXTS = {"stock_acc_group_missing": "express.error.STOCK_ACC_GROUP_UNFIT"}  # = stock_acc_group.REASON_ACC_GROUP_MISSING


def stopped_reason(doc, found=None) -> str:
    """这张写不进 Express、人在 LINE / 网页里也选不了的那句原因（:data:`STOP_TEXTS`）；没有回空（照常可保存）。"""
    found = found if found is not None else judged(doc)
    for row in found.get("not_writable") or ():
        key = STOP_TEXTS.get(str(row.get("reason") or ""))
        if key:
            return _(key)
    return ""


def cancel_card(name) -> dict:
    """LINE 卡片「取消」（用户 11-19）：这一张什么都不做——不提交、不保存、不写账本；记录落为已取消、源文件删掉（`_drop`），
    编辑页与「保存」都不再认它。已入账的不动（回原单号）；重复点只回已取消。"""
    doc = _owned(name, lock=True)
    recorded = doc.purchase if doc.direction == "purchase" else doc.sales_document
    if doc.status == CONFIRMED and recorded:
        return {"name": recorded, "duplicate": True}
    if doc.status == READY:
        _drop(doc, CANCELLED)
    elif doc.status != CANCELLED:
        frappe.throw(_("This document is no longer available"), frappe.DoesNotExistError)
    return {"cancelled": True}


# ── CC 对话收单（盘点 2026-11-08 P0-2，ADR-027 段 2）────────────────────────
#
# CC 对话（直写、代录）里的附件走 LINE 收单同一条链路：留底 → 一条收单记录（来源 cc）→ 共用识别队列 → 每张票一条待提交记录
# （一个文件几张票各一条，同 LINE 卡片）→ 用户说「录入 / 入账 / 提交」才用网页同一个入账函数提交。只说「识别 / 发内容」时不提交。
# 识别、计费、按行预选物料都是 LINE 那一份；不回 LINE、不推卡片。

SOURCE_LINE, SOURCE_CC = "line", "cc"
CC_OCR_KIND = "erp.cc_intake"


def receive_chat(*, company, direction, filename, content, request_key, session_id="") -> dict:
    """CC 对话里的一个附件交识别。同一个人同一个请求键（对话附件编号）回原来那条：不重复识别、不重复收费；上次失败的才重来。
    ``direction`` 空（代录放文件、用户没说采购还是销售）：先按采购读，票上卖方是本公司再按销售读（`run_chat`；主会话 11-08 定案 ②）。"""
    guessed = False
    if not direction:
        allowed = [kind for kind, capability in DIRECTION_CAPABILITY.items() if guard.has(capability)]
        if not allowed:
            frappe.throw(_("Not permitted"), frappe.PermissionError)
        direction, guessed = allowed[0], len(allowed) > 1
    if direction not in DIRECTION_CAPABILITY:
        frappe.throw(_("Choose a type first"))
    guard.require(DIRECTION_CAPABILITY[direction])
    from pearnly_erp_portal.masters import company as companies

    company = companies.document_company(company)
    user = frappe.session.user
    key = str(request_key or "")[:140]
    found = frappe.db.get_value(DOCTYPE, {"source": SOURCE_CC, "sent_by": user, "request_key": key},
                                ["name", "status"], as_dict=True, order_by="creation desc") if key else None
    if found and found.status != FAILED:
        return {"name": found.name, "status": found.status, "reused": True}
    file = _store(content, filename)
    doc = frappe.get_doc({
        "doctype": DOCTYPE, "status": QUEUED, "direction": direction, "owner": user, "sent_by": user,
        "source": SOURCE_CC, "session_id": str(session_id or "")[:140], "request_key": key,
        "direction_guessed": int(guessed),
        "language": getattr(frappe.local, "lang", None) or channel_language(),
        "media_type": "pdf" if str(filename or "").lower().endswith(".pdf") else "image",
        "file_hash": _sha256(content), "source_file": file.file_url, "source_file_name": file.name,
        "received_at": now(), "company": company, "party": line_flow.FROM_DOCUMENT,
        # 网页采购 / 销售默认「已付、现金」（`api_purchase_defaults` / `api_sales_defaults`）；要改的在开始前卡片里改
        "is_paid": 1, "payment_method": "cash",
    }).insert(ignore_permissions=True)
    frappe.db.commit()
    from pearnly_core.execution.errors import Deferred, QueueFull
    from pearnly_core.execution.work import submit

    try:
        submit(CC_OCR_KIND, doc.name, {"intake": doc.name}, user)
    except (QueueFull, Deferred):
        _drop(doc, FAILED, "queue_busy")
        return {"name": doc.name, "status": FAILED, "reason": "queue_busy"}
    return {"name": doc.name, "status": QUEUED}


def run_chat(intake):
    """登记在 `pearnly_ocr_handlers`（``erp.cc_intake``）的识别入口：以发起人身份识别，几张票各落一条待提交记录，每行预选物料。
    识别失败（不是单据、余额不足、贷项通知单……）落定失败、原因记在记录上，对话里由 CC 照原话说。"""
    # 同 LINE（④ #12，:func:`run`）：开头加锁读判状态；识别结果先进检查点，记结果（每行预选：同 LINE，定不了的留到开始前卡片问）
    # 撞冲突只重来那一段、不再调识别；用尽按队列稍后再来，重跑从检查点取回。原件在收下时已存好（`receive_chat`），这里不取件。
    doc = frappe.get_doc(DOCTYPE, intake, for_update=True)
    frappe.db.commit()  # 放锁：下面交识别不攥着这一行
    if doc.status != QUEUED or doc.get("source") != SOURCE_CC:
        return {"cancelled": "not_queued"}
    previous, previous_lang = frappe.session.user, getattr(frappe.local, "lang", None)
    frappe.set_user(doc.sent_by)
    frappe.local.lang = doc.get("language") or previous_lang
    try:
        try:
            result = _checkpointed(lambda: _read_chat(doc))
        except frappe.ValidationError as exc:
            _drop(doc, FAILED, str(exc))
            return {"failed": "recognition_failed"}
        extras = result.pop("additional", None) or []
        from pearnly_core.execution.errors import Deferred
        from pearnly_core.transaction import conflicts

        try:
            records = conflicts.retried(_keep_result, doc.name, result, extras)
        except (conflicts.Exhausted, conflicts.RollbackFailed):
            raise Deferred("conflict") from None  # 结果在检查点里：重跑直接取回记上，不再识别、不再扣费
        if records is None:
            return {"cancelled": "not_queued"}  # 识别期间这条已不在排队：不改回
        return {"records": [record.name for record, _data in records]}
    finally:
        frappe.set_user(previous)
        frappe.local.lang = previous_lang


# 按采购读出来是本公司开出的票（`purchasing.recognition` 拒收的那句）：方向是猜的就改按销售再读一次（这一次读的费用在我们，
# 采购那遍拒收前不扣费，租户只按销售这遍收一次）
ISSUED_HERE = "This document was issued by this company, not a purchase document"


def _read_chat(doc):
    files = [{"file": _file_name(doc), "kind": "bill"}]
    try:
        return _recogniser(doc.direction).recognise(files, billing_reference=CC_OCR_KIND + ":" + doc.name,
                                                    company=doc.company, prefill=False)
    except frappe.ValidationError as exc:
        if not (doc.get("direction_guessed") and doc.direction == "purchase" and str(exc) == _(ISSUED_HERE)
                and guard.has(DIRECTION_CAPABILITY["sales"])):
            raise
    frappe.db.rollback()
    frappe.clear_messages()
    frappe.db.set_value(DOCTYPE, doc.name, {"direction": "sales", "direction_guessed": 0})
    # 方向改定先提交（④ #12）：记结果撞冲突整笔重来时不能把它一起回滚掉（重跑从检查点取回结果、不再读这一遍）
    frappe.db.commit()
    doc.direction = "sales"
    return _recogniser("sales").recognise(files, billing_reference=CC_OCR_KIND + ":" + doc.name, company=doc.company,
                                          prefill=False)


def chat_failed(arguments, code):
    """队列本身失败（排队超时、结果不确定）：记录落定失败，原因记上（对话里由 CC 说）。"""
    doc = frappe.db.get_value(DOCTYPE, arguments.get("intake"), ["name", "status", "source_file_name"], as_dict=True)
    if doc and doc.status == QUEUED:
        _drop(doc, FAILED, code)


def chat_records(names) -> list:
    """本人在对话里交的收单记录此刻怎样（同一文件拆出的几张一起列）：状态、识别出的单头与合计（网页同一套算法）、提交后的单据。
    别人的、不是对话来的不列。"""
    user = frappe.session.user
    wanted = [str(name) for name in names or () if name][:50]
    rows = frappe.get_all(DOCTYPE, filters={"name": ["in", wanted or [""]], "sent_by": user, "source": SOURCE_CC},
                          pluck="name", limit_page_length=50)
    hashes = {frappe.db.get_value(DOCTYPE, name, "file_hash") for name in rows}
    siblings = frappe.get_all(DOCTYPE, filters={"file_hash": ["in", [h for h in hashes if h] or [""]], "sent_by": user,
                                                "source": SOURCE_CC, "name": ["not in", rows or [""]]},
                              pluck="name", limit_page_length=200) if hashes else []
    out = []
    for name in rows + siblings:
        doc = frappe.get_doc(DOCTYPE, name)
        result = json.loads(doc.result or "{}")
        item = {"record": doc.name, "status": doc.status, "direction": doc.direction, "company": doc.company,
                "file_hash": doc.file_hash or ""}
        if doc.status == FAILED:
            item["reason"] = doc.error or ""
        if doc.status in (READY, CONFIRMED):
            party = result.get("supplier_name") if doc.direction == "purchase" else result.get("buyer_name")
            item.update(doc_no=result.get("doc_no"), doc_date=result.get("doc_date"), party=party,
                        party_tax_id=result.get("supplier_tax_id") if doc.direction == "purchase" else result.get("buyer_tax_id"),
                        lines=[{key: row.get(key) for key in ("description", "qty", "unit", "unit_price", "amount")
                                if row.get(key) not in (None, "")} for row in result.get("lines") or []],
                        totals={key: value for key, value in totals(doc, result).items()
                                if key in ("subtotal", "vat_amount", "grand_total", "net_payable")},
                        is_paid=bool(doc.is_paid), payment_method=doc.payment_method)
        if doc.status == CONFIRMED:
            item["document"] = doc.purchase if doc.direction == "purchase" else doc.sales_document
        out.append(item)
    return out


def submit_chat(name) -> dict:
    """用户说「录入 / 入账 / 提交」：按记录上的选择与识别字段拼网页同一个入参（`build_payload`），走网页同一个入账函数提交
    （库存、付款照手工录入；接了 Express 的公司提交后照规则排写账）。请求号按记录固定（同 LINE），同一张只入一次。"""
    doc = _owned(name, lock=True)
    if doc.get("source") != SOURCE_CC:
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    recorded = doc.purchase if doc.direction == "purchase" else doc.sales_document
    if doc.status == CONFIRMED and recorded:
        return {"name": recorded, "duplicate": True}
    if doc.status != READY:
        frappe.throw(_("This document is no longer available"), frappe.DoesNotExistError)
    payload = build_payload(doc)
    payload["source"] = SOURCE_CC

    def finish(created):
        link = "purchase" if doc.direction == "purchase" else "sales_document"
        frappe.db.set_value(DOCTYPE, doc.name, {link: created["name"], "status": CONFIRMED, "decided_at": now()})

    if doc.direction == "purchase":
        from pearnly_erp_portal.purchasing import entry
    else:
        from pearnly_erp_portal.sales import entry
    return entry.post(payload, request_id="cc-intake:" + doc.name, finalize=finish, allow_duplicate=True)


# ── 清理 ────────────────────────────────────────────────────────────────────


def expire_stale():
    """每小时：卡在排队里超过 6 小时的记录落为失败并清掉源文件（卡片本身不过期）。"""
    cutoff = add_to_date(now_datetime(), hours=-6)
    for row in frappe.get_all(DOCTYPE, filters={"status": QUEUED, "received_at": ["<", cutoff]},
                              fields=["name", "source_file_name"], limit_page_length=500):
        _drop(row, FAILED, "stale")
