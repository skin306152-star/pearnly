"""Offline regressions for the migrated supplier posting rules (batch 49)."""

import unittest

from pearnly_cowork.masters import supplier_posting


class ValueDomains(unittest.TestCase):
    def test_the_two_domains_and_none(self):
        self.assertEqual(supplier_posting.PAYMENT_VALUES, frozenset({"cash", "credit"}))
        self.assertEqual(supplier_posting.ITEM_TYPE_VALUES, frozenset({"goods", "expense"}))
        for field, domain in (("default_payment", supplier_posting.PAYMENT_VALUES),
                ("default_item_type", supplier_posting.ITEM_TYPE_VALUES)):
            for good in domain:
                self.assertIsNone(supplier_posting.field_error(field, good))
            self.assertIsNone(supplier_posting.field_error(field, None))
            self.assertEqual(supplier_posting.field_error(field, ""),
                supplier_posting.DETAILS[field])
            self.assertEqual(supplier_posting.field_error(field, "cash "),
                supplier_posting.DETAILS[field])
            self.assertIsNone(supplier_posting.field_error(field, "", allow_empty=True))

    def test_the_user_entry_refuses_an_empty_string_and_an_unknown_source(self):
        self.assertIsNone(supplier_posting.input_error("user_rule", {}))
        self.assertIsNone(supplier_posting.input_error("user_rule",
            {"default_payment": "credit", "default_item_type": None}))
        self.assertEqual(supplier_posting.input_error("user_rule", {"default_payment": ""}),
            supplier_posting.ERR_LINE)
        self.assertEqual(supplier_posting.input_error("user_rule", {"default_item_type": "food"}),
            supplier_posting.ERR_LINE)
        self.assertIsNone(supplier_posting.input_error("correction", {"default_payment": ""},
            allow_empty=True))
        self.assertEqual(supplier_posting.input_error("agent", {}), supplier_posting.ERR_SOURCE)


class TaxId(unittest.TestCase):
    def test_the_profile_anchor_is_the_deployed_tax_id(self):
        self.assertEqual(supplier_posting.clean_seller_tax_id("0-1055-48103-91-2"), "0105548103912")
        self.assertEqual(supplier_posting.clean_seller_tax_id("0105561000013"), "0105561000013")
        for bad in ("", None, "13", "010556100001", "01055610000134", "13/06/26"):
            self.assertEqual(supplier_posting.clean_seller_tax_id(bad), "")


class Merge(unittest.TestCase):
    def test_a_first_profile_takes_the_given_values_and_blank_for_the_rest(self):
        self.assertEqual(supplier_posting.merge(None, {"default_payment": "cash"}, "user_rule"),
            {"preserved_user_rule": False, "source": "user_rule",
             "fields": {"default_payment": "cash", "default_item_type": ""}})
        self.assertEqual(
            supplier_posting.merge(None, {"default_payment": None, "default_item_type": None},
                "correction"),
            {"preserved_user_rule": False, "source": "correction",
             "fields": {"default_payment": "", "default_item_type": ""}})

    def test_a_given_field_is_written_and_a_missing_one_is_left_alone(self):
        existing = {"source": "correction", "default_payment": "cash",
            "default_item_type": "goods"}
        decision = supplier_posting.merge(existing, {"default_item_type": "expense"}, "correction")
        self.assertEqual(decision["preserved_user_rule"], False)
        self.assertEqual(decision["source"], "correction")
        self.assertEqual(decision["fields"], {"default_item_type": "expense"})
        self.assertEqual(supplier_posting.merge(existing, {}, "correction")["fields"], {})

    def test_a_user_rule_is_never_overwritten_by_a_correction(self):
        existing = {"source": "user_rule", "default_payment": "credit",
            "default_item_type": "expense"}
        decision = supplier_posting.merge(existing, {"default_payment": "cash"}, "correction")
        self.assertEqual(decision, {"preserved_user_rule": True, "source": "user_rule",
            "fields": {}})

    def test_a_user_rule_may_overwrite_anything(self):
        existing = {"source": "correction", "default_payment": "cash",
            "default_item_type": "goods"}
        decision = supplier_posting.merge(existing, {"default_payment": "credit"}, "user_rule")
        self.assertEqual(decision["preserved_user_rule"], False)
        self.assertEqual(decision["source"], "user_rule")
        self.assertEqual(decision["fields"], {"default_payment": "credit"})


class Effect(unittest.TestCase):
    def test_only_a_non_empty_default_has_an_effect(self):
        self.assertFalse(supplier_posting.has_effect(None))
        self.assertFalse(supplier_posting.has_effect({}))
        self.assertFalse(supplier_posting.has_effect(
            {"default_payment": "", "default_item_type": ""}))
        self.assertFalse(supplier_posting.has_effect(
            {"default_payment": "  ", "default_item_type": None}))
        self.assertTrue(supplier_posting.has_effect(
            {"default_payment": "cash", "default_item_type": ""}))

    def test_clearing_writes_both_fields_empty(self):
        self.assertEqual(supplier_posting.cleared(),
            {"default_payment": "", "default_item_type": ""})
