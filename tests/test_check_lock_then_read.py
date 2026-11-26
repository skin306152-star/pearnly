"""`scripts/check-lock-then-read.py`：加锁之后用普通 frappe.get_doc 读同一事务里的单据要被拦（关快照隔离后读到读视图里的旧副本）。"""
import importlib.util
import pathlib
import textwrap
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_lock_then_read", ROOT / "scripts/check-lock-then-read.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def found(source):
    return [name for _, name in check.violations(pathlib.Path("x.py"), textwrap.dedent(source))]


class LockThenReadTest(unittest.TestCase):
    def test_sql_lock_then_plain_get_doc_fails(self):
        self.assertEqual(found('''
            def pay(name):
                frappe.db.sql("select name from `tabX` where name=%s for update", (name,))
                doc = frappe.get_doc("X", name)
                doc.save()
        '''), ["pay"])

    def test_get_value_lock_and_fstring_lock_fail(self):
        self.assertEqual(found('''
            def a(name, lock=False):
                row = frappe.db.get_value("X", name, "name", for_update=lock)
                return frappe.get_doc("X", row)

            def b(name):
                frappe.db.sql(f"SELECT name FROM `tab{DT}` WHERE name = %s FOR UPDATE", (name,))
                return frappe.get_doc(DT, name)
        '''), ["a", "b"])

    def test_locked_reads_new_docs_and_marked_lines_pass(self):
        self.assertEqual(found('''
            def a(name):
                frappe.db.sql("select name from `tabX` where name=%s for update", (name,))
                doc = frappe.get_doc("X", name, for_update=True)
                other = frappe.get_doc({"doctype": "Y"})
                account = frappe.get_doc("Email Account", "x")  # lock-read-ok: 只读核对
                return doc, other, account

            def b(name):
                doc = frappe.get_doc("X", name)
                frappe.db.get_value("X", name, "name", for_update=False)
                return doc
        '''), [])

    def test_read_before_the_lock_is_not_flagged(self):
        self.assertEqual(found('''
            def a(name):
                doc = frappe.get_doc("X", name)
                frappe.db.sql("select name from `tabX` where name=%s for update", (name,))
                return doc
        '''), [])

    def test_repository_is_clean(self):
        bad = [(path, item) for path in check.files() for item in check.violations(path, path.read_text(encoding="utf-8"))]
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
