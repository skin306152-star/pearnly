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
from urllib.parse import quote

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

    client_id 来自本站 LIFF 所属的 LINE Login 频道；Messaging API 频道只用于 OA，
    不能拿来验证 LIFF 身份。缺失/无效 LIFF 配置时拒绝，不借用其他频道。
    """
    channel, separator, app_id = liff_id().partition("-")
    if not id_token or not separator or not app_id or not channel.isdigit():
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
    if not isinstance(claims, dict) or str(claims.get("aud") or "") != channel:
        return None
    return claims if claims.get("sub") and claims.get("iss") == "https://access.line.me" else None


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
    """把"本该发到 LINE"的消息交给产品记账，返回**已投递**。

    为什么要有它：本机样例站点没有 LINE 通道，可编辑器的保存那一步**必须确认卡片送达**
    才算成功（`dms_booking_edit.save`）—— 没有镜像的话，本机点一次保存永远是
    `preview_send_failed`，用户没法在本机验收任何一条涉及 LINE 的闭环。

    共用层不知道谁记账：推送台账归产品（ADR-009，今天是 DMS 的 records 域），所以这里
    只按 `pearnly_line_mirror` 派发，产品自己决定记到哪。没装那个产品的站点不记账，
    但发送结果仍是"已投递"—— 镜像的语义就是"当作发出去了"，与谁留痕无关。
    """
    logger.info("[line] 本地镜像 %s → %s（未发往 LINE）", kind, target or "-")
    for path in frappe.get_hooks("pearnly_line_mirror") or []:
        try:
            frappe.get_attr(path)(kind=kind, target=target, messages=messages)
        except Exception:
            # 记账失败不能把发送结果翻成失败：卡片该"发"还是发了（镜像里）。
            logger.warning("[line] 本地镜像记账失败", exc_info=True)
    return True


# ── 一次反应，一次发送（2026-09-25 省额度） ─────────────────────────────────
#
# LINE 的账是这么算的（官方定价页）：回覆**免费**；推播按"发给几个人"计数，一次请求里
# 带 1～5 个气泡**都只算 1 条**。而一张回覆令牌只能用一次。
#
# 所以"同一次反应里先回一句、再发一张卡"是最贵的写法：第一条用掉了令牌，第二条只能推播。
# `batch` 把一次反应里发给**同一个人**的所有消息攒起来，结束时一次发出：有令牌就整批
# 免费回覆，没令牌也只算一条推播。消息内容、顺序都不变，只是合成一次调用。
#
# 只有调用方显式 `with line.batch(...)` 才生效 —— 没开的产品行为逐字节不变。

_BATCH_LIMIT = 5  # LINE 单次请求最多 5 个消息对象


def _batch():
    return getattr(frappe.local, "pearnly_line_batch", None)


class batch:
    """`with line.batch(用户, 令牌):` —— 块内发给该用户的消息攒到块结束一次发出。

    · 嵌套同一用户：并入外层（外层没令牌、内层有，就接过令牌）；
    · 块内发给**别人**的（例如审批通知管理员）照常立即发送；
    · 需要真实送达结果的发送（带 `retry_key` 的回执）会当场连同已攒的消息一起发出，
      并返回真实结果；其余发送在块内返回 True（已接收，块结束时发）。
    """

    def __init__(self, to: str, reply_token: str = ""):
        self.to = to or ""
        self.token = reply_token or ""
        self._owner = False

    def __enter__(self):
        current = _batch()
        if current is not None:
            if current["to"] == self.to and self.token and not current["token"] and not current["used"]:
                current["token"] = current["origin"] = self.token
            return self
        if not self.to:
            return self
        frappe.local.pearnly_line_batch = {"to": self.to, "token": self.token,
                                           "origin": self.token, "used": False, "messages": []}
        self._owner = True
        return self

    def __exit__(self, *exc):
        if not self._owner:
            return False
        try:
            flush()
        except Exception:
            # 块内的业务异常优先上抛；发送失败只记日志，不能把它盖掉
            logger.warning("[line] 合并发送失败", exc_info=True)
        finally:
            frappe.local.pearnly_line_batch = None
        return False


def _collects(to: str = "", reply_token: str = "") -> "dict | None":
    """当前块是否接收这条消息：按用户认，或按本块的（原始）令牌认。"""
    current = _batch()
    if current is None:
        return None
    if to and to == current["to"]:
        return current
    if reply_token and reply_token == current["origin"]:
        return current
    return None


def flush(*, retry_key: str = "") -> bool:
    """把攒着的消息现在发出去（每 5 条一次请求）。令牌只给第一次请求用。"""
    current = _batch()
    if current is None or not current["messages"]:
        return True
    messages, current["messages"] = current["messages"], []
    frappe.local.pearnly_line_batch = None  # 发送本身不能再被自己收集
    try:
        ok = True
        for start in range(0, len(messages), _BATCH_LIMIT):
            chunk = messages[start:start + _BATCH_LIMIT]
            token = "" if current["used"] else current["token"]
            key = retry_key if start + _BATCH_LIMIT >= len(messages) else ""
            # 与 `reply_or_push` 同一口径：回覆不成（被拒或不确定）就推播。
            # 走公开的 `reply` / `push`，替换它们的调用方（测试记录器）同样能拦住。
            sent = bool(token) and reply(token, chunk)
            if token:
                current["used"] = True
            if not sent:
                sent = push(current["to"], chunk, retry_key=key) if key else push(current["to"], chunk)
            ok = sent and ok
        return ok
    finally:
        frappe.local.pearnly_line_batch = current


def reply(reply_token: str, messages: list) -> bool:
    """用 reply token 回复（免费、一次有效、60 秒内）。"""
    if not reply_token:
        return False
    current = _collects(reply_token=reply_token)
    if current is not None:
        current["messages"].extend(messages)
        return True
    return _post_reply(reply_token, messages)[0]


def _post_reply(reply_token: str, messages: list):
    """发一次回覆。返回 `(成功?, HTTP 状态码, 摘要)`；网络层失败时状态码为 0。

    为什么要返回状态码：**"令牌被明确拒绝"和"网络不知道通没通"要分开处理** ——
    前者可以放心改用推播，后者改了就可能重复发一条（见 `reply_or_push`）。
    记账也在这里做（`reply_ok` / `reply_rejected` / `reply_failed` / `reply_unknown`）。
    """
    if mirroring():
        return bool(_mirror("reply", reply_token, messages)), 200, "mirror"
    try:
        resp = requests.post(
            "https://api.line.me/v2/bot/message/reply",
            headers=_headers(),
            json={"replyToken": reply_token, "messages": messages},
            timeout=TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001 —— 超时/连接失败：**不知道发出去没有**
        _count("reply_unknown")
        logger.warning("[line] reply 请求异常（不确定是否送达）: %s", type(exc).__name__)
        return False, 0, type(exc).__name__
    if resp.status_code == 200:
        _count("reply_ok")
        return True, 200, ""
    # 400/401/403 = 令牌本身不被接受（过期、已被用过、无效）→ 明确被拒，**不含糊**
    _count("reply_rejected" if resp.status_code in (400, 401, 403) else "reply_failed")
    frappe.log_error(title="LINE reply 失败", message=f"{resp.status_code} {resp.text[:400]}")
    return False, resp.status_code, resp.text[:200]


def push(to: str, messages: list, *, retry_key: str = "") -> bool:
    """主动推送（计费）。用于没有 reply token 的场合，例如定时提醒。"""
    if not to:
        return False
    current = _collects(to=to)
    if current is not None:
        current["messages"].extend(messages)
        # 回执类要真实结果（台账按它记"已送达"），当场连同已攒的一起发
        return flush(retry_key=retry_key) if retry_key else True
    if mirroring():
        return _mirror("push", to, messages)
    headers = _headers()
    if retry_key:
        headers["X-Line-Retry-Key"] = retry_key
    try:
        resp = requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers=headers,
            json={"to": to, "messages": messages},
            timeout=TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001
        _count("push_failed")
        logger.warning("[line] push 请求异常: %s", type(exc).__name__)
        return False
    if retry_key and resp.status_code == 409 and resp.headers.get("x-line-accepted-request-id"):
        # LINE 按 `X-Line-Retry-Key` 去重：这条它已经收过了，**不算新的一条**
        _count("push_deduped")
        return True
    if resp.status_code != 200:
        _count("push_failed")
        frappe.log_error(
            title="LINE push 失败",
            message=f"{resp.status_code} {resp.text[:400]}",
        )
        return False
    _count("push_ok")
    return True


def reply_or_push(to: str, messages: list, reply_token: str = "", *,
                  retry_key: str = "") -> bool:
    """**先试免费回覆，不行才推播** —— 省 LINE 额度的主开关（2026-09-23）。

    LINE 的规则：拿用户刚发消息时那张令牌回覆是**免费、不限量**的；主动推播才吃
    每月额度（我们那个 `144 / 300`）。而我们的流程有个天然毛病：**重活都丢后台**，
    后台跑完时手上有令牌却不用（或者干脆没把令牌带进来），于是每条结果都成了计费推播。

    三条规则，每条都对应一次真实的坑：

    1. **没令牌 → 直接推播**（老行为不变）；
    2. **令牌被明确拒绝**（400/401/403：过期、已被用过、无效）→ 转推播。这是免费没戏了，
       老老实实花一条；
    3. **超时 / 5xx（不知道到底发出去没有）→ 仍然推播**。代价是极小概率出现一条重复消息；
       反过来选"不推播"的代价是**消息彻底丢掉** —— 我们的流程是"要等这张卡才能往下走"，
       丢一条会让操作员卡住、再重发一次，浪费更多，而且他会以为系统坏了。
       这种"不确定"单独记成 `reply_unknown`：比例一高就说明 LINE 或网络在抖。
    """
    current = _collects(to=to, reply_token=reply_token)
    if current is not None:
        if reply_token and not current["token"] and not current["used"]:
            current["token"] = current["origin"] = reply_token  # 块开时没令牌，块内拿到了就用它
        current["messages"].extend(messages)
        return flush(retry_key=retry_key) if retry_key else True
    if not reply_token:
        return push(to, messages, retry_key=retry_key)
    ok, status, _why = _post_reply(reply_token, messages)
    if ok:
        return True
    # 走到这里只有两种可能：令牌明确不被接受（400/401/403），或者**不确定送达**
    # （超时=0 / 5xx）。两种都转推播 —— 前者的免费机会已经没了，后者宁可冒极小概率
    # 重复也不让消息丢掉（理由见上）。比例分别记在 `reply_rejected` 与 `reply_unknown`。
    _ = status
    return push(to, messages, retry_key=retry_key)


# ── 用量记账（免费回覆 vs 计费推播） ─────────────────────────────────────────

#: Redis 里的哈希键：`{日期}|{种类}` → 条数。只计数，不发消息、不写数据库。
USAGE_HASH = "pearnly:line:usage"
USAGE_KINDS = ("reply_ok", "reply_rejected", "reply_failed", "reply_unknown",
               "push_ok", "push_failed", "push_deduped")


def _count(kind: str) -> None:
    """按天记一笔：这一次是**免费回覆**还是**计费推播**，以及失败是哪一种。

    为什么要有它：2026-09-23 用户问"几百人用，这个额度开销得起吗"，而当时**没有任何数据**
    能回答 —— 只有发送失败才写日志，成功的什么都不留。这里只往 Redis 记一个计数。
    """
    try:
        cache = frappe.cache()
        day = str(frappe.utils.now())[:10]
        cache.hincrby(cache.make_key(USAGE_HASH), f"{day}|{kind}", 1)
    except Exception:
        # 记账失败绝不能影响发送本身
        logger.warning("[line] 用量记账失败（不影响发送）", exc_info=True)


def usage(days: int = 31) -> dict:
    """读用量账：`{"2026-09-23": {"reply_ok": 12, "push_ok": 3, ...}}`。**只读**。

    `days<=0` 表示全给。给排查/估算用：免费回覆与计费推播的真实比例、令牌被拒的比例
    （那句"令牌到底多久过期"的疑问，答案就在 `reply_rejected` 上）。
    """
    try:
        cache = frappe.cache()
        # HINCRBY stores raw integers. RedisWrapper.hgetall would namespace the
        # key again and unpickle values, so use its native command counterpart.
        raw = cache.execute_command("HGETALL", cache.make_key(USAGE_HASH)) or {}
    except Exception:
        logger.warning("[line] 读用量账失败", exc_info=True)
        return {}
    out: dict = {}
    for key, value in raw.items():
        key = key.decode() if isinstance(key, bytes) else str(key)
        day, _sep, kind = key.partition("|")
        out.setdefault(day, {})[kind or "unknown"] = int(value or 0)
    if days and days > 0:
        keep = sorted(out)[-days:]
        out = {day: out[day] for day in keep}
    return out


def start_loading(user_id: str, seconds: int = 30) -> bool:
    """给对方看"正在输入"动画。短超时且失败不影响业务入队。

    重活（识别身份证、建单）开始之前叫一次，用户才知道系统在动 —— 老产品同一处也这么做。
    ⚠️ LINE 只接受 5~60 秒、且以 5 秒为步长的值；别的会被接口拒。这里先夹再对齐步长。
    """
    if not user_id or not _conf("channel_access_token"):
        return False
    step = int(seconds or 0) or 30
    step = max(5, min(60, step))
    step = min(60, max(5, (step // 5) * 5))
    try:
        resp = requests.post(
            "https://api.line.me/v2/bot/chat/loading/start",
            headers=_headers(),
            json={"chatId": user_id, "loadingSeconds": step},
            timeout=(1, 1),
        )
    except requests.RequestException:
        logger.warning("[line] loading animation unavailable", exc_info=True)
        return False
    if resp.status_code not in (200, 202):
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


# 平台有翻译的语言；LINE 给的是 BCP 47（`th`、`zh-Hant`、`en-US`、`ja`）。
USER_LANGUAGES = ("th", "zh", "en", "ja")
_LANGUAGE_TTL = 3600


def user_language(user_id: str, default: str | None = None) -> str:
    """这个 LINE 用户手机上 LINE App 的语言（用户 2026-09-26：LINE 里的字段与按钮跟着手机语言走）。

    问 LINE 的 profile（没同意隐私政策的用户不给这个字段），按一小时缓存，免得每个事件都多一次
    外部请求；问不到或不是平台有翻译的语言，就退回 `default`（再没有就用站点系统语言）。
    """
    fallback = default or frappe.db.get_single_value("System Settings", "language") or "th"
    if not user_id:
        return fallback
    key = f"pearnly:line:user-language:{user_id}"
    try:
        cached = frappe.cache.get_value(key)
        if cached:
            return cached if cached in USER_LANGUAGES else fallback
    except Exception:
        cached = None
    try:
        response = requests.get(f"https://api.line.me/v2/bot/profile/{user_id}",
                                headers=_headers(), timeout=TIMEOUT)
    except Exception:
        return fallback
    if response.status_code != 200:
        return fallback  # 没问到（限流、网络）不记缓存，下一次再问。
    try:
        tag = str(response.json().get("language") or "").strip().lower()
    except ValueError:
        return fallback
    code = tag.split("-")[0]
    language = code if code in USER_LANGUAGES else ""
    try:
        frappe.cache.set_value(key, language or "-", expires_in_sec=_LANGUAGE_TTL)
    except Exception:
        pass
    return language or fallback


def public_account() -> dict:
    """本站点这个官方号对外的身份：名字、Basic ID、加好友链接。

    为什么要问 LINE 而不是写进配置：Basic ID 是 LINE 那边的东西，抄进配置就会
    与真实账号漂移（换了官方号、改了 ID，页面还显示旧的，用户扫码加错人）。

    未配置渠道或问不到时**直接抛错**，不返回空壳 —— 一个 basic_id 为空的二维码
    比没有更糟：用户会以为是自己扫错了。

    注意：`pearnly_dms/operators/line_account.py` 里有一份面向花名册的同类实现
    （多一个本地生成的二维码，错误话术按 DMS 的键）。那份属于 DMS 的窗口，
    本次不动它；下次动 DMS 时把它改成调用本函数 + 自己补二维码。
    """
    token = _conf("channel_access_token")
    if not token or not _conf("channel_secret"):
        frappe.throw(_("The LINE channel is not configured for this account"))
    try:
        resp = requests.get("https://api.line.me/v2/bot/info",
                            headers=_headers(), timeout=TIMEOUT)
        resp.raise_for_status()
        info = resp.json()
    except (requests.RequestException, ValueError):
        frappe.throw(_("LINE could not be reached; please try again"))
    basic_id = str((info or {}).get("basicId") or "").strip()
    if not basic_id.startswith("@"):
        frappe.throw(_("LINE could not be reached; please try again"))
    return {
        "name": str(info.get("displayName") or basic_id),
        "basic_id": basic_id,
        "url": "https://line.me/R/ti/p/" + quote(basic_id, safe=""),
    }


class MediaTooLarge(ValueError):
    """The LINE media exceeds the caller's bounded download size."""


def download_message_content(message_id: str, *, max_bytes=20 * 1024 * 1024) -> bytes:
    """Stream media with a hard byte bound before buffering or decoding it."""
    if not message_id:
        return b""
    with requests.get(
        f"https://api-data.line.me/v2/bot/message/{message_id}/content",
        headers=_headers(), timeout=DOWNLOAD_TIMEOUT, stream=True,
    ) as resp:
        if resp.status_code != 200:
            frappe.logger("pearnly.line").warning("LINE media unavailable: HTTP %s", resp.status_code)
            return b""
        declared = resp.headers.get("Content-Length", "")
        if declared.isdigit() and int(declared) > max_bytes:
            raise MediaTooLarge("LINE media size limit exceeded")
        data = bytearray()
        for chunk in resp.iter_content(65536):
            if len(data) + len(chunk) > max_bytes:
                raise MediaTooLarge("LINE media size limit exceeded")
            data.extend(chunk)
        return bytes(data)


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
        pearnly_line_handlers = ["pearnly_dms.line.handlers.handle"]
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
            raise  # The event receipt must record failure, never an ignored success.
    return False
