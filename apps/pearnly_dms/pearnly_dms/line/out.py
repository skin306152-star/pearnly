# -*- coding: utf-8 -*-
"""LINE 结构化消息出口（quickReply / Flex 必须走 reply 或 push 接口）。

老产品同样有一层 `services/line_dms/_out.py`：纯文本走 `_reply`/`_push`，结构化消息
统一走 `_send` —— 它**按有没有 reply_token 自己分流**，调用方不再各写一份。

新平台少了两样东西，都是架构使然：

· **没有 channel 参数**。老产品一个库挂多个 OA，发送时要指定渠道；新平台
  **一个站点就是一家租户、一条 OA**（`pearnly_core.line` 读站点配置），不用传。
· **没有 `_thr` / `_spawn`**。那是 asyncio 的线程/后台包装；新平台是 Frappe 的同步栈，
  重活靠 `frappe.enqueue`（见 `dms_flow._enqueue`），这一层不需要。
"""

from __future__ import annotations

import hashlib
import json
import logging
import copy
import secrets
import threading
import time
from typing import Any, Dict, Optional

from pearnly_core import line
from frappe import _

logger = logging.getLogger(__name__)


def _snapshot_notice(msg):
    """**不再往消息里拼"资料时间 / 地址资料时间"**（用户 2026-09-25 明确要求）。

    以前这里会把「ข้อมูล ณ …」「ข้อมูลที่อยู่ ณ …」补到每一条 LINE 回话后面（纯文本拼一行、
    Flex 卡在正文末尾加一小行灰字）。操作员说这行没用、还占地方，所以**消息正文不再带时间**。
    时间本身照旧记在快照元数据里（连接页、查询结果卡按需显示），只是不再注入对方的消息。

    这里保留两个 flag 的消费：一次请求里读过的快照标记仍按"本消息消费一次"处理，
    免得残留在后面某条消息上（将来若要恢复显示，直接从这里接着写即可）。
    """
    import frappe

    frappe.flags.pop("dms_snapshot_status", None)
    frappe.flags.pop("dms_snapshot_geo_status", None)
    return msg


def send(line_user_id: str, msg: Optional[Dict[str, Any]] = None, reply_token: str = "") -> bool:
    """结构化消息出口：有 reply_token 就 reply，没有就 push。

    现在的口径是 **`reply_or_push`**（2026-09-23）：有令牌就先试**免费回覆**，被明确拒绝
    才转推播 —— 后台任务因此也能蹭免费窗口，而不是"手上没令牌就只能花钱"。见
    `pearnly_core.line.reply_or_push` 里那三条规则（尤其"超时也转推播、宁可极小概率重复"
    的取舍）。

    订车逐问既可能由 postback 应答（有令牌），也可能由后台任务发起（没有令牌）。
    `msg is None` 明确表示"这一步没有话说"，不算错，回 False 让调用方自己决定。
    """
    if msg is None:
        return False
    return line.reply_or_push(line_user_id, [_snapshot_notice(msg)], reply_token)


def push_or_reply_text(line_user_id: str, text: str, reply_token: str = "") -> bool:
    """纯文本的同一个口径：能免费回覆就免费，不行才推播。

    给"原本写死 `_push`（纯文本）"的那些地方用 —— 它们同样带着令牌，只是以前没用。
    """
    return line.reply_or_push(line_user_id, [_snapshot_notice({"type": "text", "text": text})], reply_token)


def reply_text(reply_token: str, text: str) -> bool:
    """纯文本回复（老产品 `_out._reply`）。"""
    if not reply_token:
        return False
    return line.reply(reply_token, [{"type": "text", "text": text}])


def reply(reply_token: str, msg: Dict[str, Any]) -> bool:
    """结构化消息**只走免费回覆**；没有令牌就不发（不转推播）。

    给"本来就只该在用户点按钮那一刻回一次"的消息用（例如菜单第 6 项的教程视频），
    这类消息不值得花推播额度。
    """
    if not reply_token:
        return False
    return line.reply(reply_token, [_snapshot_notice(msg)])


def push_text(line_user_id: str, text: str) -> bool:
    """纯文本推送（老产品 `_out._push`）。"""
    return line.push(line_user_id, [{"type": "text", "text": text}])


def start_loading(line_user_id: str, seconds: int = 30) -> bool:
    """重活开始前给对方一个"正在输入"（老产品 `_out.start_loading`）。"""
    return line.start_loading(line_user_id, seconds)


def start_loading_background(line_user_id: str, seconds: int = 30):
    """同 `start_loading`，但不等 LINE 来回；返回 `wait()`，发出回复前调用（见 `line.start_loading_background`）。"""
    return line.start_loading_background(line_user_id, seconds)


# 到 LINE 下载服务器的连接按进程复用：服务器在新加坡，去 api-data.line.me 的线路往返约 330 毫秒，
# 每次新建连接光握手就要约 1.4 秒（2026-10-03 生产实测，复用后同一请求 0.33 秒）。
# 识别线程共用一个连接池；不存 Cookie，各租户只靠每次请求带的令牌区分。
_media_lock = threading.Lock()
_media_session = None


def _line_media_session(replacing=None):
    """当前进程共用的下载会话。`replacing`：刚出过连接错误的那一个，若仍是当前的就换新的；
    旧的不主动关（别的线程可能正用它下载），没人用后随回收关闭。"""
    global _media_session
    from http.cookiejar import DefaultCookiePolicy
    import requests
    from requests.adapters import HTTPAdapter

    with _media_lock:
        if replacing is not None and _media_session is replacing:
            _media_session = None
        if _media_session is None:
            session = requests.Session()
            session.cookies.set_policy(DefaultCookiePolicy(allowed_domains=[]))
            session.mount("https://", HTTPAdapter(pool_connections=1, pool_maxsize=8))
            _media_session = session
        return _media_session


def download_image(message_id: str, max_bytes: int) -> bytes:
    """下载用户发来的图片，复用到 LINE 下载服务器的连接。

    复用的空闲连接可能刚被 LINE 关掉：这种连接错误换新连接重下一次（只读 GET，没有副作用）；
    连不上（超时）不重下，照原样上抛。"""
    import requests

    session = _line_media_session()
    try:
        return line.download_message_content(message_id, max_bytes=max_bytes, session=session)
    except requests.ConnectTimeout:
        raise
    except requests.ConnectionError:
        logger.info("[dms out] reused LINE media connection dropped; downloading again")
        return line.download_message_content(message_id, max_bytes=max_bytes,
                                             session=_line_media_session(replacing=session))


def download_content(message_id: str) -> Optional[bytes]:
    """下载消息内容（图片/凭证）。取不到就 None —— 调用方按"附件失败"如实告知。"""
    if not message_id:
        return None
    try:
        return line.download_message_content(message_id)
    except Exception:  # noqa: BLE001
        logger.warning("[dms out] 下载消息内容失败：%s", message_id, exc_info=True)
        return None


def enqueue(job: str, fn, *args) -> None:
    """识别/写入进后台；纯快照查重保留身份守卫并直接完成。

    webhook 必须立刻返回，否则 LINE 会超时重发 —— 而重发会让同一个按钮被点两次。

    ⚠️ **参数按形参名传**：后台 worker 是 `method(**kwargs)`（frappe 的
    `background_jobs.execute_job`），写成 `arg0/arg1/...` 会在 worker 里 `TypeError`，
    而 webhook 这边只看到"已入队"（用户表现为一直没有回卡）。点号路径取自函数自身，
    不写死模块名。
    """
    import inspect

    import frappe

    names = [name for name in inspect.signature(fn).parameters if name != "self"]
    if len(names) != len(args):
        # 参数对不上是**写代码时**的错：当场炸，别留成 worker 里的静默失败
        raise TypeError(
            _("{0} takes {1} arguments, but {2} were passed to the queue").format(
                fn.__name__, len(names), len(args)
            )
        )
    from pearnly_dms.line.identity import current

    arguments = dict(zip(names, args))
    identity = current(arguments.get("line_user_id"))
    if not identity or identity["user"] != (arguments.get("binding") or {}).get("user_id"):
        frappe.throw("dms_booking.not_bound", frappe.PermissionError)
    arguments["binding"] = {**arguments["binding"], "binding_revision": identity["binding_revision"]}
    if job == "dedup":
        # Cache-only read: never wait behind OCR, writes or background imports.
        if fn.__module__ != "pearnly_dms.intake.flow" or fn.__name__ != "run_dedup":
            raise ValueError("Invalid cache-only dedup target")
        return run_guarded(f"{fn.__module__}.{fn.__name__}", arguments)
    # Show activity while the job is waiting, then the worker renews it on start.
    start_loading(arguments["line_user_id"], 60)
    if job == "image":
        from pearnly_core.execution.work import submit
        from pearnly_core.execution.errors import QueueFull, Deferred
        try:
            return submit("dms.id_card", str(arguments["message_id"]), arguments, identity["user"])
        except (QueueFull, Deferred):
            push_text(arguments["line_user_id"], _("The recognition queue is busy. Please send the image again shortly."))
            raise
    frappe.enqueue(
        "pearnly_dms.line.out.run_guarded",
        queue="long",
        timeout=600,
        job_id=f"dms-{job}-{secrets.token_hex(4)}",
        target=f"{fn.__module__}.{fn.__name__}", arguments=arguments,
    )


# ── 慢任务不占推送额度 ─────────────────────────────────────────────────────
# LINE：回覆令牌收到后 1 分钟内有效、只能用一次（官方文档）。任务跑得久，结果到时令牌已过期，只能推播。
# 做法（用户 2026-10-05「其它的做掉」）：任务跑到 SLOW_AFTER 秒还没完，趁令牌还在先免费回一句「还在处理」
# 带「查看结果」按钮；任务做完不推播，结果存起来，他点按钮（一次新的消息，带新令牌）再免费回给他。
SLOW_AFTER = 40
_TOKEN_LIFE = 55
_TOKEN_AT = "pearnly:dms:reply-token-at:"
_RESULTS = "pearnly:dms:held-results:"
_RESULT_SECONDS = 24 * 3600


def _state():
    from pearnly_core.execution import state

    return state.store()


def _token_key(token: str) -> str:
    return _TOKEN_AT + hashlib.sha256(token.encode()).hexdigest()[:32]


def note_reply_token(token: str) -> None:
    """webhook 收到事件时记下回覆令牌的收到时间（2 分钟），后台任务据此算令牌还剩多久。"""
    if not token:
        return
    try:
        _state().set_value(_token_key(token), time.time(), expires_in_sec=120)
    except Exception:
        # 记不下只是少了"先回一句"的机会，不能挡住这条消息的处理。
        logger.warning("[dms out] could not note reply token time", exc_info=True)


def _show_result_button(text: str) -> Dict[str, Any]:
    from pearnly_dms.line import cards

    label = str(cards.BTN_SHOW_RESULT)[:20]
    return {"type": "text", "text": str(text), "quickReply": {"items": [{"type": "action", "action": {
        "type": "postback", "label": label, "data": "action=" + cards.ACT_SHOW_RESULT, "displayText": label}}]}}


class _SlowReply:
    """一个后台任务的「还在处理」计时：令牌快过期还没做完 → 用令牌回一句；做完时把结果改存起来。

    计时线程里没有站点上下文：存储客户端与完整键名在任务线程里先取好，线程里只用底层读写。"""

    def __init__(self, line_user_id: str, token: str):
        self.lock = threading.Lock()
        self.sent = False
        self.closed = False
        self.timer = None
        self.user = line_user_id
        if not line_user_id or not token:
            return
        try:
            client = _state()
            noted = client.get_value(_token_key(token))
            key = client.make_key(_RESULTS + line_user_id)
        except Exception:
            return
        age = time.time() - float(noted) if noted else None
        if age is None or age >= _TOKEN_LIFE:
            return
        from pearnly_dms.line import cards

        message = _show_result_button(cards.TXT_STILL_WORKING)
        send = line.reply_sender(token)
        self.client, self.key = client, key

        def fire():
            with self.lock:
                if self.closed:
                    return
                self.sent = send([message])
                if self.sent:
                    client.set(key, json.dumps({"pending": True}), ex=_RESULT_SECONDS)

        self.timer = threading.Timer(max(0.0, SLOW_AFTER - age), fire)
        self.timer.daemon = True
        self.timer.start()

    def finish(self) -> None:
        """任务结束（含出错）时调用，在 `line.batch` 发出之前。"""
        if self.timer is None:
            return
        self.timer.cancel()
        with self.lock:
            self.closed = True
            if not self.sent:
                return
        import frappe

        # 令牌已用来回「还在处理」：这一批发给本人的消息不推播，存起来等他点「查看结果」。
        current = getattr(frappe.local, "pearnly_line_batch", None)
        held = []
        if current and current.get("to") == self.user:
            held, current["messages"] = list(current["messages"]), []
            current["used"] = True
        self.client.set(self.key, json.dumps({"messages": held}), ex=_RESULT_SECONDS)


def show_result(line_user_id: str, reply_token: str) -> None:
    """「查看结果」按钮：有结果就免费回给他；还在跑就再回一句「还没好」；什么都没有就说过期。"""
    from pearnly_dms.line import cards

    client = _state()
    key = client.make_key(_RESULTS + line_user_id)
    raw = client.get(key)
    held = json.loads(raw) if raw else None
    if not held:
        line.reply(reply_token, [{"type": "text", "text": str(cards.TXT_EXPIRED)}])
        return
    if held.get("pending"):
        line.reply(reply_token, [_show_result_button(cards.TXT_NOT_READY)])
        return
    messages = list(held.get("messages") or [])
    # 一次回覆最多 5 条：多出来的留着，最后一条换成带按钮的「还有结果」。
    if len(messages) > 5:
        client.set(key, json.dumps({"messages": messages[4:]}), ex=_RESULT_SECONDS)
        messages = messages[:4] + [_show_result_button(cards.TXT_MORE_RESULTS)]
    else:
        client.delete(key)
    line.reply(reply_token, messages or [{"type": "text", "text": str(cards.TXT_EXPIRED)}])


def run_guarded(target, arguments):
    """Persisted queue entry: a rebind/revocation while waiting cancels stale work."""
    import frappe
    from pearnly_dms.line.identity import matches

    if not str(target).startswith("pearnly_dms."):
        raise ValueError("Invalid DMS job target")
    if not matches(arguments.get("binding") or {}, arguments.get("line_user_id")):
        return {"cancelled": "binding_changed"}
    # 一个后台任务 = 一次反应：发给本人的消息攒到任务结束一次发出，带着令牌就免费回覆，
    # 没令牌（或已过期）也只算一条推播。见 `pearnly_core.line.batch`。
    user = arguments.get("line_user_id") or ""
    # 排队等 DMS 账号时，每隔一会儿续一次"正在输入"（免费），别让对方以为没反应。
    frappe.local.dms_lock_wait_hook = (lambda: start_loading(user, 60)) if user else None
    from pearnly_dms import i18n

    try:
        # 后台任务同样按这个人手机上的语言说话（问过的语言有一小时缓存，不会每次都问 LINE）。
        with i18n.line_language(user), line.batch(user, arguments.get("reply_token") or ""):
            slow = _SlowReply(user, arguments.get("reply_token") or "")
            try:
                return frappe.get_attr(target)(**arguments)
            finally:
                slow.finish()
    finally:
        frappe.local.dms_lock_wait_hook = None


def run_image(binding, line_user_id, message_id, reply_token=""):
    """Registered resumable OCR entry; order writers are deliberately excluded."""
    return run_guarded("pearnly_dms.intake.flow.process_image", {
        "binding": binding, "line_user_id": line_user_id, "message_id": message_id,
        "reply_token": reply_token})


def ocr_failed(arguments, code):
    from pearnly_dms.line.identity import matches
    from pearnly_core import line_session

    user = arguments.get("line_user_id")
    payload = (line_session.get_session(user) or {}).get("payload") or {}
    if (matches(arguments.get("binding") or {}, user)
            and payload.get("pending_image") == arguments.get("message_id")):
        text = (_("The recognition result needs checking. Please do not resend this image yet.")
                if code == "provider_outcome_unknown"
                else _("Recognition could not finish. Please try again shortly."))
        # 发图那口气的令牌还在就免费回覆（识别失败往往在一分钟内就能确定）
        push_or_reply_text(user, text, arguments.get("reply_token") or "")
