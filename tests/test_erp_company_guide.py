"""ERP 公司资料（知识库笔记，总表 ERP-D-CO）与添加公司（ERP-D-ADD）的引导式：`masters.cc_guide` 声明 + `masters.guide` 产品函数，
用共用引擎 `assistant.guide.run` 真实现走一遍。替身 frappe 与公司页服务（`masters.cc_service`、`masters.company_notes`）。

断言的是应有状态：
- 菜单：能写知识库的人见「公司资料」，能建公司的人见「添加公司」；
- 公司资料：公司 = 本对话公司；加笔记只问正文，改 / 删先选这家的笔记；改时正文推荐现文（点了才算）；空正文不收；
  确定转调 `note_save`（来源 cc、带对话）/ `note_delete`；服务拒了退回；对话没挂公司时笔记没有选项、复核卡标出；
- 添加公司：选项来自「从连接添加」同一份目录（按页、搜索）；按标识核；确定一次交 `create_many`（连接 + 账套）；目录里已没有的退回。

    python3 tests/test_erp_company_guide.py
"""
import datetime
import importlib
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


def _require_write(user=None):
    if not STATE["write"]:
        raise PermissionError("Not permitted")
    return user


def _note_save(company, text, note=None, *, origin="person", session=""):
    if STATE.get("refuse"):
        raise ValueError(STATE["refuse"])
    STATE["saved"].append((company, text, note, origin, session))
    return {"id": note or "N9", "text": text}


def _pending(search=None, adapter=None, start=0, limit=50):
    rows = [row for row in STATE["catalog"] if not search or search.casefold() in row["title"].casefold()]
    return {"rows": rows[start:start + limit], "adapters": ["express"], "total": len(rows)}


def _create_many(items):
    STATE["created"].append(list(items))
    return [{"id": f"CO-{n}", "title": "x", "created": True} for n, _item in enumerate(items)]


NOTES = [{"id": "N1", "text": "Closes books on the 25th"}, {"id": "N2", "text": "Pays by transfer"}]
_module("pearnly_erp_portal.masters.cc_service", PAGE_MAX=50, require_write=_require_write,
        can_build=lambda user=None: STATE["build"], get=lambda company: types.SimpleNamespace(name=company, company_name="ABC Co"),
        detail=lambda company, period=None: {"notes": [dict(note) for note in STATE["notes"]]},
        note_save=_note_save, note_delete=lambda company, note: STATE["deleted"].append((company, note)) or {"id": note},
        pending=_pending, create_many=_create_many)


def _clean(value):
    text = " ".join(str(value or "").split())
    if not text or len(text) > 500:
        raise ValueError("Note is empty or too long")
    return text


_module("pearnly_erp_portal.masters.company_notes", clean=_clean, INVALID="Note is empty or too long")

from pearnly_core.assistant.guide import registry, run  # noqa: E402
from pearnly_erp_portal.masters import cc_guide, guide  # noqa: E402

SESSION = "S1"


class Doc(dict):
    __getattr__ = dict.get

    def __setattr__(self, key, value):
        self[key] = value


class Base(unittest.TestCase):
    def setUp(self):
        STATE.clear()
        STATE.update(write=True, build=True, notes=[dict(note) for note in NOTES], saved=[], deleted=[], created=[], rollbacks=[],
                     catalog=[{"identity": f"express:999000{n:07d}:00000", "title": f"Client {n}", "tax_id": f"999000{n:07d}",
                               "years": ["2026"], "connection": "", "account_set_key": f"999000{n:07d}:00000"}
                              for n in range(60)])
        HOOKS["pearnly_guides"] = ["pearnly_erp_portal.masters.cc_guide.GUIDES"]
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

    def walk(self, business, *answers):
        name = run.start(SESSION, business)["run"]
        found = None
        for given in answers:
            found = run.answer(name, given)
        return name, found


class Declaration(Base):
    def test_every_dotted_path_points_at_a_function(self):
        for found in cc_guide.GUIDES:
            for step in found["steps"]:
                for key in ("options", "default", "check", "apply", "facts"):
                    if key in step:
                        self.assertTrue(callable(_get_attr(step[key])), step[key])
            for key in ("allowed", "draft", "review", "confirm"):
                self.assertTrue(callable(_get_attr(found[key])), found[key])

    def test_the_menu_follows_note_writing_and_company_building(self):
        self.assertEqual(registry.offered()["chat"], ["company", "company_add"])
        STATE["build"] = False
        self.assertEqual(registry.offered()["chat"], ["company"])
        STATE["write"] = False
        self.assertEqual(registry.offered()["chat"], [])


class Consent(Base):
    def test_deleting_a_note_draws_the_allow_card(self):
        name = run.start(SESSION, "company")["run"]
        for action, consent in (("note_delete", "allow"), ("note_edit", "confirm")):
            self.assertEqual(run.answer(name, {"action": action})["consent"], consent, action)


class Notes(Base):
    def test_adding_asks_only_the_text_and_saves_it_as_from_cc(self):
        name, found = self.walk("company", {"action": "note_add"})
        self.assertEqual(list(self.questions(found)), ["text"])
        self.assertEqual(run.answer(name, {"text": "   "})["refused"], {"text": "a short text"})
        found = run.answer(name, {"text": "Uses  branch 00001"})
        lines = {line["key"]: line["value"] for line in found["review"]["lines"]}
        self.assertEqual((lines["company"], lines["text"]), ("ABC Co", "Uses branch 00001"))
        run.confirm(name, found["version"])
        self.assertEqual(STATE["saved"], [("CO-1", "Uses  branch 00001", None, "cc", SESSION)])

    def test_editing_picks_a_note_of_this_company_and_suggests_its_text(self):
        name, found = self.walk("company", {"action": "note_edit"})
        self.assertEqual([row["code"] for row in self.questions(found)["note"]["options"]], ["N1", "N2"])
        self.assertEqual(run.answer(name, {"note": "N7"})["refused"], {"note": "not offered"})
        found = run.answer(name, {"note": "N2"})
        text = self.questions(found)["text"]
        self.assertEqual((text["preselected"], text.get("value")), ("Pays by transfer", None))   # 推荐、点了才算
        found = run.answer(name, {"text": "Pays by cheque"})
        run.confirm(name, found["version"])
        self.assertEqual(STATE["saved"][0][1:3], ("Pays by cheque", "N2"))

    def test_deleting_a_note_lists_it_on_the_review_and_deletes_it(self):
        name, found = self.walk("company", {"action": "note_delete"}, {"note": "N1"})
        self.assertIn(("note", "Closes books on the 25th"), [(line["key"], line["value"]) for line in found["review"]["lines"]])
        run.confirm(name, found["version"])
        self.assertEqual(STATE["deleted"], [("CO-1", "N1")])

    def test_a_note_the_service_refuses_goes_back_with_its_reason(self):
        STATE["refuse"] = "Notes are full"
        name, found = self.walk("company", {"action": "note_add"}, {"text": "One more"})
        back = run.confirm(name, found["version"])
        self.assertEqual(back["card"]["returned"]["text"]["text"], "Notes are full")

    def test_a_conversation_without_a_company_has_no_notes_to_pick(self):
        found = guide.note_options({"company": "", "answers": {}})
        self.assertEqual((found["options"], found["facts"]), ([], ["guide.fact.company.none"]))


class AddCompanies(Base):
    def test_options_page_and_search_the_catalog(self):
        first = guide.pending_options({}, page_length=20)
        self.assertEqual((len(first["options"]), first["more"]), (20, True))
        self.assertEqual([row["name"] for row in guide.pending_options({}, txt="client 59")["options"]], ["Client 59"])

    def test_confirming_adds_the_ticked_companies_once_by_connection_and_account_set(self):
        codes = [STATE["catalog"][1]["identity"], STATE["catalog"][55]["identity"]]
        name, found = self.walk("company_add", {"companies": codes})
        self.assertEqual([row["name"] for row in found["review"]["tables"][0]["rows"]], ["Client 1", "Client 55"])
        done = run.confirm(name, found["version"])
        run.confirm(name, found["version"])
        self.assertEqual(STATE["created"], [[{"connection": "", "account_set_key": "9990000000001:00000"},
                                             {"connection": "", "account_set_key": "9990000000055:00000"}]])
        self.assertEqual(len(done["result"]["companies"]), 2)

    def test_a_company_not_in_the_catalog_is_not_accepted(self):
        name = run.start(SESSION, "company_add")["run"]
        self.assertEqual(run.answer(name, {"companies": ["express:9:00000"]})["refused"], {"companies": "not offered"})

    def test_a_company_added_elsewhere_meanwhile_goes_back_to_the_ticks(self):
        code = STATE["catalog"][3]["identity"]
        name, found = self.walk("company_add", {"companies": [code]})
        STATE["catalog"].pop(3)
        back = run.confirm(name, found["version"])
        self.assertEqual((back["outcome"], STATE["created"]), (run.REFUSED, []))


if __name__ == "__main__":
    unittest.main()
