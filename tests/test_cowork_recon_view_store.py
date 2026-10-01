"""对账结果页存档：指纹对得上直接回存档，对不上（结果 / 预设 / 语言 / 代码变了）现算一次再存。镜像里跑，不连站点。
真实数据上「存档 = 现算」逐套比对见 docs/work/2026-10-01-billing-throughput.md。"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "pearnly_cowork"))
sys.path.insert(0, str(ROOT / "apps" / "pearnly_core"))
for name in [name for name in sys.modules if name.startswith(("pearnly_cowork", "pearnly_core"))]:
    del sys.modules[name]

import frappe  # noqa: E402

from pearnly_cowork.recon_center import sheets, view_store  # noqa: E402

assert Path(view_store.__file__).is_relative_to(ROOT)


class FakeDb:
    def __init__(self):
        self.rows, self.presets, self.rerun_modified = {}, (0, None), None

    def sql(self, query, values=None):
        if "COUNT(*)" in query:
            return [self.presets]
        if query.startswith("SELECT fingerprint"):
            row = self.rows.get(values[0])
            return [row] if row else []
        if "INSERT INTO" in query:
            self.rows[values["name"]] = (values["print"], values["payload"])
            return []
        if query.startswith("DELETE"):
            for name in [name for name in self.rows if f":{values[0]}:" in name]:
                del self.rows[name]
            return []
        raise AssertionError(query)

    def get_value(self, doctype, name, field):
        return self.rerun_modified

    def table_exists(self, name):
        return True

    def commit(self):
        pass

    def rollback(self):
        pass


class ViewStoreTest(unittest.TestCase):
    def setUp(self):
        self.db = FakeDb()
        self.doc = frappe._dict(name="CBR-1", workspace="W", modified="2026-10-01 10:00:00.000001", title="t",
                                status="completed")
        self.built = []
        self.patches = [
            patch.object(frappe, "db", self.db, create=True),
            patch.object(sheets, "_task", side_effect=lambda kind, task: self.doc if task == self.doc.name
                         else frappe._dict(self.doc, name=task)),
            patch.object(sheets.revision, "revision", side_effect=lambda doc: 3),
            patch.object(sheets, "_build", side_effect=self.build),
        ]
        for item in self.patches:
            item.start()
        frappe.local.lang = "zh"

    def tearDown(self):
        for item in self.patches:
            item.stop()

    def build(self, kind, doc, lang, version):
        self.built.append((kind, doc.name, lang, version))
        return {"task": {"name": doc.name, "modified": doc.modified, "revision": version}, "sheets": [len(self.built)]}

    def test_second_open_reads_the_archive(self):
        first = sheets.load("bank", "CBR-1", "zh")
        second = sheets.load("bank", "CBR-1", "zh")
        self.assertEqual(len(self.built), 1)
        self.assertEqual(second, first)

    def test_any_change_that_affects_the_page_rebuilds_once(self):
        sheets.load("bank", "CBR-1", "zh")
        changes = [
            lambda: self.doc.update(modified="2026-10-01 10:05:00.000000"),  # 保存修改 / 回答预设后任务变了
            lambda: setattr(self.db, "presets", (1, "2026-10-01 11:00:00")),  # 改 / 删预设
            lambda: setattr(self.db, "rerun_modified", "2026-10-01 12:00:00"),  # 重跑问题记录变了
            lambda: setattr(view_store, "_code", "another-version"),  # 换了版本的代码
        ]
        for count, change in enumerate(changes, start=2):
            change()
            sheets.load("bank", "CBR-1", "zh")
            sheets.load("bank", "CBR-1", "zh")
            self.assertEqual(len(self.built), count)
        view_store._code = None

    def test_each_page_and_interface_language_has_its_own_archive(self):
        sheets.load("bank", "CBR-1", "zh")
        sheets.load("bank", "CBR-1", "th")
        frappe.local.lang = "en"
        sheets.load("bank", "CBR-1", "zh")
        frappe.local.lang = "zh"
        sheets.load("bank", "CBR-1", "zh")
        self.assertEqual(len(self.built), 3)
        self.assertEqual(sorted(self.db.rows), ["bank:CBR-1:th-zh", "bank:CBR-1:zh-en", "bank:CBR-1:zh-zh"])

    def test_a_broken_archive_is_rebuilt_and_deleting_the_task_drops_it(self):
        sheets.load("bank", "CBR-1", "zh")
        name = "bank:CBR-1:zh-zh"
        self.db.rows[name] = (self.db.rows[name][0], "{not json")
        sheets.load("bank", "CBR-1", "zh")
        self.assertEqual(len(self.built), 2)
        view_store.drop("CBR-1")
        self.assertEqual(self.db.rows, {})

    def test_finished_job_prepares_the_archive_before_announcing(self):
        source = (ROOT / "apps/pearnly_cowork/pearnly_cowork/recon_center/jobs.py").read_text()
        self.assertLess(source.index('_prepare_view(kind, result["name"]'), source.index('_state(recon_job, state="done"'))
        api = (ROOT / "apps/pearnly_cowork/pearnly_cowork/recon_center/api.py").read_text()
        self.assertIn("view_store.drop(task)", api)

    def test_backfill_stores_every_task_in_each_interface_language_and_never_raises(self):
        tasks = {"Cowork Bank Reconciliation": [frappe._dict(name="CBR-1", owner="a@x")],
                 "Cowork Income Reconciliation": [frappe._dict(name="CIR-1", owner="b@x")],
                 "Cowork VAT Check": [frappe._dict(name="CVC-1", owner="c@x")]}
        users = []
        self.db.count = lambda doctype, filters=None: (len([n for n in self.db.rows if n == filters["name"]])
                                                       if filters else len(self.db.rows))
        with patch.object(frappe, "get_all", side_effect=lambda doctype, **kw: tasks[doctype]), \
                patch.object(frappe, "set_user", side_effect=users.append), \
                patch.object(frappe, "session", frappe._dict(user="Administrator"), create=True), \
                patch.object(frappe, "log_error"):
            view_store.backfill(("th", "zh"))
            self.assertEqual(sorted(self.db.rows), ["bank:CBR-1:th-th", "bank:CBR-1:zh-zh", "income:CIR-1:th-th",
                                                    "income:CIR-1:zh-zh", "vat:CVC-1:th-th", "vat:CVC-1:zh-zh"])
            self.assertEqual(users[-1], "Administrator")  # 以发起人身份算完换回原用户
            self.assertIn("a@x", users)
            built = len(self.built)
            view_store.backfill(("th", "zh"))  # 第二次全部跳过：不重算
            self.assertEqual(len(self.built), built)
            with patch.object(sheets, "_build", side_effect=RuntimeError("broken")):
                self.db.rows.clear()
                view_store.backfill(("th",))  # 一套失败只记日志，绝不往外抛


if __name__ == "__main__":
    unittest.main()
