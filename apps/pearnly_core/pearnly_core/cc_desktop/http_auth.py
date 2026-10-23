"""CC 桌面版共用机器通道的机器认证（多产品共用，ADR-025）：只放行精确的几个 POST 方法，从不给通用 Frappe 接口。

做法照 Cowork `companion.http_auth`（同一套审过的边界）：
- ``before_request``（早于 Frappe 的 OAuth 校验）认出机器请求：路径是下面 :data:`METHODS` 之一、``Authorization: Bearer pcc_…``、
  不带会话 Cookie / sid、只接 POST、请求体是 JSON 对象；收走令牌（不留在 Frappe 后面任何一帧里）。
- ``auth_hooks`` 按令牌摘要认电脑（`devices.authenticate`：没移除、签发人此刻仍能用），切到签发人身份，清掉会话与 Cookie。
  被路由改写（override / 服务端脚本）的方法不继承这个身份。
- ``after_request`` 去掉 Set-Cookie、禁止缓存；身份服务的登录跳转改成 401（机器令牌从不跟着跳去登录）。
- 出错一律不带内部信息与堆栈。

前缀 ``pcc_`` 与 Cowork 的 ``exp_`` 互不相认：Cowork 站点上两套钩子都装着，各认各的路径与前缀，互不干扰。
"""
from copy import deepcopy
from dataclasses import dataclass
import json
import re

import frappe
from frappe import _
from frappe.recorder import do_not_record

from pearnly_core.cc_desktop import contracts, devices, products

_PREFIX = "pearnly_core.cc_desktop.machine."
METHODS = {"heartbeat": _PREFIX + "heartbeat", "lease": _PREFIX + "lease", "ack": _PREFIX + "acknowledge",
           "request_next": _PREFIX + "next_request", "request_complete": _PREFIX + "complete_request",
           "leave": _PREFIX + "leave"}
PATHS = {operation: "/api/method/" + method for operation, method in METHODS.items()}
_HEADER = re.compile(r"(?i:Bearer) (pcc_[A-Za-z0-9._-]+)")
_AUTHORITY = object()


class RequestContractError(ValueError):
    pass


class _Busy(frappe.AuthenticationError):
    http_status_code = 503


class _Forbidden(frappe.AuthenticationError):
    http_status_code = 403


class _BadRequest(frappe.AuthenticationError):
    http_status_code = 400


@dataclass(frozen=True, repr=False)
class _Pending:
    token: str
    request: object
    payload: dict
    operation: str
    authority: object


@dataclass(frozen=True, repr=False)
class _Bound:
    context: object
    request: object
    payload: dict
    operation: str
    authority: object


def _request():
    return getattr(frappe.local, "request", None)


def _candidate(request):
    if not request:
        return False
    path, header = request.path, request.headers.get("Authorization", "")
    return (any(path.startswith(prefix + method) for prefix in ("/api/method/", "/api/v1/method/", "/api/v2/method/")
                for method in METHODS.values())
            or bool(re.search(r"pcc_", header, flags=re.IGNORECASE))
            or frappe.local.form_dict.get("cmd") in METHODS.values())


@do_not_record
def _discard_recording():
    """原生的单次请求不录（Recorder 设置本身不动）。"""


def clear_private_messages():
    frappe.local.message_log = []
    frappe.flags.pop("error_message", None)


def _private():
    frappe.flags.disable_traceback = True
    clear_private_messages()


def _deny():
    _private()
    frappe.throw(_("cc_desktop.unauthorized"), frappe.AuthenticationError)


def deny_permission():
    _private()
    raise _Forbidden(_("cc_desktop.unauthorized")) from None


def reject_request(code="cc_desktop.request_invalid"):
    _discard_recording()
    _private()
    raise _BadRequest(_(code)) from None


def raise_busy(error, *, stage):
    _discard_recording()
    _private()
    frappe.logger("cc_desktop", allow_site=True).warning(
        "cc_desktop.busy stage=%s exception_type=%s", stage, type(error).__name__)
    raise _Busy(_("cc_desktop.busy")) from None


def _payload(request):
    if request.get_data() and request.is_json:
        return request.get_json(silent=True)
    if request.get_data():
        _deny()
    return {}


def _envelope(request, payload):
    match = _HEADER.fullmatch(request.headers.get("Authorization", ""))
    if (request.path not in PATHS.values() or request.method != "POST" or match is None
            or contracts.parse_token(match.group(1)) is None
            or frappe.session.user not in ("", "Guest", None)
            or "sid" in request.cookies or request.headers.get("Frappe-Authorization-Source")
            or not isinstance(payload, dict)
            or any(key in values for values in (request.args, request.form, payload) for key in ("cmd", "sid"))):
        raise RequestContractError()
    return match.group(1)


def before_login(login_manager=None):
    """机器请求冒充 cmd=login：在密码校验、建会话之前就拒。"""
    if _candidate(_request()):
        _discard_recording()
        _deny()


def before_request():
    request = _request()
    if not _candidate(request):
        return
    try:
        _discard_recording()
        payload = _payload(request)
        try:
            token = _envelope(request, payload)
        except RequestContractError:
            _deny()
        operation = next(op for op, path in PATHS.items() if path == request.path)
        frappe.local.cc_desktop_http_envelope = _Pending(token, request, deepcopy(payload), operation, _AUTHORITY)
        request.environ.pop("HTTP_AUTHORIZATION", None)
    except frappe.AuthenticationError:
        raise
    except Exception as error:
        raise_busy(error, stage="envelope")


def authenticate_request():
    request = _request()
    if not _candidate(request):
        return
    try:
        return _authenticate(request)
    except frappe.AuthenticationError:
        raise
    except frappe.PermissionError:
        deny_permission()
    except Exception as error:
        raise_busy(error, stage="authenticate")
    finally:
        frappe.local.cc_desktop_http_envelope = None


def _authenticate(request):
    _discard_recording()
    pending = getattr(frappe.local, "cc_desktop_http_envelope", None)
    if (not isinstance(pending, _Pending) or pending.authority is not _AUTHORITY or pending.request is not request
            or request.path != PATHS.get(pending.operation) or request.method != "POST"
            or request.headers.get("Authorization") or "sid" in request.cookies
            or frappe.session.user not in ("", "Guest", None)
            or any(key in request.args or key in request.form for key in ("cmd", "sid"))):
        _deny()
    method = METHODS[pending.operation]
    from frappe.core.doctype.server_script.server_script_utils import get_server_script_map
    if frappe.override_whitelisted_method(method) != method or method in get_server_script_map().get("_api", {}):
        _deny()
    if not products.enabled():
        deny_permission()
    context = devices.authenticate(pending.token)
    body = deepcopy(pending.payload)
    frappe.local.session = frappe._dict(user="Guest", sid="Guest", data=frappe._dict())
    frappe.local.session_obj = None
    frappe.set_user(context.issuer)
    frappe.local.form_dict = frappe._dict(deepcopy(body))
    frappe.local.cc_desktop_http_context = _Bound(context, request, body, pending.operation, _AUTHORITY)
    _clear_cookies()


def current_request(operation):
    """只有认证过的这一个精确请求能拿到电脑身份与请求体副本。"""
    bound = getattr(frappe.local, "cc_desktop_http_context", None)
    request = _request()
    if (not isinstance(bound, _Bound) or bound.authority is not _AUTHORITY or bound.operation != operation
            or request is not bound.request or request.path != PATHS[operation] or request.method != "POST"
            or frappe.local.site != bound.context.site or frappe.session.user != bound.context.issuer
            or any(key in request.args or key in request.form for key in ("cmd", "sid"))):
        _deny()
    return bound.context, deepcopy(bound.payload)


def _clear_cookies():
    manager = getattr(frappe.local, "cookie_manager", None)
    if manager:
        manager.cookies.clear()
        manager.to_delete.clear()


def after_request(request=None, response=None):
    if not _candidate(request or _request()):
        return
    _discard_recording()
    _clear_cookies()
    if response is not None:
        if 300 <= response.status_code < 400:
            response.status_code = 401
            response.headers.remove("Location")
            response.set_data(json.dumps({"exc_type": "AuthenticationError"}))
            response.mimetype = "application/json"
        response.headers.remove("Set-Cookie")
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
