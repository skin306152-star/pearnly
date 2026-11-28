"""输入框交来的票据进单据引导（``pearnly_core.assistant.guide.composer``，11-28 testerp「点采购后用输入框 + 上传，不进识别」）。

断言的是应有状态（替身 frappe 与替身引导引擎，不连库；真页面在流程 erp.cc.guide.purchase-composer 等）：
- 选了按单据的业务、没有开着的同业务引导：开一次（开新的照旧取消别的业务那条，`run.start` 负责），交这几份；
- 同业务引导还在交文件那一步：交进它，不另开；已过交文件（识别 / 题卡 / 复核）或是别处交来的已有单：不动它；
- 没选业务、有按单据的业务：叫模型问一次；选的是表单业务（收款）或这里没有按单据的业务：不接；代录会话、没带本人上传的文件：不接；
- 出错不挡发消息；后台交文件 = 引导交文件那一题的回答（`run.answer`，唯一入口）；
- 打字路径 ``open_with_files``：业务不在这段对话能开的里面拒；文件按本对话认编号；开好并当场交进去。

    python3 tests/test_cc_guide_composer.py
"""
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))

LOGGED, QUEUED = [], []
frappe = types.ModuleType("frappe")
frappe._ = lambda text: text
frappe.get_hooks = lambda name: []
frappe.log_error = lambda title="", message="": LOGGED.append(title)
frappe.session = types.SimpleNamespace(user="u@example.com")
frappe.local = types.SimpleNamespace(lang="zh")
frappe.db = types.SimpleNamespace(commit=lambda: None, rollback=lambda: None)
frappe.enqueue = lambda method, **kwargs: QUEUED.append((method, kwargs))
frappe.set_user = lambda user: setattr(frappe.session, "user", user)
frappe.logger = lambda name: types.SimpleNamespace(info=lambda value: None)
sys.modules["frappe"] = frappe

from pearnly_core.assistant.guide import composer  # noqa: E402

DOCUMENTS = {"intake": "a", "uploads": "b", "review": "c", "edit": "d", "confirm": "e", "result": "f"}
DECLARED = [{"id": "collection", "source": "form"},
            {"id": "purchase", "source": "documents", **DOCUMENTS},
            {"id": "sales", "source": "documents", **DOCUMENTS},
            {"id": "stocktake", "source": "documents"}]   # 声明没点名单据函数的：开不了


class Row(dict):
    __getattr__ = dict.get


class Engine:
    """替身引导引擎：开着的引导、开新的、作答。"""

    def __init__(self, runs=()):
        self.runs = {run["name"]: run for run in runs}
        self.started, self.answered = [], []
        self.DOCTYPE = "Pearnly Assistant Guide Run"

    def _open_runs(self, session_id):
        return [name for name, run in self.runs.items() if run["status"] in ("asking", "reviewing")]

    def start(self, session_id, business):
        name = f"GR-{len(self.runs) + 1}"
        self.runs[name] = {"name": name, "business": business, "status": "asking", "documents": "{}"}
        self.started.append((session_id, business))
        return {"run": name}

    def answer(self, name, given, version=None):
        self.answered.append((name, given))
        return {}


class ComposerTests(unittest.TestCase):
    def setUp(self):
        LOGGED.clear()
        QUEUED.clear()
        self.offered = {"chat": ["collection", "purchase", "sales", "stocktake"], "operator": ["purchase"]}
        self.attached = ["file_1", "file_2"]

    def _modules(self, engine):
        documents = types.ModuleType("pearnly_core.assistant.guide.documents")
        documents.FILE_KEY = "documents"
        documents.state = lambda doc: {"files": [], "batches": [], **json.loads(doc.documents or "{}")}
        files = types.ModuleType("pearnly_core.assistant.files")
        files.ids_in = lambda row, text: list(self.attached) if "/uploads/" in text else []
        files.resolve = lambda ref: str(ref).split("/")[-2] if "/" in str(ref) else str(ref)
        guided = types.ModuleType("pearnly_core.assistant.guided")
        guided.offered_for = lambda session_id: self.offered
        sessions = types.ModuleType("pearnly_core.assistant.sessions")
        sessions.owned = lambda session_id: Row(session_id=session_id, operator_task="")
        run = types.ModuleType("pearnly_core.assistant.guide.run")
        for key in ("_open_runs", "start", "answer", "DOCTYPE"):
            setattr(run, key, getattr(engine, key))
        frappe.get_doc = lambda doctype, name: Row(engine.runs[name])
        package = sys.modules["pearnly_core.assistant.guide"]
        assistant = sys.modules["pearnly_core.assistant"]
        return [mock.patch.dict(sys.modules, {"pearnly_core.assistant.guide.documents": documents,
                                              "pearnly_core.assistant.guide.run": run,
                                              "pearnly_core.assistant.files": files,
                                              "pearnly_core.assistant.guided": guided,
                                              "pearnly_core.assistant.sessions": sessions}),
                mock.patch.object(package, "documents", documents, create=True),
                mock.patch.object(package, "run", run, create=True),
                mock.patch.object(assistant, "files", files, create=True),
                mock.patch.object(assistant, "guided", guided, create=True),
                mock.patch.object(assistant, "sessions", sessions, create=True),
                mock.patch.object(composer.registry, "declared", lambda: DECLARED)]

    def _plan(self, engine, row, text="see /mnt/session/uploads/file_1/a.png"):
        patches = self._modules(engine)
        for patch in patches:
            patch.start()
        try:
            return composer.plan(row, text)
        finally:
            for patch in reversed(patches):
                patch.stop()

    def test_a_chosen_documents_business_opens_its_guide_and_takes_the_files(self):
        engine = Engine()
        found = self._plan(engine, Row(session_id="s1", business="purchase"))
        self.assertEqual(found, {"business": "purchase", "run": "GR-1", "files": ["file_1", "file_2"]})
        self.assertEqual(engine.started, [("s1", "purchase")])
        composer.queue(found)
        method, kwargs = QUEUED[-1]
        self.assertEqual(method, composer.HAND_JOB)
        self.assertEqual((kwargs["run_name"], kwargs["file_ids"], kwargs["user"], kwargs["lang"]),
                         ("GR-1", ["file_1", "file_2"], "u@example.com", "zh"))
        self.assertTrue(kwargs["enqueue_after_commit"])

    def test_an_open_guide_still_taking_files_takes_them_and_one_past_it_is_left_alone(self):
        waiting = {"name": "GR-7", "business": "purchase", "status": "asking", "documents": "{}"}
        engine = Engine([waiting])
        self.assertEqual(self._plan(engine, Row(session_id="s1", business="purchase"))["run"], "GR-7")
        self.assertEqual(engine.started, [])
        for documents in ('{"batches": ["B1"]}', '{"preset": true, "doc_ids": ["I:LI-1"]}'):
            engine = Engine([{**waiting, "documents": documents}])
            # 同业务的卡已过交文件：不另开（会把它作废），交回 busy 让模型请用户先办完
            self.assertEqual(self._plan(engine, Row(session_id="s1", business="purchase")),
                             {"busy": "purchase", "files": ["file_1", "file_2"]}, documents)
            self.assertEqual(engine.started, [])
        # 开着的是别的业务（收款）：开采购这一条（run.start 会取消收款那条）
        engine = Engine([{**waiting, "business": "collection"}])
        self.assertEqual(self._plan(engine, Row(session_id="s1", business="purchase"))["run"], "GR-2")

    def test_without_a_business_the_model_asks_once_and_other_cases_are_not_taken(self):
        engine = Engine()
        self.assertEqual(self._plan(engine, Row(session_id="s1", business="")),
                         {"ask": ["purchase", "sales"], "files": ["file_1", "file_2"]})
        for row, text in ((Row(session_id="s1", business="collection"), "x /uploads/ y"),   # 表单业务：卡上自己交凭证
                          (Row(session_id="s1", business="stocktake"), "x /uploads/ y"),    # 没点名单据函数
                          (Row(session_id="s1", business="purchase", operator_task="T1"), "x /uploads/ y"),  # 代录：发出即识别
                          (Row(session_id="s1", business="purchase"), "只有字")):
            self.assertEqual(self._plan(engine, row, text), {}, (row, text))
        self.offered = {"chat": ["collection"], "operator": []}
        self.assertEqual(self._plan(engine, Row(session_id="s1", business="")), {})
        self.assertEqual(engine.started, [])

    def test_a_failure_does_not_stop_the_message(self):
        engine = Engine()
        engine.start = mock.Mock(side_effect=RuntimeError("down"))
        self.assertEqual(self._plan(engine, Row(session_id="s1", business="purchase")), {})
        self.assertEqual(LOGGED, ["Assistant composer files to guide failed"])
        composer.queue({})
        self.assertEqual(QUEUED, [])

    def test_hand_in_answers_the_file_step_as_the_sender(self):
        engine = Engine([{"name": "GR-1", "business": "purchase", "status": "asking", "documents": "{}"}])
        execution = types.ModuleType("pearnly_core.execution")
        execution.language = types.SimpleNamespace(settle=lambda lang: None)
        patches = self._modules(engine) + [mock.patch.dict(sys.modules, {"pearnly_core.execution": execution,
                                                                         "pearnly_core.execution.language": execution.language})]
        for patch in patches:
            patch.start()
        try:
            composer.hand_in("GR-1", ["file_1"], "a@example.com", "th")
            self.assertEqual(engine.answered, [("GR-1", {"documents": ["file_1"]})])
            self.assertEqual(frappe.session.user, "a@example.com")
            sys.modules["pearnly_core.assistant.guide.run"].answer = mock.Mock(side_effect=RuntimeError("ocr down"))
            composer.hand_in("GR-1", ["file_1"], "a@example.com")
            self.assertEqual(LOGGED, ["Assistant composer files hand-in failed"])
        finally:
            for patch in reversed(patches):
                patch.stop()
            frappe.session.user = "u@example.com"

    def test_typed_path_opens_the_guide_with_the_files(self):
        engine = Engine()
        patches = self._modules(engine)
        for patch in patches:
            patch.start()
        try:
            sessions = sys.modules["pearnly_core.assistant.sessions"]
            owned = sessions.owned
            sessions.owned = lambda session_id: Row(session_id=session_id, operator_task="T1")
            self.assertEqual(composer.open_with_files("s1", "purchase", ["file_1"])["reason"], "operator_takes_files_on_send")
            sessions.owned = owned
            self.assertEqual(engine.started, [])
            refused = composer.open_with_files("s1", "collection", ["file_1"])
            self.assertEqual((refused["status"], refused["reason"], refused["offered"]),
                             ("refused", "business_not_offered", ["purchase", "sales"]))
            self.assertEqual(composer.open_with_files("s1", "purchase", [" "])["reason"], "no_files")
            found = composer.open_with_files("s1", "sales", ["/mnt/session/uploads/file_9/b.pdf", "file_9"])
            self.assertEqual(found, {"status": "opened", "run": "GR-1", "files": ["file_9"]})
            self.assertEqual(engine.answered, [("GR-1", {"documents": ["file_9"]})])
            sys.modules["pearnly_core.assistant.guide.run"].answer = lambda name, given, version=None: {"outcome": "refused"}
            self.assertEqual(composer.open_with_files("s1", "purchase", ["file_1"])["reason"], "files_not_taken")
        finally:
            for patch in reversed(patches):
                patch.stop()


if __name__ == "__main__":
    unittest.main()
