"""姓名（`account.names`，用户 10-30）：名必填、姓可空；显示用框架拼好的全名，没有回落账号。

Run with Frappe installed. No Site: the framework calls are replaced.
"""
import unittest
from unittest import mock

from pearnly_core.account import names


class Names(unittest.TestCase):
    def test_first_name_is_required_and_both_are_trimmed(self):
        with mock.patch.object(names.frappe, "throw", side_effect=ValueError) as throw:
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


if __name__ == "__main__":
    unittest.main()
