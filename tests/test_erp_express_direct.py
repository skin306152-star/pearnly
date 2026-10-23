"""ERP 当前台、Express 当账本，CC 桌面版直连 ERP（ADR-025）：ERP 这一侧不连库的检查（共用通道的表用内存替身）。

- ERP 单 → 写账规则读的票面（`express.contracts.checked` / `pages`）：方向、现 / 赊、对方档码、逐行存货码与仓库、货 / 服务；
- 准备载荷（`express.prepare`）走共用的 Express 写账规则（`pearnly_core.express_rules`，与 Cowork 推送同一份）：采购未付 RR、
  销售已收 HS，对方档 / 存货照码写、带仓库的载荷 4、分录借贷平；
- 停下的几种：本公司没有账套、主档还没读过（请电脑读一次）、同期两份账套（在 ERP 里选一次、记住复用）、服务类进项要选费用科目；
- 写账规则的按码直落：票面给了对方档码就不问，明细行给了存货码 / 仓库码就照码写，OCR 的票行为不变；
- ERP 侧主档落库与库存对齐的纯规则。
"""
from decimal import Decimal
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "apps/pearnly_core"), str(ROOT / "apps/pearnly_erp_portal")]

import cc_desktop_fake as fake  # noqa: E402

frappe = fake.install()

from pearnly_core.cc_desktop import books, contracts as channel  # noqa: E402
from pearnly_core.express_rules.push import choices as C  # noqa: E402
from pearnly_core.express_rules.push import common  # noqa: E402
from pearnly_erp_portal.express import choices, contracts as E, facts, prepare  # noqa: E402

OWN, SUPPLIER = "0105551234567", "0105536080112"
PATH = r"C:\PearnlyExpress\70EXP\T9NIEX12"
MAPPING = {"revenue_acc": "41-01-00-00", "ar_acc": "11-03-01-00", "vat_output_acc": "21-04-01-00",
           "fallback_acc": "51-03-00-00", "ap_acc": "21-01-01-00", "vat_input_acc": "11-06-01-00"}
ACCOUNTS = [{"code": code, "name": code, "type": code[0]} for code in
            ("11-01-01-00", "11-02-01-00", "11-04-01-00", "52-08-00-00", *MAPPING.values())]
SUPPLIERS = [{"code": "B004", "name": "BANGCHAK", "tax_id": SUPPLIER, "branch": "00000"},
             {"code": "B005", "name": "BANGCHAK (สาขา 2)", "tax_id": SUPPLIER, "branch": "00000"}]
CUSTOMERS = [{"code": "C01", "name": "ร้านป้าแดง", "tax_id": "", "branch": "00000"}]
PRODUCTS = [{"code": "ICE-01", "name": "น้ำแข็งแพ็ค", "kind": "stock"}, {"code": "ICE-02", "name": "อื่น", "kind": "stock"}]


def doc(**override):
    body = {"version": 1, "ref": "PUR-2026-00012@20261023101010000000", "direction": "purchase", "date": "2026-10-20",
            "number": "IV-889", "company": {"tax_id": OWN, "branch": "00012"}, "paid": False, "has_vat": True,
            "currency": "THB", "party": {"code": "B004", "name": "BANGCHAK", "tax_id": SUPPLIER, "branch": "00000"},
            "subtotal": "300.00", "vat": "21.00", "total": "321.00",
            "items": [{"name": "น้ำแข็งแพ็ค", "code": "ICE-01", "unit": "ถุง", "qty": "10", "price": "20", "amount": "200.00",
                       "loccod": "02", "stock": True},
                      {"name": "น้ำแข็งอื่น", "code": "ICE-02", "unit": "ถุง", "qty": "5", "price": "20", "amount": "100.00",
                       "loccod": "02", "stock": True}]}
    body.update(override)
    return body


def book(path=PATH, uid="vsn:1/2", start="2026-01-01", end="2026-12-31"):
    return {"code": "T9", "name": "นิภา", "company": "นิภา เอ็กเช้นจ์", "tax_id": OWN, "branch": "00012", "path": path,
            "root": r"C:\PearnlyExpress\70EXP", "row": 3, "writable": True, "uid": uid, "period_start": start,
            "period_end": end, "mapping": MAPPING}


COMPANY = {"name": "CO-00001", "company_name": "นิภา", "tax_id": OWN, "branch": "00012"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        fake.reset(self.tmp.name)
        frappe.session.user = "staff@x"
        self.reads = []

    def tearDown(self):
        self.tmp.cleanup()

    def computer(self, rows):
        books.replace("PCD-0123456789abcdef0123", rows, None)
        return [channel.book_key(channel.book_identity(row["path"], row["uid"])) for row in rows]

    def masters(self, key, **extra):
        data = {"version": 2, "customers": CUSTOMERS, "suppliers": SUPPLIERS, "products": PRODUCTS,
                "departments": [], "facts": {"accounts": ACCOUNTS, "mapping": MAPPING,
                                             "catalog": {"isacc_candidates": []}}}
        data.update(extra)
        facts.save(key, data)

    def prepare(self, body):
        return prepare.prepare(body, COMPANY, lambda company, chosen: self.reads.append(chosen["key"]))


class Prepare(Base):
    def test_purchase_and_sales_payloads_from_the_shared_rules(self):
        key, = self.computer([book()])
        self.masters(key)
        result = self.prepare(doc())
        self.assertNotIn("hold", result, result)
        payload = result["payload"]
        self.assertEqual((payload["direction"], payload["doctype"], payload["supplier"]["code"]), ("purchase", "RR", "B004"))
        self.assertEqual([(item.get("code"), item.get("loccod")) for item in payload["items"]],
                         [("ICE-01", "02"), ("ICE-02", "02")])
        self.assertEqual(payload["payload_version"], 4)
        self.assertEqual((payload["account_dir"], payload["account_set"]), (PATH, PATH))
        lines = payload["lines"]
        debit = sum(Decimal(line["amount"]) for line in lines if line["side"] == "D")
        credit = sum(Decimal(line["amount"]) for line in lines if line["side"] == "C")
        self.assertEqual((debit, credit), (Decimal("321.00"), Decimal("321.00")))
        self.assertEqual(result["book"]["key"], key)
        sales = self.prepare(doc(direction="sales", paid=True,
                                 party={"code": "C01", "name": "ร้านป้าแดง", "tax_id": "", "branch": ""}))
        self.assertNotIn("hold", sales, sales)
        self.assertEqual((sales["payload"]["doctype"], sales["payload"]["customer"]["code"]), ("HS", "C01"))

    def test_holds_until_the_computer_has_read_the_masters(self):
        self.assertEqual(self.prepare(doc())["hold"], E.NO_BOOK)  # 本公司没有一份账套被 CC 报过
        key, = self.computer([book()])
        self.assertEqual(self.prepare(doc())["hold"], E.READING)
        self.assertEqual(self.reads, [key])  # 请电脑读一次主档
        self.masters(key)
        self.assertIn("payload", self.prepare(doc()))

    def test_two_account_sets_for_the_same_year_are_chosen_once(self):
        first, second = self.computer([book(), book(path=r"C:\PearnlyExpress\70EXP\T9COPY", uid="vsn:1/9")])
        for key in (first, second):
            self.masters(key)
        held = self.prepare(doc())
        self.assertEqual(held["hold"], "choice_needed:account_set")
        needed, = held["needed"]
        self.assertEqual(sorted(option["code"] for option in needed["options"]), sorted([first, second]))
        # 选项写明位置（同名的副本与真账只有位置不同）
        self.assertEqual(sorted(option["name"] for option in needed["options"]),
                         sorted(["นิภา · " + PATH, r"นิภา · C:\PearnlyExpress\70EXP\T9COPY"]))
        choices.remember(COMPANY["name"], needed["kind"], needed["subject"], second)
        result = self.prepare(doc())
        self.assertEqual(result["book"]["key"], second)
        self.assertEqual(result["payload"]["account_dir"], r"C:\PearnlyExpress\70EXP\T9COPY")

    def test_a_service_purchase_asks_for_the_expense_account(self):
        key, = self.computer([book()])
        self.masters(key)
        service = [{"name": "ค่าขนส่ง", "code": "", "unit": "", "qty": "1", "price": "300", "amount": "300.00",
                    "loccod": "", "stock": False}]
        held = self.prepare(doc(items=service))
        self.assertEqual(held["hold"], "choice_needed:account")
        needed, = held["needed"]
        choices.remember(COMPANY["name"], "account", needed["subject"], "52-08-00-00")
        payload = self.prepare(doc(items=service))["payload"]
        self.assertIn("52-08-00-00", [line["acc"] for line in payload["lines"] if line["side"] == "D"])

    def test_a_line_code_no_longer_in_the_book_is_chosen_again(self):
        key, = self.computer([book()])
        self.masters(key)
        body = doc()
        body["items"][0].update(code="ICE\xa0OLD", name="น้ำแข็งซอง")  # ERP 商品上记的码，账套里已经没有
        held = self.prepare(body)
        self.assertEqual(held["hold"], "choice_needed:item")  # 不把认不出的码交给电脑
        needed, = held["needed"]
        choices.remember(COMPANY["name"], "item", needed["subject"], "ICE-01")
        payload = self.prepare(body)["payload"]
        self.assertEqual([item.get("loccod") for item in payload["items"]], ["02", "02"])
        self.assertEqual(payload["items"][1]["code"], "ICE-02")

    def test_the_expense_accounts_come_first_without_hiding_the_rest(self):
        accounts = facts.reported({"facts": {"accounts": ACCOUNTS}})["reported_accounts"]
        codes = [row["code"] for row in facts.accounts_first(accounts, "purchase|" + SUPPLIER)]
        self.assertEqual(sorted(codes), sorted(row["code"] for row in ACCOUNTS))  # 进项也可能记资产：不筛
        self.assertEqual(codes[:2], ["52-08-00-00", "51-03-00-00"])
        self.assertEqual(facts.accounts_first([{"code": "52-01"}, {"code": "41-01"}], "sales|x")[0]["code"], "41-01")

    def test_a_party_turned_away_by_the_computer_is_chosen_from_the_existing_records(self):
        from pearnly_erp_portal.express import prepare as P
        key, = self.computer([book()])
        self.masters(key)
        needed, = P.party_needed(doc(party={"code": "", "name": "BANGCHAK PETROLEUM", "tax_id": SUPPLIER, "branch": "00000"}),
                                 COMPANY)
        self.assertEqual((needed["kind"], needed["existing_only"]), ("party", True))
        self.assertEqual([option["code"] for option in needed["options"]], ["B004", "B005"])  # 同税号的现成档
        choices.remember(COMPANY["name"], "party", needed["subject"], "B005")
        payload = self.prepare(doc(party={"code": "", "name": "BANGCHAK PETROLEUM", "tax_id": SUPPLIER,
                                          "branch": "00000"}))["payload"]
        self.assertEqual(payload["supplier"]["code"], "B005")  # 照选的写

    def test_bad_documents_fail(self):
        key, = self.computer([book()])
        self.masters(key)
        for body, code in ((doc(total="999"), "express.amount_invalid"),
                           (doc(subtotal="250.00", total="271.00"), "express.items_mismatch"),
                           (doc(date="2026-13-01"), "express.date_invalid"),
                           (doc(company={"tax_id": "123", "branch": ""}), E.NO_TAX)):
            self.assertEqual(self.prepare(body), {"failed": code})


class PinnedCodes(unittest.TestCase):
    def test_party_and_item_codes_are_taken_without_asking(self):
        fields = E.pages(E.checked(doc()))[0]["fields"]
        config = {"reported_suppliers": SUPPLIERS, "reported_products": PRODUCTS}
        result = C.resolve(fields, direction="purchase", posting_kind=None, config=config, remembered={})
        self.assertEqual(result["needed"], [])
        self.assertEqual(result["applied"]["party"], "B004")
        ocr = {**fields, "party_code": ""}  # OCR 的票（没有 party_code）同税号两个档照旧要人选
        self.assertIn("party", [item["kind"] for item in C.resolve(ocr, direction="purchase", posting_kind=None,
                                                                    config=config, remembered={})["needed"]])

    def test_ocr_lines_unchanged(self):
        fields = {"items": [{"name": "น้ำแข็ง", "qty": "2", "price": "50", "subtotal": "100"}]}
        detail = common.extract_line_items(fields, Decimal("100"), item_mode="stock_item")
        self.assertEqual(set(detail["items"][0]), {"name", "qty", "unit", "unit_price", "amount", "item_mode"})
        self.assertEqual(common.finalize_payload({"items": detail["items"]})["payload_version"], 1)


class ErpSide(unittest.TestCase):
    def test_document_spreads_lines_to_base(self):
        body = E.document(ref="SD-00001", direction="sales", doc_date="2026-10-20", number="SD-00001",
                          company={"tax_id": OWN, "branch": ""}, party={"code": "C01", "name": "ลูกค้า"},
                          paid=True, has_vat=True, total="321.00", vat="21.00",
                          lines=[{"name": "A", "code": "ICE-01", "qty": 10, "amount": "214.00", "loccod": "02",
                                  "stock": True},
                                 {"name": "B", "code": "", "qty": 1, "amount": "107.00", "loccod": "02", "stock": False}])
        self.assertEqual([item["amount"] for item in body["items"]], ["200.00", "100.00"])
        self.assertEqual(body["items"][1]["loccod"], "")  # 没有 Express 码的行不带仓库
        fields = E.pages(E.checked(body))[0]["fields"]
        self.assertEqual((fields["seller_tax"], fields["payment_status"], fields["items"][0]["posting_kind"]),
                         (OWN, "paid", "stock"))

    def test_master_rows_and_stock_targets(self):
        units = E.unit_names({"units": [{"code": "01", "name": "ชิ้น"}, {"code": "02", "name": "กล่อง"}]})
        item = E.product({"code": "ICE-01", "name": "น้ำแข็ง", "kind": "stock", "unit": "01", "sale_unit": "02",
                          "sale_factor": "12", "purchase_unit": "01", "purchase_factor": "1",
                          "prices": ["35.00", "0", "0", "0", "0"]}, units)
        self.assertEqual((item["base_unit"], item["unit"], item["price"]), ("ชิ้น", "กล่อง", "35.00"))
        targets = E.stock_targets([{"code": "ICE-01", "loccod": "01", "qty": "50"}], {("ICE-01", "01"): Decimal("-3")})
        self.assertEqual(targets, {("ICE-01", "01"): Decimal("47")})

    def test_reported_facts_use_the_shared_sanitizers(self):
        reported = facts.reported({"customers": CUSTOMERS, "suppliers": SUPPLIERS, "products": PRODUCTS,
                                   "departments": [{"code": "SUTI", "name": "ขาย"}],
                                   "facts": {"accounts": ACCOUNTS, "catalog": {"isacc_candidates": []}}})
        self.assertEqual(reported["reported_suppliers"][0], SUPPLIERS[0])
        self.assertEqual(len(reported["reported_accounts"]), len(ACCOUNTS))
        self.assertEqual(reported["reported_stock_acc_groups"], [])
        self.assertNotIn("reported_accounts", facts.reported({"customers": []}))  # 旧版没有科目表：不当成「没有科目」
        self.assertEqual(json.loads(json.dumps(facts.mapping({"facts": {"mapping": {**MAPPING, "x": "1"}}}))), MAPPING)


class MasterSync(Base):
    """主档落进 ERP（`express.masters`）：一条 ERP 档一次只给一行 Express 档；旧版读成普通空格的编码认回原来那条。"""

    def setUp(self):
        super().setUp()
        import types
        from unittest import mock
        utils = types.ModuleType("frappe.utils")
        utils.now_datetime = utils.nowdate = lambda: "2026-10-23"
        stubs = {"frappe.utils": utils}
        for name in ("pearnly_erp_portal.express.push", "pearnly_erp_portal.express.target",
                     "pearnly_erp_portal.inventory", "pearnly_erp_portal.inventory.contracts",
                     "pearnly_erp_portal.inventory.stock"):
            stubs[name] = types.ModuleType(name)
        patcher = mock.patch.dict(sys.modules, stubs)
        patcher.start()
        self.addCleanup(patcher.stop)
        sys.modules.pop("pearnly_erp_portal.express.masters", None)
        self.addCleanup(sys.modules.pop, "pearnly_erp_portal.express.masters", None)
        from pearnly_erp_portal.express import masters
        self.sync = masters
        frappe.clear_messages = lambda: None
        frappe.log_error = lambda **kw: self.fail(kw)
        frappe.get_traceback = lambda: ""
        counter = iter(range(100, 999))

        class Named:
            def autoname(self, doc):
                doc["name"] = doc.get("name") or f"SUP-{next(counter)}"

            before_insert = validate = lambda self, doc: None

        fake.STATE.controllers["ERP Supplier"] = Named()

    def supplier(self, **values):
        doc = frappe.get_doc({"doctype": "ERP Supplier", "is_active": 1, **values})
        doc.insert()
        return doc["name"]

    def test_one_erp_supplier_is_claimed_once_and_a_spaced_code_is_taken_back(self):
        loose = self.supplier(supplier_name="ชาญ ฟู้ด", express_code="ชาญ ฟู้ด", express_company="CO-00127")
        free = self.supplier(supplier_name="วิริยะประกันภัย", tax_id="0107555000139", branch_type="head_office")
        rows = [{"code": "ชาญ\xa0ฟู้ด", "name": "ชาญ ฟู้ด", "tax_id": "", "branch": "00000"},
                {"code": "V002", "name": "วิริยะประกันภัย (ไม่ใช้)", "tax_id": "0107555000139", "branch": "00000"},
                {"code": "V004", "name": "วิริยะประกันภัย", "tax_id": "0107555000139", "branch": "00000"}]
        self.sync._parties("ERP Supplier", "CO-00127", "T.9", rows, self.sync._supplier_fields)
        table = fake.STATE.db.table("ERP Supplier")
        self.assertEqual(table[loose]["express_code"], "ชาญ\xa0ฟู้ด")  # 原来那条改成正确编码，不另建、不停用
        self.assertEqual(table[loose]["is_active"], 1)
        codes = sorted(row["express_code"] for row in table.values())
        self.assertEqual(codes, sorted(["ชาญ\xa0ฟู้ด", "V002", "V004"]))  # V002 没被 V004 顶掉
        self.assertEqual(table[free]["express_code"], "V002")


class CompanyMasters(Base):
    """识别 / 提交时认档（`masters.selection`）：本公司从 Express 同步来的优先，其次没挂 Express 的，别家公司的不认；
    销售按读出的买方税号认客户（10-23 验收：客户、商品在 Express 里建了重复档）。"""

    def setUp(self):
        super().setUp()
        from pearnly_erp_portal.masters import selection
        self.selection = selection
        counter = iter(range(100, 999))

        class Named:
            def autoname(self, doc):
                doc["name"] = doc.get("name") or f"X-{next(counter)}"

            before_insert = validate = lambda self, doc: None

        for doctype in ("ERP Customer", "ERP Product"):
            fake.STATE.controllers[doctype] = Named()

    def row(self, doctype, **values):
        doc = frappe.get_doc({"doctype": doctype, "is_active": 1, **values})
        doc.insert()
        return doc["name"]

    def test_products_of_this_company_come_first_and_other_companies_are_not_taken(self):
        mine = self.row("ERP Product", product_name="น้ำแข็งแพ็ค", express_code="น้ำแข็งแพ็ค", express_company="CO-00127")
        loose = self.row("ERP Product", product_name="น้ำแข็งแพ็ค", code="PN00002")
        self.row("ERP Product", product_name="น้ำแข็งแพ็ค", express_code="ICE-01", express_company="CO-00078")
        self.assertEqual(self.selection.match_product("น้ำแข็งแพ็ค", "CO-00127"), (mine, 1))
        self.assertEqual(self.selection.match_product("น้ำแข็งแพ็ค", "CO-00999"), (loose, 1))  # 没接 Express 的公司
        self.assertEqual(self.selection.match_product("น้ำแข็งแพ็ค")[1], 3)  # 不给公司：照旧全认，交人选

    def test_a_customer_is_found_by_the_buyer_tax_id_before_the_misread_name(self):
        head = self.row("ERP Customer", customer_name="บิ๊กซี (สำนักงานใหญ่)", tax_id="0107536000633", branch_type="hq",
                        express_code="BIG C", express_company="CO-00127")
        self.row("ERP Customer", customer_name="บิ๊กซี สาขา 2", tax_id="0107536000633", branch_type="branch",
                 branch_no="00002", express_code="BIG C2", express_company="CO-00127")
        found = self.selection.match_party("customer", "กิจยาคีน", tax_id="0107536000633", branch_type="hq",
                                           company="CO-00127", ambiguous="none")
        self.assertEqual(found, head)
        both = self.selection.match_party("customer", "x", tax_id="0107536000633", company="CO-00127", ambiguous="none")
        self.assertIsNone(both)  # 几条都像：不预选，交人选

    def test_of_several_records_with_one_tax_id_the_one_in_use_is_taken(self):
        fake.STATE.controllers["ERP Supplier"] = fake.STATE.controllers["ERP Customer"]
        fake.STATE.controllers["Purchase Document"] = fake.STATE.controllers["ERP Customer"]
        tax = "0107555000139"
        self.row("ERP Supplier", supplier_name="วิริยะประกันภัย (ไม่ใช้)", tax_id=tax, branch_type="head_office",
                 express_code="V002", express_company="CO-00127")
        used = self.row("ERP Supplier", supplier_name="วิริยะประกันภัย จำกัด มหาชน", tax_id=tax,
                        branch_type="head_office", express_code="วิริยะ", express_company="CO-00127")
        self.row("Purchase Document", supplier=used, company="CO-00127", status="submitted")
        found = self.selection.match_party("supplier", "บริษัท วิริยะประกันภัย จำกัด (มหาชน)", tax_id=tax,
                                           branch_type="head_office", company="CO-00127")
        self.assertEqual(found, used)  # 名称都对不上：取这家公司最近在单据上用过的那条，不报「找到多个匹配结果」

    def test_buyer_tax_and_branch_from_the_reading(self):
        from pearnly_erp_portal.sales import contracts as sales
        self.assertEqual(sales.buyer_tax("0-1075-36000-63-3"), "0107536000633")
        self.assertEqual(sales.buyer_tax("0107536000634"), "")  # 校验位不对不认
        self.assertEqual((sales.branch_no("สาขา 12"), sales.branch_no(""), sales.branch_no("00000")), ("00012", None, "00000"))


if __name__ == "__main__":
    unittest.main()
