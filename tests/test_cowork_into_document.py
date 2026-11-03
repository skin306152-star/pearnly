"""补传的文件接到原单后面整份重读（`pearnly_cowork.assistant.into_document`，2026-11-03「CC 通用疑难处理」）。

- 原单这几页 + 补传的文件拼成一个 PDF（同一文件里别的单不带）；
- 已推送 / 推送中的单不动；读出不止一张单不合并（不猜着合并），交 CC 问人；
- 读出一张：覆盖原单（按原单版本号）、原单改指向拼好的原件并换掉原件上绑定的单据名（推送前核对要认它）、留一条来源记录、
  删掉识别时临时建出的那张——不新建单。
"""
import importlib.util
import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
COWORK = ROOT / "apps/pearnly_cowork/pearnly_cowork"


def _module(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules[name] = module
    parent, _sep, child = name.rpartition(".")
    if parent in sys.modules:
        setattr(sys.modules[parent], child, module)
    return module


class Db:
    def __init__(self):
        self.values, self.commits = [], 0

    def set_value(self, doctype, name, field, value=None, update_modified=True):
        self.values.append((doctype, name, field if isinstance(field, dict) else {field: value}))

    def get_value(self, doctype, name, field):
        return json.dumps(["H-TMP"])

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass


frappe = _module("frappe", db=Db(), ValidationError=ValueError, DoesNotExistError=LookupError,
                 PermissionError=PermissionError, clear_messages=lambda: None)
for name in ("pearnly_cowork", "pearnly_cowork.access", "pearnly_cowork.assistant", "pearnly_cowork.history",
             "pearnly_cowork.files", "pearnly_core", "pearnly_core.assistant"):
    _module(name)
_module("pearnly_cowork.access.scope", require=lambda feature: None, default_workspace=lambda: "W1")
RECORDS = []
_module("pearnly_cowork.assistant.document_checks", record=lambda *a, **k: RECORDS.append((a, k)))
DOCS = {}
UPDATED = []
_module("pearnly_cowork.history.storage", DOCTYPE="Cowork OCR History", read=lambda name: name,
        project=lambda name: DOCS[name], parse=json.loads, encode=json.dumps,
        update_pages=lambda name, pages, version: UPDATED.append((name, pages, version)) or {
            **DOCS[name], "pages": pages, "record_version": int(version) + 1})
DELETED = []
_module("pearnly_cowork.history.removal", delete=lambda names: DELETED.append(json.loads(names)))
CREATED, PURGED = [], []
_module("pearnly_cowork.files.service",
        get_source=lambda name: types.SimpleNamespace(filename="bjc.pdf", name=name),
        read_bytes=lambda source: b"%PDF-original",
        create=lambda workspace, filename, content, key: CREATED.append((workspace, filename, key)) or {"name": "SRC-NEW"},
        purge=lambda names: PURGED.extend(names))
_module("pearnly_cowork.files.access", page_indices=lambda value: set(json.loads(value)) if value else set())
_module("pearnly_core.assistant.files", attachment=lambda file_id: ("page2.jpg", b"jpeg"),
        resolve=lambda file_id: file_id, origin=lambda file_id: "user", attachments=lambda: [])

spec = importlib.util.spec_from_file_location("pearnly_cowork.assistant.into_document", COWORK / "assistant/into_document.py")
into = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = into
spec.loader.exec_module(into)


def tools(records, state="saved"):
    recognition = types.SimpleNamespace(detail=lambda batch: {"state": state, "history_records": records})
    return types.SimpleNamespace(ENTERED_NOTE="entered.", RECOGNIZE_WAIT_SECONDS=0, FINISHED=("saved", "not_invoice"),
                                 BROKEN=("failed", "uncertain", "cancelled"), _recognition=lambda: recognition,
                                 _submit=lambda source, key, choices: ({"name": "B1"}, False))


class IntoDocument(unittest.TestCase):
    def setUp(self):
        for found in (RECORDS, UPDATED, DELETED, CREATED, PURGED, frappe.db.values):
            found.clear()
        DOCS.clear()
        DOCS["H1"] = {"name": "H1", "workspace": "W1", "filename": "bjc.pdf", "source_file": "SRC-OLD",
                      "source_page_indices": "[7]", "record_version": 3, "last_push_status": "manual",
                      "pages": [{"fields": {"subtotal": "38869.00", "vat": "2720.83", "total_amount": None}}]}
        DOCS["H-TMP"] = {"name": "H-TMP", "source_file": "SRC-NEW", "filename": "bjc+page2.pdf", "page_count": 2,
                         "source_page_indices": [1, 2], "record_version": 1,
                         "pages": [{"fields": {"subtotal": "38869.00", "vat": "2720.83", "total_amount": "41589.83"}}]}
        self.combined = mock.patch.object(into, "combine", lambda parts: self.parts.extend(parts) or b"%PDF-combined")
        self.parts = []
        self.combined.start()
        self.addCleanup(self.combined.stop)

    def test_one_document_read_back_replaces_the_original_without_a_new_document(self):
        out = into.run("H1", "f1", tools(["H-TMP"]))
        self.assertEqual(out["status"], "merged")
        self.assertEqual([(content, pages) for content, _name, pages in self.parts],
                         [(b"%PDF-original", [7]), (b"jpeg", None)])  # 原单那一页 + 补传的
        self.assertEqual(CREATED, [("W1", "bjc+page2.pdf", "assistant:into:H1:f1")])  # 请求号固定：再调不重复扣费
        self.assertEqual(UPDATED, [("H1", DOCS["H-TMP"]["pages"], "3")])  # 按原单版本号覆盖
        source = next(values for doctype, name, values in frappe.db.values if doctype == "Cowork OCR History")
        self.assertEqual((source["source_file"], source["source_page_indices"]), ("SRC-NEW", "[1, 2]"))
        binding = next(values for doctype, name, values in frappe.db.values if doctype == "Cowork Source File")
        self.assertEqual(json.loads(binding["binding_records"]), ["H1"])
        (args, kwargs), = RECORDS
        self.assertEqual(args[0], "H1")
        self.assertIn("page2.jpg", kwargs["basis"])
        self.assertEqual((DELETED, PURGED), ([["H-TMP"]], ["SRC-OLD"]))

    def test_more_than_one_document_or_a_pushed_one_is_not_merged(self):
        out = into.run("H1", "f1", tools(["H-TMP", "H-TMP2"]))
        self.assertEqual((out["status"], out["found"]), ("not_merged", 2))
        DOCS["H1"]["last_push_status"] = "success"
        self.assertEqual(into.run("H1", "f1", tools(["H-TMP"]))["status"], "not_merged")
        self.assertEqual((UPDATED, DELETED, RECORDS), ([], [], []))

    def test_still_recognizing_comes_back_to_be_asked_again(self):
        out = into.run("H1", "f1", tools([], state="running"))
        self.assertEqual((out["status"], out["batch"]), ("recognizing", "B1"))
        self.assertEqual(UPDATED, [])


class Combine(unittest.TestCase):
    def test_the_original_pages_and_the_added_photo_make_one_pdf(self):
        try:
            import fitz
        except ImportError:
            self.skipTest("pymupdf is in the product image")
        source = fitz.open()
        for _page in range(3):
            source.new_page()
        pdf = io.BytesIO()
        source.save(pdf)
        photo = fitz.open()
        photo.new_page(width=200, height=300)
        png = photo[0].get_pixmap().tobytes("png")
        merged = fitz.open(stream=into.combine([(pdf.getvalue(), "a.pdf", [2]), (png, "b.png", None)]), filetype="pdf")
        self.assertEqual(merged.page_count, 2)


if __name__ == "__main__":
    unittest.main()
