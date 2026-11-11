"""写账前置判定（`pearnly_core.write_precheck`，多产品共用，纯函数）：卡片的分组、跨单去重、三种状态、答案展开、留下的单，
以及没有记忆时的预选规则；共用包不导入 Frappe 和任何产品。评估 `docs/work/2026-11-04-precheck-card.md`。

    python3 tests/test_write_precheck.py
"""
import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))

from pearnly_core.write_precheck import contracts as c  # noqa: E402
from pearnly_core.write_precheck import preselect as p  # noqa: E402

PACKAGE = ROOT / "apps/pearnly_core/pearnly_core/write_precheck"
PRODUCTS = ("frappe", "pearnly_cowork", "pearnly_erp_portal", "pearnly_dms", "pearnly_control")
# tax_documents.amounts：票面金额与税号的共用纯函数（只依赖标准库）；复核卡的校验（`review.py`）照它算税号校验位与金额
SHARED = ("pearnly_core.write_precheck", "pearnly_core.express_rules", "pearnly_core.tax_documents")
FRAMEWORK_LAYER = ("api.py", "products.py")
STDLIB = {"__future__", "collections", "datetime", "decimal", "hashlib", "inspect", "json", "typing", "unicodedata"}

TARGET = {"connection": "C1", "connection_title": "Office", "account_set": "T9", "account_set_title": "2026"}


def doc(doc_id, *items, **extra):
    return {"doc_id": doc_id, "target": TARGET, "items": list(items), **extra}


def party(subject="purchase|0105556000001|00000|abc", value="", source="", direction="purchase"):
    return {"kind": "party", "subject": subject, "label": "ABC", "value": value, "source": source,
            "direction": direction, "options": [{"code": "__new__"}, {"code": "S001"}, {"code": "S002"}]}


def account(subject="purchase|0105556000001", value="", source=""):
    return {"kind": "account", "subject": subject, "label": "ABC", "value": value, "source": source, "search": "accounts"}


class Boundary(unittest.TestCase):
    def test_imports_no_framework_or_product_and_only_standard_library(self):
        # 契约与预选是纯函数；Frappe 只在接口层（``api`` 白名单、``products`` 读产品钩子），仍不导入任何产品
        for path in sorted(PACKAGE.rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    self.assertFalse(node.level, f"{path.name}: use absolute imports")
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    with self.subTest(module=path.name, imports=name):
                        if path.name in FRAMEWORK_LAYER:
                            self.assertTrue(name.split(".")[0] == "frappe" or name in STDLIB or name.startswith(SHARED), name)
                            continue
                        self.assertNotIn(name.split(".")[0], PRODUCTS)
                        self.assertTrue(name in STDLIB or name.startswith(SHARED), name)

    def test_package_says_it_is_shared(self):
        self.assertIn("多产品共用", (PACKAGE / "__init__.py").read_text(encoding="utf-8"))


class Groups(unittest.TestCase):
    def test_every_kind_has_a_group(self):
        for kind in c.KINDS:
            self.assertIn(c.group_of(kind, "x"), c.GROUPS)

    def test_party_and_account_follow_direction_and_subject(self):
        self.assertEqual(c.group_of("party", "x", "purchase"), c.PURCHASE)
        self.assertEqual(c.group_of("party", "x", "sales"), c.SALES)
        self.assertEqual(c.group_of("account", "purchase|1"), c.SERVICE)
        self.assertEqual(c.group_of("account", "sales|1", "sales"), c.SALES)
        self.assertEqual(c.group_of("account", "stock:OU"), c.STOCK)

    def test_unknown_kind_is_a_programming_error(self):
        with self.assertRaises(c.PrecheckError):
            c.group_of("warehouse")
        with self.assertRaises(c.PrecheckError):
            c.merge([doc("D1", {"kind": "warehouse", "subject": "x"})])

    def test_card_groups_keep_the_fixed_order_and_skip_empty_ones(self):
        card = c.merge([doc("D1", {"kind": "payment", "subject": "purchase|1", "label": "ABC"},
                            {"kind": "current_book", "subject": "b", "label": "B"}, party())])
        self.assertEqual([group["group"] for group in card["groups"]], [c.BOOK, c.PURCHASE, c.PAYMENT])


class States(unittest.TestCase):
    def test_decided_proposed_open(self):
        self.assertEqual(c.normalize_item(party(value="S001", source=c.REMEMBERED))["state"], c.DECIDED)
        self.assertEqual(c.normalize_item(party(value="S001", source=c.HISTORY))["state"], c.PROPOSED)
        self.assertEqual(c.normalize_item(party())["state"], c.OPEN)

    def test_a_value_without_a_source_is_only_proposed(self):
        self.assertEqual(c.normalize_item(party(value="S001"))["state"], c.PROPOSED)

    def test_a_value_no_longer_offered_counts_as_not_chosen(self):
        item = c.normalize_item(party(value="S999", source=c.REMEMBERED))
        self.assertEqual((item["state"], item["value"], item["source"]), (c.OPEN, "", ""))

    def test_searchable_items_keep_values_outside_the_first_page(self):
        item = c.normalize_item({**account(value="5310-01", source=c.SUGGESTED), "options": [{"code": "1"}]})
        self.assertEqual(item["state"], c.PROPOSED)

    def test_unknown_source_is_refused(self):
        with self.assertRaises(c.PrecheckError):
            c.normalize_item(party(value="S001", source="guess"))

    def test_remember_flag(self):
        self.assertTrue(c.normalize_item(party(value="S001", source=c.RULE))["remember"])
        self.assertFalse(c.normalize_item({**account(value="5310", source=c.TASK_DEFAULT)})["remember"])
        self.assertFalse(c.normalize_item({"kind": "posting_kind", "subject": "D1", "value": "stock",
                                           "source": c.RULE})["remember"])


class Recommended(unittest.TestCase):
    def test_the_chosen_value_is_marked_and_comes_first(self):
        item = c.normalize_item(party(value="S002", source=c.RULE))
        self.assertEqual(item["options"][0], {"code": "S002", "recommended": True})
        self.assertEqual([o["code"] for o in item["options"]], ["S002", "__new__", "S001"])
        self.assertFalse(any(o.get("recommended") for o in c.normalize_item(party())["options"]))


class Merge(unittest.TestCase):
    def test_same_item_on_several_documents_is_asked_once(self):
        card = c.merge([doc("D1", party()), doc("D2", party()), doc("D3", account())])
        items = c.items_of(card)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["docs"], ["D1", "D2"])
        self.assertEqual(card["targets"], [{**TARGET, "count": 3}])

    def test_strongest_state_then_most_common_value_wins(self):
        card = c.merge([doc("D1", party(value="S001", source=c.HISTORY)),
                        doc("D2", party(value="S002", source=c.HISTORY)),
                        doc("D3", party(value="S002", source=c.HISTORY)),
                        doc("D4", party())])
        (item,) = c.items_of(card)
        self.assertEqual((item["value"], item["state"], item["docs"]), ("S002", c.PROPOSED, ["D1", "D2", "D3", "D4"]))
        card = c.merge([doc("D1", party(value="S001", source=c.HISTORY)), doc("D2", party(value="S002",
                                                                                         source=c.REMEMBERED))])
        self.assertEqual(c.items_of(card)[0]["value"], "S002")

    def test_open_items_come_first_in_a_group(self):
        card = c.merge([doc("D1", {"kind": "item", "subject": "a", "label": "A", "value": "P1", "source": c.REMEMBERED},
                            {"kind": "item", "subject": "b", "label": "B", "value": "__new__", "source": c.RULE},
                            {"kind": "item", "subject": "c", "label": "C"})])
        self.assertEqual([item["state"] for item in c.items_of(card)], [c.OPEN, c.PROPOSED, c.DECIDED])
        self.assertEqual(card["counts"], {c.OPEN: 1, c.PROPOSED: 1, c.DECIDED: 1})

    def test_ready_documents_have_only_decided_items_or_none(self):
        card = c.merge([doc("D1", party(value="S001", source=c.REMEMBERED)), doc("D2"), doc("D3", party(
            subject="purchase|0105556000002|00000|x"))])
        self.assertEqual(card["ready"], ["D1", "D2"])

    def test_unknown_unwritable_and_waiting_documents_are_listed_as_facts(self):
        card = c.merge([doc("D1", state=c.UNKNOWN, ref="RR001", party="ABC", record="PR-1"),
                        doc("D2", state=c.NOT_WRITABLE, reason="period_outside"),
                        doc("D3", state=c.WAITING, reason="masters_wait"), doc("D4")])
        self.assertEqual(card["unknown"], [{"doc_id": "D1", "reason": "", "ref": "RR001", "party": "ABC",
                                            "record": "PR-1"}])
        self.assertEqual(card["not_writable"][0]["reason"], "period_outside")
        self.assertEqual(card["waiting"][0]["doc_id"], "D3")
        self.assertEqual(card["ready"], ["D4"])
        self.assertTrue(c.needs_card(card))

    def test_bad_documents_are_programming_errors(self):
        for docs in ([{"items": []}], [doc("D1"), doc("D1")], [doc("D1", state="lost")],
                     [doc(f"D{i}") for i in range(c.MAX_DOCUMENTS + 1)]):
            with self.assertRaises(c.PrecheckError):
                c.merge(docs)

    def test_item_keys_are_stable(self):
        self.assertEqual(c.item_key("party", "x"), c.item_key("party", "x"))
        self.assertNotEqual(c.item_key("party", "x"), c.item_key("item", "x"))


class Card(unittest.TestCase):
    def test_direct_write_shows_a_card_only_when_something_is_not_decided(self):
        self.assertFalse(c.needs_card(c.merge([doc("D1", party(value="S001", source=c.REMEMBERED))])))
        self.assertTrue(c.needs_card(c.merge([doc("D1", party(value="S001", source=c.RULE))])))
        self.assertTrue(c.needs_card(c.merge([doc("D1", party())])))

    def test_defaults_accept_proposals_only(self):
        card = c.merge([doc("D1", party(value="S001", source=c.HISTORY), account(),
                            {"kind": "payment", "subject": "purchase|1", "value": "cash", "source": c.FACE})])
        self.assertEqual(c.defaults(card), [{"key": c.item_key("party", party()["subject"]), "value": "S001"}])

    def test_answers_expand_to_every_document_of_the_item(self):
        card = c.merge([doc("D1", party()), doc("D2", party(), account())])
        answers = [{"key": c.item_key("party", party()["subject"]), "value": "S002"},
                   {"key": c.item_key("account", account()["subject"]), "value": "5310-01"}]
        result, errors = c.expand(card, answers)
        self.assertEqual(errors, [])
        self.assertEqual(result["D1"], [{"kind": "party", "subject": party()["subject"], "value": "S002", "label": "ABC",
                                         "remember": True}])
        self.assertEqual([row["kind"] for row in result["D2"]], ["party", "account"])

    def test_bad_answers_are_reported_one_by_one(self):
        card = c.merge([doc("D1", party(), account())])
        key = c.item_key("party", party()["subject"])
        result, errors = c.expand(card, [{"key": "nope", "value": "x"}, {"key": key, "value": "S999"},
                                         {"key": c.item_key("account", account()["subject"]), "value": " "}])
        self.assertEqual([error["code"] for error in errors],
                         [c.ERR_UNKNOWN_KEY, c.ERR_NOT_OFFERED, c.ERR_EMPTY_VALUE])
        self.assertEqual(result, {})
        result, errors = c.expand(card, [{"key": "nope", "value": "x"}, {"key": key, "value": "S001"}])
        self.assertEqual(result, {"D1": [{"kind": "party", "subject": party()["subject"], "value": "S001",
                                          "label": "ABC", "remember": True}]})
        _, errors = c.expand(card, [{"key": key, "value": "S001"}, {"key": key, "value": "S002"}])
        self.assertEqual(errors, [{"key": key, "code": c.ERR_DUPLICATE_KEY}])
        _, errors = c.expand(card, [{"key": key, "value": "S001"}] * (c.MAX_ANSWERS + 1))
        self.assertEqual(errors[0]["code"], c.ERR_TOO_MANY)

    def test_decided_items_can_be_changed(self):
        card = c.merge([doc("D1", party(value="S001", source=c.REMEMBERED))])
        result, errors = c.expand(card, [{"key": c.item_key("party", party()["subject"]), "value": "S002"}])
        self.assertEqual((errors, result["D1"][0]["value"]), ([], "S002"))

    def test_unanswered_open_items_hold_back_only_their_documents(self):
        card = c.merge([doc("D1", party()), doc("D2", party(subject="purchase|0105556000002|00000|x", value="S001",
                                                            source=c.RULE)),
                        doc("D3", account()), doc("D4"), doc("D5", state=c.UNKNOWN)])
        self.assertEqual(c.held_back(card), ["D1", "D3"])
        self.assertEqual(c.writable(card), ["D4", "D2"])
        answers = [{"key": c.item_key("account", account()["subject"]), "value": "5310"}]
        self.assertEqual(c.held_back(card, answers), ["D1"])
        self.assertEqual(c.writable(card, answers), ["D4", "D2", "D3"])

    def test_preview_lists_open_items_first_and_counts_the_rest(self):
        items = [{"kind": "item", "subject": f"i{n}", "label": f"I{n}", "value": "__new__", "source": c.RULE}
                 for n in range(5)] + [{"kind": "item", "subject": "z", "label": "Z"}]
        summary = c.preview(c.merge([doc("D1", *items)]), limit=4)
        self.assertEqual(summary["rows"][0], {"label": "Z", "value": "", "value_label": "", "state": c.OPEN})
        self.assertEqual((len(summary["rows"]), summary["more"]), (4, 2))


def payment(value="credit", source="remembered", subject="purchase|0105556000001"):
    return {"kind": "payment", "subject": subject, "label": "ABC", "value": value, "source": source,
            "direction": "purchase", "options": [{"code": "cash"}, {"code": "credit"}]}


class DocumentAnswers(unittest.TestCase):
    """单张的例外（主会话 11-05）：按供应商记住照旧是默认；答案带 ``doc_id`` 只改那一张、不记住、不合并；那一张自己定的列成单独一项，
    来源 ``document``、算已定、能再改。"""

    def card(self):
        return c.merge([doc("P1", payment()), doc("P2", payment())])

    def test_an_answer_for_one_document_changes_only_it_and_is_not_remembered(self):
        card = self.card()
        item, = c.items_of(card)
        self.assertEqual(item["docs"], ["P1", "P2"])
        expanded, errors = c.expand(card, [{"key": item["key"], "value": "cash", "doc_id": "P2"}])
        self.assertEqual(errors, [])
        self.assertEqual(expanded, {"P2": [{"kind": "payment", "subject": item["subject"], "value": "cash", "label": "ABC",
                                            "remember": False, "document": True}]})
        # 不带 doc_id：照旧记给这一家（两张都展开、要记住）
        expanded, _errors = c.expand(card, [{"key": item["key"], "value": "cash"}])
        self.assertEqual(sorted(expanded), ["P1", "P2"])
        self.assertTrue(all(row["remember"] and "document" not in row for rows in expanded.values() for row in rows))

    def test_only_payment_and_only_a_document_of_the_item_can_be_answered_alone(self):
        card = c.merge([doc("P1", payment(), account(value="5100", source="remembered"))])
        keys = {item["kind"]: item["key"] for item in c.items_of(card)}
        _expanded, errors = c.expand(card, [{"key": keys["account"], "value": "5200", "doc_id": "P1"},
                                            {"key": keys["payment"], "value": "cash", "doc_id": "P9"}])
        self.assertEqual([error["code"] for error in errors], [c.ERR_NOT_PER_DOCUMENT] * 2)

    def test_the_documents_own_value_is_decided_kept_apart_and_answered_for_it_alone(self):
        own = payment("cash", c.DOCUMENT, subject="document:P2")
        card = c.merge([doc("P1", payment()), doc("P2", own)])
        items = {item["subject"]: item for item in c.items_of(card)}
        self.assertEqual(items["document:P2"]["state"], c.DECIDED)
        self.assertEqual(items["document:P2"]["docs"], ["P2"])
        self.assertFalse(items["document:P2"]["remember"])
        self.assertFalse(c.needs_card(card))
        expanded, _errors = c.expand(card, [{"key": items["document:P2"]["key"], "value": "credit"}])
        self.assertEqual(expanded["P2"][0]["document"], True)
        self.assertEqual(expanded["P2"][0]["remember"], False)

    def test_an_open_item_answered_for_one_document_still_holds_the_others(self):
        card = c.merge([doc("P1", payment("", "")), doc("P2", payment("", ""))])
        item, = c.items_of(card)
        answers = [{"key": item["key"], "value": "cash", "doc_id": "P2"}]
        self.assertEqual(c.held_back(card, answers), ["P1"])
        self.assertEqual(c.writable(card, answers), ["P2"])
        self.assertEqual(c.held_back(card, [{"key": item["key"], "value": "cash"}]), [])


class Preselect(unittest.TestCase):
    def test_most_used(self):
        self.assertEqual(p.most_used({"B": 3, "A": 3, "C": 1}), "A")
        self.assertEqual(p.most_used({"B": 3, "C": 1}, ["C"]), "C")
        self.assertEqual(p.most_used({"B": 0, "C": "x"}), "")

    def test_party(self):
        rows = [{"code": "__new__"}, {"code": "S1", "branch": "00000"}, {"code": "S2", "branch": "00113"}]
        self.assertEqual(p.party(rows, {"S2": 4, "S1": 1}, "00000"), "S2")
        self.assertEqual(p.party(rows, {}, "113"), "S2")
        self.assertEqual(p.party(rows, {}, "00099"), "")
        self.assertEqual(p.party(rows[:2], {}, "00099"), "S1")
        self.assertEqual(p.party([{"code": "__new__"}]), p.NEW)

    def test_vat(self):
        self.assertEqual(p.vat(), p.EXPENSE)
        self.assertEqual(p.vat(["claim", "claim"]), p.CLAIM)
        self.assertEqual(p.vat(["claim", "expense"]), p.EXPENSE)

    def test_payment_follows_the_shared_funnel(self):
        self.assertEqual(p.payment({"payment_status": "paid"}), p.CASH)
        self.assertEqual(p.payment({"payment_status": "unpaid"}), p.CREDIT)
        self.assertEqual(p.payment({}), "")

    def test_refund_account(self):
        accounts = [{"code": "4101", "name": "ขายสินค้า"}, {"code": "4102", "name": "รับคืนสินค้า"}]
        self.assertEqual(p.refund_account(accounts), "4102")
        self.assertEqual(p.refund_account(accounts + [{"code": "4103", "name": "ส่วนลดหนี้"}]), "")
        self.assertEqual(p.refund_account([]), "")

    def test_vat_registered(self):
        self.assertEqual(p.vat_registered("0105556000001", True), p.VAT_YES)
        self.assertEqual(p.vat_registered("0105556000001", False), "")
        self.assertEqual(p.vat_registered("12345", True), "")

    def test_receipt_account(self):
        accounts = [{"code": "1111", "name": "เงินสด"}, {"code": "1121", "name": "เงินฝากธนาคารกสิกร"},
                    {"code": "1130", "name": "ลูกหนี้บัตรเครดิต"}, {"code": "1140", "name": "ลูกหนี้ Grab"}]
        self.assertEqual(p.receipt_account("PromptPay", accounts), "1121")
        self.assertEqual(p.receipt_account("โอนเงิน", accounts), "1121")
        self.assertEqual(p.receipt_account("บัตรเครดิต", accounts), "1130")
        self.assertEqual(p.receipt_account("GrabFood", accounts), "1140")
        self.assertEqual(p.receipt_account("LINE MAN", accounts), "")
        self.assertEqual(p.receipt_account("เงินสด", accounts), "")
        self.assertEqual(p.receipt_account("QR", accounts + [{"code": "1122", "name": "เงินฝากออมทรัพย์"}]), "")

    def test_document_policy_and_service_vat(self):
        self.assertEqual(p.document_policy("payment_evidence"), p.SKIP)
        self.assertEqual(p.service_vat(["2", "2"]), "cash_basis")
        self.assertEqual(p.service_vat(["1", "2"]), "accrual")
        self.assertEqual(p.service_vat([]), "accrual")

    def test_posting_kind(self):
        self.assertEqual(p.posting_kind([{"posting_kind": "stock", "item_type": "service"},
                                         {"item_type": "service"}, {"item_type": "Service"}]), "service")
        self.assertEqual(p.posting_kind([{"item_type": "goods"}, {"item_type": "service"}]), "")
        self.assertEqual(p.posting_kind([{"item_type": ""}]), "")
        self.assertEqual(p.posting_kind([]), "")

    def test_values_fit_the_contract(self):
        item = c.normalize_item({"kind": "receipt", "subject": "pos|qr", "value": p.receipt_account(
            "QR", [{"code": "1121", "name": "เงินฝาก"}]), "source": c.RULE})
        self.assertEqual(item["state"], c.PROPOSED)



class Conditional(unittest.TestCase):
    """测试机 11-09（shots/12）：「录入设置」选了按服务录入，后面还问从哪个仓库出库。只在某一项答成某个值时才成立的项带 ``when``：
    那一项答成别的值时它不留单、它的答案不展开。"""

    GOODS = {"kind": "goods_posting", "subject": "tax:0105556000001", "label": "", "value": "stock", "source": c.RULE,
             "options": [{"code": "stock"}, {"code": "service"}]}
    WHEN = {"kind": "goods_posting", "subject": "tax:0105556000001", "value": "stock"}
    LOCATION = {"kind": "location", "subject": "*", "label": "", "value": "", "when": WHEN,
                "options": [{"code": "01"}, {"code": "02"}]}
    ITEM = {"kind": "item", "subject": "sales|1|fee", "label": "Fee", "value": "__new__", "source": c.RULE, "when": WHEN}

    def card(self):
        return c.merge([doc("D1", self.GOODS, self.LOCATION, self.ITEM)])

    def test_the_condition_names_the_other_item_by_its_key(self):
        found = {item["kind"]: item for item in c.items_of(self.card())}
        self.assertEqual(found["location"]["when"], {"key": found["goods_posting"]["key"], "value": "stock"})
        self.assertNotIn("when", found["goods_posting"])

    def test_answering_the_other_value_drops_the_dependent_items(self):
        card = self.card()
        goods_key = c.item_key("goods_posting", self.GOODS["subject"])
        answers = [{"key": goods_key, "value": "service"},
                   {"key": c.item_key("location", "*"), "value": "01"},
                   {"key": c.item_key("item", self.ITEM["subject"]), "value": "__new__"}]
        result, errors = c.expand(card, answers)
        self.assertEqual(errors, [])
        self.assertEqual([row["kind"] for row in result["D1"]], ["goods_posting"])
        self.assertEqual(c.held_back(card, answers[:1]), [])  # 出库仓没答也不留单：这张不走库存
        self.assertEqual(c.writable(card, answers[:1]), ["D1"])

    def test_keeping_the_preselected_value_keeps_them(self):
        card = self.card()
        self.assertEqual(c.held_back(card), ["D1"])  # 预选走库存、出库仓未定：留下
        answers = [{"key": c.item_key("location", "*"), "value": "02"}]
        result, errors = c.expand(card, answers)
        self.assertEqual((errors, [row["kind"] for row in result["D1"]]), ([], ["location"]))
        self.assertEqual(c.held_back(card, answers), [])

    def test_a_malformed_condition_is_a_programming_error(self):
        with self.assertRaises(c.PrecheckError):
            c.normalize_item({**self.LOCATION, "when": {"kind": "nope", "subject": "x", "value": "y"}})


if __name__ == "__main__":
    unittest.main()
