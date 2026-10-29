"""GET 可达接口写库检查（scripts/check-get-writes.py）：拦得住、不误伤。

10-29 手机盘点空仓保存 404 的根因是 GET 接口里登记盘点行被回滚；这里守住扫描器本身。
"""

import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_get_writes", ROOT / "scripts" / "check-get-writes.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def write(base: Path, rel: str, text: str):
    path = base / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class CheckGetWritesTest(unittest.TestCase):
    def setUp(self):
        self.apps = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.apps)
        write(self.apps, "demo/demo/__init__.py", "")
        write(self.apps, "demo/demo/service.py", """
import frappe

def ensure_row(name):
    doc = frappe.get_doc({"doctype": "Row", "name": name})
    doc.insert(ignore_permissions=True)
    return doc.name

def build_book():
    import io, openpyxl
    book = openpyxl.Workbook(); buffer = io.BytesIO(); book.save(buffer)
    return buffer.getvalue()

def resolve(name, create=False):
    return frappe.db.get_value("Row", name) or (ensure_row(name) if create else None)
""")
        write(self.apps, "demo/demo/api.py", """
import frappe
from demo import service
from demo.service import build_book

@frappe.whitelist(allow_guest=True)
def lookup(name=None):
    return service.ensure_row(name)

@frappe.whitelist(methods=["POST"])
def lookup_post(name=None):
    return service.ensure_row(name)

@frappe.whitelist()
def template():
    return build_book()

@frappe.whitelist(methods=["GET"])
def resolve_only(name=None):
    return service.resolve(name, create=False)

@frappe.whitelist()
def queue_it():
    frappe.enqueue("demo.service.build_book")
""")

    def test_get_endpoint_writing_through_a_helper_is_caught(self):
        found = checker.scan(apps=self.apps)
        self.assertIn("demo.api.lookup", found)
        self.assertIn("demo.service.ensure_row", " ".join(found["demo.api.lookup"][2]))
        self.assertIn("demo.api.queue_it", found)

    def test_post_only_and_in_memory_workbooks_are_not_flagged(self):
        found = checker.scan(apps=self.apps)
        self.assertNotIn("demo.api.lookup_post", found)
        self.assertNotIn("demo.api.template", found)

    def test_a_pass_through_entry_stops_the_walk(self):
        self.assertIn("demo.api.resolve_only", checker.scan(apps=self.apps))
        self.assertNotIn("demo.api.resolve_only", checker.scan(frozenset({"demo.service.resolve"}), apps=self.apps))

    def test_the_mobile_stocktake_lookup_regression_is_caught(self):
        """把手机盘点查码改回 GET（10-29 前的样子）：扫描器必须报出来，且豁免清单里没有它。"""
        source = ROOT / "apps" / "pearnly_erp_portal" / "pearnly_erp_portal" / "inventory" / "line_mobile.py"
        text = source.read_text(encoding="utf-8")
        marker = '@frappe.whitelist(allow_guest=True, methods=["POST"])\ndef api_line_stocktake_lookup('
        self.assertIn(marker, text, "api_line_stocktake_lookup must stay POST only")
        copy = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, copy)
        shutil.copytree(ROOT / "apps" / "pearnly_erp_portal", copy / "pearnly_erp_portal")
        target = copy / "pearnly_erp_portal" / "pearnly_erp_portal" / "inventory" / "line_mobile.py"
        target.write_text(text.replace(marker, '@frappe.whitelist(allow_guest=True)\ndef api_line_stocktake_lookup('), encoding="utf-8")
        exempt, passes, broken = checker.load_exempt()
        self.assertEqual(broken, [])
        self.assertNotIn("pearnly_erp_portal.inventory.line_mobile.api_line_stocktake_lookup", exempt)
        found = checker.scan(frozenset(passes), apps=copy)
        self.assertIn("pearnly_erp_portal.inventory.line_mobile.api_line_stocktake_lookup", found)


if __name__ == "__main__":
    unittest.main()
