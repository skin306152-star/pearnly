# -*- coding: utf-8 -*-
"""LINE 传输层（租户站点侧）。三个产品共用这一层。

隔离模型（用户 2026-09-18）：**每个租户一个域名一个 app 一条 LINE**。
凭据在开通时由控制面推到本站点，本文件只读本站点自己的那份。所以：

- 验签用的是**本租户自己的** channel secret → 别的租户的签名必然验不过；
- 发消息用的是**本租户自己的** access token → 消息只会从本租户的官方号发出。

隔离靠凭据本身，不靠路由里写 if —— 少一处判断就少一处写错的机会。

三个产品的差异不在这层：这层只做"收事件 → 交给产品处理 → 回执"。
产品通过 hooks 的 `pearnly_line_handlers` 注册自己的处理器，所以：
- DMS 现在就能接（旧系统已跑通的那条）；
- ERP / Cowork 以后注册一个处理器即可，不用改本文件；
- 新事件类型、新产品都属于"加一个 handler"，不动传输层。
"""

import base64
import hashlib
import hmac
import json

import frappe
import requests
from frappe import _

TIMEOUT = 15
EVENT = "Pearnly LINE Event"


def _conf(key: str, default: str = "") -> str:
    return str(frappe.conf.get(f"pearnly_line_{key}") or default).strip()


def is_configured() -> bool:
    return bool(_conf("channel_secret") and _conf("channel_access_token"))


def channel_id() -> str:
    return _conf("channel_id")


# ── 验签 ────────────────────────────────────────────────────────────────────


def verify_signature(body: bytes, signature: str) -> bool:
    """HMAC-SHA256(body, 本租户的 channel secret) 对比 X-Line-Signature。"""
    secret = _conf("channel_secret")
    if not secret or not signature:
        return False
    mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode("utf-8")
    # 用 compare_digest 防时序攻击
    return hmac.compare_digest(expected, signature)


# ── 发消息 ──────────────────────────────────────────────────────────────────


def _headers() -> dict:
    token = _conf("channel_access_token")
    if not token:
        frappe.throw(_("本站点没有配 LINE access token，发不出消息"))
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def reply(reply_token: str, messages: list) -> bool:
    """用 reply token 回复（免费、一次有效、60 秒内）。"""
    if not reply_token:
        return False
    resp = requests.post(
        "https://api.line.me/v2/bot/message/reply",
        headers=_headers(),
        json={"replyToken": reply_token, "messages": messages},
        timeout=TIMEOUT,
    )
    if resp.status_code != 200:
        frappe.log_error(
            title="LINE reply 失败",
            message=f"{resp.status_code} {resp.text[:400]}",
        )
        return False
    return True


def push(to: str, messages: list) -> bool:
    """主动推送（计费）。用于没有 reply token 的场合，例如定时提醒。"""
    if not to:
        return False
    resp = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers=_headers(),
        json={"to": to, "messages": messages},
        timeout=TIMEOUT,
    )
    if resp.status_code != 200:
        frappe.log_error(
            title="LINE push 失败",
            message=f"{resp.status_code} {resp.text[:400]}",
        )
        return False
    return True


def get_profile(user_id: str) -> dict:
    resp = requests.get(
        f"https://api.line.me/v2/bot/profile/{user_id}",
        headers=_headers(), timeout=TIMEOUT,
    )
    return resp.json() if resp.status_code == 200 else {}


# ── 事件幂等 ────────────────────────────────────────────────────────────────


def _claim_event(event_id: str, source: str) -> bool:
    """占坑。已经有这条事件就返回 False（不重复处理）。"""
    if not event_id:
        return True
    if frappe.db.exists(EVENT, event_id):
        return False
    doc = frappe.get_doc({
        "doctype": EVENT,
        "event_id": event_id,
        "source": source,
        "status": "processing",
    })
    doc.insert(ignore_permissions=True, set_name=event_id)
    frappe.db.commit()
    return True


def _finish_event(event_id: str, status: str, detail: str = "") -> None:
    if not event_id or not frappe.db.exists(EVENT, event_id):
        return
    frappe.db.set_value(EVENT, event_id, {"status": status, "detail": (detail or "")[:500]})
    frappe.db.commit()


# ── webhook 入口 ────────────────────────────────────────────────────────────


@frappe.whitelist(allow_guest=True, methods=["POST"])
def webhook():
    """租户站点自己的 LINE webhook 地址。

    地址形如 `http://<租户域名>:<端口>/api/method/pearnly_core.line.webhook`，
    填到该租户 LINE 后台的 Webhook URL。

    行为约定（照抄旧 DMS 已验证的经验）：
    - 验签失败 → 400（不是我们的渠道，直接拒）
    - 处理中出错 → **仍然回 200**。LINE 的重投是控制台开关、不归我们控，
      回非 200 只会让 LINE 判定 webhook 挂了并降级投递，救不回这一条；
      补救靠产品处理器自己回"请重发"。
    """
    body = frappe.request.get_data()
    signature = frappe.get_request_header("X-Line-Signature") or ""

    if not verify_signature(body, signature):
        frappe.log_error(title="LINE webhook 验签失败", message=f"channel={channel_id()}")
        frappe.local.response.http_status_code = 400
        return {"ok": False, "error": "bad_signature"}

    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as e:
        frappe.log_error(title="LINE webhook JSON 解析失败", message=str(e)[:400])
        frappe.local.response.http_status_code = 400
        return {"ok": False, "error": "bad_json"}

    for event in payload.get("events") or []:
        event_id = event.get("webhookEventId") or ""
        if not _claim_event(event_id, source="line_webhook"):
            continue  # 重投的旧事件，跳过
        try:
            handled = _dispatch(event)
            _finish_event(event_id, "done" if handled else "ignored")
        except Exception as e:
            frappe.log_error(
                title="LINE 事件处理失败",
                message=f"event={event_id}\n{str(e)[:600]}",
            )
            _finish_event(event_id, "failed", str(e)[:400])

    return {"ok": True}


def _dispatch(event: dict) -> bool:
    """把事件交给产品注册的处理器。**没有处理器就当作未处理**，不报错。

    产品通过 hooks 注册：
        pearnly_line_handlers = ["pearnly_dms.line_handlers.handle"]
    每个处理器签名 `handle(event: dict) -> bool`，返回 True 表示"这个事件我处理了"。
    多个处理器按注册顺序尝试，第一个返回 True 的就结束。
    """
    for path in frappe.get_hooks("pearnly_line_handlers") or []:
        try:
            handler = frappe.get_attr(path)
        except Exception as e:
            frappe.log_error(title="LINE 处理器加载失败", message=f"{path}: {e}"[:400])
            continue
        try:
            if handler(event):
                return True
        except Exception as e:
            frappe.log_error(title="LINE 处理器执行失败", message=f"{path}: {e}"[:600])
    return False
