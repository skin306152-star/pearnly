"""Native master records; no endpoint credentials or external business writes."""
import frappe
from frappe import _
from pearnly_cowork.access import scope
from pearnly_cowork.masters import clients as clients_service
from pearnly_cowork.masters import products as products_service
from . import push_inputs


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


@frappe.whitelist()
def roster(workspace, start=0, limit=50, include_inactive=0):
    """The client roster with the deployed ordering and the three per-client totals."""
    return clients_service.list_clients(workspace, start=start, limit=limit,
        include_inactive=bool(int(include_inactive)))


@frappe.whitelist()
def client_detail(workspace, name):
    return clients_service.get_client(workspace, name)


@frappe.whitelist(methods=["POST"])
def save_client(workspace, values, name=None):
    """Create a client, or update the named one. Fields are cleaned before writing."""
    if name:
        return clients_service.update_client(workspace, name, values)
    return clients_service.create_client(workspace, values)


@frappe.whitelist(methods=["POST"])
def delete_client(workspace, name):
    """Deleting a client that still has invoice records is refused (see the service)."""
    return clients_service.delete_client(workspace, name)


@frappe.whitelist(methods=["POST"])
def resolve_buyer(workspace, buyer_name, buyer_tax=None, buyer_candidates=None):
    """Which client an invoice's buyer belongs to; the decision is the ported core."""
    return clients_service.resolve_buyer(workspace, buyer_name, buyer_tax=buyer_tax,
        buyer_candidates=buyer_candidates)


@frappe.whitelist()
def products(workspace, start=0, limit=50, include_inactive=0):
    return products_service.list_products(workspace, start=start, limit=limit,
        include_inactive=bool(int(include_inactive)))


@frappe.whitelist()
def product_detail(workspace, name):
    return products_service.get_product(workspace, name)


@frappe.whitelist(methods=["POST"])
def save_product(workspace, values, name=None):
    """Create a product with its sell units, or update the named one."""
    return products_service.save_product(workspace, values, name=name)


@frappe.whitelist(methods=["POST"])
def delete_product(workspace, name):
    return products_service.delete_product(workspace, name)


@frappe.whitelist(methods=["POST"])
def save_erp_mapping(kind, mapping_key, erp_code, expected_revision=0):
    return push_inputs.save_mapping(kind, mapping_key, erp_code, expected_revision=expected_revision)


@frappe.whitelist(methods=["POST"])
def save_supplier_profile(workspace, seller_tax_id, default_payment=None, default_item_type=None):
    return push_inputs.save_profile(workspace, seller_tax_id, default_payment=default_payment,
        default_item_type=default_item_type, source='user_rule')
