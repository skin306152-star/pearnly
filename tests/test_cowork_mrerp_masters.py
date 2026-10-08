"""MR.ERP 主档默认值的纯规则（targets/mrerp_masters.py）：解析接口与网页、判定取值、按账套记住所选。不需要 Frappe。

接口形状按 MR.ERP 源码（`*/component/bshlistboxdata.php`、`acreffile/acreffileform.php`、`armas/allform.php`），
2026-10-08 核对。
"""
import importlib.util
import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "mrerp_masters", ROOT / "apps/pearnly_cowork/pearnly_cowork/targets/mrerp_masters.py")
rules = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rules)

FABILLREV = {"txtothcombrh": [[1, "00000", "สำนักงานใหญ่"], [2, "0001", "สาขาผิดรูป"]],
             "txtothdept_h": [[1, "D001", "บัญชี"], [2, "D002", "ขาย"]],
             "txtothsuborder_h": [[1, "00001", "ทั่วไป"]],
             "txtemp": [[1, "E01", "นาย", "สมชาย ใจดี"], [2, "E02", "นาง", "มาลี ศรี สุข"]]}
ARMAS = {"txtacfile": [[1, "1111-01", "เงินสด"], [2, "1130-01", "ลูกหนี้"]],
         "txtararea": [[1, "000001", "กรุงเทพ"], [2, "สั้น", "รหัสไม่ครบหกตัว"]],
         "txtrectype": [[1, "1-11", "ลูกหนี้การค้า"]], "inpdupdata": [[1, "C1", "ลูกค้า"]]}


class Parse(unittest.TestCase):
    def test_listbox_must_be_a_json_object(self):
        self.assertEqual(rules.parse_listbox(json.dumps(FABILLREV)), FABILLREV)
        # 掉线时 MR.ERP 仍回 200：弹窗脚本 + No database selected
        self.assertIsNone(rules.parse_listbox('<script>alert("กรุณา Login เข้าสู่ระบบ")</script>No database selected'))
        self.assertIsNone(rules.parse_listbox("[]"))
        self.assertIsNone(rules.parse_listbox(""))

    def test_masters_keep_only_values_the_import_can_accept(self):
        fields = rules.masters([FABILLREV, ARMAS])
        self.assertEqual(fields["branch"], [{"code": "00000", "name": "สำนักงานใหญ่"}])  # 分店码必须正好 5 位
        self.assertEqual([v["code"] for v in fields["dept"]], ["D001", "D002"])
        # 名或姓本身带空格的员工导入永远对不上（按 CONCAT(名, 姓) 去空格比对），不给人选
        self.assertEqual(fields["employee"], [{"code": "E01", "name": "สมชาย ใจดี"}])
        self.assertEqual(fields["area"], [{"code": "000001", "name": "กรุงเทพ"}])  # 自建客户写代码，必须 6 位
        self.assertEqual([v["code"] for v in fields["chart"]], ["1111-01", "1130-01"])
        self.assertNotIn("store", fields)  # 表为空 / 没读这个接口：键不出现（≠ 空表）

    def test_reference_accounts_and_prefixes_come_from_the_pages(self):
        page = ('<input type="text" name="txtacfile[2]" id="txtacfile2" size="13" value="1111-01" readonly>'
                '<input type="text" name="txtacfiledetail[2]" value="เงินสด">'
                '<input type="text" name="txtacfile[15]" id="txtacfile15" value="" readonly>')
        self.assertEqual(rules.parse_reference(page), {2: "1111-01", 15: ""})
        self.assertIsNone(rules.parse_reference("<html></html>"))
        select = ('<select name="selprefix" id="selprefix"><option value="1">นาย</option>'
                  '<option value="2" selected>บริษัท</option><option value="3">ห้างหุ้นส่วนจำกัด</option></select>')
        prefixes = rules.parse_prefixes(select)
        self.assertEqual(prefixes, ["นาย", "บริษัท", "ห้างหุ้นส่วนจำกัด"])
        self.assertEqual(rules.prefix_for("บริษัท ลูกค้า จำกัด", prefixes), "บริษัท")
        self.assertEqual(rules.prefix_for("ห้างหุ้นส่วนจำกัด ก", prefixes), "ห้างหุ้นส่วนจำกัด")
        self.assertEqual(rules.prefix_for("ABC Co.", prefixes), "นาย")  # 都不是：用第一项（MR.ERP 网页默认）

    def test_customer_record_defaults(self):
        row = [9, "C0001", "บริษัท", "ลูกค้า", 9, "C0001", "ลูกค้า", 2, "สมหญิง ขายเก่ง", 2, "เชียงใหม่", 2, "ไปรษณีย์"]
        self.assertEqual(rules.customer_defaults({"txtarmas": [row]}),
                         {"C0001": {"employee": "สมหญิง ขายเก่ง", "area": "เชียงใหม่", "delivery": "ไปรษณีย์"}})


class Resolve(unittest.TestCase):
    def test_single_is_used_several_need_a_choice_gone_is_not_replaced(self):
        one, two = [{"code": "A", "name": "a"}], [{"code": "A", "name": "a"}, {"code": "B", "name": "b"}]
        self.assertEqual(rules.resolve(one, None), (one[0], None))
        self.assertEqual(rules.resolve(two, None), (None, rules.CHOICE_REQUIRED))
        self.assertEqual(rules.resolve(two, "B"), (two[1], None))
        self.assertEqual(rules.resolve(two, "C"), (None, rules.CHANGED))
        self.assertEqual(rules.resolve(None, "A"), (None, rules.UNREADABLE))
        self.assertEqual(rules.resolve([], None), (None, rules.UNREADABLE))
        # 科目字段不因「只有一个」就自动定
        self.assertEqual(rules.resolve(one, None, single=False), (None, rules.CHOICE_REQUIRED))

    def test_choices_are_remembered_per_account_set_and_only_from_what_was_read(self):
        observed = rules.observe(None, "15:1", rules.masters([FABILLREV, ARMAS]), at="t1")
        self.assertIsNone(rules.choose(None, "15:1", "dept", "D009", observed, actor="u", at="t2"))
        self.assertIsNone(rules.choose(None, "15:1", "nonsense", "D001", observed, actor="u", at="t2"))
        chosen = rules.choose(None, "15:1", "dept", "D002", observed, actor="u", at="t2")
        chosen = rules.choose(chosen, "15:1", "ar_account", "1130-01", observed, actor="u", at="t3")
        self.assertEqual(rules.chosen_map(chosen, "15:1"), {"dept": "D002", "ar_account": "1130-01"})
        self.assertEqual(rules.chosen_map(chosen, "16:1"), {})
        view = rules.view(chosen, observed, "15:1")
        self.assertTrue(view["known"])
        self.assertEqual(view["fields"]["dept"]["chosen"], "D002")
        self.assertFalse(view["fields"]["dept"]["needed"])
        self.assertFalse(view["fields"]["branch"]["needed"])           # 只有一个
        self.assertTrue(view["fields"]["bank_account"]["needed"])      # 科目没选过
        self.assertIsNone(view["fields"]["store"]["reason"])           # 没读到：不问（推送时再核对）
        self.assertNotIn("customer_branch", view["fields"])            # 不是选择项

    def test_a_failed_read_keeps_what_was_seen_before(self):
        observed = rules.observe(None, "15:1", rules.masters([FABILLREV]), at="t1")
        observed = rules.observe(observed, "15:1", None, at="t2", error="ERR_AUTH")
        self.assertEqual(rules.view(None, observed, "15:1")["error"], "ERR_AUTH")
        self.assertTrue(rules.observed_for(observed, "15:1")["dept"])


if __name__ == "__main__":
    unittest.main()
