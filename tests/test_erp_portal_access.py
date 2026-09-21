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
        for capability, feature in rules.CAPABILITY_FEATURES.items():
            with self.subTest(capability=capability):
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


if __name__ == "__main__":
    unittest.main()
