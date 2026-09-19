"""Native Website Settings regression in auth-preview; all writes rolled back."""
import frappe

frappe.init(site="auth-preview.localhost")
frappe.connect()
product = frappe.conf.get("pearnly_product")
try:
    assert frappe.local.site == "auth-preview.localhost"
    from pearnly_core.desk.branding import FAVICON, ensure_site_favicon
    from frappe.website.doctype.website_settings.website_settings import get_website_settings

    assert "pearnly_core.desk.branding.ensure_site_favicon" in frappe.get_hooks("after_migrate")
    for value in ("dms", "cowork", "erp"):
        frappe.conf.pearnly_product = value
        frappe.db.set_single_value("Website Settings", "favicon", "")
        ensure_site_favicon()
        assert frappe.db.get_single_value("Website Settings", "favicon") == FAVICON
        assert get_website_settings().favicon == FAVICON
        modified = frappe.db.get_single_value("Website Settings", "modified")
        ensure_site_favicon()
        assert frappe.db.get_single_value("Website Settings", "modified") == modified
    frappe.db.set_single_value("Website Settings", "favicon", "/assets/frappe/images/frappe-favicon.svg")
    ensure_site_favicon()
    assert frappe.db.get_single_value("Website Settings", "favicon") == FAVICON
    frappe.db.set_single_value("Website Settings", "favicon", "/files/company-custom.ico")
    ensure_site_favicon()
    assert frappe.db.get_single_value("Website Settings", "favicon") == "/files/company-custom.ico"
    frappe.conf.pearnly_product = ""
    frappe.db.set_single_value("Website Settings", "favicon", "")
    ensure_site_favicon()
    assert not frappe.db.get_single_value("Website Settings", "favicon")
    print("PASS three product defaults, rendered context, idempotence, framework replacement, custom/control preservation")
finally:
    frappe.conf.pearnly_product = product
    frappe.db.rollback()
    frappe.clear_cache()
    frappe.destroy()
