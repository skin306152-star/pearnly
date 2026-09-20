"""One tenant's LIFF entry contract, shared by Flex and persistent rich menus."""
from urllib.parse import urlencode

import frappe
from pearnly_core import line


def account_action(action, label):
    if action not in ("portal", "credentials") or not line.liff_id():
        return None
    query = urlencode({"action": action})
    desktop = frappe.utils.get_url().rstrip("/") + "/liff/dms-booking?" + query
    # Portal starts in the external browser so the target DMS receives browser cookies.
    uri = desktop + "&openExternalBrowser=1" if action == "portal" else f"https://liff.line.me/{line.liff_id()}?{query}"
    return {"type": "uri", "label": str(label), "uri": uri, "altUri": {"desktop": desktop}}
