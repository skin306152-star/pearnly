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

import frappe
from frappe import _
from frappe.utils import add_to_date, now

BINDING = "Pearnly LINE Binding"
BIND_CODE = "Pearnly LINE Bind Code"

# 去掉容易看错的字符（0 与 O、1 与 I 与 l），因为用户要手打或口述这个码
CODE_ALPHABET = "ACDEFGHJKLMNPQRSTUVWXY23456789"
CODE_LENGTH = 6
DEFAULT_TTL_MINUTES = 30


def generate_bind_code(user: str, ttl_minutes: int = DEFAULT_TTL_MINUTES) -> dict:
    """给某个本地账号生成一次性绑定码。旧的未用码同时作废（一人同时只有一个有效码）。"""
    if not frappe.db.exists("User", user):
        frappe.throw(_("本地账号不存在：{0}").format(user))

    frappe.db.delete(BIND_CODE, {"user": user, "used": 0})

    code = ""
    for _ in range(20):
        candidate = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        if not frappe.db.exists(BIND_CODE, candidate):
            code = candidate
            break
    if not code:
        frappe.throw(_("生成绑定码失败，请重试"))

    doc = frappe.get_doc({
        "doctype": BIND_CODE,
        "code": code,
        "user": user,
        "expires_at": add_to_date(now(), minutes=ttl_minutes),
        "used": 0,
    })
    doc.insert(ignore_permissions=True, set_name=code)
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


def consume_bind_code(code: str):
    """一次性消费绑定码。过期、已用、不存在都返回 None。"""
    code = (code or "").strip().upper()
    if not code or not frappe.db.exists(BIND_CODE, code):
        return None

    doc = frappe.get_doc(BIND_CODE, code)
    if doc.used:
        return None
    if str(doc.expires_at) < str(now()):
        return None

    # 先标记已用再返回：并发下只有一个能拿到
    frappe.db.set_value(BIND_CODE, code, {"used": 1, "used_at": now()})
    frappe.db.commit()
    return {"user": doc.user}


def bind(line_user_id: str, user: str, display_name: str = "") -> str:
    """建或更新绑定。已存在就更新，不重复建（幂等）。"""
    if not line_user_id or not user:
        frappe.throw(_("绑定需要 LINE 用户与本地账号"))
    if not frappe.db.exists("User", user):
        frappe.throw(_("本地账号不存在：{0}").format(user))

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
    frappe.db.commit()
    return user


def binding_of(line_user_id: str):
    """按 LINE 用户查绑定。没绑定或已解绑都返回 None。"""
    return frappe.db.get_value(
        BINDING,
        {"line_user_id": line_user_id, "status": "active"},
        ["name", "user", "display_name"],
        as_dict=True,
    )


def user_of(line_user_id: str):
    row = binding_of(line_user_id)
    return row.user if row else None


def unbind(line_user_id: str) -> bool:
    name = frappe.db.get_value(BINDING, {"line_user_id": line_user_id}, "name")
    if not name:
        return False
    frappe.db.set_value(BINDING, name, {"status": "unbound", "unbound_at": now()})
    frappe.db.commit()
    return True

# 网页端**没有**"给我自己发绑定码"的接口，这是有意的：
# 老产品的绑定码只从**操作员花名册的某一行**发出（见 `pearnly_dms/roster.py` 的
# `api_operator_bind_code`），因为绑定的对象是"某个操作员"，不是"当前登录的人"。
# 曾经按自己理解的"独立绑定码页"写过一对 api_my_bindings / api_generate_bind_code，
# 后来对照老产品发现那一页并不存在，就删了 —— 多出来的接口就是多出来的漏洞面。
