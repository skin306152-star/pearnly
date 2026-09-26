"""Private POST-only member endpoint; fixed data envelope, no exception disclosure."""
import frappe
from frappe.recorder import do_not_record

from pearnly_control.account import member_service
from pearnly_control.account.member_provider import MemberError

METHOD = "pearnly_control.account.member_api.execute"


@do_not_record
def _private_request():
    frappe.flags.disable_traceback = True


def _scrub_password():
    form = getattr(frappe.local, "form_dict", None)
    if form:
        form.pop("password", None)
    request = getattr(frappe.local, "request", None)
    if request:
        # The HTTP response is built before after_request, and the RPC has already
        # captured its arguments before finally. No later consumer needs this body.
        request._cached_json = (None, None)
        request._cached_data = b""


def after_request(request=None, response=None):
    request = request or getattr(frappe.local, "request", None)
    form = getattr(frappe.local, "form_dict", None) or {}
    if not request or not (request.path in {
            prefix + METHOD for prefix in ("/api/method/", "/api/v1/method/", "/api/v2/method/")}
            or form.get("cmd") == METHOD):
        return
    # Includes private-site guard, CSRF/auth and malformed-input failure paths.
    # The native after_response recorder dump runs only after this hook.
    _private_request()
    _scrub_password()
    if response is not None:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"


@frappe.whitelist(allow_guest=True, methods=["POST"])
@do_not_record
def execute(tenant=None, action=None, email=None, password=None, username="", request_id="",
            subject="", line_user_id="", login_channel=""):
    # Expected and unexpected errors stay inside this boundary. Frappe must not
    # persist a traceback with the credential-bearing request locals.
    _private_request()
    try:
        result = member_service.execute(tenant, action, email, password, username, request_id,
                                        subject, line_user_id, login_channel)
        frappe.db.commit()
        return {"ok": True, **result}
    except MemberError as error:
        code = error.code
    except Exception:
        code = "unavailable"
    finally:
        # Password is not business audit data. The endpoint never logs an actor.
        _scrub_password()
    try:
        frappe.db.rollback()
    except Exception:
        code = "unavailable"
    return {"ok": False, "error": code}
