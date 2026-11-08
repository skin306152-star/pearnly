"""采购凭证（补开收据 / 预扣税凭证）的版面与挂附件（11-08）。

- 挂附件经共用层整笔重来（两张单同时挂会死锁，实验栈流程 `erp.purchase.voucher-concurrent` 复现与回归），且出 PDF 在重来之外
  （wkhtmltopdf 一两秒，不该在锁里、也不该每次重来都再出一遍）。
"""
import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "apps/pearnly_erp_portal"
OUTPUT = APP / "pearnly_erp_portal/purchasing/output.py"


class AttachIsRetried(unittest.TestCase):
    def test_only_the_attach_step_is_retried_and_the_pdf_is_made_once_outside_it(self):
        tree = ast.parse(OUTPUT.read_text(encoding="utf-8"))
        funcs = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}

        def calls(name):
            return {ast.unparse(node.func) for node in ast.walk(funcs[name]) if isinstance(node, ast.Call)}

        self.assertIn("conflicts.retried", calls("voucher"))
        retried = next(node for node in ast.walk(funcs["voucher"]) if isinstance(node, ast.Call)
                       and ast.unparse(node.func) == "conflicts.retried")
        self.assertEqual(ast.unparse(retried.args[0]), "_attach_voucher")
        self.assertIn("_voucher_pdf", calls("voucher"))
        self.assertNotIn("_voucher_pdf", calls("_attach_voucher"))
        self.assertNotIn("get_pdf", calls("_attach_voucher"))
        # 每次重来都重新锁单、重新核状态、先看是不是已经挂上
        self.assertTrue({"access.get_document", "_voucher_allowed", "_generated"} <= calls("_attach_voucher"))


if __name__ == "__main__":
    unittest.main()
