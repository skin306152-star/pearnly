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
        self.assertEqual(out["rows"][3], {"product_id": "P4", "name": "Fuel", "unit": "litre", "qty": "50",
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

        pending = DmsClientError("x", "ERR_DMS_SNAPSHOT_PENDING")
        view = Mock(car_form=Mock(side_effect=pending), hide_freebies_price=Mock(return_value=False))
        self.assertEqual(self.freebies.from_view(view, "C1")["rows"], [])
        view = Mock(car_form=Mock(return_value=FORM), hide_freebies_price=Mock(return_value=False))
        self.assertEqual(self.freebies.from_view(view, "C1")["total"], "20,634.13")
        self.assertFalse(self.freebies.from_view(view, "C1")["hide_price"])
        view.hide_freebies_price.side_effect = pending
        self.assertTrue(self.freebies.from_view(view, "C1")["hide_price"])


class EditedTests(Isolated):
    PRODUCTS = [{"product_id": "P9", "code": "X9", "name": "Wax", "unit": "can", "unit_id": "7", "price": "120.00"}]

    def default(self):
        return self.freebies.from_form("C1", FORM)

    def test_unchanged_rows_are_not_an_edit(self):
        rows = [{"product_id": r["product_id"], "qty": r["qty"]} for r in self.default()["rows"]]
        out = self.freebies.edited("C1", self.default(), rows, self.PRODUCTS)
        self.assertFalse(out["edited"])
        self.assertEqual(out["total"], "20,634.13")

    def test_quantity_delete_and_add_follow_the_native_rules(self):
        out = self.freebies.edited("C1", self.default(), [
            {"product_id": "P4", "qty": "2"}, {"product_id": "P1", "qty": "0"},
            {"product_id": "P9", "qty": "3"}, {"product_id": "P4", "qty": "9"}], self.PRODUCTS)
        self.assertTrue(out["edited"])
        self.assertEqual([(r["product_id"], r["qty"], r["amount"]) for r in out["rows"]],
                         [("P4", "2", "36.00"), ("P1", "1", "1,500.00"), ("P9", "3", "360.00")])
        self.assertEqual(out["total"], "1,896.00")
        self.assertEqual(self.freebies.wanted({"answers": {"car": {"id": "C1"}}, "car_freebies": out}),
                         (("P4", "2"), ("P1", "1"), ("P9", "3")))

    def test_unknown_products_or_bad_numbers_are_refused(self):
        self.assertIsNone(self.freebies.edited("C1", self.default(), [{"product_id": "ZZ", "qty": "1"}], self.PRODUCTS))
        self.assertIsNone(self.freebies.edited("C1", self.default(), [{"product_id": "P4", "qty": "x"}], self.PRODUCTS))

    def test_removing_every_row_is_an_edit_with_no_freebies(self):
        out = self.freebies.edited("C1", self.default(), [], self.PRODUCTS)
        self.assertEqual((out["rows"], out["edited"]), ([], True))


class DirectoryTests(Isolated):
    def test_car_form_and_car_names_come_from_the_published_vehicle_snapshot(self):
        published = {
            "vehicles": {"data": {"masters": {"cars": []}, "paints": {},
                                  "catalogues": {"car": {"C1": {"id": "C1", "labels": {}, "fields": {
                                      **FORM, "txtdescmodelname": " Mu-X 2.2 Biturbo RS 4WD "}}},
                                  "product": {
                                      "P1": {"id": "P1", "labels": {"selstatuscancel": "ใช้งาน"}, "fields": {
                                          "txtproductcode": "A1", "txtproductname": "Mat", "txtprice": "1,500.00",
                                          "unitsval": "1", "txtunits": "set", "selstatuscancel": "0"}},
                                      "P2": {"id": "P2", "labels": {"selstatuscancel": "ยกเลิก"}, "fields": {
                                          "txtproductcode": "A2", "txtproductname": "Old", "txtprice": "1.00",
                                          "unitsval": "1", "txtunits": "set", "selstatuscancel": "1"}}}}},
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
        self.assertEqual([p["product_id"] for p in directory.freebie_products()], ["P1"])
        self.assertTrue(directory.hide_freebies_price())


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

    def qa(self, form, hide_price=False):
        return {"answers": {"car": {"id": "C1"}},
                "car_freebies": self.freebies.from_form("C1", form, hide_price=hide_price)}

    def test_card_lists_each_freebie_and_the_total(self):
        texts = self.texts(self.cards.freebie_rows(self.qa(FORM)))
        self.assertEqual(texts[0], "dms.card.qa.LBL_FREEBIES")
        self.assertIn("50 litre × 18.00 = 900.00", texts)
        self.assertEqual(texts[-2:], ["dms.card.qa.LBL_FREEBIES_TOTAL", "20,634.13"])

    def test_hidden_price_setting_shows_names_quantities_and_units_only(self):
        texts = self.texts(self.cards.freebie_rows(self.qa(FORM, hide_price=True)))
        self.assertIn("50 litre", texts)
        self.assertNotIn("dms.card.qa.LBL_FREEBIES_TOTAL", texts)
        self.assertFalse(any("900.00" in text or "×" in text for text in texts))

    def test_long_lists_stop_at_fifteen_and_say_how_many_more(self):
        many = {name: values * 5 for name, values in FORM.items() if name.endswith("[]")}
        rows = self.cards.freebie_rows(self.qa(many))
        texts = self.texts(rows)
        self.assertIn("dms.card.qa.TXT_FREEBIES_MORE|5", texts)
        self.assertEqual(texts[-1], "103,170.65")
        self.assertEqual(sum(1 for row in rows if row.get("layout") == "vertical"), 15)

    def test_each_freebie_is_one_line_with_the_quantity_on_the_right(self):
        """用户 11-28：每件赠品原先占两行（名称一行、数量另起一行靠右），太占地方。只有「数量 单位」时（默认不显示价格）改为一行：
        名称在左占剩余宽度、长名在本格折行；数量在右按内容宽度、不折行、贴顶——名称再长也不把数量挤到下一行。"""
        rows = self.cards.freebie_rows(self.qa(FORM, hide_price=True))
        items = [row for row in rows if row.get("type") == "box"]
        self.assertEqual(len(items), 4)
        for row in items:
            self.assertEqual((row["layout"], row["spacing"]), ("horizontal", "md"))
            name, detail = row["contents"]
            self.assertEqual((name["flex"], name["wrap"]), (1, True))
            self.assertEqual((detail["flex"], detail["align"], detail["gravity"]), (0, "end", "top"))
            self.assertNotIn("wrap", detail)
        self.assertEqual([row["contents"][1]["text"] for row in items], ["1 set", "1 car", "1 policy", "50 litre"])

    def test_with_prices_the_sum_stays_whole_under_the_name(self):
        # 显示价格时算式长，放同一行会被拆成两三行：仍是名称在上、算式在下靠右；合计仍是左标签右值
        rows = self.cards.freebie_rows(self.qa(FORM))
        items = [row for row in rows if row.get("type") == "box"]
        self.assertEqual([row["layout"] for row in items], ["vertical"] * 4 + ["baseline"])
        self.assertEqual(items[3]["contents"][1], {"type": "text", "text": "50 litre × 18.00 = 900.00", "size": "sm",
                                                   "color": "#111111", "align": "end", "wrap": True})

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
