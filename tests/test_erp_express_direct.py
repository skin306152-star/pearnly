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
import re
import sys
import tempfile
import types
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

    def test_unit_numbers_reach_the_payload_line_and_need_a_cc_desktop_that_writes_them(self):
        """按台号码（评估 2026-11-02）：ERP 行上拼好的说明经共用写账规则原样到载荷那一行，载荷盖 17；没有号码的行不带这一键。"""
        key, = self.computer([book()])
        self.masters(key)
        body = doc()
        body["items"][0]["remarks"] = ["เลขตัวถัง MP1TFR41JTG032993", "เลขเครื่อง FUK575 สี ELG"]
        payload = self.prepare(body)["payload"]
        self.assertEqual([item.get("remarks") for item in payload["items"]],
                         [["เลขตัวถัง MP1TFR41JTG032993", "เลขเครื่อง FUK575 สี ELG"], None])
        self.assertEqual(payload["payload_version"], common.REMARKS_PAYLOAD_VERSION)
        self.assertEqual(self.prepare(doc())["payload"]["payload_version"], 4)

    def test_an_exempt_document_keeps_the_vat_rate_on_its_head(self):
        """验收 10-30（express-rules-vat 窗口指出）：整单免税的单原生仍记税率 7（语料 158 本：明细全是免税行的进货 VATRAT 7 的
        4,822 张 / 0 的 256 张，销货 427,970 张 / 48 张），原来按「税额 > 0」给了 0。有免税行就照记税率；不收增值税、也没有
        免税行的单仍记 0。"""
        key, = self.computer([book()])
        self.masters(key)
        exempt = [{**item, "vat_exempt": True} for item in doc()["items"]]
        whole = self.prepare(doc(vat="0.00", total="300.00", items=exempt))["payload"]
        self.assertEqual((whole["vat_rate"], whole["payload_version"]), (7.0, 12))
        sales = self.prepare(doc(direction="sales", paid=True, vat="0.00", total="300.00", items=exempt,
                                 party={"code": "C01", "name": "ร้านป้าแดง", "tax_id": "", "branch": ""}))["payload"]
        self.assertEqual(sales["vat_rate"], 7.0)
        plain = self.prepare(doc(has_vat=False, vat="0.00", total="300.00"))["payload"]
        self.assertEqual(plain["vat_rate"], 0.0)
        mixed = [doc()["items"][0], {**doc()["items"][1], "vat_exempt": True}]
        self.assertEqual(self.prepare(doc(vat="14.00", total="314.00", items=mixed))["payload"]["vat_rate"], 7.0)

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
        # 合格的在前、不合格的也列（人明确选了照写，Express 原生不拦，1.12.23）
        self.assertEqual((needed["kind"], [option["code"] for option in needed["options"]]), ("stock_group", ["DM", "ST01"]))
        choices.remember(COMPANY["name"], needed["kind"], needed["subject"], "DM")
        self.assertEqual(self.prepare(doc())["payload"]["stock_acccod"], "DM")

    def test_a_book_with_stock_items_decides_the_group_for_new_items_before_the_computer_does(self):
        """11-16 真机 H5：账套里已有库存品时，以前不判组、交给 CC 桌面版按众数选，众数组 ST01（办公设备）写到一半才拦、
        单据上只剩「重试」。现在同零库存品账套一份合格名单：合格的只有一个就直接带它，两个以上开始前问一次（同一题、同一记法）。"""
        from pearnly_erp_portal.express import contracts as K
        key, = self.computer([book()])
        new = [{"name": "D1116 H5 Item", "code": "", "unit": "ถุง", "qty": "3", "price": "100", "amount": "300.00",
                "loccod": "02", "stock": True}]
        one = [{"code": "DM", "desc": "วัตถุดิบ", "inv_acc": "11-04-01-00", "fit": True},
               {"code": "ST01", "desc": "อุปกรณ์", "inv_acc": "14-01-03-00", "fit": False}]
        self.masters(key, facts={"accounts": ACCOUNTS, "mapping": MAPPING, "catalog": {"isacc_candidates": one}})
        item, = self.prepare(doc(items=new))["needed"]  # 新物料先答「新建」（H5 当时选的）
        choices.remember(COMPANY["name"], item["kind"], item["subject"], C.NEW)
        self.assertEqual(self.prepare(doc(items=new))["payload"]["stock_acccod"], "DM")
        two = one + [{"code": "P002", "desc": "สินค้า", "inv_acc": "11-04-02-00", "fit": True}]
        self.masters(key, facts={"accounts": ACCOUNTS, "mapping": MAPPING, "catalog": {"isacc_candidates": two}})
        held = self.prepare(doc(items=new))
        self.assertEqual(held["hold"], K.CHOICE + ":stock_group")
        self.assertEqual([option["code"] for option in held["needed"][0]["options"]], ["DM", "P002", "ST01"])  # 不合格的排后面
        choices.remember(COMPANY["name"], held["needed"][0]["kind"], held["needed"][0]["subject"], "P002")
        self.assertEqual(self.prepare(doc(items=new))["payload"]["stock_acccod"], "P002")

    def test_service_vat_basis_is_asked_only_when_the_books_history_cannot_decide(self):
        """10-29：赊购单服务品的进项税口径按这本账套的历史（CC 随供应商主档报的 ``srv``）定；这家两种都有才问一次，
        按「这本账套 × 这家供应商」记在公司名下，再准备时写进载荷 ``srv_vattyp``（载荷 13）。电脑写时才拦下的（主档是旧的），
        在单据上问同一项。"""
        from pearnly_core.express_rules.push import service_vat as V
        from pearnly_erp_portal.express import prepare as P
        key, = self.computer([book()])
        service = [{"name": "ค่าขนส่ง", "code": "52-08-00-00", "unit": "", "qty": "1", "price": "300", "amount": "300.00",
                    "loccod": "", "stock": False}]
        self.masters(key, suppliers=[{**SUPPLIERS[0], "srv": "2"}, {**SUPPLIERS[1], "srv": "0"}])
        payload = self.prepare(doc(items=service))["payload"]
        self.assertNotIn(V.PAYLOAD_KEY, payload)  # 历史定得了：CC 写时照历史，不问
        self.masters(key, suppliers=[{**SUPPLIERS[0], "srv": "12"}, {**SUPPLIERS[1], "srv": "0"}])
        held = self.prepare(doc(items=service))
        self.assertEqual(held["hold"], "choice_needed:" + V.KIND)
        needed, = held["needed"]
        self.assertTrue(needed["subject"].startswith(key + "|"))
        self.assertEqual(P.service_vat_needed(doc(items=service), COMPANY)[0]["subject"], needed["subject"])
        choices.remember(COMPANY["name"], V.KIND, needed["subject"], "cash_basis")
        payload = self.prepare(doc(items=service))["payload"]
        self.assertEqual((payload[V.PAYLOAD_KEY], payload["payload_version"] >= V.PAYLOAD_VERSION), ("2", True))
        self.assertNotIn(V.PAYLOAD_KEY, self.prepare(doc(items=service, paid=True))["payload"])  # 现购单恒 1，不问不带
        self.assertIn(V.CHOICE_CODE, P.SERVICE_VAT_CODES)

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

    def test_unit_numbers_become_line_remarks_express_can_hold(self):
        """汽车一台两条（标签照的三项连同标签放一条超过 50 字节）；手机电器按 S/N 装满一条再换；空的项不写；再长也折行不截断。"""
        car = [{"serial_no": "MP1TFR41JTG032993", "secondary_no": "FUK575", "color": "ELG"},
               {"serial_no": "MP1TFR41JTG032994", "secondary_no": "", "color": ""}]
        self.assertEqual(E.unit_remarks(car, "vehicle"), ["เลขตัวถัง MP1TFR41JTG032993", "เลขเครื่อง FUK575 สี ELG",
                                                          "เลขตัวถัง MP1TFR41JTG032994"])
        phones = [{"serial_no": f"35294809465{n:04d}", "secondary_no": "", "color": ""} for n in range(3)]
        self.assertEqual(E.unit_remarks(phones, "device"), ["S/N 352948094650000  352948094650001",  # 15 位 IMEI 一条两个
                                                            "S/N 352948094650002"])
        serials = [{"serial_no": f"NPB20185066{n}S", "secondary_no": "", "color": ""} for n in range(4)]
        self.assertEqual(E.unit_remarks(serials, "device"), ["S/N NPB201850660S  NPB201850661S  NPB201850662S",
                                                             "S/N NPB201850663S"])
        self.assertEqual(E.unit_remarks([{"serial_no": "A1", "secondary_no": "B2", "color": "ดำ"}], "device"), ["S/N A1/B2 ดำ"])
        long = E.unit_remarks([{"serial_no": "", "secondary_no": "E" * 40, "color": "C" * 20}], "vehicle")
        self.assertEqual(("".join(long), [len(text) <= E.REMARK_WIDTH for text in long]),
                         (f"เลขเครื่อง {'E' * 40} สี {'C' * 20}", [True, True]))
        self.assertEqual(E.unit_remarks([{"serial_no": " ", "secondary_no": "", "color": ""}], "vehicle"), [])

    def test_unit_remarks_ride_the_document_to_the_page(self):
        body = E.document(ref="SD-00002", direction="sales", doc_date="2026-11-02", number="SD-00002",
                          company={"tax_id": OWN, "branch": ""}, party={"code": "C01", "name": "ลูกค้า"},
                          paid=True, has_vat=False, total="900000.00", vat="0",
                          lines=[{"name": "UPG20", "code": "UPG20", "qty": 1, "amount": "900000.00", "loccod": "01",
                                  "stock": True, "remarks": ["เลขตัวถัง MP1TFR41JTG032993"]},
                                 {"name": "ฟรี", "code": "", "qty": 0, "amount": "0", "stock": False,
                                  "remarks": ["dropped with its zero line"]}])
        self.assertEqual([item.get("remarks") for item in body["items"]], [["เลขตัวถัง MP1TFR41JTG032993"]])
        fields = E.pages(E.checked(body))[0]["fields"]
        self.assertEqual(fields["items"][0]["remarks"], ["เลขตัวถัง MP1TFR41JTG032993"])

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
        # 内存替身没有 tabSeries / 多行 INSERT：新建的行照逐条那条退路建（批量写与逐条写逐字段一致由
        # scripts/erp-masters-bulk-lab-verify.py 在实验栈真库上核）
        self.bulk_calls = []

        def insert_new(doctype, records, one):
            self.bulk_calls.append((doctype, len(records)))
            return sum(one(values) for values in records)

        bulk_patch = mock.patch.object(masters.bulk, "insert_new", insert_new)
        bulk_patch.start()
        self.addCleanup(bulk_patch.stop)
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

    def test_the_prefix_joins_the_name_and_a_first_full_name_sync_only_rewrites_the_name_column(self):
        # 11-15：CC 桌面版只报 CUSNAM / SUPNAM，带称谓的对方在 ERP 只显示短名。1.12.22 起另报 PRENAM：显示名 = 前缀 + 核心名；
        # 已同步的档第一次改成全名（一本账套八九成档带前缀）只改名称这一列，不读出整份、不 save（不留修改记录）
        from unittest import mock
        old = [{"code": f"V{i:03d}", "name": f"ทดสอบ {i}", "tax_id": "", "branch": "00000"} for i in range(4)]
        self.assertEqual(self.sync._parties("ERP Supplier", "CO-1", "T.9", old, self.sync._supplier_fields), 4)
        new = [{**row, "prename": "ร้าน" if i % 2 else ""} for i, row in enumerate(old)]
        with mock.patch.object(frappe, "get_doc", side_effect=AssertionError("a rename must not read or save the row")):
            self.assertEqual(self.sync._parties("ERP Supplier", "CO-1", "T.9", new, self.sync._supplier_fields), 2)
            self.assertEqual(self.sync._parties("ERP Supplier", "CO-1", "T.9", new, self.sync._supplier_fields), 0)
        names = sorted(row["supplier_name"] for row in fake.STATE.db.table("ERP Supplier").values())
        self.assertEqual(names, ["ทดสอบ 0", "ทดสอบ 2", "ร้าน ทดสอบ 1", "ร้าน ทดสอบ 3"])
        self.assertEqual(len(fake.STATE.db.table("ERP Supplier")), 4)  # 按编码认档，没有一条当成新的

    def test_a_name_that_differs_only_by_spaces_is_kept_as_written(self):
        # 人在 ERP 建的「ร้านลีฟทดสอบ 1115」推进 Express 成 PRENAM ร้าน + 核心名；同步回来不改成「ร้าน ลีฟทดสอบ 1115」
        made = self.supplier(supplier_name="ร้านลีฟทดสอบ 1115", express_code="S9", express_company="CO-1",
                             branch_type="head_office")
        rows = [{"code": "S9", "name": "ลีฟทดสอบ 1115", "prename": "ร้าน", "tax_id": "", "branch": "00000"}]
        self.sync._parties("ERP Supplier", "CO-1", "T.9", rows, self.sync._supplier_fields)
        self.assertEqual(fake.STATE.db.table("ERP Supplier")[made]["supplier_name"], "ร้านลีฟทดสอบ 1115")

    def test_a_local_record_is_claimed_by_its_full_or_its_core_name(self):
        short = self.supplier(supplier_name="ลีฟ", branch_type="head_office")
        full = self.supplier(supplier_name="บริษัท เอ จำกัด", branch_type="head_office")
        rows = [{"code": "S1", "name": "ลีฟ", "prename": "ร้าน", "tax_id": "", "branch": "00000"},
                {"code": "S2", "name": "เอ จำกัด", "prename": "บริษัท", "tax_id": "", "branch": "00000"}]
        self.sync._parties("ERP Supplier", "CO-1", "T.9", rows, self.sync._supplier_fields)
        table = fake.STATE.db.table("ERP Supplier")
        self.assertEqual((table[short]["express_code"], table[short]["supplier_name"]), ("S1", "ร้าน ลีฟ"))
        self.assertEqual((table[full]["express_code"], table[full]["supplier_name"]), ("S2", "บริษัท เอ จำกัด"))
        self.assertEqual(len(table), 2)

    def test_a_book_change_rewrites_nothing_and_new_rows_go_through_the_bulk_path(self):
        # 11-13 生产 testerp CO-00146：现用账套从「T.8 …」换成「9.…」，一万多条客户因档上的 express_book 不同全部重写。
        # 档上的 express_book 只在新建时记、不比对；新建的行走批量写，已有的行不走
        rows = [{"code": f"V{i:03d}", "name": f"ผู้ขาย {i}", "tax_id": "", "branch": "00000"} for i in range(5)]
        self.assertEqual(self.sync._parties("ERP Supplier", "CO-1", "T.8 old", rows, self.sync._supplier_fields), 5)
        self.assertEqual(self.bulk_calls, [("ERP Supplier", 5)])
        with mock.patch.object(frappe, "get_doc", side_effect=AssertionError("a book change must not rewrite rows")):
            self.assertEqual(self.sync._parties("ERP Supplier", "CO-1", "9. new", rows, self.sync._supplier_fields), 0)
        books = {row.get("express_book") for row in fake.STATE.db.table("ERP Supplier").values()}
        self.assertEqual(books, {"T.8 old"})
        self.assertEqual(self.bulk_calls, [("ERP Supplier", 5)])
        self.assertNotIn("express_book", self.sync.SYNC_FIELDS)

    def test_sync_jobs_take_the_masters_lane_once_the_bench_lists_it(self):
        conf = getattr(frappe, "conf", None)
        self.addCleanup(setattr, frappe, "conf", conf)
        frappe.conf = {"workers": {"erp_masters": {"timeout": 3900}}}
        self.assertEqual(self.sync.lane(), "erp_masters")
        frappe.conf = {"workers": {}}
        self.assertEqual(self.sync.lane(), "long")  # 没登记的 bench（旧测试栈）退回 long，不排进没人取的队列

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

    def test_the_link_time_is_written_by_the_first_landing_while_checked(self):
        """接上时刻（主会话 11-01）：勾着「接 Express」、第一次落库那一刻记（收银起始日随它）；再落库不改；没勾的不记（断开时在读的
        同步落进来也不算重新接上）；断开清空后（`masters.company.disconnect_express`）下一次落库重记。"""
        import datetime as dt
        from unittest import mock
        table = fake.STATE.db.table("ERP Company")
        table["CO-1"] = {"name": "CO-1", "is_active": 1, "express_enabled": 0, "express_linked_at": None}
        clock = [dt.datetime(2026, 10, 23, 12, 1)]

        def land():
            with mock.patch.object(self.sync, "_parties", lambda *a: 0), mock.patch.object(self.sync, "_products", lambda *a: 0), \
                    mock.patch.object(self.sync, "_warehouses", lambda *a: ({}, 0)), \
                    mock.patch.object(self.sync, "_stock", lambda *a: 0), mock.patch.object(self.sync, "_profile", lambda *a: {}), \
                    mock.patch.object(self.sync, "now_datetime", lambda: clock[0]), \
                    mock.patch.object(self.sync.push, "requeue_unmapped", lambda company: 0, create=True), \
                    mock.patch.object(frappe.db, "commit", lambda: None, create=True):
                self.sync.apply("CO-1", "T9", {"products": [], "stock": []})
            return table["CO-1"].get("express_linked_at"), table["CO-1"].get("express_pos_from")

        self.assertEqual(land(), (None, None))  # 没勾
        table["CO-1"]["express_enabled"] = 1
        self.assertEqual(land(), (dt.datetime(2026, 10, 23, 12, 1), "2026-10-23"))
        clock[0] = dt.datetime(2026, 10, 30, 8, 0)
        self.assertEqual(land()[0], dt.datetime(2026, 10, 23, 12, 1))  # 再落库不改
        table["CO-1"]["express_linked_at"] = None  # 断开清空、又勾上
        self.assertEqual(land()[0], dt.datetime(2026, 10, 30, 8, 0))

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
                                  lambda row, day: {"key": "k" + row.name[-1], "label": "T.9", "path": PATH}, create=True), \
                mock.patch.object(self.sync.target, "masters_choice", lambda row, day: None, create=True):
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

    def test_masters_ask_once_when_two_books_cover_today(self):
        # 11-15：同期两份（如 C 盘副本与 S 盘年度文件夹）没选过时，主档同步不再默认读第一份，停下等人在公司页选一次；
        # 选的记在写单同一份记忆里（按公司 × 会计年度），之后主档、写单都用它
        from pearnly_erp_portal.express import choices, target
        copy, real = self.computer([book(path=PATH + "-copy", uid="vsn:1/copy", start="2026-01-01", end="2026-12-31"),
                                    book(path=PATH + "-real", uid="vsn:1/real", start="2026-01-01", end="2026-12-31")])
        asked = target.masters_choice(COMPANY, "2026-10-23")
        self.assertEqual(sorted(item["code"] for item in asked["options"]), sorted([copy, real]))
        choices.remember(COMPANY["name"], choices.ACCOUNT_SET, asked["subject"], real, "real")
        self.assertIsNone(target.masters_choice(COMPANY, "2026-10-23"))
        self.assertEqual(target.current(COMPANY, "2026-10-23")["key"], real)
        self.assertEqual(target.pick(COMPANY, "2026-10-23")["book"]["key"], real)

    def test_one_book_needs_no_choice(self):
        from pearnly_erp_portal.express import target
        self.computer([book(path=PATH + "-real", uid="vsn:1/real", start="2026-01-01", end="2026-12-31")])
        self.assertIsNone(target.masters_choice(COMPANY, "2026-10-23"))

    def test_only_connected_computers_that_report_the_chosen_roots_count(self):
        # 1.12.17 以前报位置下全部账套（同一台电脑连着 Cowork 时含事务所别的客户）、断开后名单还留着：照它们建会把别的客户建进来
        from pearnly_erp_portal.express import companies
        devices = [{"name": "PCD-new", "token_digest": "x", "companion_version": "1.12.17"},
                   {"name": "PCD-old", "token_digest": "x", "companion_version": "1.12.16"},
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
        # 单据所属的公司勾着「接 Express」、已接上（`push.enabled` / `linked_at`）；断开的情形在 LinkStates 里单独测
        fake.STATE.db.table("ERP Company")["CO-1"] = {"name": "CO-1", "express_enabled": 1,
                                                      "express_linked_at": "2026-10-01 00:00:00"}


class PartyAccount(PushModule):
    def test_cc_remembers_a_supplier_s_expense_account_checked_against_the_account_set(self):
        """欠账 B52：用户在 CC 里说「这家的单记到某科目」（`erp_party_account_save` → `api.remember_party_account`）。科目码按这张单
        所在账套报来的科目表核过才记，记在单据上「选一次」同一个归类键上：之后这家的服务类进项不再停下问科目。销售单、账套里
        没有的码不记。"""
        utils = sys.modules["frappe.utils"]
        for name, value in (("now", lambda: "2026-10-24 00:00:00"), ("flt", lambda v, *a: float(v or 0)),
                            ("cint", lambda v: int(v or 0)), ("getdate", lambda v=None: v), ("add_days", lambda d, n: d)):
            setattr(utils, name, getattr(utils, name, value))
        sys.modules.pop("pearnly_erp_portal.express.api", None)
        self.addCleanup(sys.modules.pop, "pearnly_erp_portal.express.api", None)
        from pearnly_erp_portal.express import api
        key, = self.computer([book()])
        self.masters(key)
        fake.STATE.db.table("ERP Company")[COMPANY["name"]] = dict(COMPANY)
        row = types.SimpleNamespace(company=COMPANY["name"], doc_date="2026-10-20", supplier=None, supplier_name="BANGCHAK",
                                    supplier_tax_id=SUPPLIER, supplier_branch_no="00000")
        service = [{"name": "ค่าขนส่ง", "code": "", "unit": "", "qty": "1", "price": "300", "amount": "300.00",
                    "loccod": "", "stock": False}]
        self.assertEqual(self.prepare(doc(items=service))["hold"], "choice_needed:account")
        with mock.patch.object(frappe.db, "commit", lambda: None, create=True):
            missing = api.remember_party_account("Purchase Document", row, "99-99-99-99")
            sales = api.remember_party_account("ERP Sales Document", row, "52-08-00-00")
            saved = api.remember_party_account("Purchase Document", row, " 52-08-00-00 ")
        self.assertEqual(missing["status"], "not_saved")
        self.assertTrue(missing["reason"].startswith("account_not_in_book"))
        self.assertTrue(sales["reason"].startswith("purchase_only"))
        self.assertEqual((saved["status"], saved["account"], saved["subject"]),
                         ("saved", "52-08-00-00", C.supplier_subject("purchase", SUPPLIER, "BANGCHAK")))
        payload = self.prepare(doc(items=service))["payload"]
        self.assertIn("52-08-00-00", [line["acc"] for line in payload["lines"] if line["side"] == "D"])


class Row(dict):
    """单据 / 明细行的替身：属性和 `.get` 都能取（同 Frappe Document）。"""
    __getattr__ = dict.get


class MixedRatePurchase(PushModule):
    """验收复核测试包 e2（10-26）：采购单逐行定税率，有的行 0。0 税率的行标免税，CC 桌面版写价含税的现购时不摊税给它（原生 107 / 50、
    免税行 VATCOD '0'）；载荷盖 11，旧版 CC 诚实拒收。整张无税的单不标。"""

    def lines(self, *rates, has_vat=0):
        import types

        doc = types.SimpleNamespace(has_vat=has_vat, lines=[Row(product=None, warehouse=None, description=f"L{index}", unit="ถุง", qty=1,
                                               line_total=100, vat_rate=rate, item_type="service")
                                           for index, rate in enumerate(rates)])
        return self.push._lines(self.push.PURCHASE, doc)

    def test_zero_rate_lines_of_a_taxed_purchase_are_marked_exempt(self):
        self.assertEqual([bool(line.get("vat_exempt")) for line in self.lines(7, 0)], [False, True])
        self.assertEqual([bool(line.get("vat_exempt")) for line in self.lines(0, 0)], [False, False])  # 整张无税：不标
        self.assertEqual([bool(line.get("vat_exempt")) for line in self.lines(7, 7)], [False, False])
        self.assertEqual(common.finalize_payload({"items": [{"name": "A", "vat_exempt": True}]})["payload_version"], 11)

    def test_a_whole_exempt_purchase_is_marked_like_the_native_books(self):
        """主会话 10-29（语料 158 本）：勾了税务发票、全是 0% 的采购单是整单免税，原生价格类型 1 / 2、明细 VATCOD '0'、
        单头 AMTRAT0 全额，不是无税（FLGVAT 0）。每行标免税、带零税额栏，载荷盖 12（旧版 CC 照无税写，诚实拒收）；
        没勾税务发票的照旧不标。"""
        self.assertEqual([bool(line.get("vat_exempt")) for line in self.lines(0, 0, has_vat=1)], [True, True])
        self.assertEqual([bool(line.get("vat_exempt")) for line in self.lines(0, 0, has_vat=0)], [False, False])
        self.assertEqual(self.push.vat_fields({"subtotal": "150.00"}, Decimal("150")), {"AMT01": "0.00", "AMTRAT0": "150.00"})
        exempt = [{"name": "A", "vat_exempt": True}]
        version = lambda **kw: common.finalize_payload({"items": list(exempt), **kw})["payload_version"]  # noqa: E731
        self.assertEqual(version(doctype="RR", direction="purchase", vat_amount="0.00"), 12)
        self.assertEqual(version(doctype="HP", vat_amount="0"), 12)
        self.assertEqual(version(doctype="RR", direction="purchase", vat_amount="7.00"), 11)  # 混合税率照旧
        self.assertEqual(version(doctype="HS", direction="sales", vat_amount="0.00"), 11)  # 销货早已照原生写
        # 进项税记录：AMT01 只记应税部分、免税部分记 AMTRAT0（同原生 HP67012204）；没有免税额不带
        self.assertEqual(self.push.vat_fields({"subtotal": "150.00"}, Decimal("50")), {"AMT01": "100.00", "AMTRAT0": "50.00"})
        self.assertEqual(self.push.vat_fields({"subtotal": "150.00"}, Decimal("0")), {})
        # 有整单折扣（验收复核 e3）：免税额取折后的计税基数——应税 100 + 免税 50、整单折扣 15 → 税前 135、税 6.30；
        # AMT01 90、AMTRAT0 45（两栏相加 = 税前，AMT01 × 7% = 税额）；原来按折前行额算成 85 / 50
        doc = Row(calculation_version=4, has_vat=1, price_mode="exclusive", discount_total=15, rounding=0,
                  lines=[Row(qty=1, unit_price=100, vat_rate=7, line_total=100), Row(qty=1, unit_price=50, vat_rate=0, line_total=50)])
        fields = self.push.vat_fields({"subtotal": "135.00"}, self.push._exempt_base(doc))
        self.assertEqual(fields, {"AMT01": "90.00", "AMTRAT0": "45.00"})
        self.assertEqual(Decimal(fields["AMT01"]) + Decimal(fields["AMTRAT0"]), Decimal("135.00"))
        self.assertEqual((Decimal(fields["AMT01"]) * Decimal("0.07")).quantize(Decimal("0.01")), Decimal("6.30"))
        old = Row(calculation_version=1, lines=[Row(line_total=100, vat_rate=7), Row(line_total=50, vat_rate=0)])
        self.assertEqual(self.push._exempt_base(old), Decimal("50"))  # 很早的单：照行金额


class LineStockKinds(PushModule):
    """11-06 按行选物料与库存（评估 4.4）：动不动库存按行。Express 存货但这一行只记账 → 非库存行、不带存货码；销售没码的行看
    行上的出库勾选（以前有物料就出库）；采购没码照旧看入库勾选；Express 非库存品照它的码写非库存。"""

    def setUp(self):
        super().setUp()
        fake.STATE.db.table("ERP Product").update({
            "P-STOCK": {"name": "P-STOCK", "product_name": "ICE", "express_code": "ICE-01", "express_stock": 1},
            "P-LOCAL": {"name": "P-LOCAL", "product_name": "LOCAL", "express_code": "", "express_stock": 0},
            "P-FREIGHT": {"name": "P-FREIGHT", "product_name": "FREIGHT", "express_code": "S01", "express_stock": 0}})

    def lines(self, doctype, *rows):
        import types

        doc = types.SimpleNamespace(has_vat=1, lines=[Row(product=product, item_type=kind, warehouse=None, description=product or "X",
                                                          unit="ถุง", qty=1, line_total=100, vat_rate=7)
                                                      for product, kind in rows])
        return [(line["stock"], line["code"]) for line in self.push._lines(doctype, doc)]

    def test_an_express_stock_item_recorded_only_is_a_non_stock_line_without_its_code(self):
        self.assertEqual(self.lines(self.push.PURCHASE, ("P-STOCK", "goods"), ("P-STOCK", "service")),
                         [(True, "ICE-01"), (False, "")])

    def test_sales_lines_follow_the_stock_out_mark(self):
        self.assertEqual(self.lines(self.push.SALES, ("P-LOCAL", "goods"), ("P-LOCAL", "service"), (None, "goods")),
                         [(True, ""), (False, ""), (False, "")])

    def test_purchase_lines_without_a_code_follow_the_stock_in_mark(self):
        self.assertEqual(self.lines(self.push.PURCHASE, ("P-LOCAL", "goods"), ("P-LOCAL", "service")), [(True, ""), (False, "")])

    def test_an_express_non_stock_item_keeps_its_code(self):
        self.assertEqual(self.lines(self.push.SALES, ("P-FREIGHT", "goods"), ("P-FREIGHT", "service")),
                         [(False, "S01"), (False, "S01")])

    def test_a_mixed_document_is_payload_19(self):
        items = [{"name": "ICE", "code": "ICE-01", "item_mode": "stock_item"}, {"name": "FREIGHT", "item_mode": "non_stock_item"}]
        self.assertEqual(common.finalize_payload({"items": items})["payload_version"], 19)


class UnitRemarks(PushModule):
    """按台号码（评估 2026-11-02）：单据 ``serials`` 子表按明细行号挂到那一行，按商品的号码种类拼成附加说明；没有台的行不带。"""

    def test_the_units_of_a_line_become_its_remarks(self):
        products = fake.STATE.db.table("ERP Product")
        products["P-CAR"] = {"name": "P-CAR", "product_name": "UPG20", "express_code": "UPG20", "express_stock": 1,
                             "serial_kind": "vehicle"}
        products["P-PHONE"] = {"name": "P-PHONE", "product_name": "A15", "express_code": "A15", "express_stock": 1,
                               "serial_kind": "device"}
        line = lambda product: Row(product=product, warehouse=None, description=product, unit="คัน", qty=1,  # noqa: E731
                                   line_total=100, vat_rate=7, item_type="goods")
        doc = Row(has_vat=1, lines=[line("P-PHONE"), line("P-CAR"), line(None)],
                  serials=[Row(line_no=2, serial_no="MP1TFR41JTG032993", secondary_no="FUK575", color="ELG"),
                           Row(line_no=1, serial_no="352948094653751", secondary_no="", color="")])
        self.assertEqual([item.get("remarks") for item in self.push._lines(self.push.SALES, doc)],
                         [["S/N 352948094653751"], ["เลขตัวถัง MP1TFR41JTG032993", "เลขเครื่อง FUK575 สี ELG"], None])


class PurchaseSalesVoid(PushModule):
    """采购单 / 销售单放行排队后在 ERP 作废（主会话 11-13 查出：线上会把作废单照入队时冻结的载荷写进 Express、ERP 还记「已写入」；
    「修改」作废原单时原单和新单两张都写）：作废时撤掉排队中 / 转人工的写账件、清账本栏；电脑已领走撤不了的，写完回执到了不记成
    干净的「已写入」，转人工、原因写明「作废后写进了 Express」（采购、销售没有 Express 原生作废载荷，要人在 Express 里处理）。"""

    def setUp(self):
        super().setUp()
        self.cancelled = []

    def queued(self, doctype, status="submitted", job_status="queued"):
        table = fake.STATE.db.table(doctype)
        table["D-1"] = {"name": "D-1", "creation": "2026-10-25 10:00:00", "owner": "a@x", "status": status,
                        "ledger_state": "queued", "ledger_docnum": "", "ledger_job": "J1", "ledger_reason": ""}
        self.job_status = job_status
        return table

    def run_void(self, doctype):
        from unittest import mock
        with mock.patch.object(self.push.jobs, "find_by_name", lambda name: {"job": name, "status": self.job_status}), \
                mock.patch.object(self.push.jobs, "cancel", lambda name, reason: self.cancelled.append((name, reason))):
            self.push.void(doctype, "D-1")

    def test_voiding_a_queued_purchase_or_sales_document_cancels_its_write(self):
        for doctype in ("Purchase Document", "ERP Sales Document"):
            self.cancelled.clear()
            table = self.queued(doctype, status="void")
            self.run_void(doctype)
            self.assertEqual(self.cancelled, [("J1", "voided")], doctype)
            self.assertEqual((table["D-1"]["ledger_state"], table["D-1"]["ledger_job"]), (None, None), doctype)

    def test_a_job_already_taken_is_left_and_its_receipt_is_not_a_clean_write(self):
        from unittest import mock
        table = self.queued("Purchase Document", status="void", job_status="leased")
        self.run_void("Purchase Document")
        self.assertEqual(self.cancelled, [])  # 电脑正在写：撤不了
        self.assertEqual(table["D-1"]["ledger_job"], "J1")
        with mock.patch.object(self.push, "company_of", lambda doctype, name: ""):
            self.push.job_changed({"source_doctype": "Purchase Document", "source_name": "D-1", "status": "success",
                                   "job": "J1", "docnum": "HP691025-001"})
        self.assertEqual(table["D-1"]["ledger_state"], "held")
        self.assertEqual(table["D-1"]["ledger_reason"], self.push.contracts.VOIDED_AFTER_WRITE)
        self.assertEqual(table["D-1"]["ledger_docnum"], "HP691025-001")  # 单号如实留着，人去 Express 里处理

    def test_the_web_void_and_amend_paths_call_it(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[1] / "apps/pearnly_erp_portal/pearnly_erp_portal"
        purchase = (root / "purchasing/entry.py").read_text(encoding="utf-8")
        sales = (root / "sales/entry.py").read_text(encoding="utf-8")
        cancel = purchase[purchase.index("def _cancel("):purchase.index("def cancel(")]
        self.assertIn('express_push.void("Purchase Document"', cancel)  # 「修改」作废原单也走 _cancel
        sales_cancel = sales[sales.index("def cancel("):sales.index("def payment(")]
        self.assertIn('express_push.void("ERP Sales Document"', sales_cancel)


class VoidedSourceGuard(PushModule):
    """领单前兜底（11-13）：来源单据已作废的写账件不再交给电脑（`desktop.still_wanted` → `push.still_wanted`），作废原单那一件照写；
    旧单（非 v2）在 `service.set_status` 作废也撤写账件。"""

    def test_a_voided_source_is_not_wanted_but_its_void_job_is(self):
        for doctype in ("Purchase Document", "ERP Sales Document", "ERP Stock Entry"):
            table = fake.STATE.db.table(doctype)
            table["D-9"] = {"name": "D-9", "status": "void"}
            self.assertFalse(self.push.still_wanted({"source_doctype": doctype, "source_name": "D-9",
                                                     "request_key": "erp:D-9"}), doctype)
            table["D-9"]["status"] = "submitted"
            self.assertTrue(self.push.still_wanted({"source_doctype": doctype, "source_name": "D-9",
                                                    "request_key": "erp:D-9"}), doctype)
        table = fake.STATE.db.table("ERP Stock Entry")
        table["D-9"]["status"] = "void"
        self.assertTrue(self.push.still_wanted({"source_doctype": "ERP Stock Entry", "source_name": "D-9",
                                                "request_key": self.push.stock_entry.VOID_REQUEST + "D-9"}))
        self.assertTrue(self.push.still_wanted({"source_doctype": "ERP Collection", "source_name": "C-1"}))

    def test_the_cancel_of_a_voided_source_clears_the_ledger_columns(self):
        # 领单前撤掉的那件回到产品：单据已作废，账本栏清空（同作废时撤），不记成「失败」
        table = fake.STATE.db.table("Purchase Document")
        table["D-8"] = {"name": "D-8", "status": "void", "ledger_state": "queued", "ledger_job": "J8",
                        "ledger_docnum": "", "ledger_reason": ""}
        self.push.job_changed({"source_doctype": "Purchase Document", "source_name": "D-8", "status": "cancelled",
                               "reason": "voided", "job": "J8", "request_key": "erp:D-8"})
        self.assertEqual((table["D-8"]["ledger_state"], table["D-8"]["ledger_job"], table["D-8"]["ledger_reason"]),
                         (None, None, ""))

    def test_the_old_status_path_and_the_hook_are_wired(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[1] / "apps/pearnly_erp_portal/pearnly_erp_portal"
        service = (root / "purchasing/service.py").read_text(encoding="utf-8")
        body = service[service.index("def set_status("):service.index("def _post_stock(")]
        self.assertIn('express_push.void("Purchase Document", doc.name)', body)
        self.assertLess(body.index("doc.save()"), body.index('express_push.void("Purchase Document"'))
        hooks = (root / "hooks.py").read_text(encoding="utf-8")
        self.assertIn('"still_wanted": "pearnly_erp_portal.express.desktop.still_wanted"', hooks)


class StockVoid(PushModule):
    """B 段 ①：已写进 Express 的出入库单在 ERP 作废 → 排一件作废原单；回执记在作废栏上，写入时的单号和状态不动。"""

    def setUp(self):
        super().setUp()
        self.docs = fake.STATE.db.table("ERP Stock Entry")
        self.enqueued = []

        import datetime as dt
        frappe.utils.get_datetime = lambda value: dt.datetime.fromisoformat(str(value))

    def written(self, **extra):
        self.docs["SE-1"] = {"name": "SE-1", "creation": "2026-10-25 10:00:00", "owner": "a@x", "ledger_state": "written",
                             "ledger_docnum": "OU691025-001", "ledger_void_job": None, **extra}

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
        self.assertEqual((self.docs["SE-1"]["ledger_void_state"], self.docs["SE-1"]["ledger_void_job"]), ("queued", "J9"))
        self.push.job_changed({"source_doctype": "ERP Stock Entry", "source_name": "SE-1", "status": "success", "job": "J9",
                               "request_key": self.enqueued[0]["request_key"], "docnum": "OU691025-001"})
        self.assertEqual(self.docs["SE-1"]["ledger_void_state"], "voided")
        self.assertEqual((self.docs["SE-1"]["ledger_state"], self.docs["SE-1"]["ledger_docnum"]), ("written", "OU691025-001"))
        self.push.job_changed({"source_doctype": "ERP Stock Entry", "source_name": "SE-1", "status": "manual", "job": "J9",
                               "request_key": self.enqueued[0]["request_key"], "reason": "[VOID_DOCUMENT_CHANGED] x"})
        self.assertEqual((self.docs["SE-1"]["ledger_void_state"], self.docs["SE-1"]["ledger_void_reason"]),
                         ("held", "[VOID_DOCUMENT_CHANGED] x"))

    def test_voiding_before_the_computer_wrote_it_cancels_the_queued_job(self):
        from unittest import mock
        self.written(ledger_state="queued", ledger_docnum="", ledger_job="J1")
        cancelled = []
        with mock.patch.object(self.push.jobs, "find_by_name", lambda name: {"job": name, "status": "queued"}), \
                mock.patch.object(self.push.jobs, "cancel", lambda name, reason: cancelled.append((name, reason))):
            self.push.void("ERP Stock Entry", "SE-1")
        self.assertEqual(cancelled, [("J1", "voided")])
        self.assertEqual((self.docs["SE-1"]["ledger_state"], self.docs["SE-1"]["ledger_job"]), (None, None))

    def test_a_write_that_lands_after_the_void_is_voided_at_once(self):
        """电脑正在写（领走了撤不了）时 ERP 作废：写完的回执一到就排作废原单。"""
        from unittest import mock
        self.written(ledger_state="queued", ledger_docnum="", ledger_job="J1", status="void")
        calls = []
        with mock.patch.object(self.push, "void", lambda doctype, name: calls.append(name)), \
                mock.patch.object(self.push, "company_of", lambda doctype, name: None):
            self.push.job_changed({"source_doctype": "ERP Stock Entry", "source_name": "SE-1", "status": "success",
                                   "job": "J1", "request_key": "erp:stock:SE-1@x", "docnum": "OU691025-002"})
        self.assertEqual(calls, ["SE-1"])
        self.assertEqual(self.docs["SE-1"]["ledger_docnum"], "OU691025-002")

    def test_an_entry_never_written_is_only_voided_in_erp(self):
        from unittest import mock
        self.written(ledger_state="held", ledger_docnum="")
        with mock.patch.object(self.push.jobs, "enqueue", lambda **kw: self.enqueued.append(kw) or {"job": "J9"}):
            self.push.void("ERP Stock Entry", "SE-1")
        self.assertEqual(self.enqueued, [])
        self.assertFalse(self.docs["SE-1"].get("ledger_void_state"))

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
        entry.get = lambda key, default=None: getattr(entry, key, default)
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


class LinkStates(PushModule):
    """「接 Express」状态 × 各入口（评估 docs/work/2026-11-01-express-link-states-评估.md 1 节总表，主会话 11-01 审定）：
    写不写只看公司勾着「接 Express」、有接上时刻，且单据开始时刻（盘点 = 开盘点，其余 = 建单，采购「修改」沿原单）不早于它；
    不看此刻 CC 桌面版报没报账套（提交时正好没报的照排，停在没有账套等它回来）；断开优先（撤下、标已断开、不再写）。"""

    LINKED = "2026-10-20 00:00:00"
    BEFORE, AFTER = "2026-10-19 09:00:00", "2026-10-21 09:00:00"

    def setUp(self):
        super().setUp()
        self.link_fixture()

    def link_fixture(self):
        import datetime as dt
        utils = sys.modules["frappe.utils"]
        utils.get_datetime = lambda value: value if isinstance(value, dt.datetime) else dt.datetime.fromisoformat(str(value))
        utils.now_datetime = lambda: dt.datetime(2026, 10, 24, 10, 0)
        patcher = mock.patch.object(frappe, "utils", utils, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.enqueued = []
        for target, value in ((frappe, "enqueue"), (frappe, "publish_realtime")):
            patcher = mock.patch.object(target, value, lambda *a, **kw: self.enqueued.append(kw), create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(frappe.db, "commit", lambda: None, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        fake.STATE.db.table("ERP Warehouse").update({"W1": {"name": "W1", "company": "CO-1"},
                                                     "W9": {"name": "W9", "company": "CO-9"}})
        fake.STATE.db.table("ERP Company")["CO-9"] = {"name": "CO-9", "express_enabled": 1,
                                                      "express_linked_at": self.LINKED}
        masters = types.ModuleType("pearnly_erp_portal.express.masters")  # 主档同步不在这里测：准备时的读取、写成后的同步
        masters.ensure = masters.forget = masters.queue = lambda *a, **k: None
        patcher = mock.patch.dict(sys.modules, {"pearnly_erp_portal.express.masters": masters})
        patcher.start()
        self.addCleanup(patcher.stop)

    def company(self, enabled=1, linked_at=LINKED):
        fake.STATE.db.table("ERP Company")["CO-1"].update(express_enabled=enabled, express_linked_at=linked_at)

    def documents(self, created):
        """五种单各一张，建单 / 开盘点在 ``created``，都已到「可以写了」的状态。"""
        db = fake.STATE.db
        db.table("Purchase Document")["PUR-1"] = {"name": "PUR-1", "status": "submitted", "doc_kind": "purchase_invoice",
                                                  "company": "CO-1", "creation": created}
        db.table("ERP Sales Document")["SD-1"] = {"name": "SD-1", "status": "submitted", "doc_kind": "receipt",
                                                  "company": "CO-1", "creation": created}
        db.table("ERP Stock Entry")["SE-1"] = {"name": "SE-1", "status": "submitted", "direction": "in", "ref_type": None,
                                               "warehouse": "W1", "creation": created}
        db.table("ERP Stock Count")["STC-1"] = {"name": "STC-1", "status": "Posted", "warehouse": "W1", "creation": created}
        db.table("ERP POS Day Summary")["PDS-1"] = {"name": "PDS-1", "status": "submitted", "company": "CO-1",
                                                    "creation": created}
        return {"Purchase Document": "PUR-1", "ERP Sales Document": "SD-1", "ERP Stock Entry": "SE-1",
                "ERP Stock Count": "STC-1", "ERP POS Day Summary": "PDS-1"}

    def queued(self, created):
        """每种单 :func:`push.queue` 后的状态。"""
        out = {}
        for doctype, name in self.documents(created).items():
            self.push.queue(doctype, name)
            out[doctype] = fake.STATE.db.table(doctype)[name].get("ledger_state")
        return out

    def test_unchecked_or_never_linked_companies_write_nothing(self):
        """① 没勾；② 勾了但还没接上（CC 桌面版还没报账套、或报了还没第一次同步落库：没有接上时刻）：哪种单都不排。"""
        for enabled, linked_at in ((0, self.LINKED), (0, None), (1, None)):
            self.company(enabled, linked_at)
            self.assertEqual(set(self.queued(self.AFTER).values()), {None}, (enabled, linked_at))
        self.assertEqual(self.enqueued, [])
        self.assertFalse(self.push.started_linked("Purchase Document", "PUR-1"))
        self.assertIsNone(self.push.linked_at("CO-1"))

    def test_documents_started_after_the_link_are_queued_without_looking_at_the_computer(self):
        """③ 接上：五种单都排（排进后台准备）。判据不看此刻 CC 桌面版报没报账套（④c：提交时正好没报的也排，原来永久漏写）。"""
        self.company()
        with mock.patch.object(self.push, "connected", side_effect=AssertionError("must not look at the books")):
            self.assertEqual(set(self.queued(self.AFTER).values()), {"queued"})
        self.assertEqual(len(self.enqueued), 5)
        self.assertTrue(self.push.started_linked("ERP Stock Count", "STC-1"))

    def test_the_minute_retry_leaves_a_document_waiting_in_a_hands_on_task_to_that_task(self):
        """每分钟那一轮（`poll`）只重试直写停下的单：交给代录、在任务里等电脑的那张不按直写推（原来会被当成直写写进去，
        串了模式），改为叫醒那个任务再按代录交一次（共用层 `tasks.wake_waiting`）。"""
        F = ledger_columns()
        self.company()
        self.documents(self.AFTER)
        self.push.queue("Purchase Document", "PUR-1")
        row = fake.STATE.db.table("Purchase Document")["PUR-1"]
        row.update({F["state"]: "held", F["reason"]: E.NO_BOOK, F["job"]: None})
        from pearnly_core.express_operator import tasks
        from pearnly_erp_portal.express import operator

        sent, woken = [], []
        with mock.patch.object(operator, "waiting_targets", lambda: {("Purchase Document", "PUR-1")}), \
                mock.patch.object(tasks, "wake_waiting", lambda: woken.append(1)), \
                mock.patch.object(self.push, "send", lambda doctype, name, operator=None: sent.append(name)):
            self.push.poll()
        self.assertEqual((sent, woken), ([], [1]))
        with mock.patch.object(operator, "waiting_targets", lambda: set()), \
                mock.patch.object(tasks, "wake_waiting", lambda: None), \
                mock.patch.object(self.push, "send", lambda doctype, name, operator=None: sent.append(name)):
            self.push.poll()
        self.assertEqual(sent, ["PUR-1"])  # 不归代录的照旧每分钟再试

    def test_documents_started_before_the_link_are_never_written(self):
        """④a：开始早于接上时刻（草稿接上前建、接上后提交；接上前开盘点、接上后过账）：不写，也不补。"""
        self.company()
        self.assertEqual(set(self.queued(self.BEFORE).values()), {None})
        self.assertEqual(self.enqueued, [])
        self.push.poll()  # 每分钟那一轮也不会捡起它们
        self.assertEqual(self.enqueued, [])

    def test_kinds_that_never_go_to_express_stay_out(self):
        self.company()
        self.documents(self.AFTER)
        fake.STATE.db.table("ERP Sales Document")["SD-1"]["doc_kind"] = "quotation"
        fake.STATE.db.table("ERP Stock Entry")["SE-1"]["ref_type"] = "count"  # 盘点生成的单：整份盘点写一张 TK
        for doctype, name in (("ERP Sales Document", "SD-1"), ("ERP Stock Entry", "SE-1")):
            self.push.queue(doctype, name)
            self.assertIsNone(fake.STATE.db.table(doctype)[name].get("ledger_state"))

    def test_an_amended_purchase_keeps_the_start_of_the_first_document(self):
        """采购「修改」= 作废原单 + 提交新单：新单沿 ``corrected_from`` 取最早那张的开始时刻。接上前的旧单改了也不推（原单会计可能
        已手录进 Express）；接上后开始的单改了照推。"""
        self.company()
        table = fake.STATE.db.table("Purchase Document")
        for name, created, corrected in (("PUR-OLD", self.BEFORE, None), ("PUR-A1", self.AFTER, "PUR-OLD"),
                                         ("PUR-A2", self.AFTER, "PUR-A1"), ("PUR-NEW", self.AFTER, None),
                                         ("PUR-N1", "2026-10-22 09:00:00", "PUR-NEW")):
            table[name] = {"name": name, "status": "submitted", "doc_kind": "goods", "company": "CO-1", "creation": created,
                           "corrected_from": corrected}
        self.assertEqual(str(self.push.started("Purchase Document", "PUR-A2")), self.BEFORE)
        for name, state in (("PUR-A2", None), ("PUR-N1", "queued")):
            self.push.queue("Purchase Document", name)
            self.assertEqual(table[name].get("ledger_state"), state, name)
        table["PUR-LOOP"] = {"name": "PUR-LOOP", "creation": self.AFTER, "corrected_from": "PUR-LOOP"}  # 坏数据成环：不死循环
        self.assertEqual(str(self.push.started("Purchase Document", "PUR-LOOP")), self.AFTER)

    def test_masters_made_with_a_document_belong_to_the_company_only_if_it_started_linked(self):
        """随单 / 盘点里新建的客户、供应商、商品：单据开始时已接上才归这家公司（写进去后挂码），否则是各家共用的本地档。
        盘点按开盘点那一刻：开盘点没接、中途接上，盘点里建的物料仍是本地档（这份盘点也不写 TK）。"""
        self.company()
        self.assertEqual(self.push.owner("CO-1", self.AFTER), "CO-1")
        self.assertIsNone(self.push.owner("CO-1", self.BEFORE))
        self.assertEqual(self.push.owner("CO-1", None), "CO-1")  # 新单：开始时刻就是此刻
        self.company(linked_at=None)
        self.assertIsNone(self.push.owner("CO-1", None))
        self.assertIsNone(self.push.owner(None, self.AFTER))
        source = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/inventory/count.py").read_text(encoding="utf-8")
        self.assertIn('"express_company": express_push.owner(company, task.creation)', source)
        for path, owner in (("purchasing/entry.py", "express_push.owner(company, started)"),
                            ("sales/entry.py", "express_push.owner(company, started)")):
            self.assertIn(owner, (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal" / path).read_text(encoding="utf-8"))

    def test_a_document_queued_while_the_computer_is_away_waits_and_is_written_when_it_returns(self):
        """④c：提交时 CC 桌面版没报账套 → 照排，准备时停在「没有账套」（会自己好的原因）；每分钟再试，电脑回来就交进写账队列。"""
        self.company()
        self.documents(self.AFTER)
        self.push.queue("Purchase Document", "PUR-1")
        row = fake.STATE.db.table("Purchase Document")["PUR-1"]
        self.assertEqual(row["ledger_state"], "queued")
        results = iter([{"hold": E.NO_BOOK}, {"payload": {"payload_version": 4}, "book": {"key": "BK"}}])
        made = []
        with mock.patch.object(self.push, "document", lambda doctype, name: {"ref": "PUR-1@x", "number": "IV-1"}), \
                mock.patch.object(self.push, "_company", lambda name: COMPANY), \
                mock.patch.object(self.push.prepare, "prepare", lambda *a, **k: next(results)), \
                mock.patch.object(self.push, "actor_of", lambda *a: "a@x"), \
                mock.patch.object(self.push.jobs, "of_source", lambda *a: []), \
                mock.patch.object(self.push.jobs, "enqueue", lambda **kw: made.append(kw) or {"job": "J1"}):
            self.push.send("Purchase Document", "PUR-1")
            self.assertEqual((row["ledger_state"], row["ledger_reason"]), ("held", E.NO_BOOK))
            # A 线 11-12 A9：「没有账套」不算等电脑（判定、计划如实报），每分钟那一轮仍再看（电脑报了账套就接着写）
            self.assertIn(E.NO_BOOK, E.POLL_REASONS)
            self.assertNotIn(E.NO_BOOK, E.RETRY_REASONS)
            self.push.poll()
        self.assertEqual((len(made), row["ledger_job"]), (1, "J1"))

    def test_two_preparations_of_one_document_never_queue_a_second_job_or_write_back_a_stale_stop(self):
        """11-16 管钱缺陷（验收首跑 PDS-2026-00798）：同一张单的两次准备交错——后台那次在答题前算出「停下、要问」、晚落库，
        把答题后那次写下的账套盖空、要问的项写回；按此刻件数现算请求号时还排出第二件（#2）、写两次账。现在：开头行锁读
        （for_update），写回前锁内重读账本栏、变了就放弃；锁里仍见没撤销的件就接回它、不再排；请求号按撤过的件编号。"""
        self.company()
        self.documents(self.AFTER)
        self.push.queue("Purchase Document", "PUR-1")
        row = fake.STATE.db.table("Purchase Document")["PUR-1"]
        locked, made, earlier = [], [], []
        real_get = frappe.db.get_value

        def get_value(doctype, name, fields="name", as_dict=False, for_update=False):
            if doctype == "Purchase Document" and for_update:
                locked.append(name)
            return real_get(doctype, name, fields, as_dict=as_dict, for_update=for_update)

        outcome = {"payload": {"payload_version": 4}, "book": {"key": "BK"}}
        prepared = lambda *a, **k: outcome
        with mock.patch.object(self.push, "document", lambda doctype, name: {"ref": "PUR-1@x", "number": "IV-1"}), \
                mock.patch.object(self.push, "_company", lambda name: COMPANY), \
                mock.patch.object(frappe.db, "get_value", get_value), \
                mock.patch.object(self.push, "actor_of", lambda *a: "a@x"), \
                mock.patch.object(self.push.jobs, "of_source", lambda *a: list(earlier)), \
                mock.patch.object(self.push.jobs, "enqueue", lambda **kw: made.append(kw) or {"job": f"J{len(made)}"}), \
                mock.patch.object(self.push.prepare, "prepare", lambda *a, **k: prepared()):
            # 另一次准备已排了一件（锁里看得到它，单据上的 ledger_job 还没写上）：不再排，接回那一件
            earlier[:] = [{"job": "J0", "status": "queued", "request_key": "erp:purchase:PUR-1@x"}]
            self.assertEqual(self.push.send("Purchase Document", "PUR-1"), {"status": "already_queued"})
            self.assertEqual((made, row["ledger_job"]), ([], "J0"))
            self.assertIn("PUR-1", locked)  # 开头就是行锁读
            # 准备途中有人答了题、另一次准备已排件（账本栏变了）：放弃这次的结论，不写回、不排
            row.update(ledger_job=None, ledger_state="held", ledger_reason="choice_needed:vat_registered",
                       ledger_needed='[{"kind": "vat_registered"}]', ledger_book=None)
            earlier[:] = []
            def answered_meanwhile():
                row.update(ledger_job="J9", ledger_state="queued", ledger_reason="", ledger_needed=None, ledger_book="BK")
                return {"hold": "choice_needed:vat_registered", "needed": [{"kind": "vat_registered"}]}
            prepared = answered_meanwhile
            self.assertEqual(self.push.send("Purchase Document", "PUR-1")["status"], "already_queued")
            self.assertEqual((made, row["ledger_job"], row["ledger_book"], row["ledger_needed"]), ([], "J9", "BK", None))
            # 用户「再写一次」撤了旧件后再交：请求号按撤过的件编号 #2（不按此刻件数现算出又一件）
            row.update(ledger_job=None, ledger_state="queued", ledger_reason="", ledger_needed=None)
            earlier[:] = [{"job": "J9", "status": "cancelled", "request_key": "erp:purchase:PUR-1@x"}]
            prepared = lambda *a, **k: outcome
            self.assertEqual(self.push.send("Purchase Document", "PUR-1"), {"status": "sent"})
            self.assertEqual([kw["request_key"] for kw in made], ["erp:purchase:PUR-1@x#2"])

    def detach_fixture(self):
        db = fake.STATE.db
        rows = {"SE-Q": ("queued", "J1"), "SE-H": ("held", None), "SE-M": ("held", "J2"), "SE-L": ("queued", "J3"),
                "SE-W": ("written", "J4")}
        for name, (state, job) in rows.items():
            db.table("ERP Stock Entry")[name] = {"name": name, "status": "submitted", "warehouse": "W1",
                                                 "creation": self.AFTER, "ledger_state": state, "ledger_job": job,
                                                 "ledger_reason": "x", "ledger_docnum": "OU-1" if state == "written" else ""}
        db.table("ERP Stock Entry")["SE-OTHER"] = {"name": "SE-OTHER", "status": "submitted", "warehouse": "W9",
                                                   "creation": self.AFTER, "ledger_state": "queued", "ledger_job": None}
        db.table("Purchase Document")["PUR-H"] = {"name": "PUR-H", "status": "submitted", "company": "CO-1",
                                                  "creation": self.AFTER, "ledger_state": "held", "ledger_job": None}
        return {"J1": "queued", "J2": "manual", "J3": "leased", "J4": "done"}

    def test_disconnecting_withdraws_what_is_not_written_and_marks_it_detached(self):
        """④b / 断开优先：断开时这家公司排队中、停下的单撤掉还没被电脑领走的写账件，单据照实标「已断开」（不清空、页面看得见）；
        电脑正在写的撤不了，结果回来：写成了记写入，没写成标已断开。已写进去的、别家公司的不动。"""
        self.company()
        statuses = self.detach_fixture()
        cancelled = []
        with mock.patch.object(self.push.jobs, "find_by_name", lambda name: {"job": name, "status": statuses[name]}), \
                mock.patch.object(self.push.jobs, "cancel", lambda name, reason: cancelled.append((name, reason))):
            self.company(enabled=0, linked_at=None)
            self.assertEqual(self.push.detach("CO-1"), 4)
        self.assertEqual(sorted(cancelled), [("J1", "disconnected"), ("J2", "disconnected")])
        table = fake.STATE.db.table("ERP Stock Entry")
        state = lambda doctype, name: (fake.STATE.db.table(doctype)[name]["ledger_state"],  # noqa: E731
                                       fake.STATE.db.table(doctype)[name].get("ledger_reason"))
        for name in ("SE-Q", "SE-H", "SE-M"):
            self.assertEqual(state("ERP Stock Entry", name), (E.DETACHED, E.DISCONNECTED), name)
        self.assertEqual(state("Purchase Document", "PUR-H"), (E.DETACHED, E.DISCONNECTED))
        self.assertEqual(table["SE-L"]["ledger_state"], "queued")  # 电脑正在写：等结果
        self.assertEqual(table["SE-W"]["ledger_state"], "written")
        self.assertEqual(table["SE-OTHER"]["ledger_state"], "queued")
        with mock.patch.object(self.push, "link_party", lambda *a: None), \
                mock.patch.object(self.push, "link_products", lambda *a: None):
            self.push.job_changed({"source_doctype": "ERP Stock Entry", "source_name": "SE-L", "status": "manual",
                                   "job": "J3", "request_key": "erp:stock:SE-L@x", "reason": "[PERIOD_LOCKED] x"})
        self.assertEqual(state("ERP Stock Entry", "SE-L"), (E.DETACHED, E.DISCONNECTED))
        table["SE-L"]["ledger_state"] = "queued"
        with mock.patch.object(self.push, "link_party", lambda *a: None), \
                mock.patch.object(self.push, "link_products", lambda *a: None):
            self.push.job_changed({"source_doctype": "ERP Stock Entry", "source_name": "SE-L", "status": "success",
                                   "job": "J3", "request_key": "erp:stock:SE-L@x", "docnum": "OU-9"})
        self.assertEqual((table["SE-L"]["ledger_state"], table["SE-L"]["ledger_docnum"]), ("written", "OU-9"))

    def test_after_disconnecting_send_poll_answer_retry_and_mark_handled_write_nothing(self):
        """断开后 send / poll / answer / retry / mark_handled 都复核勾选：不再准备、不再交进写账队列（原来照写，评估 4.a 反证 4）。"""
        self.company(enabled=0, linked_at=None)
        table = fake.STATE.db.table("Purchase Document")
        table["PUR-1"] = {"name": "PUR-1", "status": "submitted", "company": "CO-1", "creation": self.AFTER,
                          "ledger_state": "queued", "ledger_job": None, "ledger_reason": ""}
        with mock.patch.object(self.push.prepare, "prepare", side_effect=AssertionError("must not prepare")), \
                mock.patch.object(self.push.jobs, "enqueue", side_effect=AssertionError("must not queue")):
            self.push.poll()
        self.assertEqual((table["PUR-1"]["ledger_state"], table["PUR-1"]["ledger_reason"]), (E.DETACHED, E.DISCONNECTED))
        utils = sys.modules["frappe.utils"]
        for name, value in (("now", lambda: "2026-10-24 00:00:00"), ("flt", lambda v, *a: float(v or 0)),
                            ("cint", lambda v: int(v or 0)), ("getdate", lambda v=None: v), ("add_days", lambda d, n: d)):
            setattr(utils, name, getattr(utils, name, value))
        from pearnly_erp_portal.express import api
        row = types.SimpleNamespace(name="PDS-1", company="CO-1", ledger_state="held", ledger_job=None,
                                    ledger_void_state=None, ledger_book=None, ledger_docnum="", ledger_reason="x",
                                    ledger_needed=None, get=lambda key: None)
        with mock.patch.object(api, "_document", lambda doctype, name, write=False: row), \
                mock.patch.object(api.push, "send", side_effect=AssertionError("must not send")):
            for call in (lambda: api.answer("ERP POS Day Summary", "PDS-1", "[]"),
                         lambda: api.retry("ERP POS Day Summary", "PDS-1"),
                         lambda: api.mark_handled("ERP POS Day Summary", "PDS-1", reason="x")):
                with self.assertRaises(frappe.PermissionError):
                    call()
            view = api._view("ERP POS Day Summary", row)
        self.assertEqual((view["state"], view["can_answer"], view["can_retry"], view["can_mark_handled"]),
                         ("held", False, False, False))

    def test_a_written_entry_voided_after_disconnecting_still_voids_in_express(self):
        """④d：已写进 Express 的出入库单断开后在 ERP 作废，照样排 Express 作废原单（那张单确实在 Express 里）。"""
        self.company(enabled=0, linked_at=None)
        fake.STATE.db.table("ERP Stock Entry")["SE-1"] = {"name": "SE-1", "creation": "2026-10-25 10:00:00", "owner": "a@x",
                                                          "warehouse": "W1", "ledger_state": "written",
                                                          "ledger_docnum": "OU691025-001", "ledger_void_job": None}
        made = []
        with mock.patch.object(self.push, "_company", lambda name: COMPANY), \
                mock.patch.object(self.push.stock_entry, "void_payload",
                                  lambda name, company: {"payload": {"payload_version": 8}, "book": {"key": "BK"}}), \
                mock.patch.object(self.push.jobs, "enqueue", lambda **kw: made.append(kw) or {"job": "J9"}):
            self.push.void("ERP Stock Entry", "SE-1")
        self.assertEqual((len(made), fake.STATE.db.table("ERP Stock Entry")["SE-1"]["ledger_void_state"]), (1, "queued"))

    def test_the_form_cannot_toggle_the_link_and_disconnect_clears_the_link_time(self):
        """公司接入重做第 2 批（11-14）：「接 Express」不再由表单勾，来源决定是否互通——新建 / 改公司交来的 express_enabled 一律不收；
        断开只走「断开」（清接上时刻、撤单），之后重新接上只走「从连接添加」（`cc_service.create`），落库那一刻重记接上时刻。"""
        from pearnly_erp_portal.masters import company as companies
        table = fake.STATE.db.table("ERP Company")
        self.company()
        table["CO-1"].update(company_name="CO", tax_id=OWN, branch="00012", vat_registered="yes", express_enabled=1,
                             express_linked_at=self.LINKED)
        detached, synced = [], []
        masters = sys.modules["pearnly_erp_portal.express.masters"]
        with mock.patch.object(companies.guard, "require", lambda *a: None), \
                mock.patch.object(companies, "document_company", lambda name: name), \
                mock.patch.object(self.push, "detach", lambda name: detached.append(name)), \
                mock.patch.object(self.push, "connected", lambda name: True), \
                mock.patch.object(masters, "queue", synced.append), \
                mock.patch.dict(sys.modules, {"pearnly_erp_portal.express.push": self.push}):
            with self.assertRaises(frappe.PermissionError):
                companies.update_company("CO-1", express_enabled=0)  # 表单交来要改它的：明确拒绝
            companies.update_company("CO-1", express_enabled=1)  # 与现状相同（旧页面整份交回）：照旧忽略
            self.assertEqual((table["CO-1"]["express_enabled"], table["CO-1"]["express_linked_at"], detached, synced),
                             (1, self.LINKED, [], []))
            companies.disconnect_express("CO-1")
            self.assertEqual((table["CO-1"]["express_enabled"], table["CO-1"]["express_linked_at"], detached),
                             (0, None, ["CO-1"]))
            with self.assertRaises(frappe.PermissionError):
                companies.update_company("CO-1", express_enabled=1)  # 断开后也不能靠表单接回
            self.assertEqual((table["CO-1"]["express_enabled"], synced), (0, []))

    def test_the_cashier_day_takes_only_receipts_sold_after_the_link(self):
        """收银日汇总 / 退货单只收接上时刻之后卖 / 退的小票（原来按第一次同步那天整天算，断开后重新接上会把断开期间的补写进去，
        评估 4.a 反证 1）；每晚只轮到勾着、有接上时刻的公司（不看此刻 CC 桌面版报没报账套）。"""
        from pearnly_erp_portal.express import pos_day
        for scope in (pos_day._RECEIPTS, pos_day._REFUNDS):
            self.assertIn("c.express_enabled = 1 AND c.express_linked_at IS NOT NULL AND s.sold_at >= c.express_linked_at",
                          scope)
            self.assertNotIn("express_pos_from", scope)
        self.company()
        fake.STATE.db.table("ERP Company")["CO-1"]["is_active"] = 1
        fake.STATE.db.table("ERP Company")["CO-2"] = {"name": "CO-2", "is_active": 1, "express_enabled": 1}
        fake.STATE.db.table("ERP Company")["CO-3"] = {"name": "CO-3", "is_active": 1, "express_enabled": 0,
                                                      "express_linked_at": self.LINKED}
        self.assertEqual([row.name for row in pos_day.linked_companies()], ["CO-1"])

    def test_printing_follows_the_document_not_the_company_at_print_time(self):
        """销售单标题按单据自己的 Express 状态（排上 / 写成 → 送货单），不按打印时公司接没接；已断开、从没排上的打非税收据。"""
        self.assertEqual([E.in_express(state) for state in (None, "", "queued", "held", "written", "detached")],
                         [False, False, True, True, True, False])
        source = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/printing/context.py").read_text(encoding="utf-8")
        self.assertIn('express_contracts.in_express(doc.get("ledger_state"))', source)
        self.assertNotIn("push.linked(doc.company)", source)


def ledger_columns():
    """单据上账本栏的实际列名：改列后是通用的 ``ledger_*``（评估 2026-11-02 §12 第 6 条），改列前是 ``express_*``（DocType 里
    还有旧列就是改列前）。下面「链接状态四条规则」那组用例在改列前后同一份用例跑，结论比对同一张表。"""
    meta = json.loads((ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/pearnly_erp_portal/doctype/purchase_document/"
                       "purchase_document.json").read_text(encoding="utf-8"))
    prefix = "express_" if any(field["fieldname"] == "express_state" for field in meta["fields"]) else "ledger_"
    return {key: prefix + key for key in ("state", "docnum", "reason", "job", "book", "needed", "void_state",
                                          "void_reason", "void_job")}


# 改列前（main 9be471c5，express_*）跑出的结论；改列后同一组输入必须得到同一张表（链接状态四条规则语义不变）
LINK_RULES_GOLDEN = {'amended|PUR-A2': None,
 'amended|PUR-N1': 'queued',
 'detached|poll': ['detached', 'express.disconnected'],
 'detach|PUR-H': ['detached', 'express.disconnected', None],
 'detach|SE-H': ['detached', 'express.disconnected', None],
 'detach|SE-L': ['queued', 'x', None],
 'detach|SE-M': ['detached', 'express.disconnected', None],
 'detach|SE-OTHER': ['queued', None, None],
 'detach|SE-Q': ['detached', 'express.disconnected', None],
 'detach|SE-W': ['written', 'x', None],
 'detach|cancelled': [['J1', 'disconnected'], ['J2', 'disconnected']],
 'detach|count': 4,
 'detach|leased_manual': ['detached', 'express.disconnected', 'J3'],
 'detach|leased_success': ['written', 'OU-9', ''],
 'no_book|poll': ['held', 'express.account_set_missing', 'J1', 'BK'],
 'no_book|send': ['held', 'express.account_set_missing', None],
 'queue|0|False|after|ERP POS Day Summary': None,
 'queue|0|False|after|ERP Sales Document': None,
 'queue|0|False|after|ERP Stock Count': None,
 'queue|0|False|after|ERP Stock Entry': None,
 'queue|0|False|after|Purchase Document': None,
 'queue|0|False|before|ERP POS Day Summary': None,
 'queue|0|False|before|ERP Sales Document': None,
 'queue|0|False|before|ERP Stock Count': None,
 'queue|0|False|before|ERP Stock Entry': None,
 'queue|0|False|before|Purchase Document': None,
 'queue|0|True|after|ERP POS Day Summary': None,
 'queue|0|True|after|ERP Sales Document': None,
 'queue|0|True|after|ERP Stock Count': None,
 'queue|0|True|after|ERP Stock Entry': None,
 'queue|0|True|after|Purchase Document': None,
 'queue|0|True|before|ERP POS Day Summary': None,
 'queue|0|True|before|ERP Sales Document': None,
 'queue|0|True|before|ERP Stock Count': None,
 'queue|0|True|before|ERP Stock Entry': None,
 'queue|0|True|before|Purchase Document': None,
 'queue|1|False|after|ERP POS Day Summary': None,
 'queue|1|False|after|ERP Sales Document': None,
 'queue|1|False|after|ERP Stock Count': None,
 'queue|1|False|after|ERP Stock Entry': None,
 'queue|1|False|after|Purchase Document': None,
 'queue|1|False|before|ERP POS Day Summary': None,
 'queue|1|False|before|ERP Sales Document': None,
 'queue|1|False|before|ERP Stock Count': None,
 'queue|1|False|before|ERP Stock Entry': None,
 'queue|1|False|before|Purchase Document': None,
 'queue|1|True|after|ERP POS Day Summary': 'queued',
 'queue|1|True|after|ERP Sales Document': 'queued',
 'queue|1|True|after|ERP Stock Count': 'queued',
 'queue|1|True|after|ERP Stock Entry': 'queued',
 'queue|1|True|after|Purchase Document': 'queued',
 'queue|1|True|before|ERP POS Day Summary': None,
 'queue|1|True|before|ERP Sales Document': None,
 'queue|1|True|before|ERP Stock Count': None,
 'queue|1|True|before|ERP Stock Entry': None,
 'queue|1|True|before|Purchase Document': None,
 'void|held': [0, None, None, None],
 'void|queued': [0, None, None, None],
 'void|written': [1, 'written', 'queued', 'J9']}


class LinkRulesUnchanged(PushModule):
    """账本栏改成通用列（评估 2026-11-02 §12.1 #6）不改「接 Express」四条规则：①② 不排、③ 排且不看此刻电脑、④a 开始早于
    接上时刻不排不补（采购「修改」沿原单）、④b 断开优先（撤下标已断开，正在写的看结果）、④c 没报账套停下等、④d 断开后作废
    已写的照排。同一组输入在改列前后各跑一遍，把结论收成一张与列名无关的表比对（`LINK_RULES_GOLDEN`）。"""

    LINKED, BEFORE, AFTER = LinkStates.LINKED, LinkStates.BEFORE, LinkStates.AFTER

    def setUp(self):
        super().setUp()
        LinkStates.link_fixture(self)  # 同一套替身（同 LinkStates）

    company = LinkStates.company
    documents = LinkStates.documents

    def outcome(self):
        F = ledger_columns()
        out = {}
        # ①②③④a：四种公司状态 × 开始早于 / 晚于接上 × 五种单
        for enabled, linked_at in ((0, self.LINKED), (0, None), (1, None), (1, self.LINKED)):
            for label, created in (("before", self.BEFORE), ("after", self.AFTER)):
                fake.reset(self.tmp.name)
                self.setUp_tables()
                self.company(enabled, linked_at)
                for doctype, name in self.documents(created).items():
                    self.push.queue(doctype, name)
                    out[f"queue|{enabled}|{bool(linked_at)}|{label}|{doctype}"] = \
                        fake.STATE.db.table(doctype)[name].get(F["state"])
        # 采购「修改」沿原单的开始时刻
        fake.reset(self.tmp.name)
        self.setUp_tables()
        self.company()
        table = fake.STATE.db.table("Purchase Document")
        for name, created, corrected in (("PUR-OLD", self.BEFORE, None), ("PUR-A1", self.AFTER, "PUR-OLD"),
                                         ("PUR-A2", self.AFTER, "PUR-A1"), ("PUR-NEW", self.AFTER, None),
                                         ("PUR-N1", "2026-10-22 09:00:00", "PUR-NEW")):
            table[name] = {"name": name, "status": "submitted", "doc_kind": "goods", "company": "CO-1",
                           "creation": created, "corrected_from": corrected}
        for name in ("PUR-A2", "PUR-N1"):
            self.push.queue("Purchase Document", name)
            out[f"amended|{name}"] = table[name].get(F["state"])
        # ④c：没报账套 → 停在没有账套，每分钟再试，电脑回来交进写账队列
        fake.reset(self.tmp.name)
        self.setUp_tables()
        self.company()
        self.documents(self.AFTER)
        self.push.queue("Purchase Document", "PUR-1")
        row = fake.STATE.db.table("Purchase Document")["PUR-1"]
        results = iter([{"hold": E.NO_BOOK}, {"payload": {"payload_version": 4}, "book": {"key": "BK"}}])
        with mock.patch.object(self.push, "document", lambda doctype, name: {"ref": "PUR-1@x", "number": "IV-1"}), \
                mock.patch.object(self.push, "_company", lambda name: COMPANY), \
                mock.patch.object(self.push.prepare, "prepare", lambda *a, **k: next(results)), \
                mock.patch.object(self.push, "actor_of", lambda *a: "a@x"), \
                mock.patch.object(self.push.jobs, "of_source", lambda *a: []), \
                mock.patch.object(self.push.jobs, "enqueue", lambda **kw: {"job": "J1"}):
            self.push.send("Purchase Document", "PUR-1")
            out["no_book|send"] = [row.get(F["state"]), row.get(F["reason"]), row.get(F["job"])]
            self.push.poll()
            out["no_book|poll"] = [row.get(F["state"]), row.get(F["reason"]), row.get(F["job"]), row.get(F["book"])]
        # ④b：断开优先
        fake.reset(self.tmp.name)
        self.setUp_tables()
        self.company()
        db = fake.STATE.db
        for name, (state, job) in {"SE-Q": ("queued", "J1"), "SE-H": ("held", None), "SE-M": ("held", "J2"),
                                   "SE-L": ("queued", "J3"), "SE-W": ("written", "J4")}.items():
            db.table("ERP Stock Entry")[name] = {"name": name, "status": "submitted", "warehouse": "W1",
                                                 "creation": self.AFTER, F["state"]: state, F["job"]: job,
                                                 F["reason"]: "x", F["docnum"]: "OU-1" if state == "written" else ""}
        db.table("ERP Stock Entry")["SE-OTHER"] = {"name": "SE-OTHER", "status": "submitted", "warehouse": "W9",
                                                   "creation": self.AFTER, F["state"]: "queued", F["job"]: None}
        db.table("Purchase Document")["PUR-H"] = {"name": "PUR-H", "status": "submitted", "company": "CO-1",
                                                  "creation": self.AFTER, F["state"]: "held", F["job"]: None}
        statuses = {"J1": "queued", "J2": "manual", "J3": "leased", "J4": "done"}
        cancelled = []
        with mock.patch.object(self.push.jobs, "find_by_name", lambda name: {"job": name, "status": statuses[name]}), \
                mock.patch.object(self.push.jobs, "cancel", lambda name, reason: cancelled.append([name, reason])):
            self.company(enabled=0, linked_at=None)
            out["detach|count"] = self.push.detach("CO-1")
        out["detach|cancelled"] = sorted(cancelled)
        for doctype, name in (("ERP Stock Entry", "SE-Q"), ("ERP Stock Entry", "SE-H"), ("ERP Stock Entry", "SE-M"),
                              ("ERP Stock Entry", "SE-L"), ("ERP Stock Entry", "SE-W"), ("ERP Stock Entry", "SE-OTHER"),
                              ("Purchase Document", "PUR-H")):
            got = db.table(doctype)[name]
            out[f"detach|{name}"] = [got.get(F["state"]), got.get(F["reason"]), got.get(F["needed"])]
        with mock.patch.object(self.push, "link_party", lambda *a: None), \
                mock.patch.object(self.push, "link_products", lambda *a: None):
            self.push.job_changed({"source_doctype": "ERP Stock Entry", "source_name": "SE-L", "status": "manual",
                                   "job": "J3", "request_key": "erp:stock:SE-L@x", "reason": "[PERIOD_LOCKED] x"})
            got = db.table("ERP Stock Entry")["SE-L"]
            out["detach|leased_manual"] = [got.get(F["state"]), got.get(F["reason"]), got.get(F["job"])]
            got[F["state"]] = "queued"
            self.push.job_changed({"source_doctype": "ERP Stock Entry", "source_name": "SE-L", "status": "success",
                                   "job": "J3", "request_key": "erp:stock:SE-L@x", "docnum": "OU-9"})
            out["detach|leased_success"] = [got.get(F["state"]), got.get(F["docnum"]), got.get(F["reason"])]
        # 断开后每分钟那一轮不写，照实标已断开
        fake.reset(self.tmp.name)
        self.setUp_tables()
        self.company(enabled=0, linked_at=None)
        fake.STATE.db.table("Purchase Document")["PUR-1"] = {"name": "PUR-1", "status": "submitted", "company": "CO-1",
                                                             "creation": self.AFTER, F["state"]: "queued", F["job"]: None,
                                                  F["reason"]: ""}
        with mock.patch.object(self.push.prepare, "prepare", side_effect=AssertionError("must not prepare")):
            self.push.poll()
        got = fake.STATE.db.table("Purchase Document")["PUR-1"]
        out["detached|poll"] = [got.get(F["state"]), got.get(F["reason"])]
        # ④d：断开后作废已写的出入库单照排 Express 作废；没写成的只在 ERP 作废
        for state, docnum in (("written", "OU691025-001"), ("held", ""), ("queued", "")):
            fake.reset(self.tmp.name)
            self.setUp_tables()
            self.company(enabled=0, linked_at=None)
            fake.STATE.db.table("ERP Stock Entry")["SE-1"] = {
                "name": "SE-1", "creation": "2026-10-25 10:00:00", "owner": "a@x", "warehouse": "W1",
                F["state"]: state, F["docnum"]: docnum, F["void_job"]: None, F["job"]: None}
            made = []
            with mock.patch.object(self.push, "_company", lambda name: COMPANY), \
                    mock.patch.object(self.push.stock_entry, "void_payload",
                                      lambda name, company: {"payload": {"payload_version": 8}, "book": {"key": "BK"}}), \
                    mock.patch.object(self.push.jobs, "enqueue", lambda **kw: made.append(kw) or {"job": "J9"}):
                self.push.void("ERP Stock Entry", "SE-1")
            got = fake.STATE.db.table("ERP Stock Entry")["SE-1"]
            out[f"void|{state}"] = [len(made), got.get(F["state"]), got.get(F["void_state"]), got.get(F["void_job"])]
        return out

    def setUp_tables(self):
        fake.STATE.db.table("ERP Company")["CO-1"] = {"name": "CO-1", "express_enabled": 1,
                                                      "express_linked_at": "2026-10-01 00:00:00"}
        fake.STATE.db.table("ERP Company")["CO-9"] = {"name": "CO-9", "express_enabled": 1,
                                                      "express_linked_at": self.LINKED}
        fake.STATE.db.table("ERP Warehouse").update({"W1": {"name": "W1", "company": "CO-1"},
                                                     "W9": {"name": "W9", "company": "CO-9"}})

    def test_the_four_link_rules_reach_the_same_conclusions_before_and_after_the_columns_change(self):
        got = json.loads(json.dumps(self.outcome(), default=str))
        if LINK_RULES_GOLDEN is None:
            print(json.dumps(got, ensure_ascii=False, sort_keys=True))
            self.skipTest("golden not recorded")
        self.assertEqual(got, LINK_RULES_GOLDEN)


class LedgerColumnsPatch(unittest.TestCase):
    """补丁 v1_24（评估 2026-11-02 §12 第 6 条）：五种单的 ``express_*`` 抄到通用的 ``ledger_*``、记 ``ledger_target = express``；
    从没排进过 Express 的单不动；只抄还没抄过的（重跑不拿旧值盖新状态）；存着的视图改列名；回退把新列抄回旧列、清标记重跑。
    SQL 在内存 SQLite 上真跑（同一条 UPDATE，反引号与 COALESCE 两边都认）。"""

    DOCTYPES = ("Purchase Document", "ERP Sales Document", "ERP Stock Entry", "ERP Stock Count", "ERP POS Day Summary")
    NAMES = ("state", "docnum", "reason", "sent_at", "job", "book", "needed")
    VOIDS = ("void_state", "void_reason", "void_job")

    def setUp(self):
        import sqlite3
        self.conn = sqlite3.connect(":memory:")
        for doctype in self.DOCTYPES:
            names = self.NAMES + (self.VOIDS if doctype == "ERP Stock Entry" else ())
            columns = ["name TEXT PRIMARY KEY"] + [f"{prefix}_{name} TEXT" for prefix in ("express", "ledger")
                                                   for name in names] + ["ledger_target TEXT"]
            self.conn.execute(f"CREATE TABLE `tab{doctype}` ({', '.join(columns)})")
        self.conn.execute("CREATE TABLE `tabPatch Log` (patch TEXT)")
        self.conn.execute("CREATE TABLE `__UserSettings` (user TEXT, doctype TEXT, data TEXT)")
        self.cache = {}
        self.conn.execute("INSERT INTO `tabPatch Log` VALUES ('pearnly_erp_portal.patches.v1_24_ledger_state')")
        self.renamed = []
        db = types.SimpleNamespace(
            table_exists=lambda doctype, **kw: bool(self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name = ?", (f"tab{doctype}",)).fetchone()),
            get_tables=lambda **kw: [row[0] for row in self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")],
            has_column=lambda doctype, column: column in [row[1] for row in self.conn.execute(
                f"PRAGMA table_info(`tab{doctype}`)")],
            sql=self.sql,
            delete=lambda doctype, filters: self.conn.execute(f"DELETE FROM `tab{doctype}` WHERE patch = ?",
                                                              (filters["patch"],)),
            commit=lambda: None)
        helpers = types.ModuleType("frappe.model.utils.rename_field")
        for name in ("update_reports", "update_users_report_view_settings", "update_property_setters"):
            setattr(helpers, name, lambda doctype, old, new, name=name: self.renamed.append((name, doctype, old, new)))
        cache = types.SimpleNamespace(hgetall=lambda name: dict(self.cache),
                                      hdel=lambda name, key: self.cache.pop(key, None))
        for patcher in (mock.patch.object(frappe, "db", db), mock.patch.object(frappe, "cache", cache, create=True),
                        mock.patch.object(frappe, "safe_decode", lambda value: value, create=True),
                        mock.patch.dict(sys.modules, {"frappe.model": types.ModuleType("frappe.model"),
                                                      "frappe.model.utils": types.ModuleType("frappe.model.utils"),
                                                      "frappe.model.utils.rename_field": helpers})):
            patcher.start()
            self.addCleanup(patcher.stop)
        sys.modules.pop("pearnly_erp_portal.patches.v1_24_ledger_state", None)
        from pearnly_erp_portal.patches import v1_24_ledger_state as patch
        self.patch = patch

    def sql(self, query, values=(), as_dict=False):
        cursor = self.conn.execute(query.replace("%s", "?"), tuple(values))
        rows = cursor.fetchall()
        if not as_dict:
            return rows
        names = [column[0] for column in cursor.description]
        return [types.SimpleNamespace(**dict(zip(names, row))) for row in rows]

    def put(self, doctype, name, **values):
        columns = ["name", *values]
        self.conn.execute(f"INSERT INTO `tab{doctype}` ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
                          (name, *values.values()))

    def row(self, doctype, name):
        cursor = self.conn.execute(f"SELECT * FROM `tab{doctype}` WHERE name = ?", (name,))
        return dict(zip([column[0] for column in cursor.description], cursor.fetchone()))

    def fixture(self):
        self.put("Purchase Document", "PUR-W", express_state="written", express_docnum="RR6901-001", express_job="J1",
                 express_book="BK", express_sent_at="2026-10-24 10:00:00")
        self.put("Purchase Document", "PUR-H", express_state="held", express_reason="choice_needed:account",
                 express_needed='[{"kind": "account"}]', express_book="BK")
        self.put("Purchase Document", "PUR-NEVER")
        self.put("ERP Sales Document", "SD-D", express_state="detached", express_reason="express.disconnected")
        self.put("ERP Stock Entry", "SE-V", express_state="written", express_docnum="OU-1", express_job="J2",
                 express_void_state="queued", express_void_job="J3", express_void_reason="")
        self.put("ERP Stock Entry", "SE-SENT", express_sent_at="2026-10-25 08:00:00")  # 只有送出时刻也算排进过
        self.put("ERP Stock Count", "STC-1", express_state="queued")
        self.put("ERP POS Day Summary", "PDS-1", express_state="handled")

    def test_old_columns_are_copied_and_marked_express(self):
        self.fixture()
        self.patch.execute()
        written = self.row("Purchase Document", "PUR-W")
        self.assertEqual({key: written[key] for key in written if key.startswith("ledger_")},
                         {"ledger_state": "written", "ledger_docnum": "RR6901-001", "ledger_reason": None,
                          "ledger_sent_at": "2026-10-24 10:00:00", "ledger_job": "J1", "ledger_book": "BK",
                          "ledger_needed": None, "ledger_target": "express"})
        held = self.row("Purchase Document", "PUR-H")
        self.assertEqual((held["ledger_state"], held["ledger_reason"], held["ledger_needed"], held["ledger_target"]),
                         ("held", "choice_needed:account", '[{"kind": "account"}]', "express"))
        never = self.row("Purchase Document", "PUR-NEVER")
        self.assertEqual((never["ledger_state"], never["ledger_target"]), (None, None))  # 没排进过账本：账本留空
        voided = self.row("ERP Stock Entry", "SE-V")
        self.assertEqual((voided["ledger_void_state"], voided["ledger_void_job"], voided["ledger_docnum"]),
                         ("queued", "J3", "OU-1"))
        self.assertEqual(self.row("ERP Stock Entry", "SE-SENT")["ledger_target"], "express")
        for doctype, name, state in (("ERP Sales Document", "SD-D", "detached"), ("ERP Stock Count", "STC-1", "queued"),
                                     ("ERP POS Day Summary", "PDS-1", "handled")):
            self.assertEqual((self.row(doctype, name)["ledger_state"], self.row(doctype, name)["ledger_target"]),
                             (state, "express"))
        # 存着的视图：每种单每一列 + 栏名都照 Frappe rename_field 的四个函数改
        self.assertIn(("update_reports", "ERP Stock Entry", "express_void_state", "ledger_void_state"), self.renamed)
        self.assertIn(("update_property_setters", "ERP Stock Count", "express_section", "ledger_section"), self.renamed)
        self.assertNotIn(("update_reports", "Purchase Document", "express_void_state", "ledger_void_state"), self.renamed)

    SETTINGS = ('{"Report": {"fields": [["express_state", "Purchase Document"], ["express_docnum", "Purchase Document"], '
                '["name", "Purchase Document"]], "order_by": "`tabPurchase Document`.`express_sent_at` desc"}, '
                '"List": {"filters": [["Purchase Document", "express_state", "=", "held", false]]}}')

    def test_saved_personal_views_follow_the_new_column_names_in_one_go(self):
        """个人列表 / 报表视图（筛选、报表列、排序）里的旧列名一次换掉；别的单据、别的键不动。Frappe 的 `update_user_settings`
        连着调会把整份设置清空（实验栈实测），所以补丁自己换。缓存里还没落库的这种单的设置先清掉（空值不写回库）。"""
        self.conn.execute("INSERT INTO `__UserSettings` VALUES ('a@x', 'Purchase Document', ?)", (self.SETTINGS,))
        self.conn.execute("INSERT INTO `__UserSettings` VALUES ('a@x', 'ERP Customer', '{\"List\": {\"express_state\": 1}}')")
        self.cache.update({"Purchase Document::a@x": None, "ERP Customer::a@x": "{}"})
        self.patch.execute()
        rows = dict(self.conn.execute("SELECT doctype, data FROM `__UserSettings`").fetchall())
        self.assertEqual(json.loads(rows["Purchase Document"]),
                         {"Report": {"fields": [["ledger_state", "Purchase Document"], ["ledger_docnum", "Purchase Document"],
                                                ["name", "Purchase Document"]],
                                     "order_by": "`tabPurchase Document`.`ledger_sent_at` desc"},
                          "List": {"filters": [["Purchase Document", "ledger_state", "=", "held", False]]}})
        self.assertEqual(rows["ERP Customer"], '{"List": {"express_state": 1}}')
        self.assertEqual(self.cache, {"ERP Customer::a@x": "{}"})
        self.patch.rollback()
        rows = dict(self.conn.execute("SELECT doctype, data FROM `__UserSettings`").fetchall())
        self.assertEqual(json.loads(rows["Purchase Document"]), json.loads(self.SETTINGS))

    def test_rerunning_never_overwrites_newer_ledger_values(self):
        self.fixture()
        self.patch.execute()
        first = {doctype: [self.row(doctype, name) for (name,) in self.conn.execute(f"SELECT name FROM `tab{doctype}`")]
                 for doctype in self.DOCTYPES}
        self.patch.execute()  # 重跑：什么都不变
        self.assertEqual(first, {doctype: [self.row(doctype, name) for (name,) in
                                           self.conn.execute(f"SELECT name FROM `tab{doctype}`")]
                                 for doctype in self.DOCTYPES})
        # 改列后代码在新列上往前走了（排队中 → 写成）；旧列还是旧值。再跑也不拿旧值盖回去
        self.conn.execute("UPDATE `tabERP Stock Count` SET ledger_state = 'written', ledger_docnum = 'TK-1' WHERE name = 'STC-1'")
        # 改列后才排上的单：旧列是空的、排队时就记了账本
        self.put("Purchase Document", "PUR-NEW", ledger_state="queued", ledger_target="express")
        self.patch.execute()
        self.assertEqual((self.row("ERP Stock Count", "STC-1")["ledger_state"],
                          self.row("ERP Stock Count", "STC-1")["ledger_docnum"]), ("written", "TK-1"))
        self.assertEqual(self.row("Purchase Document", "PUR-NEW")["ledger_state"], "queued")

    def test_a_site_without_the_new_columns_or_table_is_skipped(self):
        self.conn.execute("DROP TABLE `tabERP POS Day Summary`")
        self.conn.execute("CREATE TABLE `tabERP POS Day Summary` (name TEXT, express_state TEXT)")  # 同步还没加新列
        self.conn.execute("DROP TABLE `tabERP Stock Count`")
        self.put("ERP POS Day Summary", "PDS-1", express_state="written")
        self.patch.execute()
        self.assertEqual(self.row("ERP POS Day Summary", "PDS-1"), {"name": "PDS-1", "express_state": "written"})

    def test_rollback_copies_back_and_lets_the_patch_run_again(self):
        self.fixture()
        self.patch.execute()
        self.conn.execute("UPDATE `tabERP Stock Count` SET ledger_state = 'written', ledger_docnum = 'TK-1' WHERE name = 'STC-1'")
        self.patch.rollback()
        count = self.row("ERP Stock Count", "STC-1")
        self.assertEqual((count["express_state"], count["express_docnum"], count["ledger_target"]), ("written", "TK-1", None))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM `tabPatch Log`").fetchone()[0], 0)
        self.assertIn(("update_reports", "Purchase Document", "ledger_state", "express_state"), self.renamed)
        # 旧镜像上又往前走了一步，再升级时补丁重跑、重新抄
        self.conn.execute("UPDATE `tabPurchase Document` SET express_state = 'written', express_docnum = 'RR-2' WHERE name = 'PUR-H'")
        self.patch.execute()
        held = self.row("Purchase Document", "PUR-H")
        self.assertEqual((held["ledger_state"], held["ledger_docnum"], held["ledger_target"]), ("written", "RR-2", "express"))

    def test_registered_after_the_model_sync(self):
        text = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/patches.txt").read_text(encoding="utf-8")
        self.assertGreater(text.index("pearnly_erp_portal.patches.v1_24_ledger_state"), text.index("[post_model_sync]"))


class LedgerFields(unittest.TestCase):
    """单据上的账本栏是通用列（评估 2026-11-02 §12 第 6 条）：五种单 ``ledger_*`` 与原来的 ``express_*`` 同类型、同选项、同标签
    （界面上列名、筛选名、导出表头不变），另有不进导出的 ``ledger_target``；ERP 代码、流程、冒烟里不再读写旧列（只剩迁移补丁）。"""

    DOCTYPES = {"purchase_document": False, "erp_sales_document": False, "erp_stock_entry": True,
                "erp_stock_count": False, "erp_pos_day_summary": False}
    LABELS = {"ledger_section": "Express", "ledger_state": "Express Status", "ledger_docnum": "Express No.",
              "ledger_reason": "Express Note", "ledger_sent_at": "Express Sent At", "ledger_job": "Express Job",
              "ledger_book": "Express Account Set", "ledger_needed": "Express Choices Needed"}
    VOID_LABELS = {"ledger_void_state": "Express Void", "ledger_void_reason": "Express Void Reason",
                   "ledger_void_job": "Express Void Job"}
    OLD = re.compile(r"\bexpress_(state|docnum|reason|sent_at|job|needed|void_state|void_reason|void_job)\b")

    def meta(self, slug):
        return json.loads((ROOT / f"apps/pearnly_erp_portal/pearnly_erp_portal/pearnly_erp_portal/doctype/{slug}/{slug}.json")
                          .read_text(encoding="utf-8"))

    def test_each_document_has_the_general_columns_with_the_old_labels(self):
        for slug, voids in self.DOCTYPES.items():
            meta = self.meta(slug)
            fields = {field["fieldname"]: field for field in meta["fields"]}
            expected = {**self.LABELS, **(self.VOID_LABELS if voids else {})}
            self.assertEqual({name: fields[name]["label"] for name in expected if name in fields}, expected, slug)
            self.assertEqual([name for name in fields if self.OLD.match(name) or name in ("express_book", "express_section")],
                             [], slug)
            self.assertEqual([name for name in meta["field_order"] if "express" in name], [], slug)
            state = fields["ledger_state"]
            self.assertEqual((state["fieldtype"], state.get("in_standard_filter"), state.get("read_only")), ("Select", 1, 1))
            self.assertIn("\ndetached", state["options"])
            self.assertEqual((fields["ledger_docnum"].get("search_index"), fields["ledger_job"].get("search_index")), (1, 1))
            target = fields["ledger_target"]
            self.assertEqual((target["fieldtype"], target["options"], target.get("hidden"), target.get("no_copy")),
                             ("Select", "\nexpress\nmrerp", 1, 1), slug)
            order = meta["field_order"]
            self.assertEqual(order.index("ledger_target"), max(order.index(name) for name in expected) + 1, slug)
        self.assertIn("\nhandled", {f["fieldname"]: f for f in self.meta("erp_pos_day_summary")["fields"]}["ledger_state"]
                      ["options"])

    def test_exports_leave_out_the_ledger_target(self):
        for path in ("purchasing/output.py", "sales/output.py"):
            source = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal" / path).read_text(encoding="utf-8")
            self.assertIn('"ledger_target")]]', source, path)

    def test_queueing_records_the_express_ledger(self):
        source = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/express/push.py").read_text(encoding="utf-8")
        queue = source.split("\ndef queue(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('"ledger_target": contracts.LEDGER_EXPRESS', queue)
        self.assertEqual(E.LEDGER_EXPRESS, "express")

    def test_nothing_reads_the_old_express_columns_any_more(self):
        roots = [ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal", ROOT / "scripts/flows", ROOT / "scripts/smoke"]
        allowed = {"v1_22_express_linked_at.py", "v1_24_ledger_state.py"}  # 迁移补丁：读改列前的旧列
        found = []
        for root in roots:
            for path in sorted(root.rglob("*")):
                if path.suffix not in (".py", ".js") or path.name in allowed:
                    continue
                for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                    if self.OLD.search(line):
                        found.append(f"{path.relative_to(ROOT)}:{number}")
        self.assertEqual(found, [])


class PendingKinds(PushModule):
    def test_pending_covers_every_document_that_writes_a_ledger_with_the_same_permissions(self):
        """ERP 版 CC 的待处理补齐收银日汇总、出入库、盘点：种类 = 写账本的单据（`push.DIRECTIONS`），「可以写了」的状态同
        `push.ready`，读权限同单据上 Express 弹窗（`api.CAPABILITIES`）。"""
        utils = sys.modules["frappe.utils"]
        for name, value in (("now", lambda: "2026-10-24 00:00:00"), ("flt", lambda v, *a: float(v or 0)),
                            ("cint", lambda v: int(v or 0)), ("getdate", lambda v=None: v), ("add_days", lambda d, n: d)):
            setattr(utils, name, getattr(utils, name, value))
        from pearnly_erp_portal.express import api, pending
        push = self.push
        self.assertEqual(set(pending.KINDS), set(push.DIRECTIONS))
        for doctype, kind in pending.KINDS.items():
            self.assertEqual((kind.ready, kind.capability), (push.ready(doctype), api.CAPABILITIES[doctype][0]), doctype)
            self.assertEqual(kind.by_warehouse, doctype in (push.STOCK, push.COUNT), doctype)
        self.assertEqual(set(pending.STOCK_ROUTES), {"in", "out", "transfer", "adjust"})  # 物料调整（岳父 11-06）


class LinkedAtBackfill(unittest.TestCase):
    """补丁 v1_22：已接上（勾着、落过库）的公司按第一次同步的证据回填接上时刻 —— 收银起始日 00:00 与已排进 / 写进 Express 的单
    最早的建单时刻取早的（已写过、正在排队的单都不早于它，行为不变）；都查不到记补丁时刻；只填空值。"""

    def test_backfill_takes_the_earliest_evidence(self):
        import datetime as dt
        utils = types.ModuleType("frappe.utils")
        utils.get_datetime = lambda value: value if isinstance(value, dt.datetime) else dt.datetime.fromisoformat(str(value))
        utils.now_datetime = lambda: dt.datetime(2026, 11, 2, 1, 0)
        companies = [{"name": "CO-A", "express_pos_from": "2026-10-10", "express_book": "T9"},
                     {"name": "CO-B", "express_pos_from": "2026-10-10", "express_book": "T9"},
                     {"name": "CO-C", "express_pos_from": None, "express_book": "T9"}]
        earliest = {("CO-A", "ERP Stock Entry"): dt.datetime(2026, 10, 9, 13, 0), ("CO-B", "Purchase Document"): "2026-10-12 09:00:00"}
        written = {}

        def sql(query, values=None, as_dict=False):
            if "FROM `tabERP Company`" in query:
                self.assertIn("express_enabled = 1 AND express_linked_at IS NULL", query)
                return [types.SimpleNamespace(**row) for row in companies]
            doctype = query.split("FROM `tab", 1)[1].split("`", 1)[0]
            return [(earliest.get((values[0], doctype)),)]

        db = types.SimpleNamespace(has_column=lambda doctype, column: True, table_exists=lambda doctype: True, sql=sql,
                                   set_value=lambda doctype, name, field, value, update_modified=True: written.update({name: value}),
                                   commit=lambda: None)
        with mock.patch.dict(sys.modules, {"frappe.utils": utils}), mock.patch.object(frappe, "db", db), \
                mock.patch("builtins.print"):
            sys.modules.pop("pearnly_erp_portal.patches.v1_22_express_linked_at", None)
            from pearnly_erp_portal.patches import v1_22_express_linked_at as patch
            patch.execute()
        sys.modules.pop("pearnly_erp_portal.patches.v1_22_express_linked_at", None)
        self.assertEqual(written, {"CO-A": dt.datetime(2026, 10, 9, 13, 0), "CO-B": dt.datetime(2026, 10, 10, 0, 0),
                                   "CO-C": dt.datetime(2026, 11, 2, 1, 0)})


class PosDaySend(PushModule):
    """同一天的退货冲减后（主会话 10-25）：一天全退什么都不写；冲成负数停下交人，原因 ``express.pos_net_negative``。"""

    def test_an_empty_day_writes_nothing_and_a_negative_day_is_held(self):
        from unittest import mock
        docs = fake.STATE.db.table("ERP POS Day Summary")
        for body, state, reason in (({"ref": "x", "items": [], "empty": True}, None, ""),
                                    ({"ref": "x", "items": [], "net_negative": True}, "held", "express.pos_net_negative")):
            docs["PDS-1"] = {"name": "PDS-1", "creation": "2026-10-25 01:20:00", "status": "submitted", "owner": "a@x",
                             "ledger_state": "queued", "ledger_job": None, "ledger_reason": ""}
            with mock.patch.object(self.push, "document", lambda doctype, name, body=body: body), \
                    mock.patch.object(self.push, "company_of", lambda doctype, name: "CO-1"), \
                    mock.patch.object(self.push.prepare, "prepare", side_effect=AssertionError("must not prepare")), \
                    mock.patch.object(frappe.db, "commit", lambda: None, create=True):
                self.push.send("ERP POS Day Summary", "PDS-1")
            self.assertEqual((docs["PDS-1"]["ledger_state"], docs["PDS-1"]["ledger_reason"]), (state, reason))

    def test_vat_fields_ride_on_the_payload_with_version_nine(self):
        from unittest import mock
        docs = fake.STATE.db.table("ERP POS Day Summary")
        docs["PDS-1"] = {"name": "PDS-1", "creation": "2026-10-25 01:20:00", "status": "submitted", "owner": "a@x",
                         "ledger_state": "queued", "ledger_job": None, "ledger_reason": ""}
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

class UnmappedWarehouse(PushModule):
    """多仓闭环 P0（G15）：单上的仓在 Express 里没有对应仓库（主档同步读过、仍没有仓库码）：不写进 Express 主仓，停下交人
    ``express.warehouse_unmapped``；主档还没读到时照旧先等读（读到才知道有没有码）。"""

    def test_a_warehouse_without_an_express_code_is_held_after_the_masters_are_read(self):
        from unittest import mock
        self.assertEqual(E.unmapped({"WH-1": "01", "WH-2": "", "WH-3": None, "": ""}), ["WH-2", "WH-3"])
        docs = fake.STATE.db.table("ERP POS Day Summary")
        body = {"ref": "PDS-1@x", "number": "SI-1-3", "items": [{"code": "A"}], "unmapped_warehouses": ["WH-2"]}
        for prepared, state, reason in (({"hold": E.READING, "book": {"key": "BK"}}, "held", E.READING),
                                        ({"payload": {"payload_version": 4}, "book": {"key": "BK"}}, "held", E.WAREHOUSE_UNMAPPED)):
            docs["PDS-1"] = {"name": "PDS-1", "creation": "2026-10-25 01:20:00", "status": "submitted", "owner": "a@x",
                             "ledger_state": "queued", "ledger_job": None, "ledger_reason": ""}
            seen = []
            with mock.patch.object(self.push, "document", lambda doctype, name: dict(body)), \
                    mock.patch.object(self.push, "company_of", lambda doctype, name: "CO-1"), \
                    mock.patch.object(self.push, "_company", lambda name: COMPANY), \
                    mock.patch.object(self.push.prepare, "prepare", lambda doc, company, ensure, result=prepared: seen.append(doc) or result), \
                    mock.patch.object(self.push.jobs, "enqueue", side_effect=AssertionError("must not queue")), \
                    mock.patch.object(frappe, "publish_realtime", lambda *a, **k: None, create=True), \
                    mock.patch.object(frappe.db, "commit", lambda: None, create=True):
                self.push.send("ERP POS Day Summary", "PDS-1")
            self.assertNotIn("unmapped_warehouses", seen[0])  # 不交给共用写账规则
            self.assertEqual((docs["PDS-1"]["ledger_state"], docs["PDS-1"]["ledger_reason"]), (state, reason))

    def test_unmapped_documents_go_back_to_the_queue_when_the_company_is_synced(self):
        """验收复核 P0 / P1（多仓闭环 P1）：主档同步之后（可能刚认领了新仓库码），这家公司停在 warehouse_unmapped 的单重新排队，
        下一分钟 `poll` 再交（仍没码再停）；别家公司、别的原因、已作废的不动。公司条件在查询里：别家停着 100 张以上更早的单，
        这家照样排得上（原来先取最早的 100 张再逐张判公司，会漏）；超过一批的取到空为止。出入库单按仓所属公司。"""
        from unittest import mock
        docs = fake.STATE.db.table("ERP POS Day Summary")
        for index in range(130):  # 别家更早停着的一大批
            docs[f"OTHER-{index}"] = {"name": f"OTHER-{index}", "company": "CO-2", "status": "submitted", "modified": f"2026-10-01 {index:05d}",
                                      "ledger_state": "held", "ledger_reason": E.WAREHOUSE_UNMAPPED}
        for index in range(105):  # 这家超过一批
            docs[f"MINE-{index}"] = {"name": f"MINE-{index}", "company": "CO-1", "status": "submitted", "modified": f"2026-10-20 {index:05d}",
                                     "ledger_state": "held", "ledger_reason": E.WAREHOUSE_UNMAPPED}
        docs["READING"] = {"name": "READING", "company": "CO-1", "status": "submitted", "ledger_state": "held", "ledger_reason": E.READING}
        docs["VOID"] = {"name": "VOID", "company": "CO-1", "status": "void", "ledger_state": "held", "ledger_reason": E.WAREHOUSE_UNMAPPED}
        entries = fake.STATE.db.table("ERP Stock Entry")
        entries["STE-1"] = {"name": "STE-1", "warehouse": "WH-1", "status": "submitted", "ledger_state": "held",
                            "ledger_reason": E.WAREHOUSE_UNMAPPED}
        entries["STE-2"] = {"name": "STE-2", "warehouse": "WH-9", "status": "submitted", "ledger_state": "held",
                            "ledger_reason": E.WAREHOUSE_UNMAPPED}
        from pearnly_erp_portal.inventory import warehouses
        with mock.patch.object(warehouses, "names_of", lambda company=None, include_inactive=True: ["WH-1"] if company == "CO-1" else ["WH-9"]):
            self.assertEqual(self.push.requeue_unmapped("CO-1"), 106)
        state = lambda table, name: (table[name]["ledger_state"], table[name]["ledger_reason"])
        self.assertTrue(all(state(docs, f"MINE-{index}") == ("queued", "") for index in range(105)))
        self.assertTrue(all(state(docs, f"OTHER-{index}") == ("held", E.WAREHOUSE_UNMAPPED) for index in range(130)))
        self.assertEqual([state(docs, "READING"), state(docs, "VOID"), state(entries, "STE-1"), state(entries, "STE-2")],
                         [("held", E.READING), ("held", E.WAREHOUSE_UNMAPPED), ("queued", ""), ("held", E.WAREHOUSE_UNMAPPED)])

    def test_operator_documents_stop_the_same_way(self):
        """代录（CC「代录」里交的）同样受「仓库没有 Express 码就停」约束，不排写账件；交单结论回「停下」和原因（主会话 10-27 定）。"""
        from unittest import mock
        docs = fake.STATE.db.table(self.push.PURCHASE)
        docs["PI-1"] = {"name": "PI-1", "creation": "2026-10-25 01:20:00", "status": self.push.ready(self.push.PURCHASE),
                        "owner": "a@x", "ledger_state": "queued", "ledger_job": None, "ledger_reason": ""}
        body = {"ref": "PI-1@x", "number": "PI-1", "items": [{"code": "A"}], "unmapped_warehouses": ["WH-2"]}
        with mock.patch.object(self.push, "document", lambda doctype, name: dict(body)), \
                mock.patch.object(self.push, "company_of", lambda doctype, name: "CO-1"), \
                mock.patch.object(self.push, "_company", lambda name: COMPANY), \
                mock.patch.object(self.push.prepare, "prepare", lambda doc, company, ensure:
                                  {"payload": {"payload_version": 4}, "book": {"key": "BK"}}), \
                mock.patch.object(self.push.jobs, "enqueue", side_effect=AssertionError("must not queue")), \
                mock.patch.object(frappe.db, "commit", lambda: None, create=True):
            result = self.push.send(self.push.PURCHASE, "PI-1", operator={"id": "b1", "index": 1, "total": 1})
        self.assertEqual(result, {"status": "held", "reason": E.WAREHOUSE_UNMAPPED})
        self.assertEqual((docs["PI-1"]["ledger_state"], docs["PI-1"]["ledger_reason"]), ("held", E.WAREHOUSE_UNMAPPED))


class OperatorExclusion(PushModule):
    """主会话 10-27：交给代录、还没录成的单（最近一件写账请求号带 ``erp:operator:``、不是 success / cancelled）直写不接，「再写一次」
    也不改成直写；代录再录时先撤掉停在转人工的那一件、单据回到排队，再按代录交。"""

    def jobs(self, *found):
        from unittest import mock
        return mock.patch.object(self.push.jobs, "of_source", lambda doctype, name: list(found))

    def test_a_document_owned_by_the_operator_is_never_sent_directly(self):
        from unittest import mock
        owned = {"job": "J1", "request_key": "erp:operator:b1:PI-1@x", "status": "manual"}
        with self.jobs(owned), mock.patch.object(frappe.db, "get_value", side_effect=AssertionError("must not prepare"),
                                                 create=True):
            self.assertEqual(self.push.send(self.push.PURCHASE, "PI-1"), {"status": "held", "reason": E.OPERATOR_OWNED})
        for released in ({**owned, "status": "success"}, {**owned, "status": "cancelled"},
                         {"job": "J2", "request_key": "erp:purchase:PI-1@x", "status": "manual"}):
            with self.jobs(released):
                self.assertFalse(self.push.operator_owned(self.push.PURCHASE, "PI-1"))
        with self.jobs():
            self.assertFalse(self.push.operator_owned(self.push.PURCHASE, "PI-1"))

    def test_the_operator_takes_back_a_held_operator_document_before_sending_it_again(self):
        from unittest import mock
        docs = fake.STATE.db.table(self.push.PURCHASE)
        docs["PI-1"] = {"name": "PI-1", "ledger_state": "failed", "ledger_job": "J1", "ledger_reason": "x"}
        held = {"job": "J1", "request_key": "erp:operator:b1:PI-1@x", "status": "manual"}
        cancelled = []
        with mock.patch.object(self.push.jobs, "find_by_name", lambda name: dict(held)), \
                mock.patch.object(self.push.jobs, "cancel", lambda name, reason: cancelled.append((name, reason))):
            self.assertTrue(self.push.release_operator(self.push.PURCHASE, "PI-1"))
        self.assertEqual(cancelled, [("J1", "replaced")])
        self.assertEqual((docs["PI-1"]["ledger_state"], docs["PI-1"]["ledger_job"]), ("queued", None))
        for other in ({**held, "request_key": "erp:purchase:PI-1@x"}, {**held, "status": "leased"}):
            docs["PI-1"].update(ledger_state="failed", ledger_job="J1")
            with mock.patch.object(self.push.jobs, "find_by_name", lambda name, job=other: dict(job)), \
                    mock.patch.object(self.push.jobs, "cancel", side_effect=AssertionError("must not cancel")):
                self.assertFalse(self.push.release_operator(self.push.PURCHASE, "PI-1"))

    def test_retry_on_the_document_page_refuses_an_operator_job(self):
        source = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/express/api.py").read_text(encoding="utf-8")
        start = source.index("def retry(")
        end = source.find("\ndef ", start)
        retry = source[start:end if end > 0 else len(source)]
        guard = retry.index("push.OPERATOR_KEY")
        self.assertLess(guard, retry.index('jobs.cancel(job["job"], "replaced")'))  # 撤旧件、按直写重交之前就挡住


class OperatorPrecheck(PushModule):
    """主会话 10-27（必修 ①）：代录预检走写账准备（与直写同一条路 `push._prepare_body`，不写单据、不排写账件）：要人选的连同可选值
    一次列出、准备不了的写明原因；开录时先把预检里人定的按单据页同一套核对记下，再交代录。没声明预检的产品：全部当可录。
    11-05 起经写账前置判定（`express.precheck.collect_document`）一次收齐，回包形状不变，每张另带 ``precheck``。"""

    def setUp(self):
        super().setUp()
        sys.modules.pop("pearnly_erp_portal.express.operator", None)
        self.addCleanup(sys.modules.pop, "pearnly_erp_portal.express.operator", None)
        from pearnly_erp_portal.express import operator, precheck
        self.operator = operator
        for patch in (mock.patch.object(self.push, "document", lambda doctype, name: {"name": name}),
                      mock.patch.object(precheck.printed, "hold", lambda doctype, name: None),
                      mock.patch.object(precheck.push, "_company", lambda name: {"name": name, "tax_id": OWN}),
                      mock.patch.object(precheck, "document_view", lambda doc_id, doctype, name, found=None:
                                        {"doc_id": doc_id, "state": "check", "items": found["items"]})):
            patch.start()
            self.addCleanup(patch.stop)

    def prepared(self, prepare):
        """`push._prepare_body` 的替身：按单据名（替身单据内容里带着）回准备结论。"""
        return mock.patch.object(self.push, "_prepare_body",
                                 lambda doctype, company, body: (prepare(doctype, body["name"]), None, None))

    def run_precheck(self, results, job=None):
        from unittest import mock
        from pearnly_erp_portal.express import api

        docs = fake.STATE.db.table(self.push.PURCHASE)
        for name in results:
            docs[name] = {"name": name, "ledger_job": job}
        with mock.patch.object(self.operator.guard, "has", lambda capability, user=None: True), \
                mock.patch.object(self.operator.guard, "scope_filters", lambda user=None: {}), \
                mock.patch.object(self.push, "company_of", lambda doctype, name: "CO-1"), \
                self.prepared(lambda doctype, name: results[name]), \
                mock.patch.object(api, "_options", lambda item, data: [{"code": "B9", "name": "LED"}]), \
                mock.patch.object(self.operator.jobs, "find_by_name", lambda name: None):
            return {row["doc_id"]: row for row in self.operator.precheck("CO-1", [f"P:{n}" for n in results])["documents"]}

    def test_each_document_is_prepared_like_a_direct_write_and_choices_are_asked_once(self):
        found = self.run_precheck({
            "PI-1": {"payload": {"payload_version": 4}, "book": {"key": "BK"}},
            "PI-2": {"hold": E.CHOICE + ":item", "needed": [{"kind": "item", "subject": "x|bulb", "label": "LED 9W"}],
                     "book": {}},
            "PI-3": {"hold": E.READING},
            "PI-4": {"failed": E.NO_BOOK},
            "PI-5": {"hold": E.WAREHOUSE_UNMAPPED, "book": {"key": "BK"}}})
        self.assertEqual({k: v["state"] for k, v in found.items()},
                         {"P:PI-1": "ready", "P:PI-2": "choose", "P:PI-3": "waiting", "P:PI-4": "blocked",
                          "P:PI-5": "blocked"})
        self.assertEqual(found["P:PI-2"]["needed"][0]["options"], [{"code": "B9", "name": "LED"}])
        self.assertEqual(found["P:PI-5"]["reason"], E.WAREHOUSE_UNMAPPED)

    def test_the_list_follows_the_tasks_company_not_the_users_active_one(self):
        # 10-29 测试机：单据列表的权限条件只认本人「当前公司」，任务选了别的公司时一张都取不到。按任务的公司取，可见公司、记录范围自己守
        from unittest import mock
        seen = []
        rows = [Row(name="PI-1", doc_date="2026-10-20", grand_total=1070, system_no="PE-1", supplier_name="S",
                    ledger_reason="choice_needed:account", ledger_job=None),
                Row(name="PI-2", doc_date="2026-10-21", grand_total=900000, system_no="PE-2", supplier_name="ISUZU",
                    ledger_reason="choice_needed:account", ledger_job=None)]
        # 带按台号码的单不交代录（主会话 11-02）：照直写
        with mock.patch.object(frappe.db, "sql_list", lambda query, values: ["PI-2"] if "ERP Line Serial" in query else [],
                               create=True), \
                mock.patch.object(self.operator, "_company_ok", lambda user, company: company == "CO-9"), \
                mock.patch.object(self.operator.guard, "has", lambda capability, user=None: capability == "purchase.write"), \
                mock.patch.object(self.operator.guard, "scope_filters", lambda user=None: {"owner": "a@x"}), \
                mock.patch.object(frappe, "get_all", lambda doctype, **kw: [] if doctype == "ERP LINE Intake"  # 代录放文件的待提交记录（A 批 1）：这里没有
                                  else (seen.append(kw["filters"]) or rows), create=True), \
                mock.patch.object(frappe, "get_list", side_effect=AssertionError("must not use the list permission"),
                                  create=True), \
                mock.patch.object(frappe, "session", types.SimpleNamespace(user="a@x"), create=True):
            out = self.operator.sources("CO-9", "2026-10", 0, 50)
            self.assertEqual(self.operator.sources("CO-1", "2026-10", 0, 50),
                             {"documents": [], "next_offset": None, "total": 0})
        self.assertEqual([d["doc_id"] for d in out["documents"]], ["P:PI-1"])
        self.assertEqual(out["total"], 1)  # 资料一行写「资料 N 张」用（同 Cowork）
        self.assertNotIn("moves", out["documents"][0])  # ERP 不声明改分组：资料区的单不能拖（共用层补成空）
        self.assertEqual((seen[0]["company"], seen[0]["owner"]), ("CO-9", "a@x"))  # 员工只看本人录入的

    def test_other_months_are_counted_with_the_same_rule_as_the_list(self):
        """A 线 11-15：代录这个月没有可录的单时，CC 要说清别的月份各有几张（11-13 测试机1：6 月的单放在 10 月的代录对话里只回「0 张」）。
        与资料区同一套判据（公司可见、开单权、员工只看本人的、带按台号码的不算），只换日期：这个月之前、之后各查一次。"""
        from unittest import mock
        seen = []
        older = [Row(name="PI-6", doc_date="2026-06-03", grand_total=100, system_no="PE-6", supplier_name="S",
                     ledger_reason="", ledger_job=None),
                 Row(name="PI-7", doc_date="2026-06-20", grand_total=100, system_no="PE-7", supplier_name="S",
                     ledger_reason="", ledger_job=None),
                 Row(name="PI-8", doc_date="2026-05-02", grand_total=100, system_no="PE-8", supplier_name="S",
                     ledger_reason="", ledger_job=None)]
        newer = [Row(name="PI-9", doc_date="2026-11-01", grand_total=100, system_no="PE-9", supplier_name="S",
                     ledger_reason="", ledger_job=None)]

        def get_all(doctype, **kw):
            seen.append(kw["filters"])
            return older if kw["filters"]["doc_date"][0] == "<" else newer

        with mock.patch.object(frappe.db, "sql_list", lambda query, values: ["PI-8"] if "ERP Line Serial" in query else [],
                               create=True), \
                mock.patch.object(self.operator, "_company_ok", lambda user, company: company == "CO-9"), \
                mock.patch.object(self.operator.guard, "has", lambda capability, user=None: capability == "purchase.write"), \
                mock.patch.object(self.operator.guard, "scope_filters", lambda user=None: {"owner": "a@x"}), \
                mock.patch.object(frappe, "get_all", get_all, create=True), \
                mock.patch.object(frappe, "session", types.SimpleNamespace(user="a@x"), create=True):
            self.assertEqual(self.operator.elsewhere("CO-9", "2026-10"),
                             [{"month": "2026-11", "count": 1}, {"month": "2026-06", "count": 2}])  # PI-8 带按台号码：不算
            self.assertEqual(self.operator.elsewhere("CO-1", "2026-10"), [])  # 不是本人可见的公司
        self.assertEqual([f["doc_date"] for f in seen], [["<", "2026-10-01"], [">", "2026-10-31"]])
        self.assertTrue(all(f["company"] == "CO-9" and f["owner"] == "a@x" for f in seen))

    def test_a_document_already_queued_elsewhere_is_not_offered(self):
        found = self.run_precheck({"PI-1": {"payload": {}, "book": {}}}, job="J9")
        self.assertEqual((found["P:PI-1"]["state"], found["P:PI-1"]["reason"]), ("blocked", "already_queued"))

    def test_the_answers_are_remembered_before_the_operator_gets_the_document(self):
        from unittest import mock
        from pearnly_erp_portal.express import api, choices

        calls = []
        needed = {"hold": E.CHOICE + ":item", "needed": [{"kind": "item", "subject": "x|bulb", "label": "LED 9W"}], "book": {}}
        with mock.patch.object(self.operator.guard, "has", lambda capability, user=None: True), \
                mock.patch.object(self.operator.guard, "scope_filters", lambda user=None: {}), \
                mock.patch.object(self.push, "company_of", lambda doctype, name: "CO-1"), \
                self.prepared(lambda doctype, name: needed), \
                mock.patch.object(api, "_options", lambda item, data: [{"code": C.NEW, "name": ""}]), \
                mock.patch.object(choices, "remember", lambda *args: calls.append(("remember",) + args)), \
                mock.patch.object(self.push, "release_operator", lambda doctype, name: calls.append(("release", name))), \
                mock.patch.object(self.push, "send", lambda doctype, name, operator=None: calls.append(
                    ("send", name, operator["id"])) or {"status": "sent"}), \
                mock.patch.object(frappe.db, "commit", lambda: None, create=True):
            out = self.operator.submit("CO-1", ["P:PI-1"], {"id": "b1", "total": 1}, "",
                                       {"P:PI-1": [{"kind": "item", "subject": "x|bulb", "value": C.NEW}]})
        self.assertEqual(out, [{"doc_id": "P:PI-1", "status": "sent", "reason": ""}])
        self.assertEqual([c[0] for c in calls], ["remember", "release", "send"])
        self.assertEqual(calls[0][1:4], ("CO-1", "item", "x|bulb"))
        with mock.patch.object(self.operator.guard, "has", lambda capability, user=None: True), \
                mock.patch.object(self.operator.guard, "scope_filters", lambda user=None: {}), \
                mock.patch.object(self.push, "company_of", lambda doctype, name: "CO-1"), \
                self.prepared(lambda doctype, name: needed), \
                mock.patch.object(api, "_options", lambda item, data: [{"code": "B9", "name": ""}]):
            with self.assertRaises(frappe.ValidationError):  # 不是这一项能选的值：不记、不交
                self.operator.submit("CO-1", ["P:PI-1"], {"id": "b1", "total": 1}, "",
                                     {"P:PI-1": [{"kind": "item", "subject": "x|bulb", "value": "ZZ"}]})

    def test_a_document_waiting_for_the_computer_goes_back_to_the_task_as_waiting(self):
        """线上 11-10（Cowork 代录同一类）：交单时主档还在读 / 没有电脑能写，写账准备停在「过一会儿自己会好」的原因
        （`RETRY_REASONS`）。交回共用层的是 waiting_for_cc（任务排着等电脑），不是「未完成」；别的停下照旧。「没有账套」不是等电脑
        （A 线 11-12 A9：如实报，不永远等）。"""
        from unittest import mock

        held = iter([{"status": "held", "reason": E.READING}, {"status": "held", "reason": E.NO_BOOK},
                     {"status": "held", "reason": E.WAREHOUSE_UNMAPPED}])
        with mock.patch.object(self.operator.guard, "has", lambda capability, user=None: True), \
                mock.patch.object(self.operator.guard, "scope_filters", lambda user=None: {}), \
                mock.patch.object(self.push, "company_of", lambda doctype, name: "CO-1"), \
                mock.patch.object(self.push, "release_operator", lambda doctype, name: None), \
                mock.patch.object(self.push, "send", lambda doctype, name, operator=None: next(held)):
            out = self.operator.submit("CO-1", ["P:PI-1", "P:PI-2", "P:PI-3"], {"id": "b1", "total": 3}, "", {})
        self.assertEqual([(row["status"], row["reason"]) for row in out],
                         [("waiting_for_cc", E.READING), ("held", E.NO_BOOK), ("held", E.WAREHOUSE_UNMAPPED)])

    def test_a_question_shared_by_several_documents_is_remembered_once_and_all_are_sent(self):
        """10-30 实机：三张同问「这家公司这一年用哪套账」，前端每张都带同一个答案；记下第一张后后面两张不再问，原来整批报
        「请求无效」，而第一张已经交出去了。改为不再问的项跳过，三张都交。"""
        from unittest import mock
        from pearnly_erp_portal.express import api, choices

        calls, remembered = [], set()
        ask = {"kind": "account_set", "subject": "year:2026-01-01", "label": "TEST"}

        def prepare(doctype, name):
            return {"book": {}} if ask["subject"] in remembered else {"hold": E.CHOICE + ":account_set", "needed": [ask], "book": {}}

        answer = [{"kind": "account_set", "subject": "year:2026-01-01", "value": "BK1"}]
        with mock.patch.object(self.operator.guard, "has", lambda capability, user=None: True), \
                mock.patch.object(self.operator.guard, "scope_filters", lambda user=None: {}), \
                mock.patch.object(self.push, "company_of", lambda doctype, name: "CO-1"), \
                self.prepared(prepare), \
                mock.patch.object(api, "_options", lambda item, data: [{"code": "BK1", "name": "TEST"}]), \
                mock.patch.object(choices, "remember", lambda company, kind, subject, *rest: remembered.add(subject) or
                                  calls.append(("remember", subject))), \
                mock.patch.object(choices, "chosen", lambda company, kind, subject: "BK1" if subject in remembered else ""), \
                mock.patch.object(self.push, "release_operator", lambda doctype, name: None), \
                mock.patch.object(self.push, "send", lambda doctype, name, operator=None: calls.append(("send", name))
                                  or {"status": "sent"}), \
                mock.patch.object(frappe.db, "commit", lambda: None, create=True):
            out = self.operator.submit("CO-1", ["P:PI-1", "P:PI-2", "P:PI-3"], {"id": "b1", "total": 3}, "",
                                       {f"P:PI-{i}": answer for i in (1, 2, 3)})
        self.assertEqual([row["status"] for row in out], ["sent"] * 3)
        self.assertEqual(calls, [("remember", "year:2026-01-01"), ("send", "PI-1"), ("send", "PI-2"), ("send", "PI-3")])

    def test_a_product_without_upload_or_regroup_says_unsupported(self):
        from pearnly_core.express_operator import products

        fake.STATE.hooks.pop(products.HOOK, None)
        self.assertEqual(products.intake("CO-1", "a.pdf", b"x", "k"), {"accepted": False, "code": "unsupported"})
        self.assertEqual(products.regroup("CO-1", "P:1", "sales"), {"ok": False, "reason": "unsupported"})

    def test_after_a_restart_the_site_says_where_each_document_stands(self):
        # CC 重启后恢复出来的任务以站点为准收尾：已写入 / 排着写账件 / 停住 / 没排或撤回 / 别家的单
        rows = {"PI-1": {"ledger_state": "written", "ledger_docnum": "RR0000005", "ledger_reason": "", "ledger_job": "J1",
                         "system_no": "PE-1", "supplier_name": "ISUZU"},
                "PI-2": {"ledger_state": "queued", "ledger_docnum": "", "ledger_reason": "", "ledger_job": "J2"},
                "PI-3": {"ledger_state": "held", "ledger_docnum": "", "ledger_reason": "express.warehouse_unmapped",
                         "ledger_job": None},
                "PI-4": {"ledger_state": None, "ledger_docnum": "", "ledger_reason": "", "ledger_job": None}}
        with mock.patch.object(self.operator.guard, "has", lambda capability, user=None: True), \
                mock.patch.object(self.operator.guard, "scope_filters", lambda user=None: {}), \
                mock.patch.object(self.push, "company_of", lambda doctype, name: "CO-2" if name == "PI-5" else "CO-1"), \
                mock.patch.object(frappe.db, "get_value", lambda doctype, name, fields, as_dict=False:
                                  types.SimpleNamespace(**rows[name]), create=True):
            out = self.operator.status("CO-1", ["P:PI-1", "P:PI-2", "P:PI-3", "P:PI-4", "P:PI-5"])
        self.assertEqual([(r["state"], r["docnum"], r["reason"]) for r in out],
                         [("saved", "RR0000005", ""), ("queued", "", ""), ("not_done", "", "express.warehouse_unmapped"),
                          ("cancelled", "", ""), ("unknown", "", "")])
        # 单号与对方一起回（同 Cowork）：CC 重启后页面把单据写成「单号 · 对方」；没有单号的用单据名
        self.assertEqual([(r.get("ref"), r.get("party")) for r in out],
                         [("PE-1", "ISUZU"), ("PI-2", ""), ("PI-3", ""), ("PI-4", ""), (None, None)])
        from pearnly_core.express_operator import products

        fake.STATE.hooks.pop(products.HOOK, None)  # 没声明的产品：一律 unknown，页面照旧按排队显示、不收尾
        self.assertEqual([r["state"] for r in products.status("CO-1", ["P:1"])], ["unknown"])

    def test_a_product_without_a_precheck_treats_every_document_as_ready(self):
        from pearnly_core.express_operator import products

        fake.STATE.hooks.pop(products.HOOK, None)
        self.assertEqual(products.precheck("CO-1", ["P:1", "S:2"]),
                         {"documents": [{"doc_id": "P:1", "state": "ready"}, {"doc_id": "S:2", "state": "ready"}]})


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
        doc = types.SimpleNamespace(name="PDS-1", company="CO-1", ledger_job="J1" if job else None, ledger_void_state=None,
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
        writes, cancelled, sent = self.run_exit("retry", {"ledger_state": "held"})
        self.assertEqual((writes["ledger_state"], sent), ("queued", ["PDS-1"]))  # 按此刻内容重新准备
        writes, cancelled, sent = self.run_exit("mark_handled", {"ledger_state": "held"})
        self.assertEqual((writes["ledger_state"], writes["ledger_job"], cancelled, sent), ("handled", None, [], []))
        self.assertEqual(self.comments, [("Comment", "express.state.handled: ปิดใน Express แล้ว")])  # 时间线留一条：谁、何时、原因
        with self.assertRaises(Exception):
            self.run_exit("mark_handled", {"ledger_state": "held"}, reason="  ")  # 不写原因不能标
        cancelled_job = {"job": "J0", "status": "cancelled", "reason": "replaced"}
        writes, _cancelled, sent = self.run_exit("retry", {"ledger_state": "failed"}, cancelled_job)
        self.assertEqual((writes["ledger_state"], sent), ("queued", ["PDS-1"]))  # 写账件已撤销的失败单也能再写一次
        job = {"job": "J1", "status": "manual", "reason": "x"}
        writes, cancelled, _sent = self.run_exit("mark_handled", {"ledger_state": "queued"}, job)  # 电脑转了人工
        self.assertEqual((writes["ledger_state"], cancelled), ("handled", [("J1", "replaced")]))

    def test_a_summary_that_is_not_stuck_gets_no_exit(self):
        for row, job in (({"ledger_state": "written"}, None), ({"ledger_state": "queued"}, {"job": "J1", "status": "leased"})):
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
            # 用户 11-16「按建议」：出入库等人开的单与收银日汇总同一口径（开单人名下没有电脑就由办公室电脑兜底）
            self.assertEqual(self.push.actor_of("ERP Stock Entry", row, "BK"), "bob@x")
        with mock.patch.object(self.push.jobs, "writers", lambda actor, book: {"state": "ok", "devices": ["PC-A"]}):
            self.assertEqual(self.push.actor_of("ERP POS Day Summary", types.SimpleNamespace(name="PDS-2", owner="amy@x"),
                                                "BK"), "amy@x")

class PhoneOnlySeller(PushModule):
    """用户 11-16「按建议」：采购 / 销售 / 出入库 / 盘点的写账件归谁与收款同一口径（`office_actor`）。办公室一台电脑只有 A 登录过 CC，
    销售员 B 只用手机：B 的单归 A（A 的电脑领得走），ERP 单据上仍记 B；B 自己有电脑（哪怕关着）照旧归 B；够不到这本账套的电脑
    （另一家公司的）上的人不算；兜底也没人时仍归 B（页面「CC 桌面版未连接」）。"""

    def test_a_phone_only_sellers_documents_go_to_the_office_computer_that_reaches_the_book(self):
        import types
        from unittest import mock
        peers = {"PC-OFFICE": types.SimpleNamespace(users=frozenset({"a@x"}), online=True),
                 "PC-OTHER-CO": types.SimpleNamespace(users=frozenset({"z@y"}), online=True)}
        reach = {"BK": ["PC-OFFICE"]}  # 另一家公司的电脑够不到这本账套
        row = types.SimpleNamespace(name="SD-1", owner="b@x")
        mine = lambda actor, book: {"state": "ok" if actor in {u for n in reach[book] for u in peers[n].users} else "missing",
                                    "devices": []}
        with mock.patch.object(self.push.jobs, "writers", mine), \
                mock.patch.object(self.push, "company_of", lambda doctype, name: "CO-1"), \
                mock.patch.object(self.push.devices, "peers", lambda: peers), \
                mock.patch.object(self.push.books, "reachers", lambda keys: reach), \
                mock.patch.object(self.push.devices, "issuer_valid", lambda user: True), \
                mock.patch.object(self.push.companies, "allowed_companies", lambda user: ["CO-1"] if user != "z@y" else ["CO-9"]):
            for doctype in (self.push.SALES, self.push.PURCHASE, self.push.STOCK, self.push.COUNT):
                self.assertEqual(self.push.actor_of(doctype, row, "BK"), "a@x")
            # 兜底的人也要能看这家公司：A 不在这家的可见公司里就不给 A，仍归 B（不跨公司）
            with mock.patch.object(self.push.companies, "allowed_companies", lambda user: []):
                self.assertEqual(self.push.actor_of(self.push.SALES, row, "BK"), "b@x")
            # 只有另一家公司的电脑在：够不到这本账套，不算
            reach["BK"] = []
            self.assertEqual(self.push.actor_of(self.push.SALES, row, "BK"), "b@x")
        # B 自己有登录过 CC 的电脑（关着）：照旧归 B，等他的电脑
        with mock.patch.object(self.push.jobs, "writers", lambda actor, book: {"state": "offline", "devices": ["PC-B"]}), \
                mock.patch.object(self.push, "company_of", lambda doctype, name: "CO-1"):
            self.assertEqual(self.push.actor_of(self.push.SALES, row, "BK"), "b@x")


class CollectionNotYours(PushModule):
    """收款写账件 × not_yours（集成 1113 跨边，A 线 × 收款）：收款员自己有登录过 CC 的电脑、此刻关着，办公室同事够得到这本的电脑在线
    —— `jobs.writers` 判 not_yours，`office_actor` 仍把作业归收款员本人（排队等他的电脑，不派给同事），页面原因是「您的电脑离线」。
    收款员一台电脑都没有（missing）才转给办公室的人（收款支原意，不变）。"""

    def test_a_collectors_job_waits_for_the_collectors_own_computer_when_a_colleagues_is_online(self):
        import types
        from unittest import mock
        peers = {"PC-COL": types.SimpleNamespace(users=frozenset({"col@x"}), online=False, payload_version=None,
                                                 index_pending=None, targets=None),
                 "PC-OFFICE": types.SimpleNamespace(users=frozenset({"office@x"}), online=True, payload_version=None,
                                                    index_pending=None, targets=None)}
        with mock.patch.object(self.push.jobs.devices, "peers", lambda: peers), \
                mock.patch.object(self.push.devices, "peers", lambda: peers), \
                mock.patch.object(self.push.jobs.books, "reachers", lambda keys: {"BK": ["PC-COL", "PC-OFFICE"]}), \
                mock.patch.object(self.push.books, "reachers", lambda keys: {"BK": ["PC-COL", "PC-OFFICE"]}), \
                mock.patch.object(self.push.devices, "issuer_valid", lambda user: True), \
                mock.patch.object(self.push.companies, "allowed_companies", lambda user: ["CO-1"]):
            self.assertEqual(self.push.jobs.writers("col@x", "BK")["state"], "not_yours")
            self.assertEqual(self.push.office_actor("col@x", "CO-1", "BK"), "col@x")  # 不派给 office@x
            from pearnly_erp_portal.express import pending
            self.assertEqual(pending.REASONS[self.push.contracts.NOT_YOURS], "express.reason.notYours")
            # 收款员一台电脑都没有：照收款支原意归办公室在线电脑上的人
            peers.pop("PC-COL")
            self.assertEqual(self.push.office_actor("col@x", "CO-1", "BK"), "office@x")


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

    def test_an_operator_code_without_a_line_number_is_matched_by_the_line_name(self):
        """代录回执只报随单新建的存货 {name, code}、没有序号（`common.written_codes`，11-16 验收）：按品名认，品名唯一的那一行才挂；
        同名两行认不准、不挂。"""
        self.assertEqual(self.link([{"name": "อื่น", "code": "ST-0099"}]), (None, None))
        self.assertEqual(self.link([{"name": "น้ำแข็งก้อน", "code": "ST-0099"}]), ("CO-1", "ST-0099"))
        self.products["PRD-1"].pop("express_code")
        self.products["PRD-1"].pop("express_company")
        self.lines["L3"] = {"name": "L3", "parent": "PUR-1", "parenttype": "Purchase Document", "idx": 3,
                            "product": "PRD-1", "description": "น้ำแข็งก้อน"}
        self.assertEqual(self.link([{"name": "น้ำแข็งก้อน", "code": "ST-0099"}]), (None, None))

    def test_a_line_that_does_not_line_up_is_left_alone(self):
        self.assertEqual(self.link([{"seq": 2, "name": "อื่น", "code": "ST-0099"}]), (None, None))
        self.assertEqual(self.link([{"seq": 3, "name": "น้ำแข็งก้อน", "code": "ST-0099"}]), (None, None))
        self.assertEqual(self.link(None), (None, None))


    def test_stock_documents_count_and_pos_day_link_the_codes_the_computer_used(self):
        """多仓闭环 P3x：出入库单、盘点（按交给电脑的明细序号）、收银日汇总（按品名）写进去后同样挂码；这几种单只看这件商品有没有在
        别家公司的仓里动过库存（收银卖的本地商品必然被别的小票用过）。"""
        from unittest import mock
        fake.STATE.db.table("ERP Warehouse").update({"W1": {"name": "W1", "company": "CO-1"},
                                                     "W9": {"name": "W9", "company": "CO-2"}})
        moves = fake.STATE.db.table("ERP Stock Movement")
        lines = {"lines": [{"product": "PRD-2", "name": "ถ่าน"}, {"product": "PRD-1", "name": "น้ำแข็งก้อน"}]}
        codes = [{"seq": 2, "name": "น้ำแข็งก้อน", "code": "ST-0099"}]
        for doctype, patch in (("ERP Stock Entry", mock.patch.object(self.push.stock_entry, "document", lambda name, ref: lines)),
                               ("ERP Stock Count", mock.patch.object(self.push.stock_entry, "count_document", lambda name, ref: lines)),
                               ("ERP POS Day Summary", mock.patch.object(frappe.db, "sql", lambda *a, **k: [
                                   Row(product="PRD-1", product_name="น้ำแข็งก้อน")], create=True))):
            self.products["PRD-1"] = {"name": "PRD-1", "product_name": "น้ำแข็งก้อน", "is_active": 1}
            moves.clear()
            moves["M1"] = {"name": "M1", "product": "PRD-1", "warehouse": "W1"}  # 这家公司自己的仓里卖过：照挂
            with patch:
                self.push.link_products(doctype, "DOC-1", "CO-1", codes)
            self.assertEqual(self.products["PRD-1"].get("express_code"), "ST-0099", doctype)
            self.products["PRD-1"] = {"name": "PRD-1", "product_name": "น้ำแข็งก้อน", "is_active": 1}
            moves["M2"] = {"name": "M2", "product": "PRD-1", "warehouse": "W9"}  # 别家公司的仓也动过：各家共用，不挂
            with patch:
                self.push.link_products(doctype, "DOC-1", "CO-1", codes)
            self.assertIsNone(self.products["PRD-1"].get("express_code"), doctype)
        self.assertIsNone(self.push._product_lines("ERP Something", "X"))

    def test_a_product_made_for_this_company_without_a_code_takes_it(self):
        """接上时盘点里新建的物料已归这家公司（还没有码）：写进去后照样挂上电脑建的码；归别家公司的不动。"""
        self.products["PRD-1"]["express_company"] = "CO-1"
        self.assertEqual(self.link([{"seq": 2, "name": "น้ำแข็งก้อน", "code": "ST-0099"}]), ("CO-1", "ST-0099"))
        self.products["PRD-1"] = {"name": "PRD-1", "product_name": "น้ำแข็งก้อน", "express_company": "CO-2", "is_active": 1}
        self.assertEqual(self.link([{"seq": 2, "name": "น้ำแข็งก้อน", "code": "ST-0099"}]), ("CO-2", None))

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

    def test_pending_counts_only_moves_of_documents_started_after_the_link(self):
        """主会话 11-01（库存对齐统一口径）：只叠加开始时刻不早于接上时刻、还没写成的单；更早开始的一律不叠加，接上后 ERP 结存以
        Express 为准。原来接上前的出入库 / 盘点 / 小票永久叠加，采购 / 销售 / 期初却被覆盖（评估 4.a 反证 3）。"""
        import types
        sync = self.sync
        sync.stock.MOVEMENT = "ERP Stock Movement"
        since = "2026-10-20 00:00:00"
        sync.push.linked_at = lambda company: since if company == "CO-1" else None
        sync.push.VOIDED = "voided"
        before, after = "2026-10-19 10:00:00", "2026-10-21 10:00:00"
        moves = [
            # (ref_type, ref_id, product, warehouse, qty, 流水时刻)
            ("purchase", "PUR-1", "P1", "W1", 10, after),          # 采购单已写进 Express：不算
            ("purchase", "PUR-2", "P1", "W1", 4, after),           # 接上之后开始、排队中：算
            ("purchase", "PUR-F", "P1", "W1", 3, after),           # 接上之后开始、写失败：算（还没写成）
            ("purchase", "PUR-OLD", "P1", "W1", 8, after),         # 草稿接上前建、接上后提交：开始早于接上，不写也不算
            ("ERP Sales Document", "SD-1", "P1", "W1", -3, after),  # 销售记录停下：算
            ("stock_entry", "SE-1", "P1", "W2", 7, after),          # 出入库单排队中：算
            ("stock_entry", "SE-2", "P1", "W2", -4, after),         # 已写进 Express：不算
            ("stock_entry", "SE-3", "P1", "W2", -6, after),         # 已写进去、又在 Express 作废完：原单不算
            ("void", "SE-3", "P1", "W2", 6, after),                 # 它的冲销 Express 里也有了：不算（验收 10-25）
            ("stock_entry", "SE-4", "P1", "W2", -2, after),         # 已写进去、作废还在排队：原单不算
            ("void", "SE-4", "P1", "W2", 2, after),                 # 冲销 Express 里还没有：算
            ("stock_entry", "SE-OLD", "P1", "W2", 9, before),       # 接上前的出入库单：不算（原来永久叠加）
            ("stock_entry", "SE-OLD2", "P1", "W2", 11, after),      # 草稿接上前建、接上后提交：不算
            ("stock_entry", "SE-D", "P1", "W2", 12, after),         # 断开时撤下的（已断开）：不算
            ("count", "SC-1", "P1", "W1", -1, after),               # 排队中的盘点生成的单：算
            ("count", "SC-2", "P1", "W1", 2, after),                # 盘点已整份写进 Express：不算
            ("count", "SC-OLD", "P1", "W1", 5, before),             # 接上前过账的盘点：不算
            ("void", "PUR-1", "P1", "W1", -10, after),              # 采购写进去之后在 ERP 作废：ERP 独有，算
            ("void", "PUR-OLD", "P1", "W1", -8, after),             # 接上前开始的单作废：原单不算，冲销也不算
            ("sale", "RCPT-1", "P1", "W1", -2, after),              # 接上之后卖、还没汇总：算
            ("refund", "RCPT-2", "P1", "W1", 1, after),
            ("sale", "RCPT-9", "P1", "W1", -5, after),              # 在已写进 Express 的日汇总里：不算（B3a）
            ("sale", "RCPT-OLD", "P1", "W1", -7, before),           # 接上前卖的：不算（原来永久叠加）
            ("sale", "RCPT-V", "P1", "W1", -4, after),              # 接上后卖、又作废：原单与冲销都算，正好抵消
            ("void", "RCPT-V", "P1", "W1", 4, after),
            (None, None, "P1", "W1", 5, after),                     # 没有来源（从不写进 Express）：接上之后的算
            (None, None, "P1", "W1", 30, before),                   # 接上前的：不算
            ("express_sync", None, "P1", "W1", 99, after),          # 同步调整本身：不算
            ("opening", "OP-1", "P1", "W1", 50, after),             # 期初：不算
            ("sale", "RCPT-3", "P2", "W1", -6, after),              # 不是这家公司 Express 来的品：不管
        ]
        docs = {
            "Purchase Document": [("PUR-1", "written", None), ("PUR-2", "queued", None), ("PUR-F", "failed", None),
                                  ("PUR-OLD", None, None)],
            "ERP Sales Document": [("SD-1", "held", None)],
            "ERP Stock Entry": [("SE-1", "queued", None), ("SE-2", "written", None), ("SE-3", "written", "voided"),
                                ("SE-4", "written", "queued"), ("SE-OLD", None, None), ("SE-OLD2", None, None),
                                ("SE-D", "detached", None)],
            "ERP Stock Count": [("STC-1", "queued", None), ("STC-2", "written", None), ("STC-OLD", None, None)],
        }
        count_entries = {"STC-1": ["SC-1"], "STC-2": ["SC-2"], "STC-OLD": ["SC-OLD"]}
        receipts = {"RCPT-1": (after, None), "RCPT-2": (after, None), "RCPT-9": (after, "written"),
                    "RCPT-OLD": (before, None), "RCPT-V": (after, None), "RCPT-3": (after, None)}

        def matches(value, condition):
            if isinstance(condition, list):
                op, wanted = condition
                return {"in": lambda: value in wanted, "not in": lambda: value not in wanted, "!=": lambda: value != wanted,
                        ">=": lambda: value >= wanted, "=": lambda: value == wanted}[op]()
            return value == condition

        def get_all(doctype, filters=None, fields=None, pluck=None, group_by=None, **kw):
            if doctype == "ERP Stock Movement":
                rows = [dict(zip(("ref_type", "ref_id", "product", "warehouse", "qty", "creation"), m)) for m in moves]
                for field, op, value in filters:
                    rows = [r for r in rows if r[field] is not None and matches(r[field], [op, value])]
                keys = [part.strip() for part in group_by.split(",")]
                out = {}
                for r in rows:
                    k = tuple(r[f] for f in keys)
                    out[k] = out.get(k, 0) + r["qty"]
                return [types.SimpleNamespace(**dict(zip(keys, k)), qty=v) for k, v in out.items()]
            if doctype == "ERP Stock Entry" and filters.get("ref_type") == "count":
                return [entry for count in filters["ref_id"][1] for entry in count_entries[count]]
            names = []
            for name, state, void_state in docs[doctype]:
                row = {"name": name, "ledger_state": state or "", "ledger_void_state": void_state or "",
                       "company": "CO-1", "warehouse": "W1"}
                if all(matches(row[field], value) for field, value in filters.items()):
                    names.append(name)
            return names

        def sql(query, values=None, as_dict=False):
            self.assertIn("tabERP POS Day Summary", query)
            self.assertEqual((values["sales"], values["written"]), (("sale", "refund"), ("written", "mismatch", "handled")))
            out = {}
            for ref_type, ref_id, product, warehouse, qty, created in moves:
                sold = receipts.get(ref_id)
                if ref_type not in values["sales"] or not sold or sold[0] < values["since"] or created < values["since"] \
                        or (sold[1] or "") in values["written"] or product not in values["products"]:
                    continue
                out[(ref_id, product, warehouse)] = out.get((ref_id, product, warehouse), 0) + qty
            return [types.SimpleNamespace(ref_id=k[0], product=k[1], warehouse=k[2], qty=v) for k, v in out.items()]

        with mock.patch.object(sync.frappe, "get_all", side_effect=get_all), \
                mock.patch.object(sync.frappe.db, "sql", side_effect=sql):
            pending = sync._pending("CO-1", {"ICE-01": "P1"}, {"01": "W1", "02": "W2"})
            self.assertEqual(sync._pending("CO-2", {"ICE-01": "P1"}, {"01": "W1"}), {})  # 没接上：不叠加
        self.assertEqual(pending, {("ICE-01", "01"): Decimal(4 + 3 - 3 - 1 - 10 - 2 + 1 - 4 + 4 + 5),
                                   ("ICE-01", "02"): Decimal(7 + 2)})

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
        self.assertEqual(kind({"direction": "transfer"}), "RL")
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
        self.assertNotIn("unmapped_warehouses", body)
        # 多仓闭环 P0（G15）：行上的仓在 Express 里没有仓库码 → 列出来，`push.send` 停下交人，不写进 Express 主仓
        values[("ERP Warehouse", "W2")] = ""
        with mock.patch.object(frappe, "get_doc", lambda doctype, name: doc, create=True), \
                mock.patch.object(frappe.db, "get_value", lambda doctype, name, fields=None, as_dict=False:
                                  values.get((doctype, name))):
            body = self.stock.document("SE-1", "SE-1@x")
        self.assertEqual(body["unmapped_warehouses"], ["W2"])

    def test_a_transfer_is_a_native_rl_with_both_locations_and_no_cost(self):
        """多仓闭环 P2：调拨 → RL，行上带调出仓码、单头另带调入仓码、不带价；任一个仓没有 Express 码就列出来停下交人。"""
        import types
        lines = [types.SimpleNamespace(product="P1", qty=2, unit="ถุง", unit_cost=None, warehouse="W1")]
        Doc = type("Doc", (dict,), {"__getattr__": dict.get, "__setattr__": dict.__setitem__})
        doc = Doc(direction="transfer", ref_type=None, lines=lines, warehouse="W1", to_warehouse="W2",
                  posting_date="2026-10-30", doc_no="TF2569-000001", name="SE-9", reason="ย้ายไปสาขา")
        values = {("ERP Product", "P1"): {"product_name": "น้ำแข็ง", "express_code": "ICE-01", "base_unit": "ถุง"},
                  ("ERP Warehouse", "W1"): "01", ("ERP Warehouse", "W2"): "02"}

        def document():
            with mock.patch.object(frappe, "get_doc", lambda doctype, name: doc, create=True), \
                    mock.patch.object(frappe.db, "get_value", lambda doctype, name, fields=None, as_dict=False:
                                      values.get((doctype, name))):
                return self.stock.document("SE-9", "SE-9@x")

        self.assertEqual(self.stock.kind({"direction": "transfer"}), "RL")
        body = document()
        self.assertEqual((body["doctype"], body["loccod"], body["to_loccod"]), ("RL", "01", "02"))
        self.assertEqual(body["lines"], [{"code": "ICE-01", "name": "น้ำแข็ง", "qty": "2", "unit": "ถุง", "loccod": "01",
                                          "product": "P1"}])  # ERP 商品只在服务端用（选存货、回填编码），进载荷前去掉
        self.assertNotIn("unmapped_warehouses", body)
        key = self.computer([book()])[0]
        self.masters(key)
        payload = self.stock.prepare(body, COMPANY, lambda company, chosen: None)["payload"]
        self.assertEqual((payload["payload_version"], payload["doctype"], payload["loccod"], payload["to_loccod"]),
                         (self.stock.TRANSFER_PAYLOAD_VERSION, "RL", "01", "02"))
        self.assertNotIn("account", payload)  # RL 不过账，不问科目
        values[("ERP Warehouse", "W2")] = ""
        self.assertEqual(document()["unmapped_warehouses"], ["W2"])

    def test_a_transfer_holds_only_when_the_computer_reported_no_rl_book(self):
        key = self.computer([book()])[0]
        body = self.body(doctype="RL", to_loccod="02", loccod="01")
        self.masters(key, facts={"accounts": ACCOUNTS, "mapping": MAPPING, "catalog": {"isacc_candidates": []},
                                 "stock_books": {"OU": "53-01"}, "stock_books_checked": ["JU", "OU", "RL", "TK"]})
        self.assertEqual(self.stock.prepare(body, COMPANY, lambda company, chosen: None)["hold"], E.NO_STOCK_BOOK)
        # 旧版 CC 没报核过哪些单别：交给电脑核（电脑没有 RL 簿回 JOURNAL_BOOK_NOT_FOUND）
        self.masters(key, facts={"accounts": ACCOUNTS, "mapping": MAPPING, "catalog": {"isacc_candidates": []},
                                 "stock_books": {"OU": "53-01"}})
        self.assertIn("payload", self.stock.prepare(body, COMPANY, lambda company, chosen: None))

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
            {"code": "ICE-02", "name": "น้ำแข็งบด", "qty": "-2", "unit": "ถุง", "loccod": "01", "product": "P2"},
            {"code": "CH-01", "name": "ถ่าน", "qty": "3", "unit": "ถุง", "loccod": "01", "product": "P3"}])  # 盘盈不带 ERP 估值，按 Express 均价取

    def new_item(self, **override):
        line = {"code": "", "name": "น้ำแข็งหลอด", "qty": "4", "unit": "ถุง", "loccod": "02", "product": "PRD-9"}
        line.update(override)
        return line

    def prepared(self, body):
        return self.stock.prepare(body, COMPANY, lambda company, chosen: None)

    def test_a_product_express_does_not_have_is_asked_once_and_new_marks_the_line(self):
        """多仓闭环 P3x（G14）：行上没有 Express 编码、账套里也没有同名存货 → 按 ERP 商品问一次（选现有或新建）；选新建的行标
        ``new_ok``、载荷盖 15（旧版 CC 诚实拒收），选现有的带那个码照旧 6；ERP 商品不进载荷。"""
        key = self.computer([book()])[0]
        self.masters(key)
        for doctype, extra in (("OU", {}), ("RL", {"to_loccod": "03"})):
            fake.STATE.db.table(choices.CHOICE).clear()
            body = self.body(doctype=doctype, lines=[self.new_item(), self.new_item(qty="1")], **extra)
            result = self.prepared(body)
            self.assertEqual(result["hold"], E.CHOICE + ":item")
            self.assertEqual(result["needed"], [{"kind": "item", "subject": "stock|PRD-9", "label": "น้ำแข็งหลอด",
                                                 "current": ""}])  # 同一件商品两行只问一次
            choices.remember(COMPANY["name"], "item", "stock|PRD-9", C.NEW)
            payload = self.prepared(self.body(doctype=doctype, lines=[self.new_item()], **extra))["payload"]
            self.assertEqual(payload["payload_version"], self.stock.NEW_ITEM_PAYLOAD_VERSION)
            self.assertEqual(payload["items"], [{"code": "", "name": "น้ำแข็งหลอด", "qty": "4", "unit": "ถุง", "loccod": "02",
                                                 "new_ok": True}])
            choices.remember(COMPANY["name"], "item", "stock|PRD-9", "ICE-02")
            payload = self.prepared(self.body(doctype=doctype, lines=[self.new_item()], **extra))["payload"]
            self.assertEqual(payload["payload_version"], 6 if doctype == "OU" else self.stock.TRANSFER_PAYLOAD_VERSION)
            self.assertEqual(payload["items"][0]["code"], "ICE-02")
            self.assertNotIn("new_ok", payload["items"][0])

    def test_a_same_name_item_or_a_code_on_the_line_is_not_asked(self):
        key = self.computer([book()])[0]
        self.masters(key)
        lines = [self.new_item(name="น้ำแข็งแพ็ค"), self.new_item(code="ICE-01", product="PRD-1")]
        payload = self.prepared(self.body(lines=lines))["payload"]
        self.assertEqual(payload["payload_version"], 6)  # 同名：电脑照名认；行上带码：照码写
        self.assertEqual([item["code"] for item in payload["items"]], ["", "ICE-01"])
        # 记住的码在这份账套里已经没有了：再问一次，带上原来的值
        choices.remember(COMPANY["name"], "item", "stock|PRD-9", "GONE-01")
        result = self.prepared(self.body(lines=[self.new_item()]))
        self.assertEqual(result["needed"][0]["current"], "GONE-01")

    def test_new_items_carry_the_stock_group_chosen_for_the_book(self):
        """新建存货挂的存货科目组同采购：这份账套选过就带 ``stock_acccod``（只在有新建行时带），没选过不带。"""
        key = self.computer([book()])[0]
        self.masters(key)
        choices.remember(COMPANY["name"], "item", "stock|PRD-9", C.NEW)
        payload = self.prepared(self.body(lines=[self.new_item()]))["payload"]
        self.assertNotIn("stock_acccod", payload)
        choices.remember(COMPANY["name"], choices.STOCK_GROUP, key, "ST02")
        for doctype, extra in (("JU", {}), ("RL", {"to_loccod": "03"})):
            payload = self.prepared(self.body(doctype=doctype, lines=[self.new_item()], **extra))["payload"]
            self.assertEqual(payload["stock_acccod"], "ST02", doctype)
        payload = self.prepared(self.body(lines=[self.new_item(code="ICE-01", product="PRD-1")]))["payload"]
        self.assertNotIn("stock_acccod", payload)  # 没有要新建的：不带

    def test_without_the_item_list_a_count_with_new_items_still_holds(self):
        """存货表没读到（定不了同名）：盘点当场新建的商品照旧停在 item_unmapped；出入库照旧交品名给电脑认。"""
        key = self.computer([book()])[0]
        self.masters(key, products=None)
        body = self.body(doctype="TK", lines=[self.new_item()], unmapped_items=["น้ำแข็งหลอด"])
        self.assertEqual(self.prepared(body)["hold"], E.ITEM_UNMAPPED)
        payload = self.prepared(self.body(lines=[self.new_item()]))["payload"]
        self.assertEqual((payload["payload_version"], payload["items"][0]["code"]), (6, ""))
        # 读到了：盘点同样问一次，不再停在 item_unmapped
        self.masters(key)
        self.assertEqual(self.prepared(body)["hold"], E.CHOICE + ":item")

    def test_the_form_asks_for_the_lines_before_submit(self):
        values = {("ERP Product", "PRD-9"): {"product_name": "น้ำแข็งหลอด", "express_code": ""}}
        with mock.patch.object(frappe.db, "get_value", lambda doctype, name, fields=None, as_dict=False:
                               Row(values[(doctype, name)]) if (doctype, name) in values else None):
            body = self.stock.form_body({"direction": "out", "posting_date": "2026-10-20",
                                         "lines": [{"product": "PRD-9", "qty": 1}, {"product": ""}, "x"]})
        self.assertEqual(body["lines"], [{"code": "", "name": "น้ำแข็งหลอด", "product": "PRD-9"}])
        key = self.computer([book()])[0]
        self.masters(key)
        self.assertEqual(self.prepared(body)["needed"][0]["subject"], "stock|PRD-9")

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
                                          "product": "P1", "factor": "12", "unit_cost": "120"}])
        self.assertEqual(base["lines"], [{"code": "ICE-01", "name": "น้ำแข็ง", "qty": "36", "unit": "ถุง", "loccod": "01",
                                          "product": "P1", "unit_cost": "10"}])


    def test_a_void_names_the_written_book_the_docnum_and_the_codes_the_computer_used(self):
        """B 段 ①：作废已写进 Express 的出入库单 → 载荷 8（照 Express 原生作废原单），账套就是写入时那份，逐行带电脑实际用的码。"""
        import types
        from pearnly_core.cc_desktop import jobs

        key = self.computer([book()])[0]
        lines = [types.SimpleNamespace(product="P1", qty=2, unit="ถุง", unit_cost=0, warehouse=None)]
        Doc = type("Doc", (dict,), {"__getattr__": dict.get, "__setattr__": dict.__setitem__})
        doc = Doc(direction="out", ref_type=None, lines=lines, warehouse="W1", posting_date="2026-10-20",
                  doc_no="IS2569-000001", name="SE-1", reason="", ledger_docnum="OU691020-001", ledger_book=key,
                  ledger_job="J1")
        values = {("ERP Product", "P1"): {"product_name": "น้ำแข็ง", "express_code": "", "base_unit": "ถุง"},
                  ("ERP Warehouse", "W1"): "01"}
        with mock.patch.object(frappe, "get_doc", lambda doctype, name: doc, create=True), \
                mock.patch.object(frappe.db, "get_value", lambda doctype, name, fields=None, as_dict=False:
                                  values.get((doctype, name))), \
                mock.patch.object(jobs, "find_by_name", lambda name: {"line_codes": [{"seq": 1, "code": "ICE-01"}]}):
            result = self.stock.void_payload("SE-1", COMPANY)
            doc.ledger_book = "another-book"
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
                     cross_year=False, exempt_mode="split", units=()):
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
                return types.SimpleNamespace(ledger_state=state, ledger_docnum=docnum, grand_total=D(grand), day="2026-10-24")
            if doctype == self.pos_day.DOCTYPE:
                return summary
            if doctype == "ERP Company":
                return {"tax_id": OWN, "branch": "00012"}
            if doctype == "ERP Product":
                return {"product_name": name, "express_code": name, "express_stock": 1, "base_unit": "ถุง", "unit": "ถุง",
                        "serial_kind": "device"}
            return "01"

        def sql(query, values=None, as_dict=False):
            if "tabERP Line Serial" in query:  # 小票上登记的台（按台号码）：(品, 小票类型, 主号)
                return [types.SimpleNamespace(product=product, warehouse="W1", unit="ถุง", sale_type=sale_type,
                                              serial_no=number, secondary_no="", color="") for product, sale_type, number in units]
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

    def test_the_units_sold_that_day_hang_under_their_line_without_those_returned_the_same_day(self):
        """按台号码（评估 2026-11-02）：日汇总合并行下挂当天卖出的台，同一天退回的去掉；退货日汇总挂退回的台。"""
        units = [("A", "sale", "352948094650001"), ("A", "sale", "352948094650002"), ("A", "refund", "352948094650002"),
                 ("B", "sale", "X1")]
        body = self.summary_body([("A", "1", "100"), ("B", "1", "50")], [], "157", vat="7", units=units)
        self.assertEqual([item.get("remarks") for item in body["items"]], [["S/N 352948094650001"], ["S/N X1"]])
        refund = self.summary_body([("A", "1", "93.46")], [], "100", vat="6.54", kind="SR",
                                   against=("written", "HS0000039", "200"), units=[("A", "refund", "352948094650002")])
        self.assertEqual(refund["items"][0].get("remarks"), ["S/N 352948094650002"])

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
                    mock.patch.object(target, "pick", lambda row, day, wanted=None: {"book": {"key": "BK"}}), \
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



class PrintedTotal(PushModule):
    """2026-11-03「CC 通用疑难处理」ERP 侧护栏：识别来的采购 / 销售单（单上有识别标记 ``ocr_review``）票面合计没有、或与单据
    合计对不上，Express 直写、代录交单、代录预检都停下进待处理，不准备、不排写账件（缺页的单明细不全，单据合计自洽却不完整，
    原来照写进 Express）。手工录的单、上线前的老单没有标记，照旧；票面合计印的是扣了预扣税的实付额也算对上。"""

    MARK = '{"codes":["total_missing"],"total":null}'

    def document(self, doctype=None, **extra):
        doctype = doctype or self.push.PURCHASE
        docs = fake.STATE.db.table(doctype)
        docs["PI-1"] = {"name": "PI-1", "creation": "2026-11-03 09:00:00", "status": self.push.ready(doctype),
                        "owner": "a@x", "company": "CO-1", "ledger_state": "queued", "ledger_job": None,
                        "ledger_reason": "", "grand_total": 41589.83, "net_payable": 41589.83, **extra}
        return docs["PI-1"]

    def send(self, doctype=None, operator=None):
        from unittest import mock
        with mock.patch.object(self.push, "document", side_effect=AssertionError("must not build")), \
                mock.patch.object(self.push.prepare, "prepare", side_effect=AssertionError("must not prepare")), \
                mock.patch.object(self.push.jobs, "enqueue", side_effect=AssertionError("must not queue")), \
                mock.patch.object(self.push, "company_of", lambda doctype, name: "CO-1"), \
                mock.patch.object(frappe.db, "commit", lambda: None, create=True):
            return self.push.send(doctype or self.push.PURCHASE, "PI-1", operator=operator)

    def test_the_pure_rule(self):
        hold = E.printed_hold
        self.assertIsNone(hold(False, None, 100))                      # 手工单 / 老单：不核对
        self.assertEqual(hold(True, None, 100), E.PRINTED_TOTAL_MISSING)
        self.assertEqual(hold(True, 0, 100), E.PRINTED_TOTAL_MISSING)  # 金额栏空着存成 0
        self.assertEqual(hold(True, "44372.47", "41589.83"), E.PRINTED_TOTAL_MISMATCH)
        self.assertIsNone(hold(True, "107.00", Decimal("107.005")))     # 容差同识别核对
        self.assertIsNone(hold(True, 104, 107, net_payable=104))       # 票面印的是扣预扣税后的实付额
        self.assertEqual(hold(True, 105, 107, net_payable=104), E.PRINTED_TOTAL_MISMATCH)

    def test_a_recognized_document_without_its_printed_total_is_held_before_express(self):
        """BJC 第 7 页那张（缺第 2 页）：票面合计没读到，ERP 按已有明细算出 41,589.83，原来照写进 Express。"""
        row = self.document(ocr_review=self.MARK, printed_total=0)
        self.assertEqual(self.send(), {"status": "held", "reason": E.PRINTED_TOTAL_MISSING})
        self.assertEqual((row["ledger_state"], row["ledger_reason"]), ("held", E.PRINTED_TOTAL_MISSING))
        self.assertTrue(E.needs_person(row["ledger_state"], row["ledger_reason"]))  # 进待处理，不每分钟自己再试

    def test_a_printed_total_that_differs_from_the_document_is_held_for_direct_and_operator_writes(self):
        for doctype in (self.push.PURCHASE, self.push.SALES):
            for operator in (None, {"id": "b1", "index": 1, "total": 1}):
                row = self.document(doctype, ocr_review=self.MARK, printed_total=44372.47)
                self.assertEqual(self.send(doctype, operator), {"status": "held", "reason": E.PRINTED_TOTAL_MISMATCH})
                self.assertEqual(row["ledger_reason"], E.PRINTED_TOTAL_MISMATCH, (doctype, operator))

    def test_operator_precheck_reports_the_hold_without_preparing(self):
        from unittest import mock
        self.document(ocr_review=self.MARK, printed_total=44372.47)
        with mock.patch.object(self.push, "_prepare_body", side_effect=AssertionError("must not prepare")):
            self.assertEqual(self.push.prepare_only(self.push.PURCHASE, "PI-1"), {"hold": E.PRINTED_TOTAL_MISMATCH})

    def test_matching_totals_and_unmarked_documents_still_write(self):
        from unittest import mock
        for extra in ({"ocr_review": self.MARK, "printed_total": 41589.83}, {"printed_total": 0},
                      {"ocr_review": None, "printed_total": 99}):
            self.document(**extra)
            made = []
            with mock.patch.object(self.push, "document", lambda doctype, name: {"ref": "PI-1@x", "number": "PI-1"}), \
                    mock.patch.object(self.push, "company_of", lambda doctype, name: "CO-1"), \
                    mock.patch.object(self.push, "_prepare_body", lambda doctype, company, body:
                                      ({"payload": {"payload_version": 4}, "book": {"key": "BK"}}, None, None)), \
                    mock.patch.object(self.push, "actor_of", lambda *a: "a@x"), \
                    mock.patch.object(self.push.jobs, "of_source", lambda *a: []), \
                    mock.patch.object(self.push.jobs, "enqueue", lambda **kw: made.append(kw) or {"job": "J1"}), \
                    mock.patch.object(frappe.db, "commit", lambda: None, create=True):
                self.assertEqual(self.push.send(self.push.PURCHASE, "PI-1"), {"status": "sent"}, extra)
            self.assertEqual(len(made), 1, extra)

    def test_the_hold_reasons_have_words_on_the_document_and_in_pending(self):
        from pearnly_erp_portal.express import pending
        js = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/transactions.js").read_text(encoding="utf-8")
        words = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/i18n/messages.tsv").read_text(encoding="utf-8")
        for code in (E.PRINTED_TOTAL_MISSING, E.PRINTED_TOTAL_MISMATCH):
            key = pending.REASONS[code]
            self.assertIn(f'"{code}": "{key}"', js)
            self.assertIn(f"\t{key}\n", words)


class PrintedTotalRecognition(unittest.TestCase):
    """识别结果落到单据上的两栏（`recognition.checks.marks`）：网页识别、LINE 卡片建单、LINE 页面提交都带上；采购 / 销售入账接收。"""

    def test_marks_keep_the_read_total_and_review_codes(self):
        from pearnly_erp_portal.recognition import checks
        marks = checks.marks({"printed": {"subtotal": "38869.00", "vat": "2720.83", "total": None},
                              "review": ["total_missing"]})
        self.assertEqual(marks["printed_total"], None)
        self.assertEqual(json.loads(marks["ocr_review"]), {"codes": ["total_missing"], "total": None})
        self.assertEqual(checks.marks({"printed": {"total": "1,070.00"}})["printed_total"], 1070.0)

    def test_every_recognition_path_carries_the_marks_and_entry_accepts_them(self):
        base = ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal"
        read = lambda path: (base / path).read_text(encoding="utf-8")
        self.assertIn("result.update(checks.marks(result))", read("purchasing/recognition.py"))
        self.assertIn("**checks.marks(result)}", read("sales/recognition.py"))
        intake = read("integrations/intake.py")
        self.assertEqual(intake.count("**_recogniser(doc.direction).marks(result))"), 2)  # LINE 卡片建单：采购、销售
        self.assertIn('payload["ocr_review"] = _recogniser(doc.direction).marks(', intake)  # 页面提交：服务端补标记
        for path in ("purchasing/entry.py", "sales/entry.py"):
            head = read(path).split("HEAD_FIELDS = (", 1)[1].split(")", 1)[0]
            self.assertIn('"printed_total"', head, path)
            self.assertIn('"ocr_review"', head, path)
        for slug in ("purchase_document", "erp_sales_document"):
            meta = json.loads(read(f"pearnly_erp_portal/doctype/{slug}/{slug}.json"))
            fields = {field["fieldname"]: field for field in meta["fields"]}
            self.assertEqual((fields["printed_total"]["fieldtype"], fields["printed_total"].get("read_only")), ("Currency", None))
            self.assertEqual((fields["ocr_review"].get("hidden"), fields["ocr_review"].get("read_only")), (1, 1))
            self.assertEqual(meta.get("track_changes"), 1, slug)  # 谁、何时改了票面合计查版本记录


class ExpressWaiting(PushModule):
    """排队中的单此刻在等 Express 关闭（有人开着这本账套，11-12）：网页列表的这一页与单据的 Express 弹窗多带只读的
    ``ledger_waiting`` / ``waiting``（读的时候算，`cc_desktop.jobs.express_wait_many`，一次查完）；单据上的 ``ledger_reason``
    不写（排队中时照旧是空的）。"""

    def setUp(self):
        super().setUp()
        # 这里第一次导入的 ERP 模块（`express.api` 及它带进来的）用完就卸掉：它们按导入时的 frappe.utils 替身绑定函数，
        # 留着会改变后面的测试类看到的模块
        before = set(sys.modules)

        def unload():
            for name in sorted(set(sys.modules) - before, reverse=True):
                if name.startswith("pearnly_erp_portal."):
                    parent, _dot, child = name.rpartition(".")
                    sys.modules.pop(name)
                    if getattr(sys.modules.get(parent), child, None) is not None:
                        delattr(sys.modules[parent], child)
        self.addCleanup(unload)

    def job(self, name, *, status="queued", reason="ACCOUNT_BUSY_LOCKED", ago=10):
        from datetime import timedelta
        from pearnly_core.cc_desktop import devices, documents
        from pearnly_core.cc_desktop.wire import ack_rules
        fake.STATE.db.table(documents.JOB)[name] = {
            "name": name, "status": status, "reason": reason, "book_key": "B",
            "lease_expires_at": devices.now() - timedelta(seconds=ago)
            + timedelta(seconds=ack_rules.EXPRESS_OPEN_REST_SECONDS)}

    def documents(self):
        from pearnly_core.cc_desktop.wire import ack_rules
        table = fake.STATE.db.table("Purchase Document")
        for name, state, job in (("PUR-1", "queued", "J1"), ("PUR-2", "queued", "J2"), ("PUR-3", "queued", None),
                                 ("PUR-4", "held", "J4"), ("PUR-5", "queued", "J5"), ("PUR-6", "written", "J6")):
            table[name] = {"name": name, "ledger_state": state, "ledger_job": job, "ledger_reason": ""}
        self.job("J1")
        self.job("J2", ago=ack_rules.EXPRESS_OPEN_SHOWN_SECONDS + 1)   # 电脑 90 秒没再回报：回到普通的排队中
        self.job("J4", status="manual")
        self.job("J5", reason="")                                       # 别的等待（另一台电脑在写）不算
        self.job("J6", status="success")
        return [Row(name=name, ledger_state=row["ledger_state"]) for name, row in table.items()]

    def test_a_page_of_documents_is_marked_in_one_go(self):
        from pearnly_erp_portal.express import waiting
        rows = self.documents()
        asked = []
        real = frappe.get_all
        with mock.patch.object(frappe, "get_all", lambda doctype, **kw: asked.append(doctype) or real(doctype, **kw)):
            self.assertIs(waiting.mark("Purchase Document", rows), rows)
        self.assertEqual({row["name"]: row.get("ledger_waiting") for row in rows},
                         {"PUR-1": "express_open", "PUR-2": None, "PUR-3": None, "PUR-4": None, "PUR-5": None,
                          "PUR-6": None})
        self.assertEqual(asked, ["Purchase Document", "Pearnly CC Job"])   # 这一页排队中的单一次、写账件一次
        self.assertTrue(all("ledger_job" not in row for row in rows))       # 任务名不带给页面
        self.assertEqual(fake.STATE.db.table("Purchase Document")["PUR-1"]["ledger_reason"], "")   # 单据上不写原因
        # 这一页没有排队中的单：不查
        asked.clear()
        with mock.patch.object(frappe, "get_all", lambda doctype, **kw: asked.append(doctype) or real(doctype, **kw)):
            waiting.mark("Purchase Document", [Row(name="PUR-4", ledger_state="held"), Row(name="POS-1")])
            waiting.mark("Purchase Document", [])
        self.assertEqual(asked, [])

    def test_receipts_are_marked_by_their_day_summary(self):
        from pearnly_erp_portal.express import waiting
        fake.STATE.db.table("ERP POS Day Summary")["PDS-1"] = {"name": "PDS-1", "ledger_state": "queued",
                                                               "ledger_job": "J1"}
        self.job("J1")
        rows = [Row(name="POS-1", express_summary="PDS-1", ledger_state="queued"),
                Row(name="POS-2", express_summary="PDS-1", ledger_state="queued"),
                Row(name="POS-3", express_summary="", ledger_state="")]
        waiting.mark("ERP POS Day Summary", rows, key="express_summary")
        self.assertEqual([row.get("ledger_waiting") for row in rows], ["express_open", "express_open", None])

    def api(self):
        utils = sys.modules["frappe.utils"]
        for name, value in (("now", lambda: "2026-10-24 00:00:00"), ("flt", lambda v, *a: float(v or 0)),
                            ("cint", lambda v: int(v or 0)), ("getdate", lambda v=None: v), ("add_days", lambda d, n: d)):
            setattr(utils, name, getattr(utils, name, value))
        from pearnly_erp_portal.express import api
        return api

    def view(self, **values):
        api = self.api()
        base = {"name": "PUR-1", "company": "CO-1", "ledger_state": "queued", "ledger_job": "J1", "ledger_book": None,
                "ledger_docnum": "", "ledger_reason": "", "ledger_needed": None}
        row = Row({**base, **values})
        with mock.patch.object(api.guard, "has", lambda capability, user=None: True):
            return api._view(values.pop("doctype", "Purchase Document"), row)

    def test_the_express_dialog_says_the_document_waits_for_express(self):
        from pearnly_core.cc_desktop.wire import ack_rules
        self.job("J1")
        view = self.view()
        self.assertEqual((view["state"], view["waiting"], view["reason"]), ("queued", "express_open", ""))
        self.assertEqual((view["can_answer"], view["can_retry"]), (False, False))
        self.job("J1", ago=ack_rules.EXPRESS_OPEN_SHOWN_SECONDS + 1)
        self.assertEqual(self.view()["waiting"], "")           # 过期：普通的排队中
        self.job("J1", status="leased")
        self.assertEqual(self.view()["waiting"], "")           # 被领走在写
        self.job("J1")
        self.assertEqual(self.view(ledger_state="held")["waiting"], "")   # 单已经不是排队中
        self.assertEqual(self.view(ledger_job=None)["waiting"], "")

    def test_a_void_waiting_for_express_says_so_too(self):
        # 已写进 Express 又在 ERP 作废的出入库单：Express 作废原单那一件在等 Express 关闭
        self.job("JV")
        view = self.view(doctype="ERP Stock Entry", ledger_state="written", ledger_docnum="OU1", ledger_job="J0",
                         ledger_void_state="queued", ledger_void_job="JV", ledger_void_reason="")
        self.assertEqual((view["void"], view["waiting"]), ("queued", "express_open"))
        view = self.view(doctype="ERP Stock Entry", ledger_state="written", ledger_docnum="OU1", ledger_job="J0",
                         ledger_void_state="held", ledger_void_job="JV", ledger_void_reason="x")
        self.assertEqual(view["waiting"], "")

    def test_a_disconnected_company_is_not_waiting(self):
        self.job("J1")
        fake.STATE.db.table("ERP Company")["CO-1"]["express_enabled"] = 0
        self.assertEqual(self.view()["waiting"], "")

    def test_the_dialog_takes_its_ledger_from_the_document_then_the_company(self):
        """B13（11-14）：同一个账本弹窗按账本来源取文案与调用；来源 = 单据记的账本，没记过看公司（`mrerp.push.handles`）。"""
        api = self.api()
        with mock.patch.object(api.mrerp_push, "handles", lambda company: True):
            self.assertEqual(api.doc_ledger(Row(company="CO-1", ledger_target="mrerp")), "mrerp")
            self.assertEqual(api.doc_ledger(Row(company="CO-1", ledger_target="")), "mrerp")
            self.assertEqual(api.doc_ledger(Row(company="CO-1", ledger_target="express")), "express")
        with mock.patch.object(api.mrerp_push, "handles", lambda company: False):  # 开关关着 / 公司不是 MR.ERP 账本
            self.assertEqual(api.doc_ledger(Row(company="CO-1", ledger_target="mrerp")), "express")
            self.assertEqual(api.doc_ledger(Row(company="CO-1")), "express")

    def mrerp_view(self, **values):
        api = self.api()
        with mock.patch.object(api.mrerp_push, "handles", lambda company: True):
            return self.view(ledger_target="mrerp", ledger_job=None, **values)

    def test_an_mrerp_document_gets_the_same_dialog_exits(self):
        menu = [{"kind": "mrerp_menu", "subject": "purchase", "options": [{"code": "68", "name": "ซื้อ"}]},
                {"kind": "mrerp_default", "subject": "ap_account", "options": [{"code": "2120-03", "name": "เจ้าหนี้"}]}]
        view = self.mrerp_view(ledger_state="held", ledger_reason="choice_needed:mrerp", ledger_needed=json.dumps(menu))
        self.assertEqual((view["ledger"], view["can_answer"], view["can_retry"]), ("mrerp", True, False))
        self.assertEqual([item["label"] for item in view["needed"]], ["mrerp.menu.purchase", "mrerp.default.ap_account"])
        view = self.mrerp_view(ledger_state="failed", ledger_reason="ERR_MRERP_CASH_SUPPLIER_NOT_CASH")
        self.assertEqual((view["needed"], view["can_answer"], view["can_retry"]), ([], False, True))
        self.assertTrue(self.mrerp_view(ledger_state="held", ledger_reason="mrerp.login_missing")["can_retry"])
        for state, reason in (("held", "mrerp.result_unknown: ERR_TECHNICAL"), ("queued", "mrerp.importing"),
                              ("written", ""), ("mismatch", "")):
            view = self.mrerp_view(ledger_state=state, ledger_reason=reason, ledger_docnum="PG691114-1")
            self.assertEqual((view["can_answer"], view["can_retry"], view["docnum"]), (False, False, "PG691114-1"), state)

    def test_mrerp_retry_and_answer_go_to_the_mrerp_import(self):
        api = self.api()
        failed = Row(name="PUR-1", company="CO-1", ledger_target="mrerp", ledger_state="failed", ledger_reason="ERR_X",
                     ledger_docnum="", ledger_needed=None, ledger_job=None, ledger_book=None)
        sent, answered = [], []
        with mock.patch.object(api.mrerp_push, "handles", lambda company: True), \
                mock.patch.object(api, "_document", lambda doctype, name, write=False: failed), \
                mock.patch.object(api.guard, "has", lambda capability, user=None: True), \
                mock.patch.object(api.mrerp_push, "send", lambda d, n, manual=False: sent.append((d, n, manual))), \
                mock.patch.object(api.mrerp_push, "answer", lambda d, n, a: answered.append(a) or True), \
                mock.patch.object(api.frappe, "parse_json", json.loads, create=True):
            api.retry("Purchase Document", "PUR-1")
            self.assertEqual(sent, [("Purchase Document", "PUR-1", True)])  # 人点的：清被踢标记
            api.answer("Purchase Document", "PUR-1", json.dumps([{"kind": "mrerp_menu", "subject": "purchase", "value": "68"}]))
            self.assertEqual(answered, [[{"kind": "mrerp_menu", "subject": "purchase", "value": "68"}]])
            failed["ledger_reason"] = "mrerp.result_unknown: ERR_TECHNICAL"  # 结果不明：可能已写进去，不再推
            with self.assertRaises(Exception):
                api.retry("Purchase Document", "PUR-1")
            self.assertEqual(len(sent), 1)

    def test_every_list_entry_marks_its_page(self):
        """采购、销售、出入库 / 调拨、收银小票四个列表接口都给这一页补 ``ledger_waiting``（导出与合计不走这里）。"""
        import ast
        erp = ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal"
        wanted = {"purchasing/api.py": {"api_purchase_records": 'waiting.mark("Purchase Document"'},
                  "sales/api.py": {"api_sales_records": 'waiting.mark("ERP Sales Document"'},
                  "inventory/api.py": {"api_list_stock_entries": 'waiting.mark("ERP Stock Entry"'},
                  "cashier/api.py": {"api_list_sales": 'waiting.mark("ERP POS Day Summary"'}}
        for path, functions in wanted.items():
            source = (erp / path).read_text(encoding="utf-8")
            bodies = {node.name: ast.get_source_segment(source, node) for node in ast.parse(source).body
                      if isinstance(node, ast.FunctionDef)}
            for name, call in functions.items():
                self.assertIn(call, bodies[name], f"{path}:{name}")
        cashier = (erp / "cashier/api.py").read_text(encoding="utf-8")
        # 收银台里的「本班销售」没有 Express 一栏（`api_current_sales`）：不问
        self.assertEqual(cashier.count('key="express_summary"'), 1)
        # 导出、合计取数（`records.rows` / `list_entries` 本身）不补：只有列表这一页问
        for path in ("purchasing/records.py", "sales/records.py", "inventory/documents.py", "cashier/sales.py"):
            self.assertNotIn("waiting", (erp / path).read_text(encoding="utf-8"), path)


if __name__ == "__main__":
    unittest.main()
