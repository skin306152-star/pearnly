"""Pure correction-event identities and frozen-snapshot contracts; no Site or IO."""
from copy import deepcopy
import unittest

from pearnly_cowork.ocr.feedback import model


AI = [{"fields": {"seller_tax": "0105555555555", "seller_name": "First seller", "vat": "7"}}]


def events(**changes):
    args = dict(site="test.localhost", workspace="W", client=None, actor="a@example.invalid",
                history="H", version=2, ai_pages=AI,
                edited_pages=[{"fields": {**AI[0]["fields"], "vat": "8"}}])
    args.update(changes)
    return model.events(**args)


def body():
    event = events()[0]
    return {"schema": model.SCHEMA, "site": "test.localhost", "actor": "a@example.invalid",
        "workspace": "W", "client": "", "captured_at": "2026-09-21T00:00:00",
        "examples_by_tax": {event["seller_tax"]: [{"field_name": "vat", "ai_value": "7",
            "corrected_value": "8", "use_count": 1, "event_id": "CWFC-" + event["event_key"],
            "event_fingerprint": event["fingerprint"], "source_history": "H"}]}}


class FeedbackStoreModelTests(unittest.TestCase):
    def test_replay_identity_does_not_depend_on_actor(self):
        a, b = events()[0], events(actor="b@example.invalid")[0]
        for key in ("event_key", "sample_key", "fingerprint"):
            self.assertEqual(a[key], b[key])
        self.assertNotEqual(a["actor"], b["actor"])

    def test_scope_site_client_and_history_do_not_collide(self):
        first = events()[0]
        for changes in ({"site": "second.localhost"}, {"workspace": "other"}, {"client": "buyer"},
                        {"history": "other"}, {"version": 3}):
            other = events(**changes)[0]
            self.assertNotEqual(first["event_key"], other["event_key"])
        self.assertEqual(model.scope_key("site", "W", None), model.scope_key("site", "W", ""))

    def test_revised_corrected_value_changes_fingerprint_but_not_sample(self):
        a = events()[0]
        b = events(version=3, edited_pages=[{"fields": {**AI[0]["fields"], "vat": "9"}}])[0]
        self.assertEqual(a["sample_key"], b["sample_key"])
        self.assertNotEqual(a["fingerprint"], b["fingerprint"])
        conflict = events(edited_pages=[{"fields": {**AI[0]["fields"], "vat": "9"}}])[0]
        self.assertEqual(a["event_key"], conflict["event_key"])
        self.assertNotEqual(a["fingerprint"], conflict["fingerprint"])

    def test_seller_falls_back_to_first_ai_when_edited_tax_is_cleared(self):
        row = events(edited_pages=[{"fields": {"seller_name": "Edited", "vat": "8"}}])[0]
        self.assertEqual((row["seller_tax"], row["seller_name"]), ("0105555555555", "First seller"))

    def test_edited_seller_tax_is_the_group_key(self):
        rows = events(edited_pages=[{"fields": {**AI[0]["fields"], "seller_tax": "0106666666666", "vat": "8"}}])
        self.assertTrue(all(row["seller_tax"] == "0106666666666" for row in rows))

    def test_copy_selection_and_text_money_are_not_reinterpreted(self):
        rows = events(ai_pages=[{"is_copy": True, "fields": {"total_amount": "wrong"}},
                               {"fields": {"total_amount": "1,000.00"}}],
                      edited_pages=[{"fields": {"total_amount": "1000.00"}}])
        self.assertEqual([(r["ai_value"], r["corrected_value"]) for r in rows], [("1,000.00", "1000.00")])

    def test_zero_learns_and_clearing_does_not(self):
        self.assertEqual(events(edited_pages=[{"fields": {**AI[0]["fields"], "vat": ""}}]), [])
        self.assertEqual(events(edited_pages=[{"fields": {**AI[0]["fields"], "vat": 0}}])[0]["corrected_value"], "0")

    def test_wrong_revision_and_noninvoice_rejected(self):
        for version in (1, 0, True, "2", None):
            with self.assertRaises(ValueError):
                events(version=version)
        for pages in ({"pages": AI}, [{"_document_type": "bank", "fields": {}}], [None]):
            with self.assertRaises(ValueError):
                events(ai_pages=pages)

    def test_snapshot_roundtrip_and_no_input_alias(self):
        original = body()
        frozen = model.snapshot(original)
        self.assertEqual(model.verify_snapshot(frozen), original)
        original["examples_by_tax"].clear()
        self.assertTrue(frozen["body"]["examples_by_tax"])

    def test_mutation_missing_and_unknown_fields_rejected(self):
        frozen = model.snapshot(body())
        frozen["body"]["actor"] = "other"
        with self.assertRaises(ValueError):
            model.verify_snapshot(frozen)
        for mutate in (lambda b: b.pop("workspace"), lambda b: b.update(extra=True),
                       lambda b: b.update(captured_at="not-a-time")):
            value = body()
            mutate(value)
            with self.assertRaises(ValueError):
                model.snapshot(value)

    def test_unbounded_or_malformed_samples_are_rejected(self):
        value = body()
        row = next(iter(value["examples_by_tax"].values()))[0]
        for changes in ({"use_count": True}, {"use_count": 0}, {"event_id": "arbitrary"},
                        {"field_name": "items"}, {"corrected_value": []}, {"ai_value": {}},
                        {"event_fingerprint": "missing"}):
            candidate = deepcopy(value)
            next(iter(candidate["examples_by_tax"].values()))[0].update(changes)
            with self.assertRaises(ValueError):
                model.snapshot(candidate)
        for count in (2, 21):
            candidate = deepcopy(value)
            candidate["examples_by_tax"]["0105555555555"] = [deepcopy(row) for _ in range(count)]
            with self.assertRaises(ValueError):
                model.snapshot(candidate)

    def test_total_sample_and_utf8_byte_caps(self):
        value = body()
        template = next(iter(value["examples_by_tax"].values()))[0]
        value["examples_by_tax"] = {str(n): [{**template, "event_id": "CWFC-" + f"{n:064x}"}]
                                      for n in range(model.MAX_SAMPLES + 1)}
        with self.assertRaisesRegex(ValueError, "sample limit"):
            model.snapshot(value)
        value = body()
        next(iter(value["examples_by_tax"].values()))[0]["corrected_value"] = "泰" * (model.MAX_SNAPSHOT_BYTES // 3)
        with self.assertRaisesRegex(ValueError, "byte limit"):
            model.snapshot(value)

    def test_empty_authorized_scope_is_valid(self):
        value = body()
        value["examples_by_tax"] = {}
        self.assertEqual(model.verify_snapshot(model.snapshot(value))["examples_by_tax"], {})


if __name__ == "__main__":
    unittest.main()
