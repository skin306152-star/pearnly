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


FRAMEWORK_ADMIN = "System Manager"


def guard_user_roles(doc, method=None):
    """产品站点上账号角色的两条硬规则（User ``validate``）：

    - 业务账号不许被加上 System Manager（框架超管：能看用户权限、建邮件账户、改系统设置……产品没有承接这些）。
      只有开户新建账号那一刻（`provisioning.ensure_local_user` 举旗）放行，随后「设角色」一步拿走；
    - 本人保存自己的个人页不能改动自己的角色：原生「角色编辑器」在个人页上不显示，但有编辑 User 权限的人保存时
      它会按自己的勾选重写角色表（10-26 testcowork 管理员保存个人页丢了 Desk User）。角色只在团队管理里改。
    """
    if not frappe.conf.get("pearnly_product") or doc.name in ("Administrator", "Guest") or doc.is_new():
        return
    before = doc.get_doc_before_save()
    held = {row.role for row in (before.roles if before else [])}
    wanted = {row.role for row in doc.roles}
    if FRAMEWORK_ADMIN in wanted - held and not frappe.flags.get("pearnly_grant_system_manager"):
        frappe.throw(frappe._("Not permitted"), frappe.PermissionError)
    if before and frappe.session.user == doc.name and wanted != held:
        doc.set("roles", [{"role": row.role} for row in before.roles])


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
