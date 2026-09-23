"""Offline regressions for the migrated client-record field rules (batch 44).

Runs in the lab bench (the masters package needs the app). No database, no
Frappe: these are the rules the store applies before writing.
"""

import unittest

from pearnly_cowork.masters import client_records


class CleanText(unittest.TestCase):
    def test_surrounding_space_goes_and_empty_becomes_unset(self):
        self.assertEqual(client_records.clean_text("  ACME  ", "name"), "ACME")
        self.assertIsNone(client_records.clean_text("   ", "name"))
        self.assertIsNone(client_records.clean_text("", "name"))
        self.assertIsNone(client_records.clean_text(None, "name"))

    def test_a_value_is_cut_to_the_deployed_limit(self):
        self.assertEqual(client_records.clean_text("x" * 300, "name"), "x" * 200)
        self.assertEqual(client_records.clean_text("x" * 300, "notes"), "x" * 300)  # under the limit
        self.assertEqual(client_records.clean_text("x" * 1200, "notes"), "x" * 1000)
        self.assertEqual(len(client_records.clean_text("x" * 300, "short_name")), 80)
        self.assertEqual(len(client_records.clean_text("x" * 300, "email")), 300)  # unknown field

    def test_the_value_is_stringified_like_the_deployed_store(self):
        self.assertEqual(client_records.clean_text(12345, "tax_id"), "12345")
        self.assertEqual(client_records.clean_text(0, "tax_id"), "0")


class CreateValues(unittest.TestCase):
    def test_a_client_without_a_name_is_not_created(self):
        self.assertIsNone(client_records.create_values({}))
        self.assertIsNone(client_records.create_values({"name": "   "}))
        self.assertIsNone(client_records.create_values({"short_name": "ACME"}))

    def test_the_fields_are_cleaned_and_the_colour_defaults(self):
        values = client_records.create_values({"name": "  ACME Co., Ltd.  ",
            "short_name": "", "tax_id": " 0-1055-48103-91-2 ", "notes": "  hi  "})
        self.assertEqual(values["name"], "ACME Co., Ltd.")
        self.assertIsNone(values["short_name"])          # empty stays unset, not ""
        self.assertEqual(values["tax_id"], "0-1055-48103-91-2")
        self.assertEqual(values["notes"], "hi")
        self.assertEqual(values["color"], "#3b82f6")     # deployed default
        self.assertEqual(set(client_records.CREATE_FIELDS) | {"color"},
            set(values) | set(client_records.CREATE_FIELDS) - set(values))

    def test_an_explicit_colour_is_kept(self):
        self.assertEqual(client_records.create_values({"name": "A", "color": "#ff0000"})["color"],
            "#ff0000")

    def test_the_create_path_takes_a_truthy_colour_as_given(self):
        # Deployed quirk kept on purpose: only the update path cleans the colour.
        self.assertEqual(client_records.create_values({"name": "A", "color": "  "})["color"], "  ")
        self.assertEqual(client_records.create_values({"name": "A", "color": None})["color"],
            "#3b82f6")
        self.assertEqual(client_records.update_values({"color": "  "}), {"color": None})
        self.assertEqual(client_records.update_values({"color": "x" * 30}),
            {"color": "x" * 20})

    def test_the_buyer_catalogue_fields_are_accepted(self):
        values = client_records.create_values({"name": "A", "party_type": "company",
            "branch": "00000", "promptpay_id": "0899999999"})
        self.assertEqual((values["party_type"], values["branch"], values["promptpay_id"]),
            ("company", "00000", "0899999999"))

    def test_an_unknown_field_is_dropped(self):
        values = client_records.create_values({"name": "A", "workspace": "WS-1",
            "is_active": False, "surprise": "x"})
        self.assertNotIn("workspace", values)
        self.assertNotIn("surprise", values)
        self.assertNotIn("is_active", values)  # create does not accept it, as deployed


class UpdateValues(unittest.TestCase):
    def test_only_the_fields_that_were_given_are_written(self):
        self.assertEqual(client_records.update_values({"name": "B", "notes": "n"}),
            {"name": "B", "notes": "n"})

    def test_none_means_leave_it_alone(self):
        self.assertEqual(client_records.update_values({"name": None, "notes": "n"}),
            {"notes": "n"})
        # The deployed loop skips a None value outright, so a lone None is nothing
        # to write rather than a request to clear the column.
        self.assertIsNone(client_records.update_values({"short_name": None}))

    def test_nothing_to_write_returns_nothing(self):
        self.assertIsNone(client_records.update_values({}))
        self.assertIsNone(client_records.update_values({"name": None}))
        self.assertIsNone(client_records.update_values({"workspace": "WS-1"}))

    def test_an_emptied_text_becomes_unset(self):
        self.assertEqual(client_records.update_values({"notes": "   "}), {"notes": None})

    def test_active_is_a_flag_not_text(self):
        self.assertEqual(client_records.update_values({"is_active": 0}), {"is_active": False})
        self.assertEqual(client_records.update_values({"is_active": True}), {"is_active": True})
        self.assertEqual(client_records.update_values({"is_active": False}), {"is_active": False})

    def test_the_update_whitelist_carries_active_but_not_the_scope(self):
        self.assertIn("is_active", client_records.UPDATE_FIELDS)
        for field in ("workspace", "tenant_id", "user_id", "created_at"):
            self.assertNotIn(field, client_records.UPDATE_FIELDS)


if __name__ == "__main__":
    unittest.main()
