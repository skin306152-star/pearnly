#!/usr/bin/env python3
"""浏览器直传原件到谷歌存储（ADR-016）：服务器这边的签发、核对登记、读回与下载。离线：不连站点、不连谷歌。

- 没开（没配桶或没开 Cloud Run 识别）、表格 / Word 原件：照旧经服务器上传（``direct: 0``）。
- 签发的上传地址只能写一次、大小锁定为声明的字节数；对象名由工作空间 + 上传人 + 幂等键算出。
- 登记只认 Cloud Run 读出来的信息（页数、sha256），与服务器收原件时同一份检查；对不上就拒。
- 原件在桶里时，读回按版本号取并核对 sha256；登记识别结果时不再整份下载；下载转到短时签名地址。
"""
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sys
import types
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
for app in ("pearnly_core", "pearnly_cowork"):
    sys.path.insert(0, str(ROOT / "apps" / app))

import frappe  # noqa: E402

from pearnly_cowork.files import cloud, service  # noqa: E402

SITE, BUCKET = "acme.pearnly.com", "pearnly-cowork-originals-sg"


class Thrown(Exception):
    pass


def signer():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from google.auth import crypt

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    return types.SimpleNamespace(signer=crypt.RSASigner.from_string(pem), service_account_email="ocr@p.iam"), key


class Signing(unittest.TestCase):
    def test_a_v4_signature_verifies_with_the_service_account_key(self):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        credentials, key = signer()
        now = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)
        headers = {"Content-Type": "application/octet-stream", "x-goog-if-generation-match": "0",
                   "x-goog-content-length-range": "10,10"}
        url = cloud.sign("PUT", BUCKET, SITE + "/originals/CWS-x.pdf", seconds=900, headers=headers, now=now,
                         credentials=credentials)
        parts = urlsplit(url)
        self.assertEqual((parts.scheme, parts.netloc, parts.path),
                         ("https", "storage.googleapis.com", f"/{BUCKET}/{SITE}/originals/CWS-x.pdf"))
        query = parse_qs(parts.query)
        self.assertEqual(query["X-Goog-SignedHeaders"],
                         ["content-type;host;x-goog-content-length-range;x-goog-if-generation-match"])
        self.assertEqual(query["X-Goog-Credential"], ["ocr@p.iam/20261001/auto/storage/goog4_request"])
        self.assertEqual(query["X-Goog-Expires"], ["900"])
        # 按谷歌的 V4 规则重算一遍要签的串，用公钥验签（签名本身对，地址改一个字就不对）
        canonical_query = parts.query.rsplit("&X-Goog-Signature=", 1)[0]
        canonical = "\n".join(("PUT", parts.path, canonical_query,
            "content-type:application/octet-stream\nhost:storage.googleapis.com\n"
            "x-goog-content-length-range:10,10\nx-goog-if-generation-match:0\n",
            query["X-Goog-SignedHeaders"][0], "UNSIGNED-PAYLOAD"))
        to_sign = "\n".join(("GOOG4-RSA-SHA256", "20261001T080000Z", "20261001/auto/storage/goog4_request",
                             hashlib.sha256(canonical.encode()).hexdigest()))
        key.public_key().verify(bytes.fromhex(query["X-Goog-Signature"][0]), to_sign.encode(), padding.PKCS1v15(),
                                hashes.SHA256())

    def test_upload_target_locks_size_and_forbids_overwrite(self):
        credentials, _key = signer()
        with mock.patch.object(cloud, "_service_account", return_value=credentials):
            target = cloud.upload_target(BUCKET, SITE + "/originals/CWS-x.pdf", 1234)
        self.assertEqual(target["headers"]["x-goog-if-generation-match"], "0")
        self.assertEqual(target["headers"]["x-goog-content-length-range"], "1234,1234")

    def test_download_link_pins_the_version_and_is_an_attachment(self):
        credentials, _key = signer()
        with mock.patch.object(cloud, "_service_account", return_value=credentials):
            url = cloud.download_url((BUCKET, SITE + "/originals/CWS-x.pdf", 42), "ใบกำกับ 1.pdf")
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(query["generation"], ["42"])
        self.assertEqual(query["X-Goog-Expires"], [str(cloud.DOWNLOAD_SECONDS)])
        self.assertTrue(query["response-content-disposition"][0].startswith("attachment; filename*=UTF-8''"))

    def test_stored_addresses_round_trip(self):
        url = cloud.url_of(BUCKET, SITE + "/originals/CWS-x.pdf", 42)
        self.assertEqual(url, f"gs://{BUCKET}/{SITE}/originals/CWS-x.pdf#42")
        self.assertEqual(cloud.parse_url(url), (BUCKET, SITE + "/originals/CWS-x.pdf", 42))
        for value in ("/private/files/x.pdf", "gs://b/x", "gs://b/x#abc", "", None):
            self.assertIsNone(cloud.parse_url(value))


class Direct(unittest.TestCase):
    def setUp(self):
        self.created, self.existing = [], set()
        patches = [
            mock.patch.object(service.scope, "require"), mock.patch.object(service.scope, "require_scope"),
            mock.patch.object(service.frappe, "throw", side_effect=lambda message, *a, **k: (_ for _ in ()).throw(Thrown(message))),
            mock.patch.object(service, "_", side_effect=lambda text: text),
            mock.patch.object(service.frappe, "db", types.SimpleNamespace(
                exists=lambda doctype, name: name in self.existing)),
            mock.patch.object(service.frappe, "session", types.SimpleNamespace(user="a@acme.test")),
            mock.patch.object(service, "_remote_ready", return_value=True),
            mock.patch.object(service.cloud, "bucket", return_value=BUCKET),
            mock.patch.object(service.cloud, "upload_target",
                              side_effect=lambda bucket, name, size: {"url": f"https://signed/{name}?{size}", "headers": {}}),
            mock.patch.object(service, "_new_source", side_effect=self.new_source),
            mock.patch.object(service, "project", side_effect=lambda doc: {"name": doc["name"], "page_count": doc["page_count"]}),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        frappe.local.site = SITE
        self.addCleanup(lambda: delattr(frappe.local, "site"))

    def new_source(self, name, key, workspace, client, fingerprint, meta, **stored):
        doc = {"name": name, "page_count": meta["page_count"], "meta": meta, **stored}
        self.created.append(doc)
        return doc

    def test_begin_signs_a_per_user_object_for_pdf_and_images_only(self):
        result = service.begin_direct("WS1", "scan.PDF", 1234, "k1")
        name = "CWS-" + hashlib.sha256(b'["WS1", "a@acme.test", "k1"]').hexdigest()[:48]
        self.assertEqual(result, {"direct": 1, "url": f"https://signed/{SITE}/originals/{name}.pdf?1234", "headers": {}})
        self.assertEqual(service.begin_direct("WS1", "sheet.xlsx", 10, "k2"), {"direct": 0})
        with mock.patch.object(service.cloud, "bucket", return_value=None):
            self.assertEqual(service.begin_direct("WS1", "scan.pdf", 10, "k3"), {"direct": 0})
        with mock.patch.object(service, "_remote_ready", return_value=False):
            self.assertEqual(service.begin_direct("WS1", "scan.pdf", 10, "k4"), {"direct": 0})

    def test_begin_refuses_bad_names_sizes_and_keys_with_the_old_messages(self):
        for filename, size, key, message in (("a\nb.pdf", 10, "k", "Invalid original filename."),
                                             ("virus.exe", 10, "k", "This file type is not supported."),
                                             ("scan.pdf", 0, "k", "The uploaded file is empty."),
                                             ("scan.pdf", service.MAX_BYTES + 1, "k", "The uploaded file exceeds the 100 MiB limit."),
                                             ("scan.pdf", 10, "", "A request key of 1 to 128 characters is required.")):
            with self.assertRaises(Thrown) as caught:
                service.begin_direct("WS1", filename, size, key)
            self.assertEqual(str(caught.exception), message)

    def test_the_same_key_with_another_file_is_refused_like_the_server_upload(self):
        name = "CWS-" + hashlib.sha256(b'["WS1", "a@acme.test", "k1"]').hexdigest()[:48]
        self.existing.add(name)
        stored = frappe._dict(name=name, filename="scan.pdf", byte_size=1234, page_count=3)
        with mock.patch.object(service.frappe, "get_doc", return_value=stored), \
                mock.patch.object(service.access, "require_full"):
            self.assertEqual(service.begin_direct("WS1", "scan.pdf", 1234, "k1")["done"]["name"], name)
            self.assertEqual(service.finish_direct("WS1", "scan.pdf", 1234, "k1")["name"], name)
            for filename, size in (("other.pdf", 1234), ("scan.pdf", 999)):
                for step in (service.begin_direct, service.finish_direct):
                    with self.assertRaises(Thrown) as caught:
                        step("WS1", filename, size, "k1")
                    self.assertEqual(str(caught.exception), "This request key has already been used for different content.")

    def inspected(self, **overrides):
        meta = {"filename": "scan.pdf", "suffix": ".pdf", "kind": "pdf", "page_count": 3, "byte_size": 1234,
                "sha256": "e" * 64, **overrides}
        return {"status": "ok", "meta": meta, "generation": 77, "md5": "f" * 32}

    def finish(self, result, filename="scan.pdf", size=1234):
        from pearnly_cowork.ocr.remote import forward

        with mock.patch.object(forward, "inspect_original", return_value=result) as inspect:
            return service.finish_direct("WS1", filename, size, "k1"), inspect

    def test_finish_registers_only_what_cloud_run_read(self):
        result, inspect = self.finish(self.inspected())
        name = "CWS-" + hashlib.sha256(b'["WS1", "a@acme.test", "k1"]').hexdigest()[:48]
        inspect.assert_called_once_with(f"{SITE}/originals/{name}.pdf", "scan.pdf")
        self.assertEqual(result, {"name": name, "page_count": 3})
        created = self.created[0]
        self.assertEqual(created["storage_url"], f"gs://{BUCKET}/{SITE}/originals/{name}.pdf#77")
        self.assertEqual(created["native_content_hash"], "f" * 32)
        self.assertEqual(created["meta"]["sha256"], "e" * 64)

    def test_finish_refuses_rejected_missing_or_different_files(self):
        for result, message in (({"status": "rejected", "code": "pdf_password"}, "Password-protected PDF files cannot be read."),
                                ({"status": "rejected", "code": "ocr.too_many_pages"}, "ocr.too_many_pages"),
                                ({"status": "rejected", "code": "original_missing"},
                                 "The uploaded file was not received. Please upload again."),
                                (self.inspected(byte_size=999), "The uploaded file was not received. Please upload again."),
                                (self.inspected(kind="document"), "The uploaded file was not received. Please upload again.")):
            with self.assertRaises(Thrown) as caught:
                self.finish(result)
            self.assertEqual(str(caught.exception), message)
        self.assertEqual(self.created, [])

    def test_the_two_not_received_cases_are_logged_apart(self):
        """对用户是同一句话；日志里分开记原因和对不上的字段名，不记文件名。"""
        for result, reason in (({"status": "rejected", "code": "original_missing"}, "original_missing"),
                               (self.inspected(byte_size=999), "meta_mismatch:byte_size"),
                               (self.inspected(kind="document", filename="other.pdf"), "meta_mismatch:filename,kind")):
            with self.assertLogs("pearnly_cowork.files.service", "WARNING") as logged, self.assertRaises(Thrown):
                self.finish(result)
            self.assertEqual(len(logged.records), 1)
            self.assertEqual(logged.records[0].args[1], reason)
            self.assertNotIn("scan.pdf", logged.output[0])

    def test_finish_asks_the_page_to_retry_when_cloud_run_is_busy(self):
        from pearnly_core.execution.errors import Deferred
        from pearnly_cowork.ocr.remote import forward

        with mock.patch.object(forward, "inspect_original", side_effect=Deferred("remote_http_503", 5)):
            self.assertEqual(service.finish_direct("WS1", "scan.pdf", 1234, "k1"), {"retry": 5})
        self.assertEqual(self.created, [])


class StoredInBucket(unittest.TestCase):
    def source(self, data, **values):
        return frappe._dict(stored_file=None, storage_url=f"gs://{BUCKET}/{SITE}/originals/CWS-x.pdf#77",
                            sha256=hashlib.sha256(data).hexdigest(), filename="scan.pdf", **values)

    def test_reading_back_pins_the_version_and_checks_the_checksum(self):
        data = b"%PDF-1.7 bytes"
        with mock.patch.object(service.cloud, "read", return_value=data) as read:
            self.assertEqual(service.read_bytes(self.source(data)), data)
        read.assert_called_once_with((BUCKET, SITE + "/originals/CWS-x.pdf", 77))
        with mock.patch.object(service.cloud, "read", return_value=b"changed"), \
                mock.patch.object(service, "_", side_effect=lambda text: text), \
                mock.patch.object(service.frappe, "throw", side_effect=Thrown), self.assertRaises(Thrown):
            service.read_bytes(self.source(data))

    def test_preview_of_a_bucket_original_is_drawn_on_cloud_run(self):
        from pearnly_core.execution.errors import Deferred
        from pearnly_cowork.ocr.remote import forward

        source = self.source(b"x", kind="pdf", page_count=3)
        history = types.SimpleNamespace(source_file="S1")
        with mock.patch.object(service.scope, "require_read"), mock.patch.object(service.access, "require_page"), \
                mock.patch.object(service.frappe, "get_doc",
                                  side_effect=lambda doctype, name: history if doctype == service.access.HISTORY else source), \
                mock.patch.object(service, "_", side_effect=lambda text: text), \
                mock.patch.object(service.frappe, "throw", side_effect=lambda message, *a, **k: (_ for _ in ()).throw(Thrown(message))), \
                mock.patch.object(service.cloud, "read") as read:
            with mock.patch.object(forward, "preview", return_value={"png": b"PNG"}) as preview:
                self.assertEqual(service.page("H1", 2), b"PNG")
            preview.assert_called_once_with(SITE + "/originals/CWS-x.pdf", 77, source.sha256, "pdf", 2)
            with mock.patch.object(forward, "preview", return_value={"code": "preview_too_large"}), \
                    self.assertRaises(Thrown) as caught:
                service.page("H1", 2)
            self.assertEqual(str(caught.exception), "This page is too large to preview safely.")
            with mock.patch.object(forward, "preview", side_effect=Deferred("remote_http_503", 5)), \
                    self.assertRaises(Thrown) as caught:
                service.page("H1", 1)
            self.assertEqual(str(caught.exception), "The preview is temporarily unavailable. Please try again.")
        read.assert_not_called()   # 整份原件不下载到服务器

    def test_binding_results_does_not_download_the_whole_original_again(self):
        with mock.patch.object(service.cloud, "read") as read:
            service.verify_stored(self.source(b"x"))
        read.assert_not_called()
        local = frappe._dict(stored_file="F1", storage_url="/private/files/x.pdf")
        self.assertIsNone(service.stored_original(local))
        with mock.patch.object(service, "read_bytes") as read_bytes:
            service.verify_stored(local)
        read_bytes.assert_called_once_with(local)


if __name__ == "__main__":
    unittest.main()
