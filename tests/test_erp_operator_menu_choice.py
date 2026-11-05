"""ERP 这一侧的代录子菜单选择（评估 docs/work/2026-11-05-cc-operator-menu-fallback-评估.md 修法 D 与第 14 节，同 Cowork 那一半）：
不连库的检查。

- 载荷：代录交单（`push.send` 带 operator）时 ``operator.form_choice`` 是这家公司这份账套这种单记住的菜单文字，``operator.pay_route``
  按已付 / 已收单据的「付款方式」认现金 / 转账；都没有时载荷与以前逐字节相同，不升载荷版本；
- 判定：单据当前那件代录写账件转了人工、回执 ``meta.operator`` 带候选（读时过共用净化）→ 卡片多一项 ``operator_form``，代录预检
  原来的回包列进 ``needed``；记过且仍在候选里的是已定、不再问；
- 记住：值须是候选之一（不是整批拒、一项都不记），记在 ``ERP Express Choice``（对象 = 账套 | 单种），代录交单前的答案同一条路；
- 写账准备不看它：``remembered`` 不含这一类，库里多一条时准备的结论与载荷逐字相同。
"""
import copy
import json
import sys
import unittest
from unittest import mock

import test_erp_write_precheck as pre  # noqa: E402  ERP 预检的替身与账套样本（同时装好 frappe 替身）

from pearnly_core.cc_desktop import documents  # noqa: E402
from pearnly_core.express_operator import contracts as operator_contracts  # noqa: E402
from pearnly_core.write_precheck import contracts as card  # noqa: E402
from pearnly_erp_portal.express import choices  # noqa: E402

base, fake, frappe = pre.base, pre.fake, pre.frappe
CASH_MENU = "ซื้อเงินสด-ทั่วไป"
TMB_MENU = "ซื้อโดยโอนผ่าน TMB 2-01792-3"
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
                             "ledger_reason": "OPERATOR_MENU_CHOICE"}

    def stopped(self, options=(CASH_MENU, TMB_MENU), **job):
        """PI-1 当前那件是代录交的、停在子菜单（CC 桌面版 1.12.15 的回执：operator 段另有别的键，净化时丢掉）。"""
        meta = {"operator": {"doc_type": " hp ", "menu_options": list(options), "draft": {"x": 1}, "shot": r"C:\x.png"},
                "stage": "manual"}
        fake.STATE.db.table(documents.JOB)["J1"] = {
            "name": "J1", "status": "manual", "request_key": "erp:operator:b1:PI-1@x", "book_key": "BK-1",
            "meta_json": json.dumps(meta, ensure_ascii=False), **job}

    def ready(self):
        """写账准备走完了（这张单别的都不用问）。"""
        return mock.patch.object(self.precheck, "collect_document", lambda doctype, name, given=None:
                                 {"result": {"payload": {"doctype": "HP"}, "book": {"key": "BK-1"}}, "items": [],
                                  "data": None})


class Payload(Case):
    def send(self, **row):
        self.docs["PI-1"] = {"name": "PI-1", "creation": "2026-11-05 09:00:00", "status": "submitted", "owner": "a@x",
                             "company": "CO-1", "ledger_state": "queued", "ledger_job": None, "ledger_reason": "", **row}
        made = []
        payload = {"payload_version": 4, "doctype": "HP", "lines": []}
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

    def test_without_a_remembered_menu_or_a_paid_route_the_payload_is_as_before(self):
        sent, before = self.send(is_paid=0, payment_status="unpaid", payment_method="cash")  # 采购付款方式默认现金：没付不算
        self.assertEqual(json.dumps(sent, sort_keys=True), json.dumps(before, sort_keys=True))
        self.assertNotIn("form_choice", sent["operator"])
        self.assertNotIn("pay_route", sent["operator"])

    def test_the_remembered_menu_and_the_payment_route_ride_on_the_operator_payload(self):
        choices.remember("CO-1", choices.OPERATOR_FORM, choices.operator_subject("BK-1", "hp"), TMB_MENU, "HP")
        sent, before = self.send(is_paid=1, payment_method="transfer")
        self.assertEqual((sent["operator"]["form_choice"], sent["operator"]["pay_route"]), (TMB_MENU, "transfer"))
        self.assertEqual(sent["payload_version"], before["payload_version"])  # 不升载荷版本
        rest = {key: value for key, value in sent["operator"].items() if key not in ("form_choice", "pay_route")}
        self.assertEqual(rest, before["operator"])
        # 别的账套、别的单种不借用这一条
        self.assertEqual(choices.operator_form("CO-1", "BK-2", "HP"), "")
        self.assertEqual(choices.operator_form("CO-1", "BK-1", "RR"), "")
        self.assertEqual(choices.operator_form("CO-2", "BK-1", "HP"), "")

    def test_a_direct_write_carries_neither(self):
        choices.remember("CO-1", choices.OPERATOR_FORM, choices.operator_subject("BK-1", "HP"), TMB_MENU, "HP")
        self.docs["PI-1"].update(ledger_state="queued", ledger_job=None, is_paid=1, payment_method="cash", status="submitted",
                                 creation="2026-11-05 09:00:00")
        made = []
        with mock.patch.object(self.push, "document", lambda doctype, name: {"ref": "PI-1@x", "number": "PI-1"}), \
                mock.patch.object(self.push, "company_of", lambda doctype, name: "CO-1"), \
                mock.patch.object(self.push, "_prepare_body", lambda doctype, company, body:
                                  ({"payload": {"payload_version": 4, "doctype": "HP"}, "book": {"key": "BK-1"}}, None, None)), \
                mock.patch.object(self.push, "operator_owned", lambda doctype, name: False), \
                mock.patch.object(self.push, "actor_of", lambda *a: "a@x"), \
                mock.patch.object(self.push.jobs, "of_source", lambda *a: []), \
                mock.patch.object(self.push.jobs, "enqueue", lambda **kw: made.append(kw) or {"job": "J9"}), \
                mock.patch.object(frappe.db, "commit", lambda: None, create=True):
            self.push.send(self.push.PURCHASE, "PI-1")
        self.assertEqual(made[0]["payload"], {"payload_version": 4, "doctype": "HP"})

    def test_pay_route_reads_the_payment_method_of_paid_documents_only(self):
        route = self.push.operator_pay_route
        cases = [({"is_paid": 1, "payment_method": "cash"}, "cash"),
                 ({"is_paid": 1, "payment_method": "transfer"}, "transfer"),
                 ({"is_paid": 0, "payment_status": "paid", "payment_method": "promptpay"}, "transfer"),
                 ({"is_paid": 1, "payment_method": "bank"}, "transfer"),
                 ({"is_paid": 1, "payment_method": "cheque"}, ""),
                 ({"is_paid": 1, "payment_method": "other"}, ""),
                 ({"is_paid": 0, "payment_status": "partial", "payment_method": "transfer"}, "")]
        for row, wanted in cases:
            self.docs["PI-1"] = {"name": "PI-1", **row}
            self.assertEqual(route(self.push.PURCHASE, "PI-1"), wanted, row)
        sales = fake.STATE.db.table(self.push.SALES)
        for row, wanted in (({"payment_status": "paid", "payment_method": "transfer"}, "transfer"),
                            ({"payment_status": "paid", "payment_method": "cash"}, "cash"),
                            ({"payment_status": "partial", "payment_method": "cash"}, ""),
                            ({"payment_status": "unpaid", "payment_method": None}, "")):
            sales["SI-1"] = {"name": "SI-1", **row}
            self.assertEqual(route(self.push.SALES, "SI-1"), wanted, row)
        self.assertEqual(route(self.push.STOCK, "STE-1"), "")

    def test_pay_route_follows_the_payment_rows_when_they_agree(self):
        """主会话 11-05：已付清的采购单，付款明细（不算冲销行）方式全一致就按明细；不一致或没有明细按单头；没付清不给。"""
        route = self.push.operator_pay_route
        payments = fake.STATE.db.table(self.push.PURCHASE_PAYMENT)

        def rows(*methods, reversal=()):
            payments.clear()
            for index, method in enumerate(methods):
                payments[f"PAY-{index}"] = {"name": f"PAY-{index}", "parent": "PI-1", "parenttype": self.push.PURCHASE,
                                            "method": method, "is_reversal": 0}
            for index, method in enumerate(reversal):
                payments[f"REV-{index}"] = {"name": f"REV-{index}", "parent": "PI-1", "parenttype": self.push.PURCHASE,
                                            "method": method, "is_reversal": 1}
            payments["OTHER"] = {"name": "OTHER", "parent": "PI-9", "parenttype": self.push.PURCHASE, "method": "cheque",
                                 "is_reversal": 0}  # 别的单的明细不算

        header = {"name": "PI-1", "is_paid": 1, "payment_method": "cheque"}  # 单头认不出：看得出是不是按明细
        self.docs["PI-1"] = dict(header)
        rows("cash", "cash")
        self.assertEqual(route(self.push.PURCHASE, "PI-1"), "cash")              # 明细都是现金
        rows("transfer", "promptpay", reversal=("cash",))
        self.assertEqual(route(self.push.PURCHASE, "PI-1"), "")                   # 明细混合：退回单头（cheque → 空）
        rows("bank", "bank", reversal=("cash",))
        self.assertEqual(route(self.push.PURCHASE, "PI-1"), "transfer")          # 明细都是转账，冲销行不算
        self.docs["PI-1"] = {**header, "payment_method": "transfer"}
        rows("cash", "transfer")
        self.assertEqual(route(self.push.PURCHASE, "PI-1"), "transfer")          # 混合：按单头
        rows()
        self.assertEqual(route(self.push.PURCHASE, "PI-1"), "transfer")          # 没有明细：按单头
        rows("", "")
        self.assertEqual(route(self.push.PURCHASE, "PI-1"), "transfer")          # 明细没写方式：按单头
        self.docs["PI-1"] = {"name": "PI-1", "is_paid": 0, "payment_status": "partial", "payment_method": "cash"}
        rows("cash")
        self.assertEqual(route(self.push.PURCHASE, "PI-1"), "")                   # 没付清：不给


class Judge(Case):
    def test_the_stop_is_read_from_the_stored_receipt_through_the_shared_filter(self):
        self.stopped(options=[CASH_MENU, "  " + TMB_MENU + "  ", "", 5, "x" * 300])
        stop = self.precheck.menu_stop(self.push.PURCHASE, "PI-1")
        self.assertEqual(stop["doc_type"], "HP")
        self.assertEqual(stop["book"], "BK-1")
        self.assertEqual(stop["codes"][:2], [CASH_MENU, TMB_MENU])
        self.assertEqual(stop["codes"][2], "x" * 60)  # 截到选择记忆的值上限，CC 按互相包含仍认得出
        self.assertEqual(len(stop["codes"]), 3)

    def test_no_stop_unless_the_current_job_is_a_held_operator_job_with_options(self):
        for job in ({"status": "queued"}, {"status": "success"}, {"request_key": "erp:purchase:PI-1@x"},
                    {"book_key": ""}, {"meta_json": "{"}, {"meta_json": json.dumps({"operator": {"doc_type": "HP"}})},
                    {"meta_json": json.dumps({"stage": "manual"})}):
            self.stopped(**job)
            self.assertEqual(self.precheck.menu_stop(self.push.PURCHASE, "PI-1"), {}, job)
        self.stopped()
        self.docs["PI-1"]["ledger_job"] = None
        self.assertEqual(self.precheck.menu_stop(self.push.PURCHASE, "PI-1"), {})

    def test_the_card_gets_one_operator_form_item_and_a_remembered_option_is_decided(self):
        self.stopped()
        with self.ready():
            view = self.precheck.document_view("P:PI-1", self.push.PURCHASE, "PI-1")
        self.assertEqual(view["state"], card.CHECK)
        item, = view["items"]
        self.assertEqual({key: item[key] for key in ("kind", "subject", "label", "value", "source", "direction")},
                         {"kind": "operator_form", "subject": "HP", "label": "HP", "value": "", "source": "",
                          "direction": "purchase"})
        self.assertEqual(item["options"], [{"code": CASH_MENU, "name": ""}, {"code": TMB_MENU, "name": ""}])
        merged = card.merge([{"doc_id": "P:PI-1", "items": view["items"]}])
        self.assertEqual(merged["counts"]["open"], 1)
        self.assertEqual(card.held_back(merged), ["P:PI-1"])
        choices.remember("CO-1", choices.OPERATOR_FORM, choices.operator_subject("BK-1", "HP"), TMB_MENU, "HP")
        with self.ready():
            item, = self.precheck.document_view("P:PI-1", self.push.PURCHASE, "PI-1")["items"]
        self.assertEqual((item["value"], item["source"]), (TMB_MENU, card.REMEMBERED))
        self.assertEqual(card.held_back(card.merge([{"doc_id": "P:PI-1", "items": [item]}])), [])
        # 记过的那一项已不在候选里（菜单又改过）：当没记过，重新问
        self.stopped(options=[CASH_MENU, "ซื้อโดยโอนผ่าน KBANK"])
        with self.ready():
            item, = self.precheck.document_view("P:PI-1", self.push.PURCHASE, "PI-1")["items"]
        self.assertEqual(item["value"], "")

    def test_a_document_that_did_not_stop_on_a_submenu_is_unchanged(self):
        with self.ready():
            self.assertEqual(self.precheck.document_view("P:PI-1", self.push.PURCHASE, "PI-1")["items"], [])

    def test_operator_precheck_lists_it_in_needed_until_it_is_remembered(self):
        from pearnly_erp_portal.express import operator

        self.stopped()
        with self.ready():
            row, = operator.precheck("CO-1", ["P:PI-1"])["documents"]
        self.assertEqual((row["state"], row["reason"]), ("choose", "choice_needed:operator_form"))
        need, = row["needed"]
        self.assertEqual((need["kind"], need["subject"], need["current"]), ("operator_form", "HP", ""))
        self.assertEqual([option["code"] for option in need["options"]], [CASH_MENU, TMB_MENU])
        self.assertEqual(row["precheck"]["items"][0]["kind"], "operator_form")
        choices.remember("CO-1", choices.OPERATOR_FORM, choices.operator_subject("BK-1", "HP"), CASH_MENU, "HP")
        with self.ready():
            row, = operator.precheck("CO-1", ["P:PI-1"])["documents"]
        self.assertEqual(row["state"], "ready")
        self.assertNotIn("needed", row)

    def test_with_menu_adds_to_a_choose_row_and_leaves_other_rows(self):
        from pearnly_erp_portal.express import operator

        menu = {"kind": "operator_form", "subject": "HP", "label": "HP", "value": "", "options": [{"code": CASH_MENU, "name": ""}]}
        choose = {"doc_id": "P:PI-1", "state": "choose", "reason": "choice_needed:account", "needed": [{"kind": "account"}]}
        out = operator.with_menu(choose, menu)
        self.assertEqual(out["reason"], "choice_needed:account,operator_form")
        self.assertEqual([need["kind"] for need in out["needed"]], ["account", "operator_form"])
        for row in ({"doc_id": "P:PI-1", "state": "blocked", "reason": "x"}, {"doc_id": "P:PI-1", "state": "waiting"}):
            self.assertEqual(operator.with_menu(row, menu), row)
        self.assertEqual(operator.with_menu({"state": "ready"}, {**menu, "value": CASH_MENU}), {"state": "ready"})
        self.assertEqual(operator.with_menu({"state": "ready"}, None), {"state": "ready"})


class Remember(Case):
    def answer(self, **extra):
        return {"kind": "operator_form", "subject": "HP", "value": TMB_MENU, "label": "HP", **extra}

    def test_the_answer_is_kept_for_the_company_book_and_doc_type(self):
        self.stopped()
        self.assertEqual(self.precheck.remember("CO-1", {"P:PI-1": [self.answer()]}), {"saved": 1})
        self.assertEqual(choices.operator_form("CO-1", "BK-1", "HP"), TMB_MENU)
        row, = fake.STATE.db.table(choices.CHOICE).values()
        self.assertEqual((row["company"], row["kind"], row["subject"], row["value"]),
                         ("CO-1", "operator_form", "BK-1|HP", TMB_MENU))
        # 同一项再答一次就改这一行
        self.precheck.remember("CO-1", {"P:PI-1": [self.answer(value=CASH_MENU)]})
        self.assertEqual(len(fake.STATE.db.table(choices.CHOICE)), 1)
        self.assertEqual(choices.operator_form("CO-1", "BK-1", "HP"), CASH_MENU)

    def test_a_value_that_is_not_an_option_or_a_wrong_doc_type_is_refused(self):
        self.stopped()
        for bad in (self.answer(value="ซื้อเงินเชื่อ"), self.answer(subject="RR"), self.answer(value="")):
            with self.assertRaises(frappe.ValidationError):
                self.precheck.remember("CO-1", {"P:PI-1": [bad]})
        self.assertEqual(fake.STATE.db.table(choices.CHOICE), {})
        # 这张单没停在子菜单：同样拒
        self.docs["PI-1"]["ledger_job"] = None
        with self.assertRaises(frappe.ValidationError):
            self.precheck.remember("CO-1", {"P:PI-1": [self.answer()]})
        with self.assertRaises(frappe.PermissionError):  # 别家公司的单
            self.precheck.remember("CO-2", {"P:PI-1": [self.answer()]})

    def test_one_bad_answer_keeps_nothing_and_remember_false_is_not_kept(self):
        self.stopped()
        rest = {"kind": "account", "subject": "x", "value": "1"}
        with mock.patch.object(self.precheck, "save", side_effect=AssertionError("must not save")):
            with self.assertRaises(frappe.ValidationError):
                self.precheck.save_answers("CO-1", self.push.PURCHASE, "PI-1", [self.answer(value="nope"), rest])
        self.assertEqual(fake.STATE.db.table(choices.CHOICE), {})
        self.assertEqual(self.precheck.save_answers("CO-1", self.push.PURCHASE, "PI-1", [self.answer(remember=False)]), 0)
        self.assertEqual(fake.STATE.db.table(choices.CHOICE), {})

    def test_answers_given_at_operator_submit_go_the_same_way(self):
        from pearnly_erp_portal.express import operator

        self.stopped()
        sent = []
        with mock.patch.object(self.push, "release_operator", lambda doctype, name: True), \
                mock.patch.object(self.push, "send", lambda doctype, name, operator=None: sent.append(name) or {"status": "sent"}), \
                mock.patch.object(frappe.db, "commit", lambda: None, create=True):
            out = operator.submit("CO-1", ["P:PI-1"], dict(BATCH), "PC-1", {"P:PI-1": [self.answer()]})
        self.assertEqual(out, [{"doc_id": "P:PI-1", "status": "sent", "reason": ""}])
        self.assertEqual(choices.operator_form("CO-1", "BK-1", "HP"), TMB_MENU)


class WriteRulesUnchanged(pre.Precheck):
    def test_the_write_preparation_and_its_payload_do_not_see_a_remembered_menu(self):
        key, = self.computer([base.book()])
        self.masters(key)
        for body in (base.doc(), base.doc(items=pre.SERVICE)):
            before_memory = choices.remembered("CO-1")
            before, before_items = self.write_prepare(body), self.collect(body)["items"]
            choices.remember("CO-1", choices.OPERATOR_FORM, choices.operator_subject(key, "RR"), CASH_MENU, "RR")
            choices.remember("CO-1", choices.OPERATOR_FORM, choices.operator_subject(key, "HP"), TMB_MENU, "HP")
            self.assertEqual(choices.remembered("CO-1"), before_memory)
            self.assertEqual(json.dumps(self.write_prepare(body), sort_keys=True, default=str),
                             json.dumps(before, sort_keys=True, default=str))
            self.assertEqual(self.collect(body)["items"], before_items)
            fake.STATE.db.table(choices.CHOICE).clear()


if __name__ == "__main__":
    unittest.main()
