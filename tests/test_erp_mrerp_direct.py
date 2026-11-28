"""ERP 接 MR.ERP 导入直写（`pearnly_erp_portal.mrerp`，评估 2026-11-02 §13 第 6 步）的离线检查：总开关两种状态。

- **关着**（测试里打补丁设成 False；11-22 起常量是 True，用户定对用户开放）：所有 MR.ERP 入口挡住（选账本、连接、账套、选择、CC 授权钩子都不认），
  ERP hooks 与以前一样不声明 MR.ERP 登录钩子（共用通道照旧回 unsupported），`handles` 不查库直接回假（Express 的入队、准备、
  每分钟巡检、对话工具的分路因此一字不变），对话工具的账本叫法仍是 "Express"，ERP 不导入 Cowork；
- **开着**（现在的值，测试里也打补丁设成 True）：账本是 MR.ERP 的公司提交的单排上导入、领取后按规范载荷导入（假执行）、结论记回单据的
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
        # 读视图替身（④ 11-28）：``stale[(doctype, name)]`` 是本事务普通读看到的旧行（关快照隔离后普通读停在读视图里），
        # 加锁读（``for_update``）照旧读到最新；``locked`` 记下每次加锁读了哪张表的哪一行
        self.stale, self.locked = {}, []

    def table(self, doctype):
        return self.tables.setdefault(doctype, {})

    def get_value(self, doctype, name, fields, as_dict=False, for_update=False):
        self.touched += 1
        if for_update:
            self.locked.append((doctype, name))
        row = self.table(doctype).get(name) if for_update else \
            self.stale.get((doctype, name), self.table(doctype).get(name))
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

    def rollback(self, save_point=None):
        self.rollbacks += 1

    def savepoint(self, name):
        pass


DB = Db()
ENQUEUED = []
NOW = datetime.datetime(2026, 11, 3, 10, 0, 0)

frappe = types.ModuleType("frappe")
frappe.__path__ = []
frappe.db = DB
frappe._ = lambda text, *a, **k: text
frappe.PermissionError = PermissionError_
frappe.ValidationError = ValueError
frappe.DuplicateEntryError = type("DuplicateEntryError", (Exception,), {})
frappe.QueryDeadlockError = type("QueryDeadlockError", (Exception,), {})  # 1213（共用重试 `conflicts.retried` 认它）
frappe.TimestampMismatchError = type("TimestampMismatchError", (Exception,), {})
frappe.clear_messages = lambda: None
frappe.session = types.SimpleNamespace(user="boss@x")
frappe.local = types.SimpleNamespace(site="erp.test")
frappe.get_roles = lambda user=None: ["ERP Admin"] if user == "boss@x" else []
frappe.enqueue = lambda method, **kw: ENQUEUED.append((method, kw))


def _throw(message, exc=ValueError):
    raise exc(message)


def _get_all(doctype, filters=None, fields=None, order_by=None, limit_page_length=None):
    rows = [Row(DB.stale.get((doctype, key), row)) for key, row in DB.table(doctype).items()]
    rows = [row for row in rows
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
from pearnly_erp_portal.mrerp import catalog, connection, contracts, push  # noqa: E402
from pearnly_erp_portal.express import contracts as express_contracts  # noqa: E402

# 选账套与主档整读在公司接入一侧（`mrerp.catalog.pick` / `books_of`、`mrerp.masters.after_write`，规则与它们自己的测试在那边）：
# 这里换成只给形状的替身——BOOKS 是每家公司的年度账套（没登记就是公司上记的那一本），CHOSEN 是「同期两本选过哪本」。
BOOKS, CHOSEN, AFTER_WRITE = {}, {}, []


def fake_books_of(company):
    name = company["name"] if isinstance(company, dict) else company
    if name in BOOKS:
        return BOOKS[name]
    row = DB.table("ERP Company").get(name) or {}
    return [{"key": row.get("mrerp_account_set"), "label": "TEST2026", "connection": row.get("mrerp_connection"),
             "period_start": "2026-01-01", "period_end": "2026-12-31", "next_end": None, "comidyear": "15"}] \
        if row.get("mrerp_account_set") else []


def fake_pick(company, day, wanted=None):
    name = company["name"] if isinstance(company, dict) else company
    every = fake_books_of(name)
    if wanted is not None:
        book = next((item for item in every if item["key"] == wanted), None) if wanted else None
        return {"book": book, "reason": "" if book else express_contracts.NO_BOOK, "options": [], "subject": ""}
    day = str(day or "")[:10]
    found = [item for item in every if item["period_start"] <= day <= item["period_end"]]
    if not found:
        return {"book": None, "reason": express_contracts.NO_BOOK, "options": [], "subject": ""}
    if len(found) == 1:
        return {"book": found[0], "reason": "", "options": [], "subject": ""}
    subject = f"year:{found[0]['period_start']}"
    book = next((item for item in found if item["key"] == CHOSEN.get((name, subject))), None)
    if book:
        return {"book": book, "reason": "", "options": [], "subject": subject}
    return {"book": None, "reason": express_contracts.SEVERAL_BOOKS, "subject": subject,
            "options": [{"code": item["key"], "name": item["label"]} for item in found]}


catalog.pick, catalog.books_of = fake_pick, fake_books_of
catalog.no_book_text = lambda company, day: f"no MR.ERP account set for {day}"
_masters = types.ModuleType("pearnly_erp_portal.mrerp.masters")
_masters.after_write = lambda company: AFTER_WRITE.append(company) or True
sys.modules["pearnly_erp_portal.mrerp.masters"] = _masters
mrerp.masters = _masters

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
    return E.document(ref=f"{name}@20261103090000000000", direction=push.DIRECTIONS[doctype],
                      doc_date=row.get("doc_date") or "2026-11-03",
                      number=name, company={"tax_id": "0105559000001", "branch": "00000"}, party=party,
                      paid=bool(row.get("paid")), has_vat=True, total="107.00", vat="7.00",
                      lines=[{"name": "x", "qty": "1", "amount": "100.00", "stock": True}])


def reset(enabled=True):
    DB.tables.clear()
    DB.stale.clear()
    DB.locked.clear()
    ENQUEUED.clear()
    CALLS.clear()
    BOOKS.clear()
    CHOSEN.clear()
    AFTER_WRITE.clear()
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


class SwitchDefault(unittest.TestCase):
    def test_the_switch_is_a_code_constant_and_on(self):
        """11-22 用户定对用户开放：常量是 True（不按站点 / 租户开）。"""
        self.assertIs(mrerp.MRERP_LEDGER_ENABLED, True)
        self.assertTrue(mrerp.enabled())


@patch.object(mrerp, "MRERP_LEDGER_ENABLED", False)
class SwitchedOff(unittest.TestCase):
    """关着（打补丁）：入口全挡、Express 那条一字不变——开关仍是唯一的总闸，关回去就回到没有 MR.ERP 时的样子。"""

    def setUp(self):
        reset()

    def test_the_switch_off_means_disabled(self):
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

    def test_a_write_asks_the_masters_to_be_read_once_and_a_failure_does_not(self):
        """公司接入的接缝（`mrerp.masters.after_write`，只排队、去重、不抛）：写进 MR.ERP（含写入核不上）后调一次；没导入成不调。"""
        doc(ledger_state="queued", ledger_target="mrerp")
        with _Module({"status": "success", "docnum": "PG691114-000002", "menus": {}, "masters": {}}):
            push.run(PURCHASE, "PUR-1", document)
        self.assertEqual(AFTER_WRITE, ["CO-1"])
        doc(name="PUR-2", ledger_state="queued", ledger_target="mrerp")
        with _Module({"status": "failed", "reason": "ERR_NO_SUPPLIER"}):
            push.run(PURCHASE, "PUR-2", document)
        self.assertEqual(AFTER_WRITE, ["CO-1"])

    def test_the_account_set_follows_the_document_date(self):
        """一家公司挂多本年度账套（公司接入第 3 批）：按单据日期选那本（`catalog.pick`），导入、存回的选项都用它、记在单据上。"""
        BOOKS["CO-1"] = [
            {"key": "14:1", "label": "TEST2025", "connection": "C1", "period_start": "2025-01-01", "period_end": "2025-12-31"},
            {"key": "15:1", "label": "TEST2026", "connection": "C1", "period_start": "2026-01-01", "period_end": "2026-12-31"}]
        doc(ledger_state="queued", ledger_target="mrerp", doc_date="2025-06-01")
        with _Module({"status": "success", "docnum": "PG680601-000001", "menus": {}, "masters": {}}):
            push.run(PURCHASE, "PUR-1", document)
        saved = DB.table(PURCHASE)["PUR-1"]
        self.assertEqual((saved["ledger_state"], saved["ledger_book"]), ("written", "14:1"))
        self.assertEqual(CALLS[0]["target"], {"account_set": "14:1"})

    def test_an_unset_conversation_account_set_still_picks_by_date(self):
        """MR.ERP 的年度账套还没列进 CC 对话账套：MR.ERP 公司在对话里时 `conversation.wanted` 回空串（对话账套 0 本 = 未定），
        不能当「对话账套未定」卡住——按单据日期选（与公司接入第 3 批 11-15 对齐）；对话定了某本时照那本。"""
        from pearnly_erp_portal.express import conversation

        BOOKS["CO-1"] = [
            {"key": "14:1", "label": "TEST2025", "connection": "C1", "period_start": "2025-01-01", "period_end": "2025-12-31"},
            {"key": "15:1", "label": "TEST2026", "connection": "C1", "period_start": "2026-01-01", "period_end": "2026-12-31"}]
        for wanted, expected in (("", "15:1"), ("14:1", "14:1")):
            CALLS.clear()
            doc(ledger_state="queued", ledger_target="mrerp")
            with patch.object(conversation, "wanted", lambda row, task=None, wanted=wanted: wanted), \
                    _Module({"status": "success", "docnum": "PG691103-000010", "menus": {}, "masters": {}}):
                push.run(PURCHASE, "PUR-1", document)
            self.assertEqual((DB.table(PURCHASE)["PUR-1"]["ledger_state"], CALLS[0]["target"]),
                             ("written", {"account_set": expected}))

    def test_no_account_set_for_the_date_holds_without_importing(self):
        doc(ledger_state="queued", ledger_target="mrerp", doc_date="2024-03-01")
        with _Module({"status": "success", "docnum": "x", "menus": {}, "masters": {}}):
            push.run(PURCHASE, "PUR-1", document)
        saved = DB.table(PURCHASE)["PUR-1"]
        self.assertEqual((saved["ledger_state"], saved["ledger_reason"]), ("held", express_contracts.NO_BOOK))
        self.assertEqual(CALLS, [])

    def test_two_account_sets_for_one_year_ask_once_on_the_document(self):
        """同期两本没选过：停在单据上问选哪本（同 Express 那项、记在同一个记忆）；选了再排，导入用选中的那本。"""
        BOOKS["CO-1"] = [
            {"key": "15:1", "label": "TEST2026", "connection": "C1", "period_start": "2026-01-01", "period_end": "2026-12-31"},
            {"key": "16:1", "label": "TEST2026 copy", "connection": "C1", "period_start": "2026-01-01",
             "period_end": "2026-12-31"}]
        doc(ledger_state="queued", ledger_target="mrerp")
        with _Module({"status": "success", "docnum": "x", "menus": {}, "masters": {}}):
            push.run(PURCHASE, "PUR-1", document)
        saved = DB.table(PURCHASE)["PUR-1"]
        self.assertEqual((saved["ledger_state"], saved["ledger_reason"]), ("held", contracts.CHOICE))
        needed = json.loads(saved["ledger_needed"])
        self.assertEqual([(item["kind"], item["subject"]) for item in needed], [("account_set", "year:2026-01-01")])
        self.assertEqual(CALLS, [])

        from pearnly_erp_portal.express import choices as express_choices

        def remember(company, kind, subject, value, label=""):
            CHOSEN[(company, subject)] = value
        with patch.object(express_choices, "remember", remember):
            self.assertFalse(push.answer(PURCHASE, "PUR-1", [{"kind": "account_set", "subject": "year:2026-01-01",
                                                              "value": "99:1"}]))
            self.assertTrue(push.answer(PURCHASE, "PUR-1", [{"kind": "account_set", "subject": "year:2026-01-01",
                                                             "value": "16:1"}]))
        self.assertEqual(DB.table(PURCHASE)["PUR-1"]["ledger_state"], "queued")
        with _Module({"status": "success", "docnum": "PG691103-000009", "menus": {}, "masters": {}}):
            push.run(PURCHASE, "PUR-1", document)
        self.assertEqual(CALLS[0]["target"], {"account_set": "16:1"})
        self.assertEqual(DB.table(PURCHASE)["PUR-1"]["ledger_book"], "16:1")

    def test_a_paid_purchase_is_imported_as_a_cash_purchase(self):
        # 11-14 起现购照写：票面付款 = cash，共用层第 9 列放现金供应商、第 10 列真实卖家（与 Cowork 同一份）
        doc(ledger_state="queued", ledger_target="mrerp", paid=True)
        with _Module({"status": "success", "docnum": "PG691114-000001", "menus": {}, "masters": {}}):
            push.run(PURCHASE, "PUR-1", document)
        saved = DB.table(PURCHASE)["PUR-1"]
        self.assertEqual((saved["ledger_state"], saved["ledger_docnum"]), ("written", "PG691114-000001"))
        self.assertEqual(CALLS[0]["payload"]["histories"][0]["fields"]["posting_payment_manual"], "cash")

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

    def test_a_person_checks_an_unknown_result_in_mrerp_and_records_it(self):
        """11-28 用户批准：结果不明的单人去 MR.ERP 核对后记下（同 Cowork 核对）：已入账记已写入 + 单号；没有入账回到可再推；
        别的状态不收；记一笔核对评论。"""
        from unittest import mock

        audits, refreshed = [], []
        masters = types.ModuleType("pearnly_erp_portal.mrerp.masters")
        masters.after_write = refreshed.append
        with mock.patch.object(push, "_audit", side_effect=lambda *a: audits.append(a)), \
                mock.patch.dict(sys.modules, {"pearnly_erp_portal.mrerp.masters": masters}), \
                mock.patch.object(sys.modules["pearnly_erp_portal.mrerp"], "masters", masters, create=True):
            doc(ledger_state="held", ledger_target="mrerp", ledger_reason=contracts.UNKNOWN + ": ERR_TECHNICAL")
            self.assertTrue(push.resolve_unknown(PURCHASE, "PUR-1", True, "  PG691128-000007 "))
            saved = DB.table(PURCHASE)["PUR-1"]
            self.assertEqual((saved["ledger_state"], saved["ledger_docnum"], saved["ledger_reason"]),
                             ("written", "PG691128-000007", ""))
            self.assertEqual(refreshed, ["CO-1"])  # 同写成：排一次主档整读
            self.assertFalse(push.resolve_unknown(PURCHASE, "PUR-1", False))  # 已不是结果不明：不收
            doc(ledger_state="held", ledger_target="mrerp", ledger_reason=contracts.UNKNOWN + ": ERR_TECHNICAL")
            self.assertTrue(push.resolve_unknown(PURCHASE, "PUR-1", False))
            saved = DB.table(PURCHASE)["PUR-1"]
            self.assertEqual((saved["ledger_state"], saved["ledger_reason"]), ("held", contracts.CONFIRMED_NOT_ENTERED))
            self.assertEqual(push.send(PURCHASE, "PUR-1"), {"status": "sent"})  # 没有入账：照常再推
            self.assertEqual(len(audits), 2)
            doc(ledger_state="held", ledger_target="mrerp", ledger_reason=contracts.UNKNOWN)
            with self.assertRaises(ValueError):
                push.resolve_unknown(PURCHASE, "PUR-1", "yes")  # 只收布尔（同 Cowork）

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


@patch.object(mrerp, "MRERP_LEDGER_ENABLED", True)
class ConclusionConflict(unittest.TestCase):
    """④ A2 #16 / #17 与巡检（11-28）：导入发出之后的几段写回加锁按现值再判，记结论撞冲突只重来记结论那一段、不再导入。"""

    def setUp(self):
        reset()
        self.sleep = patch("pearnly_core.transaction.conflicts.time.sleep")
        self.sleep.start()

    def tearDown(self):
        self.sleep.stop()

    def test_a_conflict_on_the_conclusion_retries_only_the_conclusion(self):
        """记结论那一步撞一次 1213：修前任务失败、单据停在「正在导入」（15 分钟后被巡检转成结果不明）；修后整笔重来只重来记结论，
        记成已写入、单号在，导入只发 1 次，结论前加锁读了这张。"""
        doc(ledger_state="queued", ledger_target="mrerp")
        real, hits = push._set, []

        def flaky(doctype, name, values):
            if values.get("ledger_state") == "written" and not hits:
                hits.append(name)
                raise frappe.QueryDeadlockError("1213 Deadlock found when trying to get lock")
            return real(doctype, name, values)

        with _Module({"status": "success", "docnum": "PG691128-000001", "menus": {}, "masters": {}}), \
                patch.object(push, "_set", flaky):
            push.run(PURCHASE, "PUR-1", document)
        saved = DB.table(PURCHASE)["PUR-1"]
        self.assertEqual((saved["ledger_state"], saved["ledger_docnum"], saved["ledger_reason"]),
                         ("written", "PG691128-000001", ""))
        self.assertEqual((len(CALLS), hits), (1, ["PUR-1"]))
        self.assertIn((PURCHASE, "PUR-1"), DB.locked[1:])  # 领取那次之后，记结论前又加锁读了一次
        self.assertEqual(AFTER_WRITE, ["CO-1"])

    def test_the_conclusion_judges_the_void_on_the_locked_row(self):
        """导入期间作废（`express.push.void` 清了账本栏、状态改作废）而本事务普通读还停在作废前：按加锁读的现值转人工、单号留着。"""
        from pearnly_erp_portal.express import contracts as E

        doc(ledger_state="queued", ledger_target="mrerp")

        def voided_meanwhile(*args):
            DB.stale[(PURCHASE, "PUR-1")] = dict(DB.table(PURCHASE)["PUR-1"])  # 普通读看到的是作废前那一份
            DB.table(PURCHASE)["PUR-1"].update(status="void", ledger_state=None, ledger_reason="")
            return {"status": "success", "docnum": "PG691128-000002", "menus": {}, "masters": {}}

        with _Module({}):
            sys.modules["pearnly_core.mrerp.push.execute"].execute = voided_meanwhile
            push.run(PURCHASE, "PUR-1", document)
        saved = DB.table(PURCHASE)["PUR-1"]
        self.assertEqual((saved["ledger_state"], saved["ledger_reason"], saved["ledger_docnum"]),
                         ("held", E.VOIDED_AFTER_WRITE, "PG691128-000002"))

    def test_a_conclusion_taken_by_someone_else_is_not_overwritten_unless_it_was_written(self):
        """导入期间这张已不是本次领取（巡检转成了结果不明）：没写进 MR.ERP 的不盖；写进了的照实记已写入与单号。"""
        for outcome, expected in (({"status": "failed", "reason": "ERR_NO_SUPPLIER"},
                                   ("held", f"{contracts.UNKNOWN}: ERR_IMPORT_INTERRUPTED", None)),
                                  ({"status": "success", "docnum": "PG691128-000003"}, ("written", "", "PG691128-000003"))):
            reset()
            doc(ledger_state="queued", ledger_target="mrerp")

            def taken(*args, outcome=outcome):
                DB.table(PURCHASE)["PUR-1"].update(ledger_state="held",
                                                   ledger_reason=f"{contracts.UNKNOWN}: ERR_IMPORT_INTERRUPTED")
                return {**outcome, "menus": {}, "masters": {}}

            with _Module({}):
                sys.modules["pearnly_core.mrerp.push.execute"].execute = taken
                push.run(PURCHASE, "PUR-1", document)
            saved = DB.table(PURCHASE)["PUR-1"]
            self.assertEqual((saved["ledger_state"], saved["ledger_reason"], saved.get("ledger_docnum")), expected, outcome)

    def test_written_after_the_poll_turned_it_unknown_and_it_was_voided_follows_the_void_rule(self):
        """巡检转成结果不明之后又在 ERP 作废（作废清账本栏、状态改作废），导入其实写进了：按「写进去后作废」转人工、单号留着，
        不记成干净的已写入；没写进的清账本栏。"""
        from pearnly_erp_portal.express import contracts as E

        for outcome, expected in (({"status": "success", "docnum": "PG691128-000005"},
                                   ("held", E.VOIDED_AFTER_WRITE, "PG691128-000005")),
                                  ({"status": "failed", "reason": "ERR_BOOM"}, (None, "", None))):
            reset()
            doc(ledger_state="queued", ledger_target="mrerp")

            def voided(*args, outcome=outcome):
                DB.table(PURCHASE)["PUR-1"].update(status="void", ledger_state=None, ledger_reason="")
                return {**outcome, "menus": {}, "masters": {}}

            with _Module({}):
                sys.modules["pearnly_core.mrerp.push.execute"].execute = voided
                push.run(PURCHASE, "PUR-1", document)
            saved = DB.table(PURCHASE)["PUR-1"]
            self.assertEqual((saved["ledger_state"], saved["ledger_reason"], saved.get("ledger_docnum")), expected, outcome)

    def test_landed_while_the_document_moved_to_another_state_goes_to_a_person_and_is_not_imported_again(self):
        """主会话 11-28 口径：导入写进了（或结果不明）、记结论时这张已被别的路径改成意外状态（这里是要人选）：停在结果不明转人工，
        写进了的 MR.ERP 单号留在单据上，记一条错误日志；之后再写一次 / 巡检 / 选一次 / 再跑任务都不再导入。"""
        for outcome, docnum in (({"status": "success", "docnum": "PG691128-000006"}, "PG691128-000006"),
                                ({"status": "unknown", "reason": "ERR_IMPORT_SENT_NO_RESULT"}, "")):
            with self.subTest(status=outcome["status"]):
                reset()
                doc(ledger_state="queued", ledger_target="mrerp")
                logged = []

                def moved(*args, outcome=outcome):
                    DB.table(PURCHASE)["PUR-1"].update(ledger_state="held", ledger_reason=contracts.CHOICE,
                                                       ledger_needed='[{"kind": "mrerp_menu", "subject": "purchase", "options": []}]')
                    return {**outcome, "menus": {}, "masters": {}}

                with _Module({}), patch.object(frappe, "log_error", lambda *a, **k: logged.append(k)):
                    sys.modules["pearnly_core.mrerp.push.execute"].execute = moved
                    push.run(PURCHASE, "PUR-1", document)
                saved = DB.table(PURCHASE)["PUR-1"]
                self.assertEqual((saved["ledger_state"], saved["ledger_reason"], saved.get("ledger_docnum") or "",
                                  saved["ledger_needed"]),
                                 ("held", f"{contracts.UNKNOWN}: {contracts.MOVED_DURING_IMPORT}", docnum, None))
                self.assertEqual([k.get("title") for k in logged if "MR.ERP" in str(k.get("title"))],
                                 ["ERP MR.ERP import landed after the document moved on"])
                self.assertEqual(len(CALLS), 0)  # 替身换掉了 execute，CALLS 不计；下面再跑也不能有导入
                ENQUEUED.clear()
                self.assertEqual(push.send(PURCHASE, "PUR-1"), {"status": "held", "reason": contracts.UNKNOWN})
                self.assertFalse(push.answer(PURCHASE, "PUR-1", [{"kind": "mrerp_menu", "subject": "purchase", "value": "1"}]))
                push.poll()
                self.assertEqual(ENQUEUED, [])
                with _Module({"status": "success", "docnum": "PG-SECOND"}):
                    push.run(PURCHASE, "PUR-1", document)
                self.assertEqual((CALLS, DB.table(PURCHASE)["PUR-1"].get("ledger_docnum") or ""), ([], docnum))

    def test_send_again_while_importing_on_an_old_read_view_leaves_the_claim_alone(self):
        """④ code-review 2：「再写一次」的那笔事务读视图早于任务领取提交（连点两次、网页 + CC 同时点）：普通读看到「排队中 + 空原因」、
        把原因改回空，盖掉了「正在导入」；导入回来记结论时这张已不是本次领取，转成结果不明。修后加锁读现值：正在导入就不动。"""
        doc(ledger_state="queued", ledger_target="mrerp")
        answers = []

        def resend_meanwhile(*args):
            DB.stale[(PURCHASE, "PUR-1")] = {**DB.table(PURCHASE)["PUR-1"], "ledger_reason": ""}  # 领取提交前的读视图
            answers.append(push.send(PURCHASE, "PUR-1"))
            return {"status": "success", "docnum": "PG691128-000009", "menus": {}, "masters": {}}

        with _Module({}):
            sys.modules["pearnly_core.mrerp.push.execute"].execute = resend_meanwhile
            push.run(PURCHASE, "PUR-1", document)
        saved = DB.table(PURCHASE)["PUR-1"]
        self.assertEqual(answers, [{"status": "already_queued"}])
        self.assertEqual((saved["ledger_state"], saved["ledger_reason"], saved["ledger_docnum"]),
                         ("written", "", "PG691128-000009"))
        self.assertEqual(len(ENQUEUED), 0)  # 没再排一次导入

    def test_a_mismatched_write_on_a_document_voided_while_importing_keeps_its_number(self):
        """④ code-review 3：写进了但核不上（mismatch）、导入期间单据作废：同「写进去后作废」转人工、单号留着（修前账本栏清空、单号丢了）。"""
        from pearnly_erp_portal.express import contracts as E

        doc(ledger_state="queued", ledger_target="mrerp")

        def voided(*args):
            DB.table(PURCHASE)["PUR-1"].update(status="void", ledger_state=None, ledger_reason="")
            return {"status": "success", "docnum": "PG691128-000010", "meta": {"verify": {"state": "mismatch"}},
                    "menus": {}, "masters": {}}

        with _Module({}):
            sys.modules["pearnly_core.mrerp.push.execute"].execute = voided
            push.run(PURCHASE, "PUR-1", document)
        saved = DB.table(PURCHASE)["PUR-1"]
        self.assertEqual((saved["ledger_state"], saved["ledger_reason"], saved.get("ledger_docnum")),
                         ("held", E.VOIDED_AFTER_WRITE, "PG691128-000010"))

    def test_the_poll_rechecks_under_the_lock_before_turning_an_import_unknown(self):
        """巡检普通读列出一张超时的「正在导入」，此间导入刚记成已写入：修前按列出时的旧值盖成结果不明；修后加锁按现值再核、不动。
        仍是超时那次领取的照旧转结果不明。"""
        doc(ledger_state="queued", ledger_target="mrerp", ledger_reason=contracts.IMPORTING,
            ledger_sent_at=NOW - datetime.timedelta(minutes=16))
        DB.stale[(PURCHASE, "PUR-1")] = dict(DB.table(PURCHASE)["PUR-1"])
        DB.table(PURCHASE)["PUR-1"].update(ledger_state="written", ledger_reason="", ledger_docnum="PG691128-000004")
        doc(name="PUR-2", ledger_state="queued", ledger_target="mrerp", ledger_reason=contracts.IMPORTING,
            ledger_sent_at=NOW - datetime.timedelta(minutes=16))
        push.poll()
        first, second = DB.table(PURCHASE)["PUR-1"], DB.table(PURCHASE)["PUR-2"]
        self.assertEqual((first["ledger_state"], first["ledger_reason"], first["ledger_docnum"]),
                         ("written", "", "PG691128-000004"))
        self.assertEqual((second["ledger_state"], second["ledger_reason"]),
                         ("held", f"{contracts.UNKNOWN}: ERR_IMPORT_INTERRUPTED"))
        self.assertIn((PURCHASE, "PUR-1"), DB.locked)

    def test_the_poll_leaves_an_import_claimed_again_meanwhile(self):
        """列出时是超时的那次，拿到锁时已是新的一次领取（新的发出时刻）：不改。"""
        doc(ledger_state="queued", ledger_target="mrerp", ledger_reason=contracts.IMPORTING,
            ledger_sent_at=NOW - datetime.timedelta(minutes=16))
        DB.stale[(PURCHASE, "PUR-1")] = dict(DB.table(PURCHASE)["PUR-1"])
        DB.table(PURCHASE)["PUR-1"]["ledger_sent_at"] = NOW
        push.poll()
        self.assertEqual(DB.table(PURCHASE)["PUR-1"]["ledger_reason"], contracts.IMPORTING)

    def test_observed_options_merge_into_the_latest_whole_record(self):
        """④ A2 #17：同一条连接两本账套同时导入，存回选项时整份写回——加锁读到另一本刚存的那格，只并本账套那一格。"""
        from pearnly_erp_portal.mrerp import choices

        connection_row = DB.table("ERP MRERP Connection")["C1"]
        connection_row["menu_options"] = None
        DB.stale[("ERP MRERP Connection", "C1")] = dict(connection_row)  # 本事务读视图里还没有别的账套
        connection_row["menu_options"] = json.dumps({"14:1": {"pages": {"impaptran": [{"idmenu": "55", "label": "x",
                                                                                     "linked": True}]},
                                                               "observed_at": "2026-11-28T09:00:00"}})
        choices.observe("C1", "15:1", {"impaptran": [{"idmenu": "68", "label": "ซื้อ", "linked": True}]}, None)
        saved = json.loads(connection_row["menu_options"])
        self.assertEqual(sorted(saved), ["14:1", "15:1"])
        self.assertEqual(saved["14:1"]["pages"]["impaptran"][0]["idmenu"], "55")
        self.assertIn(("ERP MRERP Connection", "C1"), DB.locked)
        locked = len(DB.locked)
        choices.observe("C1", "15:1", None, None)  # 什么都没读到：不写、不锁
        self.assertEqual(len(DB.locked), locked)


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
            # 11-14 公司接入第 3 批：人在从连接添加 / 公司页选账套后的那次主档整读（往下传的几层）
            ("apps/pearnly_erp_portal/pearnly_erp_portal/mrerp/catalog.py", "choose", "manual"),
            ("apps/pearnly_erp_portal/pearnly_erp_portal/mrerp/masters.py", "queue", "manual"),
            ("apps/pearnly_erp_portal/pearnly_erp_portal/mrerp/masters.py", "run", "manual"),
            # 11-22 公司页「同步 MR.ERP」（人点的，同「同步 Express」）
            ("apps/pearnly_erp_portal/pearnly_erp_portal/mrerp/masters.py", "start", "manual"),
            ("apps/pearnly_core/pearnly_core/mrerp/master_export.py", "read", "manual"),
            ("apps/pearnly_erp_portal/pearnly_erp_portal/express/api.py", "retry", "manual"),  # 单据页「再写一次」（B13）
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
            ("apps/pearnly_cowork/pearnly_cowork/push/application/card_save.py", "save", "manual"),  # LINE 卡片「保存」（人点的，11-19）
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
                                 # 11-17 ERP 收款直写的收款导入（impfarev）
                                 "apps/pearnly_core/pearnly_core/mrerp/push/receipt.py": True,
                                 # 11-19 ERP 库存单直写的入库 / 出库导入（impstktranrec / impstktraniss）
                                 "apps/pearnly_core/pearnly_core/mrerp/push/stock.py": True,
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

    def insert(self, ignore_permissions=False, set_name=None):
        table = DB.table(self["doctype"])
        if set_name and set_name in table:
            raise frappe.DuplicateEntryError(set_name)   # 同一主键已被别的请求插进去（库的唯一约束）
        self["name"] = set_name or f"C{len(table) + 1}"
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

    def test_the_authorized_time_is_utc_with_a_zone_not_the_raw_site_time(self):
        """11-28：原先 ``str()`` 原样给，设置 → 连接显示「2026-11-28 11:08:35.095638」。站点时区的裸时间 → RFC3339 UTC，页面写到时分。"""
        DB.table("ERP MRERP Connection")["C1"]["credential_authorized_at"] = datetime.datetime(2026, 11, 28, 11, 8, 35, 95638)
        with patch.object(frappe.utils, "get_system_timezone", lambda: "Asia/Bangkok", create=True):
            rows = self.api.records(limit=100)
            self.assertEqual(rows[0]["credential"]["at"], "2026-11-28T04:08:35.095638Z")
            DB.table("ERP MRERP Connection")["C1"]["credential_authorized_at"] = "2026-11-28 11:08:35.095638"  # 读成字符串也一样
            self.assertEqual(self.api.records(limit=100)[0]["credential"]["at"], "2026-11-28T04:08:35.095638Z")

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

    def test_reopening_the_dialog_reuses_the_connection_of_the_same_system(self):
        # 11-23 testerp 多出一条没授权的空连接：重开弹窗（新请求号）、旧调法不带连接号，同一系统地址都回原来那一条，不新建
        DB.table("ERP MRERP Connection").clear()
        first = self.api.configure(adapter="mrerp", request_key="k1", config={"system_url": "https://mrerp.example"})
        for again in (self.api.configure(adapter="mrerp", request_key="k9", config={"system_url": "https://MRERP.example/"}),
                      self.api.configure(system_url="https://mrerp.example.")):
            self.assertEqual(again["name"], first["name"])
        self.assertEqual(len(DB.table("ERP MRERP Connection")), 1)
        # 另一个系统地址才是另一条连接
        other = self.api.configure(adapter="mrerp", request_key="k10", config={"system_url": "https://erp.example.co.th"})
        self.assertNotEqual(other["name"], first["name"])
        self.assertEqual(len(DB.table("ERP MRERP Connection")), 2)

    def test_two_requests_with_the_same_key_at_once_leave_one_connection(self):
        # 同一请求号同时到两次：都没看见对方还没提交的那条，后插的撞主键，回同一条，不报错
        from pearnly_erp_portal.mrerp import connection

        DB.table("ERP MRERP Connection").clear()
        with patch.object(connection, "same_system", lambda url: None), patch.object(DB, "exists", lambda *a: False):
            first = connection.configure_once("https://mrerp.example", None, "k1")
            second = connection.configure_once("https://mrerp.example", None, "k1")
        self.assertEqual(first, second)
        self.assertEqual(list(DB.table("ERP MRERP Connection")), [first])
        self.assertTrue(first.startswith("mrerp-"))

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
            [{"source_id": "15:1", "label": "DEMO-A"}, {"source_id": "16:1", "label": "DEMO-B"}])
        from pearnly_erp_portal.mrerp import catalog
        self.catalog = catalog

    def test_a_linked_company_shows_its_account_set_by_name(self):
        self.assertEqual(self.catalog.company_view("CO-1"),
                         {"connection": "C1", "account_set": "15:1", "label": "DEMO-A", "linked": True})

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
        """11-14 测试账套真写：转账收款的销售记进了现金栏、没税号客户的未收款销售被改成现销挂「เงินสด」
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

    def test_items_with_a_ledger_code_go_into_the_product_lookup(self):
        """11-24 测试账套真写：从 MR.ERP 读来的物料（记着它的商品代码）原来不带代码，导入按品名新建、撞上同名已有商品，报
        「รหัสสินค้าเป็นค่าว่าง」。带代码的行放进共用层商品查找表（同名同码只放一次），没代码的行照旧按品名新建；没有就不放这一项。"""
        body = {"items": [{"name": "เม็ดสแคป", "code": "เม็ดสแคป"}, {"name": "เม็ดสแคป", "code": "เม็ดสแคป"},
                          {"name": "ของใหม่", "code": ""}, {"name": " MOBILUX EP ", "code": "หล่อลื่น021"}]}
        found = contracts.products(body)
        self.assertEqual(found, [{"item_name": "เม็ดสแคป", "erp_code": "เม็ดสแคป"},
                                 {"item_name": "MOBILUX EP", "erp_code": "หล่อลื่น021"}])
        self.assertEqual(contracts.payload({}, own_tax_id="", menus={}, defaults={}, products=found)["mappings"]["products"], found)
        self.assertNotIn("products", contracts.payload({}, own_tax_id="", menus={}, defaults={})["mappings"])
        self.assertEqual(contracts.products({"items": [{"name": "ของใหม่"}]}), [])
        # 只认账本是 MR.ERP 的物料代码（从 Express 同步来的旧档代码不放，照旧按品名新建）；同名不同码的认不准，不放
        self.assertEqual(contracts.products(body, {"หล่อลื่น021"}), [{"item_name": "MOBILUX EP", "erp_code": "หล่อลื่น021"}])
        twins = {"items": [{"name": "น้ำมัน", "code": "A"}, {"name": "น้ำมัน", "code": "B"}, {"name": "X", "code": "C"}]}
        self.assertEqual(contracts.products(twins), [{"item_name": "X", "erp_code": "C"}])

    def test_the_mrerp_number_is_stable_per_document_and_hex(self):
        self.assertEqual(contracts.history_id("PUR-1@1"), contracts.history_id("PUR-1@1"))
        self.assertNotEqual(contracts.history_id("PUR-1@1"), contracts.history_id("PUR-1@2"))
        int(contracts.history_id("PUR-1@1")[-8:], 16)


if __name__ == "__main__":
    unittest.main()
