"""Cowork 文件夹定时任务的引导式（总表 CW-D-FT，`assistant.guided.folder_task`）：用共用引擎 `assistant.guide.run` 真实现走一遍。
替身 frappe、对话账套（`assistant.ledger`、`write_scope`）、任务服务的表单选项与任务列表（`folder_intake.rules`）与 CC 工具；
提醒时刻、日程的口径用真的 `folder_intake.contracts`。

断言的是应有状态：
- 菜单：有录入功能的人才见「定时任务」；立即运行、删除画允许卡，其余是确定；
- 新建：任务一步自动定成「新建」、账套 = 本对话账套（不问）；电脑 → 根目录 → 文件夹（可不选）→ 去向 → 默认处理按去向出题
  （Express 的服务采购问现赊、抵扣、科目、部门，存货销售问科目、部门、存货）→ 日程 → 提醒 → 名称；确定交 CC 工具同一个保存函数，
  入参是任务表单（方向与入账类型分开、默认值只带答了的、提醒时刻照子题）；
- MR.ERP 账套：去向是现赊、没有默认处理题；
- 科目表还在读：科目题等（wait）；销项只列收入科目；
- 修改：各步推荐任务现值（点了才算）、存时带任务名；工具拒的退回那一步；
- 启用只列停用的；立即运行只列启用中的、注明扣费；删除一次删完；本对话公司以外的任务不在选项里；
- 当天提醒晚于运行时间退回提醒时刻那一步。

    python3 tests/test_cowork_folder_task_guide.py
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
frappe.clear_messages = lambda: None


def _throw(message, exc=ValueError):
    raise exc(message)


frappe.throw = _throw
STATE = {}
frappe.db = types.SimpleNamespace(commit=lambda: None, rollback=lambda: None)
frappe.utils = types.SimpleNamespace(get_datetime=lambda value: value, now_datetime=lambda: datetime.datetime(2026, 11, 19, 10, 5))  # 放太久失效（run._idle）：同测试里的时钟
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


BOOKS = {"L-EX": ("CONN-EX", "C:\\EX\\A"), "L-MR": ("CONN-MR", "MR01")}
TASKS = [
    {"name": "T1", "title": "Purchases", "enabled": 1, "account_label": "Book A", "company": "CO-1", "connection": "CONN-EX",
     "account_set_key": "C:\\EX\\A", "direction": "purchase", "posting_kind": "service", "payment": "",
     "reader_id": "PC1|CONN-EX", "root_id": "R1", "folder": "2026\\buy", "run_day": 5, "run_time": "09:00:00",
     "defaults": {"vat": "claim", "account": "5100"}, "remind": 1,
     "remind_times": [{"days_before": 1, "time": "08:00"}]},
    {"name": "T2", "title": "Sales", "enabled": 0, "account_label": "Book A", "company": "CO-1", "connection": "CONN-EX",
     "account_set_key": "C:\\EX\\A", "direction": "sales", "posting_kind": "stock", "payment": "",
     "reader_id": "PC1|CONN-EX", "root_id": "R1", "folder": "", "run_day": 0, "run_time": "18:00", "defaults": {},
     "remind": 0, "remind_times": None},
    {"name": "T9", "title": "Other company", "enabled": 1, "account_label": "Book Z", "company": "CO-2", "connection": "CONN-EX",
     "account_set_key": "C:\\EX\\Z", "direction": "purchase", "posting_kind": "service", "payment": "",
     "reader_id": "PC1|CONN-EX", "root_id": "R1", "folder": "z", "run_day": 1, "run_time": "09:00", "defaults": {},
     "remind": 0, "remind_times": None},
]
ACCOUNTS = [{"code": "4100", "name": "Sales income", "income": True}, {"code": "5100", "name": "Office expense"},
            {"code": "5200", "name": "Travel"}]


def _options(connection, account_set_key=None):
    STATE["options_for"].append((connection, account_set_key))
    express = connection == "CONN-EX"
    return {"readers": [{"reader": f"PC1|{connection}", "label": "Office PC", "fresh": True,
                         "roots": [{"id": "R1", "label": "S:\\Docs", "folders": ["2026\\buy", "2026\\sell"]}]},
                        {"reader": f"PC2|{connection}", "label": "Laptop", "fresh": False, "roots": []}],
            "accounts": (None if STATE["reading"] else ACCOUNTS) if express else None,
            "departments": [{"code": "D1", "name": "Head office"}] if express else None}


def _tool_tasks(payload):
    company = STATE["company"]
    return json.dumps({"tasks": [{key: task[key] for key in ("name", "title", "enabled", "account_label")}
                                 for task in TASKS if task["company"] == company]})


def _record(name, answer=None):
    def call(payload):
        STATE["calls"].append((name, payload, dict(frappe.local.pearnly_assistant or {})))
        found = (STATE.get("answers") or {}).get(name, answer)
        return None if found is None else json.dumps(found)
    return call


_module("pearnly_cowork.access")
_module("pearnly_cowork.access.scope", require=_require)
_module("pearnly_cowork.folder_intake.rules", options=_options, listing=lambda: {"rules": [dict(task) for task in TASKS]})
_module("pearnly_cowork.companies")
_module("pearnly_cowork.companies.provider", ledger_id=lambda connection, key: next(
    (code for code, place in BOOKS.items() if place == (connection, key)), f"{connection}:{key}"))
_module("pearnly_cowork.connections")
_module("pearnly_cowork.connections.service", adapter_of=lambda connection: {"CONN-EX": "express", "CONN-MR": "mrerp"}[connection])
_module("pearnly_cowork.assistant.write_scope",
        pinned=lambda: {"id": STATE["book"], "key": BOOKS[STATE["book"]][1], "connection": BOOKS[STATE["book"]][0]}
        if STATE["book"] else None,
        place=lambda pin: (pin["connection"], pin["key"]))
_module("pearnly_core.assistant.ledger",
        view=lambda session: {"current": STATE["book"],
                              "options": [{"id": "L-EX", "label": "Book A"}, {"id": "L-MR", "label": "MR Book"}]},
        choose=lambda session, value, by="page": STATE.update(book=value) or {"status": "set"})
_module("pearnly_cowork.assistant.tools",
        folder_tasks=_tool_tasks,
        folder_task_refusal=_record("folder_task_refusal"),
        save_folder_task=_record("save_folder_task", {"status": "saved", "task": "T7", "title": "Bills"}),
        set_folder_task_enabled=_record("set_folder_task_enabled", {"status": "saved", "task": "T2"}),
        run_folder_task=_record("run_folder_task", {"task": "T1", "title": "Purchases", "requested": True}),
        delete_folder_tasks=_record("delete_folder_tasks", {"deleted": 2}))

from pearnly_core.assistant.guide import declaration, registry, run  # noqa: E402
from pearnly_cowork.assistant.guided import folder_task as guide  # noqa: E402

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
        STATE.update(intake=True, company="CO-1", book="L-EX", reading=False, calls=[], options_for=[])
        HOOKS["pearnly_guides"] = ["pearnly_cowork.assistant.guided.folder_task.GUIDES"]
        frappe.local.pearnly_assistant = None
        self.docs, self.clock = {}, [0]
        self.now = {"session_id": SESSION, "mode": "chat", "company": "CO-1", "ledger": "L-EX", "period": "2026-11",
                    "stopped_at": None}
        run._insert, run._locked, run._save, run._open_runs, run._conversation, run._announce = (
            self.insert, self.locked, self.save, self.open_runs, lambda session_id: dict(self.now), lambda doc: None)

    def tick(self):
        self.clock[0] += 1
        return datetime.datetime(2026, 11, 19, 10, 0, self.clock[0])

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
    def asked(found):
        return [q["key"] for q in found["card"]["questions"]]

    @staticmethod
    def question(found, key):
        return next(q for q in found["card"]["questions"] if q["key"] == key)

    def walk(self, *answers):
        name = run.start(SESSION, "folder_task")["run"]
        found = run.view(self.docs[name])
        for given in answers:
            found = run.answer(name, given)
            self.assertNotIn("refused", found, given)
        return name, found

    def calls(self, name):
        return [payload for called, payload, _ctx in STATE["calls"] if called == name]


NEW_EXPRESS = ({"action": "new"}, {"reader": "PC1|CONN-EX"}, {"root": "R1"},
               {"folder": "2026\\buy", "target": "purchase:service", "run_day": "0"},
               {"payment": "credit", "vat": "claim", "account": "5200", "run_time": "09:30", "remind": "on"},
               {"times": {"day1": "1", "time1": "08:00", "day2": "0", "time2": "07:00"}, "title": "Bills"})


class Declaration(Base):
    def test_shape_paths_menu_and_allow_card(self):
        self.assertEqual(declaration.problems(guide.GUIDES[0]), [])
        for path in declaration.paths(guide.GUIDES[0]):
            self.assertTrue(callable(_get_attr(path)), path)
        self.assertEqual(registry.offered()["chat"], ["folder_task"])
        STATE["intake"] = False
        self.assertEqual(registry.offered()["chat"], [])
        STATE["intake"] = True
        name = run.start(SESSION, "folder_task")["run"]
        for action, consent in (("run", "allow"), ("delete", "allow"), ("new", "confirm"), ("update", "confirm"),
                                ("enable", "confirm")):
            self.assertEqual(run.answer(name, {"action": action})["consent"], consent, action)


class New(Base):
    def test_new_asks_the_form_in_order_and_saves_through_the_cc_tool(self):
        name = run.start(SESSION, "folder_task")["run"]
        found = run.answer(name, {"action": "new"})
        decided = {item["key"]: item["value"] for item in found["card"]["decided"]}
        self.assertEqual(decided, {"task": "__new__", "book": "L-EX"})   # 新建、本对话账套：不问
        self.assertEqual(self.asked(found)[:3], ["reader", "target", "run_day"])
        self.assertEqual(self.question(found, "reader")["options"],
                         [{"code": "PC1|CONN-EX", "name": "Office PC"},
                          {"code": "PC2|CONN-EX", "name": "Laptop", "note": "cowork.folder.agentStale"}])
        self.assertNotIn("preselected", self.question(found, "reader"))   # 对象题不预选
        self.assertEqual([row["code"] for row in self.question(found, "target")["options"]],
                         ["purchase:service", "purchase:stock", "sales:service", "sales:stock"])
        found = run.answer(name, {"reader": "PC1|CONN-EX"})
        self.assertEqual([row["code"] for row in self.question(found, "root")["options"]], ["R1"])
        found = run.answer(name, {"root": "R1", "target": "purchase:service"})
        self.assertEqual(sorted(set(self.asked(found)) & {"payment", "vat", "account", "department", "item"}),
                         ["account", "department", "payment", "vat"])
        self.assertFalse(self.question(found, "folder")["required"])
        _name, found = self.walk(*NEW_EXPRESS)
        self.assertEqual(found["status"], "reviewing")
        lines = {line["key"]: line["value"] for line in found["review"]["lines"]}
        self.assertEqual((lines["book"], lines["reader"], lines["folder"], lines["run_day"], lines["account"]),
                         ("Book A", "Office PC", "2026\\buy", "cowork.folder.lastDay", "5200 · Travel"))
        self.assertEqual(lines["times"], "cowork.folder.remind.dayBefore 08:00, cowork.folder.remind.sameDay 07:00")
        done = run.confirm(_name, found["version"])
        self.assertEqual(done["result"], {"status": "entered", "docnum": "Bills"})
        saved = self.calls("save_folder_task")
        self.assertEqual(saved, [{"title": "Bills", "connection": "CONN-EX", "account_set_key": "C:\\EX\\A",
                                  "direction": "purchase", "posting_kind": "service", "reader": "PC1|CONN-EX",
                                  "root_id": "R1", "folder": "2026\\buy", "run_day": 0, "run_time": "09:30",
                                  "remind": True, "defaults": {"payment": "credit", "vat": "claim", "account": "5200"},
                                  "remind_times": [{"days_before": 1, "time": "08:00"},
                                                   {"days_before": 0, "time": "07:00"}]}])
        self.assertEqual(self.calls("folder_task_refusal"), saved)   # 写入闸先核同一份
        self.assertEqual(STATE["calls"][-1][2]["session_id"], SESSION)   # 带着这段对话调工具
        run.confirm(_name, found["version"])
        self.assertEqual(len(self.calls("save_folder_task")), 1)   # 同一确定不存第二次

    def test_folder_can_be_left_for_the_root_itself(self):
        answers = [dict(given) for given in NEW_EXPRESS]
        answers[3].pop("folder")
        name, found = self.walk(*answers)
        self.assertEqual({line["key"]: line["value"] for line in found["review"]["lines"]}["folder"],
                         "cowork.folder.rootItself")
        run.confirm(name, found["version"])
        self.assertEqual(self.calls("save_folder_task")[0]["folder"], "")

    def test_stock_sales_asks_income_account_department_and_item(self):
        name, found = self.walk({"action": "new"}, {"reader": "PC1|CONN-EX"}, {"root": "R1", "target": "sales:stock"})
        self.assertEqual(sorted(set(self.asked(found)) & {"payment", "vat", "account", "department", "item"}),
                         ["account", "department", "item"])
        self.assertEqual([row["code"] for row in self.question(found, "account")["options"]], ["4100"])
        self.assertEqual(_codes(run.answer(name, {"account": "5100"})), {"account": "not offered"})

    def test_accounts_still_being_read_wait(self):
        STATE["reading"] = True
        _name, found = self.walk({"action": "new"}, {"reader": "PC1|CONN-EX"}, {"root": "R1", "target": "purchase:service"})
        account = self.question(found, "account")
        self.assertEqual((account["options"], account.get("wait"), account["facts"]), ([], True, ["Loading..."]))

    def test_mrerp_book_asks_cash_or_credit_and_no_default_handling(self):
        STATE["book"] = self.now["ledger"] = "L-MR"
        name, found = self.walk({"action": "new"}, {"reader": "PC1|CONN-MR"})
        self.assertEqual([row["code"] for row in self.question(found, "target")["options"]],
                         ["purchase:cash", "purchase:credit", "sales:cash", "sales:credit"])
        name, found = self.walk({"action": "new"}, {"reader": "PC1|CONN-MR"},
                                {"root": "R1", "target": "purchase:credit", "run_day": "3"},
                                {"run_time": "10:00", "remind": "off", "title": "MR"})
        self.assertEqual(found["status"], "reviewing")
        run.confirm(name, found["version"])
        saved = self.calls("save_folder_task")[-1]
        self.assertEqual((saved["payment"], "posting_kind" in saved, "defaults" in saved), ("credit", False, False))

    def test_without_a_conversation_book_the_book_is_asked_and_kept(self):
        STATE["book"] = ""
        name = run.start(SESSION, "folder_task")["run"]
        found = run.answer(name, {"action": "new"})
        self.assertEqual(self.asked(found), ["book", "remind"])
        found = run.answer(name, {"book": "L-EX"})
        self.assertEqual(STATE["book"], "L-EX")
        self.assertIn("reader", self.asked(found))

    def test_a_refusal_from_the_cc_tool_goes_back_with_its_reason(self):
        STATE["answers"] = {"folder_task_refusal": {"status": "not_saved", "reason": "Nothing was saved. MR.ERP setup."}}
        name, found = self.walk(*NEW_EXPRESS)
        back = run.confirm(name, found["version"])
        self.assertEqual(back["card"]["returned"]["book"]["text"], "Nothing was saved. MR.ERP setup.")
        self.assertEqual(self.calls("save_folder_task"), [])

    def test_a_same_day_reminder_after_the_run_time_goes_back(self):
        answers = [dict(given) for given in NEW_EXPRESS]
        answers[5] = {"times": {"day1": "0", "time1": "10:00"}, "title": "Bills"}
        name = run.start(SESSION, "folder_task")["run"]
        for given in answers[:-1]:
            run.answer(name, given)
        found = run.answer(name, answers[-1])
        self.assertEqual(_codes(found), {"times": "cowork.folder.remind_same_day"})


class Existing(Base):
    def test_update_suggests_current_values_and_saves_with_the_task_name(self):
        name = run.start(SESSION, "folder_task")["run"]
        run.answer(name, {"action": "update"})
        found = run.answer(name, {"task": "T1"})
        hints = {q["key"]: q.get("preselected") for q in found["card"]["questions"]}
        self.assertEqual((hints["reader"], hints["target"], hints["run_day"], hints["title"], hints["remind"]),
                         ("PC1|CONN-EX", "purchase:service", "5", "Purchases", "on"))
        self.assertEqual({item["key"]: item["value"] for item in found["card"]["decided"]}["book"], "L-EX")
        _name, found = self.walk({"action": "update"}, {"task": "T1"},
                                 {"reader": "PC1|CONN-EX", "target": "purchase:service", "run_day": "5", "remind": "off",
                                  "title": "Purchases 2"}, {"root": "R1", "run_time": "09:00"},
                                 {"folder": "2026\\buy", "vat": "expense"})
        run.confirm(_name, found["version"])
        saved = self.calls("save_folder_task")[-1]
        self.assertEqual((saved["name"], saved["title"], saved["defaults"], saved["remind"]),
                         ("T1", "Purchases 2", {"vat": "expense"}, False))

    def test_task_lists_follow_the_action_and_the_conversation_company(self):
        name = run.start(SESSION, "folder_task")["run"]
        for action, codes in (("enable", ["T2"]), ("disable", ["T1"]), ("run", ["T1"]), ("remind", ["T1", "T2"])):
            found = run.answer(name, {"action": action})
            self.assertEqual([row["code"] for row in self.question(found, "task")["options"]], codes, action)
        self.assertEqual(_codes(run.answer(name, {"task": "T9"})), {"task": "not offered"})

    def test_enable_and_reminder_go_through_the_switch_tool(self):
        name, found = self.walk({"action": "enable"}, {"task": "T2"})
        self.assertEqual(found["status"], "reviewing")
        run.confirm(name, found["version"])
        name, found = self.walk({"action": "remind"}, {"task": "T2"}, {"remind": "on"},
                                {"times": {"day1": "2", "time1": "09:00"}})
        run.confirm(name, found["version"])
        self.assertEqual(self.calls("set_folder_task_enabled"),
                         [{"task": "T2", "enabled": True},
                          {"task": "T2", "remind": True, "remind_times": [{"days_before": 2, "time": "09:00"}]}])

    def test_run_now_notes_the_charge_and_delete_deletes_once(self):
        name, found = self.walk({"action": "run"}, {"task": "T1"})
        self.assertEqual(found["consent"], "allow")
        # 复核行在 main 上另带 step / label（引擎补的），这里只比这一行自己的三项
        self.assertIn({"key": "charge", "label_key": "cowork.folder.runNow", "value": "cowork.folder.runNow.charge"},
                      [{k: line.get(k) for k in ("key", "label_key", "value")} for line in found["review"]["lines"]])
        done = run.confirm(name, found["version"])
        self.assertEqual((self.calls("run_folder_task"), done["result"]["reason_text"]),
                         ([{"task": "T1"}], "cowork.folder.runRequested"))
        name, found = self.walk({"action": "delete"}, {"tasks": ["T1", "T2"]})
        self.assertEqual([row["name"] for row in found["review"]["tables"][0]["rows"]], ["Purchases", "Sales"])
        done = run.confirm(name, found["version"])
        self.assertEqual((self.calls("delete_folder_tasks"), done["result"]["deleted"]), ([{"tasks": ["T1", "T2"]}], 2))

    def test_a_task_the_tool_refuses_goes_back_to_the_task(self):
        STATE["answers"] = {"run_folder_task": {"status": "refused", "reason": "not_this_company_task"}}
        name, found = self.walk({"action": "run"}, {"task": "T1"})
        back = run.confirm(name, found["version"])
        self.assertEqual(back["card"]["returned"]["task"]["text"], "not_this_company_task")


if __name__ == "__main__":
    unittest.main()
