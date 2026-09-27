"""ID card read once with flash-lite, misread names absorbed by the DMS master (user 2026-09-27).

Pinned Frappe, no Site/IO. The model is flash-lite whatever the site OCR mode, there is no
escalation to 3.8, and an existing customer's master name wins over an OCR name that differs
by a mark or two. Run: docker run ... <image> /candidate/scripts/test-dms-id-card-reads.py
"""
import unittest
from unittest.mock import patch

import frappe
from pearnly_core import ocr
from pearnly_core.execution.errors import Deferred
from pearnly_dms.intake.service import master_name_if_close
from pearnly_dms.ocr import ocr_id_card

VALID = "1103702587460"


def read(first="สมหญิง", last="แสนสุข", pid=VALID):
    return {"people_id": pid, "first_name_th": first, "last_name_th": last, "date_of_birth_be": "01/04/2533"}


class ExtractTests(unittest.TestCase):
    def test_one_flash_lite_read_in_every_site_mode(self):
        for mode in ("enterprise", "economy"):
            with patch.object(frappe, "conf", frappe._dict(pearnly_ocr_mode=mode), create=True), \
                    patch.object(ocr, "json_from_images", return_value=read()) as call:
                out = ocr_id_card.extract(b"\xff\xd8\xff-image")
            self.assertEqual(call.call_count, 1)
            self.assertEqual(call.call_args.kwargs["model"], "gemini-3.1-flash-lite")
            self.assertEqual(call.call_args.args[0], ocr_id_card._ID_CARD_PROMPT)
            self.assertEqual(out["id_card"]["people_id"], VALID)

    def test_a_short_id_is_not_escalated_but_flagged(self):
        with patch.object(ocr, "json_from_images", return_value=read(pid="123")) as call:
            out = ocr_id_card.extract(b"img")
        self.assertEqual(call.call_count, 1)
        self.assertEqual(out["id_card"]["people_id"], "")
        self.assertTrue(out["needs_review"])

    def test_unparsable_output_is_unreadable_and_deferral_propagates(self):
        with patch.object(ocr, "json_from_images", return_value=None), \
                patch.object(ocr_id_card, "_", side_effect=lambda text: text):
            with self.assertRaises(ocr_id_card.IdCardExtractError):
                ocr_id_card.extract(b"img")
        with patch.object(ocr, "json_from_images", side_effect=Deferred("capacity", 5)):
            with self.assertRaises(Deferred):
                ocr_id_card.extract(b"img")


class MasterNameTests(unittest.TestCase):
    def test_a_missing_mark_or_look_alike_letter_keeps_the_master(self):
        self.assertEqual(master_name_if_close("กิตติ์ ใจดี", "กิตติ ใจดี"), "กิตติ์ ใจดี")
        self.assertEqual(master_name_if_close("ซอซ์ สายซอ", "ซอช์ สายซอ"), "ซอซ์ สายซอ")
        self.assertEqual(master_name_if_close("กิตติ์  ใจดี", "กิตติ์ใจดี"), "กิตติ์  ใจดี")

    def test_a_different_name_is_reported(self):
        self.assertEqual(master_name_if_close("สมศรี ใจดี", "สมศรี แสนสุข"), "สมศรี แสนสุข")
        self.assertEqual(master_name_if_close("", "A X"), "A X")
        self.assertEqual(master_name_if_close("A X", ""), "")


if __name__ == "__main__":
    unittest.main()
