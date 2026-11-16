"""ERP 待处理的引导式（总表 ERP-D-PD，`express.cc_guide_pending` 声明 + `express.guide_pending` 产品函数）：共用引擎真实现走一遍。

断言的是应有状态：能开采购或销售单的人才见「待处理」；清单按本对话公司、只列采购 / 销售单（码 P: / S:）、能搜索；
确定后把那张单交给单据引导（业务按单据源的方向判断），从判定卡接着走；单据源核不过退回选单那一步。

    python3 tests/test_erp_pending_guide.py
"""
import datetime
import importlib
import json
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for app in ("pearnly_core", "pearnly_erp_portal"):
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





ITEMS = [{"doctype": "Purchase Document", "document": "PD-1", "invoice_no": "IV1", "counterparty": "Seller A",
          "reason_label": "Choose a value", "amount": "1,070.00", "date": "2026-11-02", "company": "CO-1"},
         {"doctype": "ERP Sales Document", "document": "SD-2", "invoice_no": "IV2", "counterparty": "Buyer B",
          "reason_label": "Result unknown", "amount": "500.00", "date": "2026-11-03", "company": "CO-1"},
         {"doctype": "ERP Stock Entry", "document": "SE-3", "invoice_no": "", "counterparty": "", "reason_label": "x",
          "amount": "", "date": "", "company": "CO-1"}]


def _pending(company=None, limit=None, search=None):
    STATE["asked"].append((company, search))
    rows = [dict(row) for row in ITEMS if (not search or search.casefold() in f"{row['invoice_no']} {row['counterparty']}".casefold())]
    return {"items": rows, "count": len(rows)}


_module("pearnly_erp_portal.access.guard", has=lambda capability, user=None: STATE["write"])
_module("pearnly_erp_portal.integrations.intake", DIRECTION_CAPABILITY={"purchase": "purchase.write", "sales": "sales.write"})
_module("pearnly_erp_portal.express.pending", pending=_pending)
_module("pearnly_erp_portal.express.guide_documents",
        direction_of=lambda doc: {"P": "purchase", "S": "sales"}.get(str(doc).partition(":")[0]))

from pearnly_core.assistant.guide import registry, run  # noqa: E402
from pearnly_erp_portal.express import guide_pending as guide  # noqa: E402

SESSION = "S1"


class Doc(dict):
    __getattr__ = dict.get

    def __setattr__(self, key, value):
        self[key] = value


class Base(unittest.TestCase):
    def setUp(self):
        STATE.clear()
        STATE.update(write=True, asked=[], started=[], rollbacks=[])
        HOOKS["pearnly_guides"] = ["pearnly_erp_portal.express.cc_guide_pending.GUIDES"]
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
                raise PermissionError(STATE["refuse"])
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


class Pending(Base):
    def test_menu_follows_purchase_or_sales_writing(self):
        self.assertEqual(registry.offered()["chat"], ["pending"])
        STATE["write"] = False
        self.assertEqual(registry.offered()["chat"], [])

    def test_only_purchase_and_sales_documents_of_this_company_are_offered(self):
        found = run.start(SESSION, "pending")
        options = found["card"]["questions"][0]["options"]
        self.assertEqual([row["code"] for row in options], ["P:PD-1", "S:SD-2"])
        self.assertEqual(STATE["asked"][-1], ("CO-1", None))
        self.assertEqual([row["code"] for row in guide.item_options({"company": "CO-1"}, txt="buyer")["options"]], ["S:SD-2"])

    def test_confirming_hands_the_document_to_its_documents_guide(self):
        name = run.start(SESSION, "pending")["run"]
        found = run.answer(name, {"item": "S:SD-2"})
        self.assertEqual(found["review"]["tables"][0]["rows"][0]["name"], "IV2 · Buyer B")
        done = run.confirm(name, found["version"])
        self.assertEqual(STATE["started"], [(SESSION, "sales", ["S:SD-2"], "asking")])
        self.assertEqual(done["result"]["next"], {"run": "RUN9", "business": "sales"})

    def test_a_document_the_documents_source_refuses_goes_back(self):
        STATE["refuse"] = "Not permitted"
        name = run.start(SESSION, "pending")["run"]
        found = run.answer(name, {"item": "P:PD-1"})
        back = run.confirm(name, found["version"])
        self.assertEqual(back["card"]["returned"]["item"]["text"], "Not permitted")

    def test_a_stock_entry_is_not_accepted(self):
        name = run.start(SESSION, "pending")["run"]
        self.assertEqual({key: value["code"] for key, value in run.answer(name, {"item": "M:SE-3"})["refused"].items()}, {"item": "not offered"})  # 拒收照引擎形状 {格: {code, text}}，只比码


if __name__ == "__main__":
    unittest.main()
