"""Product admins manage business users, never framework roles or permissions."""
import frappe

PRODUCT_ADMINS = ("DMS Admin", "Cowork Admin", "ERP Admin")
PLATFORM_DOCTYPES = ("Role", "User Permission")
WRITE_PERMISSIONS = ("write", "create", "delete", "submit", "cancel", "amend", "import", "share")


def restrict_platform_permissions():
    if not frappe.conf.get("pearnly_product"):
        return
    # Touch only the product-admin custom grants created by our provisioner.
    # Preserve System Manager, role membership and business DocType grants.
    for name in frappe.get_all("Custom DocPerm", filters={
        "parent": ["in", PLATFORM_DOCTYPES], "role": ["in", PRODUCT_ADMINS],
    }, pluck="name"):
        doc = frappe.get_doc("Custom DocPerm", name)
        for key in WRITE_PERMISSIONS:
            doc.set(key, 0)
        doc.save(ignore_permissions=True)
    frappe.clear_cache()


def user_permission(doc, user=None, permission_type=None):
    """Customer account settings are self-service; the roster owns LINE identities."""
    user = user or frappe.session.user
    if frappe.conf.get("pearnly_product") and user != "Administrator" and doc.name != user:
        return False
    return None


def user_query(user=None):
    user = user or frappe.session.user
    if frappe.conf.get("pearnly_product") and user != "Administrator":
        return "`tabUser`.`name` = " + frappe.db.escape(user)
    return ""
