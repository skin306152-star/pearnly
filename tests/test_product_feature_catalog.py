"""The sellable feature catalog is declared by each product and only aggregated.

This replaces what used to be a single review of one control-plane file: the
contract (which codes exist, which role and doctypes each one grants, which tables
a post reads but cannot write) is now pinned per product, so a product can change
its own catalog without editing a shared file and without silently changing
another product's enforcement.
"""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
APPS = {
    "cowork": "pearnly_cowork",
    "erp": "pearnly_erp_portal",
    "dms": "pearnly_dms",
}
DECLARATIONS = {product: json.loads((ROOT / f"apps/{app}/{app}/features.json").read_text())
                for product, app in APPS.items()}
HOME = "home_subscription"


class CatalogShape(unittest.TestCase):
    def test_the_catalog_is_one_file_per_product_beside_the_product(self):
        for product, app in APPS.items():
            with self.subTest(product=product):
                declaration = DECLARATIONS[product]
                self.assertEqual(declaration["product"], product)
                for key in ("label", "icon", "module", "admin_role", "features"):
                    self.assertTrue(declaration.get(key), key)
                self.assertTrue((ROOT / f"apps/{app}/{app}/product_manifest.py").is_file())

    def test_the_catalog_is_in_a_plain_data_format(self):
        # The control site does not install the product apps, so it reads the file
        # rather than importing product code; a Python module would not work there.
        for product, app in APPS.items():
            with self.subTest(product=product):
                self.assertTrue((ROOT / f"apps/{app}/{app}/features.json").is_file())

    def test_codes_are_unique_and_labelled(self):
        codes = {}
        for product, declaration in DECLARATIONS.items():
            seen = set()
            for item in declaration["features"]:
                with self.subTest(product=product, code=item["code"]):
                    self.assertNotIn(item["code"], seen)
                    self.assertNotEqual(item["code"], HOME)
                    seen.add(item["code"])
                    codes[(product, item["code"])] = item
                    for key in ("code", "label", "description"):
                        self.assertTrue(str(item.get(key) or "").strip(), key)
                    self.assertIn("default_on", item)
        self.assertEqual(sum(1 for _ in codes), len(codes))


class CatalogContract(unittest.TestCase):
    """The numbers the old single-file catalog pinned, asserted per product."""

    def test_feature_counts_are_unchanged(self):
        self.assertEqual(
            {product: len(d["features"]) + 1 for product, d in DECLARATIONS.items()},
            {"cowork": 10, "erp": 10, "dms": 6},
        )

    def test_cowork_codes_are_the_declared_set(self):
        self.assertEqual([item["code"] for item in DECLARATIONS["cowork"]["features"]],
            ["intake", "ocr_history", "push_log", "recon", "tax_drafts", "master_data", "stocktake",
             "companion", "express_guide"])

    def test_the_features_that_start_switched_off_are_the_declared_ones(self):
        off = {(product, item["code"]) for product, d in DECLARATIONS.items()
               for item in d["features"] if not item["default_on"]}
        self.assertEqual(off, {("cowork", "stocktake"), ("cowork", "companion"),
                              ("cowork", "express_guide"), ("erp", "erp_push")})

    def test_enforcement_is_declared_with_the_role_and_its_tables(self):
        enforcement = {(product, item["code"]): (item["role"], item["doctypes"])
                       for product, d in DECLARATIONS.items() for item in d["features"]
                       if item["role"]}
        self.assertEqual(enforcement[("cowork", "intake")], ("Cowork Intake", ["Intake Record"]))
        # 报税草稿（2026-10-13）：一个角色管 ภ.พ.30 与 ภ.ง.ด.3 / 53 草稿
        self.assertEqual(enforcement[("cowork", "tax_drafts")], ("Cowork Tax Filing", ["Cowork Tax Draft"]))
        self.assertEqual(enforcement[("dms", "roster")], ("DMS Operator", ["DMS Operator"]))
        self.assertEqual(enforcement[("erp", "clients")],
            ("ERP Sales", ["ERP Customer", "ERP Company"]))
        self.assertEqual(enforcement[("erp", "cashier")][0], "ERP Cashier")
        self.assertEqual(len(enforcement), 13)

    def test_a_feature_without_a_role_does_not_pretend_to_have_one(self):
        for product, declaration in DECLARATIONS.items():
            for item in declaration["features"]:
                with self.subTest(product=product, code=item["code"]):
                    if not item["role"]:
                        self.assertEqual(item["doctypes"], [])
                        self.assertEqual(item["read_only"], [])

    def test_read_only_tables_belong_to_the_same_role_and_are_granted(self):
        for product, declaration in DECLARATIONS.items():
            for item in declaration["features"]:
                for doctype in item["read_only"]:
                    with self.subTest(product=product, code=item["code"], doctype=doctype):
                        self.assertIn(doctype, item["doctypes"])

    def test_the_company_record_stays_admin_owned(self):
        erp = {item["code"]: item for item in DECLARATIONS["erp"]["features"]}
        business_roles = [erp[code]["role"] for code in
                          ("products", "warehouses", "stockcard", "purchase", "sales", "cashier")]
        self.assertEqual(sorted(set(business_roles)),
            ["ERP Cashier", "ERP Products", "ERP Purchase", "ERP Sales", "ERP Stock"])
        for code in ("products", "warehouses", "stockcard", "purchase", "sales", "cashier"):
            self.assertIn("ERP Company", erp[code]["read_only"], code)
        self.assertEqual(DECLARATIONS["erp"]["admin_extra_doctypes"], ["ERP Company"])
        # Only the sales post may write the customer master.
        self.assertNotIn("ERP Customer", erp["clients"]["read_only"])
        self.assertIn("ERP Customer", erp["cashier"]["read_only"])

    def test_the_web_intake_entry_stays_read_only(self):
        dms = {item["code"]: item for item in DECLARATIONS["dms"]["features"]}
        self.assertEqual(dms["id_card_ocr"]["read_only"], ["DMS ID Card Intake"])
        self.assertEqual(DECLARATIONS["dms"]["workspace_labels"],
            {"DMS ID Card Intake": "Push Records"})

    def test_every_workspace_label_points_at_a_granted_table(self):
        for product, declaration in DECLARATIONS.items():
            granted = {doctype for item in declaration["features"] for doctype in item["doctypes"]}
            for doctype in declaration["workspace_labels"]:
                with self.subTest(product=product, doctype=doctype):
                    self.assertIn(doctype, granted)

    def test_admin_roles_are_distinct_per_product(self):
        roles = [d["admin_role"] for d in DECLARATIONS.values()]
        self.assertEqual(len(set(roles)), len(roles))
        self.assertEqual(set(roles), {"Cowork Admin", "ERP Admin", "DMS Admin"})


class AggregatedPermissions(unittest.TestCase):
    """Exercise the shared reader without importing any installed product App."""

    def setUp(self):
        import types
        import sys
        from unittest.mock import patch
        frappe = types.ModuleType('frappe')
        utils = types.ModuleType('frappe.utils')
        utils.get_bench_path = lambda: str(ROOT)
        with patch.dict(sys.modules, {'frappe': frappe, 'frappe.utils': utils}):
            self.catalog = {}
            path = ROOT / 'apps/pearnly_control/pearnly_control/features.py'
            exec(compile(path.read_text(), str(path), 'exec'), self.catalog)

    def test_cost_access_follows_declared_roles(self):
        roles = self.catalog['roles_payload']('erp', ['warehouses', 'stocktake'])
        grants = {(r['doctype'], r['role']): r for r in roles['field_permissions']}
        self.assertEqual(set(grants), {('ERP Product', 'ERP Admin'),
            ('ERP Stock Count Item', 'ERP Admin'), ('ERP Stock Count Item', 'ERP Stock')})
        # Employee product costs are projected through live menu/feature checks.
        # A static field grant would survive revocation; access.setup removes it.
        self.assertNotIn(('ERP Product', 'ERP Stock'), grants)
        self.assertEqual(grants['ERP Product', 'ERP Admin']['perms'], {'read': 1, 'write': 1})
        # Frozen valuation is readable but never editable through native forms.
        for role in ['ERP Admin', 'ERP Stock']:
            self.assertEqual(grants['ERP Stock Count Item', role]['perms'], {'read': 1, 'write': 0})
        self.assertTrue(all(grant['permlevel'] == 1 for grant in grants.values()))
        for code in ['products', 'purchase', 'sales', 'cashier']:
            payload = self.catalog['roles_payload']('erp', [code])
            self.assertEqual({(g['doctype'], g['role']) for g in payload['field_permissions']},
                {('ERP Product', 'ERP Admin'), ('ERP Stock Count Item', 'ERP Admin')})
        for product in ['cowork', 'dms']:
            self.assertEqual(self.catalog['roles_payload'](product, self.catalog['codes_of'](product))['field_permissions'], [])

    def test_inventory_and_count_share_one_role_without_losing_product_read_only(self):
        payload = self.catalog['roles_payload']('erp', ['warehouses', 'stockcard', 'stocktake'])
        stock, = payload['roles']
        self.assertEqual(stock['role'], 'ERP Stock')
        self.assertTrue({'ERP Stock Count', 'ERP Stock Count Item', 'ERP Stock Count Entry',
                         'ERP Stock Opening', 'ERP Product'} <= set(stock['doctypes']))
        self.assertEqual(stock['perms_by_doctype']['ERP Product'], {'read': 1, 'report': 1})

    def test_missing_product_catalog_fails_closed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RuntimeError):
                self.catalog['read_catalog']('erp', directory)


if __name__ == "__main__":
    unittest.main()
