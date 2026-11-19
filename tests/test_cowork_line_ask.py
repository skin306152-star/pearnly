"""Cowork LINE 出卡前把题问完（`line.ask`，用户 11-19「所有问题问完 … 然后保存编辑或者取消，一气呵成」；评估
docs/work/2026-11-19-line-ask-all-before-card-评估.md 第 1、3、4、7、10 节）。

真跑编排（`line.ask`）、按行追问（`line.items`）与共用出题器（`pearnly_core.write_precheck.line_ask`）；判定换成替身（同
tests/test_write_precheck_line_ask.py：``contracts.merge`` 合卡、记住的答案下次变成已定），卡片、作废、LINE 发送换成记录。

- 「仅识别」、没有题的票直接出卡；有题的问完才出卡，卡在最后、带答过的结果；不再「卡片 + 追问一起发」；
- 账套、存货科目组、Express 对应项（对方档打字搜）都在 LINE 问完才出卡；推荐项排第一，但没点不往下走；
- 先问按行追问（新物料 / 仓），再问判定剩下的；答完一律出卡（不回「已保存」）；
- 提问里取消 = 卡片「取消」（`line.save.drop`），回「已取消」、接着问下一张；
- 答题中来新票排在后面，答案只记给当前这一张；会话过期不出卡；判定出错照出卡。
"""
import contextlib
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "apps" / "pearnly_cowork"))
sys.path.insert(0, str(root / "apps" / "pearnly_core"))

from pearnly_core.write_precheck import contracts as c  # noqa: E402
from pearnly_core.write_precheck import line_ask  # noqa: E402
from pearnly_cowork.line import ask, cards, flow, handlers, items, save  # noqa: E402

TEXTS = {"guide.q.payment": "Payment", "guide.q.party": "Counterparty", "guide.q.current_book": "Account set",
         "guide.option.payment.cash": "Cash", "guide.option.payment.credit": "Credit"}
BOOK = {"kind": "current_book", "subject": "K1|K2", "label": "",
        "options": [{"code": "K1", "name": "69EXP · TEST"}, {"code": "K2", "name": "69EXP · TEST2"}]}
GROUP = {"kind": "stock_group", "subject": "s:\\69exp\\test", "label": "Inventory account group",
         "options": [{"code": "1", "name": "1 · 1141 · สินค้า"}, {"code": "2", "name": "2 · 1142 · วัตถุดิบ"}]}
PARTY = {"kind": "party", "subject": "purchase|0105550000001", "label": "ABC", "search": "parties",
         "value": "__new__", "value_label": "New", "source": c.RULE}
PAY = {"kind": "payment", "subject": "purchase|0105550000001", "label": "ABC", "value": "credit", "source": c.RULE,
       "options": [{"code": "cash"}, {"code": "credit"}]}


class Backend:
    """替身判定（出题器的 backend）：每张票的项按 ``docs`` 给；记住的答案下次取卡时是已定。"""

    def __init__(self, docs):
        self.docs, self.memory, self.records, self.serial = docs, {}, [], 0
        self.parties = [{"code": "S001", "name": "ABC Co"}, {"code": "S002", "name": "ABD Co"}]

    def card(self, company, doc_ids):
        merged = []
        for doc_id in doc_ids:
            rows = []
            for raw in self.docs.get(doc_id) or ():
                found = self.memory.get((raw["kind"], raw["subject"]))
                rows.append({**raw, "value": found, "source": c.REMEMBERED} if found else dict(raw))
            merged.append({"doc_id": doc_id, "items": rows})
        return c.merge(merged)

    def record(self, company, doc_ids, answers):
        self.records.append((company, list(doc_ids), list(answers)))
        expanded, errors = c.expand(self.card(company, doc_ids), answers)
        if errors:
            return {"errors": errors}
        for rows in expanded.values():
            for row in rows:
                self.memory[(row["kind"], row["subject"])] = row["value"]
        return self.card(company, doc_ids)

    def search(self, company, doc_id, kind, txt, start, page_length):
        rows = [row for row in self.parties if txt.lower() in (row["code"] + row["name"]).lower()]
        return {"options": rows[start:start + page_length], "more": False}

    def allowed(self, user, company):
        return True

    def user(self):
        return "a@x"

    def text(self, key):
        return TEXTS.get(key, key)

    def token(self):
        self.serial += 1
        return f"t{self.serial}"


class LineAskTest(unittest.TestCase):
    def setUp(self):
        self.store, self.sent, self.dropped, self.saved = {}, [], [], []
        self.backend = Backend({})
        self.rows = {}  # 按行追问：每张票推送侧给的行
        self.company = "CO-1"
        store = self.store

        def set_session(user, state, payload, ttl_minutes=None):
            store[user] = {"state": state, "payload": json.loads(json.dumps(payload))}

        side = mock.Mock(rows=lambda history: self.rows.get(history, []), title=lambda history: "ABC · " + history,
                         save=lambda history, answers: self.saved.append((history, answers)), search=lambda *a: [])
        patches = [
            mock.patch.object(flow, "locked", lambda user: contextlib.nullcontext()),
            mock.patch.object(flow.line_session, "get_session", side_effect=lambda user: store.get(user)),
            mock.patch.object(flow.line_session, "set_session", side_effect=set_session),
            mock.patch.object(ask, "_", side_effect=lambda text, *a, **k: text),
            mock.patch.object(items, "_", side_effect=lambda text, *a, **k: text),
            mock.patch.object(items, "_side", side_effect=lambda name: getattr(side, name)),
            mock.patch.object(line_ask, "_default", return_value=self.backend),
            mock.patch("pearnly_cowork.assistant.precheck_source.line_ticket",
                       side_effect=lambda name: {"company": self.company, "heading": "ABC · " + name}),
            mock.patch.object(cards, "previews", side_effect=lambda records, pending, answered=None: [
                {"type": "flex", "card": name, "answered": list((answered or {}).get(name) or ())} for name in records]),
            mock.patch.object(save, "drop", side_effect=self.dropped.append),
            mock.patch.object(ask.line, "reply_or_push", side_effect=lambda to, messages, token="": self.sent.append(messages)),
            mock.patch.object(ask.line, "push", side_effect=lambda to, messages: self.sent.append(messages)),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    # ── 帮手 ──

    def arrive(self, *records, push=True):
        messages = ask.arrive("U1", list(records), {"direction": "purchase", "target": "Express", "mode": "", "push": push})
        ask.send("U1", messages, "tok")
        return messages

    def last(self):
        return self.sent[-1][-1]

    def labels(self, message=None):
        return [row["action"]["label"] for row in (message or self.last())["quickReply"]["items"]]

    def tap(self, label, message=None):
        rows = (message or self.last())["quickReply"]["items"]
        ask.step("U1", next(row["action"]["data"] for row in rows if row["action"]["label"] == label), "tok")

    def type(self, words):
        return ask.text("U1", words, "tok")

    def payload(self):
        return (self.store.get("U1") or {}).get("payload") or {}

    def cards_sent(self):
        return [message for batch in self.sent for message in batch if message.get("type") == "flex"]

    # ── 出卡的时机 ──

    def test_recognize_only_cards_go_straight_out_without_a_session(self):
        self.backend.docs = {"H1": [BOOK]}
        messages = self.arrive("H1", "H2", push=False)
        self.assertEqual([m["card"] for m in messages], ["H1", "H2"])
        self.assertEqual(self.store, {})
        self.assertEqual(self.backend.records, [])

    def test_a_document_without_questions_gets_its_card_straight_away(self):
        messages = self.arrive("H1")
        self.assertEqual(messages, [{"type": "flex", "card": "H1", "answered": []}])
        self.assertIsNone(self.payload()["tickets"]["current"])

    def test_questions_come_first_and_the_card_last_with_the_answers(self):
        self.backend.docs = {"H1": [PAY]}
        messages = self.arrive("H1")
        self.assertEqual(len(messages), 1)  # 只有题，卡还没出（不再「卡片 + 追问一起发」）
        self.assertEqual(messages[0]["text"].splitlines(), ["ABC · H1", "Payment", "ABC"])
        self.assertEqual(self.labels(), ["Credit", "Cash", "Cancel"])  # 推荐项排第一
        self.tap("Cash")
        self.assertEqual(self.sent[-1], [{"type": "flex", "card": "H1",
                                          "answered": [{"label": "Payment · ABC", "value": "Cash"}]}])
        self.assertEqual(self.backend.records[-1], (self.company, ["H1"], [{"key": c.item_key("payment", PAY["subject"]),
                                                                         "value": "cash"}]))

    def test_account_set_stock_group_and_counterparty_are_asked_in_line_before_the_card(self):
        self.backend.docs = {"H1": [BOOK, GROUP, PARTY]}
        self.arrive("H1")
        self.assertIn("Account set", self.last()["text"])
        self.assertEqual(self.labels(), ["69EXP · TEST", "69EXP · TEST2", "Cancel"])  # 「做不做」类不预选
        self.tap("69EXP · TEST2")
        self.assertEqual(self.labels(), ["New", "Cancel"])  # 对方档：预选（新建）作第一个按钮，可打字找
        self.assertTrue(self.type("abd"))
        self.assertIn("1. S002 ABD Co", self.last()["text"])
        self.tap("1")
        self.assertIn("Inventory account group", self.last()["text"])
        self.assertEqual(self.labels(), ["1 · 1141 · สินค้า", "2 · 1142 · วัตถุดิบ", "Cancel"])
        self.assertEqual(self.cards_sent(), [])
        self.tap("2 · 1142 · วัตถุดิบ")
        card = self.cards_sent()
        self.assertEqual(len(card), 1)
        self.assertEqual(card[0]["answered"], [{"label": "Account set", "value": "69EXP · TEST2"},
                                               {"label": "Counterparty · ABC", "value": "S002 ABD Co"},
                                               {"label": "Inventory account group", "value": "2 · 1142 · วัตถุดิบ"}])
        self.assertEqual([row[2][0]["value"] for row in self.backend.records], ["K2", "S002", "2"])

    def test_a_recommendation_that_was_not_tapped_does_not_move_on(self):
        self.backend.docs = {"H1": [PAY]}
        self.arrive("H1")
        self.assertTrue(self.type("whatever"))  # 打别的字：回没找到、重问这一题
        self.assertEqual(self.sent[-1][0]["text"], "No match for “whatever”")
        question = self.last()
        ask.step("U1", "a=precheck&b=old&n=1&q=x&k=0", "tok")  # 旧按钮：无效
        self.assertEqual(self.last()["text"], "This option is no longer current")
        self.assertEqual((self.backend.records, self.cards_sent()), ([], []))
        self.tap("Credit", question)  # 点了推荐项才算
        self.assertEqual(len(self.cards_sent()), 1)

    # ── 取消、排队、过期、出错 ──

    def test_cancel_drops_the_document_like_the_card_cancel_and_moves_to_the_next(self):
        self.backend.docs = {"H1": [PAY], "H2": [{**PAY, "subject": "purchase|2"}]}
        self.arrive("H1")
        self.arrive("H2")  # 正在问 H1：H2 排在后面，什么都不发
        self.assertEqual(self.sent[-1][0]["text"].splitlines()[0], "ABC · H1")
        self.tap("Cancel")
        self.assertEqual(self.dropped, ["H1"])
        self.assertEqual(self.sent[-1][0], {"type": "text", "text": "Cancelled"})
        self.assertEqual(self.last()["text"].splitlines()[0], "ABC · H2")  # 接着问排着的下一张
        self.assertEqual((self.backend.records, self.cards_sent()), ([], []))

    def test_a_new_document_while_answering_waits_and_answers_stay_on_their_own_document(self):
        self.backend.docs = {"H1": [PAY], "H2": [GROUP]}
        self.arrive("H1")
        self.assertEqual(self.arrive("H2"), [])
        self.tap("Cash")
        batch = self.sent[-1]
        self.assertEqual(batch[0]["card"], "H1")  # 先出 H1 的卡
        self.assertEqual(batch[-1]["text"].splitlines()[:2], ["ABC · H2", "Inventory account group"])  # 再问 H2
        self.tap("1 · 1141 · สินค้า")
        self.assertEqual(self.sent[-1][0]["card"], "H2")
        self.assertEqual([row[1] for row in self.backend.records], [["H1"], ["H2"]])
        self.assertEqual(self.sent[-1][0]["answered"], [{"label": "Inventory account group", "value": "1 · 1141 · สินค้า"}])

    def test_item_questions_come_first_then_the_rest_then_one_card(self):
        self.rows = {"H1": [{"kind": "item", "subject": "purchase|t|box", "label": "Box"}]}
        self.backend.docs = {"H1": [PAY]}
        self.arrive("H1")
        self.assertEqual(self.last()["text"].splitlines(), ["ABC · H1", "cowork.line.items.new", "1. Box"])
        self.tap("cowork.line.items.al")  # 标签截到 20 字
        self.assertEqual(self.saved, [("H1", [{"kind": "item", "subject": "purchase|t|box", "value": "__new__", "label": "Box"}])])
        self.assertNotIn("Saved", [m.get("text") for m in self.sent[-1]])  # 不再只回「已保存」
        self.assertIn("Payment", self.last()["text"])  # 接着问判定剩下的
        self.tap("Credit")
        self.assertEqual(self.cards_sent()[0]["answered"], [{"label": "Box", "value": "cowork.line.items.newOne"},
                                                            {"label": "Payment · ABC", "value": "Credit"}])

    def test_cancel_in_the_item_question_drops_the_document_too(self):
        self.rows = {"H1": [{"kind": "item", "subject": "purchase|t|box", "label": "Box"}]}
        self.arrive("H1")
        self.tap("Cancel")
        self.assertEqual((self.dropped, self.sent[-1]), (["H1"], [{"type": "text", "text": "Cancelled"}]))
        self.assertEqual(self.cards_sent(), [])

    def test_an_expired_session_sends_no_card(self):
        self.backend.docs = {"H1": [PAY]}
        self.arrive("H1")
        question = self.last()
        self.store.clear()  # 30 分钟过了
        self.tap("Cash", question)
        self.assertEqual(self.sent[-1], [{"type": "text", "text": "This option is no longer current"}])
        self.assertEqual((self.cards_sent(), self.backend.records, self.store), ([], [], {}))

    def test_a_failing_precheck_or_an_unattributed_company_still_sends_the_card(self):
        self.backend.docs = {"H1": [PAY], "H2": [PAY]}
        with mock.patch.object(ask.frappe, "db", mock.Mock(), create=True), \
                mock.patch.object(ask.frappe, "clear_messages", create=True), \
                mock.patch.object(ask.frappe, "log_error", create=True) as log, \
                mock.patch.object(ask.frappe, "get_traceback", return_value="t", create=True), \
                mock.patch.object(self.backend, "card", side_effect=RuntimeError("boom")):
            self.assertEqual([m["card"] for m in self.arrive("H1")], ["H1"])
        log.assert_called_once()
        self.company = ""  # 归不上公司：不问
        self.assertEqual([m["card"] for m in self.arrive("H2")], ["H2"])

    def test_more_than_five_messages_reply_five_and_push_the_rest(self):
        self.arrive(*[f"H{index}" for index in range(7)])
        self.assertEqual([len(batch) for batch in self.sent], [5, 2])

    # ── 入口与依赖 ──

    def test_the_question_buttons_and_typed_words_reach_the_asker(self):
        self.assertEqual(ask.STEP, "a=" + line_ask.ACTION)  # 产品地图按这个字面量认入口
        self.assertTrue(ask.is_step("a=precheck&b=t1&n=1&q=x&k=0"))
        self.assertTrue(ask.is_step("a=cowork_item&v=each"))
        self.assertFalse(ask.is_step("a=cowork_save&n=H1"))
        with mock.patch.object(handlers, "_", side_effect=lambda text, *a, **k: text), \
                mock.patch.object(handlers.binding, "enabled", return_value=True), \
                mock.patch.object(handlers.flow, "allowed", return_value=True), \
                mock.patch.object(handlers, "_as", side_effect=lambda user, work: work()), \
                mock.patch.object(handlers.line_binding, "user_of", return_value="a@x"), \
                mock.patch.object(ask, "step") as step, \
                mock.patch.object(ask, "text", return_value=True) as typed, \
                mock.patch.object(handlers.flow, "search", return_value=False) as account_search:
            for data in ("a=precheck&b=t1&n=1&q=x&k=0", "a=cowork_item&v=each"):
                self.assertTrue(handlers._handle({"type": "postback", "replyToken": "tok", "postback": {"data": data}}, "U1"))
            handlers._handle({"type": "message", "replyToken": "tok", "message": {"type": "text", "text": "abc"}}, "U1")
        self.assertEqual([call.args[1] for call in step.call_args_list], ["a=precheck&b=t1&n=1&q=x&k=0", "a=cowork_item&v=each"])
        account_search.assert_called_once_with("U1", "abc", "tok")  # 只在重新点菜单、停在选账套时才接
        typed.assert_called_once_with("U1", "abc", "tok")

    def test_line_reaches_assistant_only_through_line_ticket(self):
        """架构登记 line → assistant（11-19）只为出卡前的提问：line 域只许导入 `assistant.precheck_source`、只调它的 `line_ticket`。"""
        import ast

        base = root / "apps" / "pearnly_cowork" / "pearnly_cowork" / "line"
        found = set()
        for path in base.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("pearnly_cowork.assistant"):
                    found |= {(node.module, alias.name) for alias in node.names}
                elif isinstance(node, ast.Import):
                    found |= {(alias.name, "") for alias in node.names if alias.name.startswith("pearnly_cowork.assistant")}
                elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "precheck_source":
                    found.add(("precheck_source.", node.attr))
        self.assertEqual(found, {("pearnly_cowork.assistant", "precheck_source"), ("precheck_source.", "line_ticket")})


if __name__ == "__main__":
    unittest.main()
