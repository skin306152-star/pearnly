#!/usr/bin/env python3
"""Customer identity protocol regression: real client, fake outer transport only.

No Frappe, credentials, Site, browser, network or external business writes.
Run directly: python3 scripts/test-dms-customer-identity.py
"""
from copy import deepcopy
from html import escape
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/pearnly_dms"))

from pearnly_dms.client.gateway import DmsClient
from pearnly_dms.contracts.client_base import DmsClientError

PID = "1000000730317"
OTHER = "1000001205453"
NAME = "สมชาย ใจดี"
ADDR = dict(province_id="P", district_id="D", subdistrict_id="S", zipcode_id="Z")
FIELDS = dict(people_id=PID, name=NAME, prefix_id="17", **ADDR)
SELECTS = {"selprefix": "17", "selprovinces": "P", "seldistricts": "D",
           "selsubdistricts": "S", "selzipcodes": "Z"}


def record(pid=PID, **extra):
    result = dict(txtcuscode="C001", txtpeopleid=pid, txttaxid=pid,
                  txtcusname=NAME, txtbirthday="2530-01-01", txttel="0800000001")
    for suffix in ("", "_ct", "_sd"):
        result.update({key + suffix: value for key, value in SELECTS.items() if key != "selprefix"})
        for field in ("txthousenum", "txtbuilding", "txtfloor", "txtroom",
                      "txtvillage", "txtmoo", "txtsoi", "txtroad"):
            result[field + suffix] = "unchanged-" + field + suffix
    return {**result, "selprefix": "17", **extra}


def form(data):
    parts = ["<form>"]
    for key, value in data.items():
        if key.startswith("sel"):
            parts.append(f'<select name="{key}"><option value="{escape(value)}" selected>label</option></select>')
        else:
            parts.append(f'<input name="{key}" value="{escape(value, quote=True)}">')
    return "".join(parts) + "</form>"


def rows(ids):
    # Both native attribute orders are valid; labels are deliberately not identity authority.
    return "dt::" + "".join(
        f'<div data-val="{cid}" onclick="ctllistdata(this);"><div class="detaildata">'
        f'<div><p>C-{cid}</p><p>{NAME}</p></div><div><p>{PID}</p></div></div></div>' for cid in ids
    ) if ids else "ndt::"


class Transport:
    def __init__(self, records=None, searches=None):
        self.records = deepcopy(records or {})
        self.searches = list(searches) if searches is not None else None
        self.calls = []
        self.form_overrides = {}
        self.ignore_fields = set()
        self.create_collision = False
        self.collision_record = None
        self.collision_body = "err::ข้อมูลซ้ำ"
        self.created = 0
        self.statuses = {}
        # The new-customer form's own code (DMS-TEST leaves it empty) and its length limit;
        # with unique_codes on, new.php refuses a code another record holds, as DMS does.
        self.new_code = "AUTO-NEXT"
        self.code_maxlength = None
        self.unique_codes = False
        self.code_taken_body = 'err::"รหัสลูกค้า" ซ้ำ'

    def post(self, url, *, data, **kwargs):
        path = url.removeprefix("https://identity.invalid/dms/")
        self.calls.append((path, dict(data)))
        if path == "cus/component/showdata.php":
            if self.searches is not None:
                if not self.searches:
                    raise AssertionError("unplanned search")
                result = self.searches.pop(0)
                body = rows(result) if isinstance(result, list) else result
            else:
                term = data["sd"]
                body = rows([cid for cid, item in self.records.items()
                             if term in (item["txtpeopleid"], item["txtcuscode"])])
        elif path == "cus/form.php":
            if data["status"] == "n":
                body = form(record("", txtcuscode=self.new_code, txtcusname=""))
                if self.code_maxlength is not None:
                    body = body.replace('<input name="txtcuscode"',
                                        f'<input maxlength="{self.code_maxlength}" name="txtcuscode"', 1)
            else:
                cid = data["id"]
                override = self.form_overrides.get(cid)
                body = override() if callable(override) else override
                if body is None:
                    body = form(self.records[cid])
        elif path in ("cus/component/listdistricts.php", "cus/component/listsubdistricts.php",
                      "cus/component/listzipcodes.php"):
            expected, child = {"cus/component/listdistricts.php": ("P", "D"),
                               "cus/component/listsubdistricts.php": ("D", "S"),
                               "cus/component/listzipcodes.php": ("S", "Z")}[path]
            assert list(data.values()) == [expected]
            body = f'<option value="{child}">label</option>'
        elif path == "cus/new.php":
            if self.create_collision:
                if self.collision_record is not None:
                    self.records["collision"] = deepcopy(self.collision_record)
                body = self.collision_body
            elif self.unique_codes and any(item["txtcuscode"] == data.get("txtcuscode")
                                           for item in self.records.values()):
                body = self.code_taken_body
            else:
                self.created += 1
                self.records["created"] = {k: v for k, v in data.items() if k not in ("stsel", "idsel")}
                body = "ok::"
        elif path == "cus/edit.php":
            cid = data["idsel"]
            self.records[cid].update({k: v for k, v in data.items()
                                      if k not in self.ignore_fields and k not in ("stsel", "idsel")})
            body = "ok::"
        else:
            raise AssertionError("forbidden transport path: " + path)
        return SimpleNamespace(status_code=self.statuses.get(path, 200), text=body)

    def writes(self):
        return [(path, data) for path, data in self.calls if path in ("cus/new.php", "cus/edit.php")]


class IdentityTests(unittest.TestCase):
    def setUp(self):
        # Readback retries pause 1.5 s between reads; record the pauses instead of waiting.
        patcher = patch("pearnly_dms.client.client_intake.time.sleep")
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    def client(self, records=None, searches=None):
        transport = Transport(records, searches)
        return DmsClient(transport, "https://identity.invalid/dms/"), transport

    def rejects(self, call, transport, *, writes=0):
        with self.assertRaises(DmsClientError) as caught:
            call()
        self.assertEqual(len(transport.writes()), writes)
        # Neither raw native HTML nor full customer IDs should be error messages.
        for secret in (PID, OTHER, "<form", "<html"):
            self.assertNotIn(secret, str(caught.exception))

    def test_lookup_wrong_code_is_not_identity(self):
        client, _ = self.client({"own": record(OTHER, txtcuscode=PID)})
        self.assertEqual(client.lookup_customer(PID), {"found": False, "customer_id": None, "fields": {}})

    def test_lookup_checks_all_candidates_and_returns_unique_exact(self):
        client, transport = self.client({"wrong": record(OTHER), "right": record()}, [["wrong", "right"]])
        self.assertEqual(client.lookup_customer(PID)["customer_id"], "right")
        self.assertEqual(sum(p == "cus/form.php" for p, _ in transport.calls), 2)

    def test_formatted_thai_identity_matches_normalized_full_number(self):
        thai = str.maketrans("0123456789", "๐๑๒๓๔๕๖๗๘๙")
        value = "1-0000-00730-31-7".translate(thai)
        client, transport = self.client({"right": record(value)}, [["right"]])
        self.assertTrue(client.lookup_customer("1 0000 00730 31 7")["found"])
        self.assertEqual(transport.calls[0][1]["sd"], PID)

    def test_invalid_requested_identity_is_not_absence(self):
        for value in ("", "0730317", "1000000730317x", "１００００００７３０３１７"):
            with self.subTest(value=value):
                client, transport = self.client()
                self.rejects(lambda: client.lookup_customer(value), transport)
                self.assertFalse(transport.calls)

    def test_two_distinct_exact_customers_are_ambiguous(self):
        client, transport = self.client({"one": record(), "two": record()}, [["one", "two"]])
        self.rejects(lambda: client.lookup_customer(PID), transport)

    def test_duplicate_same_customer_id_is_read_once(self):
        client, transport = self.client({"one": record()}, [["one", "one"]])
        self.assertEqual(client.lookup_customer(PID)["customer_id"], "one")
        self.assertEqual(sum(p == "cus/form.php" for p, _ in transport.calls), 1)

    def test_full_ten_row_page_is_not_complete_evidence(self):
        client, transport = self.client({str(i): record() for i in range(10)}, [list(map(str, range(10)))])
        self.rejects(lambda: client.lookup_customer(PID), transport)
        self.assertEqual(len(transport.calls), 1)

    def test_unknown_or_partial_search_shape_is_not_absence(self):
        for body in ("", "<html>login</html>", "dt::", "err::failure",
                     rows(["one"]) + '<div data-val="lost" onclick="other">bad</div>',
                     rows(["one"]) + '<div data-val="lost" onclick="ctllistdata(this)">',
                     "ndt::" + rows(["one"])):
            with self.subTest(body=body[:20]):
                client, transport = self.client({"one": record()}, [body])
                self.rejects(lambda: client.lookup_customer(PID), transport)

    def test_search_attribute_order_does_not_change_identity(self):
        body = '<div class="row" onclick="ctllistdata(this);" data-val="one"><p>label</p></div>'
        client, _ = self.client({"one": record()}, [body])
        self.assertEqual(client.lookup_customer(PID)["customer_id"], "one")

    def test_empty_or_login_form_is_not_identity(self):
        for body in ("", "<html>login</html>", '<input name="txtpeopleid" value="' + PID + '">',
                     form(record()) + '<input name="txtpeopleid" value="' + OTHER + '">'):
            with self.subTest(body=body[:20]):
                client, transport = self.client({"one": record()}, [["one"]])
                transport.form_overrides["one"] = body
                self.rejects(lambda: client.lookup_customer(PID), transport)

    def test_search_and_form_http_failure_are_not_absence(self):
        for path in ("cus/component/showdata.php", "cus/form.php"):
            with self.subTest(path=path):
                client, transport = self.client({"one": record()})
                transport.statuses[path] = 502
                self.rejects(lambda: client.lookup_customer(PID), transport)

    def test_blank_or_partial_stored_identity_is_not_exact(self):
        for value in ("", PID[-4:], PID + "x"):
            with self.subTest(value=value):
                client, _ = self.client({"one": record(value)}, [["one"]])
                self.assertFalse(client.lookup_customer(PID)["found"])

    def test_generic_search_keeps_first_hit_semantics(self):
        client, transport = self.client({"wrong": record(OTHER)}, [["wrong"]])
        self.assertEqual(client.search_customer(PID), "wrong")
        self.assertEqual(len(transport.calls), 1)

    def test_ledger_is_direct_read_full_identity_not_a_first_match(self):
        client, transport = self.client({"old": record(OTHER), "right": record()}, [[]])
        self.assertEqual(client.lookup_customer(PID, fallback_customer_ids=["old", "right"])["customer_id"], "right")
        self.assertEqual(len(transport.calls), 3)

    def test_ledger_duplicates_and_search_overlap_are_read_once(self):
        client, transport = self.client({"one": record()}, [["one"]])
        self.assertTrue(client.lookup_customer(PID, fallback_customer_ids=["one", "one"])["found"])
        self.assertEqual(len(transport.calls), 2)

    def test_ledger_and_cross_source_ambiguity_fail_closed(self):
        for search in ([], ["one"]):
            with self.subTest(search=search):
                client, transport = self.client({"one": record(), "two": record()}, [search])
                self.rejects(lambda: client.lookup_customer(PID, fallback_customer_ids=["one", "two"]), transport)

    def test_ledger_unknown_form_or_over_limit_cannot_become_new_customer(self):
        client, transport = self.client({"one": record()}, [[]])
        transport.form_overrides["one"] = "<html>login</html>"
        self.rejects(lambda: client.lookup_customer(PID, fallback_customer_ids=["one"]), transport)
        client, transport = self.client()
        self.rejects(lambda: client.lookup_customer(PID, fallback_customer_ids=list(map(str, range(6)))), transport)
        self.assertFalse(transport.calls)

    def test_create_does_not_convert_customer_code_collision(self):
        client, transport = self.client({"wrong": record(OTHER, txtcuscode=PID)})
        original = deepcopy(transport.records["wrong"])
        self.assertEqual(client.save_customer(fields=FIELDS, mode="create"), ("created", False))
        self.assertEqual(transport.records["wrong"], original)
        self.assertEqual([p for p, _ in transport.writes()], ["cus/new.php"])

    def test_create_exact_existing_requires_new_review(self):
        client, transport = self.client({"right": record()})
        self.rejects(lambda: client.save_customer(fields=FIELDS, mode="create"), transport)
        self.assertEqual(transport.records["right"]["txtpeopleid"], PID)

    def test_overwrite_rechecks_loaded_identity_against_baseline(self):
        client, transport = self.client({"right": record()})
        transport.form_overrides["right"] = form(record(OTHER))
        self.rejects(lambda: client.save_customer(fields=FIELDS, mode="overwrite", customer_id="right",
                     baseline=client._extract_customer_fields(record())), transport)

    def test_duplicate_response_research_does_not_overwrite_wrong_identity(self):
        client, transport = self.client({"wrong": record(OTHER, txtcuscode=PID)})
        transport.create_collision = True
        original = deepcopy(transport.records["wrong"])
        with patch("pearnly_dms.client.client_intake.time.sleep") as sleep:
            self.rejects(lambda: client.save_customer(fields=FIELDS, mode="create"), transport, writes=1)
        self.assertEqual(transport.records["wrong"], original)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(sum(p == "cus/component/showdata.php" for p, _ in transport.calls), 4)

    def test_duplicate_response_exact_match_requires_new_review(self):
        client, transport = self.client()
        transport.create_collision = True
        transport.collision_record = record()
        self.rejects(lambda: client.save_customer(fields=FIELDS, mode="create"), transport, writes=1)
        self.assertEqual([p for p, _ in transport.writes()], ["cus/new.php"])

    def test_create_success_readback_rejects_wrong_or_missing_identity(self):
        for result in (["wrong"], [], ["wrong", "other"]):
            with self.subTest(result=result):
                self.sleep.reset_mock()
                # Preflight search, then the readback is read three times; never a second create.
                client, transport = self.client({"wrong": record(OTHER), "other": record(OTHER)},
                                                [[], result, result, result])
                self.rejects(lambda: client.save_customer(fields=FIELDS, mode="create"), transport, writes=1)
                self.assertEqual(sum(p == "cus/component/showdata.php" for p, _ in transport.calls), 4)
                self.assertEqual(self.sleep.call_count, 2)

    def test_create_success_readback_rejects_two_exact_ids(self):
        client, transport = self.client({"a": record(), "b": record()}, [[], ["a", "b"], ["a", "b"], ["a", "b"]])
        self.rejects(lambda: client.save_customer(fields=FIELDS, mode="create"), transport, writes=1)

    def test_create_readback_missed_by_search_is_read_again_not_reposted(self):
        # The search can come back empty right after a create (2026-10-12 reproduction): the
        # write is already in DMS, so only the read is repeated and the result is a success.
        client, transport = self.client(searches=[[], [], ["created"]])
        self.assertEqual(client.save_customer(fields=FIELDS, mode="create"), ("created", False))
        self.assertEqual([p for p, _ in transport.writes()], ["cus/new.php"])
        self.assertEqual(self.sleep.call_count, 1)

    def test_overwrite_readback_page_failure_is_read_again_not_reposted(self):
        client, transport = self.client({"own": record()})
        forms = iter([form(record()), "<html>login</html>"])
        transport.form_overrides["own"] = lambda: next(forms, None)
        self.assertEqual(client.save_customer(fields={"phone": "0800000002"}, mode="overwrite", customer_id="own",
                         baseline=client._extract_customer_fields(record())), ("own", False))
        self.assertEqual([p for p, _ in transport.writes()], ["cus/edit.php"])
        self.assertEqual(self.sleep.call_count, 1)

    def test_lost_connection_on_readback_is_not_read_again(self):
        # The transport already retried the read once before reporting the connection lost;
        # more reads would only keep the operator waiting while DMS is down.
        client, transport = self.client({"own": record()})
        reads = []
        def lost():
            reads.append(True)
            if len(reads) == 1:
                return form(record())
            failure = DmsClientError("socket hang up", "ERR_DMS_TECHNICAL")
            raise failure from ConnectionError("synthetic")
        transport.form_overrides["own"] = lost
        with self.assertRaises(DmsClientError) as caught:
            client.save_customer(fields={"phone": "0800000002"}, mode="overwrite", customer_id="own",
                                 baseline=client._extract_customer_fields(record()))
        self.assertEqual(caught.exception.error_code, "ERR_DMS_CUSTOMER_UNCONFIRMED")
        self.assertEqual((len(reads), len(transport.writes()), self.sleep.call_count), (2, 1, 0))

    def test_overwrite_cannot_succeed_when_identity_or_tax_write_was_ignored(self):
        for ignored in ("txtpeopleid", "txttaxid"):
            with self.subTest(ignored=ignored):
                client, transport = self.client({"own": record(OTHER)})
                transport.ignore_fields = {ignored}
                self.rejects(lambda: client.save_customer(fields=FIELDS, mode="overwrite", customer_id="own",
                             baseline=client._extract_customer_fields(record(OTHER))),
                             transport, writes=1)
                # A value DMS kept differently is read three times, never written twice.
                self.assertEqual(sum(p == "cus/form.php" for p, _ in transport.calls), 4)

    def test_explicit_tax_write_must_be_read_back(self):
        client, transport = self.client({"own": record()})
        transport.ignore_fields = {"txttaxid"}
        self.rejects(lambda: client.save_customer(fields={"tax_id": OTHER}, mode="overwrite", customer_id="own",
                     baseline=client._extract_customer_fields(record())),
                     transport, writes=1)

    def test_formatted_identity_is_accepted_on_write_readback(self):
        client, transport = self.client({"own": record(PID)})
        original_post = transport.post

        def post(url, *, data, **kwargs):
            response = original_post(url, data=data, **kwargs)
            if url.endswith("cus/edit.php"):
                transport.records["own"]["txtpeopleid"] = "1-0000-00730-31-7"
                transport.records["own"]["txttaxid"] = "1 0000 00730 31 7"
            return response

        transport.post = post
        self.assertEqual(client.save_customer(fields={**FIELDS, "phone": "0800000002"}, mode="overwrite",
                         customer_id="own", baseline=client._extract_customer_fields(record())), ("own", False))

    def test_overwrite_empty_readback_cannot_pass_even_when_name_not_submitted(self):
        client, transport = self.client({"own": record()})
        forms = iter([form(record()), "", "", ""])
        transport.form_overrides["own"] = lambda: next(forms)
        self.rejects(lambda: client.save_customer(fields={"phone": "0800000002"},
                                                  mode="overwrite", customer_id="own",
                                                  baseline=client._extract_customer_fields(record())), transport, writes=1)
        self.assertEqual(self.sleep.call_count, 2)

    def test_implicit_and_explicit_tax_identity_success(self):
        for fields, expected in (({"people_id": OTHER, "tax_id": ""}, OTHER),
                                 ({"people_id": OTHER, "tax_id": PID}, PID),
                                 ({"tax_id": ""}, "")):
            with self.subTest(fields=fields):
                client, transport = self.client({"own": record()})
                client.save_customer(fields=fields, mode="overwrite", customer_id="own",
                                     baseline=client._extract_customer_fields(record()))
                self.assertEqual(transport.records["own"]["txttaxid"], expected)

    def test_manual_candidate_claim_and_restore_preserve_all_other_fields(self):
        client, transport = self.client({"own": record()})
        original = deepcopy(transport.records["own"])
        for pid in (OTHER, PID):
            self.assertEqual(client.save_customer(fields={"people_id": pid, "name": NAME},
                                                 mode="overwrite", customer_id="own",
                                                 baseline=client.read_customer("own")), ("own", False))
            saved = transport.records["own"]
            self.assertEqual(saved["txtpeopleid"], pid)
            self.assertEqual(saved["txttaxid"], pid)
            for key, value in original.items():
                if key not in ("txtpeopleid", "txttaxid"):
                    self.assertEqual(saved[key], value, key)

    def test_full_synthetic_claim_restore_and_new_customer_sequence(self):
        client, transport = self.client()
        self.assertEqual(client.save_customer(fields=FIELDS, mode="create"), ("created", False))
        original_id = "created"
        # Freeze the first fixture ID; later creation must be a distinct record.
        transport.records["own"] = transport.records.pop(original_id)
        original = deepcopy(transport.records["own"])
        original["txtcuscode"] = PID
        transport.records["own"]["txtcuscode"] = PID
        for pid, absent in ((OTHER, PID), (PID, OTHER)):
            client.save_customer(fields={"people_id": pid, "name": NAME}, mode="overwrite", customer_id="own",
                                 baseline=client.read_customer("own"))
            self.assertFalse(client.lookup_customer(absent, fallback_customer_ids=["own"])["found"])
        self.assertEqual({k: transport.records["own"][k] for k in original}, original)
        self.assertEqual(client.save_customer(fields={**FIELDS, "people_id": OTHER}, mode="create"), ("created", False))
        self.assertEqual(transport.records["created"]["txtpeopleid"], OTHER)
        self.assertEqual(transport.records["own"]["txtpeopleid"], PID)


    # ── G8: the code made from this ID is held by an older record of another person ──────
    # Old bug (DMS-TEST, 2026-10-12): the new-customer form leaves the code empty, so the
    # code is this ID; an older record already held that code with someone else's ID.
    # DMS refused "code duplicate", the ID research found no one, and the operator only got
    # "save failed" -- every retry and re-scan failed the very same way.
    def taken(self, records, searches=None, maxlength=20):
        client, transport = self.client(records, searches)
        transport.new_code, transport.unique_codes, transport.code_maxlength = "", True, maxlength
        return client, transport

    @staticmethod
    def registered(fail_recheck=False):
        seen = {"attempts": 0, "rechecks": 0}

        def on_attempt(verify):
            seen["attempts"] += 1

        def recheck():
            seen["rechecks"] += 1
            if fail_recheck:
                raise DmsClientError("binding changed", "ERR_DMS_CUSTOMER_SAVE")

        on_attempt.assert_current = recheck
        return on_attempt, seen

    def refused(self, call, transport, *, writes=1):
        """Today's outcome: a clean rejection after one (or the one extra) send, never 'unknown'."""
        with self.assertRaises(DmsClientError) as caught:
            call()
        self.assertEqual(caught.exception.error_code, "ERR_DMS_CUSTOMER_SAVE")
        self.assertEqual(caught.exception.response_body, {"submitted": False, "rejected": True})
        self.assertEqual(len(transport.writes()), writes)
        self.assertNotIn("created", transport.records)

    def test_code_held_by_another_person_is_created_under_the_next_free_code(self):
        client, transport = self.taken({"wrong": record(OTHER, txtcuscode=PID)})
        original = deepcopy(transport.records["wrong"])
        on_attempt, seen = self.registered()
        self.assertEqual(client.save_customer(fields=FIELDS, mode="create", on_attempt=on_attempt),
                         ("created", False))
        first, second = (data for _, data in transport.writes())
        self.assertEqual((first["txtcuscode"], second["txtcuscode"]), (PID, PID + "-1"))
        self.assertEqual({k: v for k, v in first.items() if k != "txtcuscode"},
                         {k: v for k, v in second.items() if k != "txtcuscode"})
        self.assertEqual(transport.records["created"]["txtpeopleid"], PID)
        self.assertEqual(transport.records["wrong"], original)  # the holder is never written
        self.assertEqual(seen, {"attempts": 1, "rechecks": 1})  # one registration, rechecked once

    def test_codes_held_by_other_people_are_skipped_up_to_three(self):
        third = "1000002000006"
        client, transport = self.taken({"wrong": record(OTHER, txtcuscode=PID),
                                        "next": record(third, txtcuscode=PID + "-1")})
        self.assertEqual(client.save_customer(fields=FIELDS, mode="create"), ("created", False))
        self.assertEqual([data["txtcuscode"] for _, data in transport.writes()], [PID, PID + "-2"])
        held = {"wrong": record(OTHER, txtcuscode=PID)}
        held.update({f"next{n}": record(third, txtcuscode=f"{PID}-{n}") for n in (1, 2, 3)})
        client, transport = self.taken(held)
        self.refused(lambda: client.save_customer(fields=FIELDS, mode="create"), transport)

    def test_code_search_rows_that_only_contain_the_code_are_not_its_holder(self):
        # DMS's list search is a substring match, so searching the code also lists the record
        # coded "<ID>-1"; only the form's exact code makes a row the holder (else: two holders).
        third = "1000002000006"
        client, transport = self.taken({"wrong": record(OTHER, txtcuscode=PID),
                                        "next": record(third, txtcuscode=PID + "-1")},
                                       [[], [], [], [], ["next", "wrong"], ["next"], [], ["created"]])
        self.assertEqual(client.save_customer(fields=FIELDS, mode="create"), ("created", False))
        self.assertEqual([data["txtcuscode"] for _, data in transport.writes()], [PID, PID + "-2"])

    def test_unclear_code_holder_keeps_the_rejection(self):
        many = [str(n) for n in range(10)]
        cases = {
            # The holder's ID is blank or partial: it may be this very person.
            "blank holder": ({"wrong": record("", txtcuscode=PID)}, None, {}),
            "partial holder": ({"wrong": record(PID[-4:], txtcuscode=PID)}, None, {}),
            # Nobody visible holds the code (search lag, or another account's customer).
            "holder not found": ({"wrong": record(OTHER, txtcuscode=PID)}, [[], [], [], [], []], {}),
            "two holders": ({"a": record(OTHER, txtcuscode=PID), "b": record("1000002000006", txtcuscode=PID)},
                            None, {}),
            "search error": ({"wrong": record(OTHER, txtcuscode=PID)}, [[], [], [], [], "err::busy"], {}),
            "full page": ({"wrong": record(OTHER, txtcuscode=PID)}, [[], [], [], [], many], {}),
            "form unreadable": ({"wrong": record(OTHER, txtcuscode=PID)}, [[], [], [], [], ["wrong"]],
                                {"wrong": "<html>login</html>"}),
        }
        for name, (records, searches, overrides) in cases.items():
            with self.subTest(name):
                client, transport = self.taken(records, searches)
                transport.form_overrides.update(overrides)
                self.refused(lambda: client.save_customer(fields=FIELDS, mode="create"), transport)

    def test_spare_code_must_fit_the_form_limit(self):
        for maxlength in (None, 14):
            with self.subTest(maxlength=maxlength):
                client, transport = self.taken({"wrong": record(OTHER, txtcuscode=PID)}, maxlength=maxlength)
                self.refused(lambda: client.save_customer(fields=FIELDS, mode="create"), transport)

    def test_code_holder_with_this_identity_goes_to_the_existing_review(self):
        # The ID search missed them three times, the code search finds them: same person.
        client, transport = self.taken({"own": record(PID, txtcuscode=PID)}, [[], [], [], [], ["own"]])
        with self.assertRaises(DmsClientError) as caught:
            client.save_customer(fields=FIELDS, mode="create")
        self.assertEqual(caught.exception.error_code, "ERR_DMS_CUSTOMER_EXISTS")
        self.assertEqual(caught.exception.response_body["customer_conflict"]["customer_id"], "own")
        self.assertEqual(len(transport.writes()), 1)

    def test_changed_binding_before_the_second_send_keeps_the_rejection(self):
        client, transport = self.taken({"wrong": record(OTHER, txtcuscode=PID)})
        on_attempt, seen = self.registered(fail_recheck=True)
        self.refused(lambda: client.save_customer(fields=FIELDS, mode="create", on_attempt=on_attempt), transport)
        self.assertEqual(seen, {"attempts": 1, "rechecks": 1})
        # A registration that cannot be rechecked is not sent again either.
        client, transport = self.taken({"wrong": record(OTHER, txtcuscode=PID)})
        self.refused(lambda: client.save_customer(fields=FIELDS, mode="create", on_attempt=lambda verify: None),
                     transport)

    def test_second_send_outcome_is_final_and_never_sent_a_third_time(self):
        def lost(transport, response):
            raise TimeoutError("lost acknowledgement")

        def bad_gateway(transport, response):
            return SimpleNamespace(status_code=502, text="")

        def taken_meanwhile(transport, response):
            transport.records.pop("created", None)
            return SimpleNamespace(status_code=200, text=transport.code_taken_body)

        def never_read_back(transport, response):
            transport.records.pop("created", None)
            return response

        for change, code in ((lost, "ERR_DMS_CUSTOMER_UNCONFIRMED"), (bad_gateway, "ERR_DMS_CUSTOMER_UNCONFIRMED"),
                             (taken_meanwhile, "ERR_DMS_CUSTOMER_SAVE"),
                             (never_read_back, "ERR_DMS_CUSTOMER_UNCONFIRMED")):
            with self.subTest(change.__name__):
                client, transport = self.taken({"wrong": record(OTHER, txtcuscode=PID)})
                post = transport.post

                def second(url, *, data, _post=post, _transport=transport, _change=change, **kwargs):
                    response = _post(url, data=data, **kwargs)
                    if url.endswith("cus/new.php") and len(_transport.writes()) == 2:
                        return _change(_transport, response)
                    return response

                transport.post = second
                with self.assertRaises(DmsClientError) as caught:
                    client.save_customer(fields=FIELDS, mode="create")
                self.assertEqual(caught.exception.error_code, code)
                self.assertEqual(len(transport.writes()), 2)

    def test_name_duplicate_and_prefilled_codes_are_unchanged(self):
        # DMS refused the name, not the code: research only, exactly as before.
        client, transport = self.taken({})
        transport.create_collision = True
        transport.collision_body = 'err::"ชื่อ นามสกุลลูกค้า" ซ้ำ'
        self.refused(lambda: client.save_customer(fields=FIELDS, mode="create"), transport)
        self.assertEqual([p for p, _ in transport.calls if p in ("cus/component/showdata.php", "cus/form.php")],
                         ["cus/component/showdata.php", "cus/form.php"] + ["cus/component/showdata.php"] * 3)
        # DMS numbered the customer itself: its code is never replaced by ours.
        client, transport = self.taken({"auto": record(OTHER, txtcuscode="AUTO-NEXT")})
        transport.new_code = "AUTO-NEXT"
        self.refused(lambda: client.save_customer(fields=FIELDS, mode="create"), transport)
        self.assertEqual(sum(p == "cus/component/showdata.php" for p, _ in transport.calls), 4)

    def test_spare_code_read_back_differently_is_logged(self):
        client, transport = self.taken({"wrong": record(OTHER, txtcuscode=PID)})
        post = transport.post

        def truncating(url, *, data, **kwargs):
            response = post(url, data=data, **kwargs)
            if "created" in transport.records:
                transport.records["created"]["txtcuscode"] = PID + "-"
            return response

        transport.post = truncating
        with self.assertLogs("pearnly_dms.client.client_intake", "ERROR") as logs:
            self.assertEqual(client.save_customer(fields=FIELDS, mode="create"), ("created", False))
        self.assertNotIn(PID, "".join(logs.output))  # lengths only, never the ID itself


if __name__ == "__main__":
    with patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")), \
         patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")):
        unittest.main(verbosity=2)
