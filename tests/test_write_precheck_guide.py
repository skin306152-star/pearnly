"""引导卡（``write_precheck.guide``，CC 引导式录入第 1 期，纯函数）：由后台按 ``contracts.merge`` 合成的卡片出题，模型不出题。

用户与主会话 11-12 定的三档（评估 ``docs/work/2026-11-12-cc-guided-entry.md`` 第 12 节第 1 问）：
- 后台能定的事实（已定）不问，收进「已定 N 项」可改；
- 「怎么录」的题：推荐项排第一并预选，但要用户点了才算——**卡上没点过的预选项不生效**；
- 「做不做」的题（录不录、按哪种业务录、哪本账套、明细对不上怎么录、出库成本）：不预选、不标推荐，必须明选。
同类同归类键只出一题并写明张数；题目文字由服务端按本轮语言出（这里给文字键与参数，接口层翻译）。

    python3 tests/test_write_precheck_guide.py
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))

from pearnly_core.write_precheck import contracts as c  # noqa: E402
from pearnly_core.write_precheck import guide as g  # noqa: E402


def doc(doc_id, *items, **extra):
    return {"doc_id": doc_id, "items": list(items), **extra}


PAY = {"kind": "payment", "subject": "purchase|0105550000001|abc", "label": "ABC",
       "options": [{"code": "cash"}, {"code": "credit"}]}
POLICY = {"kind": "document_policy", "subject": "transfer_slip", "label": "",
          "options": [{"code": "record"}, {"code": "skip"}]}
DETAIL = {"kind": "detail", "subject": "detail|purchase|1|abc|INV-1", "label": "ABC",
          "options": [{"code": "amount_only"}, {"code": "skip"}]}


def card(*docs):
    return c.merge(list(docs))


class Tiers(unittest.TestCase):
    def test_how_question_is_preselected_and_recommended(self):
        built = g.build(card(doc("d1", {**PAY, "value": "credit", "source": c.RULE})))
        q = built["questions"][0]
        self.assertEqual(q["tier"], g.HOW)
        self.assertEqual(q["preselected"], "credit")
        self.assertEqual(q["options"][0], {"code": "credit", "recommended": True, "label_key": "guide.option.payment.credit"})

    def test_whether_question_is_never_preselected(self):
        for raw in (POLICY, DETAIL):
            built = g.build(card(doc("d1", {**raw, "value": raw["options"][0]["code"], "source": c.RULE})))
            q = built["questions"][0]
            self.assertEqual(q["tier"], g.WHETHER, raw["kind"])
            self.assertEqual(q["preselected"], "")
            self.assertFalse(any(o.get("recommended") for o in q["options"]))

    def test_decided_goes_to_the_decided_list(self):
        built = g.build(card(doc("d1", {**PAY, "value": "cash", "source": c.REMEMBERED})))
        self.assertEqual(built["questions"], [])
        self.assertEqual([d["value"] for d in built["decided"]], ["cash"])
        self.assertEqual(built["counts"], {"questions": 0, "decided": 1})

    def test_decided_whether_question_is_still_asked(self):
        """「做不做」的题后台给了值也不算已定（例：账套记忆里有「不录」但这次是新的一批）——只认人明选。"""
        built = g.build(card(doc("d1", {**DETAIL, "value": "skip", "source": c.REMEMBERED})))
        self.assertEqual(built["questions"][0]["tier"], g.WHETHER)
        self.assertEqual(built["decided"], [])


class Merge(unittest.TestCase):
    def test_same_supplier_on_five_documents_is_one_question_with_count(self):
        built = g.build(card(*(doc(f"d{i}", dict(PAY)) for i in range(5))))
        self.assertEqual(len(built["questions"]), 1)
        self.assertEqual(built["questions"][0]["count"], 5)
        self.assertEqual(built["questions"][0]["title_params"], {"label": "ABC", "count": 5})

    def test_title_key_per_kind(self):
        built = g.build(card(doc("d1", dict(PAY))))
        self.assertEqual(built["questions"][0]["title_key"], "guide.q.payment")


class OnlyClickedCounts(unittest.TestCase):
    """卡上没点过的预选项不生效（11-12 用户批准）：没答的题，它的单留下，不照预选写。"""

    def test_unanswered_proposed_holds_the_documents(self):
        built_card = card(doc("d1", {**PAY, "value": "credit", "source": c.RULE}), doc("d2"))
        self.assertEqual(c.held_back(built_card), [])                 # 旧口径：预选没答也照写
        self.assertEqual(g.held_back(built_card, []), ["d1"])          # 引导卡：没点过不生效
        self.assertEqual(g.writable(built_card, []), ["d2"])

    def test_clicked_proposed_releases(self):
        built_card = card(doc("d1", {**PAY, "value": "credit", "source": c.RULE}))
        key = c.items_of(built_card)[0]["key"]
        self.assertEqual(g.held_back(built_card, [{"key": key, "value": "credit"}]), [])
        self.assertEqual(g.writable(built_card, [{"key": key, "value": "credit"}]), ["d1"])

    def test_decided_needs_no_click(self):
        built_card = card(doc("d1", {**PAY, "value": "cash", "source": c.REMEMBERED}))
        self.assertEqual(g.held_back(built_card, []), [])

    def test_defaults_are_empty(self):
        """一键照预选全收（LINE「ยืนยัน」用的 ``contracts.defaults``）不进引导卡：引导卡没有「全部接受」。"""
        built = g.build(card(doc("d1", {**PAY, "value": "credit", "source": c.RULE})))
        self.assertNotIn("defaults", built)


class OtherEntriesUnchanged(unittest.TestCase):
    """主会话 11-12：「没点过不生效」只在 CC 引导卡（``guide.held_back`` / ``guide.writable``）；LINE 一键确认与网页待处理页
    照旧走共用契约 ``contracts.defaults`` / ``held_back`` / ``writable``：预选没答照预选写。两个入口显式分开，这里钉住旧行为。"""

    def setUp(self):
        self.card = card(doc("d1", {**PAY, "value": "credit", "source": c.RULE}),
                         doc("d2", dict(POLICY)))

    def test_line_one_tap_confirm_sends_the_preselected_values(self):
        key = next(i["key"] for i in c.items_of(self.card) if i["kind"] == "payment")
        self.assertEqual(c.defaults(self.card), [{"key": key, "value": "credit"}])

    def test_web_pending_writes_unanswered_proposed(self):
        self.assertEqual(c.held_back(self.card), ["d2"])         # 只有未定项留下
        self.assertEqual(c.writable(self.card), ["d1"])

    def test_guide_holds_both(self):
        self.assertEqual(sorted(g.held_back(self.card)), ["d1", "d2"])
        self.assertEqual(g.writable(self.card), [])


class Facts(unittest.TestCase):
    def test_unknown_and_not_writable_are_listed(self):
        built = g.build(card(doc("d1", state=c.UNKNOWN, reason="sent"), doc("d2", state=c.NOT_WRITABLE, reason="period")))
        self.assertEqual([f["doc_id"] for f in built["facts"]["unknown"]], ["d1"])
        self.assertEqual([f["doc_id"] for f in built["facts"]["not_writable"]], ["d2"])

    def test_needs_card(self):
        self.assertFalse(g.needs_card(g.build(card(doc("d1", {**PAY, "value": "cash", "source": c.REMEMBERED})))))
        self.assertTrue(g.needs_card(g.build(card(doc("d1", dict(PAY))))))


class Pure(unittest.TestCase):
    def test_no_framework(self):
        import ast
        tree = ast.parse((ROOT / "apps/pearnly_core/pearnly_core/write_precheck/guide.py").read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
            elif isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
        self.assertLessEqual(names, {"__future__", "typing", "pearnly_core"})


if __name__ == "__main__":
    unittest.main()
