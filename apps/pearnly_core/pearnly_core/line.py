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
import logging

import frappe
import requests
from frappe import _

logger = logging.getLogger(__name__)

TIMEOUT = 15
# 下载媒体比发消息慢得多（图片可能好几 MB），单独给一个更宽的超时
DOWNLOAD_TIMEOUT = 60
# LIFF `id_token` 的验签端点（见 `verify_id_token`）
_VERIFY_ENDPOINT = "https://api.line.me/oauth2/v2.1/verify"
EVENT = "Pearnly LINE Event"


def _conf(key: str, default: str = "") -> str:
    return str(frappe.conf.get(f"pearnly_line_{key}") or default).strip()


def is_configured() -> bool:
    return bool(_conf("channel_secret") and _conf("channel_access_token"))


def channel_id() -> str:
    return _conf("channel_id")


def liff_id() -> str:
    """本站点的 LIFF ID（开通时由开通流程写进站点配置）。

    用途是"把用户带回本站点页面"的深链前缀（见 `dms_edit_link.py`）。
    没配就是空串 —— 调用方据此**省略按钮**，而不是给一个打不开的链接。
    """
    return _conf("liff_id")


def verify_id_token(id_token: str) -> "dict | None":
    """LINE 内 LIFF 页拿到的 `id_token` → 验签后的 claims；**验不过一律 None**。

    照搬老产品 `services/line_platform/liff.verify_id_token`（线上基准 501589f0），
    但**去掉了多 OA 那层**：老产品一个 LIFF 服务好几个官方号，所以要按 `liff_env` 查
    渠道注册表、还要处理"这个 OA 没有自己的 LIFF 就借共享 Provider 的"。新平台
    一站一渠道，渠道 id 只有一个来源：本站点配置。

    用哪个 client_id 验：优先 `channel_id`；没单独配就用 LIFF ID 的**前缀**
    （`2006123456-AbCdEf` 里 `-` 前面那段就是 LINE Login 渠道号，这是 LINE 自己的格式）。
    两者都取不到 → **直接 None，不发这次请求**（fail closed：宁可不认，也不去认一个
    别的渠道签出来的 token）。
    """
    channel = channel_id()
    if not channel:
        liff = liff_id()
        channel = liff.split("-")[0] if liff else ""
    if not id_token or not channel:
        return None
    try:
        response = requests.post(
            _VERIFY_ENDPOINT,
            data={"id_token": id_token, "client_id": channel},
            timeout=TIMEOUT,
        )
    except requests.RequestException:
        logger.warning("[line] id_token 验签请求失败", exc_info=True)
        return None
    if response.status_code != 200:
        return None
    try:
        claims = response.json()
    except ValueError:
        return None
    return claims if isinstance(claims, dict) else None


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
        frappe.throw(_("This site has no LINE access token configured, so it cannot send messages"))
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def mirroring() -> bool:
    """本机样例站点的**本地镜像**是否开启（见 `_mirror`）。

    两个条件**同时**成立才算开：站点开了 `pearnly_line_mirror`，**并且**这个站点根本没配
    LINE 凭据。第二个条件是安全绳：真有 LINE 通道的站点永远走真实发送 ——
    哪怕开关被误写进生产配置，也不会把消息吞进日志了事。
    """
    return _conf("mirror") == "1" and not is_configured()


def _mirror(kind: str, target: str, messages: list) -> bool:
    """把"本该发到 LINE"的消息记进 `Pearnly Push Log`，返回**已投递**。

    为什么要有它：本机样例站点没有 LINE 通道，可编辑器的保存那一步**必须确认卡片送达**
    才算成功（`dms_booking_edit.save`）—— 没有镜像的话，本机点一次保存永远是
    `preview_send_failed`，用户没法在本机验收任何一条涉及 LINE 的闭环。

    记的账目是诚实的：`trigger="line_mirror"` 与真实推送一眼可分，正文里写明
    "本机镜像，未发往 LINE"，卡片原文在 `response_body` 里，能逐字核对。
    """
    import json

    from pearnly_core import push_log

    logger.info("[line] 本地镜像 %s → %s（未发往 LINE）", kind, target or "-")
    try:
        push_log.record(
            "success",
            trigger="line_mirror",
            reference_doctype="Pearnly LINE Binding",
            business_key=str(target or ""),
            response_body=json.dumps(
                {
                    "mirrored": True,
                    "note": _("Local sample site: not sent to LINE, the raw text is only recorded"),
                    "kind": kind,
                    "to": target or "",
                    "messages": messages,
                },
                ensure_ascii=False,
            ),
        )
    except Exception:
        # 记账失败不能把发送结果翻成失败：卡片该"发"还是发了（镜像里）。
        logger.warning("[line] 本地镜像记账失败", exc_info=True)
    return True


def reply(reply_token: str, messages: list) -> bool:
    """用 reply token 回复（免费、一次有效、60 秒内）。"""
    if not reply_token:
        return False
    if mirroring():
        return _mirror("reply", reply_token, messages)
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
    if mirroring():
        return _mirror("push", to, messages)
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


def start_loading(user_id: str, seconds: int = 30) -> bool:
    """给对方看"正在输入"动画。**阻塞 HTTP**，调用方自己负责别占着 webhook。

    重活（识别身份证、建单）开始之前叫一次，用户才知道系统在动 —— 老产品同一处也这么做。
    ⚠️ LINE 只接受 5~60 秒、且以 5 秒为步长的值；别的会被接口拒。这里先夹再对齐步长。
    """
    if not user_id:
        return False
    step = int(seconds or 0) or 30
    step = max(5, min(60, step))
    step = min(60, max(5, (step // 5) * 5))
    resp = requests.post(
        "https://api.line.me/v2/bot/chat/loading/start",
        headers=_headers(),
        json={"chatId": user_id, "loadingSeconds": step},
        timeout=TIMEOUT,
    )
    if resp.status_code != 200:
        frappe.log_error(
            title="LINE loading 动画失败",
            message=f"{resp.status_code} {resp.text[:300]}",
        )
        return False
    return True


def get_profile(user_id: str) -> dict:
    resp = requests.get(
        f"https://api.line.me/v2/bot/profile/{user_id}",
        headers=_headers(), timeout=TIMEOUT,
    )
    return resp.json() if resp.status_code == 200 else {}


def download_message_content(message_id: str) -> bytes:
    """下载用户发来的**图片/文件**内容。

    ⚠️ 媒体内容不在 `api.line.me` 而在 `api-data.line.me` —— 用错域名会 404，
    这是 LINE 的一个常见坑。

    拿不到就返回 `b""`：调用方按"读不出"回话，而不是抛异常把整条流程打断。
    """
    if not message_id:
        return b""
    resp = requests.get(
        f"https://api-data.line.me/v2/bot/message/{message_id}/content",
        headers=_headers(), timeout=DOWNLOAD_TIMEOUT,
    )
    if resp.status_code != 200:
        frappe.log_error(
            title="LINE 取媒体失败",
            message=f"{message_id}: {resp.status_code} {resp.text[:200]}",
        )
        return b""
    return resp.content or b""


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
