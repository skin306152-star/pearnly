"""MR.ERP 选公司页 → 「公司 → 年度」目录（纯函数，无 IO）。

页面形状：2026-09-26 用 test01 实读的 selectdb.php（`<p>` 行、`<span>` 格：Comname / Comfullname /
Remark / Database name，一行一家公司，最后一格是这家公司的各年度链接）；旧线上 `ec2e5c957e6b`
单测的平铺链接（TEST2019=6/1 · TEST2020=15/1）；以及 `<tr>/<td>` 写法。
"""
from datetime import datetime, timezone
import unittest

from pearnly_cowork.connections import mrerp_catalog as catalog
from pearnly_cowork.targets.choices import account_choices_for_endpoint
from pearnly_cowork.targets.projection import normalize_projection

# 实测形状（test01 登录后的 selectdb.php，2026-09-26）：行是 <p>，格是 <span>。
LIVE = """<div id="showdata"> <p> <span>Comname</span> <span>Comfullname</span> <span>Remark</span>
<span>Database name</span> </p> <p> <span>TEST</span> <span>1010-01-000006 บริษัท ทดสอบการใช้ จำกัด</span>
<span> </span> <span> <a href="mainmenu.php?comidyear=6&seldb=1">TEST2019</a>
<a href="mainmenu.php?comidyear=15&seldb=1">TEST2020</a> </span> </p> </div>
<div id="footer"> <p> <span>Username : </span>test01 <span>Status : </span>User</p> </div>"""
TABLE = """
<table><tr><th>Comname</th><th>Comfullname</th><th>Remark</th><th>Database name</th></tr>
<tr><td>TEST</td><td>1010-01-000006 บริษัท ทดสอบการใช้ จำกัด</td><td></td>
<td><a class="btn" href="mainmenu.php?comidyear=6&seldb=1">TEST2019</a><br>
<a class="btn" href="mainmenu.php?comidyear=15&amp;seldb=1"> TEST2020 </a></td></tr>
<tr><td>ACME</td><td>Acme &amp; Sons Co., Ltd.</td><td>main</td>
<td><a href='mainmenu.php?comidyear=21&seldb=2'><span>ACME2024</span></a></td></tr>
</table>"""


class ParseAccountSets(unittest.TestCase):
    def test_live_paragraph_rows_group_years_under_their_company(self):
        company = ("TEST", "1010-01-000006 บริษัท ทดสอบการใช้ จำกัด")
        self.assertEqual([(r["comidyear"], r["seldb"], r["name"], r["company"], r["company_name"])
                          for r in catalog.parse_account_sets(LIVE)],
                         [("6", "1", "TEST2019", *company), ("15", "1", "TEST2020", *company)])

    def test_rows_group_years_under_their_company(self):
        self.assertEqual(catalog.parse_account_sets(TABLE), [
            {"comidyear": "6", "seldb": "1", "name": "TEST2019", "company": "TEST",
             "company_name": "1010-01-000006 บริษัท ทดสอบการใช้ จำกัด"},
            {"comidyear": "15", "seldb": "1", "name": "TEST2020", "company": "TEST",
             "company_name": "1010-01-000006 บริษัท ทดสอบการใช้ จำกัด"},
            {"comidyear": "21", "seldb": "2", "name": "ACME2024", "company": "ACME",
             "company_name": "Acme & Sons Co., Ltd."},
        ])

    def test_flat_links_keep_the_legacy_reading_without_a_company(self):
        # 旧版单测的原样输入：没有表格行时一个年度都不能漏（公司留空）。
        page = ('<a href="mainmenu.php?comidyear=6&seldb=1">TEST2019</a>\r\n'
                '<a href="mainmenu.php?comidyear=15&seldb=1">TEST2020</a>')
        self.assertEqual([(r["comidyear"], r["seldb"], r["name"], r["company"])
                          for r in catalog.parse_account_sets(page)],
                         [("6", "1", "TEST2019", ""), ("15", "1", "TEST2020", "")])

    def test_duplicate_links_and_empty_pages(self):
        page = ('<a href="mainmenu.php?comidyear=6&amp;seldb=1">TEST2019</a>'
                '<a href="mainmenu.php?comidyear=6&amp;seldb=1">TEST2019</a>')
        self.assertEqual(len(catalog.parse_account_sets(page)), 1)
        self.assertEqual(catalog.parse_account_sets(""), [])
        self.assertEqual(catalog.parse_account_sets(None), [])
        # 登录页（被踢回）没有账套链接。
        self.assertEqual(catalog.parse_account_sets('<input name="txtusers"><input name="txtpasswords">'), [])


class Observation(unittest.TestCase):
    def test_observation_is_a_valid_endpoint_projection_with_company_attributes(self):
        observed = datetime(2026, 9, 26, 3, 0, tzinfo=timezone.utc)
        normalized = normalize_projection(catalog.observation(observed, catalog.parse_account_sets(TABLE)))
        self.assertEqual((normalized.adapter, normalized.scope_kind, normalized.scope_key),
                         ("mrerp", "endpoint", "@endpoint"))
        self.assertEqual(normalized.collector, {"kind": "cloud", "adapter_version": "mrerp-live-v1"})
        rows = {row["source_id"]: row for row in normalized.account_sets}
        self.assertEqual(set(rows), {"6:1", "15:1", "21:2"})
        self.assertEqual(rows["15:1"]["label"], "TEST2020")
        self.assertEqual(rows["15:1"]["attributes"], {
            "comidyear": "15", "seldb": "1", "company": "TEST",
            "company_name": "1010-01-000006 บริษัท ทดสอบการใช้ จำกัด"})
        self.assertEqual(normalized.masters, {entity: [] for entity in normalized.masters})

    def test_same_page_hashes_the_same_regardless_of_observation_time(self):
        rows = catalog.parse_account_sets(TABLE)
        first = normalize_projection(catalog.observation(datetime(2026, 9, 26, tzinfo=timezone.utc), rows))
        later = normalize_projection(catalog.observation(datetime(2026, 9, 27, tzinfo=timezone.utc), rows))
        self.assertEqual(first.source_hash, later.source_hash)

    def test_selection_layer_still_reads_year_and_database_from_the_projection(self):
        # 推送时的选择层（第七批纯合同）只认 comidyear/seldb：公司属性不能改变它的读法。
        probe = {"companies": [{"label": r["name"], "comidyear": r["comidyear"], "seldb": r["seldb"]}
                               for r in catalog.parse_account_sets(TABLE)]}
        keys = [row["key"] for row in account_choices_for_endpoint({"adapter": "mrerp"}, probe)]
        self.assertEqual(keys, ["6:1", "15:1", "21:2"])

    def test_rows_without_year_or_database_are_dropped_and_labels_are_bounded(self):
        rows = catalog.account_sets([{"comidyear": "", "seldb": "1", "name": "X"},
                                     {"comidyear": "7", "seldb": "1", "name": "Y" * 200}])
        self.assertEqual([row["source_id"] for row in rows], ["7:1"])
        self.assertEqual(len(rows[0]["label"]), 80)


if __name__ == "__main__":
    unittest.main()
