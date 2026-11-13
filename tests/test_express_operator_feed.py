"""代录事件源（共用层 `express_operator.feed`，统一对话框架，评估 2026-11-04 第 14 节，主会话 11-04 审定）：代录任务就是一段
标准对话会话，任务记录投影成统一对话事件。这里守住：

- 每种状态的事件（R3：任务状态 × 单据状态 × 原因级别全排列，对照 14.1 / 14.10）；
- 重放一致（同一份记录两次投影逐条相同）、增量（锚点时间之后的，加上不按时间筛的几类）；
- 时间（交进队列 / 有结论的时刻只按与库里的差别记，交回队列留着上一轮的结论时刻，页面据此收口上一轮的卡片）；
- 权限（只推会话主人，别人读不到这个任务的事件、报告）；
- 停止（任务控制 stop 只在等电脑 / 进行中可记）、回执后的只读入口只排还可能变的任务；
- 两个补丁（旧任务补建会话、手排顺序换成会话编号）幂等；
- 业务调用不变：一个任务从建到收尾，`products.submit` / `products.status` 的调用次数与参数与改造前逐字相同。

    python3 tests/test_express_operator_feed.py
"""
import copy
import datetime
import importlib.util
import itertools
import json
import sys
import types
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "apps/pearnly_core/pearnly_core"
TZ = "Asia/Bangkok"
T0 = datetime.datetime(2026, 11, 4, 9, 0)


class _Row(dict):
    __getattr__ = dict.get

    def __setattr__(self, key, value):
        self[key] = value


class _Throw(Exception):
    pass


class PermissionError_(_Throw):
    pass


class DoesNotExistError_(_Throw):
    pass


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


TASK = "Pearnly Express Operator Task"
DOC = "Pearnly Express Operator Task Document"
SESSION = "Pearnly Assistant Session"


def _env(test, tasks_path=CORE / "express_operator/tasks.py"):
    """一套假 frappe（库里存的是保存时的快照，读出来是副本，同 Frappe）+ 产品桩，装上共用层的契约、形状、任务、事件源。
    回 (tasks, feed, state)；``state["calls"]`` 记下对产品 submit / status 的每一次调用。"""
    state = {"db": {}, "sessions": [], "seq": 0, "child": 0, "now": T0, "pushed": [], "enqueued": [], "calls": [],
             "statuses": {}, "user": "a@example.com", "allowed": {"CO-1"}, "logged": []}
    frappe = types.ModuleType("frappe")
    frappe._dict = _Row
    frappe.PermissionError, frappe.DoesNotExistError = PermissionError_, DoesNotExistError_
    frappe.session = types.SimpleNamespace(user="a@example.com")
    frappe.conf = {"pearnly_product": "cowork"}
    frappe.local = types.SimpleNamespace()
    frappe.flags = types.SimpleNamespace()
    frappe.response = {}

    def throw(message, exc=None):
        raise (exc or _Throw)(message)

    frappe.throw = throw
    frappe._ = lambda text: text
    frappe.whitelist = lambda **kw: (lambda fn: fn)
    frappe.log_error = lambda **kw: state["logged"].append(kw.get("title"))

    def match(row, filters):
        for key, wanted in (filters or {}).items():
            value = row.get(key)
            if isinstance(wanted, list):
                op, arg = wanted
                if op == "in" and value not in arg:
                    return False
                if op == "is" and bool(value) != (arg == "set"):
                    return False
            elif value != wanted:
                return False
        return True

    class Doc(_Row):
        def __init__(self, data):
            super().__init__(data)
            self["flags"] = types.SimpleNamespace(ignore_permissions=False)
            self.set("documents", data.get("documents") or [])
            self.set("uploads", data.get("uploads") or [])

        def set(self, key, value):
            self[key] = [v if isinstance(v, _Row) else _Row(v) for v in value] if key in ("documents", "uploads") else value

        def append(self, key, value):
            row = _Row(value)
            self[key].append(row)
            return row

        def _store(self):
            for key in ("documents", "uploads"):
                for index, row in enumerate(self[key], start=1):
                    if not row.get("name"):
                        state["child"] += 1
                        row["name"] = f"row{state['child']:03d}"
                    row["idx"] = index
            snapshot = copy.deepcopy({k: v for k, v in self.items() if k != "flags"})
            state["db"][self["name"]] = snapshot

        def insert(self):
            assert self.flags.ignore_permissions
            state["seq"] += 1
            self.update(name=f"tk{state['seq']:04d}", owner=frappe.session.user, starred=self.get("starred") or 0,
                        creation=state["now"], modified=state["now"])
            self["_doc_before_save"] = None
            self._store()
            return self

        def save(self):
            assert self.flags.ignore_permissions
            self["_doc_before_save"] = _load_doc(self["name"])
            self["modified"] = state["now"]
            self._store()

        def get_doc_before_save(self):
            return self.get("_doc_before_save")

    def _load_doc(name):
        stored = state["db"].get(name)
        if stored is None:
            raise DoesNotExistError_(name)
        doc = Doc({k: v for k, v in copy.deepcopy(stored).items() if k not in ("documents", "uploads", "_doc_before_save")})
        doc.set("documents", copy.deepcopy(stored.get("documents") or []))
        doc.set("uploads", copy.deepcopy(stored.get("uploads") or []))
        return doc

    def get_doc(arg, name=None):
        return Doc(arg) if isinstance(arg, dict) else _load_doc(name)

    def get_value(doctype, filters, field, for_update=False, as_dict=False):
        if doctype == SESSION:
            rows = state["sessions"]
        else:
            rows = list(state["db"].values())
            if isinstance(filters, str):
                filters = {"name": filters}
        found = [row for row in rows if match(row, filters)]
        if not found:
            return None
        return _Row({f: found[0].get(f) for f in field}) if isinstance(field, list) else found[0].get(field)

    def exists(doctype, filters):
        rows = state["sessions"] if doctype == SESSION else list(state["db"].values())
        filters = {"name": filters} if isinstance(filters, str) else filters
        return any(match(row, filters) for row in rows)

    def set_value(doctype, name, values, value=None, update_modified=True):
        values = {values: value} if isinstance(values, str) else values
        if doctype == SESSION:
            rows = state["sessions"]
        else:
            rows = list(state["db"].values())
        filters = name if isinstance(name, dict) else {"name": name}
        for row in rows:
            if match(row, filters):
                row.update(values)

    def get_all(doctype, filters=None, fields=None, order_by="", limit_start=0, limit_page_length=20, pluck=None,
                distinct=False, **_kw):
        if doctype == DOC:
            rows = [_Row(parent=task["name"], parenttype=TASK, **doc) for task in state["db"].values()
                    for doc in task.get("documents") or []]
        elif doctype == SESSION:
            rows = state["sessions"]
        else:
            rows = sorted(state["db"].values(), key=lambda r: r["modified"], reverse="desc" in (order_by or ""))
        found = [row for row in rows if match(row, filters)]
        if pluck:
            values = [row.get(pluck) for row in found]
            return list(dict.fromkeys(values)) if distinct else values
        return [_Row({f: row.get(f) for f in fields}) for row in found]

    def delete_doc(doctype, name, ignore_permissions=False, force=False):
        state["db"].pop(name, None)

    frappe.get_doc, frappe.get_all, frappe.delete_doc = get_doc, get_all, delete_doc
    frappe.publish_realtime = lambda event, message, user=None, after_commit=False: state["pushed"].append(
        (event, message, user, after_commit))
    frappe.enqueue = lambda method, **kw: state["enqueued"].append((method, kw))
    frappe.set_user = lambda user: setattr(frappe.session, "user", user)
    frappe.db = types.SimpleNamespace(get_value=get_value, exists=exists, set_value=set_value,
                                      commit=lambda: state.setdefault("commits", []).append(1))
    frappe.parse_json = lambda text: json.loads(text) if isinstance(text, str) else text
    frappe.as_json = lambda value: json.dumps(value, default=str)
    utils = types.ModuleType("frappe.utils")
    utils.now_datetime = lambda: state["now"]
    utils.get_system_timezone = lambda: TZ
    frappe.utils = utils

    products = types.ModuleType("pearnly_core.express_operator.products")
    products.allowed = lambda user, company: company in state["allowed"]
    products.enabled = lambda: True
    products.pending_documents = lambda: set()

    def status(company, ids):
        state["calls"].append(("status", company, list(ids)))
        return [{"doc_id": i, **state["statuses"].get(i, {"state": "queued"})} for i in ids]

    def submit(company, ids, batch, computer, answers):
        state["calls"].append(("submit", company, list(ids), dict(batch), computer, copy.deepcopy(answers)))
        return [{"doc_id": i, "status": "sent"} for i in ids]

    products.status, products.submit = status, submit
    products.waiting_on = lambda company, user: state.get("waiting_on", {}).get((company, user), "")
    products.uploads = lambda company, period, batches: dict(state.get("ocr") or {})

    sessions = types.ModuleType("pearnly_core.assistant.sessions")
    sessions.SESSION = SESSION

    def open_operator(task, scope="", ledger_id=""):
        sid = f"op_{task['name']}"
        state["sessions"].append(_Row(name=f"s{sid}", session_id=sid, user=task["owner"], operator_task=task["name"]))
        return sid

    sessions.open_operator = open_operator
    sessions.delete = lambda sid: state["sessions"].__setitem__(
        slice(None), [row for row in state["sessions"] if row["session_id"] != sid])

    names = ("frappe", "frappe.utils", "pearnly_core", "pearnly_core.express_operator", "pearnly_core.assistant",
             "pearnly_core.assistant.contracts", "pearnly_core.assistant.sessions", "pearnly_core.express_operator.contracts",
             "pearnly_core.express_operator.products", "pearnly_core.express_operator.shapes",
             "pearnly_core.express_operator.tasks", "pearnly_core.express_operator.feed")
    saved = {k: sys.modules.get(k) for k in names}

    def restore():
        for key, value in saved.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value

    test.addCleanup(restore)
    package = types.ModuleType("pearnly_core.express_operator")
    assistant = types.ModuleType("pearnly_core.assistant")
    sys.modules.update({"frappe": frappe, "frappe.utils": utils, "pearnly_core": types.ModuleType("pearnly_core"),
                        "pearnly_core.express_operator": package, "pearnly_core.assistant": assistant,
                        "pearnly_core.assistant.sessions": sessions, "pearnly_core.express_operator.products": products})
    assistant.sessions = sessions
    package.products = products
    assistant.contracts = _load("pearnly_core.assistant.contracts", CORE / "assistant/contracts.py")
    package.contracts = _load("pearnly_core.express_operator.contracts", CORE / "express_operator/contracts.py")
    package.shapes = _load("pearnly_core.express_operator.shapes", CORE / "express_operator/shapes.py")
    package.tasks = _load("pearnly_core.express_operator.tasks", tasks_path)
    package.feed = _load("pearnly_core.express_operator.feed", CORE / "express_operator/feed.py")
    state["frappe"] = frappe
    return package.tasks, package.feed, state


# ── 投影用的任务记录（纯数据）───────────────────────────────────────────────────
def _task(status="preparing", docs=(), uploads=(), **extra):
    started = None if status == "preparing" else T0 + datetime.timedelta(minutes=5)
    return _Row(name="tk0001", owner="a@example.com", company="CO-1", company_title="Siam", period="2026-10",
                status=status, creation=T0, started_at=started, finished_at=extra.pop("finished_at", None),
                message_at=extra.pop("message_at", T0),
                computer="CCD-1", session_id="op_tk0001", documents=[_Row(d) for d in docs],
                uploads=[_Row(u) for u in uploads], **extra)


def _doc(n, state, reason="", **extra):
    queued = T0 + datetime.timedelta(minutes=5)
    done = queued + datetime.timedelta(minutes=n) if state not in ("pending", "queued") else None
    return {"name": f"row{n:03d}", "doc_id": f"H-{n}", "doc_group": "purchase", "state": state, "reason": reason,
            "ref": f"IV{n}", "party": "Siam Supply", "queued_at": queued, "done_at": done, **extra}


# 原因级别：自动、自动报告、要人、整批停（登录）、因停止跳过、结果不明、产品自己的码
REASONS = {"auto": "[OPERATOR_SCREEN_LOCKED] locked", "auto_reported": "[OPERATOR_DUPLICATE_REF] dup",
           "human": "[OPERATOR_PARTY_MISSING] no party", "batch": "[OPERATOR_LOGIN_MISSING] no login",
           "stopped": "[OPERATOR_STOPPED] stopped", "unknown": "push.result_unknown", "product": "needs_choice"}
DOC_STATES = ("pending", "queued", "saved", "not_done", "cancelled", "skipped")


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tasks, self.feed, self.state = _env(self)
        self.chat = sys.modules["pearnly_core.assistant.contracts"]

    def project(self, task, uploads=None):
        return self.feed.project(task, uploads, TZ)

    def check_shape(self, events):
        ids = [event["id"] for event in events]
        self.assertEqual(len(ids), len(set(ids)), ids)
        for event in events:
            self.assertRegex(event["id"], self.chat.OPAQUE_ID.pattern)
            self.assertTrue(event["id"].startswith("pop_tk0001_") or event["id"].startswith("ptoolrun_pop_tk0001_"),
                            event["id"])
            self.assertTrue(event["processed_at"].endswith("Z"))
        self.assertEqual([e["processed_at"] for e in events], sorted(e["processed_at"] for e in events))
        json.dumps(events)  # 能原样推给页面
        statuses = [e for e in events if e["type"].startswith("session.status_")]
        self.assertEqual(len(statuses), 1)
        self.assertIs(events[-1]["type"].startswith("session.status_") or events[-1]["type"] == "pearnly.progress", True)
        uses = {e["id"]: e for e in events if e["type"] in ("agent.tool_use", "agent.custom_tool_use")}
        for event in events:
            if event["type"] in ("agent.tool_result", "pearnly.progress") and event.get("tool_use_id"):
                self.assertIn(event["tool_use_id"], uses)
            if event["type"] == "agent.tool_result":
                self.assertEqual(uses[event["tool_use_id"]]["type"], "agent.tool_use")  # 卡片的结果走 custom_tool_result
        for name in {e["name"] for e in uses.values()}:
            self.assertIn(name, self.feed.TOOL_NAMES)
        return {e["id"]: e for e in events}, statuses[0]

    def test_every_task_state_document_state_and_reason_level_has_its_events(self):
        # R3：任务状态 × 单据状态 × 原因级别全排列
        for status, doc_state, (level, reason) in itertools.product(
                self.tasks.STATUSES, DOC_STATES, REASONS.items()):
            if status == "preparing":
                docs = []
            else:
                docs = [_doc(1, doc_state, reason if doc_state == "not_done" else ""), _doc(2, "saved")]
            with self.subTest(status=status, doc=doc_state, level=level):
                events = self.project(_task(status, docs))
                by_id, run = self.check_shape(events)
                start = "pop_tk0001_start"
                self.assertEqual(by_id[start]["name"], "operator_start")
                if status == "preparing":
                    # 11-11 一句话下令：开始前卡片只展示，不停着等人点（开始走写入计划），模型空闲、输入框可打字
                    self.assertEqual(run["stop_reason"], {"type": "end_turn"})
                    self.assertNotIn(f"ptoolrun_{start}_awaiting_approval", by_id)
                    self.assertNotIn(start + "_r", by_id)
                    continue
                self.assertEqual(by_id[start + "_r"]["type"], "user.custom_tool_result")
                self.assertIn(f"ptoolrun_{start}_done", by_id)
                self.assertEqual(json.loads(by_id["pop_tk0001_sources_r"]["content"][0]["text"]),
                                 {"total": 2, "groups": {"purchase": 2}})
                step = "pop_tk0001_drow001"
                self.assertEqual((by_id[step]["name"], by_id[step]["input"]["n"], by_id[step]["input"]["doc_id"]),
                                 ("operator_document", 1, "H-1"))
                results = [e for e in events if e.get("tool_use_id") == step and e["type"] == "agent.tool_result"]
                progress = [e for e in events if e.get("tool_use_id") == step and e["type"] == "pearnly.progress"]
                cards = [e for e in events if e["type"] == "agent.custom_tool_use" and e["id"] != start]
                if doc_state in ("pending", "queued"):
                    self.assertEqual((len(results), [p["label"] for p in progress]), (0, ["queued"]))
                    self.assertEqual(cards, [])
                else:
                    self.assertEqual((len(results), progress), (1, []))
                    value = json.loads(results[0]["content"][0]["text"])
                    self.assertEqual(value["state"], doc_state)
                    self.assertEqual(results[0]["is_error"], doc_state == "not_done")
                    if doc_state == "not_done":
                        code = {"auto": "screen_locked", "auto_reported": "duplicate_ref", "human": "party_missing",
                                "batch": "login_missing", "stopped": "stopped", "unknown": "push.result_unknown",
                                "product": "needs_choice"}[level]
                        self.assertEqual((value["reason"], value["result_unknown"]), (code, level == "unknown"))
                        self.assertEqual(value["level"], {"auto": "auto", "auto_reported": "auto_reported"}.get(level, "human"))
                if doc_state == "not_done" and level in ("batch", "stopped"):
                    if status in ("attention", "stopped"):
                        self.assertEqual([c["name"] for c in cards], ["operator_resume"])
                        self.assertEqual((cards[0]["input"]["reason"], cards[0]["input"]["doc_ids"]),
                                         ("login_missing" if level == "batch" else "stopped", ["H-1"]))
                    else:
                        self.assertEqual(cards, [])
                elif doc_state == "not_done":
                    self.assertEqual([c["name"] for c in cards], ["operator_choose"])
                    self.assertEqual(cards[0]["input"]["doc_id"], "H-1")
                for card in cards:
                    self.assertIn(f"ptoolrun_{card['id']}_awaiting_approval", by_id)
                if status in ("queued", "running"):
                    self.assertEqual(run["type"], "session.status_running")
                    label = "waiting_computer" if status == "queued" else "entering"
                    whole = [e for e in events if e["type"] == "pearnly.progress" and not e["tool_use_id"]]
                    self.assertEqual([(p["label"], p["total"]) for p in whole], [(label, 2)])
                    self.assertEqual(self.feed.run_state(events)["status"], "running")
                else:
                    want = [c["id"] for c in cards]
                    self.assertEqual(run["stop_reason"], {"type": "requires_action", "event_ids": want} if want
                                     else {"type": "end_turn"})
                    self.assertEqual(self.feed.run_state(events)["status"], "requires_action" if want else "idle")
                    ends = [e for e in events if e["type"] == "agent.message"]
                    if doc_state in ("pending", "queued"):
                        self.assertEqual(ends, [])
                    else:
                        self.assertEqual(ends[0]["operator"]["kind"], status)
                        self.assertEqual(ends[0]["operator"]["report"]["url"],
                                         "/api/method/pearnly_core.express_operator.feed.report?name=tk0001")

    def test_a_document_waiting_for_the_computer_says_so_on_its_step(self):
        """线上 11-10：等电脑（科目表还没读到 / 电脑不在线）的单排着，步骤行写明在等 CC 桌面版；交上以后换回「排队」（编号不同，
        页面认最新的那条）。"""
        def progress(rows):
            events = self.project(_task("queued", rows))
            return [(e["id"], e["label"]) for e in events if e.get("type") == "pearnly.progress" and "_d" in e["id"]]
        waiting = progress([_doc(1, "queued", "waiting_for_cc")])
        self.assertEqual([label for _id, label in waiting], ["waiting_for_cc"])
        sent = progress([_doc(1, "queued")])
        self.assertEqual([label for _id, label in sent], ["queued"])
        self.assertNotEqual(waiting[0][0], sent[0][0])

    def test_a_task_waiting_for_the_computer_names_the_owners_computer(self):
        # A 线 11-12：任务排着等电脑时，整段进度带上在等的本人电脑名称（产品钩子 waiting_on，按任务主人；同事的电脑不算），
        # 页面写「等待电脑 · 名称」；在录、产品没给时不带
        self.state["waiting_on"] = {("CO-1", "a@example.com"): "PC-ME"}
        whole = lambda status: [e for e in self.project(_task(status, [_doc(1, "queued")]))
                                if e["type"] == "pearnly.progress" and not e["tool_use_id"]]
        self.assertEqual([(e["label"], e["detail"]) for e in whole("queued")], [("waiting_computer", "PC-ME")])
        self.assertEqual([e["detail"] for e in whole("running")], [""])
        self.state["waiting_on"] = {}
        self.assertEqual([e["detail"] for e in whole("queued")], [""])

    def test_an_expired_task_shows_its_cards_as_expired_and_waits_for_nothing(self):
        # 卡片有效期（设计 2.3 + 用户 11-13）：任务已失效 → 卡片标 expired（页面「已失效」、按钮点不动），会话不再等人点
        with mock.patch.object(self.tasks, "expired", lambda task: True):
            events = self.project(_task("attention", [_doc(1, "not_done", REASONS["human"])]))
        runs = [e for e in events if e["type"] == "pearnly.tool_run"]
        statuses = {e["status"] for e in runs}
        self.assertIn("expired", statuses)
        self.assertNotIn("awaiting_approval", statuses)
        self.assertNotEqual(self.feed.run_state(events)["status"], "requires_action")

    def test_the_same_record_projects_to_the_same_events_twice(self):
        task = _task("attention", [_doc(1, "saved", docnum="HP01"), _doc(2, "not_done", REASONS["batch"]),
                                   _doc(3, "not_done", REASONS["human"]), _doc(4, "queued", blocked_by="IV9 · X")],
                     uploads=[{"name": "up1", "request_key": "k1", "filename": "a.pdf", "size": 10, "batch": "OCR-1",
                               "uploaded_at": T0 + datetime.timedelta(minutes=1)}])
        states = {"up1": {"state": "reading", "pages_done": 2, "page_count": 5}}
        first, again = self.project(task, states), self.project(copy.deepcopy(task), copy.deepcopy(states))
        self.assertEqual(first, again)
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(again, sort_keys=True))

    def test_the_start_card_follows_the_users_first_message(self):
        """11-08 生产：资料与开始前卡片锚在建任务那一刻，排在用户第一句之前；11-09 发布前流程：单多时卡片很高，用户的话和回复被
        挤出视口；锚成「建任务 + 2 秒」时卡片是未来的时刻，页面先收到未来的「等你点开始」，后到的「本轮结束」被当成旧的丢掉，
        状态行停在「正在执行」。改：第一句发出前不出这两张卡；发出后锚在第一句（与文件）之后，不用未来的时刻；整段重放再垫在
        引擎记下的第一句之后。"""
        ids = lambda events: [e["id"] for e in events]
        fresh = self.project(_task("preparing", message_at=None))
        self.assertNotIn("pop_tk0001_start", ids(fresh))
        self.assertNotIn("pop_tk0001_sources", ids(fresh))
        self.assertEqual(self.feed.run_state(fresh)["status"], "idle")
        said = T0 + datetime.timedelta(seconds=7)
        events = self.project(_task("preparing", message_at=said))
        start = next(e for e in events if e["id"] == "pop_tk0001_start")
        self.assertGreater(start["processed_at"], self.feed._iso(said, TZ))
        self.assertLess(start["processed_at"], self.feed._iso(said + datetime.timedelta(seconds=1), TZ))
        self.assertEqual(self.feed.run_state(events)["awaiting_approval"], [])  # 只展示，不等人点（11-11）
        self.assertEqual(self.feed.run_state(events)["status"], "idle")
        up = {"name": "up1", "request_key": "k1", "filename": "a.pdf", "size": 10, "batch": "OCR-1",
              "uploaded_at": said + datetime.timedelta(seconds=1)}
        events = self.project(_task("preparing", uploads=[up], message_at=said))
        order = ids(events)
        self.assertLess(order.index("pop_tk0001_uup1"), order.index("pop_tk0001_start"))
        self.assertLess(order.index("pop_tk0001_uup1"), order.index("pop_tk0001_sources"))
        self.assertEqual(events, self.project(_task("preparing", uploads=[up], message_at=said)))
        engine = said + datetime.timedelta(seconds=3)  # 引擎记下的第一句比本站记的晚
        replay = self.feed.project(_task("preparing", message_at=said), None, TZ, engine)
        start = next(e for e in replay if e["id"] == "pop_tk0001_start")
        self.assertGreater(start["processed_at"], self.feed._iso(engine, TZ))

    def test_the_start_card_keeps_its_own_line_after_start(self):
        """11-08 生产：点开始后没收的 24 张原地回填进开始前卡片，同一批单在这里、各自的卡、结尾各出一遍。改：交单结论只写张数，
        没录成的只在它自己那一步出现。"""
        held = [_doc(n, "not_done", "choice_needed:party", done_at=T0 + datetime.timedelta(minutes=5)) for n in (1, 2)]
        answer = next(e for e in self.project(_task("attention", held))
                      if e["type"] == "user.custom_tool_result" and e.get("custom_tool_use_id") == "pop_tk0001_start")
        body = json.loads(answer["content"][0]["text"])
        self.assertEqual((body.get("not_sent"), body.get("held")), ([], 2))

    def test_upload_steps_follow_the_recognition_state(self):
        up = {"name": "up1", "request_key": "k1", "filename": "a.pdf", "size": 10, "batch": "OCR-1", "source": "f1",
              "uploaded_at": T0 + datetime.timedelta(minutes=1)}
        task = _task("preparing", uploads=[up])
        use = "pop_tk0001_uup1"
        bare = {e["id"]: e for e in self.project(task)}
        self.assertEqual((bare[use]["name"], bare[use]["input"]["filename"]), ("operator_upload", "a.pdf"))
        self.assertFalse([e for e in bare.values() if e.get("tool_use_id") == use])  # 推送时不读识别状态
        reading = self.project(task, {"up1": {"state": "reading", "pages_done": 3, "page_count": 12}})
        progress = [e for e in reading if e.get("tool_use_id") == use]
        self.assertEqual([(p["type"], p["label"], p["done"], p["total"]) for p in progress],
                         [("pearnly.progress", "reading_pages", 3, 12)])
        done = self.project(task, {"up1": {"state": "done", "page_count": 2,
                                           "documents": [{"doc_id": "H-1", "ref": "IV1"}]}})
        result = next(e for e in done if e.get("tool_use_id") == use)
        self.assertEqual((result["id"], result["is_error"], json.loads(result["content"][0]["text"])["documents"][0]["doc_id"]),
                         (use + "_rdone", False, "H-1"))
        failed = next(e for e in self.project(task, {"up1": {"state": "failed", "code": "unavailable"}})
                      if e.get("tool_use_id") == use)
        self.assertEqual((failed["id"], failed["is_error"]), (use + "_rfailed", True))
        # 页面「一步的状态 = processed_at 最新的那条」：结果晚于进度
        self.assertGreater(result["processed_at"], progress[0]["processed_at"])

    def test_increments_keep_what_came_after_the_anchor_and_the_few_kinds_without_times(self):
        task = _task("running", [_doc(1, "saved"), _doc(2, "saved"), _doc(9, "queued")],
                     uploads=[{"name": "up1", "batch": "OCR-1", "uploaded_at": T0}])
        events = self.project(task, {"up1": {"state": "done", "page_count": 1, "documents": []}})
        anchor = datetime.datetime(2026, 11, 4, 2, 14, tzinfo=datetime.timezone.utc)  # 曼谷 09:14 之后（第 2 张 09:12 已过）
        later = self.feed.since(events, anchor, "pop_tk0001_u", slack=datetime.timedelta(0))
        ids = {e["id"] for e in later}
        self.assertNotIn("pop_tk0001_drow001", ids)
        self.assertFalse(any(i.startswith("pop_tk0001_drow001_r") for i in ids))
        self.assertTrue({"pop_tk0001_uup1", "pop_tk0001_uup1_rdone"} <= ids)  # 上传各步：识别状态没有时间，一律带
        self.assertTrue(any(e["type"] == "session.status_running" for e in later))
        self.assertTrue(any(e["type"] == "pearnly.progress" and not e["tool_use_id"] for e in later))
        self.assertEqual(self.feed.since(events, anchor - datetime.timedelta(hours=1), "pop_tk0001_u"), events)

    def test_a_requeued_document_closes_its_card_and_shows_progress_newer_than_the_old_result(self):
        first = _doc(1, "not_done", REASONS["human"])
        before = self.project(_task("attention", [first]))
        card = next(e for e in before if e["type"] == "agent.custom_tool_use" and e["name"] == "operator_choose")
        again = dict(first, state="queued", reason="", queued_at=first["done_at"] + datetime.timedelta(minutes=3))
        after = {e["id"]: e for e in self.project(_task("queued", [again]))}
        self.assertEqual(after[card["id"] + "_r"]["custom_tool_use_id"], card["id"])
        self.assertIn(f"ptoolrun_{card['id']}_done", after)
        old = next(e for e in before if e["type"] == "agent.tool_result" and e["tool_use_id"] == "pop_tk0001_drow001")
        fresh = [e for e in after.values() if e["type"] == "pearnly.progress" and e["tool_use_id"] == "pop_tk0001_drow001"]
        self.assertGreater(fresh[0]["processed_at"], old["processed_at"])

    def test_a_resumed_batch_closes_the_resume_card(self):
        stuck = [_doc(1, "not_done", REASONS["batch"]), _doc(2, "not_done", REASONS["stopped"])]
        before = self.project(_task("stopped", stuck))
        card = next(e for e in before if e["type"] == "agent.custom_tool_use" and e["name"] == "operator_resume")
        self.assertEqual((card["name"], card["input"]["reason"], card["input"]["doc_ids"]),
                         ("operator_resume", "login_missing", ["H-1", "H-2"]))
        moved = [dict(d, state="queued", reason="", queued_at=d["done_at"] + datetime.timedelta(minutes=9)) for d in stuck]
        after = {e["id"]: e for e in self.project(_task("queued", moved))}
        self.assertEqual(after[card["id"] + "_r"]["type"], "user.custom_tool_result")

    def test_old_tasks_without_times_still_project_in_order(self):
        # R5：旧任务的子表没有时间：按开始时刻加序号排
        docs = [dict(_doc(n, "saved"), queued_at=None, done_at=None) for n in (1, 2, 3)]
        events = self.project(_task("finished", docs, finished_at=T0 + datetime.timedelta(hours=1)))
        self.check_shape(events)
        steps = [e["input"]["n"] for e in events if e["type"] == "agent.tool_use" and e["name"] == "operator_document"]
        self.assertEqual(steps, [1, 2, 3])


class DetailTests(unittest.TestCase):
    """AI 接手的结论完整到卡片（11-05 真机：feed 只留原因码、单据行截 140，卡片只剩「无法识别的 Express 画面」）：CC 回执
    ``[OPERATOR_X] ai: <结论> · <步骤>: <问题>`` 整段记在单据行，单据结果、停下等人的卡片、整批停的卡片都带 ``detail``；
    旧版 CC 开头的本机截图路径不给页面；产品自己的原因码没有说明。"""

    def setUp(self):
        self.tasks, self.feed, self.state = _env(self)

    def stopped(self, reason):
        made = self.tasks.create("CO-1", "2026-10", "Siam")
        self.tasks.attach(made["name"], [{"doc_id": "H-1", "status": "sent"}], {}, "CCD-1", None)
        self.state["statuses"] = {"H-1": {"state": "not_done", "reason": reason}}
        self.tasks.settle(made["name"])
        task = self.state["db"][made["name"]]
        return task, self.feed.project(task, None, TZ)

    def test_the_whole_ai_conclusion_reaches_the_document_result_and_the_card(self):
        conclusion = "ai: 未能打开「RR - ซื้อเงินเชื่อ」单据窗口，已停止操作。\n" + "已尝试按 Alt 打开菜单。" * 120 + " · form_open: 画面不对"
        self.assertGreater(len(conclusion), 1400)
        task, events = self.stopped("[OPERATOR_UNKNOWN_SCREEN] " + conclusion)
        self.assertEqual(task["documents"][0]["reason"], "[OPERATOR_UNKNOWN_SCREEN] " + conclusion)
        card = next(e for e in events if e.get("name") == self.feed.CHOOSE)
        self.assertEqual((card["input"]["reason"], card["input"]["detail"]), ("unknown_screen", conclusion))
        result = next(e for e in events if e["type"] == "agent.tool_result" and "_rnot_done_" in e["id"])
        self.assertEqual(json.loads(result["content"][0]["text"])["detail"], conclusion)

    def test_an_old_computer_s_screenshot_path_is_not_shown_and_product_codes_have_no_detail(self):
        old = "[OPERATOR_UNKNOWN_SCREEN] C:\\Users\\skin3\\AppData\\Local\\CC\\operator\\b1\\need-human.png · form_open: ai: 没打开"
        self.assertEqual(self.feed.reason_detail(old), "form_open: ai: 没打开")
        self.assertEqual(self.feed.reason_detail("[OPERATOR_LOGIN_MISSING] no login"), "no login")
        for code in ("needs_choice", "push.result_unknown", ""):
            self.assertEqual(self.feed.reason_detail(code), "")
        _task, events = self.stopped("[OPERATOR_LOGIN_MISSING] " + "登录" * 400)
        card = next(e for e in events if e.get("name") == self.feed.RESUME)
        self.assertEqual(card["input"]["detail"], "登录" * 400)


class StampTests(unittest.TestCase):
    def setUp(self):
        self.tasks, self.feed, self.state = _env(self)

    def test_times_follow_what_changed_against_the_stored_rows(self):
        now = T0 + datetime.timedelta(minutes=10)
        doc = _task("queued", [])
        doc.documents = [_Row(doc_id="H-1", state="queued"), _Row(doc_id="H-2", state="not_done", reason="needs_choice")]
        self.feed.stamp(doc, {}, now)
        self.assertEqual([(r.queued_at, r.done_at) for r in doc.documents], [(now, None), (now, now)])
        doc.documents[0].name, doc.documents[1].name = "a", "b"
        stored = {"a": _Row(state="queued", reason="", docnum=""), "b": _Row(state="not_done", reason="needs_choice")}
        later = now + datetime.timedelta(minutes=5)
        doc.documents[0].state, doc.documents[0].docnum = "saved", "HP01"
        self.feed.stamp(doc, stored, later)
        self.assertEqual((doc.documents[0].done_at, doc.documents[1].done_at), (later, now))  # 没变的不动
        stored = {"a": _Row(state="saved", reason="", docnum="HP01"), "b": _Row(state="not_done", reason="needs_choice")}
        doc.documents[1].state, doc.documents[1].reason = "queued", ""  # 交回队列：新的排队时刻，上一轮的结论时刻留着
        again = later + datetime.timedelta(minutes=1)
        self.feed.stamp(doc, stored, again)
        self.assertEqual((doc.documents[1].queued_at, doc.documents[1].done_at), (again, now))
        stored["b"] = _Row(state="queued", reason="")
        doc.documents[1].state = "saved"
        end = again + datetime.timedelta(minutes=1)
        self.feed.stamp(doc, stored, end)
        self.assertEqual(doc.documents[1].done_at, end)
        # 结论变了（结果不明核对成已入账）也记
        stored["b"] = _Row(state="not_done", reason="push.result_unknown")
        doc.documents[1].state, doc.documents[1].done_at = "saved", now
        self.feed.stamp(doc, stored, end)
        self.assertEqual(doc.documents[1].done_at, end)


class LifecycleTests(unittest.TestCase):
    """一个任务从建到收尾：业务调用不变；保存时只推会话主人、只推新出现的事件；停止；回执入口；报告；权限。"""

    def setUp(self):
        self.tasks, self.feed, self.state = _env(self)

    def tick(self, minutes=1):
        self.state["now"] = self.state["now"] + datetime.timedelta(minutes=minutes)

    def run_task(self, tasks):
        state = self.state
        made = tasks.create("CO-1", "2026-10", "Siam")
        name = made["name"]
        self.tick()
        tasks.attach(name, [{"doc_id": "H-1", "status": "sent"}, {"doc_id": "H-2", "status": "sent"},
                            {"doc_id": "H-3", "status": "needs_choice"}], {"H-1": "purchase"}, "CCD-1", None)
        self.tick()
        tasks.mark_running(name)
        self.tick()
        state["statuses"] = {"H-1": {"state": "saved", "docnum": "HP01"},
                             "H-2": {"state": "not_done", "reason": "[OPERATOR_LOGIN_MISSING] no login"}}
        tasks.settle(name)
        self.tick()
        tasks.answer(name, "H-3", [{"kind": "party", "subject": "S", "value": "new", "label": "S"}])
        self.tick()
        state["statuses"] = {"H-3": {"state": "saved", "docnum": "HP03"}}
        tasks.settle(name)
        self.tick()
        tasks.resume(name)
        self.tick()
        state["statuses"] = {"H-2": {"state": "saved", "docnum": "HP02"}}
        tasks.settle(name)
        return name

    def test_a_task_from_start_to_finish_calls_the_product_exactly_as_before(self):
        name = self.run_task(self.tasks)
        self.assertEqual(self.tasks.read(name)["status"], "finished")
        # 改造前（3d46f2ae0 的 tasks.py）同一过程逐字记下的调用；改造只加时间与事件，不改交单与回执
        self.assertEqual(self.state["calls"], [
            # 11-08 生产事故起：交单时先问一次单号与对方（当场停下的单原来一直没有，任务卡只能写内部编号），只读，不交单
            ("status", "CO-1", ["H-1", "H-2", "H-3"]),
            ("status", "CO-1", ["H-1", "H-2"]),
            # 批次信息带任务号（A 线 11-12：产品按任务会话的本对话账套写），其余逐字不变
            ("submit", "CO-1", ["H-3"], {"id": name, "total": 1, "start_at": 0, "task": name}, "CCD-1",
             {"H-3": [{"kind": "party", "subject": "S", "value": "new", "label": "S"}]}),
            ("status", "CO-1", ["H-3"]),
            ("submit", "CO-1", ["H-2"], {"id": name, "total": 1, "start_at": 0, "task": name}, "CCD-1", {}),
            ("status", "CO-1", ["H-2"]),
        ])

    def test_every_save_pushes_only_new_events_to_the_owner_of_the_conversation(self):
        name = self.run_task(self.tasks)
        chat = sys.modules["pearnly_core.assistant.contracts"]
        pushed = [(m, user, after) for event, m, user, after in self.state["pushed"] if event == chat.REALTIME_EVENT]
        self.assertTrue(pushed)
        self.assertEqual({(m["session_id"], user, after) for m, user, after in pushed}, {(f"op_{name}", "a@example.com", True)})
        ids = [m["event"]["id"] for m, _u, _a in pushed]
        import collections
        twice = [i for i, n in collections.Counter(ids).items() if n > 1]
        # 只推这次新出现的：同一条只在它消失后又出现时再推一次（整批停的「继续」卡片在别的单重交、任务回到等电脑空闲时
        # 不在投影里，那几张录完又回来；页面按编号去重）
        self.assertEqual(sorted(twice), sorted([i for i in twice if f"pop_{name}_resume_" in i]))
        self.assertLessEqual(len(twice), 2)
        # 推过的事件 ⊆ 最后的整段投影 + 中途的（等人的卡片、进度）：最终整段里的单据结果都推过
        final = self.feed.project(self.state["frappe"].get_doc(TASK, name), None, TZ)
        results = {e["id"] for e in final if e["type"] == "agent.tool_result" and "_drow" in e["id"]}
        self.assertTrue(results <= set(ids))
        self.assertTrue(any(i.startswith(f"pop_{name}_resume_") for i in ids))
        self.assertTrue(any(i.endswith("_r") and f"pop_{name}_resume_" in i for i in ids))  # 继续后那张卡收口

    def test_stop_is_kept_only_while_the_task_is_queued_or_running(self):
        name = self.tasks.create("CO-1", "2026-10")["name"]
        with self.assertRaises(_Throw):
            self.tasks.push_control(name, "stop", [])  # 准备中没有可停的
        self.tasks.attach(name, [{"doc_id": "H-1", "status": "sent"}], {}, "CCD-1", None)
        out = self.tasks.push_control(name, "stop", [])
        self.assertEqual(([(c["action"], c["doc_id"]) for c in out["queued"]], out["computer"]), ([("stop", "")], "CCD-1"))
        self.assertEqual([c["action"] for c in self.tasks.take_controls(name)], ["stop"])
        with self.assertRaises(_Throw):
            self.tasks.push_control(name, "delete", ["H-1"])
        self.assertNotIn("stop", self.tasks.CONTROL_ACTIONS)  # 给模型的工具照旧只有跳过 / 重做

    def test_receipts_queue_one_refresh_per_task_that_can_still_change(self):
        name = self.tasks.create("CO-1", "2026-10")["name"]
        self.tasks.attach(name, [{"doc_id": "H-1", "status": "sent"}], {}, "CCD-1", None)
        done = self.tasks.create("CO-1", "2026-10")["name"]
        self.tasks.attach(done, [{"doc_id": "H-9", "status": "sent"}], {}, "CCD-1", None)
        self.state["statuses"] = {"H-9": {"state": "saved"}}
        self.tasks.settle(done)
        self.feed.receipt("H-1")
        self.feed.receipt(["H-9", "nope"])
        self.assertEqual([(m, kw["task"], kw["enqueue_after_commit"]) for m, kw in self.state["enqueued"]],
                         [(self.feed.RECEIPT_JOB, name, True)])
        # 后台以任务主人身份收尾（同页面的 task_settle）
        self.state["frappe"].session.user = "Administrator"
        self.state["statuses"] = {"H-1": {"state": "saved"}}
        self.feed.refresh(name)
        self.assertEqual((self.state["frappe"].session.user, self.tasks.read(name)["status"]), ("a@example.com", "finished"))

    def test_only_the_owner_reads_the_task_events_and_report(self):
        name = self.tasks.create("CO-1", "2026-10")["name"]
        row = _Row(operator_task=name, user="a@example.com")
        self.assertEqual(self.feed.events(row), [])  # 第一句还没发出：还没有卡片
        self.assertTrue(self.tasks.message_sent(name))
        self.assertFalse(self.tasks.message_sent(name))  # 只记第一句
        self.assertTrue(self.feed.events(row))
        self.state["frappe"].session.user = "b@example.com"
        self.assertEqual(self.feed.events(_Row(operator_task=name, user="b@example.com")), [])
        with self.assertRaises(DoesNotExistError_):
            self.feed.report(name)

    def test_the_report_has_the_same_columns_as_the_page_export(self):
        name = self.run_task(self.tasks)
        frappe = self.state["frappe"]
        doc = frappe.get_doc(TASK, name)
        doc.documents[1].update(state="not_done", reason="[OPERATOR_PARTY_MISSING] x", voided_docnum="HP77")
        rows = self.feed.report_rows(doc, lambda key: key)
        self.assertEqual(rows[0], [f"operator.report.column.{k}" for k in ("ref", "party", "total", "status", "number",
                                                                          "reason", "time")])
        self.assertEqual(rows[1][3:6], ["operator.report.state.saved", "HP01", "operator.report.match"])
        self.assertEqual(rows[2][3], "operator.report.state.notDone")
        self.assertEqual(rows[2][5], "operator.report.reason.party_missing · operator.report.voided")
        self.feed.report(name)
        body = frappe.response["filecontent"].decode("utf-8")
        self.assertTrue(body.startswith("﻿"))
        self.assertEqual((frappe.response["type"], frappe.response["filename"]), ("download", "Siam 2026-10.csv"))
        self.assertEqual(len(body.strip().split("\r\n")), 4)
        tsv = (CORE / "i18n/messages.tsv").read_text(encoding="utf-8")
        keys = {line.split("\t")[4] for line in tsv.splitlines() if line.count("\t") >= 4}
        mod = sys.modules["pearnly_core.express_operator.contracts"]
        wanted = ({f"operator.report.column.{k}" for k in self.feed.REPORT_COLUMNS}
                  | {f"operator.report.state.{v}" for v in self.feed.STATE_KEY.values()}
                  | {f"operator.report.reason.{code}" for code in mod.REASONS}
                  | {"operator.report.reason.unknown", "operator.report.reason.result_unknown", "operator.report.match",
                     "operator.report.mismatch", "operator.report.voided"})
        self.assertEqual(wanted - keys, set())  # 每个用到的词条都在术语表里（四种语言）


class PatchTests(unittest.TestCase):
    def setUp(self):
        self.tasks, self.feed, self.state = _env(self)

    def test_old_tasks_get_one_conversation_each_and_running_it_again_adds_none(self):
        frappe = self.state["frappe"]
        for _ in range(3):
            self.tasks.create("CO-1", "2026-10")
        first = sorted(self.state["db"])[0]
        self.state["db"][first]["session_id"] = ""  # 旧任务：没有会话
        self.state["sessions"][:] = [s for s in self.state["sessions"] if s["operator_task"] != first]
        second = sorted(self.state["db"])[1]
        self.state["db"][second]["session_id"] = "sesn_typed"  # 在任务里打过字的（对话框甲建的会话）
        self.state["sessions"][:] = [s for s in self.state["sessions"] if s["operator_task"] != second]
        self.state["sessions"].append(_Row(name="sx", session_id="sesn_typed", user="a@example.com",
                                           operator_task=sorted(self.state["db"])[1]))
        patch = _load("v0_0_9_operator_task_sessions", CORE / "patches/v0_0_9_operator_task_sessions.py")
        frappe.db.table_exists = lambda name: True
        config = types.ModuleType("pearnly_core.assistant.config")
        config.engine_code = lambda: "claude"
        sys.modules["pearnly_core.assistant.config"] = config
        sys.modules["pearnly_core.assistant"].config = config
        self.addCleanup(sys.modules.pop, "pearnly_core.assistant.config", None)
        patch.execute()
        patch.execute()
        tasks = sorted(self.state["db"].values(), key=lambda t: t["name"])
        sessions = {s["session_id"]: s for s in self.state["sessions"]}
        self.assertEqual(len(sessions), len(tasks))  # 任务数 = 代录会话数
        self.assertEqual([t["session_id"] for t in tasks], [f"op_{tasks[0]['name']}", "sesn_typed", f"op_{tasks[2]['name']}"])
        self.assertEqual({sessions[t["session_id"]]["operator_task"] for t in tasks}, {t["name"] for t in tasks})

    def test_hand_order_moves_from_task_ids_to_conversation_ids_once(self):
        patch = _load("v0_0_10_operator_order_into_sessions", CORE / "patches/v0_0_10_operator_order_into_sessions.py")
        prefs = {"order": {"companies": ["CO-1"], "pinned": ["sesn_a"], "group:CO-1": {"keys": ["sesn_b", "sesn_c"], "at": 5,
                                                                                         "top": {"n": 1, "at": 5}}},
                 "view": {"sort": "updated"},
                 "operator": {"order/companies": ["CO-2", "CO-1"], "order/pinned": ["t2", "gone"],
                              "order/group:CO-1": {"keys": ["t3", "t1"], "at": 9},
                              "order/group:CO-2": {"keys": ["t4"], "at": 3}, "cards/folds": {"x": 1}}}
        session_of = {"t1": "op_t1", "t2": "op_t2", "t3": "op_t3", "t4": "op_t4"}
        merged = patch.merge(prefs, session_of)
        self.assertNotIn("operator", merged)
        self.assertEqual(merged["view"], {"sort": "updated"})
        order = merged["order"]
        self.assertEqual(order["companies"], ["CO-1", "CO-2"])
        self.assertEqual(order["pinned"], ["sesn_a", "op_t2"])  # 删了的任务丢掉
        self.assertEqual(order["group:CO-1"], {"keys": ["sesn_b", "sesn_c", "op_t3", "op_t1"], "at": 9,
                                               "top": {"n": 1, "at": 5}})  # 各自的先后不变
        self.assertEqual(order["group:CO-2"], {"keys": ["op_t4"], "at": 3})
        self.assertEqual(patch.merge(merged, session_of), merged)  # 再跑一次不变


if __name__ == "__main__":
    unittest.main()
