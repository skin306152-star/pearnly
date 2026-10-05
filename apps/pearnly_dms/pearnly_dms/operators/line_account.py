"""Public details of this site's configured OA, never a hardcoded shared account."""
import base64
import io
from urllib.parse import quote

import frappe
import pyqrcode
import requests
from frappe import _
from pearnly_core import line as line_api


def public_account():
    if not line_api.is_configured():
        frappe.throw(_("roster.line.notConfigured"))
    try:
        # 长期令牌或用频道号 + 密钥现取的短时令牌（见 `pearnly_core.line.access_token`）。
        response = requests.get("https://api.line.me/v2/bot/info",
                                headers={"Authorization": "Bearer " + line_api.access_token()}, timeout=10)
        response.raise_for_status()
        info = response.json()
    except (requests.RequestException, ValueError):
        frappe.throw(_("roster.line.unavailable"))
    if not isinstance(info, dict):
        frappe.throw(_("roster.line.unavailable"))
    basic_id = str(info.get("basicId") or "").strip()
    if not basic_id.startswith("@"):
        frappe.throw(_("roster.line.unavailable"))
    url = "https://line.me/R/ti/p/" + quote(basic_id, safe="")
    output = io.BytesIO()
    pyqrcode.create(url).svg(output, scale=4, background="white")
    return {"name": str(info.get("displayName") or basic_id), "basic_id": basic_id,
            "url": url, "qr": "data:image/svg+xml;base64," + base64.b64encode(output.getvalue()).decode()}
