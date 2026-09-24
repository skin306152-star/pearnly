"""Zero-IO tests for the native company catalogue field allowlist.

The checked-in schema, not a fake parser or sample business values, is tested.
No Frappe, credentials, Site, HTTP or external DMS is needed.
"""
import ast
from pathlib import Path
import runpy
import unittest


SCHEMA = Path(__file__).resolve().parents[1] / (
    "apps/pearnly_dms/pearnly_dms/client/company_catalogue_schema.py"
)
VALUES = runpy.run_path(str(SCHEMA))
REGISTRY = VALUES["REGISTRY"]


class CompanyCatalogueSchemaTests(unittest.TestCase):
    def test_exact_group_membership_and_isolation(self):
        groups = {
            "COMPANY_TABLES": {"company", "branch", "team", "stores", "orders", "works", "users", "bank", "finance"},
            "COMPANY_SINGLETONS": {"businessowner", "setcbc"},
            "CUSTOMER_AUX_TABLES": {"prefix", "branchcus", "cusbank"},
            "GEO_TABLES": {"provinces", "districts", "subdistricts", "zipcodes"},
        }
        seen = set()
        for name, expected in groups.items():
            with self.subTest(group=name):
                self.assertEqual(set(VALUES[name]), expected)
                self.assertFalse(seen & expected)
                seen |= expected
                for kind, fields in VALUES[name].items():
                    self.assertIs(REGISTRY[kind]["fields"], fields)
        self.assertEqual(set(REGISTRY), seen)
        self.assertNotIn("cus", REGISTRY)  # A buyer is not the dealer company.

    def test_schema_shapes_are_frozen_complete_and_disjoint(self):
        required = {"dataset", "fields", "selects", "textareas", "arrays", "radios", "excluded", "singleton"}
        for kind, spec in REGISTRY.items():
            with self.subTest(kind=kind):
                self.assertTrue(required <= spec.keys())
                self.assertTrue(spec["fields"])
                for fieldset in ("fields", "selects", "textareas", "arrays", "radios", "excluded"):
                    self.assertIsInstance(spec[fieldset], frozenset)
                    self.assertTrue(all(isinstance(name, str) and name.strip() == name and name for name in spec[fieldset]))
                self.assertFalse(spec["fields"] & spec["excluded"])
                typed = set()
                for fieldset in ("selects", "textareas", "arrays", "radios", "checkboxes"):
                    fields = spec.get(fieldset, frozenset())
                    self.assertIsInstance(fields, frozenset)
                    self.assertTrue(fields <= spec["fields"])
                    self.assertFalse(typed & fields)
                    typed |= fields
                self.assertEqual(spec["singleton"], kind in {"businessowner", "setcbc"})
                self.assertEqual(spec["selects"], frozenset(name for name in spec["fields"] if name.startswith("sel")))
                self.assertFalse(spec["textareas"])

    def test_company_identity_address_contact_and_bank_fields_are_not_reduced_to_labels(self):
        address = {
            "txthousenum", "txtbuilding", "txtfloor", "txtroom", "txtvillage", "txtmoo",
            "txtsoi", "txtroad", "selprovinces", "seldistricts", "selsubdistricts", "selzipcodes",
        }
        contact = {"txttel", "txtemail", "txtlineid", "txtfacebook", "txttaxid"}
        for kind in ("company", "branch", "users", "finance", "businessowner"):
            with self.subTest(kind=kind):
                self.assertTrue(address | contact <= REGISTRY[kind]["fields"])
        self.assertTrue({"txtcompanycode", "txtcompanyname"} <= REGISTRY["company"]["fields"])
        self.assertTrue({"selcompany", "txtbranchautonum"} <= REGISTRY["branch"]["fields"])
        self.assertTrue({"txtbankcode", "txtbankname", "txtbranchname", "txtaccountnum", "selstatuscancel"} <= REGISTRY["bank"]["fields"])
        self.assertTrue(address | (contact - {"txttaxid"}) <= REGISTRY["bank"]["fields"])
        self.assertNotIn("txttaxid", REGISTRY["bank"]["fields"])
        self.assertIn("txtcontactother", REGISTRY["finance"]["fields"])
        self.assertEqual(REGISTRY["works"]["fields"], frozenset({"txtworkscode", "txtworksname", "txtstartdate", "txtenddate", "ordersval", "txtorders"}))

    def test_users_are_explicit_business_projection_without_credentials_or_form_context(self):
        spec = REGISTRY["users"]
        expected_excluded = {"txtpasswords", "idsu", "iduo", "stsel", "idsel", "btnufimg", "fimg", "selstatususersgrant"}
        self.assertEqual(spec["excluded"], frozenset(expected_excluded))
        self.assertFalse(spec["fields"] & expected_excluded)
        self.assertTrue({"txtuserscode", "txtusers", "txtusersname", "selposition", "selbranch", "txttelwork", "txttelhome", "txtufimg"} <= spec["fields"])
        self.assertEqual(spec["arrays"], frozenset({
            "chkteamposi2[]", "chkuserheadposi2[]", "chkteamposi3[]", "chkuserheadposi3[]", "chkteamposi4[]",
            "chkuserheadposi4[]", "chkteamposi5[]", "chkuserheadposi5[]",
        }))
        self.assertTrue(all(name.endswith("[]") for name in spec["arrays"]))
        self.assertEqual(spec["radios"], frozenset({"chkteamrdo"}))
        # These checked native business controls describe hierarchy, not an
        # open-ended prefix allowlist for menu/role/permission checkboxes.
        self.assertEqual({name for name in spec["fields"] if name.startswith("chk")}, spec["arrays"] | spec["radios"])

    def test_checked_employee_form_variants_keep_status_optional_without_caching_grants(self):
        spec = REGISTRY["users"]
        self.assertEqual(spec["optional"], frozenset({"selstatususers"}))
        self.assertTrue(spec["optional"] <= spec["fields"])
        self.assertTrue(spec["optional"] <= spec["selects"])
        self.assertIn("chkuserheadposi2[]", spec["arrays"])
        self.assertIn("selstatususersgrant", spec["excluded"])
        self.assertNotIn("selstatususersgrant", spec["fields"])
        for kind, entry in REGISTRY.items():
            if kind != "users":
                self.assertFalse(entry.get("optional", frozenset()))

    def test_singletons_preserve_business_values_but_not_upload_or_context_controls(self):
        owner = REGISTRY["businessowner"]
        self.assertTrue({"txtbusinessownername", "txtufimg"} <= owner["fields"])
        self.assertEqual(owner["excluded"], frozenset({"status", "btnufimg", "fimg"}))
        self.assertEqual(REGISTRY["setcbc"]["fields"], frozenset({"cbhidefreebiesprice"}))
        self.assertEqual(REGISTRY["setcbc"]["checkboxes"], frozenset({"cbhidefreebiesprice"}))
        self.assertEqual(REGISTRY["setcbc"]["excluded"], frozenset({"status"}))
        for kind, spec in REGISTRY.items():
            with self.subTest(kind=kind):
                self.assertFalse({"status", "stsel", "idsel", "txtpasswords", "idsu", "iduo", "btnufimg", "fimg"} & spec["fields"])
                if not spec["singleton"]:
                    self.assertTrue({"stsel", "idsel"} <= spec["excluded"])

    def test_customer_aux_and_geo_do_not_share_company_namespace(self):
        for kind in VALUES["CUSTOMER_AUX_TABLES"]:
            self.assertEqual(REGISTRY[kind]["dataset"], "customer_aux")
        for kind in VALUES["GEO_TABLES"]:
            self.assertEqual(REGISTRY[kind]["dataset"], "geo_aux")
        for kind in VALUES["COMPANY_TABLES"] | VALUES["COMPANY_SINGLETONS"]:
            self.assertEqual(REGISTRY[kind]["dataset"], "company")
        self.assertEqual(REGISTRY["prefix"]["fields"], frozenset({"txtprefixname"}))
        self.assertEqual(REGISTRY["branchcus"]["fields"], frozenset({"txtbranchcuscode", "txtbranchcusname"}))
        self.assertEqual(REGISTRY["cusbank"]["fields"], frozenset({"txtcusbankcode", "txtcusbankname", "selstatuscancel"}))
        for kind in ("provinces", "districts", "subdistricts"):
            self.assertIn(f"txt{kind}engname", REGISTRY[kind]["fields"])
        self.assertEqual(REGISTRY["districts"]["selects"], frozenset({"selprovinces"}))
        self.assertEqual(REGISTRY["subdistricts"]["selects"], frozenset({"selprovinces", "seldistricts"}))
        self.assertEqual(REGISTRY["zipcodes"]["fields"], frozenset({"selprovinces", "seldistricts", "selsubdistricts", "txtzipcodesname"}))

    def test_schema_is_pure_constants_without_runtime_or_io_dependencies(self):
        tree = ast.parse(SCHEMA.read_text())
        forbidden = (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.With, ast.AsyncWith, ast.Await)
        self.assertFalse(any(isinstance(node, forbidden) for node in ast.walk(tree)))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                allowed = isinstance(node.func, ast.Name) and node.func.id == "frozenset"
                allowed |= isinstance(node.func, ast.Attribute) and node.func.attr == "items" and isinstance(node.func.value, ast.Name) and node.func.value.id in {
                    "COMPANY_TABLES", "COMPANY_SINGLETONS", "CUSTOMER_AUX_TABLES", "GEO_TABLES",
                }
                self.assertTrue(allowed, ast.unparse(node))


if __name__ == "__main__":
    unittest.main()
