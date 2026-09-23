"""Pure capability checks; native Administrator/Site regression belongs to root lab."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pearnly_cowork.ocr.execution import work_guard


class NativeAllowsAdministrator:
    def check_permission(self, permtype="read", permlevel=None):
        return "native-allowed"

    def has_permission(self, permtype="read", *, debug=False, user=None):
        return True


class Work(work_guard.CoworkWorkGuard, NativeAllowsAdministrator, dict):
    @property
    def name(self):
        return self.get("name")

    def is_new(self):
        return self.get("__islocal", False)


class WorkCapability(unittest.TestCase):
    def setUp(self):
        self.rows = {"existing": {"product": "cowork", "kind": "cowork.invoice"}}
        self.shares = {}
        self.share_queries = []
        def sql(query, params):
            self.share_queries.append(params)
            return [("synthetic-share",)] if any(self.shares.get(params[1], {}).values()) else []
        def deny(message, exception):
            raise exception(message)
        self.frappe = SimpleNamespace(flags={}, PermissionError=PermissionError, throw=deny,
            db=SimpleNamespace(get_value=lambda doctype, name, fields, as_dict: self.rows.get(name), sql=sql))
        self.patch = patch.object(work_guard, "frappe", self.frappe)
        self.patch.start()
        self.translation = patch.object(work_guard, "_", lambda value: value)
        self.translation.start()

    def tearDown(self):
        self.patch.stop()
        self.translation.stop()

    def test_admin_native_read_write_and_delete_refuse_existing_cowork(self):
        doc = Work(name="existing", product="cowork", kind="cowork.invoice")
        for action in ("read", "write", "delete", "export", "print", "submit", "cancel"):
            self.assertFalse(doc.has_permission(action, user="Administrator"))
            with self.assertRaises(PermissionError):
                doc.check_permission(action)
        with self.assertRaises(PermissionError):
            work_guard.mutation(doc)
        with self.assertRaises(PermissionError):
            work_guard.deny_delete(doc)

    def test_changing_product_kind_and_new_flag_cannot_escape_stored_identity(self):
        doc = Work(name="existing", product="dms", kind="dms.identity", __islocal=True)
        with self.assertRaises(PermissionError):
            work_guard.mutation(doc)
        with work_guard.work_creation():
            with self.assertRaises(PermissionError):
                work_guard.mutation(doc)
            with self.assertRaises(PermissionError):
                doc.check_permission("create")

    def test_only_object_capability_allows_new_internal_admission(self):
        doc = Work(name="new-work", product="cowork", kind="cowork.invoice", __islocal=True)
        for value in (True, "true", {}, "_CREATE_CAPABILITY"):
            self.frappe.flags["_cowork_work_creation"] = value
            with self.assertRaises(PermissionError):
                work_guard.mutation(doc)
        self.frappe.flags.clear()
        with work_guard.work_creation():
            work_guard.mutation(doc)
            self.assertEqual(doc.check_permission("create"), "native-allowed")
            self.assertFalse(doc.has_permission("read", user="Administrator"))
            with work_guard.work_creation():
                work_guard.mutation(doc)
            work_guard.mutation(doc)
        self.assertEqual(self.frappe.flags, {})
        with self.assertRaises(PermissionError):
            work_guard.mutation(doc)

    def test_other_product_work_retains_native_behavior(self):
        doc = Work(name="dms-work", product="dms", kind="dms.identity", __islocal=True)
        work_guard.mutation(doc)
        work_guard.deny_delete(doc)
        self.assertTrue(doc.has_permission("read", user="Administrator"))
        self.assertEqual(doc.check_permission("write"), "native-allowed")
        self.assertEqual(self.share_queries, [])

    def test_preplaced_active_shares_block_creation_but_revoked_audit_rows_do_not(self):
        doc = Work(name="new-work", product="cowork", kind="cowork.invoice", __islocal=True)
        for flag in ("read", "write", "share", "submit"):
            self.shares["new-work"] = {flag: 1}
            with work_guard.work_creation(), self.assertRaises(PermissionError):
                work_guard.mutation(doc)
        self.shares["new-work"] = dict.fromkeys(("read", "write", "share", "submit"), 0)
        with work_guard.work_creation():
            work_guard.mutation(doc)
            self.assertEqual(doc.check_permission("create"), "native-allowed")
        self.assertTrue(self.share_queries)


if __name__ == "__main__":
    unittest.main()
