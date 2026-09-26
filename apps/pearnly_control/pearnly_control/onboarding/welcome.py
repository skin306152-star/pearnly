"""First-use welcome mail; native queue delivery is independent of provisioning."""
from pathlib import Path
import re
import json

import frappe
from frappe import _

from pearnly_control.account import identity_pools
from pearnly_control.onboarding.targets import require_cloud_target

ISSUE = "Pearnly Issue"
REQUEST = "Pearnly Provisioning Request"
SENDER = "hello@pearnly.com"
PRODUCTS = {"dms": "Pearnly DMS", "erp": "Pearnly ERP", "cowork": "Pearnly Cowork"}
DOMAIN = re.compile(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
REQUIRED = {"new_site", "set_oidc", "bind_user", "publish_site", "mark_active"}
PRODUCT_APPS = {"dms": "pearnly_dms", "erp": "pearnly_erp_portal", "cowork": "pearnly_cowork"}


def request_completed(doc, method=None):
    """Run only on completion, never replay historical completed requests at startup."""
    if doc.status != "done" or not doc.has_value_changed("status"):
        return
    if not doc.steps or any(s.action in ("drop_site", "purge_control") for s in doc.steps):
        return
    _queue_safely(doc)


def _queue_safely(request):
    frappe.db.savepoint("welcome_email")
    try:
        return queue_for_request(request)
    except Exception:
        # Neither traceback locals nor SMTP errors may disclose initial passwords.
        frappe.db.rollback(save_point="welcome_email")
        frappe.db.set_value(ISSUE, request.issue, "welcome_email_error",
            _("Welcome email could not be queued. Check the outgoing email account, then retry the email."),
            update_modified=False)
        return None


def ready_products(issue, request):
    if not identity_pools.issued(issue) or request.status != "done":
        raise ValueError("Account is not ready")
    if not issue.apps or not request.steps or any(s.status not in ("done", "skipped") for s in request.steps):
        raise ValueError("Provisioning is incomplete")
    if any(s.action in ("drop_site", "purge_control") for s in request.steps):
        raise ValueError("Not an onboarding request")
    products = []
    for row in issue.apps:
        domain = (row.domain or "").strip().lower()
        if row.app not in PRODUCTS or not DOMAIN.fullmatch(domain):
            raise ValueError("Invalid product domain")
        dep = frappe.db.get_value("Pearnly Deployment", {
            "issue": issue.name, "tenant": issue.tenant, "product": row.app,
            "site_name": domain, "status": "active",
        }, "name")
        actions = {s.action for s in request.steps if s.deployment == dep and s.status == "done"}
        if not dep or not REQUIRED.issubset(actions):
            raise ValueError("Published account is not ready")
        # install_core/install_product are step keys, not executable actions.
        # Require both actual app installations for this deployment; do not
        # accept an unrelated install_app or the same step duplicated twice.
        installed = {}
        for step in request.steps:
            if step.deployment != dep or step.status != "done" or step.action != "install_app":
                continue
            payload = json.loads(step.payload or "{}")
            if payload.get("site") == domain:
                installed[step.step_key] = payload.get("app")
        if (installed.get(f"{dep}:install_core") != "pearnly_core"
                or installed.get(f"{dep}:install_product") != PRODUCT_APPS[row.app]):
            raise ValueError("Required product apps are not installed")
        products.append({"name": PRODUCTS[row.app], "domain": domain, "url": f"https://{domain}/"})
    return products


def queue_for_request(request):
    # Lock the durable issue receipt: concurrent completion/retry creates one queue row.
    frappe.db.sql("SELECT name FROM `tabPearnly Issue` WHERE name=%s FOR UPDATE", (request.issue,))
    issue = frappe.get_doc(ISSUE, request.issue)
    if issue.get("welcome_email_queue"):
        return issue.welcome_email_queue
    require_cloud_target(issue.target, use_default=False)
    products = ready_products(issue, request)
    account_name = frappe.conf.get("pearnly_welcome_email_account")
    if not account_name:
        raise ValueError("Official email account is not configured")
    account = frappe.get_doc("Email Account", account_name)
    if not account.enable_outgoing or account.email_id != SENDER or account.always_bcc:
        raise ValueError("Official email account must have no BCC")
    password = issue.get_password("account_password", raise_exception=False)
    if not password:
        raise ValueError("Initial credential is unavailable")
    language = issue.get("welcome_email_language") or "th"
    language = {"zh-CN": "zh", "zh-cn": "zh"}.get(language, language)
    if language not in ("th", "zh", "en", "ja"):
        language = "th"
    product_name = products[0]["name"] if len(products) == 1 else "Pearnly"
    translate = lambda message: _(message, lang=language)
    args = {
        "language": "zh-CN" if language == "zh" else language,
        "company": issue.tenant_name, "username": issue.account_username,
        "email": issue.account_email, "password": password, "products": products,
        "product_name": product_name, "t": translate,
    }
    queue = frappe.sendmail(
        recipients=[issue.account_email], sender=f"Pearnly <{SENDER}>", reply_to=SENDER,
        subject=translate("Welcome to {0} — your account is ready").format(product_name),
        template="pearnly_welcome", args=args, delayed=True,
        reference_doctype=ISSUE, reference_name=issue.name,
        message_id=f"<welcome-{issue.name}@pearnly.com>",
        add_unsubscribe_link=0, is_notification=True, raw_html=True, add_css=False,
        redact_message_after_send=True,
        inline_images=[{"filename": "mail-logo.png", "filecontent":
            Path(frappe.get_app_path("pearnly_control", "public", "images", "mail-logo.png")).read_bytes()}],
    )
    if not queue or queue.email_account != account.name:
        raise ValueError("Official queue was not created")
    frappe.db.set_value(ISSUE, issue.name, {
        "welcome_email_queue": queue.name, "welcome_email_error": "",
    }, update_modified=False)
    return queue.name


@frappe.whitelist(methods=["POST"])
def retry_queue(issue: str):
    """Retry queue creation only. SMTP retries use the existing native Email Queue."""
    frappe.only_for("System Manager")
    doc = frappe.get_doc(ISSUE, issue)
    doc.check_permission("write")
    request_name = frappe.db.get_value(REQUEST, {"issue": issue, "status": "done"}, "name")
    if not request_name:
        frappe.throw(_("Provisioning must be complete before sending the welcome email."))
    queue = _queue_safely(frappe.get_doc(REQUEST, request_name))
    return {"queue": queue, "error": not bool(queue)}
