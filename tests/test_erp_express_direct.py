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
        self.assertEqual((queued[0]["company"], queued[0]["notify"]), ("CO-1", "admin@x"))  # 读好了也不在请求里落库
        self.assertEqual(fake.STATE.db.table("ERP Company")["CO-1"]["express_sync_state"], "reading")
        errors = []
        with mock.patch.object(self.sync, "_sync_once", side_effect=RuntimeError("disk full")), \
                mock.patch.object(frappe, "log_error", lambda **kw: errors.append(kw), create=True), \
                mock.patch.object(frappe, "publish_realtime", lambda event, data, **kw: pushed.append((event, data, kw)),
                                  create=True):
            result = self.sync.sync("CO-1", notify="admin@x")
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

        def request(company):
            if company == "CO-0":
                return {"state": "reading", "made": {"status": "done"}}
            started.append(company)
            return {"state": "reading", "made": {"status": "requested"}}

        with mock.patch.object(self.sync, "_request", request), \
                mock.patch.object(frappe, "enqueue", lambda method, **kw: queued.append(kw), create=True):
            self.sync.advance()
        self.assertEqual([(kw["company"], kw["wait"]) for kw in queued], [("CO-0", 0)])  # 读好的排落库，不再等
        self.assertEqual(started, ["CO-1", "CO-2", "CO-3"])
        self.assertEqual([table[f"CO-{i}"]["express_sync_state"] for i in range(1, 6)],
                         ["reading", "reading", "reading", "queued", "queued"])
        self.sync.queue("CO-1")  # 已经在读的不打回排队
        self.assertEqual(table["CO-1"]["express_sync_state"], "reading")

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

    def test_an_empty_company_address_and_phone_come_from_the_book(self):
        table = fake.STATE.db.table("ERP Company")
        table["CO-1"] = {"name": "CO-1", "address": "", "phone": "02-111"}
        settings = {"address": "99 ถ.สุขุมวิท กรุงเทพฯ", "phone": "02-999"}
        self.assertEqual(self.sync._profile("CO-1", settings), {"address": "99 ถ.สุขุมวิท กรุงเทพฯ"})  # 人填过的不动
        self.assertEqual(self.sync._profile("CO-1", {}), {})

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
                         "(ifnull(`tabERP Supplier`.`express_company`, '') = '' "
                         "or `tabERP Supplier`.`express_company` = 'CO-A')")
        self.assertEqual(self.access.master_query("ERP Supplier", "Administrator"), "")

    def test_a_record_opens_for_a_company_the_user_may_use(self):
        allowed = self.access.master_permission
        self.assertIsNone(allowed({"express_company": ""}, user="staff@x"))       # 本地档：照角色权限
        self.assertIsNone(allowed({"express_company": "CO-B"}, user="staff@x"))   # 能用的另一家：旧单的链接打得开
        self.assertFalse(allowed({"express_company": "CO-Z"}, user="staff@x"))

if __name__ == "__main__":
    unittest.main()
