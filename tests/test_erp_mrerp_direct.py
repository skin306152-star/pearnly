"""ERP 接 MR.ERP 导入直写（`pearnly_erp_portal.mrerp`，评估 2026-11-02 §13 第 6 步）的离线检查：总开关两种状态。

- **关着**（:data:`MRERP_LEDGER_ENABLED` = False，现在的值）：所有 MR.ERP 入口挡住（选账本、连接、账套、选择、CC 授权钩子都不认），
  ERP hooks 与以前一样不声明 MR.ERP 登录钩子（共用通道照旧回 unsupported），`handles` 不查库直接回假（Express 的入队、准备、
  每分钟巡检、对话工具的分路因此一字不变），对话工具的账本叫法仍是 "Express"，ERP 不导入 Cowork；
- **开着**（测试里打补丁设成 True）：账本是 MR.ERP 的公司提交的单排上导入、领取后按规范载荷导入（假执行）、结论记回单据的
  通用账本栏；现购停下；要人选的停在单据上、选一次记在公司上后再排；账号被代录占着时照旧排队（不算失败）；结果不明不自动重推；
  「正在导入」过期转结果不明。

不连库、不连 MR.ERP：Frappe 用内存替身；导入执行、账号占用与凭据用替身（逐字节的导入表由 `test_erp_mrerp_golden.py` 在生产镜像里锁）。
"""
from __future__ import annotations

import ast
import datetime
import json
import pathlib
import sys
import types
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
ERP = ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal"
for path in (ROOT / "apps/pearnly_core", ROOT / "apps/pearnly_erp_portal"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


class Row(dict):
    __getattr__ = dict.get


class PermissionError_(Exception):
    pass


class Db:
    def __init__(self):
        self.tables, self.commits, self.rollbacks, self.touched = {}, 0, 0, 0

    def table(self, doctype):
        return self.tables.setdefault(doctype, {})

    def get_value(self, doctype, name, fields, as_dict=False, for_update=False):
        self.touched += 1
        row = self.table(doctype).get(name)
        if row is None:
            return None
        if isinstance(fields, str):
            return row.get(fields)
        return Row({field: row.get(field) for field in fields}) if as_dict else [row.get(f) for f in fields]

    def set_value(self, doctype, name, values, value=None, update_modified=True):
        values = values if isinstance(values, dict) else {values: value}
        self.table(doctype)[name].update(values)

    def exists(self, doctype, name):
        return name in self.table(doctype)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


DB = Db()
ENQUEUED = []
NOW = datetime.datetime(2026, 11, 3, 10, 0, 0)

frappe = types.ModuleType("frappe")
frappe.__path__ = []
frappe.db = DB
frappe._ = lambda text, *a, **k: text
frappe.PermissionError = PermissionError_
frappe.ValidationError = ValueError
frappe.session = types.SimpleNamespace(user="boss@x")
frappe.local = types.SimpleNamespace(site="erp.test")
frappe.get_roles = lambda user=None: ["ERP Admin"] if user == "boss@x" else []
frappe.enqueue = lambda method, **kw: ENQUEUED.append((method, kw))


def _throw(message, exc=ValueError):
    raise exc(message)


def _get_all(doctype, filters=None, fields=None, order_by=None, limit_page_length=None):
    rows = [Row(row) for row in DB.table(doctype).values()
            if all(row.get(k) == v for k, v in (filters or {}).items() if not isinstance(v, list))]
    return rows[:limit_page_length] if limit_page_length else rows


frappe.throw = _throw
frappe.get_all = _get_all
frappe.log_error = lambda *a, **k: None
frappe.utils = types.ModuleType("frappe.utils")
frappe.utils.now_datetime = lambda: NOW
frappe.utils.get_datetime = lambda value: value if isinstance(value, datetime.datetime) else \
    datetime.datetime.fromisoformat(str(value))
frappe.utils.password = types.ModuleType("frappe.utils.password")
frappe.utils.password.get_decrypted_password = lambda doctype, name, field, raise_exception=False: \
    DB.table(doctype).get(name, {}).get(field) or ""
sys.modules.update({"frappe": frappe, "frappe.utils": frappe.utils, "frappe.utils.password": frappe.utils.password})

import pearnly_erp_portal.mrerp as mrerp  # noqa: E402
from pearnly_erp_portal.mrerp import connection, contracts, push  # noqa: E402

PURCHASE, SALES = "Purchase Document", "ERP Sales Document"
OPERATOR = {"held": False}
CALLS = []


def execute_stub(outcome):
    def run(payload, target, secrets, system_url, today):
        CALLS.append({"payload": payload, "target": target, "secrets": secrets, "url": system_url})
        return outcome
    return run


def document(doctype, name):
    """ERP 交给写账规则的单据形状（`express.push.document`），这里直接给。"""
    row = DB.table(doctype)[name]
    from pearnly_erp_portal.express import contracts as E

    party = {"code": "", "name": "บริษัท ผู้ขาย จำกัด", "tax_id": "0105561012345", "branch": "00000"}
    return E.document(ref=f"{name}@20261103090000000000", direction=push.DIRECTIONS[doctype], doc_date="2026-11-03",
                      number=name, company={"tax_id": "0105559000001", "branch": "00000"}, party=party,
                      paid=bool(row.get("paid")), has_vat=True, total="107.00", vat="7.00",
                      lines=[{"name": "x", "qty": "1", "amount": "100.00", "stock": True}])


def reset(enabled=True):
    DB.tables.clear()
    ENQUEUED.clear()
    CALLS.clear()
    OPERATOR["held"] = False
    DB.table("ERP Company")["CO-1"] = {"name": "CO-1", "company_name": "บริษัท เรา จำกัด", "tax_id": "0105559000001",
                                      "branch": "00000", "express_enabled": 0, "mrerp_enabled": 1,
                                      "mrerp_connection": "C1", "mrerp_account_set": "15:1",
                                      "mrerp_linked_at": datetime.datetime(2026, 11, 1), "mrerp_menus": None,
                                      "mrerp_defaults": None}
    DB.table("ERP MRERP Connection")["C1"] = {"name": "C1", "system_url": "https://www.mrerp4sme.com", "enabled": 1,
                                             "credential_username": "u1", "credential_password": "secret"}


def doc(doctype=PURCHASE, name="PUR-1", **extra):
    DB.table(doctype)[name] = {"name": name, "status": "submitted", "company": "CO-1", "doc_kind": "goods",
                               "creation": datetime.datetime(2026, 11, 3, 9), "ledger_state": None,
                               "ledger_reason": "", "owner": "boss@x", **extra}
    return Row(DB.table(doctype)[name])


class _Module:
    """导入任务里才载入的共用层（连网那一半）换成替身。"""

    def __init__(self, outcome):
        self.outcome = outcome

    def __enter__(self):
        execute_module = types.ModuleType("pearnly_core.mrerp.push.execute")
        execute_module.execute = execute_stub(self.outcome)
        session_module = types.ModuleType("pearnly_core.mrerp.session")
        session_module.lock_key = lambda url, user: f"{url}|{user}"
        lock_module = types.ModuleType("pearnly_core.session_lock")
        lock_module.operator_holds = lambda key: OPERATOR["held"]
        self.patch = patch.dict(sys.modules, {"pearnly_core.mrerp.push.execute": execute_module,
                                              "pearnly_core.mrerp.session": session_module,
                                              "pearnly_core.session_lock": lock_module})
        self.patch.start()
        return self

    def __exit__(self, *exc):
        self.patch.stop()


class SwitchedOff(unittest.TestCase):
    """关着：入口全挡、Express 那条一字不变。"""

    def setUp(self):
        reset()

    def test_the_switch_is_a_code_constant_and_off(self):
        self.assertIs(mrerp.MRERP_LEDGER_ENABLED, False)
        self.assertFalse(mrerp.enabled())

    def test_handles_says_no_without_reading_the_database(self):
        before = DB.touched
        self.assertFalse(push.handles("CO-1"))
        self.assertEqual(DB.touched, before)

    def test_every_mrerp_entry_is_refused(self):
        from pearnly_erp_portal.mrerp import catalog, choices

        for call in (lambda: connection.configure("https://www.mrerp4sme.com"), connection.require_admin,
                     lambda: connection.records(), lambda: connection.configure_once("https://www.mrerp4sme.com", None, "k1"),
                     lambda: catalog.refresh("C1", manual=True), lambda: catalog.account_sets("C1"),
                     lambda: catalog.choose("CO-1", "C1", "15:1"), lambda: catalog.detach("CO-1"),
                     lambda: choices.request_read("CO-1", manual=True), lambda: choices.choose("CO-1", "mrerp_menu", "purchase", "68")):
            with self.subTest(call=call):
                with self.assertRaises(PermissionError_):
                    call()
        self.assertEqual(connection.login_scope("boss@x", "C1"), "")
        with self.assertRaises(PermissionError_):
            connection.login_store("C1", "u", "p", user="boss@x", device_label="PC", scope="site")

    def test_queue_send_poll_and_jobs_do_nothing(self):
        row = doc()
        push.queue(PURCHASE, row)
        self.assertEqual(push.send(PURCHASE, "PUR-1"), {"status": "not_ready"})
        push.poll()
        push.run(PURCHASE, "PUR-1", document)
        from pearnly_erp_portal.mrerp import catalog, choices

        catalog.run("C1")
        choices.run_read("C1", "15:1")
        self.assertEqual(DB.table(PURCHASE)["PUR-1"]["ledger_state"], None)
        self.assertEqual(ENQUEUED, [])

    def test_hooks_do_not_declare_the_mrerp_login_and_tools_keep_the_express_word(self):
        tree = ast.parse((ERP / "hooks.py").read_text(encoding="utf-8"))
        guarded = [node for node in ast.walk(tree) if isinstance(node, ast.If)
                   and "MRERP_LEDGER_ENABLED" in ast.unparse(node.test)]
        self.assertEqual(len(guarded), 1)
        self.assertIn("mrerp_login_scope", ast.unparse(guarded[0]))
        literal = next(node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                       and any(getattr(t, "id", "") == "pearnly_cc_desktop" for t in node.targets))
        self.assertNotIn("mrerp", ast.unparse(literal.value))
        source = (ERP / "express/cc_tools.py").read_text(encoding="utf-8")
        self.assertIn('PROGRAM = "Express or MR.ERP" if mrerp.MRERP_LEDGER_ENABLED else "Express"', source)

    def test_erp_never_imports_cowork(self):
        for path in (ERP / "mrerp").rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                    [node.module or ""] if isinstance(node, ast.ImportFrom) else []
                for name in names:
                    with self.subTest(module=path.name, imports=name):
                        self.assertFalse(name.startswith("pearnly_cowork"))


@patch.object(mrerp, "MRERP_LEDGER_ENABLED", True)
class SwitchedOn(unittest.TestCase):
    """开着：直写链路通（假执行）。"""

    def setUp(self):
        reset()

    def test_a_submitted_document_is_queued_for_mrerp(self):
        row = doc()
        self.assertTrue(push.handles("CO-1"))
        push.queue(PURCHASE, row)
        saved = DB.table(PURCHASE)["PUR-1"]
        self.assertEqual((saved["ledger_state"], saved["ledger_target"]), ("queued", "mrerp"))
        self.assertEqual(ENQUEUED[0][0], "pearnly_erp_portal.express.push.mrerp_run")

    def test_documents_started_before_the_ledger_was_chosen_are_not_queued(self):
        row = doc(creation=datetime.datetime(2026, 10, 1))
        push.queue(PURCHASE, row)
        self.assertIsNone(DB.table(PURCHASE)["PUR-1"]["ledger_state"])

    def test_an_express_company_is_not_mrerp(self):
        DB.table("ERP Company")["CO-1"]["express_enabled"] = 1
        self.assertFalse(push.handles("CO-1"))

    def test_success_writes_the_mrerp_number_on_the_document(self):
        doc(ledger_state="queued", ledger_target="mrerp")
        with _Module({"status": "success", "docnum": "PG691103-000123", "menus": {}, "masters": {}}):
            push.run(PURCHASE, "PUR-1", document)
        saved = DB.table(PURCHASE)["PUR-1"]
        self.assertEqual((saved["ledger_state"], saved["ledger_docnum"], saved["ledger_reason"]),
                         ("written", "PG691103-000123", ""))
        self.assertEqual(CALLS[0]["target"], {"account_set": "15:1"})
        self.assertEqual(CALLS[0]["secrets"], {"username": "u1", "password": "secret"})
        payload = CALLS[0]["payload"]
        self.assertEqual(payload["mappings"]["_own_tax_id"], "0105559000001")
        self.assertEqual(payload["histories"][0]["fields"]["posting_payment_manual"], "credit")
        self.assertEqual(payload["histories"][0]["fields"]["posting_item_type_manual"], "goods")

    def test_a_document_voided_while_importing_is_not_recorded_as_a_clean_write(self):
        """11-13：领单之后、MR.ERP 导入那几十秒里单据在 ERP 作废——导入成功的转人工、原因「作废后写进了」、单号如实留着；
        没导入成的清账本栏（不再显示排队 / 失败）。"""
        from pearnly_erp_portal.express import contracts as E

        for outcome, expected in (({"status": "success", "docnum": "PG691113-000001"},
                                   ("held", E.VOIDED_AFTER_WRITE, "PG691113-000001")),
                                  ({"status": "failed", "reason": "boom"}, (None, "", None))):
            reset()
            doc(ledger_state="queued", ledger_target="mrerp")

            def voided_meanwhile(*args, outcome=outcome):
                DB.table(PURCHASE)["PUR-1"]["status"] = "void"
                return {**outcome, "menus": {}, "masters": {}}

            with _Module(outcome):
                sys.modules["pearnly_core.mrerp.push.execute"].execute = voided_meanwhile
                push.run(PURCHASE, "PUR-1", document)
            saved = DB.table(PURCHASE)["PUR-1"]
            self.assertEqual((saved["ledger_state"], saved["ledger_reason"], saved.get("ledger_docnum")), expected)

    def test_a_cash_purchase_stops_mrerp_only_imports_credit(self):
        doc(ledger_state="queued", ledger_target="mrerp", paid=True)
        with _Module({"status": "success"}):
            push.run(PURCHASE, "PUR-1", document)
        saved = DB.table(PURCHASE)["PUR-1"]
        self.assertEqual((saved["ledger_state"], saved["ledger_reason"]), ("failed", contracts.PURCHASE_CASH))
        self.assertEqual(CALLS, [])

    def test_a_recognized_document_whose_printed_total_does_not_match_is_held_before_mrerp(self):
        """2026-11-03「CC 通用疑难处理」：识别来的单票面合计没有 / 对不上，同 Express 直写停下进待处理，不导入 MR.ERP；
        对上了、或没有识别标记（手工单、老单）照常导入。"""
        from pearnly_erp_portal.express import contracts as E

        mark = '{"codes":[],"total":150.0}'
        for extra, reason in (({"ocr_review": mark, "printed_total": 150, "grand_total": 107}, E.PRINTED_TOTAL_MISMATCH),
                              ({"ocr_review": mark, "printed_total": 0, "grand_total": 107}, E.PRINTED_TOTAL_MISSING)):
            reset()
            doc(ledger_state="queued", ledger_target="mrerp", net_payable=107, **extra)
            with _Module({"status": "success", "docnum": "PG1"}):
                push.run(PURCHASE, "PUR-1", document)
            saved = DB.table(PURCHASE)["PUR-1"]
            self.assertEqual((saved["ledger_state"], saved["ledger_reason"]), ("held", reason))
            self.assertEqual(CALLS, [])
        for extra in ({"ocr_review": mark, "printed_total": 107, "grand_total": 107}, {"printed_total": 0, "grand_total": 107}):
            reset()
            doc(ledger_state="queued", ledger_target="mrerp", net_payable=107, **extra)
            with _Module({"status": "success", "docnum": "PG1", "menus": {}, "masters": {}}):
                push.run(PURCHASE, "PUR-1", document)
            self.assertEqual(DB.table(PURCHASE)["PUR-1"]["ledger_state"], "written", extra)

    def test_a_busy_account_waits_in_the_queue(self):
        doc(ledger_state="queued", ledger_target="mrerp")
        OPERATOR["held"] = True
        with _Module({"status": "success"}):
            push.run(PURCHASE, "PUR-1", document)
        saved = DB.table(PURCHASE)["PUR-1"]
        self.assertEqual((saved["ledger_state"], saved["ledger_reason"]), ("queued", contracts.ACCOUNT_BUSY))
        self.assertEqual(CALLS, [])
        push.poll()
        self.assertEqual(ENQUEUED[-1][0], "pearnly_erp_portal.express.push.mrerp_run")

    def test_no_login_yet_holds_the_document(self):
        doc(ledger_state="queued", ledger_target="mrerp")
        DB.table("ERP MRERP Connection")["C1"]["credential_password"] = ""
        with _Module({"status": "success"}):
            push.run(PURCHASE, "PUR-1", document)
        self.assertEqual(DB.table(PURCHASE)["PUR-1"]["ledger_reason"], contracts.NO_LOGIN)

    def test_a_menu_choice_stops_on_the_document_and_answering_queues_it_again(self):
        doc(ledger_state="queued", ledger_target="mrerp")
        observed = {"impaptran": [{"idmenu": "68", "label": "ซื้อ", "linked": True},
                                  {"idmenu": "70", "label": "ซื้อ 2", "linked": True}]}
        with _Module({"status": "failed", "reason": "ERR_MRERP_MENU_CHOICE_REQUIRED", "menus": observed, "masters": {}}):
            push.run(PURCHASE, "PUR-1", document)
        saved = DB.table(PURCHASE)["PUR-1"]
        self.assertEqual((saved["ledger_state"], saved["ledger_reason"]), ("held", contracts.CHOICE))
        needed = json.loads(saved["ledger_needed"])
        # 同 Cowork：按方向问（采购的货 / 费两类菜单都在同一个导入页上）
        self.assertEqual([(item["kind"], item["subject"]) for item in needed],
                         [("mrerp_menu", "purchase"), ("mrerp_menu", "purchase_expense")])
        self.assertFalse(push.answer(PURCHASE, "PUR-1", [{"kind": "mrerp_menu", "subject": "purchase", "value": "99"}]))
        self.assertTrue(push.answer(PURCHASE, "PUR-1", [{"kind": "mrerp_menu", "subject": "purchase", "value": "70"}]))
        self.assertEqual(json.loads(DB.table("ERP Company")["CO-1"]["mrerp_menus"])["15:1"]["purchase"]["idmenu"], "70")
        self.assertEqual(DB.table(PURCHASE)["PUR-1"]["ledger_state"], "queued")

    def test_an_unknown_result_is_not_pushed_again(self):
        doc(ledger_state="queued", ledger_target="mrerp")
        with _Module({"status": "unknown", "reason": "accepted but not listed", "menus": {}, "masters": {}}):
            push.run(PURCHASE, "PUR-1", document)
        saved = DB.table(PURCHASE)["PUR-1"]
        self.assertEqual(saved["ledger_state"], "held")
        self.assertTrue(saved["ledger_reason"].startswith(contracts.UNKNOWN))
        self.assertEqual(push.send(PURCHASE, "PUR-1"), {"status": "held", "reason": contracts.UNKNOWN})

    def test_a_second_run_does_not_import_twice(self):
        doc(ledger_state="queued", ledger_target="mrerp", ledger_reason=contracts.IMPORTING)
        with _Module({"status": "success"}):
            push.run(PURCHASE, "PUR-1", document)
        self.assertEqual(CALLS, [])

    def test_an_interrupted_import_turns_unknown_after_fifteen_minutes(self):
        doc(ledger_state="queued", ledger_target="mrerp", ledger_reason=contracts.IMPORTING,
            ledger_sent_at=NOW - datetime.timedelta(minutes=16))
        push.poll()
        saved = DB.table(PURCHASE)["PUR-1"]
        self.assertEqual(saved["ledger_state"], "held")
        self.assertTrue(saved["ledger_reason"].startswith(contracts.UNKNOWN))

    def test_only_an_erp_admin_authorizes_the_login_and_the_password_is_never_returned(self):
        self.assertEqual(connection.login_scope("boss@x", "C1"), "site")
        self.assertEqual(connection.login_scope("clerk@x", "C1"), "")
        self.assertEqual(connection.view("C1").get("credential", {}).get("username"), "u1")
        self.assertNotIn("secret", json.dumps(connection.view("C1")))


class _Redis:
    def __init__(self):
        self.data = {}

    def get(self, name):
        return self.data.get(name)

    def set(self, name, value, ex=None):
        assert ex, "state Redis keys need an expiry"
        self.data[name] = value


@patch.object(mrerp, "MRERP_LEDGER_ENABLED", True)
class KickedAccount(unittest.TestCase):
    """11-09 风险 7（主会话定 A）：MR.ERP 账号被踢后按账号记标记（与 Cowork 同一个）；ERP 的自动路径（提交新单排上、每分钟巡检、
    选一次后再排、默认的 send）都不清标记，导入入口快速失败进待处理；只有人点的推送入口（CC 推送工具，``manual=True``）清了再推。"""

    KEY = "https://www.mrerp4sme.com|u1"  # `_Module` 里 lock_key 的替身：网址|用户名

    def setUp(self):
        from pearnly_core.mrerp import kick_mark as account_kick

        reset()
        self.kick, self.redis = account_kick, _Redis()
        self.patch = patch.object(account_kick, "_store", lambda: self.redis)
        self.patch.start()
        self.kick.mark(self.KEY)

    def tearDown(self):
        self.patch.stop()

    def test_a_manual_send_clears_the_mark_and_queues_the_import(self):
        doc(ledger_state="failed", ledger_target="mrerp", ledger_reason="ERR_MRERP_ACCOUNT_IN_USE: signed in elsewhere")
        with _Module({"status": "success"}):
            self.assertEqual(push.send(PURCHASE, "PUR-1", manual=True), {"status": "sent"})
        self.assertIsNone(self.kick.state(self.KEY))
        self.assertEqual(DB.table(PURCHASE)["PUR-1"]["ledger_state"], "queued")

    def test_automatic_paths_keep_the_mark(self):
        doc(ledger_state="failed", ledger_target="mrerp", ledger_reason="ERR_MRERP_ACCOUNT_IN_USE: signed in elsewhere")
        fresh = doc(name="PUR-2")
        doc(name="PUR-3", ledger_state="queued", ledger_target="mrerp", ledger_reason=contracts.ACCOUNT_BUSY)
        with _Module({"status": "success"}):
            push.send(PURCHASE, "PUR-1")      # 默认（选一次后再排、改票面合计放行、Express 那条转过来的）
            push.queue(PURCHASE, fresh)        # 提交新单
            push.poll()                        # 每分钟巡检
        self.assertTrue(self.kick.state(self.KEY))

    def test_the_kicked_outcome_lands_in_pending_as_a_failure_with_its_code(self):
        doc(ledger_state="queued", ledger_target="mrerp")
        reason = "ERR_MRERP_ACCOUNT_IN_USE: signed in elsewhere, waiting for a manual push"
        with _Module({"status": "failed", "reason": reason, "menus": {}, "masters": {}}):
            push.run(PURCHASE, "PUR-1", document)
        saved = DB.table(PURCHASE)["PUR-1"]
        self.assertEqual((saved["ledger_state"], saved["ledger_reason"]), ("failed", reason))

    def test_only_the_manual_entries_clear_the_mark(self):
        """源码锁：清标记的调用只在人点的入口里；自动路径（Cowork 定时巡检 / 自动推档位 / 文件夹定时 / 同批后续，ERP 提交新单 /
        每分钟巡检 / 选一次后再排）一处都没有。新加入口要在这里登记。

        11-11 起读菜单 / 主档、目录采集也走同一套（`mrerp.account.use`，``manual`` 由入口显式给）：人点的只有网页与 CC 共用的申请接口
        （Cowork `connections.api.request_refresh / request_mrerp_menu`，ERP `mrerp.api` 的三个申请）；其余是把入口说的原样往下传
        （排任务 → 后台任务 → 共用读取），后台任务自己默认不算。"""
        allowed = {
            ("apps/pearnly_cowork/pearnly_cowork/push/application/api.py", "submit", "manual"),
            ("apps/pearnly_cowork/pearnly_cowork/assistant/tools.py", "_submit_one", "manual"),
            ("apps/pearnly_cowork/pearnly_cowork/push/application/service.py", "submit", "release_kick"),
            ("apps/pearnly_cowork/pearnly_cowork/push/mrerp/runner.py", "release_kick", "clear"),
            ("apps/pearnly_cowork/pearnly_cowork/assistant/operator_source.py", "_release_kick", "release_kick"),
            ("apps/pearnly_cowork/pearnly_cowork/assistant/operator_source.py", "submit", "_release_kick"),
            ("apps/pearnly_erp_portal/pearnly_erp_portal/mrerp/push.py", "send", "release_kick"),
            ("apps/pearnly_erp_portal/pearnly_erp_portal/mrerp/push.py", "release_kick", "clear"),
            ("apps/pearnly_erp_portal/pearnly_erp_portal/express/cc_tools.py", "_route", "manual"),
            # 11-14 公司接入第 3 批（B13）：单据页「再写一次」对 MR.ERP 的单是人点的
            ("apps/pearnly_erp_portal/pearnly_erp_portal/express/api.py", "retry", "manual"),
            # 11-11 读取：共用入口（清标记只在这一处）与把 manual 往下传的几层
            ("apps/pearnly_core/pearnly_core/mrerp/account.py", "use", "clear"),
            ("apps/pearnly_core/pearnly_core/mrerp/menu_read.py", "read", "manual"),
            ("apps/pearnly_core/pearnly_core/mrerp/collect.py", "collect", "manual"),
            ("apps/pearnly_core/pearnly_core/mrerp/collect.py", "_collect_once", "manual"),
            ("apps/pearnly_cowork/pearnly_cowork/connections/api.py", "request_refresh", "manual"),
            ("apps/pearnly_cowork/pearnly_cowork/connections/api.py", "request_mrerp_menu", "manual"),
            ("apps/pearnly_cowork/pearnly_cowork/connections/mrerp_menu_reader.py", "dispatch", "manual"),
            ("apps/pearnly_cowork/pearnly_cowork/connections/mrerp_menu_reader.py", "run", "manual"),
            ("apps/pearnly_cowork/pearnly_cowork/connections/mrerp_collector.py", "dispatch", "manual"),
            ("apps/pearnly_cowork/pearnly_cowork/connections/mrerp_collector.py", "run", "manual"),
            ("apps/pearnly_erp_portal/pearnly_erp_portal/mrerp/api.py", "request_refresh", "manual"),
            ("apps/pearnly_erp_portal/pearnly_erp_portal/mrerp/api.py", "refresh_catalog", "manual"),
            ("apps/pearnly_erp_portal/pearnly_erp_portal/mrerp/api.py", "read_choices", "manual"),
            ("apps/pearnly_erp_portal/pearnly_erp_portal/mrerp/catalog.py", "refresh", "manual"),
            ("apps/pearnly_erp_portal/pearnly_erp_portal/mrerp/catalog.py", "run", "manual"),
            ("apps/pearnly_erp_portal/pearnly_erp_portal/mrerp/choices.py", "request_read", "manual"),
            ("apps/pearnly_erp_portal/pearnly_erp_portal/mrerp/choices.py", "run_read", "manual"),
        }
        found = set()
        for path in (ROOT / "apps").rglob("*.py"):
            if "/tests/" in str(path):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for func in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
                for call in [n for n in ast.walk(func) if isinstance(n, ast.Call)]:
                    name = call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")
                    receiver = ast.unparse(call.func.value) if isinstance(call.func, ast.Attribute) else ""
                    rel = str(path.relative_to(ROOT))
                    if name in ("release_kick", "_release_kick") or (name == "clear" and receiver in ("account_kick",
                                                                                                         "kick_mark")):
                        found.add((rel, func.name, name))
                    if any(k.arg == "manual" and not (isinstance(k.value, ast.Constant) and k.value.value is False)
                           for k in call.keywords):
                        found.add((rel, func.name, "manual"))
        self.assertEqual(found, allowed)

    def test_every_mrerp_login_for_a_push_goes_through_the_import_entry(self):
        """推送用的登录都经 `MrErpImport`（``with importer``），它从共用入口 `mrerp.account.use` 拿会话、一律不算人点的；
        共用入口先看标记，再拿锁、再建会话（11-11 起读菜单 / 主档、目录采集也经同一入口）。"""
        users = {}
        for path in (ROOT / "apps").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "MrErpImport(" in text and "class MrErpImport" not in text:
                users[str(path.relative_to(ROOT))] = "with importer:" in text
        self.assertEqual(users, {"apps/pearnly_core/pearnly_core/mrerp/push/execute.py": True,
                                 "apps/pearnly_cowork/pearnly_cowork/push/mrerp/operator.py": True})
        enter = (ROOT / "apps/pearnly_core/pearnly_core/mrerp/push/transport.py").read_text(encoding="utf-8")
        body = enter[enter.index('def __enter__(self) -> "MrErpImport":'):enter.index("def __exit__")]
        self.assertIn("mrerp_account.use(", body)
        self.assertIn("manual=False", body)
        shared = (ROOT / "apps/pearnly_core/pearnly_core/mrerp/account.py").read_text(encoding="utf-8")
        use = shared[shared.index("def use("):]
        self.assertLess(use.index("kick_mark.state(account)"), use.index("account_lock("))
        self.assertLess(use.index("kick_mark.state(account)"), use.index("MrErpSession("))


class _Doc(Row):
    """`frappe.get_doc` 的最小替身：连接表的读、改、存、新建（名字按 C1、C2… 顺排）。"""

    __setattr__ = dict.__setitem__

    def save(self, ignore_permissions=False):
        DB.table(self["doctype"])[self["name"]] = {k: v for k, v in self.items() if k != "doctype"}

    def insert(self, ignore_permissions=False):
        table = DB.table(self["doctype"])
        self["name"] = f"C{len(table) + 1}"
        self.setdefault("creation", datetime.datetime(2026, 11, 9, 10, len(table)))
        self.save()
        return self


def _get_doc(doctype, name=None, for_update=False):
    if isinstance(doctype, dict):
        return _Doc(doctype)
    return _Doc({"doctype": doctype, **DB.table(doctype)[name]})


def _get_all_paged(doctype, filters=None, fields=None, order_by=None, limit_start=0, limit_page_length=None):
    rows = [Row(row) for row in DB.table(doctype).values()
            if all(row.get(k) == v for k, v in (filters or {}).items() if not isinstance(v, list))]
    if order_by == "creation asc":
        rows.sort(key=lambda row: row.get("creation") or datetime.datetime.min)
    rows = rows[limit_start:]
    return rows[:limit_page_length] if limit_page_length else rows


class _Cache:
    def __init__(self):
        self.values = {}

    def get_value(self, key):
        return self.values.get(key)

    def set_value(self, key, value, expires_in_sec=None):
        self.values[key] = value


class _SiteFakes(unittest.TestCase):
    """连接表、公司表的最小站点替身（get_doc / 分页 get_all / cache / whitelist）。"""

    def setUp(self):
        reset()
        DB.table("ERP MRERP Connection")["C1"]["creation"] = datetime.datetime(2026, 11, 1)
        self.cache = _Cache()
        self.patches = [patch.object(frappe, "whitelist", lambda **kw: (lambda fn: fn), create=True),
                        patch.object(frappe, "get_doc", _get_doc, create=True),
                        patch.object(frappe, "get_all", _get_all_paged),
                        patch.object(frappe, "cache", self.cache, create=True)]
        for item in self.patches:
            item.start()
        from pearnly_erp_portal.mrerp import api
        self.api = api

    def tearDown(self):
        for item in self.patches:
            item.stop()
        frappe.session.user = "boss@x"


@patch.object(mrerp, "MRERP_LEDGER_ENABLED", True)
class ConnectRow(_SiteFakes):
    """CC 设置 → 连接的 MR.ERP 一行（`frontend/assistant/src/components/mrerp-connect.tsx`）调的接口：ERP 要有、形状对、只限管理员、
    不回密码、建连接按请求号幂等；旧的 ``configure(system_url)`` 调法照旧（实验栈脚本在用）。"""

    def test_every_method_the_settings_row_calls_exists_and_is_whitelisted(self):
        import re

        source = (ROOT / "frontend/assistant/src/components/mrerp-connect.tsx").read_text(encoding="utf-8")
        called = set(re.findall(r'api \+ "(\w+)"', source))
        self.assertEqual(called, {"records", "configure", "request_refresh"})
        tree = ast.parse((ERP / "mrerp/api.py").read_text(encoding="utf-8"))
        whitelisted = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)
                       and any("whitelist" in ast.unparse(d) for d in node.decorator_list)}
        self.assertLessEqual(called, whitelisted)

    def test_records_has_the_row_shape_marks_the_main_one_and_never_the_password(self):
        rows = self.api.records(limit=100)
        self.assertEqual(rows, [{"name": "C1", "adapter": "mrerp", "enabled": 1, "main": True,
                                 "config": {"system_url": "https://www.mrerp4sme.com"},
                                 "credential": {"username": "u1", "device": "", "at": ""}}])
        self.assertNotIn("secret", json.dumps(rows))
        DB.table("ERP MRERP Connection")["C0"] = {"name": "C0", "system_url": "https://a.example", "enabled": 0,
                                                 "creation": datetime.datetime(2026, 10, 1)}
        rows = self.api.records(start=0, limit=1)
        self.assertEqual([(row["name"], row["main"]) for row in rows], [("C0", False)])  # 停用的不是主连接
        self.assertEqual([row["name"] for row in self.api.records(start=1, limit=500)], ["C1"])

    def test_only_erp_admins_get_any_of_the_three(self):
        frappe.session.user = "staff@x"
        for call in (lambda: self.api.records(), lambda: self.api.request_refresh("C1", "k1"),
                     lambda: self.api.configure(adapter="mrerp", request_key="k1", config={"system_url": "https://x.example"})):
            with self.subTest(call=call):
                with self.assertRaises(PermissionError_):
                    call()
        self.assertEqual(ENQUEUED, [])

    def test_configure_from_the_settings_row_creates_once_per_request_key(self):
        DB.table("ERP MRERP Connection").clear()
        body = {"adapter": "mrerp", "request_key": "k1", "label": "MR.ERP",
                "config": json.dumps({"system_url": "https://www.mrerp4sme.com"})}
        first = self.api.configure(**body)
        again = self.api.configure(**body)  # 双击 / 网络重试：同一个请求号
        self.assertEqual(first["name"], again["name"])
        self.assertEqual(len(DB.table("ERP MRERP Connection")), 1)
        self.assertEqual((first["adapter"], first["main"], first["config"], first["connection"]),
                         ("mrerp", True, {"system_url": "https://www.mrerp4sme.com"}, first["name"]))
        changed = self.api.configure(adapter="mrerp", request_key="k2", name=first["name"], expected_revision=None,
                                     config={"system_url": "https://erp.example.co.th"})
        self.assertEqual((changed["name"], changed["system_url"]), (first["name"], "https://erp.example.co.th"))
        with self.assertRaises(ValueError):
            self.api.configure(adapter="express", request_key="k3", config={"system_url": "https://x.example"})

    def test_the_old_configure_call_keeps_its_view(self):
        view = self.api.configure(system_url="https://www.mrerp4sme.com", connection_name="C1")
        self.assertEqual((view["connection"], view["system_url"], view["enabled"]), ("C1", "https://www.mrerp4sme.com", True))

    def test_request_refresh_reads_the_catalog_once(self):
        out = self.api.request_refresh("C1", "k1")
        self.assertEqual(out["connection"], "C1")
        self.assertEqual([call[0] for call in ENQUEUED], ["pearnly_erp_portal.mrerp.catalog.run"])
        self.assertTrue(ENQUEUED[0][1]["deduplicate"])


@patch.object(mrerp, "MRERP_LEDGER_ENABLED", True)
class CompanySection(_SiteFakes):
    """公司页的 MR.ERP 一节（11-09 P0-⑤b）：只在开关开着、ERP 管理员、公司没接 Express、站点有主连接时给；现在的账套与它在目录里的名字；
    页面用官方对话框与 Autocomplete、按输入在服务端按页搜，只调已有的选账套 / 断开接口。"""

    def setUp(self):
        super().setUp()
        DB.table("ERP MRERP Connection")["C1"]["catalog_json"] = json.dumps(
            [{"source_id": "15:1", "label": "TEST2019"}, {"source_id": "16:1", "label": "TEST2020"}])
        from pearnly_erp_portal.mrerp import catalog
        self.catalog = catalog

    def test_a_linked_company_shows_its_account_set_by_name(self):
        self.assertEqual(self.catalog.company_view("CO-1"),
                         {"connection": "C1", "account_set": "15:1", "label": "TEST2019", "linked": True})

    def test_an_unlinked_company_offers_the_main_connection(self):
        DB.table("ERP Company")["CO-1"]["mrerp_enabled"] = 0
        self.assertEqual(self.catalog.company_view("CO-1"),
                         {"connection": "C1", "account_set": "", "label": "", "linked": False})

    def test_no_section_for_express_companies_staff_or_a_site_without_a_connection(self):
        DB.table("ERP Company")["CO-1"]["express_enabled"] = 1
        self.assertIsNone(self.catalog.company_view("CO-1"))
        DB.table("ERP Company")["CO-1"]["express_enabled"] = 0
        frappe.session.user = "staff@x"
        self.assertIsNone(self.catalog.company_view("CO-1"))
        frappe.session.user = "boss@x"
        DB.table("ERP MRERP Connection")["C1"]["enabled"] = 0
        self.assertIsNone(self.catalog.company_view("CO-1"))
        with patch.object(mrerp, "MRERP_LEDGER_ENABLED", False):
            DB.table("ERP MRERP Connection")["C1"]["enabled"] = 1
            self.assertIsNone(self.catalog.company_view("CO-1"))

    def test_the_page_section_uses_official_controls_and_the_existing_entries(self):
        page = (ERP / "public/desk/company.js").read_text(encoding="utf-8")
        section = page[page.index("const MRERP = "):page.index("// 还没写进 Express 的退货小票")]
        for part in ('new frappe.ui.Dialog(', 'fieldtype: "Autocomplete"', 'MRERP + "account_sets"', 'search: text || ""',
                     'MRERP + "choose_ledger"', 'MRERP + "detach"', "frappe.confirm(", "frappe.utils.debounce("):
            self.assertIn(part, section)
        self.assertNotIn('__("Change")', section)  # ERP 术语表里 Change 是收银「找零」
        self.assertIn("...mrerp_section(data, name),", page)
        profile = (ERP / "masters/company.py").read_text(encoding="utf-8")
        self.assertIn("mrerp_view = mrerp_catalog.company_view(doc.name)", profile)


class PureRules(unittest.TestCase):
    def test_outcomes_map_to_the_generic_ledger_columns(self):
        self.assertEqual(contracts.settle({"status": "success", "docnum": "SI1"})["ledger_state"], "written")
        self.assertEqual(contracts.settle({"status": "failed", "reason": "ERR_MRERP_DEFAULT_CHANGED"})["ledger_reason"],
                         contracts.CHOICE)
        self.assertEqual(contracts.settle({"status": "failed", "reason": "ERR_AUTH: x"}),
                         {"ledger_state": "failed", "ledger_reason": "ERR_AUTH: x"})

    def test_a_written_import_whose_listed_amount_differs_is_a_mismatch(self):
        verify = {"meta": {"verify": {"state": "mismatch", "fields": ["amount"]}}}
        self.assertEqual(contracts.settle({"status": "success", "docnum": "SI1", **verify})["ledger_state"], "mismatch")
        self.assertEqual(contracts.settle({"status": "success", "docnum": "SI1",
                                           "meta": {"verify": {"state": "ok"}}})["ledger_docnum"], "SI1")

    def test_the_document_face_carries_payment_method_party_record_branch_and_currency(self):
        """11-14 TEST2019 真写：转账收款的销售记进了现金栏（SE691114-861564）、没税号客户的未收款销售被改成现销挂「เงินสด」
        （SE691114-614656）——ERP 票面没带付款方式与对方记录号。"""
        body = {"ref": "SD-1@1", "direction": "sales", "date": "2026-11-14", "number": "SD-1", "paid": True,
                "company": {"tax_id": "0105989581512", "branch": "00000"},
                "party": {"code": "", "name": "ลูกค้า", "tax_id": "", "branch": "00001"}, "currency": "THB",
                "subtotal": "300.00", "vat": "21.00", "total": "321.00",
                "items": [{"name": "A", "qty": "2", "price": "150.00", "amount": "300.00"}]}
        flat = contracts.history(body, company_name="เรา", payment_method="transfer", party_id="CUS-00012")
        self.assertEqual((flat["client_id"], flat["fields"]["payment_method"], flat["fields"]["buyer_branch"],
                          flat["fields"]["currency"]), ("CUS-00012", "transfer", "00001", "THB"))
        unpaid = contracts.history(dict(body, paid=False), company_name="เรา", payment_method="cash")
        self.assertNotIn("payment_method", unpaid["fields"])
        self.assertNotIn("client_id", unpaid)

    def test_the_mrerp_number_is_stable_per_document_and_hex(self):
        self.assertEqual(contracts.history_id("PUR-1@1"), contracts.history_id("PUR-1@1"))
        self.assertNotEqual(contracts.history_id("PUR-1@1"), contracts.history_id("PUR-1@2"))
        int(contracts.history_id("PUR-1@1")[-8:], 16)


if __name__ == "__main__":
    unittest.main()
