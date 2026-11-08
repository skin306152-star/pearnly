"""CC 工具一致检查（ADR-027 标准二，`scripts/check-cc-tools.py`）的比对规则：不导入 Frappe，用造的声明。

- 两产品同类工具（去掉 cowork_ / erp_ 前缀同名）逐项比：步骤名（做完 / 进行中）、术语表译文、参数名字与类型、必填、要不要「允许」、modes；
- 说明文字不比（业务说法可以不同）；只有一边有的工具不比（覆盖由产品地图的 CC 覆盖检查管）；
- 差异要在基线里（只许减少）：新出现的差异、该删的旧条目、重复条目都拦。
"""
import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_cc_tools", ROOT / "scripts" / "check-cc-tools.py")
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)


def tool(name, label="Listed documents", props=None, required=None, **extra):
    return {name: {"name": name, "label": label, "description": f"{name} words differ freely",
                   "input_schema": {"type": "object", "properties": props or {"search": {"type": "string"}},
                                    "required": required or []}, **extra}}


WORDS = {"Listed documents": ["查看了单据", "x", "y"], "Listing documents": ["正在查看单据", "x", "y"]}


class Compare(unittest.TestCase):
    def products(self, cowork, erp, c_steps=None, e_steps=None):
        return {"cowork": (cowork, c_steps or {}), "erp": (erp, e_steps or {})}

    def test_same_shape_is_consistent_and_descriptions_are_not_compared(self):
        steps = {"cowork_list_documents": "Listing documents"}
        products = self.products(tool("cowork_list_documents") | tool("cowork_only"), tool("erp_list_documents") | tool("erp_only"),
                                 steps, {"erp_list_documents": "Listing documents"})
        self.assertEqual(cc.compare(products, {"cowork": WORDS, "erp": WORDS}), [])

    def test_every_difference_is_named(self):
        products = self.products(
            tool("cowork_push", label="Pushed documents", props={"a": {"type": "string"}, "b": {"type": "integer"}},
                 required=["a"], approval=True, modes=["chat"]),
            tool("erp_push", label="Sent documents", props={"a": {"type": "integer"}, "c": {"type": "string"}}),
            {"cowork_push": "Pushing documents"}, {"erp_push": "Sending documents"})
        words = {"cowork": {"Pushed documents": ["推送了单据", "", ""]}, "erp": {"Pushed documents": ["推了", "", ""]}}
        self.assertEqual(cc.compare(products, words),
                         ["push:label", "push:step", "push:words", "push:params:a", "push:params:b", "push:params:c",
                          "push:required", "push:approval", "push:modes"])

    def test_operator_hooks_must_match_across_products(self):
        """代录是 CC 独有的，两产品接法要一样（ADR-027 A）：一边声明了另一边没有的键都列出来。"""
        hooks = {"cowork": {"pearnly_express_operator": ["allowed", "intake", "regroup"], "pearnly_write_precheck": ["check"]},
                 "erp": {"pearnly_express_operator": ["allowed", "intake", "target"], "pearnly_write_precheck": ["check"]}}
        self.assertEqual(cc.compare_operator(hooks), ["operator:pearnly_express_operator:regroup",
                                                      "operator:pearnly_express_operator:target"])
        real = cc.operator_hooks()  # 读真 hooks.py，不导入
        self.assertIn("intake", real["erp"]["pearnly_express_operator"])  # A 批 1：ERP 补上放文件
        self.assertEqual(sorted(cc.compare_operator(real)), sorted(
            line.split("  #")[0] for line in (ROOT / "scripts/impact/cc-tools-baseline.txt").read_text(encoding="utf-8").splitlines()
            if line.startswith("operator:")))

    def test_baseline_only_shrinks(self):
        self.assertEqual(cc.check(["a:label"], ["a:label"]), [])
        errors = cc.check(["a:label", "b:step"], ["a:label", "gone:modes", "gone:modes"])
        self.assertTrue(any("b:step" in e and "不一致" in e for e in errors))
        self.assertTrue(any("基线该删" in e and "gone:modes" in e for e in errors))
        self.assertTrue(any("重复" in e for e in errors))

    def test_baseline_file_entries_carry_reasons(self):
        text = (ROOT / "scripts/impact/cc-tools-baseline.txt").read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.strip() and not line.startswith("#"):
                self.assertIn("  # ", line, line)  # 每条登记都写原因
        self.assertIn("/candidate/scripts/check-cc-tools.py", (ROOT / "scripts/ci-candidate-checks.sh").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
