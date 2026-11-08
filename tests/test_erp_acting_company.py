"""ERP「当前公司」在 CC 工具执行期间认对话挂的那家（盘点 2026-11-08 ①，主会话选 A，`pearnly_erp_portal.masters.company.acting`）：

- 同一个人能用两家公司（A 是默认）：B 公司的对话里，按当前公司取数的服务拿到的是 B（库存按 B 的仓过滤），拿不到 A 的；
- 只认本人可用的公司；中途被收回那家公司就拒，不回落到别家；
- 退出一定还原（工具出错也还原）、可嵌套；只对这个人生效，别的用户不受影响；放在 frappe.local，后台任务另起 frappe.local 带不过去；
- ERP 在 hooks 声明 `pearnly_assistant_tool_context`，对话没挂公司、或挂的那家已不可用时不改当前公司。

不连库：Frappe 用内存替身（用户默认值、User Permission、ERP Company、ERP Warehouse）。
"""
from __future__ import annotations

import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
ERP = ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal"
sys.path.insert(0, str(ROOT / "apps/pearnly_erp_portal"))
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))


class PermissionError_(Exception):
    pass


class ValidationError(Exception):
    pass


TABLES = {
    "ERP Company": [{"name": "A", "is_active": 1, "is_default": 1, "company_name": "Alpha"},
                    {"name": "B", "is_active": 1, "is_default": 0, "company_name": "Beta"}],
    "User Permission": [],
    "ERP Warehouse": [{"name": "WA", "company": "A"}, {"name": "WB", "company": "B"}],
}
DEFAULTS = {}


def get_all(doctype, filters=None, fields=None, order_by=None, pluck=None, **kwargs):
    rows = []
    for row in TABLES.get(doctype, []):
        ok = True
        for key, wanted in (filters or {}).items():
            if isinstance(wanted, (list, tuple)):
                if wanted[0] == "in" and row.get(key) not in wanted[1]:
                    ok = False
            elif row.get(key) != wanted:
                ok = False
        if ok:
            rows.append(row)
    if pluck:
        return [row.get(pluck) for row in rows]
    return [{f: row.get(f) for f in (fields or row)} for row in rows]


def throw(message, exc=None):
    raise (exc or ValidationError)(message)


frappe = types.ModuleType("frappe")
frappe.get_all = get_all
frappe.throw = throw
frappe._ = lambda text, *a, **k: text
frappe.PermissionError, frappe.ValidationError = PermissionError_, ValidationError
frappe.session = types.SimpleNamespace(user="u@x")
frappe.local = types.SimpleNamespace()
frappe.db = types.SimpleNamespace(get_default=lambda key: None)
defaults = types.ModuleType("frappe.defaults")
defaults.get_user_default = lambda key, user=None: DEFAULTS.get((user, key))
frappe.defaults = defaults
sys.modules["frappe"] = frappe
sys.modules["frappe.defaults"] = defaults

# company.py 只在函数里用到这几个依赖；替身只为导入成功
for name in ("pearnly_core.desk.list_filters", "pearnly_erp_portal.access.guard", "pearnly_erp_portal.access.listing",
             "pearnly_erp_portal.masters.contracts", "pearnly_erp_portal.masters.tax_ids"):
    sys.modules[name] = types.ModuleType(name)
pkg = {"pearnly_core": ROOT / "apps/pearnly_core/pearnly_core", "pearnly_core.desk": None,
       "pearnly_erp_portal": ERP, "pearnly_erp_portal.access": None, "pearnly_erp_portal.masters": ERP / "masters"}
for name, path in pkg.items():
    module = types.ModuleType(name)
    module.__path__ = [str(path)] if path else []
    sys.modules.setdefault(name, module)
sys.modules["pearnly_core.desk"].list_filters = sys.modules["pearnly_core.desk.list_filters"]
sys.modules["pearnly_erp_portal.access"].guard = sys.modules["pearnly_erp_portal.access.guard"]
sys.modules["pearnly_erp_portal.access"].listing = sys.modules["pearnly_erp_portal.access.listing"]
sys.modules["pearnly_erp_portal.masters"].contracts = sys.modules["pearnly_erp_portal.masters.contracts"]
sys.modules["pearnly_erp_portal.masters"].tax_ids = sys.modules["pearnly_erp_portal.masters.tax_ids"]

import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location("pearnly_erp_portal.masters.company", ERP / "masters/company.py")
company = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = company
sys.modules["pearnly_erp_portal.masters"].company = company
spec.loader.exec_module(company)


def warehouses_now(user=None):
    """「看这家公司的库存」= 当前公司名下的仓（同 `inventory.warehouses.names_of` 不给公司时）。"""
    return get_all("ERP Warehouse", {"company": company.active_company(user)}, pluck="name")


class Acting(unittest.TestCase):
    def setUp(self):
        frappe.session.user = "u@x"
        frappe.local = types.SimpleNamespace()
        TABLES["User Permission"] = [{"user": "u@x", "allow": "ERP Company", "for_value": "A"},
                                     {"user": "u@x", "allow": "ERP Company", "for_value": "B"}]
        DEFAULTS.clear()
        DEFAULTS[("u@x", "company")] = "A"

    def test_a_b_company_chat_never_sees_a_s_stock(self):
        self.assertEqual(warehouses_now(), ["WA"])
        with company.acting("B"):
            self.assertEqual(company.active_company(), "B")
            self.assertEqual(warehouses_now(), ["WB"])  # B 公司的对话里查库存：只有 B 的仓
            self.assertNotIn("WA", warehouses_now())
        self.assertEqual(warehouses_now(), ["WA"])  # 退出还原

    def test_only_companies_the_user_may_use(self):
        TABLES["User Permission"] = [{"user": "u@x", "allow": "ERP Company", "for_value": "A"}]
        with self.assertRaises(PermissionError_):
            with company.acting("B"):
                pass
        self.assertEqual(company.active_company(), "A")

    def test_revoked_mid_way_refuses_instead_of_falling_back(self):
        with company.acting("B"):
            TABLES["User Permission"] = [{"user": "u@x", "allow": "ERP Company", "for_value": "A"}]
            with self.assertRaises(PermissionError_):
                company.active_company()

    def test_restored_after_errors_nested_and_per_user(self):
        with self.assertRaises(RuntimeError):
            with company.acting("B"):
                raise RuntimeError("tool failed")
        self.assertEqual(company.active_company(), "A")
        with company.acting("B"):
            with company.acting("A"):
                self.assertEqual(company.active_company(), "A")
            self.assertEqual(company.active_company(), "B")
            with company.acting(None):  # 空 = 不改
                self.assertEqual(company.active_company(), "B")
            # 别的用户（例如工具里以另一个身份读）不受影响
            TABLES["User Permission"].append({"user": "v@x", "allow": "ERP Company", "for_value": "A"})
            self.assertEqual(company.active_company("v@x"), "A")
        self.assertIsNone(getattr(frappe.local, company._ACTING))

    def test_a_fresh_frappe_local_does_not_carry_it(self):
        with company.acting("B"):
            frappe.local = types.SimpleNamespace()  # 后台任务 / 下一个请求另起 frappe.local
            self.assertEqual(company.active_company(), "A")


class Declared(unittest.TestCase):
    def test_erp_declares_the_tool_context_and_falls_back_when_not_filed(self):
        hooks = (ERP / "hooks.py").read_text(encoding="utf-8")
        self.assertIn('pearnly_assistant_tool_context = ["pearnly_erp_portal.masters.cc_tools.acting_filed"]', hooks)
        source = (ERP / "masters/cc_tools.py").read_text(encoding="utf-8")
        self.assertIn("companies.acting(company) if company and company in companies.allowed_companies() else nullcontext()",
                      source)
        tools = (ROOT / "apps/pearnly_core/pearnly_core/assistant/tools.py").read_text(encoding="utf-8")
        self.assertIn('frappe.get_hooks("pearnly_assistant_tool_context")', tools)
        count = (ERP / "inventory/count.py").read_text(encoding="utf-8")
        self.assertNotIn("company=None) -> list[dict]", count)  # P1-1 逐个函数加的公司参数已改回（选 A）


if __name__ == "__main__":
    unittest.main()
