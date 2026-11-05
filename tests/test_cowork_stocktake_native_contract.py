"""Offline tests for the stocktake scan JSON boundary, independent of Frappe/Site."""
import unittest
from uuid import uuid4

from pearnly_cowork.stocktake.contracts import StocktakeValidationError
from pearnly_cowork.stocktake.native_contract import identifier, revision, void_flag


class NativeContract(unittest.TestCase):
    def test_uuid_revisions_and_void_flags_are_unambiguous(self):
        token = str(uuid4())
        self.assertEqual(identifier(token.upper()), token)
        for value in (None, True, [], "invalid"):
            with self.assertRaises(StocktakeValidationError):
                identifier(value)
        self.assertEqual(revision("0"), 0)
        self.assertEqual(revision(123), 123)
        for value in (None, True, -1, "0.0", "01", "1e3", "2147483647"):
            with self.assertRaises(StocktakeValidationError):
                revision(value)
        for value in (False, 0, "0", "false"):
            self.assertFalse(void_flag(value))
        for value in (True, 1, "1", "true"):
            self.assertTrue(void_flag(value))
        for value in (None, [], 2, "False", "anything"):
            with self.assertRaises(StocktakeValidationError):
                void_flag(value)


if __name__ == "__main__":
    unittest.main()
