"""Zero-I/O checks for the DMS customer update fixes found on ris 2026-10-05.

Three real-world shapes the old tests never had: a customer whose 「สาขา」 is empty (about 95%
of ris customers), a contact address that already holds where the customer lives while the
ID card shows the hometown, and a salesperson who sends the phone and then an address line.
The DMS side is a fake form transport; nothing logs in or writes anywhere.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_dms"))

from pearnly_dms.client.gateway import DmsClient, DmsClientError  # noqa: E402
from pearnly_dms.client.transport import Resp  # noqa: E402
from pearnly_dms.contracts.customer_merge import secondary_addresses  # noqa: E402
from pearnly_dms.intake.phone_text import extract_phone  # noqa: E402

PEOPLE_ID = "1310200148678"
# Two address trees: the ID-card hometown (บุรีรัมย์) and where the customer lives (ระยอง).
GEO = {
    "selprovinces": [("20", "บุรีรัมย์"), ("12", "ระยอง")],
    "seldistricts": {"20": [("231", "คูเมือง")], "12": [("142", "เมืองระยอง")]},
    "selsubdistricts": {"231": [("1751", "หนองขมาร")], "142": [("1019", "แกลง")]},
    "selzipcodes": {"1751": [("1880", "31190")], "1019": [("1060", "21160")]},
}
HOME = {"house_no": "133", "moo": "2", "province_id": "20", "district_id": "231",
        "subdistrict_id": "1751", "zipcode_id": "1880"}
LIVES = {"house_no": "142/5", "moo": "3", "province_id": "12", "district_id": "142",
         "subdistrict_id": "1019", "zipcode_id": "1060"}
TEXT = {"house_no": "txthousenum", "moo": "txtmoo"}
SELECT = {"province_id": "selprovinces", "district_id": "seldistricts",
          "subdistrict_id": "selsubdistricts", "zipcode_id": "selzipcodes"}
BRANCHES = [[1, "00000", "สำนักงานใหญ่ สาขาของลูกค้า"], [2, "00001", "สาขาของลูกค้า 1"]]


class FakeDms:
    """Native ``cus`` form over an in-memory record; ``cus/edit.php`` behaves like DMS:
    an empty branch id is a foreign-key failure, anything else is stored as posted."""

    def __init__(self, record, branches=BRANCHES):
        self.record, self.branches, self.calls = dict(record), branches, []

    def _select(self, name, value):
        base = name.split("_")[0]
        options = GEO["selprovinces"] if base == "selprovinces" else [
            row for rows in GEO[base].values() for row in rows]
        body = '<option value="">-</option>' + "".join(
            '<option value="%s"%s>%s</option>' % (oid, " selected" if oid == value else "", label)
            for oid, label in options)
        return '<select name="%s">%s</select>' % (name, body)

    def form(self):
        r = self.record
        parts = ['<input name="%s" value="%s">' % (key, r.get(key, "")) for key in (
            "txtcuscode", "txtcusname", "txtpeopleid", "txttaxid", "txttel", "txtbirthday")]
        parts.append('<input type="hidden" name="branchcusval" value="%s">' % r.get("branchcusval", ""))
        parts.append('<input name="txtbranchcus" value="%s">' % r.get("txtbranchcus", ""))
        parts.append('<select name="selprefix"><option value="3" selected>นางสาว</option></select>')
        for suffix in ("", "_ct", "_sd"):
            for name in TEXT.values():
                parts.append('<input name="%s" value="%s">' % (name + suffix, r.get(name + suffix, "")))
            for name in SELECT.values():
                parts.append(self._select(name + suffix, r.get(name + suffix, "")))
        return "<form>%s</form>" % "".join(parts)

    def children(self, level, parent_field, data):
        rows = GEO[level].get(str(data.get(parent_field) or ""), [])
        return "".join('<option value="%s">%s</option>' % row for row in rows)

    def post(self, url, data=None, files=None, timeout_ms=None):
        data = dict(data or {})
        self.calls.append((url.split("/dms/")[-1], data))
        path = self.calls[-1][0]
        if path == "cus/form.php":
            body = self.form()
        elif path == "cus/component/bshsd.php":
            body = json.dumps([row for row in self.branches if row[1].startswith(data.get("sdt", ""))])
        elif path == "cus/component/listdistricts.php":
            body = self.children("seldistricts", "selprovinces", data)
        elif path == "cus/component/listsubdistricts.php":
            body = self.children("selsubdistricts", "seldistricts", data)
        elif path == "cus/component/listzipcodes.php":
            body = self.children("selzipcodes", "selsubdistricts", data)
        elif path == "cus/edit.php":
            if not str(data.get("branchcusval") or "").strip():
                body = "err::ข้อมูลดังกล่าวถูกใช้งานในระบบ"
            else:
                self.record.update({k: v for k, v in data.items() if k not in ("stsel", "idsel")})
                body = ""
        else:
            body = ""
        return Resp(200, body, body.encode())

    def get(self, url, timeout_ms=None):
        return Resp(200, "", b"")

    def edits(self):
        return [data for path, data in self.calls if path == "cus/edit.php"]


def native(block, suffix):
    out = {}
    for key, value in block.items():
        out[(TEXT.get(key) or SELECT[key]) + suffix] = value
    return out


def ris_customer(**extra):
    """Customer 15476 as DMS held it: no branch, all three blocks at the Rayong address."""
    record = {"txtcuscode": "8678", "txtcusname": "อิศราถรณ์ เต็นรัมย์", "txtpeopleid": PEOPLE_ID,
              "txttaxid": PEOPLE_ID, "txttel": "0611655634", "txtbirthday": "01/09/2541"}
    for suffix in ("", "_ct", "_sd"):
        record.update(native(LIVES, suffix))
    record.update(extra)
    return record


def update_from_card(dms):
    """What 「อัปเดตข้อมูล」 sends for an ID card read: identity plus the card's address,
    with no explicit blocks (the write layer used to copy it into all three)."""
    client = DmsClient(dms, "https://example.test/dms/")
    baseline = client.read_customer("15476")
    fields = {"people_id": PEOPLE_ID, "name": "อริศราภรณ์ เต็นรัมย์", **HOME}
    return client.save_customer(fields=fields, mode="overwrite", customer_id="15476", baseline=baseline)


class BranchTests(unittest.TestCase):
    def test_empty_branch_is_filled_with_head_office_and_the_update_lands(self):
        dms = FakeDms(ris_customer())
        cid, _ = update_from_card(dms)
        self.assertEqual(cid, "15476")
        [edit] = dms.edits()
        self.assertEqual((edit["branchcusval"], edit["txtbranchcus"]), ("1", "00000"))

    def test_existing_branch_is_kept_without_looking_up_branches(self):
        dms = FakeDms(ris_customer(branchcusval="2", txtbranchcus="00001"))
        update_from_card(dms)
        [edit] = dms.edits()
        self.assertEqual((edit["branchcusval"], edit["txtbranchcus"]), ("2", "00001"))
        self.assertFalse([p for p, _ in dms.calls if p == "cus/component/bshsd.php"])

    def test_dms_without_head_office_branch_stops_before_writing(self):
        dms = FakeDms(ris_customer(), branches=[[2, "00001", "สาขา 1"]])
        with self.assertRaises(DmsClientError) as caught:
            update_from_card(dms)
        self.assertEqual(caught.exception.error_code, "ERR_DMS_CUSTOMER_BRANCH")
        self.assertFalse(dms.edits())


class AddressRuleTests(unittest.TestCase):
    def test_card_address_only_replaces_the_id_card_block(self):
        dms = FakeDms(ris_customer())
        update_from_card(dms)
        record = dms.record
        self.assertEqual(record["selprovinces"], "20")
        self.assertEqual(record["txthousenum"], "133")
        for suffix in ("_ct", "_sd"):
            self.assertEqual(record["selprovinces" + suffix], "12", suffix)
            self.assertEqual(record["txthousenum" + suffix], "142/5", suffix)

    def test_empty_contact_and_delivery_blocks_are_filled_from_the_card(self):
        empty = {key: "" for suffix in ("_ct", "_sd") for key in native(LIVES, suffix)}
        dms = FakeDms(ris_customer(branchcusval="1", txtbranchcus="00000", **empty))
        update_from_card(dms)
        for suffix in ("", "_ct", "_sd"):
            self.assertEqual(dms.record["selzipcodes" + suffix], "1880", suffix)
            self.assertEqual(dms.record["txtmoo" + suffix], "2", suffix)

    def test_rule_keeps_used_blocks_and_fills_empty_ones(self):
        current = {"house_no": "142/5", "house_no_ct": "142/5", "province_id_ct": "12"}
        desired = {"name": "x", "house_no": "133", "house_no_ct": "133", "house_no_sd": "133"}
        result = secondary_addresses(desired, current)
        self.assertNotIn("house_no_ct", result)
        self.assertEqual(result["house_no_sd"], "133")
        self.assertEqual(result["province_id_sd"], "")
        self.assertEqual((result["name"], result["house_no"]), ("x", "133"))


class PhoneTextTests(unittest.TestCase):
    def test_phone_is_taken_from_the_message(self):
        self.assertEqual(extract_phone("0611655634"), "0611655634")
        self.assertEqual(extract_phone("เบอร์ 061-165-5634 ค่ะ"), "061-165-5634")
        self.assertEqual(extract_phone("02 123 4567"), "02 123 4567")
        self.assertEqual(extract_phone("+66 61 165 5634"), "+66 61 165 5634")

    def test_address_and_other_numbers_are_not_a_phone(self):
        address = "ที่อยู่ปัจจุบัน \n142/5 ม.3 ต.แกลง อ.เมือง จ.ระยอง 21160 (ห้อง142/50)  คะ"
        for text in (address, PEOPLE_ID, "01/09/2541", "1,000.00", "ห้อง 142/50", "2"):
            self.assertIsNone(extract_phone(text), text)

    def test_two_numbers_in_one_message_are_not_guessed(self):
        self.assertIsNone(extract_phone("0611655634 หรือ 0812345678"))


if __name__ == "__main__":
    unittest.main()
