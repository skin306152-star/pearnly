#!/usr/bin/env python3
"""统一原件层入口（ADR-024，``pearnly_core.originals.store``）：按租户路线选存放、直传两步、生产交字节、引用解析、删除分组。
不联网（甲骨文 / 谷歌 / Cloud Run 的调用都打桩）；在生产镜像里跑。"""
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "pearnly_core"))

import frappe  # noqa: E402

from pearnly_core.originals import store  # noqa: E402

SITE = "acme.pearnly.com"
ID = "ERO-" + "a" * 48
SHA = "f" * 64
OK_META = {"filename": "a.pdf", "suffix": ".pdf", "kind": "pdf", "page_count": 2, "byte_size": 10, "sha256": SHA}


class Store(unittest.TestCase):
    def setUp(self):
        frappe.local.site = SITE
        self.addCleanup(lambda: delattr(frappe.local, "site"))

    def route(self, engine, *, oracle=True, bucket="b", cloud_run=True):
        patches = [mock.patch.object(store, "where", wraps=store.where),
                   mock.patch("pearnly_core.claude_ocr.client.uses_claude", return_value=engine == "claude"),
                   mock.patch.object(store.oracle, "enabled", return_value=oracle),
                   mock.patch.object(store.gcs, "bucket", return_value=bucket),
                   mock.patch.object(store.cloud_run, "settings", return_value={"url": "https://x.run.app"} if cloud_run else None)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_route_picks_the_store(self):
        self.route("claude")
        self.assertEqual(store.where(), store.ORACLE)
        mock.patch.stopall()
        self.route("google")
        self.assertEqual(store.where(), store.GCS)
        mock.patch.stopall()
        self.route("google", cloud_run=False)
        self.assertEqual(store.where(), store.LOCAL)  # 谷歌那一路的核对要 Cloud Run：没配就照旧本机
        mock.patch.stopall()
        self.route("claude", oracle=False)
        self.assertEqual(store.where(), store.LOCAL)  # Claude 租户绝不落到谷歌存储

    def test_claude_upload_goes_to_oracle(self):
        self.route("claude")
        name = f"{SITE}/originals/{ID}.pdf"
        with mock.patch.object(store.oracle, "upload_target", return_value={"url": "u", "headers": {}}) as target:
            self.assertEqual(store.begin_upload(ID, ".pdf", 10), {"direct": 1, "url": "u", "headers": {}})
        target.assert_called_once_with(name, 10)
        with mock.patch.object(store.oracle, "inspect", return_value={"status": "ok", "meta": OK_META, "md5": "m"}):
            done = store.finish_upload(ID, ".pdf", "a.pdf")
        self.assertEqual(done["ref"], f"oracle://{name}#{SHA}")
        with mock.patch.object(store.oracle, "put", return_value={"status": "ok", "meta": OK_META, "md5": "m"}) as put, \
                mock.patch.object(store.gcs, "upload") as gcs_upload:
            self.assertEqual(store.put(ID, ".pdf", "a.pdf", b"%PDF")["ref"], f"oracle://{name}#{SHA}")
        put.assert_called_once_with(name, "a.pdf", b"%PDF")
        gcs_upload.assert_not_called()  # 选了 Claude：一个字节也不进谷歌
        self.assertEqual(store.parse(done["ref"]), (store.ORACLE, (name, SHA)))
        self.assertEqual(store.recognition_reference(done["ref"]), (name, SHA))

    def test_google_upload_goes_to_the_bucket_and_cloud_run_checks_it(self):
        self.route("google")
        name = f"{SITE}/originals/{ID}.pdf"
        with mock.patch.object(store.gcs, "upload_target", return_value={"url": "g", "headers": {}}):
            self.assertEqual(store.begin_upload(ID, ".pdf", 10)["url"], "g")
        with mock.patch.object(store.cloud_run, "inspect",
                               return_value={"status": "ok", "meta": OK_META, "md5": "m", "generation": 7}):
            done = store.finish_upload(ID, ".pdf", "a.pdf")
        self.assertEqual(done["ref"], f"gs://b/{name}#7")
        with mock.patch.object(store.gcs, "upload") as upload, mock.patch.object(
                store.cloud_run, "inspect", return_value={"status": "rejected", "code": "pdf_password"}):
            self.assertEqual(store.put(ID, ".pdf", "a.pdf", b"x"), {"status": "rejected", "code": "pdf_password"})
        upload.assert_called_once_with("b", name, b"x")
        self.assertIsNone(store.preview_url(done["ref"], "pdf", 1))  # 谷歌的预览走产品原有的存好预览 / Cloud Run 现画

    def test_unconfigured_site_keeps_local_storage(self):
        self.route("google", bucket=None)
        self.assertEqual(store.begin_upload(ID, ".pdf", 10), {"direct": 0})
        self.assertIsNone(store.put(ID, ".pdf", "a.pdf", b"x"))
        self.assertIsNone(store.parse("/private/files/a.pdf"))
        with self.assertRaises(RuntimeError):
            store.finish_upload(ID, ".pdf", "a.pdf")

    def test_delete_groups_by_store_and_takes_previews_along(self):
        oracle_ref = f"oracle://{SITE}/originals/{ID}.pdf#{SHA}"
        gcs_ref = f"gs://b/{SITE}/originals/CWS-{'c' * 48}.jpg#3"
        with mock.patch.object(store.oracle, "delete") as oracle_delete, \
                mock.patch.object(store.gcs, "delete_prefix") as gcs_delete:
            store.delete([oracle_ref, gcs_ref, "/private/files/x.pdf", None])
        oracle_delete.assert_called_once_with([f"{SITE}/originals/{ID}.pdf"])
        self.assertEqual([c.args for c in gcs_delete.call_args_list],
                         [("b", f"{SITE}/originals/CWS-{'c' * 48}.jpg"), ("b", f"{SITE}/previews/CWS-{'c' * 48}/")])


if __name__ == "__main__":
    unittest.main()
