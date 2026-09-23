"""Exact machine RPC authentication; never grants general Frappe API access.

The before_login guard is earlier than auth_hooks in Frappe 16.33. Session
objects are detached before set_user so this request cannot update a browser
session or the shared Guest cache. No credentials or identity come from body.
"""
from copy import deepcopy
from dataclasses import dataclass
import json
import re

import frappe
from frappe import _
from frappe.recorder import do_not_record

from . import credentials, queue_contracts, receipt_auth
from .contracts import AgentContext, parse_token

METHOD = 'pearnly_cowork.companion.heartbeat.report'
PATH = '/api/method/' + METHOD
METHODS = {'heartbeat': METHOD, 'lease': 'pearnly_cowork.companion.queue_api.lease',
           'ack': 'pearnly_cowork.companion.queue_api.acknowledge'}
PATHS = {operation: '/api/method/' + method for operation, method in METHODS.items()}
_AUTHORITY = object()
_HEADER = re.compile(r'(?i:Bearer) (exp_[A-Za-z0-9._-]+)')


class RequestContractError(ValueError):
    pass


def _machine_candidate(path, header, command=None):
    return (any(path.startswith(prefix + method) for prefix in
                ('/api/method/', '/api/v1/method/', '/api/v2/method/') for method in METHODS.values())
            or bool(re.search(r'exp_', header, flags=re.IGNORECASE))
            or command in METHODS.values())


def _envelope(*, path, method, header, cookies, query, form, payload,
              session_user, authorization_source=None):
    """Pure input boundary; run before identity and again at the handler seam."""
    match = _HEADER.fullmatch(header)
    if (path not in PATHS.values() or method != 'POST' or match is None
            or parse_token(match.group(1)) is None
            or session_user not in ('', 'Guest', None)
            or 'sid' in cookies or authorization_source
            or not isinstance(payload, dict)
            or any(key in values for values in (query, form, payload) for key in ('cmd', 'sid'))):
        raise RequestContractError('companion.unauthorized')
    return match.group(1)


def _deny():
    _private_error()
    frappe.throw(_('companion.unauthorized'), frappe.AuthenticationError)


def _private_error():
    """Replace all native error-message channels before returning a safe error.

    frappe.throw queues a message before raising; changing exception types alone
    leaves that message in the v1 _server_messages response, even with traceback
    disabled. No internal diagnostic or attempt secret belongs on this wire.
    """
    frappe.local.cowork_private_response = True
    frappe.flags.disable_traceback = True
    clear_private_messages()


def clear_private_messages():
    """Machine responses carry their DTO, never a preceding internal message."""
    frappe.local.message_log = []
    frappe.flags.pop('error_message', None)


def _request():
    return getattr(frappe.local, 'request', None)


def _candidate(request):
    return bool(request and _machine_candidate(request.path,
        request.headers.get('Authorization', ''), frappe.local.form_dict.get('cmd')))


def _payload(request):
    # Frappe has already rejected invalid JSON and non-object/scalar bodies.
    # Read the raw cached body rather than form_dict: Session consumed sid and
    # set_user clears form_dict. A parsed JSON list is not a report object.
    if request.get_data() and request.is_json:
        return request.get_json(silent=True)
    if request.get_data():
        _deny()
    return {}


def _validated(request):
    if not request:
        _deny()
    payload = _payload(request)
    try:
        token = _envelope(path=request.path, method=request.method,
            header=request.headers.get('Authorization', ''), cookies=request.cookies,
            query=request.args, form=request.form, payload=payload,
            session_user=frappe.session.user,
            authorization_source=request.headers.get('Frappe-Authorization-Source'))
    except RequestContractError:
        _deny()
    return token, payload


@do_not_record
def _discard_recording():
    """Native per-request opt-out; leaves the global Recorder setting intact."""


def before_login(login_manager=None):
    """Deny cmd=login before password checks, session creation or login hooks."""
    if _candidate(_request()):
        _discard_recording()
        _deny()


def before_request():
    request = _request()
    if _candidate(request):
        try:
            _discard_recording()
            frappe.local.cowork_private_response = True
            token, payload = _validated(request)
            # Core validate_auth runs OAuth before custom hooks and IP validation
            # after them. Do not leave the raw machine token in either Core frame.
            # EnvironHeaders reads this mapping live; ordinary requests never enter.
            frappe.local.cowork_companion_http_envelope = _PendingRequest(
                token, request, deepcopy(payload),
                next(operation for operation, path in PATHS.items() if path == request.path), _AUTHORITY)
            request.environ.pop('HTTP_AUTHORIZATION', None)
        except frappe.AuthenticationError:
            raise
        except frappe.PermissionError:
            _deny_permission()
        except Exception as error:
            raise_busy(error, stage='envelope')


@dataclass(frozen=True, repr=False)
class _PendingRequest:
    token: str
    request: object
    payload: dict
    operation: str
    authority: object


@dataclass(frozen=True, repr=False)
class _BoundRequest:
    context: object
    request: object
    payload: dict
    operation: str
    authority: object


class _ReportBusy(frappe.AuthenticationError):
    # Frappe excludes AuthenticationError subclasses from Error Log snapshots.
    # Keep retryable 503 semantics without recording validate_auth's plaintext
    # authorization_header local in a with-context traceback.
    http_status_code = 503


class _MachinePermission(frappe.AuthenticationError):
    # Ordinary PermissionError is snapshot-logged in developer mode. Keep the
    # machine's 403 semantics without an authentication-frame Error Log.
    http_status_code = 403


class _MachineRequest(frappe.AuthenticationError):
    http_status_code = 400


class _MachineConflict(frappe.AuthenticationError):
    http_status_code = 409


def reject_request(code='companion.queue_request_invalid', *, conflict=False):
    """Do not snapshot receipt frames, which may contain an attempt secret."""
    _discard_recording()
    _private_error()
    error_type = _MachineConflict if conflict else _MachineRequest
    raise error_type(_(code)) from None


def _deny_permission():
    _private_error()
    raise _MachinePermission(_('companion.unauthorized')) from None


def raise_busy(error, *, stage):
    """Safe diagnostics for a local authentication/write availability failure."""
    _discard_recording()
    _private_error()
    frappe.logger('cowork_companion', allow_site=True).warning(
        'companion.report_busy stage=%s exception_type=%s', stage, type(error).__name__)
    raise _ReportBusy(_('companion.report_busy')) from None


def authenticate_request():
    request = _request()
    if not _candidate(request):
        return
    try:
        return _authenticate_request(request)
    except frappe.AuthenticationError:
        raise
    except frappe.PermissionError:
        _deny_permission()
    except Exception as error:
        # Includes route selection and request-local binding, not only the DB
        # lookup. None of those unexpected failures may expose auth frame data.
        raise_busy(error, stage='authenticate')
    finally:
        frappe.local.cowork_companion_http_envelope = None


def _authenticate_request(request):
    _discard_recording()
    frappe.local.cowork_private_response = True
    pending = getattr(frappe.local, 'cowork_companion_http_envelope', None)
    if (not isinstance(pending, _PendingRequest) or pending.authority is not _AUTHORITY
            or pending.request is not request or request.path != PATHS.get(pending.operation) or request.method != 'POST'
            or request.headers.get('Authorization') or 'sid' in request.cookies
            or frappe.session.user not in ('', 'Guest', None)
            or request.headers.get('Frappe-Authorization-Source')
            or any(key in request.args or key in request.form for key in ('cmd', 'sid'))):
        _deny()
    token, payload, operation = pending.token, pending.payload, pending.operation
    method = METHODS[operation]
    # handler.execute_cmd can select an override/server script before reaching
    # the whitelist check. Such routing changes must not inherit this identity.
    from frappe.core.doctype.server_script.server_script_utils import get_server_script_map
    if (frappe.override_whitelisted_method(method) != method
            or method in get_server_script_map().get('_api', {})):
        _deny()
    try:
        if operation == 'lease':
            queue_contracts.lease_request(payload)
        elif operation == 'ack':
            queue_contracts.ack_request(payload)
    except queue_contracts.QueueContractError as error:
        reject_request(error.code)
    # Session setup may already have established an RR view.
    # A current Connection lock can then raise MariaDB 1020/deadlock before the
    # handler retry boundary. This segment has emitted no external IO or new
    # identity; roll back and authenticate completely again, never reuse context.
    for attempt in range(3):
        try:
            context = (receipt_auth.authenticate(token, payload['log_id']) if operation == 'ack'
                       else credentials.authenticate(token))
            break
        except frappe.QueryDeadlockError as error:
            frappe.db.rollback()
            clear_private_messages()
            if attempt == 2:
                raise_busy(error, stage='authenticate')
        except (frappe.AuthenticationError, frappe.PermissionError):
            raise
        except Exception as error:
            # An unexpected authentication failure is visible as retryable 503,
            # with a fixed/type-only diagnostic. Never log original token frames.
            raise_busy(error, stage='authenticate')
    body = deepcopy(payload)
    # local.session aliases session_obj.data before this point. Never change it
    # in place. Also prevent sync_database's after_response session.update.
    frappe.local.session = frappe._dict(user='Guest', sid='Guest', data=frappe._dict())
    frappe.local.session_obj = None
    frappe.set_user(context.issuer)
    frappe.local.form_dict = frappe._dict(deepcopy(body))
    frappe.local.cowork_companion_http_context = _BoundRequest(context, request, body, operation, _AUTHORITY)
    _clear_cookies()


def current_request(operation='heartbeat'):
    """Only the authenticated exact request may obtain context and copied body."""
    bound = getattr(frappe.local, 'cowork_companion_http_context', None)
    request = _request()
    if (not isinstance(bound, _BoundRequest) or bound.authority is not _AUTHORITY
            or operation not in METHODS or bound.operation != operation
            or request is not bound.request or request.path != PATHS[operation] or request.method != 'POST'
            or frappe.local.site != bound.context.site or frappe.session.user != bound.context.issuer
            or not isinstance(bound.context, receipt_auth.ReceiptContext if operation == 'ack' else AgentContext)
            or any(key in request.args or key in request.form for key in ('cmd', 'sid'))):
        _deny()
    return bound.context, deepcopy(bound.payload)


def _clear_cookies():
    manager = getattr(frappe.local, 'cookie_manager', None)
    if manager:
        manager.cookies.clear()
        manager.to_delete.clear()


def after_request(request=None, response=None):
    # This also covers errors before auth_hooks/HTTPRequest finishes, including
    # CSRF or malformed JSON. Never clear a browser cookie by sending deletion.
    if _candidate(request or _request()):
        _discard_recording()
        _clear_cookies()
        if response is not None:
            # Core's earlier before_request may redirect Guest website routes
            # before Cowork's guard. A machine credential never follows a login
            # redirect or becomes an interactive browser session.
            if 300 <= response.status_code < 400:
                response.status_code = 401
                response.headers.remove('Location')
                response.set_data(json.dumps({'exc_type': 'AuthenticationError'}))
                response.mimetype = 'application/json'
            response.headers.remove('Set-Cookie')
            response.headers['Cache-Control'] = 'no-store'
            response.headers['Pragma'] = 'no-cache'
            response.headers['X-Content-Type-Options'] = 'nosniff'
