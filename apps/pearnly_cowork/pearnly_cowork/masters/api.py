"""Native master records; no endpoint credentials or external business writes."""
import frappe
from frappe import _
from pearnly_cowork.access import scope


def require_manager():
    scope.require("master_data")
    if not scope.administrator():
        frappe.throw(_("Not Permitted"), frappe.PermissionError)


@frappe.whitelist(methods=["POST"])
def create_workspace(display_name, tax_id=None):
    require_manager()
    doc = frappe.get_doc({"doctype": "Cowork Workspace", "display_name": display_name,
                          "tax_id": tax_id, "enabled": 1}).insert()
    return {"name": doc.name, "display_name": doc.display_name}


@frappe.whitelist(methods=["POST"])
def create_client(workspace, display_name, tax_id=None):
    require_manager()
    scope.require_scope(workspace)
    doc = frappe.get_doc({"doctype": "Cowork Client", "workspace": workspace,
                          "display_name": display_name, "tax_id": tax_id, "enabled": 1}).insert()
    return {"name": doc.name, "workspace": doc.workspace, "display_name": doc.display_name}


@frappe.whitelist()
def workspaces(start=0, limit=50):
    if not any(scope.allowed(f) for f in (*scope.READ_FEATURES, "master_data")):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    return frappe.get_list("Cowork Workspace", filters={"enabled": 1},
                           fields=["name", "display_name", "tax_id"], order_by="display_name, name",
                           limit_start=max(0, int(start)), limit_page_length=max(1, min(int(limit), 200)))


@frappe.whitelist()
def clients(workspace, start=0, limit=50):
    if not any(scope.allowed(f) for f in (*scope.READ_FEATURES, "master_data")):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    scope.require_scope(workspace)
    return frappe.get_list("Cowork Client", filters={"workspace": workspace, "enabled": 1},
                           fields=["name", "display_name", "tax_id"], order_by="display_name, name",
                           limit_start=max(0, int(start)), limit_page_length=max(1, min(int(limit), 200)))
