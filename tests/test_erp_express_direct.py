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
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "apps/pearnly_core"), str(ROOT / "apps/pearnly_erp_portal")]

import cc_desktop_fake as fake  # noqa: E402

frappe = fake.install()

from pearnly_core.cc_desktop import books, contracts as channel  # noqa: E402
from pearnly_core.express_rules.push import choices as C  # noqa: E402
from pearnly_core.express_rules.push import common  # noqa: E402
from pearnly_erp_portal.express import choices, contracts as E, facts, prepare  # noqa: E402

OWN, SUPPLIER = "0105551234567", "0105536080112"
OWN_TAX = "0105549038811"
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
    def test_a_pack_unit_carries_the_erp_factor_and_needs_a_cc_desktop_that_knows_it(self):
        """验收 10-25：包装单位按 1 写进 Express。ERP 单据行带上自己的换算率，载荷盖 7（1.12.3 测试包认 6 却没有换算，诚实拒收）。"""
        key, = self.computer([book()])
        self.masters(key)
        body = doc()
        body["items"][0].update(unit="ลัง", qty="1", factor="12")
        payload = self.prepare(body)["payload"]
        self.assertEqual([(item.get("unit"), item.get("qty"), item.get("factor")) for item in payload["items"]],
                         [("ลัง", "1.00", "12.00"), ("ถุง", "5.00", None)])
        self.assertEqual(payload["payload_version"], 7)

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

    def test_a_service_item_that_is_an_express_account_keeps_its_own_account(self):
        # 10-24 验收问题 24：同一供应商记住了电费科目，再开一张维修费（明细认出 Express 费用类物料 5320-02），被记成了电费
        key, = self.computer([book()])
        self.masters(key, facts={"accounts": [*ACCOUNTS, *({"code": code, "name": code, "type": "5"}
                                                           for code in ("53-30-02-00", "53-20-02-00"))],
                                 "mapping": MAPPING, "catalog": {"isacc_candidates": []}})
        repair = [{"name": "ค่าซ่อมแซมโกดัง", "code": "53-20-02-00", "unit": "", "qty": "1", "price": "200",
                   "amount": "200.00", "loccod": "", "stock": False},
                  {"name": "ค่าขนส่ง", "code": "", "unit": "", "qty": "1", "price": "100", "amount": "100.00",
                   "loccod": "", "stock": False}]
        held = self.prepare(doc(items=repair))
        self.assertEqual(held["hold"], "choice_needed:account")   # 认不出物料的那一行照旧要选费用科目
        needed, = held["needed"]
        choices.remember(COMPANY["name"], "account", needed["subject"], "53-30-02-00")   # 这家记住的是电费
        payload = self.prepare(doc(items=repair))["payload"]
        debits = {line["acc"]: line["amount"] for line in payload["lines"] if line["side"] == "D"}
        self.assertEqual((debits["53-20-02-00"], debits["53-30-02-00"]), ("200.00", "100.00"))   # 维修记维修，运费记记住的
        self.assertEqual([(item["item_mode"], item.get("account")) for item in payload["items"]],
                         [("direct_account", "53-20-02-00"), ("direct_account", None)])
        only = self.prepare(doc(items=repair[:1], subtotal="200.00", vat="14.00", total="214.00"))
        self.assertNotIn("hold", only, only)   # 都认出了：不问、不套用记住的电费
        self.assertEqual([line["acc"] for line in only["payload"]["lines"] if line["side"] == "D"][0], "53-20-02-00")
        sales = self.prepare(doc(direction="sales", paid=True, items=repair[:1], subtotal="200.00", vat="14.00",
                                 total="214.00", party={"code": "C01", "name": "ร้านป้าแดง", "tax_id": "", "branch": ""}))
        self.assertIn("53-20-02-00", [line["acc"] for line in sales["payload"]["lines"] if line["side"] == "C"])

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
        self.assertEqual(needed["kind"], "party")
        self.assertNotIn("existing_only", needed)  # 现成档之外也能选新建（10-24 验收：真是新客户被误判时）
        self.assertEqual([option["code"] for option in needed["options"]], ["B004", "B005"])  # 同税号的现成档
        choices.remember(COMPANY["name"], "party", needed["subject"], "B005")
        payload = self.prepare(doc(party={"code": "", "name": "BANGCHAK PETROLEUM", "tax_id": SUPPLIER,
                                          "branch": "00000"}))["payload"]
        # 照选的写，名称与税号用那条档登记的（10-24 验收：ISVAT 税号空、名称是手输的）
        self.assertEqual((payload["supplier"]["code"], payload["supplier"]["name"], payload["supplier"]["tax_id"]),
                         ("B005", "BANGCHAK (สาขา 2)", SUPPLIER))
        choices.remember(COMPANY["name"], "party", needed["subject"], C.NEW)
        payload = self.prepare(doc(party={"code": "", "name": "BANGCHAK PETROLEUM", "tax_id": SUPPLIER,
                                          "branch": "00000"}))["payload"]
        self.assertTrue(payload["supplier"]["confirmed_new"])  # 选新建：电脑不再按疑似重复拦

    def test_a_chosen_existing_party_writes_its_registered_name_and_tax(self):
        from pearnly_erp_portal.express import prepare as P
        data = {"customers": [{"code": "CPMMMM338", "name": "สันติชัย ยี่สุ่น", "tax_id": "1769900186992", "branch": "00000"}]}
        payload = {"direction": "sales", "customer": {"code": "CPMMMM338", "name": "สันติชัย ยี่สุ่น.", "tax_id": "",
                                                      "customer_new": False}}
        self.assertEqual(P.master_party(payload, data)["customer"], {"code": "CPMMMM338", "name": "สันติชัย ยี่สุ่น",
                                                                     "tax_id": "1769900186992", "customer_new": False})
        fresh = {"direction": "sales", "customer": {"code": "", "name": "ใหม่", "customer_new": True}}
        self.assertEqual(P.master_party(fresh, data), fresh)  # 新建的不动

    def test_an_unfit_stock_group_is_chosen_once_and_written_with_the_choice(self):
        # 10-23 第 3 轮：电脑按账套里挂得最多的组建库存品被判据拦下 → 在 ERP 里从合格的组里选一次，再写时带上
        from pearnly_erp_portal.express import prepare as P
        key, = self.computer([book()])
        groups = [{"code": "DM", "desc": "วัตถุดิบ", "inv_acc": "11-04-01-00", "fit": True},
                  {"code": "ST01", "desc": "อุปกรณ์", "inv_acc": "14-01-03-00", "fit": False}]
        self.masters(key, facts={"accounts": ACCOUNTS, "mapping": MAPPING, "catalog": {"isacc_candidates": groups}})
        needed, = P.group_needed(doc(), COMPANY)
        self.assertEqual((needed["kind"], [option["code"] for option in needed["options"]]), ("stock_group", ["DM"]))
        choices.remember(COMPANY["name"], needed["kind"], needed["subject"], "DM")
        self.assertEqual(self.prepare(doc())["payload"]["stock_acccod"], "DM")

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
        self.assertEqual(body["items"][1]["loccod"], "")  # 非库存行不带仓库
        fields = E.pages(E.checked(body))[0]["fields"]
        self.assertEqual((fields["seller_tax"], fields["payment_status"], fields["items"][0]["posting_kind"]),
                         (OWN, "paid", "stock"))

    def test_a_stock_line_without_an_express_code_still_names_its_warehouse(self):
        # 出入库第一期（10-25）：随单新建的物料也按这一行的仓写、建 STLOC 行（原来不带仓库码、落主仓，核载荷还拒「无码带仓」）
        body = E.document(ref="PD-00001", direction="purchase", doc_date="2026-10-20", number="PD-00001",
                          company={"tax_id": OWN, "branch": ""}, party={"code": "S01", "name": "ผู้ขาย"},
                          paid=False, has_vat=False, total="120.00", vat="0",
                          lines=[{"name": "น้ำแข็งบด", "code": "", "qty": 6, "amount": "120.00", "loccod": "02",
                                  "stock": True}])
        self.assertEqual((body["items"][0]["code"], body["items"][0]["loccod"]), ("", "02"))
        fields = E.pages(E.checked(body))[0]["fields"]
        self.assertEqual((fields["items"][0]["loccod"], fields["items"][0]["posting_kind"]), ("02", "stock"))

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
        import datetime as dt
        utils.nowdate = lambda: "2026-10-23"
        utils.now_datetime = lambda: dt.datetime(2026, 10, 23, 12, 1)
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
        store = {}

        class Cache:
            def hset(self, name, key, value):
                store.setdefault(name, {})[key] = value

            def hkeys(self, name):
                return list(store.get(name, {}))

            def delete_value(self, name):
                store.pop(name, None)

            def get_value(self, name):
                return store.get(name)

            def set_value(self, name, value, expires_in_sec=None):
                store[name] = value

        frappe.cache = Cache()
        self.cache = store
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

    def test_a_second_sync_reads_no_unchanged_record(self):
        # 10-23 生产：逐条 get_doc 比对，几千条要几分钟。现有值一次取出，没变的行不读出、不保存
        from unittest import mock
        rows = [{"code": f"V{i:03d}", "name": f"ผู้ขาย {i}", "tax_id": "", "branch": "00000"} for i in range(30)]
        self.assertEqual(self.sync._parties("ERP Supplier", "CO-1", "T.9", rows, self.sync._supplier_fields), 30)
        with mock.patch.object(frappe, "get_doc", side_effect=AssertionError("unchanged rows must not be read")):
            self.assertEqual(self.sync._parties("ERP Supplier", "CO-1", "T.9", rows, self.sync._supplier_fields), 0)
        rows[3] = {**rows[3], "name": "ผู้ขาย ใหม่"}
        self.assertEqual(self.sync._parties("ERP Supplier", "CO-1", "T.9", rows, self.sync._supplier_fields), 1)

    def test_manual_sync_only_starts_and_the_job_records_what_happened(self):
        # 手动同步：请求里只发起读取、排后台任务，不等不落库；任务做完（或出错）结果记在公司上并推给发起人
        from unittest import mock
        fake.STATE.db.table("ERP Company")["CO-1"] = {"name": "CO-1", "tax_id": OWN, "branch": "00012"}
        frappe.session.user = "admin@x"
        queued, pushed = [], []
        book_row = {"key": "a" * 40, "path": PATH, "label": "T.9"}
        made = {"request": "R1", "status": "done"}
        with mock.patch.object(self.sync.target, "current", lambda row, day: book_row, create=True), \
                mock.patch.object(self.sync, "requests", mock.Mock(find=lambda kind, key: made)), \
                mock.patch.object(frappe, "enqueue", lambda *a, **kw: queued.append(kw), create=True):
            self.assertEqual(self.sync.start("CO-1"), {"state": "reading", "book": book_row, "made": made})
        self.assertEqual(queued[0]["company"], "CO-1")  # 读好了也不在请求里落库
        self.assertEqual(list(self.cache["erp-express-sync-notify:CO-1"]), ["admin@x"])  # 自动那件做完也推给本人
        self.assertEqual(fake.STATE.db.table("ERP Company")["CO-1"]["express_sync_state"], "reading")
        errors = []
        with mock.patch.object(self.sync, "_sync_once", side_effect=RuntimeError("disk full")), \
                mock.patch.object(frappe, "log_error", lambda **kw: errors.append(kw), create=True), \
                mock.patch.object(frappe, "publish_realtime", lambda event, data, **kw: pushed.append((event, data, kw)),
                                  create=True):
            result = self.sync.sync("CO-1")
        company = fake.STATE.db.table("ERP Company")["CO-1"]
        self.assertEqual((result["state"], company["express_sync_state"]), ("failed", "failed"))
        self.assertIn("disk full", company["express_sync_note"])  # 不静默：原因记在公司上、另记 Error Log
        self.assertEqual(len(errors), 1)
        self.assertEqual((pushed[0][0], pushed[0][1]["state"], pushed[0][2]["user"]),
                         ("pearnly_erp_express_sync", "failed", "admin@x"))

    def test_a_chosen_book_read_by_the_computer_lands_when_preparing(self):
        # 10-23 第 3 轮：同期几份账套时人选的那份不是主档同步读的那份，读好了也没人落地，单据一直「正在读取」
        import gzip
        import types
        from unittest import mock
        key = "af714e1f" + "0" * 32
        data = {"version": 2, "customers": CUSTOMERS, "suppliers": SUPPLIERS, "products": PRODUCTS}
        dropped = []
        done = types.SimpleNamespace(find=lambda kind, request_key: {"request": "R1", "status": "done"},
                                     result=lambda name: (gzip.compress(json.dumps(data).encode()), {}),
                                     drop=dropped.append, waiting=lambda *a: None, create=lambda *a, **k: None)
        fake.STATE.db.table("ERP Company")["CO-1"] = {"name": "CO-1", "tax_id": OWN, "branch": "00012"}
        with mock.patch.object(self.sync, "requests", done), \
                mock.patch.object(self.sync.target, "current", lambda row, day: {"key": "f" * 40}, create=True):
            self.sync.ensure("CO-1", {"key": key, "label": "T.9", "path": PATH})
        self.assertEqual(facts.load(key)["suppliers"], SUPPLIERS)  # 账套资料落地，下一次准备就有
        self.assertEqual(dropped, ["R1"])
        self.assertEqual(fake.STATE.db.table("ERP Supplier"), {})  # 不是这家公司现用的那份：不落进 ERP 主档
        # 是现用的那份：准备可能在网页请求里，这里只排后台任务落库
        queued = []
        with mock.patch.object(self.sync, "requests", done), \
                mock.patch.object(self.sync.target, "current", lambda row, day: {"key": key}, create=True), \
                mock.patch.object(frappe, "enqueue", lambda method, **kw: queued.append((method, kw)), create=True):
            self.sync.ensure("CO-1", {"key": key, "label": "T.9", "path": PATH})
        self.assertEqual([(method, kw["book_key"]) for method, kw in queued],
                         [("pearnly_erp_portal.express.masters.land", key)])
        self.assertEqual(fake.STATE.db.table("ERP Supplier"), {})

    def test_local_records_are_claimed_only_when_the_tenant_has_one_company(self):
        # 10-24 审查：几家公司时本地档认领进一家，就从别家的列表、选择里消失了
        local = self.supplier(supplier_name="ปตท", tax_id="0107544000108", branch_type="head_office")
        rows = [{"code": "P01", "name": "ปตท", "tax_id": "0107544000108", "branch": "00000"}]
        table = fake.STATE.db.table("ERP Company")
        table["CO-1"] = {"name": "CO-1", "is_active": 1}
        table["CO-2"] = {"name": "CO-2", "is_active": 1}
        self.sync._parties("ERP Supplier", "CO-1", "T.9", rows, self.sync._supplier_fields)
        suppliers = fake.STATE.db.table("ERP Supplier")
        self.assertFalse(suppliers[local].get("express_company"))  # 本地档留给各家
        self.assertEqual([row["express_code"] for row in suppliers.values() if row.get("express_company") == "CO-1"],
                         ["P01"])

    def test_a_code_twice_in_the_book_is_one_record_and_old_copies_are_retired(self):
        # 10-24 测试机：Express 客户表里同一个编码两行，ERP 每同步一次多建一条（一家累积到 14 条）
        rows = [{"code": "C1", "name": "หนึ่ง", "tax_id": "", "branch": "00000"},
                {"code": "C1", "name": "หนึ่ง (ซ้ำ)", "tax_id": "", "branch": "00000"},
                {"code": "C 2", "name": "สอง", "tax_id": "", "branch": "00000"},
                {"code": "C\xa02", "name": "สอง (อีกรหัส)", "tax_id": "", "branch": "00000"}]  # 只差空格写法：两件
        old = self.supplier(supplier_name="หนึ่ง", express_code="C1", express_company="CO-1")
        extra = self.supplier(supplier_name="หนึ่ง", express_code="C1", express_company="CO-1")
        self.sync._parties("ERP Supplier", "CO-1", "T.9", rows, self.sync._supplier_fields)
        self.sync._parties("ERP Supplier", "CO-1", "T.9", rows, self.sync._supplier_fields)
        table = fake.STATE.db.table("ERP Supplier")
        active = sorted((row["express_code"], row["name"]) for row in table.values() if row.get("is_active"))
        self.assertEqual([code for code, _name in active], ["C 2", "C1", "C\xa02"])
        self.assertIn(("C1", old), active)  # 认最早的那条，单据里引用的不变
        self.assertEqual(table[extra]["is_active"], 0)

    def test_automatic_syncs_start_a_few_at_a_time_and_land_what_was_read(self):
        # 10-23：上百家公司自动建好后一起排同步。后台进程不空等电脑；同时只发起几家（电脑一本一本读，排多了等过领取时限）
        from unittest import mock
        table = fake.STATE.db.table("ERP Company")
        table["CO-0"] = {"name": "CO-0", "is_active": 1, "express_sync_state": "reading"}
        for i in range(1, 6):
            table[f"CO-{i}"] = {"name": f"CO-{i}", "is_active": 1, "express_sync_state": "ready"}
            self.sync.queue(f"CO-{i}")
        self.assertEqual({table[f"CO-{i}"]["express_sync_state"] for i in range(1, 6)}, {"queued"})
        started, queued = [], []

        def request(company, fresh=False):
            if company == "CO-0":
                return {"state": "reading", "made": {"status": "done"}}
            started.append(company)
            return {"state": "reading", "made": {"status": "requested"}}

        with mock.patch.object(self.sync, "_request", request), mock.patch.object(self.sync, "OPEN_READS", 3), \
                mock.patch.object(frappe, "enqueue", lambda method, **kw: queued.append(kw), create=True):
            self.sync.advance()
        self.assertEqual([(kw["company"], kw["wait"]) for kw in queued], [("CO-0", 0)])  # 读好的排落库，不再等
        self.assertEqual(started, ["CO-1", "CO-2", "CO-3"])
        self.assertEqual([table[f"CO-{i}"]["express_sync_state"] for i in range(1, 6)],
                         ["reading", "reading", "reading", "queued", "queued"])
        self.sync.queue("CO-1")  # 已经在读的不打回排队
        self.assertEqual(table["CO-1"]["express_sync_state"], "reading")
        table["CO-0"]["express_sync_state"] = "syncing"  # 正在落库的：不打回排队（打断的由 advance 收回）
        self.sync.queue("CO-0")
        self.assertEqual(table["CO-0"]["express_sync_state"], "syncing")

    def test_stock_is_not_applied_when_a_write_landed_after_the_read_started(self):
        """验收 10-26：读主档开始之后这份账套又有写账回执（写入或作废），读到的结存和 ERP 此刻不在同一时刻，套用会差那张单的数量。
        这一轮库存不套用、再排一轮；主档照落。"""
        from unittest import mock
        table = fake.STATE.db.table("ERP Company")
        table["CO-1"] = {"name": "CO-1", "is_active": 1, "express_sync_state": "reading", "express_pos_from": "2026-10-01"}
        applied, receipts = [], []

        def get_all(doctype, filters=None, limit=None, **kw):
            self.assertEqual(doctype, "Pearnly CC Job")
            self.assertEqual((filters["book_key"], filters["status"], filters["written_at"][0]), ("BK", "success", ">="))
            return receipts

        with mock.patch.object(frappe, "get_all", side_effect=get_all):
            self.assertFalse(self.sync._stock_stale("BK", "2026-10-26 00:24:00"))
            receipts.append("JOB-1")
            self.assertTrue(self.sync._stock_stale("BK", "2026-10-26 00:24:00"))
            self.assertFalse(self.sync._stock_stale("BK", ""))  # 没有读的时刻（旧数据）照旧套用
        for stale, expected in ((True, "stale"), (False, 3)):
            with mock.patch.object(self.sync, "_parties", lambda *a: 0), mock.patch.object(self.sync, "_products", lambda *a: 0), \
                    mock.patch.object(self.sync, "_warehouses", lambda *a: ({}, 0)), \
                    mock.patch.object(self.sync, "_stock", lambda *a: applied.append(1) or 3), \
                    mock.patch.object(self.sync, "_profile", lambda *a: {}), \
                    mock.patch.object(frappe.db, "commit", lambda: None, create=True):
                counts = self.sync.apply("CO-1", "T9", {"products": [], "stock": []}, stale=stale)
            self.assertEqual(counts["stock"], expected)
        self.assertEqual(applied, [1])
        table["CO-1"]["express_sync_state"] = "ready"
        self.sync._requeue_if_stale("CO-1", {"state": "ready", "stock": "stale"})  # 再排一轮，读写完之后的
        self.assertEqual(table["CO-1"]["express_sync_state"], "queued")

    def test_a_failed_read_is_recorded_once_and_a_cut_off_landing_is_queued_again(self):
        # 10-24 审查：电脑读失败的请求每分钟重发、一直算「读取中」占名额；落库被重启打断的一直停在「同步中」
        import types
        from unittest import mock
        table = fake.STATE.db.table("ERP Company")
        table["CO-1"] = {"name": "CO-1", "tax_id": OWN, "branch": "00012", "is_active": 1, "express_sync_state": "reading"}
        table["CO-2"] = {"name": "CO-2", "tax_id": OWN, "branch": "00012", "is_active": 1, "express_sync_state": "syncing"}
        table["CO-3"] = {"name": "CO-3", "tax_id": OWN, "branch": "00012", "is_active": 1, "express_sync_state": "syncing"}
        made = []
        stub = types.SimpleNamespace(find=lambda kind, key: None, waiting=lambda kind, key: None,
                                     latest=lambda kind, key: {"status": "failed", "code": "account_tables_missing"},
                                     create=lambda kind, key, params, **kw: made.append(key) or {"status": "requested"})
        with mock.patch.object(self.sync, "requests", stub), \
                mock.patch.object(self.sync, "_landing", lambda company: company == "CO-3"), \
                mock.patch.object(self.sync.target, "current", lambda row, day: {"key": "k1", "path": PATH, "label": "T.9"},
                                  create=True):
            self.sync.advance()
            self.assertEqual((table["CO-1"]["express_sync_state"], table["CO-1"]["express_sync_note"]),
                             ("failed", "account_tables_missing"))  # 不重发，原因记在公司上
            self.assertEqual(table["CO-2"]["express_sync_state"], "reading")  # 打断的收回、重新发起读取
            self.assertEqual(table["CO-3"]["express_sync_state"], "syncing")  # 还在落的不动
            self.assertEqual(made, ["erp_masters:k1"])
            self.sync.queue("CO-1")  # 新一轮（每小时兜底 / 改动）照样重发
            self.sync.advance()
        self.assertEqual(table["CO-1"]["express_sync_state"], "reading")

    def test_an_offline_company_is_queued_again_when_a_computer_is_back(self):
        # 10-24 验收：读取中电脑下线一直「读取中」；电脑回来后一直显示「未连接」
        import datetime as dt
        import types
        from unittest import mock
        table = fake.STATE.db.table("ERP Company")
        table["CO-1"] = {"name": "CO-1", "tax_id": OWN, "branch": "00012", "is_active": 1, "express_sync_state": "reading"}
        table["CO-2"] = {"name": "CO-2", "tax_id": OWN, "branch": "00012", "is_active": 1, "express_sync_state": "offline"}
        online = {"value": False}
        stub = types.SimpleNamespace(find=lambda kind, key: {"status": "requested"}, latest=lambda kind, key: None,
                                     waiting=lambda kind, key: None if online["value"] else {"reason": "offline"},
                                     create=lambda *a, **kw: {"status": "requested"})
        with mock.patch.object(self.sync, "requests", stub), \
                mock.patch.object(self.sync, "now_datetime", lambda: dt.datetime(2026, 10, 24, 14, 25)), \
                mock.patch.object(self.sync.target, "current", lambda row, day: {"key": "k1", "path": PATH, "label": "T.9"},
                                  create=True):
            self.sync.advance()
            self.assertEqual([table[c]["express_sync_state"] for c in ("CO-1", "CO-2")], ["offline", "offline"])
            online["value"] = True
            self.cache.pop("erp-express-offline-check", None)  # 隔了几分钟（缓存过期）
            self.sync.advance()
        self.assertEqual([table[c]["express_sync_state"] for c in ("CO-1", "CO-2")], ["reading", "reading"])

    def test_a_read_from_before_a_write_is_dropped(self):
        # 10-24 验收：写单后马上同步用了写单前读好的主档，把刚挂上码的客户停用了
        import types
        from unittest import mock
        dropped = []
        stub = types.SimpleNamespace(find=lambda kind, key: {"request": "R9", "status": "done"} if key == "erp_masters:k1" else None,
                                     drop=dropped.append)
        with mock.patch.object(self.sync, "requests", stub):
            self.sync.forget("k1")
            self.sync.forget("k2")
            self.sync.forget("")
        self.assertEqual(dropped, ["R9"])

    def test_only_a_changed_mark_or_a_new_book_starts_a_full_sync(self):
        # Express 主档有改动 ERP 自动跟上：每 10 分钟请电脑看一眼主档表的文件属性，变了 / 换了新年度账套才整套读
        import gzip
        import types
        from unittest import mock
        table = fake.STATE.db.table("ERP Company")
        table["CO-1"] = {"name": "CO-1", "express_mark": "m1", "express_book": "T.9", "express_sync_state": "ready"}
        table["CO-2"] = {"name": "CO-2", "express_mark": "m2", "express_book": "T.9", "express_sync_state": "ready"}
        table["CO-3"] = {"name": "CO-3", "express_mark": "m3", "express_book": "T.8", "express_sync_state": "ready"}
        marks = {"k1": "m1", "k2": "m2-new", "k3": "m3"}
        made = []
        stub = types.SimpleNamespace(
            find=lambda kind, key: {"request": key, "status": "done"},
            result=lambda name: (gzip.compress(json.dumps({"mark": marks[name.split(":")[1]]}).encode()), {}),
            drop=lambda name: None, waiting=lambda kind, key: None,
            create=lambda kind, key, params, **kw: made.append((kind, key)))
        rows = [types.SimpleNamespace(name=f"CO-{i}") for i in (1, 2, 3)]
        with mock.patch.object(self.sync, "requests", stub), \
                mock.patch.object(self.sync, "linked_companies", lambda: rows), \
                mock.patch.object(self.sync.target, "current",
                                  lambda row, day: {"key": "k" + row.name[-1], "label": "T.9", "path": PATH}, create=True):
            self.sync.check_marks()
        self.assertEqual([table[f"CO-{i}"]["express_sync_state"] for i in (1, 2, 3)], ["ready", "queued", "queued"])
        self.assertEqual(made, [("express_marks", "erp_marks:k1"), ("express_marks", "erp_marks:k2"),
                                ("express_marks", "erp_marks:k3")])

    def test_the_hourly_sync_only_takes_old_computers_and_stale_companies(self):
        import datetime as dt
        import types
        from unittest import mock
        now = dt.datetime(2026, 10, 23, 12, 0)
        table = fake.STATE.db.table("ERP Company")
        table["CO-1"] = {"name": "CO-1", "express_mark": "m", "express_synced_at": now - dt.timedelta(hours=2)}
        table["CO-2"] = {"name": "CO-2", "express_mark": None, "express_synced_at": now - dt.timedelta(hours=2)}
        table["CO-3"] = {"name": "CO-3", "express_mark": "m", "express_synced_at": now - dt.timedelta(hours=30)}
        rows = [types.SimpleNamespace(name=f"CO-{i}") for i in (1, 2, 3)]
        with mock.patch.object(self.sync, "linked_companies", lambda: rows), \
                mock.patch.object(self.sync, "now_datetime", lambda: now):
            self.sync.sync_all()
        self.assertEqual([table[f"CO-{i}"].get("express_sync_state") for i in (1, 2, 3)], [None, "queued", "queued"])

    def test_the_company_name_address_and_phone_follow_the_book(self):
        """主会话 10-26：接了 Express 的公司，名称、地址、电话以 Express 为准（打印抬头取自这里），同步时覆盖；账套里空的不动。"""
        table = fake.STATE.db.table("ERP Company")
        table["CO-1"] = {"name": "CO-1", "company_name": "นิภา", "address": "", "phone": "02-111"}
        settings = {"address": "99 ถ.สุขุมวิท กรุงเทพฯ", "phone": "02-999"}
        self.assertEqual(self.sync._profile("CO-1", settings, "นิภา เอ็กเช้นจ์"),
                         {"company_name": "นิภา เอ็กเช้นจ์", "address": "99 ถ.สุขุมวิท กรุงเทพฯ", "phone": "02-999"})
        self.assertEqual(self.sync._profile("CO-1", {}), {})
        table["CO-1"].update(address="99 ถ.สุขุมวิท กรุงเทพฯ", phone="02-999")
        self.assertEqual(self.sync._profile("CO-1", settings, "นิภา"), {})  # 一样的不重写

    def test_a_linked_company_keeps_its_express_name_address_and_phone(self):
        """主会话 10-26：接了 Express 的公司（有 express_book）在 ERP 里改名称、地址、电话被拦；没接的照常改；同步自己写入放行。"""
        from pearnly_erp_portal.express import locks

        class Doc(dict):
            doctype = "ERP Company"
            flags = None

            def __init__(self, before, **now):
                super().__init__(now)
                self.before = before
                self.flags = type("F", (), {"express_sync": False})()

            def get(self, key, default=None):
                return dict.get(self, key, default)

            def is_new(self):
                return False

            def get_doc_before_save(self):
                return self.before

        linked = {"express_book": "BK", "company_name": "นิภา", "address": "A", "phone": "1", "logo": ""}
        with self.assertRaises(Exception):
            locks.guard(Doc(linked, **{**linked, "company_name": "แก้ชื่อ"}))
        locks.guard(Doc(linked, **{**linked, "logo": "/private/files/logo.png"}))  # Logo 不是 Express 的字段：照常改
        local = {"express_book": "", "company_name": "นิภา", "address": "A", "phone": "1"}
        locks.guard(Doc(local, **{**local, "company_name": "แก้ชื่อ"}))  # 没接 Express：照常改

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
        # 本公司同名几条：编码就是品名的那条优先（Express 常拿品名当编码）
        self.row("ERP Product", product_name="น้ำแข็งแพ็ค", express_code="ICE-PACK", express_company="CO-00127")
        self.assertEqual(self.selection.match_product("น้ำแข็งแพ็ค", "CO-00127"), (mine, 1))

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

    def test_an_erp_customer_made_earlier_for_an_ambiguous_tax_id_is_reused(self):
        tax = "0105541034263"
        for code, branch in (("ม005", "00020"), ("โบว์ลิ่ง01", "00020")):
            self.row("ERP Customer", customer_name="เมเจอร์ โบว์ล " + code, tax_id=tax, branch_type="branch",
                     branch_no=branch, express_code=code, express_company="CO-00127")
        self.assertIsNone(self.selection.match_party("customer", "บริษัท เมเจอร์ โบว์ล กรุ๊ป จำกัด", tax_id=tax,
                                                     branch_type="branch", branch_no="00020", company="CO-00127",
                                                     ambiguous="none"))
        self.assertIsNone(self.selection.unlinked_party("customer", tax, branch_type="branch", branch_no="00020"))
        made = self.row("ERP Customer", customer_name="บริษัท เมเจอร์ โบว์ล กรุ๊ป จำกัด", tax_id=tax,
                        branch_type="branch", branch_no="00020")
        self.assertEqual(self.selection.unlinked_party("customer", tax, branch_type="branch", branch_no="00020"), made)

    def test_an_auto_matched_product_takes_its_own_unit(self):
        import types
        ice = types.SimpleNamespace(base_unit="กิโลกรัม", unit="กิโลกรัม",
                                    units=[types.SimpleNamespace(unit_name="กระสอบ")])
        self.assertEqual(self.selection.own_unit(ice, "ถุง"), "กิโลกรัม")  # 票面读的单位商品没有：用商品的
        self.assertEqual(self.selection.own_unit(ice, "กระสอบ"), "กระสอบ")
        self.assertEqual(self.selection.own_unit(ice, ""), "กิโลกรัม")

    def test_buyer_tax_and_branch_from_the_reading(self):
        from pearnly_erp_portal.sales import contracts as sales
        self.assertEqual(sales.buyer_tax("0-1075-36000-63-3"), "0107536000633")
        self.assertEqual(sales.buyer_tax("0107536000634"), "")  # 校验位不对不认
        self.assertEqual((sales.branch_no("สาขา 12"), sales.branch_no(""), sales.branch_no("00000")), ("00012", None, "00000"))



class AutoCompanies(Base):
    """CC 桌面版报来的账套 → ERP 公司（`express.companies`）。"""

    def test_books_are_grouped_by_tax_and_branch_and_the_latest_name_is_taken(self):
        from pearnly_erp_portal.express import companies
        rows = [{"company": "นิภา 2568", "tax_id": OWN, "branch": "12", "period_end": "2025-12-31"},
                {"company": "นิภา เอ็กเช้นจ์", "tax_id": OWN, "branch": "00012", "period_end": "2026-12-31"},
                {"company": "สาขาใหญ่", "tax_id": OWN, "branch": "", "period_end": "2026-12-31"},
                {"company": "ไม่มีเลขภาษี", "tax_id": "", "branch": "", "period_end": "2026-12-31"},
                {"company": "เลขผิด", "tax_id": "12345", "branch": "", "period_end": "2026-12-31"}]
        found = companies.groups(rows)
        self.assertEqual(found, {(OWN, "00012"): "นิภา เอ็กเช้นจ์", (OWN, "00000"): "สาขาใหญ่"})  # 没有效税号的不建
        existing = [{"name": "CO-1", "tax_id": OWN, "branch": "00000"}]
        self.assertEqual(companies.missing(found, existing), [(OWN, "00012", "นิภา เอ็กเช้นจ์")])

    def test_masters_are_read_from_the_latest_book_when_this_year_has_none(self):
        # 10-24 测试机：按账套自动建的公司里有只剩往年账套的；主档按公司认，读最近一年的，不报「没有账套」
        from pearnly_erp_portal.express import target
        old, older = self.computer([book(path=PATH + "-68", uid="vsn:1/68", start="2025-01-01", end="2025-12-31"),
                                    book(path=PATH + "-67", uid="vsn:1/67", start="2024-01-01", end="2024-12-31")])
        self.assertEqual(target.current(COMPANY, "2026-10-23")["key"], old)
        self.assertEqual(target.pick(COMPANY, "2026-10-23")["reason"], E.NO_BOOK)  # 今年的单照旧没有账套可写
        self.assertEqual(target.current(COMPANY, "2024-05-01")["key"], older)

    def test_only_connected_computers_that_report_the_chosen_roots_count(self):
        # 1.11.0 报整台电脑的账套、断开后名单还留着：照它们建会把事务所别的客户建进来
        from pearnly_erp_portal.express import companies
        devices = [{"name": "PCD-new", "token_digest": "x", "companion_version": "1.12.0"},
                   {"name": "PCD-old", "token_digest": "x", "companion_version": "1.11.0"},
                   {"name": "PCD-left", "token_digest": None, "companion_version": "1.12.3"},
                   {"name": "PCD-retired", "token_digest": "x", "retired_at": "2026-10-23", "companion_version": "1.13.0"}]
        self.assertEqual(companies.scoped(devices), ["PCD-new"])


class MasterIsolation(unittest.TestCase):
    """主档按公司隔离（`masters.access`）：Express 同步来的档只在当前公司列得出，本地档各家都能用。"""

    def setUp(self):
        from unittest import mock
        from pearnly_erp_portal.masters import access
        self.access = access
        patches = [mock.patch.object(access.company, "active_company", lambda user=None: "CO-A", create=True),
                   mock.patch.object(access, "company_allowed", lambda name, user=None: name in ("CO-A", "CO-B")),
                   mock.patch.object(frappe.db, "escape", lambda value: f"'{value}'", create=True)]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_lists_show_local_records_and_the_current_company_only(self):
        self.assertEqual(self.access.master_query("ERP Supplier", "staff@x"),
                         "(`tabERP Supplier`.`express_company` is null or `tabERP Supplier`.`express_company` = '' "
                         "or `tabERP Supplier`.`express_company` = 'CO-A')")  # 不包 ifnull，索引用得上
        self.assertEqual(self.access.master_query("ERP Supplier", "Administrator"), "")

    def test_a_record_opens_for_a_company_the_user_may_use(self):
        allowed = self.access.master_permission
        # 锁定版本的 Frappe 把钩子回 None 当成不允许（`has_controller_permissions`），放行要回 True
        self.assertIs(allowed({"express_company": ""}, user="staff@x"), True)       # 本地档：照角色权限
        self.assertIs(allowed({"express_company": "CO-B"}, user="staff@x"), True)   # 能用的另一家：旧单的链接打得开
        self.assertIs(allowed({"express_company": "CO-Z"}, user="staff@x"), False)


class PushModule(Base):
    def setUp(self):
        super().setUp()
        import types
        from unittest import mock
        utils = types.ModuleType("frappe.utils")
        utils.now_datetime = utils.nowdate = lambda: "2026-10-24"
        patcher = mock.patch.dict(sys.modules, {"frappe.utils": utils})
        patcher.start()
        self.addCleanup(patcher.stop)
        sys.modules.pop("pearnly_erp_portal.express.push", None)
        self.addCleanup(sys.modules.pop, "pearnly_erp_portal.express.push", None)
        from pearnly_erp_portal.express import push
        self.push = push


class MixedRatePurchase(PushModule):
    """验收复核测试包 e2（10-26）：采购单逐行定税率，有的行 0。0 税率的行标免税，CC 桌面版写价含税的现购时不摊税给它（原生 107 / 50、
    免税行 VATCOD '0'）；载荷盖 11，旧版 CC 诚实拒收。整张无税的单不标。"""

    def lines(self, *rates):
        import types

        class Row(dict):
            __getattr__ = dict.get

        doc = types.SimpleNamespace(lines=[Row(product=None, warehouse=None, description=f"L{index}", unit="ถุง", qty=1,
                                               line_total=100, vat_rate=rate, item_type="service")
                                           for index, rate in enumerate(rates)])
        return self.push._lines(self.push.PURCHASE, doc)

    def test_zero_rate_lines_of_a_taxed_purchase_are_marked_exempt(self):
        self.assertEqual([bool(line.get("vat_exempt")) for line in self.lines(7, 0)], [False, True])
        self.assertEqual([bool(line.get("vat_exempt")) for line in self.lines(0, 0)], [False, False])  # 整张无税：不标
        self.assertEqual([bool(line.get("vat_exempt")) for line in self.lines(7, 7)], [False, False])
        self.assertEqual(common.finalize_payload({"items": [{"name": "A", "vat_exempt": True}]})["payload_version"], 11)
        # 进项税记录：AMT01 只记应税部分、免税部分记 AMTRAT0（同原生 HP67012204）；没有免税行不带
        body = {"subtotal": "150.00", "items": [{"amount": "100.00"}, {"amount": "50.00", "vat_exempt": True}]}
        self.assertEqual(self.push.vat_fields(body), {"AMT01": "100.00", "AMTRAT0": "50.00"})
        self.assertEqual(self.push.vat_fields({"subtotal": "150.00", "items": [{"amount": "150.00"}]}), {})


class StockVoid(PushModule):
    """B 段 ①：已写进 Express 的出入库单在 ERP 作废 → 排一件作废原单；回执记在作废栏上，写入时的单号和状态不动。"""

    def setUp(self):
        super().setUp()
        self.docs = fake.STATE.db.table("ERP Stock Entry")
        self.enqueued = []

        import datetime as dt
        frappe.utils.get_datetime = lambda value: dt.datetime.fromisoformat(str(value))

    def written(self, **extra):
        self.docs["SE-1"] = {"name": "SE-1", "creation": "2026-10-25 10:00:00", "owner": "a@x", "express_state": "written",
                             "express_docnum": "OU691025-001", "express_void_job": None, **extra}

    def test_a_written_entry_queues_a_void_and_the_receipt_marks_it_cancelled(self):
        from unittest import mock
        from pearnly_erp_portal.express import stock_entry
        self.written()
        with mock.patch.object(self.push, "company_of", lambda doctype, name: "CO-1"), \
                mock.patch.object(self.push, "_company", lambda name: COMPANY), \
                mock.patch.object(stock_entry, "void_payload",
                                  lambda name, company: {"payload": {"payload_version": 8, "action": "void"}, "book": {"key": "BK"}}), \
                mock.patch.object(self.push.jobs, "enqueue", lambda **kw: self.enqueued.append(kw) or {"job": "J9"}):
            self.push.void("ERP Stock Entry", "SE-1")
            self.push.void("ERP Stock Entry", "SE-1")  # 已排过：不再排
        self.assertEqual(len(self.enqueued), 1)
        self.assertTrue(self.enqueued[0]["request_key"].startswith(stock_entry.VOID_REQUEST))
        self.assertEqual((self.docs["SE-1"]["express_void_state"], self.docs["SE-1"]["express_void_job"]), ("queued", "J9"))
        self.push.job_changed({"source_doctype": "ERP Stock Entry", "source_name": "SE-1", "status": "success", "job": "J9",
                               "request_key": self.enqueued[0]["request_key"], "docnum": "OU691025-001"})
        self.assertEqual(self.docs["SE-1"]["express_void_state"], "voided")
        self.assertEqual((self.docs["SE-1"]["express_state"], self.docs["SE-1"]["express_docnum"]), ("written", "OU691025-001"))
        self.push.job_changed({"source_doctype": "ERP Stock Entry", "source_name": "SE-1", "status": "manual", "job": "J9",
                               "request_key": self.enqueued[0]["request_key"], "reason": "[VOID_DOCUMENT_CHANGED] x"})
        self.assertEqual((self.docs["SE-1"]["express_void_state"], self.docs["SE-1"]["express_void_reason"]),
                         ("held", "[VOID_DOCUMENT_CHANGED] x"))

    def test_voiding_before_the_computer_wrote_it_cancels_the_queued_job(self):
        from unittest import mock
        self.written(express_state="queued", express_docnum="", express_job="J1")
        cancelled = []
        with mock.patch.object(self.push.jobs, "find_by_name", lambda name: {"job": name, "status": "queued"}), \
                mock.patch.object(self.push.jobs, "cancel", lambda name, reason: cancelled.append((name, reason))):
            self.push.void("ERP Stock Entry", "SE-1")
        self.assertEqual(cancelled, [("J1", "voided")])
        self.assertEqual((self.docs["SE-1"]["express_state"], self.docs["SE-1"]["express_job"]), (None, None))

    def test_a_write_that_lands_after_the_void_is_voided_at_once(self):
        """电脑正在写（领走了撤不了）时 ERP 作废：写完的回执一到就排作废原单。"""
        from unittest import mock
        self.written(express_state="queued", express_docnum="", express_job="J1", status="void")
        calls = []
        with mock.patch.object(self.push, "void", lambda doctype, name: calls.append(name)), \
                mock.patch.object(self.push, "company_of", lambda doctype, name: None):
            self.push.job_changed({"source_doctype": "ERP Stock Entry", "source_name": "SE-1", "status": "success",
                                   "job": "J1", "request_key": "erp:stock:SE-1@x", "docnum": "OU691025-002"})
        self.assertEqual(calls, ["SE-1"])
        self.assertEqual(self.docs["SE-1"]["express_docnum"], "OU691025-002")

    def test_an_entry_never_written_is_only_voided_in_erp(self):
        from unittest import mock
        self.written(express_state="held", express_docnum="")
        with mock.patch.object(self.push.jobs, "enqueue", lambda **kw: self.enqueued.append(kw) or {"job": "J9"}):
            self.push.void("ERP Stock Entry", "SE-1")
        self.assertEqual(self.enqueued, [])
        self.assertFalse(self.docs["SE-1"].get("express_void_state"))

    def test_an_entry_from_a_count_sent_to_express_cannot_be_voided_on_its_own(self):
        """盘点整份轧差写一张 TK，它生成的单不逐张写：盘点进了写 Express 就不让单独作废（验收 10-25），没进的照常作废。"""
        from unittest import mock
        import types
        utils = sys.modules["frappe.utils"]
        if not hasattr(utils, "now"):
            utils.now = lambda: "2026-10-25 10:00:00"
        from pearnly_erp_portal.inventory import documents
        entry = types.SimpleNamespace(name="AD-1", status="submitted", direction="out", ref_type="count", ref_id="STC-1",
                                      warehouse="W1")
        reversed_ = []
        for count_state, blocked in (("written", True), ("queued", True), (None, False)):
            with mock.patch.object(documents.guard, "require", lambda *a, **k: None), \
                    mock.patch.object(documents.frappe.db, "sql", lambda *a, **k: None, create=True), \
                    mock.patch.object(documents, "_get", lambda name: entry), \
                    mock.patch.object(documents.frappe.db, "get_value", lambda doctype, name, field: count_state, create=True), \
                    mock.patch.object(documents.stock, "reverse_movements", lambda **kw: reversed_.append(kw["ref_id"])), \
                    mock.patch.object(documents.warehouses, "company_of", lambda w: "CO-1"), \
                    mock.patch.object(documents.access, "write_entry", lambda doc, action: None), \
                    mock.patch.object(self.push, "void", lambda doctype, name: None), \
                    mock.patch.object(documents.frappe.db, "commit", lambda: None, create=True):
                entry.status = "submitted"
                if blocked:
                    with self.assertRaises(Exception) as raised:
                        documents.void_entry("AD-1")
                    self.assertIn("stock.countEntryVoid", str(raised.exception))
                else:
                    documents.void_entry("AD-1")
        self.assertEqual(reversed_, ["AD-1"])


class PosDaySend(PushModule):
    """同一天的退货冲减后（主会话 10-25）：一天全退什么都不写；冲成负数停下交人，原因 ``express.pos_net_negative``。"""

    def test_an_empty_day_writes_nothing_and_a_negative_day_is_held(self):
        from unittest import mock
        docs = fake.STATE.db.table("ERP POS Day Summary")
        for body, state, reason in (({"ref": "x", "items": [], "empty": True}, None, ""),
                                    ({"ref": "x", "items": [], "net_negative": True}, "held", "express.pos_net_negative")):
            docs["PDS-1"] = {"name": "PDS-1", "creation": "2026-10-25 01:20:00", "status": "submitted", "owner": "a@x",
                             "express_state": "queued", "express_job": None, "express_reason": ""}
            with mock.patch.object(self.push, "document", lambda doctype, name, body=body: body), \
                    mock.patch.object(self.push, "company_of", lambda doctype, name: "CO-1"), \
                    mock.patch.object(self.push.prepare, "prepare", side_effect=AssertionError("must not prepare")), \
                    mock.patch.object(frappe.db, "commit", lambda: None, create=True):
                self.push.send("ERP POS Day Summary", "PDS-1")
            self.assertEqual((docs["PDS-1"]["express_state"], docs["PDS-1"]["express_reason"]), (state, reason))

    def test_vat_fields_ride_on_the_payload_with_version_nine(self):
        from unittest import mock
        docs = fake.STATE.db.table("ERP POS Day Summary")
        docs["PDS-1"] = {"name": "PDS-1", "creation": "2026-10-25 01:20:00", "status": "submitted", "owner": "a@x",
                         "express_state": "queued", "express_job": None, "express_reason": ""}
        body = {"ref": "PDS-1@x", "number": "SI-1-3", "items": [{"code": "A"}], "vat_fields": {"AMT01": "100.00"}}
        seen, queued = [], []

        def prepared(doc, company, ensure):
            seen.append(dict(doc))
            return {"payload": {"payload_version": 4, "doctype": "HS"}, "book": {"key": "BK"}}

        with mock.patch.object(self.push, "document", lambda doctype, name: dict(body)), \
                mock.patch.object(self.push, "company_of", lambda doctype, name: "CO-1"), \
                mock.patch.object(self.push, "_company", lambda name: COMPANY), \
                mock.patch.object(self.push.prepare, "prepare", side_effect=prepared), \
                mock.patch.object(self.push, "actor_of", lambda doctype, row, book: "a@x"), \
                mock.patch.object(self.push.jobs, "of_source", lambda doctype, name: []), \
                mock.patch.object(self.push.jobs, "enqueue", lambda **kw: queued.append(kw) or {"job": "J1"}), \
                mock.patch.object(frappe.db, "commit", lambda: None, create=True):
            self.push.send("ERP POS Day Summary", "PDS-1")
        self.assertNotIn("vat_fields", seen[0])  # 不交给共用写账规则，准备好后才放进载荷
        self.assertEqual((queued[0]["payload"]["vat_fields"], queued[0]["payload_version"]), ({"AMT01": "100.00"}, 9))

class PosDayExits(PushModule):
    """B3b 验收复核（主会话 10-26）：收银日汇总 / 退货单停住（定不了、跨年、原单是手工处理的、电脑转人工）不能永远卡着：
    「再写一次」按此刻内容重新准备，「已在 Express 手工处理」标 handled、撤掉挂着的写账件；没停住的单不给这两个出口。"""

    def run_exit(self, action, row, job=None, reason="ปิดใน Express แล้ว"):
        import types
        from unittest import mock

        utils = sys.modules["frappe.utils"]
        for name, value in (("now", lambda: "2026-10-24 00:00:00"), ("flt", lambda v, *a: float(v or 0)),
                            ("cint", lambda v: int(v or 0)), ("getdate", lambda v=None: v), ("add_days", lambda d, n: d)):
            setattr(utils, name, getattr(utils, name, value))
        from pearnly_erp_portal.express import api
        writes, cancelled, sent, comments = {}, [], [], []
        self.comments = comments
        doc = types.SimpleNamespace(name="PDS-1", express_job="J1" if job else None, express_void_state=None,
                                    get=lambda key: None, **row)
        with mock.patch.object(api, "_document", lambda doctype, name, write=False: doc), \
                mock.patch.object(api, "_view", lambda doctype, row: "view"), \
                mock.patch.object(api.jobs, "find_by_name", lambda name: job), \
                mock.patch.object(api.jobs, "cancel", lambda name, reason: cancelled.append((name, reason))), \
                mock.patch.object(api.push, "send", lambda doctype, name: sent.append(name)), \
                mock.patch.object(frappe.db, "set_value", lambda doctype, name, values, update_modified=False:
                                  writes.update(values), create=True), \
                mock.patch.object(frappe, "get_doc", lambda doctype, name: types.SimpleNamespace(
                    add_comment=lambda kind, text: comments.append((kind, text))), create=True), \
                mock.patch.object(frappe.db, "commit", lambda: None, create=True):
            getattr(api, action)("ERP POS Day Summary", "PDS-1", **({"reason": reason} if action == "mark_handled" else {}))
        return writes, cancelled, sent

    def test_a_held_summary_can_be_retried_or_marked_handled(self):
        writes, cancelled, sent = self.run_exit("retry", {"express_state": "held"})
        self.assertEqual((writes["express_state"], sent), ("queued", ["PDS-1"]))  # 按此刻内容重新准备
        writes, cancelled, sent = self.run_exit("mark_handled", {"express_state": "held"})
        self.assertEqual((writes["express_state"], writes["express_job"], cancelled, sent), ("handled", None, [], []))
        self.assertEqual(self.comments, [("Comment", "express.state.handled: ปิดใน Express แล้ว")])  # 时间线留一条：谁、何时、原因
        with self.assertRaises(Exception):
            self.run_exit("mark_handled", {"express_state": "held"}, reason="  ")  # 不写原因不能标
        cancelled_job = {"job": "J0", "status": "cancelled", "reason": "replaced"}
        writes, _cancelled, sent = self.run_exit("retry", {"express_state": "failed"}, cancelled_job)
        self.assertEqual((writes["express_state"], sent), ("queued", ["PDS-1"]))  # 写账件已撤销的失败单也能再写一次
        job = {"job": "J1", "status": "manual", "reason": "x"}
        writes, cancelled, _sent = self.run_exit("mark_handled", {"express_state": "queued"}, job)  # 电脑转了人工
        self.assertEqual((writes["express_state"], cancelled), ("handled", [("J1", "replaced")]))

    def test_a_summary_that_is_not_stuck_gets_no_exit(self):
        for row, job in (({"express_state": "written"}, None), ({"express_state": "queued"}, {"job": "J1", "status": "leased"})):
            with self.assertRaises(Exception):
                self.run_exit("mark_handled", row, job)


class PosDayActor(PushModule):
    """收银日汇总每晚由系统合（建单人 Administrator 名下没有电脑）：作业归这家公司里电脑够得到账套的员工，否则一直领不走（10-25 实验栈）。"""

    def test_a_system_made_day_summary_goes_to_a_member_whose_computer_reaches_the_book(self):
        import types
        from unittest import mock
        row = types.SimpleNamespace(name="PDS-1", owner="Administrator")
        peers = {"PC-B": types.SimpleNamespace(users=frozenset({"zed@x", "bob@x"}), online=False),
                 "PC-A": types.SimpleNamespace(users=frozenset({"amy@x"}), online=True),
                 "PC-C": types.SimpleNamespace(users=frozenset({"cat@x"}), online=True)}
        with mock.patch.object(self.push.jobs, "writers", lambda actor, book: {"state": "missing", "devices": []}), \
                mock.patch.object(self.push, "company_of", lambda doctype, name: "CO-1"), \
                mock.patch.object(self.push.devices, "peers", lambda: peers), \
                mock.patch.object(self.push.books, "reachers", lambda keys: {"BK": ["PC-B", "PC-A"]}), \
                mock.patch.object(self.push.devices, "issuer_valid", lambda user: True), \
                mock.patch.object(self.push.companies, "allowed_companies", lambda user: ["CO-1"] if user != "amy@x" else []):
            # 在线的 PC-A 上的 amy 没有这家公司 → 离线的 PC-B 上按名字排的 bob；PC-C 够不到账套不算
            self.assertEqual(self.push.actor_of("ERP POS Day Summary", row, "BK"), "bob@x")
            self.assertEqual(self.push.actor_of("ERP Stock Entry", row, "BK"), "Administrator")  # 人开的单归开单人
        with mock.patch.object(self.push.jobs, "writers", lambda actor, book: {"state": "ok", "devices": ["PC-A"]}):
            self.assertEqual(self.push.actor_of("ERP POS Day Summary", types.SimpleNamespace(name="PDS-2", owner="amy@x"),
                                                "BK"), "amy@x")

class LinkParty(PushModule):
    """写进 Express 后把单上随手建的对方档挂上电脑实际用的码（10-24 验收：同步回来又多一条、还对各家公司都可见）。"""

    def setUp(self):
        super().setUp()
        self.docs = fake.STATE.db.table("ERP Sales Document")
        self.parties = fake.STATE.db.table("ERP Customer")

    def test_a_new_customer_takes_the_code_the_computer_used(self):
        self.parties["CUS-1"] = {"name": "CUS-1", "customer_name": "ประกิจ", "express_company": "CO-1", "is_active": 1}
        self.docs["SD-1"] = {"name": "SD-1", "customer": "CUS-1", "company": "CO-1"}
        self.push.link_party("ERP Sales Document", "SD-1", "CO-1", "ป001")
        self.assertEqual((self.parties["CUS-1"]["express_company"], self.parties["CUS-1"]["express_code"]), ("CO-1", "ป001"))

    def test_a_chosen_existing_record_takes_over_and_the_throwaway_is_retired(self):
        self.parties["CUS-1"] = {"name": "CUS-1", "customer_name": "สันติชัย.", "express_company": "CO-1", "is_active": 1}
        self.parties["CUS-9"] = {"name": "CUS-9", "customer_name": "สันติชัย", "express_company": "CO-1",
                                 "express_code": "CPMMMM338", "is_active": 1}
        self.docs["SD-1"] = {"name": "SD-1", "customer": "CUS-1", "company": "CO-1"}
        self.push.link_party("ERP Sales Document", "SD-1", "CO-1", "CPMMMM338")
        self.assertEqual((self.docs["SD-1"]["customer"], self.parties["CUS-1"]["is_active"]), ("CUS-9", 0))

    def test_a_local_record_other_companies_use_stays_shared(self):
        self.parties["CUS-1"] = {"name": "CUS-1", "customer_name": "เงินสด", "is_active": 1}
        self.docs["SD-1"] = {"name": "SD-1", "customer": "CUS-1", "company": "CO-1"}
        self.docs["SD-2"] = {"name": "SD-2", "customer": "CUS-1", "company": "CO-2"}
        self.push.link_party("ERP Sales Document", "SD-1", "CO-1", "C01")
        self.assertFalse(self.parties["CUS-1"].get("express_code"))

class LinkProducts(PushModule):
    """写进 Express 后把随单自动建的本地商品挂上电脑实际用的存货码（10-24：几家公司的租户同步按新码又建一条同名档）。"""

    def setUp(self):
        super().setUp()
        self.products = fake.STATE.db.table("ERP Product")
        self.lines = fake.STATE.db.table("Purchase Line")
        self.products["PRD-1"] = {"name": "PRD-1", "product_name": "น้ำแข็งก้อน", "is_active": 1}
        self.lines["L1"] = {"name": "L1", "parent": "PUR-1", "parenttype": "Purchase Document", "idx": 1,
                            "product": None, "description": "ค่าขนส่ง"}
        self.lines["L2"] = {"name": "L2", "parent": "PUR-1", "parenttype": "Purchase Document", "idx": 2,
                            "product": "PRD-1", "description": "น้ำแข็งก้อน"}

    def link(self, codes):
        self.push.link_products("Purchase Document", "PUR-1", "CO-1", codes)
        return self.products["PRD-1"].get("express_company"), self.products["PRD-1"].get("express_code")

    def test_the_new_local_product_takes_the_code_the_computer_made(self):
        self.assertEqual(self.link([{"seq": 1, "name": "ค่าขนส่ง", "code": "5330-02"},
                                    {"seq": 2, "name": "น้ำแข็งก้อน", "code": "ST-0099"}]), ("CO-1", "ST-0099"))

    def test_a_product_used_elsewhere_or_a_code_already_mapped_stays_as_it_is(self):
        self.lines["L9"] = {"name": "L9", "parent": "PUR-2", "parenttype": "Purchase Document", "idx": 1,
                            "product": "PRD-1", "description": "น้ำแข็งก้อน"}
        self.assertEqual(self.link([{"seq": 2, "name": "น้ำแข็งก้อน", "code": "ST-0099"}]), (None, None))
        self.lines.pop("L9")
        self.products["PRD-9"] = {"name": "PRD-9", "product_name": "น้ำแข็ง", "express_company": "CO-1",
                                  "express_code": "ST-0099", "is_active": 1}
        self.assertEqual(self.link([{"seq": 2, "name": "น้ำแข็งก้อน", "code": "ST-0099"}]), (None, None))

    def test_a_line_that_does_not_line_up_is_left_alone(self):
        self.assertEqual(self.link([{"seq": 2, "name": "อื่น", "code": "ST-0099"}]), (None, None))
        self.assertEqual(self.link([{"seq": 3, "name": "น้ำแข็งก้อน", "code": "ST-0099"}]), (None, None))
        self.assertEqual(self.link(None), (None, None))


class PurchaseFromOwnCompany(unittest.TestCase):
    """采购页上传本公司开出的单据拒收（用户 10-24「采购页上传收据拒收」）：卖方税号 = 本公司、分店相同或没印分店。"""

    def test_a_document_sold_by_this_company_is_not_a_purchase(self):
        from pearnly_erp_portal.recognition import checks
        ours = {"supplier_tax_id": "0105549038811", "supplier_branch_type": "branch", "supplier_branch_no": "1",
                "buyer_tax_id": "0105536080112"}
        self.assertTrue(checks.issued_by(ours, OWN_TAX))  # 本公司 00001 分店开给第三方：仍是本公司开出的（10-24 验收）
        self.assertTrue(checks.issued_by({**ours, "buyer_tax_id": ""}, OWN_TAX))  # 买方没税号的收据
        self.assertFalse(checks.issued_by({**ours, "buyer_tax_id": OWN_TAX}, OWN_TAX))  # 分店之间调拨：照常是进项
        self.assertFalse(checks.issued_by({**ours, "supplier_tax_id": SUPPLIER}, OWN_TAX))
        self.assertFalse(checks.issued_by(ours, ""))  # 公司没税号：判不出，不拒



class ServiceLinesTakeNoWarehouse(unittest.TestCase):
    """10-24 验收问题 22：多仓、没有收银台的公司开费用单，关联了服务物料的行提交报「请选择仓库」，表单上又没有仓库栏。
    服务 / 费用行不进出库，不取仓；货物行照旧按收银台 → 唯一仓 → 报错定仓。"""

    def test_a_service_line_with_a_product_needs_no_warehouse(self):
        from types import SimpleNamespace
        from unittest import mock

        from pearnly_erp_portal.inventory import positions

        product = SimpleNamespace(is_active=1, default_location="")
        with mock.patch.object(positions.frappe.db, "get_value", return_value=product), \
                mock.patch.object(positions.warehouses, "receiving", side_effect=AssertionError("asked for a warehouse")):
            line = positions.prepare({"product": "PRD-68362", "item_type": "service", "qty": 1}, company="CO-42069")
        self.assertNotIn("warehouse", line)
        self.assertEqual(positions.saved({"product": "PRD-68362", "item_type": "service"}, company="CO-42069"),
                         {"warehouse": None, "location": ""})
        with mock.patch.object(positions.frappe.db, "get_value", return_value=product), \
                mock.patch.object(positions.warehouses, "receiving", return_value="WH-68368") as receiving:
            goods = positions.prepare({"product": "PRD-1", "item_type": "goods", "qty": 1}, company="CO-42069")
        self.assertEqual(goods["warehouse"], "WH-68368")
        receiving.assert_called_once()




class StockAlignment(MasterSync):
    """出入库第一期（10-25）：对齐目标算全部还没进 Express 的 ERP 变动；调整写回原库位；Express 自己对不上的存货不抹、进差异清单。"""

    def test_pending_counts_every_erp_move_not_yet_in_express(self):
        sync = self.sync
        sync.stock.MOVEMENT = "ERP Stock Movement"
        moves = [
            # (ref_type, ref_id, product, warehouse, qty)
            ("purchase", "PUR-1", "P1", "W1", 10),          # 采购单已写进 Express：不算
            ("purchase", "PUR-2", "P1", "W1", 4),           # 采购单还没写：算
            ("ERP Sales Document", "SD-1", "P1", "W1", -3),  # 销售记录还没写：算
            ("sale", "RCPT-1", "P1", "W1", -2),             # 收银小票还没进已写入的日汇总：算
            ("sale", "RCPT-9", "P1", "W1", -5),             # 收银小票在已写进 Express 的日汇总里：不算（B3a）
            ("refund", "RCPT-2", "P1", "W1", 1),
            ("stock_entry", "SE-1", "P1", "W2", 7),          # 出入库单还没写进 Express（B 段之前开的、没写成的）：算
            ("stock_entry", "SE-2", "P1", "W2", -4),         # 出入库单已写进 Express：不算
            ("stock_entry", "SE-3", "P1", "W2", -6),         # 已写进去、又在 Express 作废完的出入库单：原单不算
            ("void", "SE-3", "P1", "W2", 6),                 # 它的冲销 Express 里也有了：不算（验收 10-25）
            ("stock_entry", "SE-4", "P1", "W2", -2),         # 已写进去、作废还在排队：原单不算
            ("void", "SE-4", "P1", "W2", 2),                 # 冲销 Express 里还没有：算
            ("count", "SC-2", "P1", "W1", 2),                # 盘点生成的单，所在盘点已整份写进 Express：不算
            ("count", "SC-1", "P1", "W2", -1),               # 盘点调整：算
            ("void", "PUR-1", "P1", "W1", -10),              # 写进去之后作废的冲销：ERP 独有，算
            (None, None, "P1", "W2", 5),                     # 没有来源：算
            ("express_sync", None, "P1", "W1", 99),          # 同步调整本身：不算
            ("opening", "OP-1", "P1", "W1", 50),             # 期初：不算
            ("sale", "RCPT-3", "P2", "W1", -6),              # 不是这家公司 Express 来的品：不管
        ]
        open_docs = {"Purchase Document": ["PUR-2"], "ERP Sales Document": ["SD-1"], "ERP Stock Entry": ["SE-2", "SE-3", "SE-4"],
                     "ERP Stock Count": ["STC-2"]}

        def get_all(doctype, filters=None, fields=None, pluck=None, group_by=None, **kw):
            if doctype == "ERP Stock Entry" and isinstance(filters, dict) and filters.get("ref_type") == "count":
                self.assertEqual(filters["ref_id"], ["in", ["STC-2"]])
                return ["SC-2"]  # 已写进 Express 的那份盘点生成的单
            if doctype == "ERP Stock Entry" and isinstance(filters, dict) and "express_void_state" in filters:
                self.assertEqual(filters["express_void_state"], "voided")
                return ["SE-3"]  # 在 Express 作废完的单
            if doctype in open_docs:
                if doctype in ("ERP Stock Entry", "ERP Stock Count"):
                    self.assertEqual(filters["express_state"], ["in", ("written", "mismatch", "handled")])
                return list(open_docs[doctype])
            rows = [dict(zip(("ref_type", "ref_id", "product", "warehouse", "qty"), m)) for m in moves]
            for field, op, value in filters:
                rows = [r for r in rows if (r[field] in value if op == "in" else r[field] == value)]
            keys = [part.strip() for part in group_by.split(",")]
            out = {}
            for r in rows:
                k = tuple(r[f] for f in keys)
                out[k] = out.get(k, 0) + r["qty"]
            import types
            return [types.SimpleNamespace(**dict(zip(keys, k)), qty=v) for k, v in out.items()]

        import types
        summarized = [types.SimpleNamespace(product="P1", warehouse="W1", qty=-5)]  # RCPT-9 的流水（日汇总已写入）

        def sql(query, values=None, as_dict=False):
            self.assertIn("tabERP POS Day Summary", query)
            self.assertEqual(values["written"], ("written", "mismatch", "handled"))
            return summarized

        with mock.patch.object(sync.frappe, "get_all", side_effect=get_all), \
                mock.patch.object(sync.frappe.db, "sql", side_effect=sql):
            pending = sync._pending("CO-1", {"ICE-01": "P1"}, {"01": "W1", "02": "W2"})
        self.assertEqual(pending, {("ICE-01", "01"): Decimal(4 - 3 - 2 + 1 - 10), ("ICE-01", "02"): Decimal(7 - 1 + 5 + 2)})

    def test_express_own_mismatch_is_listed_and_not_aligned(self):
        sync = self.sync
        products = [{"code": "ICE-01", "name": "น้ำแข็ง", "kind": "stock", "balance": "12"},
                    {"code": "ICE-02", "name": "น้ำแข็งบด", "kind": "stock", "balance": "5"},
                    {"code": "FEE", "name": "ค่าส่ง", "kind": "non_stock", "balance": "3"},
                    {"code": "OLD", "name": "รุ่นเก่า", "kind": "stock"}]  # 旧版 CC 桌面版不交 balance：不核
        stock = [{"code": "ICE-01", "loccod": "01", "qty": "7"}, {"code": "ICE-01", "loccod": "02", "qty": "5"},
                 {"code": "ICE-02", "loccod": "01", "qty": "3"}, {"code": "OLD", "loccod": "01", "qty": "9"}]
        diffs = sync.stock_differences(products, stock)
        self.assertEqual(diffs, {"ICE-02": (Decimal(3), Decimal(5))})
        self.assertEqual(json.loads(sync._differences_json(products, diffs)),
                         [{"code": "ICE-02", "name": "น้ำแข็งบด", "locations": "3", "balance": "5"}])
        self.assertIsNone(sync._differences_json(products, {}))

    def test_adjustments_go_back_to_the_goods_own_place(self):
        place = self.sync._place
        present = {("P1", "W1"): {"A-01"}, ("P2", "W1"): {"A-01", "B-02"}}
        self.assertEqual(place("P1", "W1", "SHELF-9", present), "SHELF-9")  # 默认库位优先
        self.assertEqual(place("P1", "W1", "", present), "A-01")             # 这个仓里只有一个库位
        self.assertEqual(place("P2", "W1", "", present), "")                # 几个库位又没设默认：空库位
        self.assertEqual(place("P3", "W1", "", present), "")


class StockEntries(Base):
    """出入库第一期 B 段：出入库单 / 盘点调整 → Express 库存单（载荷 6，CC 桌面版 dbf_stock）。"""

    def setUp(self):
        super().setUp()
        import types
        utils = types.ModuleType("frappe.utils")
        utils.now_datetime = utils.nowdate = lambda: "2026-10-24"
        import datetime as dt
        utils.getdate = lambda value: dt.date.fromisoformat(str(value)[:10])
        patcher = mock.patch.dict(sys.modules, {"frappe.utils": utils})
        patcher.start()
        self.addCleanup(patcher.stop)
        frappe.utils = utils
        from pearnly_erp_portal.express import stock_entry
        self.stock = stock_entry

    def body(self, **override):
        body = {"ref": "SE-0001@20261020101010000000", "direction": "stock", "doctype": "OU", "date": "2026-10-20",
                "number": "IS2569-000001", "remark": "เบิกใช้ในสำนักงาน", "loccod": "01",
                "lines": [{"code": "ICE-01", "name": "น้ำแข็งแพ็ค", "qty": "2", "unit": "ถุง", "loccod": "02"}]}
        body.update(override)
        return body

    def test_kinds_and_what_is_not_written(self):
        kind, eligible = self.stock.kind, self.stock.eligible
        self.assertEqual(kind({"direction": "out"}), "OU")
        self.assertEqual(kind({"direction": "in"}), "JU")
        self.assertEqual(kind({"direction": "out", "ref_type": "count"}), "TK")
        self.assertTrue(eligible({"status": "submitted", "ref_type": None}))
        self.assertFalse(eligible({"status": "submitted", "ref_type": "purchase"}))  # 采购自动入库：采购单已写进去
        self.assertFalse(eligible({"status": "draft"}))
        self.assertEqual(self.stock.be_date("2026-10-20"), "691020")

    def test_a_ready_payload_names_the_book_the_lines_and_the_version(self):
        key = self.computer([book()])[0]
        self.masters(key)
        result = self.stock.prepare(self.body(), COMPANY, lambda company, chosen: self.reads.append(chosen["key"]))
        payload = result["payload"]
        self.assertEqual((payload["payload_version"], payload["direction"], payload["doctype"]), (6, "stock", "OU"))
        self.assertEqual((payload["docdate_be"], payload["ref_no"], payload["remark"]), ("691020", "IS2569-000001",
                                                                                         "เบิกใช้ในสำนักงาน"))
        self.assertEqual(payload["account_dir"], PATH)
        self.assertEqual(payload["account_set"], PATH)
        self.assertEqual(payload["items"], self.body()["lines"])
        self.assertTrue(payload["respect_period_lock"])
        self.assertEqual(result["book"]["key"], key)

    def test_holds_until_the_masters_are_read_and_asks_once_between_two_books(self):
        key = self.computer([book()])[0]
        result = self.stock.prepare(self.body(), COMPANY, lambda company, chosen: self.reads.append(chosen["key"]))
        self.assertEqual(result["hold"], E.READING)
        self.assertEqual(self.reads, [key])
        self.computer([book(), book(path=r"S:\2569\EXP69\69NIEX12", uid="vsn:9/9")])
        result = self.stock.prepare(self.body(), COMPANY, lambda company, chosen: None)
        self.assertEqual(result["hold"], E.CHOICE + ":" + choices.ACCOUNT_SET)

    def test_document_signs_count_issues_and_passes_the_cost_of_increases(self):
        import types
        lines = [types.SimpleNamespace(product="P1", qty=3, unit="ถุง", unit_cost=0, warehouse=None),
                 types.SimpleNamespace(product="P1", qty=1, unit="ถุง", unit_cost=12, warehouse="W2")]
        Doc = type("Doc", (dict,), {"__getattr__": dict.get, "__setattr__": dict.__setitem__})
        doc = Doc(direction="out", ref_type="count", lines=lines, warehouse="W1", posting_date="2026-10-20",
                  doc_no="AD2569-000001", name="SE-1", reason="")
        values = {("ERP Product", "P1"): {"product_name": "น้ำแข็ง", "express_code": "ICE-01", "base_unit": "ถุง"},
                  ("ERP Warehouse", "W1"): "01", ("ERP Warehouse", "W2"): "02"}
        with mock.patch.object(frappe, "get_doc", lambda doctype, name: doc, create=True), \
                mock.patch.object(frappe.db, "get_value", lambda doctype, name, fields=None, as_dict=False:
                                  values.get((doctype, name))):
            body = self.stock.document("SE-1", "SE-1@x")
        self.assertEqual(body["doctype"], "TK")
        self.assertEqual([(line["qty"], line["loccod"], line.get("unit_cost")) for line in body["lines"]],
                         [("-3", "01", None), ("-1", "02", None)])  # 盘点出库：数量记负，减少的不带成本
        doc.direction, doc.ref_type = "in", None
        with mock.patch.object(frappe, "get_doc", lambda doctype, name: doc, create=True), \
                mock.patch.object(frappe.db, "get_value", lambda doctype, name, fields=None, as_dict=False:
                                  values.get((doctype, name))):
            body = self.stock.document("SE-1", "SE-1@x")
        self.assertEqual(body["doctype"], "JU")
        self.assertEqual([(line["qty"], line.get("unit_cost")) for line in body["lines"]], [("3", None), ("1", "12")])

    def test_a_stock_entry_with_a_pack_unit_needs_payload_seven(self):
        """验收 10-25：出入库单自己拼载荷，包装单位的行带 factor 时原来仍盖 6，1.12.3 测试包会收下按 1 写。"""
        key = self.computer([book()])[0]
        self.masters(key)
        body = self.body()
        body["lines"][0].update(unit="ลัง", factor="12")
        payload = self.stock.prepare(body, COMPANY, lambda company, chosen: None)["payload"]
        self.assertEqual(payload["payload_version"], 7)
        self.assertEqual(self.stock.prepare(self.body(), COMPANY, lambda company, chosen: None)["payload"]["payload_version"], 6)

    def test_a_count_nets_its_entries_into_one_count_document(self):
        """验收 10-25：盘点按库位逐笔开单（−100、+60、+40）原来各写一张 TK；现在整份盘点按「存货 × Express 仓」轧差写一张，净 0 不写。"""
        import types
        Doc = type("Doc", (dict,), {"__getattr__": dict.get, "__setattr__": dict.__setitem__})

        def entry(name, direction, product, qty, location, cost=0):
            line = types.SimpleNamespace(product=product, qty=qty, unit="ถุง", unit_cost=cost, warehouse=None, location=location)
            return Doc(direction=direction, ref_type="count", lines=[line], warehouse="W1", posting_date="2026-10-25",
                       doc_no=name, name=name, reason="")

        docs = {"E1": entry("E1", "out", "P1", 100, "A"), "E2": entry("E2", "in", "P1", 60, "B", 10),
                "E3": entry("E3", "in", "P1", 40, "C", 10), "E4": entry("E4", "out", "P2", 2, "A"),
                "E5": entry("E5", "in", "P3", 3, "A", 8)}
        values = {("ERP Product", "P1"): {"product_name": "น้ำแข็ง", "express_code": "ICE-01", "base_unit": "ถุง"},
                  ("ERP Product", "P2"): {"product_name": "น้ำแข็งบด", "express_code": "ICE-02", "base_unit": "ถุง"},
                  ("ERP Product", "P3"): {"product_name": "ถ่าน", "express_code": "CH-01", "base_unit": "ถุง"},
                  ("ERP Warehouse", "W1"): "01",
                  ("ERP Stock Count", "STC-1"): {"title": "ตรวจนับ ต.ค.", "warehouse": "W1"}}
        rows = [types.SimpleNamespace(name=name, posting_date="2026-10-25") for name in docs]
        with mock.patch.object(frappe, "get_doc", lambda doctype, name: docs[name], create=True), \
                mock.patch.object(frappe, "get_all", lambda *a, **k: rows, create=True), \
                mock.patch.object(frappe.db, "get_value", lambda doctype, name, fields=None, as_dict=False:
                                  values.get((doctype, name))):
            body = self.stock.count_document("STC-1", "STC-1@x")
        self.assertEqual((body["doctype"], body["number"], body["remark"], body["loccod"]), ("TK", "STC-1", "ตรวจนับ ต.ค.", "01"))
        self.assertEqual(body["lines"], [
            {"code": "ICE-02", "name": "น้ำแข็งบด", "qty": "-2", "unit": "ถุง", "loccod": "01"},
            {"code": "CH-01", "name": "ถ่าน", "qty": "3", "unit": "ถุง", "loccod": "01"}])  # 盘盈不带 ERP 估值，按 Express 均价取

    def test_a_pack_unit_is_written_in_the_stock_unit(self):
        """验收 10-25：行上选「ลัง（= 12 ถุง）」时原来按 1 写进 Express；现在照行上单位交、带上 ERP 的换算率 12（CC 按 Express 主档的
        包装单位照原生记法写，认不出时按它换成库存单位）；盘点轧差用的库存单位口径是 36 ถุง、单位成本 10。"""
        import types
        lines = [types.SimpleNamespace(product="P1", qty=3, unit="ลัง", unit_cost=120, warehouse=None)]
        Doc = type("Doc", (dict,), {"__getattr__": dict.get, "__setattr__": dict.__setitem__})
        doc = Doc(direction="in", ref_type=None, lines=lines, warehouse="W1", posting_date="2026-10-20",
                  doc_no="IR2569-000001", name="SE-2", reason="")

        def value(doctype, name, fields=None, as_dict=False):
            if doctype == "ERP Product Unit":
                return {"ลัง": 12.0}.get(name["unit_name"])
            return {("ERP Product", "P1"): {"product_name": "น้ำแข็ง", "express_code": "ICE-01", "base_unit": "ถุง"},
                    ("ERP Warehouse", "W1"): "01"}.get((doctype, name))

        with mock.patch.object(frappe, "get_doc", lambda doctype, name: doc, create=True), \
                mock.patch.object(frappe.db, "get_value", value):
            body = self.stock.document("SE-2", "SE-2@x")
            base = self.stock.document("SE-2", "SE-2@x", base=True)
            lines[0].unit = "แพ็ค"
            with self.assertRaises(Exception):
                self.stock.document("SE-2", "SE-2@x")  # 商品单位表里没有的单位：同 ERP 流水，报错不猜
        self.assertEqual(body["lines"], [{"code": "ICE-01", "name": "น้ำแข็ง", "qty": "3", "unit": "ลัง", "loccod": "01",
                                          "factor": "12", "unit_cost": "120"}])
        self.assertEqual(base["lines"], [{"code": "ICE-01", "name": "น้ำแข็ง", "qty": "36", "unit": "ถุง", "loccod": "01",
                                          "unit_cost": "10"}])


    def test_a_void_names_the_written_book_the_docnum_and_the_codes_the_computer_used(self):
        """B 段 ①：作废已写进 Express 的出入库单 → 载荷 8（照 Express 原生作废原单），账套就是写入时那份，逐行带电脑实际用的码。"""
        import types
        from pearnly_core.cc_desktop import jobs

        key = self.computer([book()])[0]
        lines = [types.SimpleNamespace(product="P1", qty=2, unit="ถุง", unit_cost=0, warehouse=None)]
        Doc = type("Doc", (dict,), {"__getattr__": dict.get, "__setattr__": dict.__setitem__})
        doc = Doc(direction="out", ref_type=None, lines=lines, warehouse="W1", posting_date="2026-10-20",
                  doc_no="IS2569-000001", name="SE-1", reason="", express_docnum="OU691020-001", express_book=key,
                  express_job="J1")
        values = {("ERP Product", "P1"): {"product_name": "น้ำแข็ง", "express_code": "", "base_unit": "ถุง"},
                  ("ERP Warehouse", "W1"): "01"}
        with mock.patch.object(frappe, "get_doc", lambda doctype, name: doc, create=True), \
                mock.patch.object(frappe.db, "get_value", lambda doctype, name, fields=None, as_dict=False:
                                  values.get((doctype, name))), \
                mock.patch.object(jobs, "find_by_name", lambda name: {"line_codes": [{"seq": 1, "code": "ICE-01"}]}):
            result = self.stock.void_payload("SE-1", COMPANY)
            doc.express_book = "another-book"
            moved = self.stock.void_payload("SE-1", COMPANY)
        payload = result["payload"]
        self.assertEqual((payload["payload_version"], payload["action"], payload["doctype"], payload["docnum"]),
                         (8, "void", "OU", "OU691020-001"))
        self.assertEqual(payload["items"], [{"seq": 1, "code": "ICE-01", "loccod": "01", "qty": "2"}])
        self.assertEqual(result["book"]["key"], key)
        self.assertTrue(payload["respect_period_lock"])
        self.assertEqual(moved, {"hold": E.NO_BOOK})  # 写入时那份账套不在了：不另找一份作废

    def masters_with_books(self, key, stock_books):
        self.masters(key, facts={"accounts": ACCOUNTS, "mapping": MAPPING, "catalog": {"isacc_candidates": []},
                                 "stock_books": stock_books})

    def test_a_default_account_that_cannot_be_posted_is_chosen_once_per_company(self):
        """10-25 离线回归：6 本账套（ASIAFC / TUCL）ISRUN 的默认科目不在科目表里 → 停下让人选一次科目，按公司记住。"""
        key = self.computer([book()])[0]
        self.masters_with_books(key, {"OU": "5320-03", "JU": "", "TK": "52-08-00-00"})
        ensure = lambda company, chosen: None  # noqa: E731
        for doctype in ("OU", "JU"):  # 不在科目表里 / 没有默认科目
            result = self.stock.prepare(self.body(doctype=doctype), COMPANY, ensure)
            self.assertEqual(result["hold"], E.CHOICE + ":account")
            self.assertEqual(result["needed"][0]["subject"], f"stock:{doctype}")
        result = self.stock.prepare(self.body(doctype="TK"), COMPANY, ensure)  # 默认科目能记账：不问、不带科目
        self.assertNotIn("account", result["payload"])
        choices.remember(COMPANY["name"], "account", "stock:OU", "52-08-00-00", "")
        result = self.stock.prepare(self.body(doctype="OU"), COMPANY, ensure)
        self.assertEqual(result["payload"]["account"], "52-08-00-00")

    def test_an_older_cc_desktop_without_stock_books_is_not_asked(self):
        key = self.computer([book()])[0]
        self.masters(key)
        result = self.stock.prepare(self.body(), COMPANY, lambda company, chosen: None)
        self.assertIn("payload", result)
        self.assertNotIn("account", result["payload"])



class PosDaySummaries(Base):
    """出入库第一期 B3a：收银小票按天合成现销日汇总（载荷 3 的 POS 日汇总，共用约定 `express_rules.contracts.pos`）。"""

    def setUp(self):
        super().setUp()
        from pearnly_erp_portal.express import pos_day
        self.pos_day = pos_day

    def day_doc(self, payments):
        body = doc(direction="sales", paid=True, number="R0001-R0003",
                   party={"code": "C01", "name": "ร้านป้าแดง", "tax_id": "", "branch": "00000"})
        body["pos"] = {"first": "R0001", "last": "R0003", "count": 3, "payments": payments}
        return body

    def test_payments_take_the_change_off_cash_and_add_up_to_the_total(self):
        D = Decimal
        self.assertEqual(self.pos_day.payments_of(D("1070"), {"promptpay": D("300"), "card": D("0")}),
                         [("เงินสด", D("770")), ("พร้อมเพย์", D("300"))])
        self.assertEqual(self.pos_day.payments_of(D("500"), {}), [("เงินสด", D("500"))])
        self.assertEqual(self.pos_day.payments_of(D("200"), {"transfer": D("200")}), [("โอนเงิน", D("200"))])

    def test_the_page_carries_the_day_facts_and_each_non_cash_method_asks_for_its_account_once(self):
        key = self.computer([book()])[0]
        self.masters(key)
        body = self.day_doc([["เงินสด", "221.00"], ["พร้อมเพย์", "100.00"]])
        page = E.pages(E.checked(body))[0]
        self.assertEqual(page["_extraction_audit"]["pos"]["count"], 3)
        result = self.prepare(body)
        self.assertEqual(result["hold"], E.CHOICE + ":receipt")
        self.assertEqual(result["needed"][0]["label"], "พร้อมเพย์")
        choices.remember(COMPANY["name"], "receipt", result["needed"][0]["subject"], "11-02-01-00", "")
        result = self.prepare(body)
        payload = result["payload"]
        self.assertGreaterEqual(payload["payload_version"], 3)  # 带仓库码的明细是 4，CC 认 4 就认 3
        self.assertEqual(payload["pos"]["transfers"], [{"acc": "11-02-01-00", "amount": "100.00", "label": "พร้อมเพย์"}])
        self.assertEqual((payload["pos"]["first"], payload["pos"]["last"], payload["pos"]["count"]), ("R0001", "R0003", 3))

    def test_a_refund_summary_asks_for_the_returns_account_once_and_becomes_an_sr(self):
        """B3b：退货日汇总交给共用写账规则：主页面带 refund 块（`pos_contract.refund_audit`），先问一次退货科目（按公司记住），
        记住后出退货单 SR、挂原 HS，载荷第 10 版（旧版 CC 诚实拒收）。"""
        key = self.computer([book()])[0]
        self.masters(key)
        body = self.day_doc([["เงินสด", "321.00"]])
        body["refund"] = {"against": "HS0000039", "against_total": "500.00"}
        page = E.pages(E.checked(body))[0]
        self.assertEqual(page["_extraction_audit"]["refund"], {"against": "HS0000039", "against_total": "500.00"})
        result = self.prepare(body)
        self.assertEqual(result["hold"], E.CHOICE + ":refund")
        choices.remember(COMPANY["name"], "refund", result["needed"][0]["subject"], ACCOUNTS[0]["code"], "")
        payload = self.prepare(body)["payload"]
        self.assertEqual(payload["doctype"], "SR")
        self.assertEqual((payload["refund"]["against"], payload["refund"]["cntyp"], payload["refund"]["retstk"]),
                         ("HS0000039", 3, "Y"))
        self.assertGreaterEqual(payload["payload_version"], 10)

    def test_the_reference_of_a_day_fits_express_with_erp_receipt_numbers(self):
        """实验栈冒烟 10-25：ERP 收银票号 13 字（SI2569-000006），「首-尾」27 字超过 Express 参考号 15 字节被 CC 拦下；
        日汇总改用共用约定的 `day_ref`（同 Cowork 的 POS 导入）。"""
        import datetime
        import inspect

        from pearnly_core.express_rules.contracts import pos

        day = datetime.date(2026, 10, 25)
        self.assertEqual(pos.day_ref("SI2569-000006", "SI2569-000007", day), "SI2569-000006-7")
        self.assertEqual(pos.day_ref("SI2569-000006", "SI2569-000006", day), "SI2569-000006")
        self.assertEqual(pos.day_ref("SI2569-000006", "SI2569-000123", day), "ABB691025")
        self.assertIn("pos_contract.day_ref(", inspect.getsource(self.pos_day.document))

    def test_the_receiving_account_choice_lists_the_account_set_accounts_assets_first(self):
        """实验栈冒烟 10-25：日汇总停在「选收款科目」时，ERP 弹窗那一项没有可选值（kind receipt 没对到科目表）。"""
        from pearnly_erp_portal.express import api

        key = self.computer([book()])[0]
        self.masters(key)
        item = {"kind": "receipt", "subject": "pos|พร้อมเพย์", "label": "พร้อมเพย์"}
        options = api._options(item, facts.load(key))
        self.assertEqual(sorted(row["code"] for row in options), sorted(row["code"] for row in ACCOUNTS))
        self.assertTrue(options[0]["code"].startswith("1"), options[:3])

    def test_a_receipt_is_in_at_most_one_summary_and_late_receipts_only_go_into_the_follow_up(self):
        """主会话 10-25：同一张小票最多进一张汇总。同一批票重复触发汇总不生成第二张；晚到的离线票只进补单（seq 2）。"""
        import types
        receipts = [{"name": "S1", "day": "2026-10-24", "summary": "", "total": 20}, {"name": "S2", "day": "2026-10-24", "summary": "", "total": 30}]
        made, deleted, queued = [], [], []

        class Doc(dict):
            __getattr__ = dict.get

            def insert(self, ignore_permissions=False):
                self["name"] = f"PDS-{len(made) + 1}"
                made.append(self["name"])

        def sql(query, values=None, as_dict=False):
            if query.lstrip().startswith("UPDATE"):
                for r in receipts:
                    if not r["summary"] and r["name"] in values[1]:
                        r["summary"] = values[0]
                return None
            if "COUNT(*) FROM" in query:  # 这家公司这天这个单别已经有几张（补单序号）
                return [[len(made)]]
            mine = [r for r in receipts if r["summary"] == values[1]]
            return [types.SimpleNamespace(receipts=len(mine), count=len(mine), total=sum(r["total"] for r in mine), vat=0)]

        def sql_list(query, values):
            if isinstance(values, dict):  # 选这天还没汇总的票
                return [r["name"] for r in receipts if not r["summary"] and r["day"] == values["day"]]
            return [r["name"] for r in receipts if r["summary"] == values[0]]

        from pearnly_erp_portal.express import push as push_module
        with mock.patch.object(frappe, "get_doc", lambda body: Doc(body), create=True), \
                mock.patch.object(frappe.db, "sql", side_effect=sql), \
                mock.patch.object(frappe.db, "sql_list", side_effect=sql_list, create=True), \
                mock.patch.object(frappe.db, "set_value", lambda *a, **k: None), \
                mock.patch.object(frappe, "delete_doc", lambda doctype, name, **k: deleted.append(name), create=True), \
                mock.patch.object(push_module, "queue", lambda doctype, name: queued.append(name)):
            first = self.pos_day.summarize("CO-1", "2026-10-24")
            again = self.pos_day.summarize("CO-1", "2026-10-24")  # 同一批票再触发：一张都不剩，不生成第二张
            receipts.append({"name": "S3", "day": "2026-10-24", "summary": "", "total": 5})  # 晚到的离线票
            late = self.pos_day.summarize("CO-1", "2026-10-24")
        self.assertEqual((first, again, late), ("PDS-1", None, "PDS-2"))  # 一张都不剩时不建空单
        self.assertEqual(deleted, [])
        self.assertEqual(queued, ["PDS-1", "PDS-2"])
        self.assertEqual({r["name"]: r["summary"] for r in receipts}, {"S1": "PDS-1", "S2": "PDS-1", "S3": "PDS-2"})

    def test_net_day_drops_zero_rows_skips_a_fully_refunded_day_and_stops_on_negatives(self):
        """共用约定 `net_day`（主会话 10-25，ERP 日汇总与 Cowork POS 导入同一处定）。"""
        from pearnly_core.express_rules.contracts import pos
        D = Decimal
        net = pos.net_day(D("70"), [("เงินสด", D("70")), ("พร้อมเพย์", D("0"))],
                          [{"code": "A", "qty": D("1"), "amount": D("70")}, {"code": "B", "qty": D("0"), "amount": D("0")}])
        self.assertEqual((net.payments, [line["code"] for line in net.items]), ((("เงินสด", D("70")),), ["A"]))
        self.assertIsNone(pos.net_day(D("0"), [("เงินสด", D("0"))], [{"qty": D("0"), "amount": D("0")}]))
        for gross, paid, items, code in (
                (D("-10"), [("เงินสด", D("-10"))], [], pos.DAY_NOT_POSITIVE),
                (D("0"), [("เงินสด", D("30")), ("พร้อมเพย์", D("-30"))], [], pos.DAY_NOT_POSITIVE),
                (D("50"), [("เงินสด", D("80")), ("พร้อมเพย์", D("-30"))], [], pos.PAYMENT_NEGATIVE),
                (D("50"), [("เงินสด", D("50"))], [{"qty": D("-1"), "amount": D("0")}, {"qty": D("2"), "amount": D("50")}],
                 pos.ITEMS_NOT_POSITIVE)):
            with self.assertRaises(pos.NetError) as raised:
                pos.net_day(gross, paid, items)
            self.assertEqual(raised.exception.code, code)
        net = pos.net_day(D("20"), [("เงินสด", D("-80")), ("พร้อมเพย์", D("100"))])  # 现金退款多过现金收款：现金可以为负
        self.assertEqual(net.payments, (("เงินสด", D("-80")), ("พร้อมเพย์", D("100"))))

    def summary_body(self, lines, paid, total, vat="0", registered="yes", exempt="0", kind=None, against=None,
                     cross_year=False, exempt_mode="split"):
        """同一天的退货并进日汇总后 `pos_day.document` 的结果（行与收款已按退货记负数求和，见 `_SIGN`；行金额是税前）。
        ``kind="SR"``：退货日汇总（照正数求和），``against`` 是原 HS 的 (Express 状态, Express 单号, 含税合计)。"""
        import types
        D = Decimal
        summary = types.SimpleNamespace(company="CO-1", day="2026-10-24", first_receipt="SI-1", last_receipt="SI-2",
                                        sales_count=2, doc_kind=kind, against="PDS-0" if against else None)
        refreshed = []

        def get_value(doctype, name, fields=None, as_dict=False):
            if doctype == self.pos_day.DOCTYPE and name == "PDS-0":
                state, docnum, grand = against
                return types.SimpleNamespace(express_state=state, express_docnum=docnum, grand_total=D(grand), day="2026-10-24")
            if doctype == self.pos_day.DOCTYPE:
                return summary
            if doctype == "ERP Company":
                return {"tax_id": OWN, "branch": "00012"}
            if doctype == "ERP Product":
                return {"product_name": name, "express_code": name, "express_stock": 1, "base_unit": "ถุง", "unit": "ถุง"}
            return "01"

        def sql(query, values=None, as_dict=False):
            if kind == "SR":  # 退货日汇总全是退货小票，照正数求和
                self.assertNotIn("CASE WHEN", query)
            else:
                self.assertIn("CASE WHEN s.sale_type = 'refund' THEN -1 ELSE 1 END", query)
            if "s.vat_amount" in query:
                return [types.SimpleNamespace(total=D(total), vat=D(vat))]
            if "tabERP Sale Payment" in query:
                return [types.SimpleNamespace(method=method, amount=D(amount)) for method, amount in paid]
            return [types.SimpleNamespace(product=line[0], warehouse="W1", unit="ถุง", qty=D(line[1]), amount=D(line[2]),
                                          vat=D(line[3] if len(line) > 3 else "1"),  # 第 4 项是这行拆出的税额，"0" = 免税
                                          gross=D(line[2]) + D(line[3] if len(line) > 3 else "1"))
                    for line in lines]

        with mock.patch.object(frappe.db, "get_value", side_effect=get_value, create=True), \
                mock.patch.object(frappe.db, "sql", side_effect=sql), \
                mock.patch.object(frappe.db, "sql_list", lambda q, v: ["SI-1", "SI-2"], create=True), \
                mock.patch.object(frappe.db, "set_value", lambda *a, **k: None), \
                mock.patch.object(self.pos_day.companies, "vat_registered", lambda company, lookup=False: registered), \
                mock.patch.object(self.pos_day.receipt_vat, "refresh", lambda names: refreshed.append(list(names))), \
                mock.patch.object(self.pos_day.receipt_vat, "exempt_total", lambda names: D(exempt)), \
                mock.patch.object(self.pos_day, "_cross_year", lambda company, first, second: cross_year), \
                mock.patch.object(self.pos_day, "_exempt_mode", lambda company, day: exempt_mode), \
                mock.patch.object(self.pos_day, "_book_path", lambda company, day: r"C:\Books\T8ACME"), \
                mock.patch.object(frappe, "utils", types.SimpleNamespace(getdate=lambda value: __import__("datetime").date(2026, 10, 24)),
                                  create=True):
            body = self.pos_day.document("PDS-1", "PDS-1@x")
        if registered and "hold" not in body:
            self.assertEqual(refreshed, [["SI-1", "SI-2"]])  # 写之前按公司此刻的状态重算小票税额
        return body

    def test_same_day_refunds_net_into_the_day_summary(self):
        """主会话 10-25：同一天卖、同一天退的退货冲进当天日汇总。部分退照净额写；一天全退整张不写；退货的付款方式和原单不同
        时按方式各自冲（非现金冲成负数停下交人，现金可以为负）。"""
        # 部分退：A 卖 3 退 1、B 全退（这一行不写）；PromptPay 收 100、退 0，现金 = 合计 − 非现金
        body = self.summary_body([("A", "2", "60"), ("B", "0", "0")], [("promptpay", "40")], "60")
        self.assertEqual([(line["code"], line["qty"], line["amount"]) for line in body["items"]], [("A", "2", "60.00")])
        self.assertEqual(body["pos"]["payments"], [["เงินสด", "20.00"], ["พร้อมเพย์", "40.00"]])
        self.assertEqual(body["pos"]["count"], 2)
        # 一天全退：什么都不写（推送把它记成「不用写」）
        self.assertEqual(self.summary_body([("A", "0", "0")], [("promptpay", "0")], "0"), {"ref": "PDS-1@x", "items": [], "empty": True})
        # 原单 PromptPay、退货退现金：PromptPay 照收的、现金为负（合计为正就照写）
        body = self.summary_body([("A", "1", "50")], [("promptpay", "100")], "50")
        self.assertEqual(body["pos"]["payments"], [["เงินสด", "-50.00"], ["พร้อมเพย์", "100.00"]])
        # 原单现金、退货退到 PromptPay：PromptPay 冲成负数 → 停下交人
        self.assertEqual(self.summary_body([("A", "1", "50")], [("promptpay", "-50")], "50"),
                         {"ref": "PDS-1@x", "items": [], "net_negative": True})
        # 晚到的同日退货单独成一张补单：整张为负 → 停下交人
        self.assertTrue(self.summary_body([("A", "-1", "-50")], [], "-50").get("net_negative"))

    def test_a_refund_after_the_day_summary_becomes_a_credit_note_against_that_hs(self):
        """B3b（10-26）：原小票已进 HS 之后的退货写成退货单 SR：照正数记，挂原 HS 的 Express 单号与合计；原 HS 还没写进
        Express 先等（每分钟再看）。"""
        waiting = self.summary_body([("A", "1", "93.46")], [], "100", vat="6.54", kind="SR", against=("queued", "", "200"))
        self.assertEqual(waiting, {"ref": "PDS-1@x", "hold": "express.pos_refund_waiting"})
        self.assertIn("express.pos_refund_waiting", E.RETRY_REASONS)
        body = self.summary_body([("A", "1", "93.46")], [("promptpay", "40")], "100", vat="6.54", kind="SR",
                                 against=("written", "HS0000039", "200"))
        self.assertEqual(body["refund"], {"against": "HS0000039", "against_total": "200.00"})
        self.assertEqual([(line["code"], line["qty"], line["amount"]) for line in body["items"]], [("A", "1", "93.46")])
        self.assertEqual(body["pos"]["payments"], [["เงินสด", "60.00"], ["พร้อมเพย์", "40.00"]])  # 退给客户的钱，正数
        self.assertEqual((body["total"], body["vat"]), ("100.00", "6.54"))

    def test_a_refund_summary_matches_its_original_on_the_taxable_base_and_stops_on_open_questions(self):
        """B3b 验收复核（主会话 10-26）：混有免税商品的退货单与原 HS 同一套规则只记应税部分（退货小票的免税净额 `exempt_total`
        记负数，退货单里要照正数扣）；原 HS 与退货在不同年度账套时实录前停人工；原 HS 是人手工处理的，退货单也停下手工处理。"""
        written = ("written", "HS0000039", "157")
        body = self.summary_body([("A", "1", "100"), ("B", "1", "50", "0")], [], "157", vat="7", exempt="-50", kind="SR",
                                 against=written)
        self.assertEqual(body["vat_fields"], {"AMT01": "100.00", "AMTRAT0": "50.00"})  # 卖应税 107 + 免税 50 次日全退：SR 同原 HS
        # 免税行标出来，CC 桌面版摊各行含税额时不摊税给它（实验栈实测 10-26：原来摊成 104.67 / 52.33）
        self.assertEqual([bool(item.get("vat_exempt")) for item in body["items"]], [False, True])
        # 原 HS 在上一年度的账套：不停人工，照 Express 原生写（主会话 10-26，B3b 实录点 ②），退货块标 cross_year
        body = self.summary_body([("A", "1", "100")], [], "107", vat="7", kind="SR", against=written, cross_year=True)
        self.assertNotIn("hold", body)
        # 带原 HS 所在的账套位置：新年度账套没有现金客户档时 CC 桌面版从这里复制（同 Express 年度结转）
        self.assertEqual(body["refund"], {"against": "HS0000039", "against_total": "157.00", "cross_year": True,
                                          "against_book": r"C:\Books\T8ACME"})
        held = self.summary_body([("A", "1", "100")], [], "107", vat="7", kind="SR", against=("handled", "", "157"))
        self.assertEqual(held["hold"], "express.pos_refund_original_manual")

    def test_refunds_after_the_day_summary_are_grouped_by_the_original_hs(self):
        """B3b：同一天卖、同一天退、原小票还没汇总的退货照旧并进 HS；原小票已进 HS 的退货（跨天的、晚到的）按 (退货那天, 原 HS)
        合成退货日汇总（单别 SR、挂原 HS），SR 的原单号只能挂一张原单。"""
        import inspect
        source = inspect.getsource(self.pos_day)
        self.assertIn("AND COALESCE(o.express_summary, '') = ''))", source)  # 并进 HS 的同日退货：原小票还没汇总
        self.assertIn("COALESCE(o.express_summary, '') != ''", source)  # 进 SR 的：原小票已在某张 HS
        self.assertIn("GROUP BY DATE(s.sold_at), o.express_summary", source)
        made, queued = [], []

        class Doc(dict):
            __getattr__ = dict.get

            def insert(self, ignore_permissions=False):
                self["name"] = f"PDS-{len(made) + 1}"
                made.append(dict(self))

        import types
        from pearnly_erp_portal.express import push as push_module
        with mock.patch.object(frappe, "get_doc", lambda body: Doc(body), create=True), \
                mock.patch.object(frappe.db, "sql", side_effect=lambda q, v=None, as_dict=False: (
                    [[0]] if "COUNT(*) FROM" in q else None if q.lstrip().startswith("UPDATE") else
                    [types.SimpleNamespace(receipts=2, count=2, total=150, vat=0)])), \
                mock.patch.object(frappe.db, "sql_list", lambda q, v: ["R1", "R2"], create=True), \
                mock.patch.object(frappe.db, "set_value", lambda *a, **k: None), \
                mock.patch.object(push_module, "queue", lambda doctype, name: queued.append(name)):
            name = self.pos_day.summarize_refunds("CO-1", "2026-10-25", "PDS-7")
        self.assertEqual((made[0]["doc_kind"], made[0]["against"], made[0]["day"]), ("SR", "PDS-7", "2026-10-25"))
        self.assertEqual(queued, [name])

    def test_receipts_split_vat_line_by_line_and_the_day_adds_the_lines_up(self):
        """主会话 10-25：标价含税。增值税登记户按小票每一行拆（商品勾了「คิด VAT」才拆 7/107，免税为 0），整单折扣按行摊，
        应收合计不变；退货按原行比例；日汇总合计各行税额、明细交税前；还不知道是不是登记户就停下问一次。"""
        from pearnly_erp_portal.cashier import contracts as cashier
        D = Decimal
        # 含免税商品：107（应税）+ 50（免税）→ 只有应税那行 7
        self.assertEqual(cashier.line_vats([D("107"), D("50")], [True, False]), [D("7.00"), D("0")])
        # 整单折扣 15.70 按行金额摊：应税行摊 10.70 → (107 − 10.70) × 7/107 = 6.30
        self.assertEqual(cashier.line_vats([D("107"), D("50")], [True, False], D("15.70")), [D("6.30"), D("0")])
        self.assertEqual(cashier.included_vat(D("100")), D("6.54"))
        # 退货按原行：原行 3 件税 21，退 1 件 → 7
        self.assertEqual(cashier.refund_line_vat(D("21"), D("3"), D("1")), D("7.00"))
        self.assertEqual(cashier.refund_line_vat(D("0"), D("3"), D("1")), D("0.00"))
        # 日汇总：合计 157、税 7（各行之和），明细是各行税前额；带税写
        body = self.summary_body([("A", "1", "100"), ("B", "1", "50")], [], "157", vat="7")
        self.assertEqual((body["vat"], body["total"], body["has_vat"]), ("7.00", "157.00", True))
        self.assertEqual([line["amount"] for line in body["items"]], ["100.00", "50.00"])
        self.assertNotIn("vat_fields", body)  # 交进来的行没有免税的：应税基数照整张税前
        # 混有免税商品（免税行净额 50）：销项税记录 AMT01 只记应税部分，免税部分记 AMTRAT0（验收实录第 10 条，Express 原生写法）；
        # 免税行标出来，CC 桌面版明细记 VATCOD '0'、单头记 AMTRAT0
        mixed = [("A", "1", "100"), ("B", "1", "50", "0")]
        body = self.summary_body(mixed, [], "157", vat="7", exempt="50")
        self.assertEqual(body["vat_fields"], {"AMT01": "100.00", "AMTRAT0": "50.00"})
        self.assertEqual([bool(item.get("vat_exempt")) for item in body["items"]], [False, True])
        # 有整单折扣（复核 10-26）：应税 107 + 免税 50、折 15.70，收银小票按含税额摊折扣，免税净额 45.00、税 6.30；明细两组各自摊，
        # 免税行 45.00 = AMTRAT0、应税行 90.00 = AMT01（原来统一按比例摊成 44.79 / 90.21，与销项税记录对不上）
        body = self.summary_body([("A", "1", "100.70"), ("B", "1", "50", "0")], [], "141.30", vat="6.30", exempt="45.00")
        self.assertEqual([line["amount"] for line in body["items"]], ["90.00", "45.00"])
        self.assertEqual(body["vat_fields"], {"AMT01": "90.00", "AMTRAT0": "45.00"})
        # 账套没开逐行 VAT、这家公司还没定：停下问一次（按商品免税拆开写 / 照账套整张计税），记住复用
        held = self.summary_body(mixed, [], "157", vat="7", exempt="50", exempt_mode=None)
        self.assertEqual(held["hold"], "choice_needed:exempt_mode")
        self.assertEqual([option["code"] for option in held["needed"][0]["options"]], ["split", "whole"])
        # 选了照账套整张计税（主会话 10-26 选 A）：同 Express 没开逐行 VAT 的账套，整张按含税价倒算 157 × 7/107 = 10.27、税前 146.73
        # （原生 HS0000041），合计不变；不拆、明细不标免税
        body = self.summary_body([("A", "1", "100", "7"), ("B", "1", "50", "0")], [], "157", vat="7", exempt="50", exempt_mode="whole")
        self.assertNotIn("vat_fields", body)
        self.assertFalse(any(item.get("vat_exempt") for item in body["items"]))
        self.assertEqual((body["vat"], body["total"], body["subtotal"]), ("10.27", "157.00", "146.73"))
        # 各行税前 = 收银小票含税额 107 / 50 × 100/107（同原生整张计税），CC 桌面版摊回的含税额就是 107 / 50
        self.assertEqual([line["amount"] for line in body["items"]], ["100.00", "46.73"])
        # 整张计税下全是免税商品的一天：不倒算出税，照免税写（原生对全免税的单不计税）
        body = self.summary_body([("B", "1", "50", "0")], [], "50", vat="0", exempt="50", exempt_mode="whole")
        self.assertEqual((body["vat"], body["has_vat"]), ("0.00", False))
        self.assertNotIn("vat_fields", body)
        # 整张计税下全是应税商品的一天：没有免税、不问，税额照小票 107 × 7/107 = 7.00
        for mode in (None, "whole"):
            body = self.summary_body([("A", "1", "107")], [], "107", vat="7", exempt_mode=mode)
            self.assertNotIn("hold", body)
            self.assertEqual((body["vat"], body["total"], body["has_vat"]), ("7.00", "107.00", True))
            self.assertNotIn("vat_fields", body)
        # 整张都是免税的（登记户）：也带金额栏，AMT01 0、AMTRAT0 全额（同原生 SR002100392），CC 桌面版照样写销项税记录
        body = self.summary_body([("B", "1", "50", "0")], [], "50", vat="0", exempt="50")
        self.assertEqual((body["vat_fields"], body["has_vat"]), ({"AMT01": "0.00", "AMTRAT0": "50.00"}, False))
        # 不是登记户：小票税额全是 0，日汇总不带税
        body = self.summary_body([("A", "1", "107", "0")], [], "107", vat="0", registered="no")
        self.assertEqual((body["vat"], body["has_vat"]), ("0.00", False))
        self.assertNotIn("vat_fields", body)  # 非登记户：没有免税这回事，不标
        self.assertFalse(any(item.get("vat_exempt") for item in body["items"]))
        # 还不知道：停下，在单据上问一次「该公司是增值税登记户吗」
        held = self.summary_body([("A", "1", "107")], [], "107", registered="")
        self.assertEqual(held["hold"], "choice_needed:vat_registered")
        self.assertEqual([option["code"] for option in held["needed"][0]["options"]], ["yes", "no"])

    def test_the_exempt_mode_follows_the_book_and_is_asked_once_when_line_vat_is_off(self):
        """主会话 10-26：账套开了逐行 VAT（ISINFO.VAT_INITEM = Y）或电脑还没回报这一项（旧版 CC）→ 拆开写；没开的按公司问一次、记住复用。"""
        import types
        from pearnly_erp_portal.express import choices, facts, push, target
        remembered = {}
        for initem, chosen, want in ((None, None, "split"), ("Y", None, "split"), ("", None, None), ("N", "whole", "whole"),
                                     ("", "split", "split")):
            remembered.clear()
            if chosen:
                remembered[("CO-1", "exempt_mode", "company")] = chosen
            with mock.patch.object(push, "_company", lambda name: types.SimpleNamespace(name=name), create=True), \
                    mock.patch.object(target, "pick", lambda row, day: {"book": {"key": "BK"}}), \
                    mock.patch.object(facts, "load", lambda key: {"facts": {} if initem is None else {"vat_initem": initem}}), \
                    mock.patch.object(choices, "chosen", lambda company, kind, subject: remembered.get((company, kind, subject), "")):
                self.assertEqual(self.pos_day._exempt_mode("CO-1", "2026-10-24") or None, want, (initem, chosen))

    def test_the_vat_status_comes_from_the_revenue_department_and_is_unknown_on_any_failure(self):
        """主会话 10-26：查到 VAT 登记 → 是；税号存在没登记 → 否；连不上、查不到、出错都记「未知」，走单据上问一次，不挡建公司和写单。"""
        import types
        utils = sys.modules.setdefault("frappe.utils", types.ModuleType("frappe.utils"))  # 替身没有的两个工具补上（只读不改）
        for name in ("add_days", "now_datetime"):
            if not hasattr(utils, name):
                setattr(utils, name, lambda *a: None)
        from pearnly_core.revenue_department import service as core
        from pearnly_erp_portal.masters import company as companies, rd
        for result, state in (({"ok": True, "data": {}}, "yes"), ({"ok": False, "error": "not_vat_registered"}, "no"),
                              ({"ok": False, "error": "rd_unreachable"}, ""), ({"ok": False, "error": "not_found"}, "")):
            with mock.patch.object(core, "lookup", lambda *a, result=result, **k: result), \
                    mock.patch.object(rd, "_DocTypeCache", lambda: None):
                self.assertEqual(companies._rd_vat("0105545112901", "00000"), state)
        with mock.patch.object(rd, "lookup", side_effect=TimeoutError("rd")), \
                mock.patch.object(frappe, "log_error", lambda **k: None, create=True), \
                mock.patch.object(frappe, "get_traceback", lambda: "tb", create=True):
            self.assertEqual(companies._rd_vat("0105545112901", "00000", network=True), "")
        self.assertEqual(companies._rd_vat("", "00000"), "")

    def test_a_non_vat_company_keeps_every_receipt_line_at_zero(self):
        from pearnly_erp_portal.cashier import vat
        lines = [{"product": "P1", "line_total": Decimal("107")}]
        with mock.patch.object(vat.companies, "vat_registered", lambda company, lookup=False: "no"):
            self.assertEqual(vat.sale_line_vats("CO-1", lines), [Decimal(0)])
        with mock.patch.object(vat.companies, "vat_registered", lambda company, lookup=False: "yes"), \
                mock.patch.object(vat, "_taxable", lambda products: {"P1": True}):
            self.assertEqual(vat.sale_line_vats("CO-1", lines), [Decimal("7.00")])

    def test_an_ordinary_sale_carries_no_day_facts(self):
        page = E.pages(E.checked(doc(direction="sales", paid=True)))[0]
        self.assertNotIn("_extraction_audit", page)


if __name__ == "__main__":
    unittest.main()
