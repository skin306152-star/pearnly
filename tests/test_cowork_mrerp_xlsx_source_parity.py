"""MR.ERP 导入表：新包与旧线上生成器逐项对照（离线）。

旧来源是冻结版本 ec2e5c957e6b 的 `services/erp/mrerp_xlsx_*` 与所需叶子模块，用 `git show` 导到一个目录后
经 `PEARNLY_MRERP_LEGACY_ROOT` 传入；没给就跳过（本仓不带旧代码）。两个旧包的 `__init__.py`
（`services/erp/express_push`、`services/sales_agg`）只做包级再导出，对照时换成空文件，不影响被比较的函数。

比较的是 xlsx 解压后每个成员的字节（zip 时间戳等容器元数据不算），覆盖推送会用到的全部表型：
销项赊账 / 现金、采购货品 / 费用、客户 / 供应商 / 商品主档。
"""
import io
import os
import sys
import unittest
import zipfile

LEGACY = os.environ.get("PEARNLY_MRERP_LEGACY_ROOT")

BUYER = {"buyer_name": "บริษัท ลูกค้า จำกัด", "buyer_tax": "0105562055501"}
SELLER = {"seller_name": "บริษัท ผู้ขาย จำกัด", "seller_tax": "0105561012345"}
ITEMS = [
    {"name": "กระดาษ A4 80 แกรม", "qty": "5", "unit_price": "115.00", "amount": "575.00"},
    {"name": "หมึกพิมพ์ HP 85A", "qty": "2", "unit_price": "950.00", "amount": "1900.00"},
]


def history(hid, **fields):
    base = {"invoice_number": f"INV-{hid}", "date": "2026-09-12", "subtotal": "2475.00", "vat": "173.25",
            "total_amount": "2648.25", "items": ITEMS}
    base.update(fields)
    return {"id": f"CWH-{hid:0>12}", "invoice_no": base["invoice_number"], "invoice_date": base["date"],
            "total_amount": base["total_amount"], "fields": base}


# 有意变化（2026-10-08）：分店 / 部门 / 员工 / 科目等不再写死 TEST2019 的值，改由推送前按这家公司的主档算好放进
# mappings。对照时把 TEST2019 的值从 mappings 喂给新生成器（旧生成器认得其中的 `_mrerp_customer_*` 等覆盖键，
# 不认得的键忽略），其余输出必须与旧线上逐字节一致。
TEST2019 = {
    "_mrerp_header": {"branch": "00000", "dept": "BOI1", "job": "00002", "store": "0000", "employee": "กร ทดสอบ",
                      "area": "สุพรรณบุรี", "delivery": "ขนส่งโดยบริษัท"},
    "_mrerp_customer_type": "1-11", "_mrerp_customer_account": "1130-03", "_mrerp_customer_salesman": "กร",
    "_mrerp_customer_area": "สุพรรณ", "_mrerp_customer_shipping": "TRUCK1", "_mrerp_customer_branch": "00000",
    "_mrerp_customer_prefix": "บริษัท",
    "_mrerp_supplier_type": "2-21", "_mrerp_supplier_account": "2110-01", "_mrerp_supplier_branch": "00000",
    "_mrerp_supplier_prefix": "บริษัท",
    "_mrerp_product_category": "03-FIG", **{f"_mrerp_product_unit_{k}": "ผน" for k in ("count", "std", "buy", "sell")},
    "_mrerp_product_acc_rev": "4110-01", "_mrerp_product_acc_ret": "4110-10", "_mrerp_product_acc_dis": "4110-20",
    "_mrerp_product_acc_pur": "5110-01", "_mrerp_product_acc_purret": "5110-11", "_mrerp_product_acc_purdis": "5110-21",
    "_mrerp_product_acc_inv": "1150-30", "_mrerp_product_acc_cost": "5100-01", "_mrerp_product_supplier": "CODE_NOAP",
    "_mrerp_receipt_account_cash": "1111-01", "_mrerp_receipt_account_bank": "1112-01",
}

CASES = [
    # 客户编号取两边都认的值：旧版按整数比较，新平台按字符串（Cowork Client 名称）比较。
    # 两个商品都映射：对不上商品码时旧版兜底写 TEST2019 的商品 "123"，新版留空让 MR.ERP 明确拒（有意变化）
    ("sales_credit", False, [dict(history("a1b2c3d4", **SELLER, **BUYER), client_id="7")],
     {"clients": [{"erp_type": "mrerp", "client_id": 7, "erp_code": "C0001"}],
      "products": [{"item_name": "กระดาษ A4 80 แกรม", "erp_code": "P-A4"},
                   {"item_name": "หมึกพิมพ์ HP 85A", "erp_code": "P-HP"}]}),
    ("sales_cash", False, [history("0000beef", **SELLER, payment_method="cash")],
     {"products": [{"item_name": "กระดาษ A4 80 แกรม", "erp_code": "P-A4"},
                   {"item_name": "หมึกพิมพ์ HP 85A", "erp_code": "P-HP"}]}),
    ("purchase", False, [history("00c0ffee", **SELLER, **BUYER)], {}),
    ("purchase", True, [history("00facade", **SELLER, **BUYER)], {}),
    ("customer_master", False, [{"code": "0105562055501", "name": "บริษัท ลูกค้า จำกัด",
                                 "tax_id": "0105562055501", "address": "99 ถนนสุขุมวิท"}], {}),
    ("supplier_master", False, [{"code": "0105561012345", "name": "บริษัท ผู้ขาย จำกัด",
                                 "tax_id": "0105561012345"}], {}),
    ("product_master", False, [{"code": "P3A1B2C4D5E6", "name": "กระดาษ A4 80 แกรม", "price": "115.00"}], {}),
]


def members(blob):
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


@unittest.skipUnless(LEGACY, "set PEARNLY_MRERP_LEGACY_ROOT to the extracted ec2e5c95 tree")
class XlsxSourceParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, LEGACY)
        from services.erp import mrerp_xlsx_generator as legacy
        from pearnly_cowork.push.mrerp.xlsx import generator as native
        cls.legacy, cls.native = legacy, native

    def test_every_push_sheet_matches_member_for_member(self):
        import copy
        for kind, expense, rows, mappings in CASES:
            with self.subTest(kind=kind, expense=expense):
                old = self.legacy.generate_xlsx(copy.deepcopy(rows), copy.deepcopy(mappings),
                                                sheet_kind=kind, expense=expense)
                new = self.native.generate_xlsx(copy.deepcopy(rows), {**TEST2019, **copy.deepcopy(mappings)},
                                                sheet_kind=kind, expense=expense)
                if kind == "sales_cash":
                    # 有意变化：现金收款写现金栏（第 16 列），不再记进「รับอื่นๆ 1」槽（MR.ERP 过账时那一槽借记的是
                    # 菜单关联的借方 3，不一定是现金）。只换这两格的值，其余逐字节相同。
                    old, new = members(old), members(new)
                    self.assertEqual(set(old), set(new))
                    for name in old:
                        if name != "xl/worksheets/sheet1.xml":
                            self.assertEqual(new[name], old[name], name)
                    sheet = new["xl/worksheets/sheet1.xml"].decode()
                    self.assertIn('<c r="P2" s="10"><v>2648.25</v></c>', sheet)  # 现金栏 = 全额
                    self.assertIn('<c r="R2" s="10"><v>0</v></c>', sheet)        # รับอื่นๆ 1 = 0
                    self.assertEqual(sheet.replace('<c r="P2" s="10"><v>2648.25</v></c>', '<c r="P2" s="10"><v>0</v></c>')
                                     .replace('<c r="R2" s="10"><v>0</v></c>', '<c r="R2" s="10"><v>2648.25</v></c>'),
                                     old["xl/worksheets/sheet1.xml"].decode())
                    continue
                self.assertEqual(members(new), members(old))

    def test_invoice_number_and_preflight_match(self):
        import copy
        for _, _, rows, mappings in CASES[:4]:
            for row in rows:
                self.assertEqual(self.native.derive_mrerp_invoice_no(row), self.legacy.derive_mrerp_invoice_no(row))
                self.assertEqual(self.native.validate_history_for_sales_credit(copy.deepcopy(row), mappings),
                                 self.legacy.validate_history_for_sales_credit(copy.deepcopy(row), mappings))


if __name__ == "__main__":
    unittest.main()
