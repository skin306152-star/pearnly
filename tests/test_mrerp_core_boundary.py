"""共用层 MR.ERP 规则（`pearnly_core.mrerp`，评估 2026-11-02 §13 第 5 步）的边界：多产品共用，不认识任何产品。

- 不导入 Frappe、任何产品 App（Cowork / ERP / DMS / 控制面）；共用层里只用自己、Express 写账规则、票种决策表，会话锁只给导入用；
- 连网只在登录会话（`session`：requests、DNS）与导入（`push.transport`：会话锁、计时）；读写 xlsx 的 openpyxl 只在生成器与报告解析；
- 菜单、主档默认值、要人选的项、账套目录、地址校验、「单号已存在」这几条规则只用标准库的纯计算部分。

只读源码（ast），不需要 Frappe，CI 直接跑。
"""
import ast
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "apps/pearnly_core/pearnly_core/mrerp"
PRODUCTS = ("frappe", "pearnly_cowork", "pearnly_erp_portal", "pearnly_dms", "pearnly_control")
SHARED = ("pearnly_core.mrerp", "pearnly_core.express_rules", "pearnly_core.tax_documents")
STDLIB = {"__future__", "collections", "copy", "dataclasses", "datetime", "decimal", "hashlib", "html", "io",
          "ipaddress", "json", "logging", "os", "re", "typing", "urllib.parse", "zipfile"}
# 有外部副作用或第三方依赖的，逐个登记
EXTRA = {
    "session.py": {"requests", "socket"},
    "push/transport.py": {"time", "contextlib"},
    # 11-11：登录 MR.ERP 的唯一入口（同账号锁、被踢标记、会话复用），推送、读菜单 / 主档、目录采集都经它
    "account.py": {"contextlib", "pearnly_core.session_lock"},
    # 11-11 核账本的 MR.ERP 一侧：经共用会话入口读列表（拿不到锁 = 核不了）
    "ledger_check.py": {"pearnly_core.session_lock"},
    # 11-09 风险 7：复用的会话存在不淘汰的状态 Redis（与账号锁同一个，键不带站点前缀，两产品共用）
    "session_cache.py": {"pearnly_core.execution"},
    "kick_mark.py": {"pearnly_core.execution", "sys"},
    # 2026-11-03 由 Cowork 下沉的执行与读取（纯网络，凭据由调用方交进来）：只经会话 / 导入连网，各拿账号锁
    "push/execute.py": {"pearnly_core.session_lock"},
    "menu_read.py": {"pearnly_core.session_lock"},
    "collect.py": {"time", "pearnly_core.session_lock"},
    "push/report_parser.py": {"openpyxl"},
    "push/xlsx/generator.py": {"openpyxl", "openpyxl.styles"},
}
PURE_RULES = {"menus.py", "masters.py", "needs.py", "catalog.py", "urls.py", "push/duplicates.py",
              "push/user_data.py"}
PURE_STDLIB = {"__future__", "copy", "datetime", "html", "ipaddress", "json", "re", "typing", "urllib.parse"}


def imports(path):
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                raise AssertionError(f"{path}: use absolute imports inside pearnly_core.mrerp")
            names.add(node.module or "")
    return names


class SharedMrErpBoundary(unittest.TestCase):
    def modules(self):
        found = {path.relative_to(PACKAGE).as_posix(): path for path in sorted(PACKAGE.rglob("*.py"))}
        self.assertIn("push/transport.py", found)
        return found

    def test_never_imports_frappe_or_a_product(self):
        for name, path in self.modules().items():
            for module in imports(path):
                with self.subTest(module=name, imports=module):
                    self.assertFalse(module.split(".")[0] in PRODUCTS, f"{name} imports {module}")

    def test_network_and_third_party_only_where_registered(self):
        for name, path in self.modules().items():
            allowed = STDLIB | EXTRA.get(name, set())
            for module in imports(path):
                with self.subTest(module=name, imports=module):
                    self.assertTrue(module in allowed or module.startswith(SHARED), f"{name} imports {module}")

    def test_choice_and_catalog_rules_are_pure(self):
        modules = self.modules()
        for name in sorted(PURE_RULES):
            for module in imports(modules[name]):
                with self.subTest(module=name, imports=module):
                    self.assertTrue(module in PURE_STDLIB or module.startswith(SHARED), f"{name} imports {module}")


if __name__ == "__main__":
    unittest.main()
