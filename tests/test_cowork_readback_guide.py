"""Cowork 读回的引导式（总表 CW-D-RB，`assistant.guided.readback`：声明与产品函数）：用共用引擎 `assistant.guide.run` 真实现走一遍。

不连库、不出网：替身 frappe；对话账套（`assistant.ledger.view / choose`）、写入闸（`assistant.write_scope`）、连接类型、读回服务
（`readback.service.request / status / file`）、对话文件（`assistant.files`）都是内存替身；读回的月份校验用真的 `readback.contracts`。

断言的是应有状态：
- 本对话账套是 Express：账套不问（进「已定」），先问月份；推荐本对话月份，但要点了才算（月份每次明选）；
- 本对话账套没定、或定的是 MR.ERP：问账套，选项只列这家的 Express 账套；一本都没有时题目下一行「只有 Express 账套可以回读」；
  选了记进本对话（同右栏选择器，`ledger.choose(by="page")`）；MR.ERP 账套不收；
- 月份写错（不是 YYYY-MM）不收；答完不读；
- 确定 = 页面上这一下：请电脑读一次（同一确定再来一次不读第二次）；之后在右栏换了账套：不读，退回账套那一步；
  读回服务拒绝：退回账套那一步、带服务的原因；
- 结果：在读（页面过一会儿再取）/ 电脑没来领（一行事实，不当成读了）/ 读完（四本表放进对话、带文件编号，只放一次）/
  失败（原因）；引擎没把确定的回包交来时回 None（引擎照存下的显示）；
- 菜单：有录入功能的人才见「读回」，代录菜单上没有。

    python3 tests/test_cowork_readback_guide.py
"""
import datetime
import importlib
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for app in ("pearnly_core", "pearnly_cowork"):
    sys.path.insert(0, str(ROOT / "apps" / app))

HOOKS, LOGGED, CACHE = {}, [], {}
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
frappe.db = types.SimpleNamespace(commit=lambda: None, rollback=lambda: None)
frappe.utils = types.SimpleNamespace(get_datetime=lambda value: value, now_datetime=datetime.datetime.now)  # 放太久失效（run._idle）
frappe.cache = types.SimpleNamespace(get_value=lambda key: CACHE.get(key),
                                     set_value=lambda key, value, expires_in_sec=None: CACHE.__setitem__(key, value))
frappe.publish_realtime = lambda *args, **kwargs: None
sys.modules["frappe"] = frappe


def _module(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules[name] = module
    return module


_module("pearnly_core.assistant.contracts", REALTIME_EVENT="pearnly_assistant_event")
_module("pearnly_core.assistant.write_plan", _iso=lambda moment: moment.isoformat() + "Z")

STATE = {}


def _view(session_id):
    return {"current": STATE["current"], "options": [{"id": i, "label": label} for i, label in STATE["books"]]}


def _choose(session_id, ledger_id, by="page"):
    STATE["chosen"].append((session_id, ledger_id, by))
    if ledger_id not in {i for i, _label in STATE["books"]}:
        return {"status": "refused", "reason": "not_an_option"}
    STATE["current"] = ledger_id
    return {"status": "set", "ledger": ledger_id}


_module("pearnly_core.assistant.ledger", view=_view, choose=_choose)


def _add(filename, content, mime, origin="tool"):
    STATE["added"].append(filename)
    return f"/mnt/session/uploads/F{len(STATE['added'])}/{filename}"


_module("pearnly_core.assistant.files", add_to_conversation=_add, resolve=lambda path: path.split("/")[-2])


def _allowed():
    STATE["sessions_seen"].append((getattr(frappe.local, "pearnly_assistant", None) or {}).get("session_id"))
    return {"company": "CO1", "sets": [{"connection": c, "key": k, "identity": i} for i, c, k in STATE["sets"]]}


def _pinned():
    found = next((row for row in STATE["sets"] if row[0] == STATE["current"]), None)
    if not found:
        return {"status": "needed", "id": "", "key": "", "connection": ""}
    return {"status": "set", "id": found[0], "connection": found[1], "key": found[2], "places": [(found[1], found[2])]}


_module("pearnly_cowork.assistant.write_scope", allowed=_allowed, pinned=_pinned,
        place=lambda pin, connection=None: (pin["connection"], pin["key"]))
_module("pearnly_cowork.companies")
_module("pearnly_cowork.companies.provider", ledger_id=lambda connection, key: f"{connection}|{key}")
_module("pearnly_cowork.connections")
_module("pearnly_cowork.connections.service", adapter_of=lambda connection: STATE["adapters"].get(connection))
_module("pearnly_cowork.access")


def _require(capability):
    if not STATE["intake"]:
        raise PermissionError("Not permitted")


_module("pearnly_cowork.access.scope", require=_require)


def _request(connection, key, period, accounts=None):
    STATE["requests"].append((connection, key, period, accounts))
    if STATE.get("refuse"):
        raise ValueError(STATE["refuse"])
    return dict(STATE["made"])


_module("pearnly_cowork.readback.service", request=_request, status=lambda name: dict(STATE["status"]),
        file=lambda name, kind: (f"{kind}-2026-10.xlsx", b"x"))

from pearnly_core.assistant.guide import registry, run  # noqa: E402
from pearnly_cowork.assistant.guided import readback as guide  # noqa: E402
cc_guide = guide

EXPRESS_ID, MRERP_ID, OTHER_ID = "CONN-E|2026", "CONN-M|MR1", "CONN-E|2025"
SESSION = "S1"
NAME = "a" * 32


class Doc(dict):
    __getattr__ = dict.get

    def __setattr__(self, key, value):
        self[key] = value


class Base(unittest.TestCase):
    def setUp(self):
        STATE.clear()
        STATE.update(current=EXPRESS_ID, books=[(EXPRESS_ID, "ABC 2026"), (MRERP_ID, "ABC MR.ERP"), (OTHER_ID, "ABC 2025")],
                     sets=[(EXPRESS_ID, "CONN-E", "2026"), (MRERP_ID, "CONN-M", "MR1"), (OTHER_ID, "CONN-E", "2025")],
                     adapters={"CONN-E": "express", "CONN-M": "mrerp"}, chosen=[], requests=[], added=[], sessions_seen=[],
                     intake=True, made={"readback": NAME, "status": "reading"}, status={"readback": NAME, "status": "reading"})
        CACHE.clear()
        HOOKS["pearnly_guides"] = ["pearnly_cowork.assistant.guided.readback.GUIDES"]
        self.docs, self.clock = {}, [0]
        self.now = {"session_id": SESSION, "mode": "chat", "company": "CO1", "ledger": EXPRESS_ID, "period": "2026-10",
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

    def answered(self, period="2026-10"):
        name = run.start(SESSION, "readback")["run"]
        return name, run.answer(name, {"period": period})


class Declaration(Base):
    def test_readback_is_a_read_only_form_guide_in_cowork_direct_write(self):
        found = cc_guide.GUIDES[0]
        self.assertEqual((found["source"], found["modes"], found["writes"]), ("form", ["chat"], False))
        self.assertEqual(found["ledgers"], ["express_local", "express_cloud"])   # MR.ERP 不能读回
        self.assertEqual({s["key"]: s["rule"] for s in found["steps"]}, {"book": "auto", "period": "always"})
        for step in found["steps"]:
            for key in ("options", "default", "check", "apply", "facts"):
                if key in step:
                    self.assertTrue(callable(_get_attr(step[key])), step[key])
        for key in ("allowed", "draft", "confirm", "result"):
            self.assertTrue(callable(_get_attr(found[key])), found[key])

    def test_the_menu_lists_readback_only_for_people_with_intake_and_not_in_operator_mode(self):
        self.assertEqual(registry.offered(), {"chat": ["readback"], "operator": []})
        STATE["intake"] = False
        self.assertEqual(registry.offered(), {"chat": [], "operator": []})


class Book(Base):
    def test_an_express_conversation_book_is_decided_and_the_month_is_asked_with_the_conversation_month_suggested(self):
        found = run.start(SESSION, "readback")
        self.assertEqual(list(self.questions(found)), ["period"])
        decided = {item["key"]: item for item in found["card"]["decided"]}
        self.assertEqual((decided["book"]["value"], decided["book"]["source"]), (EXPRESS_ID, "conversation"))
        period = self.questions(found)["period"]
        self.assertEqual(period["preselected"], "2026-10")
        self.assertNotIn("value", period)
        self.assertFalse(found["card"]["ready"])   # 推荐的月份点了才算
        self.assertEqual(STATE["sessions_seen"][-1], SESSION)   # 按本对话认公司

    def test_an_mrerp_conversation_book_is_asked_with_only_express_books_offered(self):
        STATE["current"] = MRERP_ID
        self.now["ledger"] = MRERP_ID
        found = run.start(SESSION, "readback")
        question = self.questions(found)["book"]
        self.assertEqual([row["code"] for row in question["options"]], [EXPRESS_ID, OTHER_ID])
        self.assertNotIn("period", self.questions(found))   # 月份等账套定了再问

    def test_choosing_a_book_files_it_on_the_conversation_like_the_sidebar(self):
        STATE["current"] = ""
        self.now["ledger"] = ""
        name = run.start(SESSION, "readback")["run"]
        self.now["ledger"] = OTHER_ID   # 记进本对话后对话选择器读到的
        found = run.answer(name, {"book": OTHER_ID})
        self.assertEqual(STATE["chosen"], [(SESSION, OTHER_ID, "page")])
        self.assertEqual(list(self.questions(found)), ["period"])
        self.assertEqual(found["status"], run.ASKING)

    def test_an_mrerp_book_is_not_accepted(self):
        STATE["current"] = ""
        self.now["ledger"] = ""
        name = run.start(SESSION, "readback")["run"]
        found = run.answer(name, {"book": MRERP_ID})
        self.assertEqual((found["outcome"], found["refused"]), (run.REFUSED, {"book": "not offered"}))
        self.assertEqual(STATE["chosen"], [])

    def test_a_company_without_express_books_says_so_under_the_question(self):
        STATE.update(current="", books=[(MRERP_ID, "ABC MR.ERP")], sets=[(MRERP_ID, "CONN-M", "MR1")])
        self.now["ledger"] = ""
        question = self.questions(run.start(SESSION, "readback"))["book"]
        self.assertEqual((question["options"], question["facts"]), ([], ["Only Express account sets can be read back."]))


class Month(Base):
    def test_a_month_not_written_as_year_and_month_is_refused(self):
        name = run.start(SESSION, "readback")["run"]
        found = run.answer(name, {"period": "10/2026"})
        self.assertEqual(found["outcome"], run.REFUSED)
        self.assertIn("period", found["refused"])

    def test_answering_the_month_makes_the_guide_ready_without_reading_anything(self):
        _name, found = self.answered()
        self.assertTrue(found["card"]["ready"])
        self.assertEqual(found["status"], run.ASKING)
        self.assertEqual(STATE["requests"], [])   # 答完不读，确定才读


class Confirm(Base):
    def test_confirming_reads_the_conversation_book_for_the_chosen_month_once(self):
        name, found = self.answered("2026-09")
        done = run.confirm(name, found["version"])
        self.assertEqual(done["status"], run.DONE)
        self.assertEqual(STATE["requests"], [("CONN-E", "2026", "2026-09", None)])
        again = run.confirm(name, found["version"])
        self.assertEqual(again["status"], run.DONE)
        self.assertEqual(len(STATE["requests"]), 1)   # 同一确定不读第二次

    def test_after_confirming_the_result_card_follows_the_readback_through_the_engine(self):
        name, found = self.answered()
        done = run.confirm(name, found["version"])
        self.assertEqual(done["result"], {"status": "queued", "wait": True, "reason_text": "Reading back the accounting books"})
        STATE["status"] = {"readback": NAME, "status": "ready", "account_set": "ABC 2026", "period": "2026-10",
                           "files": ["general_ledger"]}
        later = run.view(self.docs[name])
        self.assertEqual((later["result"]["status"], [row["file"] for row in later["result"]["files"]]), ("ready", ["F1"]))

    def test_a_book_that_is_no_longer_the_conversation_book_is_not_read_and_is_asked_again(self):
        STATE["current"] = OTHER_ID   # 卡上定的是 EXPRESS_ID，之后对话账套换成了另一本
        done = guide.confirm({"session": SESSION, "answers": {}}, {"book": EXPRESS_ID, "period": "2026-10"}, "", "c")
        self.assertEqual(done, {"returned": {"book": {"reason": "not_this_conversation_book"}}})
        self.assertEqual(STATE["requests"], [])

    def test_a_readback_refused_by_the_service_goes_back_to_the_book_with_its_reason(self):
        STATE["refuse"] = "Only Express account sets can be read back."
        name, found = self.answered()
        back = run.confirm(name, found["version"])
        self.assertEqual(back["outcome"], run.REFUSED)
        self.assertEqual(back["card"]["returned"]["book"]["text"], "Only Express account sets can be read back.")


class Result(Base):
    CTX = {"session": SESSION, "answers": {}}

    def test_without_the_confirm_outcome_the_engine_shows_what_it_stored(self):
        self.assertIsNone(guide.result(dict(self.CTX), "x@2026-10"))

    def test_reading_asks_the_page_to_look_again(self):
        found = guide.result({**self.CTX, "done": {"readback": NAME, "status": "reading"}}, "")
        self.assertEqual(found, {"status": "queued", "wait": True, "reason_text": "Reading back the accounting books"})

    def test_a_computer_that_did_not_take_the_request_is_a_fact_not_a_read(self):
        found = guide.result({**self.CTX, "done": {"status": "waiting_for_cc", "reason": "unreachable"}}, "")
        self.assertEqual(found, {"status": "failed",
                                 "reason_text": "None of your computers with CC Desktop can open this account set"})

    def test_a_finished_readback_puts_the_four_workbooks_in_the_conversation_once(self):
        STATE["status"] = {"readback": NAME, "status": "ready", "account_set": "ABC 2026", "period": "2026-10",
                           "files": ["general_ledger", "sales_vat", "purchase_vat", "changes"]}
        ctx = {**self.CTX, "done": {"readback": NAME, "status": "reading"}}
        found = guide.result(ctx, "")
        self.assertEqual((found["status"], found["account_set"], found["period"]), ("ready", "ABC 2026", "2026-10"))
        self.assertEqual([row["name"] for row in found["files"]],
                         ["general_ledger-2026-10.xlsx", "sales_vat-2026-10.xlsx", "purchase_vat-2026-10.xlsx",
                          "changes-2026-10.xlsx"])
        self.assertEqual([row["file"] for row in found["files"]], ["F1", "F2", "F3", "F4"])
        guide.result(ctx, "")
        self.assertEqual(len(STATE["added"]), 4)   # 同一段对话同一份只放一次

    def test_a_failed_readback_gives_its_reason(self):
        STATE["status"] = {"readback": NAME, "status": "failed", "code": "cowork.readback.account_missing"}
        found = guide.result({**self.CTX, "done": {"readback": NAME, "status": "reading"}}, "")
        self.assertEqual(found, {"status": "failed", "reason_text": "cowork.readback.account_missing"})


if __name__ == "__main__":
    unittest.main()
