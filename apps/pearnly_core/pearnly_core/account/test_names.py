"""姓名（`account.names`，用户 10-30）：名必填、姓可空；显示用框架拼好的全名，没有回落账号。

Run with Frappe installed. No Site: the framework calls are replaced.
"""
import unittest
from unittest import mock

from pearnly_core.account import names


class Names(unittest.TestCase):
    def test_first_name_is_required_and_both_are_trimmed(self):
        # No Site, no translation cache: `_` is replaced so only the guard is under test.
        with mock.patch.object(names.frappe, "throw", side_effect=ValueError) as throw, \
                mock.patch.object(names, "_", lambda text: text):
            with self.assertRaises(ValueError):
                names.clean("  ", "Jaidee")
            throw.assert_called_once()
        self.assertEqual(names.clean(" Somchai ", " Jaidee "), {"first_name": "Somchai", "last_name": "Jaidee"})
        self.assertEqual(names.clean("Somchai", None), {"first_name": "Somchai", "last_name": ""})

    def test_display_name_uses_the_full_name_and_falls_back_to_the_account(self):
        with mock.patch.object(names.frappe.utils, "get_fullname", return_value="Somchai Jaidee"):
            self.assertEqual(names.display_name("a@example.test"), "Somchai Jaidee")
        with mock.patch.object(names.frappe.utils, "get_fullname", return_value=""):
            self.assertEqual(names.display_name("a@example.test"), "a@example.test")
        self.assertEqual(names.display_name(None), "")

    def test_display_names_takes_a_batch_in_one_query_with_the_same_fallback(self):
        rows = [names.frappe._dict(name="a@x.test", full_name="Somchai Jaidee"), names.frappe._dict(name="b@x.test", full_name="")]
        with mock.patch.object(names.frappe, "get_all", return_value=rows) as query:
            self.assertEqual(names.display_names(["a@x.test", "b@x.test", "c@x.test", "", None, "a@x.test"]),
                             {"a@x.test": "Somchai Jaidee", "b@x.test": "b@x.test", "c@x.test": "c@x.test"})
            query.assert_called_once()
        self.assertEqual(names.display_names([]), {})


if __name__ == "__main__":
    unittest.main()
