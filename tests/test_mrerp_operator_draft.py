"""MR.ERP 代录要填的值（共用层 `pearnly_core.mrerp.push.operator_draft`，ADR-026 第 2 条，第 3 步服务端这一侧）。

- **黄金样本**：赊购（货）、赊购（费用）、赊销、现销四种单，固定输入 → 固定 ``mrerp`` 块 JSON（`fixtures/mrerp/operator-draft-golden.json`，
  键的先后也比）。第 2 步 CC 执行层（分支 `pearnly/cc-mrerp-operator`，`mrerp_operator/draft.py`）拿同一份样本的 ``block`` 喂
  ``draft.from_payload`` 核两边一致（那一侧的测试在第 2 步分支补，见施工记录 `docs/work/2026-11-03-cowork-mrerp-operator-wiring.md`）。
  改了块的形状要同时改两边：``--write`` 重写样本后，第 2 步的契约测试必须跟着过；
- **页面同算法的合计**（MR.ERP ``allform.js::calcgrandtotal``）：价外按税前乘税率、价内逐行拆税、表头折扣、``นอกระบบ`` 不算税；
- **开录前准备** ``stage``：与直写同一套分流（方向 / 付款 / 货费 / 防呆），每组切到对应模块后交 ``ready_rows``（匹配闸、导入值、菜单、
  自建对方 / 商品都在那里），挡下的带原因、没验证的模块整组转人工、做完把模块换回来；不生成导入表、不上传。

纯函数与替身，不连网、不要 Frappe。``python3 tests/test_mrerp_operator_draft.py --write`` 重写黄金样本（只在有意改块的形状时）。
"""
import copy
import json
import pathlib
import sys
import unittest
from datetime import date
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))

from pearnly_core.mrerp.push import operator_draft, routing  # noqa: E402
from pearnly_core.mrerp.push.models import FailedRow  # noqa: E402
from pearnly_core.mrerp.push.modules import get_module  # noqa: E402

GOLDEN = ROOT / "tests/fixtures/mrerp/operator-draft-golden.json"
TODAY = date(2026, 9, 26)
OWN = "0105559000001"
SUPPLIER = "0105561012345"
CUSTOMER = "0105562055501"
URL = "https://www.mrerp4sme.com/"
CONNECTION = "CWEC-MRERP-1"
ACCOUNT_SET = {"comidyear": "12", "seldb": "1", "name": "TEST2026", "company_name": "บริษัท เรา จำกัด"}

ITEMS = [{"name": "กระดาษ A4 80 แกรม", "qty": "5", "unit_price": "115.00", "amount": "575.00"},
         {"name": "หมึกพิมพ์ HP 85A", "qty": "2", "unit_price": "950.00", "amount": "1900.00"}]

# `ready_rows` 之后的 mappings：导入值、对方 / 商品码都已注入（自建的也在里面）
MAPPINGS = {
    "_own_tax_id": OWN,
    "_mrerp_header": {"branch": "00000", "dept": "D001", "job": "00001", "store": "0001", "employee": "สมชาย ใจดี",
                      "area": "กรุงเทพ", "delivery": "ส่งเอง"},
    "_mrerp_customer_header": {"C0001": {"employee": "สมหญิง ขายดี", "area": "เชียงใหม่", "delivery": ""}},
    "suppliers": [{"erp_code": "S-0001", "seller_tax": SUPPLIER}],
    "clients": [{"erp_type": "mrerp", "client_id": "CL-7", "erp_code": "C0001"}],
    "products": [{"item_name": "กระดาษ A4 80 แกรม", "erp_code": "P-A4"},
                 {"item_name": "หมึกพิมพ์ HP 85A", "erp_code": "P-HP"}],
    "_mrerp_expense_item_code": "EXP-01",
}


def flat(hid, direction, **fields):
    """`flatten_history_for_mrerp` 的形状：顶层几列 + ``fields``。"""
    base = {"invoice_number": f"INV-{hid}", "date": "2026-09-12", "subtotal": "2475.00", "vat": "173.25",
            "total_amount": "2648.25", "direction": direction, "items": copy.deepcopy(ITEMS)}
    base.update(fields)
    return {"id": f"CWH-{hid:0>12}", "invoice_no": base["invoice_number"], "invoice_date": base["date"],
            "total_amount": base["total_amount"], "direction": direction, "fields": base}


def purchase(hid, **fields):
    """进项货品单（复核屏点过「货品」，与直写同一个判据）；``fields`` 覆盖票面。"""
    return flat(hid, "purchase", **{"seller_name": "บริษัท ผู้ขาย จำกัด", "seller_tax": SUPPLIER,
                                    "buyer_name": "บริษัท เรา จำกัด", "buyer_tax": OWN,
                                    "posting_item_type_manual": "goods", **fields})


def sales(hid, **fields):
    return flat(hid, "sales", seller_name="บริษัท เรา จำกัด", seller_tax=OWN, **fields)


# 四种单：(样本名, doc_type, 菜单号, 票)
CASES = [
    ("purchase_goods", "purchase", "68", purchase("a1b2c3d4")),
    ("purchase_expense", "purchase_expense", "453",
     purchase("b2c3d4e5", posting_item_type_manual="expense", subtotal="500.00", vat="35.00", total_amount="535.00",
              items=[{"name": "ค่าบริการขนส่ง", "qty": "1", "unit_price": "500.00", "amount": "500.00"}])),
    ("sales_credit_included", "sales_credit", "70",
     {**sales("c3d4e5f6", buyer_name="บริษัท ลูกค้า จำกัด", buyer_tax=CUSTOMER, vat_included=True, subtotal="2313.08",
              vat="161.92", total_amount="2475.00"), "client_id": "CL-7"}),
    ("sales_cash_walkin_discount", "sales_cash", "71",
     sales("d4e5f6a7", discount="75.00", subtotal="2400.00", vat="168.00", total_amount="2568.00",
           posting_payment_manual="cash")),
]


def build(case):
    _name, doc_type, menu, history = case
    return operator_draft.block(copy.deepcopy(history), copy.deepcopy(MAPPINGS), doc_type=doc_type, menu=menu,
                                url=URL, connection=CONNECTION, account_set=ACCOUNT_SET)


def dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=1)


def write_golden():
    cases = []
    for case in CASES:
        name, doc_type, menu, history = case
        cases.append({"name": name, "doc_type": doc_type, "menu": menu, "url": URL, "connection": CONNECTION,
                      "account_set": ACCOUNT_SET, "history": history, "mappings": MAPPINGS, "block": build(case)})
    GOLDEN.write_text(dump({"about": "operator_draft.block 黄金样本（tests/test_mrerp_operator_draft.py --write）；输入全是合成值",
                            "cases": cases}) + "\n", encoding="utf-8")


class Golden(unittest.TestCase):
    """固定输入 → 固定 ``mrerp`` 块（逐字符，含键的先后）。"""

    @classmethod
    def setUpClass(cls):
        cls.golden = json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]

    def test_the_four_document_kinds_match_the_golden_blocks_character_for_character(self):
        self.assertEqual([c["name"] for c in self.golden], [c[0] for c in CASES])
        self.assertEqual({c["doc_type"] for c in self.golden}, set(operator_draft.DOC_TYPES))
        for case, saved in zip(CASES, self.golden):
            with self.subTest(case[0]):
                # 样本里存的输入就是这里的输入（样本自带输入，第 2 步那一侧只读样本也能复算）
                self.assertEqual((saved["history"], saved["mappings"]), (case[3], MAPPINGS))
                self.assertEqual(dump(build(case)), dump(saved["block"]))

    def test_the_blocks_carry_the_shape_the_cc_side_reads_and_no_password(self):
        keys = ["url", "connection", "account_set", "doc_type", "menu", "docnum", "date", "party_code", "ref_no",
                "ref_date", "credit_days", "vat_rate", "vat_included", "discount", "defaults", "lines", "totals"]
        for saved in self.golden:
            block = saved["block"]
            with self.subTest(saved["name"]):
                self.assertEqual(list(block), keys)
                self.assertEqual(list(block["account_set"]), ["comidyear", "seldb", "name", "company_name"])
                self.assertEqual(list(block["defaults"]), ["dept", "job", "employee", "branch", "store"])
                self.assertEqual(list(block["totals"]), ["before_vat", "vat", "net"])
                for line in block["lines"]:
                    self.assertEqual(list(line), ["code", "qty", "price", "unit", "store", "desc"])
                self.assertEqual(block["url"], "https://www.mrerp4sme.com")  # 去掉末尾斜杠
                self.assertIn(block["vat_rate"], ("7 (แยก)", "7 (รวม)", "0", "นอกระบบ"))
                text = dump(block).lower()
                self.assertFalse(any(word in text for word in ("password", "passwd", "credential", "token")))

    def test_each_cell_comes_from_the_same_source_as_the_import_sheet(self):
        by = {c["name"]: c["block"] for c in self.golden}
        goods = by["purchase_goods"]
        self.assertEqual((goods["party_code"], goods["ref_no"], goods["vat_rate"], goods["vat_included"]),
                         ("S-0001", "INV-a1b2c3d4", "7 (แยก)", False))
        self.assertEqual([(l["code"], l["qty"], l["price"], l["store"]) for l in goods["lines"]],
                         [("P-A4", "5", "115", "0001"), ("P-HP", "2", "950", "0001")])
        self.assertEqual(goods["totals"], {"before_vat": "2475.00", "vat": "173.25", "net": "2648.25"})
        self.assertEqual(goods["defaults"], {"dept": "D001", "job": "00001", "employee": "สมชาย ใจดี", "branch": "00000",
                                             "store": "0001"})
        self.assertRegex(goods["docnum"], r"^690912-\d{6}$")  # 佛历年 + 月日 + 票号派生序号，与导入表同一个
        self.assertEqual((goods["date"], goods["ref_date"]), ("2026-09-12", "2026-09-12"))
        # 费用档：一行含税总额、通用费用码、นอกระบบ 不算税
        expense = by["purchase_expense"]
        self.assertEqual((expense["vat_rate"], [(l["code"], l["qty"], l["price"]) for l in expense["lines"]]),
                         ("นอกระบบ", [("EXP-01", "1", "535")]))
        self.assertEqual(expense["totals"], {"before_vat": "535.00", "vat": "0.00", "net": "535.00"})
        # 赊销：客户码按映射、表头员工 / 区域用客户档的（空的不覆盖）、税务发票号 = 票面号（11-14 起不再写死 SI + 单号）
        credit = by["sales_credit_included"]
        self.assertEqual((credit["party_code"], credit["vat_rate"], credit["vat_included"]), ("C0001", "7 (รวม)", True))
        self.assertEqual(credit["ref_no"], "INV-c3d4e5f6")  # `fmt.mrerp_bill_no`
        self.assertEqual((credit["defaults"]["employee"], credit["defaults"]["store"]), ("สมหญิง ขายดี", "0001"))
        # 价内逐行拆税：575 → 37.62，1900 → 124.30
        self.assertEqual(credit["totals"], {"before_vat": "2313.08", "vat": "161.92", "net": "2475.00"})
        # 现销散客：现金客户、折扣写表头、价外
        cash = by["sales_cash_walkin_discount"]
        self.assertEqual((cash["party_code"], cash["discount"], cash["vat_rate"]), ("เงินสด", "75", "7 (แยก)"))
        self.assertEqual(cash["totals"], {"before_vat": "2400.00", "vat": "168.00", "net": "2568.00"})


class Totals(unittest.TestCase):
    def test_page_arithmetic(self):
        line = [{"qty": "3", "price": "33.335"}]
        self.assertEqual(operator_draft.totals(line, "7 (แยก)"), {"before_vat": "100.01", "vat": "7.00", "net": "107.01"})
        self.assertEqual(operator_draft.totals(line, "0"), {"before_vat": "100.01", "vat": "0.00", "net": "100.01"})
        self.assertEqual(operator_draft.totals(line, "นอกระบบ"),
                         {"before_vat": "100.01", "vat": "0.00", "net": "100.01"})
        # 价内带表头折扣：税额先逐行拆、再减折扣的税
        self.assertEqual(operator_draft.totals([{"qty": "1", "price": "107"}], "7 (รวม)", "10.70"),
                         {"before_vat": "89.30", "vat": "6.25", "net": "95.55"})
        # 认不出的税率文字按不算税（CC 读页面合计对不上就不存）
        self.assertEqual(operator_draft.totals(line, "8%")["vat"], "0.00")

    def test_an_unknown_document_kind_is_refused(self):
        with self.assertRaises(ValueError):
            operator_draft.block(CASES[0][3], MAPPINGS, doc_type="master_customer", menu="", url=URL,
                                 connection=CONNECTION, account_set=ACCOUNT_SET)


class FakeImporter:
    """`MrErpImport` 的替身：记下每组交给 ``ready_rows`` 的模块与票；可配置挡下哪些票、自建补进哪些码。"""

    def __init__(self, *, stop=(), menus=None, created=None):
        self.module = get_module("sales_credit")
        self.stop, self.menus, self.created = set(stop), menus or {}, created or {}
        self.calls, self.taxed = [], []

    def note_account_tax(self, histories, mappings):
        self.taxed.append([h["id"] for h in histories])

    def ready_rows(self, rows, mappings):
        doc_type = self.module.doc_type
        self.calls.append((doc_type, [h["id"] for h in rows]))
        mappings.setdefault("suppliers", []).extend(self.created.get(doc_type, []))  # 自建对方：同一份 mappings
        valid = [h for h in rows if h["id"] not in self.stop]
        stopped = [FailedRow(invoice_no="x", reasons=["ERR_NO_SUPPLIER"], original=h) for h in rows if h["id"] in self.stop]
        return valid, stopped, self.menus.get(doc_type)


def stage(importer, histories, mappings=None):
    return operator_draft.stage(importer, histories, copy.deepcopy(mappings or MAPPINGS), today=TODAY, url=URL,
                                connection=CONNECTION, account_set=ACCOUNT_SET)


class Stage(unittest.TestCase):
    def test_the_same_routing_as_direct_writes_then_ready_rows_per_group_and_the_module_is_put_back(self):
        goods, expense, credit, cash = (copy.deepcopy(case[3]) for case in CASES)
        importer = FakeImporter(menus={"purchase": "68", "purchase_expense": "453", "sales_credit": "70",
                                       "sales_cash": "71"})
        blocks, failed = stage(importer, [credit, goods, cash, expense])
        self.assertEqual(importer.calls, [("sales_credit", [credit["id"]]), ("purchase", [goods["id"]]),
                                          ("sales_cash", [cash["id"]]), ("purchase_expense", [expense["id"]])])
        self.assertEqual(importer.taxed, [[credit["id"], goods["id"], cash["id"], expense["id"]]])
        self.assertEqual(failed, {})
        self.assertEqual({key: (b["doc_type"], b["menu"]) for key, b in blocks.items()},
                         {credit["id"]: ("sales_credit", "70"), goods["id"]: ("purchase", "68"),
                          cash["id"]: ("sales_cash", "71"), expense["id"]: ("purchase_expense", "453")})
        self.assertEqual(importer.module.doc_type, "sales_credit")
        # 与黄金样本同一份块（stage 只是按组调 block）
        golden = {c["history"]["id"]: c["block"] for c in json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]}
        for key, block in blocks.items():
            self.assertEqual(dump(block), dump(golden[key]))

    def test_codes_created_in_ready_rows_reach_the_block(self):
        stranger = purchase("e5f6a7b8", seller_tax="0105563099990", seller_name="บริษัท ใหม่ จำกัด")
        importer = FakeImporter(created={"purchase": [{"erp_code": "S-NEW", "seller_tax": "0105563099990"}]})
        blocks, failed = stage(importer, [stranger])
        self.assertEqual((failed, blocks[stranger["id"]]["party_code"]), ({}, "S-NEW"))

    def test_held_rows_carry_their_reasons_and_never_get_a_block(self):
        goods, other = purchase("f6a7b8c9"), purchase("a7b8c9d0")
        foreign = purchase("b8c9d0e1", currency="USD")
        importer = FakeImporter(stop={other["id"]})
        blocks, failed = stage(importer, [goods, other, foreign])
        self.assertEqual(list(blocks), [goods["id"]])
        self.assertEqual(failed, {other["id"]: ["ERR_NO_SUPPLIER"], foreign["id"]: ["mrerp_currency_not_thb:usd"]})
        self.assertEqual(importer.calls, [("purchase", [goods["id"], other["id"]])])  # 防呆挡下的不进准备

    def test_a_cash_purchase_is_held_for_a_person_before_any_preparation(self):
        # 现购代录还没做（配方没有开票用供应商那一格）：不进准备、不建主档，原因码转人工；同批赊购照常出块
        goods, paid = purchase("e1f2a3b4"), purchase("f2a3b4c5", posting_payment_manual="cash")
        importer = FakeImporter(menus={"purchase": "68"})
        blocks, failed = stage(importer, [goods, paid])
        self.assertEqual((list(blocks), failed), ([goods["id"]], {paid["id"]: [operator_draft.OPERATOR_CASH_PURCHASE]}))
        self.assertEqual(importer.calls, [("purchase", [goods["id"]])])

    def test_a_module_not_verified_on_a_real_site_is_held_as_a_whole_group(self):
        goods, credit = purchase("c9d0e1f2"), {**sales("d0e1f2a3", buyer_tax=CUSTOMER), "client_id": "CL-7"}
        real = get_module

        def unverified(doc_type):
            module = real(doc_type)
            return module.__class__(**{**module.__dict__, "verified": False}) if doc_type == "purchase" else module

        importer = FakeImporter()
        with mock.patch.object(operator_draft, "get_module", unverified):
            blocks, failed = stage(importer, [goods, credit])
        self.assertEqual(failed, {goods["id"]: [routing.MODULE_UNAVAILABLE_CODE]})
        self.assertEqual(list(blocks), [credit["id"]])
        self.assertEqual(importer.module.doc_type, "sales_credit")

    def test_the_module_is_put_back_even_when_preparation_fails(self):
        importer = FakeImporter()
        importer.ready_rows = mock.Mock(side_effect=RuntimeError("site down"))
        with self.assertRaises(RuntimeError):
            stage(importer, [purchase("e1f2a3b4")])
        self.assertEqual(importer.module.doc_type, "sales_credit")


if __name__ == "__main__":
    if "--write" in sys.argv:
        write_golden()
        print(f"wrote {GOLDEN.relative_to(ROOT)}")
    else:
        unittest.main()
