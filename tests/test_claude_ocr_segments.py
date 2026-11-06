"""Claude 识别长文件分段读（2026-11-03，``claude_ocr.segments``）：一份文件多少页就读多少页，不截前 8 页。
锁住三条：一定读完（每段至少往后推进一页，段数到上限把剩下的页标出、不静默丢页）；段界的单只留一份（以后一段完整的为准）；
不超过一段的文件与原来一次调用完全相同。纯计算，不调接口、不花钱。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/pearnly_core"))
from pearnly_core.claude_ocr import engine, segments  # noqa: E402


def doc(page, number, total="107.00", lines=("x",)):
    return {"page": page, "invoice_number": number, "total": total, "subtotal": "100.00" if total else None,
            "vat": "7.00" if total else None, "seller_tax": "0105546015062",
            "lines": [{"description": d, "amount": "1.00"} for d in lines]}


def outcome(*docs, entry="enter", why=(), uncertain=(), usd=0.01):
    return {"read": {"doc_kind": "tax_invoice", "entry": entry, "copy_marking": "none", "documents": list(docs),
                     "uncertain": list(uncertain)},
            "stages": [{"model": engine.HAIKU, "usd": usd, "seconds": 1.0}], "escalated": False, "why": list(why),
            "usd": usd, "seconds": 1.0, "where": "oracle", "settled": [], "why_after": []}


class Reader:
    """按原文件的「单据起始页 → 单号 / 跨几页」模拟模型：每段只报起始页在本段内的单，页号按本段从 1 数。"""

    def __init__(self, starts, long=None):
        self.starts, self.long, self.calls = starts, long or {}, []

    def __call__(self, first, last):
        self.calls.append((first, last))
        docs = [doc(page - first + 1, number) for page, number in sorted(self.starts.items()) if first <= page <= last]
        # 一张单本身超过一段：后面的段第一页上读出同单号的续页（合计印在最后一页）
        for number, (start, end) in self.long.items():
            if start < first <= end:
                docs.insert(0, doc(1, number, total="999.00" if end <= last else None, lines=(f"p{first}",)))
            elif first <= start <= last:
                docs.insert(0, doc(start - first + 1, number, total=None, lines=(f"p{start}",)))
        return outcome(*docs)


class Plan(unittest.TestCase):
    def test_short_files_are_one_call_exactly_as_before(self):
        for total in (1, 5, engine.PAGES_PER_CALL):
            reader = Reader({1: "A"})
            got = segments.read_all(total, reader)
            self.assertEqual(reader.calls, [(1, total)])
            self.assertNotIn("page_total", got)  # 原样返回这一次调用的结果，不加不改

    def test_short_file_result_is_the_same_object(self):
        one = outcome(doc(1, "A"))
        self.assertIs(segments.read_all(3, lambda first, last: one), one)

    def test_twelve_pages_one_invoice_a_page(self):
        reader = Reader({page: f"N{page}" for page in range(1, 13)})
        got = segments.read_all(12, reader)
        self.assertEqual(reader.calls, [(1, 8), (8, 12)])  # 第 8 页那张在第二段重读，只留第二段的
        self.assertEqual([d["invoice_number"] for d in got["read"]["documents"]], [f"N{p}" for p in range(1, 13)])
        self.assertEqual([d["page"] for d in got["read"]["documents"]], list(range(1, 13)))
        self.assertEqual(got["page_total"], 12)
        self.assertEqual(got["pages_unread"], [])

    def test_each_segment_reports_the_pages_really_read(self):
        """代录卡片识别期间一直「0 / 12 页」（2026-11-06）：每段读完报一次真实已读页数（下一段从第 8 页重读 → 前 7 页读完）；
        只有一段的文件中途没有可报的页数；报进度出错不影响读。"""
        reports = []
        reader = Reader({page: f"N{page}" for page in range(1, 25)})
        segments.read_all(24, reader, lambda done, total: reports.append((done, total)))
        self.assertEqual(reader.calls, [(1, 8), (8, 15), (15, 22), (22, 24)])
        self.assertEqual(reports, [(7, 24), (14, 24), (21, 24)])
        reports.clear()
        segments.read_all(None, Reader({1: "N1"}), lambda done, total: reports.append((done, total)))
        self.assertEqual(reports, [])

        def broken(done, total):
            raise RuntimeError("socket down")

        got = segments.read_all(12, Reader({page: f"N{page}" for page in range(1, 13)}), broken)
        self.assertEqual(len(got["read"]["documents"]), 12)

    def test_bjc_shape_eleven_invoices_one_spanning_pages_nine_and_ten(self):
        """BJC 税票 12 页 11 张：第 9、10 页是同一张单的 1/2、2/2，模型按规则只报一张、记在第 9 页。"""
        starts = {page: f"N{page}" for page in (1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12)}
        reader = Reader(starts)
        got = segments.read_all(12, reader)
        docs = got["read"]["documents"]
        self.assertEqual(len(docs), 11)
        self.assertEqual([d["page"] for d in docs], [1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12])
        self.assertEqual(len({d["invoice_number"] for d in docs}), 11)

    def test_invoice_crossing_the_boundary_is_read_whole_in_the_next_segment(self):
        """第 7–9 页一张单：第一段只看到 7、8 两页（不全），下一段从第 7 页起重读，只留下一段那份。"""
        calls = []

        def read(first, last):
            calls.append((first, last))
            if first == 1:
                return outcome(*[doc(p, f"N{p}") for p in range(1, 7)], doc(7, "N7", total=None, lines=("p7", "p8")))
            return outcome(doc(1, "N7", lines=("p7", "p8", "p9")), *[doc(p - first + 1, f"N{p}") for p in range(10, last + 1)])
        got = segments.read_all(14, read)
        self.assertEqual(calls, [(1, 8), (7, 14)])
        docs = got["read"]["documents"]
        self.assertEqual([d["invoice_number"] for d in docs].count("N7"), 1)
        whole = next(d for d in docs if d["invoice_number"] == "N7")
        self.assertEqual((whole["page"], whole["total"], len(whole["lines"])), (7, "107.00", 3))

    def test_one_invoice_longer_than_a_segment_still_moves_forward(self):
        """一张 20 页的单：每段只认出这一张、都从本段第一页开始 → 下一段从末页的下一页开始，续页按单号并入。"""
        reader = Reader({}, long={"BIG": (1, 20)})
        got = segments.read_all(20, reader)
        self.assertEqual(reader.calls, [(1, 8), (9, 16), (17, 20)])
        [big] = got["read"]["documents"]
        self.assertEqual((big["page"], big["invoice_number"], big["total"]), (1, "BIG", "999.00"))
        self.assertEqual([line["description"] for line in big["lines"]], ["p1", "p9", "p17"])

    def test_invoice_with_attachment_pages_then_new_invoices(self):
        reader = Reader({1: "A", 10: "B", 11: "C"})
        got = segments.read_all(11, reader)
        self.assertEqual(reader.calls, [(1, 8), (9, 11)])
        self.assertEqual([(d["page"], d["invoice_number"]) for d in got["read"]["documents"]], [(1, "A"), (10, "B"), (11, "C")])

    def test_pages_without_invoices(self):
        reader = Reader({})
        got = segments.read_all(17, reader)
        self.assertEqual(reader.calls, [(1, 8), (9, 16), (17, 17)])
        self.assertEqual(got["read"]["documents"], [])

    def test_every_segment_moves_forward_and_the_limit_flags_the_rest(self):
        """最坏情况：每段的最后一张单都从本段第 2 页开始（每段只推进一页）。到段数上限停下，最后一段读到的单留下，
        之后的页标出并报原因。"""
        calls = []

        def stubborn(first, last):
            calls.append((first, last))
            return outcome(doc(1, f"N{first}"), doc(2, f"N{first + 1}"))
        got = segments.read_all(50, stubborn)
        self.assertEqual(len(calls), segments.MAX_SEGMENTS)
        self.assertEqual([first for first, _last in calls], list(range(1, segments.MAX_SEGMENTS + 1)))
        last = calls[-1][1]
        self.assertEqual(got["pages_unread"], list(range(last + 1, 51)))
        self.assertTrue(any("not read" in reason for reason in got["why"]))
        self.assertEqual([d["page"] for d in got["read"]["documents"]], list(range(1, segments.MAX_SEGMENTS + 2)))

    def test_model_pages_out_of_range_count_as_the_first_page(self):
        reader_calls = []

        def read(first, last):
            reader_calls.append((first, last))
            return outcome(doc(99 if first == 1 else 1, f"N{first}"))
        got = segments.read_all(10, read)
        self.assertEqual(reader_calls, [(1, 8), (9, 10)])
        self.assertEqual([d["page"] for d in got["read"]["documents"]], [1, 9])

    def test_a_segment_that_reads_nothing_makes_the_whole_file_unread(self):
        def read(first, last):
            return outcome(doc(1, "A")) if first == 1 else {**outcome(), "read": None}
        got = segments.read_all(12, read)
        self.assertIsNone(got["read"])

    def test_total_learned_from_the_first_answer(self):
        """原件在甲骨文：事先不知道几页，第一段的回复带 ``page_total``。"""
        calls = []

        def read(first, last):
            calls.append((first, last))
            return {**outcome(doc(1, f"N{first}")), "page_total": 12, "page_range": [first, last]}
        got = segments.read_all(None, read)
        self.assertEqual(calls, [(1, 8), (9, 12)])
        self.assertEqual(got["page_total"], 12)
        short = segments.read_all(None, lambda first, last: {**outcome(doc(1, "A")), "page_total": 3})
        self.assertEqual(short["page_total"], 3)


class Merge(unittest.TestCase):
    def test_paths_renumbered_costs_summed_dropped_paths_removed(self):
        first = outcome(doc(1, "A"), doc(8, "B"), why=["documents[1].total missing", "documents[0].date missing"],
                        uncertain=["documents[0].seller_tax", "documents[1].vat"], usd=0.02)
        first["escalated"] = True
        second = outcome(doc(1, "B"), doc(3, "C"), uncertain=["documents[1].seller_tax"], usd=0.03)
        got = segments.merge([(1, 8, first), (8, 10, second)], 10)
        self.assertEqual([d["invoice_number"] for d in got["read"]["documents"]], ["A", "B", "C"])
        self.assertEqual(got["read"]["uncertain"], ["documents[0].seller_tax", "documents[2].seller_tax"])
        self.assertEqual(got["why"], ["documents[0].date missing"])  # 指向第一段丢掉的 B 的那条去掉
        self.assertEqual((got["usd"], got["seconds"], got["escalated"]), (0.05, 2.0, True))
        self.assertEqual([stage["pages"] for stage in got["stages"]], [[1, 8], [8, 10]])

    def test_entry_and_kind_from_the_first_segment_that_records(self):
        first = outcome(entry="not_invoice")
        first["read"]["doc_kind"] = "statement"
        got = segments.merge([(1, 8, first), (9, 10, outcome(doc(1, "A")))], 10)
        self.assertEqual((got["read"]["entry"], got["read"]["doc_kind"]), ("enter", "tax_invoice"))
        self.assertEqual(got["read"]["documents"][0]["page"], 9)


if __name__ == "__main__":
    unittest.main()
