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


DOCS = [{"name": f"H{i}", "invoice_no": f"INV{i}", "invoice_date": "2026-11-05", "seller_name": "Seller", "buyer_name": "Me",
         "total_amount": "107.00", "filename": f"f{i}.jpg", "direction": "purchase", "push_connection": "C1",
         "push_account_set": "BOOK"} for i in range(1, 8)]
DOCS.append({"name": "OTHER", "invoice_no": "INVX", "invoice_date": "2026-11-05", "seller_name": "Elsewhere", "buyer_name": "B",
             "total_amount": "1.00", "filename": "x.jpg", "direction": "purchase", "push_connection": "C2", "push_account_set": "B2"})
INVOICE_DATES = {"D1": "2026-11-02", "D2": "2026-11-03"}   # 待处理行的发票日期（`frappe.get_all` 替身按它回）


def _listed(payload):
    """`tools.list_documents` 的替身：按页切、给 next_offset 与 total（同真实工具的形状）。"""
    rows = [row for row in DOCS if row["push_connection"] == "C1" and (not payload.get("search") or payload["search"] in row["invoice_no"])]
    start, size = int(payload.get("offset") or 0), int(payload.get("limit") or 20)
    page = rows[start:start + size]
    return {"documents": page, "total": len(rows), "offset": start, "next_offset": start + len(page)}


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
        delete_documents=lambda payload: STATE["deleted"].append(payload["documents"]) or json.dumps(
            STATE.get("delete_result") or {"deleted": 1, "skipped": 1}),
        list_documents=lambda payload: STATE["listed"].append(dict(payload)) or json.dumps(_listed(payload)),
        # 公司范围（同 `tools.list_documents`）：本对话公司推往 C1 的 BOOK
        _company_scope=lambda tool_input: {"sets": [{"connection": "C1", "key": "BOOK"}]},
        _scope_pairs=lambda sets: {(item["connection"], item["key"].casefold()) for item in sets},
        _account_key=lambda value: str(value or "").casefold())

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
        STATE.update(intake=True, asked=[], exported=[], deleted=[], started=[], rollbacks=[], listed=[], got=[])
        frappe.get_list = lambda doctype, filters=None, fields=None, limit_page_length=0, **k: STATE["got"].append(filters) or [
            dict(row) for row in DOCS if row["name"] in filters["name"][1]]
        frappe.get_all = lambda doctype, filters=None, fields=None, limit_page_length=0, **k: [
            types.SimpleNamespace(name=name, invoice_date=INVOICE_DATES.get(name, "")) for name in filters["name"][1]]
        HOOKS["pearnly_guides"] = ["pearnly_cowork.assistant.guided.pending.GUIDES"]
        self.docs, self.clock = {}, [0]
        self.now = {"session_id": SESSION, "mode": "chat", "company": "CO-1", "ledger": "BOOK", "period": "2026-11",
                    "stopped_at": None}
        # 代录对话里清单按对话月份筛（`pending._operator` 读会话的模式）
        frappe.db.get_value = lambda doctype, filters, field=None, *a, **k: (
            "TASK1" if field == "operator_task" and self.now.get("mode") == "operator" else None)
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
        # 待处理与单据（单据维护，总表第 14 行）两份声明，直写与代录都开（11-24 主会话）
        self.assertEqual(registry.offered()["chat"], ["pending", "documents"])
        self.assertEqual(registry.offered()["operator"], ["pending", "documents"])
        for found in guide.GUIDES:
            self.assertTrue(callable(_get_attr(found["result"])), found["id"])
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
        self.assertEqual(done["result"]["docnum"], "IV2 · Buyer B")   # 结果卡写单号 · 对方（交出那刻记下）
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


class OperatorMonth(Base):
    def test_in_an_operator_conversation_pending_lists_only_the_conversation_month(self):
        # D3 是 CC 提议：待处理行上显示的是提议日期（11 月），票面发票日期是 10 月——代录按发票日期筛（同代录交单）
        ITEMS.append({"document": "D3", "invoice_no": "IV3", "counterparty": "Old", "reason_label": "", "amount": "1.00",
                      "date": "2026-11-20", "company": "CO-1"})
        INVOICE_DATES["D3"] = "2026-10-30"
        self.addCleanup(ITEMS.pop)
        self.addCleanup(INVOICE_DATES.pop, "D3")
        ctx = {"company": "CO-1", "session": SESSION, "period": "2026-11", "business": "pending"}
        self.assertEqual([row["code"] for row in guide.item_options(ctx)["options"]], ["D1", "D2", "D3"])   # 直写不按月
        self.now["mode"] = "operator"
        self.assertEqual([row["code"] for row in guide.item_options(ctx)["options"]], ["D1", "D2"])


class Documents(Base):
    CTX = {"company": "CO-1", "session": SESSION, "period": "2026-11", "business": "documents"}

    def test_the_list_is_every_document_of_the_conversation_month_paged_on_the_server(self):
        found = guide.item_options(self.CTX, txt="", start=0, page_length=5)
        self.assertEqual([row["code"] for row in found["options"]], ["H1", "H2", "H3", "H4", "H5"])
        self.assertTrue(found["more"])
        self.assertEqual(STATE["listed"][-1], {"invoice_date_from": "2026-11-01", "invoice_date_to": "2026-11-30",
                                               "search": "", "limit": 5, "offset": 0})
        self.assertEqual(found["options"][0], {"code": "H1", "name": "INV1 · Seller", "note": "107.00 · 2026-11-05"})
        rest = guide.item_options(self.CTX, txt="", start=5, page_length=5)
        self.assertEqual(([row["code"] for row in rest["options"]], rest["more"]), (["H6", "H7"], False))
        self.assertEqual([row["code"] for row in guide.item_options(self.CTX, txt="INV3")["options"]], ["H3"])

    def test_chosen_documents_are_read_back_by_name_within_the_conversation_month(self):
        found = guide.item_options(self.CTX, codes=["H2", "H9", "OTHER"])
        self.assertEqual([row["code"] for row in found["options"]], ["H2"])   # 别家公司的单（OTHER）不收
        self.assertEqual(STATE["got"][-1], {"name": ["in", ["H2", "H9", "OTHER"]], "invoice_date": ["between", ["2026-11-01", "2026-11-30"]]})

    def test_the_result_says_what_was_done(self):
        result = guide.result
        handed = result({**self.CTX, "done": {"status": "entered", "docnum": "H1", "label": "INV1 · Seller",
                                              "next": {"run": "R2", "business": "purchase"}}}, "")
        self.assertEqual((handed["status"], handed["docnum"]), ("ready", "INV1 · Seller"))   # 交出那刻记下的单号 · 对方，不出内部编号
        files = result({**self.CTX, "done": {"status": "entered", "files": [{"file": "F", "name": "r.xlsx"}]}}, "")
        self.assertEqual(files["status"], "ready")
        self.assertEqual(result({**self.CTX, "done": {"status": "entered", "deleted": 2, "skipped": 0}}, "")["reason_text"],
                         "intake.delete.done")
        part = result({**self.CTX, "done": {"status": "entered", "deleted": 1, "skipped": 1}}, "")
        self.assertEqual((part["status"], part["reason_text"]), ("ready", "intake.delete.partial"))
        none = result({**self.CTX, "done": {"status": "entered", "deleted": 0, "skipped": 2}}, "")
        self.assertEqual((none["status"], none["reason_text"]), ("held", "intake.delete.none"))

    def test_the_documents_guide_runs_end_to_end_with_the_same_functions(self):
        name = run.start(SESSION, "documents")["run"]
        found = run.answer(name, {"action": "delete"})
        self.assertEqual(found["consent"], "allow")
        found = run.answer(name, {"items": ["H1", "H2"]})
        STATE["delete_result"] = {"deleted": 2, "skipped": 0}
        done = run.confirm(name, found["version"])
        self.assertEqual(STATE["deleted"], [["H1", "H2"]])
        self.assertEqual(done["result"]["reason_text"], "intake.delete.done")


if __name__ == "__main__":
    unittest.main()
