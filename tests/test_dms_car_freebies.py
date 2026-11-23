"""Zero-I/O checks for the car freebies shown read-only on the DMS LINE booking preview and editor (2026-11-23).

Freebies come from the published vehicle snapshot (the car form's six freebie columns); nothing logs into DMS,
sends LINE or touches a Site.
"""
from __future__ import annotations

import importlib
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_dms"))

FORM = {
    "txtmodelcode": "VUK60",
    "txtfbproduct[]": ["Floor mat", "Film", "Insurance", "Fuel"],
    "txtfbunits[]": ["set", "car", "policy", "litre"],
    "txtfbquantity[]": ["1", "1", "1", "50"],
    "txtfbprice[]": ["1,500.00", "4,000.00", "14,234.13", "18.00"],
    "txtfbamount[]": ["1,500.00", "4,000.00", "14,234.13", "900.00"],
    "fbproductval[]": ["P1", "P2", "P3", "P4"],
}


class Isolated(unittest.TestCase):
    def setUp(self):
        guard = patch.dict(sys.modules)
        guard.start()
        self.addCleanup(guard.stop)
        for name in list(sys.modules):
            if name == "frappe" or name.startswith(("frappe.", "pearnly_core", "pearnly_dms")):
                del sys.modules[name]
        self.freebies = importlib.import_module("pearnly_dms.booking.car_freebies")

    def stub(self, name, **attrs):
        module = types.ModuleType(name)
        module.__dict__.update(attrs)
        sys.modules[name] = module
        parent, _, child = name.rpartition(".")
        if parent in sys.modules:
            setattr(sys.modules[parent], child, module)
        return module


class FromFormTests(Isolated):
    def test_rows_and_total_use_every_freebie(self):
        out = self.freebies.from_form("C1", FORM)
        self.assertEqual(out["car_id"], "C1")
        self.assertEqual([row["name"] for row in out["rows"]], FORM["txtfbproduct[]"])
        self.assertEqual(out["rows"][3], {"name": "Fuel", "unit": "litre", "qty": "50",
                                          "price": "18.00", "amount": "900.00"})
        self.assertEqual(out["total"], "20,634.13")

    def test_no_freebies_or_unreadable_columns_show_nothing(self):
        self.assertEqual(self.freebies.from_form("C1", {})["rows"], [])
        broken = {**FORM, "txtfbamount[]": FORM["txtfbamount[]"][:3]}
        self.assertEqual(self.freebies.from_form("C1", broken)["rows"], [])
        bad_amount = {**FORM, "txtfbamount[]": ["x", "1", "1", "1"]}
        self.assertEqual(self.freebies.from_form("C1", bad_amount)["rows"], [])

    def test_saved_freebies_only_count_for_the_same_car(self):
        saved = self.freebies.from_form("C1", FORM)
        self.assertEqual(self.freebies.for_car({"answers": {"car": {"id": "C1"}}, "car_freebies": saved}), saved)
        self.assertEqual(self.freebies.for_car({"answers": {"car": {"id": "C2"}}, "car_freebies": saved}), {})
        self.assertEqual(self.freebies.for_car({"answers": {"car": {"id": "C1"}}}), {})

    def test_unreadable_snapshot_never_blocks(self):
        from pearnly_dms.contracts.client_base import DmsClientError

        view = Mock(car_form=Mock(side_effect=DmsClientError("x", "ERR_DMS_SNAPSHOT_PENDING")))
        self.assertEqual(self.freebies.from_view(view, "C1")["rows"], [])
        view = Mock(car_form=Mock(return_value=FORM))
        self.assertEqual(self.freebies.from_view(view, "C1")["total"], "20,634.13")


class DirectoryTests(Isolated):
    def test_car_form_and_car_names_come_from_the_published_vehicle_snapshot(self):
        published = {
            "vehicles": {"data": {"masters": {"cars": []}, "paints": {},
                                  "catalogues": {"car": {"C1": {"id": "C1", "labels": {}, "fields": {
                                      **FORM, "txtdescmodelname": " Mu-X 2.2 Biturbo RS 4WD "}}}}},
                         "as_of": 1.0, "completed_at": 1.0, "generation": "g", "stale": False},
            "company": {"data": {"masters": {}, "catalogues": {"setcbc": {"singleton": {
                "id": "singleton", "fields": {"cbhidefreebiesprice": "1"}, "labels": {}}}}},
                        "as_of": 1.0, "completed_at": 1.0, "generation": "g", "stale": False},
        }

        class Unavailable(Exception):
            pass

        self.stub("pearnly_dms.snapshots.store", read=lambda endpoint, dataset, **kw: published[dataset],
                  SnapshotUnavailable=Unavailable)
        self.stub("pearnly_dms.contracts.geo_matching", resolve=Mock())
        directory = importlib.import_module("pearnly_dms.snapshots.directory").Directory("EP1")
        self.assertEqual(directory.car_form("C1")["txtfbamount[]"], FORM["txtfbamount[]"])
        self.assertEqual(directory.car_form("missing"), {})
        self.assertEqual(directory.car_names(), {"C1": "Mu-X 2.2 Biturbo RS 4WD"})


class PreviewRowTests(Isolated):
    def setUp(self):
        super().setUp()
        self.stub("pearnly_dms.i18n", t=lambda key, lang=None, **kw: key + ("|" + str(kw["n"]) if "n" in kw else ""),
                  line_lang=lambda: "th", all_languages=lambda key: {"th": key})
        self.stub("pearnly_dms.booking.booking_car_details", rows=lambda qa: [])
        self.stub("pearnly_dms.editor.edit_link")
        self.stub("pearnly_dms.booking.booking_payments", payment_preview_detail=Mock())
        self.cards = importlib.import_module("pearnly_dms.booking.conversation.qa_cards")

    def texts(self, rows):
        found = []
        for row in rows:
            found += [part["text"] for part in row.get("contents", [])] if row.get("contents") else [row.get("text")]
        return found

    def qa(self, form):
        return {"answers": {"car": {"id": "C1"}}, "car_freebies": self.freebies.from_form("C1", form)}

    def test_card_lists_each_freebie_and_the_total(self):
        texts = self.texts(self.cards.freebie_rows(self.qa(FORM)))
        self.assertEqual(texts[0], "dms.card.qa.LBL_FREEBIES")
        self.assertIn("50 litre × 18.00 = 900.00", texts)
        self.assertEqual(texts[-2:], ["dms.card.qa.LBL_FREEBIES_TOTAL", "20,634.13"])

    def test_long_lists_stop_at_fifteen_and_say_how_many_more(self):
        many = {name: values * 5 for name, values in FORM.items() if name.endswith("[]")}
        rows = self.cards.freebie_rows(self.qa(many))
        texts = self.texts(rows)
        self.assertIn("dms.card.qa.TXT_FREEBIES_MORE|5", texts)
        self.assertEqual(texts[-1], "103,170.65")
        self.assertEqual(sum(1 for row in rows if row.get("layout") == "horizontal"), 15)

    def test_another_car_or_no_freebies_adds_nothing(self):
        qa = self.qa(FORM)
        qa["answers"]["car"]["id"] = "C2"
        self.assertEqual(self.cards.freebie_rows(qa), [])
        self.assertEqual(self.cards.freebie_rows({"answers": {"car": {"id": "C1"}}}), [])


class CarLabelTests(Isolated):
    # 生产 ris 的 VUK60 那一行（下拉行没有 ชื่อรถ；ชื่อรถ 在车型主档表单 txtdescmodelname）。
    ROW = [558, "VUK60", "TFS41GWRHRVU", 1, "อีซูซุ"]
    NAMES = {"558": "Mu-X 2.2 Biturbo RS 4WD", "600": "D-Max Spark 1.9"}

    def setUp(self):
        super().setUp()
        self.util = importlib.import_module("pearnly_dms.booking.conversation.qa_util")

    def test_option_shows_code_and_car_name(self):
        self.assertEqual(self.util.car_display(self.ROW, self.NAMES), "VUK60 / Mu-X 2.2 Biturbo RS 4WD")
        self.assertEqual(self.util.car_display(self.ROW, {}), "VUK60 / TFS41GWRHRVU")

    def test_search_also_matches_the_car_name(self):
        dmax = [600, "VAA10", "TFR86HDMXE"]
        needle = self.util.norm("dmax")
        self.assertFalse(self.util.car_matches(dmax, needle, {}))
        self.assertTrue(self.util.car_matches(dmax, needle, self.NAMES))
        self.assertTrue(self.util.car_matches(self.ROW, self.util.norm("vuk60"), self.NAMES))
        self.assertFalse(self.util.car_matches(self.ROW, "", self.NAMES))

    def test_chosen_car_text_prefers_the_display_and_old_sessions_keep_their_label(self):
        self.assertEqual(self.util.car_text({"car": {"label": "VUK60 TFS41GWRHRVU",
                                                     "display": "VUK60 / Mu-X 2.2 Biturbo RS 4WD"}}),
                         "VUK60 / Mu-X 2.2 Biturbo RS 4WD")
        self.assertEqual(self.util.car_text({"car": {"label": "VUK60 TFS41GWRHRVU"}}), "VUK60 TFS41GWRHRVU")

    def test_confirmation_check_still_compares_the_unchanged_label(self):
        # 显示名不进确认复核：label 仍是 代码 + ชื่อรุ่นรถ，确认时不会误报主档已变。
        self.assertEqual(self.util.car_label(self.ROW), "VUK60 TFS41GWRHRVU")


if __name__ == "__main__":
    unittest.main()
