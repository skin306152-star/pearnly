"""Zero-I/O regression: every booking card writes the address the same way, with its Thai prefixes.

Old bug (11-01 on the Samsung test phone, testdms): the first summary card read
"12/7 หมู่ 5 ซ.ลาดพร้าว 71 ต.ลาดพร้าว อ.ลาดพร้าว จ.กรุงเทพมหานคร 10230"; after "Edit" → change only the
delivery date → save, the new card read "12/7 5 ลาดพร้าว 71 ลาดพร้าว ลาดพร้าว กรุงเทพมหานคร 10230".
The editor save rebuilt the summary with the preview card's own join (`qa_cards._fallback_summary`),
which dropped every prefix. All three cards now use `intake.draft.address_line`.
"""
from __future__ import annotations

import importlib
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_dms"))

ADDRESS = {
    "house_no": "12/7", "moo": "5", "soi": "ลาดพร้าว 71",
    "subdistrict_name": "ลาดพร้าว", "district_name": "ลาดพร้าว", "province_name": "กรุงเทพมหานคร",
}
EXPECTED = "12/7 หมู่ 5 ซ.ลาดพร้าว 71 ต.ลาดพร้าว อ.ลาดพร้าว จ.กรุงเทพมหานคร 10230"


class Isolated(unittest.TestCase):
    def setUp(self):
        guard = patch.dict(sys.modules)
        guard.start()
        self.addCleanup(guard.stop)
        for name in list(sys.modules):
            if name == "frappe" or name.startswith(("frappe.", "pearnly_core", "pearnly_dms")):
                del sys.modules[name]
        self.stub("frappe", _=lambda text: text)
        self.stub("pearnly_core", __path__=[])
        self.stub("pearnly_dms.i18n", t=lambda key, lang=None, **kw: key, line_lang=lambda: "th")
        self.draft = importlib.import_module("pearnly_dms.intake.draft")
        self.qa_cards = importlib.import_module("pearnly_dms.booking.conversation.qa_cards")

    def stub(self, name, **attrs):
        module = types.ModuleType(name)
        module.__dict__.update(attrs)
        sys.modules[name] = module
        return module


class AddressLine(Isolated):
    def test_the_card_after_an_editor_save_keeps_the_prefixes(self):
        # What `editor.booking_edit` leaves in the session: the draft with readable names, no summary.
        summary = self.qa_cards._fallback_summary({"draft": {**ADDRESS, "zipcode": "10230"}})
        self.assertEqual(summary["address"], EXPECTED)

    def test_the_first_card_and_the_master_snapshot_read_the_same(self):
        _, snapshot = self.draft.master_snapshot({**ADDRESS, "zipcode_name": "10230"})
        self.assertEqual(snapshot["address"], EXPECTED)
        geo = {
            "provinces": [["1", "กรุงเทพมหานคร"]], "districts": [["4", "ลาดพร้าว"]],
            "subdistricts": [["29", "ลาดพร้าว"]], "zipcodes": [["147", "10230"]],
        }
        ids = {"house_no": "12/7", "moo": "5", "soi": "ลาดพร้าว 71", "province_id": "1",
               "district_id": "4", "subdistrict_id": "29", "zipcode_id": "147"}
        self.assertEqual(self.draft.build_summary(ids, geo)["address"], EXPECTED)

    def test_building_village_and_road_sit_in_place_and_empty_parts_leave_no_gaps(self):
        line = self.draft.address_line({"house_no": "9", "building": "A", "village": "บ้านสวน",
                                        "road": "พหลโยธิน", "province_name": "ลำปาง", "soi": " "})
        self.assertEqual(line, "9 A บ้านสวน ถ.พหลโยธิน จ.ลำปาง")
        self.assertEqual(self.qa_cards._fallback_summary({"draft": {}})["address"], "—")


if __name__ == "__main__":
    unittest.main()
