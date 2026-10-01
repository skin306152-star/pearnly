"""识别任务单更新时不再逐个回库核对关联字段（2026-10-01 生产压测：一次识别存 4 次，每次 4 条查询）。

守住：新建照旧核对；关联字段都是不可改字段；没有依赖关联带出的字段（fetch_from）；不可改字段改了照旧拒绝。离线。"""
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import frappe

from pearnly_cowork.ocr.execution import store

BATCH = Path(store.__file__).resolve().parents[2] / "pearnly_cowork/doctype/cowork_ocr_batch/cowork_ocr_batch.json"


class Doc(SimpleNamespace):
    def is_new(self):
        return self.new

    def save(self, ignore_permissions=False):
        self.saved_flags = dict(vars(self.flags))
        return self


class Save(unittest.TestCase):
    def test_updates_skip_link_rechecks_and_inserts_do_not(self):
        existing = store.save(Doc(new=False, flags=SimpleNamespace()))
        self.assertTrue(existing.saved_flags.get("ignore_links"))
        fresh = store.save(Doc(new=True, flags=SimpleNamespace()))
        self.assertFalse(fresh.saved_flags.get("ignore_links"))

    def test_every_link_is_immutable_and_nothing_is_fetched_from_a_link(self):
        meta = json.loads(BATCH.read_text())
        links = {f["fieldname"] for f in meta["fields"] if f["fieldtype"] in ("Link", "Dynamic Link")}
        self.assertTrue(links)
        self.assertLessEqual(links, set(store.IMMUTABLE))
        self.assertEqual([f["fieldname"] for f in meta["fields"] if f.get("fetch_from")], [])

    def test_changing_an_immutable_field_is_still_refused(self):
        previous = frappe._dict({key: "old" for key in store.IMMUTABLE})
        doc = mock.MagicMock()
        doc.flags.cowork_ocr_write = store.WRITE
        doc.get_doc_before_save.return_value = previous
        doc.get.side_effect = lambda key: "new" if key == "workspace" else previous.get(key)
        with mock.patch.object(store, "_", side_effect=lambda text: text), \
                mock.patch.object(store.frappe, "throw", side_effect=frappe.ValidationError):
            with self.assertRaises(frappe.ValidationError):
                store.validate(doc)


if __name__ == "__main__":
    unittest.main()
