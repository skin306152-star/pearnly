"""LINE 选择超过 12 项改打字找（用户 2026-10-24），以及等结果时统一显示官方三个点。

Run with Frappe installed. No Site, Redis or network: database reads, LINE requests and saving are replaced.
"""
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pearnly_core import line
from pearnly_cowork.line import flow as cowork_flow
from pearnly_erp_portal.integrations import line_flow

USER = "U" + "a" * 32
OTHER = "U" + "b" * 32


class ErpFind(unittest.TestCase):
    ROWS = [
        ("CO-1", "บริษัท สยามพลาสติก จำกัด", ["CO-1", "สยามพลาสติก", "0105551000001"]),
        ("CO-2", "บริษัท สยามพลาสติก จำกัด (00001)", ["CO-2", "", "0105551000001"]),
        ("CO-3", "ห้างหุ้นส่วนจำกัด พลาสติกไทย", ["CO-3", "", "0105551000003"]),
        ("CO-4", "Siam Steel Co., Ltd.", ["CO-4", "SSC", "0105551000004"]),
    ]

    def find(self, text):
        with patch.object(line_flow, "_searchable", return_value=self.ROWS):
            return line_flow.find("company", {}, "u@example.com", text)

    def test_an_exact_code_selects_without_listing(self):
        self.assertEqual(self.find("CO-3"), ("CO-3", []))
        self.assertEqual(self.find("ssc"), ("CO-4", []))
        self.assertEqual(self.find("0105551000004"), ("CO-4", []))

    def test_a_tax_number_shared_by_branches_lists_the_branches(self):
        self.assertEqual(self.find("0105551000001"), (None, [self.ROWS[0][:2], self.ROWS[1][:2]]))

    def test_a_name_matches_by_contained_words_with_closest_first(self):
        exact, hits = self.find("พลาสติก")
        self.assertIsNone(exact)
        self.assertEqual([key for key, _label in hits], ["CO-3", "CO-1", "CO-2"])
        self.assertEqual([key for key, _label in self.find("siam steel")[1]], ["CO-4"])
        self.assertEqual(self.find("ไม่มีชื่อนี้"), (None, []))

    def test_a_partial_code_never_selects(self):
        self.assertEqual(self.find("CO-")[0], None)


class ErpNoMatch(unittest.TestCase):
    def test_no_match_asks_the_step_again_with_its_own_buttons(self):
        # 验收 10-24：没找到后只剩「取消」，最近用过的供应商按钮没了；改为照这一步原来的问法与按钮再问
        payload = {"direction": "purchase", "entry": "upload", "company": "CO-1"}
        with patch.object(line_flow, "choices", return_value=payload), \
                patch.object(line_flow, "_pending", return_value="party"), \
                patch.object(line_flow, "find", return_value=(None, [])), \
                patch.object(line_flow, "_", side_effect=lambda text: text), \
                patch.object(line_flow, "_next") as again:
            self.assertTrue(line_flow.search(USER, "u@example.com", "13", "r"))
        again.assert_called_once_with(USER, "u@example.com", payload, "r", note="No match for “13”")


class CoworkStockGroupSearch(unittest.TestCase):
    GROUPS = [{"acccod": "1150-01", "label": "สินค้าสำเร็จรูป"}, {"acccod": "1150-02", "label": "วัตถุดิบ"},
              {"acccod": "1150-03", "label": "สินค้าระหว่างทาง"}]

    def setUp(self):
        service = SimpleNamespace(stock_acc_group=Mock(return_value={"groups": self.GROUPS}))
        self.enterContext(patch.dict("sys.modules", {"pearnly_cowork.connections.service": service}))
        self.enterContext(patch("pearnly_cowork.connections.service", service, create=True))
        self.ask = self.enterContext(patch.object(cowork_flow, "_ask"))
        self.chosen = self.enterContext(patch.object(cowork_flow, "_choose_stock_group"))
        self.next = self.enterContext(patch.object(cowork_flow, "_next"))
        self.enterContext(patch.object(cowork_flow, "_save"))
        self.enterContext(patch.object(cowork_flow, "_", side_effect=lambda text: text))
        self.payload = {"connection": "C1", "account_set_key": "K1"}

    def test_an_exact_account_code_is_chosen(self):
        self.assertTrue(cowork_flow._search_stock_group(USER, self.payload, "1150-02", "r"))
        self.chosen.assert_called_once_with(self.payload, "1150-02")
        self.next.assert_called_once()

    def test_a_name_lists_numbered_matches(self):
        self.assertTrue(cowork_flow._search_stock_group(USER, self.payload, "สินค้า", "r"))
        self.assertEqual(self.payload["offered"][cowork_flow.STOCK_GROUP], ["1150-01", "1150-03"])
        self.assertEqual(len(self.ask.call_args.args[2]), 2)
        self.chosen.assert_not_called()

    def test_no_match_asks_again(self):
        self.assertTrue(cowork_flow._search_stock_group(USER, self.payload, "xyz", "r"))
        self.assertEqual(self.ask.call_args.args[2], [])


class WaitingBubble(unittest.TestCase):
    """官方三个点：处理超过 `FEEDBACK_DELAY` 才发，回复一定排在它后面；立即能回的不发。"""

    def setUp(self):
        self.calls, self.lock = [], threading.Lock()

        def post(url, **kwargs):
            if url.endswith("/chat/loading/start"):
                time.sleep(0.2)  # LINE 来回
            with self.lock:
                self.calls.append(url.rsplit("/", 2)[-2] + "/" + url.rsplit("/", 1)[-1])
            return Mock(status_code=200, text="", headers={})

        self.enterContext(patch.object(line.requests, "post", side_effect=post))
        self.enterContext(patch.object(line, "can_send", return_value=True))
        self.enterContext(patch.object(line, "mirroring", return_value=False))
        self.enterContext(patch.object(line, "_headers", return_value={}))
        self.enterContext(patch.object(line, "_count"))
        self.enterContext(patch.object(line, "FEEDBACK_DELAY", 0.1))

    def run_event(self, handler, kind="message", source_type="user"):
        event = {"type": kind, "source": {"type": source_type, "userId": USER}, "replyToken": "r"}
        with patch.object(line.frappe, "get_hooks", return_value=["h"]), \
                patch.object(line.frappe, "get_attr", return_value=handler):
            return line._dispatch_with_feedback(event)

    def test_an_instant_reply_shows_no_bubble(self):
        self.assertTrue(self.run_event(lambda event: line.reply("r", [{"type": "text", "text": "ok"}])))
        self.assertEqual(self.calls, ["message/reply"])

    def test_a_slow_reply_comes_after_the_bubble(self):
        def slow(event):
            time.sleep(0.15)  # 计时已到、三个点请求还在路上
            return line.reply("r", [{"type": "text", "text": "ok"}])

        self.assertTrue(self.run_event(slow))
        self.assertEqual(self.calls, ["loading/start", "message/reply"])

    def test_pushing_to_someone_else_does_not_cancel_the_bubble(self):
        def notify_other(event):
            line.push(OTHER, [{"type": "text", "text": "fyi"}])
            time.sleep(0.4)
            return True

        self.run_event(notify_other)
        self.assertEqual(self.calls, ["message/push", "loading/start"])

    def test_groups_and_follow_events_get_no_bubble(self):
        self.run_event(lambda event: time.sleep(0.3) or True, source_type="group")
        self.run_event(lambda event: time.sleep(0.3) or True, kind="follow")
        self.assertEqual(self.calls, [])
        self.assertIsNone(line.frappe.local.pearnly_line_feedback)


if __name__ == "__main__":
    unittest.main()
