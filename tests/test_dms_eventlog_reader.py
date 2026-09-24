"""DMS 使用记录读取器：形状严格性 + 结束条件（零 IO，纯解析）。

样本逐字取自 2026-09-25 在测试租户上的实测响应（含原生缩进与换行）。这里要守住的是
"看不懂就失败"：少一行、多一个 span、模块对不上，都必须抛错，绝不能悄悄当成
"没有新变化"——那正是最难查的错。
"""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_dms"))

from pearnly_dms.client import eventlog_reader as reader
from pearnly_dms.contracts.client_base import DmsClientError

CUSTOMERS = "ลูกค้า"

#: 实测响应片段（两行，含原生缩进与前导 `dt::`）。
PAGE = (
    'dt::\t\t\t\t\t<div data-val="831||2" data-allo="" data-cf="">\r\n'
    '\t\t\t\t\t\t<div class="detaildata">\r\n\t\t\t\t\t\t\t<p>\t\t\t\t\t\t\r\n'
    "\t\t\t\t\t\t\t\t<span>dmstest</span>\r\n"
    "\t\t\t\t\t\t\t\t<span>24/09/2569</span>\r\n"
    "\t\t\t\t\t\t\t\t<span>11:11:59</span>\t\t\t\t\t\t\r\n"
    "\t\t\t\t\t\t\t\t<span>107.155.65.74</span>\r\n"
    "\t\t\t\t\t\t\t\t<span>ลูกค้า</span>\r\n"
    "\t\t\t\t\t\t\t\t<span>แก้ไข</span>\r\n"
    "\t\t\t\t\t\t\t\t<span>1103702587460</span>\r\n"
    "\t\t\t\t\t\t\t</p>\r\n\t\t\t\t\t\t</div>\r\n\t\t\t\t\t</div>\r\n"
    '\t\t\t\t\t<div data-val="826||2" data-allo="" data-cf="">\r\n'
    '\t\t\t\t\t\t<div class="detaildata">\r\n\t\t\t\t\t\t\t<p>\t\t\t\t\t\t\r\n'
    "\t\t\t\t\t\t\t\t<span>dmstest</span>\r\n"
    "\t\t\t\t\t\t\t\t<span>24/09/2569</span>\r\n"
    "\t\t\t\t\t\t\t\t<span>08:41:32</span>\t\t\t\t\t\t\r\n"
    "\t\t\t\t\t\t\t\t<span>107.155.65.74</span>\r\n"
    "\t\t\t\t\t\t\t\t<span>ลูกค้า</span>\r\n"
    "\t\t\t\t\t\t\t\t<span>เพิ่ม</span>\r\n"
    "\t\t\t\t\t\t\t\t<span>1000001205453</span>\r\n"
    "\t\t\t\t\t\t\t</p>\r\n\t\t\t\t\t\t</div>\r\n\t\t\t\t\t</div>\r\n"
)


class FakeClient:
    def __init__(self, body):
        self.body = body
        self.calls = []

    def _post_text(self, path, data):
        self.calls.append((path, dict(data)))
        return self.body


class EventLogReaderTests(unittest.TestCase):
    def test_page_parses_every_native_column(self):
        client = FakeClient(PAGE)
        events, _usage = reader.page(client, CUSTOMERS)
        self.assertEqual([event.seq for event in events], [831, 826])
        first = events[0]
        self.assertEqual(first.account, "dmstest")
        self.assertEqual(first.date, "24/09/2569")
        self.assertEqual(first.time, "11:11:59")
        self.assertEqual(first.ip, "107.155.65.74")
        self.assertEqual(first.module, CUSTOMERS)
        self.assertEqual(first.action, reader.ACTION_EDIT)
        self.assertEqual(first.target, "1103702587460")
        self.assertTrue(first.names_record)
        self.assertEqual(client.calls[0][0], "eventlog/component/showdata.php")
        self.assertEqual(client.calls[0][1]["ftd"], CUSTOMERS + "||2")
        self.assertEqual(client.calls[0][1]["sdtamt"], "200")
        self.assertEqual(client.calls[0][1]["sdtpage"], "1")

    def test_count_request_must_not_carry_paging(self):
        client = FakeClient("dt::230")
        total, _usage = reader.count(client, CUSTOMERS)
        self.assertEqual(total, 230)
        self.assertEqual(set(client.calls[0][1]), {"sd", "ftd"})

    def test_empty_module_counts_as_zero(self):
        self.assertEqual(reader.count(FakeClient("ndt::"), CUSTOMERS)[0], 0)
        self.assertEqual(reader.page(FakeClient("ndt::"), CUSTOMERS)[0], [])

    def test_an_unnamed_or_unknown_action_cannot_name_a_record(self):
        # 空标识、或动作我们不认识（原生里确实有 ตรวจสอบ 这类）→ 点不到具体记录。
        for action, target in ((reader.ACTION_EDIT, ""), (reader.ACTION_ADD, ""),
                               ("ตรวจสอบ", "1103702587460")):
            body = PAGE.replace("<span>แก้ไข</span>", f"<span>{action}</span>", 1).replace(
                "<span>1103702587460</span>", f"<span>{target}</span>", 1)
            events, _usage = reader.page(FakeClient(body), CUSTOMERS)
            self.assertFalse(events[0].names_record, (action, target))

    def test_rejections_and_shapes_fail_loudly(self):
        cases = {
            "refused": "err::",
            "not_a_list": " quelque chose ",
            "missing_span": PAGE.replace("<span>107.155.65.74</span>", "", 1),
            "extra_span": PAGE.replace("<span>แก้ไข</span>", "<span>แก้ไข</span><span>x</span>", 1),
            "unparsed_tail": PAGE + '<div data-val="825||2" data-allo="" data-cf=""><div>',
            "other_module": PAGE.replace("<span>ลูกค้า</span>", "<span>ใบจอง</span>", 1),
            "bad_identity": PAGE.replace('data-val="831||2"', 'data-val="831||1"', 1),
            "span_outside_row": "<span>orphan</span>" + PAGE,
            "empty_cell": PAGE.replace("<span>dmstest</span>", "<span></span>", 1),
            "empty_action": PAGE.replace("<span>แก้ไข</span>", "<span></span>", 1),
        }
        for name, body in cases.items():
            with self.subTest(name=name), self.assertRaises(DmsClientError):
                reader.page(FakeClient(body), CUSTOMERS)
        with self.subTest(name="page_out_of_bounds"), self.assertRaises(DmsClientError):
            reader.page(FakeClient(PAGE), CUSTOMERS, page_number=21)

    def test_count_response_must_be_a_count(self):
        for body in ("dt::", "dt::230 rows", "", "err::"):
            with self.subTest(body=body), self.assertRaises(DmsClientError):
                reader.count(FakeClient(body), CUSTOMERS)

    def test_module_names_are_bounded(self):
        for module in ("", "  x", "x ", "a" * 101, "a||2", "a&b", "a#b", "a?b", None):
            with self.subTest(module=module), self.assertRaises(DmsClientError):
                reader.page(FakeClient(PAGE), module)

    def test_response_byte_budget_is_enforced(self):
        limits = reader.EventLogLimits(max_response_bytes=10)
        with self.assertRaises(DmsClientError):
            reader.page(FakeClient(PAGE), CUSTOMERS, limits=limits)


if __name__ == "__main__":
    unittest.main()
