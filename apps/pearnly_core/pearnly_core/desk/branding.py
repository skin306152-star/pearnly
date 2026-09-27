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


APP_NAME = "Pearnly"
FRAMEWORK_APP_NAMES = {"", "Frappe"}


def ensure_site_app_name():
    """浏览器标签在桌面脚本接管前显示的是框架的「应用名称」（`www/desk.py`：网站设置 → 系统设置 → "Frappe"），
    刷新时会闪出「Frappe」（用户 2026-09-27：不希望有这个）。产品站点上两处都还是空或框架默认值时填成 Pearnly，
    已自定义的保留；控制面不动。"""
    if frappe.conf.get("pearnly_product") not in {"dms", "cowork", "erp"}:
        return
    for doctype in ("Website Settings", "System Settings"):
        if (frappe.db.get_single_value(doctype, "app_name") or "").strip() in FRAMEWORK_APP_NAMES:
            frappe.db.set_single_value(doctype, "app_name", APP_NAME)
