"""CC 写账由用户一句话下令开始（用户 11-10；主会话 11-10 批复，A 线 11-11）：写入计划 `pearnly_core.assistant.write_plan`。

三道保险都在服务端：复述之后用户确实又说了一句才开始（用户消息序号）；只写看过之后没再改的票（写账判定摘要）；停过就不开始、
直写已开始的计划停之后不再推。代录按服务端完整待录清单定这次写哪几张（51 张不漏），开始时整份交给任务；直写只推已开始计划里的单。
写账前置判定的契约用真函数（纯函数），产品、对话、代录接口用替身。
"""
import importlib.util
import json
import sys
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "apps/pearnly_core/pearnly_core"
T0 = datetime(2026, 11, 11, 10, 0, 0)


class _Throw(Exception):
    pass


class _Db:
    def __init__(self):
        self.rows = {}
        self.sessions = {}

    def get_value(self, doctype, filters, fields=None, as_dict=False):
        if doctype == "Pearnly Assistant Session":
            row = self.sessions.get((filters or {}).get("session_id"))
            if row and row["user"] == filters.get("user"):
                return types.SimpleNamespace(**row) if as_dict else row
            return None
        raise AssertionError(doctype)

    def commit(self):
        self.committed = {name: dict(row) for name, row in self.rows.items()}

    def exists(self, doctype, name):
        return name in self.rows

    def set_value(self, doctype, name, field, value, update_modified=True):
        self.rows[name][field] = value
        self.rows[name]["modified"] = FAKE.now()

    def delete(self, doctype, filters):
        for name in [n for n, row in self.rows.items() if row["session_id"] == filters["session_id"]]:
            del self.rows[name]


class _Doc(types.SimpleNamespace):
    def insert(self, ignore_permissions=False):
        FAKE.count += 1
        self.name = f"WP{FAKE.count}"
        self.creation = self.modified = FAKE.now()
        FAKE.db.rows[self.name] = dict(vars(self))
        return self

    def db_set(self, values, update_modified=True):
        values = dict(values, modified=FAKE.now())
        vars(self).update(values)
        FAKE.db.rows[self.name].update(values)


class _Fake:
    def __init__(self):
        self.db = _Db()
        self.count = 0
        self.clock = T0
        self.published = []

    def now(self):
        self.clock += timedelta(seconds=1)
        return self.clock


FAKE = _Fake()


def _frappe():
    frappe = types.ModuleType("frappe")
    frappe._ = lambda text: text
    frappe.DoesNotExistError = type("DoesNotExistError", (Exception,), {})
    frappe.PermissionError = type("PermissionError", (Exception,), {})

    def throw(message, exc=None):
        raise (exc or _Throw)(message)

    frappe.throw = throw
    frappe.session = types.SimpleNamespace(user="u@example.com")
    frappe.local = types.SimpleNamespace(pearnly_assistant={"session_id": "sesn_1"})
    frappe.db = FAKE.db

    def get_doc(value, name=None):
        if isinstance(value, dict):
            return _Doc(**value)
        return _Doc(**FAKE.db.rows[name])

    def get_all(doctype, filters=None, fields=None, pluck=None, order_by=None, limit_page_length=None):
        rows = [row for row in FAKE.db.rows.values() if all(row.get(k) == v for k, v in (filters or {}).items()
                                                            if not isinstance(v, list))]
        rows = sorted(rows, key=lambda row: row.get("started_at") or row["modified"], reverse="desc" in (order_by or ""))
        if pluck:
            return [row[pluck] for row in rows]
        return [types.SimpleNamespace(**row) for row in rows]

    frappe.get_doc, frappe.get_all = get_doc, get_all
    frappe.publish_realtime = lambda event, payload, user=None, after_commit=False: FAKE.published.append(payload["event"])
    frappe.log_error = lambda title=None: None
    frappe.utils = types.SimpleNamespace(get_datetime=lambda value: value, now_datetime=FAKE.now,
                                         get_system_timezone=lambda: "UTC")
    frappe.model = types.SimpleNamespace(document=types.SimpleNamespace(Document=object))
    return frappe


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class WritePlanTests(unittest.TestCase):
    def setUp(self):
        global FAKE
        FAKE = _Fake()
        self.turn, self.stopped, self.handed = [0], [None], []
        self.docs = {f"D{n:03d}": {"state": "check", "items": []} for n in range(1, 61)}
        sessions = types.ModuleType("pearnly_core.assistant.sessions")
        sessions.user_turn = lambda sid: self.turn[0]
        sessions.last_stop = lambda sid: self.stopped[0]
        products = types.ModuleType("pearnly_core.write_precheck.products")
        products.allowed = lambda user, company: company == "CO-1"
        products.check = lambda company, ids: {"company_title": "Acme", "documents": [
            {"doc_id": doc_id, "target": {"connection": "EXP1", "account_set": "K1", "account_set_title": "Acme 2026"},
             **self.docs[doc_id]} for doc_id in ids if doc_id in self.docs]}
        operator_api = types.ModuleType("pearnly_core.express_operator.api")
        # 完整待录清单（不看页面加载了几页）：D007 移到不入账，D010 已写进 Express；D061 起不在这家这个月
        operator_api.waiting = lambda company, period, again: (
            [d for d in self.docs if d not in ("D007", "D010") or d in again], {},
            [{"doc_id": "D007", "reason": "moved_to_not_entered"}]
            + ([] if "D010" in again else [{"doc_id": "D010", "reason": "already_entered"}]))
        def hand_in(company, task, ids, again=()):
            # 产品交单途中会回滚当前事务：交单前计划的「已开始」必须已经提交（11-11 真模型场景）
            self.committed_at_hand_in = [row["status"] for row in getattr(FAKE.db, "committed", {}).values()]
            self.handed.append((company, task, list(ids), list(again)))
            return {"batch": task}

        operator_api.hand_in = hand_in
        # 录过的票先核账本（`ledger_tickets.check_entered`）：D010 是我们写过的，默认账本里还在
        self.ledger = {"D010": {"state": "present", "docnum": "RR6911-010", "twin": "D099"}}
        self.checked = []
        ledger = types.ModuleType("pearnly_core.ledger_tickets")
        ledger.GONE, ledger.PRESENT, ledger.QUEUED_STATE = "gone", "present", "queued"

        def check_entered(doc_ids, again=()):
            self.checked.append((list(doc_ids), list(again)))
            return {d: dict(v) for d, v in self.ledger.items() if d in doc_ids and d not in again}

        ledger.check_entered = check_entered
        tasks = types.ModuleType("pearnly_core.express_operator.tasks")
        tasks.read = lambda name: {"company": "CO-1", "period": "2026-10"}
        assistant = types.ModuleType("pearnly_core.assistant")
        assistant.sessions = sessions
        assistant.contracts = types.SimpleNamespace(REALTIME_EVENT="pearnly_assistant")
        express_operator = types.ModuleType("pearnly_core.express_operator")
        express_operator.api, express_operator.tasks = operator_api, tasks
        write_precheck = types.ModuleType("pearnly_core.write_precheck")
        write_precheck.products = products
        modules = {"frappe": _frappe(), "pearnly_core": types.ModuleType("pearnly_core"), "pearnly_core.assistant": assistant,
                   "pearnly_core.assistant.sessions": sessions, "pearnly_core.assistant.contracts": assistant.contracts,
                   "pearnly_core.write_precheck": write_precheck, "pearnly_core.write_precheck.products": products,
                   "pearnly_core.express_operator": express_operator, "pearnly_core.express_operator.api": operator_api,
                   "pearnly_core.express_operator.tasks": tasks, "pearnly_core.ledger_tickets": ledger}
        patcher = mock.patch.dict(sys.modules, modules)
        patcher.start()
        self.addCleanup(patcher.stop)
        write_precheck.contracts = _load("pearnly_core.write_precheck.contracts", CORE / "write_precheck/contracts.py")
        self.plan_module = _load("pearnly_core.assistant.write_plan", CORE / "assistant/write_plan.py")
        self.conversation(operator_task="OPT-1")

    def conversation(self, operator_task="", project="CO-1"):
        FAKE.db.sessions["sesn_1"] = {"name": "S1", "session_id": "sesn_1", "user": "u@example.com",
                                      "operator_task": operator_task, "project": project, "period": "2026-10"}

    def plan(self, **tool_input):
        return json.loads(self.plan_module.tool_plan(tool_input))

    def start(self, plan):
        return json.loads(self.plan_module.tool_start({"plan": plan}))

    def test_an_operator_plan_takes_the_whole_waiting_list_not_only_a_loaded_page(self):
        # 第 4 项同一个口子：代录的单由服务端按完整待录清单定，51 张起也在；跳过的列原因
        out = self.plan()
        self.assertEqual(out["status"], "ready")  # 与 B 线「录吧」的约定：有要写的回 ready
        self.assertEqual(out["count"], 58)
        self.assertIn("D051", out["documents"])
        self.assertEqual({item["doc_id"]: item["reason"] for item in out["skipped"]},
                         {"D007": "moved_to_not_entered", "D010": "already_entered"})
        self.assertEqual(out["account_sets"], [{"account_set": "K1", "account_set_title": "Acme 2026", "count": 58}])
        self.assertIn("say back to the user in one sentence", out["note"])
        self.assertEqual(FAKE.published[-1]["status"], "awaiting")  # 页面据此出「录吧」

    def test_writing_starts_only_after_the_user_answered_the_plan(self):
        out = self.plan()
        refused = self.start(out["plan"])  # CC 复述后没等用户回话就开始
        self.assertEqual(refused["status"], "not_confirmed")
        self.assertEqual(self.handed, [])
        self.turn[0] += 1  # 用户回了一句「录吧」
        started = self.start(out["plan"])
        self.assertEqual(started["status"], "started")
        self.assertEqual(self.handed, [("CO-1", "OPT-1", out["documents"], [])])
        self.assertEqual(self.committed_at_hand_in, ["started"])  # 先提交「已开始」再交单
        self.assertEqual(self.start(out["plan"])["status"], "not_awaiting")  # 同一份计划只开始一次

    def test_a_document_changed_after_the_plan_is_left_out(self):
        out = self.plan()
        self.docs["D003"] = {"state": "check", "items": [{"kind": "account", "subject": "x", "value": "5101", "label": "",
                                                          "source": "remembered"}]}  # 计划后改了科目
        self.turn[0] += 1
        started = self.start(out["plan"])
        self.assertEqual(started["changed"], ["D003"])
        self.assertNotIn("D003", self.handed[0][2])
        self.assertEqual(len(self.handed[0][2]), 57)

    def test_a_stop_after_the_plan_keeps_it_from_starting(self):
        out = self.plan()
        self.turn[0] += 1
        self.stopped[0] = FAKE.now()
        self.assertEqual(self.start(out["plan"])["status"], "stopped")
        self.assertEqual(self.handed, [])

    def test_nothing_writable_says_why_and_offers_no_start(self):
        self.docs = {"D001": {"state": "not_writable", "reason": "period_locked"}}
        out = self.plan()
        self.assertEqual((out["status"], out["count"]), ("nothing_to_write", 0))
        self.assertIn({"doc_id": "D001", "reason": "period_locked"}, out["skipped"])
        self.assertEqual((out["plan"], FAKE.db.rows), ("", {}))  # 不存计划：页面不出「录吧」
        self.assertEqual(FAKE.published, [])

    def test_again_lets_an_entered_document_in_only_when_the_user_said_so(self):
        out = self.plan(again=["D010"])
        self.assertIn("D010", out["documents"])
        self.turn[0] += 1
        self.start(out["plan"])
        self.assertEqual(self.handed[0][3], ["D010"])

    def test_a_ticket_entered_before_is_checked_in_the_ledger_before_it_is_asked(self):
        # 用户 11-10「万一 exp 那边删了呢」：录过的票先核账本——还在：不写、进跳过清单带单号，CC 复述时问跳过还是再录
        out = self.plan()
        entered = next(item for item in out["skipped"] if item["doc_id"] == "D010")
        self.assertEqual(entered, {"doc_id": "D010", "reason": "already_entered", "docnum": "RR6911-010", "in_ledger": "yes"})
        self.assertIn("in_ledger", out["note"])
        # 核不了（电脑不在线、太旧、超时）：同样不写，照实说核不了
        self.ledger["D010"]["state"] = "unknown"
        self.assertEqual(next(i for i in self.plan()["skipped"] if i["doc_id"] == "D010")["in_ledger"], "could_not_check")
        # 账本里删了 / 作废了：当没录过照写，记成再录（交单时不再按「写过」跳过）
        self.ledger["D010"]["state"] = "gone"
        out = self.plan()
        self.assertIn("D010", out["documents"])
        self.turn[0] += 1
        self.start(out["plan"])
        self.assertEqual(self.handed[-1][3], ["D010"])
        # 另一条正在写同一张票：这次不写
        self.ledger["D010"]["state"] = "queued"
        self.assertIn({"doc_id": "D010", "reason": "being_written"}, self.plan()["skipped"])

    def test_the_user_saying_enter_again_skips_the_check(self):
        self.plan(again=["D010"])
        self.assertEqual(self.checked[-1][1], ["D010"])  # 再录的不核

    def test_direct_push_enters_again_what_the_plan_marked(self):
        self.conversation(operator_task="")
        self.ledger = {"D002": {"state": "gone", "docnum": "RR-2", "twin": "D090"},
                       "D003": {"state": "present", "docnum": "RR-3", "twin": "D091"}}
        out = self.plan(documents=["D001", "D002", "D003"])
        self.assertEqual(out["documents"], ["D001", "D002"])
        self.assertEqual([i["doc_id"] for i in out["skipped"]], ["D003"])
        self.turn[0] += 1
        self.start(out["plan"])
        self.assertEqual(self.plan_module.again_of(["D001", "D002", "D003"]), ["D002"])

    def test_a_new_plan_replaces_the_one_not_started(self):
        first = self.plan()
        second = self.plan()
        self.assertEqual(FAKE.db.rows[first["plan"]]["status"], "replaced")
        self.turn[0] += 1
        self.assertEqual(self.start(first["plan"])["status"], "not_awaiting")
        self.assertEqual(self.start(second["plan"])["status"], "started")

    def test_direct_push_only_pushes_documents_of_a_started_plan_until_a_stop(self):
        self.conversation(operator_task="")
        allowed, refused = self.plan_module.permit(["D001"])
        self.assertEqual((allowed, refused), ([], {"D001": "not_started"}))  # 没有计划：不推
        out = self.plan(documents=["D001", "D002"])
        self.assertEqual(self.plan_module.permit(["D001"])[1], {"D001": "not_started"})  # 还没开始：不推
        self.turn[0] += 1
        self.assertEqual(self.start(out["plan"])["status"], "started")
        self.assertEqual(self.handed, [])  # 直写由 CC 接着用推送工具推
        self.assertEqual(self.plan_module.permit(["D001", "D009"]), (["D001"], {"D009": "not_in_plan"}))
        self.stopped[0] = FAKE.now()
        self.assertEqual(self.plan_module.permit(["D002"]), ([], {"D002": "stopped"}))
        # 不在 CC 对话里执行（网页待处理、定时任务）不经计划
        sys.modules["frappe"].local.pearnly_assistant = None
        self.assertEqual(self.plan_module.permit(["D005"]), (["D005"], {}))

    def test_a_direct_conversation_without_a_company_is_refused(self):
        self.conversation(operator_task="", project="")
        out = self.plan(documents=["D001"])
        self.assertEqual(out["status"], "no_company")
        self.assertIn("pearnly_file_conversation", out["note"])
        self.assertEqual(FAKE.db.rows, {})

    def test_the_plan_belongs_to_its_conversation(self):
        out = self.plan()
        FAKE.db.rows[out["plan"]]["session_id"] = "sesn_other"
        self.turn[0] += 1
        with self.assertRaises(_Throw):
            self.start(out["plan"])

    def test_both_tools_are_declared_for_both_modes_and_direct_push_asks_the_plan(self):
        source = (CORE / "assistant/tools.py").read_text(encoding="utf-8")
        for name, handler in (("pearnly_write_plan", "write_plan.tool_plan"), ("pearnly_write_start", "write_plan.tool_start")):
            block = source[source.index(f'{{"name": "{name}"'):]
            block = block[:block.index('"handler"') + 80]
            self.assertIn(handler, block)
            self.assertNotIn('"modes"', block)
        for path in ("apps/pearnly_cowork/pearnly_cowork/assistant/tools.py",
                     "apps/pearnly_erp_portal/pearnly_erp_portal/express/cc_tools.py"):
            text = (ROOT / path).read_text(encoding="utf-8")
            push = text[text.index("def push_documents(tool_input)"):]
            push = push[:push.index("\ndef ")]
            self.assertIn("direct_push.permitted(names)", push)


if __name__ == "__main__":
    unittest.main()
