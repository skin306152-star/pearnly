"""Reuse the ERP intake Site suite with signed Guest webhooks in release labs.

Only HTTP request context and LINE transport are synthetic. DocTypes, permissions,
bindings, event receipts, files, purchase/sales confirmation and rollback use the
real isolated MariaDB Sites. All Python HTTP requests are blocked as a backstop.
Use fresh release-erp-line-a/b Sites: successful purchase/sales fixtures are
immutable business records, not something a regression test may delete on rerun.
"""
import base64
from contextlib import redirect_stdout
import hashlib
import hmac
import importlib.util
import io
import json
import os
from pathlib import Path
import uuid
from unittest.mock import patch

import frappe
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request


SITE_ROOT = Path("/home/frappe/frappe-bench/sites")
SITE = "release-erp-line-a.localhost"
OTHER_SITE = "release-erp-line-b.localhost"
SECRET = "synthetic-release-line-signing-secret"
SIGNED_COUNT = 0


def connect(site=SITE):
    if os.environ.get("PEARNLY_RELEASE_LAB") != "1":
        raise SystemExit("Requires dedicated release laboratory")
    if site not in {SITE, OTHER_SITE} or not (SITE_ROOT / ".unified-release-lab").is_file():
        raise SystemExit("Refusing foreign Site or volume")
    os.chdir(SITE_ROOT)
    frappe.init(site=site)
    frappe.connect()
    if not frappe.conf.pearnly_release_lab or frappe.conf.db_host != "db":
        raise SystemExit("Refusing foreign database")
    frappe.flags.mute_emails = True
    frappe.conf.pearnly_line_channel_secret = SECRET
    return site


def send(line_module, line_user_id, text=None, *, message_type="image", message_id="m1",
         filename="", invalid_signature=False):
    global SIGNED_COUNT
    message = {"type": message_type, "id": message_id}
    if text is not None:
        message.update(type="text", text=text)
    if filename:
        message["fileName"] = filename
    event_id = "release-line-event-" + uuid.uuid4().hex
    event = {"type": "message", "webhookEventId": event_id,
             "source": {"userId": line_user_id}, "replyToken": "lab-reply", "message": message}
    body = json.dumps({"events": [event]}).encode()
    signature = base64.b64encode(hmac.new(SECRET.encode(), body, hashlib.sha256).digest()).decode()
    if invalid_signature:
        signature = "wrong-signature"
    request = Request(EnvironBuilder(
        path="/api/method/pearnly_core.line.webhook", method="POST", data=body,
        headers={"X-Line-Signature": signature, "Content-Type": "application/json"},
    ).get_environ())
    previous = frappe.session.user
    try:
        frappe.set_user("Guest")
        frappe.local.response.http_status_code = 200
        with patch.object(frappe, "request", request):
            result = line_module.webhook()
        assert frappe.session.user == "Guest", "Bound employee context leaked"
        if invalid_signature:
            assert result == {"ok": False, "error": "bad_signature"}
            assert frappe.local.response.http_status_code == 400
            assert not frappe.db.exists("Pearnly LINE Event", event_id)
        else:
            assert result == {"ok": True}
            assert frappe.local.response.http_status_code == 200
            assert frappe.db.get_value("Pearnly LINE Event", event_id, "status") == "done"
            SIGNED_COUNT += 1
        return not invalid_signature
    finally:
        frappe.set_user(previous)


def main():
    spec = importlib.util.spec_from_file_location("release_intake_suite", "/workspace/scripts/erp-pos-lab-intake-verify.py")
    suite = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(suite)
    # Reuse fixture/assertion logic; only adapt its dedicated-lab boundary and
    # replace direct dispatch with actual signature verification + event receipt.
    suite.connect, suite.send = connect, send
    suite.SITE, suite.OTHER_SITE = SITE, OTHER_SITE
    with patch("requests.sessions.Session.request", side_effect=AssertionError("External HTTP forbidden")) as network:
        output = io.StringIO()
        with redirect_stdout(output):
            status = suite.main()
        if status:
            print(output.getvalue())
            raise SystemExit(status)
        print("PASS reused ERP Site suite: Guest ownership, purchase/sales roles, confirms, content dedupe, two-Site isolation")
        frappe.destroy()
        connect()
        frappe.set_user("Administrator")
        from pearnly_core import line, line_session
        from pearnly_erp_portal.integrations import intake

        for row in frappe.get_all("ERP LINE Intake", fields=["name", "sent_by", "owner"]):
            assert row.owner == row.sent_by
            attached = frappe.get_all("File", filters={"attached_to_doctype": "ERP LINE Intake",
                                                      "attached_to_name": row.name},
                                      fields=["owner", "is_private"])
            assert attached and all(f.owner == row.sent_by and f.is_private for f in attached)
        print("PASS native private files and intake records both belong to the bound employee")

        line_session.set_session(suite.LINE_USER, "erp_intake", {"direction": "purchase"})
        fake = suite.FakeLine(line, {"failure-media": suite.PDF})
        try:
            before_rows = frappe.db.count("ERP LINE Intake")
            before_files = frappe.db.count("File")
            with patch.object(intake, "_store_file", side_effect=RuntimeError("synthetic storage failure")):
                send(line, suite.LINE_USER, message_type="file", message_id="failure-media", filename="failure.pdf")
            assert frappe.db.count("ERP LINE Intake") == before_rows
            assert frappe.db.count("File") == before_files
            assert fake.last_text() == "Could not store this document"
            print("PASS storage failure rolls back newly inserted intake; signed event remains recorded; Guest restored")

            with patch.object(line, "download_message_content", side_effect=AssertionError("Must not download")) as download:
                send(line, suite.LINE_USER, invalid_signature=True)
                frappe.db.set_value("User", suite.BUYER, "enabled", 0)
                frappe.db.commit()
                frappe.clear_cache(user=suite.BUYER)
                send(line, suite.LINE_USER)
                assert fake.last_text() == "Bind this account first"
                assert frappe.db.count("ERP LINE Intake") == before_rows
                download.assert_not_called()
            print("PASS bad signature and disabled bound account never download or store")
        finally:
            fake.restore()
            frappe.db.set_value("User", suite.BUYER, "enabled", 1)
            frappe.db.commit()
            frappe.destroy()
        network.assert_not_called()
    print(f"PASS {SIGNED_COUNT} signed Guest events through real webhook, native receipts and Site storage; zero external HTTP")


if __name__ == "__main__":
    main()
