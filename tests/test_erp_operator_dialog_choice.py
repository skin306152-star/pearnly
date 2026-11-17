"""ERP 这一侧的代录中途配方外业务选择框（CC 桌面版 1.12.23 ③④，主会话 11-16 并入 A5）：不连库的检查，照子菜单那套
（`test_erp_operator_menu_choice.py`）。

- 判定：当前那件代录写账件转了人工、回执 ``meta.operator.dialog`` 带框（读时过共用净化）→ 卡片多一项 ``operator_dialog``
  （问框的原文，选项是框上的按钮）；这家公司答过同一个框（指纹）、那个钮仍在的是已定；
- 记住：值须是框上的按钮、指纹对得上（批里别的单的跳过、不拒），按公司 + 指纹记；同时给留着框的那台电脑发一条「点这个钮」；
- 载荷：交代录时 ``operator.dialog_answers`` 带这家公司答过的；没答过时载荷与以前逐字节相同。
"""
import copy
import json
import unittest
from unittest import mock

import test_erp_write_precheck as pre  # noqa: E402  ERP 预检的替身与账套样本（同时装好 frappe 替身）

from pearnly_core.cc_desktop import documents  # noqa: E402
from pearnly_core.express_operator import contracts as operator_contracts  # noqa: E402
from pearnly_core.write_precheck import contracts as card  # noqa: E402
from pearnly_erp_portal.express import choices  # noqa: E402

base, fake, frappe = pre.base, pre.fake, pre.frappe
DIGEST = "0123456789abcdef"
BOX = {"digest": DIGEST, "title": "ExpressI", "text": "เลือกวิธีบันทึกรายการนี้", "buttons": ["แบบ ก", "แบบ ข"],
       "doc_type": "RR", "stage": "saving"}
BATCH = {"id": "b1", "index": 1, "total": 1}


class Case(pre.Precheck):
    def setUp(self):
        super().setUp()
        for patch in (mock.patch.object(self.precheck.guard, "has", lambda capability, user=None: True),
                      mock.patch.object(self.precheck.guard, "scope_filters", lambda user=None: {}),
                      mock.patch.object(self.precheck.companies, "allowed_companies", lambda user=None: ["CO-1"])):
            patch.start()
            self.addCleanup(patch.stop)
        self.docs = fake.STATE.db.table(self.push.PURCHASE)
        self.docs["PI-1"] = {"name": "PI-1", "company": "CO-1", "owner": "staff@x", "system_no": "PE-1",
                             "supplier_name": "S", "status": "submitted", "ledger_state": "held", "ledger_job": "J1",
                             "ledger_reason": "OPERATOR_DIALOG_CHOICE"}
        self.sent = []
        from pearnly_core.cc_desktop import operator_controls

        patch = mock.patch.object(operator_controls, "send", lambda *args: self.sent.append(args) or True)
        patch.start()
        self.addCleanup(patch.stop)

    def stopped(self, box=None, **job):
        meta = {"operator": {"batch": "b1", "doc": "P:PI-1", "reason": "dialog_choice", "dialog": dict(box or BOX)},
                "stage": "manual"}
        fake.STATE.db.table(documents.JOB)["J1"] = {
            "name": "J1", "status": "manual", "request_key": "erp:operator:b1:PI-1@x", "book_key": "BK-1",
            "written_by_device": "PC-1", "meta_json": json.dumps(meta, ensure_ascii=False), **job}

    def ready(self):
        return mock.patch.object(self.precheck, "collect_document", lambda doctype, name, given=None:
                                 {"result": {"payload": {"doctype": "RR"}, "book": {"key": "BK-1"}}, "items": [],
                                  "data": None})

    def answer(self, **extra):
        return {"kind": "operator_dialog", "subject": DIGEST, "value": "แบบ ข", "label": "x", **extra}


class Judge(Case):
    def test_the_stop_is_read_from_the_stored_receipt(self):
        self.stopped()
        stop = self.precheck.dialog_stop(self.push.PURCHASE, "PI-1")
        self.assertEqual((stop["digest"], stop["buttons"], stop["device"], stop["batch"], stop["doc"]),
                         (DIGEST, ["แบบ ก", "แบบ ข"], "PC-1", "b1", "P:PI-1"))
        for job in ({"status": "queued"}, {"request_key": "erp:purchase:PI-1@x"}, {"meta_json": "{"}):
            self.stopped(**job)
            self.assertEqual(self.precheck.dialog_stop(self.push.PURCHASE, "PI-1"), {}, job)
        self.stopped(box={**BOX, "digest": "nothex"})
        self.assertEqual(self.precheck.dialog_stop(self.push.PURCHASE, "PI-1"), {})

    def test_the_card_asks_the_box_and_a_remembered_button_is_decided(self):
        self.stopped()
        with self.ready():
            view = self.precheck.document_view("P:PI-1", self.push.PURCHASE, "PI-1")
        self.assertEqual(view["state"], card.CHECK)
        item, = view["items"]
        self.assertEqual({key: item[key] for key in ("kind", "subject", "value", "direction")},
                         {"kind": "operator_dialog", "subject": DIGEST, "value": "", "direction": "purchase"})
        self.assertIn("เลือกวิธีบันทึกรายการนี้", item["label"])
        self.assertEqual(item["options"], [{"code": "แบบ ก", "name": "แบบ ก"}, {"code": "แบบ ข", "name": "แบบ ข"}])
        choices.remember("CO-1", choices.OPERATOR_DIALOG, DIGEST, "แบบ ข", "x")
        with self.ready():
            item, = self.precheck.document_view("P:PI-1", self.push.PURCHASE, "PI-1")["items"]
        self.assertEqual((item["value"], item["source"]), ("แบบ ข", card.REMEMBERED))


class Remember(Case):
    def test_the_answer_is_kept_per_company_and_fingerprint_and_the_holding_computer_is_told(self):
        self.stopped()
        self.assertEqual(self.precheck.remember("CO-1", {"P:PI-1": [self.answer()]}), {"saved": 1})
        self.assertEqual(choices.operator_dialogs("CO-1"), {DIGEST: "แบบ ข"})
        self.assertEqual(self.sent, [("PC-1", "b1", "P:PI-1", "answer", DIGEST, "แบบ ข")])
        self.assertEqual(choices.operator_dialogs("CO-2"), {})
        self.assertNotIn(choices.OPERATOR_DIALOG, choices.remembered("CO-1"))  # 不是写账规则

    def test_a_button_not_on_the_box_is_refused_and_another_fingerprint_is_skipped(self):
        self.stopped()
        with self.assertRaises(frappe.ValidationError):
            self.precheck.remember("CO-1", {"P:PI-1": [self.answer(value="แบบ ค")]})
        self.assertEqual(self.precheck.remember("CO-1", {"P:PI-1": [self.answer(subject="f" * 16)]}), {"saved": 0})
        self.assertEqual((fake.STATE.db.table(choices.CHOICE), self.sent), ({}, []))
        self.docs["PI-1"]["ledger_job"] = None  # 这张单没停在框上：带来的答案跳过、不拒、不记
        self.assertEqual(self.precheck.remember("CO-1", {"P:PI-1": [self.answer()]}), {"saved": 0})


class Payload(Case):
    def send(self):
        self.docs["PI-1"] = {"name": "PI-1", "creation": "2026-11-17 09:00:00", "status": "submitted", "owner": "a@x",
                             "company": "CO-1", "ledger_state": "queued", "ledger_job": None, "ledger_reason": "",
                             "is_paid": 0, "payment_status": "unpaid"}
        made = []
        payload = {"payload_version": 4, "doctype": "RR", "lines": []}
        with mock.patch.object(self.push, "document", lambda doctype, name: {"ref": "PI-1@x", "number": "PI-1"}), \
                mock.patch.object(self.push, "company_of", lambda doctype, name: "CO-1"), \
                mock.patch.object(self.push, "_prepare_body", lambda doctype, company, body:
                                  ({"payload": copy.deepcopy(payload), "book": {"key": "BK-1"}}, None, None)), \
                mock.patch.object(self.push, "actor_of", lambda *a: "a@x"), \
                mock.patch.object(self.push.jobs, "of_source", lambda *a: []), \
                mock.patch.object(self.push.jobs, "enqueue", lambda **kw: made.append(kw) or {"job": "J9"}), \
                mock.patch.object(frappe.db, "commit", lambda: None, create=True):
            self.assertEqual(self.push.send(self.push.PURCHASE, "PI-1", operator=dict(BATCH)), {"status": "sent"})
        return made[0]["payload"], operator_contracts.for_operator(payload, BATCH)

    def test_without_answers_the_payload_is_as_before_and_with_them_they_ride_along(self):
        sent, before = self.send()
        self.assertEqual(json.dumps(sent, sort_keys=True), json.dumps(before, sort_keys=True))
        choices.remember("CO-1", choices.OPERATOR_DIALOG, DIGEST, "แบบ ข", "x")
        sent, before = self.send()
        self.assertEqual(sent["operator"]["dialog_answers"], {DIGEST: "แบบ ข"})
        self.assertEqual({k: v for k, v in sent["operator"].items() if k != "dialog_answers"}, before["operator"])


if __name__ == "__main__":
    unittest.main()
