# -*- coding: utf-8 -*-
"""复用 DMS 登录会话：登一次，后面的操作接着用（2026-09-27）。

## 为什么

每次操作都"开浏览器 → 登录 → 干活 → 登出"。登录本身在生产上实测 6–16 秒（几乎全花在
新连接的 TLS 握手上），比真正的业务请求（每个 0.07–0.5 秒）贵一两个数量级；而同一个账号
同时只能有一处登录，于是所有操作员在账号锁上排队时，排的主要是别人的**登录时间**。

2026-09-27 在 DMS-TEST 实测：会话只靠一个 `PHPSESSID` cookie；把它放回新的浏览器上下文
（甚至普通 HTTP 请求）照样是已登录状态，闲置数分钟后依然有效。

## 怎么做

* 操作结束时**不登出**，把本账号的 cookie 加密存进 Redis（按账号分键，键名不含账号原文）。
* 下一次同账号操作先把 cookie 放回新上下文，用一个只读计数请求确认会话还在（约 0.1 秒）；
  在就跳过登录，不在就清掉缓存、照原流程登录。
* 仍然在**账号锁里**使用：同一时刻一个账号只有一个操作拿着这份会话，与原来一样串行。

## 边界（如实写下）

* 会话被人顶掉（有人用同一账号登录网页）时，缓存会在下一次存活检查里失效，回到原流程；
  那一次登录照样会把网页上的人顶下线 —— 这与改动前每次都登录的行为相同。
* 缓存期间账号保持"已登录"。人如果同时在网页上用同一个账号，会看到重复登录提示。
  所以**管理员/同步账号应只给系统用**；闲置多久作废由 `pearnly_dms_session_idle_seconds`
  控制，`0` 表示关闭复用（完全回到每次登录、每次登出）。
* cookie 等同于这段时间内的登录能力：只存加密值、带过期时间，出错一律丢弃重登。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from typing import Any, List, Optional
from urllib.parse import urljoin, urlsplit

import frappe

logger = logging.getLogger(__name__)

#: 闲置多久作废（秒）。DMS-TEST 实测会话闲置数分钟仍有效；缓存按"最后一次使用"续期，
#: 取保守值，宁可多登一次也不拿一份刚过期的会话去干活（存活检查会兜住，但白花一个请求）。
DEFAULT_IDLE_SECONDS = 600
#: 销售账号是员工本人的账号，他可能同时在 DMS 网页上用：只保留够一串连续 LINE 操作的时间
#: （查重 → 确认 → 订车逐问 → 建单），别让他在网页上老撞见"重复登录"。
DEFAULT_SALES_IDLE_SECONDS = 180
_KEY_PREFIX = "pearnly:dms:session:"
_KICKED_PREFIX = "pearnly:dms:session-kicked:"
#: 会话被顶掉之后，后台同步多久不再为自己去登录（秒）：有人正在网页上用这个账号。
KICK_BACKOFF_SECONDS = 900
#: 存活检查：客户列表的计数请求。登录态回 `dt::N`（或 `ndt::`），未登录回登录页/错误。
_PROBE_PATH = "cus/component/showdata.php"
_PROBE_DATA = {"sd": "", "selcolsort": "1", "selcolsorttype": "1"}
_ALIVE = re.compile(r"^\s*(dt::\d+|ndt::)")

def _state_store():
    """不淘汰的状态存储（``pearnly_core.execution.state``；用到时才导入，离线检查导入本模块不需要共用层）。"""
    from pearnly_core.execution import state as pearnly_state

    return pearnly_state.store()



def idle_seconds() -> int:
    try:
        value = int(frappe.conf.get("pearnly_dms_session_idle_seconds", DEFAULT_IDLE_SECONDS))
    except (TypeError, ValueError):
        return DEFAULT_IDLE_SECONDS
    return max(0, value)


def sales_idle_seconds() -> int:
    """销售账号的保留时长（`pearnly_dms_sales_session_idle_seconds`），不超过总开关。"""
    try:
        value = int(frappe.conf.get("pearnly_dms_sales_session_idle_seconds", DEFAULT_SALES_IDLE_SECONDS))
    except (TypeError, ValueError):
        value = DEFAULT_SALES_IDLE_SECONDS
    return max(0, min(value, idle_seconds()))


def enabled() -> bool:
    return idle_seconds() > 0


def _key(account_key: str) -> str:
    return _KEY_PREFIX + hashlib.sha256(account_key.encode("utf-8")).hexdigest()[:32]


def _same_site(cookie: dict, base_url: str) -> bool:
    host = (urlsplit(base_url).hostname or "").lower()
    domain = str(cookie.get("domain") or "").lstrip(".").lower()
    return bool(host) and (host == domain or host.endswith("." + domain))


def load(account_key: str) -> Optional[List[dict]]:
    """取缓存的 cookie；没有、过期、解不开一律返回 None。"""
    if not enabled():
        return None
    try:
        raw = frappe.cache().get_value(_key(account_key))
        if not raw:
            return None
        from frappe.utils.password import decrypt

        data = json.loads(decrypt(raw))
        limit = min(int(data.get("idle") or idle_seconds()), idle_seconds())
        if time.time() - float(data.get("used_at") or 0) > limit:
            forget(account_key)
            return None
        cookies = data.get("cookies")
        return cookies if isinstance(cookies, list) and cookies else None
    except Exception:
        logger.warning("[dms session] cached session unreadable; signing in normally")
        forget(account_key)
        return None


def save(account_key: str, cookies: List[dict], base_url: str, *, idle: Optional[int] = None) -> bool:
    """只存本 DMS 站点的 cookie；存不进去不影响业务（下次照常登录）。

    `idle`：这份会话最多闲置多久（秒），默认用总开关；销售账号传 `sales_idle_seconds()`。
    """
    limit = idle_seconds() if idle is None else min(max(0, int(idle)), idle_seconds())
    if limit <= 0:
        return False
    kept = [c for c in cookies or [] if _same_site(c, base_url)]
    if not kept:
        return False
    try:
        from frappe.utils.password import encrypt

        payload = encrypt(json.dumps({"cookies": kept, "used_at": time.time(), "idle": limit}))
        frappe.cache().set_value(_key(account_key), payload, expires_in_sec=limit)
        return True
    except Exception:
        logger.warning("[dms session] could not cache the session; next run signs in")
        return False


def forget(account_key: str) -> None:
    try:
        frappe.cache().delete_value(_key(account_key))
    except Exception:
        logger.warning("[dms session] could not drop the cached session (it will expire)")


def alive(request_context: Any, base_url: str) -> bool:
    """用一个只读计数请求确认会话仍是登录态。任何异常都当作"不在"。"""
    try:
        resp = request_context.post(urljoin(base_url, _PROBE_PATH), form=_PROBE_DATA, timeout=15000)
        body = resp.body().decode("utf-8", "replace")
        return resp.status == 200 and bool(_ALIVE.match(body))
    except Exception:
        return False


def restore(context: Any, account_key: str, base_url: str) -> bool:
    """把缓存的会话放回浏览器上下文并确认可用。成功返回 True；否则清干净返回 False。"""
    cookies = load(account_key)
    if not cookies:
        return False
    try:
        context.add_cookies(cookies)
        if alive(context.request, base_url):
            return True
        context.clear_cookies()
        # 还在闲置期限内却已失效 = 有人用同一账号登录顶掉了它（或对方重启过会话）。
        _mark_kicked(account_key)
    except Exception:
        logger.warning("[dms session] session reuse failed; signing in again")
        try:
            context.clear_cookies()
        except Exception:
            pass
    forget(account_key)
    return False


def _mark_kicked(account_key: str) -> None:
    try:
        _state_store().set_value(_KICKED_PREFIX + _key(account_key)[len(_KEY_PREFIX):], "1",
                                 expires_in_sec=KICK_BACKOFF_SECONDS)
    except Exception:
        pass


def recently_kicked(account_key: str) -> bool:
    """这个账号的缓存会话最近被人顶掉过吗（后台任务据此暂不为自己去登录）。"""
    try:
        return bool(_state_store().get_value(_KICKED_PREFIX + _key(account_key)[len(_KEY_PREFIX):]))
    except Exception:
        return False
