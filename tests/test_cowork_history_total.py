"""录入工作台列表的总数：翻页不数、第一页没装满不数、装满了 20 秒内沿用（2026-10-02 m6：每次刷新都数全部可见行，列表 p95 约 2 秒）。
离线：框架用替身。"""
import unittest
from unittest import mock

import frappe

from pearnly_cowork.history import api


class Cache:
    def __init__(self):
        self.values = {}

    def get_value(self, key):
        return self.values.get(key)

    def set_value(self, key, value, expires_in_sec=None):
        self.values[key] = value


class Total(unittest.TestCase):
    def setUp(self):
        self.cache, self.counts = Cache(), []

        def get_list(doctype, filters=None, fields=None, **kwargs):
            if fields and isinstance(fields[0], dict):
                self.counts.append(filters)
                return [{"total": 57}]
            return []

        self.patches = [mock.patch.object(api.frappe, "cache", self.cache),
                        mock.patch.object(api.frappe, "get_list", side_effect=get_list),
                        mock.patch.object(api.frappe, "session", mock.Mock(user="u@example.com"))]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()

    def test_paging_and_short_pages_do_not_count(self):
        self.assertIsNone(api._total({}, 20, 20, [{}] * 20))            # 翻页：沿用第一页的总数
        self.assertEqual(api._total({}, 0, 20, [{}] * 7), 7)             # 第一页没装满：就这几行
        self.assertEqual(self.counts, [])

    def test_a_full_first_page_counts_once_per_user_and_filter(self):
        self.assertEqual(api._total({"staged": 1}, 0, 20, [{}] * 20), 57)
        self.assertEqual(api._total({"staged": 1}, 0, 20, [{}] * 20), 57)    # 20 秒内沿用
        self.assertEqual(len(self.counts), 1)
        self.assertEqual(api._total({"staged": 0}, 0, 20, [{}] * 20), 57)    # 换了筛选就重新数
        self.assertEqual(len(self.counts), 2)
        self.cache.values = {k: 3 for k in self.cache.values}
        self.assertEqual(api._total({"staged": 1}, 0, 20, [{}] * 20), 20)    # 不小于这一页已有的行数


if __name__ == "__main__":
    unittest.main()
