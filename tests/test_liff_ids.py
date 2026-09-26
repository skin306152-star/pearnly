"""LIFF ID normalization for issues and LINE channels. Pure; no Frappe or network."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_control"))
from pearnly_control.onboarding.line_ids import normalize_liff_id


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


if __name__ == "__main__":
    unittest.main()
