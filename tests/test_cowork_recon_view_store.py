"""对账结果页存档：指纹对得上直接回存档，对不上（结果 / 预设 / 语言 / 代码变了）现算一次再存。镜像里跑，不连站点。
真实数据上「存档 = 现算」逐套比对见 docs/work/2026-10-01-billing-throughput.md。"""
import contextlib
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
        self.rows, self.presets, self.rerun_modified, self.preset_scopes = {}, (0, None), None, []

    def sql(self, query, values=None):
        if "COUNT(*)" in query:
            self.preset_scopes.append(values[2])
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

    commits = 0

    def commit(self):
        self.commits += 1

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
            patch.object(view_store, "building", side_effect=lambda *a: contextlib.nullcontext()),
            patch.object(view_store, "refresh_later", side_effect=lambda *a: self.refreshes.append(a)),
        ]
        self.refreshes = []
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

    def test_any_data_change_rebuilds_once(self):
        sheets.load("bank", "CBR-1", "zh")
        changes = [
            lambda: self.doc.update(modified="2026-10-01 10:05:00.000000"),  # 保存修改 / 回答预设后任务变了
            lambda: setattr(self.db, "presets", (1, "2026-10-01 11:00:00")),  # 这家银行的预设改 / 删了
            lambda: setattr(self.db, "rerun_modified", "2026-10-01 12:00:00"),  # 重跑问题记录变了
        ]
        for count, change in enumerate(changes, start=2):
            change()
            sheets.load("bank", "CBR-1", "zh")
            sheets.load("bank", "CBR-1", "zh")
            self.assertEqual(len(self.built), count)
        self.assertEqual(self.refreshes, [])

    def test_a_new_code_version_serves_the_old_archive_and_rebuilds_it_in_the_background(self):
        first = sheets.load("bank", "CBR-1", "zh")
        view_store._code = "another-version"
        try:
            self.assertEqual(sheets.load("bank", "CBR-1", "zh"), first)  # 部署后第一次打开：先给旧的，不让人等
            self.assertEqual(len(self.built), 1)
            self.assertEqual(self.refreshes, [("bank", "CBR-1", "zh")])
            self.assertTrue(sheets.rebuild_view("bank", "CBR-1", "zh"))  # 后台那一份按新代码重建
            self.assertEqual(len(self.built), 2)
            self.assertFalse(sheets.rebuild_view("bank", "CBR-1", "zh"))  # 已是最新就不动
            sheets.load("bank", "CBR-1", "zh")
            self.assertEqual((len(self.built), len(self.refreshes)), (2, 1))
        finally:
            view_store._code = None

    def test_presets_in_the_fingerprint_are_only_those_that_can_touch_this_task(self):
        self.doc.bank_code = "kbank"
        sheets.load("bank", "CBR-1", "zh")
        self.assertEqual(self.db.preset_scopes, ["bank|KBANK|%"])
        self.assertIsNone(view_store._preset_scope("vat", self.doc))
        with patch.object(sheets.revision, "income_tax_id", return_value="0105556012345"):
            self.assertEqual(view_store._preset_scope("income", self.doc), "income|0105556012345|%")
        with patch.object(sheets.revision, "income_tax_id", return_value=""):
            self.assertEqual(view_store._preset_scope("income", self.doc), "%")

    def test_an_unknown_bank_counts_only_presets_of_its_own_tail(self):
        # 复查 2026-10-13：通用版式按尾号收窄，一个账户答题不作废别的通用版式存档；没有读出尾号的本来就没有预设。
        self.doc.bank_code = "generic"
        self.doc.parse_info_json = '{"stmt_files": [{"ok": true, "account": "4821", "account_read": true}]}'
        sheets.load("bank", "CBR-1", "zh")
        self.assertEqual(self.db.preset_scopes, ["bank|GENERIC|4821|%"])
        self.doc.parse_info_json = '{"stmt_files": [{"ok": true, "account": "2026"}]}'
        self.assertIsNone(view_store._preset_scope("bank", self.doc))
        self.doc.bank_code = "kbank"
        self.assertEqual(view_store._preset_scope("bank", self.doc), "bank|KBANK|%")

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

    def test_only_code_that_shapes_the_page_is_fingerprinted(self):
        view_store._code = None
        with patch.object(view_store.Path, "read_bytes", autospec=True, side_effect=lambda path: (
                self.read.append(path.name) or b"")):
            self.read = []
            view_store._code_fingerprint()
        view_store._code = None
        self.assertIn("sheets.py", self.read)
        self.assertNotIn("view_store.py", self.read)  # 存档怎么存不影响页面：改它不让存档作废

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
            built, commits = len(self.built), self.db.commits
            view_store.backfill(("th", "zh"))  # 第二次全部跳过：不重算
            self.assertEqual(len(self.built), built)
            # 部署只换了代码：补算不重算（打开时先给旧的、后台重建），不在部署时把所有对账重算一遍
            view_store._code = "another-version"
            try:
                view_store.backfill(("th", "zh"))
                self.assertEqual(len(self.built), built)
            finally:
                view_store._code = None
            # 跳过的也逐套结束读快照：不能整个补算开着一个事务（2026-10-02 压测锁采样：最长事务 287 秒）
            self.assertEqual(self.db.commits - commits, 12)
            with patch.object(sheets, "_build", side_effect=RuntimeError("broken")):
                self.db.rows.clear()
                view_store.backfill(("th",))  # 一套失败只记日志，绝不往外抛


if __name__ == "__main__":
    unittest.main()
