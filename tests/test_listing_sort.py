# -*- coding: utf-8 -*-
"""列表列头排序的判据（`pearnly_erp_portal.access.listing`）。

纯函数、不连站点：这一层只有"白名单 + 方向 + 排序键"三件事，全部可以在本机跑。
真实列表还要靠浏览器验收（页面 → 接口 → 白名单这条链），这里只钉住容易回归的判据。
"""
import datetime
import sys
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "pearnly_erp_portal"))

from pearnly_erp_portal.access import listing  # noqa: E402

FIELDS = {"name": "product_name", "qty": "qty_on_hand", "at": "creation"}


def rows(*triples):
    return [{"product_name": name, "qty_on_hand": qty, "creation": at} for name, qty, at in triples]


class ListingTests(unittest.TestCase):
    def test_order_by_only_trusts_the_whitelist(self):
        self.assertEqual("product_name asc", listing.order_by("name", "asc", FIELDS, "name asc"))
        self.assertEqual("qty_on_hand desc", listing.order_by("qty", "DESC", FIELDS, "name asc"))
        # 认不出来的字段一律回落到默认顺序 —— 绝不把用户输入拼进 SQL。
        self.assertEqual("creation desc",
                         listing.order_by("1; drop table x", "asc", FIELDS, "creation desc"))
        self.assertEqual("creation desc", listing.order_by(None, "desc", FIELDS, "creation desc"))
        # 方向只认 asc / desc。
        self.assertEqual("product_name asc", listing.order_by("name", "sideways", FIELDS, "x"))

    def test_sort_rows_asc_desc_and_reset(self):
        data = rows(("乙", 2, "2026-09-02"), ("甲", 10, "2026-09-01"))
        self.assertEqual([2, 10],
                         [r["qty_on_hand"] for r in listing.sort_rows(data, "qty", "asc", FIELDS)])
        self.assertEqual([10, 2],
                         [r["qty_on_hand"] for r in listing.sort_rows(data, "qty", "desc", FIELDS)])
        # 白名单外的字段（或没给）就是原样，等于"取消排序"。
        self.assertEqual(data, listing.sort_rows(data, None, None, FIELDS))
        self.assertEqual(data, listing.sort_rows(data, "nope", "asc", FIELDS))

    def test_empties_go_last_both_ways(self):
        data = rows(("空的", None, ""), ("有货", 3, "2026-09-01"))
        asc = listing.sort_rows(data, "qty", "asc", FIELDS)
        desc = listing.sort_rows(data, "qty", "desc", FIELDS)
        self.assertEqual(["有货", "空的"], [r["product_name"] for r in asc])
        self.assertEqual(["有货", "空的"], [r["product_name"] for r in desc])

    def test_mixed_types_do_not_blow_up(self):
        """销售列表按「单据日期」排就是这个形状：单据那边是 date，小票那边是字符串。

        以前直接 `sort` 会抛 `TypeError: '<' not supported between instances of 'str' and
        'datetime.date'`（真实 500，探针在预览站点上撞到过）。
        """
        data = [{"doc_date": "2026-09-03"}, {"doc_date": datetime.date(2026, 9, 1)},
                {"doc_date": "2026-09-02"}, {"doc_date": None}]
        fields = {"doc_date": "doc_date"}
        asc = listing.sort_rows(data, "doc_date", "asc", fields)
        desc = listing.sort_rows(data, "doc_date", "desc", fields)
        self.assertEqual(["2026-09-01", "2026-09-02", "2026-09-03"],
                         [str(r["doc_date"]) for r in asc][:3])
        self.assertEqual(["2026-09-03", "2026-09-02", "2026-09-01"],
                         [str(r["doc_date"]) for r in desc][:3])
        # 空值那一行两个方向都在最后。
        self.assertIsNone(asc[-1]["doc_date"])
        self.assertIsNone(desc[-1]["doc_date"])

    def test_numbers_and_decimals_sort_by_value(self):
        data = [{"qty_on_hand": 9}, {"qty_on_hand": Decimal("10.5")}, {"qty_on_hand": 2}]
        self.assertEqual([2, 9, Decimal("10.5")],
                         [r["qty_on_hand"] for r in listing.sort_rows(data, "qty", "asc", FIELDS)])


if __name__ == "__main__":
    unittest.main()
