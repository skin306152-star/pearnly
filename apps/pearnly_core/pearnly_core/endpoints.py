# -*- coding: utf-8 -*-
"""外部系统连接的服务层（共用层，三个产品都用）。

老产品的这一层散在 `services/erp/endpoint_config.py`、
`routes/erp_endpoints_routes.py` 和 `db.list_erp_endpoints()` 里；新平台收成一处。

照搬的三条语义：

1. **凭据不回显**。列表/详情接口一律不返回密码，只返回"配没配过"
   （老产品用 `strip_endpoint_for_response` + 掩码判断）。
2. **业务的写入用「有修改权限」那组账号**，操作员自己的账号只用来查重、
   匹配提成归属（老产品原话见 DocType 的字段说明）。所以取凭据时要能选"用哪一组"。
3. **停用即不参与**。`enabled=0` 的连接不能拿来推送（"同批不误投"的闸）。
"""

import contextlib

import frappe
from frappe import _
from frappe.utils.password import get_decrypted_password

ENDPOINT = "Pearnly ERP Endpoint"


def _clean(value) -> str:
    return str(value or "").strip()


@contextlib.contextmanager
def _quiet_decrypt():
    """丢掉本次解密排队的那条框架消息。

    为什么需要：Frappe 解不开密文时（站点密钥换过、密文损坏）是**先 msgprint 再抛**，
    而 `raise_exception=False` 只把异常转成 None —— 那条消息仍留在消息队列里，
    于是"只是想看一眼配没配过密码"也会在界面上弹出加密密钥的技术细节，
    同一次请求里多个字段各弹一次就会重复出现。这里只清消息队列，
    异常语义、缓存与解密行为都不变。
    """
    log = getattr(frappe.local, "message_log", None)
    if log is None:
        yield
        return
    frappe.local.message_log = []
    try:
        yield
    finally:
        frappe.local.message_log = log


def read_password(doctype: str, name: str, fieldname: str = "password") -> str:
    """读一个 Password 字段的明文；读不出来（没配过、或站点密钥对不上）返回空串。

    空串是统一的"不可用"信号：调用方据此判断这组凭据能不能用，而**不是**判断
    "配没配过"。解密失败不会抛异常，也不会在界面留下框架技术细节（见 `_quiet_decrypt`）。
    """
    with _quiet_decrypt():
        return get_decrypted_password(doctype, name, fieldname, raise_exception=False) or ""


def list_for(adapter: str | None = None, *, owner_user: str | None = None,
             enabled_only: bool = True) -> list[dict]:
    """列连接（**不含凭据**）。owner_user 给 None 表示"只列共享的那条"。"""
    filters: dict = {}
    if adapter:
        filters["adapter"] = adapter
    if enabled_only:
        filters["enabled"] = 1
    filters["owner_user"] = owner_user or ""
    rows = frappe.get_all(
        ENDPOINT,
        filters=filters,
        fields=[
            "name", "label", "adapter", "owner_user", "system_url", "enabled",
            "is_default", "auto_push", "last_used_at", "last_status",
            "success_count", "failure_count", "username", "admin_username",
        ],
        order_by="is_default desc, modified desc",
        limit_page_length=0,
    )
    for row in rows:
        row["has_credential"] = has_credential(row["name"])
        # 账号名本身不算秘密（拿它去对端登录还需要密码），但要给界面用
        row["username"] = row.get("username") or ""
        list_ok = bool(row["enabled"] and row["has_credential"])
        row["usable"] = list_ok
    return rows


def has_credential(name: str) -> bool:
    """这条连接配过密码没有。老产品也是这么判的（成对存在才算配过）。"""
    user_pw = read_password(ENDPOINT, name, "password")
    admin_pw = read_password(ENDPOINT, name, "admin_password")
    user_name = _clean(frappe.db.get_value(ENDPOINT, name, "username"))
    admin_name = _clean(frappe.db.get_value(ENDPOINT, name, "admin_username"))
    return bool((user_name and user_pw) or (admin_name and admin_pw))


def credentials(name: str, *, as_admin: bool = False) -> dict:
    """取一组可用凭据。`as_admin=True` 取"有修改权限"那组（业务写入用）。

    取不到就报错，**不回退到另一组** —— 老产品那句"需要改写客户档时由服务端继承
    老板的这组凭据"是**有意的**授权动作，静默换账号会把权限边界搞糊。
    """
    doc = frappe.get_doc(ENDPOINT, name)
    if not doc.enabled:
        frappe.throw(_("Connection \"{0}\" is disabled and cannot be used").format(doc.label))

    if as_admin:
        username = _clean(doc.admin_username)
        password = read_password(ENDPOINT, name, "admin_password")
        if not (username and password):
            frappe.throw(
                _("Connection \"{0}\" has no account with write access configured, so writes are not possible").format(doc.label)
            )
    else:
        username = _clean(doc.username)
        password = read_password(ENDPOINT, name, "password")
        if not (username and password):
            frappe.throw(_("Connection \"{0}\" has no sign-in credentials yet").format(doc.label))

    return {
        "endpoint": doc.name,
        "label": doc.label,
        "adapter": doc.adapter,
        "system_url": _clean(doc.system_url),
        "username": username,
        "password": password,
        "as_admin": bool(as_admin),
    }


def default_for(adapter: str, *, owner_user: str | None = None) -> str:
    """该适配器的默认连接名。没有默认就退到最近改过的那条；一条都没有返回空串。"""
    base = {"adapter": adapter, "enabled": 1, "owner_user": owner_user or ""}
    name = frappe.db.get_value(ENDPOINT, {**base, "is_default": 1}, "name")
    if name:
        return name
    return frappe.db.get_value(ENDPOINT, base, "name", order_by="modified desc") or ""


def mark_used(name: str, status: str) -> None:
    """记一次使用结果。老产品同样维护 last_used_at / last_status / 成功失败计数。"""
    if not name or not frappe.db.exists(ENDPOINT, name):
        return
    counter = "success_count" if status == "success" else "failure_count"
    frappe.db.set_value(
        ENDPOINT, name,
        {"last_used_at": frappe.utils.now(), "last_status": status},
        update_modified=False,
    )
    frappe.db.sql(
        f"update `tab{ENDPOINT}` set {counter} = coalesce({counter}, 0) + 1 where name = %s",
        (name,),
    )
    frappe.db.commit()


@frappe.whitelist()
def api_list_endpoints(adapter: str | None = None, owner_user: str | None = None):
    """给界面用。**不含凭据**。"""
    return {"items": list_for(adapter, owner_user=owner_user)}


@frappe.whitelist(methods=["POST"])
def api_test_connection(name: str):
    """测试连接。

    ⚠️ **现在是诚实的占位**：真正的连通性测试要对端配合（老产品调
    `/api/erp/endpoints/{id}/test-connection`，走适配器自己的登录流程）。
    新平台还**没有**任何适配器实现（DMS 客户端、登录流程都还没搬），
    所以这里只能做"配置是否齐全"的检查，**不谎报"连上了"**。
    """
    doc = frappe.get_doc(ENDPOINT, name)
    problems = []
    if not _clean(doc.system_url):
        problems.append(_("System URL is not filled in"))
    if not has_credential(name):
        problems.append(_("No sign-in credentials configured"))
    if problems:
        return {"ok": False, "checked": "config", "problems": problems,
                "note": _("Only the configuration was checked; the connection was not tested")}
    return {
        "ok": True,
        "checked": "config",
        "problems": [],
        "note": _("Configuration is complete. The connection was not tested."),
    }
