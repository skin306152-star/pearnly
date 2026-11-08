"""ERP CC 对话收单（盘点 2026-11-08 P0-2，`integrations.intake` 的 receive_chat / run_chat / chat_records / submit_chat）。

在锁定的 Frappe 运行时里跑（候选离线车道）；不连库、不识别、不发 LINE：协作者全换成替身。
- 收件：要选采购 / 销售、要开单权、公司只认本人可用的；同一个人同一个请求键回原来那条（不重复识别、不重复收费），上次失败的才重来；
  记录来源 cc、按票面对方、默认已付现金（同网页默认）；交共用识别队列（``erp.cc_intake``），队列满落失败。
- 识别：只认对话来的、还在排队的；失败落定失败带原因；成功几张票各一条待提交记录、每行预选物料，计费请求号按记录固定；不回 LINE。
- 提交：只认本人对话来的待提交记录，用网页同一个入账函数（请求号按记录固定，同一张只入一次），来源记 cc；已提交的回原单。
"""
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import frappe

from pearnly_erp_portal.integrations import intake


def _same(text, *args, **kwargs):
    return text


class Doc(SimpleNamespace):
    def get(self, key, default=None):
        return getattr(self, key, default)

    def insert(self, **kwargs):
        self.name = self.name if getattr(self, "name", None) else "LI-00001"
        return self

    def reload(self):
        return self

    def save(self, **kwargs):
        return self


class Base(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(frappe, "_", _same, create=True))
        self.enterContext(patch.object(intake, "_", _same))
        self.enterContext(patch.object(frappe, "session", SimpleNamespace(user="buyer@x"), create=True))
        self.enterContext(patch.object(frappe, "local", SimpleNamespace(lang="th"), create=True))
        self.db = Mock(**{"get_value.return_value": None})
        self.enterContext(patch.object(frappe, "db", self.db, create=True))
        self.enterContext(patch.object(frappe, "throw", side_effect=self.reject))
        self.enterContext(patch.object(frappe, "set_user", lambda user: setattr(frappe.session, "user", user)))
        self.require = self.enterContext(patch("pearnly_erp_portal.access.guard.require"))
        self.capabilities = {"purchase.write", "sales.write"}
        self.enterContext(patch("pearnly_erp_portal.access.guard.has", side_effect=lambda capability, user=None: capability in self.capabilities))
        self.enterContext(patch("pearnly_erp_portal.masters.company.document_company", side_effect=lambda company, user=None: company or "CO-1"))
        self.enterContext(patch("pearnly_core.line.reply_or_push", side_effect=AssertionError("no LINE from the chat")))
        self.enterContext(patch("requests.sessions.Session.request", side_effect=AssertionError("External network forbidden")))
        self.drops = []
        self.enterContext(patch.object(intake, "_drop", side_effect=lambda doc, status, reason="": self.drops.append((doc.name, status, reason))))

    @staticmethod
    def reject(message, exception=frappe.ValidationError, **kwargs):
        raise exception(message)


class Receive(Base):
    def setUp(self):
        super().setUp()
        self.inserted = []

        def get_doc(values):
            doc = Doc(**values)
            self.inserted.append(doc)
            return doc

        self.enterContext(patch.object(frappe, "get_doc", side_effect=get_doc))
        self.enterContext(patch.object(intake, "_store", return_value=SimpleNamespace(file_url="/private/files/a.pdf", name="F-1")))
        self.submit = self.enterContext(patch("pearnly_core.execution.work.submit"))
        self.enterContext(patch.object(intake, "now", return_value="2026-11-08 15:00:00"))

    def receive(self, **overrides):
        values = dict(company="", direction="purchase", filename="bill.pdf", content=b"%PDF", request_key="assistant:file_1",
                      session_id="sesn_1")
        values.update(overrides)
        return intake.receive_chat(**values)

    def test_a_new_attachment_is_queued_as_a_chat_record(self):
        self.assertEqual(self.receive(), {"name": "LI-00001", "status": intake.QUEUED})
        doc = self.inserted[0]
        self.assertEqual((doc.source, doc.session_id, doc.request_key, doc.sent_by, doc.company),
                         ("cc", "sesn_1", "assistant:file_1", "buyer@x", "CO-1"))
        self.assertEqual((doc.party, doc.is_paid, doc.payment_method, doc.media_type), ("@doc", 1, "cash", "pdf"))
        self.require.assert_called_with("purchase.write")
        self.submit.assert_called_once_with("erp.cc_intake", "LI-00001", {"intake": "LI-00001"}, "buyer@x")

    def test_the_same_attachment_is_not_recognised_or_charged_twice(self):
        self.db.get_value.return_value = SimpleNamespace(name="LI-00007", status=intake.READY)
        self.assertEqual(self.receive(), {"name": "LI-00007", "status": intake.READY, "reused": True})
        self.assertEqual(self.inserted, [])
        self.submit.assert_not_called()
        self.db.get_value.return_value = SimpleNamespace(name="LI-00007", status=intake.FAILED)
        self.assertEqual(self.receive()["status"], intake.QUEUED)  # 上次失败的才重来

    def test_no_direction_reads_as_purchase_first_and_a_full_queue_fails_the_record(self):
        self.receive(direction="")
        self.assertEqual((self.inserted[-1].direction, self.inserted[-1].direction_guessed), ("purchase", 1))
        self.capabilities = {"sales.write"}
        self.receive(direction="", request_key="assistant:file_2")
        self.assertEqual((self.inserted[-1].direction, self.inserted[-1].direction_guessed), ("sales", 0))  # 只能开销售单就不猜
        self.capabilities = set()
        with self.assertRaises(frappe.PermissionError):
            self.receive(direction="", request_key="assistant:file_3")
        self.capabilities = {"purchase.write", "sales.write"}
        with self.assertRaises(frappe.ValidationError):
            self.receive(direction="refund")
        from pearnly_core.execution.errors import QueueFull

        self.submit.side_effect = QueueFull()
        self.assertEqual(self.receive()["status"], intake.FAILED)
        self.assertEqual(self.drops[-1][1:], (intake.FAILED, "queue_busy"))


class Run(Base):
    def setUp(self):
        super().setUp()
        self.doc = Doc(name="LI-00001", status=intake.QUEUED, source="cc", direction="purchase", sent_by="buyer@x",
                       company="CO-1", language="th", source_file_name="F-1", result=None)
        self.enterContext(patch.object(frappe, "get_doc", return_value=self.doc))
        self.recogniser = Mock()
        self.enterContext(patch.object(intake, "_recogniser", return_value=self.recogniser))
        self.prepared = []
        self.enterContext(patch("pearnly_erp_portal.integrations.line_items.prepare",
                                side_effect=lambda doc, data, line_user_id=None: self.prepared.append((doc.name, line_user_id))))
        self.enterContext(patch.object(intake, "_sibling", side_effect=lambda doc, data: Doc(name="LI-00002")))

    def test_each_document_in_the_file_becomes_a_pending_record(self):
        self.recogniser.recognise.return_value = {"doc_no": "IV1", "additional": [{"doc_no": "IV2"}]}
        self.assertEqual(intake.run_chat("LI-00001"), {"records": ["LI-00001", "LI-00002"]})
        self.assertEqual(self.doc.status, intake.READY)
        self.assertEqual(json.loads(self.doc.result), {"doc_no": "IV1"})
        kwargs = self.recogniser.recognise.call_args.kwargs
        self.assertEqual((kwargs["billing_reference"], kwargs["company"], kwargs["prefill"]),
                         ("erp.cc_intake:LI-00001", "CO-1", False))
        self.assertEqual(self.prepared, [("LI-00001", None), ("LI-00002", None)])
        self.assertEqual(frappe.session.user, "buyer@x")

    def test_a_guessed_purchase_issued_by_this_company_is_read_again_as_sales(self):
        self.doc.direction_guessed = 1
        purchase, sales = Mock(), Mock()
        purchase.recognise.side_effect = frappe.ValidationError(intake.ISSUED_HERE)
        sales.recognise.return_value = {"doc_no": "SI1"}
        self.enterContext(patch.object(intake, "_recogniser", side_effect=lambda direction: sales if direction == "sales" else purchase))
        self.enterContext(patch.object(frappe, "clear_messages", create=True))
        self.assertEqual(intake.run_chat("LI-00001"), {"records": ["LI-00001"]})
        self.assertEqual(self.doc.direction, "sales")
        self.db.set_value.assert_any_call(intake.DOCTYPE, "LI-00001", {"direction": "sales", "direction_guessed": 0})
        self.assertEqual(sales.recognise.call_args.kwargs["billing_reference"], "erp.cc_intake:LI-00001")
        self.doc.direction, self.doc.direction_guessed, self.doc.status = "purchase", 0, intake.QUEUED
        self.assertEqual(intake.run_chat("LI-00001"), {"failed": "recognition_failed"})  # 方向是人说的：不改读

    def test_failures_keep_the_reason_and_only_chat_records_run(self):
        self.recogniser.recognise.side_effect = frappe.ValidationError("This is not a purchase or sales document")
        self.assertEqual(intake.run_chat("LI-00001"), {"failed": "recognition_failed"})
        self.assertEqual(self.drops[-1], ("LI-00001", intake.FAILED, "This is not a purchase or sales document"))
        self.doc.source = "line"
        self.doc.status = intake.QUEUED
        self.assertEqual(intake.run_chat("LI-00001"), {"cancelled": "not_queued"})


class Billing(unittest.TestCase):
    """主会话 11-08（A 批 1 合入前）：CC 对话 / 代录放文件的识别与网页同一个识别函数、同一套按页计费；一条收单记录只扣一次。
    同一份上传（同一个请求键：重试、刷新、重复提交）不重读不重扣（``Receive.test_the_same_attachment_is_not_recognised_or_charged_twice``）；
    另放一次同内容的文件是一次新上传，照常识别、照常扣费（用户 10-02 定，同网页与 Cowork）。"""

    def test_the_web_recognition_and_its_page_billing_are_used(self):
        from pearnly_erp_portal.purchasing import recognition as purchase
        from pearnly_erp_portal.sales import recognition as sales

        self.assertIs(intake._recogniser("purchase"), purchase)  # 网页采购页「识别」调的就是它
        self.assertIs(intake._recogniser("sales"), sales)
        for module in (purchase, sales):
            text = Path(module.__file__).read_text(encoding="utf-8")
            self.assertIn("units = billing.pages(images)", text)  # 按物理页计
            # 识别成功才报账：不是单据的在扣费前拒收
            self.assertLess(text.index("This is not a purchase or sales document"), text.index("billing.charge("))
        text = Path(purchase.__file__).read_text(encoding="utf-8")
        # 方向猜成采购、票是本公司开出的：采购那遍在扣费前拒收，改按销售读的那遍才扣（租户只付一次）
        self.assertLess(text.index("issued by this company, not a purchase document"), text.index("billing.charge("))
        source = Path(intake.__file__).read_text(encoding="utf-8")
        self.assertEqual(source.count('billing_reference=CC_OCR_KIND + ":" + doc.name'), 2)  # 两遍同一个报账号

    def test_pages_count_like_the_web(self):
        from pearnly_erp_portal.recognition import billing, inputs

        with patch.object(inputs, "pdf_page_count", return_value=3):
            self.assertEqual(billing.pages([(b"%PDF", "application/pdf"), (b"img", "image/jpeg")]), 4)

    def test_the_same_record_is_charged_once(self):
        from pearnly_core import billing_client
        from pearnly_erp_portal.recognition import billing

        with patch.object(billing_client, "charge", return_value={"ok": True}) as charge:
            billing.charge(2, "erp.cc_intake:LI-1", "ERP purchase recognition")
        # 报账号按记录固定：重试同一个号，控制面同一个 reference 只扣一次（`recognition.billing` 文件头）
        self.assertEqual(charge.call_args.kwargs["reference"], "erp.cc_intake:LI-1")
        self.assertEqual(charge.call_args.args[:3], ("erp", "pdf", 2))


class Submit(Base):
    def setUp(self):
        super().setUp()
        self.doc = Doc(name="LI-00001", status=intake.READY, source="cc", direction="purchase", sent_by="buyer@x",
                       purchase=None, sales_document=None)
        self.enterContext(patch.object(intake, "_owned", return_value=self.doc))
        self.enterContext(patch.object(intake, "build_payload", return_value={"company": "CO-1", "source": "line"}))
        self.post = self.enterContext(patch("pearnly_erp_portal.purchasing.entry.post", return_value={"name": "PO-1"}))

    def test_submits_through_the_web_entry_function_once(self):
        self.assertEqual(intake.submit_chat("LI-00001"), {"name": "PO-1"})
        payload = self.post.call_args.args[0]
        self.assertEqual(payload["source"], "cc")
        self.assertEqual(self.post.call_args.kwargs["request_id"], "cc-intake:LI-00001")
        self.assertTrue(self.post.call_args.kwargs["allow_duplicate"])
        self.doc.status, self.doc.purchase = intake.CONFIRMED, "PO-1"
        self.assertEqual(intake.submit_chat("LI-00001"), {"name": "PO-1", "duplicate": True})
        self.assertEqual(self.post.call_count, 1)

    def test_only_chat_records_that_are_ready(self):
        self.doc.source = "line"
        with self.assertRaises(frappe.PermissionError):
            intake.submit_chat("LI-00001")
        self.doc.source, self.doc.status = "cc", intake.FAILED
        with self.assertRaises(frappe.DoesNotExistError):
            intake.submit_chat("LI-00001")


if __name__ == "__main__":
    unittest.main()
