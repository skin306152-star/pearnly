"""采购凭证（补开收据 / 预扣税凭证）的版面与挂附件（11-08）。

- 公司主档地址 / 税号为空时，抬头那几行空着不印，不印出「None」（版面渲染要 jinja2：生产镜像里有，候选检查跑；开发检查跳过）；
- 挂附件经共用层整笔重来（两张单同时挂会死锁，实验栈流程 `erp.purchase.voucher-concurrent` 复现与回归），且出 PDF 在重来之外
  （wkhtmltopdf 一两秒，不该在锁里、也不该每次重来都再出一遍）。
"""
import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "apps/pearnly_erp_portal"
OUTPUT = APP / "pearnly_erp_portal/purchasing/output.py"

try:
    import jinja2
except ImportError:
    jinja2 = None


class Row(dict):
    __getattr__ = dict.get

    def get_formatted(self, key):
        return str(self.get(key, ""))


@unittest.skipIf(jinja2 is None, "jinja2 not installed")
class Header(unittest.TestCase):
    def render(self, company):
        env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(APP)))
        env.globals["_"] = lambda text: text
        doc = Row(name="PUR-1", system_no="PE2569-000001", doc_date="2026-11-08", supplier_name="S", doc_no="", supplier_tax_id="",
                  supplier_branch_no="", supplier_branch_name="", supplier_address="", currency="THB", notes="",
                  lines=[Row(description="x", qty=1, unit_price=1, line_total=1, wht_amount=0)])
        return env.get_template("pearnly_erp_portal/purchasing/voucher.html").render(
            doc=doc, company=company, kind="substitute_receipt", title="Substitute Receipt", print_style="")

    def header(self, html):
        return html.split("</h2>", 1)[1].split("<table", 1)[0]

    def test_an_empty_address_and_tax_id_print_nothing(self):
        head = self.header(self.render({"company_name": "บริษัท ก จำกัด", "tax_id": None, "address": None}))
        self.assertNotIn("None", head)
        self.assertEqual(head.strip(), "<p>บริษัท ก จำกัด</p>")

    def test_filled_lines_are_kept_one_per_line_and_escaped(self):
        head = self.header(self.render({"company_name": "A & B", "tax_id": "0105", "address": "1 Road"}))
        self.assertEqual(head.strip(), "<p>A &amp; B<br>0105<br>1 Road</p>")


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
