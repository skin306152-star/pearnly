"""ERP 收发存报表的引导式（总表 ERP-D-SC，`inventory.cc_guide_card` 声明 + `inventory.guide_card` 产品函数）：用共用引擎真实现走一遍。

断言的是应有状态：
- 菜单：有报表读取权的人才见「收发存报表」；
- 先问样式与起止日期（推荐本对话月份首末日、点了才算），格式默认 Excel 进「已定」；物料「从 / 到」可空；仓「从 / 到」只在分仓时问；
- 结束日期早于开始日期不收；物料选项走原生 search_widget、按本人能看的核；仓只列本公司的；
- 确定在本对话公司下调 `card_export.build` 一次，只带填了的范围，文件放进对话；业务拒绝退回日期那一步。

    python3 tests/test_erp_stock_card_guide.py
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
_module("pearnly_erp_portal.access.guard", has=lambda capability, user=None: STATE["report"] and capability == "report.read")


@contextlib.contextmanager
def _acting(company, user=None):
    STATE["acting"].append(company)
    yield company or None


_module("pearnly_erp_portal.masters.company", acting=_acting)
PRODUCTS = [{"name": f"P{n}", "product_name": f"Item {n}", "description": f"C{n}"} for n in range(30)]
_module("frappe.desk")
_module("frappe.desk.search", search_widget=lambda doctype, txt, start=0, page_length=10, as_dict=False: [
    dict(row) for row in PRODUCTS if txt.casefold() in row["product_name"].casefold()][start:start + page_length])
frappe.get_list = lambda doctype, filters=None, fields=None, limit_page_length=0: [
    types.SimpleNamespace(**row) for row in PRODUCTS if row["name"] in filters["name"][1]]
_module("pearnly_erp_portal.inventory.count", _warehouse_labels=lambda names: {name: f"{name} name" for name in names})
_module("pearnly_erp_portal.inventory.warehouses", names_of=lambda company=None, include_inactive=True: ["WH1", "WH2"])


def _build(**kwargs):
    if STATE.get("refuse"):
        raise ValueError(STATE["refuse"])
    STATE["built"].append(kwargs)
    return {"filename": "stock-card.xlsx", "content": b"x"}


_module("pearnly_erp_portal.inventory.card_export", build=_build, content_type=lambda output: "x/" + str(output),
        ARGS=("report", "format", "date_from", "date_to", "product_from", "product_to", "warehouse_from", "warehouse_to"))
_module("pearnly_core.assistant.files", add_to_conversation=lambda name, content, mime: STATE["added"].append(name) or f"/mnt/session/uploads/F1/{name}",
        resolve=lambda path: path.split("/")[-2])

from pearnly_core.assistant.guide import registry, run  # noqa: E402
from pearnly_erp_portal.inventory import guide_card  # noqa: E402

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
        STATE.update(report=True, acting=[], built=[], added=[])
        HOOKS["pearnly_guides"] = ["pearnly_erp_portal.inventory.cc_guide_card.GUIDES"]
        self.docs, self.clock = {}, [0]
        self.now = {"session_id": SESSION, "mode": "chat", "company": "CO-1", "ledger": "BOOK", "period": "2026-02",
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


class StockCard(Base):
    def test_menu_follows_report_reading(self):
        self.assertEqual(registry.offered()["chat"], ["stock_card"])
        STATE["report"] = False
        self.assertEqual(registry.offered()["chat"], [])

    def test_first_card_suggests_the_conversation_month_and_decides_excel(self):
        found = run.start(SESSION, "stock_card")
        asked = self.questions(found)
        self.assertEqual(list(asked), ["report", "date_from", "date_to", "product_from", "product_to"])
        self.assertEqual(asked["date_from"]["preselected"], "2026-02-01")
        self.assertEqual({item["key"]: item["value"] for item in found["card"]["decided"]}, {"format": "xlsx"})
        self.assertEqual(asked["date_to"]["preselected"], "2026-02-28")   # 2 月末
        self.assertNotIn("warehouse_from", asked)

    def test_warehouse_range_only_by_warehouse_and_only_this_company(self):
        name = run.start(SESSION, "stock_card")["run"]
        found = run.answer(name, {"report": "by_warehouse"})
        self.assertEqual([row["code"] for row in self.questions(found)["warehouse_from"]["options"]], ["WH1", "WH2"])
        self.assertEqual(_codes(run.answer(name, {"warehouse_from": "WH9"})), {"warehouse_from": "not offered"})

    def test_an_end_before_the_start_is_refused(self):
        name = run.start(SESSION, "stock_card")["run"]
        found = run.answer(name, {"report": "all", "date_from": "2026-02-10", "date_to": "2026-02-01"})
        self.assertEqual(_codes(found), {"date_to": "trade.export.error.dateOrder"})

    def test_products_search_natively_and_are_checked_by_what_the_user_can_see(self):
        found = guide_card.product_options({"company": "CO-1"}, txt="item 2", page_length=5)
        self.assertEqual(([row["code"] for row in found["options"]], found["more"]), (["P2", "P20", "P21", "P22", "P23"], True))
        self.assertEqual(guide_card.product_options({"company": "CO-1"}, codes=["P3", "X"])["options"], [{"code": "P3", "name": "Item 3"}])

    def test_confirming_builds_once_with_only_the_ranges_given_and_files_it(self):
        name = run.start(SESSION, "stock_card")["run"]
        found = run.answer(name, {"report": "summary", "date_from": "2026-02-01", "date_to": "2026-02-28", "product_from": "P3"})
        self.assertTrue(found["card"]["ready"])
        done = run.confirm(name, found["version"])
        run.confirm(name, found["version"])
        self.assertEqual(STATE["built"], [{"report": "summary", "format": "xlsx", "date_from": "2026-02-01",
                                           "date_to": "2026-02-28", "product_from": "P3"}])
        self.assertEqual(done["result"]["files"], [{"file": "F1", "name": "stock-card.xlsx"}])
        self.assertIn("CO-1", STATE["acting"])

    def test_a_refused_export_goes_back_to_the_dates(self):
        STATE["refuse"] = "trade.export.error.productOrder"
        name = run.start(SESSION, "stock_card")["run"]
        found = run.answer(name, {"report": "all", "date_from": "2026-02-01", "date_to": "2026-02-28"})
        back = run.confirm(name, found["version"])
        self.assertEqual(back["card"]["returned"]["date_from"]["text"], "trade.export.error.productOrder")


if __name__ == "__main__":
    unittest.main()
