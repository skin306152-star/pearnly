"""Pure input boundaries; integration lives in the isolated Site script."""
import unittest
from copy import deepcopy
from pearnly_cowork.connections import model


class ConnectionModelTests(unittest.TestCase):
    def test_real_source_posting_profile_is_a_dictionary(self):
        config = {"posting_profile": {"inventory_usage": "perpetual", "posting_mode": "stock"}}
        self.assertEqual(model.configuration("express", config), config)

    def test_source_autonomy_levels_and_direction_formats(self):
        for level in ("manual", "standard", "auto"):
            self.assertEqual(model.configuration("express", {"autonomy": level})["autonomy"], level)
        for value in ([], "", ["purchase", "sales"], "purchase,sales"):
            self.assertEqual(model.configuration("express", {"directions": value})["directions"], value)

    def test_client_config_cannot_supply_collector_proofs_credentials_or_generation(self):
        for field in ("password", "password_enc", "token", "reported_accounts", "max_payload_version", "binding_generation"):
            with self.subTest(field=field), self.assertRaises(model.ConnectionError):
                model.configuration("express", {field: "synthetic"})

    def test_config_snapshot_detaches_input_and_default_mrerp_url(self):
        self.assertEqual(model.configuration("mrerp", {})["system_url"], "https://www.mrerp4sme.com")
        value={"directions": ["purchase"]}; result=model.configuration("express", value)
        result["directions"].append("sales")
        self.assertEqual(value["directions"], ["purchase"])

    def test_literal_private_metadata_and_credentials_in_urls_are_rejected(self):
        for url in ("http://127.0.0.1", "http://169.254.169.254", "http://metadata.google.internal",
                    "http://user:secret@example.com", "file:///tmp/a", "https://foo.local"):
            with self.subTest(url=url), self.assertRaises(model.ConnectionError): model.validate_url(url)
        self.assertEqual(model.validate_url("http://www.mrerp4sme.com"), "http://www.mrerp4sme.com")

    def test_stable_request_identity_is_site_actor_and_purpose_bound(self):
        original = model.request_identity("site", "actor", "purpose", "key")
        self.assertEqual(original, model.request_identity("site", "actor", "purpose", "key"))
        for args in (("site2","actor","purpose","key"),("site","actor2","purpose","key"),("site","actor","other","key")):
            self.assertNotEqual(original, model.request_identity(*args))

    def test_missing_preparation_is_not_empty_complete_collection(self):
        self.assertIsNone(model.preparation(None))
        with self.assertRaises(model.ConnectionError): model.preparation({})
        self.assertFalse(model.preparation(self.prepared(False))["complete"])

    def test_complete_empty_source_context_is_explicit_and_bounded(self):
        value=self.prepared(True)
        self.assertEqual(model.preparation(value), value)
        value["mappings"]={}
        with self.assertRaises(model.ConnectionError): model.preparation(value)

    def test_reported_config_cannot_override_target_or_account_defaults(self):
        for key in ("account_set", "account_dir", "express_root", "reported_account_sets", "ar_acc", "password"):
            value=self.prepared(True); value["reported_config"][key]="synthetic"
            with self.subTest(key=key), self.assertRaises(model.ConnectionError): model.preparation(value)

    @staticmethod
    def prepared(complete=True):
        return {"complete":complete,"account_set":r"C:\BOOK","root_key":r"D:\APP",
                "mappings":{"accounts":[],"clients":[]},"supplier_profiles":{},"bank_index":[],"reported_config":{}}


if __name__ == '__main__': unittest.main()
