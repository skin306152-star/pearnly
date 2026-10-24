#!/usr/bin/env python3
"""下载响应头的文件名（`pearnly_core.exports.disposition`，纯函数）：中文 / 泰文按 RFC 5987 写、带 ASCII 兜底，
ASCII 名字原样；共用 `after_request` 钩子已登记，三个产品的原生下载都经过它。

ERP 任务管理复测 10-23：导出文件名乱码（框架按 latin-1 原样写进响应头）。离线跑，不要 Frappe。
"""
import ast
import importlib.util
from pathlib import Path
import unittest
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "disposition", ROOT / "apps/pearnly_core/pearnly_core/exports/disposition.py")
disposition = importlib.util.module_from_spec(spec)
spec.loader.exec_module(disposition)


def encoded_name(header):
    return unquote(header.split("filename*=UTF-8''", 1)[1])


def fallback_name(header):
    return header.split('filename="', 1)[1].split('"', 1)[0]


class Header(unittest.TestCase):
    def test_chinese_and_thai_names_are_encoded_with_an_ascii_fallback(self):
        for name, fallback in (("การจัดการงาน-2026-10-24.xlsx", "2026-10-24.xlsx"),
                               ("任务管理-2026-10-24.xlsx", "2026-10-24.xlsx"),
                               ("盘点.xlsx", "download.xlsx"),
                               ("ไทย", "download"),
                               ("LAB ไทย 01.csv", "LAB _ 01.csv")):
            with self.subTest(name=name):
                header = disposition.header("attachment", name)
                self.assertTrue(header.isascii(), header)
                self.assertTrue(header.startswith("attachment; filename=\""), header)
                self.assertEqual(encoded_name(header), name)
                self.assertEqual(fallback_name(header), fallback)

    def test_inline_stays_inline(self):
        self.assertTrue(disposition.header("inline", "ใบกำกับ.pdf").startswith("inline; "))
        self.assertTrue(disposition.header(" inline ", "ใบกำกับ.pdf").startswith("inline; "))

    def test_ascii_names_are_unchanged(self):
        self.assertFalse(disposition.needs_encoding("sales.xlsx"))
        self.assertEqual(disposition.header("attachment", "sales.xlsx"), 'attachment; filename="sales.xlsx"')

    def test_quotes_and_line_breaks_cannot_break_the_header(self):
        header = disposition.header("attachment", 'a"b\\c\r\nd.xlsx')
        self.assertNotIn("\r", header)
        self.assertNotIn("\n", header)
        self.assertEqual(fallback_name(header), "a_b_cd.xlsx")
        self.assertEqual(encoded_name(header), 'a"b\\cd.xlsx')

    def test_no_name_means_no_parameter(self):
        self.assertEqual(disposition.header("attachment", ""), "attachment")


class Hook(unittest.TestCase):
    def test_the_shared_after_request_hook_is_registered(self):
        tree = ast.parse((ROOT / "apps/pearnly_core/pearnly_core/hooks.py").read_text(encoding="utf-8"))
        hooks = [ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                 and any(getattr(target, "id", "") == "after_request" for target in node.targets)]
        self.assertEqual(len(hooks), 1)
        self.assertIn("pearnly_core.exports.service.encode_filename", hooks[0])
        source = (ROOT / "apps/pearnly_core/pearnly_core/exports/service.py").read_text(encoding="utf-8")
        self.assertIn("def encode_filename(response):", source)


if __name__ == "__main__":
    unittest.main()
