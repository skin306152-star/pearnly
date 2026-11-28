"""ERP LINE intake regressions (user-approved flow of 2026-09-25).

Runs in the pinned Frappe runtime via unittest. No Site, database, credentials, OCR,
outbound LINE or DMS call is used: collaborators are replaced and network is blocked.
"""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import frappe

from pearnly_core.write_precheck import contracts as precheck_card
from pearnly_erp_portal.integrations import handlers, intake, line_flow, line_questions


def _same(text, *args, **kwargs):
    return text


class Base(unittest.TestCase):
    def setUp(self):
        self.replies = []
        self.sessions = {}
        self.roles = {"buyer": {"purchase.write", "integration.read"}}
        self.enterContext(patch.object(frappe, "_", _same, create=True))
        for module in (handlers, intake, line_flow, line_questions):
            self.enterContext(patch.object(module, "_", _same))
        self.enterContext(patch.object(frappe, "session", SimpleNamespace(user="Guest"), create=True))
        # sql：这家公司能选的对方档条数（`line_flow._more_parties`），1 条 = 按钮里已列全
        self.enterContext(patch.object(frappe, "db", Mock(**{"get_value.return_value": None, "sql.return_value": [[1]]}),
                                       create=True))
        self.enterContext(patch.object(frappe, "throw", side_effect=self.reject))
        self.enterContext(patch.object(frappe, "set_user", lambda user: setattr(frappe.session, "user", user)))
        # 卡片保存 / 取消整笔重来（`conflicts.retried`，11-28 B ②）：替身里没有请求上下文的 frappe.local.flags
        from pearnly_core.transaction import conflicts

        self.enterContext(patch.object(conflicts, "_error_message", return_value=conflicts._UNSET))
        self.enterContext(patch("pearnly_core.line.reply", side_effect=lambda token, messages: self.replies.append(messages) or True))
        self.enterContext(patch("pearnly_core.line.reply_or_push",
                                side_effect=lambda to, messages, token="", **kw: self.replies.append(messages) or True))
        self.enterContext(patch("pearnly_core.line_session.get_session", side_effect=lambda uid: self.sessions.get(uid)))
        self.enterContext(patch("pearnly_core.line_session.set_session",
                                side_effect=lambda uid, state, payload, ttl_minutes=None: self.sessions.__setitem__(uid, {"state": state, "payload": dict(payload)})))
        self.enterContext(patch("pearnly_core.line_session.clear_session", side_effect=lambda uid, **kw: self.sessions.pop(uid, None)))
        self.enterContext(patch("pearnly_erp_portal.access.guard.has",
                                side_effect=lambda capability, user=None: capability in self.roles.get(user or frappe.session.user, set())))
        self.network = self.enterContext(patch("requests.sessions.Session.request",
                                               side_effect=AssertionError("External network forbidden")))
        # 识别任务的检查点（④ #12，`execution.work.checkpoint`）：替身按一份 Work 的检查点存在内存里
        self.checkpoints = {}
        self.enterContext(patch("pearnly_core.execution.work.checkpoint", side_effect=self._checkpoint))

    def _checkpoint(self, key, state=None, body=None):
        if state is None:
            return self.checkpoints.get(key)
        self.checkpoints[key] = {"state": state, "body": body}
        return self.checkpoints[key]

    @staticmethod
    def reject(message, exception=frappe.ValidationError, **kwargs):
        raise exception(message)

    def last_text(self):
        return self.replies[-1][0]["text"]

    def buttons(self):
        return [item["action"] for item in self.replies[-1][0]["quickReply"]["items"]]


class Flow(Base):
    def setUp(self):
        super().setUp()
        self.companies = ["CO-1"]
        self.enterContext(patch("pearnly_erp_portal.masters.company.allowed_companies",
                                side_effect=lambda user=None: list(self.companies)))
        self.enterContext(patch.object(line_flow, "_options", side_effect=self.options))
        self.enterContext(patch("frappe.utils.get_url", return_value="https://testerp.pearnly.com"))

    def options(self, step, payload, user):
        return {"entry": [("upload", "Upload and recognise")],
                "company": [("CO-1", "Alpha"), ("CO-2", "Beta")],
                "party": [(line_flow.FROM_DOCUMENT, "From the document"), (line_flow.FALLBACK, "General supplier"),
                          ("SUP-1", "Acme")],
                "paid": [("1", "Paid"), ("0", "Unpaid")],
                "method": [("cash", "Cash"), ("transfer", "Transfer")]}[step]

    def tap(self, action):
        handlers.handle({"type": "postback", "source": {"userId": "U1"}, "replyToken": "r",
                         "postback": {"data": action["data"]}})

    def test_steps_run_in_order_and_skip_single_company(self):
        """11-06 按行选物料与库存：上传前不再问「服务 / 库存商品」和仓库（识别后按行预选，`line_items`）。"""
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            self.tap({"data": "a=mode:purchase"})
            self.tap(self.buttons()[0])                        # Upload and recognise
            self.assertEqual(self.buttons()[0]["label"], "1. From the document")
            self.assertEqual(self.buttons()[-1]["data"], "a=erp&s=cancel")
            self.assertEqual(self.buttons()[1]["label"], "2. General supplier")
            self.tap(self.buttons()[2])                        # Acme
            self.tap(self.buttons()[0])                        # Paid
            self.tap(next(b for b in self.buttons() if "transfer" in b["data"]))
        payload = self.sessions["U1"]["payload"]
        self.assertEqual(payload, {"direction": "purchase", "entry": "upload", "company": "CO-1", "party": "SUP-1",
                                   "is_paid": 1, "payment_method": "transfer"})
        self.assertTrue(line_flow.ready(payload))
        self.assertIn("Please upload a PDF or image", self.last_text())

    def send_image(self):
        handlers.handle({"type": "message", "source": {"userId": "U1"}, "replyToken": "r",
                         "message": {"type": "image", "id": "M1"}})

    def test_a_document_sent_before_the_questions_are_answered_asks_the_current_one_again(self):
        # 用户 11-25 批：方向已选、后面的题没答完就发文件 → 不回「请先选择类型」，把当前那一题连按钮重新问一遍
        self.companies = ["CO-1", "CO-2"]
        receive = self.enterContext(patch.object(intake, "receive"))
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            self.tap({"data": "a=mode:purchase"})
            self.tap({"data": "a=erp&s=entry&v=upload"})
            asked = self.replies[-1]
            self.send_image()
        receive.assert_not_called()
        self.assertEqual(self.replies[-1], asked)  # 同一题（选公司）、同一组按钮
        self.assertEqual([b["data"] for b in self.buttons()][:2], ["a=erp&s=company&v=CO-1", "a=erp&s=company&v=CO-2"])
        self.assertNotIn("Choose a type first", self.last_text())

    def test_a_type_no_longer_permitted_offers_the_types_this_account_has(self):
        self.roles["buyer"] |= {"sales.write"}
        receive = self.enterContext(patch.object(intake, "receive"))
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            self.tap({"data": "a=mode:purchase"})
            self.roles["buyer"].discard("purchase.write")   # 答题中被取消了采购权限
            self.send_image()
        receive.assert_not_called()
        self.assertEqual(self.last_text(), "Choose a type first")
        self.assertEqual([b["data"] for b in self.buttons()], ["a=mode:sales", "a=erp&s=cancel"])

    def test_a_stale_button_after_the_type_expired_offers_the_types_again(self):
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            self.tap({"data": "a=erp&s=paid&v=0"})  # 会话已过期（没有方向）时点了旧按钮
        self.assertEqual(self.last_text(), "Choose a type first")
        self.assertEqual([b["data"] for b in self.buttons()], ["a=mode:purchase", "a=erp&s=cancel"])

    def test_several_companies_are_offered_as_numbered_buttons(self):
        self.companies = ["CO-1", "CO-2"]
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            self.tap({"data": "a=mode%3Apurchase"})           # the old menu's encoding
            self.tap({"data": "a=erp&s=entry&v=upload"})
        self.assertEqual([b["label"] for b in self.buttons()], ["1. Alpha", "2. Beta", "Cancel"])

    def test_similar_company_names_stay_distinguishable_within_the_20_character_buttons(self):
        """录制问题 #26：按钮去掉通用词，提问里列出带编号的全名。"""
        self.companies = ["CO-1", "CO-2"]
        names = [("CO-1", "บริษัท เดโม่ เทรดดิ้ง จำกัด"), ("CO-2", "บริษัท เดโม่ เทคโนโลยี จำกัด (มหาชน)")]
        with patch.object(line_flow, "_options", side_effect=lambda step, payload, user:
                          names if step == "company" else self.options(step, payload, user)), \
                patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            self.tap({"data": "a=mode:purchase"})
            self.tap({"data": "a=erp&s=entry&v=upload"})
        self.assertEqual([b["label"] for b in self.buttons()], ["1. เดโม่ เทรดดิ้ง", "2. เดโม่ เทคโนโลยี", "Cancel"])
        self.assertEqual(self.last_text().splitlines()[1:],
                         ["1. บริษัท เดโม่ เทรดดิ้ง จำกัด", "2. บริษัท เดโม่ เทคโนโลยี จำกัด (มหาชน)"])
        self.assertEqual(line_flow.short_name("Acme Co., Ltd."), "Acme")
        self.assertEqual(line_flow.short_name("บริษัท จำกัด"), "บริษัท จำกัด")

    def test_stale_or_forged_answers_do_not_advance(self):
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            self.tap({"data": "a=mode:purchase"})
            self.tap({"data": "a=erp&s=paid&v=1"})             # not the current step
            self.assertEqual(self.last_text(), "This option is no longer current")
            self.tap({"data": "a=erp&s=party&v=SUP-404"})      # not an offered value
            self.assertEqual(self.last_text(), "This option is no longer current")
        self.assertNotIn("party", self.sessions["U1"]["payload"])

    def test_unpaid_skips_method(self):
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            for data in ("a=mode:purchase", "a=erp&s=entry&v=upload", "a=erp&s=party&v=%40doc", "a=erp&s=paid&v=0"):
                self.tap({"data": data})
        payload = self.sessions["U1"]["payload"]
        self.assertEqual((payload["is_paid"], payload["payment_method"], payload.get("warehouse")), (0, "", None))
        self.assertTrue(line_flow.ready(payload))

    def test_manual_entry_is_a_link_to_the_blank_native_page(self):
        """与卡片「编辑」、菜单「盘点」一样经 LINE 入口：LINE 里未登录时走绑定的 LINE 免密登录。"""
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            self.tap({"data": "a=mode:purchase"})
        upload, manual, cancel = self.buttons()
        self.assertEqual((upload["label"], upload["data"]), ("Upload and recognise", "a=erp&s=entry&v=upload"))
        self.assertEqual(manual, {"type": "uri", "label": "Enter manually",
                                  "uri": "https://testerp.pearnly.com/api/method/pearnly_core.account.line_login.enter"
                                         "?to=%2Fdesk%2Fpearnly-purchase%2Fnew%3Fentry%3Dmanual"})  # 新建页默认手动录入
        self.assertEqual(cancel["data"], "a=erp&s=cancel")
        with patch("pearnly_core.line_binding.user_of", return_value="seller"):
            self.roles["seller"] = {"sales.write", "integration.read"}
            self.tap({"data": "a=mode:sales"})
        self.assertEqual(self.buttons()[1]["uri"], "https://testerp.pearnly.com/api/method/"
                         "pearnly_core.account.line_login.enter?to=%2Fdesk%2Fpearnly-sales%2Fnew%3Fentry%3Dmanual")

    def test_role_and_binding_are_checked_before_any_step(self):
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            self.tap({"data": "a=mode:sales"})
        self.assertEqual(self.last_text(), "This account cannot send documents")
        with patch("pearnly_core.line_binding.user_of", return_value=None):
            self.tap({"data": "a=mode:purchase"})
        self.assertEqual(self.last_text(), "Bind this account first")
        self.assertNotIn("U1", self.sessions)

    def test_cancel_clears_the_choices(self):
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            self.tap({"data": "a=mode:purchase"})
            self.tap({"data": "a=erp&s=cancel"})
        self.assertNotIn("U1", self.sessions)
        self.assertEqual(self.last_text(), "Cancelled")

    def test_collection_events_go_to_the_collection_flow_before_other_typed_words(self):
        """收款（11-11）：按钮 ``a=col``、停在收款里打的字（含「1」这种金额，不当成选采购）、等凭证时发的图都交给 `line_collection`。"""
        from pearnly_erp_portal.integrations import line_collection

        self.roles["buyer"] = {"purchase.write", "integration.read", "collection.write"}
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"), \
                patch.object(line_collection, "answer") as answer, \
                patch.object(line_collection, "text", return_value=True) as typed, \
                patch.object(line_collection, "wants_voucher", return_value=True), \
                patch.object(line_collection, "receive_voucher") as voucher:
            handlers.handle({"type": "postback", "source": {"userId": "U1"}, "replyToken": "r", "webhookEventId": "E1",
                             "postback": {"data": "a=col&s=start"}})
            handlers.handle({"type": "message", "source": {"userId": "U1"}, "replyToken": "r", "webhookEventId": "E2",
                             "message": {"type": "text", "text": "1"}})
            handlers.handle({"type": "message", "source": {"userId": "U1"}, "replyToken": "r",
                             "message": {"type": "image", "id": "M1"}})
        answer.assert_called_once_with("U1", "buyer", "a=col&s=start", "r", "E1")
        typed.assert_called_once_with("U1", "buyer", "1", "r", "E2")
        voucher.assert_called_once()
        self.assertNotIn("U1", self.sessions)  # 没有进采购的选择

    def test_menu_word_offers_collections_to_collectors(self):
        self.roles["buyer"] = {"integration.read", "collection.write"}
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            handlers.handle({"type": "message", "source": {"userId": "U1"}, "replyToken": "r",
                             "message": {"type": "text", "text": "เมนู"}})
        self.assertEqual([(b["label"], b["data"]) for b in self.buttons()],
                         [("Collections", "a=col&s=start"), ("Cancel", "a=erp&s=cancel")])

    def test_unfollow_unbinds_and_resyncs_the_menu(self):
        """删除 / 屏蔽官方号：解绑这个 LINE、按权限重挂菜单，不回消息（与 Cowork 同一做法）。"""
        self.enterContext(patch("pearnly_core.line.user_language", return_value="th"))
        with patch("pearnly_core.line_binding.unbind") as unbind, \
                patch("pearnly_erp_portal.integrations.line_menu.request_sync") as sync:
            self.assertTrue(handlers.handle({"type": "unfollow", "source": {"userId": "U1"}}))
        unbind.assert_called_once_with("U1")
        sync.assert_called_once_with("U1")
        self.assertEqual(self.replies, [])


class Documents(Base):
    def setUp(self):
        super().setUp()
        # 会话已过期时识别完照旧直接出卡；出卡前核「写不了、又没得选」的原因（`intake.stopped_reason`）这里没有可判的
        self.enterContext(patch.object(intake, "stopped_reason", return_value=""))

    def event(self):
        return {"type": "message", "source": {"userId": "U1"}, "replyToken": "r",
                "message": {"type": "image", "id": "M1"}}

    def test_document_before_choices_is_refused_without_download(self):
        download = self.enterContext(patch("pearnly_core.line.download_message_content"))
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            handlers.handle(self.event())
        # 用户 11-25 批：「请先选择类型」带与菜单同一组原生按钮（按这个账号的权限：这里只有采购），不再只回一句字
        self.assertEqual(self.last_text(), "Choose a type first")
        self.assertEqual([b["data"] for b in self.buttons()], ["a=mode:purchase", "a=erp&s=cancel"])
        download.assert_not_called()

    def test_the_type_buttons_follow_the_accounts_permissions(self):
        self.roles["buyer"] |= {"sales.write", "collection.write"}
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            handlers.handle(self.event())
        self.assertEqual(self.last_text(), "Choose a type first")
        self.assertEqual([b["data"] for b in self.buttons()],
                         ["a=mode:purchase", "a=mode:sales", "a=col&s=start", "a=erp&s=cancel"])

    def test_ready_document_is_received_as_the_bound_user_and_guest_restored(self):
        self.sessions["U1"] = {"state": line_flow.STATE, "payload": {"direction": "purchase", "entry": "upload", "company": "CO-1",
            "party": "@doc", "is_paid": 0, "payment_method": "", "item_kind": "service"}}
        download = self.enterContext(patch("pearnly_core.line.download_message_content", return_value=b"file"))
        seen = {}
        receive = self.enterContext(patch.object(intake, "receive",
                                    side_effect=lambda **kw: seen.update(user=frappe.session.user, **kw)))
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            handlers.handle(self.event())
        receive.assert_called_once()
        self.assertEqual((seen["user"], seen["sent_by"], seen["choices"]["company"]), ("buyer", "buyer", "CO-1"))
        self.assertEqual(frappe.session.user, "Guest")
        # 回调里不下载（09-25 发图 499）：只登记排队，识别任务里再取。
        download.assert_not_called()
        self.assertEqual((seen["content"], seen["line_message_id"]), (None, "M1"))

    def test_the_recognition_job_fetches_the_document_once(self):
        from pearnly_core import line

        doc = _doc(status=intake.QUEUED, language="th", source_file_name=None, source_file=None, line_message_id="M1")
        # 与 Frappe `Document.update(d)` 同一签名（传字典）：09-26 用关键字参数写错，测试替身没拦住。
        doc.update = lambda values: [setattr(doc, key, value) for key, value in values.items()]
        doc.get = lambda key, default=None: getattr(doc, key, default)
        doc.save = lambda **kw: None
        doc.reload = lambda: None
        stored = SimpleNamespace(name="FILE-9", file_url="/private/files/x.jpg")
        self.enterContext(patch.object(frappe, "get_doc", side_effect=lambda *a, **kw: doc if a and a[0] == intake.DOCTYPE
                                       else SimpleNamespace(insert=lambda **k: stored), create=True))
        self.enterContext(patch("pearnly_core.line_binding.user_of", return_value="buyer"))
        download = self.enterContext(patch.object(line, "download_message_content", return_value=b"img"))
        seen = {}
        self.enterContext(patch.object(intake, "_recogniser", return_value=SimpleNamespace(
            recognise=lambda files, **kw: seen.setdefault("files", files) and {"lines": []})))
        self.enterContext(patch.object(intake, "totals", return_value={}))
        self.enterContext(patch("pearnly_erp_portal.integrations.line_cards.preview", return_value={}))
        intake.run("LI-1", "U1", filename="line-M1.jpg")
        download.assert_called_once_with("M1", max_bytes=intake.MAX_BYTES)
        self.assertEqual((doc.source_file_name, seen["files"]), ("FILE-9", [{"file": "FILE-9", "kind": "bill"}]))

    def test_each_document_in_a_file_gets_its_own_record_and_card(self):
        from pearnly_core import line

        doc = _doc(status=intake.QUEUED, language="th")
        doc.reload = lambda: None
        doc.save = lambda **kw: None
        inserted = []

        def get_doc(value, name=None, **kwargs):
            if isinstance(value, dict) and value.get("doctype") == "File":
                return SimpleNamespace(insert=lambda **kw: SimpleNamespace(name="FILE-X", file_url="/private/files/x.jpg"))
            if isinstance(value, dict):
                return SimpleNamespace(insert=lambda **kw: inserted.append(value) or SimpleNamespace(
                    **value, get=value.get, name=f"NEW-{len(inserted)}"))
            if value == "File":
                return SimpleNamespace(get_content=lambda: b"img", file_name="a.jpg")
            return doc

        self.enterContext(patch.object(frappe, "get_doc", side_effect=get_doc, create=True))
        self.enterContext(patch("pearnly_core.line_binding.user_of", return_value="buyer"))
        self.enterContext(patch.object(intake, "totals", return_value={}))
        self.enterContext(patch.object(intake, "_recogniser", return_value=SimpleNamespace(
            recognise=lambda files, **kw: {"doc_no": "A", "lines": [], "additional": [{"doc_no": "B", "lines": []},
                                                                                 {"doc_no": "C", "lines": []}]})))
        self.enterContext(patch("pearnly_erp_portal.integrations.line_cards.preview",
                                side_effect=lambda card, data, totals: data["doc_no"]))
        intake.run("LI-1", "U1")
        siblings = [row for row in inserted if row.get("doctype") == intake.DOCTYPE]
        self.assertEqual([json.loads(row["result"])["doc_no"] for row in siblings], ["B", "C"])
        self.assertEqual({row["status"] for row in siblings}, {intake.READY})
        self.assertEqual(self.replies[-1], ["A", "B", "C"])
        self.assertNotIn("additional", json.loads(doc.result))

    def test_a_document_that_cannot_be_fetched_fails_and_says_so(self):
        from pearnly_core import line

        doc = _doc(status=intake.QUEUED, language="th", source_file_name=None, line_message_id="M1")
        self.enterContext(patch.object(frappe, "get_doc", return_value=doc, create=True))
        self.enterContext(patch("pearnly_core.line_binding.user_of", return_value="buyer"))
        self.enterContext(patch.object(line, "download_message_content", side_effect=RuntimeError("expired")))
        self.enterContext(patch.object(frappe, "log_error", create=True))
        dropped = self.enterContext(patch.object(intake, "_drop"))
        self.assertEqual(intake.run("LI-1", "U1"), {"failed": "download_failed", "notified": True})
        dropped.assert_called_once_with(doc, intake.FAILED, "download_failed")
        self.assertEqual(self.last_text(), "Could not store this document")

    def _run_line_file(self, size, declared=True):
        """识别任务从 LINE 取一份 `size` 字节的文件：返回 (run 的结果, 存下的字节数或 None, 失败原因或 None)。"""
        doc = _doc(status=intake.QUEUED, language="th", source_file_name=None, source_file=None, line_message_id="M1")
        doc.update = lambda values: [setattr(doc, key, value) for key, value in values.items()]
        doc.get = lambda key, default=None: getattr(doc, key, default)
        doc.save = lambda **kw: None
        doc.reload = lambda: None
        stored = []
        self.enterContext(patch.object(frappe, "get_doc", return_value=doc, create=True))
        self.enterContext(patch("pearnly_core.line_binding.user_of", return_value="buyer"))
        self.enterContext(patch("pearnly_core.line.requests.get", return_value=_Media(size, declared)))
        self.enterContext(patch("pearnly_core.line._headers", return_value={}))
        self.enterContext(patch.object(intake, "_store", side_effect=lambda content, name: stored.append(len(content))
                                       or SimpleNamespace(name="FILE-9", file_url="/private/files/x.pdf")))
        self.enterContext(patch.object(intake, "_recogniser", return_value=SimpleNamespace(
            recognise=lambda files, **kw: {"lines": []})))
        self.enterContext(patch.object(intake, "totals", return_value={}))
        self.enterContext(patch("pearnly_erp_portal.integrations.line_cards.preview", return_value={}))
        logged = self.enterContext(patch.object(frappe, "log_error", create=True))
        dropped = self.enterContext(patch.object(intake, "_drop"))
        outcome = intake.run("LI-1", "U1", filename="line-M1.pdf")
        reason = dropped.call_args.args[2] if dropped.called else None
        return outcome, (stored[0] if stored else None), reason, logged

    def test_a_line_file_of_exactly_the_shared_upload_limit_is_stored(self):
        # 11-11：与网页、CC 对话、CC 桌面版文件夹同一个 40MB 上限（原来按下载函数默认的 20MB）
        self.assertEqual(intake.MAX_BYTES, 40 * 1024 * 1024)
        outcome, stored, reason, _logged = self._run_line_file(intake.MAX_BYTES)
        self.assertEqual((stored, reason), (intake.MAX_BYTES, None), outcome)

    def test_a_line_file_over_the_limit_fails_and_says_how_large(self):
        # 原来 20MB 起只回「没能保存这份文件」；现在超 40MB 回四语词条 upload.tooLarge，记 too_large，不记错误日志
        for declared in (True, False):
            with self.subTest(declared=declared):
                self.replies.clear()
                outcome, stored, reason, logged = self._run_line_file(intake.MAX_BYTES + 1, declared)
                self.assertEqual((outcome, stored, reason), ({"failed": "too_large", "notified": True}, None, "too_large"))
                logged.assert_not_called()
                self.assertEqual(self.last_text(), "upload.tooLarge")


class _Media:
    """LINE 下载服务器的回复替身：流式给 `size` 个字节；`declared` 为假时不带 Content-Length（逐块累加时才发现超了）。"""

    def __init__(self, size, declared=True):
        self.size, self.status_code = size, 200
        self.headers = {"Content-Length": str(size)} if declared else {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, chunk):
        block, left = b"\0" * chunk, self.size
        while left > 0:
            yield block[:min(chunk, left)]
            left -= chunk


def _raise(message, exc=Exception):
    raise exc(message)


def _doc(**values):
    base = dict(name="LI-1", direction="purchase", company="CO-1", party="SUP-1", is_paid=1,
                payment_method="transfer", item_kind="goods", warehouse="WH-1", status="ready",
                purchase=None, sales_document=None, line_user_id="U1", source_file_name="FILE-1", sent_by="buyer", source_file="/private/files/a.jpg",
                result=json.dumps({"doc_no": "INV-1", "doc_date": "2026-09-25", "supplier_name": "Acme",
                                   "lines": [{"description": "Soap", "qty": 2, "unit_price": 10}]}))
    base.update(values)
    return SimpleNamespace(**base, get=lambda key, default=None: base.get(key, default))


class Payload(Base):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.object(intake, "_file_name", return_value="FILE-1"))

    def test_purchase_payload_uses_the_chosen_masters_and_stock_choice(self):
        payload = intake.build_payload(_doc())
        self.assertEqual((payload["company"], payload["supplier"], payload["is_paid"], payload["payment_method"]),
                         ("CO-1", "SUP-1", 1, "transfer"))
        self.assertEqual((payload["update_stock"], payload["auto_create_product"]), (1, 1))
        self.assertEqual(payload["lines"][0]["item_type"], "goods")
        self.assertEqual(payload["lines"][0]["warehouse"], "WH-1")
        self.assertEqual(payload["attachments"], [{"file": "FILE-1", "kind": "bill"}])
        self.assertNotIn("company", json.loads(_doc().result))  # OCR never chooses the company

    def test_purchase_from_document_and_service_does_not_touch_stock(self):
        payload = intake.build_payload(_doc(party="@doc", item_kind="service", warehouse=None))
        self.assertNotIn("supplier", payload)
        self.assertEqual(payload["supplier_name"], "Acme")
        self.assertEqual((payload["update_stock"], payload["lines"][0]["item_type"]), (0, "service"))
        self.assertNotIn("warehouse", payload["lines"][0])

    def test_fallback_parties_ignore_the_document_party(self):
        purchase = intake.build_payload(_doc(party="@any", result=json.dumps({"doc_no": "INV-1",
            "supplier_name": "Shop", "supplier_tax_id": "0105", "lines": []})))
        self.assertEqual((purchase["supplier_name"], "supplier" in purchase, "supplier_tax_id" in purchase),
                         ("General supplier", False, False))
        sales = intake.build_payload(_doc(direction="sales", party="@any", result=json.dumps(
            {"buyer_name": "Somchai", "address": "Bangkok", "lines": []})))
        self.assertEqual((sales["buyer_name"], sales["party_type"], "customer" in sales, "address" in sales),
                         ("Walk-in customer", "anonymous", False, False))

    def test_sales_payload_is_unpaid_and_names_an_unknown_buyer(self):
        doc = _doc(direction="sales", party="@doc", is_paid=0, payment_method="",
                   result=json.dumps({"doc_date": "2026-09-25", "lines": [{"description": "Soap", "qty": 1, "unit_price": 5}]}))
        payload = intake.build_payload(doc)
        self.assertEqual((payload["is_paid"], payload["paid_amount"], payload["buyer_name"]), (0, 0, "Unknown buyer"))
        self.assertEqual(payload["lines"][0]["warehouse"], "WH-1")


    def test_old_records_with_printed_spellings_post_as_numbers_and_keep_the_percentage_discount(self):
        # 主会话 11-19：「1,234.50」的数量 / 单价 / 凑整入账报值无效；销售的「฿」单价、整单折扣静默变 0；识别出的百分比折扣被丢掉
        purchase = intake.build_payload(_doc(party="@doc", item_kind="service", warehouse=None, result=json.dumps(
            {"doc_no": "INV-1", "supplier_name": "Acme", "rounding": "(0.25)", "wht_rate": "3%",
             "lines": [{"description": "A", "qty": "1,234.50", "unit_price": "1,234.50", "vat_rate": "7%"}]})))
        line = purchase["lines"][0]
        self.assertEqual((line["qty"], line["unit_price"], line["vat_rate"], purchase["rounding"], purchase["wht_rate"]),
                         (1234.5, 1234.5, 7.0, -0.25, 3.0))
        sales = intake.build_payload(_doc(direction="sales", party="@any", result=json.dumps(
            {"vat_rate": "7%", "header_discount": "฿50",
             "lines": [{"description": "Soap", "qty": "1,000", "unit_price": "฿5", "discount_pct": "10%"}]})))
        line = sales["lines"][0]
        self.assertEqual((line["qty"], line["unit_price"], line["discount_pct"], sales["header_discount"], sales["vat_rate"]),
                         (1000.0, 5.0, 10.0, 50.0, 7.0))

    def test_paid_sales_takes_the_document_date_and_vat_mode(self):
        # 定·09-25：付款日期取单据日期（09-26 实测记成了当天）；含税小票不再加一次 VAT。
        doc = _doc(direction="sales", party="@any", payment_method="cash", result=json.dumps(
            {"doc_date": "2026-05-27", "price_includes_vat": True,
             "lines": [{"description": "Tweezers", "qty": 1, "unit_price": 79}]}))
        payload = intake.build_payload(doc)
        self.assertEqual((payload["payment_date"], payload["price_includes_vat"]), ("2026-05-27", True))

    def test_fallback_names_are_stored_in_the_channel_language(self):
        seen = []
        self.enterContext(patch.object(intake, "channel_language", return_value="th"))
        self.enterContext(patch.object(intake, "_", lambda text, lang=None: seen.append(lang) or text))
        intake.build_payload(_doc(direction="sales", party="@any", result=json.dumps({"lines": []})))
        self.assertEqual(seen, ["th"])


class ChosenPayload(Base):
    """11-06 按行选物料与库存：识别后每行的选择存在票上（`line_items`），建单按行带上入库与否、物料、新建、仓位；
    有入库行才带单头仓；不再交整张的 update_stock / auto_create_product。"""

    def setUp(self):
        super().setUp()
        self.enterContext(patch.object(intake, "_file_name", return_value="FILE-1"))

    def chosen(self, direction="purchase"):
        rows = [{"description": "Soap", "qty": 2, "unit_price": 10}, {"description": "Freight", "qty": 1, "unit_price": 50},
                {"description": "New box", "qty": 1, "unit_price": 5}]
        choices = {"warehouse": "WH-2", "location_asked": True, "lines": [
            {"item_type": "goods", "product": "P-SOAP", "location": "A1", "decided": True, "new": False},
            {"item_type": "service", "product": None, "location": None, "decided": True, "new": False},
            {"item_type": "goods", "product": None, "location": None, "decided": True, "new": True}]}
        return _doc(direction=direction, item_kind=None, warehouse=None, line_choices=json.dumps(choices),
                    result=json.dumps({"doc_no": "INV-1", "doc_date": "2026-09-25", "supplier_name": "Acme", "lines": rows}))

    def test_purchase_lines_carry_each_choice(self):
        payload = intake.build_payload(self.chosen())
        # 改版前存下的票（11-19 以前）行上带着仓位：不再交给提交（库存只到仓库）
        self.assertEqual([(row["item_type"], row.get("product"), row.get("new_item"), row.get("location"))
                          for row in payload["lines"]],
                         [("goods", "P-SOAP", None, None), ("service", None, None, None), ("goods", None, 1, None)])
        self.assertEqual(payload["warehouse"], "WH-2")
        self.assertNotIn("update_stock", payload)
        self.assertNotIn("auto_create_product", payload)

    def test_sales_lines_carry_each_choice(self):
        payload = intake.build_payload(self.chosen("sales"))
        self.assertEqual([row["item_type"] for row in payload["lines"]], ["goods", "service", "goods"])
        self.assertEqual(payload["warehouse"], "WH-2")

    def test_record_only_documents_carry_no_warehouse(self):
        doc = self.chosen()
        choices = json.loads(doc.line_choices)
        for row in choices["lines"]:
            row["item_type"] = "service"
        doc = _doc(item_kind=None, warehouse=None, line_choices=json.dumps(choices), result=doc.result)
        self.assertNotIn("warehouse", intake.build_payload(doc))


class Judge:
    """替身写账前置判定（共用出题器 `line_ask` 的后台）：每张 ``I:<记录>`` 的项按 ``docs`` 给；记住的答案下次取卡时成为已定；
    ``refuse`` 里的值记答时被产品拒。"""

    def __init__(self, docs=None):
        self.docs, self.memory, self.records, self.refuse, self.serial = dict(docs or {}), {}, [], set(), 0
        self.facts = {}  # doc_id → {state, reason}：判定判出写不了 / 等电脑的单

    def card(self, company, doc_ids):
        merged = []
        for doc_id in doc_ids:
            items = []
            for raw in self.docs.get(doc_id) or ():
                found = self.memory.get((raw["kind"], raw["subject"]))
                items.append({**raw, "value": found, "source": precheck_card.REMEMBERED} if found else dict(raw))
            merged.append({"doc_id": doc_id, "items": items, **self.facts.get(doc_id, {})})
        return precheck_card.merge(merged)

    def record(self, company, doc_ids, answers):
        self.records.append((company, list(doc_ids), list(answers)))
        before = self.card(company, doc_ids)
        if any(answer["value"] in self.refuse for answer in answers):
            return {"refused": "Not accepted"}
        expanded, errors = precheck_card.expand(before, answers)
        if errors:
            return {**before, "errors": errors}
        for rows in expanded.values():
            for row in rows:
                self.memory[(row["kind"], row["subject"])] = row["value"]
        return {**self.card(company, doc_ids), "writable": list(doc_ids), "held": []}

    def search(self, company, doc_id, kind, txt, start, page_length):
        return {"options": [], "more": False}

    def allowed(self, user, company):
        return user == "buyer"

    def user(self):
        return frappe.session.user

    def text(self, key):
        return key

    def token(self):
        self.serial += 1
        return f"t{self.serial}"


# Express 要人选的一项（小票税额抵不抵，推荐「记费用」）
VAT = {"kind": "vat", "label": "Freight", "options": [{"code": "expense"}, {"code": "claim"}], "value": "expense",
       "source": precheck_card.RULE}


class LineItems(Base):
    """识别后先问完再出卡（用户 11-19，`line_questions`）：定不了的行问一次，整批 / 逐项 / 选已有 / 取消；有入库行、仓定不下来才
    问仓库，同一批沿用；接着问写账前置判定剩下的项（共用出题器）；全部问完才出卡。一批几张一次只问一张；旧按钮、别人的票无效。"""

    def setUp(self):
        super().setUp()
        from pearnly_erp_portal.integrations import line_items

        self.items = line_items
        self.enterContext(patch.object(line_items, "_", _same))
        self.docs = {}
        self.enterContext(patch.object(line_items, "_doc", side_effect=lambda name: self.docs[name]))
        self.enterContext(patch.object(line_questions, "_doc", side_effect=lambda name: self.docs[name]))
        self.enterContext(patch.object(line_items, "_save_choices",
                                       side_effect=lambda name, value: setattr(self.docs[name], "line_choices", json.dumps(value))))
        self.enterContext(patch.object(line_questions, "_card", side_effect=lambda doc, save=True:
                                       {"card": doc.name, **({} if save else {"save": False})}))
        self.cancelled = []
        self.enterContext(patch.object(intake, "cancel_card", side_effect=lambda name: self.cancelled.append(name)
                                       or {"cancelled": True}))
        self.judge = Judge()
        self.enterContext(patch("pearnly_core.write_precheck.line_ask._default", side_effect=lambda: self.judge))
        # 出卡时核「写不了、又没得选」的那句原因（`intake.stopped_reason`）用同一份替身判定
        self.enterContext(patch("pearnly_core.write_precheck.api.card", side_effect=self.judge.card))
        self.enterContext(patch.object(line_items, "_prefill", return_value=None))
        self.enterContext(patch.object(line_items, "_warehouses", return_value=[("WH-1", "Main"), ("WH-2", "Shop")]))
        self.enterContext(patch.object(line_items, "_usable_warehouse", return_value=True))
        # 仓建了仓位清单也不问仓位（11-19 只问仓库）：清单在这里照样给，追问不该去读它
        self.locations = ["A1", "B2"]
        self.enterContext(patch("pearnly_erp_portal.inventory.positions.listed", side_effect=lambda w: list(self.locations)))
        frappe.db.exists.return_value = True
        # 上传时的会话：上传前的几步都选好了（`line_flow.ready`）
        self.sessions["U1"] = {"state": line_flow.STATE, "payload": {"direction": "purchase", "entry": "upload", "company": "CO-1",
                                                                     "party": "@doc", "is_paid": 0, "payment_method": ""}}
        self.roles["buyer"] = {"purchase.write", "purchase.read", "integration.read"}

    def add(self, name, lines, warehouse=None):
        choices = {"warehouse": warehouse, "lines": lines}
        doc = _doc(name=name, party="@doc", item_kind=None, warehouse=None, line_choices=json.dumps(choices), result=json.dumps(
            {"doc_no": name, "supplier_name": "Acme",
             "lines": [{"description": f"Item {index + 1}"} for index in range(len(lines))]}))
        doc.reload = lambda: None
        doc.get = lambda key, default=None: getattr(doc, key, default)
        self.docs[name] = doc
        return doc

    @staticmethod
    def row(decided=False, item_type="goods", product=None):
        return {"item_type": item_type, "product": product, "decided": decided, "new": False}

    def tap(self, data):
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            handlers.handle({"type": "postback", "source": {"userId": "U1"}, "replyToken": "r", "postback": {"data": data}})

    def begin(self, *names):
        """识别任务里（以发单人身份）交进来的几张：回这次要发的消息。"""
        frappe.session.user = "buyer"
        try:
            return line_questions.begin("U1", list(names))
        finally:
            frappe.session.user = "Guest"

    def press(self, message, index):
        self.tap(message["quickReply"]["items"][index]["action"]["data"])

    def say(self, text):
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            handlers.handle({"type": "message", "source": {"userId": "U1"}, "replyToken": "r",
                             "message": {"type": "text", "text": text}})

    def labels(self, message):
        return [item["action"]["label"] for item in message["quickReply"]["items"]]

    def lines(self, name):
        return json.loads(self.docs[name].line_choices)["lines"]

    def test_a_document_with_every_line_decided_and_a_warehouse_asks_nothing(self):
        self.add("LI-1", [self.row(True, product="P1"), self.row(True, "service")], warehouse="WH-1")
        self.assertEqual(self.begin("LI-1"), [{"card": "LI-1"}])  # 没有题：直接出卡
        self.assertNotIn(line_questions.WAITING, self.sessions["U1"]["payload"])

    def test_undecided_lines_are_listed_with_four_buttons(self):
        self.add("LI-1", [self.row(True, product="P1"), self.row(), self.row()], warehouse="WH-1")
        messages = self.begin("LI-1")
        self.assertEqual(len(messages), 1)  # 只有提问，卡片等问完
        question = messages[0]
        self.assertEqual(question["text"].splitlines(), ["Acme · LI-1", "erp.line.items.new", "2. Item 2", "3. Item 3"])
        self.assertEqual(self.labels(question), [key[:20] for key in ("erp.line.items.allNewIn", "erp.line.items.allNone",
                                                                      "erp.line.items.each", "Cancel")])

    def test_all_new_marks_the_undecided_lines_and_replies_the_updated_card(self):
        self.add("LI-1", [self.row(True, product="P1"), self.row()], warehouse="WH-1")
        self.begin("LI-1")
        self.tap("a=erp_item&v=all_new&n=LI-1")
        self.assertEqual([(row["item_type"], row["new"], row["decided"]) for row in self.lines("LI-1")],
                         [("goods", False, True), ("goods", True, True)])
        self.assertEqual(self.replies[-1], [{"card": "LI-1"}])
        self.assertNotIn(self.items.ASKING, self.sessions["U1"]["payload"])

    def test_one_by_one_with_record_only_and_an_existing_item(self):
        self.add("LI-1", [self.row(), self.row()], warehouse="WH-1")
        self.begin("LI-1")
        self.tap("a=erp_item&v=each&n=LI-1")
        self.assertEqual(self.labels(self.replies[-1][0]), [key[:20] for key in ("erp.line.items.newIn", "erp.line.tag.none",
                                                                                 "erp.line.items.pick", "Cancel")])
        self.tap("a=erp_item&v=none&n=LI-1&i=0")
        self.tap("a=erp_item&v=pick&n=LI-1&i=1")
        self.assertEqual(self.replies[-1][0]["text"], "erp.line.items.type")
        with patch.object(self.items, "_products", return_value=[("P-9", "C9 Box", False), ("P-8", "C8 Bag", False)]):
            self.say("bo")
        self.assertTrue(self.replies[-1][0]["text"].startswith("Found 2."))
        frappe.db.get_value.return_value = {"express_code": "", "express_stock": 0}
        self.tap("a=erp_item&v=o&n=LI-1&i=1&k=0")
        frappe.db.get_value.return_value = None
        self.assertEqual([(row["item_type"], row["product"]) for row in self.lines("LI-1")],
                         [("service", None), ("goods", "P-9")])
        self.assertEqual(self.replies[-1], [{"card": "LI-1"}])

    def test_the_warehouse_is_asked_only_for_stock_lines_and_kept_for_the_batch(self):
        self.add("LI-1", [self.row(True, product="P1")])
        self.add("LI-2", [self.row(True, product="P2")])
        question, = self.begin("LI-1", "LI-2")
        self.assertEqual(self.labels(question), ["1. Main", "2. Shop", "Cancel"])
        self.tap("a=erp_item&v=w&n=LI-1&k=1")
        self.assertEqual(json.loads(self.docs["LI-1"].line_choices)["warehouse"], "WH-2")
        self.assertEqual(self.sessions["U1"]["payload"]["batch_warehouse"], "WH-2")
        # 排着的第二张沿用这次选的仓、不再问：两张逐张出卡
        self.assertEqual(json.loads(self.docs["LI-2"].line_choices)["warehouse"], "WH-2")
        self.assertEqual(self.replies[-1], [{"card": "LI-1"}, {"card": "LI-2"}])

    def test_only_the_warehouse_is_asked_never_a_location(self):
        """11-19 只问仓库（一张票问一次，各行同仓；要逐行不同仓在「编辑」里改）：仓定了、仓建了仓位清单也不问仓位，
        行上不记仓位，记忆也不再带仓位。"""
        self.add("LI-1", [self.row(True, product="P1"), self.row(True, product="P2")], warehouse="WH-1")
        self.assertEqual(self.begin("LI-1"), [{"card": "LI-1"}])
        self.assertTrue(all("location" not in row for row in self.lines("LI-1")))
        self.assertNotIn("location_asked", json.loads(self.docs["LI-1"].line_choices))
        self.add("LI-2", [self.row(True, product="P1")])
        question, = self.begin("LI-2")
        self.assertEqual(self.labels(question), ["1. Main", "2. Shop", "Cancel"])
        self.tap("a=erp_item&v=w&n=LI-2&k=0")
        self.assertEqual(self.replies[-1], [{"card": "LI-2"}])  # 选了仓就回卡片，不接着问仓位
        self.tap("a=erp_item&v=l&n=LI-2&k=0")  # 老版本提问留下的仓位按钮：不认
        self.assertEqual(self.last_text(), "This option is no longer current")

    def test_documents_are_asked_one_at_a_time_and_cancel_moves_on(self):
        self.add("LI-1", [self.row()], warehouse="WH-1")
        self.add("LI-2", [self.row()], warehouse="WH-1")
        question, = self.begin("LI-1")
        # 排在 LI-1 后面：回「排队中」，后面紧跟 LI-1 在问的那道题（快捷回复只挂在最后一条上，11-19 真机），不出卡、不问 LI-2
        queued, again = self.begin("LI-2")
        self.assertEqual(queued, {"type": "text", "text": "Queued"})
        self.assertEqual((again["text"], self.labels(again)), (question["text"], self.labels(question)))
        self.tap("a=erp_item&v=all_new&n=LI-2")               # 不是当前这张：无效
        self.assertEqual(self.last_text(), "This option is no longer current")
        self.tap("a=erp_item&v=cancel&n=LI-1")
        # 取消 = 这张作废（同卡片「取消」），回「已取消」，接着问下一张；作废的这张不出卡
        self.assertEqual(self.cancelled, ["LI-1"])
        self.assertEqual(self.replies[-1][0], {"type": "text", "text": "Cancelled"})
        self.assertTrue(self.replies[-1][1]["text"].startswith("Acme · LI-2"))
        self.assertNotIn({"card": "LI-1"}, [message for reply in self.replies for message in reply])

    def test_another_person_s_document_is_not_answered(self):
        self.add("LI-1", [self.row()], warehouse="WH-1")
        self.begin("LI-1")
        self.docs["LI-1"].sent_by = "someone-else"
        self.tap("a=erp_item&v=all_none&n=LI-1")
        self.assertEqual(self.last_text(), "This option is no longer current")
        self.assertFalse(self.lines("LI-1")[0]["decided"])

    # ── 写账前置判定的题：出卡前问完（用户 11-19，共用出题器 `line_ask`）──

    def all_messages(self):
        return [message for reply in self.replies for message in reply]

    def test_express_items_are_asked_in_line_after_the_lines_and_the_card_comes_last(self):
        self.add("LI-1", [self.row(True, product="P1"), self.row()], warehouse="WH-1")
        self.judge.docs["I:LI-1"] = [{**VAT, "subject": "LI-1|freight"}]
        self.begin("LI-1")
        self.tap("a=erp_item&v=all_new&n=LI-1")          # 先答物料
        question, = self.replies[-1]                     # 接着是 Express 要人选的那一题，不是卡
        self.assertEqual(question["text"].splitlines(), ["Acme · LI-1", "Freight"])
        self.assertEqual(self.labels(question), ["expense", "claim", "Cancel"])  # 推荐排第一
        self.assertNotIn({"card": "LI-1"}, self.all_messages())
        self.press(question, 1)
        self.assertEqual(self.judge.records, [("CO-1", ["I:LI-1"], [{"key": precheck_card.item_key("vat", "LI-1|freight"),
                                                                     "value": "claim"}])])
        self.assertEqual(self.replies[-1], [{"card": "LI-1"}])  # 问完才出卡，卡在最后
        self.assertFalse(line_questions.line_ask.asking(self.sessions["U1"]["payload"]))

    def test_mr_erp_items_are_asked_in_line_before_the_card(self):
        """11-19：MR.ERP 公司导入前要人定的（导入菜单）同样出卡前在 LINE 里问完。"""
        self.add("LI-1", [self.row(True, product="P1")], warehouse="WH-1")
        self.judge.docs["I:LI-1"] = [{"kind": "mrerp_menu", "subject": "purchase", "label": "purchase",
                                      "options": [{"code": "HP", "name": "HP"}, {"code": "RR", "name": "RR"}]}]
        question, = self.begin("LI-1")
        self.assertEqual(self.labels(question), ["HP", "RR", "Cancel"])
        self.press(question, 1)
        self.assertEqual(self.judge.records[-1][2], [{"key": precheck_card.item_key("mrerp_menu", "purchase"), "value": "RR"}])
        self.assertEqual(self.replies[-1], [{"card": "LI-1"}])

    def test_the_queued_reply_carries_the_open_item_question_and_its_buttons_work(self):
        """11-19 真机：票 A 的新物料题在等答时发票 B，只回「排队中」，A 的按钮就没了。现在后面紧跟 A 的题，点它照样往下走。"""
        self.add("LI-1", [self.row()], warehouse="WH-1")
        self.add("LI-2", [self.row(True, product="P2")], warehouse="WH-1")
        self.begin("LI-1")
        queued, again = self.begin("LI-2")
        self.assertEqual(queued["text"], "Queued")
        self.assertTrue(again["text"].startswith("Acme · LI-1"))
        self.press(again, 0)                              # 全部新建并入库
        self.assertEqual(self.lines("LI-1")[0]["new"], True)
        self.assertEqual(self.replies[-1], [{"card": "LI-1"}, {"card": "LI-2"}])

    def test_unrelated_words_while_an_item_question_waits_ask_it_again(self):
        """11-19 真机：答物料题时打「x」回「请点击下方菜单开始」，题再也找不回来。现在重发这道题（按钮照样能点）。"""
        self.add("LI-1", [self.row()], warehouse="WH-1")
        question, = self.begin("LI-1")
        self.say("x")
        again, = self.replies[-1]
        self.assertEqual((again["text"], self.labels(again)), (question["text"], self.labels(question)))
        self.press(again, 0)
        self.assertEqual(self.replies[-1], [{"card": "LI-1"}])

    def test_unrelated_words_while_a_precheck_question_waits_ask_it_again(self):
        self.add("LI-1", [self.row(True, product="P1")], warehouse="WH-1")
        self.judge.docs["I:LI-1"] = [{**VAT, "subject": "s"}]
        question, = self.begin("LI-1")
        self.say("x")
        miss, again = self.replies[-1]
        self.assertEqual(miss["text"], "No match for “x”")
        self.assertEqual((again["text"], self.labels(again)), (question["text"], self.labels(question)))
        self.press(again, 1)                              # 重出的按钮能点
        self.assertEqual(self.replies[-1], [{"card": "LI-1"}])
        self.say("เมนู")                                  # 没题在等答：菜单词照常交给菜单
        self.assertNotEqual(self.replies[-1], [again])

    def other_card(self, action):
        """点另一张（已出卡的）卡片上的按钮：保存回「已过账」、取消回「已取消」。"""
        with patch.object(intake, "save_card", return_value={"name": "PD-1", "system_no": "PE-1"}), \
                patch.object(frappe, "get_doc", side_effect=lambda doctype, name=None: self.docs[name], create=True), \
                patch("pearnly_erp_portal.integrations.line_cards.saved", return_value={"type": "text", "text": "Posted · PE-1"}):
            self.tap(f"a={action}&n=LI-1")
        return self.replies[-1]

    def test_the_reply_exit_reads_the_same_session_keys(self):
        from pearnly_erp_portal.integrations import line_out

        self.assertEqual((line_out.STATE, line_out.ITEMS, line_out.PRECHECK),
                         (line_flow.STATE, self.items.ASKING, line_questions.line_ask.KEY))

    def test_replies_to_another_card_carry_the_open_item_question(self):
        """11-19 真机：B 的物料题在等答时点 A 卡「保存」，「已过账」成了最后一条，B 的按钮看不见了。现在末尾重带 B 的题，点它照样走。"""
        self.add("LI-1", [self.row(True, product="P1")], warehouse="WH-1")
        self.add("LI-2", [self.row()], warehouse="WH-1")
        card, question = self.begin("LI-1", "LI-2")
        self.assertEqual(card, {"card": "LI-1"})
        posted, again = self.other_card("erp_save")
        self.assertEqual(posted["text"], "Posted · PE-1")
        self.assertEqual((again["text"], self.labels(again)), (question["text"], self.labels(question)))
        cancelled, again = self.other_card("erp_cancel")
        self.assertEqual((cancelled["text"], again["text"]), ("Cancelled", question["text"]))
        self.press(again, 0)
        self.assertEqual(self.replies[-1], [{"card": "LI-2"}])  # 问完不再重带

    def test_replies_to_another_card_carry_the_open_precheck_question(self):
        self.add("LI-1", [self.row(True, product="P1")], warehouse="WH-1")
        self.add("LI-2", [self.row(True, product="P2")], warehouse="WH-1")
        self.judge.docs["I:LI-2"] = [{**VAT, "subject": "two"}]
        card, question = self.begin("LI-1", "LI-2")
        posted, again = self.other_card("erp_save")
        self.assertEqual(posted["text"], "Posted · PE-1")
        self.assertEqual(again, question)                 # 同一题、同一组按钮（已发的那组仍有效）
        self.press(question, 1)
        self.assertEqual(self.judge.records[-1][1:], (["I:LI-2"], [{"key": precheck_card.item_key("vat", "two"), "value": "claim"}]))
        self.assertEqual(self.replies[-1], [{"card": "LI-2"}])

    def test_the_inventory_account_group_is_asked_before_the_card(self):
        """11-19 真机：账套里已有库存品、挂得最多的组不合格，新建库存品写账时才停下。判定现在出这一题（合格的组里选、按账套记住）。"""
        self.add("LI-1", [self.row(True, product="P1")], warehouse="WH-1")
        self.judge.docs["I:LI-1"] = [{"kind": "stock_group", "subject": "BOOK-1", "label": "69EXP\\TEST",
                                      "options": [{"code": "DM", "name": "DM · 11-04-01-00"}, {"code": "ST10", "name": "ST10"}]}]
        question, = self.begin("LI-1")
        self.assertEqual(self.labels(question), ["DM · 11-04-01-00", "ST10", "Cancel"])
        self.press(question, 0)
        self.assertEqual(self.judge.records[-1][2], [{"key": precheck_card.item_key("stock_group", "BOOK-1"), "value": "DM"}])
        self.assertEqual(self.replies[-1], [{"card": "LI-1"}])

    def test_no_fit_inventory_account_group_says_why_and_the_card_has_no_save(self):
        """没有合格的组可选（只能会计在 Express 里改科目）：出卡前一句现有原因，卡上不放「保存」。"""
        self.add("LI-1", [self.row(True, product="P1")], warehouse="WH-1")
        self.judge.facts["I:LI-1"] = {"state": "not_writable", "reason": "stock_acc_group_missing"}
        self.assertEqual(self.begin("LI-1"), [{"type": "text", "text": "express.error.STOCK_ACC_GROUP_UNFIT"},
                                              {"card": "LI-1", "save": False}])

    def test_a_ticket_with_nothing_to_ask_gets_its_card_at_once(self):
        self.add("LI-1", [self.row(True, product="P1")], warehouse="WH-1")
        self.judge.docs["I:LI-1"] = [{**VAT, "subject": "s", "value": "claim", "source": precheck_card.REMEMBERED}]
        self.assertEqual(self.begin("LI-1"), [{"card": "LI-1"}])
        self.assertEqual(self.judge.records, [])

    def test_a_recommendation_not_tapped_does_not_move_on(self):
        self.add("LI-1", [self.row(True, product="P1")], warehouse="WH-1")
        self.judge.docs["I:LI-1"] = [{**VAT, "subject": "s"}]
        question, = self.begin("LI-1")
        self.assertEqual(self.labels(question)[0], "expense")
        self.say("ok")                                    # 不是编号、也不是搜索题：回没找到并重问这一题
        self.assertEqual(self.replies[-1][0]["text"], "No match for “{0}”".replace("{0}", "ok"))
        self.assertEqual(self.replies[-1][1]["text"], question["text"])
        self.assertEqual(self.judge.records, [])          # 推荐只是推荐：没点不记、不出卡
        self.assertNotIn({"card": "LI-1"}, self.all_messages())

    def test_a_refused_answer_says_why_and_asks_again(self):
        self.add("LI-1", [self.row(True, product="P1")], warehouse="WH-1")
        self.judge.docs["I:LI-1"] = [{**VAT, "subject": "s"}]
        self.judge.refuse.add("claim")
        question, = self.begin("LI-1")
        self.press(question, 1)
        self.assertEqual(self.replies[-1][0], {"type": "text", "text": "Not accepted"})
        self.assertEqual(self.labels(self.replies[-1][1]), self.labels(question))
        self.assertNotIn({"card": "LI-1"}, self.all_messages())
        self.press(self.replies[-1][1], 0)
        self.assertEqual(self.replies[-1], [{"card": "LI-1"}])

    def test_cancel_on_a_precheck_question_voids_the_ticket(self):
        self.add("LI-1", [self.row(True, product="P1")], warehouse="WH-1")
        self.judge.docs["I:LI-1"] = [{**VAT, "subject": "s"}]
        question, = self.begin("LI-1")
        self.press(question, len(question["quickReply"]["items"]) - 1)  # 「取消」
        self.assertEqual(self.cancelled, ["LI-1"])
        self.assertEqual(self.replies[-1], [{"type": "text", "text": "Cancelled"}])
        self.assertNotIn({"card": "LI-1"}, self.all_messages())
        self.press(question, 0)                           # 取消后旧按钮无效
        self.assertEqual(self.last_text(), "This option is no longer current")
        self.assertEqual(self.judge.records, [])

    def test_a_new_ticket_while_answering_waits_and_answers_stay_on_their_own_ticket(self):
        self.add("LI-1", [self.row(True, product="P1")], warehouse="WH-1")
        self.add("LI-2", [self.row(True, product="P2")], warehouse="WH-1")
        self.judge.docs["I:LI-1"] = [{**VAT, "subject": "one"}]
        self.judge.docs["I:LI-2"] = [{**VAT, "subject": "two"}]
        asked, = self.begin("LI-1")
        queued, first = self.begin("LI-2")               # 答题中来的新票：排队，不出卡、不插题；后面紧跟 LI-1 在问的题
        self.assertEqual(queued, {"type": "text", "text": "Queued"})
        self.assertEqual((first["text"], self.labels(first)), (asked["text"], self.labels(asked)))
        self.press(first, 0)                              # 重出的这一组按钮照样能点
        self.assertEqual(self.judge.records[-1][1], ["I:LI-1"])  # 答案只记给 LI-1
        card, second = self.replies[-1]
        self.assertEqual(card, {"card": "LI-1"})
        self.assertEqual(second["text"].splitlines()[0], "Acme · LI-2")
        self.press(first, 1)                              # LI-1 的旧按钮点在 LI-2 的题上：无效，不串单
        self.assertEqual(self.last_text(), "This option is no longer current")
        self.assertEqual(len(self.judge.records), 1)
        self.press(second, 1)
        self.assertEqual(self.judge.records[-1][1:], (["I:LI-2"], [{"key": precheck_card.item_key("vat", "two"),
                                                                     "value": "claim"}]))
        self.assertEqual(self.replies[-1], [{"card": "LI-2"}])

    def test_an_expired_session_shows_no_card(self):
        self.add("LI-1", [self.row(True, product="P1")], warehouse="WH-1")
        self.judge.docs["I:LI-1"] = [{**VAT, "subject": "s"}]
        question, = self.begin("LI-1")
        self.sessions.pop("U1")                           # 30 分钟过了
        self.press(question, 0)
        self.assertEqual(self.replies[-1], [{"type": "text", "text": "This option is no longer current"}])
        self.assertNotIn("U1", self.sessions)             # 不重建会话（别的流程的会话不被盖掉）
        self.assertEqual(self.judge.records, [])
        self.assertNotIn({"card": "LI-1"}, self.all_messages())

    def test_choosing_purchase_again_keeps_the_open_question(self):
        self.add("LI-1", [self.row(True, product="P1")], warehouse="WH-1")
        self.judge.docs["I:LI-1"] = [{**VAT, "subject": "s"}]
        question, = self.begin("LI-1")
        with patch("pearnly_erp_portal.masters.company.allowed_companies", return_value=["CO-1", "CO-2"]), \
                patch.object(line_flow, "_options", return_value=[("upload", "Upload and recognise")]), \
                patch("frappe.utils.get_url", return_value="https://testerp.pearnly.com"):
            self.tap("a=mode:purchase")                   # 答题中又点菜单「ซื้อ」重选
            self.tap("a=erp&s=cancel")                    # 再取消重选
        self.press(question, 0)
        self.assertEqual(self.replies[-1], [{"card": "LI-1"}])


class Language(Base):
    def test_replies_follow_the_phone_language_and_restore_the_site_language(self):
        from pearnly_core import line

        frappe.local.lang = "th"
        seen = []
        self.enterContext(patch.object(line, "user_language", return_value="zh"))
        self.enterContext(patch.object(handlers, "_handle", side_effect=lambda event: seen.append(frappe.local.lang)))
        handlers.handle({"type": "follow", "source": {"userId": "U1"}})
        self.assertEqual((seen, frappe.local.lang), (["zh"], "th"))

    def test_phone_language_is_read_from_the_line_profile(self):
        from pearnly_core import line

        cache = {}
        self.enterContext(patch.object(frappe, "cache", Mock(**{"get_value.side_effect": cache.get,
            "set_value.side_effect": lambda key, value, expires_in_sec=None: cache.__setitem__(key, value)}), create=True))
        def reply(status, body):
            return Mock(status_code=status, json=Mock(return_value=body))

        self.enterContext(patch.object(line, "_headers", return_value={}))
        for tag, expected in (("zh-Hant", "zh"), ("ja", "ja"), ("ko", "th"), (None, "th")):
            cache.clear()
            with patch("requests.get", return_value=reply(200, {"language": tag} if tag else {})) as profile:
                self.assertEqual(line.user_language("U1", default="th"), expected)
                self.assertEqual(line.user_language("U1", default="th"), expected)
                profile.assert_called_once()  # 第二次走缓存
        cache.clear()
        with patch("requests.get", return_value=reply(429, {})) as profile:
            self.assertEqual(line.user_language("U1", default="th"), "th")
            self.assertEqual(line.user_language("U1", default="th"), "th")
            self.assertEqual(profile.call_count, 2)  # 没问到不记缓存

    def test_japanese_typed_words_are_understood(self):
        # 11-08 补日文：手打的菜单、解绑、采购 / 销售词也认日文（同按钮上的字与 DMS、Cowork）
        self.assertTrue(handlers._is_unbind("連携解除"))
        self.assertIn("メニュー", line_flow.MENU_WORDS)
        self.assertEqual((line_flow.DIRECTION_WORDS["仕入"], line_flow.DIRECTION_WORDS["売上"]), ("purchase", "sales"))

    def test_unsupported_phone_language_uses_the_bound_account_language(self):
        # 用户 2026-11-08（主会话转）：跟手机 LINE 语言不变；手机语言不在四种里（韩文）或问不到时，
        # 已绑定的用网页个人资料的语言，未绑定的才回落站点语言
        from pearnly_core import line, line_binding

        cache = {}
        self.enterContext(patch.object(frappe, "cache", Mock(**{"get_value.side_effect": cache.get,
            "set_value.side_effect": lambda key, value, expires_in_sec=None: cache.__setitem__(key, value)}), create=True))
        self.enterContext(patch.object(line, "_headers", return_value={}))
        bound = {"U1": "somchai@example.com"}
        self.enterContext(patch.object(line_binding, "user_of", side_effect=bound.get))
        self.enterContext(patch.object(frappe.db, "get_value", side_effect=lambda doctype, name, field=None, *a, **k:
                                       "ja" if (doctype, name, field) == ("User", "somchai@example.com", "language") else None))

        def phone(tag):
            cache.clear()
            body = {"language": tag} if tag else {}
            return patch("requests.get", return_value=Mock(status_code=200, json=Mock(return_value=body)))

        with phone("ko"):
            self.assertEqual(line.user_language("U1", default="th"), "ja")
            self.assertEqual(line.user_language("U1", default="th"), "ja")  # 走缓存也一样
        with phone(None):
            self.assertEqual(line.user_language("U1", default="th"), "ja")
        with phone("zh-Hant"):
            self.assertEqual(line.user_language("U1", default="th"), "zh")  # 手机语言在四种里：照旧跟手机
        with phone("ko"):
            self.assertEqual(line.user_language("U2", default="th"), "th")  # 没绑定：回落
        cache.clear()
        with patch("requests.get", return_value=Mock(status_code=429, json=Mock(return_value={}))):
            self.assertEqual(line.user_language("U1", default="th"), "ja")  # 问不到 LINE 也先用账号语言

    def test_the_card_uses_the_language_recorded_when_the_document_arrived(self):
        self.enterContext(patch.object(intake, "stopped_reason", return_value=""))
        from pearnly_core import line

        seen = {}
        doc = _doc(status=intake.QUEUED, language="zh")
        doc.reload = lambda: None
        doc.save = lambda **kw: None
        self.enterContext(patch.object(frappe, "get_doc", return_value=doc, create=True))
        self.enterContext(patch("pearnly_core.line_binding.user_of", return_value="buyer"))
        self.enterContext(patch.object(line, "user_language", side_effect=AssertionError("must not ask LINE")))
        self.enterContext(patch.object(intake, "_file_name", return_value="FILE-1"))
        self.enterContext(patch.object(intake, "totals", return_value={}))
        self.enterContext(patch.object(intake, "_recogniser", return_value=SimpleNamespace(
            recognise=lambda files, **kw: seen.setdefault("lang", frappe.local.lang) and {"lines": []})))
        self.enterContext(patch("pearnly_erp_portal.integrations.line_cards.preview", return_value={}))
        self.enterContext(patch.object(frappe.db, "commit", create=True))
        intake.run("LI-1", "U1")
        self.assertEqual(seen["lang"], "zh")


class RichMenu(Base):
    def test_tiles_follow_permissions_and_stocktake_opens_the_native_page(self):
        from pearnly_erp_portal.integrations import line_menu

        self.enterContext(patch.object(line_menu, "_", _same))
        self.enterContext(patch("frappe.utils.get_url", return_value="https://testerp.pearnly.com"))
        self.assertEqual(len(line_menu.VARIANTS), 15)  # 11-11 第 4 格收款
        areas = line_menu.payload("purchase+stocktake", "zh")["areas"]
        self.assertEqual([area["bounds"]["x"] for area in areas], [0, 1666])  # 销售那格是锁、不可点
        self.assertEqual(areas[0]["action"]["data"], "a=mode:purchase")
        self.assertEqual(areas[1]["action"], {"type": "uri", "label": "Stock count",
                         "uri": "https://testerp.pearnly.com/api/method/pearnly_core.account.line_login.enter?to=%2Fdesk%2Fpearnly-stock-count"})
        self.roles["buyer"] = {"integration.read", "purchase.write", "count.write"}
        self.assertEqual(line_menu.wanted_variant("buyer"), "purchase+stocktake")
        self.roles["buyer"] = {"purchase.write"}
        self.assertEqual(line_menu.wanted_variant("buyer"), "")

    def test_menu_image_is_composed_from_the_tiles_for_each_language(self):
        import io
        from pathlib import Path

        from PIL import Image

        from pearnly_erp_portal.integrations import line_menu

        root = Path(line_menu.__file__).resolve().parents[1]
        self.enterContext(patch.object(frappe, "get_app_path", create=True,
                                       side_effect=lambda app, *parts: str(root.joinpath(*parts))))
        for language in line_menu.LANGUAGES:
            image = line_menu._image("purchase+sales+stocktake", language)
            self.assertEqual(Image.open(io.BytesIO(image)).size, (2500, 1686))
        self.assertEqual(line_menu._image("sales", "th"), line_menu._image("sales", "th"))  # 同内容同散列，重发复用

    def test_each_user_gets_the_menu_of_their_phone_language(self):
        from pearnly_erp_portal.integrations import line_menu

        menus = {line_menu.key(language, variant): f"{language}/{variant}"
                 for language in line_menu.LANGUAGES for variant in line_menu.VARIANTS}
        self.enterContext(patch.object(frappe, "conf", {line_menu.CONFIG: menus}, create=True))
        self.enterContext(patch("pearnly_core.line_binding.binding_of", return_value=SimpleNamespace(user="buyer")))
        self.roles["buyer"] = {"integration.read", "sales.write"}
        self.assertEqual(line_menu.wanted_menu("U1", "ja")[1], "ja/sales")
        self.enterContext(patch.object(frappe, "conf", {line_menu.CONFIG: {"both": "old", "sales": "old"}}, create=True))
        self.assertIsNone(line_menu.wanted_menu("U1", "ja")[0])  # 旧的三套泰文发布：不碰官方号


class Recognition(unittest.TestCase):
    def test_percent_discounts_become_amounts(self):
        # 09-26 采购票面「20.00%」被整个丢掉，合计多记 16,963.20。
        from pearnly_erp_portal.purchasing import recognition

        clean = recognition.clean_result({"lines": [
            {"description": "A", "qty": 48, "unit_price": 589, "discount": "20.00%"},
            {"description": "B", "qty": "60", "unit_price": "589.00", "discount": "1,000"},
            {"description": "C", "qty": 1, "unit_price": 10, "discount": "n/a"}],
            "discount_total": "10%"})
        self.assertEqual([row["discount"] for row in clean["lines"]], [5654.4, 1000.0, 0])
        self.assertEqual(clean["discount_total"], round((48 * 589 * 0.8 + 60 * 589 - 1000 + 10) * 0.1, 2))

    def test_vat_included_sales_prices_are_not_taxed_again(self):
        from pearnly_erp_portal.sales import entry

        base = {"has_vat": 1, "vat_rate": 7, "lines": [{"description": "Cool Betty Inliner", "qty": 1, "unit_price": 290},
                {"description": "Eyelash Curler", "qty": 1, "unit_price": 120},
                {"description": "Tweezers", "qty": 1, "unit_price": 79}]}
        included = entry._calculate({**base, "price_includes_vat": 1})
        self.assertEqual((included["grand_total"], included["vat_amount"]), ("489.00", "31.99"))
        self.assertEqual(entry._calculate(base)["grand_total"], "523.23")

    def test_sideways_photo_with_orientation_is_turned_upright_locally(self):
        import io

        from PIL import Image

        from pearnly_core import ocr

        def jpeg(orientation=None):
            out, image = io.BytesIO(), Image.new("RGB", (40, 20), "white")
            exif = image.getexif()
            if orientation:
                exif[0x0112] = orientation
            image.save(out, "JPEG", exif=exif)
            return out.getvalue()

        turned, mime = ocr.upright(jpeg(6), "image/jpeg")
        self.assertEqual((Image.open(io.BytesIO(turned)).size, mime), ((20, 40), "image/jpeg"))
        plain = jpeg()
        self.assertIs(ocr.upright(plain, "image/jpeg")[0], plain)
        self.assertEqual(ocr.upright(b"%PDF-1.4", "application/pdf"), (b"%PDF-1.4", "application/pdf"))


class Amounts(unittest.TestCase):
    def test_amount_only_lines_get_a_unit_price(self):
        # 09-26：小票只印「数量 + 金额」，单价全成了 0，整张单金额为 0。
        from pearnly_erp_portal.purchasing import recognition
        from pearnly_erp_portal.sales import contracts

        mapped = contracts.map_recognised({"doc_no": "02000137", "lines": [
            {"description": "Clinicare", "qty": 2, "amount": "940.00"},
            {"description": "Tweezers", "qty": 1, "unit_price": 79, "amount": 79},
            {"description": "Gift", "qty": 1}]})
        self.assertEqual([row["unit_price"] for row in mapped["lines"]], [470.0, 79.0, 0])
        self.assertEqual(mapped["doc_no"], "02000137")
        clean = recognition.clean_result({"lines": [{"description": "Tyre", "qty": 2, "unit_price": None, "amount": "1,500"}]})
        self.assertEqual(clean["lines"][0]["unit_price"], 750.0)
        self.assertNotIn("amount", clean["lines"][0])


class Checks(unittest.TestCase):
    """旧线上发票硬闸 / 软标注（`recognition.checks`），数字取自 09-26 真机那几张。"""

    def test_consistent_documents_pass(self):
        from pearnly_erp_portal.recognition import checks

        # กิจสยามยานยนต์ 税票：含税 7,500，税前 7,009.35 + VAT 490.65。
        printed = {"subtotal": "7,009.35", "discount": "0.00", "vat": "490.65", "total": "7,500.00"}
        self.assertEqual(checks.printed_issues(printed, [6400, 750, 350], seller_tax="0105542027023",
                                               buyer_tax="0105546015062"), [])
        self.assertEqual(checks.computed_issues(printed, "7500.00"), [])
        # Sister Makeup 含税小票（ABB）：明细和 2,768 = 合计，大于税前小计也放行。
        receipt = {"subtotal": "2,586.92", "vat": "181.08", "total": "2,768.00"}
        self.assertEqual(checks.printed_issues(receipt, [390, 390, 940, 79, 189, 780]), [])

    def test_a_vat_inclusive_amount_read_as_subtotal_is_not_a_mismatch(self):
        from pearnly_erp_portal.recognition import checks

        # 09-26 实测：模型把「รวมเป็นเงิน 7,500」当小计，VAT 490.65、合计 7,500 —— 票面本身是平的。
        self.assertEqual(checks.printed_issues({"subtotal": 7500, "discount": 0, "vat": 490.65, "total": 7500},
                                               [6400, 750, 350]), [])
        self.assertEqual(checks.printed_issues({"subtotal": 67852.8, "discount": 0, "vat": 4438.97,
                                                "total": 67852.8}, [22617.6, 28272, 16963.2]), [])

    def test_a_printed_line_amount_below_qty_times_price_is_a_discount(self):
        from pearnly_erp_portal.purchasing import recognition

        clean = recognition.clean_result({"lines": [
            {"description": "A", "qty": 6, "unit_price": 589, "amount": "2,827.20"},
            {"description": "B", "qty": 2, "unit_price": 589},
            {"description": "C", "qty": 1, "unit_price": 100, "amount": 100}]})
        self.assertEqual([row.get("discount") for row in clean["lines"]], [706.8, None, None])

    def test_percent_discount_column_becomes_an_amount(self):
        from pearnly_erp_portal.purchasing import recognition

        clean = recognition.clean_result({"lines": [{"description": "A", "qty": 48, "unit_price": 589,
                                                     "discount": None, "discount_percent": 20}]})
        self.assertEqual(clean["lines"][0]["discount"], 5654.4)

    def test_the_two_real_failures_are_caught(self):
        from pearnly_erp_portal.recognition import checks

        # NBC：票面合计 67,852.80，丢了 20% 折扣算出 84,816.00。
        self.assertEqual(checks.computed_issues({"total": "67,852.80"}, "84816.00"), ["computed_total"])
        # กิจสยามยานยนต์：合计对上了，但读成「无 VAT」—— 入账会少记 490.65 的税。
        self.assertEqual(checks.computed_issues({"total": "7500", "vat": "490.65"}, "7500.00", "0.00"), ["computed_vat"])
        self.assertEqual(checks.computed_issues({"total": "7500", "vat": "490.65"}, "7500.00", "490.65"), [])
        # 小票只印金额：单价全 0，算出 0，票面 2,768。
        self.assertEqual(checks.computed_issues({"total": "2768"}, "0.00"), ["computed_total"])

    def test_old_hard_gates(self):
        from pearnly_erp_portal.recognition import checks

        self.assertIn("total_missing", checks.printed_issues({"subtotal": "100"}, []))
        self.assertIn("printed_math", checks.printed_issues({"subtotal": "100", "vat": "7", "total": "120"}, []))
        self.assertIn("vat_ratio", checks.printed_issues({"subtotal": "100", "vat": "10", "total": "110"}, []))
        self.assertIn("negative", checks.printed_issues({"subtotal": "-100", "vat": "-7", "total": "-107"}, []))
        self.assertIn("same_tax_id", checks.printed_issues({"total": "1"}, [], seller_tax="0105555167627",
                                                           buyer_tax="0105-555-167627"))
        self.assertIn("tax_id_checksum", checks.printed_issues({"total": "1"}, [], seller_tax="0105555167628"))
        self.assertIn("total_below_line", checks.printed_issues({"total": "50"}, [100]))
        self.assertIn("line_sum", checks.printed_issues({"subtotal": "100", "total": "100"}, [80, 80]))
        # 折扣印在表尾：折前明细和 − 折扣 = 小计，不误杀（旧线上 f003 实案）。
        self.assertEqual(checks.printed_issues({"subtotal": "110", "discount": "5", "vat": "7.35",
                                                "total": "112.35"}, [60, 55]), [])

    def test_vat_mode_comes_from_the_printed_numbers(self):
        from pearnly_erp_portal.purchasing import recognition
        from pearnly_erp_portal.recognition import checks

        printed = {"subtotal": 7009.35, "vat": 490.65, "total": 7500}
        self.assertEqual(checks.vat_mode(printed, 7500), "inclusive")
        self.assertEqual(checks.vat_mode(printed, "7009.35"), "exclusive")
        self.assertIsNone(checks.vat_mode(printed, 1000))
        self.assertIsNone(checks.vat_mode({"total": 100}, 100))
        # 模型读成「无 VAT、不含税」，票面数字说它是含税的 —— 用票面的。
        result = recognition._structured({"has_vat": False, "price_mode": "exclusive", "printed": printed,
                                          "document_type": "tax_invoice", "buyer_tax_id": "0105546015062", "lines": [
            {"description": "Tyre", "qty": 1, "unit_price": 6400, "vat_rate": None, "discount": None},
            {"description": "Tube", "qty": 1, "unit_price": 750},
            {"description": "Flap", "qty": 1, "unit_price": 350}]})
        self.assertEqual((result["has_vat"], result["price_mode"]), (True, "inclusive"))
        self.assertEqual(recognition.review(result), [])

    def test_a_document_without_item_lines_gets_one_line_from_its_printed_amounts(self):
        # 10-23 验收：保险单只印了小计 / 增值税 / 合计、没有明细，卡片金额全是空。按票面税前额补一行服务。
        from pearnly_erp_portal.purchasing import recognition as purchase

        printed = {"subtotal": "32841.04", "vat": "2298.87", "total": "35139.91"}
        result = purchase._structured({"printed": printed, "lines": [], "notes": "เบี้ยประกันภัยรถยนต์\nกรมธรรม์ 1",
                                       "supplier_name": "วิริยะประกันภัย", "document_type": "tax_invoice",
                                       "buyer_tax_id": "0105546015062", "has_vat": True})
        self.assertEqual([(row["description"], row["qty"], row["unit_price"], row["item_type"], row.get("vat_rate"))
                          for row in result["lines"]], [("เบี้ยประกันภัยรถยนต์", 1, 32841.04, "service", 7)])
        self.assertEqual(purchase._structured({"printed": {}, "lines": [], "notes": "x"})["lines"], [])  # 没印金额不补

    def test_tax_ids_that_lost_the_leading_zero_are_restored_only_when_the_check_digit_agrees(self):
        # 用户 2026-09-30：识别出 12 位税号补回开头的 0，补完要过 MOD-11 校验位才补（与 Cowork 同一个共用函数）。
        from pearnly_erp_portal.purchasing import recognition as purchase
        from pearnly_erp_portal.sales import recognition as sales

        lines = [{"description": "Tyre", "qty": 1, "unit_price": 100}]
        bought = purchase._structured({"lines": lines, "supplier_tax_id": "105546015062", "buyer_tax_id": "105546015063"})
        self.assertEqual((bought["supplier_tax_id"], bought["buyer_tax_id"]), ("0105546015062", "105546015063"))
        sold = sales._structured({"lines": lines, "seller_tax_id": "105546015062", "buyer_tax_id": "0105546015062"})
        self.assertEqual((sold["seller_tax_id"], sold["buyer_tax_id"]), ("0105546015062", "0105546015062"))

    def test_abbreviated_tax_invoices_do_not_claim_input_vat(self):
        from pearnly_erp_portal.purchasing import entry, recognition

        # 09-26 真机 7-Eleven 小票（ใบกำกับภาษีอย่างย่อ，VAT Included）：记成 122.43 + 进项税 8.57。
        lines = [{"description": "ข้าวผัดกะเพราหมู", "qty": 1, "unit_price": 47},
                 {"description": "LPขนมปังฟักทองลูกเก", "qty": 1, "unit_price": 35},
                 {"description": "Hซินเซน น้ำส้มคั้น", "qty": 1, "unit_price": 49}]
        raw = {"doc_kind": "purchase_invoice", "has_vat": True, "price_mode": "inclusive",
               "document_type": "simplified_tax_invoice", "lines": lines,
               "printed": {"subtotal": 122.43, "vat": 8.57, "total": 131}}
        result = recognition._structured(raw)
        self.assertIs(result["has_vat"], False)
        self.assertEqual(recognition.review(result), [])
        totals = entry._calculate({"lines": result["lines"], "has_vat": result["has_vat"],
                                   "price_mode": result["price_mode"]})
        self.assertEqual((str(totals["vat_amount"]), str(totals["grand_total"])), ("0.00", "131.00"))
        self.assertEqual([str(row["line_total"]) for row in totals["lines"]], ["47.00", "35.00", "49.00"])
        # 票面标题认得出就以标题为准：模型说是完整税票，标题写着「อย่างย่อ」照样不抵。
        titled = recognition._structured({**raw, "document_type": "tax_invoice",
                                          "document_title": "ใบเสร็จรับเงิน/ใบกำกับภาษีอย่างย่อ"})
        self.assertEqual((titled["document_type"], titled["document_type_basis"], titled["has_vat"]),
                         ("simplified_tax_invoice", "title", False))
        # 完整税票：印了 VAT、写明买方才记进项税；缺买方不记，原因只留在检查代码里（不在卡上显示）。
        full = recognition._structured({**raw, "document_type": "tax_invoice", "buyer_tax_id": "0105546015062"})
        self.assertIs(full["has_vat"], True)
        self.assertEqual(recognition.review(full), [])
        anonymous = recognition._structured({**raw, "document_type": "tax_invoice"})
        self.assertIs(anonymous["has_vat"], False)
        self.assertIn("tax_invoice_without_buyer", recognition.review(anonymous))
        # 认不出票种：不记进项税，交人看；这类原因只标注，不触发换模型重读（不多花识别费）。
        unknown = recognition._structured({**raw, "document_type": None})
        self.assertEqual((unknown["document_type"], unknown["has_vat"]), ("other", False))
        self.assertIn("document_type_unknown", recognition.review(unknown))
        from pearnly_erp_portal.recognition import checks

        self.assertEqual(checks.hard(recognition.review(unknown)), [])

    def test_corpus_receipts_without_quantities_or_with_discount_rows_still_add_up(self):
        from pearnly_erp_portal.purchasing import recognition

        base = {"document_type": "simplified_tax_invoice", "document_title": "ใบกำกับภาษีอย่างย่อ"}
        # 语料 9c2c…：小票不印数量 —— 当 1 件，合计与票面一致。
        no_qty = recognition._structured({**base, "printed": {"subtotal": 204.67, "vat": 14.33, "total": 219}, "lines": [
            {"description": "น้ำเปล่า", "qty": 2, "unit_price": 7}, {"description": "ข้าวกะเพรา", "qty": None, "unit_price": 75},
            {"description": "กาแฟดำ", "unit_price": 50}, {"description": "มักกะโรนี", "amount": 80}]})
        self.assertEqual([row["qty"] for row in no_qty["lines"]], [2, 1, 1, 1])
        self.assertEqual(recognition.review(no_qty), [])
        # 语料 fd54…：折扣行没有单价、只有折扣 —— 并进整单折扣，不算商品。
        coupon = recognition._structured({**base, "printed": {"total": 278}, "lines": [
            {"description": "มันหวาน", "qty": 1, "unit_price": 29}, {"description": "น้ำดื่ม", "qty": 2, "unit_price": 7},
            {"description": "นมโปร", "qty": 3, "unit_price": 49}, {"description": "อเมริกาโน่", "qty": 2, "unit_price": 45},
            {"description": "ส่วนลดโปรเทีนโปรคืนสูง", "qty": 1, "unit_price": None, "discount": 2}]})
        self.assertEqual((len(coupon["lines"]), coupon["discount_total"]), (4, 2.0))
        self.assertEqual(recognition.review(coupon), [])
        # 语料 2470…：模型把同一笔折扣既读成一行、又读进整单折扣 —— 不重复扣。
        twice = recognition._structured({**base, "discount_total": 5, "printed": {"total": 110}, "lines": [
            {"description": "อเมริกาโน่เย็น", "qty": 2, "unit_price": 45}, {"description": "ข้าวโพด", "qty": 1, "unit_price": 25},
            {"description": "ส่วนลดข้าวโพด", "qty": 1, "unit_price": 5}]})
        self.assertEqual((len(twice["lines"]), twice["discount_total"]), (2, 5))
        self.assertEqual(recognition.review(twice), [])

    def test_withholding_on_the_printed_total_is_carried_to_the_lines(self):
        from pearnly_erp_portal.purchasing import entry, recognition
        from pearnly_erp_portal.recognition import checks

        # 语料 v2_007：1,946 + VAT 136.22 − 3% 预扣 58.38 = 实付 2,023.84，模型没读出预扣税率。
        raw = {"document_type": "tax_invoice", "buyer_tax_id": "0105546015062",
               "printed": {"subtotal": 1946, "vat": 136.22, "total": 2023.84}, "lines": [
                   {"description": "ค่าซ่อมเครื่องพิมพ์", "qty": 4, "unit_price": 154},
                   {"description": "กระดาษ A4", "qty": 1, "unit_price": 183},
                   {"description": "หมึกพิมพ์", "qty": 2, "unit_price": 212},
                   {"description": "กาแฟ", "qty": 3, "unit_price": 241}]}
        result = recognition._structured(raw)
        self.assertEqual({row["wht_rate"] for row in result["lines"]}, {3.0})
        totals = entry._calculate({"lines": result["lines"], "has_vat": True, "price_mode": result["price_mode"]})
        self.assertEqual((str(totals["grand_total"]), str(totals["net_payable"])), ("2082.22", "2023.84"))
        self.assertNotIn("printed_math", recognition.review(result))
        # 等式不成立（或几档都说得通）就不猜。
        self.assertIsNone(checks.withholding_rate(1000, 70, 1051))
        self.assertEqual(checks.withholding_rate(1000, 70, 1000, wht_amount=30), 3)
        self.assertEqual(checks.money("(183.82)"), -checks.money("183.82"))
        # 语料 v2_006：5% 折扣与 5% 预扣的算式凑巧相同 —— 扣了折扣已经平，就不是预扣税。
        discounted = recognition._structured({**raw, "discount_total": 63.5,
            "printed": {"subtotal": 1270, "discount": 63.5, "vat": 84.46, "total": 1290.96},
            "lines": [{"description": "a", "qty": 3, "unit_price": 137}, {"description": "b", "qty": 4, "unit_price": 166},
                      {"description": "c", "qty": 1, "unit_price": 195}]})
        self.assertFalse(any(row.get("wht_rate") for row in discounted["lines"]))
        # 语料 v2_005：整张单只有一行 0 元「ส่วนลดคูปอง」—— 照常留作一行。
        zero = recognition._structured({"document_type": "tax_invoice", "printed": {"total": 0},
                                        "lines": [{"description": "ส่วนลดคูปองเต็มจำนวน", "qty": 1, "unit_price": 0}]})
        self.assertEqual(len(zero["lines"]), 1)

    def test_a_missed_bill_discount_is_derived_only_when_the_printed_vat_confirms_it(self):
        from pearnly_erp_portal.purchasing import entry, recognition

        # 语料 Giffarine（ADR-013）：3.1 把整单折扣 3,162 读成 0；票面 VAT 631.18 = 9,648 × 7/107 印证折后金额。
        lines = [{"description": "Giffarine A", "qty": 40, "unit_price": 300}, {"description": "Giffarine B", "qty": 6,
                                                                                    "unit_price": 135}]
        raw = {"document_type": "tax_invoice", "buyer_tax_id": "0105567178203", "discount_total": 0,
               "printed": {"subtotal": 9016.82, "discount": 0, "vat": 631.18, "total": 9648}, "lines": lines}
        result = recognition._structured(raw)
        self.assertEqual(result["discount_total"], 3162.0)
        totals = entry._calculate({"lines": result["lines"], "has_vat": True, "price_mode": result["price_mode"],
                                   "discount_total": result["discount_total"]})
        self.assertEqual((str(totals["vat_amount"]), str(totals["grand_total"])), ("631.18", "9648.00"))
        self.assertEqual(recognition.review(result), [])
        # 票面 VAT 对不上折后金额：不猜，照常标红。
        unproven = recognition._structured({**raw, "printed": {"subtotal": 9016.82, "vat": 700, "total": 9648}})
        self.assertFalse(unproven.get("discount_total"))
        self.assertIn("computed_total", recognition.review(unproven))

    def test_ocr_text_confirms_our_company_as_buyer_and_recovers_a_misread_tax_id(self):
        from pearnly_erp_portal.purchasing import recognition

        # 语料 SM 采购税票：3.1 没读到买方 → 判成「没写买方、不抵进项」；Vision 原文里印着本公司税号。
        raw = {"document_type": "tax_invoice", "supplier_tax_id": "0107542000017",
               "printed": {"subtotal": 2407.48, "vat": 168.52, "total": 2576},
               "lines": [{"description": "อุปกรณ์สำนักงาน", "qty": 1, "unit_price": 2576}]}
        result = recognition._structured(raw)
        issues = recognition.review(result)
        self.assertEqual((result["has_vat"], "tax_invoice_without_buyer" in issues, "tax_id_checksum" in issues),
                         (False, True, True))
        text = "ใบกำกับภาษี\nเลขประจำตัวผู้เสียภาษี 0107542000011\nลูกค้า บริษัท ซิสเตอร์ เมคอัพ\nTax ID 0105567178203\n2,576.00"
        fixed, found = recognition._anchor(result, issues, text, "0105567178203")
        self.assertEqual((fixed["buyer_tax_id"], fixed["supplier_tax_id"], fixed["has_vat"], found),
                         ("0105567178203", "0107542000011", True, []))
        # 买卖读反：本公司税号被读进卖方 —— 挪到买方，卖方取原文里另一个合法税号。
        swapped = recognition._structured({**raw, "supplier_tax_id": "0105567178203"})
        fixed, found = recognition._anchor(swapped, recognition.review(swapped), text, "0105567178203")
        self.assertEqual((fixed["buyer_tax_id"], fixed["supplier_tax_id"], fixed["has_vat"]),
                         ("0105567178203", "0107542000011", True))
        # 原文里没有本公司税号：不改。
        same, found = recognition._anchor(result, issues, "no tax id here 2,576.00", "0105567178203")
        self.assertIs(same, result)

    def test_vision_is_only_called_when_the_checks_ask_for_it(self):
        from pearnly_core import ocr
        from pearnly_erp_portal.purchasing import recognition

        clean = [({"lines": []}, [], "m")]
        with patch.object(ocr, "text_from_image") as vision:
            self.assertIs(recognition._anchor_with_ocr(clean, [(b"x", "image/jpeg")], "CO-1"), clean)
            flagged = [({"lines": []}, ["computed_total"], "m")]
            recognition._anchor_with_ocr(flagged, [(b"x", "image/jpeg")], "CO-1")
            pdf = [({"lines": []}, ["tax_id_checksum"], "m")]
            recognition._anchor_with_ocr(pdf, [(b"%PDF", "application/pdf")], "CO-1")
        vision.assert_not_called()

    def test_credit_notes_are_not_recorded_as_purchases(self):
        from pearnly_erp_portal.purchasing import recognition
        from pearnly_erp_portal.recognition import policy

        result = recognition._structured({"document_type": "credit_note", "document_title": "ใบลดหนี้",
                                          "printed": {"total": "(349.89)", "vat": "(22.89)"},
                                          "lines": [{"description": "น้ำดื่ม", "qty": 2, "unit_price": -48}]})
        with patch.object(recognition.guard, "require"), \
                patch("pearnly_core.ocr.is_configured", return_value=True), \
                patch.object(recognition.files, "upload", return_value=frappe._dict(file_name="a.jpg",
                             get_content=lambda: b"x")), \
                patch.object(recognition.billing, "pages", return_value=1), \
                patch.object(recognition.billing, "gate"), patch.object(recognition.billing, "charge") as charge, \
                patch.object(policy, "read_all", return_value=[(result, [], "m")]), \
                patch("pearnly_core.ocr.orient", side_effect=lambda content, mime: (content, mime)), \
                patch.object(recognition.frappe, "throw", side_effect=_raise), patch.object(recognition, "_", str):
            with self.assertRaises(policy.UnsupportedDocument):
                recognition.recognise([{"file": "F", "kind": "bill"}])
        charge.assert_not_called()

    def test_non_claimable_vat_added_on_top_is_part_of_the_cost(self):
        from pearnly_erp_portal.integrations import intake
        from pearnly_erp_portal.purchasing import entry, recognition

        # 语料 e402…：自助餐收据（不能抵进项），票面 2,544 + VAT 178.08 = 2,722。
        # 行名按站点语言翻译：离线容器没有翻译缓存，这里不测翻译本身。
        translate = patch.object(recognition, "_", lambda message, lang=None: message)
        translate.start()
        self.addCleanup(translate.stop)
        raw = {"document_type": "receipt", "document_title": "ใบเสร็จรับเงิน", "rounding": -0.08,
               "printed": {"subtotal": 2544, "vat": 178.08, "total": 2722}, "lines": [
                   {"description": "Buffet Premium 799", "qty": 3, "unit_price": 799},
                   {"description": "เครื่องดื่มรีฟิล", "qty": 3, "unit_price": 49}]}
        result = recognition._structured(raw)
        recognition._finish(result)  # 归组后会再跑一次：不重复加
        vat_rows = [row for row in result["lines"] if row.get(recognition.NON_CLAIMABLE_VAT)]
        self.assertEqual((result["has_vat"], len(vat_rows), vat_rows[0]["unit_price"], vat_rows[0]["item_type"]),
                         (False, 1, 178.08, "service"))
        totals = entry._calculate({"lines": result["lines"], "has_vat": False, "price_mode": result.get("price_mode"),
                                   "rounding": result.get("rounding")})
        self.assertEqual(str(totals["grand_total"]), "2722.00")
        self.assertEqual(recognition.review(result), [])
        # LINE 里选了「库存商品」：这一行仍是服务、不进仓库。
        doc = _doc(item_kind="goods", result=json.dumps(result))
        with patch.object(intake, "_fallback_name", return_value="x"):
            rows = intake.build_payload(doc)["lines"]
        self.assertEqual([(row["item_type"], "warehouse" in row) for row in rows],
                         [("goods", True), ("goods", True), ("service", False)])
        # 含税价的简式税票明细里已含 VAT：不加这一行。
        abb = recognition._structured({"document_type": "simplified_tax_invoice", "printed": {"vat": 8.57, "total": 131},
                                       "lines": [{"description": "x", "qty": 1, "unit_price": 131}]})
        self.assertFalse(any(row.get(recognition.NON_CLAIMABLE_VAT) for row in abb["lines"]))

    def test_printed_dates_are_converted_by_code(self):
        from datetime import date

        from pearnly_erp_portal.recognition import dates

        today = date(2026, 9, 26)
        self.assertEqual(dates.printed("10/05/2569", today), "2026-05-10")   # 佛历四位
        self.assertEqual(dates.printed("07/05/2026", today), "2026-05-07")   # 公历四位
        self.assertEqual(dates.printed("19/05/69", today), "2026-05-19")     # 两位：只有佛历说得通
        self.assertEqual(dates.printed("07.05.26", today), "2026-05-07")     # 两位：只有公历说得通
        self.assertEqual(dates.printed("2569-05-27", today), "2026-05-27")
        self.assertIsNone(dates.printed("10 พ.ค. 69", today))               # 泰文月份：交给模型
        self.assertIsNone(dates.printed("31/02/2026", today))
        # 到期日：模型照抄了票面「19/05/69」—— 按单据日期之后换算；已是 ISO 原样；换不出不填。
        self.assertEqual(dates.due("19/05/69", "2026-05-19"), "2026-05-19")
        self.assertEqual(dates.due("18/06/69", "2026-05-19"), "2026-06-18")
        self.assertEqual(dates.due("2026-06-18", "2026-05-19"), "2026-06-18")
        self.assertIsNone(dates.due("30 วัน", "2026-05-19"))

    def test_dates_are_soft(self):
        from datetime import date

        from pearnly_erp_portal.recognition import checks

        today = date(2026, 9, 26)
        self.assertEqual(checks.date_issues("2026-09-28", today), ["date_future"])
        self.assertEqual(checks.date_issues("2016-05-07", today), ["date_old"])  # 佛历年份读错十年
        self.assertEqual(checks.date_issues("2026-05-07", today), [])
        self.assertEqual(checks.hard(["date_old", "computed_total"]), ["computed_total"])


class Escalation(unittest.TestCase):
    """ADR-013：3.1 读 → 金额 / 结构对不上才 3.1 带提示复核 → 站点开关打开才升级（默认关）。"""

    def read(self, answers, review, upgrade=None):
        from pearnly_core import ocr

        from pearnly_erp_portal.recognition import policy

        calls = []

        self.last_prompts = []

        def fake(prompt, images, *, model, thinking=None, **kw):
            calls.append((model, thinking, "failed these checks" in prompt))
            self.last_prompts.append(prompt)
            self.assertEqual(kw.get("max_tokens"), policy.MAX_OUTPUT_TOKENS)
            return answers[len(calls) - 1]

        vision = patch.object(ocr, "text_from_image", return_value=None)
        if not isinstance(ocr.text_from_image, Mock):  # 调用方自己替换了原文时不覆盖
            vision.start()
            self.addCleanup(vision.stop)
        with patch.object(ocr, "json_from_images", side_effect=fake), \
                patch.object(policy, "escalation", return_value=upgrade), \
                patch.object(frappe, "logger", return_value=Mock(), create=True):
            result = policy.read("P", [(b"x", "image/jpeg")], structured=lambda raw: raw if "lines" in raw else None,
                                 review=review, label="t")
        return result, calls

    def test_a_clean_first_read_is_not_read_again(self):
        (result, issues, model), calls = self.read([{"lines": [], "n": 1}], lambda r: [])
        self.assertEqual(calls, [("gemini-3.1-flash-lite", {"thinkingLevel": "MINIMAL"}, False)])
        self.assertEqual((result["n"], issues, model), (1, [], "gemini-3.1-flash-lite"))

    def test_amounts_that_do_not_add_up_get_a_hinted_recheck_with_the_same_cheap_model(self):
        answers = [{"lines": [], "n": 1, "printed": {"total": 10}}, {"lines": [], "n": 2}]
        (result, issues, model), calls = self.read(answers, lambda r: ["computed_total"] if r["n"] == 1 else ["date_old"])
        self.assertEqual(calls, [("gemini-3.1-flash-lite", {"thinkingLevel": "MINIMAL"}, False),
                                 ("gemini-3.1-flash-lite", {"thinkingLevel": "LOW"}, True)])
        self.assertEqual((result["n"], issues, model), (2, ["date_old"], "gemini-3.1-flash-lite"))
        # 复核没有改善：保留第一次。
        (result, _issues, _model), _calls = self.read(answers, lambda r: ["computed_total"])
        self.assertEqual(result["n"], 1)

    def test_problems_a_reread_does_not_fix_are_only_flagged(self):
        # 语料实测：税号校验位不过，3.8 重读多半还是错 —— 只标红（采购识别另用 OCR 原文核对）；软标注同样不重读。
        for issues in (["tax_id_checksum"], ["date_old"], ["credit_note"], ["tax_invoice_without_buyer"]):
            with self.subTest(issues):
                (_result, got, _model), calls = self.read([{"lines": [], "n": 1}], lambda r, i=issues: i)
                self.assertEqual((len(calls), got), (1, issues))

    def test_an_unreadable_photo_goes_straight_to_the_ocr_text_read(self):
        from pearnly_core import ocr

        # 语料实测：严重退化的照片让模型重复输出、写满上限；看图复核会同样失控，直接「原图 + 原文」读。
        with patch.object(ocr, "text_from_image", return_value="TOTAL 98.00"):
            (result, issues, model), calls = self.read([None, {"lines": [], "n": 2}], lambda r: [])
        self.assertEqual((len(calls), calls[1][2], result["n"]), (2, False, 2))
        self.assertIn("OCR transcript", self.last_prompts[1])
        (result, _issues, _model), calls = self.read([None], lambda r: [])  # 没有原文：不再花钱
        self.assertEqual((len(calls), result), (1, None))

    def test_a_recheck_that_still_fails_reads_the_image_with_its_ocr_text(self):
        from pearnly_core import ocr

        from pearnly_erp_portal.recognition import policy

        # 语料 NBC 逐行折扣 / Giffarine 整单折扣：看图两次都对不上，「原图 + OCR 原文」读对。
        answers = [{"lines": [], "n": 1}, {"lines": [], "n": 2}, {"lines": [], "n": 3}]
        review = lambda r: [] if r["n"] == 3 else ["computed_total"]
        with patch.object(ocr, "text_from_image", return_value="TOTAL 67,852.80") as vision:
            (result, issues, model), calls = self.read(answers, review)
        vision.assert_called_once()
        self.assertEqual((result["n"], issues, len(calls)), (3, [], 3))
        with patch.object(ocr, "text_from_image", return_value=None):
            (result, _issues, _model), calls = self.read(answers[:2], review)
        self.assertEqual((result["n"], len(calls)), (1, 2))  # 没有原文：不再读

    def test_the_site_switch_upgrades_only_high_value_documents_that_still_fail(self):
        big = {"lines": [], "n": 1, "printed": {"total": 80000}}
        answers = [big, dict(big, n=2), {"lines": [], "n": 3}]
        review = lambda r: [] if r["n"] == 3 else ["computed_total"]
        (result, _issues, model), calls = self.read(answers, review, upgrade=("gemini-3.8-flash", 50000))
        self.assertEqual(([c[0] for c in calls], result["n"], model),
                         (["gemini-3.1-flash-lite", "gemini-3.1-flash-lite", "gemini-3.8-flash"], 3, "gemini-3.8-flash"))
        small = {"lines": [], "n": 1, "printed": {"total": 300}}
        (_result, _issues, _model), calls = self.read([small, dict(small, n=2)], review,
                                                      upgrade=("gemini-3.8-flash", 50000))
        self.assertEqual(len(calls), 2)  # 金额不到下限：不升级
        (_result, _issues, _model), calls = self.read([big, dict(big, n=2)], review)
        self.assertEqual(len(calls), 2)  # 默认没有开关：不升级

    def test_a_pdf_with_a_text_layer_is_read_as_text_first(self):
        from pearnly_core import ocr

        from pearnly_erp_portal.recognition import inputs, policy

        sent = []

        def fake(prompt, images, *, model, thinking=None, **kw):
            sent.append((model, "PAGE TEXT" in prompt, len(images)))
            return {"lines": [], "n": len(sent)}

        pdf = [(b"%PDF", "application/pdf")]
        with patch.object(ocr, "json_from_images", side_effect=fake), \
                patch.object(policy, "escalation", return_value=None), \
                patch.object(frappe, "logger", return_value=Mock(), create=True), \
                patch.object(inputs, "pdf_text", return_value=(1, "PAGE TEXT " * 40)):
            policy.read("P", pdf, structured=lambda raw: raw, review=lambda r: ["computed_total"] if r["n"] == 1 else [],
                        label="t")
        # 首读只给文字、不传原件；金额对不上，复核读 PDF 原件。
        self.assertEqual(sent, [("gemini-3.1-flash-lite", True, 0), ("gemini-3.1-flash-lite", False, 1)])
        with patch.object(inputs, "pdf_text", return_value=(3, "short")):
            self.assertIsNone(inputs.text_path(pdf))  # 文字太少（扫描件）：照常传原件

    def test_pdf_page_limit(self):
        from pearnly_erp_portal.recognition import inputs

        # 上限是三产品原件上传的共用上限（50，盘点 2026-11-08 P0-3；旧线上 20）：50 页照常读，51 页拒
        from pearnly_core.originals import rules

        self.assertEqual(inputs.MAX_PAGES, rules.MAX_PDF_PAGES)
        within = SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: "x")] * inputs.MAX_PAGES)
        over = SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: "x")] * (inputs.MAX_PAGES + 1))
        with patch("pypdf.PdfReader", return_value=within, create=True), \
                patch.object(frappe, "throw", side_effect=frappe.ValidationError, create=True), \
                patch.object(frappe, "_", _same, create=True):
            self.assertEqual(inputs.pdf_text(b"%PDF")[0], inputs.MAX_PAGES)
        with patch("pypdf.PdfReader", return_value=over, create=True), \
                patch.object(frappe, "throw", side_effect=frappe.ValidationError, create=True), \
                patch.object(frappe, "_", _same, create=True):
            with self.assertRaises(frappe.ValidationError):
                inputs.pdf_text(b"%PDF")

    def read_all(self, pages, answers, review=lambda result: [], progress=None):
        from pearnly_core import ocr

        from pearnly_erp_portal.recognition import inputs, policy

        calls = []

        def fake(prompt, images, *, model, thinking=None, **kw):
            calls.append(model)
            return answers[len(calls) - 1]

        with patch.object(ocr, "json_from_images", side_effect=fake), \
                patch.object(frappe, "logger", return_value=Mock(), create=True), \
                patch.object(inputs, "pdf_pages", return_value=pages), \
                patch.object(inputs, "text_path", return_value=None):
            documents = policy.read_all("P", [(b"%PDF", "application/pdf")], structured=lambda raw: dict(raw),
                                        review=review, label="t", fields=("doc_no", "supplier_name"), progress=progress)
        return documents, calls

    def test_web_wait_screen_hears_each_page_as_it_is_read(self):
        # 用户 2026-09-30「按页分进度，不要按份」：多页 PDF 逐页读，每读完一页回报一次（含读不出的页）。
        heard = []
        a1 = {"doc_no": "A1", "lines": [{"n": 1}], "printed": {"total": 30}}
        self.read_all([b"p1", b"p2", b"p3"], [dict(a1), None, None, dict(a1)],
                      progress=lambda done, total: heard.append((done, total)))
        self.assertEqual(heard, [(0, 3), (1, 3), (2, 3), (3, 3)])

    def test_progress_is_kept_per_user_and_a_bad_key_reports_nothing(self):
        from pearnly_erp_portal.recognition import progress

        store = {}
        cache = Mock(set_value=lambda key, value, expires_in_sec=None: store.__setitem__(key, value),
                     get_value=lambda key: store.get(key))
        # 进度与结果在不淘汰的状态存储（`pearnly_core.execution.state`，2026-10-02 网页识别改后台）；推送这里不管
        with patch("pearnly_core.execution.state.store", return_value=cache), \
                patch.object(frappe, "publish_realtime", Mock(), create=True), \
                patch.object(frappe, "session", Mock(user="a@example.com"), create=True):
            self.assertIsNone(progress.reporter("x"))
            self.assertIsNone(progress.reporter(None))
            progress.reporter("k" * 24)(2, 5)
            self.assertEqual(progress.read("k" * 24), {"state": "running", "done": 2, "total": 5})
            self.assertEqual(progress.read("../other"), {})
        with patch("pearnly_core.execution.state.store", return_value=cache), \
                patch.object(frappe, "session", Mock(user="b@example.com"), create=True):
            self.assertEqual(progress.read("k" * 24), {})

    def test_pages_are_read_one_by_one_and_grouped_by_document_number(self):
        # 旧线上 13 页撞限流：逐页读、按单号归组；续页没合计不触发升级。
        answers = [{"doc_no": "A1", "supplier_name": "S", "lines": [{"n": 1}], "printed": {}},
                   {"doc_no": "", "lines": [{"n": 2}], "printed": {"total": 30}},
                   {"doc_no": "B7", "lines": [{"n": 3}], "printed": {"total": 5}}]
        missing = lambda result: [] if (result.get("printed") or {}).get("total") else ["total_missing"]
        documents, calls = self.read_all([b"p1", b"p2", b"p3"], answers, review=missing)
        self.assertEqual(calls, ["gemini-3.1-flash-lite"] * 3)
        self.assertEqual([(d["doc_no"], [row["n"] for row in d["lines"]], d["printed"].get("total"), codes)
                          for d, codes, _model in documents],
                         [("A1", [1, 2], 30, []), ("B7", [3], 5, [])])

    def test_recognition_asks_the_shared_reader_to_keep_stacked_documents(self):
        seen = []
        from pearnly_core import ocr

        from pearnly_erp_portal.recognition import inputs, policy

        def fake(prompt, images, *, model, thinking=None, **kw):
            seen.append(kw.get("stacked"))
            return {"doc_no": "A1", "lines": [], "printed": {"total": 1}}

        with patch.object(ocr, "json_from_images", side_effect=fake), \
                patch.object(frappe, "logger", return_value=Mock(), create=True), \
                patch.object(inputs, "text_path", return_value=None):
            policy.read_all("P", [(b"x", "image/jpeg")], structured=lambda raw: dict(raw), review=lambda r: [],
                            label="t", fields=("doc_no",))
        self.assertEqual(seen, ["additional_documents"])  # 读法里每次调用都带，这里首读就过了

    def test_an_unreadable_page_marks_the_document_before_it_instead_of_vanishing(self):
        # 2026-09-28 压测：一页模型复读失控（单号后一长串 0），几次都读不出；旧做法静默跳过，那页上的单据就漏了。
        a1 = {"doc_no": "A1", "lines": [{"n": 1}], "printed": {"total": 30}}
        b7 = {"doc_no": "B7", "lines": [{"n": 3}], "printed": {"total": 5}}
        documents, calls = self.read_all([b"p1", b"p2", b"p3"], [dict(a1), None, None, dict(b7)])
        self.assertEqual(len(calls), 4)  # 读不出的那页照旧带提示复核一次
        self.assertEqual([(d["doc_no"], d.get("unread_pages"), codes) for d, codes, _model in documents],
                         [("A1", [2], ["page_unread"]), ("B7", None, [])])
        documents, _calls = self.read_all([b"p1", b"p2"], [None, None, dict(a1)])
        self.assertEqual([(d["doc_no"], d.get("unread_pages"), codes) for d, codes, _model in documents],
                         [("A1", [1], ["page_unread"])])
        continued = {"doc_no": "", "lines": [{"n": 2}], "printed": {}}
        documents, _calls = self.read_all([b"p1", b"p2", b"p3"], [dict(a1), dict(continued), None, None])
        self.assertEqual([(d["doc_no"], d.get("unread_pages"), codes) for d, codes, _model in documents],
                         [("A1", [3], ["page_unread"])])

    def test_a_single_result_wrapped_in_an_array_is_read(self):
        from pearnly_core import ocr

        replies = []

        def post(url, headers, payload, timeout_s, model):
            return Mock(status_code=200, json=Mock(return_value={"candidates": [
                {"content": {"parts": [{"text": replies.pop(0)}]}}]}))

        self.enterContext(patch.object(ocr, "backend", return_value="aistudio"))
        self.enterContext(patch.object(ocr, "api_key", return_value="k"))
        self.enterContext(patch.object(ocr, "endpoint", return_value="https://x/{model}"))
        self.enterContext(patch.object(ocr, "_post_with_backoff", side_effect=post))
        self.enterContext(patch.object(frappe, "logger", return_value=Mock(), create=True))
        stacked = '[{"doc_no": "A1"}, {"doc_no": "A2", "additional_documents": [{"doc_no": "A3"}]}]'
        for text, key, expected in (
                ('[{"doc_no": "A1"}]', None, {"doc_no": "A1"}), ('{"doc_no": "A1"}', None, {"doc_no": "A1"}),
                # 一张图几张单写成数组（2026-09-28 压测实测）：ERP 给了 stacked 就第一张作主、其余进附加单据；
                # DMS 身份证等不传 stacked 的照旧按读不出。
                (stacked, "additional_documents",
                 {"doc_no": "A1", "additional_documents": [{"doc_no": "A2"}, {"doc_no": "A3"}]}),
                (stacked, None, None), ("[1]", "additional_documents", None), ("[]", None, None),
                ('"text"', None, None), ("broken", None, None)):
            with self.subTest(text=text, key=key):
                replies.append(text)
                self.assertEqual(ocr.json_from_images("P", [(b"x", "image/jpeg")], model="m", stacked=key), expected)

    def test_several_documents_on_one_image_become_separate_documents(self):
        answers = [{"doc_no": "R1", "lines": [], "additional_documents": [{"doc_no": "R2", "lines": []}]}]
        documents, calls = self.read_all([], answers)
        self.assertEqual([d["doc_no"] for d, _codes, _model in documents], ["R1", "R2"])
        self.assertTrue(all("additional_documents" not in d for d, _codes, _model in documents))

    def test_thinking_is_sent_only_when_asked(self):
        from pearnly_core import ocr

        sent = {}

        def post(url, headers, payload, timeout_s, model):
            sent.update(payload["generationConfig"])
            return Mock(status_code=200, json=Mock(return_value={"candidates": [
                {"content": {"parts": [{"text": "{}"}]}}]}))

        self.enterContext(patch.object(ocr, "backend", return_value="aistudio"))
        self.enterContext(patch.object(ocr, "api_key", return_value="k"))
        self.enterContext(patch.object(ocr, "endpoint", return_value="https://x/{model}"))
        self.enterContext(patch.object(ocr, "_post_with_backoff", side_effect=post))
        ocr.json_from_images("P", [(b"x", "image/jpeg")], model="gemini-3.8-flash", thinking=ocr.thinking_for("gemini-3.8-flash"))
        self.assertEqual(sent["thinkingConfig"], {"thinkingLevel": "LOW"})
        sent.clear()
        ocr.json_from_images("P", [(b"x", "image/jpeg")], model="gemini-3.8-flash")
        self.assertNotIn("thinkingConfig", sent)  # DMS 等不传的调用照旧


class WebRecognition(unittest.TestCase):
    def test_busy_provider_gives_a_plain_message_on_the_web_only(self):
        from pearnly_core.execution.errors import Deferred

        from pearnly_erp_portal.recognition import policy

        def busy():
            raise Deferred("provider_429", 10)

        with patch.object(frappe, "throw", side_effect=RuntimeError, create=True) as thrown, \
                patch.object(frappe, "_", _same, create=True):
            with self.assertRaises(RuntimeError):
                policy.for_web(busy)
        self.assertIn("busy", thrown.call_args.args[0])
        with self.assertRaises(Deferred):
            busy()  # LINE 队列那条路不包：限流要原样交给队列重试


class Billing(Base):
    """按套餐扣：开工前查余额，识别成功才报账，按物理页，B 档（用户 2026-09-26）。"""

    def setUp(self):
        super().setUp()
        self.enterContext(patch.object(intake, "stopped_reason", return_value=""))  # 出卡前核原因：这里没有可判的

    def test_units_are_physical_pages(self):
        from pearnly_erp_portal.recognition import billing, inputs

        with patch.object(inputs, "pdf_page_count", return_value=13):
            self.assertEqual(billing.pages([(b"%PDF", "application/pdf"), (b"img", "image/jpeg")]), 14)

    def test_gate_says_insufficient_balance_but_never_blames_the_user_for_our_outage(self):
        from pearnly_core import billing_client
        from pearnly_core.execution.errors import Deferred

        from pearnly_erp_portal.recognition import billing

        self.enterContext(patch.object(billing, "_", _same))
        self.enterContext(patch.object(frappe, "throw", side_effect=lambda msg, exc=None: (_ for _ in ()).throw(
            (exc or frappe.ValidationError)(msg)), create=True))
        with patch.object(billing_client, "status", return_value={"allowed": False, "error_code": "insufficient_balance"}):
            with self.assertRaises(billing.InsufficientBalance):
                billing.gate(1)
        with patch.object(billing_client, "status", return_value={"allowed": False, "error_code": "lookup_error"}):
            with self.assertRaises(Deferred):
                billing.gate(1)
        with patch.object(billing_client, "status", return_value={"allowed": True}) as status:
            billing.gate(3)
        status.assert_called_once_with("pdf", 3, tier="B")

    def test_line_charges_once_per_intake_and_tells_the_sender_to_top_up(self):
        from pearnly_erp_portal.recognition import billing

        doc = _doc(status=intake.QUEUED, language="th")
        doc.reload = lambda: None
        doc.save = lambda **kw: None
        self.enterContext(patch.object(frappe, "get_doc", return_value=doc, create=True))
        self.enterContext(patch("pearnly_core.line_binding.user_of", return_value="buyer"))
        seen = {}
        self.enterContext(patch.object(intake, "_recogniser", return_value=SimpleNamespace(
            recognise=lambda files, **kw: seen.update(kw) or {"lines": []})))
        self.enterContext(patch.object(intake, "totals", return_value={}))
        self.enterContext(patch("pearnly_erp_portal.integrations.line_cards.preview", return_value={}))
        intake.run("LI-1", "U1")
        self.assertEqual(seen["billing_reference"], "erp.line_intake:LI-1")

        def broke(files, **kw):
            raise billing.InsufficientBalance("Top up please")

        doc.status = intake.QUEUED
        self.checkpoints.clear()  # 另一张、另一份 Work（检查点按 Work 存）
        self.enterContext(patch.object(intake, "_recogniser", return_value=SimpleNamespace(recognise=broke)))
        self.enterContext(patch.object(intake, "_drop"))
        intake.run("LI-1", "U1")
        self.assertEqual(self.last_text(), "Top up please")


def _stale_save():
    """Frappe 保存前比对抛的那一种 TimestampMismatchError（`conflicts._stale_save` 按 frappe/model/document.py 的 check_if_latest 认）。"""
    namespace = {"frappe": frappe}
    exec(compile("def check_if_latest():\n    raise frappe.TimestampMismatchError('modified after you opened it')\n",
                 "frappe/model/document.py", "exec"), namespace)
    namespace["check_if_latest"]()


class _Card:
    """一张收单记录的替身：按名字加锁读回同一张；``conflicts`` 次保存先撞保存前比对，撞了就退回上次存下的样子（同整笔回滚）。"""

    def __init__(self, conflicts=0, **values):
        self.__dict__.update(dict(name="LI-1", direction="purchase", company="CO-1", party="SUP-1", status=intake.QUEUED,
                                  sent_by="buyer", line_user_id="U1", language="th", line_message_id="M1",
                                  source_file_name="FILE-1", source_file="/private/files/a.jpg", result=None), **values)
        self.conflicts, self.saves = conflicts, []
        self._kept = self._fields()

    def _fields(self):
        return {key: value for key, value in self.__dict__.items() if key not in ("conflicts", "saves", "_kept")}

    def get(self, key, default=None):
        return self.__dict__.get(key, default)

    def update(self, values):
        self.__dict__.update(values)

    def reload(self):
        pass

    def save(self, **kwargs):
        if self.conflicts:
            self.conflicts -= 1
            self.__dict__.update(self._kept)
            _stale_save()
        self._kept = self._fields()
        self.saves.append({"status": self.status, "source_file_name": self.source_file_name})


class SaveConflict(Base):
    """④ #12：识别任务记结果 / 记原件撞保存前比对（关快照隔离后另一路在识别期间改过这张）只重来库内那一段：识别、扣费、取件、上传都
    只做一次；用尽按队列稍后再来，重跑从检查点取回结果；记不上的原件登记删掉。"""

    def setUp(self):
        super().setUp()
        from pearnly_erp_portal.integrations import line_out

        self.enterContext(patch.object(intake, "stopped_reason", return_value=""))
        self.enterContext(patch.object(intake, "totals", return_value={}))
        self.enterContext(patch("pearnly_erp_portal.integrations.line_cards.preview", return_value={}))
        self.enterContext(patch("pearnly_core.line_binding.user_of", return_value="buyer"))
        self.enterContext(patch("pearnly_core.transaction.conflicts.time.sleep"))
        self.enterContext(patch("pearnly_core.transaction.conflicts._drop_messages"))  # 替身里没有 frappe.local.flags
        self.enterContext(patch.object(frappe, "log_error", create=True))
        self.enterContext(patch.object(intake.line_items, "prepare"))
        self.sent = self.enterContext(patch.object(line_questions, "send"))
        self.enterContext(patch.object(line_questions, "begin", return_value=[{"type": "flex"}]))
        self.enterContext(patch.object(line_out, "reply_or_push"))
        self.recognised, self.charges = [], []

        def recognise(files, **kw):
            self.recognised.append(files)
            self.charges.append(kw["billing_reference"])  # 识别成功才报账（同一 reference 只扣一次）
            return {"doc_no": "INV-9", "lines": []}

        self.enterContext(patch.object(intake, "_recogniser", return_value=SimpleNamespace(recognise=recognise)))
        self.deleted = []
        self.enterContext(patch.object(frappe, "delete_doc", side_effect=lambda doctype, name, **kw: self.deleted.append(
            (doctype, name)), create=True))
        frappe.db.exists.return_value = True

    def card(self, card):
        self.locked = []

        def get_doc(*args, **kwargs):
            if args and args[0] == intake.DOCTYPE:
                self.locked.append(kwargs.get("for_update", False))
                return card
            raise AssertionError(args)

        self.enterContext(patch.object(frappe, "get_doc", side_effect=get_doc, create=True))
        return card

    def test_a_stale_save_after_recognition_records_the_result_without_recognising_again(self):
        card = self.card(_Card(conflicts=1))
        self.assertEqual(intake.run("LI-1", "U1"), {})
        self.assertEqual((card.status, json.loads(card.result)["doc_no"]), (intake.READY, "INV-9"))
        self.assertEqual((len(self.recognised), self.charges), (1, ["erp.line_intake:LI-1"]))
        self.sent.assert_called_once()  # 发 LINE 在记结果提交之后，只一次
        # 开头判状态与记结果都是加锁读（关快照隔离后普通读可能还是取消之前的样子）
        self.assertTrue(self.locked and all(self.locked), self.locked)

    def test_exhausted_record_defers_and_the_rerun_takes_the_checkpoint(self):
        from pearnly_core.execution.errors import Deferred
        from pearnly_core.transaction import conflicts

        card = self.card(_Card(conflicts=conflicts.ATTEMPTS))
        with self.assertRaises(Deferred):
            intake.run("LI-1", "U1")
        self.assertEqual(card.status, intake.QUEUED)  # 替身里没有真回滚：状态没写成待提交就是没记上
        self.sent.assert_not_called()
        intake.run("LI-1", "U1")  # 队列稍后再来：同一份 Work 的检查点里有结果
        self.assertEqual((card.status, len(self.recognised), len(self.charges)), (intake.READY, 1, 1))
        self.sent.assert_called_once()

    def test_a_card_cancelled_during_recognition_is_not_turned_back_to_ready(self):
        card = self.card(_Card())
        original = intake._recogniser.return_value.recognise

        def cancelled_meanwhile(files, **kw):
            card.status = intake.CANCELLED
            return original(files, **kw)

        intake._recogniser.return_value.recognise = cancelled_meanwhile
        self.assertEqual(intake.run("LI-1", "U1"), {"cancelled": "not_queued"})
        self.assertEqual((card.status, card.saves), (intake.CANCELLED, []))
        self.sent.assert_not_called()

    def fetching(self, conflicts):
        from pearnly_core import line

        card = self.card(_Card(conflicts=conflicts, source_file_name=None, source_file=None))
        self.download = self.enterContext(patch.object(line, "download_message_content", return_value=b"img"))
        self.stored = self.enterContext(patch.object(intake, "_store", return_value=SimpleNamespace(
            name="ERO-F1.bin", file_url="/private/files/ERO-F1.bin")))
        return card

    def test_a_stale_save_after_fetching_records_the_file_without_downloading_again(self):
        card = self.fetching(1)
        self.assertEqual(intake.run("LI-1", "U1", filename="line-M1.jpg"), {})
        self.assertEqual((self.download.call_count, self.stored.call_count), (1, 1))
        self.assertEqual((card.source_file_name, card.status, self.deleted), ("ERO-F1.bin", intake.READY, []))
        self.assertEqual(self.recognised, [[{"file": "ERO-F1.bin", "kind": "bill"}]])

    def test_exhausted_file_record_deletes_the_uploaded_registration(self):
        from pearnly_core.execution.errors import Deferred
        from pearnly_core.transaction import conflicts

        card = self.fetching(conflicts.ATTEMPTS)
        with self.assertRaises(Deferred):
            intake.run("LI-1", "U1", filename="line-M1.jpg")
        # 存放处那份：登记 File 删掉时排提交后任务删（`originals.registered.RegisteredFile.on_trash`）
        self.assertEqual(self.deleted, [("File", "ERO-F1.bin")])
        self.assertEqual((card.source_file_name, self.recognised, self.charges), (None, [], []))


class Products(Base):
    def test_purchase_created_items_take_our_running_code_not_the_printed_one(self):
        from pearnly_erp_portal.products import purchase, service

        created = []
        item = SimpleNamespace(name="PRD-9", code="P-000099", barcode=None, base_unit="pcs", unit="pcs", is_active=1)

        def get_doc(value, name=None):
            if isinstance(value, dict):
                created.append(value)
                return SimpleNamespace(insert=lambda **kw: None, name="PRD-9")
            return item

        self.enterContext(patch("pearnly_erp_portal.access.guard.require"))
        self.enterContext(patch.object(frappe, "get_all", return_value=[], create=True))
        self.enterContext(patch.object(frappe, "get_doc", side_effect=get_doc, create=True))
        self.enterContext(patch.object(service, "_allocate_code", return_value="P-000099"))
        self.enterContext(patch.object(service, "_assert_unique"))
        line = purchase.resolve({"description": "Cushion 01", "code": "110814SX001", "qty": 48,
                                 "unit": "pcs", "unit_price": 589, "line_total": 22617.6})
        self.assertEqual(created[0]["code"], "P-000099")
        self.assertEqual((line["product"], line["code"]), ("PRD-9", "P-000099"))


class SourceFile(Base):
    def test_the_card_uses_its_own_file_record_not_a_shared_url(self):
        # The same photo sent twice gets two File records sharing one file_url; looking the
        # file up by URL returned the earlier card's attached copy and recognition was refused.
        self.assertEqual(intake._file_name(_doc(source_file_name="FILE-2")), "FILE-2")

    def test_the_source_file_is_never_auto_attached_to_the_intake_record(self):
        # Frappe attaches Files referenced by Attach fields to the saved record; an attached
        # file is no longer the uploader's own unattached upload, so recognition refused it.
        from pathlib import Path

        meta = json.loads((Path(intake.__file__).parents[1] / "pearnly_erp_portal" / "doctype"
                           / "erp_line_intake" / "erp_line_intake.json").read_text())
        types = {f["fieldname"]: f["fieldtype"] for f in meta["fields"]}
        self.assertNotIn("Attach", {types["source_file"], types["source_file_name"]})
        self.assertFalse([name for name, kind in types.items() if kind in ("Attach", "Attach Image")])


class Web(Base):
    """卡片「编辑」打开网页端原生页 → 提交入账（用户 2026-09-25）。"""

    def setUp(self):
        super().setUp()
        self.doc = _doc()
        self.enterContext(patch.object(frappe, "db", Mock(), create=True))
        frappe.db.exists.return_value = True
        frappe.db.get_value.side_effect = lambda doctype, name, fields=None, **kw: (
            frappe._dict(name="FILE-1", file_name="a.jpg", file_url="/private/files/a.jpg") if doctype == "File"
            else "Acme Co" if doctype == "ERP Supplier"
            else name if doctype == intake.DOCTYPE and kw.get("for_update") else None)  # `_owned(lock=True)` 的加锁读
        self.enterContext(patch.object(frappe, "get_doc", side_effect=lambda doctype, name=None, **kw: self.doc))
        self.enterContext(patch.object(intake, "_file_name", return_value="FILE-1"))
        self.enterContext(patch.object(intake, "now", lambda: "2026-09-25 23:00:00"))
        self.active = self.enterContext(patch("pearnly_erp_portal.masters.company.active_company", return_value="CO-1"))
        self.switch = self.enterContext(patch("pearnly_erp_portal.masters.company.set_active_company"))
        self.allowed = self.enterContext(patch("pearnly_erp_portal.masters.company.allowed_companies",
                                               return_value=["CO-1", "CO-2"]))
        frappe.session.user = "buyer"
        self.roles["buyer"] |= {"purchase.write"}

    def test_only_the_sender_can_open_or_submit(self):
        frappe.session.user = "someone-else"
        self.roles["someone-else"] = {"purchase.write"}
        with self.assertRaises(frappe.PermissionError):
            intake.open_for_edit("LI-1")
        with self.assertRaises(frappe.PermissionError):
            intake.submit("LI-1", {})

    def test_open_prefills_the_native_form_with_choices_and_source_file(self):
        opened = intake.open_for_edit("LI-1")
        form = opened["doc"]
        self.assertEqual((opened["intake"], opened["company"]), ("LI-1", "CO-1"))
        self.assertEqual((form["supplier"], form["supplier_name"], form["is_paid"], form["payment_method"]),
                         ("SUP-1", "Acme Co", 1, "transfer"))
        self.assertEqual((form["doc_no"], form["lines"][0]["item_type"], form["lines"][0]["warehouse"]),
                         ("INV-1", "goods", "WH-1"))
        self.assertEqual(form["attachments"], [{"file": "FILE-1", "file_name": "a.jpg",
                         "file_url": "/private/files/a.jpg", "kind": "bill", "intake": True}])
        self.assertNotIn("company", form)
        self.switch.assert_not_called()

    def test_open_does_not_hand_recognition_check_reasons_to_the_page(self):
        # 用户 2026-10-02：录入侧不出识别后的错误提示；检查代码只留在结果里供重读 / 升级用。
        self.doc = _doc(result=json.dumps({**json.loads(_doc().result), "review": ["computed_total", "date_old"]}))
        opened = intake.open_for_edit("LI-1")
        self.assertNotIn("review", opened)
        self.assertNotIn("review", opened["doc"])
        self.assertNotIn("ocr_review", opened["doc"])  # 识别标记提交时服务端补（11-03），页面只有票面合计

    def test_open_hands_the_card_company_to_the_page_without_changing_the_users_company(self):
        # 线上 6（11-15）：原来打开就把本人网页的当前公司写成卡片那家；现在只把那家交给这一页（`company.page_company`）
        self.active.return_value = "CO-2"
        self.assertEqual(intake.open_for_edit("LI-1")["company"], "CO-1")
        self.switch.assert_not_called()

    def test_a_card_of_a_company_the_sender_can_no_longer_use_is_refused(self):
        self.allowed.return_value = ["CO-2"]
        with self.assertRaises(frappe.PermissionError):
            intake.open_for_edit("LI-1")

    def test_recorded_card_opens_the_record(self):
        self.doc.status, self.doc.purchase = "confirmed", "PD-1"
        opened = intake.open_for_edit("LI-1")
        self.assertEqual((opened["direction"], opened["recorded"], opened["company"]), ("purchase", "PD-1", "CO-1"))

    def test_submit_posts_once_with_the_card_company_and_file_and_no_line_reply(self):
        seen = {}

        def post(payload, *, request_id, finalize, allow_duplicate):
            seen.update(payload=payload, request_id=request_id, allow_duplicate=allow_duplicate)
            finalize({"name": "PD-1"})
            return {"name": "PD-1"}

        self.enterContext(patch("pearnly_erp_portal.purchasing.entry.post", side_effect=post))
        result = intake.submit("LI-1", {"company": "CO-9", "doc_no": "INV-9", "lines": [],
                                        "attachments": [{"file": "EXTRA", "kind": "payment_proof"}]})
        self.assertEqual(result, {"name": "PD-1"})
        self.assertEqual((seen["request_id"], seen["allow_duplicate"]), ("line-intake:LI-1", True))
        self.assertEqual((seen["payload"]["company"], seen["payload"]["source"], seen["payload"]["doc_no"]),
                         ("CO-1", "line", "INV-9"))
        self.assertEqual([row["file"] for row in seen["payload"]["attachments"]], ["FILE-1", "EXTRA"])
        # 识别标记按这张卡的识别结果补上（写账前核对票面合计，`express.contracts.printed_hold`）
        self.assertEqual(json.loads(seen["payload"]["ocr_review"])["codes"], json.loads(self.doc.result).get("review") or [])
        args = frappe.db.set_value.call_args.args
        self.assertEqual((args[1], args[2]["purchase"], args[2]["status"]), ("LI-1", "PD-1", "confirmed"))
        self.assertEqual(self.replies, [])

    def test_submitted_card_is_not_recorded_twice(self):
        self.doc.status, self.doc.purchase = "confirmed", "PD-1"
        post = self.enterContext(patch("pearnly_erp_portal.purchasing.entry.post"))
        self.assertEqual(intake.submit("LI-1", {}), {"name": "PD-1", "duplicate": True})
        post.assert_not_called()


class CardSave(Web):
    """卡片「保存」（用户 11-18）：不进编辑页直接提交，同编辑页的提交；要人定的项 / 提交时的报错不入账，回原因 + 「编辑」。"""

    def setUp(self):
        super().setUp()
        from pearnly_erp_portal.integrations import line_cards

        self.enterContext(patch.object(line_cards, "_", _same))
        self.enterContext(patch("frappe.utils.get_url", return_value="https://testerp.pearnly.com"))
        self.linked = self.enterContext(patch("pearnly_erp_portal.express.push.linked", return_value=False))
        # 出卡前问的同一份写账前置判定（`intake.unanswered`）：默认都答过了
        self.judged, self.judged_fact = [], {}
        self.card = self.enterContext(patch("pearnly_core.write_precheck.api.card",
                                            side_effect=lambda company, doc_ids: precheck_card.merge(
                                                [{"doc_id": doc_ids[0], "items": list(self.judged), **self.judged_fact}])))
        self.preflight = self.enterContext(patch("pearnly_erp_portal.express.api.preflight", return_value={"needed": []}))
        self.seen = {}

        def post(payload, *, request_id, finalize, allow_duplicate):
            self.seen.update(payload=payload, request_id=request_id)
            finalize({"name": "PD-1"})
            return {"name": "PD-1", "status": "submitted", "system_no": "PE-0001"}

        self.post = self.enterContext(patch("pearnly_erp_portal.purchasing.entry.post", side_effect=post))

    def press(self):
        handlers._postback("U1", "buyer", "a=erp_save&n=LI-1", "tok")
        return self.replies[-1][0]

    def test_save_and_cancel_end_the_callback_read_view_retry_whole_and_commit_before_replying(self):
        # 11-28 B ②：回调开头读过绑定、权限（读视图已建），锁卡片前先提交；撞冲突整笔重来；回 LINE 前已提交
        from pearnly_core.transaction import conflicts

        seen = []
        for act in (None, intake.cancel_card):
            frappe.db.commit.reset_mock()
            calls = []

            def recorded(name, calls=calls):
                calls.append(frappe.db.commit.call_count)
                if len(calls) == 1:
                    raise frappe.QueryDeadlockError("deadlock")
                return {"cancelled": True}

            target = "cancel_card" if act else "save_card"
            with patch.object(intake, target, side_effect=recorded), patch.object(conflicts.time, "sleep"), \
                    patch.object(conflicts, "_drop_messages"), \
                    patch("pearnly_core.line.reply", side_effect=lambda token, messages: seen.append(
                        (target, frappe.db.commit.call_count)) or True):
                handlers._save_card("LI-1", "tok", getattr(intake, target) if act else None)
            self.assertEqual(calls, [1, 1], target)  # 两次都在开头那次提交之后；第一次撞死锁整笔重来
            self.assertEqual(seen[-1], (target, 2))  # 回 LINE 时已提交第二次

    def test_save_posts_the_card_through_the_edit_page_submit(self):
        reply = self.press()
        self.assertEqual(reply, {"type": "text", "text": "Succeeded · PE-0001"})
        self.assertEqual(self.seen["request_id"], "line-intake:LI-1")  # 同编辑页的请求号：两边只入一次
        payload = self.seen["payload"]
        self.assertEqual((payload["company"], payload["source"], payload["supplier"], payload["lines"][0]["warehouse"]),
                         ("CO-1", "line", "SUP-1", "WH-1"))
        self.assertEqual(payload["attachments"][0]["file"], "FILE-1")
        self.preflight.assert_not_called()  # 没接 Express：不判

    def test_save_does_not_post_when_express_needs_a_person(self):
        self.linked.return_value = True
        self.preflight.return_value = {"needed": [{"kind": "account", "subject": "x", "label": "Freight", "options": [],
                                                   "current": ""}, {"kind": "party", "subject": "y", "label": "", "options": []}]}
        reply = self.press()
        self.post.assert_not_called()
        self.assertEqual(self.preflight.call_args.args[0], "Purchase Document")
        self.assertEqual(reply["text"], "express.kind.account · Freight\nSupplier")
        self.assertEqual(reply["quickReply"]["items"][0]["action"]["type"], "uri")

    def test_save_does_not_post_while_a_question_is_unanswered(self):
        """旧卡、LINE 里问不了的题（11-19 问完才出卡）：判定里还有没定的项（推荐没点过的同样算）就不提交，回原因 + 「编辑」。"""
        self.judged = [{**VAT, "subject": "s"}, {"kind": "location", "subject": "erp-intake:LI-1:warehouse",
                                                  "label": "Warehouse", "options": [{"code": "WH-1", "name": "Main"}]}]
        reply = self.press()
        self.post.assert_not_called()
        self.preflight.assert_not_called()
        self.assertEqual(self.card.call_args.args, ("CO-1", ["I:LI-1"]))
        self.assertEqual(sorted(reply["text"].splitlines()), ["Warehouse", "express.kind.vat · Freight"])
        self.assertEqual(reply["quickReply"]["items"][0]["action"]["label"], "Edit")
        self.judged = [{**VAT, "subject": "s", "value": "claim", "source": precheck_card.REMEMBERED}]  # 答过了：照常提交
        self.assertEqual(self.press(), {"type": "text", "text": "Succeeded · PE-0001"})

    def test_save_does_not_post_without_a_fit_inventory_account_group(self):
        """旧卡：判定判出账套里没有合格的存货科目组（只能在 Express 里改）：不入账，回现有原因 + 「编辑」。"""
        self.judged_fact = {"state": "not_writable", "reason": "stock_acc_group_missing"}
        reply = self.press()
        self.post.assert_not_called()
        self.assertEqual(reply["text"], "express.error.STOCK_ACC_GROUP_UNFIT")
        self.assertEqual(reply["quickReply"]["items"][0]["action"]["label"], "Edit")

    def test_save_reports_the_submit_error_with_edit(self):
        self.post.side_effect = frappe.ValidationError("Choose the warehouse")
        reply = self.press()
        self.assertEqual(reply["text"], "Choose the warehouse")
        self.assertEqual(reply["quickReply"]["items"][0]["action"]["label"], "Edit")
        frappe.db.rollback.assert_called()

    def test_only_the_sender_can_save(self):
        self.roles["someone"] = {"purchase.write", "integration.read"}
        handlers._postback("U2", "someone", "a=erp_save&n=LI-1", "tok")
        self.post.assert_not_called()
        self.assertEqual(self.replies[-1][0], {"type": "text", "text": "Not permitted"})

    def test_cancel_does_nothing_and_a_later_save_only_says_cancelled(self):
        drop = self.enterContext(patch.object(intake, "_drop"))
        handlers._postback("U1", "buyer", "a=erp_cancel&n=LI-1", "tok")
        self.assertEqual(self.replies[-1][0], {"type": "text", "text": "Cancelled"})
        self.assertEqual(drop.call_args.args[1], "cancelled")
        self.doc.status = "cancelled"
        self.assertEqual(self.press(), {"type": "text", "text": "Cancelled"})
        handlers._postback("U1", "buyer", "a=erp_cancel&n=LI-1", "tok")
        self.assertEqual(self.replies[-1][0], {"type": "text", "text": "Cancelled"})
        self.post.assert_not_called()
        self.assertEqual(drop.call_count, 1)

    def test_cancel_after_save_keeps_the_record(self):
        self.doc.status, self.doc.purchase = "confirmed", "PD-1"
        drop = self.enterContext(patch.object(intake, "_drop"))
        handlers._postback("U1", "buyer", "a=erp_cancel&n=LI-1", "tok")
        self.assertEqual(self.replies[-1][0], {"type": "text", "text": "Succeeded · PD-1"})
        drop.assert_not_called()

    def test_an_unexpected_error_answers_failed_and_posts_nothing(self):
        self.enterContext(patch.object(frappe, "log_error", create=True))
        self.enterContext(patch.object(frappe, "get_traceback", return_value="trace", create=True))
        self.post.side_effect = RuntimeError("boom")
        self.assertEqual(self.press(), {"type": "text", "text": "Failed"})
        frappe.db.rollback.assert_called()

    def test_recognised_percent_rates_are_numbers_when_the_card_is_saved(self):
        # 11-19 真机：识别结果的税率是「7%」字样，卡片「保存」报「值无效」、卡片合计全是「-」
        result = json.loads(self.doc.result)
        result["lines"] = [{**row, "vat_rate": "7%", "wht_rate": "3.00 %"} for row in result.get("lines") or [{}]]
        self.doc.result = json.dumps({**result, "vat_rate": "7 %"})
        self.press()
        line = self.seen["payload"]["lines"][0]
        self.assertEqual((line["vat_rate"], line["wht_rate"]), (7.0, 3.0))
        self.assertEqual(intake._tidy_numbers("purchase", {"lines": [{"vat_rate": "n/a", "wht_rate": 1}]})["lines"], [{"wht_rate": 1.0}])

    def express_row(self, state, reason="", docnum=""):
        """接了 Express：提交出来的单此刻的账本栏（`line_cards.result` 读的那几栏）。"""
        row = frappe._dict(name="PD-1", system_no="PE-0001", ledger_state=state, ledger_reason=reason, ledger_docnum=docnum)
        other = getattr(self, "_other_values", None) or frappe.db.get_value.side_effect
        self._other_values = other
        frappe.db.get_value.side_effect = lambda doctype, name, fields=None, **kw: (
            row if doctype == "Purchase Document" and isinstance(fields, list) else other(doctype, name, fields, **kw))

    def test_a_linked_company_waits_for_the_write_receipt(self):
        """用户 11-22：接了 Express 的公司提交后还在排写账：先三个点、不先回「已入账」；回执到了回「成功 · Express 单号」，
        没写成回「失败 · 原因」+【编辑】；等不到回「录入中」+【查看结果】。"""
        from pearnly_core.line_ui import receipt as line_receipt
        from pearnly_erp_portal.express import pending
        from pearnly_erp_portal.integrations import line_cards

        self.linked.return_value = True
        self.express_row("queued")
        start = self.enterContext(patch.object(line_receipt, "start", return_value=True))
        settle = self.enterContext(patch.object(line_receipt, "settle"))
        before = len(self.replies)
        handlers._postback("U1", "buyer", "a=erp_save&n=LI-1", "tok")
        self.assertEqual(len(self.replies), before)
        key, _user, token, fallback = start.call_args.args
        self.assertEqual((key, token), ("erp:doc:Purchase Document:PD-1", "tok"))
        # 同一张单：等结果的键 = 回执叫醒的键（`express.pending.line_key`），接着回的短任务也带上这个名字
        self.assertEqual(key, pending.line_key("Purchase Document", "PD-1"))
        self.assertEqual(start.call_args.kwargs["args"], {"intake": "LI-1", "name": "PD-1"})
        self.assertEqual(fallback[0]["text"], "Entering")
        self.assertEqual(fallback[0]["quickReply"]["items"][0]["action"]["data"], "a=erp_result&n=LI-1")
        settle.assert_not_called()
        resume = lambda: line_cards.resume("LI-1", "PD-1")  # noqa: E731  回执叫醒的短任务（`line_receipt.resume` 按 args 调）
        self.assertIsNone(resume())  # 还在排：接着等
        self.express_row("held", "express.no_writer")
        self.assertIsNone(resume())  # 等电脑、每分钟自己再试：还算在录入
        self.express_row("written", docnum="HP6911-0001")
        self.assertEqual(resume(), [{"type": "text", "text": "Succeeded · HP6911-0001"}])
        self.express_row("mismatch", docnum="HP6911-0001")
        self.assertEqual(resume()[0]["text"], "Failed · express.reason.mismatch")
        self.express_row("failed", "[WRITE_FAILED] no account")
        failed = resume()[0]
        self.assertTrue(failed["text"].startswith("Failed · "), failed["text"])
        self.assertEqual(failed["quickReply"]["items"][0]["action"]["label"], "Edit")

    def test_view_result_answers_the_sender_with_the_current_state(self):
        self.doc.status, self.doc.purchase = "confirmed", "PD-1"
        self.express_row("queued")
        handlers._postback("U1", "buyer", "a=erp_result&n=LI-1", "tok")
        self.assertEqual(self.replies[-1][0]["text"], "Entering")
        self.express_row("written", docnum="HP6911-0002")
        handlers._postback("U1", "buyer", "a=erp_result&n=LI-1", "tok2")
        self.assertEqual(self.replies[-1][0], {"type": "text", "text": "Succeeded · HP6911-0002"})
        self.roles["someone"] = {"purchase.write", "integration.read"}
        handlers._postback("U2", "someone", "a=erp_result&n=LI-1", "tok3")
        self.assertEqual(self.replies[-1][0], {"type": "text", "text": "Not permitted"})

    def test_a_saved_card_is_not_recorded_twice(self):
        self.doc.status, self.doc.purchase = "confirmed", "PD-1"
        frappe.db.get_value.side_effect = lambda doctype, name, fields=None, **kw: (
            "PE2569-000020" if (doctype, fields) == ("Purchase Document", "system_no") else None)
        # 再点回的是同一个系统号（11-19 真机：原来第二次显示内部名）
        self.assertEqual(self.press(), {"type": "text", "text": "Succeeded · PE2569-000020"})
        self.post.assert_not_called()


class CardShape(Base):
    def card(self, doc, result, totals):
        from pearnly_erp_portal.integrations import line_cards

        masters = {"ERP Company": {"company_name": "Sister Makeup Co., Ltd.", "tax_id": "0105567178203"},
                   "ERP Supplier": {"supplier_name": "NBC", "tax_id": "0105555167627", "branch_type": "head"}}

        def get_value(doctype, name=None, fields=None, as_dict=False):
            row = masters.get(doctype, {})
            return row if as_dict else (row.get(fields) if isinstance(fields, str) else "Main")

        self.enterContext(patch.object(frappe, "db", Mock(**{"get_value.side_effect": get_value}), create=True))
        self.enterContext(patch.object(line_cards, "_", _same))
        self.enterContext(patch("frappe.utils.get_url", return_value="https://testerp.pearnly.com"))
        return line_cards.preview(doc, result, totals)

    def test_a_card_that_cannot_be_written_has_no_save(self):
        from pearnly_erp_portal.integrations import line_cards

        doc = _doc()
        result = json.loads(doc.result)
        self.card(doc, result, {})
        bare = line_cards.preview(doc, result, {}, save=False)
        self.assertEqual([button["action"]["label"] for button in bare["contents"]["footer"]["contents"]], ["Edit", "Cancel"])

    @staticmethod
    def rows(card):
        # 顶层的横排行就是「左标签 · 右值」那些字段行；明细在底下那个框里。
        return {row["contents"][0]["text"]: row["contents"][1]["text"]
                for row in card["contents"]["body"]["contents"] if row.get("layout") == "horizontal"}

    def test_card_follows_the_old_layout_and_has_only_an_edit_link(self):
        # 11-06 按行选物料与库存：整张一行「物料类型」去掉，有入库行时列单头仓，每行标入库 / 新建 · 入库
        doc = _doc(line_choices=json.dumps({"warehouse": "WH-1", "lines": [
            {"item_type": "goods", "product": "P" if i % 2 else None} for i in range(12)]}))
        result = {**json.loads(doc.result), "doc_kind": "purchase_invoice",
                  "lines": [{"description": f"Item {i}", "qty": 1, "unit_price": 10} for i in range(12)]}
        card = self.card(doc, result, {"grand_total": "107.00", "vat_amount": "7.00", "discount_total": "0",
                                       "lines": [{"line_total": "10.00", "discount": "0"}] * 12})
        header = card["contents"]["header"]["contents"]
        self.assertEqual((header[0]["text"], card["contents"]["header"]["backgroundColor"]),
                         ("Review document · Buy", "#16873E"))
        rows = self.rows(card)
        labels = [label for label in rows]
        self.assertEqual(labels[:4], ["Company", "Transaction type", "Documents", "Warehouse"])
        self.assertEqual(rows["Warehouse"], "WH-1")  # 替身里仓库没有名称：显示编号
        # 采购只列卖方；金额与付款在明细下面（用户 2026-09-26）。
        self.assertEqual(labels[4:], ["Document no.", "Date", "Document type", "Seller", "Seller tax ID",
                         "Seller branch", "Seller address", "Notes",
                         "Subtotal", "Discount", "VAT", "WHT amount", "Document total", "Currency", "Payment method"])
        body = card["contents"]["body"]["contents"]
        items_at = next(i for i, row in enumerate(body) if row.get("layout") == "vertical" and "contents" in row
                        and any("Items" in str(part.get("text", "")) for part in row["contents"]))
        subtotal_at = next(i for i, row in enumerate(body) if row.get("layout") == "horizontal"
                           and row["contents"][0]["text"] == "Subtotal")
        self.assertLess(items_at, subtotal_at)
        self.assertEqual((rows["Date"], rows["Seller"], rows["Subtotal"], rows["Document total"]),
                         ("25/09/2569", "NBC", "100.00", "107.00"))
        text = json.dumps(card, ensure_ascii=False)
        for i in range(12):  # 商品名称全部显示（用户 2026-09-26）
            self.assertIn(f"{i + 1}. Item {i}", text)
        self.assertIn("1 × 10.00 · erp.line.tag.newIn", text)
        self.assertIn("1 × 10.00 · trade.groupIn", text)
        old = self.card(_doc(), result, {})  # 改版前识别的票没有每行选择：不列仓库、不标
        self.assertNotIn("Warehouse", self.rows(old))
        self.assertNotIn("trade.groupIn", json.dumps(old))
        footer = card["contents"]["footer"]["contents"]
        self.assertEqual(len(footer), 3)  # 三个实底：保存（业务色）/ 编辑（灰）/ 取消（灰）（用户 11-18、11-19）
        self.assertEqual(footer[0]["action"], {"type": "postback", "label": "Save", "displayText": "Save",
                                               "data": "a=erp_save&n=LI-1"})
        self.assertEqual((footer[0]["style"], footer[0]["color"], footer[1]["style"]), ("primary", "#16873E", "secondary"))
        self.assertEqual((footer[2]["style"], footer[2]["action"]["data"]), ("secondary", "a=erp_cancel&n=LI-1"))
        self.assertEqual(footer[1]["action"], {"type": "uri", "label": "Edit",
                         "uri": "https://testerp.pearnly.com/api/method/pearnly_core.account.line_login.enter?to=%2Fdesk%2Fpearnly-purchase%2Fline%2FLI-1"})

    def test_card_shows_no_recognition_check_box(self):
        # 用户 2026-10-02：识别没过检查也不在卡上画红框写原因，卡片照常从「公司」开始。
        doc = _doc()
        result = {**json.loads(doc.result), "review": ["computed_total", "tax_id_checksum", "date_old"]}
        card = self.card(doc, result, {"grand_total": "107.00", "vat_amount": "7.00"})
        text = json.dumps(card, ensure_ascii=False)
        self.assertNotIn("Please check this document before submitting", text)
        self.assertNotIn("#D93025", text)
        for reason in ("The items do not add up to the printed total", "A tax ID failed its check digit",
                       "The document date is more than 5 years old"):
            self.assertNotIn(reason, text)
        first = card["contents"]["body"]["contents"][0]
        self.assertEqual(first["contents"][0]["text"], "Company")

    def test_long_documents_stay_within_the_line_bubble_limit(self):
        doc = _doc()
        name = "เอสเอ็มเอ็กซ์อาร์ ดิวอี้สกินคุชชั่น 01พิงค์ไอวอรี่ 12 กรัม"
        lines = [{"description": name, "qty": 48, "unit": "ชิ้น", "unit_price": 589, "discount": 5654.4}] * 60
        card = self.card(doc, {"lines": lines}, {"grand_total": "1", "vat_amount": "0",
                                                  "lines": [{"line_total": "22617.60", "discount": "5654.40"}] * 60})
        text = json.dumps(card, ensure_ascii=False)
        self.assertLessEqual(len(text.encode()), 28 * 1024)
        self.assertIn("more items", text)

    def test_card_names_an_abbreviated_tax_invoice(self):
        card = self.card(_doc(), {"doc_kind": "purchase_invoice", "document_type": "simplified_tax_invoice",
                                  "lines": []}, {})
        self.assertEqual(self.rows(card)["Document type"], "Abbreviated Tax Invoice")

    def test_sales_card_shows_our_company_as_seller_and_the_fallback_buyer(self):
        doc = _doc(direction="sales", party="@any")
        card = self.card(doc, {"doc_date": "2026-05-27", "lines": []}, {})
        rows = self.rows(card)
        self.assertEqual((rows["Transaction type"], rows["Buyer"], rows["Payment method"]), ("Sell", "Walk-in customer", "Transfer"))
        self.assertNotIn("Seller", rows)  # 销售不列卖方（就是我方公司）
        self.assertEqual(card["contents"]["header"]["backgroundColor"], "#B11B50")
        self.assertIn("No items were read", json.dumps(card))

    def test_old_card_postbacks_are_not_handled(self):
        with patch("pearnly_core.line_binding.user_of", return_value="buyer"):
            self.assertFalse(handlers.handle({"type": "postback", "source": {"userId": "U1"},
                                              "postback": {"data": "a=card&op=confirm&i=LI-1&n=n1"}}))



class ProfileBinding(Base):
    """录制问题 #8：租户没配 LINE 渠道时，个人资料页的 LINE 块只说一句，不去发码（发码会弹错误框）。"""

    def test_status_says_whether_the_channel_is_configured(self):
        from pearnly_erp_portal.integrations import binding

        self.roles["buyer"] = {"integration.read"}
        with patch.object(frappe, "session", SimpleNamespace(user="buyer")):
            for configured in (False, True):
                with patch("pearnly_core.line.is_configured", return_value=configured):
                    self.assertEqual(binding.status(), {"bound": False, "channel": configured,
                                                        "display_name": None, "bound_at": None})


if __name__ == "__main__":
    unittest.main(verbosity=2)
