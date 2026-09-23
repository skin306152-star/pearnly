"""Zero-I/O call-arity regression gate; no Frappe required.

Checks actual producer calls against actual consumer/output definitions. This
guards the known dropped-token regressions on every push, while candidate-image
CI separately executes the runtime and native-Site tests. It is not an E2E test.
"""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1] / "apps/pearnly_dms/pearnly_dms"
SOURCES = ("intake/flow.py", "line/text_router.py", "menu/menu_flow.py",
           "query/query_flow.py", "booking/conversation/booking_flow.py")


def tree(path):
    return ast.parse((ROOT / path).read_text(encoding="utf-8"))


def functions(module):
    return {node.name: node for node in module.body if isinstance(node, ast.FunctionDef)}


def arity_error(function, count, *, queued=False):
    positional = len(function.args.posonlyargs) + len(function.args.args)
    # out.enqueue deliberately requires all parameters, including defaults,
    # because the producer owns propagation of the webhook's reply token.
    minimum = positional if queued else positional - len(function.args.defaults)
    return not minimum <= count <= positional


def selected_names(expression):
    if isinstance(expression, ast.Name):
        return [expression.id]
    if isinstance(expression, ast.IfExp):
        return selected_names(expression.body) + selected_names(expression.orelse)
    raise AssertionError("Unsupported dynamic queue selector: " + ast.unparse(expression))


class CallContracts(unittest.TestCase):
    def test_queue_producers_match_consumer_definitions(self):
        intake = functions(tree("intake/flow.py"))
        checked, errors, dedup = 0, [], 0
        for path in SOURCES:
            module = tree(path)
            local = functions(module)
            for call in ast.walk(module):
                if not isinstance(call, ast.Call) or len(call.args) < 2:
                    continue
                if (getattr(call.func, "id", None) or getattr(call.func, "attr", None)) != "_enqueue":
                    continue
                target = call.args[1]
                name = getattr(target, "id", None) or getattr(target, "attr", None)
                candidates = [intake.get(name) if isinstance(target, ast.Attribute) else local.get(name)]
                if candidates == [None] and isinstance(target, ast.Name):
                    assignments = [node.value for node in ast.walk(module) if isinstance(node, ast.Assign)
                                   and any(isinstance(part, ast.Name) and part.id == name for part in node.targets)]
                    self.assertEqual(len(assignments), 1, f"Unresolved selector {path}:{call.lineno} {name}")
                    candidates = [local.get(branch) for branch in selected_names(assignments[0])]
                self.assertTrue(all(candidates), f"Unresolved queue consumer {path}:{call.lineno} {name}")
                self.assertFalse(any(isinstance(arg, ast.Starred) for arg in call.args))
                count = len(call.args) - 2
                for function in candidates:
                    if arity_error(function, count, queued=True):
                        errors.append(f"{path}:{call.lineno} -> {function.name}: {count} arguments")
                checked += 1
                dedup += name == "run_dedup"
        self.assertGreaterEqual(checked, 8)
        self.assertGreaterEqual(dedup, 3)
        self.assertEqual(errors, [])

    def test_query_text_calls_match_imported_output_helper(self):
        module = tree("query/query_flow.py")
        imported = [alias.name for node in module.body if isinstance(node, ast.ImportFrom)
                    and node.module == "pearnly_dms.line.out" for alias in node.names if alias.asname == "_push"]
        self.assertEqual(len(imported), 1)
        target = functions(tree("line/out.py"))[imported[0]]
        calls = [node for node in ast.walk(module) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == "_push"]
        self.assertGreaterEqual(len(calls), 5)
        self.assertEqual([call.lineno for call in calls if arity_error(target, len(call.args))], [])

    def test_checker_rejects_the_original_missing_argument_and_wrong_alias(self):
        self.assertTrue(arity_error(functions(tree("intake/flow.py"))["run_dedup"], 6, queued=True))
        self.assertTrue(arity_error(functions(tree("line/out.py"))["push_text"], 3))
        self.assertFalse(arity_error(functions(tree("line/out.py"))["push_or_reply_text"], 3))


if __name__ == "__main__":
    unittest.main(verbosity=2)
