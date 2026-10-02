# -*- coding: utf-8 -*-
"""LINE 用户与本地账号的绑定（共用层，三个产品都用）。

与旧系统的关键区别：旧系统按产品共享一套渠道，所以绑定记录里必须自带 tenant_id
来区分是谁家的；新模型里每个租户一个站点，绑定记录天然只属于本站点 ——
少一个字段，也少一类写错的机会（跨租户串号在结构上就不可能）。

绑定流程（照旧系统已验证的语义）：
1. 网页端调用 generate_bind_code(user) 拿到一个一次性短码（有有效期）；
2. 用户把这个码发给该租户的 LINE 官方号；
3. webhook 收到文本，consume_bind_code(code) 一次性消费掉并建绑定；
4. 之后该 LINE 用户的消息都按这个绑定找本地账号。

一次性消费是硬要求：码被用掉就作废，避免猜码或转发复用。
"""

import secrets
import hashlib

import frappe
from frappe import _
from frappe.utils import add_to_date, now
from pearnly_core.execution import state as pearnly_state

BINDING = "Pearnly LINE Binding"
BIND_CODE = "Pearnly LINE Bind Code"

# Six digits are kept as text so leading zeroes survive manual entry.
CODE_ALPHABET = "0123456789"
CODE_LENGTH = 6
DEFAULT_TTL_MINUTES = 30


def generate_bind_code(user: str, ttl_minutes: int = DEFAULT_TTL_MINUTES) -> dict:
    """给某个本地账号生成一次性绑定码。旧的未用码同时作废（一人同时只有一个有效码）。"""
    # This function already owns a committed transaction. Retry its local-only
    # work after MariaDB snapshot conflicts; callers must not batch other writes.
    # Allocation and revocation share one short site-scoped critical section.
    # This also avoids InnoDB next-key lock cycles between fresh user ranges.
    with pearnly_state.store().lock(pearnly_state.store().make_key("pearnly_bind_allocation"), timeout=30, blocking_timeout=10):
        for attempt in range(3):
            try:
                return _generate_bind_code(user, ttl_minutes)
            except frappe.QueryDeadlockError:
                frappe.db.rollback()
                if attempt == 2:
                    raise


def _generate_bind_code(user, ttl_minutes):
    rows = frappe.db.sql("select name, enabled from `tabUser` where name=%s for update", (user,), as_dict=True)
    if not rows:
        frappe.throw(_("No such local account: {0}").format(user))
    if not rows[0].enabled:
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    frappe.db.delete(BIND_CODE, {"user": user, "used": 0})

    # The primary key is the allocation lock: an existence check alone races
    # when different operators request codes concurrently.
    for attempt in range(20):
        code = "".join(secrets.choice(CODE_ALPHABET) for digit in range(CODE_LENGTH))
        frappe.db.savepoint("bind_code_candidate")
        doc = frappe.get_doc({
            "doctype": BIND_CODE,
            "code": code,
            "user": user,
            "expires_at": add_to_date(now(), minutes=ttl_minutes),
            "used": 0,
        })
        try:
            doc.insert(ignore_permissions=True, set_name=code)
            break
        except frappe.DuplicateEntryError:
            frappe.db.rollback(save_point="bind_code_candidate")
    else:
        frappe.throw(_("Could not generate a bind code; please try again"))

    frappe.db.commit()
    # ⚠️ **一定要给 `expires_in`（剩余秒数）**，界面别去解析 `expires_at` 那个字符串。
    #
    # 为什么：`expires_at` 是**站点时区的裸时间**（例如站点时区是 Asia/Kolkata 时就是
    # IST），而浏览器按**自己所在时区**解析同一串字符。两边不一致时算出来的
    # "还能用多久"就是错的 —— 实测：站点时区 IST、浏览器 +07，刚生成的码立刻显示
    # "已过期"。秒数没有时区问题，倒计时只认它。
    return {
        "code": code,
        "expires_at": str(doc.expires_at),
        "expires_in": int(ttl_minutes) * 60,
        "ttl_minutes": ttl_minutes,
    }


def consume_bind_code(code: str, *, line_user_id: str = ""):
    """一次性消费绑定码。过期、已用、不存在都返回 None。"""
    code = (code or "").strip().upper()
    # During rollout, already-issued six-character codes remain valid until expiry.
    if len(code) != CODE_LENGTH or not code.isascii() or not code.isalnum():
        return None
    if not line_user_id:
        return None
    # Signed webhook supplies the sender. Atomic, site-scoped fixed window prevents
    # the shorter human-friendly code from becoming an unlimited guessing oracle.
    actor = hashlib.sha256(line_user_id.encode()).hexdigest()
    key = pearnly_state.store().make_key("pearnly_bind_attempts:" + actor)
    attempts = pearnly_state.store().eval(
        "local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],900) end; return n",
        1, key,
    )
    if attempts > 5:
        return None
    rows = frappe.db.sql(
        "select user, used, expires_at from `tabPearnly LINE Bind Code` "
        "where name=%s for update", (code,), as_dict=True,
    )
    if not rows:
        return None
    doc = rows[0]
    if doc.used:
        return None
    if str(doc.expires_at) < str(now()):
        return None

    if not frappe.db.get_value("User", doc.user, "enabled"):
        return None
    # 行锁覆盖读与写；另一个请求必须在提交后重新读取 used。
    frappe.db.set_value(BIND_CODE, code, {"used": 1, "used_at": now()})
    frappe.db.commit()
    pearnly_state.store().delete(key)
    return {"user": doc.user}


def bind(line_user_id: str, user: str, display_name: str = "") -> str:
    """一位员工对应一个有效 LINE 账号；换绑同时收回旧账号。"""
    if not line_user_id or not user:
        frappe.throw(_("Binding needs a LINE user and a local account"))
    if not frappe.db.exists("User", user):
        frappe.throw(_("No such local account: {0}").format(user))
    frappe.db.sql("select name from `tabUser` where name=%s for update", (user,))
    prior = frappe.db.sql(
        "select name, line_user_id from `tabPearnly LINE Binding` "
        "where user=%s and status='active' and line_user_id<>%s for update",
        (user, line_user_id), as_dict=True,
    )
    for row in prior:
        frappe.db.set_value(BINDING, row.name, {"status": "unbound", "unbound_at": now()})
        _notify_changed(row.line_user_id)
    name = frappe.db.get_value(BINDING, {"line_user_id": line_user_id}, "name")
    if name:
        frappe.db.set_value(BINDING, name, {
            "user": user,
            "status": "active",
            "display_name": display_name or "",
            "bound_at": now(),
        })
    else:
        frappe.get_doc({
            "doctype": BINDING,
            "line_user_id": line_user_id,
            "user": user,
            "display_name": display_name or "",
            "status": "active",
            "bound_at": now(),
        }).insert(ignore_permissions=True)
    _notify_changed(line_user_id)
    frappe.db.commit()
    return user


def binding_of(line_user_id: str):
    """按 LINE 用户查绑定。没绑定或已解绑都返回 None。"""
    row = frappe.db.get_value(
        BINDING,
        {"line_user_id": line_user_id, "status": "active"},
        ["name", "user", "display_name", "modified", "bound_at"],
        as_dict=True,
    )
    if row and frappe.db.get_value("User", row.user, "enabled"):
        return row
    return None


def user_of(line_user_id: str):
    row = binding_of(line_user_id)
    return row.user if row else None


def unbind(line_user_id: str) -> bool:
    name = frappe.db.get_value(BINDING, {"line_user_id": line_user_id}, "name")
    if not name:
        return False
    frappe.db.set_value(BINDING, name, {"status": "unbound", "unbound_at": now()})
    _notify_changed(line_user_id)
    frappe.db.commit()
    return True


def _notify_changed(line_user_id):
    for handler in frappe.get_hooks("pearnly_line_binding_changed"):
        frappe.get_attr(handler)(line_user_id)

# 网页端**没有**"给我自己发绑定码"的接口，这是有意的：
# 老产品的绑定码只从**操作员花名册的某一行**发出（见 `pearnly_dms/operators/roster.py` 的
# `api_operator_bind_code`），因为绑定的对象是"某个操作员"，不是"当前登录的人"。
# 曾经按自己理解的"独立绑定码页"写过一对 api_my_bindings / api_generate_bind_code，
# 后来对照老产品发现那一页并不存在，就删了 —— 多出来的接口就是多出来的漏洞面。
