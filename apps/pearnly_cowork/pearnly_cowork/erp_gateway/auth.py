"""网关的机器身份：只认站点配置里登记的 ERP 站点令牌，验过后以网关服务账号的身份做事（录入权限与连接授权照网页判）。

站点配置 ``pearnly_erp_gateway``::

    {"user": "<网关服务账号，Cowork Intake 角色、在 Express 主连接的使用名单里>",
     "clients": {"<ERP 站点>": "<令牌的 sha256 十六进制>"}}

请求头 ``X-Pearnly-Gateway-Client`` / ``X-Pearnly-Gateway-Token``。只接 POST、只接没有登录会话的请求；令牌原文不落日志、
不进响应。身份切换照 `companion.http_auth`：先换掉会话对象再 ``set_user``，这次请求不能更新任何浏览器会话。
"""
import hashlib
import hmac
import json

import frappe

from pearnly_cowork.erp_gateway.contracts import GatewayError

CONFIG = "pearnly_erp_gateway"
CLIENT_HEADER = "X-Pearnly-Gateway-Client"
TOKEN_HEADER = "X-Pearnly-Gateway-Token"
BODY_MAX = 4 * 1024 * 1024


def digest(token: str) -> str:
    return hashlib.sha256(str(token).encode()).hexdigest()


def _settings():
    found = frappe.conf.get(CONFIG)
    if not isinstance(found, dict) or not isinstance(found.get("clients"), dict) or not found.get("user"):
        return None
    return found


def _refuse(status):
    frappe.local.response.http_status_code = status
    return {"ok": 0}


def _clear_cookies():
    manager = getattr(frappe.local, "cookie_manager", None)
    if manager:
        manager.cookies.clear()
        manager.to_delete.clear()


def run(handler):
    """验身份 → 读 JSON → 以网关服务账号执行 ``handler(client, body)`` → 提交。回 ``{"ok": 1, ...}`` 或 ``{"ok": 0, "code"}``。"""
    settings = _settings()
    request = frappe.request
    if settings is None:
        return _refuse(404)
    if (request.method != "POST" or frappe.session.user not in ("Guest", None, "")
            or "sid" in request.cookies or request.headers.get("Authorization")):
        return _refuse(403)
    client = str(request.headers.get(CLIENT_HEADER) or "")
    token = str(request.headers.get(TOKEN_HEADER) or "")
    expected = settings["clients"].get(client) if client else None
    if not (isinstance(expected, str) and token and hmac.compare_digest(digest(token), expected)):
        return _refuse(401)
    raw = request.get_data() or b""
    if len(raw) > BODY_MAX:
        return _refuse(413)
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        return {"ok": 0, "code": "erp_gateway.body_invalid"}
    frappe.local.session = frappe._dict(user="Guest", sid="Guest", data=frappe._dict())
    frappe.local.session_obj = None
    frappe.set_user(settings["user"])
    _clear_cookies()
    try:
        result = handler(client, body)
        frappe.db.commit()
        return {"ok": 1, **result}
    except GatewayError as error:
        frappe.db.rollback()
        return {"ok": 0, "code": error.code}
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return {"ok": 0, "code": str(error)[:200] or type(error).__name__}
    finally:
        _clear_cookies()
