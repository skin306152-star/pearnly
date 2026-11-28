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


def repo_hits(files: dict):
    """几个模块放进临时仓库（``apps/demo/demo/<名>.py``）一起扫：三类新写法要跨函数、跨模块看。回 ``[(类别, 函数, 表)]``。"""
    import tempfile
    from unittest import mock
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        for name, source in files.items():
            path = root / "apps" / "demo" / "demo" / f"{name}.py"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(source), encoding="utf-8")
        with mock.patch.object(check, "REVIEWED", root / "none.tsv"), \
                mock.patch.object(check, "EXT_REVIEWED", root / "none2.tsv"), \
                mock.patch.object(check, "BASELINE", root / "none3.tsv"):
            return [(f["kind"], f["function"], f["doctype"]) for f in check.scan(root, extended=True) if not f["marked"]]


class Extended(unittest.TestCase):
    """11-28（B ③）补的三类：借函数读、跨函数传值、判断用旧值。"""

    def test_helper_that_locks_the_name_but_reads_the_doc_plainly(self):
        found = repo_hits({"tasks": '''
            import frappe
            DOCTYPE = "Operator Task"
            def _own(name, for_update=False):
                row = frappe.db.get_value(DOCTYPE, name, "name", for_update=for_update)
                return frappe.get_doc(DOCTYPE, row)
        ''', "listed": '''
            import frappe
            from demo import tasks
            def remember(task, rows):
                doc = tasks._own(task, for_update=True)
                snapshot = dict(doc.listed or {})
                snapshot.update(rows)
                frappe.db.set_value(tasks.DOCTYPE, doc.name, "listed", snapshot)
        '''})
        self.assertIn(("借函数", "remember", "Operator Task"), found)

    def test_helper_with_a_locked_read_passes(self):
        found = repo_hits({"listed": '''
            import frappe
            DOCTYPE = "Operator Task"
            def _locked(name):
                return frappe.db.get_value(DOCTYPE, name, ["name", "listed"], as_dict=True, for_update=True)
            def remember(task, rows):
                doc = _locked(task)
                snapshot = dict(doc.listed or {})
                frappe.db.set_value(DOCTYPE, doc.name, "listed", snapshot)
        '''})
        self.assertEqual(found, [])

    def test_value_read_here_written_by_another_function(self):
        found = repo_hits({"automation": '''
            import frappe
            AUTOMATION = "Company Automation"
            def state(company):
                return frappe.db.get_value(AUTOMATION, {"company": company}, ["tier"], as_dict=True)
            def _set(company, tier):
                name = frappe.db.get_value(AUTOMATION, {"company": company}, "name", for_update=True)
                doc = frappe.get_doc(AUTOMATION, name, for_update=True)
                doc.tier, doc.reason = tier, "x"
                doc.save()
            def on_readback(company):
                current = state(company)
                tier = current.tier
                _set(company, tier)
        '''})
        self.assertIn(("跨函数", "on_readback", "Company Automation"), found)

    def test_value_read_with_a_lock_passed_to_the_writer_passes(self):
        found = repo_hits({"automation": '''
            import frappe
            AUTOMATION = "Company Automation"
            def state(company):
                return frappe.db.get_value(AUTOMATION, {"company": company}, ["tier"], as_dict=True, for_update=True)
            def _set(company, tier):
                frappe.db.set_value(AUTOMATION, company, "tier", tier)
            def on_readback(company):
                _set(company, state(company).tier)
        '''})
        self.assertEqual(found, [])

    def test_a_name_used_only_as_the_key_is_not_a_value(self):
        found = repo_hits({"m": '''
            import frappe
            def _mark(name):
                frappe.db.set_value("Card", name, "seen", 1)
            def touch(card):
                row = frappe.db.get_value("Card", card, ["name"], as_dict=True)
                _mark(row.name)
        '''})
        self.assertEqual(found, [])

    def test_whether_to_write_decided_by_a_stale_read(self):
        found = repo_hits({"push_state": '''
            import frappe
            def withdrawn(row):
                found = frappe.db.get_value("OCR History", row.history, "last_push_record")
                if found != row.name:
                    return
                frappe.db.set_value("OCR History", row.history, {"last_push_status": None})
            def queue(issue):
                doc = frappe.get_doc("Issue", issue)
                if not doc.get("queued"):
                    frappe.db.set_value("Issue", issue, "queued", "Q-1")
            def drop(history):
                records = frappe.get_all("Push Record", filters={"history": history}, pluck="name")
                if records:
                    frappe.db.delete("Push Record", {"name": ["in", records]})
        '''})
        self.assertEqual(sorted(found), [("判断", "drop", "Push Record"), ("判断", "queue", "Issue"),
                                         ("判断", "withdrawn", "OCR History")])

    def test_default_mode_does_not_report_the_new_kinds(self):
        """不带 ``--extended`` 时行为同 11-23（CI 现有那一步不变）；新三类由开关另跑。"""
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "apps" / "demo" / "demo" / "m.py"
            path.parent.mkdir(parents=True)
            path.write_text(textwrap.dedent('''
                import frappe
                def withdrawn(row):
                    if frappe.db.get_value("OCR History", row.history, "x") != row.name:
                        return
                    frappe.db.set_value("OCR History", row.history, "y", None)
            '''), encoding="utf-8")
            with mock.patch.object(check, "REVIEWED", root / "none.tsv"):
                self.assertEqual(check.scan(root), [])
                self.assertEqual([f["kind"] for f in check.scan(root, extended=True)], ["判断"])

    def test_baseline_rejects_false_positives_and_unknown_owners(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            table = Path(folder) / "baseline.tsv"
            table.write_text("a.py\tf\tX\t判断\t②\t误报\t原因\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                check.keyed(table, 7)  # 误报不进基线
            table.write_text("a.py\tf\tX\t判断\t⑤\t真\t原因\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                check.keyed(table, 7)
            table.write_text("a.py\tf\tX\t判断\t④\t真\t\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                check.keyed(table, 7)  # 原因不许空
            table.write_text("a.py\tf\tX\t判断\t④\t真\t连发丢失\n", encoding="utf-8")
            self.assertEqual(check.keyed(table, 7), {("a.py", "f", "X", "判断"): ["④", "真", "连发丢失"]})

    def test_whether_to_write_decided_by_a_locked_read_passes(self):
        found = repo_hits({"push_state": '''
            import frappe
            def withdrawn(row):
                found = frappe.db.get_value("OCR History", row.history, "last_push_record", for_update=True)
                if found != row.name:
                    return
                frappe.db.set_value("OCR History", row.history, {"last_push_status": None})
            def other_table(row):
                found = frappe.db.get_value("Push Record", row.name, "status")
                if found == "x":
                    frappe.db.set_value("OCR History", row.history, {"last_push_status": None})
        '''})
        self.assertEqual(found, [])


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

    def test_extended_mode_has_no_new_hit_and_no_stale_line(self):
        """三类新写法：仓库里每一处不是登记的误报就是存量基线，没有新增；修好的同一提交删掉那一行（基线只许减少）。"""
        self.assertEqual(check.main(["--extended"]), 0)

    def test_the_b3_fixes_are_not_in_the_baseline(self):
        """11-28 B ③ 修好的几处不在基线里、也不会再报（去掉加锁读就会回来）。"""
        lines = check.BASELINE.read_text(encoding="utf-8") + check.EXT_REVIEWED.read_text(encoding="utf-8")
        for path, function, table in (("express_operator/listed.py", "remember", "Pearnly Express Operator Task"),
                                      ("companies/automation.py", "on_readback", "Cowork Company Automation"),
                                      ("companies/automation.py", "_set", "Cowork Company Automation"),
                                      ("history/push_state.py", "withdrawn", "Cowork OCR History"),
                                      ("history/push_state.py", "retarget", "Cowork OCR History"),
                                      ("history/removal.py", "_drop_related", "Cowork Push Record")):
            self.assertFalse([line for line in lines.splitlines() if path in line and f"\t{function}\t{table}\t" in line],
                             (path, function))

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
