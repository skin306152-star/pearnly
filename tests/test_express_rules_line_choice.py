"""采购 / 销售按行选物料与库存（11-06，评估 docs/work/2026-11-06-erp-line-item-stock-choice-评估.md 第 15 节）：共用写账规则。

不连库、不依赖 Frappe：
- 「存货对照」一问扩到没声明过账类型的行：选存货 / 新建存货 = 库存行，选「记费用」或 Express 非库存品 = 费用行，名字对上目录的不问；
- 调用方没要按行问（``undeclared_lines`` 不给）时，没声明的行照旧不问、不改（周期制账套不受影响）；
- 这一行记过的优先于整批声明；行上声明的（复核页逐行选的）优先于记过的；
- 混行单有费用行时问费用科目（以前有库存行就不问）；
- 映射器按定下的过账类型出混行载荷，盖混行版本。
"""
import pathlib
import sys
import unittest
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))

from pearnly_core.express_rules.push import choices  # noqa: E402
from pearnly_core.express_rules.push.mapper import build_express_payload  # noqa: E402

TAX = "0105550000001"
PRODUCTS = [{"code": "ICE01", "name": "Ice", "kind": "stock"}, {"code": "FRT01", "name": "Freight", "kind": "service"}]
CONFIG = {"reported_products": PRODUCTS, "reported_accounts": [{"code": "53-01"}]}


def _fields(*names, kinds=None):
    items = [{"name": name, "qty": "1", "subtotal": "10"} for name in names]
    for item, kind in zip(items, kinds or ()):
        if kind:
            item["posting_kind"] = kind
    return {"seller_name": "Synthetic Supplier", "seller_tax": TAX, "items": items}


def _key(name):
    return choices.item_subject("purchase", TAX, "Synthetic Supplier", name)


def _resolve(fields, posting_kind=None, remembered=None, **kw):
    return choices.resolve(fields, direction="purchase", posting_kind=posting_kind, config=CONFIG,
                           remembered=remembered or {}, **kw)


def _asked(out, kind="item"):
    return [row for row in out["needed"] if row["kind"] == kind]


class LineChoiceTest(unittest.TestCase):
    def test_undeclared_lines_are_asked_once_each_with_an_expense_option(self):
        out = _resolve(_fields("Ice", "Freight", "Box"), undeclared_lines=True)
        self.assertEqual(out["applied"]["kinds"], {"ice": "stock", "freight": "service"})
        self.assertEqual([(row["label"], row["expense"], row["subject"]) for row in _asked(out)],
                         [("Box", True, _key("Box"))])

    def test_without_the_flag_undeclared_lines_stay_as_before(self):
        out = _resolve(_fields("Ice", "Freight", "Box"))
        self.assertNotIn("kinds", out["applied"])
        self.assertEqual(_asked(out), [])

    def test_a_remembered_line_beats_the_batch_and_a_line_declared_on_the_page_beats_the_memory(self):
        memory = {"item": {_key("Box"): choices.EXPENSE, _key("Ice"): choices.EXPENSE}}
        out = _resolve(_fields("Box", "Ice"), posting_kind="stock", remembered=memory)
        self.assertEqual(out["applied"]["kinds"], {"box": "service", "ice": "service"})
        out = _resolve(_fields("Box", "Ice", kinds=("stock", "stock")), posting_kind="stock", remembered=memory)
        self.assertNotIn("kinds", out["applied"])
        # 行上声明了存货：记过的「记费用」不算；同名存货照名找得到不问，不同名的问、不给「记费用」
        self.assertEqual([(row["label"], row["expense"], row["current"]) for row in _asked(out)], [("Box", False, "")])

    def test_a_batch_stock_line_can_be_turned_into_an_expense_line(self):
        out = _resolve(_fields("Box"), posting_kind="stock")
        self.assertEqual([(row["label"], row["expense"]) for row in _asked(out)], [("Box", True)])

    def test_a_new_stock_item_and_an_existing_code_are_kept_as_items(self):
        memory = {"item": {_key("Box"): choices.NEW, _key("Bag"): "ICE01"}}
        out = _resolve(_fields("Box", "Bag"), remembered=memory, undeclared_lines=True)
        self.assertEqual(out["applied"]["kinds"], {"box": "stock", "bag": "stock"})
        self.assertEqual(out["applied"]["items"], {"box": choices.NEW, "bag": "ICE01"})

    def test_a_mixed_purchase_asks_the_expense_account(self):
        memory = {"item": {_key("Box"): choices.EXPENSE}}
        self.assertEqual(len(_asked(_resolve(_fields("Ice", "Box"), posting_kind="stock", remembered=memory), "account")), 1)
        self.assertEqual(_asked(_resolve(_fields("Ice"), posting_kind="stock"), "account"), [])
        remembered = {**memory, "account": {choices.supplier_subject("purchase", TAX, "Synthetic Supplier"): "53-01"}}
        out = _resolve(_fields("Ice", "Box"), posting_kind="stock", remembered=remembered)
        self.assertEqual((out["applied"].get("account"), _asked(out, "account")), ("53-01", []))

    def test_line_kinds_and_with_line_kinds(self):
        rows = _fields("Ice", "Box")["items"]
        self.assertEqual(choices.line_kinds(rows, "stock", {"box": "service"}), ["stock", "service"])
        self.assertEqual(choices.line_kinds(rows, None, {"box": "service"}), ["", "service"])
        out = choices.with_line_kinds({"items": rows}, {"box": "service"}, "stock")
        self.assertEqual([row["posting_kind"] for row in out["items"]], ["stock", "service"])
        partial = {"items": rows}
        self.assertIs(choices.with_line_kinds(partial, {"box": "service"}, None), partial)  # 有的行定不下：不硬写

    def test_the_mapper_writes_a_mixed_purchase_from_the_decided_kinds(self):
        fields = {"seller_name": "Synthetic Supplier", "seller_tax": TAX, "subtotal": "100.00", "vat": "7.00",
                  "invoice_number": "SYN-9", "posting_item_type_manual": "goods",
                  "items": [{"name": "Ice", "qty": "1", "subtotal": "60.00"}, {"name": "Box", "qty": "1", "subtotal": "40.00"}]}
        history = {"id": "h9", "invoice_date": "2026-09-28", "invoice_no": "SYN-9", "total_amount": "107.00", "fields": fields}
        config = {"account_set": "TEST", "fallback_acc": "51-01", "vat_input_acc": "11-05", "ap_acc": "21-02",
                  "catalog_fingerprint": {"stock_master_count": 5, "stcrd_lines": 10, "stcrd_lines_moving_stock": 0},
                  "choices": {"kinds": {"box": "service"}, "account": "53-01"}}
        result = build_express_payload(history, config=config, posting_kind="stock", today=date(2026, 9, 28))
        self.assertTrue(result.ok, result.reason)
        self.assertEqual([item["item_mode"] for item in result.payload["items"]], ["stock_item", "direct_account"])
        self.assertEqual(result.payload["payload_version"], 19)


if __name__ == "__main__":
    unittest.main()
