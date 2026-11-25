"""Cowork 公司资料的引导式（总表 CW-D-CO，`assistant.guided.company`）：用共用引擎 `assistant.guide.run` 真实现走一遍。
替身 frappe、公司服务（`companies.service / rules / choices`）与 CC 工具（`assistant.company_tools`）；笔记规整用真的 `team_notes`。

断言的是应有状态：
- 菜单：能写公司资料的人才见「公司资料」；声明与 ERP 的公司资料同菜单字、图标、顺序；
- 加笔记只问正文（空的不收），改 / 删先选这家的笔记，改时正文推荐现文；设为全公司只列记住的选择里能提升的行，删规则只列公司规则；
- 确定都在本对话里转调 CC 工具同一个函数（note_save / note_remove / choice_save 带 key / choice_remove）；工具没存成退回对象那一步；
- 对话没挂公司：对象没有选项、题目下一行事实。

    python3 tests/test_cowork_company_guide.py
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



def _require_write():
    if not STATE["write"]:
        raise PermissionError("Not permitted")


CHOICES = [{"key": "express|A", "promotable": True, "subject_label": "Seller A", "value": "5100", "value_label": "Rent",
            "year_label": "2026"},
           {"key": "express|B", "promotable": False, "subject_label": "Seller B", "value": "cash"}]
RULES = [{"id": "R1", "subject_label": "Seller C", "value": "credit"}]
_module("pearnly_cowork.companies")
_module("pearnly_cowork.companies.service", require_write=_require_write,
        get=lambda name: types.SimpleNamespace(name=name, title="ABC Co"),
        detail=lambda company, period=None: {"notes": [dict(n) for n in STATE["notes"]], "choices": [dict(c) for c in CHOICES]})
_module("pearnly_cowork.companies.rules", listing=lambda company, limit=200: [dict(r) for r in RULES])
_module("pearnly_cowork.companies.choices", encode=lambda *parts: "|".join(parts))


def _tool(name):
    def call(payload):
        STATE["calls"].append((name, dict(payload), (getattr(frappe.local, "pearnly_assistant", None) or {}).get("session_id")))
        if STATE.get("refuse"):
            return json.dumps({"status": "not_saved", "reason": STATE["refuse"]})
        return json.dumps({"status": "removed" if "remove" in name else "saved", "note": {"id": "N9"}})
    return call


_module("pearnly_cowork.assistant.company_tools", **{name: _tool(name) for name in
                                                      ("note_save", "note_remove", "choice_save", "choice_remove")})

from pearnly_core.assistant.guide import registry, run  # noqa: E402
from pearnly_cowork.assistant.guided import company as guide  # noqa: E402

SESSION = "S1"
NOTES = [{"id": "N1", "text": "Closes books on the 25th"}, {"id": "N2", "text": "Pays by transfer"}]


class Doc(dict):
    __getattr__ = dict.get

    def __setattr__(self, key, value):
        self[key] = value


class Base(unittest.TestCase):
    def setUp(self):
        STATE.clear()
        STATE.update(write=True, notes=[dict(n) for n in NOTES], calls=[], rollbacks=[])
        HOOKS["pearnly_guides"] = ["pearnly_cowork.assistant.guided.company.GUIDES"]
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
        name = run.start(SESSION, "company")["run"]
        found = None
        for given in answers:
            found = run.answer(name, given)
        return name, found


class Declaration(Base):
    def test_paths_resolve_and_the_menu_matches_erp(self):
        found = guide.GUIDES[0]
        for step in found["steps"]:
            for key in ("options", "default", "check"):
                if key in step:
                    self.assertTrue(callable(_get_attr(step[key])), step[key])
        for key in ("allowed", "draft", "review", "confirm"):
            self.assertTrue(callable(_get_attr(found[key])), found[key])
        erp = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/masters/cc_guide.py").read_text(encoding="utf-8")
        for key in ("label_key", "icon", "order", "opening_key"):
            self.assertIn(f'"{key}": {found[key]!r}'.replace("'", '"'), erp)

    def test_the_menu_is_for_people_who_can_write_company_details(self):
        self.assertEqual(registry.offered()["chat"], ["company"])
        STATE["write"] = False
        self.assertEqual(registry.offered()["chat"], [])


class Consent(Base):
    def test_deleting_draws_the_allow_card(self):
        name = run.start(SESSION, "company")["run"]
        for action, consent in (("note_delete", "allow"), ("rule_delete", "allow"), ("note_add", "confirm"), ("rule_promote", "confirm")):
            self.assertEqual(run.answer(name, {"action": action})["consent"], consent, action)


class Notes(Base):
    def test_adding_a_note_goes_through_the_cc_tool_in_this_conversation(self):
        name, found = self.walk({"action": "note_add"})
        self.assertEqual(list(self.questions(found)), ["text"])
        found = run.answer(name, {"text": "Uses branch 00001"})
        run.confirm(name, found["version"])
        self.assertEqual(STATE["calls"], [("note_save", {"text": "Uses branch 00001"}, SESSION)])

    def test_editing_suggests_the_current_text_and_saves_that_note(self):
        name, found = self.walk({"action": "note_edit"}, {"note": "N2"})
        self.assertEqual(self.questions(found)["text"]["preselected"], "Pays by transfer")
        found = run.answer(name, {"text": "Pays by cheque"})
        done = run.confirm(name, found["version"])
        self.assertEqual(STATE["calls"][0][:2], ("note_save", {"text": "Pays by cheque", "note": "N2"}))
        self.assertEqual(done["result"]["docnum"], "Pays by cheque")   # 结果卡写改后的正文，不是改前的

    def test_deleting_lists_the_note_on_the_review(self):
        name, found = self.walk({"action": "note_delete"}, {"note": "N1"})
        self.assertIn(("note", "Closes books on the 25th"), [(l["key"], l["value"]) for l in found["review"]["lines"]])
        run.confirm(name, found["version"])
        self.assertEqual(STATE["calls"][0][:2], ("note_remove", {"note": "N1"}))

    def test_a_tool_that_did_not_save_sends_the_step_back(self):
        STATE["refuse"] = "cowork.company.notes_full"
        name, found = self.walk({"action": "note_add"}, {"text": "One more"})
        back = run.confirm(name, found["version"])
        self.assertEqual(back["card"]["returned"]["text"]["text"], "cowork.company.notes_full")


class Rules(Base):
    def test_only_promotable_choices_are_offered_and_promoted_by_key(self):
        name, found = self.walk({"action": "rule_promote"})
        self.assertEqual([(r["code"], r["name"]) for r in self.questions(found)["note"]["options"]], [("express|A", "Seller A → Rent")])
        found = run.answer(name, {"note": "express|A"})
        run.confirm(name, found["version"])
        self.assertEqual(STATE["calls"][0][:2], ("choice_save", {"key": "express|A"}))

    def test_deleting_a_rule_offers_the_company_rules(self):
        name, found = self.walk({"action": "rule_delete"})
        self.assertEqual([r["code"] for r in self.questions(found)["note"]["options"]], ["rule|R1"])
        found = run.answer(name, {"note": "rule|R1"})
        run.confirm(name, found["version"])
        self.assertEqual(STATE["calls"][0][:2], ("choice_remove", {"key": "rule|R1"}))

    def test_without_a_company_there_is_nothing_to_pick(self):
        found = guide.target_options({"company": "", "answers": {"action": "note_edit"}})
        self.assertEqual((found["options"], found["facts"]), ([], ["guide.fact.company.none"]))


if __name__ == "__main__":
    unittest.main()
