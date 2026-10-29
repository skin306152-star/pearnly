"""开户自动建公司也要「至少一个仓」（10-29：生产 gize 开户后 0 个仓）。

Run with Frappe installed. No Site, Redis or network: the database calls are replaced.
- 开户建默认公司（`ensure_default`）走「新建公司」表单同一段 `_create_warehouses`、同一个默认仓名；
- 已有仓、勾了「接 Express」或已连上 Express 的公司不建；
- 补丁只给 0 仓、未接 Express 的公司补；所有建公司的路径都要经过建仓（防再犯）。
"""
import pathlib
import re
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]


class DefaultWarehouse(unittest.TestCase):
    def setUp(self):
        from pearnly_erp_portal.masters import company

        self.company = company
        self.warehouses = {}   # company -> [仓]
        self.express = {}      # company -> express_enabled
        self.connected = set()
        db = mock.MagicMock()
        db.exists.side_effect = lambda doctype, filters: bool(self.warehouses.get(filters["company"]))
        db.get_value.side_effect = lambda doctype, name, field: self.express.get(name, 0)
        db.get_single_value.return_value = "th"
        made = []

        def create(company_name, first, extra):
            made.append((company_name, first))
            self.warehouses.setdefault(company_name, []).append("WH-NEW")
            return [{"name": "WH-NEW", "warehouse_name": first}]

        self.made = made
        for patcher in (mock.patch.object(company.frappe, "db", db), mock.patch.object(company, "_create_warehouses", side_effect=create),
                        mock.patch.object(company, "_", side_effect=lambda text, lang=None: f"{text}@{lang}"),
                        mock.patch("pearnly_erp_portal.express.push.connected", side_effect=lambda name: name in self.connected)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_company_without_a_warehouse_gets_the_form_default(self):
        self.assertEqual(self.company.ensure_warehouse("CO-1"), "WH-NEW")
        self.assertEqual(self.made, [("CO-1", "company.warehouseDefaultName@th")])  # 表单同一个默认仓名，按站点语言
        self.assertIsNone(self.company.ensure_warehouse("CO-1"))  # 幂等：有了就不再建

    def test_express_companies_and_companies_with_warehouses_are_left_alone(self):
        self.warehouses["CO-2"] = ["WH-1"]
        self.express["CO-3"] = 1
        self.connected.add("CO-4")
        for name in ("CO-2", "CO-3", "CO-4"):
            self.assertIsNone(self.company.ensure_warehouse(name))
        self.assertEqual(self.made, [])

    def test_every_company_insert_goes_through_a_warehouse(self):
        """建 ERP Company 的每一处，同一个函数里都要建仓（`_create_warehouses` / `ensure_warehouse` / `ensure_for_company`）。"""
        insert = re.compile(r'"doctype": (?:COMPANY|DOCTYPE), "company_name"')
        found = []
        for path in sorted((ROOT / "apps").rglob("*.py")):
            if "/test" in str(path) or "/patches/" in str(path):
                continue
            text = path.read_text(encoding="utf-8")
            for match in insert.finditer(text):
                body = text[match.start():match.start() + 1500]
                found.append(str(path.relative_to(ROOT)))
                self.assertRegex(body, r"_create_warehouses|ensure_warehouse\(|ensure_for_company\(", f"{path} 建公司没建仓")
        self.assertGreaterEqual(len(found), 3, found)  # 新建公司表单、开户默认公司、按 Express 账套建


if __name__ == "__main__":
    unittest.main()
