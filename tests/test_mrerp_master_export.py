"""MR.ERP 整读主档（`pearnly_core.mrerp.master_export`，公司接入第 3 批）：导出页与按仓结存报表的原样 xlsx（TEST2019 只读取得）
解析成与 Express 主档同一形状；网络一侧按固定的 10 个请求读完、读不到不当成空账套。不需要 Frappe。"""
from datetime import date
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))
# CI 的 python3 不装第三方包：登录会话模块只在类定义里用到 requests，这里不连网
if "requests" not in sys.modules:
    _requests = types.ModuleType("requests")
    _requests.Session, _requests.RequestException = object, Exception
    sys.modules["requests"] = _requests

from pearnly_core.mrerp import master_export as mx  # noqa: E402

FIXTURES = ROOT / "tests/fixtures/mrerp"


def rows(name):
    return mx.xlsx_rows((FIXTURES / name).read_bytes())


class ParseTests(unittest.TestCase):
    def test_customers(self):
        found = {row["code"]: row for row in mx.parties(rows("export_customers.xlsx"), customer=True)}
        self.assertEqual(len(found), 7)
        self.assertEqual(found["0105115236825"]["name"], "บริษัท ลูกค้าเงินสด สี่ จำกัด")
        self.assertEqual(found["0105115236825"]["tax_id"], "0105115236825")
        self.assertEqual(found["0105115236825"]["branch"], "00000")
        self.assertEqual(found["0105115236825"]["address"], "4 ถนนทดสอบ กรุงเทพ 10230")
        # 称谓 + 名；税号写「-」的是没有税号（落库时 `contracts.tax13` 当空）
        self.assertEqual(found["01-อนุรักษ์-001"]["name"], "นาย อนุรักษ์ ทดสอบ")
        self.assertEqual(found["01-อนุรักษ์-001"]["tax_id"], "-")

    def test_suppliers(self):
        found = {row["code"]: row for row in mx.parties(rows("export_suppliers.xlsx"), customer=False)}
        self.assertEqual(len(found), 14)
        self.assertEqual(found["0105138069232"]["name"], "บริษัท เจ้าหนี้ทดสอบ สี่ จำกัด")
        self.assertEqual(found["0105138069232"]["tax_id"], "0105138069232")

    def test_products(self):
        found = {row["code"]: row for row in mx.products(rows("export_products.xlsx"))}
        self.assertEqual(len(found), 12)
        item = found["P17DDFA43C32"]
        self.assertEqual((item["name"], item["kind"], item["unit"], item["prices"]), ("สินค้า ERP ขาย A", "stock", "คัน", ["200"]))
        self.assertEqual(found["EXPENSE"]["kind"], "service")

    def test_stock_by_store(self):
        warehouses, stock = mx.stock(rows("report_stock_by_store.xlsx"))
        self.assertLessEqual({row["code"] for row in warehouses}, {"0000", "0001", "0002"})
        by_key = {(row["code"], row["loccod"]): row["qty"] for row in stock}
        self.assertEqual(by_key[("P6506F369EE8", "0000")], "88")
        self.assertEqual(by_key[("P17DDFA43C32", "0000")], "-10")
        self.assertEqual(len(stock), 11)

    def test_shape_matches_express_contract(self):
        """读出的行能直接交 Express 的契约函数（`express.contracts.party / product`）。"""
        sys.modules.setdefault("frappe", types.ModuleType("frappe"))
        sys.path.insert(0, str(ROOT / "apps/pearnly_erp_portal"))
        from pearnly_erp_portal.express import contracts

        party = contracts.party(mx.parties(rows("export_customers.xlsx"), customer=True)[0])
        self.assertEqual(party["tax_id"], "")
        product = contracts.product(mx.products(rows("export_products.xlsx"))[1],
                                    {"คัน": "คัน"})
        self.assertTrue(product["stock"])
        self.assertEqual(product["price"], "200.00")

    def test_not_xlsx_is_unreadable(self):
        with self.assertRaises(mx.ExportError):
            mx.xlsx_rows(b"<script>alert('No database selected')</script>")


class FakeResponse:
    def __init__(self, text="", content=b""):
        self.text, self.content = text, content or text.encode()


class FakeSession:
    base = "https://mrerp.example.com"

    def __init__(self, broken=None):
        self.calls, self.broken = [], broken

    def request(self, method, path, **kwargs):
        self.calls.append((method, path))
        if path == self.broken:
            return FakeResponse("<script>alert('x')</script>No database selected")
        if path.endswith("bshlistboxdata.php"):
            import json
            if path.startswith("exparmas"):
                body = {"txtarcodebegin": [[1, "0105115236825", "x", 1], [9, "CCUS-00021", "y", 2]]}
            elif path.startswith("expapmas"):
                body = {"txtapcodebegin": [[1, "0105102376381", "x", 1]]}
            elif path.startswith("expstkmas"):
                body = {"txtstkcodebegin": [[1, "EXPENSE", "x", 1]]}
            elif path.startswith("stkmas"):
                body = {"txtunit_usell": [[1, "คัน", "คัน"]]}
            else:
                body = {"txtstorebegin": [[1, "0000", "สนง ใหญ่", 1], [3, "0002", "คลัง สาขา 2", 3]],
                        "txtstkmasbegin": [[1, "EXPENSE", "x", 1], [2, "P6506F369EE8", "y", 2]]}
            return FakeResponse(json.dumps(body))
        if path.endswith("form.php"):
            return FakeResponse('<select name="selstktypebegin" id="selstktypebegin"><option value="1">a</option>'
                                '<option value="4">b</option></select>')
        name = {"exparmas/component/expdata.php": "export_customers.xlsx",
                "expapmas/component/expdata.php": "export_suppliers.xlsx",
                "expstkmas/component/expdata.php": "export_products.xlsx",
                "component/php/showreportfile.php": "report_stock_by_store.xlsx"}[path]
        self.last = kwargs.get("data")
        return FakeResponse(content=(FIXTURES / name).read_bytes())


class ReadTests(unittest.TestCase):
    def test_the_negative_stock_setting_is_read_from_the_issue_form(self):
        class Negative(FakeSession):
            def request(self, method, path, **kwargs):
                if path == mx.NEGATIVE_PAGE:
                    self.calls.append((method, path))
                    return FakeResponse("<script>var ssunegative = 2;</script>")
                return super().request(method, path, **kwargs)

        facts, _stats = mx.read_session(Negative(), today=date(2026, 11, 14))
        self.assertEqual(facts["settings"], {"mainloc": "0000", "stock_negative": "2"})

    def test_fixed_requests_and_ranges(self):
        session = FakeSession()
        facts, stats = mx.read_session(session, today=date(2026, 11, 14))
        self.assertEqual(stats["requests"], 11)  # 11-19 起多读一次出库新单网页的「允许负库存」（读不到不进 settings）
        self.assertEqual(len(facts["customers"]), 7)
        self.assertEqual(facts["units"], [{"code": "คัน", "name": "คัน"}])
        self.assertEqual(facts["settings"], {"mainloc": "0000"})
        # 结存报表：仓库与物料的首末编码、全部物料类型、今天（佛历）
        self.assertEqual((session.last["txtstorebegin"], session.last["txtstoreend"]), ("0000", "0002"))
        self.assertEqual((session.last["selstktypebegin"], session.last["selstktypeend"]), (1, 4))
        self.assertEqual(session.last["txtstkmasdate"], "14/11/2569")
        # 仓库：列表接口的全部仓（没有结存的仓也建），报表里另见到的补上
        self.assertLessEqual({"0000", "0002"}, {row["code"] for row in facts["warehouses"]})

    def test_unreadable_list_is_an_error_not_an_empty_ledger(self):
        with self.assertRaises(mx.ExportError):
            mx.read_session(FakeSession(broken="expapmas/component/bshlistboxdata.php"))


if __name__ == "__main__":
    unittest.main()
