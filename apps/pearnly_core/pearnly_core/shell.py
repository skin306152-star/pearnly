# -*- coding: utf-8 -*-
"""产品壳的启动数据：前端一进来先问这个。

要回答三个问题，缺一个前端都没法正确渲染：

1. **本站点是哪个产品** —— 决定侧栏长什么样；
2. **这家租户买到了哪些功能** —— 决定侧栏显示哪些条目（ADR-005 要求前端根据后端
   返回的授权展示菜单，不能自己决定）。注意"买到的"不等于产品清单里声明的全部功能；
3. **csrf token** —— 写操作要用。Frappe 校验的是请求头与会话里存的那个值是否相等
   （`frappe/auth.py:81`），而会话里的值前端看不到，也**没有 csrf cookie** 可读
   （Frappe 不下发它），所以只能由站点自己下发。

授权清单从哪来：开通收尾时把它写进站点配置 `pearnly_features`（见 provisioning.py）。
为什么不从角色反推：角色名是给人看的显示名（"ERP 采购"），拿它反解功能码属于推断；
而开通时本来就拿着准确的功能清单，直接落盘更实在。
"""

import frappe
from frappe import _

PRODUCT_LABELS = {
    "cowork": "Cowork",
    "erp": "ERP",
    "dms": "DMS",
    "control": "Console",
}


def entitlements() -> list:
    """本站点买到的功能码。没写过就是空（控制面就是这种）。"""
    raw = frappe.conf.get("pearnly_features")
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.split(",") if part.strip()]
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(code) for code in raw if code]


@frappe.whitelist()
def describe() -> dict:
    """给前端用：这个壳是谁、买了什么、写操作要带的 csrf。"""
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("Not signed in"), frappe.AuthenticationError)

    product = str(frappe.conf.get("pearnly_product") or "").strip()
    return {
        "product": product,
        "product_label": PRODUCT_LABELS.get(product, product or "—"),
        "features": entitlements(),
        "user": user,
        "full_name": frappe.utils.get_fullname(user),
        # 站点已按 ADR-006 把租户默认语言设为泰语；这里给的是**本次请求**解析出来的
        # 语言（用户自己的偏好优先），前端拿它设 <html lang>。
        "language": frappe.local.lang or "",
        "csrf_token": frappe.sessions.get_csrf_token(),
        "native_account": bool(frappe.conf.get("pearnly_native_account")),
        # CC 对话页：本站还开不了对话的原因（没配 CC 等，已翻译），打开新对话就先显示；能开是空串
        "assistant_unavailable": _assistant_unavailable(),
    }


def _assistant_unavailable() -> str:
    from pearnly_core.assistant import config

    return config.unavailable()
