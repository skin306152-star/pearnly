"""手机 / 电脑浏览器的网页推送（ADR-019 送达通道 2）：本站的 VAPID 密钥、设备订阅与发出。

- 密钥：每个站点一对（`Pearnly Assistant Push Key`，私钥是 Frappe 的密码字段，加密存放），第一次用到时生成；
  站点级锁防止两个并发请求各生成一把（订阅与签名会对不上）。私钥解不开（例如恢复站点时没带原加密密钥）就换一对，
  旧订阅都绑在旧公钥上，一并删掉，对话页下次打开时按新公钥重新订阅。
- 订阅：`Pearnly Assistant Push Subscription`，按推送地址的 sha256 唯一。对话页每次打开都重新认领，同一台设备换人
  登录就覆盖成当前登录人；每人最多 MAX_PER_USER 台，多了删最久没认领的。只删自己的。
- 订阅跟着认领它的那次登录走（`sid` 存登录会话编号的摘要，不存编号本身：它就是登录凭据，表又对管理员可读）：
  退出登录时删掉（`on_logout` 钩子 `forget_session`）；身份服务统一登出、被踢、过期等不经钩子的，发出前核对会话
  已不在就删掉、不发。共用的电脑或手机上，上一个人的提醒不会再弹给下一个人。
- 发出：只发往白名单里的推送服务（`webpush.push_host`），不跟随跳转；推送服务回 404 / 410 表示订阅已失效，删掉；
  其余失败（含 403：签名写错也会 403，不能因此删掉好订阅）只记在订阅行上，并汇总记一次错误日志。
  日志与订阅行只写状态码（或异常类型）和推送服务主机，不写推送地址和密钥。
- 提醒（`Pearnly Assistant Notice`）由 `notices` 提交后排队 `send_notice`，点开去那段对话或定时任务页（`contracts.deep_link`）。

只由本模块读写这两张表。
"""
import hashlib
import time

import frappe
import requests
from frappe import _

from pearnly_core.assistant import config, contracts, webpush

SUBSCRIPTION = "Pearnly Assistant Push Subscription"
KEY = "Pearnly Assistant Push Key"
NOTICE = "Pearnly Assistant Notice"
APP_NAME = "CC"
# VAPID 的 sub：苹果要求 mailto: 或 https 地址
SUBJECT = "https://pearnly.com"
MAX_PER_USER = 10
TIMEOUT = (5, 10)
LOCK_TIMEOUT = 30
DEFAULT_TTL = 6 * 3600
TTL_MAX = 28 * 86400
URGENCIES = ("very-low", "low", "normal", "high")


def _user() -> str:
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("Not signed in"), frappe.AuthenticationError)
    return user


def session_key(sid) -> str:
    """登录会话编号的摘要（订阅行的 `sid`）。没有真会话（访客、空）回空串。"""
    sid = str(sid or "")
    if not sid or sid == "Guest":
        return ""
    return hashlib.sha256(sid.encode()).hexdigest()


def _lock():
    return frappe.cache.lock(f"{frappe.local.site}:pearnly_assistant:vapid", timeout=LOCK_TIMEOUT)


# ---- 密钥 ----

def public_key() -> str:
    """本站的 VAPID 公钥（浏览器订阅时用，base64url）；还没有就生成一对。"""
    found = frappe.db.get_single_value(KEY, "public_key")
    if found:
        return found
    with _lock():
        found = _stored_public()
        if not found:
            found = _generate()[1]
    return found


def _stored_public() -> str:
    # 等锁期间别的进程可能已生成并提交：加锁读才读得到最新提交（普通读停在本事务开始时的快照）
    rows = frappe.db.sql("SELECT `value` FROM `tabSingles` WHERE `doctype`=%s AND `field`=%s FOR UPDATE",
                         (KEY, "public_key"))
    return (rows[0][0] or "") if rows else ""


def _generate(drop_subscriptions=False) -> tuple[str, str]:
    private_key, public = webpush.new_keys()
    if drop_subscriptions:
        frappe.db.delete(SUBSCRIPTION)
    doc = frappe.get_single(KEY)
    doc.public_key, doc.private_key = public, private_key
    doc.save(ignore_permissions=True)
    # 对话页取公钥是 GET，Frappe 的 GET 不自动提交：不提交的话这对密钥随请求回滚，下次又生成一对，设备全对不上
    frappe.db.commit()
    return private_key, public


def _stored_private() -> str:
    try:
        return frappe.get_single(KEY).get_password("private_key", raise_exception=False) or ""
    except frappe.ValidationError:  # 站点加密密钥换过，解不开
        return ""


def _keys():
    """(私钥, 公钥)。私钥解不开就换一对并删掉全部订阅，回 None：这一次没有能收的设备（刚被别的进程换过时也一样）。"""
    public = public_key()
    private_key = _stored_private()
    if webpush.matches(private_key, public):
        return private_key, public
    with _lock():
        if _stored_public() == public:
            frappe.log_error(title="Assistant push keys replaced",
                             message="The stored push key could not be read. New keys were made and subscriptions removed.")
            _generate(drop_subscriptions=True)
    return None


# ---- 订阅 ----

def subscribe(endpoint, p256dh, auth) -> dict:
    """当前登录人认领这台设备的推送订阅（新建或覆盖）。不合格抛 ContractError。"""
    user = _user()
    config.product()
    try:
        sub = webpush.subscription(endpoint, p256dh, auth)
    except ValueError:
        raise contracts.ContractError("invalid push subscription") from None
    values = {"user": user, "sid": session_key(frappe.session.sid), "endpoint": sub["endpoint"], "p256dh": sub["p256dh"],
              "auth": sub["auth"], "last_error": ""}
    if frappe.db.exists(SUBSCRIPTION, sub["key"]):
        frappe.db.set_value(SUBSCRIPTION, sub["key"], values)
    else:
        try:
            frappe.get_doc({"doctype": SUBSCRIPTION, "endpoint_key": sub["key"], **values}).insert(ignore_permissions=True)
        except (frappe.DuplicateEntryError, frappe.UniqueValidationError):  # 同一台设备的两个页签同时认领
            frappe.db.set_value(SUBSCRIPTION, sub["key"], values)
    names = frappe.get_all(SUBSCRIPTION, filters={"user": user}, order_by="modified desc, name desc", pluck="name")
    if len(names) > MAX_PER_USER:
        frappe.db.delete(SUBSCRIPTION, {"name": ["in", names[MAX_PER_USER:]]})
    return {"ok": True}


def unsubscribe(endpoint) -> None:
    """删掉当前登录人的这条订阅；别人的、不存在的都不动。"""
    user = _user()
    endpoint = str(endpoint or "").strip()
    if not endpoint or len(endpoint) > webpush.ENDPOINT_MAX:
        return
    frappe.db.delete(SUBSCRIPTION, {"name": webpush.endpoint_key(endpoint), "user": user})


def forget_session(login_manager=None) -> None:
    """`on_logout` 钩子（框架在删会话之前调）：这次登录认领的设备不再收这个人的推送。别人的、别的登录的都不动；
    删不掉只记一条日志，不挡退出。"""
    user, key = frappe.session.user, session_key(frappe.session.sid)
    if not user or user == "Guest" or not key:
        return
    try:
        frappe.db.delete(SUBSCRIPTION, {"user": user, "sid": key})
    except Exception as exc:  # noqa: BLE001 —— 退出登录不能因为这里失败
        frappe.log_error(title="Assistant push sign-out cleanup failed", message=type(exc).__name__)


def _live_sessions(user) -> set:
    """这个人现在还有效的登录会话（摘要）：退出、被登出、被踢的已删掉，过期的按框架同一条界线不算。"""
    from frappe.sessions import get_expired_threshold

    rows = frappe.db.sql("SELECT `sid` FROM `tabSessions` WHERE `user`=%s AND `lastupdate`>%s",
                         (user, get_expired_threshold()))
    return {session_key(row[0]) for row in rows} - {""}


# ---- 发出 ----

def send(user, *, title, body, url, tag, ttl=DEFAULT_TTL, urgency="high") -> dict:
    """把一条通知推到这个用户订阅过的每台设备，回各结果的台数；推送服务那边的失败不抛错。

    `url` 只能是对话页（`contracts.deep_link`）；`tag` 相同的通知在设备上互相覆盖；`ttl` 是推送服务替离线设备
    保留的秒数（0 = 不保留，设备不在线就不送）。认领它的登录已结束的设备删掉、算在 `gone` 里。"""
    result = {"sent": 0, "gone": 0, "failed": 0}
    if not user or user == "Guest" or not frappe.db.get_value("User", user, "enabled"):
        return result
    rows = frappe.get_all(SUBSCRIPTION, filters={"user": user}, fields=["name", "endpoint", "p256dh", "auth", "sid"])
    if not rows:
        return result
    # 认领它的那次登录已结束（统一登出、被踢、过期，或认领时没有登录会话）：删掉、不发
    live = _live_sessions(user)
    ended = [row.name for row in rows if row.sid not in live]
    if ended:
        frappe.db.delete(SUBSCRIPTION, {"name": ["in", ended]})
        result["gone"] += len(ended)
        rows = [row for row in rows if row.sid in live]
    if not rows:
        return result
    keys = _keys()
    if not keys:
        return result
    payload = webpush.push_payload(title, body, url, tag)
    headers = {"TTL": str(max(0, min(int(ttl), TTL_MAX))), "Content-Encoding": "aes128gcm",
               "Content-Type": "application/octet-stream", "Urgency": urgency if urgency in URGENCIES else "normal"}
    problems = []
    for row in rows:
        outcome, problem = _deliver(row, payload, headers, keys)
        result[outcome] += 1
        if problem:
            problems.append(problem)
    if problems:
        frappe.log_error(title="Assistant push failed", message="\n".join(problems))
    return result


def _deliver(row, payload: bytes, headers: dict, keys) -> tuple[str, str]:
    """发给一台设备。所有异常都在这里收住：异常日志会带各层变量，不能让推送地址、签名和密钥进去。"""
    host = ""
    try:
        host = webpush.push_host(row.endpoint)
        sealed = webpush.encrypt(payload, webpush.b64d(row.p256dh), webpush.b64d(row.auth))
        signed = {**headers, "Authorization": webpush.vapid_authorization(row.endpoint, keys[0], keys[1], SUBJECT,
                                                                           int(time.time()))}
        response = None
        for _attempt in range(2):  # 连不上推送服务时再试一次
            try:
                response = requests.post(row.endpoint, data=sealed, headers=signed, timeout=TIMEOUT,
                                         allow_redirects=False)
                break
            except requests.exceptions.ConnectionError:
                continue
        if response is None:
            return _failed(row.name, "connection failed", host)
        status = response.status_code
    except Exception as exc:  # noqa: BLE001 —— 一台失败不拦其余设备
        return _failed(row.name, type(exc).__name__, host)
    if 200 <= status < 300:
        frappe.db.set_value(SUBSCRIPTION, row.name, {"last_ok": frappe.utils.now(), "last_error": ""},
                            update_modified=False)
        return "sent", ""
    if status in (404, 410):
        frappe.db.delete(SUBSCRIPTION, {"name": row.name})
        return "gone", ""
    return _failed(row.name, f"HTTP {status}", host)


def _failed(name: str, reason: str, host: str) -> tuple[str, str]:
    note = f"{reason} {host}".strip()[:140]
    frappe.db.set_value(SUBSCRIPTION, name, "last_error", note, update_modified=False)
    return "failed", note


def send_notice(notice) -> dict:
    """提醒的推送任务（`notices.post` 提交后排队）：推给提醒的主人，点开去那段对话或定时任务页。"""
    row = frappe.db.get_value(NOTICE, notice, ["name", "user", "title", "text", "session_id", "ttl", "view"],
                              as_dict=True)
    if not row:
        return {"sent": 0, "gone": 0, "failed": 0}
    return send(row.user, title=row.title or APP_NAME, body=row.text or "",
                url=contracts.deep_link(row.session_id, row.get("view") or ""),
                tag="notice:pnotice_" + row.name, ttl=DEFAULT_TTL if row.ttl is None else int(row.ttl))
