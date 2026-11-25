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


_module("pearnly_erp_portal.access.guard", has=lambda capability, user=None: STATE["write"],
        require=lambda capability: STATE["required"].append(capability))
_module("pearnly_erp_portal.integrations.intake", DIRECTION_CAPABILITY={"purchase": "purchase.write", "sales": "sales.write"})
_module("pearnly_erp_portal.express.pending", pending=_pending)
_module("pearnly_erp_portal.express.guide_documents",
        direction_of=lambda doc: {"P": "purchase", "S": "sales"}.get(str(doc).partition(":")[0]))


# ── 单据维护（documents）用到的网页函数：列表查询、导出工作簿、删草稿、对话文件、本对话公司 ──
import contextlib  # noqa: E402

DOCS = {"purchase": [{"name": "PD-1", "doc_no": "IV1", "supplier_name": "Seller A", "doc_date": "2026-11-02", "grand_total": 1070,
                      "status": "draft", "company": "CO-1"},
                     {"name": "PD-4", "doc_no": "IV4", "supplier_name": "Seller D", "doc_date": "2026-11-20", "grand_total": 30,
                      "status": "submitted", "company": "CO-1"},
                     {"name": "PD-5", "doc_no": "IV5", "supplier_name": "Seller E", "doc_date": "2026-10-20", "grand_total": 9,
                      "status": "draft", "company": "CO-1"},
                     {"name": "PD-6", "doc_no": "IV6", "supplier_name": "Other Co", "doc_date": "2026-11-21", "grand_total": 9,
                      "status": "draft", "company": "CO-2"}],
        "sales": [{"name": "SD-2", "doc_no": "", "system_no": "SI-2", "buyer_name": "Buyer B", "doc_date": "2026-11-03",
                   "grand_total": 500, "status": "draft", "company": "CO-1", "source": "cc"},
                  {"name": "POS-7", "doc_no": "", "system_no": "SI-7", "buyer_name": "", "doc_date": "2026-11-04",
                   "grand_total": 50, "status": "submitted", "company": "CO-1", "source": "pos"}]}
ACTING = []


@contextlib.contextmanager
def _acting(company):
    if company and company not in ("CO-1", "CO-2"):
        raise PermissionError("Not permitted")
    ACTING.append(company)
    try:
        yield company
    finally:
        ACTING.pop()


def _scope(params, direction):
    company = ACTING[-1] if ACTING else "CO-1"
    month = (params or {}).get("month") or ""
    # 网页筛选条件（只认本测试用到的「字段 != 值」）
    rules = [(field, value) for field, op, value in (params or {}).get("conditions") or () if op == "!="]
    return [row for row in DOCS[direction] if row["company"] == company and str(row["doc_date"]).startswith(month)
            and all(row.get(field) != value for field, value in rules)]


def _records(direction):
    def query(params=None):
        STATE["queries"].append((direction, dict(params or {})))
        return ([["scope", "=", (ACTING[-1] if ACTING else "CO-1"), (params or {}).get("month"), direction,
                  (params or {}).get("conditions") or []]], None, "doc_date")

    def rows(params=None, *, start=0, limit=20, **kwargs):
        STATE["queries"].append((direction, dict(params or {})))
        text = str((params or {}).get("q") or "").casefold()
        found = [dict(row) for row in _scope(params, direction)
                 if not text or text in f"{row.get('doc_no')} {row.get('supplier_name') or row.get('buyer_name')}".casefold()]
        return found[start:start + limit] if limit else found[start:]
    return _module(f"pearnly_erp_portal.{'sales' if direction == 'sales' else 'purchasing'}.records", query=query, rows=rows,
                   FIELDS=("name", "status"), DOCTYPE="ERP Sales Document")


def _get_list(doctype, filters=None, **kwargs):
    direction = "sales" if doctype == "ERP Sales Document" else "purchase"
    scope = next(item for item in filters if item[0] == "scope")
    names = next(item[2] for item in filters if item[0] == "name")
    return [dict(row) for row in _scope({"month": scope[3], "conditions": scope[5]}, direction) if row["name"] in names]


frappe.get_list = _get_list
_module("pearnly_erp_portal.masters.company", acting=_acting)
_records("purchase")
_records("sales")
_module("pearnly_erp_portal.sales.contracts", SOURCES=("pos", "ocr", "manual"), SOURCE_POS="pos")


def _deleter(direction):
    def delete_drafts(names):
        STATE["deleted"].append((direction, ACTING[-1], sorted(names)))
        return {"deleted": len(names)}
    return delete_drafts


_module("pearnly_erp_portal.purchasing.entry", delete_drafts=_deleter("purchase"))
_module("pearnly_erp_portal.sales.entry", delete_drafts=_deleter("sales"))
_module("pearnly_erp_portal.purchasing.output",
        workbook=lambda params=None: STATE["exported"].append(("purchase", ACTING[-1], dict(params))) or b"xlsx")
_module("pearnly_erp_portal.sales.output",
        workbook=lambda params=None: STATE["exported"].append(("sales", ACTING[-1], dict(params))) or b"xlsx")
_module("pearnly_core.assistant.files",
        add_to_conversation=lambda name, content, mime, origin="tool": STATE["files"].append(name) or f"/mnt/session/outputs/{name}",
        resolve=lambda path: "F1")

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
        STATE.update(write=True, asked=[], started=[], rollbacks=[], required=[], queries=[], deleted=[], exported=[], files=[])
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
        self.assertEqual(registry.offered()["chat"], ["pending", "documents"])
        self.assertEqual(registry.offered()["operator"], ["documents"])
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


class Documents(Base):
    """单据维护（总表第 14 行）：对话公司 + 对话月份里一个方向的全部单据；处理交单据引导、导出整月列表、删除只删草稿。"""

    def opened(self, action, direction):
        name = run.start(SESSION, "documents")["run"]
        found = run.answer(name, {"action": action})
        found = run.answer(name, {"direction": direction})
        return name, found

    def test_list_is_this_company_this_month_this_direction_searched_on_the_server(self):
        options = guide.document_options({"company": "CO-1", "period": "2026-11", "answers": {"direction": "purchase"}})["options"]
        self.assertEqual([row["code"] for row in options], ["P:PD-1", "P:PD-4"])   # 10 月的、别家公司的不列
        self.assertEqual(options[0]["name"], "IV1 · Seller A")
        found = guide.document_options({"company": "CO-1", "period": "2026-11", "answers": {"direction": "purchase"}}, txt="seller d")
        self.assertEqual([row["code"] for row in found["options"]], ["P:PD-4"])
        self.assertEqual(STATE["queries"][-1], ("purchase", {"month": "2026-11", "q": "seller d"}))
        sales = guide.document_options({"company": "CO-1", "period": "2026-11", "answers": {"direction": "sales"}})
        self.assertEqual(STATE["queries"][-1][1]["conditions"], [["source", "!=", "pos"]])   # 收银小票不是 S: 单，其余来源都列
        self.assertEqual([row["name"] for row in sales["options"]], ["SI-2 · Buyer B"])   # CC 收单列、收银小票不列
        page = guide.document_options({"company": "CO-1", "period": "2026-11", "answers": {"direction": "purchase"}}, page_length=1)
        self.assertTrue(page["more"])

    def test_chosen_codes_outside_the_company_or_month_are_not_returned(self):
        ctx = {"company": "CO-1", "period": "2026-11", "answers": {"direction": "purchase"}}
        self.assertEqual([row["code"] for row in guide.document_options(ctx, codes=["P:PD-5", "P:PD-6", "S:SD-2", "P:PD-1"])["options"]],
                         ["P:PD-1"])

    def test_another_users_company_is_refused(self):
        with self.assertRaises(PermissionError):
            guide.document_options({"company": "CO-9", "period": "2026-11", "answers": {"direction": "purchase"}})

    def test_handle_hands_the_document_to_its_documents_guide(self):
        name, found = self.opened("handle", "sales")
        found = run.answer(name, {"item": "S:SD-2"})
        self.assertEqual(found["review"]["tables"][0]["rows"][0]["name"], "SI-2 · Buyer B")
        done = run.confirm(name, found["version"])
        self.assertEqual(STATE["started"], [(SESSION, "sales", ["S:SD-2"], "asking")])
        self.assertEqual(done["result"]["docnum"], "SI-2 · Buyer B")

    def test_export_is_the_whole_month_list_of_the_web_export_into_the_conversation(self):
        name, found = self.opened("export", "purchase")
        self.assertEqual(found["review"]["tables"], [])
        self.assertEqual(found["consent"], "confirm")
        done = run.confirm(name, found["version"])
        self.assertEqual(STATE["exported"], [("purchase", "CO-1", {"month": "2026-11"})])
        self.assertEqual(STATE["files"], ["purchase-2026-11.xlsx"])
        self.assertEqual(done["result"]["status"], "ready")
        self.assertEqual(done["result"]["files"][0]["file"], "F1")

    def test_delete_asks_consent_and_deletes_only_drafts_like_the_web(self):
        name, found = self.opened("delete", "purchase")
        found = run.answer(name, {"items": ["P:PD-1", "P:PD-4"]})
        self.assertTrue(found["review"]["tables"][0]["rows"])
        self.assertEqual(found["consent"], "allow")    # 删除走允许卡（只有「允许」）
        done = run.confirm(name, found["version"])
        self.assertEqual(STATE["deleted"], [("purchase", "CO-1", ["PD-1"])])
        self.assertEqual(done["result"]["status"], "ready")
        self.assertEqual(done["result"]["reason_text"], "intake.delete.done · Only Draft documents can be deleted")

    def test_skipped_documents_say_why_each_was_skipped(self):
        name, found = self.opened("delete", "purchase")
        found = run.answer(name, {"items": ["P:PD-1", "P:PD-4"]})
        DOCS["purchase"][0]["doc_date"] = "2026-10-01"     # 确定前那张被改到别的月份：查不到
        self.addCleanup(DOCS["purchase"][0].__setitem__, "doc_date", "2026-11-02")
        done = run.confirm(name, found["version"])
        self.assertEqual(STATE["deleted"], [])
        self.assertEqual(done["result"]["reason_text"], "Only Draft documents can be deleted · Not found")

    def test_nothing_deleted_when_none_is_a_draft(self):
        name, found = self.opened("delete", "purchase")
        found = run.answer(name, {"items": ["P:PD-4"]})
        done = run.confirm(name, found["version"])
        self.assertEqual(STATE["deleted"], [])
        self.assertEqual(done["result"]["status"], "held")
        self.assertEqual(done["result"]["reason_text"], "Only Draft documents can be deleted")


if __name__ == "__main__":
    unittest.main()
