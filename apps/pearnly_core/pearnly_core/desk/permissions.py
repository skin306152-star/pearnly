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
    product = frappe.conf.get("pearnly_product")
    if product and user != "Administrator":
        from pearnly_core.desk.navigation import _can_open_page
        if doc.name != user or not _can_open_page(product, "user", user=user):
            return False
    return None


def user_query(user=None):
    user = user or frappe.session.user
    if frappe.conf.get("pearnly_product") and user != "Administrator":
        return "`tabUser`.`name` = " + frappe.db.escape(user)
    return ""


def sync_profile_share(doc, method=None):
    """Native User self-shares must follow the product's optional profile grant."""
    product = frappe.conf.get("pearnly_product")
    if not product or doc.name in ("Administrator", "Guest"):
        return
    from pearnly_core.desk.navigation import _can_open_page
    if not _can_open_page(product, "user", user=doc.name):
        from frappe.share import remove
        remove("User", doc.name, doc.name, flags={"ignore_permissions": True, "ignore_share_permission": True})
