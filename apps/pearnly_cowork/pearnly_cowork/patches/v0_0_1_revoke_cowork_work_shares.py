"""Protective upgrade: revoke only legacy Cowork Work share rights, keep the rows.

Frappe 16's no-role-permission list path returns shared documents before custom
query hooks. Cowork paid-call evidence is service-only and was never a separately
shareable product record. Do not grant shared Core Work read/select roles, delete
DocShare audit rows, or touch DMS/other-product Work shares.
"""


def revoke_cowork_work_shares(database):
    """Idempotent scoped update. The native migration owns transaction/commit."""
    database.sql("""UPDATE `tabDocShare`
        SET `read`=0, `write`=0, `share`=0, `submit`=0
        WHERE share_doctype=%s
          AND share_name IN (
              SELECT name FROM `tabPearnly OCR Work`
              WHERE product=%s OR kind LIKE %s
          )
          AND (COALESCE(`read`, 0)<>0 OR COALESCE(`write`, 0)<>0
               OR COALESCE(`share`, 0)<>0 OR COALESCE(`submit`, 0)<>0)
        """, ("Pearnly OCR Work", "cowork", "cowork.%"))


def execute():
    import frappe

    # Fresh installs with no shared Core Work table have no legacy rows to revoke.
    if not frappe.db.table_exists("Pearnly OCR Work") or not frappe.db.table_exists("DocShare"):
        return
    revoke_cowork_work_shares(frappe.db)
