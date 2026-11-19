"""Cowork LINE 识别后按行追问存货对照（11-06 按行选物料与库存，评估 2026-11-06 第 15 节 C7）。不连库、不依赖 Frappe：

- 识别完有定不了的行：出卡前先发一条提问（新物料列全、四个按钮）；没有就不问（11-19 起一张一张、问完接什么由 `line.ask` 编排，
  见 tests/test_cowork_line_ask.py；这里只看一张票的题与记答）；
- 全部新建存货 / 全部记费用：一次记进账套记忆，这一张问完（不回「已保存」，接着由 `line.ask` 问判定剩下的题、出卡）；
- 逐项：一项一问，「选已有存货」打字找（库存品与非库存品），点序号记下；旧按钮、别的项的按钮不生效；
- 取消：回「取消」状态、不记任何选择（这张票照卡片「取消」处理在 `line.ask`）；
- 多仓账套还没记过默认仓（推送侧在行后面跟一项仓库，11-08）：行问完 / 点了「全部…」接着问一次仓，点一个记成默认仓；
- 答过的结果交回卡片列出（行名 → 选的值）。
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
                       "H3": [],
                       "H4": [{"kind": "item", "subject": "purchase|t|box", "label": "Box", "expense": True},
                              {"kind": "location", "subject": "*", "label": "",
                               "options": [{"code": "01", "name": "Main"}, {"code": "30", "name": "Branch"}]}]}
        self.payload = {}
        flow = types.ModuleType("pearnly_cowork.line.flow")
        products = [{"code": "ICE01", "name": "Ice cube"}, {"code": "FRT01", "name": "Freight"}]
        # 推送侧（钩子 ``cowork_line_items`` → `push.application.line_items`）的替身
        side = types.SimpleNamespace(
            rows=lambda history: [{"subject": row["subject"], "label": row["label"]} for row in self.needed[history]
                                  if row["kind"] == "item" and row.get("expense")]
            + [row for row in self.needed[history] if row["kind"] == "location"],
            title=lambda history: "Shop · IV" + history,
            save=lambda history, items: self.saved.append((history, items)),
            search=lambda history, text, limit: [row for row in products
                                                 if text.casefold() in (row["code"] + row["name"]).casefold()][:limit])
        line = types.SimpleNamespace(reply=lambda token, messages: self.replies.append(messages))  # 不该再自己回话
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
        """点一个按钮：回 (状态, 最后一条消息或 None)；会话载荷照真实那样每步存回再读（JSON 往返）。"""
        data = next(row["action"]["data"] for row in message["quickReply"]["items"] if row["action"]["label"] == label)
        status, messages = self.items.answer(self.payload, data)
        self.payload = json.loads(json.dumps(self.payload))
        return status, (messages[-1] if messages else None)

    def test_a_card_gets_one_question_listing_the_new_items_and_a_document_without_rows_is_not_asked(self):
        first = self.items.start(self.payload, "H1")
        self.assertEqual(first["text"].splitlines(), ["Shop · IVH1", "new", "1. Box", "2. Bag"])
        self.assertEqual(self.labels(first), ["allNew", "allExpense",
                                              "each", "Cancel"])
        self.assertIsNone(self.items.start({}, "H3"))  # 没有要问的行：不问
        status, reply = self.tap(first, "allExpense")
        self.assertEqual(self.saved, [("H1", [{"kind": "item", "subject": "purchase|t|box", "value": "__expense__", "label": "Box"},
                                              {"kind": "item", "subject": "purchase|t|bag", "value": "__expense__", "label": "Bag"}])])
        self.assertEqual((status, reply), (self.items.DONE, None))  # 问完：不回「已保存」，由 line.ask 接着问 / 出卡
        self.assertEqual(self.items.finish(self.payload), [{"label": "Box", "value": "expenseLine"},
                                                           {"label": "Bag", "value": "expenseLine"}])
        self.assertNotIn("items", self.payload)
        nxt = self.items.start(self.payload, "H2")
        self.assertEqual(nxt["text"].splitlines()[-1], "1. Cup")
        self.tap(nxt, "allNew")
        self.assertEqual(self.saved[-1], ("H2", [{"kind": "item", "subject": "purchase|t|cup", "value": "__new__", "label": "Cup"}]))
        self.assertEqual(self.replies, [])  # 回话都由 line.ask 发

    def test_one_by_one_with_a_typed_search_and_stale_buttons(self):
        first = self.items.start(self.payload, "H1")
        _status, row = self.tap(first, "each")
        self.assertEqual(row["text"], "1/2 · Box")
        self.assertEqual(self.labels(row), ["newOne", "expenseLine",
                                            "pick", "Cancel"])
        status, stale = self.tap(first, "allNew")  # 整张的按钮已经不是当前这一步
        self.assertEqual((status, stale["text"]), (self.items.STALE, "This option is no longer current"))
        self.tap(row, "pick")
        found = self.items.search(self.payload, "zzz")
        self.assertEqual(found[0]["text"], "No match for “{0}”".format("zzz"))
        found = self.items.search(self.payload, "ice")[-1]
        self.assertEqual(found["text"].splitlines()[1:], ["1. ICE01 Ice cube"])
        status, nxt = self.tap(found, "1")
        self.assertEqual(self.saved[-1][1][0]["value"], "ICE01")
        self.assertEqual(nxt["text"], "2/2 · Bag")
        self.assertIsNone(self.items.search(self.payload, "ice"))  # 不在选已有存货这一步：交给别的处理
        status, stale = self.tap(row, "newOne")  # 第 1 项的旧按钮：不生效
        self.assertEqual(stale["text"], "This option is no longer current")
        status, done = self.tap(nxt, "expenseLine")
        self.assertEqual(self.saved[-1][1][0], {"kind": "item", "subject": "purchase|t|bag", "value": "__expense__", "label": "Bag"})
        self.assertEqual((status, done), (self.items.DONE, None))
        self.assertEqual(self.items.finish(self.payload), [{"label": "Box", "value": "ICE01 Ice cube"},
                                                           {"label": "Bag", "value": "expenseLine"}])

    def test_cancel_stops_this_document_and_records_nothing(self):
        first = self.items.start(self.payload, "H1")
        status, reply = self.tap(first, "Cancel")
        self.assertEqual((status, reply, self.saved), (self.items.CANCELLED, None, []))
        self.assertEqual(self.items.finish(self.payload), [])
        self.assertNotIn("items", self.payload)

    def test_a_multi_warehouse_book_asks_the_warehouse_once_after_the_lines(self):
        first = self.items.start(self.payload, "H4")
        self.assertEqual(first["text"].splitlines(), ["Shop · IVH4", "new", "1. Box"])  # 仓库不进新物料清单
        _status, ask = self.tap(first, "allNew")
        self.assertEqual(ask["text"].splitlines(), ["Warehouse", "1. 01 Main", "2. 30 Branch"])
        self.assertEqual(self.labels(ask), ["01 Main", "30 Branch", "Cancel"])
        status, done = self.tap(ask, "30 Branch")
        self.assertEqual(self.saved[-1], ("H4", [{"kind": "location", "subject": "*", "value": "30", "label": ""}]))
        self.assertEqual((status, done), (self.items.DONE, None))
        self.assertEqual(self.items.finish(self.payload), [{"label": "Box", "value": "newOne"},
                                                           {"label": "Warehouse", "value": "30 Branch"}])

    def test_one_by_one_reaches_the_warehouse_question_last(self):
        first = self.items.start(self.payload, "H4")
        _status, row = self.tap(first, "each")
        self.assertEqual(row["text"], "1/1 · Box")
        _status, ask = self.tap(row, "expenseLine")
        self.assertEqual(ask["text"].splitlines()[0], "Warehouse")
        _status, stale = self.tap(row, "newOne")  # 物料那一项的旧按钮：不生效
        self.assertEqual(stale["text"], "This option is no longer current")
        self.tap(ask, "01 Main")
        self.assertEqual(self.saved[-1][1], [{"kind": "location", "subject": "*", "value": "01", "label": ""}])


if __name__ == "__main__":
    unittest.main()
