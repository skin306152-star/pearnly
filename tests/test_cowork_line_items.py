"""Cowork LINE 识别后按行追问存货对照（11-06 按行选物料与库存，评估 2026-11-06 第 15 节 C7）。不连库、不依赖 Frappe：

- 识别完有定不了的行：随卡片一起发一条提问（新物料列全、四个按钮）；没有就不问；正在问别的票时排队；
- 全部新建存货 / 全部记费用：一次记进账套记忆，接着问排队的下一张；
- 逐项：一项一问，「选已有存货」打字找（库存品与非库存品），点序号记下；旧按钮、别的项的按钮不生效；
- 取消只停这张的提问，不记任何选择。
"""
import json
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "pearnly_cowork"))
sys.path.insert(0, str(ROOT / "apps" / "pearnly_core"))


class _Error(Exception):
    pass


def _stub_frappe():
    frappe = types.ModuleType("frappe")
    frappe._ = lambda text, *a, **k: text.rsplit(".", 1)[-1] if text.startswith("cowork.") else text  # 键 → 末段，按钮 20 字内
    frappe.ValidationError = _Error
    frappe.PermissionError = _Error
    frappe.clear_messages = lambda: None
    frappe.db = types.SimpleNamespace(rollback=lambda: None, commit=lambda: None)
    return frappe


class LineItemsTest(unittest.TestCase):
    def setUp(self):
        self.saved, self.replies, self.session = [], [], {}
        self.needed = {"H1": [{"kind": "item", "subject": "purchase|t|box", "label": "Box", "expense": True},
                              {"kind": "item", "subject": "purchase|t|bag", "label": "Bag", "expense": True},
                              {"kind": "account", "subject": "purchase|t", "label": "Shop"},
                              {"kind": "item", "subject": "purchase|t|ice", "label": "Ice", "expense": False}],
                       "H2": [{"kind": "item", "subject": "purchase|t|cup", "label": "Cup", "expense": True}],
                       "H3": []}
        session = self.session
        flow = types.ModuleType("pearnly_cowork.line.flow")
        flow.choices = lambda user: json.loads(json.dumps(session.get(user, {})))
        flow.keep = lambda user, payload: session.__setitem__(user, json.loads(json.dumps(payload)))
        products = [{"code": "ICE01", "name": "Ice cube"}, {"code": "FRT01", "name": "Freight"}]
        # 推送侧（钩子 ``cowork_line_items`` → `push.application.line_items`）的替身
        side = types.SimpleNamespace(
            rows=lambda history: [{"subject": row["subject"], "label": row["label"]} for row in self.needed[history]
                                  if row["kind"] == "item" and row.get("expense")],
            title=lambda history: "Shop · IV" + history,
            save=lambda history, items: self.saved.append((history, items)),
            search=lambda history, text, limit: [row for row in products
                                                 if text.casefold() in (row["code"] + row["name"]).casefold()][:limit])
        line = types.SimpleNamespace(reply=lambda token, messages: self.replies.append(messages))
        core = types.ModuleType("pearnly_core")
        core.__path__ = [str(ROOT / "apps/pearnly_core/pearnly_core")]  # 其余子模块（共用写账规则）照真的读
        core.line = line
        frappe = _stub_frappe()
        frappe.get_hooks = lambda name: ["side"] if name == "cowork_line_items" else []
        frappe.get_attr = lambda path: getattr(side, path.rsplit(".", 1)[-1])
        modules = {"frappe": frappe, "pearnly_core": core, "pearnly_core.line": line, "pearnly_cowork.line.flow": flow}
        self.previous = {name: sys.modules.get(name) for name in [*modules, "pearnly_cowork.line.items",
                                                                   "pearnly_cowork.line",
                                                                   "pearnly_core.express_rules"]}
        sys.modules.update(modules)
        package = types.ModuleType("pearnly_cowork.line")
        package.__path__ = [str(ROOT / "apps/pearnly_cowork/pearnly_cowork/line")]
        package.flow = flow
        sys.modules["pearnly_cowork.line"] = package
        sys.modules.pop("pearnly_cowork.line.items", None)
        import importlib

        self.items = importlib.import_module("pearnly_cowork.line.items")
        self.addCleanup(self._restore)

    def _restore(self):
        for name, module in self.previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def labels(self, message):
        return [row["action"]["label"] for row in message["quickReply"]["items"]]

    def tap(self, message, label):
        data = next(row["action"]["data"] for row in message["quickReply"]["items"] if row["action"]["label"] == label)
        self.items.answer("U1", data, "tok")
        return self.replies[-1][-1]

    def test_a_card_gets_one_question_listing_the_new_items_and_the_next_card_waits(self):
        first = self.items.queue("U1", "H1")
        self.assertEqual(first["text"].splitlines(), ["Shop · IVH1", "new", "1. Box", "2. Bag"])
        self.assertEqual(self.labels(first), ["allNew", "allExpense",
                                              "each", "Cancel"])
        self.assertIsNone(self.items.queue("U1", "H2"))  # 正在问 H1：排队
        self.assertIsNone(self.items.queue("U1", "H3"))  # 没有要问的行：不问
        reply = self.tap(first, "allExpense")
        self.assertEqual(self.saved, [("H1", [{"kind": "item", "subject": "purchase|t|box", "value": "__expense__", "label": "Box"},
                                              {"kind": "item", "subject": "purchase|t|bag", "value": "__expense__", "label": "Bag"}])])
        self.assertEqual(self.replies[-1][0]["text"], "Saved")
        self.assertEqual(reply["text"].splitlines()[-1], "1. Cup")  # 接着问排队的 H2
        self.tap(reply, "allNew")
        self.assertEqual(self.saved[-1], ("H2", [{"kind": "item", "subject": "purchase|t|cup", "value": "__new__", "label": "Cup"}]))
        self.assertNotIn("items", self.session["U1"])

    def test_one_by_one_with_a_typed_search_and_stale_buttons(self):
        first = self.items.queue("U1", "H1")
        row = self.tap(first, "each")
        self.assertEqual(row["text"], "1/2 · Box")
        self.assertEqual(self.labels(row), ["newOne", "expenseLine",
                                            "pick", "Cancel"])
        stale = self.tap(first, "allNew")  # 整张的按钮已经不是当前这一步
        self.assertEqual(stale["text"], "This option is no longer current")
        self.tap(row, "pick")
        self.assertTrue(self.items.search("U1", "zzz", "tok"))
        self.assertEqual(self.replies[-1][0]["text"], "No match for “{0}”".format("zzz"))
        self.assertTrue(self.items.search("U1", "ice", "tok"))
        found = self.replies[-1][-1]
        self.assertEqual(found["text"].splitlines()[1:], ["1. ICE01 Ice cube"])
        nxt = self.tap(found, "1")
        self.assertEqual(self.saved[-1][1][0]["value"], "ICE01")
        self.assertEqual(nxt["text"], "2/2 · Bag")
        self.assertFalse(self.items.search("U1", "ice", "tok"))  # 不在选已有存货这一步：交给别的处理
        self.tap(row, "newOne")  # 第 1 项的旧按钮：不生效
        self.assertEqual(self.replies[-1][-1]["text"], "This option is no longer current")
        self.tap(nxt, "expenseLine")
        self.assertEqual(self.saved[-1][1][0], {"kind": "item", "subject": "purchase|t|bag", "value": "__expense__", "label": "Bag"})
        self.assertEqual(self.replies[-1][-1]["text"], "Saved")

    def test_cancel_stops_this_document_and_records_nothing(self):
        first = self.items.queue("U1", "H1")
        reply = self.tap(first, "Cancel")
        self.assertEqual((reply["text"], self.saved), ("Cancelled", []))
        self.assertNotIn("items", self.session["U1"])


if __name__ == "__main__":
    unittest.main()
