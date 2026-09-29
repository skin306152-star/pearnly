"""LIFF ID and credential normalization for issues and LINE channels. Pure; no Frappe or network."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_control"))
from pearnly_control.onboarding.line_ids import clean_credential, half_filled, normalize_liff_id


class LiffIdTests(unittest.TestCase):
    def test_plain_id_and_empty(self):
        self.assertEqual(normalize_liff_id(" 2010411313-6iTLjpWn "), "2010411313-6iTLjpWn")
        self.assertEqual(normalize_liff_id(""), "")
        self.assertEqual(normalize_liff_id(None), "")

    def test_liff_urls_reduce_to_id(self):
        for url in ("https://liff.line.me/2010411313-6iTLjpWn",
                    "https://liff.line.me/2010411313-6iTLjpWn/dms-booking?draft=1",
                    "line://app/2010411313-6iTLjpWn"):
            self.assertEqual(normalize_liff_id(url), "2010411313-6iTLjpWn", url)

    def test_other_values_rejected(self):
        for bad in ("6iTLjpWn", "2010411313", "https://example.com/2010411313-6iTLjpWn",
                    "https://liff.line.me/", "2010411313-6iTL jpWn"):
            with self.assertRaises(ValueError, msg=bad):
                normalize_liff_id(bad)


class CredentialTests(unittest.TestCase):
    def test_pasted_whitespace_is_removed_but_stored_placeholders_are_kept(self):
        # LINE-DEP-2026-0297 / 0285：密钥前面带了一个空格（33 位）
        self.assertEqual(clean_credential(" 0123456789abcdef0123456789abcdef"), "0123456789abcdef0123456789abcdef")
        self.assertEqual(clean_credential("token\n"), "token")
        self.assertEqual(clean_credential("   "), "")
        self.assertEqual(clean_credential(None), "")
        self.assertEqual(clean_credential("*" * 33), "*" * 33)

    def test_only_one_of_secret_and_token_is_half_filled(self):
        self.assertTrue(half_filled("secret", ""))
        self.assertTrue(half_filled("", "token"))
        self.assertFalse(half_filled("secret", "token"))
        self.assertFalse(half_filled("", None))


if __name__ == "__main__":
    unittest.main()
