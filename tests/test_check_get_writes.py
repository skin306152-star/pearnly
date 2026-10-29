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

def _run(function, *args):
    return function(*args)

@frappe.whitelist(methods=["GET"])
def passed_along(name=None):
    # 写库的函数当参数交给包装函数去调（ERP / Cowork `work/api.py` 的 `_run(service.f, …)`，10-29 扫描器漏过）
    return _run(service.ensure_row, name)

@frappe.whitelist(methods=["GET"])
def passed_as_keyword(name=None):
    return frappe.call(fn=service.ensure_row, name=name)

@frappe.whitelist(methods=["GET"])
def passed_by_dotted_path(name=None):
    return frappe.get_attr("demo.service.ensure_row")(name)

@frappe.whitelist(methods=["GET"])
def shadowed(ensure_row=None):
    # 参数同名不是那个函数：只是把值传下去，不算调用
    return frappe.db.get_value("Row", ensure_row)
""")
        write(self.apps, "demo/demo/shadow.py", """
import frappe

def start():
    frappe.enqueue("demo.service.build_book")

@frappe.whitelist()
def tasks(start=0, limit=20):
    # 分页参数 `start` 与同模块的 `start()` 同名（Cowork `recon_center.api.tasks` 就是这样）：不是调用
    return frappe.get_all("Row", start=start, page_length=limit)
""")

    def test_get_endpoint_writing_through_a_helper_is_caught(self):
        found = checker.scan(apps=self.apps)
        self.assertIn("demo.api.lookup", found)
        self.assertIn("demo.service.ensure_row", " ".join(found["demo.api.lookup"][2]))
        self.assertIn("demo.api.queue_it", found)

    def test_a_writing_function_passed_as_an_argument_is_caught(self):
        found = checker.scan(apps=self.apps)
        for endpoint in ("demo.api.passed_along", "demo.api.passed_as_keyword", "demo.api.passed_by_dotted_path"):
            self.assertIn(endpoint, found)
            self.assertIn("demo.service.ensure_row", " ".join(found[endpoint][2]))

    def test_a_local_name_shadowing_a_function_is_not_a_call(self):
        found = checker.scan(apps=self.apps)
        self.assertNotIn("demo.api.shadowed", found)
        self.assertNotIn("demo.shadow.tasks", found)

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
