#!/usr/bin/env python3
"""Cowork 对账中心的原件（ADR-024）：上传的对账文件交原件层存、本机只留登记；识别按引用读（甲骨文逐页、Cloud Run 按版本号），
甲骨文逐页出的图与生产逐页渲染（``PageSource.frame``）逐字节相同；对账结束连原件一起删。不联网，在生产镜像里跑。"""
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for app in ("pearnly_core", "pearnly_cowork"):
    sys.path.insert(0, str(ROOT / "apps" / app))

import frappe  # noqa: E402

from pearnly_core.claude_ocr import client, pages, server  # noqa: E402
from pearnly_core.ocr_models import orient  # noqa: E402
from pearnly_cowork.ocr.engine.errors import ContentError  # noqa: E402
from pearnly_cowork.ocr.inputs_runtime.source import PageSource  # noqa: E402
from pearnly_cowork.recon_center import document_ocr, storage  # noqa: E402

SITE = "acme.pearnly.com"
OBJECT = f"{SITE}/originals/CWS-{'a' * 48}.pdf"


def pdf(count=2):
    import pymupdf

    with pymupdf.open() as document:
        for number in range(1, count + 1):
            document.new_page().insert_text((72, 72), f"Statement page {number}  1,234.50")
        return document.tobytes()


def jpeg():
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (900, 1200), (250, 250, 240)).save(buffer, "JPEG", quality=90)
    return buffer.getvalue()


class Files:
    """内存版 File 表：只有对账存放用到的几个调用。"""

    def __init__(self):
        self.rows = {}

    def get_doc(self, *args):
        if isinstance(args[0], dict):
            values, doc = args[0], mock.Mock()

            def insert(**_kwargs):
                doc.name = f"F{len(self.rows) + 1}"
                self.rows[doc.name] = values
                return doc
            doc.insert = insert
            return doc
        values = self.rows[args[1]]
        return mock.Mock(file_name=values["file_name"], get_content=lambda: values["content"])

    def patch(self, case):
        db = mock.MagicMock()
        db.get_value.side_effect = lambda _doctype, name, field: self.rows.get(name, {}).get(field)
        db.exists.side_effect = lambda _doctype, name: name in self.rows
        for patch in (mock.patch.object(storage.frappe, "get_doc", self.get_doc), mock.patch.object(storage.frappe, "db", db),
                      mock.patch.object(storage.frappe, "delete_doc", lambda _d, name, **_k: self.rows.pop(name)),
                      mock.patch.object(storage, "_is_original_copy", return_value=False)):
            patch.start()
            case.addCleanup(patch.stop)


class Storage(unittest.TestCase):
    def setUp(self):
        self.files = Files()
        self.files.patch(self)
        self.content = pdf()
        self.sha = hashlib.sha256(self.content).hexdigest()

    def put(self, ref):
        return mock.patch.object(storage.store, "put", return_value={
            "status": "ok", "ref": ref, "md5": "m",
            "meta": {"sha256": self.sha, "page_count": 2, "kind": "pdf", "suffix": ".pdf", "filename": "Bank.pdf"}})

    def test_an_upload_goes_to_the_originals_layer_and_only_a_registration_stays(self):
        ref = f"oracle://{OBJECT}#{self.sha}"
        with self.put(ref) as put:
            name = storage.save(self.content, "Bank.pdf")
        put.assert_called_once()
        self.assertEqual(put.call_args.args[1:], (".pdf", "Bank.pdf", self.content))
        row = self.files.rows[name]
        self.assertEqual(row["file_name"], storage.REF_NAME)
        self.assertLess(len(row["content"]), 1024)
        self.assertNotIn(self.content, row["content"])  # 原件一个字节也不留在这台
        with mock.patch.object(storage.store, "read", return_value=self.content) as read:
            self.assertEqual(storage.read(name), ("Bank.pdf", self.content))  # 表格 / 电子 PDF 在这台解析要用
        read.assert_called_once_with(ref)
        self.assertEqual(storage.stored(name), (OBJECT, self.sha))
        self.assertEqual(storage.content_hash(name), self.sha)
        with mock.patch.object(storage.store, "delete") as delete:
            storage.delete(name)
        delete.assert_called_once_with([ref])
        self.assertNotIn(name, self.files.rows)

    def test_google_storage_gives_bucket_generation_and_sha(self):
        name_in_bucket = OBJECT
        with self.put(f"gs://bucket/{name_in_bucket}#7"):
            name = storage.save(self.content, "Bank.pdf")
        self.assertEqual(storage.stored(name), ("bucket", name_in_bucket, 7, self.sha))

    def test_a_failed_remote_delete_keeps_the_registration(self):
        with self.put(f"oracle://{OBJECT}#{self.sha}"):
            name = storage.save(self.content, "Bank.pdf")
        with mock.patch.object(storage.store, "delete", side_effect=RuntimeError("down")), self.assertRaises(RuntimeError):
            storage.delete(name)
        self.assertIn(name, self.files.rows)  # 登记还在：按日志处理，不留下没人知道的原件

    def test_without_an_originals_layer_or_when_it_refuses_the_file_stays_here(self):
        for result in (None, {"status": "rejected", "code": "pdf_pages"}):
            with mock.patch.object(storage.store, "put", return_value=result):
                name = storage.save(self.content, "Ledger.pdf")
            self.assertEqual((self.files.rows[name]["file_name"], self.files.rows[name]["content"]),
                             ("Ledger.pdf", self.content))
            self.assertIsNone(storage.stored(name))
            self.assertEqual(storage.read(name), ("Ledger.pdf", self.content))

    def test_an_unreachable_store_asks_to_try_later_instead_of_keeping_it_here(self):
        from pearnly_core.execution.errors import Deferred

        with mock.patch.object(storage.store, "put", side_effect=Deferred("down", 5)), \
                mock.patch.object(storage, "_", side_effect=lambda text: text), \
                mock.patch.object(storage.frappe, "throw", side_effect=frappe.ValidationError) as throw, \
                self.assertRaises(frappe.ValidationError):
            storage.save(self.content, "Bank.pdf")
        throw.assert_called_once_with("reconciliation.system_busy")
        self.assertEqual(self.files.rows, {})


class Source(unittest.TestCase):
    """识别时拿什么：路线能按引用读就只给引用，不取回字节。"""

    def source(self, stored, claude, remote):
        with mock.patch.object(storage, "stored", return_value=stored), \
                mock.patch.object(storage, "read", return_value=("a.pdf", b"bytes")) as read, \
                mock.patch("pearnly_core.claude_ocr.client.uses_claude", return_value=claude), \
                mock.patch.object(document_ocr.forward, "settings", return_value={"url": "u"} if remote else None):
            return document_ocr._source("F1"), read.called

    def test_each_route_reads_its_own_store_by_reference(self):
        oracle, gcs = (OBJECT, "s" * 64), ("bucket", OBJECT, 7, "s" * 64)
        self.assertEqual(self.source(oracle, True, False), ((oracle, None), False))
        self.assertEqual(self.source(gcs, False, True), ((gcs, None), False))
        # 路线换了（原件存的那边这条路线读不到）或没开 Cloud Run：取回字节照原样读
        self.assertEqual(self.source(gcs, True, False), ((None, b"bytes"), True))
        self.assertEqual(self.source(oracle, False, True), ((None, b"bytes"), True))
        self.assertEqual(self.source(gcs, False, False), ((None, b"bytes"), True))
        self.assertEqual(self.source(None, True, False), ((None, b"bytes"), True))

    def test_claude_reads_each_page_on_oracle_without_rendering_here(self):
        calls = []

        def read_original(kind, object_name, sha256, own_tax=None, page=None):
            calls.append((kind, object_name, sha256, page))
            return {"read": None}

        with mock.patch.object(document_ocr, "_progress"), mock.patch.object(document_ocr.progress, "latest"), \
                mock.patch.object(document_ocr.frappe, "db", mock.MagicMock()), \
                mock.patch.object(document_ocr.core_pages, "mapper", return_value=None), \
                mock.patch.object(client, "read_original", read_original), \
                mock.patch.object(client, "read", side_effect=AssertionError("rendered here")), \
                mock.patch.object(document_ocr, "PageSource", side_effect=AssertionError("rendered here")), \
                self.assertRaises(ContentError):  # 两页都没读出：与谷歌路线同样整份读不出
            document_ocr._read_claude(None, "a.pdf", 2, {"environment": {}}, "bank_statement",
                                      stored=(OBJECT, "s" * 64))
        self.assertEqual(calls, [("bank", OBJECT, "s" * 64, 1), ("bank", OBJECT, "s" * 64, 2)])


class OraclePages(unittest.TestCase):
    """甲骨文按页出的图与生产逐页渲染一样：闸门测的就是生产跑的。"""

    def test_a_page_rendered_on_oracle_is_byte_for_byte_the_production_page(self):
        for content, filename, media_type, count in ((pdf(3), "s.pdf", "application/pdf", 3),
                                                     (jpeg(), "s.jpg", "image/jpeg", 1)):
            with PageSource(content, filename, expected_page_count=count, enable_text_path=False,
                            image_max_long_edge=2400, orient=lambda data, mime: orient.orient(data, mime)) as source:
                for number in range(1, count + 1):
                    frame = source.frame(number)
                    here = pages.frame(content, media_type, number)
                    self.assertEqual(here, (frame.image_bytes, frame.mime_type))
                    self.assertEqual(pages.prepare([here]), pages.prepare([(frame.image_bytes, frame.mime_type)]))
        for content, media_type, number in ((pdf(2), "application/pdf", 3), (jpeg(), "image/jpeg", 2)):
            with self.assertRaises(ValueError):
                pages.frame(content, media_type, number)

    def test_the_service_reads_one_page_of_a_stored_original(self):
        content = pdf(3)
        sha = hashlib.sha256(content).hexdigest()
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, SITE, "originals"))
            with open(os.path.join(root, OBJECT), "wb") as handle:
                handle.write(content)
            with mock.patch.dict(os.environ, {"ORIGINALS_ROOT": root}):
                one = server.stored_pages({"object": OBJECT, "sha256": sha, "page": 2})
                every = server.stored_pages({"object": OBJECT, "sha256": sha})
        self.assertEqual(one, pages.prepare([pages.frame(content, "application/pdf", 2)]))
        self.assertEqual(len(one), 1)
        self.assertEqual(len(every), 3)  # 不带页号：整份（发票）照旧

    def test_the_site_asks_for_one_page_and_keeps_a_checkpoint_per_page(self):
        sent, saved = [], {}

        def checkpoint(key, state=None, body=None):
            if state:
                saved[key] = {"state": state, "body": body}
            return saved.get(key)

        with mock.patch.object(client, "internal", side_effect=lambda path, body, timeout: sent.append(body) or {"read": {}}), \
                mock.patch("pearnly_core.execution.work.checkpoint", checkpoint):
            client.read_original("bank", OBJECT, "s" * 64, page=2)
            client.read_original("bank", OBJECT, "s" * 64, page=2)  # 续跑：同一页不再付费
            client.read_original("bank", OBJECT, "s" * 64, page=3)
        self.assertEqual([body["original"] for body in sent],
                         [{"object": OBJECT, "sha256": "s" * 64, "page": 2}, {"object": OBJECT, "sha256": "s" * 64, "page": 3}])
        self.assertEqual(json.dumps(sorted(saved)), json.dumps(sorted(
            [f"claude:bank:original:{'s' * 64}::p2", f"claude:bank:original:{'s' * 64}::p3"])))


if __name__ == "__main__":
    unittest.main()
