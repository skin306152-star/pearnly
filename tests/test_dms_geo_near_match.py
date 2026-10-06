"""Zero-I/O checks: an ID-card address read one character wrong still resolves (2026-10-06).

Real-LINE test on testdms: the card's ต.หนองขมาร was read as หนองชมาร, the exact-name match broke
the chain at the subdistrict, the postal code stayed empty and the diff card listed half an
address, so the salesperson was sent to the edit page. A unique one-character neighbour inside the
already-resolved parent is now accepted; anything ambiguous still stays empty.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_dms"))

from pearnly_dms.contracts.geo_matching import near, resolve  # noqa: E402

PROVINCES = [["12", "ระยอง"], ["20", "บุรีรัมย์"]]
CHILDREN = {
    ("districts", "20"): [["231", "คูเมือง"], ["232", "กระสัง"]],
    ("subdistricts", "231"): [["1751", "หนองขมาร"], ["1752", "หนองแวง"], ["1753", "บ้านแพ"]],
    ("zipcodes", "1751"): [["1880", "31190"]],
    ("zipcodes", "1752"): [["1881", "31190"], ["1882", "32000"]],
}


@dataclass(frozen=True)
class Address:
    province_name: str = ""
    district_name: str = ""
    subdistrict_name: str = ""
    zipcode: str = ""
    province_id: str = ""
    district_id: str = ""
    subdistrict_id: str = ""
    zipcode_id: str = ""


def run(**names):
    return resolve(Address(**names), PROVINCES, lambda level, parent: CHILDREN.get((level, parent), []))


class NearMatchTests(unittest.TestCase):
    def test_one_misread_letter_in_the_subdistrict_still_completes_the_chain(self):
        got = run(province_name="จ.บุรีรัมย์", district_name="อ.คูเมือง", subdistrict_name="ต.หนองชมาร")
        self.assertEqual((got.province_id, got.district_id, got.subdistrict_id, got.zipcode_id),
                         ("20", "231", "1751", "1880"))

    def test_exact_names_win_and_still_resolve(self):
        got = run(province_name="บุรีรัมย์", district_name="คูเมือง", subdistrict_name="หนองขมาร")
        self.assertEqual(got.subdistrict_id, "1751")

    def test_two_wrong_letters_are_not_guessed(self):
        got = run(province_name="บุรีรัมย์", district_name="คูเมือง", subdistrict_name="หนองชมา")
        self.assertEqual((got.district_id, got.subdistrict_id, got.zipcode_id), ("231", "", ""))

    def test_two_equally_close_names_are_not_guessed(self):
        options = [["1", "หนองขมาร"], ["2", "หนองชมาร"]]
        self.assertEqual(near(options, "หนองฆมาร"), "")

    def test_short_names_are_never_near_matched(self):
        self.assertEqual(near([["1", "บาง"]], "บาน"), "")

    def test_postal_codes_are_never_near_matched(self):
        got = run(province_name="บุรีรัมย์", district_name="คูเมือง", subdistrict_name="หนองแวง",
                  zipcode="31199")
        self.assertEqual((got.subdistrict_id, got.zipcode_id), ("1752", ""))

    def test_a_single_postal_code_is_still_filled_when_the_card_has_none(self):
        got = run(province_name="บุรีรัมย์", district_name="คูเมือง", subdistrict_name="หนองชมาร")
        self.assertEqual(got.zipcode_id, "1880")


if __name__ == "__main__":
    unittest.main()
