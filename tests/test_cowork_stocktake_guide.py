"""Cowork 盘点的引导式（总表 CW-D-ST，`assistant.guided.stocktake`：声明与产品函数）：用共用引擎 `assistant.guide.run` 真实现走一遍。

不连库、不出网：替身 frappe；盘点服务（`stocktake.upload / counting / desk_api / access`）、CC 工具里放文件的三个函数、
对话文件（`assistant.files`）都是内存替身。

断言的是应有状态：
- 菜单：有盘点功能的人才见「盘点」，代录菜单上没有；第一题只问「做什么」；
- 记数：盘点单只列进行中的、按原样核；物料按本盘点的行搜索、翻页，不是本盘点的行不收；数量 0 收、负数不收；记法默认累加（进「已定」）；
  复核卡列要记的各行；确定按账面位置逐行记、同一确定再来一次不记第二次；重新盘点先作废再记；服务拒了退回盘点单那一步、一行都不留；
- 新建：只收本对话里的一份 .xlsx；确定转调 CC 工具同一个函数（带文件与名称）；工具拒了退回文件那一步；
- 结束：复核卡列已盘 / 未盘与差异表（只列有差异的）；确定先结束再把差异工作簿放进对话；
- 导出、模板：文件放进对话；删除：多选、确定一次删完。

    python3 tests/test_cowork_stocktake_guide.py
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
frappe.utils = types.SimpleNamespace(get_datetime=lambda value: value)
frappe.publish_realtime = lambda *args, **kwargs: None
sys.modules["frappe"] = frappe


def _module(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules[name] = module
    return module


_module("pearnly_core.assistant.contracts", REALTIME_EVENT="pearnly_assistant_event")
_module("pearnly_core.assistant.write_plan", _iso=lambda moment: moment.isoformat() + "Z")


def _attachment(file_id):
    STATE["sessions_seen"].append((getattr(frappe.local, "pearnly_assistant", None) or {}).get("session_id"))
    if file_id not in STATE["attachments"]:
        raise ValueError("This attachment is not in this conversation.")
    return STATE["attachments"][file_id], b"x"


_module("pearnly_core.assistant.files", attachment=_attachment, resolve=lambda path: path.split("/")[-2])

# 盘点单：T1 进行中、T2 已结束；T1 的物料行 I1–I3
TASKS = [{"id": "T1", "name": "Shelf A", "status": "active"}, {"id": "T2", "name": "Shelf B", "status": "closed"}]
ITEMS = {"I1": ("Apple", "A01", "885001"), "I2": ("Banana", "B01", ""), "I3": ("Cherry", "C01", "")}


def _records(start=0, limit=20, sort_by="created_at", sort_order="desc"):
    STATE["record_calls"] += 1
    return {"tasks": [dict(row, total=3, counted=1, differences=1) for row in STATE["tasks"][start:start + limit]],
            "total": len(STATE["tasks"])}


def _summaries(names):
    return {row["id"]: {"title": row["name"], "status": row["status"], "total": 3, "counted": 1}
            for row in STATE["tasks"] if row["id"] in names}


def _delete(names):
    STATE["deleted"].append(list(names))
    return {"deleted": len(names), "ids": list(names)}


_module("pearnly_cowork.stocktake")
_module("pearnly_cowork.stocktake.upload", records=_records, summaries=_summaries, delete=_delete)
_module("pearnly_cowork.stocktake.access", allowed=lambda user=None: STATE["stocktake"])


def _search(task_id, txt="", start=0, page_len=20):
    rows = [[key, name, " · ".join(filter(None, (code, barcode)))] for key, (name, code, barcode) in ITEMS.items()
            if task_id == "T1" and str(txt).casefold() in f"{name} {code} {barcode}".casefold()]
    return rows[start:start + page_len]


def _lookup(task_id, code=None, item_id=None):
    if task_id != "T1" or item_id not in ITEMS:
        raise LookupError("stocktake.not_found")
    name, code, barcode = ITEMS[item_id]
    return {"item": {"id": item_id, "product_name": name, "product_code": code, "barcode": barcode}, "active": True}


_module("pearnly_cowork.stocktake.counting", search=_search, lookup=_lookup)


def _desk_lookup(count, code=None, product=None):
    return {"product": {"name": product}, "rows": [{"warehouse": "WH-1", "location": f"L-{product}"}]}


def _add_entry(count, qty, item=None, warehouse=None, location=None, request_id=None, **kwargs):
    if STATE.get("refuse"):
        raise ValueError(STATE["refuse"])
    if request_id not in STATE["seen_requests"]:   # 服务按操作号重放：同一号只记一笔
        STATE["seen_requests"].add(request_id)
        STATE["entries"].append((count, item, qty, warehouse, location))
    return {"entry": "E", "item": item}


def _review(count):
    return {"items": [{"name": "I1", "product_name": "Apple", "code": "A01", "counted": 1, "difference": -2.0},
                      {"name": "I2", "product_name": "Banana", "code": "B01", "counted": 1, "difference": None},
                      {"name": "I3", "product_name": "Cherry", "code": "C01", "counted": 0, "difference": None}]}


_module("pearnly_cowork.stocktake.desk_api", api_stock_count_lookup=_desk_lookup, api_stock_count_add_entry=_add_entry,
        api_stock_count_recount=lambda count, item: STATE["recounts"].append((count, item)),
        api_stock_count_close=lambda name, review_token=None: STATE["closed"].append(name),
        api_stock_count_review=_review)


def _tool_template(_input):
    return json.dumps({"status": "ready", "file": "/mnt/session/uploads/F9/stocktake-template.xlsx"})


def _tool_create(tool_input):
    STATE["created"].append(dict(tool_input))
    return json.dumps(STATE["create_result"])


def _tool_export(tool_input):
    STATE["exported"].append(tool_input["count"])
    return json.dumps({"status": "exported", "file": "/mnt/session/uploads/F7/Shelf A-20261116.xlsx"})


_module("pearnly_cowork.assistant.tools", stocktake_template=_tool_template, stocktake_create=_tool_create,
        stocktake_export=_tool_export)

from pearnly_core.assistant.guide import registry, run  # noqa: E402
from pearnly_cowork.assistant.guided import stocktake as guide  # noqa: E402

SESSION = "S1"


class Doc(dict):
    __getattr__ = dict.get

    def __setattr__(self, key, value):
        self[key] = value


class Base(unittest.TestCase):
    def setUp(self):
        STATE.clear()
        STATE.update(tasks=[dict(row) for row in TASKS], stocktake=True, record_calls=0, deleted=[], recounts=[], closed=[],
                     entries=[], seen_requests=set(), created=[], exported=[], rollbacks=[], sessions_seen=[],
                     attachments={"F1": "count.xlsx", "F2": "photo.jpg"},
                     create_result={"id": "T3", "replayed": False, "imported": 3, "counted": 0, "problems": []})
        HOOKS["pearnly_guides"] = ["pearnly_cowork.assistant.guided.stocktake.GUIDES"]
        self.docs, self.clock = {}, [0]
        self.now = {"session_id": SESSION, "mode": "chat", "company": "CO1", "ledger": "CONN|2026", "period": "2026-11",
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
    def test_every_dotted_path_points_at_a_function(self):
        found = guide.GUIDES[0]
        self.assertEqual((found["id"], found["source"], found["modes"], found["writes"]), ("stocktake", "form", ["chat"], True))
        for step in found["steps"]:
            for key in ("options", "default", "check", "apply", "facts"):
                if key in step:
                    self.assertTrue(callable(_get_attr(step[key])), step[key])
        for key in ("allowed", "draft", "review", "confirm"):
            self.assertTrue(callable(_get_attr(found[key])), found[key])

    def test_the_menu_lists_stocktake_only_for_people_with_the_stocktake_function(self):
        self.assertEqual(registry.offered(), {"chat": ["stocktake"], "operator": []})
        STATE["stocktake"] = False
        self.assertEqual(registry.offered(), {"chat": [], "operator": []})

    def test_close_and_delete_draw_the_allow_card_and_the_rest_confirm(self):
        name = run.start(SESSION, "stocktake")["run"]
        self.assertEqual(run.view(self.docs[name])["consent"], "confirm")
        for action, consent in (("delete", "allow"), ("close", "allow"), ("count", "confirm"), ("new", "confirm")):
            self.assertEqual(run.answer(name, {"action": action})["consent"], consent, action)

    def test_the_first_card_asks_only_what_to_do(self):
        found = run.start(SESSION, "stocktake")
        self.assertEqual(list(self.questions(found)), ["action"])
        self.assertEqual([row["code"] for row in self.questions(found)["action"]["options"]],
                         ["template", "new", "count", "close", "export", "delete"])


class Count(Base):
    def test_counting_offers_only_active_counts_and_then_the_rows_with_the_method_decided(self):
        name, found = self.walk({"action": "count"})
        self.assertEqual([row["code"] for row in self.questions(found)["count"]["options"]], ["T1"])
        refused = run.answer(name, {"count": "T2"})   # 已结束的不收
        self.assertEqual(refused["refused"], {"count": "not offered"})
        found = run.answer(name, {"count": "T1"})
        self.assertEqual(list(self.questions(found)), ["rows"])
        self.assertEqual({item["key"]: item["value"] for item in found["card"]["decided"]}, {"mode": "add"})
        rows = self.questions(found)["rows"]
        self.assertEqual(([row["code"] for row in rows["options"]], rows["search"]), (["I1", "I2", "I3"], True))

    def test_item_search_pages_and_matches_names_codes_and_barcodes(self):
        ctx = {"answers": {"count": "T1"}}
        self.assertEqual([row["code"] for row in guide.item_options(ctx, txt="885")["options"]], ["I1"])
        first = guide.item_options(ctx, page_length=2)
        self.assertEqual(([row["code"] for row in first["options"]], first["more"]), (["I1", "I2"], True))
        self.assertEqual(guide.item_options(ctx, codes=["I2", "X9", " I1"])["options"], [{"code": "I2", "name": "Banana"}])

    def test_zero_is_a_count_a_negative_quantity_or_a_row_not_in_this_count_is_not(self):
        name, _found = self.walk({"action": "count"}, {"count": "T1"})
        self.assertIn("rows", run.answer(name, {"rows": [{"code": "I1", "amount": "-1"}]})["refused"])
        self.assertEqual(run.answer(name, {"rows": [{"code": "X9", "amount": "1"}]})["refused"], {"rows": "not offered"})
        found = run.answer(name, {"rows": [{"code": "I1", "amount": "0"}, {"code": "I2", "amount": "4.5"}]})
        self.assertEqual(found["status"], run.REVIEWING)
        table = found["review"]["tables"][0]
        self.assertEqual([(row["name"], row["amount"], row.get("note")) for row in table["rows"]],
                         [("Apple", "0", "A01 · 885001"), ("Banana", "4.5", "B01")])
        self.assertEqual(STATE["entries"], [])   # 答完不记，确定才记

    def test_confirming_records_each_row_at_its_book_position_once(self):
        name, found = self.walk({"action": "count"}, {"count": "T1"},
                                {"rows": [{"code": "I1", "amount": "0"}, {"code": "I2", "amount": "4.5"}]})
        done = run.confirm(name, found["version"])
        self.assertEqual((done["status"], done["result"]["recorded"]), (run.DONE, 2))
        self.assertEqual(STATE["entries"], [("T1", "I1", "0", "WH-1", "L-I1"), ("T1", "I2", "4.5", "WH-1", "L-I2")])
        self.assertEqual(STATE["recounts"], [])
        run.confirm(name, found["version"])
        self.assertEqual(len(STATE["entries"]), 2)   # 同一确定不记第二次

    def test_the_same_rows_replayed_by_the_service_are_recorded_once(self):
        ctx = {"run": "RUN1"}
        guide._record(ctx, "T1", [{"code": "I1", "amount": "1"}], "add")
        guide._record(ctx, "T1", [{"code": "I1", "amount": "1"}], "add")
        self.assertEqual(len(STATE["entries"]), 1)

    def test_recount_voids_the_earlier_counts_first(self):
        name, _found = self.walk({"action": "count"}, {"count": "T1"})
        found = run.answer(name, {"mode": "recount", "rows": [{"code": "I3", "amount": "7"}]})
        run.confirm(name, found["version"])
        self.assertEqual((STATE["recounts"], STATE["entries"]), ([("T1", "I3")], [("T1", "I3", "7", "WH-1", "L-I3")]))

    def test_a_count_the_service_refuses_goes_back_to_the_count_with_its_reason(self):
        STATE["refuse"] = "This stock count task is closed."
        name, found = self.walk({"action": "count"}, {"count": "T1"}, {"rows": [{"code": "I1", "amount": "1"}]})
        back = run.confirm(name, found["version"])
        self.assertEqual(back["outcome"], run.REFUSED)
        self.assertEqual(back["card"]["returned"]["count"]["text"], "This stock count task is closed.")
        self.assertEqual(STATE["rollbacks"], [1])   # 全有或全无


class New(Base):
    def test_only_one_xlsx_attachment_of_this_conversation_is_accepted(self):
        name, found = self.walk({"action": "new"})
        self.assertEqual(list(self.questions(found)), ["file", "title"])
        for value in (["F2"], ["F9"], ["F1", "F2"]):   # 不是 .xlsx、不在本对话、不止一份
            self.assertEqual(run.answer(name, {"file": value})["refused"], {"file": "stocktake.file_invalid"})
        self.assertEqual(STATE["sessions_seen"][-1], SESSION)

    def test_confirming_creates_through_the_cc_tool_with_the_file_and_name(self):
        name, found = self.walk({"action": "new"}, {"file": ["F1"], "title": "Shelf C"})
        self.assertEqual(found["status"], run.REVIEWING)
        done = run.confirm(name, found["version"])
        self.assertEqual(STATE["created"], [{"file": "F1", "title": "Shelf C"}])
        self.assertEqual((done["result"]["status"], done["result"]["docnum"], done["result"]["imported"]), ("entered", "T3", 3))

    def test_a_sheet_the_tool_refuses_goes_back_to_the_file(self):
        STATE["create_result"] = {"status": "not_saved", "reason": "The import columns do not match the stock count template."}
        name, found = self.walk({"action": "new"}, {"file": ["F1"]})
        back = run.confirm(name, found["version"])
        self.assertEqual(back["card"]["returned"]["file"]["text"], "The import columns do not match the stock count template.")


class Close(Base):
    def test_the_review_lists_counted_uncounted_and_only_the_rows_with_differences(self):
        _name, found = self.walk({"action": "close"}, {"count": "T1"})
        lines = {line["key"]: line["value"] for line in found["review"]["lines"]}
        self.assertEqual((lines["count"], lines["counted"], lines["uncounted"]), ("Shelf A", 1, 2))
        self.assertEqual([(row["name"], row["amount"]) for row in found["review"]["tables"][0]["rows"]], [("Apple", -2.0)])

    def test_confirming_closes_then_puts_the_variance_workbook_in_the_conversation(self):
        name, found = self.walk({"action": "close"}, {"count": "T1"})
        done = run.confirm(name, found["version"])
        self.assertEqual((STATE["closed"], STATE["exported"]), (["T1"], ["T1"]))
        self.assertEqual(done["result"]["files"], [{"file": "F7", "name": "Shelf A-20261116.xlsx"}])

    def test_a_closed_count_is_not_offered_for_closing(self):
        self.assertEqual(guide.count_options({"answers": {"action": "close"}}, codes=["T2"])["options"], [])


class Others(Base):
    def test_export_offers_every_count_and_puts_the_workbook_in_the_conversation(self):
        name, found = self.walk({"action": "export"})
        self.assertEqual([row["code"] for row in self.questions(found)["count"]["options"]], ["T1", "T2"])
        found = run.answer(name, {"count": "T2"})
        done = run.confirm(name, found["version"])
        self.assertEqual((STATE["closed"], STATE["exported"], done["result"]["status"]), ([], ["T2"], "entered"))

    def test_the_template_goes_into_the_conversation(self):
        name, found = self.walk({"action": "template"})
        done = run.confirm(name, found["version"])
        self.assertEqual(done["result"]["files"], [{"file": "F9", "name": "stocktake-template.xlsx"}])

    def test_delete_lists_the_chosen_counts_and_deletes_them_in_one_go(self):
        name, found = self.walk({"action": "delete"}, {"counts": ["T1", "T2"]})
        self.assertEqual([row["name"] for row in found["review"]["tables"][0]["rows"]], ["Shelf A", "Shelf B"])
        done = run.confirm(name, found["version"])
        self.assertEqual((STATE["deleted"], done["result"]["deleted"]), ([["T1", "T2"]], ["T1", "T2"]))

    def test_count_options_page_through_the_list_and_search_titles(self):
        STATE["tasks"] = [{"id": f"T{n}", "name": f"Shelf {n}", "status": "active"} for n in range(250)]
        found = guide.count_options({"answers": {"action": "export"}}, txt="shelf 24", page_length=50)
        self.assertEqual([row["code"] for row in found["options"]], ["T24"] + [f"T{n}" for n in range(240, 250)])
        self.assertEqual(STATE["record_calls"], 3)   # 按页取（每页 100）


if __name__ == "__main__":
    unittest.main()
