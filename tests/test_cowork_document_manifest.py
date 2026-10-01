"""Actual manifest/authorization functions with explicit zero-IO runtime fakes.

Compile unchanged functions from the checked-out files. Native Site/controller,
File storage, DB locks and transaction behavior are tested separately by the
dedicated lab script; these tests never import Frappe or open original files.
"""
import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "apps/pearnly_cowork/pearnly_cowork"


class Row(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


class Rejected(Exception):
    pass


class Denied(Rejected):
    pass


def throw(message, exc=Rejected):
    raise exc(message)


def load_functions(relative, names, namespace):
    path = APP / relative
    nodes = [node for node in ast.parse(path.read_text()).body
             if isinstance(node, ast.FunctionDef) and node.name in names]
    if {node.name for node in nodes} != set(names):
        raise AssertionError("Real source functions missing")
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


class Page:
    def __init__(self, number, text="Synthetic table"):
        self.page_number, self.text = number, text

    def model_dump(self, **kwargs):
        return {"page_number": self.page_number, "full_text": self.text}


class Document:
    def __init__(self, source, count=2, complete=True):
        self.pages = tuple(Page(i) for i in range(1, count + 1))
        self.page_count, self.engine, self.billing_characters = count, "table_path_excel", 23
        self.source_sha256 = source.sha256
        self.provenance = {"version": 1, "reference_kind": "logical_section", "unit_kind": "worksheet",
            "coverage_complete": complete,
            "coverage_issues": [] if complete else [{"code": "duplicate_headers", "page_number": 1}],
            "units": [{"page_number": i, "rows": [{"source_row": 1, "cells": ["原行"]}]} for i in range(1, count + 1)]}

    @property
    def coverage_complete(self):
        return self.provenance["coverage_complete"]


class DocumentManifestTests(unittest.TestCase):
    def setUp(self):
        self.events, self.rows, self.denied = [], [], set()
        self.source = Row(name="CWS-test", workspace="company", client="client", kind="document", page_count=1,
            sha256="a" * 64, filename="original.xlsx", uploaded_by="uploader", binding_complete=0,
            reference_kind="", reference_count=0, provenance_json="", coverage_complete=0,
            request_fingerprint="upload-fingerprint", flags=Row())
        self.previous = self.source.copy()
        self.source.get_doc_before_save = lambda: Row(self.previous)
        self.source.save = self.save
        self.document = Document(self.source)
        self.frappe = SimpleNamespace(throw=throw, PermissionError=Denied, QueryDeadlockError=type("Deadlock", (Exception,), {}),
            session=Row(user="uploader"), get_doc=self.get_doc,
            db=SimpleNamespace(savepoint=lambda *a: self.events.append(("savepoint", *a)),
                rollback=lambda **kw: self.events.append(("rollback", kw))))
        self.scope = SimpleNamespace(READ_FEATURES=("intake",), allowed=lambda *a: True, allowed_any=lambda *a: True,
            contains=lambda workspace, client, user=None: workspace not in self.denied,
            require=lambda feature: self.events.append(("feature", feature)), require_scope=self.require_scope)
        constants = {}
        tree = ast.parse((APP / "files/access.py").read_text())
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(n, ast.Name) and n.id in {
                    "SOURCE", "HISTORY", "MANIFEST_FIELDS", "MANIFEST_SCHEMA", "MAX_DOCUMENT_MANIFEST_BYTES"}
                    for n in node.targets):
                exec(compile(ast.Module(body=[node], type_ignores=[]), "access constants", "exec"), constants)
        self.access_env = dict(frappe=self.frappe, scope=self.scope, json=json, _=lambda text: text,
                              _MANIFEST_CAPABILITY=object(), **{k: v for k, v in constants.items() if k != "__builtins__"})
        load_functions("files/access.py", ["reference_kind", "reference_count", "_manifest_state", "validate_manifest",
            "readable", "page_indices", "full_allowed", "require_full", "require_page"], self.access_env)
        self.access_env["references"] = lambda name: self.rows
        self.access = SimpleNamespace(**{k: v for k, v in self.access_env.items() if k != "__builtins__"})
        self.guard = SimpleNamespace(WRITE_CAPABILITY=object())
        self.service = dict(access=self.access, guard=self.guard, frappe=self.frappe, scope=self.scope,
            hashlib=hashlib, json=json, _=lambda text: text, read_bytes=lambda doc: self.events.append(("read", doc.name)))
        load_functions("files/service.py", ["_document_manifest", "freeze_doc_manifest", "project"], self.service)

    def get_doc(self, doctype, name, **kwargs):
        self.events.append(("get_doc", doctype, name, kwargs))
        self.assertEqual((doctype, name), (self.access.SOURCE, self.source.name))
        return self.source

    def save(self, **kwargs):
        # Native Frappe _save overwrites the flag from the method argument.
        self.source.flags.ignore_version = kwargs.get("ignore_version", False)
        self.assertIs(self.source.flags.cowork_original_write, self.guard.WRITE_CAPABILITY)
        self.access.validate_manifest(self.source)
        self.events.append(("save", kwargs))
        self.previous = self.source.copy()

    def require_scope(self, workspace, client):
        if workspace in self.denied:
            raise Denied("scope")

    def freeze(self):
        return self.service["freeze_doc_manifest"](self.source, self.document)

    def bind(self, indices, workspace="company"):
        self.rows.append(Row(workspace=workspace, client="client", source_page_indices=json.dumps(indices)))
        self.source.binding_complete = 1

    def test_legacy_pdf_and_image_keep_physical_count(self):
        for kind in ("pdf", "image"):
            self.source.kind, self.source.page_count = kind, 3
            self.assertEqual(self.access.reference_kind(self.source), "physical_page")
            self.assertEqual(self.access.reference_count(self.source), 3)
            self.bind([1, 2, 3])
            self.assertTrue(self.access.full_allowed(self.source, "reader"))

    def test_document_placeholder_never_becomes_one_authorized_page(self):
        self.assertEqual(self.access.reference_kind(self.source), "logical_section")
        self.assertEqual(self.access.reference_count(self.source), 0)
        self.bind([1])
        self.assertFalse(self.access.full_allowed(self.source, "reader"))
        self.assertTrue(self.access.full_allowed(self.source, "uploader"))

    def test_unknown_kind_and_invalid_counts_fail_closed(self):
        self.source.kind = "pdf"
        for value in (-1, 0, True, "2", None):
            self.source.page_count = value
            self.assertEqual(self.access.reference_count(self.source), 0)
        self.source.reference_kind = "logical_section"
        self.assertIsNone(self.access.reference_kind(self.source))

    def test_first_freeze_keeps_upload_identity_and_suppresses_native_version(self):
        self.assertIs(self.freeze(), self.source)
        self.assertEqual((self.source.page_count, self.source.reference_count), (1, 2))
        self.assertEqual(self.source.request_fingerprint, "upload-fingerprint")
        self.assertTrue(self.source.flags.ignore_version)
        self.assertIn(("save", {"ignore_permissions": True, "ignore_version": True}), self.events)
        self.assertFalse(self.source.flags.notify_update)
        self.assertEqual(self.events[1][-1], {"for_update": True})
        body = json.loads(self.source.provenance_json)
        self.assertEqual(body["source_sha256"], self.source.sha256)
        self.assertEqual(len(body["page_inputs_sha256"]), 2)
        self.assertEqual(body["provenance"]["units"][0]["rows"][0]["cells"], ["原行"])

    def test_identical_freeze_replay_checks_bytes_without_save(self):
        self.freeze()
        self.events.clear()
        self.assertIs(self.freeze(), self.source)
        self.assertIn(("read", self.source.name), self.events)
        self.assertFalse(any(event[0] == "save" for event in self.events))

    def test_same_raw_provenance_with_changed_parser_projection_is_rejected(self):
        self.freeze()
        self.document.pages[0].text += " changed by a parser upgrade"
        with self.assertRaises(Denied):
            self.freeze()

    def test_changed_billing_or_provenance_on_replay_is_rejected(self):
        self.freeze()
        self.document.billing_characters += 1
        with self.assertRaises(Denied):
            self.freeze()
        self.document.billing_characters -= 1
        self.document.provenance["units"][0]["rows"][0]["cells"].append("extra")
        with self.assertRaises(Denied):
            self.freeze()

    def test_wrong_sha_fails_before_read_or_write(self):
        self.document.source_sha256 = "b" * 64
        with self.assertRaises(Rejected):
            self.freeze()
        self.assertFalse(any(e[0] in {"read", "save"} for e in self.events))

    def test_nonsequential_pages_and_units_are_rejected(self):
        self.document.pages[1].page_number = 3
        with self.assertRaises(Rejected):
            self.freeze()
        self.document.pages[1].page_number = 2
        self.document.provenance["units"][1]["page_number"] = 1
        with self.assertRaises(Rejected):
            self.freeze()

    def test_invalid_coverage_and_nonfinite_json_are_rejected(self):
        self.document.provenance["coverage_issues"] = [{"code": "lost_column"}]
        with self.assertRaises(Rejected):
            self.freeze()
        self.document.provenance["coverage_issues"] = []
        self.document.provenance["units"][0]["bad"] = float("nan")
        with self.assertRaises(Rejected):
            self.freeze()

    def test_manifest_limit_counts_utf8_bytes_and_never_truncates(self):
        self.document.provenance["units"][0]["rows"][0]["cells"] = ["字" * (8 * 1024 * 1024 // 3)]
        with self.assertRaisesRegex(Rejected, "8 MiB"):
            self.freeze()
        self.assertEqual(self.source.provenance_json, "")
        self.assertFalse(any(e[0] == "save" for e in self.events))

    def test_already_bound_placeholder_cannot_be_upgraded(self):
        self.source.binding_complete = 1
        with self.assertRaises(Denied):
            self.freeze()

    def test_general_write_capability_cannot_forge_manifest(self):
        self.source.reference_kind, self.source.reference_count = "logical_section", 2
        self.source.provenance_json = self.service["_document_manifest"](self.source, self.document)
        self.source.coverage_complete = 1
        self.source.flags.cowork_original_write = self.guard.WRITE_CAPABILITY
        self.source.flags.cowork_document_manifest = {"trusted": True}
        with self.assertRaises(Denied):
            self.access.validate_manifest(self.source)

    def test_frozen_manifest_never_mutates_even_with_real_manifest_token(self):
        self.freeze()
        self.source.reference_count = 1
        with self.assertRaises(Denied):
            self.access.validate_manifest(self.source)

    def test_all_logical_sections_are_required_before_non_uploader_download(self):
        self.freeze()
        self.bind([1])
        self.assertFalse(self.access.full_allowed(self.source, "reader"))
        self.bind([2])
        self.assertTrue(self.access.full_allowed(self.source, "reader"))
        self.source.binding_complete = 0
        self.assertFalse(self.access.full_allowed(self.source, "reader"))

    def test_out_of_range_binding_does_not_satisfy_coverage(self):
        self.freeze()
        self.bind([1, 2, 3])
        self.assertFalse(self.access.full_allowed(self.source, "reader"))

    def test_incomplete_prompt_coverage_is_uploader_only(self):
        self.document.provenance["coverage_complete"] = False
        self.document.provenance["coverage_issues"] = [{"code": "word_other_table", "page_number": 1}]
        self.freeze()
        self.bind([1, 2])
        self.assertTrue(self.access.full_allowed(self.source, "uploader"))
        self.assertFalse(self.access.full_allowed(self.source, "reader"))

    def test_uploader_must_retain_initial_and_every_known_invoice_scope(self):
        self.freeze()
        self.bind([1, 2], "another-company")
        self.denied.add("another-company")
        self.assertFalse(self.access.full_allowed(self.source, "uploader"))
        self.denied.clear()
        self.denied.add("company")
        self.assertFalse(self.access.full_allowed(self.source, "uploader"))

    def test_logical_reference_cannot_enter_physical_preview(self):
        self.freeze()
        self.bind([1, 2])
        history = Row(workspace="company", client="client", source_file=self.source.name, source_page_indices="[1,2]")
        with self.assertRaisesRegex(Denied, "PDF and image"):
            self.access.require_page(history, 1)

    def test_projection_exposes_reference_semantics_without_original_cells(self):
        self.freeze()
        result = self.service["project"](self.source)
        self.assertEqual((result["page_count"], result["reference_count"], result["reference_kind"], result["reference_unit"]),
                         (1, 2, "logical_section", "worksheet"))
        self.assertTrue(result["coverage_complete"])
        self.assertNotIn("provenance_json", result)
        self.assertNotIn("原行", json.dumps(result, ensure_ascii=False))

    def test_before_validate_runs_manifest_guard_even_if_validate_is_ignored(self):
        path = APP / "pearnly_cowork/doctype/cowork_source_file/cowork_source_file.py"
        tree = ast.parse(path.read_text())
        node = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        called = []
        env = {"Document": object, "validate_source": lambda doc: called.append("source"),
               "validate_manifest": lambda doc: called.append("manifest")}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), env)
        env["CoworkSourceFile"]().before_validate()
        self.assertEqual(called, ["source", "manifest"])

    def recognition(self):
        self.created = []
        self.contracted = []
        def create(workspace, body, key, client, *, attachment, check_duplicates=False):
            self.created.append(attachment)
            self.events.append(("create", attachment["source_page_indices"]))
            return {"name": "H-" + str(len(self.created)), **attachment}
        storage = SimpleNamespace(encode=lambda value: json.dumps(value, sort_keys=True), parse=json.loads,
            contract=lambda fn, pages, **kw: self.contracted.append(kw) or {"source_page_indices": [page["page_index"] for page in pages]},
            anchor_year=lambda: 2026, _create=create,
            read=lambda name, **kw: Row(name=name, source_file=self.source.name), project=dict)
        env = dict(frappe=self.frappe, scope=self.scope, access=self.access, guard=self.guard,
            files=SimpleNamespace(read_bytes=self.service["read_bytes"]), storage=storage,
            hashlib=hashlib, initial_history=object(), _=lambda text: text)
        load_functions("history/recognition.py", ["persist_batch"], env)
        return env, storage

    def group(self, indices):
        return {"workspace": "company", "client": "client", "source_page_indices": indices,
                "pages": [{"page_index": number} for number in indices]}

    def test_document_history_uses_logical_range_without_pdf_identity(self):
        self.freeze()
        env, _ = self.recognition()
        result = env["persist_batch"](self.source.name, [self.group([2])], "recognition")
        self.assertEqual(result[0]["source_page_indices"], "[2]")
        self.assertIsNone(result[0]["source_pdf_id"])
        self.assertTrue(self.source.binding_complete)
        self.assertFalse(self.access.full_allowed(self.source, "reader"))
        # 识别入库走宽松日期口径：票面日期写法认不出不让整批失败（复核编辑仍严格）。
        self.assertEqual([kw.get("recognized") for kw in self.contracted], [True])

    def test_unfrozen_document_is_rejected_before_history_write(self):
        env, _ = self.recognition()
        with self.assertRaisesRegex(Rejected, "manifest is not ready"):
            env["persist_batch"](self.source.name, [self.group([1])], "recognition")
        self.assertEqual(self.created, [])

    def test_document_rejects_foreign_or_mismatched_reference(self):
        self.freeze()
        env, _ = self.recognition()
        with self.assertRaises(Rejected):
            env["persist_batch"](self.source.name, [self.group([3])], "recognition")
        group = self.group([2])
        group["pages"] = [{"page_index": 1}]
        with self.assertRaises(Rejected):
            env["persist_batch"](self.source.name, [group], "recognition")
        self.assertEqual(self.created, [])

    def test_same_logical_section_can_bind_multiple_invoices(self):
        self.freeze()
        env, _ = self.recognition()
        result = env["persist_batch"](self.source.name, [self.group([1]), self.group([1, 2])], "recognition")
        self.assertEqual([json.loads(row["source_page_indices"]) for row in result], [[1], [1, 2]])
        self.assertEqual([row["source_total"] for row in result], [2, 2])

    def test_sealed_replay_reuses_first_history_names(self):
        self.freeze()
        env, _ = self.recognition()
        first = env["persist_batch"](self.source.name, [self.group([1, 2])], "recognition")
        replay = env["persist_batch"](self.source.name, [self.group([1, 2])], "recognition")
        self.assertEqual([row["name"] for row in first], [row["name"] for row in replay])
        self.assertEqual(len(self.created), 1)

    def test_legacy_physical_binding_identity_is_unchanged(self):
        self.source.kind, self.source.page_count = "pdf", 2
        env, storage = self.recognition()
        result = env["persist_batch"](self.source.name, [self.group([1, 2])], "recognition")
        body = {"source_page_indices": [1, 2]}
        prepared = [{"workspace": "company", "client": "client", "body": body, "indices": [1, 2]}]
        expected = hashlib.sha256(storage.encode(["recognition", prepared]).encode()).hexdigest()
        self.assertEqual(self.source.binding_fingerprint, expected)
        self.assertEqual(result[0]["source_pdf_id"], self.source.name)

    def test_failed_history_binding_uses_savepoint_and_does_not_seal(self):
        self.freeze()
        env, storage = self.recognition()
        def fail(*args, **kwargs):
            raise Rejected("Synthetic insert failure")
        storage._create = fail
        with self.assertRaises(Rejected):
            env["persist_batch"](self.source.name, [self.group([1, 2])], "recognition")
        self.assertFalse(self.source.binding_complete)
        self.assertIn(("rollback", {"save_point": "cowork_bind_original"}), self.events)


if __name__ == "__main__":
    unittest.main()
