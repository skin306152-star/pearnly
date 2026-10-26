"""收银退货单 SR（ERP B3b，载荷 10）的共用写账规则：纯规则离线检查（不连库、不依赖 Frappe；python3 直接跑）。

覆盖：``refund`` 块的写法与形状核对、退货的付款方式（现金也不能为负）、映射成 Express 原生退货单（实录 SR0000001：
借 退货科目 93.46 + 借 销项税 6.54 / 贷 现金 100，CNTYP 3、货回仓）、退货科目按公司问一次（推送选择 ``refund``）、
非库存行（服务、费用）只记金额与总账、带科目的借它自己的科目（10-26），明细不可信 / 库存行没码 / 没选退货科目 / 没有 POS 块时停下，
以及**没有 refund 块的载荷与改动前逐字节相同**（基线 main d8d42e6f）。
"""
import json
import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps/pearnly_core")]

from pearnly_core.express_rules.contracts import pos as P  # noqa: E402
from pearnly_core.express_rules.push import choices as choice_rules  # noqa: E402
from pearnly_core.express_rules.push.sales_mapper import build_express_sales_payload  # noqa: E402

CONFIG = {"account_set": "DATAT", "revenue_acc": "41-01-00-00", "vat_output_acc": "11-05-04-02", "ar_acc": "11-01-01-00"}
REFUND_ACC, QR_ACC, CARD_ACC = "41-02-00-00", "11-02-01-01", "11-02-01-02"
TODAY = date(2026, 10, 26)


def _page(*, total="100.00", vat="6.54", subtotal="93.46", items=None, payments=None, refund=True, pos=True,
          against="HS0000039", against_total="200.00"):
    """ERP 收银日汇总 / 退货日汇总交给写账规则的主页面（同 `pearnly_erp_portal.express.contracts.pages` 的形状）。"""
    items = items if items is not None else [
        {"name": "น้ำดื่ม 600 มล.", "qty": "1", "price": "93.46", "subtotal": "93.46", "unit": "ขวด", "code": "500310",
         "loccod": "01", "posting_kind": "stock"}]
    fields = {"direction": "sales", "date": "2026-10-25", "invoice_number": "SI2569-000007",
              "document_type": "tax_invoice", "currency": "THB", "subtotal": subtotal, "vat": vat,
              "total_amount": total, "payment_status": "paid", "posting_item_type_manual": "goods",
              "buyer_name": "เงินสด", "buyer_tax": "", "buyer_branch": "", "seller_tax": "0105551234567",
              "seller_branch": "00000", "party_code": "", "items": items}
    page = {"page_index": 1, "fields": fields}
    if pos:
        paid = payments if payments is not None else [("เงินสด", Decimal(total))]
        page["_extraction_audit"] = P.audit("SI2569-000007", "SI2569-000007", 1, paid)
        if refund:
            page["_extraction_audit"].update(P.refund_audit(against, against_total))
    elif refund:
        page["_extraction_audit"] = P.refund_audit(against, against_total)
    return page


def _history(page):
    fields = page["fields"]
    return {"id": "PDS-2026-0001@ERP", "source": "erp", "direction": "sales", "invoice_date": fields["date"],
            "invoice_no": fields["invoice_number"], "total_amount": fields["total_amount"], "fields": dict(fields),
            "pages": [page]}


def _map(page, choices=None):
    config = {**CONFIG, "choices": {"refund": REFUND_ACC} if choices is None else choices}
    return build_express_sales_payload(_history(page), config=config, today=TODAY)


class RefundBlockTests(unittest.TestCase):
    def test_the_block_round_trips_and_its_shape_is_checked(self):
        page = _page()
        self.assertEqual(page["_extraction_audit"]["refund"], {"against": "HS0000039", "against_total": "200.00"})
        self.assertEqual(P.refund_facts(page), P.Refund("HS0000039", Decimal("200.00")))
        self.assertIsNone(P.refund_facts(_page(refund=False)))
        self.assertEqual(P.refund_audit(" HS691025-001 ", "1070"), {"refund": {"against": "HS691025-001",
                                                                              "against_total": "1070.00"}})
        for bad in ({"against": "", "against_total": "1.00"}, {"against": "HS691025-0001", "against_total": "1.00"},
                    {"against": "HS1", "against_total": "0.00"}, {"against": "HS1", "against_total": "1.005"},
                    {"against": "HS1", "against_total": "x"}, {"against": 7, "against_total": "1.00"}, "HS1"):
            broken = _page()
            broken["_extraction_audit"]["refund"] = bad
            with self.assertRaises(P.PosFactsError):
                P.refund_facts(broken)

    def test_refund_payments_add_up_and_cash_is_never_negative(self):
        facts = P.facts(_page(payments=[("เงินสด", Decimal("40.00")), ("พร้อมเพย์", Decimal("60.00"))]))
        self.assertEqual(P.check_refund(facts, "100.00"), "")
        self.assertEqual(P.check_refund(facts, "99.00"), P.MISMATCH)
        negative = P.facts(_page(payments=[("เงินสด", Decimal("-20.00")), ("พร้อมเพย์", Decimal("120.00"))]))
        self.assertEqual(P.check(negative, "100.00"), "")  # 日汇总允许现金净额为负
        self.assertEqual(P.check_refund(negative, "100.00"), P.MISMATCH)  # 退货不允许


class RefundMappingTests(unittest.TestCase):
    def test_a_cash_refund_maps_to_the_native_return_document(self):
        result = _map(_page())
        self.assertTrue(result.ok, result.reason)
        payload = result.payload
        self.assertEqual((payload["doctype"], payload["payload_version"]), ("SR", P.REFUND_PAYLOAD_VERSION))
        self.assertEqual(payload["refund"], {"against": "HS0000039", "against_total": "200.00", "cntyp": 3,
                                             "retstk": "Y"})
        self.assertEqual(payload["lines"], [
            {"acc": REFUND_ACC, "side": "D", "amount": "93.46", "desc": "ลดหนี้/รับคืน", "role": "refund"},
            {"acc": "11-05-04-02", "side": "D", "amount": "6.54", "desc": "ภาษีขาย", "role": "output_vat"},
            {"acc": "11-01-01-00", "side": "C", "amount": "100.00", "desc": "เงินสด", "role": "cash"},
        ])
        self.assertEqual((payload["base_amount"], payload["vat_amount"], payload["total_amount"]),
                         ("93.46", "6.54", "100.00"))
        self.assertEqual(payload["customer"]["code"], "เงินสด")
        self.assertEqual(payload["items_status"], "ok")
        self.assertEqual(payload["items"], [{"name": "น้ำดื่ม 600 มล.", "qty": "1.00", "unit": "ขวด", "unit_price": "93.46",
                                             "amount": "93.46", "item_mode": "stock_sale", "code": "500310",
                                             "loccod": "01"}])
        self.assertEqual(payload["pos"], {"first": "SI2569-000007", "last": "SI2569-000007", "count": 1,
                                          "docnum": "auto", "cash": "100.00", "transfers": []})

    def test_a_non_cash_refund_keeps_the_receiving_account_of_the_sale(self):
        subject = P.method_subject("พร้อมเพย์")
        page = _page(payments=[("เงินสด", Decimal("40.00")), ("พร้อมเพย์", Decimal("60.00"))])
        result = _map(page, choices={"refund": REFUND_ACC, "receipts": {subject: QR_ACC}})
        self.assertTrue(result.ok, result.reason)
        self.assertEqual(result.payload["pos"]["transfers"], [{"acc": QR_ACC, "amount": "60.00", "label": "พร้อมเพย์"}])
        self.assertEqual(result.payload["pos"]["cash"], "40.00")
        self.assertEqual(_map(page).reason, P.ACCOUNT_MISSING)  # 收款科目没记过：同 HS 先问

    def test_a_refund_without_vat_has_no_output_vat_line(self):
        page = _page(total="93.46", vat="0.00", subtotal="93.46")
        payload = _map(page).payload
        self.assertEqual([line["role"] for line in payload["lines"]], ["refund", "cash"])
        self.assertEqual(payload["vat_rate"], 0.0)

    def test_a_cross_year_refund_carries_its_flag_to_cc_desktop(self):
        """B3b 实录点 ②：原 HS 在上一年度的账套时退货块标 ``cross_year``，CC 桌面版照 Express 原生挂「AVGPR」、按平均成本回冲。"""
        page = _page()
        page["_extraction_audit"].update(P.refund_audit("HS0000039", "200.00", True))
        payload = _map(page).payload
        self.assertEqual((payload["refund"]["cross_year"], payload["payload_version"]), (True, 11))  # 旧版 CC 诚实拒收
        self.assertNotIn("cross_year", _map(_page()).payload["refund"])
        self.assertEqual(P.refund_facts(page).cross_year, True)

    def test_an_exempt_line_keeps_its_flag_for_cc_desktop(self):
        """实验栈实测 10-26：应税 100 + 免税 50 的退货，CC 桌面版按占比摊各行含税额摊成 104.67 / 52.33。ERP 标的免税行原样带进
        载荷，CC 桌面版摊含税额时不摊税给它。"""
        items = [{"name": "น้ำดื่ม", "qty": "1", "price": "100.00", "subtotal": "100.00", "unit": "ขวด", "code": "500310",
                  "loccod": "01", "posting_kind": "stock"},
                 {"name": "ผักสด", "qty": "1", "price": "50.00", "subtotal": "50.00", "unit": "กก.", "code": "500311",
                  "loccod": "01", "posting_kind": "stock", "vat_exempt": True}]
        payload = _map(_page(total="157.00", vat="7.00", subtotal="150.00", items=items)).payload
        self.assertEqual([bool(item.get("vat_exempt")) for item in payload["items"]], [False, True])
        self.assertEqual(payload["payload_version"], 11)  # 旧版 CC 免税行照应税写：诚实拒收

    def test_non_stock_lines_only_post_their_amount_and_an_own_account_is_kept(self):
        """退货里有服务、费用（非库存行）：同 HS 只记金额与总账；带科目的（ERP 认出的 Express 服务类物料）借它自己的科目，其余借退货科目。"""
        items = [
            {"name": "น้ำดื่ม 600 มล.", "qty": "1", "price": "60.00", "subtotal": "60.00", "unit": "ขวด", "code": "500310",
             "loccod": "01", "posting_kind": "stock"},
            {"name": "ค่าบริการ", "qty": "1", "price": "13.46", "subtotal": "13.46", "posting_kind": "service"},
            {"name": "ค่าติดตั้ง", "qty": "1", "price": "20.00", "subtotal": "20.00", "posting_kind": "service",
             "account": "41-03-00-00"},
        ]
        result = _map(_page(items=items))
        self.assertTrue(result.ok, result.reason)
        payload = result.payload
        self.assertEqual([item["item_mode"] for item in payload["items"]], ["stock_sale", "non_stock_item", "direct_account"])
        self.assertEqual(payload["lines"], [
            {"acc": REFUND_ACC, "side": "D", "amount": "73.46", "desc": "ลดหนี้/รับคืน", "role": "refund"},
            {"acc": "41-03-00-00", "side": "D", "amount": "20.00", "desc": "ลดหนี้/รับคืน", "role": "refund"},
            {"acc": "11-05-04-02", "side": "D", "amount": "6.54", "desc": "ภาษีขาย", "role": "output_vat"},
            {"acc": "11-01-01-00", "side": "C", "amount": "100.00", "desc": "เงินสด", "role": "cash"},
        ])
        service = [{"name": "ค่าบริการ", "qty": "1", "price": "93.46", "subtotal": "93.46", "posting_kind": "service"}]
        only = _map(_page(items=service))
        self.assertTrue(only.ok, only.reason)
        self.assertEqual(only.payload["lines"][0], {"acc": REFUND_ACC, "side": "D", "amount": "93.46",
                                                    "desc": "ลดหนี้/รับคืน", "role": "refund"})
        self.assertEqual(only.payload["items"][0]["item_mode"], "non_stock_item")

    def test_what_stops_a_refund(self):
        self.assertEqual(_map(_page(), choices={}).reason, P.REFUND_ACCOUNT_MISSING)
        unreadable = [{"name": "", "qty": "1", "price": "93.46", "subtotal": "93.46", "posting_kind": "service"}]
        self.assertEqual(_map(_page(items=unreadable)).reason, P.REFUND_ITEMS_INVALID)  # 明细不可信
        no_code = [{"name": "น้ำดื่ม", "qty": "1", "price": "93.46", "subtotal": "93.46", "posting_kind": "stock"}]
        self.assertEqual(_map(_page(items=no_code)).reason, P.REFUND_ITEMS_INVALID)
        self.assertEqual(_map(_page(items=[])).reason, P.REFUND_ITEMS_INVALID)
        self.assertEqual(_map(_page(pos=False)).reason, P.FACTS_INVALID)  # 退货单必须同时是 POS 日汇总
        negative = _page(payments=[("เงินสด", Decimal("-20.00")), ("พร้อมเพย์", Decimal("120.00"))])
        self.assertEqual(_map(negative, choices={"refund": REFUND_ACC, "receipts": {
            P.method_subject("พร้อมเพย์"): QR_ACC}}).reason, P.MISMATCH)
        broken = _page()
        broken["_extraction_audit"]["refund"]["against"] = "HS0000000000039"
        self.assertEqual(_map(broken).reason, P.FACTS_INVALID)


class RefundChoiceTests(unittest.TestCase):
    CONFIG = {"reported_accounts": [{"code": REFUND_ACC}, {"code": "41-01-00-00"}]}

    def _resolve(self, remembered, **kw):
        return choice_rules.resolve(_page()["fields"], direction="sales", posting_kind=None, config=self.CONFIG,
                                    remembered=remembered, **kw)

    def test_the_refund_account_is_asked_once_per_company(self):
        first = self._resolve({}, refund=True)
        self.assertEqual(first["needed"], [{"kind": "refund", "subject": "pos_refund", "label": "", "current": ""}])
        again = self._resolve({"refund": {"pos_refund": REFUND_ACC}}, refund=True)
        self.assertEqual((again["needed"], again["applied"]["refund"]), ([], REFUND_ACC))
        gone = self._resolve({"refund": {"pos_refund": "41-09-09-09"}}, refund=True)  # 科目表里已经没有：重新问
        self.assertEqual(gone["needed"][0]["current"], "41-09-09-09")
        trace = {}
        self._resolve({"refund": {"pos_refund": REFUND_ACC}}, refund=True, trace=trace)
        self.assertEqual(trace["refund"], ["pos_refund"])

    def test_without_the_refund_flag_nothing_changes(self):
        remembered = {"refund": {"pos_refund": REFUND_ACC}}
        self.assertEqual(self._resolve(remembered), self._resolve({}))
        self.assertNotIn("refund", self._resolve(remembered)["applied"])
        self.assertNotIn("refund", choice_rules.KINDS)  # Cowork 按 KINDS 存取账套记忆：不加
        self.assertEqual(choice_rules.REFUND, "refund")
        purchase = choice_rules.resolve(_page()["fields"], direction="purchase", posting_kind=None, config=self.CONFIG,
                                        remembered={}, refund=True)
        self.assertNotIn("refund", [item["kind"] for item in purchase["needed"]])


def _ordinary_histories():
    """没有 refund 块的几种载荷：ERP 收银日汇总 HS（含非现金）、ERP 普通销售单、OCR 的赊销票。"""
    subject = P.method_subject("พร้อมเพย์")
    pos_day = _page(refund=False, total="107.00", vat="7.00", subtotal="100.00",
                    items=[{"name": "น้ำดื่ม", "qty": "2", "price": "50.00", "subtotal": "100.00", "unit": "ขวด",
                            "code": "500310", "loccod": "01", "posting_kind": "stock"}],
                    payments=[("เงินสด", Decimal("47.00")), ("พร้อมเพย์", Decimal("60.00"))])
    erp_sale = _page(refund=False, pos=False, total="107.00", vat="7.00", subtotal="100.00",
                     items=[{"name": "บริการติดตั้ง", "qty": "1", "price": "100.00", "subtotal": "100.00",
                             "posting_kind": "service"}])
    ocr = {"id": "hist-sale", "invoice_date": "2015-12-15", "invoice_no": "SO-9001", "total_amount": "25097.92",
           "fields": {"buyer_name": "บริษัท ลูกค้า จำกัด", "buyer_tax": "0105551234567", "subtotal": "23456.00",
                      "vat": "1641.92", "invoice_number": "SO-9001"}}
    return [("pos_day", _history(pos_day), {"receipts": {subject: QR_ACC}, "refund": REFUND_ACC}),
            ("erp_sale", _history(erp_sale), {"refund": REFUND_ACC}),
            ("ocr", ocr, {})]


def _dump(histories):
    out = {}
    for name, history, choices in histories:
        result = build_express_sales_payload(history, config={**CONFIG, "choices": choices}, today=TODAY)
        out[name] = {"ok": result.ok, "reason": result.reason, "payload": result.payload}
    return json.dumps(out, ensure_ascii=False, sort_keys=True)


# `_dump(_ordinary_histories())` 用改动前的共用规则（main d8d42e6f）算出的结果，逐字节钉住
BASELINE_SHA256 = "92de6adf7dca6d556398641338d53febf503eaacf76c8679f52a3a0c8793c0a9"


class OrdinaryPayloadTests(unittest.TestCase):
    def test_payloads_without_a_refund_block_are_byte_for_byte_unchanged(self):
        import hashlib

        dumped = _dump(_ordinary_histories())
        self.assertEqual(hashlib.sha256(dumped.encode("utf-8")).hexdigest(), BASELINE_SHA256, dumped)
        payloads = json.loads(dumped)
        self.assertEqual(payloads["pos_day"]["payload"]["doctype"], "HS")
        self.assertNotIn("refund", payloads["pos_day"]["payload"])
        self.assertEqual(payloads["pos_day"]["payload"]["payload_version"], 4)


if __name__ == "__main__":
    unittest.main()
