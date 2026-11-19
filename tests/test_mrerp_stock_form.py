"""共用层 MR.ERP 调拨 / 调整网页表单原生保存（`pearnly_core.mrerp.push.stock_form`，11-19）的离线检查：纯函数部分只用标准库，CI 直接跑。

照 MR.ERP 源码与测试账套实录（11-19）：主菜单页读菜单、隐藏值原样带回、表单传数据库 id、日期 dd/mm/佛历、第二单位不传（传空串会被当 0
号单位违反外键整张回滚）、会计期间 ``dt||Y`` / ``dt||N||天数``、调拨两仓不同与同物料并行、调整同物料同仓轧差、``Save Success``、
列表按完整单号找（调拨 ``<p>``、调整 ``<tr>``）。
"""
from __future__ import annotations

import pathlib
import sys
import unittest
from datetime import date
from decimal import Decimal

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT / "apps/pearnly_core") not in sys.path:
    sys.path.insert(0, str(ROOT / "apps/pearnly_core"))

from pearnly_core.mrerp import menus  # noqa: E402
from pearnly_core.mrerp.push import stock_form as form  # noqa: E402

LISTBOX_TRANSFER = {
    "txtcode[]": [[223, "P-01", "สีแดง", 8, "กิโลกรัม"], [224, "P-02", "สีฟ้า", 9, "ชิ้น"]],
    "txtdept[]": [[1, "0000", "-"], [2, "d001", "dname"]], "txtsuborderd[]": [[1, "0000", "-"]],
    "txtothcombrh": [[1, "00000", "สำนักงานใหญ่"]],
    "txtstorefrom": [[1, "0000", "ใหญ่"], [2, "0001", "สาขา"]], "txtstoreto": [[1, "0000", "ใหญ่"], [2, "0001", "สาขา"]]}
LISTBOX_ADJUST = {**{k: v for k, v in LISTBOX_TRANSFER.items() if not k.startswith("txtstore")},
                  "txtcode[]": [[223, "P-01", "สีแดง", 1, 8, "กิโลกรัม"]], "txtstore[]": [[1, "0000", "ใหญ่"], [2, "0001", "สาขา"]]}
HIDDEN = {"statusautono": "", "userdefinedno": "", "runautono": "", "othcombrhval": "1", "txtdoctype": "IT"}
HEAD = {"branch": "00000", "dept": "0000", "job": "0000"}


def transfer(**extra):
    base = {"kind": form.TRANSFER, "number": "ERPT-1", "date": "2026-11-19", "notes": ["n1", "", "ERPT-1"],
            "from_store": "0000", "to_store": "0001", "lines": [{"code": "P-01", "qty": "2"}, {"code": "P-01", "qty": "1"}]}
    base.update(extra)
    return base


def adjust(**extra):
    base = {"kind": form.ADJUST, "number": "ERPA-1", "date": "2026-11-19", "notes": [],
            "lines": [{"code": "P-01", "store": "0000", "qty": "5", "price": "12"},
                      {"code": "P-01", "store": "0000", "qty": "-2"}, {"code": "P-01", "store": "0001", "qty": "-1"}]}
    base.update(extra)
    return base


def as_dict(fields):
    out = {}
    for key, value in fields:
        out.setdefault(key, []).append(value)
    return out


class Pure(unittest.TestCase):
    def test_menus_come_from_the_main_menu_links(self):
        page = ('<a href="../stktrant/allview.php?idmenu=37"><span>โอนย้าย</span></a>'
                '<a href="../stktranmod/allview.php?idmenu=39"><span>ปรับปรุง</span></a>'
                '<a href="../stktrant/allview.php?idmenu=37">x</a>')
        self.assertEqual(form.menus_of(page, "stktrant"), [{"idmenu": "37", "label": "โอนย้าย"}])
        self.assertEqual(form.menus_of(page, "stktranmod"), [{"idmenu": "39", "label": "ปรับปรุง"}])

    def test_dates_periods_and_header_rules(self):
        self.assertEqual(form.be_date("2026-11-19"), "19/11/2569")
        today = date(2026, 11, 19)
        self.assertTrue(form.period_problem("dt||Y", "2026-11-19", today))
        self.assertFalse(form.period_problem("dt||N||365", "2026-01-01", today))
        self.assertTrue(form.period_problem("dt||N||30", "2026-10-01", today))
        self.assertEqual(form.check(transfer()), "")
        self.assertEqual(form.check(transfer(to_store="0000")), form.SAME_STORE)
        self.assertEqual(form.check(transfer(number="X" * 19)), form.NUMBER_INVALID)
        self.assertEqual(form.check(transfer(lines=[{"code": "P-01", "qty": "-1"}])), form.QTY_INVALID)
        self.assertEqual(form.check(adjust(lines=[{"code": "P-01", "store": "0000", "qty": "1"},
                                                  {"code": "P-01", "store": "0000", "qty": "-1"}])), form.LINES_EMPTY)

    def test_transfer_fields_carry_ids_merge_the_same_item_and_skip_the_second_unit(self):
        fields, problem = form.form_fields(transfer(), HIDDEN, LISTBOX_TRANSFER, HEAD, {"P-01": Decimal("4.5")})
        self.assertEqual(problem, "")
        got = as_dict(fields)
        self.assertEqual((got["txtdocnum"], got["txtdate"], got["othcombrhval"]), (["ERPT-1"], ["19/11/2569"], ["1"]))
        self.assertEqual((got["txtstorefromval"], got["txtstoretoval"]), (["1"], ["2"]))
        self.assertEqual((got["txtcodeval[]"], got["txtamount[]"], got["txtunitval[]"]), (["223"], ["3"], ["8"]))
        self.assertEqual((got["txtprice[]"], got["txttotal[]"], got["txtgrandtotal"]), (["4.5"], ["13.5"], ["13.5"]))
        for name in ("txtamount_pg2[]", "txtunitval_pg2[]", "txtstkmet[]", "txtstoreval[]"):
            self.assertNotIn(name, got)

    def test_adjust_fields_net_per_item_and_warehouse_with_the_costing_method(self):
        fields, problem = form.form_fields(adjust(), {**HIDDEN, "txtdoctype": "IA"}, LISTBOX_ADJUST, HEAD,
                                           {"P-01": Decimal("10")})
        self.assertEqual(problem, "")
        got = as_dict(fields)
        self.assertEqual(got["txtamount[]"], ["3", "-1"])           # 0000：5 − 2；0001：−1
        self.assertEqual(got["txtstoreval[]"], ["1", "2"])
        self.assertEqual(got["txtstkmet[]"], ["1", "1"])
        self.assertEqual(got["txtunitval[]"], ["8", "8"])
        self.assertEqual(got["txtprice[]"], ["12", "10"])           # 增加行用给的价，减少行用系统成本
        self.assertEqual(got["txtcodematchval_pg3[]"], ["", ""])

    def test_unknown_codes_stop_before_saving(self):
        self.assertEqual(form.form_fields(transfer(lines=[{"code": "NOPE", "qty": "1"}]), HIDDEN, LISTBOX_TRANSFER, HEAD, {})[1],
                         form.ITEM_UNMAPPED)
        self.assertEqual(form.form_fields(transfer(to_store="9999"), HIDDEN, LISTBOX_TRANSFER, HEAD, {})[1],
                         form.WAREHOUSE_UNMAPPED)
        self.assertEqual(form.form_fields(transfer(), HIDDEN, LISTBOX_TRANSFER, {**HEAD, "dept": "x"}, {})[1],
                         "ERR_MRERP_STOCK_FIELD")

    def test_save_page_and_read_back(self):
        self.assertEqual(form.saved_problem("<span>Save Success</span><script>location='allview.php'</script>"), "")
        self.assertEqual(form.saved_problem('<script>alert("Error : Data is use in the system")</script>'),
                         f"{form.SAVE_FAILED}; Error : Data is use in the system")
        listing = ('<p><span>ITERPT-10</span><a href="allform.php?id=7&status=vie"></a></p>'
                   '<p><span>ITERPT-1</span><a href="allform.php?id=8&status=vie"></a></p>')
        self.assertEqual(form.listed(listing, "ITERPT-1"), "8")
        self.assertIsNone(form.listed(listing, "ITERPT-2"))
        rows = '<tr><td>IAERPA-1</td><td><a href="allform.php?id=5&amp;status=vie">v</a></td></tr>'
        self.assertEqual(form.listed(rows, "IAERPA-1"), "5")

    def test_hidden_values_are_read_from_the_new_form(self):
        page = ('<input type="hidden" name="statusautono" id="statusautono" value="">'
                '<input type="text" name="txtdoctype" id="txtdoctype" value="IT" readonly>'
                '<input type="hidden" name="othcombrhval" id="othcombrhval" value="1">')
        self.assertEqual(form.hidden_values(page), {"statusautono": "", "txtdoctype": "IT", "othcombrhval": "1"})

    def test_menu_rules_are_the_import_ones(self):
        options = [{"idmenu": "39", "label": "a", "linked": False}]
        self.assertEqual(menus.resolve(options, "")[1], menus.NOT_LINKED)
        self.assertEqual(menus.resolve([{**options[0], "linked": True}], "")[0]["idmenu"], "39")


if __name__ == "__main__":
    unittest.main()
