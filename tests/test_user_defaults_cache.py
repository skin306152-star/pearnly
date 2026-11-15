"""用户默认值写入后提交之后再清缓存（`pearnly_core.account.user_defaults`，主会话 11-15「切换当前公司间歇不生效」）。

Frappe 16.33.0 的 `set_default` 在提交前就清缓存：并发请求在提交前读库会把旧值回填进缓存；之后再设同一个值 Frappe 判「没变」、
不清缓存，旧值一直卡着。这里用替身按真实顺序走一遍：写（未提交）→ 另一个请求读、回填 → 提交 → 读；再走「卡住后再设同一家」。
真 Frappe、两条连接的复现在 scripts/erp-active-company-race-lab-verify.py（实验栈）。
"""
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps" / "pearnly_core"))


class Db:
    """已提交的值 + 本事务未提交的值；提交时跑 after_commit 回调（同 Frappe 的 CallbackManager）。"""

    def __init__(self):
        self.committed, self.pending, self.callbacks = {}, {}, []
        self.after_commit = types.SimpleNamespace(add=self.callbacks.append)

    def commit(self):
        self.committed.update(self.pending)
        self.pending.clear()
        for callback in self.callbacks:
            callback()
        self.callbacks.clear()


class Cache:
    def __init__(self):
        self.values = {}


frappe = types.ModuleType("frappe")
frappe.flags = types.SimpleNamespace(in_install=False)
frappe.session = types.SimpleNamespace(user="boss@x")
frappe.db = Db()
frappe.cache_store = Cache()
manager = types.ModuleType("frappe.cache_manager")
manager.clear_defaults_cache = lambda user=None: frappe.cache_store.values.pop(f"defaults::{user}", None)
frappe.clear_cache = lambda user=None: None
frappe.get_all = lambda doctype, filters=None, **kw: (
    [types.SimpleNamespace(defvalue=frappe.db.committed[(filters["parent"], filters["defkey"])])]
    if (filters["parent"], filters["defkey"]) in frappe.db.committed else [])
sys.modules["frappe"] = frappe
sys.modules["frappe.cache_manager"] = manager

from pearnly_core.account import user_defaults  # noqa: E402


def framework_set(key, value, user):
    """同 Frappe `set_default`：值没变直接返回；变了就写（未提交）、插一行（钩子）、当场清缓存。"""
    if frappe.db.committed.get((user, key)) == value and (user, key) not in frappe.db.pending:
        return
    frappe.db.pending[(user, key)] = value
    user_defaults.after_write({"parent": user})
    manager.clear_defaults_cache(user)


def other_request_reads(user):
    """另一个请求（另一条连接，只看得到已提交的）：缓存没有就读库并回填。"""
    key = f"defaults::{user}"
    if key not in frappe.cache_store.values:
        frappe.cache_store.values[key] = {k: v for (u, k), v in frappe.db.committed.items() if u == user}
    return frappe.cache_store.values[key]


class Tests(unittest.TestCase):
    def setUp(self):
        frappe.db.committed, frappe.db.pending, frappe.db.callbacks[:] = {("boss@x", "company"): "CO-A"}, {}, []
        frappe.cache_store.values.clear()

    def test_a_read_between_write_and_commit_does_not_leave_the_old_value(self):
        framework_set("company", "CO-B", "boss@x")
        self.assertEqual(other_request_reads("boss@x")["company"], "CO-A")    # 竞态：提交前另一个请求回填了旧值
        frappe.db.commit()
        self.assertEqual(other_request_reads("boss@x")["company"], "CO-B")    # 提交后钩子再清一次：读到新值
        self.assertEqual(user_defaults.current("company", "boss@x"), "CO-B")

    def test_setting_the_same_company_again_unsticks_an_old_cached_value(self):
        frappe.db.committed[("boss@x", "company")] = "CO-B"
        frappe.cache_store.values["defaults::boss@x"] = {"company": "CO-A"}   # 之前被回填、卡住的旧值
        framework_set("company", "CO-B", "boss@x")                             # Frappe 判没变：不清
        self.assertEqual(other_request_reads("boss@x")["company"], "CO-A")
        user_defaults.refresh("boss@x")                                        # 切公司时当场清、提交后再清
        frappe.db.commit()
        self.assertEqual(other_request_reads("boss@x")["company"], "CO-B")

    def test_installs_are_left_alone(self):
        frappe.flags.in_install = True
        try:
            user_defaults.after_write({"parent": "boss@x"})
            self.assertEqual(frappe.db.callbacks, [])
        finally:
            frappe.flags.in_install = False


if __name__ == "__main__":
    unittest.main()
