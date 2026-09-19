"""Default browser branding through native Website Settings, shared by all products."""
import frappe

FAVICON = "/assets/pearnly_core/images/app-icon.png"
FRAMEWORK_DEFAULTS = {"", "attach_files:", "/assets/frappe/images/frappe-favicon.svg"}


def ensure_site_favicon():
    """Fill missing/framework branding on provisioning and upgrade; preserve custom icons."""
    if frappe.conf.get("pearnly_product") not in {"dms", "cowork", "erp"}:
        return
    settings = frappe.get_single("Website Settings")
    if (settings.favicon or "").strip() in FRAMEWORK_DEFAULTS:
        settings.favicon = FAVICON
        settings.save(ignore_permissions=True)
