"""ERP 当前台、Express 当账本（2026-10-22）的纯规则：不连库、不依赖 Frappe。

- Cowork 网关契约（`erp_gateway.contracts`）：校验 ERP 交来的单、转成推送读的页面、结果状态；
- 推送按码直落：票面给了对方档码就不问，明细行给了存货码 / 仓库码就照码写（`push.express.choices` / `common`），
  OCR 的票行为不变；
- ERP 侧（`pearnly_erp_portal.express.contracts`）：行金额摊成税前额、Express 主档落 ERP 的字段、库存对齐目标。
"""
from decimal import Decimal
import importlib.util
import pathlib
import unittest

from pearnly_cowork.erp_gateway import contracts as G
from pearnly_core.express_rules.push import choices as C
from pearnly_core.express_rules.push import common

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "erp_express_contracts", ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/express/contracts.py")
E = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(E)

OWN, SUPPLIER = "0105551234567", "0105536080112"


def doc(**override):
    body = {"version": 1, "ref": "PUR-2026-00012", "direction": "purchase", "date": "2026-10-20", "number": "IV-889",
            "company": {"tax_id": OWN, "branch": "0"}, "paid": False, "has_vat": True,
            "party": {"code": "B004", "name": "BANGCHAK", "tax_id": SUPPLIER, "branch": "00000"},
            "subtotal": "300.00", "vat": "21.00", "total": "321.00",
            "items": [{"name": "น้ำแข็งแพ็ค", "code": "ICE-01", "unit": "ถุง", "qty": "10", "price": "20", "amount": "200",
                       "loccod": "02", "stock": True},
                      {"name": "ค่าขนส่ง", "code": "", "unit": "", "qty": "1", "price": "100", "amount": "100",
                       "loccod": "", "stock": False}]}
    body.update(override)
    return body


class GatewayContract(unittest.TestCase):
    def test_document_and_pages(self):
        clean = G.document(doc())
        fields = G.pages(clean)[0]["fields"]
        self.assertEqual((fields["direction"], fields["seller_tax"], fields["buyer_tax"], fields["party_code"]),
                         ("purchase", SUPPLIER, OWN, "B004"))
        self.assertEqual((fields["payment_status"], fields["posting_item_type_manual"], fields["currency"]),
                         ("unpaid", "goods", "THB"))
        self.assertEqual(G.pages(G.document(doc(currency="usd")))[0]["fields"]["currency"], "USD")
        self.assertEqual((fields["subtotal"], fields["vat"], fields["total_amount"]), ("300.00", "21.00", "321.00"))
        first, second = fields["items"]
        self.assertEqual((first["code"], first["loccod"], first["posting_kind"]), ("ICE-01", "02", "stock"))
        self.assertEqual(second["posting_kind"], "service")
        sales = G.pages(G.document(doc(direction="sales", paid=True)))[0]["fields"]
        self.assertEqual((sales["buyer_tax"], sales["seller_tax"], sales["payment_status"]), (SUPPLIER, OWN, "paid"))

    def test_rejections(self):
        for body, code in ((doc(version=2), "erp_gateway.version_unsupported"),
                           (doc(company={"tax_id": "123"}), "erp_gateway.company_tax_missing"),
                           (doc(total="999"), "erp_gateway.amount_invalid"),
                           (doc(subtotal="250.00", total="271.00"), "erp_gateway.items_mismatch"),
                           (doc(date="2026-13-01"), "erp_gateway.date_invalid"),
                           (doc(has_vat=False), "erp_gateway.amount_invalid"),
                           (doc(currency="baht"), "erp_gateway.currency_invalid")):
            with self.assertRaises(G.GatewayError) as caught:
                G.document(body)
            self.assertEqual(caught.exception.code, code)
        # 带仓库的库存行必须是 Express 已有的存货
        items = doc()["items"]
        items[0] = {**items[0], "code": ""}
        with self.assertRaises(G.GatewayError):
            G.document(doc(items=items))

    def test_request_key_is_per_document(self):
        clean = G.document(doc())
        self.assertEqual(G.request_key("erp.test", clean), G.request_key("erp.test", {"ref": clean["ref"],
                                                                                         "direction": "purchase"}))
        self.assertNotEqual(G.request_key("erp.test", clean), G.request_key("erp.other", clean))

    def test_state(self):
        self.assertEqual(G.state_of("success", "", "success", "RR691020-001", "ok"), G.WRITTEN)
        self.assertEqual(G.state_of("success", "", "success", "RR691020-001", "mismatch"), G.MISMATCH)
        self.assertEqual(G.state_of("pending", "", "pending", "", ""), G.QUEUED)
        self.assertEqual(G.state_of("", "choice_needed:party", "", "", ""), G.HELD)
        self.assertEqual(G.state_of("manual", "push.no_writer", "manual", "", ""), G.HELD)
        # 回执成功却没有单号不算写进去
        self.assertEqual(G.state_of("success", "", "success", "", ""), G.QUEUED)


SUPPLIERS = [{"code": "B004", "name": "BANGCHAK", "tax_id": SUPPLIER, "branch": "00000"},
             {"code": "B005", "name": "BANGCHAK (สาขา 2)", "tax_id": SUPPLIER, "branch": "00000"}]
PRODUCTS = [{"code": "ICE-01", "name": "น้ำแข็งแพ็ค", "kind": "stock"}, {"code": "ICE-02", "name": "อื่น", "kind": "stock"}]


class PinnedCodes(unittest.TestCase):
    def test_party_and_item_codes_are_taken_without_asking(self):
        fields = G.pages(G.document(doc()))[0]["fields"]
        config = {"reported_suppliers": SUPPLIERS, "reported_products": PRODUCTS}
        result = C.resolve(fields, direction="purchase", posting_kind=None, config=config, remembered={})
        self.assertEqual(result["needed"], [])
        self.assertEqual(result["applied"]["party"], "B004")
        self.assertNotIn("items", result["applied"])
        # 同一税号两个档：OCR 的票（没有 party_code）照旧要人选
        ocr = {**fields, "party_code": ""}
        self.assertIn("party", [item["kind"] for item in C.resolve(ocr, direction="purchase", posting_kind=None,
                                                                    config=config, remembered={})["needed"]])
        # 给的码不在这份账套里：当没给，照旧问
        wrong = {**fields, "party_code": "ZZZ"}
        self.assertIn("party", [item["kind"] for item in C.resolve(wrong, direction="purchase", posting_kind=None,
                                                                    config=config, remembered={})["needed"]])

    def test_line_code_and_location_reach_the_payload(self):
        fields = G.pages(G.document(doc()))[0]["fields"]
        modes = ["stock_item", "non_stock_item"]
        detail = common.extract_line_items(fields, Decimal("300.00"), total=Decimal("321.00"),
                                           item_mode=lambda index, _item: modes[index])
        self.assertEqual(detail["status"], common.ITEMS_OK)
        stock, service = detail["items"]
        self.assertEqual((stock["code"], stock["loccod"]), ("ICE-01", "02"))
        self.assertNotIn("code", service)
        self.assertNotIn("loccod", service)
        payload = {"items": detail["items"], "payload_version": 1}
        C.apply_to_payload(payload, {"items": {C.norm_name("น้ำแข็งแพ็ค"): "ICE-02"}})
        self.assertEqual(payload["items"][0]["code"], "ICE-01")  # 行上自带的码不被同名的选择盖掉
        self.assertEqual(common.finalize_payload(payload)["payload_version"], 4)

    def test_ocr_lines_unchanged(self):
        fields = {"items": [{"name": "น้ำแข็ง", "qty": "2", "price": "50", "subtotal": "100"}]}
        detail = common.extract_line_items(fields, Decimal("100"), item_mode="stock_item")
        self.assertEqual(set(detail["items"][0]), {"name", "qty", "unit", "unit_price", "amount", "item_mode"})
        self.assertEqual(common.finalize_payload({"items": detail["items"]})["payload_version"], 1)


class ErpSide(unittest.TestCase):
    def test_document_spreads_lines_to_base(self):
        # 含税单价、整单折扣：行合计 321 + 0，税前额 300 → 摊回 200 / 100
        body = E.document(ref="SD-00001", direction="sales", doc_date="2026-10-20", number="SD-00001",
                          company={"tax_id": OWN, "branch": ""}, party={"code": "C01", "name": "ลูกค้า"},
                          paid=True, has_vat=True, total="321.00", vat="21.00",
                          lines=[{"name": "A", "code": "ICE-01", "qty": 10, "amount": "214.00", "loccod": "02",
                                  "stock": True},
                                 {"name": "B", "code": "", "qty": 1, "amount": "107.00", "loccod": "02", "stock": False}])
        self.assertEqual([item["amount"] for item in body["items"]], ["200.00", "100.00"])
        self.assertEqual(body["items"][0]["price"], "20.0000")
        self.assertEqual(body["items"][1]["loccod"], "")  # 没有 Express 码的行不带仓库
        clean = G.document(body)  # ERP 出的单网关照收
        self.assertEqual(clean["subtotal"], Decimal("300"))

    def test_master_rows(self):
        units = E.unit_names({"units": [{"code": "01", "name": "ชิ้น"}, {"code": "02", "name": "กล่อง"}]})
        item = E.product({"code": "ICE-01", "name": "น้ำแข็ง", "kind": "stock", "unit": "01", "sale_unit": "02",
                          "sale_factor": "12", "purchase_unit": "01", "purchase_factor": "1",
                          "prices": ["35.00", "0", "0", "0", "0"]}, units)
        self.assertEqual((item["base_unit"], item["unit"], item["price"], item["stock"]), ("ชิ้น", "กล่อง", "35.00", True))
        self.assertEqual(item["units"], [{"unit_name": "กล่อง", "factor": "12"}])
        party = E.party({"code": "B004", "name": "X", "tax_id": SUPPLIER, "branch": "3"})
        self.assertEqual((party["branch"], party["head"]), ("00003", False))

    def test_stock_targets_add_pending(self):
        targets = E.stock_targets([{"code": "ICE-01", "loccod": "01", "qty": "50"},
                                   {"code": "ICE-01", "loccod": "02", "qty": "5"}],
                                  {("ICE-01", "01"): Decimal("-3"), ("ICE-02", "01"): Decimal("4")})
        self.assertEqual(targets, {("ICE-01", "01"): Decimal("47"), ("ICE-01", "02"): Decimal("5"),
                                   ("ICE-02", "01"): Decimal("4")})


if __name__ == "__main__":
    unittest.main()
