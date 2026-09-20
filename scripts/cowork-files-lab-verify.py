"""Private original / sealed invoice manifest verification in the dedicated lab.

Synthetic PDF/image/text only. No OCR provider, external network or UI.
"""
import copy
import hashlib
import io
import json
from pathlib import Path
import runpy
import subprocess
import sys
import unittest
import uuid
from unittest.mock import patch

import frappe
import pymupdf
from PIL import Image

BASE = runpy.run_path(str(Path(__file__).with_name("cowork-backend-lab-verify.py")))
connect, user, grant, PAGES = (BASE[k] for k in ("connect", "user", "grant", "PAGES"))


def pdf():
    with pymupdf.open() as doc:
        for index, color in enumerate(((1, 0, 0), (0, 0, 1)), 1):
            page = doc.new_page(width=120, height=120)
            page.draw_rect(page.rect, fill=color)
            page.insert_text((10, 25), f"SYNTHETIC {index}")
        return doc.tobytes()


class NativeFiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pearnly_cowork.masters import api as masters
        suffix = uuid.uuid4().hex[:10]
        cls.actor, cls.reader, cls.other = [f"cw-file-{role}-{suffix}@example.invalid"
                                           for role in ("actor", "reader", "other")]
        for actor, roles in ((cls.actor, ["Cowork Intake"]), (cls.reader, ["Cowork OCR History"]),
                             (cls.other, ["Cowork Intake"])):
            user(actor, roles)
        cls.workspace = masters.create_workspace("Synthetic File A " + suffix)["name"]
        cls.foreign = masters.create_workspace("Synthetic File B " + suffix)["name"]
        for actor, workspace in ((cls.actor, cls.workspace), (cls.actor, cls.foreign),
                                 (cls.reader, cls.workspace), (cls.other, cls.foreign)):
            grant(actor, "Cowork Workspace", workspace)
        cls.bytes = pdf()
        frappe.db.commit()

    def setUp(self):
        frappe.set_user(self.actor)
        from pearnly_cowork.files import service
        from pearnly_cowork.history import recognition
        self.files, self.recognition = service, recognition
        self.source = self.files.create(self.workspace, "synthetic.pdf", self.bytes, uuid.uuid4().hex)

    def tearDown(self):
        frappe.db.rollback()
        frappe.set_user("Administrator")
        frappe.clear_cache()

    def groups(self, cross=False, same_page=False):
        second_pages = copy.deepcopy(PAGES)
        second_pages[0]["page_index"] = 1 if same_page else 2
        return [{"workspace": self.workspace, "source_page_indices": [1], "pages": copy.deepcopy(PAGES),
                 "source": "manual", "confidence": "high", "ai_raw": {"synthetic": 1}},
                {"workspace": self.foreign if cross else self.workspace,
                 "source_page_indices": [1 if same_page else 2], "pages": second_pages,
                 "source": "manual", "confidence": "high", "ai_raw": {"synthetic": 2}}]

    def manifest(self, **kwargs):
        return self.recognition.persist_batch(self.source["name"], self.groups(**kwargs), "manifest")

    def native_file(self):
        source = frappe.get_doc("Cowork Source File", self.source["name"])
        return frappe.get_doc("File", source.stored_file)

    def test_upload_exact_bytes_idempotent_and_private(self):
        key = uuid.uuid4().hex
        first = self.files.create(self.workspace, "original.pdf", self.bytes, key)
        again = self.files.create(self.workspace, "original.pdf", self.bytes, key)
        self.assertEqual(first, again)
        self.assertEqual(self.files.original(first["name"]), ("original.pdf", self.bytes))
        self.assertEqual(first["sha256"], hashlib.sha256(self.bytes).hexdigest())
        self.assertNotIn("storage_url", first)
        with self.assertRaises(frappe.ValidationError):
            self.files.create(self.workspace, "different.pdf", self.bytes, key)
        self.assertTrue(self.native_file().is_private)

    def test_bound_partial_scope_can_only_read_its_physical_page(self):
        rows = self.manifest(cross=True)
        frappe.set_user(self.reader)
        image = Image.open(io.BytesIO(self.files.page(rows[0]["name"], 1)))
        self.assertGreater(image.getpixel((125, 125))[0], 200)
        with self.assertRaises(frappe.PermissionError):
            self.files.original(self.source["name"])
        for history, page in ((rows[0]["name"], 2), (rows[1]["name"], 2)):
            with self.assertRaises(frappe.PermissionError):
                self.files.page(history, page)

    def test_same_physical_page_cross_company_denies_preview(self):
        rows = self.manifest(cross=True, same_page=True)
        frappe.set_user(self.reader)
        with self.assertRaises(frappe.PermissionError):
            self.files.page(rows[0]["name"], 1)

    def test_unsealed_and_incomplete_manifest_deny_other_originals(self):
        frappe.set_user(self.reader)
        with self.assertRaises(frappe.PermissionError):
            self.files.original(self.source["name"])
        frappe.set_user(self.actor)
        self.recognition.persist_batch(self.source["name"], self.groups()[:1], "partial")
        frappe.set_user(self.reader)
        with self.assertRaises(frappe.PermissionError):
            self.files.original(self.source["name"])

    def test_all_pages_in_scope_allows_business_original_only(self):
        self.manifest()
        frappe.set_user(self.reader)
        self.assertEqual(self.files.original(self.source["name"])[1], self.bytes)
        file = self.native_file()
        self.assertFalse(file.is_downloadable())
        with self.assertRaises(frappe.PermissionError):
            file.get_content()

    def test_sealed_manifest_retry_does_not_duplicate_or_allow_rebind(self):
        first = self.manifest()
        second = self.manifest()
        self.assertEqual([r["name"] for r in first], [r["name"] for r in second])
        changed = self.groups(cross=True)
        with self.assertRaises(frappe.ValidationError):
            self.recognition.persist_batch(self.source["name"], changed, "manifest")
        self.assertEqual(frappe.db.count("Cowork OCR History", {"source_file": self.source["name"]}), 2)

    def test_manifest_replay_by_another_authorized_actor_returns_original_rows(self):
        first = self.manifest()
        frappe.set_user("Administrator")
        if not frappe.db.exists("User Permission", {"user": self.other, "allow": "Cowork Workspace", "for_value": self.workspace}):
            grant(self.other, "Cowork Workspace", self.workspace)
        frappe.set_user(self.other)
        second = self.manifest()
        self.assertEqual([r["name"] for r in first], [r["name"] for r in second])
        self.assertEqual(frappe.db.count("Cowork OCR History", {"source_file": self.source["name"]}), 2)

    def test_manifest_mismatched_page_evidence_is_rejected(self):
        groups = self.groups()
        groups[0]["pages"][0]["page_index"] = 2
        with self.assertRaises(frappe.ValidationError):
            self.recognition.persist_batch(self.source["name"], groups, "bad-pages")

    def test_repeated_physical_page_keeps_every_invoice_payload(self):
        groups = self.groups()[:1]
        groups[0]["source_page_indices"] = [1, 1]
        groups[0]["pages"].append(copy.deepcopy(PAGES[0]))
        groups[0]["pages"][1]["fields"]["invoice_number"] = "CW-SIBLING"
        rows = self.recognition.persist_batch(self.source["name"], groups, "siblings")
        self.assertEqual(rows[0]["source_page_indices"], [1])
        self.assertEqual(len(rows[0]["pages"]), 2)
        self.assertEqual(rows[0]["pages"][1]["fields"]["invoice_number"], "CW-SIBLING")

    def test_late_binding_failure_rolls_back_every_reference(self):
        from pearnly_cowork.history import storage
        original = storage._create
        calls = []
        def failure(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                frappe.throw("Synthetic late failure")
            return original(*args, **kwargs)
        with patch.object(storage, "_create", side_effect=failure), self.assertRaises(frappe.ValidationError):
            self.manifest()
        self.assertEqual(frappe.db.count("Cowork OCR History", {"source_file": self.source["name"]}), 0)
        self.assertFalse(frappe.db.get_value("Cowork Source File", self.source["name"], "binding_complete"))

    def test_attachment_evidence_cannot_be_forged_unlinked_or_deleted(self):
        rows = self.manifest(cross=True)
        from pearnly_cowork.history import storage
        doc = frappe.get_doc("Cowork OCR History", rows[0]["name"])
        doc.flags.cowork_history_update = storage._UPDATE_TOKEN
        doc.source_page_indices = "[2]"
        with self.assertRaises(frappe.ValidationError):
            doc.save()
        frappe.set_user("Administrator")
        with self.assertRaises(frappe.PermissionError):
            frappe.delete_doc("Cowork OCR History", rows[1]["name"], ignore_permissions=True)
        source = frappe.get_doc("Cowork Source File", self.source["name"])
        source.flags.cowork_original_write = True
        source.binding_complete = 0
        with self.assertRaises(frappe.PermissionError):
            source.save(ignore_permissions=True)

    def test_native_file_mutations_fail_before_disk_change(self):
        from pearnly_cowork.files import guard
        file = self.native_file()
        source = frappe.get_doc("Cowork Source File", self.source["name"])
        for operation in (lambda f: f.save_file(overwrite=True), lambda f: f.make_thumbnail(),
                          lambda f: f.unzip(), lambda f: f.delete(ignore_permissions=True)):
            with self.assertRaises(frappe.PermissionError):
                operation(self.native_file())
        for changes in ({"is_private": 0}, {"file_url": "/files/else.pdf"},
                        {"attached_to_doctype": None, "attached_to_name": None}):
            file = self.native_file()
            file.update(changes)
            file.flags.cowork_original_write = True
            with self.assertRaises(frappe.PermissionError):
                file.save(ignore_permissions=True)
        self.assertEqual(self.files.read_bytes(source), self.bytes)
        self.assertIsNot(guard.WRITE_CAPABILITY, True)

    def test_local_url_and_hash_aliases_cannot_read_or_publish_original(self):
        source = frappe.get_doc("Cowork Source File", self.source["name"])
        for url in (source.storage_url, source.storage_url.replace("/files/", "/files/./"),
                    "http://" + BASE["SITE"] + source.storage_url,
                    source.storage_url.replace("/files/", "/files/%2e/")):
            alias = frappe.get_doc({"doctype": "File", "file_name": "alias.pdf", "file_url": url,
                                   "is_private": 1})
            with self.assertRaises(frappe.PermissionError):
                alias.insert(ignore_permissions=True)
        # Simulate a pre-existing hash alias: native privacy changes otherwise
        # update every File with the same hash directly, bypassing validation.
        alias = frappe.get_doc({"doctype": "File", "file_name": uuid.uuid4().hex + ".txt",
                              "content": b"synthetic hash alias", "is_private": 1}).insert(ignore_permissions=True)
        frappe.db.set_value("File", alias.name, "content_hash", source.native_content_hash)
        alias.reload()
        alias.is_private = 0
        with self.assertRaises(frappe.PermissionError):
            alias.save(ignore_permissions=True)
        self.assertEqual(self.files.read_bytes(source), self.bytes)
        self.assertEqual(self.native_file().file_url, source.storage_url)

    def test_source_native_shares_and_lists_cannot_expose_files(self):
        file = self.native_file()
        for doctype, name in (("File", file.name), ("Cowork Source File", self.source["name"])):
            share = frappe.get_doc({"doctype": "DocShare", "share_doctype": doctype,
                "share_name": name, "user": self.other, "read": 1})
            share.flags.ignore_validate = True
            with self.assertRaises((frappe.ValidationError, frappe.PermissionError)):
                share.insert(ignore_permissions=True)
        self.assertNotIn(file.name, frappe.get_list("File", pluck="name", limit_page_length=0))

    def test_thumbnail_alias_cannot_delete_or_modify_original(self):
        source = frappe.get_doc("Cowork Source File", self.source["name"])
        ordinary = frappe.get_doc({"doctype": "File", "file_name": "thumbnail-alias.txt", "is_private": 1,
                                   "content": b"ordinary thumbnail fixture"}).insert(ignore_permissions=True)
        for url in (source.storage_url, source.storage_url.replace("/files/", "/files/./"),
                    "http://" + BASE["SITE"] + source.storage_url):
            ordinary.reload()
            ordinary.thumbnail_url = url
            ordinary.flags.ignore_validate = True
            with self.assertRaises(frappe.PermissionError):
                ordinary.save(ignore_permissions=True)
        frappe.db.set_value("File", ordinary.name, "thumbnail_url", source.storage_url)
        ordinary.reload()
        with self.assertRaises(frappe.PermissionError):
            ordinary.delete(ignore_permissions=True)
        self.assertEqual(self.files.read_bytes(source), self.bytes)

    def test_revocation_applies_to_original_uploader_and_pages(self):
        rows = self.manifest()
        name = frappe.db.get_value("User Permission", {"user": self.actor,
                                      "allow": "Cowork Workspace", "for_value": self.workspace}, "name")
        frappe.set_user("Administrator")
        frappe.delete_doc("User Permission", name)
        frappe.set_user(self.actor)
        with self.assertRaises(frappe.PermissionError):
            self.files.original(self.source["name"])
        with self.assertRaises(frappe.PermissionError):
            self.files.page(rows[0]["name"], 1)

    def test_bad_content_real_pdf_limits_and_large_original(self):
        for filename, data in (("a.pdf", b"bad"), ("a.png", b"bad"), ("a.exe", b"bad"), ("a.pdf", b"")):
            with self.assertRaises(frappe.ValidationError):
                self.files.create(self.workspace, filename, data, uuid.uuid4().hex)
        with pymupdf.open() as doc:
            for _ in range(51):
                doc.new_page(width=10, height=10)
            with self.assertRaises(frappe.ValidationError):
                self.files.create(self.workspace, "too-many.pdf", doc.tobytes(), uuid.uuid4().hex)
        large = b"a" * (26 * 1024 * 1024)
        source = self.files.create(self.workspace, "large.txt", large, uuid.uuid4().hex)
        self.assertEqual(self.files.original(source["name"])[1], large)

    def test_jpeg_original_keeps_exif_and_ordinary_file_still_works(self):
        output = io.BytesIO()
        image = Image.new("RGB", (20, 20), (10, 20, 30))
        exif = Image.Exif()
        exif[270] = "Synthetic evidence"
        image.save(output, format="JPEG", exif=exif)
        jpeg = output.getvalue()
        original_settings = frappe.get_system_settings
        def settings(key=None):
            return 1 if key == "strip_exif_metadata_from_uploaded_images" else original_settings(key)
        with patch.object(frappe, "get_system_settings", side_effect=settings):
            source = self.files.create(self.workspace, "original.jpg", jpeg, uuid.uuid4().hex)
        self.assertEqual(self.files.original(source["name"])[1], jpeg)
        ordinary = frappe.get_doc({"doctype": "File", "file_name": "ordinary.txt", "is_private": 1,
                                   "content": b"ordinary attachment"}).insert(ignore_permissions=True)
        self.assertEqual(ordinary.get_content(encodings=[]), b"ordinary attachment")
        self.assertTrue(ordinary.is_downloadable())
        self.assertTrue(frappe.has_permission("File", "read", ordinary))
        ordinary.delete(ignore_permissions=True)

    def test_concurrent_upload_rpc_keeps_one_source(self):
        key = uuid.uuid4().hex
        command = [sys.executable, __file__, "--upload-worker", self.actor, self.workspace, key]
        workers = [subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True) for _ in range(2)]
        try:
            for worker in workers:
                self.assertEqual(worker.stdout.readline().strip(), "READY")
            for worker in workers:
                worker.stdin.write("go\n")
                worker.stdin.flush()
            results = []
            for worker in workers:
                stdout, stderr = worker.communicate(timeout=30)
                self.assertEqual(worker.returncode, 0, stderr)
                results.append(json.loads(stdout.strip()))
            self.assertEqual(results[0]["name"], results[1]["name"])
            frappe.db.rollback()
            self.assertEqual(frappe.db.count("Cowork Source File", {"name": results[0]["name"]}), 1)
        finally:
            for worker in workers:
                if worker.poll() is None:
                    worker.kill()
                    worker.communicate()

    def test_manifest_replay_after_another_connection_old_snapshot(self):
        frappe.set_user("Administrator")
        if not frappe.db.exists("User Permission", {"user": self.other, "allow": "Cowork Workspace", "for_value": self.workspace}):
            grant(self.other, "Cowork Workspace", self.workspace)
        frappe.set_user(self.actor)
        frappe.db.commit()
        command = [sys.executable, __file__, "--manifest-worker", self.other, self.source["name"],
                   json.dumps(self.groups())]
        worker = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(worker.stdout.readline().strip(), "READY")
            first = self.manifest()
            frappe.db.commit()
            stdout, stderr = worker.communicate("go\n", timeout=30)
            self.assertEqual(worker.returncode, 0, stderr)
            self.assertEqual(json.loads(stdout.strip()), [row["name"] for row in first])
            self.assertEqual(frappe.db.count("Cowork OCR History", {"source_file": self.source["name"]}), 2)
        finally:
            if worker.poll() is None:
                worker.kill()
                worker.communicate()


def http_smoke():
    from werkzeug.test import Client
    from werkzeug.wrappers import Response
    from frappe.app import application
    NativeFiles.setUpClass()
    fixture = NativeFiles()
    fixture.setUp()
    rows = fixture.manifest(cross=True)
    stored = fixture.native_file()
    file_name, file_url = stored.name, stored.file_url
    source = fixture.source["name"]
    credentials = {}
    for actor in (fixture.actor, fixture.reader, fixture.other):
        frappe.set_user("Administrator")
        account = frappe.get_doc("User", actor)
        account.api_key, secret = uuid.uuid4().hex, uuid.uuid4().hex
        account.api_secret = secret
        account.save()
        credentials[actor] = {"Authorization": f"token {account.api_key}:{secret}"}
    # Reproduce legacy owner bypass as stored data, without changing the product
    # owner through a public endpoint (that endpoint must reject it).
    frappe.db.set_value("File", file_name, "owner", fixture.reader)
    frappe.db.commit()
    frappe.destroy()
    client = Client(application, Response)
    base = "http://" + BASE["SITE"]
    prefix = "/api/method/pearnly_cowork.files.api."
    def call(path, actor=fixture.reader, method="GET", data=None, multipart=False):
        return client.open(path, base_url=base, method=method,
            headers=credentials.get(actor, {}), **({"data": data} if multipart else {"json": data}))
    original = call(prefix + "original?name=" + source, fixture.actor)
    assert original.status_code == 200 and original.data == fixture.bytes, original.status_code
    assert original.headers.get("Cache-Control") == "no-store", dict(original.headers)
    assert original.headers.get("X-Content-Type-Options") == "nosniff"
    denied = call(prefix + "original?name=" + source)
    assert denied.status_code == 403, denied.status_code
    page = call(prefix + "page?history_name=" + rows[0]["name"] + "&physical_page=1")
    assert page.status_code == 200 and page.mimetype == "image/png", page.get_data(as_text=True)
    assert page.headers.get("Cache-Control") == "no-store"
    for path in (file_url, file_url + "?fid=" + file_name, "/api/resource/File/" + file_name):
        response = call(path)
        assert response.status_code == 403, (path, response.status_code)
    zipped = call("/api/method/frappe.core.api.file.zip_files", method="POST",
                  data={"files": json.dumps([file_name])})
    assert zipped.status_code == 403, zipped.status_code
    unzip = call("/api/method/frappe.core.api.file.unzip_file", method="POST", data={"name": file_name})
    assert unzip.status_code == 403, unzip.status_code
    public = call("/api/resource/File/" + file_name, method="PUT", data={"is_private": 0})
    assert public.status_code == 403, public.status_code
    anonymous = call(prefix + "page?history_name=" + rows[0]["name"] + "&physical_page=1", actor=None)
    assert anonymous.status_code == 403
    uploaded = call(prefix + "upload", fixture.actor, "POST", multipart=True, data={
        "workspace": fixture.workspace, "request_key": uuid.uuid4().hex,
        "file": (io.BytesIO(b"synthetic HTTP upload"), "original.txt")})
    assert uploaded.status_code == 200, uploaded.get_data(as_text=True)
    name = uploaded.json["message"]["name"]
    downloaded = call(prefix + "original?name=" + name, fixture.actor)
    assert downloaded.status_code == 200 and downloaded.data == b"synthetic HTTP upload"
    assert call(prefix + "upload", fixture.actor).status_code == 403
    connect()
    assert frappe.db.get_value("File", file_name, "is_private") == 1
    print(json.dumps({"multipart_upload_and_exact_download": "PASS", "page_preview": "PASS",
        "private_no_store": "PASS", "native_file_owner_raw_zip_unzip_publish": "403",
        "anonymous_and_get_write": "403"}))


if __name__ == "__main__":
    connect()
    try:
        if len(sys.argv) > 1 and sys.argv[1] == "--http-smoke":
            http_smoke()
        elif len(sys.argv) > 1 and sys.argv[1] == "--upload-worker":
            from werkzeug.wrappers import Request
            from pearnly_cowork.files import api
            frappe.set_user(sys.argv[2])
            frappe.db.count("Cowork Source File")
            print("READY", flush=True)
            if sys.stdin.readline().strip() != "go":
                raise SystemExit("Missing concurrency barrier")
            frappe.local.request = Request.from_values(method="POST", data={
                "file": (io.BytesIO(b"synthetic race"), "race.txt")})
            row = api.upload(sys.argv[3], sys.argv[4])
            frappe.db.commit()
            print(json.dumps({"name": row["name"]}))
        elif len(sys.argv) > 1 and sys.argv[1] == "--manifest-worker":
            from pearnly_cowork.history.recognition import persist_batch
            frappe.set_user(sys.argv[2])
            frappe.db.count("Cowork OCR History")
            print("READY", flush=True)
            if sys.stdin.readline().strip() != "go":
                raise SystemExit("Missing concurrency barrier")
            for attempt in range(3):
                try:
                    rows = persist_batch(sys.argv[3], json.loads(sys.argv[4]), "manifest")
                    break
                except frappe.QueryDeadlockError:
                    # The eventual handler owns transaction retries; this
                    # harness simulates that boundary without a paid provider.
                    frappe.db.rollback()
                    if attempt == 2:
                        raise
            frappe.db.commit()
            print(json.dumps([row["name"] for row in rows]))
        else:
            result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(NativeFiles))
            raise SystemExit(0 if result.wasSuccessful() else 1)
    finally:
        if getattr(frappe.local, "db", None):
            frappe.db.rollback()
        frappe.destroy()
