"""LINE 写前判定出题器（``pearnly_core.write_precheck.line_ask``，评估 docs/work/2026-11-19-line-ask-all-before-card-评估.md 第 3、10 节）。

出卡前在 LINE 里把一张票要人定的项一题一题问完：题目与预选照 ``guide.build``（同 CC 引导式），答案交 ``record`` 按公司记住。
替身判定（``contracts.merge`` 合卡、记住的答案下次变成已定），不连库、不导入 Frappe。

    python3 tests/test_write_precheck_line_ask.py
"""
import importlib
import sys
import types
import unittest
from unittest import mock
from pathlib import Path
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))

from pearnly_core.write_precheck import contracts as c  # noqa: E402
from pearnly_core.write_precheck import line_ask as ask  # noqa: E402

TEXTS = {"guide.q.payment": "付款方式", "guide.q.party": "对方", "guide.q.account_set": "账套", "guide.q.location": "仓库",
         "guide.q.cost_basis": "出库成本", "guide.option.payment.cash": "现金", "guide.option.payment.credit": "赊账",
         "guide.line.search": "输入名称或编码查找", "Cancel": "取消", "This option is no longer current": "此选项已失效",
         "No match for “{0}”": "没有找到「{0}」", "Too many matches; type a few more letters to narrow it down": "结果过多"}

PAY = {"kind": "payment", "subject": "purchase|1|abc", "label": "ABC", "options": [{"code": "cash"}, {"code": "credit"}]}
PARTY = {"kind": "party", "subject": "purchase|0105550000001", "label": "ABC", "search": "parties"}
BOOKS = {"kind": "account_set", "subject": "2026", "label": "",
         "options": [{"code": f"B{i:02d}", "name": f"账套 {i:02d}"} for i in range(1, 16)]}
COST = {"kind": "cost_basis", "subject": "line-1", "label": "",
        "options": [{"code": "none"}, {"code": "given", "entry": {"input": "number", "min_exclusive": "0"}}]}
WHERE = {"kind": "location", "subject": "loc", "label": "",
         "options": [{"code": "W1", "name": "主仓"}, {"code": "W2", "name": "二仓"}],
         "when": {"kind": "payment", "subject": PAY["subject"], "value": "credit"}}
PAY_KEY, PARTY_KEY, BOOKS_KEY = (c.item_key(r["kind"], r["subject"]) for r in (PAY, PARTY, BOOKS))


class Backend:
    """替身判定：每张单的项按 ``docs`` 给；记住的答案（``remember``）下次取卡时成为已定；``refuse`` 里的值记答时被产品拒。"""

    def __init__(self, docs, parties=()):
        self.docs, self.parties = docs, list(parties)
        self.memory, self.records, self.refuse, self.denied, self.serial = {}, [], set(), set(), 0

    def card(self, company, doc_ids):
        merged = []
        for doc_id in doc_ids:
            items = []
            for raw in self.docs.get(doc_id) or ():
                found = self.memory.get((raw["kind"], raw["subject"]))
                items.append({**raw, "value": found, "source": c.REMEMBERED} if found else dict(raw))
            merged.append({"doc_id": doc_id, "items": items})
        return c.merge(merged)

    def record(self, company, doc_ids, answers):
        self.records.append((company, list(doc_ids), list(answers)))
        before = self.card(company, doc_ids)
        if any(a["value"] in self.refuse for a in answers):
            return {"refused": "产品不收"}
        expanded, errors = c.expand(before, answers)
        if errors:
            return {**before, "errors": errors}
        for rows in expanded.values():
            for row in rows:
                self.memory[(row["kind"], row["subject"])] = row["value"]
        return {**self.card(company, doc_ids), "writable": list(doc_ids), "held": []}

    def search(self, company, doc_id, kind, txt, start, page_length):
        rows = [row for row in self.parties if txt.lower() in (row["code"] + row["name"]).lower()]
        return {"options": rows[start:start + page_length], "more": len(rows) > start + page_length}

    def allowed(self, user, company):
        return user not in self.denied

    def user(self):
        return "u@x"

    def text(self, key):
        return TEXTS.get(key, key)

    def token(self):
        self.serial += 1
        return f"t{self.serial}"


def buttons(message):
    return message["quickReply"]["items"]


def labels(message):
    return [item["action"]["label"] for item in buttons(message)]


def data(message, index):
    return buttons(message)[index]["action"]["data"]


class Fixture(unittest.TestCase):
    def setUp(self):
        self.payload = {}

    def start(self, backend, doc_ids=("d1",), ref="I-1", heading="ABC · INV-1"):
        return ask.enqueue(self.payload, "C1", list(doc_ids), ref=ref, heading=heading, backend=backend)

    def tap(self, backend, message, index):
        return ask.postback(self.payload, data(message, index), backend=backend)

    def type(self, backend, words):
        return ask.text(self.payload, words, backend=backend)


class Render(Fixture):
    def test_small_list_is_quick_replies_with_the_recommendation_first(self):
        backend = Backend({"d1": [{**PAY, "value": "credit", "source": c.RULE}]})
        out = self.start(backend)
        self.assertEqual(out["status"], ask.ASKING)
        message = out["messages"][0]
        self.assertEqual(labels(message), ["赊账", "现金", "取消"])  # 推荐（预选）排第一
        self.assertEqual(message["text"], "ABC · INV-1\n付款方式\nABC")
        self.assertEqual(backend.records, [])  # 推荐只是推荐：没点不记

    def test_whether_question_keeps_its_order_and_has_no_recommendation(self):
        # 「做不做」类照 guide 出的顺序列（不另排），guide 不给推荐
        from pearnly_core.write_precheck import guide

        raw = {**BOOKS, "options": BOOKS["options"][:3], "value": "B03", "source": c.RULE}
        backend = Backend({"d1": [raw]})
        message = self.start(backend)["messages"][0]
        built = guide.build(backend.card("C1", ["d1"]))["questions"][0]
        self.assertFalse(any(o.get("recommended") for o in built["options"]))
        self.assertEqual(built["preselected"], "")
        self.assertEqual(labels(message), [o["name"] for o in built["options"]] + ["取消"])
        self.assertEqual(backend.records, [])

    def test_13_to_20_options_list_numbers_and_first_12_buttons(self):
        backend = Backend({"d1": [BOOKS]})
        message = self.start(backend)["messages"][0]
        self.assertEqual(len(buttons(message)), 13)
        self.assertEqual(labels(message)[0], "1. 账套 01")
        self.assertEqual(labels(message)[-1], "取消")
        for index in range(1, 16):
            self.assertIn(f"{index}. 账套 {index:02d}", message["text"])
        out = self.type(backend, "14")  # 打编号选第 13 个以后的
        self.assertEqual(backend.records[-1][2], [{"key": BOOKS_KEY, "value": "B14"}])
        self.assertEqual(out["finished"][0]["answers"][0]["value_label"], "账套 14")

    def test_long_names_are_cut_on_buttons_and_written_in_full(self):
        long = "บริษัท ตัวอย่างชื่อยาวมาก จำกัด สาขาที่หนึ่ง"
        backend = Backend({"d1": [{"kind": "location", "subject": "x", "label": "",
                                   "options": [{"code": "W1", "name": long}, {"code": "W2", "name": "สั้น"}]}]})
        message = self.start(backend)["messages"][0]
        self.assertTrue(all(len(label) <= 20 for label in labels(message)))
        self.assertIn(f"1. {long}", message["text"])

    def test_search_question_offers_the_preselected_value_and_the_prompt(self):
        backend = Backend({"d1": [{**PARTY, "value": "S01", "value_label": "ABC Co", "source": c.HISTORY}]})
        message = self.start(backend)["messages"][0]
        self.assertEqual(labels(message), ["ABC Co", "取消"])
        self.assertIn("输入名称或编码查找", message["text"])

    def test_postback_data_carries_index_not_value_and_fits(self):
        backend = Backend({"d1": [BOOKS]})
        message = self.start(backend)["messages"][0]
        for item in buttons(message):
            raw = item["action"]["data"]
            self.assertLessEqual(len(raw.encode("utf-8")), ask.DATA_MAX)
            self.assertTrue(ask.is_step(raw))
            params = parse_qs(raw)
            self.assertEqual(params["a"], [ask.ACTION])
            self.assertNotIn("B01", raw)
            self.assertIn("b", params)
        self.assertEqual(parse_qs(data(message, 0))["q"], [BOOKS_KEY])

    def test_questions_line_cannot_ask_are_left(self):
        backend = Backend({"d1": [COST]})
        out = self.start(backend)
        self.assertEqual(out["status"], ask.IDLE)
        self.assertEqual(out["finished"][0]["left"], [c.item_key("cost_basis", "line-1")])

    def test_nothing_to_ask(self):
        backend = Backend({"d1": [{**PAY, "value": "cash", "source": c.REMEMBERED}]})
        out = self.start(backend)
        self.assertEqual((out["status"], out["messages"]), (ask.IDLE, []))
        self.assertEqual(out["finished"][0]["ref"], "I-1")
        self.assertFalse(ask.asking(self.payload))


class Answer(Fixture):
    def test_tap_records_then_next_question_then_finished(self):
        backend = Backend({"d1": [PAY, {**PARTY, "value": "S01", "value_label": "ABC Co", "source": c.RULE}]})
        first = self.start(backend)["messages"][0]
        self.assertEqual(first["text"].splitlines()[1], "对方")
        second = self.tap(backend, first, 0)["messages"][0]
        self.assertEqual(backend.records[-1], ("C1", ["d1"], [{"key": PARTY_KEY, "value": "S01"}]))
        self.assertEqual(second["text"].splitlines()[1], "付款方式")
        out = self.tap(backend, second, 1)
        self.assertEqual(out["status"], ask.IDLE)
        self.assertEqual([a["value"] for a in out["finished"][0]["answers"]], ["S01", "credit"])
        self.assertEqual(out["finished"][0]["answers"][1]["value_label"], "赊账")
        self.assertFalse(ask.asking(self.payload))

    def test_typed_search_lists_results_and_tap_records(self):
        backend = Backend({"d1": [PARTY]}, parties=[{"code": "S01", "name": "ABC Co"}, {"code": "S02", "name": "ABD Ltd"},
                                                    {"code": "S03", "name": "XYZ"}])
        self.start(backend)
        out = self.type(backend, "ab")
        message = out["messages"][0]
        self.assertIn("1. S01 ABC Co", message["text"])
        self.assertEqual(labels(message), ["1", "2", "取消"])
        done = self.tap(backend, message, 1)
        self.assertEqual(backend.records[-1][2], [{"key": PARTY_KEY, "value": "S02"}])
        self.assertEqual(done["finished"][0]["answers"][0]["value_label"], "S02 ABD Ltd")

    def test_typed_search_more_results_say_narrow_it_down(self):
        backend = Backend({"d1": [PARTY]}, parties=[{"code": f"S{i:02d}", "name": "ABC"} for i in range(20)])
        self.start(backend)
        message = self.type(backend, "abc")["messages"][0]
        self.assertIn("结果过多", message["text"])
        self.assertEqual(len(buttons(message)), 13)

    def test_no_match_reasks_without_recording(self):
        backend = Backend({"d1": [PARTY]}, parties=[{"code": "S01", "name": "ABC"}])
        self.start(backend)
        out = self.type(backend, "zzz")
        self.assertEqual(out["messages"][0]["text"], "没有找到「zzz」")
        self.assertEqual(out["messages"][1]["text"].splitlines()[1], "对方")
        self.assertEqual(backend.records, [])

    def test_unrelated_text_does_not_move_on(self):
        backend = Backend({"d1": [{**PAY, "value": "credit", "source": c.RULE}]})
        self.start(backend)
        out = self.type(backend, "hello")
        self.assertEqual(out["status"], ask.ASKING)
        self.assertEqual(out["messages"][1]["text"].splitlines()[1], "付款方式")
        self.assertEqual(backend.records, [])
        self.assertIsNone(ask.text({}, "hello", backend=backend))  # 没在答题：交回产品

    def test_wrong_answer_says_why_and_asks_again(self):
        backend = Backend({"d1": [PAY]})
        message = self.start(backend)["messages"][0]
        backend.refuse.add("cash")
        out = self.tap(backend, message, 0)
        self.assertEqual(out["messages"][0]["text"], "产品不收")
        self.assertEqual(out["messages"][1]["text"].splitlines()[1], "付款方式")
        self.assertTrue(ask.asking(self.payload))
        backend.refuse.clear()
        self.assertEqual(self.tap(backend, out["messages"][1], 1)["status"], ask.IDLE)

    def test_value_not_offered_reasks(self):
        backend = Backend({"d1": [PAY]})
        message = self.start(backend)["messages"][0]
        self.payload[ask.KEY]["current"]["shown"][0] = "cheque"  # 判定那边选项已经变了
        out = self.tap(backend, message, 0)
        self.assertEqual(out["messages"][0]["text"], "此选项已失效")
        self.assertTrue(ask.asking(self.payload))

    def test_cancel_stops_this_batch(self):
        backend = Backend({"d1": [PAY]})
        message = self.start(backend)["messages"][0]
        out = self.tap(backend, message, len(buttons(message)) - 1)
        self.assertEqual(out["status"], ask.IDLE)
        self.assertEqual(out["cancelled"][0]["doc_ids"], ["d1"])
        self.assertEqual(backend.records, [])

    def test_old_buttons_do_nothing(self):
        backend = Backend({"d1": [PAY, {**PARTY, "value": "S01", "value_label": "ABC Co", "source": c.RULE}]})
        first = self.start(backend)["messages"][0]
        self.tap(backend, first, 0)
        out = self.tap(backend, first, 0)  # 上一题的按钮
        self.assertEqual((out["status"], out["messages"][0]["text"]), (ask.STALE, "此选项已失效"))
        self.assertEqual(len(backend.records), 1)
        forged = data(first, 0).replace("b=t1", "b=t9")
        self.assertEqual(ask.postback(self.payload, forged, backend=backend)["status"], ask.STALE)

    def test_answer_is_only_for_the_owner(self):
        backend = Backend({"d1": [PAY]})
        message = self.start(backend)["messages"][0]
        backend.denied.add("u@x")
        self.assertEqual(self.tap(backend, message, 0)["status"], ask.STALE)
        self.assertEqual(backend.records, [])

    def test_condition_skips_question_when_not_met(self):
        backend = Backend({"d1": [PAY, WHERE]})
        message = self.start(backend)["messages"][0]
        self.assertEqual(self.tap(backend, message, 0)["status"], ask.IDLE)  # 现金：不问仓
        backend = Backend({"d2": [PAY, WHERE]})
        self.payload = {}
        message = self.start(backend, doc_ids=("d2",))["messages"][0]
        out = self.tap(backend, message, 1)  # 赊账：接着问仓
        self.assertEqual(out["messages"][0]["text"].splitlines()[1], "仓库")


class Queue(Fixture):
    def test_new_ticket_waits_and_answers_stay_on_their_ticket(self):
        backend = Backend({"d1": [PAY], "d2": [{**PARTY, "value": "S01", "value_label": "ABC Co", "source": c.RULE}]})
        first = self.start(backend)["messages"][0]
        queued = self.start(backend, doc_ids=("d2",), ref="I-2", heading="DEF · INV-2")
        self.assertEqual((queued["status"], queued["messages"], queued["finished"]), (ask.QUEUED, [], []))
        out = self.tap(backend, first, 0)
        self.assertEqual(backend.records[-1][1], ["d1"])  # 只记给当前这张
        self.assertEqual([b["ref"] for b in out["finished"]], ["I-1"])
        self.assertEqual(out["status"], ask.ASKING)
        self.assertEqual(out["messages"][0]["text"].splitlines()[:2], ["DEF · INV-2", "对方"])
        self.assertEqual(self.tap(backend, first, 1)["status"], ask.STALE)  # 前一张的按钮不会答到这一张

    def test_ticket_without_questions_is_finished_at_once_while_asking(self):
        backend = Backend({"d1": [PAY], "d2": []})
        self.start(backend)
        out = self.start(backend, doc_ids=("d2",), ref="I-2")
        self.assertEqual([b["ref"] for b in out["finished"]], ["I-2"])
        self.assertEqual(out["status"], ask.ASKING)
        self.assertEqual(self.payload[ask.KEY]["current"]["ref"], "I-1")

    def test_remembered_answer_lets_the_next_ticket_skip(self):
        backend = Backend({"d1": [PAY], "d2": [PAY]})
        first = self.start(backend)["messages"][0]
        self.start(backend, doc_ids=("d2",), ref="I-2")
        out = self.tap(backend, first, 0)
        self.assertEqual([b["ref"] for b in out["finished"]], ["I-1", "I-2"])
        self.assertEqual(out["status"], ask.IDLE)

    def test_cancel_moves_to_the_next_ticket(self):
        backend = Backend({"d1": [PAY], "d2": [PAY]})
        message = self.start(backend)["messages"][0]
        self.start(backend, doc_ids=("d2",), ref="I-2", heading="DEF")
        out = self.tap(backend, message, len(buttons(message)) - 1)
        self.assertEqual([b["ref"] for b in out["cancelled"]], ["I-1"])
        self.assertEqual(out["messages"][0]["text"].splitlines()[0], "DEF")

    def test_stop_drops_everything(self):
        backend = Backend({"d1": [PAY], "d2": [PAY]})
        message = self.start(backend)["messages"][0]
        self.start(backend, doc_ids=("d2",), ref="I-2")
        self.assertEqual([b["ref"] for b in ask.pending(self.payload)], ["I-1", "I-2"])
        self.assertEqual(len(ask.stop(self.payload)), 2)
        self.assertFalse(ask.asking(self.payload))
        self.assertEqual(self.tap(backend, message, 0)["status"], ask.STALE)

    def test_session_key_is_separate(self):
        self.assertNotEqual(ask.KEY, "items")  # ERP line_items / Cowork line.items 用 items
        self.assertEqual(ask.ACTION, "precheck")


class SiteBackend(unittest.TestCase):
    """站点那一份（``line_backend``）：记答走 ``api.record(guided=True)``；产品拒收（``frappe.throw``）退回保存点、回原话。"""

    def load(self, record):
        frappe = types.ModuleType("frappe")
        calls = []

        class ValidationError(Exception):
            pass

        frappe.ValidationError = ValidationError
        frappe._ = lambda text: text
        frappe.session = types.SimpleNamespace(user="u@x")
        frappe.db = types.SimpleNamespace(savepoint=lambda name: calls.append(("savepoint", name)),
                                          rollback=lambda save_point=None: calls.append(("rollback", save_point)))
        frappe.clear_messages = lambda: calls.append(("clear",))
        api = types.ModuleType("pearnly_core.write_precheck.api")
        api.SEARCH_KINDS = ("parties",)
        api.card = lambda company, doc_ids: {}
        api.record = record
        products = types.ModuleType("pearnly_core.write_precheck.products")
        products.search = lambda *args: {"options": [{"code": "S1", "name": "A"}], "more": False}
        products.allowed = lambda user, company: True
        import pearnly_core.write_precheck as package

        modules = {"frappe": frappe, "pearnly_core.write_precheck.api": api, "pearnly_core.write_precheck.products": products}
        with mock.patch.dict(sys.modules, modules), mock.patch.object(package, "api", api, create=True), \
                mock.patch.object(package, "products", products, create=True):
            sys.modules.pop("pearnly_core.write_precheck.line_backend", None)
            module = importlib.import_module("pearnly_core.write_precheck.line_backend")
            sys.modules.pop("pearnly_core.write_precheck.line_backend", None)
        return module.BACKEND, calls, ValidationError

    def test_record_is_guided(self):
        seen = []
        backend, calls, _error = self.load(lambda company, doc_ids, answers, guided=False: seen.append(guided) or {"held": []})
        self.assertEqual(backend.record("C1", ["d1"], [{"key": "k", "value": "v"}]), {"held": []})
        self.assertEqual(seen, [True])
        self.assertEqual(calls, [("savepoint", "pearnly_precheck_line_answer")])

    def test_refused_rolls_back_to_the_savepoint(self):
        holder = {}

        def record(company, doc_ids, answers, guided=False):
            raise holder["error"]("编码已停用")

        backend, calls, error = self.load(record)
        holder["error"] = error
        self.assertEqual(backend.record("C1", ["d1"], []), {"refused": "编码已停用"})
        self.assertIn(("rollback", "pearnly_precheck_line_answer"), calls)

    def test_search_only_known_kinds(self):
        backend, _calls, _error = self.load(lambda *a, **k: {})
        self.assertEqual(backend.search("C1", "d1", "secret", "a", 0, 12), {"options": [], "more": False})
        self.assertEqual(backend.search("C1", "d1", "parties", "a", 0, 12)["options"][0]["code"], "S1")


if __name__ == "__main__":
    unittest.main()
