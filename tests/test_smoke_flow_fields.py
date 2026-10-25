"""写 Express 的冒烟脚本（scripts/smoke/*_flow.py）读的字段必须是 DocType 里真有的列（验收 10-25：Cowork 冒烟写成
account_set_key，真表叫 account_key，到实验栈才报 1054）。按脚本里 `frappe.get_all / db.get_value` 的字面量字段名核 DocType JSON。"""
import ast
import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCTYPES = {
    "Cowork Push Record": "apps/pearnly_cowork/pearnly_cowork/pearnly_cowork/doctype/cowork_push_record/cowork_push_record.json",
    "Cowork ERP Connection": "apps/pearnly_cowork/pearnly_cowork/pearnly_cowork/doctype/cowork_erp_connection/cowork_erp_connection.json",
    "Cowork OCR History": "apps/pearnly_cowork/pearnly_cowork/pearnly_cowork/doctype/cowork_ocr_history/cowork_ocr_history.json",
}
STANDARD = {"name", "owner", "creation", "modified", "modified_by", "docstatus"}


def columns(doctype):
    data = json.loads((ROOT / DOCTYPES[doctype]).read_text(encoding="utf-8"))
    return STANDARD | {field["fieldname"] for field in data["fields"]}


def literal(node):
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None


class SmokeFields(unittest.TestCase):
    def test_cowork_flow_reads_real_columns(self):
        path = ROOT / "scripts/smoke/cowork_express_flow.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {node.targets[0].id: node.value.value for node in ast.walk(tree)
                 if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                 and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)}
        checked = 0
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("get_all", "get_list", "get_value") and node.args):
                continue
            first = node.args[0]
            doctype = names.get(first.id) if isinstance(first, ast.Name) else literal(first)
            if doctype not in DOCTYPES:
                continue
            wanted = []
            for keyword in node.keywords:
                if keyword.arg in ("fields", "filters"):
                    value = literal(keyword.value)
                    wanted += list(value) if isinstance(value, (list, dict)) else []
            if node.func.attr == "get_value" and len(node.args) > 2:
                value = literal(node.args[2])
                wanted += [value] if isinstance(value, str) else list(value or [])
            for field in wanted:
                if isinstance(field, str):
                    self.assertIn(field, columns(doctype), f"{path.name}: {doctype} has no column {field}")
                    checked += 1
        self.assertGreaterEqual(checked, 8)


if __name__ == "__main__":
    unittest.main()
