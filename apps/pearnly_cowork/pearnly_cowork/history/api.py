"""Desk-ready backend RPCs. No frontend or external recognition/push is installed here."""
import frappe
from pearnly_cowork.access import scope
from pearnly_cowork.history import storage


@frappe.whitelist(methods=["POST"])
def create(workspace, pages, request_key, client=None):
    # This RPC owns a single local creation transaction. MariaDB may report
    # snapshot conflict 1020 instead of duplicate key for concurrent INSERT
    # RETURNING. Restart this read/insert-only request at most twice.
    # Internal workflows use storage directly and own their transaction retry.
    for attempt in range(3):
        try:
            return storage.create_manual(workspace, pages, request_key, client)
        except frappe.QueryDeadlockError:
            frappe.db.rollback()
            if attempt == 2:
                raise


@frappe.whitelist()
def detail(name):
    return storage.project(storage.read(name))


@frappe.whitelist(methods=["POST"])
def save(name, pages, expected_version):
    return storage.update_pages(name, pages, expected_version)


@frappe.whitelist(methods=["POST"])
def complete(records):
    return storage.complete(records)


@frappe.whitelist()
def records(workspace, staged=0, start=0, limit=50):
    scope.require_read()
    scope.require_scope(workspace)
    limit, start = max(1, min(int(limit), 100)), max(0, int(start))
    return frappe.get_list(storage.DOCTYPE, filters={"workspace": workspace, "staged": int(staged) != 0},
        fields=["name", "client", "source", "staged", "record_version", "invoice_no", "invoice_date",
                "seller_name", "total_amount", "quality_status", "confidence", "modified"],
        order_by="creation desc, name desc", limit_start=start, limit_page_length=limit)
