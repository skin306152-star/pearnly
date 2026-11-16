"""Cowork 对账的引导式（总表 CW-D-BR / IR / VC，`assistant.guided.recon`）：用共用引擎 `assistant.guide.run` 真实现走一遍。

不连库、不出网：替身 frappe；对账中心（`recon_center.jobs / sheets / presets`）、各对账域导出、读回服务、对话账套 / 写入闸、对话文件都是
内存替身。

断言的是应有状态（主会话 11-16 定的口径）：
- 菜单一项「对账」，有任一种对账功能的人才见；第一题种类只列这个人有权限的；
- 本对话账套是 Express：问来源（从 Express 读回 / 交文件）；不是（MR.ERP、没定）：只能交文件，不问；
- 读回：月份推荐对话月份、点了才算；这个月已经读完的直接用（不问）；没有就出卡时请电脑读一次（核答案不再请读），在读给「等」与事实；
- 交文件：各种对账只问它那两侧；银行账单一侧带网页同一句提示；文件只认本对话的；收入对账的总账只收一份；
- 确定：照网页「新建」开一套（两侧文件、读回那一侧取读回的工作簿），双击不开第二套；服务拒绝退回来源那一步；
- 结果：在跑「等」；完成给标题、要定一次的问题与结果工作簿（同一段对话只放一次）；失败给原因；
- 结果里的问题作答：记成预设（同结果页），重跑类的新任务号接着等；不在选项里的不收。

    python3 tests/test_cowork_recon_guide.py
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
frappe.local = types.SimpleNamespace(site="s1", lang="th")
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
EXPRESS_ID, MRERP_ID = "CONN-E|2026", "CONN-M|MR1"
SESSION = "S1"


def _view(session_id):
    return {"current": STATE["current"], "options": [{"id": EXPRESS_ID, "label": "ABC 2026"}, {"id": MRERP_ID, "label": "ABC MR"}]}


_module("pearnly_core.assistant.ledger", view=_view, choose=lambda *args, **kwargs: {"status": "set"})


def _attachment(file_id):
    if file_id not in STATE["attachments"]:
        raise ValueError("This attachment is not in this conversation.")
    return (f"{file_id}.xlsx", b"x" + file_id.encode())


def _add(filename, content, mime, origin="tool"):
    STATE["added"].append(filename)
    return f"/mnt/session/uploads/F{len(STATE['added'])}/{filename}"


_module("pearnly_core.assistant.files", attachment=_attachment, add_to_conversation=_add,
        resolve=lambda path: path.split("/")[-2])
SETS = [(EXPRESS_ID, "CONN-E", "2026"), (MRERP_ID, "CONN-M", "MR1")]


def _pinned():
    found = next((row for row in SETS if row[0] == STATE["current"]), None)
    if not found:
        return {"status": "needed", "id": "", "key": "", "connection": ""}
    return {"status": "set", "id": found[0], "connection": found[1], "key": found[2]}


_module("pearnly_cowork.assistant.write_scope", pinned=_pinned, place=lambda pin, connection=None: (pin["connection"], pin["key"]),
        allowed=lambda: {"company": "CO1", "sets": [{"connection": c, "key": k, "identity": i} for i, c, k in SETS]})
_module("pearnly_cowork.companies")
_module("pearnly_cowork.companies.provider", ledger_id=lambda connection, key: f"{connection}|{key}")
_module("pearnly_cowork.connections")
_module("pearnly_cowork.connections.service", adapter_of=lambda connection: {"CONN-E": "express", "CONN-M": "mrerp"}[connection])
_module("pearnly_cowork.access")
_module("pearnly_cowork.access.scope", require=lambda capability: None)


def _latest(connection, key, period):
    STATE["latest"].append((connection, key, period))
    return STATE["readbacks"].get(period)


def _request(connection, key, period, accounts=None):
    STATE["requests"].append((connection, key, period))
    STATE["readbacks"][period] = {"readback": "RB-" + period, "status": "reading"}
    return dict(STATE["readbacks"][period])


_module("pearnly_cowork.readback.service", latest=_latest, request=_request,
        file=lambda name, kind: (f"{kind}.xlsx", f"{name}:{kind}".encode()))


class Access:
    def __init__(self, kind):
        self.kind = kind

    def allowed(self, user=None):
        return self.kind in STATE["kinds"]


def _start(kind, title, sides, options=None):
    STATE["started"].append((kind, title, sides, options))
    if STATE.get("refuse"):
        raise ValueError(STATE["refuse"])
    return {"job": f"J{len(STATE['started'])}"}


_module("pearnly_cowork.recon_center")
_module("pearnly_cowork.recon_center.jobs", KINDS={kind: Access(kind) for kind in ("bank", "income", "vat")},
        MULTIPLE={"vat": ("left", "right"), "bank": ("left", "right"), "income": ("right",)},
        start=_start, status=lambda job: dict(STATE["jobs"][job]))
_module("pearnly_cowork.recon_center.sheets", load=lambda kind, task, lang=None: {"task": dict(STATE["sheets"][task])})


def _answer(kind, task, rule, value, subject=None, lang=None):
    STATE["answered"].append((kind, task, rule, value, subject))
    return {"job": "J9"} if rule == "gl_account" else {"task": {"name": task}}


_module("pearnly_cowork.recon_center.presets", answer=_answer)
_module("pearnly_cowork.reconciliation")
for _name in ("bank_recon_service", "income_recon_service", "vat_check_service"):
    _module(f"pearnly_cowork.reconciliation.{_name}",
            export=lambda task, lang=None: STATE["exported"].append(task) or {"filename": f"{task}.xlsx", "content": b"PK"})

from pearnly_core.assistant.guide import registry, run  # noqa: E402
from pearnly_cowork.assistant.guided import recon  # noqa: E402

BANK_SHEET = {"name": "CBR-1", "title": "KBank 10", "notes": [], "questions": [
    {"rule": "gl_account", "title": "Which account?", "count": "3 rows",
     "options": [{"value": "1100", "label": "1100 (5)"}, {"value": "1110", "label": "1110 (1)"}]},
    {"rule": "excluded", "subject": "4100", "title": "Leave 4100 out?",
     "options": [{"value": "yes", "label": "Yes"}, {"value": "no", "label": "No"}]}]}


class Doc(dict):
    __getattr__ = dict.get

    def __setattr__(self, key, value):
        self[key] = value


class Base(unittest.TestCase):
    def setUp(self):
        STATE.clear()
        STATE.update(current=EXPRESS_ID, kinds={"bank", "income", "vat"}, attachments={"F1", "F2", "F3"}, added=[],
                     latest=[], requests=[], readbacks={}, started=[], jobs={}, sheets={}, answered=[], exported=[])
        CACHE.clear()
        HOOKS["pearnly_guides"] = ["pearnly_cowork.assistant.guided.recon.GUIDES"]
        self.docs, self.clock = {}, [0]
        self.now = {"session_id": SESSION, "mode": "chat", "company": "CO1", "ledger": EXPRESS_ID, "period": "2026-10",
                    "stopped_at": None}
        run._insert, run._locked, run._save, run._open_runs, run._conversation, run._announce = (
            self.insert, lambda name: self.docs.get(name), self.save, self.open_runs, lambda session_id: dict(self.now),
            lambda doc: None)

    def insert(self, values):
        self.clock[0] += 1
        doc = Doc(values, name=f"RUN{len(self.docs) + 1}", modified=datetime.datetime(2026, 11, 16, 10, 0, self.clock[0]))
        self.docs[doc.name] = doc
        return doc

    def save(self, doc):
        self.clock[0] += 1
        doc.modified = datetime.datetime(2026, 11, 16, 10, 0, self.clock[0])

    def open_runs(self, session_id):
        return [d.name for d in self.docs.values() if d.session_id == session_id and d.status in run.OPEN]

    @staticmethod
    def asked(found):
        return {q["key"]: q for q in found["card"]["questions"]}

    def bank_upload(self):
        name = run.start(SESSION, "recon")["run"]
        run.answer(name, {"kind": "bank"})
        run.answer(name, {"source": "bank:upload"})
        return name, run.answer(name, {"statement": ["F1"], "ledger": ["F2", "F3"]})

    def bank_done(self):
        name, found = self.bank_upload()
        done = run.confirm(name, found["version"])
        STATE["jobs"]["J1"] = {"state": "completed", "task": "CBR-1"}
        STATE["sheets"]["CBR-1"] = dict(BANK_SHEET)
        return name, run.view(self.docs[name])


class Declaration(Base):
    def test_one_menu_entry_for_people_with_any_reconciliation(self):
        self.assertEqual(registry.offered(), {"chat": ["recon"], "operator": []})
        STATE["kinds"] = set()
        self.assertEqual(registry.offered()["chat"], [])
        for step in recon.GUIDES[0]["steps"]:
            for key in ("options", "default", "check", "facts"):
                if key in step:
                    self.assertTrue(callable(_get_attr(step[key])), step[key])

    def test_the_kinds_offered_are_the_ones_this_person_may_use(self):
        STATE["kinds"] = {"bank", "vat"}
        question = self.asked(run.start(SESSION, "recon"))["kind"]
        self.assertEqual([row["code"] for row in question["options"]], ["bank", "vat"])
        self.assertEqual(question["options"][0]["name"], "cowork.nav.reconBank")


class Source(Base):
    def test_an_express_conversation_book_asks_where_the_ledger_comes_from(self):
        name = run.start(SESSION, "recon")["run"]
        found = run.answer(name, {"kind": "income"})
        self.assertEqual([row["code"] for row in self.asked(found)["source"]["options"]], ["income:readback", "income:upload"])

    def test_without_an_express_book_only_upload_is_possible_and_not_asked(self):
        STATE["current"] = MRERP_ID
        self.now["ledger"] = MRERP_ID
        name = run.start(SESSION, "recon")["run"]
        found = run.answer(name, {"kind": "bank"})
        self.assertEqual(list(self.asked(found)), ["statement", "ledger"])
        self.assertEqual([item["value"] for item in found["card"]["decided"] if item["key"] == "source"], ["bank:upload"])

    def test_each_kind_asks_only_its_own_two_sides(self):
        for kind, wanted in (("bank", ["statement", "ledger"]), ("income", ["ledger", "sales_vat"]),
                             ("vat", ["sales_vat", "invoices"])):
            name = run.start(SESSION, "recon")["run"]
            run.answer(name, {"kind": kind})
            self.assertEqual(list(self.asked(run.answer(name, {"source": f"{kind}:upload"}))), wanted, kind)

    def test_the_bank_statement_carries_the_same_hint_as_the_web_page_and_nothing_else_does(self):
        name = run.start(SESSION, "recon")["run"]
        run.answer(name, {"kind": "bank"})
        found = self.asked(run.answer(name, {"source": "bank:upload"}))
        self.assertEqual(found["statement"]["facts"], ["cowork.recon.bank.leftHint"])
        self.assertNotIn("facts", found["ledger"])


class Readback(Base):
    def to_month(self, kind="bank"):
        name = run.start(SESSION, "recon")["run"]
        run.answer(name, {"kind": kind})
        return name, run.answer(name, {"source": f"{kind}:readback"})

    def test_the_month_suggests_the_conversation_month(self):
        _name, found = self.to_month()
        self.assertEqual(self.asked(found)["period"]["preselected"], "2026-10")
        self.assertNotIn("readback", self.asked(found))

    def test_a_finished_readback_of_that_month_is_used_without_asking(self):
        STATE["readbacks"]["2026-10"] = {"readback": "RB1", "status": "ready", "account_set": "ABC 2026", "period": "2026-10"}
        name, _found = self.to_month()
        found = run.answer(name, {"period": "2026-10"})
        self.assertEqual(list(self.asked(found)), ["statement"])   # 总账取读回，只交银行账单
        self.assertEqual([item["value"] for item in found["card"]["decided"] if item["key"] == "readback"], ["RB1"])
        self.assertEqual(STATE["requests"], [])

    def test_without_one_the_computer_is_asked_to_read_once_and_the_page_waits(self):
        name, _found = self.to_month("income")
        found = run.answer(name, {"period": "2026-09"})
        question = self.asked(found)["readback"]
        self.assertEqual((question["options"], question["wait"], question["facts"]),
                         ([], True, ["Reading back the accounting books"]))
        self.assertEqual(STATE["requests"], [("CONN-E", "2026", "2026-09")])
        run.view(self.docs[name])
        self.assertEqual(len(STATE["requests"]), 1)   # 同一份在读：不再请读
        STATE["readbacks"]["2026-09"] = {"readback": "RB-2026-09", "status": "ready", "period": "2026-09"}
        done = run.view(self.docs[name])
        self.assertTrue(done["card"]["ready"])   # 收入对账两侧都取读回：读完就能确定

    def test_confirming_with_the_readback_side_sends_its_workbooks(self):
        STATE["readbacks"]["2026-10"] = {"readback": "RB1", "status": "ready"}
        name, _found = self.to_month("income")
        found = run.answer(name, {"period": "2026-10"})
        run.confirm(name, found["version"])
        kind, title, sides, _options = STATE["started"][0]
        self.assertEqual((kind, title), ("income", ""))
        self.assertEqual(sides, {"left": [("general_ledger.xlsx", b"RB1:general_ledger")],
                                 "right": [("sales_vat.xlsx", b"RB1:sales_vat")]})


class Files(Base):
    def test_a_file_not_in_this_conversation_is_refused(self):
        name = run.start(SESSION, "recon")["run"]
        run.answer(name, {"kind": "bank"})
        run.answer(name, {"source": "bank:upload"})
        found = run.answer(name, {"statement": ["F9"]})
        self.assertEqual(found["refused"], {"statement": "attachment_not_in_conversation"})

    def test_the_income_ledger_takes_one_file_and_the_bank_ledger_several(self):
        name = run.start(SESSION, "recon")["run"]
        run.answer(name, {"kind": "income"})
        run.answer(name, {"source": "income:upload"})
        self.assertEqual(run.answer(name, {"ledger": ["F1", "F2"]})["refused"], {"ledger": "reconciliation.files_required"})
        _name, found = self.bank_upload()
        self.assertTrue(found["card"]["ready"])


class Confirm(Base):
    def test_confirming_starts_one_reconciliation_like_the_web_page(self):
        name, found = self.bank_upload()
        done = run.confirm(name, found["version"])
        self.assertEqual(STATE["started"][0][2], {"left": [("F1.xlsx", b"xF1")], "right": [("F2.xlsx", b"xF2"), ("F3.xlsx", b"xF3")]})
        self.assertEqual(done["status"], run.DONE)
        run.confirm(name, found["version"])
        self.assertEqual(len(STATE["started"]), 1)

    def test_a_reconciliation_refused_by_the_service_goes_back_to_the_source(self):
        STATE["refuse"] = "reconciliation.files_required"
        name, found = self.bank_upload()
        back = run.confirm(name, found["version"])
        self.assertEqual((back["outcome"], back["card"]["returned"]["source"]["text"]),
                         (run.REFUSED, "reconciliation.files_required"))


class Result(Base):
    def test_while_running_the_page_waits(self):
        name, found = self.bank_upload()
        STATE["jobs"]["J1"] = {"state": "running"}
        self.assertEqual(run.confirm(name, found["version"])["result"], {"status": "queued", "wait": True})

    def test_a_finished_reconciliation_gives_its_questions_and_its_workbook_once(self):
        name, found = self.bank_done()
        result = found["result"]
        self.assertEqual((result["status"], result["task"], result["title"]), ("ready", "CBR-1", "KBank 10"))
        self.assertEqual([(q["key"], q["title"]) for q in result["questions"]], [("q1", "Which account?"), ("q2", "Leave 4100 out?")])
        self.assertEqual(result["questions"][0]["options"], [{"code": "1100", "name": "1100 (5)"}, {"code": "1110", "name": "1110 (1)"}])
        self.assertEqual(result["files"], [{"file": "F1", "name": "CBR-1.xlsx"}])
        run.view(self.docs[name])
        self.assertEqual(STATE["exported"], ["CBR-1"])

    def test_a_failed_reconciliation_gives_its_reason(self):
        name, found = self.bank_upload()
        STATE["jobs"]["J1"] = {"state": "failed", "error": "reconciliation.system_busy"}
        self.assertEqual(run.confirm(name, found["version"])["result"],
                         {"status": "failed", "reason_text": "reconciliation.system_busy"})


class Follow(Base):
    def test_an_answer_is_kept_as_a_preset_and_a_rerun_is_followed_by_its_new_job(self):
        name, found = self.bank_done()
        STATE["jobs"]["J9"] = {"state": "running"}
        after = run.follow(name, {"q1": "1100", "q2": "yes"}, found["version"])
        self.assertEqual(STATE["answered"], [("bank", "CBR-1", "gl_account", "1100", None),
                                             ("bank", "CBR-1", "excluded", "yes", "4100")])
        self.assertEqual(after["result"], {"status": "queued", "wait": True})
        self.assertEqual(self.docs[name].result, '{"status": "queued", "kind": "bank", "job": "J9"}')

    def test_an_answer_not_among_the_options_is_not_taken(self):
        name, found = self.bank_done()
        refused = run.follow(name, {"q1": "9999", "q2": "yes"}, found["version"])
        self.assertEqual(refused["refused"], {"questions": "not offered"})
        self.assertEqual(STATE["answered"], [])


if __name__ == "__main__":
    unittest.main()
