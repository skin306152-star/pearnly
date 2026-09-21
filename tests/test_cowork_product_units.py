"""Offline regressions for the migrated product name and unit rules (batch 46)."""

import unittest
from decimal import Decimal

from pearnly_cowork.masters import product_names, product_units


class ProductNames(unittest.TestCase):
    def test_the_three_languages_come_in_order(self):
        self.assertEqual(product_names.product_names(
            {"name_th": "น้ำ", "name_en": "Water", "name_zh": "水"}),
            ["น้ำ", "Water", "水"])
        self.assertEqual(product_names.product_names({"name_en": "Water", "name_zh": "水"}),
            ["Water", "水"])

    def test_empty_and_duplicate_names_are_left_out(self):
        self.assertEqual(product_names.product_names(
            {"name_th": "  ", "name_en": "Water", "name_zh": "water"}), ["Water"])
        self.assertEqual(product_names.product_names({}), [])
        self.assertEqual(product_names.product_names({"name_en": None}), [])

    def test_the_display_joins_with_a_slash_and_takes_a_placeholder(self):
        row = {"name_th": "น้ำ", "name_en": "Water"}
        self.assertEqual(product_names.display_product_name(row), "น้ำ / Water")
        self.assertEqual(product_names.display_product_name({}, missing="—"), "—")
        self.assertEqual(product_names.display_product_name({}), "")

    def test_the_name_object_keeps_the_raw_values_and_the_display(self):
        self.assertEqual(product_names.product_name_object({"name_th": " น้ำ ", "name_en": "Water"}),
            {"th": " น้ำ ", "en": "Water", "zh": None, "display": "น้ำ / Water"})


class UnitNumbers(unittest.TestCase):
    def test_a_number_becomes_an_exact_decimal_and_none_stays_none(self):
        self.assertEqual(product_units.coerce_number("0.1"), Decimal("0.1"))
        self.assertEqual(product_units.coerce_number(0.1), Decimal("0.1"))
        self.assertEqual(product_units.coerce_number(3), Decimal("3"))
        self.assertIsNone(product_units.coerce_number(None))

    def test_a_non_number_raises_like_the_deployed_conversion(self):
        from decimal import InvalidOperation
        with self.assertRaises(InvalidOperation):
            product_units.coerce_number("not a number")


class UnitInsert(unittest.TestCase):
    def test_only_the_fields_that_have_a_value_are_written(self):
        values = product_units.unit_insert_values({"unit_name": "กล่อง", "factor_to_base": "12",
            "barcode": None, "price": None, "is_default_sell": False})
        self.assertEqual(values, {"unit_name": "กล่อง", "factor_to_base": Decimal("12"),
            "is_default_sell": False})

    def test_the_numbers_are_decimals(self):
        values = product_units.unit_insert_values({"unit_name": "แผง",
            "factor_to_base": "0.5", "price": "12.50"})
        self.assertEqual((values["factor_to_base"], values["price"]),
            (Decimal("0.5"), Decimal("12.50")))

    def test_nothing_given_writes_nothing(self):
        self.assertEqual(product_units.unit_insert_values({}), {})
        self.assertEqual(product_units.unit_insert_values({"unit_name": None}), {})


class UnitUpdate(unittest.TestCase):
    def test_an_explicit_none_clears_a_nullable_column(self):
        self.assertEqual(product_units.unit_update_values({"barcode": None}), {"barcode": None})
        self.assertEqual(product_units.unit_update_values({"price": None}), {"price": None})

    def test_an_explicit_none_on_a_required_column_is_not_a_write(self):
        self.assertEqual(product_units.unit_update_values({"unit_name": None}), None)
        self.assertEqual(product_units.unit_update_values({"factor_to_base": None}), None)
        self.assertEqual(product_units.unit_update_values({"is_default_sell": None}), None)

    def test_only_the_keys_that_are_present_are_written(self):
        values = product_units.unit_update_values({"unit_name": "โหล", "factor_to_base": "12",
            "barcode": "8850000000001"})
        self.assertEqual(values, {"unit_name": "โหล", "factor_to_base": Decimal("12"),
            "barcode": "8850000000001"})

    def test_nothing_to_write_returns_nothing(self):
        self.assertEqual(product_units.unit_update_values({}), None)
        self.assertEqual(product_units.unit_update_values({"unknown": "x"}), None)

    def test_a_false_default_flag_is_a_write_like_the_deployed_one(self):
        # False is not None, so it is written (turning the flag off).
        self.assertEqual(product_units.unit_update_values({"is_default_sell": False}),
            {"is_default_sell": False})


class SingleDefault(unittest.TestCase):
    def test_a_truthy_default_asks_the_caller_to_clear_the_others(self):
        self.assertTrue(product_units.clears_other_defaults({"is_default_sell": True}))
        self.assertTrue(product_units.clears_other_defaults({"is_default_sell": 1}))
        self.assertFalse(product_units.clears_other_defaults({"is_default_sell": False}))
        self.assertFalse(product_units.clears_other_defaults({"unit_name": "กล่อง"}))
        self.assertFalse(product_units.clears_other_defaults({}))


if __name__ == "__main__":
    unittest.main()
