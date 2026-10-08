"""删除就删干净（用户 2026-10-08「删就删除所有记录和储存，不要给服务器带来负担」）：不连库、不依赖 Frappe。

- 录入记录：推送记录 / 尝试 / 通知、纠错样本一起删；只有推送没出结论的不删；
- 原件：没有别的票再用时，原件记录、识别批次、服务器文件、原件桶里的原件与预览一起删；
- 自动录入规则：规则、运行记录、文件进度一起删，网页直传的暂存文件提交后清。
"""
import importlib.util
import json
import pathlib
import sys
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "apps/pearnly_cowork/pearnly_cowork"


class Row(dict):
    __getattr__ = dict.get


class FakeDB:
    """几张内存表：{doctype: {name: Row}}；过滤只支持本测试用到的「等于 / in / 列表里」。"""

    def __init__(self, tables):
        self.tables = {doctype: {name: Row(name=name, **fields) for name, fields in rows.items()}
                       for doctype, rows in tables.items()}
        self.after_commit = types.SimpleNamespace(callbacks=[], add=lambda fn: self.after_commit.callbacks.append(fn))

    @staticmethod
    def _match(row, filters):
        if isinstance(filters, str):
            return row.name == filters
        for key, want in (filters or {}).items():
            got = row.get(key)
            if isinstance(want, list) and want and want[0] == "in":
                if got not in want[1]:
                    return False
            elif got != want:
                return False
        return True

    def rows(self, doctype, filters=None):
        return [row for row in self.tables.get(doctype, {}).values() if self._match(row, filters)]

    def exists(self, doctype, filters=None):
        found = self.rows(doctype, filters)
        return found[0].name if found else None

    def get_value(self, doctype, filters, fields="name", as_dict=False, for_update=False):
        found = self.rows(doctype, filters)
        if not found:
            return None
        row = found[0]
        if isinstance(fields, (list, tuple)):
            picked = Row({key: row.get(key) for key in fields})
            return picked if as_dict else tuple(picked.values())
        return row.get(fields)

    def delete(self, doctype, filters=None):
        for row in self.rows(doctype, filters):
            del self.tables[doctype][row.name]

    def get_all(self, doctype, filters=None, fields=None, pluck=None, **_ignored):
        found = self.rows(doctype, filters)
        if pluck:
            return [row.get(pluck) for row in found]
        return [Row({key: row.get(key) for key in (fields or ["name"])}) for row in found]


def _frappe(db, **extra):
    class Error(Exception):
        pass

    def throw(message, exc=None):
        raise (exc or Error)(message)

    module = types.SimpleNamespace(
        db=db, get_all=db.get_all, throw=throw, _=lambda text: text, ValidationError=Error, PermissionError=Error,
        DoesNotExistError=Error, local=types.SimpleNamespace(site="testcowork.pearnly.com"),
        get_site_path=lambda *parts: "/sites/testcowork.pearnly.com/" + "/".join(parts),
        log_error=lambda **kw: None, get_traceback=lambda: "", enqueue=mock.Mock(), **extra)
    return module


def _load(name, path, modules):
    with mock.patch.dict(sys.modules, modules):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class HistoryRemoval(unittest.TestCase):
    def _removal(self, db, purged):
        def delete_doc(doctype, name, **kwargs):
            self.assertTrue(kwargs.get("delete_permanently"))  # 不留「Deleted Document」备份
            db.delete(doctype, {"name": name})

        frappe = _frappe(db, get_doc=lambda doctype, name: db.rows(doctype, {"name": name})[0], delete_doc=delete_doc)
        scope = types.SimpleNamespace(require=lambda *a: None, require_scope=lambda *a: None)
        package = types.ModuleType("pearnly_cowork.history")
        package.__path__ = [str(APP / "history")]
        storage = types.SimpleNamespace(DOCTYPE="Cowork OCR History", parse=lambda v: json.loads(v) if isinstance(v, str) else v)
        modules = {"frappe": frappe, "pearnly_cowork": types.ModuleType("pearnly_cowork"),
                   "pearnly_cowork.access": types.SimpleNamespace(scope=scope),
                   "pearnly_cowork.files": types.SimpleNamespace(service=types.SimpleNamespace(purge=purged.extend)),
                   "pearnly_cowork.history": package, "pearnly_cowork.history.storage": storage}
        package.storage = storage
        return _load("pearnly_cowork.history.removal", APP / "history/removal.py", modules)

    def test_pushed_records_go_with_their_push_records_and_original(self):
        db = FakeDB({
            "Cowork OCR History": {"H1": {"source_file": "CWS-a", "last_push_status": "success"},
                                   "H2": {"source_file": "CWS-a", "last_push_status": "pending"},
                                   "H3": {"source_file": "", "last_push_status": ""}},
            "Cowork Push Record": {"P1": {"history": "H1", "status": "success"},
                                   "P2": {"history": "H2", "status": "pending"}},
            "Cowork Push Attempt": {"A1": {"push_record": "P1"}, "A2": {"push_record": "P2"}},
            "Cowork Push Notification": {"N1": {"push_record": "P1", "history": "H1"}},
            "Cowork OCR Correction": {"C1": {"source_history": "H1"}, "C3": {"source_history": "H3"}},
            "Cowork Push Writer Lock": {},
        })
        purged = []
        result = self._removal(db, purged).delete(json.dumps(["H1", "H2", "H3", "gone"]))
        self.assertEqual(result, {"deleted": 2, "skipped": 2})
        self.assertEqual(set(db.tables["Cowork OCR History"]), {"H2"})  # 正在推送的留着
        self.assertEqual(set(db.tables["Cowork Push Record"]), {"P2"})
        self.assertEqual(set(db.tables["Cowork Push Attempt"]), {"A2"})
        self.assertEqual(db.tables["Cowork Push Notification"], {})
        self.assertEqual(db.tables["Cowork OCR Correction"], {})
        self.assertEqual(purged, ["CWS-a"])

    def test_unknown_result_or_held_writer_lock_counts_as_pushing(self):
        db = FakeDB({
            "Cowork OCR History": {"H1": {"last_push_status": "manual"}, "H2": {"last_push_status": "success"}},
            "Cowork Push Record": {"P1": {"history": "H1", "status": "manual", "result_unknown": 1},
                                   "P2": {"history": "H2", "status": "success"}},
            "Cowork Push Writer Lock": {"L1": {"holder": "P2"}},
        })
        result = self._removal(db, []).delete(["H1", "H2"])
        self.assertEqual(result, {"deleted": 0, "skipped": 2})


class OriginalPurge(unittest.TestCase):
    def _files(self, db, bucket="pearnly-cowork-originals-sg"):
        frappe = _frappe(db)
        cloud = types.SimpleNamespace(bucket=lambda: bucket, parse_url=lambda v: (
            (v[5:].split("/", 1)[0], v[5:].split("/", 1)[1].rpartition("#")[0], 7) if v else None))
        package = types.ModuleType("pearnly_cowork.files")
        package.access = types.SimpleNamespace(SOURCE="Cowork Source File", HISTORY="Cowork OCR History")
        package.cloud = cloud
        package.guard = types.SimpleNamespace()
        ocr = types.SimpleNamespace(originals=types.SimpleNamespace(),
                                    inputs=types.SimpleNamespace(IMAGE_EXTENSIONS=frozenset(), MAX_FILE_BYTES=1, OcrInputError=Exception))
        modules = {"frappe": frappe, "frappe.utils": types.SimpleNamespace(cint=int),
                   "pearnly_cowork": types.ModuleType("pearnly_cowork"),
                   "pearnly_cowork.access": types.SimpleNamespace(scope=types.SimpleNamespace()),
                   "pearnly_cowork.files": package, "pearnly_cowork.ocr": ocr,
                   "pearnly_cowork.ocr.inputs": ocr.inputs}
        return _load("files_service_under_test", APP / "files/service.py", modules), frappe

    def test_unused_original_and_its_storage_are_removed(self):
        db = FakeDB({
            "Cowork Source File": {
                "CWS-a": {"stored_file": "F1", "storage_url": ""},
                "CWS-b": {"stored_file": "", "storage_url": "gs://pearnly-cowork-originals-sg/testcowork.pearnly.com/originals/CWS-b.pdf#7"},
                "CWS-c": {"stored_file": "F3", "storage_url": ""},
                "CWS-d": {"stored_file": "", "storage_url": ""}},
            "Cowork OCR History": {"H9": {"source_file": "CWS-c"}},  # 同一份原件里还有没删的票
            "Cowork OCR Batch": {"B1": {"source_file": "CWS-a", "phase": "saved"},
                                 "B2": {"source_file": "CWS-b", "phase": "saved"},
                                 "B4": {"source_file": "CWS-d", "phase": "recognizing"}},  # 还在识别
            "File": {"F1": {"file_url": "/private/files/a.pdf", "attached_to_doctype": "Cowork Source File",
                            "attached_to_name": "CWS-a"},
                     "F2": {"file_url": "/private/files/a.pdf", "attached_to_doctype": "Other", "attached_to_name": "x"},
                     "F3": {"file_url": "/private/files/c.pdf", "attached_to_doctype": "Cowork Source File",
                            "attached_to_name": "CWS-c"}},
        })
        files, frappe = self._files(db)
        self.assertEqual(files.purge(["CWS-a", "CWS-b", "CWS-c", "CWS-d", "CWS-a"]), 2)
        self.assertEqual(set(db.tables["Cowork Source File"]), {"CWS-c", "CWS-d"})
        self.assertEqual(set(db.tables["Cowork OCR Batch"]), {"B4"})
        self.assertEqual(set(db.tables["File"]), {"F2", "F3"})
        # a.pdf 还有别的文件记录指着（框架按内容去重）：磁盘文件留着
        removed = []
        with mock.patch("os.remove", removed.append):
            for callback in db.after_commit.callbacks:
                callback()
        self.assertEqual(removed, [])
        frappe.enqueue.assert_called_once()
        self.assertEqual(frappe.enqueue.call_args.kwargs["prefixes"], [
            ("pearnly-cowork-originals-sg", "testcowork.pearnly.com/previews/CWS-a/"),
            ("pearnly-cowork-originals-sg", "testcowork.pearnly.com/originals/CWS-b.pdf"),
            ("pearnly-cowork-originals-sg", "testcowork.pearnly.com/previews/CWS-b/")])
        self.assertTrue(frappe.enqueue.call_args.kwargs["enqueue_after_commit"])

    def test_server_file_is_removed_after_commit_when_nothing_else_points_at_it(self):
        db = FakeDB({"Cowork Source File": {"CWS-a": {"stored_file": "F1", "storage_url": ""}},
                     "Cowork OCR History": {}, "Cowork OCR Batch": {},
                     "File": {"F1": {"file_url": "/private/files/a.pdf", "attached_to_doctype": "Cowork Source File",
                                     "attached_to_name": "CWS-a"}}})
        files, frappe = self._files(db, bucket="")
        files.purge(["CWS-a"])
        removed = []
        with mock.patch("os.remove", removed.append):
            for callback in db.after_commit.callbacks:
                callback()
        self.assertEqual(removed, ["/sites/testcowork.pearnly.com/private/files/a.pdf"])
        frappe.enqueue.assert_not_called()


class CloudPrefix(unittest.TestCase):
    def test_only_single_original_prefixes_are_accepted(self):
        source = (APP / "files/cloud.py").read_text()
        frappe = types.SimpleNamespace(conf={})
        cloud = _load("files_cloud_under_test", APP / "files/cloud.py", {"frappe": frappe})
        for prefix in ("", "testcowork.pearnly.com/", "testcowork.pearnly.com/originals/",
                       "testcowork.pearnly.com/originals/x", "nosite/originals/CWS-a", "a.b/other/CWS-a"):
            with self.assertRaises(ValueError, msg=prefix):
                cloud.delete_prefix("bucket", prefix, http=object())
        self.assertIn('"versions": "true"', source)

    def test_deletes_every_version_and_treats_missing_as_deleted(self):
        frappe = types.SimpleNamespace(conf={})
        cloud = _load("files_cloud_under_test", APP / "files/cloud.py", {"frappe": frappe})
        calls = []

        class Response:
            def __init__(self, status=200, body=None):
                self.status_code, self.body = status, body or {}

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise RuntimeError(self.status_code)

            def json(self):
                return self.body

        listing = {"items": [{"name": "s.com/previews/CWS-a/v1-x/p1", "generation": "5"},
                             {"name": "s.com/previews/CWS-a/v1-x/p2", "generation": "6"}]}
        http = types.SimpleNamespace(
            get=lambda url, **kw: Response(body=listing),
            delete=lambda url, **kw: calls.append((url, kw["params"])) or Response(404 if len(calls) == 2 else 204))
        with mock.patch.object(cloud, "_token", lambda: "t"):
            self.assertEqual(cloud.delete_prefix("bucket", "s.com/previews/CWS-a/", http=http), 2)
        self.assertEqual([params["generation"] for _url, params in calls], ["5", "6"])


class FolderRuleDelete(unittest.TestCase):
    def test_rule_goes_with_its_runs_and_files(self):
        db = FakeDB({"Cowork Folder Intake Rule": {"R1": {"connection": "C1"}, "R2": {"connection": "C1"}},
                     "Cowork Folder Intake Run": {"U1": {"rule": "R1"}, "U2": {"rule": "R2"}},
                     "Cowork Folder Intake File": {"E1": {"rule": "R1", "route": "inline"},
                                                   "E2": {"rule": "R1", "route": "direct"},
                                                   "E3": {"rule": "R2", "route": "inline"}}})
        frappe = _frappe(db, get_doc=None)
        checked, dropped = [], []
        access = types.SimpleNamespace(FILE="Cowork Folder Intake File", RULE="Cowork Folder Intake Rule",
                                       RUN="Cowork Folder Intake Run", write=None,
                                       read=lambda doctype, name, for_update=False: db.rows(doctype, {"name": name})[0])
        stub = types.SimpleNamespace
        modules = {"frappe": frappe, "frappe.utils": stub(now_datetime=lambda: None),
                   "pearnly_cowork": types.ModuleType("pearnly_cowork"),
                   "pearnly_cowork.access": stub(scope=stub(require=lambda *a: None)),
                   "pearnly_cowork.connections": stub(access=stub(), preparation=stub(), service=stub()),
                   "pearnly_cowork.folder_intake": stub(agent=stub(), contracts=stub(), destinations=stub(),
                                                        inbox=stub(drop=dropped.append)),
                   "pearnly_cowork.folder_intake.access": access,
                   "pearnly_cowork.targets.identity": stub(normalize_express_account_key=lambda v: v)}
        rules = _load("folder_rules_under_test", APP / "folder_intake/rules.py", modules)
        rules._connection = checked.append  # 每条都按连接使用权核过
        self.assertEqual(rules.delete(json.dumps(["R1", "missing", "R1"])), {"deleted": 1})
        self.assertEqual(checked, ["C1"])
        self.assertEqual(set(db.tables["Cowork Folder Intake Rule"]), {"R2"})
        self.assertEqual(set(db.tables["Cowork Folder Intake Run"]), {"U2"})
        self.assertEqual(set(db.tables["Cowork Folder Intake File"]), {"E3"})
        self.assertEqual(dropped, [])
        for callback in db.after_commit.callbacks:
            callback()
        self.assertEqual(dropped, ["E1"])
        with self.assertRaises(Exception):
            rules.delete("[]")


if __name__ == "__main__":
    unittest.main()
