"""删除就删干净（用户 2026-10-08「删就删除所有记录和储存，不要给服务器带来负担」）：不连库、不依赖 Frappe。

- 录入记录：推送记录 / 尝试 / 通知、纠错样本一起删；只有推送没出结论的不删；
- 原件：没有别的票再用时，原件记录、识别批次、50 ทวิ 参考记录、服务器文件、原件桶里的原件与预览一起删；
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
CORE = ROOT / "apps/pearnly_core/pearnly_core"


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

    def sql(self, query, values=(), as_dict=False):
        """只认 `removal._pushing` 那一句：按票锁它的推送记录。"""
        assert "FROM `tabCowork Push Record`" in query and "FOR UPDATE" in query, query
        rows = self.rows("Cowork Push Record", {"history": values[0]})
        return [Row({key: row.get(key) for key in ("name", "status", "result_unknown")}) for row in rows]

    def delete(self, doctype, filters=None):
        for row in self.rows(doctype, filters):
            del self.tables[doctype][row.name]

    def get_all(self, doctype, filters=None, fields=None, pluck=None, ignore_permissions=False, limit_page_length=None,
                order_by=None, limit=None):
        # 只收锁定版 Frappe 的 get_all 收的参数：多给（如 for_update）照真库抛 TypeError（11-16 实验室删单据就是这样坏的）
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

        frappe = _frappe(db, get_doc=lambda doctype, name, for_update=False: db.rows(doctype, {"name": name})[0], delete_doc=delete_doc)
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
        # 已放行、还在排队的（P2 pending）删前随票撤掉：推送记录一起删，电脑不会再领到（A 线 11-13 主会话定）
        self.assertEqual(result, {"deleted": 3, "skipped": 1})
        self.assertEqual(db.tables["Cowork OCR History"], {})
        self.assertEqual(db.tables["Cowork Push Record"], {})
        self.assertEqual(db.tables["Cowork Push Attempt"], {})
        self.assertEqual(db.tables["Cowork Push Notification"], {})
        self.assertEqual(db.tables["Cowork OCR Correction"], {})
        self.assertEqual(purged, ["CWS-a"])

    def test_unknown_result_or_held_writer_lock_counts_as_pushing(self):
        db = FakeDB({
            "Cowork OCR History": {"H1": {"last_push_status": "manual"}, "H2": {"last_push_status": "success"},
                                   "H3": {"last_push_status": "pending"}, "H4": {"last_push_status": ""}},
            "Cowork Push Record": {"P1": {"history": "H1", "status": "manual", "result_unknown": 1},
                                   "P2": {"history": "H2", "status": "success"},
                                   "P3": {"history": "H3", "status": "leased"},  # CC 桌面版正在写：不删（写完再删）
                                   "P4": {"history": "H4", "status": "prepared"}},
            "Cowork Push Writer Lock": {"L1": {"holder": "P2"}},
        })
        result = self._removal(db, []).delete(["H1", "H2", "H3", "H4"])
        self.assertEqual(result, {"deleted": 0, "skipped": 4})
        self.assertEqual(set(db.tables["Cowork Push Record"]), {"P1", "P2", "P3", "P4"})


class _Refused(Exception):
    pass


class PushWithdraw(unittest.TestCase):
    """已放行、还在排队的推送可以撤回（A 线 11-13 主会话定、11-15 施工）：只撤 pending；电脑正在写的、已有结论的拒并说明；
    撤成了票回到「没推」。替身模块上加载真 `push/native/service.py`，不连库。"""

    def _ledger(self):
        stub = mock.MagicMock
        names = ["frappe", "pearnly_cowork", "pearnly_cowork.access", "pearnly_cowork.access.scope",
                 "pearnly_cowork.history", "pearnly_cowork.history.push_state", "pearnly_cowork.connections",
                 "pearnly_cowork.connections.service", "pearnly_cowork.connections.access",
                 "pearnly_cowork.connections.pool", "pearnly_cowork.connections.preparation", "pearnly_cowork.push",
                 "pearnly_cowork.push.native", "pearnly_cowork.push.native.completion",
                 "pearnly_cowork.push.native.contracts", "pearnly_cowork.push.native.documents",
                 "pearnly_cowork.targets", "pearnly_cowork.targets.freeze", "pearnly_cowork.targets.contracts",
                 "pearnly_core", "pearnly_core.express_rules", "pearnly_core.express_rules.contracts",
                 "pearnly_core.express_rules.contracts.push", "pearnly_core.express_rules.push",
                 "pearnly_core.express_rules.push.common", "pearnly_core.express_rules.push.written_check",
                 "pearnly_core.express_rules.push.referenced", "pearnly_core.cc_desktop", "pearnly_core.cc_desktop.wire",
                 "pearnly_core.cc_desktop.wire.ack_rules", "pearnly_core.express_rules.targets",
                 "pearnly_core.express_rules.targets.identity"]
        modules = {name: stub() for name in names}
        modules["pearnly_cowork.push.native.documents"].TYPES = ("Cowork Push Record", "Cowork Push Attempt", "Lock")
        module = _load("pearnly_cowork.push.native.service_withdraw", APP / "push/native/service.py", modules)
        self.saved, self.cleared, self.checked = [], [], []

        def refuse(code):
            raise _Refused(code)

        module.fail = refuse
        module.call = lambda function, *args, **kwargs: None
        module.atomic = mock.MagicMock()
        module._context = lambda context: None
        module._authorize = lambda context: self.checked.append("authorize")
        module._history = lambda context: self.checked.append("history")
        module._record = lambda name, context: self.row
        module._save_record = lambda row, timestamp, select_history=False: self.saved.append((row.status, row.reason))
        module.now_utc = lambda value=None: "2026-11-15T14:00:00Z"
        module.project = lambda row: dict(vars(row))
        module.push_state = types.SimpleNamespace(withdrawn=lambda row: self.cleared.append(row.name))
        return module

    def _row(self, **values):
        return types.SimpleNamespace(**{"name": "CWP-1", "history": "H1", "status": "pending", "revision": 4,
                                        "result_unknown": 0, "actor": "colleague@x", **values})

    def test_only_a_queued_push_is_withdrawn_and_the_document_goes_back_to_not_pushed(self):
        ledger = self._ledger()
        self.row = self._row()
        out = ledger.withdraw_pending("CWP-1", context=object(), expected_revision=4)
        self.assertEqual((out["status"], out["reason"]), ("cancelled", "push.withdrawn"))
        self.assertEqual(self.saved, [("cancelled", "push.withdrawn")])
        self.assertEqual(self.cleared, ["CWP-1"])  # 票上的推送状态清回「没推」，可以重推
        self.assertEqual(self.checked, ["authorize", "history"])  # 录入权限与公司范围照旧守；不核是谁放行的（11-14 口径）

    def test_a_push_being_written_or_already_settled_is_refused_with_its_reason(self):
        ledger = self._ledger()
        for values, code in (({"status": "leased"}, "push.withdraw_writing"),
                             ({"status": "success"}, "push.withdraw_settled"),
                             ({"status": "manual"}, "push.withdraw_settled"),
                             ({"status": "cancelled"}, "push.withdraw_settled"),
                             ({"status": "pending", "result_unknown": 1}, "push.withdraw_settled")):
            row = {"name": "CWP-1", "history": "H1", "revision": 4, "result_unknown": 0, **values}
            self.row = types.SimpleNamespace(**row)
            with self.assertRaises(_Refused) as raised:
                ledger.withdraw_pending("CWP-1", context=object(), expected_revision=4)
            self.assertEqual(raised.exception.args[0], code, values)
        self.row = self._row()
        with self.assertRaises(_Refused) as raised:  # 页面上的版本过时
            ledger.withdraw_pending("CWP-1", context=object(), expected_revision=3)
        self.assertEqual(raised.exception.args[0], "push.native_revision_conflict")
        self.assertEqual((self.saved, self.cleared), ([], []))

    def test_every_refusal_and_the_button_have_four_languages(self):
        rows = {line.split("\t")[-1]: line.split("\t") for line in
                (APP / "i18n/messages.tsv").read_text(encoding="utf-8").splitlines() if line.strip()}
        for key in ("push.withdrawn", "push.withdraw_writing", "push.withdraw_settled", "cowork.push.withdraw"):
            self.assertEqual(len([cell for cell in rows[key][:4] if cell]), 4, key)
        page = (APP / "public/desk/intake-upload.js").read_text(encoding="utf-8")
        self.assertIn('PUSH + "withdraw"', page)
        self.assertIn('__("cowork.push.withdraw")', page)
        api = (APP / "push/application/api.py").read_text(encoding="utf-8")
        self.assertIn("def withdraw(name):", api)


class OraclePurge(unittest.TestCase):
    def test_original_on_the_oracle_host_is_removed_there(self):
        name = "erpcowork.pearnly.com/originals/CWS-" + "a" * 48 + ".jpg"
        db = FakeDB({"Cowork Source File": {"CWS-o": {"stored_file": "", "storage_url": "oracle://" + name + "#" + "f" * 64}},
                     "Cowork OCR Batch": {}, "Cowork WHT Certificate": {}, "File": {}})
        files, frappe = OriginalPurge._files(self, db, bucket=None)
        self.assertEqual(files.purge(["CWS-o"]), 1)
        self.assertEqual(db.tables["Cowork Source File"], {})
        frappe.enqueue.assert_called_once()
        self.assertEqual(frappe.enqueue.call_args.args[0], "pearnly_cowork.files.service.purge_edge")
        self.assertEqual(frappe.enqueue.call_args.kwargs["names"], [name])


class OriginalPurge(unittest.TestCase):
    def _files(self, db, bucket="pearnly-cowork-originals-sg"):
        frappe = _frappe(db)
        cloud = types.SimpleNamespace(bucket=lambda: bucket, parse_url=lambda v: (
            (v[5:].split("/", 1)[0], v[5:].split("/", 1)[1].rpartition("#")[0], 7) if v and v.startswith("gs://") else None))
        package = types.ModuleType("pearnly_cowork.files")
        package.access = types.SimpleNamespace(SOURCE="Cowork Source File", HISTORY="Cowork OCR History")
        package.guard = types.SimpleNamespace()
        # 原件层（ADR-024）：引用解析用真的签名模块与上面的谷歌解析，删除走后台任务，这里只看排了什么
        links = _load("links_under_test", CORE / "originals/links.py", {})

        def parse(ref):
            found = links.parse_url(ref)
            if found:
                return "oracle", found
            found = cloud.parse_url(ref)
            return ("gcs", found) if found else None
        layer = types.ModuleType("pearnly_core.originals")
        layer.gcs, layer.checks = cloud, types.SimpleNamespace()
        layer.local_file = _load("local_file_under_test", CORE / "originals/local_file.py", {})  # 只依赖标准库，用真的
        layer.store = types.SimpleNamespace(parse=parse, ORACLE="oracle", GCS="gcs")
        rules = types.SimpleNamespace(IMAGE_EXTENSIONS=frozenset(), TABLE_EXTENSIONS=frozenset(), MAX_FILE_BYTES=1,
                                      OcrInputError=Exception)
        modules = {"frappe": frappe, "frappe.utils": types.SimpleNamespace(cint=int),
                   "pearnly_cowork": types.ModuleType("pearnly_cowork"),
                   "pearnly_cowork.access": types.SimpleNamespace(scope=types.SimpleNamespace()),
                   "pearnly_cowork.files": package, "pearnly_core.originals": layer,
                   "pearnly_core.originals.rules": rules}
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
                                 "B3": {"source_file": "CWS-c", "phase": "saved"},
                                 "B4": {"source_file": "CWS-d", "phase": "recognizing"}},  # 还在识别
            # 50 ทวิ 参考记录跟着原件与批次走（批次上没记原件的按批次删）
            "Cowork WHT Certificate": {"W1": {"source_file": "CWS-a", "batch": "B1"},
                                       "W2": {"source_file": "", "batch": "B2"},
                                       "W3": {"source_file": "CWS-c", "batch": "B3"}},
            "File": {"F1": {"file_url": "/private/files/a.pdf", "attached_to_doctype": "Cowork Source File",
                            "attached_to_name": "CWS-a"},
                     "F2": {"file_url": "/private/files/a.pdf", "attached_to_doctype": "Other", "attached_to_name": "x"},
                     "F3": {"file_url": "/private/files/c.pdf", "attached_to_doctype": "Cowork Source File",
                            "attached_to_name": "CWS-c"}},
        })
        files, frappe = self._files(db)
        self.assertEqual(files.purge(["CWS-a", "CWS-b", "CWS-c", "CWS-d", "CWS-a"]), 2)
        self.assertEqual(set(db.tables["Cowork Source File"]), {"CWS-c", "CWS-d"})
        self.assertEqual(set(db.tables["Cowork OCR Batch"]), {"B3", "B4"})
        self.assertEqual(set(db.tables["Cowork WHT Certificate"]), {"W3"})  # 原件还有票在用：参考记录也留着
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
        source = (CORE / "originals/gcs.py").read_text()
        frappe = types.SimpleNamespace(conf={})
        cloud = _load("files_cloud_under_test", CORE / "originals/gcs.py", {"frappe": frappe})
        for prefix in ("", "testcowork.pearnly.com/", "testcowork.pearnly.com/originals/",
                       "testcowork.pearnly.com/originals/x", "nosite/originals/CWS-a", "a.b/other/CWS-a"):
            with self.assertRaises(ValueError, msg=prefix):
                cloud.delete_prefix("bucket", prefix, http=object())
        self.assertIn('"versions": "true"', source)

    def test_deletes_every_version_and_treats_missing_as_deleted(self):
        frappe = types.SimpleNamespace(conf={})
        cloud = _load("files_cloud_under_test", CORE / "originals/gcs.py", {"frappe": frappe})
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
                   "pearnly_cowork.companion": stub(devices=stub()),
                   "pearnly_cowork.connections": stub(access=stub(), preparation=stub(), service=stub(),
                                                      desktop_update=_load("connections_desktop_update_delete",
                                                                           APP / "connections/desktop_update.py", {})),
                   "pearnly_cowork.folder_intake": stub(agent=stub(), contracts=stub(), destinations=stub(), readers=stub(),
                                                        follow=stub(), inbox=stub(drop=dropped.append)),
                   "pearnly_cowork.folder_intake.access": access,
                   "pearnly_core.express_rules.targets.identity": stub(normalize_express_account_key=lambda v: v)}
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
