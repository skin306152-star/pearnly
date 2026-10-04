"""Zero-I/O regressions for the DMS issues found while recording the manual (2026-10).

Each case loads the real module under test with small stand-ins for Frappe, LINE output,
the session store and DMS. Nothing logs into DMS, sends LINE messages or touches a Site.
"""
from __future__ import annotations

import csv
import importlib
import io
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "apps/pearnly_dms/pearnly_dms"
sys.path.insert(0, str(ROOT / "apps/pearnly_dms"))

TEMPLATES = {
    "dms.card.TXT_RECEIPT_SAVE": "{action}|{customer_code}|{name}",
    "dms.card.qa.TXT_MASTER_EMPTY_ITEM": "EMPTY:{item}",
}


class Thrown(Exception):
    pass


def throw(message, exception=None):
    raise Thrown(message)


class Isolated(unittest.TestCase):
    """Each test imports fresh copies of the Pearnly modules over its own stand-ins."""

    def setUp(self):
        guard = patch.dict(sys.modules)
        guard.start()
        self.addCleanup(guard.stop)
        for name in list(sys.modules):
            if name == "frappe" or name.startswith(("frappe.", "pearnly_core", "pearnly_dms")):
                del sys.modules[name]
        self.frappe = self.stub("frappe", _=lambda text: text, throw=throw,
                                whitelist=lambda **kw: (lambda function: function),
                                local=types.SimpleNamespace(site="lab.localhost", lang="zh"),
                                PermissionError=PermissionError, ValidationError=ValueError)
        self.stub("frappe.utils", getdate=lambda value: value, now_datetime=lambda: "now")
        self.frappe.utils = sys.modules["frappe.utils"]
        self.stub("pearnly_core", __path__=[])
        self.stub("pearnly_dms.i18n", t=self.text, line_lang=lambda: "th",
                  all_languages=lambda key: {"th": key})

    @staticmethod
    def text(key, lang=None, **kwargs):
        template = TEMPLATES.get(key, key)
        return template.format(**kwargs) if kwargs else template

    def stub(self, name, **attrs):
        module = types.ModuleType(name)
        module.__dict__.update(attrs)
        sys.modules[name] = module
        parent, _, child = name.rpartition(".")
        if parent in sys.modules:
            setattr(sys.modules[parent], child, module)
        return module


class FakeStore:
    def __init__(self, session=None):
        self.session = session
        self.writes = []

    def get_session(self, line_user_id):
        return self.session

    def set_session(self, line_user_id, state, payload):
        self.writes.append((state, payload))
        self.session = {"state": state, "payload": payload}

    def clear_session(self, line_user_id):
        self.writes.append(("cleared", None))
        self.session = None

    def verify_nonce(self, sess, nonce, state=None):
        return bool(nonce) and ((sess or {}).get("payload") or {}).get("nonce") == nonce


# ── 8 / 9：菜单会话与「ใช้ข้อมูลเดิม」 ─────────────────────────────────────────
class MenuSessionTests(Isolated):
    def setUp(self):
        super().setUp()
        self.store = FakeStore()
        self.stub("pearnly_core.line_session", **{k: getattr(self.store, k) for k in (
            "get_session", "set_session", "clear_session", "verify_nonce")})
        self.started, self.replies, self.pushes, self.sends, self.queued = [], [], [], [], []
        self.stub("pearnly_dms.booking.conversation.booking_qa",
                  start=lambda *a, **kw: self.started.append(kw))
        self.stub("pearnly_dms.menu.menu_cards", menu_card=lambda **kw: {"type": "flex"})
        self.stub("pearnly_dms.query.query_access", can_query=lambda binding: False)
        self.stub("pearnly_dms.line.out",
                  send=lambda uid, msg, token="": self.sends.append((msg, token)),
                  push_or_reply_text=lambda uid, text, token="": self.pushes.append((text, token)),
                  reply_text=lambda token, text: self.replies.append(text))
        self.stub("pearnly_dms.intake.flow", run_dedup=Mock(__name__="run_dedup"),
                  _enqueue=lambda job, fn, *args: self.queued.append((job, args)))
        # 提示发身份证的文字带「扫身份证」快捷按钮（`intake.scan.prompt`）。
        self.stub("pearnly_dms.intake.scan",
                  prompt=lambda text: {"type": "text", "text": text, "quickReply": "scan"})
        self.menu = importlib.import_module("pearnly_dms.menu.menu_flow")
        self.cards = importlib.import_module("pearnly_dms.line.cards")

    def collected(self, state):
        return {"state": state, "payload": {"id_card": {"name": "ก"}, "phone": "0899991234",
                                            "endpoint_id": "EP", "id_card_mid": "M1",
                                            "nonce": "n1", "mode": "customer"}}

    def test_menu_one_with_a_pending_customer_card_starts_over(self):
        self.menu.handle_postback({"user_id": "u"}, "U1", "tok", self.cards.ACT_MENU_CUSTOMER, {},
                                  self.collected("reviewing"))
        self.assertEqual(self.store.writes, [("collecting", {"mode": "customer"})])
        self.assertEqual(self.queued, [], "the old card must not be looked up and sent again")
        self.assertEqual(self.sends, [({"type": "text", "text": "dms.card.TXT_MENU_SEND_CARD",
                                        "quickReply": "scan"}, "tok")])

    def test_menu_word_with_a_pending_card_drops_it(self):
        self.menu.open_menu({"user_id": "u"}, "U1", "tok", self.collected("reviewing"))
        self.assertEqual(self.store.writes, [("menu", {})])
        for state in ("booking_qa", "booking_review", "query_results", "approval_pending"):
            self.store.writes.clear()
            self.menu.handle_postback({"user_id": "u"}, "U1", "tok", self.cards.ACT_MENU_BOOKING, {},
                                      self.collected(state))
            self.assertEqual(self.store.writes, [("collecting", {"mode": "booking"})], state)

    def test_material_collected_before_the_card_is_still_carried(self):
        self.menu.open_menu({"user_id": "u"}, "U1", "tok", self.collected("collecting"))
        menu_payload = self.store.writes[-1][1]
        self.assertEqual(menu_payload["phone"], "0899991234")
        self.assertNotIn("nonce", menu_payload)
        self.menu.handle_choice({"user_id": "u"}, "U1", "tok", self.store.session, "1")
        self.assertEqual(self.store.writes[-1][0], "collecting")
        self.assertEqual(self.store.writes[-1][1]["mode"], "customer")
        self.assertEqual([job for job, _ in self.queued], ["dedup"])

    def test_keep_result_uses_the_button_reply_token(self):
        self.store.session = {"state": "reviewing", "payload": {}}
        self.menu.after_customer_saved({"user_id": "u"}, "U1", endpoint_id="EP", customer_id="132",
                                       draft={}, mode="customer", same_data=True, reply_token="tok")
        self.assertEqual(self.pushes, [("dms.card.TXT_DONE_SAME", "tok")])
        self.menu.after_customer_saved({"tenant_id": "t", "user_id": "u"}, "U1", endpoint_id="EP",
                                       customer_id="132", draft={"name": "A"}, mode="booking",
                                       same_data=True, reply_token="tok2")
        self.assertEqual(self.started[-1]["reply_token"], "tok2")


class KeepDataPostbackTests(Isolated):
    def setUp(self):
        super().setUp()
        self.store = FakeStore({"state": "reviewing", "payload": {
            "nonce": "n1", "customer_id": "132", "endpoint_id": "EP", "mode": "customer",
            "draft": {"name": "A"}}})
        self.stub("pearnly_core.line_session", **{k: getattr(self.store, k) for k in (
            "get_session", "set_session", "clear_session", "verify_nonce")})
        self.replies = []
        self.stub("pearnly_core.line", reply=lambda token, msgs: self.replies.append(msgs))
        for name in ("pearnly_dms.intake.draft", "pearnly_dms.intake.service", "pearnly_dms.ocr.ocr_review",
                     "pearnly_dms.ocr.ocr_service", "pearnly_dms.ocr.timing", "pearnly_dms.line.out",
                     "pearnly_dms.line.identity", "pearnly_dms.config.pacing"):
            self.stub(name)
        sys.modules["pearnly_dms.line.identity"].matches = lambda *a: True
        sys.modules["pearnly_dms.ocr.timing"].traced = lambda name: (lambda function: function)
        self.stub("pearnly_dms.booking.conversation.booking_flow", BOOKING_ACTIONS=frozenset())
        self.stub("pearnly_dms.query.query_flow", QUERY_ACTIONS=frozenset())
        self.stub("pearnly_dms.approval.approval_flow", APPROVAL_ACTIONS=frozenset())
        self.saved = Mock()
        self.stub("pearnly_dms.menu.menu_flow", MENU_ACTIONS=frozenset(), after_customer_saved=self.saved)
        self.flow = importlib.import_module("pearnly_dms.intake.flow")

    def test_keep_does_not_echo_the_button_before_the_result(self):
        self.flow.handle_postback({"user_id": "u"}, "U1", "tok", {"data": "action=keep&nonce=n1"})
        self.assertEqual(self.replies, [], "no 'ใช้ข้อมูลเดิมต่อ' echo before the real answer")
        self.assertEqual(self.saved.call_args.kwargs["reply_token"], "tok")
        self.assertTrue(self.saved.call_args.kwargs["same_data"])

    def test_retired_keep_text_is_gone_from_the_terms(self):
        terms = (APP / "i18n/messages.tsv").read_text(encoding="utf-8")
        self.assertNotIn("\tdms.card.TXT_KEEP\n", terms)


# ── 5：订车问答里发错图 ────────────────────────────────────────────────────────
class BookingImageTests(Isolated):
    def setUp(self):
        super().setUp()
        self.store = FakeStore()
        self.stub("pearnly_core.line_session", **{k: getattr(self.store, k) for k in (
            "get_session", "set_session", "clear_session", "verify_nonce")})
        self.sent = []
        self.stub("pearnly_dms.line.out", send=lambda uid, msg, token=None: self.sent.append((msg, token)))
        self.stub("pearnly_dms.booking.conversation.qa_cards", TXT_NO_IMAGE_NEEDED="no-image")
        for name in ("booking_qa_pages", "booking_qa_payment", "booking_qa_start", "booking_qa_sync",
                     "booking_qa_transfer"):
            self.stub("pearnly_dms.booking.conversation." + name)
        self.stub("pearnly_dms.masters.advisor", resolve_operator_advisor=Mock())
        self.stub("pearnly_dms.ocr.ocr_service", resolve_dms_endpoint=Mock())
        self.qa = importlib.import_module("pearnly_dms.booking.conversation.booking_qa")

    def session(self, step):
        return {"state": "booking_qa", "payload": {"qa": {"step": step, "files": {}, "audit": []}}}

    def test_photo_on_a_question_step_is_answered_and_not_recognised(self):
        sess = self.session("place")
        self.assertTrue(self.qa.handle_image("t", "U1", "MID", "tok", sess=sess))
        self.assertEqual(self.sent, [({"type": "text", "text": "no-image"}, "tok")])
        self.assertEqual(self.store.writes, [], "the booking session is not rewritten")

    def test_slip_step_still_takes_the_photo(self):
        self.qa.send_step = Mock()
        sess = self.session("slip")
        self.assertTrue(self.qa.handle_image("t", "U1", "MID", "tok", sess=sess))
        qa = self.store.writes[-1][1]["qa"]
        self.assertEqual((qa["files"]["slip_mid"], qa["step"]), ("MID", "place"))

    def test_other_states_are_left_to_the_id_card_flow(self):
        self.assertFalse(self.qa.handle_image("t", "U1", "MID", "tok", sess={"state": "collecting"}))


class BookingReviewImageTests(Isolated):
    def test_photo_while_the_booking_preview_waits_does_not_restart(self):
        replies = []
        session = {"state": "booking_review", "payload": {"qa": {}}}
        self.stub("pearnly_core.line", reply=lambda token, msgs: replies.append(msgs))
        self.stub("pearnly_core.line_binding", user_of=lambda uid: "op@x")
        self.stub("pearnly_core.line_session", get_session=lambda uid: session)
        self.stub("pearnly_dms.booking.booking_attempt")
        self.stub("pearnly_dms.booking.conversation.booking_qa", handle_image=Mock(return_value=False))
        self.stub("pearnly_dms.booking.conversation.qa_cards", TXT_NO_IMAGE_NEEDED="no-image")
        intake = self.stub("pearnly_dms.intake.flow", handle_image=Mock())
        handlers = importlib.import_module("pearnly_dms.line.handlers")
        handlers._binding = lambda uid, bound: {"user_id": bound}
        handled = handlers._handle({"type": "message", "replyToken": "tok", "source": {"userId": "U1"},
                                    "message": {"type": "image", "id": "MID"}})
        self.assertTrue(handled)
        self.assertEqual(replies, [[{"type": "text", "text": "no-image"}]])
        intake.handle_image.assert_not_called()


# ── 10：刷卡 / 支票银行目录 ────────────────────────────────────────────────────
class ChannelBankTests(Isolated):
    MASTERS = {"place_books": [["1", "P", "Place"]], "cars": [["1", "C", "Car"]],
               "term_sales": [["1", "T", "Term"]], "regis_behalfs": [["1", "R", "Regis"]],
               "company_banks": [["9", "K", "KBank", "1", "x"]], "card_banks": [],
               "cheque_banks": [["3", "S", "SCB"]], "cashier_banks": []}

    def setUp(self):
        super().setUp()
        from pearnly_dms.contracts.client_base import DmsClientError

        masters = self.MASTERS

        class Directory:
            def __init__(self, endpoint):
                self.endpoint = endpoint

            def masters(self):
                if self.endpoint == "missing":
                    raise DmsClientError("pending", "ERR_DMS_SNAPSHOT_PENDING")
                return dict(masters)

        self.stub("pearnly_dms.snapshots.directory", Directory=Directory)
        self.sync = importlib.import_module("pearnly_dms.booking.conversation.booking_qa_sync")
        self.contract = importlib.import_module("pearnly_dms.masters.master_contract")

    def read(self, key, endpoint="EP"):
        return self.sync.masters("t", "U1", {"endpoint_id": endpoint}, key, persist=lambda *a: None)

    def test_card_bank_step_reads_the_directory_instead_of_failing(self):
        self.assertEqual(self.read("card_banks"), [], "an empty DMS list is a valid answer")
        self.assertEqual(self.read("cheque_banks"), [["3", "S", "SCB"]])
        from pearnly_dms.masters.company_banks import manual_bank_allowed_for_rows

        self.assertTrue(manual_bank_allowed_for_rows("card_banks", self.read("card_banks")))

    def test_a_directory_absent_from_the_snapshot_is_still_unavailable(self):
        snapshot = self.contract.build_snapshot({k: v for k, v in self.MASTERS.items() if k != "card_banks"})
        with self.assertRaises(self.contract.MasterSyncError) as raised:
            self.contract.snapshot_rows(snapshot, "card_banks")
        self.assertEqual(raised.exception.code, "ERR_DMS_MASTER_UNAVAILABLE")


class MasterProblemTests(Isolated):
    def test_missing_dms_setting_names_the_item(self):
        self.stub("pearnly_dms.booking.booking_car_details")
        self.stub("pearnly_dms.editor.edit_link")
        self.stub("pearnly_dms.booking.booking_payments", payment_preview_detail=Mock())
        self.stub("pearnly_dms.line.errors", friendly=lambda code: {"th": "generic:" + code})
        cards = importlib.import_module("pearnly_dms.booking.conversation.qa_cards")
        self.assertEqual(cards.master_problem("ERR_DMS_MASTER_EMPTY", "term_sales")["text"],
                         "EMPTY:dms.card.qa.LBL_TERM")
        self.assertEqual(cards.master_problem("ERR_DMS_MASTER_EMPTY", "")["text"],
                         "generic:ERR_DMS_MASTER_EMPTY")
        self.assertEqual(cards.master_problem("ERR_DMS_MASTER_UNAVAILABLE", "cars")["text"],
                         "generic:ERR_DMS_MASTER_UNAVAILABLE")


# ── 7：回执与发送记录显示 DMS 客户编号 ────────────────────────────────────────
class CustomerCodeTests(Isolated):
    def test_receipt_shows_the_dms_customer_code(self):
        cards = importlib.import_module("pearnly_dms.line.cards")
        self.assertEqual(cards.receipt_text("1103700000001", "สมชาย", "create"),
                         "dms.card.TXT_ACTION_CREATE|1103700000001|สมชาย")
        self.assertEqual(cards.receipt_text("", "สมชาย", "update"),
                         "dms.card.TXT_ACTION_UPDATE|—|สมชาย")

    def test_receipt_template_uses_the_code_in_every_language(self):
        rows = [line.split("\t") for line in (APP / "i18n/messages.tsv").read_text(encoding="utf-8").splitlines()]
        receipt = next(row for row in rows if row[-1] == "dms.card.TXT_RECEIPT_SAVE")
        self.assertTrue(all("{customer_code}" in text and "{customer_id}" not in text for text in receipt[:4]))

    def test_write_through_returns_the_read_back_code(self):
        self.stub("pearnly_dms.adapter.admin_read", authoritative_read_session=lambda client: _Null())
        self.stub("pearnly_dms.client.snapshot_reader",
                  read_customer_complete=lambda client, cid: {"customer_id": cid, "cuscode": "C-0001"})
        self.stub("pearnly_dms.snapshots.store", verified_customer=Mock())
        writeback = importlib.import_module("pearnly_dms.adapter.snapshot_writeback")
        self.assertEqual(writeback.customer("EP", object(), "132", expected_scope="s")["cuscode"], "C-0001")
        self.assertIs(writeback.customer("EP", object(), "132", expected_scope=None), False)

    def test_records_list_shows_type_and_business_number(self):
        rows = [
            {"name": "CW-1", "business_key": "132", "error_msg": "", "request_mode": None,
             "request_operation": "customer_write", "cuscode": "C-0001"},
            {"name": "CW-2", "business_key": "133", "error_msg": "", "request_mode": None,
             "request_operation": "customer_write", "cuscode": None},
            {"name": "L-3", "business_key": "BK000002610000001", "error_msg": "", "request_mode": "booking",
             "request_operation": None, "cuscode": None},
            {"name": "L-4", "business_key": "140", "error_msg": "", "request_mode": "overwrite",
             "request_operation": None, "cuscode": "C-0002"},
        ]
        queries = []

        def sql(query, values=None, as_dict=False):
            queries.append(query)
            return [(len(rows),)] if "COUNT(*)" in query else [dict(row) for row in rows]

        self.frappe.db = types.SimpleNamespace(sql=sql)
        self.stub("pearnly_dms.records.access", DMS_SCOPE="1=1", LOG_SOURCE="FROM t l", require_access=lambda: None)
        records = importlib.import_module("pearnly_dms.query.records")
        items = records.list_records()["items"]
        self.assertEqual([(row["kind"], row["number"]) for row in items], [
            ("customer", "C-0001"), ("customer", ""), ("booking", "BK000002610000001"), ("customer", "C-0002")])
        self.assertTrue(all(key not in row for row in items for key in ("request_mode", "cuscode")))
        self.assertIn("$.cuscode", queries[-1])
        self.assertNotIn("l.request_body,", queries[-1], "raw payloads stay in the database")


class _Null:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# ── 1 / 4：操作员账号 ─────────────────────────────────────────────────────────
class OperatorAccountTests(Isolated):
    def setUp(self):
        super().setUp()
        self.inserted = []

        class Doc(types.SimpleNamespace):
            flags = types.SimpleNamespace()

            def insert(doc, **kw):
                self.inserted.append(doc.doctype)
                doc.name = doc.__dict__.get("name") or "OP-1"
                return doc

            def save(doc, **kw):
                self.inserted.append("save")

        self.doc = Doc
        system = [{"username": "dmstest001", "admin_username": "dmstest001"}]

        def get_all(doctype, filters=None, fields=None, pluck=None, **kw):
            if doctype == "Pearnly ERP Endpoint":
                return system
            if doctype == "DMS System Account":
                return ["dmstest002", "dmstest003"]
            return []

        self.existing = Doc(doctype="DMS Operator", name="OP-9", dms_username="sale02", display_name="A",
                            dms_password="old")
        self.frappe.session = types.SimpleNamespace(user="admin@x")
        self.frappe.get_roles = lambda user: ["DMS Admin"]
        self.frappe.get_all = get_all
        self.frappe.db = types.SimpleNamespace(exists=lambda *a, **k: True, commit=lambda: None,
                                               get_value=lambda *a, **k: "https://dms.example.test")
        self.frappe.get_doc = lambda arg, name=None: Doc(**arg) if isinstance(arg, dict) else self.existing
        self.frappe.clear_cache = lambda **kw: None
        self.stub("pearnly_core.endpoints", read_password=lambda *a: "")
        self.stub("pearnly_dms.operators.advisors", resolve=lambda value: {"id": "", "name": ""})
        self.stub("pearnly_dms.operators.line_account")
        self.probe = Mock(return_value={"ok": True, "missing": []})
        self.stub("pearnly_dms.adapter.account_probe", probe=self.probe)
        self.roster = importlib.import_module("pearnly_dms.operators.roster")
        self.roster._row = lambda doc: {"name": doc.name}

    def test_operator_cannot_reuse_a_system_account(self):
        for username in ("dmstest001", "dmstest003"):
            with self.assertRaises(Thrown) as raised:
                self.roster.create_operator("A", username, "pw")
            self.assertIn("already used", str(raised.exception))
        self.probe.assert_not_called()
        self.assertEqual(self.inserted, [])

    def test_new_operator_signs_in_once_before_anything_is_saved(self):
        self.roster.create_operator("A", "sale02", "pw")
        self.probe.assert_called_once_with("https://dms.example.test", "sale02", "pw", "login")
        self.assertEqual(self.inserted, ["User", "DMS Operator"])

    def test_failed_sign_in_names_the_reason_and_saves_nothing(self):
        for code in ("ERR_DMS_AUTH", "ERR_DMS_UNREACHABLE"):
            self.probe.return_value = {"ok": False, "error_code": code}
            with self.assertRaises(Thrown) as raised:
                self.roster.create_operator("A", "sale02", "bad")
            self.assertEqual(str(raised.exception), "dms.err." + code)
        self.assertEqual(self.inserted, [])

    def test_edit_signs_in_only_when_the_password_changes(self):
        self.roster.update_operator("OP-9", display_name="B")
        self.probe.assert_not_called()
        self.probe.return_value = {"ok": False, "error_code": "ERR_DMS_AUTH"}
        with self.assertRaises(Thrown):
            self.roster.update_operator("OP-9", dms_password="new")
        self.probe.assert_called_once_with("https://dms.example.test", "sale02", "new", "login")
        self.assertEqual(self.inserted, ["save"], "only the display-name edit was saved")


class LoginProbeTests(Isolated):
    def test_login_probe_signs_out_and_reads_nothing(self):
        adapters = []

        class Adapter:
            def __init__(self, **kw):
                self.kw = kw
                adapters.append(self)

            def client(self):
                raise AssertionError("a login check must not open the DMS client")

        class AuthError(Exception):
            pass

        class Technical(Exception):
            pass

        class Unreachable(Technical):
            pass

        self.stub("pearnly_dms.adapter.session", MrerpDmsAdapter=Adapter, DmsAuthError=AuthError,
                  DmsTechnicalError=Technical, DmsUnreachableError=Unreachable)
        self.stub("pearnly_dms.adapter.push", logged_in_probe=lambda adapter, inspect: inspect(adapter))
        self.stub("pearnly_dms.line.errors", friendly=lambda code: {"th": code})
        probe = importlib.import_module("pearnly_dms.adapter.account_probe")
        self.assertEqual(probe.probe("https://dms", "sale02", "pw", "login"), {"ok": True, "missing": []})
        self.assertTrue(adapters[0].verify_password and adapters[0].sign_out_on_exit)
        sys.modules["pearnly_dms.adapter.push"].logged_in_probe = Mock(side_effect=AuthError())
        self.assertEqual(probe.probe("https://dms", "sale02", "bad", "login")["error_code"], "ERR_DMS_AUTH")
        sys.modules["pearnly_dms.adapter.push"].logged_in_probe = Mock(side_effect=Unreachable())
        self.assertEqual(probe.probe("https://dms", "sale02", "pw", "login")["error_code"], "ERR_DMS_UNREACHABLE")

    def test_session_exit_signs_out_instead_of_caching(self):
        self.stub("pearnly_core.browser", BrowserSession=object)
        self.stub("pearnly_core.endpoints", read_password=lambda *a: "")
        self.stub("pearnly_core.session_lock", account_lock=Mock(), dms_login_key=lambda url, user: user,
                  SessionLockUnavailable=RuntimeError)
        self.stub("pearnly_dms.adapter.read_priority", foreground=Mock(), yield_to_foreground=Mock(),
                  wait_for_foreground=Mock(), ReadDeferred=RuntimeError)
        cache = self.stub("pearnly_dms.adapter.session_cache", enabled=lambda: True, save=Mock(return_value=True),
                          forget=Mock(), idle_seconds=lambda: 60, sales_idle_seconds=lambda: 60)
        session = importlib.import_module("pearnly_dms.adapter.session")
        adapter = session.MrerpDmsAdapter(system_url="https://dms.example.test/index.php",
                                          username="sale02", password="pw")
        page = Mock()
        adapter._session = types.SimpleNamespace(page=page, dialogs=[], __exit__=lambda *a: None)
        adapter._logged_in = True
        adapter._logout_page = Mock()
        adapter.sign_out_on_exit = True
        adapter.__exit__(None, None, None)
        cache.save.assert_not_called()
        cache.forget.assert_called_once_with("sale02")
        adapter._logout_page.assert_called_once_with(page)


# ── 2 / 3：读档提示与残留词条 ─────────────────────────────────────────────────
class SnapshotTextTests(unittest.TestCase):
    def test_no_text_points_to_a_manual_read_that_no_longer_exists(self):
        rows = [line.split("\t") for line in (APP / "i18n/messages.tsv").read_text(encoding="utf-8").splitlines()]
        for row in rows:
            if row[-1].startswith("dms.err.ERR_DMS_SNAPSHOT_"):
                self.assertNotIn("连接页手动", row[1], row[-1])
                self.assertNotIn("手动重新读取", row[1], row[-1])
                self.assertNotIn("点击读取", row[1], row[-1])

    def test_retired_terms_are_removed_from_every_language_pack(self):
        for lang in ("en", "zh", "th", "ja"):
            keys = {row[0] for row in csv.reader(io.StringIO(
                (APP / "translations" / f"{lang}.csv").read_text(encoding="utf-8"))) if row}
            self.assertNotIn("dms.connect.acct.readFirst", keys, lang)
            self.assertNotIn("dms.card.TXT_KEEP", keys, lang)
        for path in APP.rglob("*"):
            if path.suffix in {".py", ".js"}:
                self.assertNotIn("acct.readFirst", path.read_text(encoding="utf-8"), path)


# ── 11 / 12：本地 LINE 实验室 ──────────────────────────────────────────────────
class LineLabTests(unittest.TestCase):
    def test_rich_menu_without_an_image_answers_404_like_line(self):
        source = (ROOT / "scripts/dms_line_lab/server.py").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "line.sqlite3")
            source = source.replace('DB = "/lab-data/line.sqlite3"', f"DB = {database!r}")
            source = source.split("\nsecure = Server(")[0]
            namespace = {"__name__": "lab_server", "__file__": str(ROOT / "scripts/dms_line_lab/server.py")}
            with patch.dict(sys.modules, {"requests": types.ModuleType("requests"),
                                          "config": types.SimpleNamespace(SITES=["lab"], channel_config=None)}):
                exec(compile(source, "server.py", "exec"), namespace)
            with sqlite3.connect(database) as db:
                db.execute("INSERT INTO rich_menus(id,site,body) VALUES('m1','lab','{}')")
            handler = namespace["Handler"].__new__(namespace["Handler"])
            answers = []
            handler.respond = lambda body, status=200, **kw: answers.append(status)
            handler.command = "GET"
            handler.rich_menu_api("lab", "richmenu/m1/content")
            handler.command, handler.body = "POST", b"\x89PNG-image"
            handler.rich_menu_api("lab", "richmenu/m1/content")
            handler.command = "GET"
            handler.rich_menu_api("lab", "richmenu/m1/content")
        self.assertEqual(answers, [404, 200, 200])

    def test_lab_runs_the_dms_sync_queue(self):
        compose = (ROOT / "infrastructure/compose/dms-line-lab.yml").read_text(encoding="utf-8")
        self.assertIn('command: ["bench", "worker", "--queue", "dms_sync"]', compose)
        self.assertIn('"dms_sync": {"timeout": 420}', (ROOT / "scripts/dms-line-lab-init.py").read_text())
        self.assertIn("dms-sync-worker", (ROOT / "scripts/dms-line-lab.sh").read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
