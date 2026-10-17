#!/usr/bin/env python3
"""原件层的登记文件（ADR-024，``pearnly_core.originals.registered``）与 ERP 附件原件上传（``recognition.originals``）：
下载转签名地址、删最后一条才删原件、复制不再存一次、预览由存放那边画；ERP 直传核对、甲骨文按生产同样做法读几份附件。
不联网，在生产镜像里跑。"""
import hashlib
import io
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for app in ("pearnly_core", "pearnly_erp_portal"):
    sys.path.insert(0, str(ROOT / "apps" / app))

import frappe  # noqa: E402

from pearnly_core.claude_ocr import client, pages, server  # noqa: E402
from pearnly_core.ocr_models import orient  # noqa: E402
from pearnly_core.originals import registered  # noqa: E402

SITE = "acme.pearnly.com"
ID = "ERO-" + "b" * 48
OBJECT = f"{SITE}/originals/{ID}.pdf"
SHA = "c" * 64
ENTRY = {"filename": "Bill 7.pdf", "ref": f"oracle://{OBJECT}#{SHA}", "sha256": SHA, "pages": 3, "kind": "pdf",
         "byte_size": 10}


def stub(entry=ENTRY, name=None):
    import json

    content = registered.PREFIX + json.dumps(entry).encode()
    return types.SimpleNamespace(name=name or "F1", file_name=ID + ".bin", content_hash="h1", get_content=lambda: content)


def pdf(count=2):
    import pymupdf

    with pymupdf.open() as document:
        for number in range(1, count + 1):
            document.new_page().insert_text((72, 72), f"Invoice page {number}  2,000.00")
        return document.tobytes()


def jpeg():
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (800, 1100), (240, 240, 230)).save(buffer, "JPEG", quality=90)
    return buffer.getvalue()


class Registration(unittest.TestCase):
    def test_only_registration_files_are_read_as_registrations(self):
        self.assertEqual(registered.entry(stub()), ENTRY)
        self.assertEqual(registered.filename(stub()), "Bill 7.pdf")
        self.assertEqual(registered.sha256(stub()), SHA)
        self.assertEqual(registered.stored(stub()), (OBJECT, SHA))
        local = types.SimpleNamespace(file_name="bill.pdf", get_content=lambda: b"%PDF-1.4 x")
        self.assertIsNone(registered.entry(local))
        self.assertEqual(registered.filename(local), "bill.pdf")
        self.assertEqual(registered.content(local), b"%PDF-1.4 x")
        self.assertIsNone(registered.stored(local))
        gcs = dict(ENTRY, ref=f"gs://bucket/{OBJECT}#5")
        self.assertEqual(registered.stored(stub(gcs)), ("bucket", OBJECT, 5, SHA))

    def test_put_keeps_only_a_small_registration_and_converts_iphone_photos(self):
        inserted = []
        result = {"status": "ok", "ref": ENTRY["ref"], "md5": "m",
                  "meta": {"sha256": SHA, "page_count": 3, "kind": "pdf", "byte_size": 10}}
        with mock.patch.object(registered.store, "put", return_value=result) as put, \
                mock.patch.object(registered.frappe, "get_doc", side_effect=lambda values: types.SimpleNamespace(
                    insert=lambda **_k: inserted.append(values) or values)):
            registered.put(ID, "Bill 7.pdf", b"%PDF", attached_to_doctype="X")
        put.assert_called_once_with(ID, ".pdf", "Bill 7.pdf", b"%PDF")
        values = inserted[0]
        self.assertEqual((values["file_name"], values["is_private"], values["attached_to_doctype"]), (ID + ".bin", 1, "X"))
        self.assertLess(len(values["content"]), 1024)
        with mock.patch.object(registered.store, "put", return_value=None) as put, \
                mock.patch("pearnly_core.desk.heic.is_heic", return_value=True), \
                mock.patch("pearnly_core.desk.heic.to_jpeg", return_value=b"jpeg"):
            self.assertIsNone(registered.put(ID, "IMG_1.HEIC", b"heic"))  # 没配原件层：调用方照旧存本机
        self.assertEqual(put.call_args.args[1:], (".jpg", "IMG_1.jpg", b"jpeg"))

    def test_a_copy_shares_the_registration_instead_of_storing_again(self):
        inserted = []
        with mock.patch.object(registered.frappe, "get_doc", side_effect=lambda values: types.SimpleNamespace(
                insert=lambda **_k: inserted.append(values) or values)), \
                mock.patch.object(registered.store, "read", side_effect=AssertionError("fetched")):
            registered.copy(stub(), attached_to_name="PUR-2")
        self.assertEqual(inserted[0]["content"], stub().get_content())  # 同一份登记（Frappe 按内容共用同一个文件）
        self.assertEqual(inserted[0]["attached_to_name"], "PUR-2")

    def test_previews_are_drawn_where_the_original_is(self):
        with mock.patch.object(registered.store, "preview_url", return_value="https://originals/p2") as preview:
            self.assertEqual(registered.page_image(stub(), 2), "https://originals/p2")
        preview.assert_called_once_with(ENTRY["ref"], "pdf", 2)
        gcs = stub(dict(ENTRY, ref=f"gs://bucket/{OBJECT}#5"))
        png = b"\x89PNG\r\n\x1a\nxx"
        with mock.patch.object(registered.store, "render", return_value={"png": png}) as render:
            self.assertTrue(registered.page_image(gcs, 1).startswith("data:image/png;base64,"))
        render.assert_called_once_with(f"gs://bucket/{OBJECT}#5", SHA, "pdf", 1)
        with self.assertRaises(ValueError):
            registered.page_image(stub(), 4)
        self.assertIsNone(registered.page_image(types.SimpleNamespace(file_name="a.pdf"), 1))


class Download(unittest.TestCase):
    def open(self, path, user="buyer@example.com", found=None):
        request = types.SimpleNamespace(path=path)
        with mock.patch.object(registered.frappe, "local", types.SimpleNamespace(request=request), create=True), \
                mock.patch.object(registered.frappe, "session", types.SimpleNamespace(user=user), create=True), \
                mock.patch.object(registered.frappe, "form_dict", frappe._dict(), create=True), \
                mock.patch("frappe.core.doctype.file.utils.find_file_by_url", return_value=found) as find, \
                mock.patch("frappe.core.doctype.access_log.access_log.make_access_log"), \
                mock.patch.object(registered.store, "download_url", return_value="https://originals/dl") as url:
            try:
                registered.redirect_download()
            except registered._Redirect as redirect:
                return redirect.get_response(), find, url
            return None, find, url

    def test_a_readable_registration_goes_to_the_signed_download(self):
        response, find, url = self.open(f"/private/files/{ID}.bin", found=stub())
        self.assertEqual((response.status_code, response.headers["Location"]), (302, "https://originals/dl"))
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")
        url.assert_called_once_with(ENTRY["ref"], "Bill 7.pdf")
        find.assert_called_once()  # 能不能看照 Frappe 自己的私有文件规则（is_downloadable）

    def test_everything_else_is_left_to_frappe(self):
        for path, user, found in ((f"/private/files/{ID}.bin", "buyer@example.com", None),  # 不能看：Frappe 回无权限
                                  (f"/private/files/{ID}.bin", "Guest", stub()),
                                  ("/private/files/bill.pdf", "buyer@example.com", stub()),
                                  ("/app/purchase", "buyer@example.com", stub())):
            response, _find, url = self.open(path, user, found)
            self.assertIsNone(response)
            url.assert_not_called()


class Delete(unittest.TestCase):
    def trash(self, doc, others=0):
        calls = []

        class Base:
            def on_trash(self):
                calls.append("frappe")

        class File(registered.RegisteredFile, Base):
            pass

        file = File()
        file.__dict__.update(vars(doc))
        db = mock.MagicMock()
        db.count.return_value = others
        with mock.patch.object(registered.frappe, "db", db), \
                mock.patch.object(registered.frappe, "enqueue", side_effect=lambda *a, **k: calls.append(k)):
            file.on_trash()
        return calls

    def test_the_last_registration_takes_the_original_along(self):
        calls = self.trash(stub())
        self.assertEqual(calls[0], "frappe")
        self.assertEqual(calls[1]["refs"], [ENTRY["ref"]])
        self.assertTrue(calls[1]["enqueue_after_commit"])  # 删除真的提交了才删原件

    def test_a_shared_registration_or_a_local_file_keeps_the_original(self):
        self.assertEqual(self.trash(stub(), others=1), ["frappe"])
        self.assertEqual(self.trash(types.SimpleNamespace(file_name="a.pdf", content_hash="h", name="F2")), ["frappe"])


class ErpUpload(unittest.TestCase):
    def setUp(self):
        from pearnly_erp_portal.recognition import originals

        self.originals = originals
        for patch in (mock.patch.object(originals.guard, "require"),
                      mock.patch.object(originals.frappe, "local", types.SimpleNamespace(site=SITE), create=True),
                      mock.patch.object(originals.frappe, "session", types.SimpleNamespace(user="buyer@example.com"),
                                        create=True),
                      mock.patch.object(originals, "_", side_effect=lambda text: text),
                      mock.patch.object(originals.frappe, "throw", side_effect=frappe.ValidationError),
                      mock.patch.object(originals, "_existing", return_value=None)):
            patch.start()
            self.addCleanup(patch.stop)

    def test_ids_are_per_site_person_direction_and_key(self):
        one = self.originals._original_id("purchase", "key-0001")
        self.assertRegex(one, r"^ERO-[0-9a-f]{48}$")
        self.assertEqual(one, self.originals._original_id("purchase", "key-0001"))  # 重试同一份
        self.assertNotEqual(one, self.originals._original_id("sales", "key-0001"))
        for bad in ("short", "../../x", None):
            with self.assertRaises(frappe.ValidationError):
                self.originals._original_id("purchase", bad)

    def test_direct_upload_is_signed_checked_then_registered(self):
        with mock.patch.object(self.originals.store, "begin_upload", return_value={"direct": 1, "url": "u"}) as begin:
            self.assertEqual(self.originals.begin("purchase", "Bill 7.pdf", "10", "key-0001"), {"direct": 1, "url": "u"})
        original_id = begin.call_args.args[0]
        self.assertEqual(begin.call_args.args[1:], (".pdf", 10))
        self.assertEqual(self.originals.begin("purchase", "IMG_1.HEIC", "10", "key-0002"), {"direct": 0})  # 经服务器转 JPEG
        ok = {"status": "ok", "ref": ENTRY["ref"], "md5": "m",
              "meta": {"filename": "Bill 7.pdf", "suffix": ".pdf", "byte_size": 10, "kind": "pdf", "sha256": SHA,
                       "page_count": 3}}
        created = types.SimpleNamespace(name="F9", file_url=f"/private/files/{original_id}.bin")
        with mock.patch.object(self.originals.store, "where", return_value="oracle"), \
                mock.patch.object(self.originals.store, "finish_upload", return_value=ok), \
                mock.patch.object(self.originals.registered, "create", return_value=created) as create, \
                mock.patch.object(self.originals.registered, "filename", return_value="Bill 7.pdf"):
            self.assertEqual(self.originals.finish("purchase", "Bill 7.pdf", "10", "key-0001"),
                             {"name": "F9", "file_url": created.file_url, "file_name": "Bill 7.pdf"})
        create.assert_called_once_with(original_id, "Bill 7.pdf", ok)

    def test_an_upload_that_differs_from_what_was_declared_is_removed(self):
        changed = {"status": "ok", "ref": ENTRY["ref"], "md5": "m",
                   "meta": {"filename": "Bill 7.pdf", "suffix": ".pdf", "byte_size": 99, "kind": "pdf", "sha256": SHA,
                            "page_count": 3}}
        with mock.patch.object(self.originals.store, "where", return_value="oracle"), \
                mock.patch.object(self.originals.store, "finish_upload", return_value=changed), \
                mock.patch.object(self.originals.store, "delete") as delete, \
                mock.patch.object(self.originals.registered, "create") as create, \
                self.assertRaises(frappe.ValidationError):
            self.originals.finish("purchase", "Bill 7.pdf", "10", "key-0001")
        delete.assert_called_once_with([ENTRY["ref"]])
        create.assert_not_called()

    def test_a_busy_store_asks_the_page_to_confirm_again(self):
        from pearnly_core.execution.errors import Deferred

        with mock.patch.object(self.originals.store, "where", return_value="oracle"), \
                mock.patch.object(self.originals.store, "finish_upload", side_effect=Deferred("busy", 3)):
            self.assertEqual(self.originals.finish("sales", "a.jpg", "10", "key-0001"), {"retry": 3})

    def test_claude_reads_by_reference_only_when_every_attachment_is_on_oracle(self):
        oracle, gcs, local = stub(), stub(dict(ENTRY, ref=f"gs://bucket/{OBJECT}#5")), types.SimpleNamespace(file_name="a.pdf")
        with mock.patch("pearnly_core.claude_ocr.client.uses_claude", return_value=True):
            self.assertEqual(self.originals.claude_originals([oracle, oracle]), [(OBJECT, SHA), (OBJECT, SHA)])
            self.assertIsNone(self.originals.claude_originals([oracle, local]))
            self.assertIsNone(self.originals.claude_originals([gcs]))
        with mock.patch("pearnly_core.claude_ocr.client.uses_claude", return_value=False):
            self.assertIsNone(self.originals.claude_originals([oracle]))


class OracleReadsLikeErp(unittest.TestCase):
    """ERP 在生产上先转正（``ocr.orient``）再交 Claude；甲骨文按引用读几份附件时同样先转正，交出的页逐字节相同。"""

    def test_the_pages_oracle_sends_are_the_pages_erp_would_send(self):
        files = [(pdf(2), "application/pdf", ".pdf"), (jpeg(), "image/jpeg", ".jpg")]
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, SITE, "originals"))
            refs = []
            for index, (data, _mime, suffix) in enumerate(files):
                name = f"{SITE}/originals/ERO-{str(index) * 48}{suffix}"
                with open(os.path.join(root, name), "wb") as handle:
                    handle.write(data)
                refs.append({"object": name, "sha256": hashlib.sha256(data).hexdigest()})
            with mock.patch.dict(os.environ, {"ORIGINALS_ROOT": root}):
                there = server.stored_set(refs, orient=True)
        here = pages.prepare([orient.orient(data, mime) for data, mime, _suffix in files])
        self.assertEqual(there, here)
        self.assertEqual(len(there), 3)

    def test_the_site_sends_references_and_keeps_one_checkpoint(self):
        sent, saved = [], {}

        def checkpoint(key, state=None, body=None):
            if state:
                saved[key] = {"state": state, "body": body}
            return saved.get(key)

        with mock.patch.object(client, "internal", side_effect=lambda path, body, timeout: sent.append(body) or {"read": {}}), \
                mock.patch("pearnly_core.execution.work.checkpoint", checkpoint):
            client.read_originals("voucher", [(OBJECT, SHA), (OBJECT, "d" * 64)], "0105")
            client.read_originals("voucher", [(OBJECT, SHA), (OBJECT, "d" * 64)], "0105")  # 续跑不再付费
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["originals"], [{"object": OBJECT, "sha256": SHA}, {"object": OBJECT, "sha256": "d" * 64}])
        self.assertTrue(sent[0]["orient"])


if __name__ == "__main__":
    unittest.main()
