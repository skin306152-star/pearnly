"""大数据量列表按页取（10-25：testerp 7.4 万客户，客户页一次取全卡死）：客户、供应商、公司、收银班次。

Run with Frappe installed. No Site, Redis or network: frappe.get_list is replaced.
"""
import unittest
from unittest import mock


class PagedLists(unittest.TestCase):
    def capture(self, module):
        calls = []

        def get_list(doctype, **kwargs):
            calls.append((doctype, kwargs))
            if {"COUNT": "name", "as": "total"} in (kwargs.get("fields") or []):
                return [{"total": 3}]  # 计数在数据库里数（list_filters.count 的 COUNT 聚合）
            return ["N1", "N2", "N3"] if kwargs.get("pluck") else [{"name": "N1"}]

        patches = [mock.patch.object(module.frappe, "get_list", side_effect=get_list), mock.patch.object(module.guard, "require")]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        return calls

    def test_customers_page_search_sort_and_count(self):
        from pearnly_erp_portal.masters import customers

        calls = self.capture(customers)
        customers.list_customers(q="0105", include_inactive=1, start=50, limit=20, sort_by="tax_id", sort_order="desc")
        _, kwargs = calls[-1]
        self.assertEqual((kwargs["limit_start"], kwargs["limit_page_length"], kwargs["order_by"]), (50, 20, "tax_id desc"))
        self.assertEqual([f[0] for f in kwargs["or_filters"]], list(customers.SEARCH_FIELDS))  # 名称 / 简称 / 税号 / 编码 / 电话任一
        self.assertEqual(kwargs["filters"], [])
        customers.list_customers(sort_by="customer_name; drop table", sort_order="asc")
        self.assertEqual(calls[-1][1]["order_by"], "customer_name asc")  # 不在白名单：回默认顺序
        self.assertEqual(calls[-1][1]["filters"], [["is_active", "=", 1]])
        self.assertIsNone(calls[-1][1]["or_filters"])
        self.assertEqual(customers.count_customers(q="0105", include_inactive=1), 3)
        self.assertEqual(calls[-1][1]["or_filters"], calls[0][1]["or_filters"])  # 计数与列表同一份筛选

    def test_suppliers_page_search_sort_and_count(self):
        from pearnly_erp_portal.purchasing import suppliers

        calls = self.capture(suppliers)
        suppliers.list_suppliers(q="ก-002", start=0, limit=20)
        self.assertEqual([f[0] for f in calls[-1][1]["or_filters"]], list(suppliers.SEARCH_FIELDS))
        self.assertEqual(calls[-1][1]["limit_page_length"], 20)
        self.assertEqual(suppliers.count_suppliers(q="ก-002"), 3)

    def test_companies_and_shifts_are_paged(self):
        from pearnly_erp_portal.cashier import shifts
        from pearnly_erp_portal.masters import company

        calls = self.capture(company)
        company.list_companies(include_inactive=1, start=20, limit=20, sort_by="tax_id")
        self.assertEqual((calls[-1][1]["limit_start"], calls[-1][1]["order_by"]), (20, "tax_id asc"))
        company.list_companies()
        self.assertEqual(calls[-1][1]["order_by"], "is_default desc, company_name asc")
        self.assertEqual(company.count_companies(include_inactive=1), 3)

        # 从连接添加后（主会话 11-15：定位到新公司）：刚加的几家排最前，其余照排序接着分页、不重复
        rows = {"A": "ก", "B": "ข", "C": "ค", "D": "ง", "E": "จ"}

        def fake(doctype, filters=None, order_by=None, limit_start=0, limit_page_length=20, **kwargs):
            names = sorted(rows, key=rows.get)
            wanted = (filters or {}).get("name")
            if wanted and wanted[0] == "in":
                names = [name for name in names if name in wanted[1]]
            elif wanted and wanted[0] == "not in":
                names = [name for name in names if name not in wanted[1]]
            return [{"name": name} for name in names[limit_start:limit_start + limit_page_length]]

        with mock.patch.object(company.frappe, "get_list", side_effect=fake):
            first_page = [row["name"] for row in company.list_companies(limit=3, first="D,B")]
            second_page = [row["name"] for row in company.list_companies(start=3, limit=3, first="D,B")]
            plain = [row["name"] for row in company.list_companies(limit=3)]
        self.assertEqual(first_page, ["B", "D", "A"])
        self.assertEqual(second_page, ["C", "E"])
        self.assertEqual(plain, ["A", "B", "C"])

        calls = self.capture(shifts)
        with mock.patch.object(shifts.terminals, "scope_filter", return_value={"terminal": ["in", ["T1"]]}), \
                mock.patch.object(shifts.rules, "record_scope", return_value="own"), \
                mock.patch.object(shifts.guard, "current_roles", return_value=[]), \
                mock.patch.object(shifts.frappe, "session", mock.Mock(user="cashier@x")):
            shifts.list_shifts(start=40, limit=20, sort_by="cash_diff", sort_order="desc")
            self.assertEqual(calls[-1][1]["filters"], {"terminal": ["in", ["T1"]], "cashier": "cashier@x"})  # 非管理员只看自己的班
            self.assertEqual((calls[-1][1]["limit_start"], calls[-1][1]["order_by"]), (40, "cash_diff desc"))
            self.assertEqual(shifts.count_shifts(), 3)
            self.assertEqual(calls[-1][1]["filters"], calls[-2][1]["filters"])


if __name__ == "__main__":
    unittest.main()
