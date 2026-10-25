"""库存单附件（入库 / 出库附图片、PDF、拍照；主会话 10-26）：只挂本人刚上传、私有、没挂过的 File；每张单最多 10 个；
已提交 / 作废的单只能加不能删；作废的单不再加；读写都跟着单据的方向能力。共用件的表用内存替身，不连库。"""
import pathlib
import sys
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "apps/pearnly_core"), str(ROOT / "apps/pearnly_erp_portal")]

import cc_desktop_fake as fake  # noqa: E402

frappe = fake.install()
utils = sys.modules.setdefault("frappe.utils", types.ModuleType("frappe.utils"))
for name in ("now", "flt", "cint", "nowdate", "today", "getdate", "add_days", "now_datetime"):
    if not hasattr(utils, name):
        setattr(utils, name, lambda *a, **k: None)

# 原件层登记件要 werkzeug（开发检查的裸 Python 没有）：这里只用到它的 filename，换成替身
registered = types.ModuleType("pearnly_core.originals.registered")
registered.filename = lambda name: name
sys.modules.setdefault("pearnly_core.originals.registered", registered)
import pearnly_core.originals as originals_package  # noqa: E402

originals_package.registered = sys.modules["pearnly_core.originals.registered"]

from pearnly_erp_portal.inventory import attachments as A  # noqa: E402

USER = "clerk@x"


class Files(dict):
    """File 表替身：``{name: {owner, is_private, attached_to_doctype, attached_to_name, file_url}}``。"""


class Attachments(unittest.TestCase):
    def setUp(self):
        self.files = Files()
        self.entry = types.SimpleNamespace(name="STE-1", direction="in", status="submitted")
        self.required = []

        def get_doc(doctype, name):
            row = self.files[name]
            doc = types.SimpleNamespace(name=name, **row)
            doc.db_set = lambda values, update_modified=False: row.update(values)
            return doc

        def get_all(doctype, filters=None, fields=None, **kw):
            rows = [types.SimpleNamespace(name=n, file_name=n, creation=n, **r) for n, r in self.files.items()
                    if r.get("attached_to_doctype") == filters["attached_to_doctype"]
                    and r.get("attached_to_name") == filters["attached_to_name"]]
            return rows

        self.patches = [mock.patch.object(A.documents, "_get", lambda name: self.entry),
                        mock.patch.object(A.guard, "require", lambda cap: self.required.append(cap)),
                        mock.patch.object(frappe, "get_doc", side_effect=get_doc, create=True),
                        mock.patch.object(frappe, "get_all", side_effect=get_all, create=True),
                        mock.patch.object(frappe.db, "exists", lambda doctype, name=None: name in self.files, create=True),
                        mock.patch.object(frappe.db, "sql", lambda *a, **k: None, create=True),
                        mock.patch.object(frappe.db, "commit", lambda: None, create=True),
                        mock.patch.object(A.registered, "filename", lambda name: f"{name}.jpg"),
                        mock.patch.object(frappe, "session", types.SimpleNamespace(user=USER), create=True)]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def upload(self, name, owner=USER, private=1, attached=""):
        self.files[name] = {"owner": owner, "is_private": private, "attached_to_doctype": "ERP Stock Entry" if attached else "",
                            "attached_to_name": attached, "file_url": f"/private/files/{name}.bin"}

    def test_own_uploads_attach_and_need_the_direction_capability(self):
        self.upload("F1")
        self.upload("F2")
        rows = A.add("STE-1", ["F1", "F2", "F1"])
        self.assertEqual([row["name"] for row in rows], ["F1", "F2"])
        self.assertEqual(self.files["F1"]["attached_to_name"], "STE-1")
        self.assertIn("stock.in", self.required)  # 入库单要入库能力
        self.entry.direction = "out"
        A.files("STE-1")
        self.assertIn("stock.out", self.required)

    def test_someone_elses_public_or_already_attached_file_is_refused(self):
        self.upload("OTHER", owner="boss@x")
        self.upload("PUBLIC", private=0)
        self.upload("USED", attached="STE-9")
        for name in ("OTHER", "PUBLIC", "USED", "MISSING"):
            with self.assertRaises(Exception, msg=name):
                A.add("STE-1", [name])

    def test_ten_at_most_and_checked_before_the_entry_is_made(self):
        for i in range(10):
            self.upload(f"F{i}", attached="STE-1")
        self.upload("F10")
        with self.assertRaises(Exception):
            A.add("STE-1", ["F10"])
        names = [f"N{i}" for i in range(11)]
        for name in names:
            self.upload(name)
        with self.assertRaises(Exception):
            A.check(names)  # 新建时 11 个：建单前就拦
        self.assertEqual(A.check(names[:10]), names[:10])

    def test_submitted_entries_keep_their_attachments_and_void_ones_take_no_more(self):
        self.upload("F1", attached="STE-1")
        deleted = []
        with mock.patch.object(frappe, "delete_doc", lambda doctype, name, **k: deleted.append(name), create=True), \
                mock.patch.object(frappe.db, "get_value", lambda doctype, name, fields, as_dict=False: types.SimpleNamespace(
                    attached_to_doctype="ERP Stock Entry", attached_to_name="STE-1"), create=True):
            with self.assertRaises(Exception):
                A.remove("STE-1", "F1")  # 已提交：只能加不能删
            self.entry.status = "draft"
            A.remove("STE-1", "F1")
        self.assertEqual(deleted, ["F1"])
        self.entry.status = "void"
        self.upload("F2")
        with self.assertRaises(Exception):
            A.add("STE-1", ["F2"])
        self.assertEqual([row["name"] for row in A.files("STE-1")], ["F1"])  # 作废的单附件照留、照能看


if __name__ == "__main__":
    unittest.main()
