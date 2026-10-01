"""Indexes for the original-file guard's exact lookups (``files.access.protected_sources``).

Without them the guard's locking read scans, and locks, every source row on each File
write (three-tenant stress test 2026-10-01: reconciliation starts failed with 1020).

- ``tabFile.content_hash``: a native ``search_index`` property setter, so later
  migrations of File keep the index instead of dropping an unknown one.
- ``storage_url`` is long text, which Frappe neither indexes nor drops indexes on;
  a prefix index serves its equality lookup.
"""
import frappe
from frappe.custom.doctype.property_setter.property_setter import make_property_setter


def execute():
    make_property_setter("File", "content_hash", "search_index", "1", "Check", for_doctype=False,
                         validate_fields_for_doctype=False)
    frappe.db.add_index("File", ["content_hash"])
    if not frappe.db.has_index("tabCowork Source File", "storage_url_prefix"):
        frappe.db.commit()
        frappe.db.sql_ddl("ALTER TABLE `tabCowork Source File` ADD INDEX IF NOT EXISTS "
                          "`storage_url_prefix` (`storage_url`(255))")
