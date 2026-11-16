"""存货科目组每个账套选一次（用户 2026-09-28）：纯规则与推送载荷。组码是合成的，不是真账套。

选过的组按账套存；只能选小助手为这份账套判过合格的组；有存货主档的账套也把所选的组带给小助手
（新建库存品挂它），零存货主档的账套照旧：选过用所选、只有一个自动用、没有就拦。
"""
import json
import sys
import unittest
from datetime import date
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "apps" / "pearnly_cowork"))
sys.path.insert(0, str(root / "apps" / "pearnly_core"))

from pearnly_core.express_rules.targets import stock_groups  # noqa: E402
from pearnly_core.express_rules.push.mapper import build_express_payload  # noqa: E402
from pearnly_core.express_rules.push.sales_mapper import build_express_sales_payload  # noqa: E402

ACCOUNT = r"s:\2569\exp69\test"
OTHER = r"s:\2569\exp69\other"
GROUPS = [{"fit": True, "acccod": "SA", "stock_acc": "11-04-01-00", "stock_acc_name": "Synthetic stock A", "used_by": 3},
          {"fit": True, "acccod": "SB", "stock_acc": "11-04-02-00", "stock_acc_name": "Synthetic stock B", "used_by": 0},
          {"fit": False, "acccod": "SX", "stock_acc": "52-00-00-00"}]
TODAY = date(2026, 9, 28)
STOCKED = {"stock_master_count": 5, "stcrd_lines": 10, "stcrd_lines_moving_stock": 0}
EMPTY = {"stock_master_count": 0, "stcrd_lines": 0, "stcrd_lines_moving_stock": 0}


class ChoiceTest(unittest.TestCase):
    def test_each_account_set_keeps_its_own_choice(self):
        value = stock_groups.choose(None, ACCOUNT, "SA", GROUPS, actor="a@example.com", at="2026-09-28T00:00:00")
        value = stock_groups.choose(json.dumps(value), OTHER, "SB", GROUPS, actor="b@example.com", at="t")
        raw = json.dumps(value)
        self.assertEqual((stock_groups.chosen(raw, ACCOUNT), stock_groups.chosen(raw, OTHER)), ("SA", "SB"))
        self.assertEqual(value[ACCOUNT]["chosen_by"], "a@example.com")
        self.assertEqual(stock_groups.chosen(raw, r"s:\none"), "")

    def test_only_a_group_reported_fit_for_this_account_can_be_chosen(self):
        for code, groups in (("SX", GROUPS), ("ZZ", GROUPS), ("", GROUPS), ("SA", None), ("SA", [])):
            self.assertIsNone(stock_groups.choose(None, ACCOUNT, code, groups, actor="a", at="t"))

    def test_view_says_when_the_candidates_are_not_known_yet(self):
        view = stock_groups.view(None, ACCOUNT, GROUPS)
        self.assertEqual((view["known"], view["chosen"]), (True, None))
        self.assertEqual([g["acccod"] for g in view["groups"]], ["SA", "SB"])
        self.assertEqual(view["groups"][0]["label"], "SA · 11-04-01-00 · Synthetic stock A")
        self.assertEqual(view["groups"][0]["used_by"], 3)
        unknown = stock_groups.view(None, ACCOUNT, None)
        self.assertEqual((unknown["known"], unknown["groups"]), (False, []))

    def test_bad_stored_values_count_as_not_chosen(self):
        for raw in ("not json", "[1]", json.dumps({ACCOUNT: "SA"}), json.dumps({ACCOUNT: {"acccod": " "}})):
            self.assertEqual(stock_groups.chosen(raw, ACCOUNT), "")


def _item(code=""):
    return {"name": "Synthetic item", "qty": "1", "subtotal": "100.00", **({"code": code} if code else {})}


def _purchase(config, kind="stock", code=""):
    fields = {"seller_name": "Synthetic Supplier", "seller_tax": "0105550000001", "subtotal": "100.00", "vat": "7.00",
              "invoice_number": "SYN-1", "posting_item_type_manual": "goods", "items": [_item(code)]}
    history = {"id": "h1", "invoice_date": "2026-09-28", "invoice_no": "SYN-1", "total_amount": "107.00", "fields": fields}
    return build_express_payload(history, config={"account_set": "TEST", "fallback_acc": "11-04-02-00",
                                                  "vat_input_acc": "11-05-04-01", "ap_acc": "21-02-01-00", **config},
                                 posting_kind=kind, today=TODAY)


def _sale(config, kind="stock", code=""):
    fields = {"buyer_name": "Synthetic Buyer", "buyer_tax": "0105550000002", "subtotal": "100.00", "vat": "7.00",
              "invoice_number": "SYN-2", "items": [_item(code)]}
    history = {"id": "h2", "invoice_date": "2026-09-28", "invoice_no": "SYN-2", "total_amount": "107.00", "fields": fields}
    return build_express_sales_payload(history, config={"account_set": "TEST", "revenue_acc": "41-01-00-00",
                                                        "vat_output_acc": "11-05-04-02", "ar_acc": "11-02-01-00", **config},
                                       posting_kind=kind, today=TODAY)


class PayloadTest(unittest.TestCase):
    """推送服务把这份账套选过的组放进 config.stock_acccod；两个映射器照它带给小助手。"""

    def test_a_chosen_group_reaches_the_companion_even_when_the_account_has_stock_items(self):
        for build in (_purchase, _sale):
            result = build({"catalog_fingerprint": STOCKED, "reported_stock_acc_groups": GROUPS, "stock_acccod": "SB"})
            self.assertTrue(result.ok, result.reason)
            self.assertEqual(result.payload["stock_acccod"], "SB")

    def test_an_account_with_stock_items_decides_the_group_for_a_new_item_from_the_fit_list(self):
        """账套里已有库存品、这张单的库存行没带物料码（CC 桌面版会新建）：同零库存品账套一份合格名单定组，不再交 CC 按众数选
        （11-16 真机 H5：众数组 ST01 是办公设备，写到一半才拦）。两个合格的开始前问一次，一个合格的自动用它。"""
        for build in (_purchase, _sale):
            self.assertEqual(build({"catalog_fingerprint": STOCKED, "reported_stock_acc_groups": GROUPS}).reason,
                             "stock_acc_group_required")
            one = build({"catalog_fingerprint": STOCKED, "reported_stock_acc_groups": [GROUPS[1], GROUPS[2]]})
            self.assertTrue(one.ok, one.reason)
            self.assertEqual(one.payload["stock_acccod"], "SB")

    def test_an_account_with_stock_items_goes_on_as_before_when_there_is_nothing_to_decide(self):
        """库存行都带了物料码（不新建）、心跳没报组、一个合格的都没有：不另判、不拦，照旧交给 CC 桌面版（它按品名找到现成的品照写）。"""
        for build in (_purchase, _sale):
            for config in ({"reported_stock_acc_groups": GROUPS}, {}, {"reported_stock_acc_groups": GROUPS[2:]}):
                coded = config if config.get("reported_stock_acc_groups") is GROUPS else None
                result = build({"catalog_fingerprint": STOCKED, **config}, code="P001" if coded else "")
                self.assertTrue(result.ok, result.reason)
                self.assertNotIn("stock_acccod", result.payload)

    def test_an_account_without_stock_items_uses_the_choice_or_stops(self):
        for build in (_purchase, _sale):
            chosen = build({"catalog_fingerprint": EMPTY, "reported_stock_acc_groups": GROUPS, "stock_acccod": "SA"})
            self.assertEqual(chosen.payload["stock_acccod"], "SA")
            self.assertEqual(build({"catalog_fingerprint": EMPTY, "reported_stock_acc_groups": GROUPS}).reason,
                             "stock_acc_group_required")
            self.assertEqual(build({"catalog_fingerprint": EMPTY, "reported_stock_acc_groups": GROUPS[2:]}).reason,
                             "stock_acc_group_missing")

    def test_service_documents_carry_no_group(self):
        for build in (_purchase, _sale):
            result = build({"catalog_fingerprint": STOCKED, "stock_acccod": "SA"}, kind="service")
            self.assertTrue(result.ok, result.reason)
            self.assertNotIn("stock_acccod", result.payload)


    def test_a_mixed_purchase_whose_first_line_is_a_fee_still_carries_the_group(self):
        """混行（第一行运费不入库、第二行入库，ERP 按行选，11-06）：按「有没有库存行」带组，不按第一行；载荷盖混行版本。"""
        fields = {"seller_name": "Synthetic Supplier", "seller_tax": "0105550000001", "subtotal": "100.00", "vat": "7.00",
                  "invoice_number": "SYN-3", "posting_item_type_manual": "goods",
                  "items": [{"name": "Freight", "qty": "1", "subtotal": "40.00", "posting_kind": "service"},
                            {"name": "Synthetic item", "qty": "1", "subtotal": "60.00", "posting_kind": "stock"}]}
        history = {"id": "h3", "invoice_date": "2026-09-28", "invoice_no": "SYN-3", "total_amount": "107.00", "fields": fields}
        result = build_express_payload(history, config={"account_set": "TEST", "fallback_acc": "11-04-02-00",
                                                        "vat_input_acc": "11-05-04-01", "ap_acc": "21-02-01-00",
                                                        "catalog_fingerprint": EMPTY, "reported_stock_acc_groups": GROUPS,
                                                        "stock_acccod": "SA"}, today=TODAY)
        self.assertTrue(result.ok, result.reason)
        self.assertEqual([item["item_mode"] for item in result.payload["items"]], ["non_stock_item", "stock_item"])
        self.assertEqual(result.payload["stock_acccod"], "SA")
        self.assertEqual(result.payload["payload_version"], 19)

if __name__ == "__main__":
    unittest.main()
