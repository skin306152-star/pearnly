"""Invoice category management; no display-only labels, statistics or memory listing."""
import frappe
from . import categories


def _call(function, *args, **kwargs):
    # Only these standalone local RPCs own their whole transaction. Internal
    # recognition/review callers use categories directly and propagate conflicts
    # to their enclosing workflow, without rolling back another caller's work here.
    for attempt in range(3):
        try:
            return function(*args, **kwargs)
        except frappe.QueryDeadlockError:
            frappe.db.rollback()
            if attempt == 2:
                raise


@frappe.whitelist()
def category_tree(workspace):
    return _call(categories.tree, workspace)


@frappe.whitelist(methods=["POST"])
def initialize_categories(workspace):
    return _call(categories.initialize, workspace)


@frappe.whitelist(methods=["POST"])
def save_category(workspace, label, parent=None, name=None, request_key=None, expected_revision=0, sort=0):
    return _call(categories.save, workspace, label, parent=parent, name=name, request_key=request_key,
        expected_revision=expected_revision, sort=sort)


@frappe.whitelist(methods=["POST"])
def disable_category(workspace, name, expected_revision):
    return _call(categories.disable, workspace, name, expected_revision)


@frappe.whitelist(methods=["POST"])
def set_category_enabled(workspace, name, enabled, expected_revision):
    return _call(categories.set_enabled, workspace, name, enabled, expected_revision)
