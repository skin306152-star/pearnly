"""Operator-only stdin setup of the existing official SMTP account. Never sends mail."""
import json
import sys

import frappe
from frappe.installer import update_site_config

from pearnly_control.onboarding.welcome import SENDER


def configure(values):
    frappe.only_for("System Manager")
    if values.get("SMTP_FROM") != SENDER:
        raise ValueError("Official sender must be hello@pearnly.com")
    if not all(values.get(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD")):
        raise ValueError("Official SMTP settings are incomplete")
    port = int(values.get("SMTP_PORT") or 587)
    if port not in (587, 465):
        raise ValueError("Encrypted SMTP is required")
    existing = frappe.db.get_value("Email Account", {"email_id": SENDER}, "name")
    account = frappe.get_doc("Email Account", existing) if existing else frappe.new_doc("Email Account")
    account.update({
        "email_account_name": "Pearnly", "email_id": SENDER,
        "login_id_is_different": 1, "login_id": values["SMTP_USER"],
        "password": values["SMTP_PASSWORD"], "smtp_server": values["SMTP_HOST"],
        "smtp_port": port, "use_tls": int(port == 587), "use_ssl_for_outgoing": int(port == 465),
        "enable_outgoing": 1, "enable_incoming": 0,
        "always_use_account_email_id_as_sender": 1, "always_use_account_name_as_sender_name": 1,
        "reply_to_addresses": [{"email": SENDER, "_name": "Pearnly"}], "add_reply_to_header": 1,
        "track_email_status": 0, "send_unsubscribe_message": 0, "always_bcc": "",
        "add_signature": 0, "append_emails_to_sent_folder": 0,
    })
    account.save(ignore_permissions=True)
    frappe.db.commit()
    update_site_config("pearnly_welcome_email_account", account.name)
    return {"sender": SENDER, "account": account.name, "email_sent": False}


if __name__ == "__main__":
    frappe.init(site="control.localhost", sites_path="/home/frappe/frappe-bench/sites")
    frappe.connect()
    frappe.set_user("Administrator")
    try:
        result = configure(json.load(sys.stdin))
        print(json.dumps(result))
    except Exception:
        frappe.db.rollback()
        raise SystemExit("Official email setup failed; credentials were not logged") from None
    finally:
        frappe.destroy()
