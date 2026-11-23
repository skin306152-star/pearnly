"""`scripts/check-unlocked-overwrite.py`：同一张表没加锁读出、再写回算好的值（11-23，MariaDB 关快照隔离）。"""
import ast
import importlib.util
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_unlocked_overwrite", ROOT / "scripts" / "check-unlocked-overwrite.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def hits(source: str):
    source = textwrap.dedent(source)
    tree = ast.parse(source)
    module, lines = check.Module(tree), source.splitlines()
    out = []
    for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
        for call, doctype, used in check.scan_function(fn, module):
            out.append((fn.name, doctype, used, check.marked(lines, call)))
    return out


class Detects(unittest.TestCase):
    def test_unlocked_read_then_absolute_write_of_the_same_table(self):
        found = hits('''
            import frappe
            CARD = "Cowork Card"
            def add_reason(name, reason):
                old = frappe.db.get_value(CARD, name, "reasons")
                frappe.db.set_value(CARD, name, "reasons", old + "," + reason)
        ''')
        self.assertEqual(found, [("add_reason", "Cowork Card", ["old"], False)])

    def test_taint_follows_assignments_and_loops(self):
        found = hits('''
            import frappe
            def renumber(board):
                rows = frappe.get_all("Cowork Board List", filters={"board": board}, pluck="name")
                for name, value in zip(rows, range(len(rows))):
                    frappe.db.set_value("Cowork Board List", name, "position", value)
        ''')
        self.assertEqual([f[0] for f in found], ["renumber"])

    def test_raw_select_then_raw_update_with_a_parameter(self):
        found = hits('''
            import frappe
            def bump(name):
                n = frappe.db.sql("select current from `tabSeries` where name=%s", (name,))[0][0]
                frappe.db.sql("update `tabSeries` set current = %s where name = %s", (n + 1, name))
        ''')
        self.assertEqual([f[1] for f in found], ["Series"])

    def test_aliased_constants_match_by_name(self):
        found = hits('''
            import frappe
            from x import access
            COLUMN = access.COLUMN
            def move(name):
                pos = frappe.db.get_value(COLUMN, name, "position")
                frappe.db.set_value(COLUMN, name, "position", pos + 1)
        ''')
        self.assertEqual([f[1] for f in found], ["$COLUMN"])


class Passes(unittest.TestCase):
    def test_locked_read(self):
        self.assertEqual(hits('''
            import frappe
            def add_reason(name, reason):
                old = frappe.db.get_value("Cowork Card", name, "reasons", for_update=True)
                frappe.db.set_value("Cowork Card", name, "reasons", old + "," + reason)
        '''), [])

    def test_locked_raw_select_and_relative_update(self):
        self.assertEqual(hits('''
            import frappe
            def bump(name):
                n = frappe.db.sql("select current from `tabSeries` where name=%s for update", (name,))[0][0]
                frappe.db.sql("update `tabSeries` set current = %s where name = %s", (n + 1, name))
            def relative(name, k):
                n = frappe.db.get_value("Series", name, "current")
                frappe.db.sql("update `tabSeries` set current = current + 1 where name = %s", (name,))
        '''), [])

    def test_a_different_table_is_not_an_overwrite(self):
        self.assertEqual(hits('''
            import frappe
            def total(sale):
                lines = frappe.get_all("ERP Sale Line", filters={"parent": sale}, pluck="amount")
                frappe.db.set_value("ERP Sale", sale, "total", sum(lines))
        '''), [])

    def test_dynamic_table_names_are_not_guessed(self):
        self.assertEqual(hits('''
            import frappe
            def claim(name, me):
                rows = frappe.db.sql(f"select name from `tab{LOG}` where name=%s", (name,))
                frappe.db.sql(f"update `tab{LOG}` set owner = %s where name = %s", (me, rows[0]))
        '''), [])

    def test_mark_on_the_line_above_or_on_the_call(self):
        found = hits('''
            import frappe
            def a(name):
                v = frappe.db.get_value("X", name, "v")
                # overwrite-ok: 后写的为准
                frappe.db.set_value("X", name, "v", v)
            def b(name):
                v = frappe.db.get_value("X", name, "v")
                frappe.db.set_value("X", name, "v",
                                    v)  # overwrite-ok: 后写的为准
        ''')
        self.assertEqual([f[3] for f in found], [True, True])


class Repository(unittest.TestCase):
    def test_every_hit_in_the_repository_is_reviewed(self):
        found = check.scan()
        self.assertEqual([f for f in found if not f["marked"]], [])

    def test_the_fixed_read_modify_writes_stay_locked(self):
        """11-23 改成加锁读的几处：去掉 for_update 检查就会报（不是靠标注放过的）。"""
        found = {(f["path"].rsplit("/", 1)[-1], f["function"]) for f in check.scan()}
        for fixed in (("count.py", "_refresh_totals"), ("push_state.py", "withdraw_automation"),
                      ("into_document.py", "merge"), ("operator.py", "apply_intake_answers"),
                      ("service.py", "move_list"), ("service.py", "sort_list"), ("service.py", "move")):
            self.assertNotIn(fixed, found)
        for path, needle in (("apps/pearnly_erp_portal/pearnly_erp_portal/inventory/count.py", "for_update=True"),
                             ("apps/pearnly_cowork/pearnly_cowork/history/push_state.py", "for_update=True"),
                             ("apps/pearnly_cowork/pearnly_cowork/assistant/into_document.py", "for_update=True"),
                             ("apps/pearnly_erp_portal/pearnly_erp_portal/express/operator.py",
                              "frappe.get_doc(chat_intake.DOCTYPE, name, for_update=True)")):
            self.assertIn(needle, (ROOT / path).read_text(encoding="utf-8"))

    def test_main_exit_code(self):
        self.assertEqual(check.main([]), 0)

    def test_registry_entries_must_still_match_code(self):
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as folder:
            registry = Path(folder) / "reviewed.tsv"
            registry.write_text(check.REVIEWED.read_text(encoding="utf-8")
                                + "apps/x.py\tgone\tX\t已经删掉的代码\n", encoding="utf-8")
            with mock.patch.object(check, "REVIEWED", registry):
                self.assertEqual(check.main([]), 1)  # 登记过期
            registry.write_text("apps/x.py\tf\tX\n", encoding="utf-8")
            with mock.patch.object(check, "REVIEWED", registry), self.assertRaises(SystemExit):
                check.reviewed(registry)  # 少了原因

    def test_business_files_carry_no_review_marks(self):
        """确认过的写在登记表里，不为登记去改业务文件（改到入口文件会把影响面拉大）。"""
        self.assertFalse([f for f in check.scan() if f["marked"] and not f["registered"]])


if __name__ == "__main__":
    unittest.main()
