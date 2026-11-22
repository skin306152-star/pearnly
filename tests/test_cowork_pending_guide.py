"""Cowork 待处理的引导式（总表 CW-D-PD，`assistant.guided.pending`）：用共用引擎 `assistant.guide.run` 真实现走一遍。
替身 frappe、待处理清单（`companies.pending`）、单据源的方向判断（`assistant.guide_documents.direction_of`）与 CC 工具。

断言的是应有状态：
- 菜单：有录入功能的人才见「待处理」；删除画允许卡，其余是确定；
- 清单按本对话公司、能搜索；不在清单里的单不收；
- 处理 / 改票面：确定后把这张单交给单据引导（采购或销售按单据方向），从判定（asking）或复核（review）接着走；单据源核不过退回选单那一步；
- 导出复核工作簿：文件放进对话；删除：一次删完。

    python3 tests/test_cowork_pending_guide.py
"""
import datetime
import importlib
import json
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for app in ("pearnly_core", "pearnly_cowork"):
    sys.path.insert(0, str(ROOT / "apps" / app))

HOOKS, LOGGED = {}, []
frappe = types.ModuleType("frappe")
frappe._ = lambda text: text
frappe.get_hooks = lambda name: HOOKS.get(name, [])


def _get_attr(path):
    module, _dot, attr = path.rpartition(".")
    return getattr(importlib.import_module(module), attr)


frappe.get_attr = _get_attr
frappe.log_error = lambda title="", message="": LOGGED.append(title)
frappe.session = types.SimpleNamespace(user="a@x")
frappe.local = types.SimpleNamespace(site="s1")
frappe.DoesNotExistError = LookupError
frappe.PermissionError = PermissionError
frappe.ValidationError = ValueError
frappe.QueryDeadlockError = type("QueryDeadlockError", (Exception,), {})
frappe.clear_messages = lambda: None


def _throw(message, exc=ValueError):
    raise exc(message)


frappe.throw = _throw
STATE = {}
frappe.db = types.SimpleNamespace(commit=lambda: None, rollback=lambda: STATE["rollbacks"].append(1))
frappe.utils = types.SimpleNamespace(get_datetime=lambda value: value, now_datetime=lambda: datetime.datetime(2026, 11, 16, 10, 5))  # 放太久失效（run._idle）：同测试里的时钟
frappe.publish_realtime = lambda *args, **kwargs: None
sys.modules["frappe"] = frappe


def _module(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules[name] = module
    return module


_module("pearnly_core.assistant.contracts", REALTIME_EVENT="pearnly_assistant_event")
_module("pearnly_core.assistant.write_plan", _iso=lambda moment: moment.isoformat() + "Z")




def _require(capability):
    if not STATE["intake"]:
        raise PermissionError("Not permitted")


ITEMS = [{"document": "D1", "invoice_no": "IV1", "counterparty": "Seller A", "reason_label": "Choose a value", "amount": "1,070.00",
          "date": "2026-11-02", "company": "CO-1"},
         {"document": "D2", "invoice_no": "IV2", "counterparty": "Buyer B", "reason_label": "Result unknown", "amount": "500.00",
          "date": "2026-11-03", "company": "CO-1"}]


def _pending(company=None, limit=None, search=None):
    STATE["asked"].append((company, search))
    rows = [dict(row) for row in ITEMS if (not company or row["company"] == company)
            and (not search or search.casefold() in f"{row['invoice_no']} {row['counterparty']}".casefold())]
    return {"items": rows, "count": len(rows)}


_module("pearnly_cowork.access")
_module("pearnly_cowork.access.scope", require=_require)
_module("pearnly_cowork.companies")
_module("pearnly_cowork.companies.pending", pending=_pending)
_module("pearnly_cowork.assistant.guide_documents", direction_of=lambda doc: {"D1": "purchase", "D2": "sales"}.get(doc))
_module("pearnly_core.assistant.files", resolve=lambda path: path.split("/")[-2])
_module("pearnly_cowork.assistant.tools",
        export_review_workbook=lambda payload: STATE["exported"].append(payload["documents"]) or json.dumps(
            {"status": "exported", "documents": 2, "file": "/mnt/session/uploads/F3/review.xlsx"}),
        delete_documents=lambda payload: STATE["deleted"].append(payload["documents"]) or json.dumps({"deleted": 1, "skipped": 1}))

from pearnly_core.assistant.guide import registry, run  # noqa: E402
from pearnly_cowork.assistant.guided import pending as guide  # noqa: E402

SESSION = "S1"


def _codes(found):
    """拒收照引擎形状 {格: {code, text}}（main 8172605d2）：只比码。"""
    return {key: value["code"] for key, value in found["refused"].items()}


class Doc(dict):
    __getattr__ = dict.get

    def __setattr__(self, key, value):
        self[key] = value


class Base(unittest.TestCase):
    def setUp(self):
        STATE.clear()
        STATE.update(intake=True, asked=[], exported=[], deleted=[], started=[], rollbacks=[])
        HOOKS["pearnly_guides"] = ["pearnly_cowork.assistant.guided.pending.GUIDES"]
        self.docs, self.clock = {}, [0]
        self.now = {"session_id": SESSION, "mode": "chat", "company": "CO-1", "ledger": "BOOK", "period": "2026-11",
                    "stopped_at": None}
        run._insert, run._locked, run._save, run._open_runs, run._conversation, run._announce = (
            self.insert, self.locked, self.save, self.open_runs, lambda session_id: dict(self.now), lambda doc: None)
        self.real_start = run.start

        def start(session_id, business, doc_ids=None, at="asking", **kwargs):
            if doc_ids is None:
                return self.real_start(session_id, business)
            STATE["started"].append((session_id, business, doc_ids, at))
            if STATE.get("refuse"):
                raise ValueError(STATE["refuse"])
            return {"run": "RUN9"}
        run.start = start
        self.addCleanup(setattr, run, "start", self.real_start)

    def tick(self):
        self.clock[0] += 1
        return datetime.datetime(2026, 11, 16, 10, 0, self.clock[0])

    def insert(self, values):
        doc = Doc(values, name=f"RUN{len(self.docs) + 1}", modified=self.tick())
        self.docs[doc.name] = doc
        return doc

    def locked(self, name):
        doc = self.docs.get(name)
        return doc if doc and doc.user == frappe.session.user else None

    def save(self, doc):
        doc.modified = self.tick()

    def open_runs(self, session_id):
        return [d.name for d in self.docs.values() if d.session_id == session_id and d.status in run.OPEN]

    @staticmethod
    def questions(found):
        return {q["key"]: q for q in found["card"]["questions"]}

    def walk(self, *answers):
        name = run.start(SESSION, "pending")["run"]
        found = None
        for given in answers:
            found = run.answer(name, given)
        return name, found


class Pending(Base):
    def test_paths_resolve_and_the_menu_follows_intake(self):
        found = guide.GUIDES[0]
        for step in found["steps"]:
            self.assertTrue(callable(_get_attr(step["options"])), step["options"])
        for key in ("allowed", "draft", "review", "confirm"):
            self.assertTrue(callable(_get_attr(found[key])), found[key])
        self.assertEqual(registry.offered()["chat"], ["pending"])
        STATE["intake"] = False
        self.assertEqual(registry.offered()["chat"], [])

    def test_delete_draws_the_allow_card(self):
        name = run.start(SESSION, "pending")["run"]
        for action, consent in (("delete", "allow"), ("handle", "confirm"), ("export", "confirm")):
            self.assertEqual(run.answer(name, {"action": action})["consent"], consent, action)

    def test_the_list_is_the_conversation_company_pending_and_searchable(self):
        name, found = self.walk({"action": "handle"})
        self.assertEqual([row["code"] for row in self.questions(found)["item"]["options"]], ["D1", "D2"])
        self.assertEqual(STATE["asked"][-1], ("CO-1", None))
        self.assertEqual([row["code"] for row in guide.item_options({"company": "CO-1"}, txt="buyer")["options"]], ["D2"])
        self.assertEqual(_codes(run.answer(name, {"item": "D9"})), {"item": "not offered"})

    def test_handling_hands_the_document_to_its_documents_guide_at_the_card(self):
        name, found = self.walk({"action": "handle"}, {"item": "D2"})
        self.assertEqual(found["review"]["tables"][0]["rows"][0]["name"], "IV2 · Buyer B")
        done = run.confirm(name, found["version"])
        self.assertEqual(STATE["started"], [(SESSION, "sales", ["D2"], "asking")])
        self.assertEqual(done["result"]["next"], {"run": "RUN9", "business": "sales"})
        run.confirm(name, found["version"])
        self.assertEqual(len(STATE["started"]), 1)   # 同一确定不交第二次

    def test_editing_the_face_opens_at_review(self):
        name, found = self.walk({"action": "review"}, {"item": "D1"})
        run.confirm(name, found["version"])
        self.assertEqual(STATE["started"], [(SESSION, "purchase", ["D1"], "review")])

    def test_a_document_the_documents_source_refuses_goes_back_to_the_pick(self):
        STATE["refuse"] = "Invalid request"
        name, found = self.walk({"action": "handle"}, {"item": "D1"})
        back = run.confirm(name, found["version"])
        self.assertEqual(back["card"]["returned"]["item"]["text"], "Invalid request")

    def test_export_puts_the_workbook_in_the_conversation_and_delete_deletes_once(self):
        name, found = self.walk({"action": "export"}, {"items": ["D1", "D2"]})
        done = run.confirm(name, found["version"])
        self.assertEqual((STATE["exported"], done["result"]["files"]), ([["D1", "D2"]], [{"file": "F3", "name": "review.xlsx"}]))
        name, found = self.walk({"action": "delete"}, {"items": ["D1"]})
        done = run.confirm(name, found["version"])
        self.assertEqual((STATE["deleted"], done["result"]["deleted"]), ([["D1"]], 1))


if __name__ == "__main__":
    unittest.main()
