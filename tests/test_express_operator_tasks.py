"""实操任务（共用层 `express_operator.tasks`，主会话 10-27 审定）：只给创建人本人看、改；准备中才能改公司、月份；交单写进任务、
状态转等电脑空闲；收尾以产品回执为准推出状态；列表服务端分页；交出去还没收尾的不能删。

    python3 tests/test_express_operator_tasks.py
"""
import datetime
import importlib.util
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class _Row(dict):
    __getattr__ = dict.get

    def __setattr__(self, key, value):
        self[key] = value

    def set(self, key, value):
        self[key] = [_Row(v) for v in value] if key == "documents" else value


class _Throw(Exception):
    pass


class PermissionError_(_Throw):
    pass


class DoesNotExistError_(_Throw):
    pass


def _fake_frappe(state):
    frappe = types.ModuleType("frappe")
    frappe._dict = _Row
    frappe.PermissionError, frappe.DoesNotExistError = PermissionError_, DoesNotExistError_
    frappe.session = types.SimpleNamespace(user="a@example.com")
    frappe.conf = {"pearnly_product": "cowork"}

    def throw(message, exc=None):
        raise (exc or _Throw)(message)

    frappe.throw = throw
    frappe._ = lambda text: text

    def match(row, filters):
        return all(row.get(k) in v[1] if isinstance(v, list) and v[0] == "in" else row.get(k) == v for k, v in filters.items())

    class Doc(_Row):
        def __init__(self, data):
            super().__init__(data)
            self["flags"] = types.SimpleNamespace(ignore_permissions=False)
            self.set("documents", data.get("documents") or [])

        def insert(self):
            assert self.flags.ignore_permissions
            state["seq"] += 1
            self.update(name=f"t{state['seq']:03d}", owner=frappe.session.user, starred=self.get("starred") or 0,
                        creation=state["now"], modified=state["now"] + datetime.timedelta(seconds=state["seq"]))
            state["rows"].append(self)
            return self

        def save(self):
            assert self.flags.ignore_permissions
            state["seq"] += 1
            self["modified"] = state["now"] + datetime.timedelta(seconds=state["seq"])

    def get_doc(arg, name=None):
        if isinstance(arg, dict):
            return Doc(arg)
        return next(row for row in state["rows"] if row["name"] == name)

    def get_value(doctype, filters, field, for_update=False, as_dict=False):
        rows = state["sessions"] if doctype == "Pearnly Assistant Session" else state["rows"]
        found = [row for row in rows if match(row, filters)]
        if not found:
            return None
        return _Row({f: found[0].get(f) for f in field}) if isinstance(field, list) else found[0][field]

    def exists(doctype, filters):
        return any(match(row, filters) for row in state["sessions"])

    def set_value(doctype, name, values, value=None, update_modified=True):
        values = {values: value} if isinstance(values, str) else values
        rows = state["sessions"] if doctype == "Pearnly Assistant Session" else state["rows"]
        next(row for row in rows if row["name"] == name).update(values)

    def get_all(doctype, filters=None, fields=None, order_by="", limit_start=0, limit_page_length=20):
        if doctype == "Pearnly Express Operator Task Document":  # 子表：每行带 parent / parenttype
            rows = [_Row(parent=row["name"], parenttype="Pearnly Express Operator Task", **doc)
                    for row in state["rows"] for doc in row.get("documents") or []]
            return [_Row({f: row.get(f) for f in fields}) for row in rows if match(row, filters or {})]
        limit_page_length = limit_page_length or len(state["rows"])
        found = sorted((row for row in state["rows"] if match(row, filters or {})), key=lambda r: r["modified"],
                       reverse="desc" in order_by)
        return [_Row({f: row.get(f) for f in fields}) for row in found[limit_start:limit_start + limit_page_length]]

    def delete_doc(doctype, name, ignore_permissions=False, force=False):
        assert ignore_permissions
        state["rows"][:] = [row for row in state["rows"] if row["name"] != name]

    frappe.get_doc, frappe.get_all, frappe.delete_doc = get_doc, get_all, delete_doc
    frappe.publish_realtime = lambda event, message, user=None, after_commit=False: state["pushed"].append(
        (event, message, user, after_commit))
    frappe.db = types.SimpleNamespace(get_value=get_value, exists=exists, set_value=set_value)
    frappe.parse_json = lambda text: __import__("json").loads(text) if isinstance(text, str) else text
    frappe.as_json = lambda value: __import__("json").dumps(value, default=str)
    utils = types.ModuleType("frappe.utils")
    utils.now_datetime = lambda: state["now"]
    frappe.utils = utils
    return frappe, utils


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.state = {"rows": [], "sessions": [], "seq": 0, "now": datetime.datetime(2026, 10, 27, 20, 0), "pushed": [],
                      "fed": []}
        self.listed = set()  # 产品自己的待处理里此刻列着的单（products.pending_documents）
        self.submitted = []
        self.allowed = {"CO-1"}
        self.statuses = {}
        frappe, utils = _fake_frappe(self.state)
        products = types.ModuleType("pearnly_core.express_operator.products")
        products.allowed = lambda user, company: company in self.allowed
        products.pending_documents = lambda: set(self.listed)
        products.reason_text = lambda reason: ""  # 产品没声明 reason_text（同真模块：回空串）
        products.computer_words = lambda company, user: ""  # 本人有能写的电脑（同真模块：能写回空串）
        products.status = lambda company, ids: [{"doc_id": i, **self.statuses.get(i, {"state": "queued"})} for i in ids]
        products.submit = lambda company, ids, batch, computer, answers: self.submitted.append(
            (company, list(ids), batch, computer, answers)) or [{"doc_id": i, "status": "sent"} for i in ids]
        package = types.ModuleType("pearnly_core.express_operator")
        package.products = products
        contracts_spec = importlib.util.spec_from_file_location(
            "op_contracts", ROOT / "apps/pearnly_core/pearnly_core/express_operator/contracts.py")
        package.contracts = importlib.util.module_from_spec(contracts_spec)
        contracts_spec.loader.exec_module(package.contracts)  # 纯规则，无框架
        sessions = types.ModuleType("pearnly_core.assistant.sessions")
        sessions.SESSION = "Pearnly Assistant Session"

        def create_session():
            sid = f"sesn_{len(self.state['sessions']) + 1}"
            self.state["sessions"].append(_Row(name=f"s{sid}", session_id=sid, user=frappe.session.user, title=""))
            return {"id": sid}

        sessions.create = create_session

        def open_operator(task, scope="", ledger_id=""):
            # 统一对话框架：建任务即建会话（op_<任务号>，同 sessions.open_operator）
            sid = f"op_{task['name']}"
            self.state["sessions"].append(_Row(name=f"s{sid}", session_id=sid, user=task["owner"], operator_task=task["name"],
                                              title=task.get("title") or task.get("company_title") or task.get("company")))
            return sid

        sessions.open_operator = open_operator
        feed = types.ModuleType("pearnly_core.express_operator.feed")
        feed.stamp = lambda doc, before, now: None
        feed.stored_rows = lambda name: {}
        feed.publish = lambda doc: self.state["fed"].append((doc["name"], doc.get("session_id")))
        package.feed = feed
        sessions.delete = lambda sid: self.state["sessions"].__setitem__(
            slice(None), [row for row in self.state["sessions"] if row["session_id"] != sid])
        assistant = types.ModuleType("pearnly_core.assistant")
        assistant.sessions = sessions
        assistant.titles = types.SimpleNamespace(PLACEHOLDER="placeholder")
        self.saved = {k: sys.modules.get(k) for k in ("frappe", "frappe.utils", "pearnly_core", "pearnly_core.express_operator",
                                                       "pearnly_core.express_operator.products",
                                                       "pearnly_core.express_operator.contracts", "pearnly_core.assistant",
                                                       "pearnly_core.express_operator.feed",
                                                       "pearnly_core.assistant.sessions", "pearnly_core.express_operator.tasks")}
        sys.modules.update({"frappe": frappe, "frappe.utils": utils, "pearnly_core": types.ModuleType("pearnly_core"),
                            "pearnly_core.express_operator": package, "pearnly_core.express_operator.products": products,
                            "pearnly_core.express_operator.contracts": package.contracts,
                            "pearnly_core.express_operator.feed": feed,
                            "pearnly_core.assistant": assistant, "pearnly_core.assistant.sessions": sessions})
        spec = importlib.util.spec_from_file_location(
            "op_tasks", ROOT / "apps/pearnly_core/pearnly_core/express_operator/tasks.py")
        self.tasks = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.tasks)
        self.frappe = frappe
        self.package = package

    def tearDown(self):
        for key, value in self.saved.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value

    def test_only_the_creator_sees_and_changes_a_task_and_only_on_this_product(self):
        made = self.tasks.create("CO-1", "2026-10", "Siam")
        self.assertEqual((made["status"], made["period"], made["product"]), ("preparing", "2026-10", "cowork"))
        self.assertEqual([t["name"] for t in self.tasks.listing()["tasks"]], [made["name"]])
        self.frappe.session.user = "b@example.com"
        self.assertEqual(self.tasks.listing()["tasks"], [])
        with self.assertRaises(DoesNotExistError_):
            self.tasks.update(made["name"], title="mine")
        self.frappe.session.user = "a@example.com"
        self.frappe.conf["pearnly_product"] = "erp"
        with self.assertRaises(DoesNotExistError_):
            self.tasks.read(made["name"])  # 同一个人在 ERP 站点看不到 Cowork 的任务
        self.frappe.conf["pearnly_product"] = "cowork"
        with self.assertRaises(PermissionError_):
            self.tasks.create("CO-2", "2026-10")  # 产品不认的公司不能建

    def test_rename_and_pin_anytime_but_company_and_month_only_while_preparing(self):
        made = self.tasks.create("CO-1", "2026-10")
        out = self.tasks.update(made["name"], title=" 10 月进项 ", starred="1", period="2026-9")
        self.assertEqual((out["title"], out["starred"], out["period"]), ("10 月进项", True, "2026-09"))
        self.tasks.attach(made["name"], [{"doc_id": "h1", "status": "sent"}], {"h1": "expense"}, "", None)
        with self.assertRaises(_Throw):
            self.tasks.update(made["name"], period="2026-08")
        self.assertFalse(self.tasks.update(made["name"], starred="0")["starred"])

    def test_start_records_each_document_and_settle_follows_the_product(self):
        made = self.tasks.create("CO-1", "2026-10")
        out = self.tasks.attach(made["name"], [{"doc_id": "h1", "status": "sent"}, {"doc_id": "h2", "status": "queued"},
                                               {"doc_id": "h3", "status": "needs_choice", "reason": ""}],
                                {"h1": "purchase"}, "CCD-1", None)
        self.assertEqual(out["status"], "queued")
        self.assertEqual([(d["doc_id"], d["state"], d["reason"]) for d in out["documents"]],
                         [("h1", "queued", ""), ("h2", "queued", ""), ("h3", "not_done", "needs_choice")])
        with self.assertRaises(_Throw):
            self.tasks.delete(made["name"])  # 交出去还没收尾：不能删
        self.assertEqual(self.tasks.mark_running(made["name"])["status"], "running")
        self.statuses = {"h1": {"state": "saved", "docnum": "RR0000009", "ref": "INV-1", "party": "Siam"}}
        half = self.tasks.settle(made["name"])
        self.assertEqual(half["status"], "running")  # h2 还在队列：不收尾
        self.statuses["h2"] = {"state": "saved", "docnum": "RR0000010", "voided_docnum": "RR0000008"}
        done = self.tasks.settle(made["name"])
        self.assertEqual(done["status"], "attention")  # h3 交单时就没收：需要你处理
        self.assertEqual([(d["state"], d["docnum"], d["voided_docnum"]) for d in done["documents"]][:2],
                         [("saved", "RR0000009", ""), ("saved", "RR0000010", "RR0000008")])
        self.assertTrue(done["finished_at"])
        self.assertEqual(self.tasks.delete(made["name"]), {"deleted": made["name"]})

    def test_a_document_waiting_for_the_computer_waits_in_the_queue_and_goes_in_once_it_can(self):
        """线上 11-10（testcowork 代录 t4n5b25940）：点开始时电脑还没读到这份账套的科目表，产品回 waiting_for_cc，任务当场记
        「未完成」收尾成需要你处理，之后再没人交它。照「电脑关了就排队」：记成排队、原因是等电脑，任务等着；之后每次收尾
        （页面、CC 查状态、电脑读完账套资料后的刷新）先把这几张再交一次，交上了照常排队，还在等就继续等。"""
        made = self.tasks.create("CO-1", "2026-10")
        out = self.tasks.attach(made["name"], [{"doc_id": "h1", "status": "waiting_for_cc", "reason": "reading"},
                                               {"doc_id": "h2", "status": "sent"}], {}, "", None)
        self.assertEqual(out["status"], "queued")
        self.assertEqual([(d["doc_id"], d["state"], d["reason"]) for d in out["documents"]],
                         [("h1", "queued", "waiting_for_cc"), ("h2", "queued", "")])
        # 产品此刻对没推过的单会说「已取消」：等电脑的不拿这个结论，先再交一次
        self.statuses = {"h1": {"state": "cancelled"}, "h2": {"state": "saved", "docnum": "RR1"}}
        waiting = self.tasks.products.submit
        self.tasks.products.submit = lambda company, ids, batch, computer, answers: self.submitted.append(
            (company, list(ids), batch, computer, answers)) or [{"doc_id": i, "status": "waiting_for_cc", "reason": "offline"}
                                                                for i in ids]
        still = self.tasks.settle(made["name"])
        self.assertEqual(self.submitted[-1][1], ["h1"])
        self.assertEqual(still["status"], "queued")
        self.assertEqual([(d["state"], d["reason"]) for d in still["documents"]], [("queued", "waiting_for_cc"), ("saved", "")])
        self.tasks.products.submit = waiting  # 电脑读到了：交上
        moved = self.tasks.settle(made["name"])
        self.assertEqual(self.submitted[-1][1:3], (["h1"], {"id": made["name"], "total": 1, "start_at": 0, "task": made["name"]}))
        self.assertEqual([(d["state"], d["reason"]) for d in moved["documents"]][0], ("queued", ""))
        self.statuses["h1"] = {"state": "saved", "docnum": "RR2"}
        self.assertEqual(self.tasks.settle(made["name"])["status"], "finished")

    def test_a_queued_document_records_which_unknown_one_blocks_it_until_it_moves(self):
        # 同一份账套里一张结果不明的单持着写者锁、人还没核对：排着的这张记下是哪张（「单号 · 对方」），页面照它写；
        # 不再被挡、或已定下来，就清掉（ADR-021 11-02 修订）
        made = self.tasks.create("CO-1", "2026-10")
        self.tasks.attach(made["name"], [{"doc_id": "h2", "status": "sent"}], {}, "", None)
        self.statuses = {"h2": {"state": "queued", "blocked_by": {"doc_id": "h1", "ref": "INV-1", "party": "Siam"}}}
        self.assertEqual(self.tasks.settle(made["name"])["documents"][0]["blocked_by"], "INV-1 · Siam")
        self.statuses = {"h2": {"state": "queued"}}
        self.assertEqual(self.tasks.settle(made["name"])["documents"][0]["blocked_by"], "")
        self.statuses = {"h2": {"state": "saved", "docnum": "RR9", "blocked_by": {"ref": "x"}}}
        self.assertEqual(self.tasks.settle(made["name"])["documents"][0]["blocked_by"], "")

    def test_an_unknown_result_is_asked_again_until_someone_checks_it(self):
        # CC 重启打断、站点定成结果不明的单：人在 Express 核对之前一直「需要处理」，核对后再收尾就按核对的结论（10-29 测试机）
        made = self.tasks.create("CO-1", "2026-10")
        self.tasks.attach(made["name"], [{"doc_id": "h1", "status": "sent"}], {}, "", None)
        self.statuses = {"h1": {"state": "not_done", "reason": "push.result_unknown"}}
        self.assertEqual(self.tasks.settle(made["name"])["status"], "attention")
        self.statuses = {"h1": {"state": "saved", "docnum": "RR7"}}
        done = self.tasks.settle(made["name"])
        self.assertEqual((done["status"], done["documents"][0]["docnum"]), ("finished", "RR7"))
        self.statuses = {"h1": {"state": "not_done", "reason": "other"}}
        self.tasks.update(made["name"], title="x")
        self.assertEqual(self.tasks.settle(made["name"])["documents"][0]["state"], "saved")  # 定了的不再问

    def test_all_entered_finishes_and_a_cancelled_one_stops(self):
        first = self.tasks.create("CO-1", "2026-10")
        self.tasks.attach(first["name"], [{"doc_id": "h1", "status": "sent"}], {}, "", None)
        self.statuses = {"h1": {"state": "saved", "docnum": "RR1"}}
        self.assertEqual(self.tasks.settle(first["name"])["status"], "finished")
        second = self.tasks.create("CO-1", "2026-10")
        self.tasks.attach(second["name"], [{"doc_id": "h2", "status": "sent"}], {}, "", None)
        self.statuses = {"h2": {"state": "cancelled"}}
        self.assertEqual(self.tasks.settle(second["name"])["status"], "stopped")

    def test_the_list_pages_newest_first(self):
        names = [self.tasks.create("CO-1", "2026-10")["name"] for _ in range(3)]
        self.tasks.update(names[0], title="touched")  # 改过的排到最前
        page = self.tasks.listing(0, 2)
        self.assertEqual([t["name"] for t in page["tasks"]], [names[0], names[2]])
        self.assertEqual(page["next"], 2)
        self.assertEqual([t["name"] for t in self.tasks.listing(2, 2)["tasks"]], [names[1]])
        self.assertIsNone(self.tasks.listing(2, 2)["next"])


    def _started(self, title=""):
        made = self.tasks.create("CO-1", "2026-10", title)
        self.tasks.attach(made["name"], [{"doc_id": "h1", "status": "sent"}, {"doc_id": "h2", "status": "sent"},
                                         {"doc_id": "h3", "status": "needs_choice"}], {}, "CCD-1", None)
        return made["name"]

    def test_a_task_gets_one_conversation_kept_out_of_the_sidebar_and_its_note_names_the_task(self):
        name = self._started("<b>Siam</b> & Co")
        first = self.tasks.thread(name)
        self.assertEqual(self.tasks.thread(name), first)  # 第二次不再建
        self.assertEqual(len(self.state["sessions"]), 1)
        session = self.state["sessions"][0]
        self.assertEqual((session["operator_task"], session["title"]), (name, "<b>Siam</b> & Co"))
        self.assertEqual(self.tasks.read(name)["session_id"], first["session_id"])
        note = self.tasks.thread_note(name)
        self.assertIn(f'id="{name}"', note)
        self.assertIn("&lt;b&gt;Siam&lt;/b&gt; &amp; Co", note)  # 租户输入的公司名转义后才进系统消息
        self.frappe.session.user = "b@example.com"
        self.assertEqual(self.tasks.thread_note(name), "")  # 别人的任务不带说明
        with self.assertRaises(DoesNotExistError_):
            self.tasks.thread(name)

    def test_skip_and_redo_are_kept_only_for_documents_of_the_task_until_the_computer_takes_them(self):
        name = self._started()
        out = self.tasks.push_control(name, "skip", ["h2"])
        self.assertEqual(([c["doc_id"] for c in out["queued"]], out["computer"]), (["h2"], "CCD-1"))
        self.tasks.push_control(name, "retry", ["h3"])
        for bad in (("delete", ["h1"]), ("skip", ["x9"]), ("skip", [])):
            with self.assertRaises(_Throw):
                self.tasks.push_control(name, *bad)
        self.assertEqual([(c["action"], c["doc_id"]) for c in self.tasks.take_controls(name)], [("skip", "h2"), ("retry", "h3")])
        self.assertEqual(self.tasks.take_controls(name), [])  # 取走即清空
        draft = self.tasks.create("CO-1", "2026-11")
        with self.assertRaises(_Throw):
            self.tasks.push_control(draft["name"], "skip", ["h1"])  # 准备中没有可控的单
        self.allowed.clear()
        with self.assertRaises(PermissionError_):
            self.tasks.push_control(name, "skip", ["h1"])  # 公司权限收回后不再收

    def test_an_answer_resubmits_only_a_not_done_document_into_the_same_task(self):
        name = self._started()
        self.statuses = {"h1": {"state": "saved", "docnum": "RR1"}, "h2": {"state": "saved", "docnum": "RR2"}}
        self.assertEqual(self.tasks.settle(name)["status"], "attention")
        with self.assertRaises(_Throw):
            self.tasks.answer(name, "h1", [{"kind": "account", "subject": "x", "value": "1"}])  # 已入账的不再交
        choice = [{"kind": "supplier", "subject": "Siam", "value": "S001", "label": "Siam Co"}]
        out = self.tasks.answer(name, "h3", choice)
        self.assertEqual(self.submitted, [("CO-1", ["h3"], {"id": name, "total": 1, "start_at": 0, "task": name}, "CCD-1", {"h3": choice})])
        self.assertEqual((out["status"], out["documents"][2]["state"], out["finished_at"]), ("queued", "queued", ""))

    def test_starting_names_every_document_even_those_held_on_the_spot(self):
        """11-08 生产：当场停下等人选的单一直没有单号与对方，任务卡只能写内部编号。交单时就向产品要一次单号与对方。"""
        self.statuses = {f"h{i}": {"state": "not_done", "ref": f"IV69/0075{i}", "party": "Big C"} for i in (1, 2, 3)}
        name = self._started()
        rows = {row["doc_id"]: (row.get("ref"), row.get("party")) for row in self.tasks.read(name)["documents"]}
        self.assertEqual(rows, {f"h{i}": (f"IV69/0075{i}", "Big C") for i in (1, 2, 3)})

    def test_one_answer_sends_back_every_document_waiting_for_a_choice(self):
        """11-08 生产事故：同一客户在 24 张上各停一次、要答 24 遍。答了其中一张：这张带答案交回（产品记下），同一任务里别的
        「要人选」的一起交回（不重带答案，照记下的用），别的原因停下的不动。"""
        name = self._started()
        self.statuses = {"h1": {"state": "not_done", "reason": "choice_needed:party"},
                         "h2": {"state": "not_done", "reason": "choice_needed:party,item"},
                         "h3": {"state": "not_done", "reason": "[OPERATOR_STOPPED] stopped"}}
        self.tasks.settle(name)
        self.submitted.clear()
        choice = [{"kind": "party", "subject": "sales|0107536000633|00000", "value": "BIGC-002", "label": "Big C"}]
        out = self.tasks.answer(name, "h1", choice)
        self.assertEqual([(c[1], c[4]) for c in self.submitted], [(["h1"], {"h1": choice}), (["h2"], {})])
        self.assertEqual([d["state"] for d in out["documents"]], ["queued", "queued", "not_done"])

    def test_a_document_already_in_express_is_skipped_not_held(self):
        """用户 11-09：同一张票已写进 Express 的默认跳过——产品交单回 written（原因带单号），任务里记「已跳过」，不是要人处理的。"""
        made = self.tasks.create("CO-1", "2026-10")
        out = self.tasks.attach(made["name"], [{"doc_id": "h1", "status": "sent"},
                                               {"doc_id": "h2", "status": "written", "reason": "IV69/00753"}], {}, "", None)
        self.assertEqual([(d["state"], d["reason"]) for d in out["documents"]], [("queued", ""), ("skipped", "IV69/00753")])

    def test_one_card_answers_several_documents_at_once(self):
        """11-08：同一题的几张合成一张卡，一次交来各张的答案（answer_many）；别的原因停下的不动，不认识的单整批拒。"""
        name = self._started()
        self.statuses = {"h1": {"state": "not_done", "reason": "choice_needed:party"},
                         "h2": {"state": "not_done", "reason": "choice_needed:party"},
                         "h3": {"state": "not_done", "reason": "[OPERATOR_STOPPED] stopped"}}
        self.tasks.settle(name)
        self.submitted.clear()
        choice = [{"kind": "party", "subject": "sales|0107536000633|00000", "value": "BIGC-002", "label": "Big C"}]
        out = self.tasks.answer_many(name, {"h1": choice, "h2": choice})
        self.assertEqual([(c[1], c[4]) for c in self.submitted], [(["h1", "h2"], {"h1": choice, "h2": choice})])
        self.assertEqual([d["state"] for d in out["documents"]], ["queued", "queued", "not_done"])
        with self.assertRaises(_Throw):
            self.tasks.answer_many(name, {"zz": choice})

    def test_a_retry_resubmits_a_document_the_computer_skipped_or_held_with_no_answers(self):
        # 单张「重试」走作答同一条路（answers 空）：CC 停止跳过的单在站点上是「未完成」（原因 [OPERATOR_STOPPED]）；任务还记着
        # 排队的先问产品一次（CC 回执了、任务还没收尾）；结果不明的不重交（要人先在 Express 核对）
        name = self._started()
        self.statuses = {"h1": {"state": "not_done", "reason": "[OPERATOR_STOPPED] stopped"}}
        out = self.tasks.answer(name, "h1", [])
        self.assertEqual(self.submitted, [("CO-1", ["h1"], {"id": name, "total": 1, "start_at": 0, "task": name}, "CCD-1", {"h1": []})])
        self.assertEqual(out["documents"][0]["state"], "queued")
        self.statuses = {"h2": {"state": "not_done", "reason": "push.result_unknown"}}
        with self.assertRaises(_Throw):
            self.tasks.answer(name, "h2", [])
        self.statuses = {"h2": {"state": "saved", "docnum": "RR2"}}
        with self.assertRaises(_Throw):
            self.tasks.answer(name, "h2", [])                     # 已入账的不再交
        self.assertEqual(len(self.submitted), 1)

    def _stopped_batch(self):
        """CC 登录失败整批停：h1 卡住（登录失败）、h2 停止跳过、h3 交单时就要人选（与整批停无关）。"""
        name = self._started()
        self.statuses = {"h1": {"state": "not_done", "reason": "[OPERATOR_LOGIN_FAILED] login window"},
                         "h2": {"state": "not_done", "reason": "[OPERATOR_STOPPED] stopped"}}
        return name

    def test_resume_hands_back_only_the_documents_the_batch_stop_left_undone(self):
        name = self._stopped_batch()
        out = self.tasks.resume(name)                              # 没收尾也先以站点为准收尾一次
        self.assertEqual(self.submitted, [("CO-1", ["h1", "h2"], {"id": name, "total": 2, "start_at": 0, "task": name}, "CCD-1", {})])
        self.assertEqual([(d["doc_id"], d["state"]) for d in out["documents"]],
                         [("h1", "queued"), ("h2", "queued"), ("h3", "not_done")])
        self.assertEqual((out["status"], out["finished_at"]), ("queued", ""))
        self.statuses = {}
        with self.assertRaises(_Throw):
            self.tasks.resume(name)                                # 已交回、还在队列：不再交

    def test_resume_is_only_for_the_owner_of_a_stopped_task_with_something_to_hand_back(self):
        # 测试替身不回滚：每种拒绝各用一个新任务（真站点 frappe.throw 整个请求回滚）
        name = self._stopped_batch()
        self.statuses["h2"] = {"state": "queued"}
        with self.assertRaises(_Throw):
            self.tasks.resume(name)                                # 还有单在路上：任务没停下
        name = self._stopped_batch()
        self.statuses["h1"] = {"state": "saved", "docnum": "RR1"}
        self.statuses["h2"] = {"state": "not_done", "reason": "[OPERATOR_PARTY_MISSING] x"}
        with self.assertRaises(_Throw):
            self.tasks.resume(name)                                # 没有因整批停没录成的单（要人处理的单走单张重试）
        name = self._stopped_batch()
        self.frappe.session.user = "b@example.com"
        with self.assertRaises(DoesNotExistError_):
            self.tasks.resume(name)                                # 别人的任务
        self.frappe.session.user = "a@example.com"
        self.allowed.clear()
        with self.assertRaises(PermissionError_):
            self.tasks.resume(name)                                # 公司权限收回
        self.allowed.add("CO-1")
        draft = self.tasks.create("CO-1", "2026-11")
        with self.assertRaises(_Throw):
            self.tasks.resume(draft["name"])                       # 准备中没有可继续的
        self.assertEqual(self.submitted, [])
        self.statuses = {"h1": {"state": "not_done", "reason": "[OPERATOR_NETWORK_DOWN] x"},
                         "h2": {"state": "not_done", "reason": "[OPERATOR_PARTY_MISSING] x"}}
        self.assertEqual(self.tasks.resume(name)["documents"][0]["state"], "queued")
        self.assertEqual(self.submitted[-1][1], ["h1"])            # 只交整批停留下的那张
        self.assertEqual(self.tasks.RESUMABLE, self.package.contracts.BATCH_STOPPERS | {"stopped"})

    def test_the_tools_number_documents_in_task_order_and_refuse_ones_outside_it(self):
        import json

        sys.modules["pearnly_core.express_operator"].tasks = self.tasks
        spec = importlib.util.spec_from_file_location(
            "op_tools", ROOT / "apps/pearnly_core/pearnly_core/express_operator/tools.py")
        tools = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tools)
        name = self._started()
        self.statuses = {"h1": {"state": "saved", "docnum": "RR1"}}
        seen = json.loads(tools.status({"task": name}))
        self.assertEqual([(d["n"], d["doc_id"], d["state"]) for d in seen["documents"]],
                         [(1, "h1", "saved"), (2, "h2", "queued"), (3, "h3", "not_done")])
        self.assertEqual(seen["counts"], {"saved": 1, "queued": 1, "not_done": 1})
        done = json.loads(tools.control({"task": name, "action": "skip", "documents": ["2"]}))
        self.assertEqual([c["doc_id"] for c in done["queued"]], ["h2"])  # 「第 2 张」对上任务里的第二张
        with self.assertRaises(_Throw):
            tools.control({"task": name, "action": "retry", "documents": ["9"]})
        self.assertEqual({t["name"] for t in tools.TOOLS}, set(tools.STEP_NAMES))
        # 没录成的原因交给模型的是产品给的那句话（测试机1 11-12：模型把「choice_needed:account_set」原样说给用户），码另放
        # reason_code；产品没声明 reason_text、给不出话的照原样
        # （第 3 张交单时停在要人选，任务记的原因是码 needs_choice；ERP 记的是 choice_needed:account_set 这类）
        self.tasks.products.reason_text = lambda reason: "Choose before writing" if reason == "needs_choice" else ""
        seen = json.loads(tools.status({"task": name}))
        third = seen["documents"][2]
        self.assertEqual((third["reason"], third["reason_code"]), ("Choose before writing", "needs_choice"))
        self.assertNotIn("reason_code", seen["documents"][0])
        self.tasks.products.reason_text = lambda reason: ""
        self.assertEqual(json.loads(tools.status({"task": name}))["documents"][2]["reason"], "needs_choice")

    def test_before_the_task_starts_the_status_lists_what_it_will_enter_and_the_dropped_files(self):
        # 11-16 H3：准备中的任务里 CC 只看得到「还没录」，不知道放进来的票已识别，叫用户去采购页上传。准备中的状态带上待录的单
        # （产品 sources，同写入计划那份清单）与对话里放进来的每一份此刻识别到哪（uploads.read）
        import json

        sys.modules["pearnly_core.express_operator"].tasks = self.tasks
        asked = []
        self.tasks.products.sources = lambda company, period, offset, limit: asked.append((company, period, offset, limit)) or {
            "documents": [{"doc_id": "I:LI-1", "kind": "purchase", "group": "purchase", "date": "2026-10-03", "party": "Siam",
                           "ref": "INV-1", "total": "107.00", "stopped": ""},
                          {"doc_id": "P:PI-9", "kind": "purchase", "group": "skip", "ref": "X"}],
            "total": 2}
        uploads = types.ModuleType("pearnly_core.express_operator.uploads")
        uploads.read = lambda name: {"task": name, "uploads": [
            {"filename": "h3.jpg", "state": "done", "batch": "B1",
             "documents": [{"doc_id": "I:LI-1", "ref": "INV-1", "party": "Siam", "date": "2026-10-03", "waiting": True}]}]}
        sys.modules["pearnly_core.express_operator.uploads"] = uploads
        self.package.uploads = uploads
        self.addCleanup(sys.modules.pop, "pearnly_core.express_operator.uploads", None)
        spec = importlib.util.spec_from_file_location(
            "op_tools", ROOT / "apps/pearnly_core/pearnly_core/express_operator/tools.py")
        tools = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tools)
        made = self.tasks.create("CO-1", "2026-10")
        seen = json.loads(tools.status({"task": made["name"]}))
        self.assertEqual(asked, [("CO-1", "2026-10", 0, tools.WAITING_SHOWN)])
        self.assertEqual(seen["waiting"], [{"doc_id": "I:LI-1", "kind": "purchase", "date": "2026-10-03", "party": "Siam",
                                            "ref": "INV-1", "total": "107.00"}])  # 移到「不入账」的不列
        self.assertEqual(seen["waiting_total"], 2)
        self.assertEqual(seen["uploads"], [{"filename": "h3.jpg", "state": "done", "documents": [
            {"doc_id": "I:LI-1", "ref": "INV-1", "party": "Siam", "date": "2026-10-03"}]}])
        self.assertIn("never send the user elsewhere to upload", seen["next"])
        self.assertNotIn("no_computer", seen)  # 本人有能写的电脑
        # 本人一台能写的电脑都没有：给模型单据页同一句，照话告诉用户，不说「已交给电脑」（11-16 H 组）
        self.tasks.products.computer_words = lambda company, user: "CC Desktop not connected"
        self.assertEqual(json.loads(tools.status({"task": made["name"]}))["no_computer"], "CC Desktop not connected")
        self.tasks.products.computer_words = lambda company, user: ""
        # 开始以后不再带这两项（任务里的单以 documents 为准）
        name = self._started()
        self.assertNotIn("waiting", json.loads(tools.status({"task": name})))

    def test_attention_lists_tasks_that_need_you_counting_each_document_once(self):
        # 主会话 11-03：「待处理」里的代录一组只算「需要你处理」的任务；任务里没录成的单都已在产品自己的待处理里列着的不另算
        first = self.tasks.create("CO-1", "2026-10")
        self.tasks.attach(first["name"], [{"doc_id": "h1", "status": "needs_choice"}], {}, "", None)
        second = self.tasks.create("CO-1", "2026-09")
        self.tasks.attach(second["name"], [{"doc_id": "h2", "status": "needs_choice"}, {"doc_id": "h3", "status": "sent"}], {}, "", None)
        self.tasks.create("CO-1", "2026-08")                                           # 准备中：不算
        out = self.tasks.attention()
        self.assertEqual(out["total"], 1)                                              # second 还有排着的：等电脑空闲，不算
        self.assertEqual(([t["name"] for t in out["tasks"]], out["companies"]), ([first["name"]], {"CO-1": 1}))
        self.assertNotIn("documents", out["tasks"][0])
        self.listed = {"h1"}                                                           # h1 已在产品的待处理里：同一张单只算一次
        self.assertEqual(self.tasks.attention(), {"total": 0, "companies": {}, "tasks": []})
        self.frappe.session.user = "b@example.com"
        self.listed = set()
        self.assertEqual(self.tasks.attention()["total"], 0)                           # 只给本人
        self.frappe.session.user = "a@example.com"
        self.frappe.conf["pearnly_product"] = "erp"
        self.assertEqual(self.tasks.attention()["total"], 0)                           # 只在本站产品

    def test_every_change_is_announced_to_the_owner_after_commit(self):
        made = self.tasks.create("CO-1", "2026-10")
        self.tasks.update(made["name"], title="进项")
        self.tasks.attach(made["name"], [{"doc_id": "h1", "status": "needs_choice"}], {}, "", None)
        self.tasks.delete(made["name"])
        pushed = self.state["pushed"]
        self.assertEqual({(event, user, after) for event, _m, user, after in pushed},
                         {(self.tasks.EVENT, "a@example.com", True)})
        self.assertEqual([m["op"] for _e, m, _u, _a in pushed], ["upsert", "upsert", "upsert", "delete"])
        self.assertEqual([m["task"]["status"] for _e, m, _u, _a in pushed[:3]], ["preparing", "preparing", "attention"])
        self.assertNotIn("documents", pushed[2][1]["task"])
        self.assertEqual(pushed[3][1], {"op": "delete", "name": made["name"]})
        # 每次保存（不含删除）同时把代录会话里新出现的事件推给会话主人（feed.publish，统一对话框架 14.2）
        self.assertEqual(self.state["fed"], [(made["name"], f"op_{made['name']}")] * 3)

    def test_deleting_a_task_deletes_its_conversation(self):
        name = self._started()
        self.tasks.thread(name)
        self.statuses = {d: {"state": "saved"} for d in ("h1", "h2")}
        self.tasks.settle(name)
        self.tasks.delete(name)
        self.assertEqual(self.state["sessions"], [])


if __name__ == "__main__":
    unittest.main()
