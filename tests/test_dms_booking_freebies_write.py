"""Zero-I/O checks for writing a booking's freebie rows to DMS the way the native booking form does (2026-11-23).

Native evidence (DMS-TEST, read-only): picking a car calls drfcbc/component/cardffreebies.php, whose rows are
seven same-name arrays; rows can be edited (quantity), removed or added from the product picker; a whole-form
write-back (amend, attachment) must keep every row. Nothing here logs into DMS or touches a Site.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.parse import parse_qsl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_dms"))

ROW = ('<div id="listfreebies{i}" data-idx="{i}"><p><input type="text" name="txtfbproduct[]" value="{name}">'
       '<input type="text" name="txtfbquantity[]" value="{qty}"><input type="text" name="txtfbunits[]" readonly value="{unit}">'
       '<input type="hidden" name="txtfbprice[]" value="{price}"><input type="hidden" name="txtfbamount[]" value="{amount}">'
       '<input type="hidden" name="fbunitsval[]" value="{uid}"><input type="hidden" name="fbproductval[]" value="{pid}"></p></div>')
NATIVE_HTML = (ROW.format(i=1, name="ฟิล์ม เซรามิค", qty="1.00", unit="แผ่น", price="18,826.50", amount="18,826.50", uid="2", pid="9")
               + ROW.format(i=2, name="กรอบป้ายทะเบียน", qty="1.00", unit="แผ่น", price="307.63", amount="307.63", uid="2", pid="4"))
NATIVE = json.dumps([2, "2.00", "19,134.13", NATIVE_HTML])


class Isolated(unittest.TestCase):
    def setUp(self):
        guard = patch.dict(sys.modules)
        guard.start()
        self.addCleanup(guard.stop)
        for name in list(sys.modules):
            if name == "frappe" or name.startswith(("frappe.", "pearnly_core", "pearnly_dms")):
                del sys.modules[name]
        self.fb = importlib.import_module("pearnly_dms.booking.booking_freebies")
        forms = importlib.import_module("pearnly_dms.client.client_forms").DmsClientFormsMixin
        self.forms = forms()

    def native(self):
        return self.fb.native_rows(NATIVE, self.forms._parse_form_arrays)


class FormParsingTests(Isolated):
    def test_same_name_inputs_keep_every_row_in_page_order(self):
        arrays = self.forms._parse_form_arrays(NATIVE_HTML + '<input type="checkbox" name="x[]" value="a">')
        self.assertEqual(arrays["fbproductval[]"], ["9", "4"])
        self.assertEqual(arrays["txtfbprice[]"], ["18,826.50", "307.63"])
        self.assertNotIn("x[]", arrays)  # unchecked boxes are not values, same as the one-value parser

    def test_booking_form_adds_the_arrays_and_leaves_single_fields_as_text(self):
        page = '<input name="stsel" value="e"><input name="idsel" value="7">' + NATIVE_HTML
        form = self.forms._parse_booking_form(page)
        self.assertEqual(form["stsel"], "e")
        self.assertEqual(form["txtfbproduct[]"], ["ฟิล์ม เซรามิค", "กรอบป้ายทะเบียน"])
        # the old one-value parser alone keeps only the last row: the defect this fixes
        self.assertEqual(self.forms._parse_form_defaults(page)["fbproductval[]"], "4")


class TransportEncodingTests(Isolated):
    def setUp(self):
        super().setUp()
        self.transport = importlib.import_module("pearnly_dms.client.transport")

    def test_urlencoded_body_repeats_array_names_in_order(self):
        body, content_type = self.transport.encode_form({"stsel": "e", "fbproductval[]": ["9", "4"], "txtcus": "สมชาย"})
        self.assertEqual(content_type, "application/x-www-form-urlencoded")
        self.assertEqual(parse_qsl(body.decode("ascii")),
                         [("stsel", "e"), ("fbproductval[]", "9"), ("fbproductval[]", "4"), ("txtcus", "สมชาย")])

    def test_multipart_body_keeps_arrays_and_the_file(self):
        body, content_type = self.transport.encode_form(
            {"fbproductval[]": ["9", "4"], "fulnewname[]": "card"}, {"fulnew[]": ("a.jpg", b"\xff\xd8", "image/jpeg")})
        self.assertTrue(content_type.startswith("multipart/form-data; boundary="))
        self.assertEqual(body.count(b'name="fbproductval[]"'), 2)
        self.assertIn(b'filename="a.jpg"\r\nContent-Type: image/jpeg\r\n\r\n\xff\xd8', body)

    def test_plain_posts_still_use_the_form_dictionary(self):
        calls = []

        class Context:
            def post(self, url, **kw):
                calls.append(kw)

                class R:
                    status = 200

                    def body(self):
                        return b"ok"
                return R()

        transport = self.transport.PlaywrightTransport(Context())
        transport.post("https://dms.invalid/drfcbc/new.php", data={"a": "1"})
        transport.post("https://dms.invalid/drfcbc/new.php", data={"a[]": ["1", "2"]})
        self.assertEqual(calls[0]["form"], {"a": "1"})
        self.assertEqual(calls[1]["data"], b"a%5B%5D=1&a%5B%5D=2")
        self.assertEqual(calls[1]["headers"], {"content-type": "application/x-www-form-urlencoded"})


class NativeRowsTests(Isolated):
    def test_native_rows_are_read_from_the_car_freebies_reply(self):
        rows = self.native()
        self.assertEqual([r["product_id"] for r in rows], ["9", "4"])
        self.assertEqual(rows[0]["unit_id"], "2")

    def test_native_errors_and_mismatched_counts_stop_the_booking(self):
        from pearnly_dms.contracts.client_base import DmsClientError

        for body in ("err::no car", "", json.dumps([3, "3.00", "0", NATIVE_HTML]), "<html>"):
            with self.assertRaises(DmsClientError):
                self.fb.native_rows(body, self.forms._parse_form_arrays)
        self.assertEqual(self.fb.native_rows(json.dumps([0, "0.00", "0.00", ""]), self.forms._parse_form_arrays), [])

    def test_untouched_freebies_go_in_exactly_as_dms_brought_them(self):
        arrays = self.fb.form_arrays(self.fb.final_rows(self.native(), None, lambda: self.fail("no picker")))
        self.assertEqual(arrays["fbproductval[]"], ["9", "4"])
        self.assertEqual(arrays["txtfbquantity[]"], ["1.00", "1.00"])
        self.assertEqual(arrays["txtfbprice[]"], ["18826.50", "307.63"])
        self.assertEqual(arrays["txtfbamount[]"], ["18,826.50", "307.63"])
        self.assertEqual(list(arrays), list(self.fb.ARRAYS))

    def test_edits_change_quantity_drop_rows_and_add_from_the_picker(self):
        picker_calls = []

        def picker():
            picker_calls.append(1)
            return [[11, "P011", "บัตรเติมน้ำมัน", 3, "บัตร", "400.00"]]

        rows = self.fb.final_rows(self.native(), (("4", "2"), ("11", "1"), ("4", "5"), ("9", "0")), picker)
        self.assertEqual([(r["product_id"], r["qty"], r["amount"]) for r in rows],
                         [("4", "2.00", "615.26"), ("11", "1.00", "400.00"), ("9", "1.00", "18,826.50")])
        self.assertEqual(rows[1]["unit_id"], "3")
        self.assertEqual(len(picker_calls), 1)

    def test_all_rows_removed_sends_no_freebie_fields(self):
        self.assertEqual(self.fb.form_arrays(self.fb.final_rows(self.native(), (), lambda: None)), {})

    def test_an_added_product_dms_does_not_know_is_refused(self):
        from pearnly_dms.contracts.client_base import DmsClientError

        with self.assertRaises(DmsClientError):
            self.fb.final_rows(self.native(), (("99", "1"),), lambda: [])
        with self.assertRaises(DmsClientError):
            self.fb.final_rows(self.native(), (("99", "1"),), lambda: None)


class ReviewTests(Isolated):
    def setUp(self):
        super().setUp()
        self.review = importlib.import_module("pearnly_dms.booking.booking_review")

    def sent(self, **extra):
        rows = self.fb.final_rows(self.native(), None, lambda: None)
        return {**self.fb.form_arrays(rows), **extra}

    def test_the_same_rows_back_from_dms_raise_no_difference(self):
        form = {**self.sent(), "txtfbamount[]": ["18826.50", "307.63"], "txtfbquantity[]": ["1", "1.00"]}
        self.assertFalse([d for d in self.review.compare(self.sent(), form) if d["item"] == "qa.LBL_FREEBIES"])

    def test_missing_rows_come_back_as_a_difference_that_writes_the_arrays(self):
        form = {**self.sent(), "fbproductval[]": ["4"], "txtfbquantity[]": ["1.00"], "txtfbproduct[]": ["กรอบป้ายทะเบียน"],
                "txtfbunits[]": ["แผ่น"]}
        diff = next(d for d in self.review.compare(self.sent(), form) if d["item"] == "qa.LBL_FREEBIES")
        self.assertEqual(diff["write"]["fbproductval[]"], ["9", "4"])
        self.assertEqual(diff["inputs"], [])
        self.assertIn("ฟิล์ม เซรามิค 1.00 แผ่น", diff["sent"])
        self.assertFalse(self.review.unresolved([diff], self.sent()))

    def test_bookings_without_freebies_have_no_freebie_item(self):
        self.assertFalse([d for d in self.review.compare({}, {"fbproductval[]": []}) if d["item"] == "qa.LBL_FREEBIES"])


class WantedTests(Isolated):
    def setUp(self):
        super().setUp()
        self.car = importlib.import_module("pearnly_dms.booking.car_freebies")

    def test_untouched_or_another_car_uses_the_native_default(self):
        saved = {"car_id": "1", "rows": [{"product_id": "9", "qty": "2"}]}
        self.assertIsNone(self.car.wanted({"answers": {"car": {"id": "1"}}, "car_freebies": saved}))
        edited = {**saved, "edited": True}
        self.assertIsNone(self.car.wanted({"answers": {"car": {"id": "2"}}, "car_freebies": edited}))
        self.assertEqual(self.car.wanted({"answers": {"car": {"id": "1"}}, "car_freebies": edited}), (("9", "2"),))
        self.assertEqual(self.car.wanted({"answers": {"car": {"id": "1"}}, "car_freebies": {**edited, "rows": []}}), ())


if __name__ == "__main__":
    unittest.main()
