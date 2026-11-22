"""ERP 盘点的引导式（总表 ERP-D-ST，`inventory.cc_guide` 声明 + `inventory.guide` 产品函数）：用共用引擎 `assistant.guide.run` 真实现走一遍。

不连库、不出网：替身 frappe；盘点服务（`inventory.count`）、仓（`inventory.warehouses`）、当前公司（`masters.company.acting`）、
权限（`access.guard`）都是内存替身。

断言的是应有状态：
- 菜单：有盘点功能的人才见「盘点」，代录菜单上没有；声明与 Cowork 的盘点引导同菜单字、图标、顺序；
- 每一步都在本对话公司下执行（`acting`）；
- 新建：仓只列这家公司启用的仓，本人上次选的预先定下（进「已定」）、可改；名称每次填；确定 = `create_count`；
- 记数：盘点单只列进行中的；物料按录入框同一查询搜索、按物料号核；数量 0 收、负数不收；确定逐行 `add_entry`、同一运行同一行一个操作号；
  重新盘点先 `recount_product`；服务拒了退回盘点单那一步并回滚；
- 结束：复核卡列已盘 / 未盘与差异表（只列有差异的）；确定先取审阅令牌再 `close_count`；
- 按盘点结果调整物料：只列已结束的，确定 = `adjustment_draft`（草稿号）；删除：只列进行中的，确定 = `delete_count`。

    python3 tests/test_erp_stocktake_guide.py
"""
import contextlib
import datetime
import importlib
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for app in ("pearnly_core", "pearnly_erp_portal", "pearnly_cowork"):
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
_module("pearnly_erp_portal.access.guard", has=lambda capability, user=None: STATE["count_write"] and capability == "count.write")


@contextlib.contextmanager
def _acting(company, user=None):
    STATE["acting"].append(company)
    yield company or None


_module("pearnly_erp_portal.masters.company", acting=_acting)

COUNTS = [{"name": "SC1", "title": "Shelf A", "status": "Active", "warehouse_name": "Main"},
          {"name": "SC2", "title": "Shelf B", "status": "Closed", "warehouse_name": "Main"},
          {"name": "SC3", "title": "Shelf C", "status": "Posted", "warehouse_name": "Back"}]
PRODUCTS = {"P1": ("Apple", "A01"), "P2": ("Banana", "B01")}


def _list_counts(*, status=None, limit=50, start=0, sort_by=None, sort_order=None):
    rows = [dict(row) for row in STATE["counts"] if not status or row["status"] == status]
    return rows[start:start + limit]


def _get_count(name):
    row = next((row for row in STATE["counts"] if row["name"] == name), None)
    if not row:
        raise LookupError("Stock count not found")
    return {**row, "item_total": 3, "counted_total": 2}


def _lookup(*, count, code=None, product=None):
    if product not in PRODUCTS:
        raise ValueError("Product not found")
    name, code = PRODUCTS[product]
    return {"product": {"name": product, "product_name": name, "code": code, "barcode_shown": ""}}


def _search(*, count, txt="", start=0, page_len=20):
    rows = [[key, name, code] for key, (name, code) in PRODUCTS.items() if str(txt).casefold() in f"{name} {code}".casefold()]
    return rows[start:start + page_len]


def _add_entry(*, count, qty, product=None, warehouse=None, location=None, request_id=None, **kwargs):
    if STATE.get("refuse"):
        raise ValueError(STATE["refuse"])
    STATE["entries"].append((count, product, qty, warehouse, location, request_id))
    return {"entry": "E"}


def _review(name, **kwargs):
    STATE["reviews"] += 1
    return {"review_token": f"tok{STATE['reviews']}",
            "items": [{"name": "R1", "product_name": "Apple", "code": "A01", "counted": 1, "difference": 3.0},
                      {"name": "R2", "product_name": "Banana", "code": "B01", "counted": 1, "difference": 0.0},
                      {"name": "R3", "product_name": "Cherry", "code": "C01", "counted": 0, "difference": None}]}


def _create(*, title, warehouse=None, notes=None, snapshot=True):
    STATE["created"].append((title, warehouse))
    return {"name": "SC9", "rows": 12}


_module("pearnly_erp_portal.inventory.count", list_counts=_list_counts, get_count=_get_count, lookup_count=_lookup,
        search_count_products=_search, add_entry=_add_entry, review_count=_review, create_count=_create,
        recount_product=lambda *, count, product: STATE["recounts"].append((count, product)),
        close_count=lambda name, review_token=None: STATE["closed"].append((name, review_token)) or {"status": "Closed"},
        create_product_here=lambda *, count, code, product_name, **extra: STATE["new_products"].append(
            (count, code, product_name, extra)) or ({"name": "P1", "created": False} if code == "A01" else {"name": "P7", "created": True}),
        adjustment_draft=lambda name: STATE["adjusted"].append(name) or {"name": "SE-1", "doc_no": "ADJ-0001"},
        delete_count=lambda name: STATE["deleted"].append(name) or {"ok": True},
        _warehouse_labels=lambda names: {name: f"{name} name" for name in names})
_module("pearnly_erp_portal.inventory.warehouses", names_of=lambda company=None, include_inactive=True: ["WH1", "WH2"],
        prefill=lambda kind, company=None, context=None: {"warehouse": STATE["last_warehouse"], "reason": "last"})

from pearnly_core.assistant.guide import registry, run  # noqa: E402
from pearnly_erp_portal.inventory import cc_guide, guide  # noqa: E402

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
        STATE.update(counts=[dict(row) for row in COUNTS], count_write=True, acting=[], entries=[], recounts=[], closed=[],
                     adjusted=[], deleted=[], created=[], new_products=[], rollbacks=[], reviews=0, last_warehouse="WH2")
        HOOKS["pearnly_guides"] = ["pearnly_erp_portal.inventory.cc_guide.GUIDES"]
        self.docs, self.clock = {}, [0]
        self.now = {"session_id": SESSION, "mode": "chat", "company": "CO-1", "ledger": "BOOK", "period": "2026-11",
                    "stopped_at": None}
        run._insert, run._locked, run._save, run._open_runs, run._conversation, run._announce = (
            self.insert, self.locked, self.save, self.open_runs, lambda session_id: dict(self.now), lambda doc: None)

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
        name = run.start(SESSION, "stocktake")["run"]
        found = None
        for given in answers:
            found = run.answer(name, given)
        return name, found


class Declaration(Base):
    def test_every_dotted_path_points_at_a_function_and_the_menu_matches_cowork(self):
        found = cc_guide.GUIDES[0]
        for step in found["steps"]:
            for key in ("options", "default", "check", "apply", "facts"):
                if key in step:
                    self.assertTrue(callable(_get_attr(step[key])), step[key])
        for key in ("allowed", "draft", "review", "confirm"):
            self.assertTrue(callable(_get_attr(found[key])), found[key])
        source = (ROOT / "apps/pearnly_cowork/pearnly_cowork/assistant/guided/stocktake.py").read_text(encoding="utf-8")
        for key in ("label_key", "icon", "order", "opening_key"):
            self.assertIn(f'"{key}": {found[key]!r}'.replace("'", '"'), source)

    def test_close_and_delete_draw_the_allow_card_and_the_rest_confirm(self):
        name = run.start(SESSION, "stocktake")["run"]
        for action, consent in (("delete", "allow"), ("close", "allow"), ("adjust", "confirm"), ("count", "confirm")):
            self.assertEqual(run.answer(name, {"action": action})["consent"], consent, action)

    def test_the_menu_lists_stocktake_only_for_people_with_the_stocktake_function(self):
        self.assertEqual(registry.offered(), {"chat": ["stocktake"], "operator": []})
        STATE["count_write"] = False
        self.assertEqual(registry.offered(), {"chat": [], "operator": []})


class New(Base):
    def test_the_last_warehouse_is_decided_and_the_name_asked(self):
        name, found = self.walk({"action": "new"})
        self.assertEqual(list(self.questions(found)), ["title"])
        self.assertEqual({item["key"]: item["value"] for item in found["card"]["decided"]}, {"warehouse": "WH2"})
        self.assertEqual(set(STATE["acting"]), {"CO-1"})   # 都在本对话公司下
        refused = run.answer(name, {"warehouse": "WH9"})
        self.assertEqual(_codes(refused), {"warehouse": "not offered"})

    def test_without_a_remembered_warehouse_it_is_asked(self):
        STATE["last_warehouse"] = ""
        _name, found = self.walk({"action": "new"})
        self.assertEqual([row["code"] for row in self.questions(found)["warehouse"]["options"]], ["WH1", "WH2"])

    def test_confirming_creates_the_count_of_that_warehouse(self):
        name, found = self.walk({"action": "new"}, {"title": "Year end"})
        lines = {line["key"]: line["value"] for line in found["review"]["lines"]}
        self.assertEqual((lines["warehouse"], lines["title"]), ("WH2 name", "Year end"))
        done = run.confirm(name, found["version"])
        self.assertEqual((STATE["created"], done["result"]["docnum"]), ([("Year end", "WH2")], "SC9"))


class Count(Base):
    def test_only_active_counts_are_offered_and_items_come_from_the_count_search(self):
        name, found = self.walk({"action": "count"})
        self.assertEqual([row["code"] for row in self.questions(found)["count"]["options"]], ["SC1"])
        self.assertEqual(_codes(run.answer(name, {"count": "SC2"})), {"count": "not offered"})
        found = run.answer(name, {"count": "SC1"})
        self.assertEqual([row["code"] for row in self.questions(found)["rows"]["options"]], ["P1", "P2"])
        self.assertEqual([row["code"] for row in guide.item_options({"answers": {"count": "SC1"}}, txt="b01")["options"]],
                         ["P2"])

    def test_zero_counts_negative_and_unknown_products_do_not(self):
        name, _found = self.walk({"action": "count"}, {"count": "SC1"})
        self.assertIn("rows", _codes(run.answer(name, {"rows": [{"code": "P1", "amount": "-2"}]})))
        self.assertEqual(_codes(run.answer(name, {"rows": [{"code": "P9", "amount": "1"}]})), {"rows": "not offered"})
        found = run.answer(name, {"rows": [{"code": "P1", "amount": "0"}, {"code": "P2", "amount": "5"}]})
        self.assertEqual([(row["name"], row["amount"]) for row in found["review"]["tables"][0]["rows"]],
                         [("Apple", "0"), ("Banana", "5")])

    def test_confirming_records_each_row_once_with_a_fixed_operation_id(self):
        name, found = self.walk({"action": "count"}, {"count": "SC1"}, {"rows": [{"code": "P1", "amount": "0"}]})
        done = run.confirm(name, found["version"])
        run.confirm(name, found["version"])
        self.assertEqual(done["result"]["recorded"], 1)
        self.assertEqual(len(STATE["entries"]), 1)
        count, product, qty, warehouse, location, request = STATE["entries"][0]
        self.assertEqual((count, product, qty, warehouse, location), ("SC1", "P1", "0", None, None))
        self.assertTrue(request.startswith("cc:") and len(request) == 35)
        guide._record({"run": name}, "SC1", [{"code": "P1", "amount": "0"}], "add")
        self.assertEqual(STATE["entries"][-1][5], request)   # 同一运行同一行同一个操作号（服务去重）

    def test_recount_voids_first(self):
        name, _found = self.walk({"action": "count"}, {"count": "SC1"})
        found = run.answer(name, {"mode": "recount", "rows": [{"code": "P2", "amount": "7"}]})
        run.confirm(name, found["version"])
        self.assertEqual(STATE["recounts"], [("SC1", "P2")])

    def test_a_count_the_service_refuses_goes_back_to_the_count(self):
        STATE["refuse"] = "This stock count is closed"
        name, found = self.walk({"action": "count"}, {"count": "SC1"}, {"rows": [{"code": "P1", "amount": "1"}]})
        back = run.confirm(name, found["version"])
        self.assertEqual(back["card"]["returned"]["count"]["text"], "This stock count is closed")
        self.assertEqual(STATE["rollbacks"], [1])


class NewProduct(Base):
    def test_an_unknown_code_creates_the_product_with_its_fields_and_records_the_count(self):
        name, found = self.walk({"action": "new_product"}, {"count": "SC1"})
        self.assertEqual(list(self.questions(found)),
                         ["code", "product_name", "unit", "unit_price", "default_cost", "category", "qty"])
        found = run.answer(name, {"code": "8850001", "product_name": "Green tea", "unit": "box", "unit_price": "25",
                                  "qty": "0"})
        lines = {line["key"]: line["value"] for line in found["review"]["lines"]}
        self.assertEqual((lines["code"], lines["product_name"], lines["qty"]), ("8850001", "Green tea", "0"))
        done = run.confirm(name, found["version"])
        self.assertEqual(STATE["new_products"], [("SC1", "8850001", "Green tea", {"unit": "box", "unit_price": "25"})])
        self.assertEqual(STATE["entries"][0][:3], ("SC1", "P7", "0"))
        self.assertEqual((done["result"]["product"], done["result"]["created"]), ("P7", True))

    def test_a_code_that_turns_out_to_exist_is_counted_on_that_product(self):
        name, found = self.walk({"action": "new_product"}, {"count": "SC1"},
                                {"code": "A01", "product_name": "Apple", "qty": "3"})
        done = run.confirm(name, found["version"])
        self.assertEqual((STATE["entries"][0][1], done["result"]["created"]), ("P1", False))

    def test_a_negative_quantity_is_refused_and_only_active_counts_are_offered(self):
        name, found = self.walk({"action": "new_product"})
        self.assertEqual([row["code"] for row in self.questions(found)["count"]["options"]], ["SC1"])
        run.answer(name, {"count": "SC1"})
        self.assertEqual(_codes(run.answer(name, {"code": "X1", "product_name": "X", "qty": "-1"})), {"qty": "Enter a quantity"})


class CloseAdjustDelete(Base):
    def test_close_reviews_the_differences_and_closes_with_a_fresh_review_token(self):
        name, found = self.walk({"action": "close"}, {"count": "SC1"})
        lines = {line["key"]: line["value"] for line in found["review"]["lines"]}
        self.assertEqual((lines["counted"], lines["uncounted"]), (2, 1))
        self.assertEqual([(row["name"], row["amount"]) for row in found["review"]["tables"][0]["rows"]], [("Apple", 3.0)])
        done = run.confirm(name, found["version"])
        self.assertEqual(STATE["closed"], [("SC1", f"tok{STATE['reviews']}")])
        self.assertEqual(done["result"]["count_status"], "Closed")

    def test_adjust_offers_closed_counts_and_makes_the_draft(self):
        name, found = self.walk({"action": "adjust"})
        self.assertEqual([row["code"] for row in self.questions(found)["count"]["options"]], ["SC2"])
        found = run.answer(name, {"count": "SC2"})
        done = run.confirm(name, found["version"])
        self.assertEqual((STATE["adjusted"], done["result"]["docnum"]), (["SC2"], "ADJ-0001"))

    def test_delete_offers_active_counts_only(self):
        name, found = self.walk({"action": "delete"})
        self.assertEqual([row["code"] for row in self.questions(found)["count"]["options"]], ["SC1"])
        found = run.answer(name, {"count": "SC1"})
        run.confirm(name, found["version"])
        self.assertEqual(STATE["deleted"], ["SC1"])


if __name__ == "__main__":
    unittest.main()
