"""Cowork 任务看板的引导式（总表 CW-D-WK，`assistant.guided.work`）：用共用引擎 `assistant.guide.run` 真实现走一遍；看板接口（`work.api`）是内存替身。

断言的是应有状态：
- 菜单：有任务看板功能的人才见；默认看板预先定下（进「已定」）；
- 建卡问列表与标题；改卡先选卡片，各字段可选、推荐现值、点了才算，一项都没改不收；移动问列表；完成只问卡片；评论问正文；
- 确定各转调看板接口一次：改卡只带改了的字段、带复核时卡片的 modified（别处改过由服务拒、退回卡片那一步）。

    python3 tests/test_cowork_work_guide.py
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




CARDS = {"C1": {"name": "C1", "title": "VAT for ABC", "board_list": "L1", "modified": "2026-11-16 09:00:00", "description": "",
                "assignees": ["a@x"], "labels": ["G1"],
                "checklists": [{"title": "Docs", "items": [{"title": "Bank statement", "done": 1}, {"title": "Sales report", "done": 0}]}],
                "start_date": None, "due_date": "2026-11-20", "due_time": "17:00:00", "reminder": "1d", "recurrence": "never"},
         "C2": {"name": "C2", "title": "Payroll", "board_list": "L2", "modified": "2026-11-16 09:30:00"}}


def _call(name):
    def call(*args):
        STATE["calls"].append((name, args))
        if STATE.get("refuse"):
            raise ValueError(STATE["refuse"])
        return {"name": "C9"}
    return call


_module("pearnly_cowork.work")
_module("pearnly_cowork.work.service", attach=lambda name, upload: STATE["attached"].append((name, upload.filename, upload.stream.read())))
_module("pearnly_core.assistant.files", attachment=lambda file_id: (STATE["files"][file_id], b"pdf") if file_id in STATE["files"]
        else (_ for _ in ()).throw(ValueError("not here")))
_module("pearnly_cowork.work.access", allowed=lambda user=None: STATE["work"])
_module("pearnly_cowork.work.api",
        boards_list=lambda: [{"name": "B1", "title": "Team board", "is_default": 1}, {"name": "B2", "title": "Payroll", "is_default": 0}],
        board_lists=lambda board: [{"name": "L1", "title": "To do"}, {"name": "L2", "title": "Done"}],
        board=lambda board=None: {"lists": [{"name": "L1", "title": "To do"}, {"name": "L2", "title": "Done"}],
                                  "cards": [dict(card) for card in CARDS.values()],
                                  "members": [{"user": "a@x", "name": "Aom"}, {"user": "b@x", "name": "Bee"}],
                                  "labels": [{"name": "G1", "title": "Urgent", "color": "red"}, {"name": "G2", "title": "", "color": "blue"}]},
        card=lambda name: dict(CARDS[name]), create=_call("create"), update=_call("update"), move=_call("move"),
        toggle_done=_call("toggle_done"), comment=_call("comment"))

from pearnly_core.assistant.guide import registry, run  # noqa: E402
from pearnly_cowork.assistant.guided import work as guide  # noqa: E402

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
        STATE.update(work=True, calls=[], rollbacks=[], attached=[], files={"F1": "receipt.pdf"})
        HOOKS["pearnly_guides"] = ["pearnly_cowork.assistant.guided.work.GUIDES"]
        self.docs, self.clock = {}, [0]
        self.now = {"session_id": SESSION, "mode": "chat", "company": "CO-1", "ledger": "BOOK", "period": "2026-11",
                    "stopped_at": None}
        run._insert, run._locked, run._save, run._open_runs, run._conversation, run._announce = (
            self.insert, self.locked, self.save, self.open_runs, lambda session_id: dict(self.now), lambda doc: None)
        # 交文件的步骤登记成用户交出的（`assistant.files.commit`，同 test_cowork_recon_guide）：本对话的附件才认
        run._commit_files = lambda session_id, by_step: next(
            (key for key, ids in by_step.items() if any(i not in STATE["files"] for i in ids)), "")

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
        name = run.start(SESSION, "work")["run"]
        found = None
        for given in answers:
            found = run.answer(name, given)
        return name, found


class Work(Base):
    def test_paths_resolve_and_the_menu_follows_the_work_function(self):
        found = guide.GUIDES[0]
        for step in found["steps"]:
            for key in ("options", "default", "check"):
                if key in step:
                    self.assertTrue(callable(_get_attr(step[key])), step[key])
        for key in ("allowed", "draft", "review", "confirm"):
            self.assertTrue(callable(_get_attr(found[key])), found[key])
        self.assertEqual(registry.offered()["chat"], ["work"])
        STATE["work"] = False
        self.assertEqual(registry.offered()["chat"], [])

    def test_creating_decides_the_default_board_and_asks_list_then_title(self):
        name, found = self.walk({"action": "create"})
        self.assertEqual({item["key"]: item["value"] for item in found["card"]["decided"]}, {"board": "B1"})
        self.assertEqual(list(self.questions(found)), ["list"])
        found = run.answer(name, {"list": "L1"})
        self.assertEqual(list(self.questions(found)), ["title"])
        found = run.answer(name, {"title": "Close October"})
        run.confirm(name, found["version"])
        self.assertEqual(STATE["calls"], [("create", ("Close October", "L1"))])

    def test_updating_suggests_current_values_and_sends_only_what_changed_with_modified(self):
        name, found = self.walk({"action": "update"}, {"card": "C1"})
        asked = self.questions(found)
        self.assertEqual((asked["rename"]["preselected"], asked["due_time"]["preselected"], asked["reminder"]["preselected"]),
                         ("VAT for ABC", "17:00", "1d"))
        self.assertTrue(found["card"]["ready"] is False or "value" not in asked["rename"])
        found = run.answer(name, {"due_date": "2026-11-25", "reminder": "2h"})
        run.confirm(name, found["version"])
        call, args = STATE["calls"][0]
        self.assertEqual((call, args[0], args[1]), ("update", "C1", "2026-11-16 09:00:00"))
        self.assertEqual(json.loads(args[2]), {"due_date": "2026-11-25", "reminder": "2h"})

    def test_an_update_that_changes_nothing_is_sent_back(self):
        name, found = self.walk({"action": "update"}, {"card": "C1"})
        self.assertEqual(found["status"], run.ASKING)
        self.assertEqual(STATE["calls"], [])

    def test_move_done_and_comment_go_through_their_endpoints(self):
        for answers, expected in (([{"action": "move"}, {"card": "C2", "list": "L1"}], ("move", ("C2", "L1"))),
                                  ([{"action": "done"}, {"card": "C2"}], ("toggle_done", ("C2",))),
                                  ([{"action": "comment"}, {"card": "C1", "content": "Waiting for the bank statement"}],
                                   ("comment", ("C1", "Waiting for the bank statement")))):
            STATE["calls"].clear()
            name, found = self.walk(*answers)
            run.confirm(name, found["version"])
            self.assertEqual(STATE["calls"], [expected])

    def test_a_card_changed_elsewhere_goes_back_to_the_card(self):
        STATE["refuse"] = "cowork.work.error.changed"
        name, found = self.walk({"action": "update"}, {"card": "C1"}, {"rename": "VAT ABC"})
        back = run.confirm(name, found["version"])
        self.assertEqual(back["card"]["returned"]["card"]["text"], "cowork.work.error.changed")

    def test_members_and_labels_suggest_the_current_ones_and_replace_them(self):
        name, found = self.walk({"action": "members"}, {"card": "C1"})
        self.assertEqual(self.questions(found)["assignees"]["preselected"], ["a@x"])
        found = run.answer(name, {"assignees": ["a@x", "b@x"]})
        self.assertEqual([row["name"] for row in found["review"]["tables"][0]["rows"]], ["Aom", "Bee"])
        run.confirm(name, found["version"])
        self.assertEqual(json.loads(STATE["calls"][0][1][2]), {"assignees": ["a@x", "b@x"]})
        STATE["calls"].clear()
        name, found = self.walk({"action": "labels"}, {"card": "C1"}, {"label_ids": ["G2"]})
        run.confirm(name, found["version"])
        self.assertEqual(json.loads(STATE["calls"][0][1][2]), {"labels": ["G2"]})

    def test_checklist_ticks_and_a_new_item_keep_the_other_items(self):
        name, found = self.walk({"action": "checklist"}, {"card": "C1"})
        self.assertEqual(self.questions(found)["checked"]["preselected"], ["0:0"])
        found = run.answer(name, {"checked": ["0:1"], "new_item": "Payroll"})
        run.confirm(name, found["version"])
        self.assertEqual(json.loads(STATE["calls"][0][1][2]), {"checklists": [{"title": "Docs", "items": [
            {"title": "Bank statement", "done": 0}, {"title": "Sales report", "done": 1}, {"title": "Payroll", "done": 0}]}]})

    def test_a_new_checklist_holds_the_new_item_and_ticks_stay_when_not_answered(self):
        name, found = self.walk({"action": "checklist"}, {"card": "C1"}, {"new_group": "Tax", "new_item": "PP30"})
        run.confirm(name, found["version"])
        groups = json.loads(STATE["calls"][0][1][2])["checklists"]
        self.assertEqual((groups[0]["items"][0]["done"], groups[1]), (1, {"title": "Tax", "items": [{"title": "PP30", "done": 0}]}))

    def test_attaching_puts_conversation_files_on_the_card(self):
        name, found = self.walk({"action": "attach"}, {"card": "C1"})
        self.assertEqual(_codes(run.answer(name, {"files": ["F9"]})), {"files": "cowork.work.error.file"})
        found = run.answer(name, {"files": ["F1"]})
        self.assertEqual(found["review"]["tables"][0]["rows"][0]["name"], "receipt.pdf")
        run.confirm(name, found["version"])
        self.assertEqual(STATE["attached"], [("C1", "receipt.pdf", b"pdf")])

    def test_cards_search_by_title_and_list(self):
        found = guide.card_options({"answers": {"board": "B1"}}, txt="done")
        self.assertEqual([row["code"] for row in found["options"]], ["C2"])


if __name__ == "__main__":
    unittest.main()
