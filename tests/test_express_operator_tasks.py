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
        return all(row.get(k) == v for k, v in filters.items())

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

    def set_value(doctype, name, values, update_modified=True):
        next(row for row in state["sessions"] if row["name"] == name).update(values)

    def get_all(doctype, filters=None, fields=None, order_by="", limit_start=0, limit_page_length=20):
        found = sorted((row for row in state["rows"] if match(row, filters or {})), key=lambda r: r["modified"],
                       reverse="desc" in order_by)
        return [_Row({f: row.get(f) for f in fields}) for row in found[limit_start:limit_start + limit_page_length]]

    def delete_doc(doctype, name, ignore_permissions=False, force=False):
        assert ignore_permissions
        state["rows"][:] = [row for row in state["rows"] if row["name"] != name]

    frappe.get_doc, frappe.get_all, frappe.delete_doc = get_doc, get_all, delete_doc
    frappe.db = types.SimpleNamespace(get_value=get_value, exists=exists, set_value=set_value)
    frappe.parse_json = lambda text: __import__("json").loads(text) if isinstance(text, str) else text
    frappe.as_json = lambda value: __import__("json").dumps(value, default=str)
    utils = types.ModuleType("frappe.utils")
    utils.now_datetime = lambda: state["now"]
    frappe.utils = utils
    return frappe, utils


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.state = {"rows": [], "sessions": [], "seq": 0, "now": datetime.datetime(2026, 10, 27, 20, 0)}
        self.submitted = []
        self.allowed = {"CO-1"}
        self.statuses = {}
        frappe, utils = _fake_frappe(self.state)
        products = types.ModuleType("pearnly_core.express_operator.products")
        products.allowed = lambda user, company: company in self.allowed
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
        sessions.delete = lambda sid: self.state["sessions"].__setitem__(
            slice(None), [row for row in self.state["sessions"] if row["session_id"] != sid])
        assistant = types.ModuleType("pearnly_core.assistant")
        assistant.sessions = sessions
        self.saved = {k: sys.modules.get(k) for k in ("frappe", "frappe.utils", "pearnly_core", "pearnly_core.express_operator",
                                                       "pearnly_core.express_operator.products",
                                                       "pearnly_core.express_operator.contracts", "pearnly_core.assistant",
                                                       "pearnly_core.assistant.sessions", "pearnly_core.express_operator.tasks")}
        sys.modules.update({"frappe": frappe, "frappe.utils": utils, "pearnly_core": types.ModuleType("pearnly_core"),
                            "pearnly_core.express_operator": package, "pearnly_core.express_operator.products": products,
                            "pearnly_core.express_operator.contracts": package.contracts,
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
        self.assertEqual(self.submitted, [("CO-1", ["h3"], {"id": name, "total": 1, "start_at": 0}, "CCD-1", {"h3": choice})])
        self.assertEqual((out["status"], out["documents"][2]["state"], out["finished_at"]), ("queued", "queued", ""))

    def test_a_retry_resubmits_a_document_the_computer_skipped_or_held_with_no_answers(self):
        # 单张「重试」走作答同一条路（answers 空）：CC 停止跳过的单在站点上是「未完成」（原因 [OPERATOR_STOPPED]）；任务还记着
        # 排队的先问产品一次（CC 回执了、任务还没收尾）；结果不明的不重交（要人先在 Express 核对）
        name = self._started()
        self.statuses = {"h1": {"state": "not_done", "reason": "[OPERATOR_STOPPED] stopped"}}
        out = self.tasks.answer(name, "h1", [])
        self.assertEqual(self.submitted, [("CO-1", ["h1"], {"id": name, "total": 1, "start_at": 0}, "CCD-1", {"h1": []})])
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
        self.assertEqual(self.submitted, [("CO-1", ["h1", "h2"], {"id": name, "total": 2, "start_at": 0}, "CCD-1", {})])
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

    def test_deleting_a_task_deletes_its_conversation(self):
        name = self._started()
        self.tasks.thread(name)
        self.statuses = {d: {"state": "saved"} for d in ("h1", "h2")}
        self.tasks.settle(name)
        self.tasks.delete(name)
        self.assertEqual(self.state["sessions"], [])


if __name__ == "__main__":
    unittest.main()
