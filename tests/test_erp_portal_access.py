"""Offline checks for the ERP portal access rules.

No Frappe, no Site, no network: `access.rules` must stay pure. The role names are
compared against the control-plane catalogue that actually creates them, so a
rename on either side fails here instead of silently granting nothing.
"""
import ast
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_erp_portal"))

from pearnly_erp_portal.access import rules  # noqa: E402
from pearnly_erp_portal.products import contracts  # noqa: E402

FEATURES = ROOT / "apps/pearnly_control/pearnly_control/features.py"


def _literal(name):
    """Pull one module-level literal assignment out of the control-plane module."""
    tree = ast.parse(FEATURES.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {FEATURES}")


class RoleParity(unittest.TestCase):
    def test_admin_role_matches_control_plane(self):
        self.assertEqual(rules.ADMIN_ROLE, _literal("PRODUCT_ADMIN_ROLE")["erp"])

    def test_base_role_is_granted_by_control_plane(self):
        self.assertIn(rules.BASE_ROLE, _literal("BASE_ROLES"))

    def test_feature_roles_match_control_plane(self):
        enforcement = _literal("ENFORCEMENT")
        expected = {
            code: role for (product, code), (role, _doctypes) in enforcement.items()
            if product == "erp"
        }
        self.assertTrue(expected, "control plane must enforce at least one ERP feature")
        for code, role in expected.items():
            with self.subTest(code=code):
                self.assertEqual(rules.FEATURE_ROLES.get(code), role)

    def test_every_capability_points_at_a_real_feature(self):
        for capability, features in rules.CAPABILITY_FEATURES.items():
            with self.subTest(capability=capability):
                self.assertTrue(features, "a capability must name at least one feature")
                for feature in features:
                    self.assertIn(feature, rules.FEATURE_ROLES)


class Capabilities(unittest.TestCase):
    def test_purchase_requires_the_feature_role(self):
        self.assertEqual(rules.required_roles("purchase.read"), ("ERP Purchase",))
        self.assertTrue(rules.can({"ERP Purchase"}, "purchase.read"))
        self.assertTrue(rules.can({"ERP Purchase"}, "purchase.write"))
        self.assertFalse(rules.can({"Desk User"}, "purchase.read"))

    def test_unknown_capability_is_denied(self):
        self.assertEqual(rules.required_roles("purchase.delete"), ())
        self.assertFalse(rules.can({"ERP Admin", "ERP Purchase"}, "purchase.delete"))

    def test_admin_role_alone_grants_no_business_capability(self):
        self.assertFalse(rules.can({rules.ADMIN_ROLE}, "purchase.read"))


class RecordScope(unittest.TestCase):
    def test_admins_see_all_records(self):
        self.assertEqual(rules.record_scope({rules.ADMIN_ROLE}), rules.ALL_RECORDS)
        self.assertEqual(rules.record_scope({rules.SYSTEM_ADMIN_ROLE}), rules.ALL_RECORDS)

    def test_employee_role_sees_own_records_only(self):
        self.assertEqual(rules.record_scope({"ERP Purchase"}), rules.OWN_RECORDS)
        self.assertEqual(rules.record_scope(set()), rules.OWN_RECORDS)


class TeamRoles(unittest.TestCase):
    def test_team_management_is_admin_only(self):
        self.assertEqual(rules.required_roles("team.manage"), (rules.ADMIN_ROLE,))
        self.assertTrue(rules.can({rules.ADMIN_ROLE}, "team.manage"))
        self.assertFalse(rules.can({"ERP Purchase"}, "team.manage"))

    def test_only_roles_present_on_the_site_are_assignable(self):
        present = ["ERP Purchase", "Desk User", "System Manager"]
        self.assertEqual(rules.assignable_roles(present), ("ERP Purchase",))
        self.assertTrue(rules.is_assignable("ERP Purchase", present))
        # 平台角色与产品管理员角色永不外发
        self.assertFalse(rules.is_assignable("System Manager", present))
        self.assertFalse(rules.is_assignable(rules.ADMIN_ROLE, [rules.ADMIN_ROLE]))

    def test_nothing_is_assignable_without_purchased_roles(self):
        self.assertEqual(rules.assignable_roles(["Desk User"]), ())


class ProductContracts(unittest.TestCase):
    def test_blank_price_stays_blank(self):
        # 旧线上强调过：0 当默认值会让"没设价"与"真的 ฿0"在库里变成同一个值。
        self.assertIsNone(contracts.decimal_or_none(None))
        self.assertIsNone(contracts.decimal_or_none(""))
        self.assertEqual(str(contracts.decimal_or_none("0")), "0")

    def test_negative_or_junk_price_is_rejected(self):
        for value in ("-1", "abc", "nan", "inf"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                contracts.decimal_or_none(value)

    def test_unit_factor_must_be_positive(self):
        self.assertEqual(str(contracts.unit_factor(12)), "12")
        for value in (0, -3, None, "x"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                contracts.unit_factor(value)

    def test_name_is_required_and_bounded(self):
        self.assertEqual(contracts.require_name("  สินค้า  "), "สินค้า")
        with self.assertRaises(ValueError):
            contracts.require_name("   ")
        with self.assertRaises(ValueError):
            contracts.require_name("x" * (contracts.MAX_NAME + 1))

    def test_costs_are_masked_without_the_role(self):
        row = {"name": "PRD-1", "default_cost": "12", "avg_cost": "13"}
        masked = contracts.mask_costs(dict(row), False)
        self.assertIsNone(masked["default_cost"])
        self.assertIsNone(masked["avg_cost"])
        self.assertEqual(masked["name"], "PRD-1")
        self.assertEqual(contracts.mask_costs(dict(row), True), row)

    def test_stock_role_sees_cost_but_a_shop_role_does_not(self):
        self.assertTrue(rules.cost_visible({rules.FEATURE_ROLES["warehouses"]}))
        self.assertTrue(rules.cost_visible({rules.ADMIN_ROLE}))
        self.assertFalse(rules.cost_visible({rules.FEATURE_ROLES["products"]}))
        self.assertFalse(rules.cost_visible(set()))

    def test_product_capabilities_follow_the_products_feature(self):
        role = rules.FEATURE_ROLES["products"]
        self.assertTrue(rules.can({role}, "products.view"))
        self.assertTrue(rules.can({role}, "products.manage"))
        self.assertFalse(rules.can({rules.FEATURE_ROLES["cashier"]}, "products.manage"))

    def test_stock_role_may_view_products_but_not_manage_them(self):
        # 库存岗位做入库/出库要先看得到商品，但商品主数据仍归商品岗位。
        stock = rules.FEATURE_ROLES["warehouses"]
        self.assertTrue(rules.can({stock}, "products.view"))
        self.assertFalse(rules.can({stock}, "products.manage"))


if __name__ == "__main__":
    unittest.main()
