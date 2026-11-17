"""开始前卡片的两条守门（11-17 回归 erp.cc.operator.intake.sales 之后，主会话要求按类守住）：

1. 题类名：共用写账前置判定会出的每一类（``write_precheck.contracts.KINDS``），对话页四语都有名字，题卡不落到「其他」
   （科目题按分组叫费用 / 收入 / 存货科目，对方按方向叫供应商 / 客户）。
2. 同一口径：代录开始卡出题（``express_operator.api.precheck``）、记答案（``remember``）与任务重交（``tasks._resubmit``）都在
   同一段对话里做（``_in_task_conversation`` / ``assistant.guided.within``）；新加的页面接口调产品判定或记答案却不进对话，这里拦下。

    python3 tests/test_cc_card_same_basis.py
"""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "apps/pearnly_core/pearnly_core/write_precheck/contracts.py"
I18N = ROOT / "frontend/assistant/src/i18n.ts"
API = ROOT / "apps/pearnly_core/pearnly_core/express_operator/api.py"
TASKS = ROOT / "apps/pearnly_core/pearnly_core/express_operator/tasks.py"
# 前端按分组 / 方向另起名字的类（ask-shared.ts kindName）
NAMED_ELSEWHERE = {"account": ("expense", "income", "stockAccount"), "party": ("supplier", "customer")}
ENTER = ("_in_task_conversation", "guided.within")


def server_kinds() -> set:
    source = CONTRACTS.read_text(encoding="utf-8")
    block = source[source.index("KIND_GROUPS = {"):source.index("\nKINDS =")]
    extra = re.search(r"\nKINDS = frozenset\(KIND_GROUPS\) \| \{(.*?)\}", source).group(1)
    return set(re.findall(r'"([a-z_]+)":', block)) | set(re.findall(r'"([a-z_]+)"', extra))


def page_kind_names() -> list:
    text = I18N.read_text(encoding="utf-8")
    blocks = re.findall(r"precheck: \{\n\s+kind: \{(.*?)\} as Record<string, string>", text, re.S)
    return [dict(re.findall(r'(\w+): "([^"]*)"', block)) for block in blocks]


def _calls(function: ast.FunctionDef, names: tuple) -> tuple:
    """(这个函数里调了 ``names`` 之一的次数, 其中在 ``with <进对话>`` 里的次数)。"""
    found = [0, 0]

    def visit(node, within):
        if isinstance(node, ast.With):
            enters = any(isinstance(item.context_expr, ast.Call) and ast.unparse(item.context_expr.func).endswith(ENTER)
                         for item in node.items)
            for child in node.body:
                visit(child, within or enters)
            return
        if isinstance(node, ast.Call) and ast.unparse(node.func).endswith(names):
            found[0] += 1
            found[1] += 1 if within else 0
        for child in ast.iter_child_nodes(node):
            visit(child, within)

    for statement in function.body:
        visit(statement, False)
    return tuple(found)


def _function(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)


class KindNames(unittest.TestCase):
    def test_every_kind_the_server_asks_has_a_name_in_all_four_languages(self):
        langs = page_kind_names()
        self.assertEqual(len(langs), 4)
        for kind in sorted(server_kinds()):
            for index, names in enumerate(langs):
                for key in NAMED_ELSEWHERE.get(kind, (kind,)):
                    self.assertTrue(names.get(key), f"language {index}: no name for kind {kind!r} (key {key!r}); the card says Other")


class SameBasis(unittest.TestCase):
    def test_the_start_card_asks_and_saves_inside_the_task_conversation(self):
        for name, called in (("precheck", ("products.precheck",)), ("remember", ("precheck_products.remember",))):
            total, inside = _calls(_function(API, name), called)
            self.assertTrue(total, name)
            self.assertEqual(total, inside, f"api.{name} calls the product outside the task conversation")

    def test_a_task_hands_documents_in_again_inside_its_conversation(self):
        total, inside = _calls(_function(TASKS, "_resubmit"), ("products.submit",))
        self.assertTrue(total)
        self.assertEqual(total, inside, "tasks._resubmit submits outside the task conversation")

    def test_every_page_endpoint_that_judges_or_saves_a_start_card_takes_the_task(self):
        tree = ast.parse(API.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            whitelisted = any("whitelist" in ast.unparse(d) for d in node.decorator_list)
            total, inside = _calls(node, ("products.precheck", "precheck_products.remember"))
            if whitelisted and total:
                self.assertIn("task", [a.arg for a in node.args.args], node.name)
                self.assertEqual(total, inside, node.name)


if __name__ == "__main__":
    unittest.main()
