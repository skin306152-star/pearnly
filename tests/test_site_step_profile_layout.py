#!/usr/bin/env python3
"""No Frappe: the onboarding readback checks the same account profile layout that new sites are given.

On 2026-11-11 the new tenant klm stopped at its last step: verify_site still asserted the retired
first_name "Username" label that ``pearnly_core.account.layout`` removes since 9b0aef8f0.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
SITE_STEP = ROOT / "infrastructure/production/site-step.py"
LAYOUT = ROOT / "apps/pearnly_core/pearnly_core/account/layout.py"


class ProfileLayoutReadback(unittest.TestCase):
    def test_layout_removes_the_first_name_label_override(self):
        source = LAYOUT.read_text()
        self.assertRegex(source, r'frappe\.db\.delete\("Property Setter", \{"doc_type": "User", "field_name": "first_name"')

    def test_readback_does_not_require_the_removed_override(self):
        source = SITE_STEP.read_text()
        self.assertNotIn('"value": "Username"', source)
        self.assertRegex(source, re.compile(
            r'assert not frappe\.db\.exists\("Property Setter", \{"doc_type": "User", "field_name": "first_name"'))


if __name__ == "__main__":
    unittest.main()
