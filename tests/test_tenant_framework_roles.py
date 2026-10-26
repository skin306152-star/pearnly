#!/usr/bin/env python3
"""产品站点的框架角色（10-26 testcowork 个人页露出原生「权限」「创建用户电子邮件」的根因与防再犯）：

- 开户 `ensure_local_user` 只在**新建**账号时给临时的 System Manager；已有账号重绑身份（换号池的 `bind_user`）不加角色
  （10-20 换池给 testcowork 管理员加回了 System Manager）；
- 产品站点上业务账号不许被加上 System Manager（只有开户新建那一刻举旗放行）；
- 本人保存个人页不能改动自己的角色（隐藏的原生角色编辑器重写角色表，10-26 丢了 Desk User）；
- 页头入口与快捷键白名单只读产品 `pearnly_navigation` 的声明（`navigation.head_entries`）。

离线跑：frappe 换成假的，不连站点。
"""
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))


class Refused(Exception):
    pass


class Dict(dict):
    """同 `frappe._dict`：属性读写都落在字典上。"""
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


def fake_frappe():
    module = types.ModuleType("frappe")
    module.conf = Dict(pearnly_product="cowork")
    module.flags = Dict()
    module.session = Dict(user="Administrator")
    module._ = lambda text: text
    module.PermissionError = Refused
    module._dict = Dict

    def throw(message, exc=Exception):
        raise exc(message)

    module.throw = throw
    module.get_hooks = lambda name: {}
    module.whitelist = lambda *args, **kwargs: (lambda fn: fn)
    sys.modules["frappe"] = module
    return module


FRAPPE = fake_frappe()
sys.modules.setdefault("pearnly_core.identity", types.ModuleType("pearnly_core.identity"))
from pearnly_core.desk import permissions  # noqa: E402


class Role(Dict):
    pass


class User:
    def __init__(self, name, roles, before=None, new=False):
        self.name = name
        self.roles = [Role(role=role) for role in roles]
        self._before = before
        self._new = new

    def is_new(self):
        return self._new

    def get_doc_before_save(self):
        return self._before

    def set(self, key, rows):
        assert key == "roles"
        self.roles = [Role(row) for row in rows]


class GuardUserRoles(unittest.TestCase):
    def setUp(self):
        FRAPPE.conf = Dict(pearnly_product="cowork")
        FRAPPE.flags = Dict()
        FRAPPE.session = Dict(user="Administrator")

    def test_a_business_account_cannot_gain_system_manager(self):
        before = User("boss@x.co", ["Cowork Admin", "Desk User"])
        with self.assertRaises(Refused):
            permissions.guard_user_roles(User("boss@x.co", ["Cowork Admin", "Desk User", "System Manager"], before))
        FRAPPE.flags.pearnly_grant_system_manager = True  # 开户新建账号那一刻
        permissions.guard_user_roles(User("boss@x.co", ["Cowork Admin", "System Manager"], before))

    def test_keeping_or_dropping_system_manager_is_fine_and_the_control_plane_is_untouched(self):
        before = User("boss@x.co", ["System Manager", "Cowork Admin"])
        permissions.guard_user_roles(User("boss@x.co", ["System Manager", "Cowork Admin"], before))
        permissions.guard_user_roles(User("boss@x.co", ["Cowork Admin"], before))
        FRAPPE.conf = Dict()  # 控制面 / 裸 Desk 不是产品站点
        permissions.guard_user_roles(User("ops@x.co", ["System Manager"], User("ops@x.co", [])))

    def test_saving_ones_own_profile_never_changes_ones_own_roles(self):
        FRAPPE.session = Dict(user="boss@x.co")
        before = User("boss@x.co", ["Cowork Admin", "Desk User"])
        saved = User("boss@x.co", ["Cowork Admin"], before)
        permissions.guard_user_roles(saved)
        self.assertEqual(sorted(row.role for row in saved.roles), ["Cowork Admin", "Desk User"])
        # 管理员在团队管理里改别人：照常
        other = User("staff@x.co", ["Cowork Work"], User("staff@x.co", ["Cowork Work", "Cowork Intake"]))
        permissions.guard_user_roles(other)
        self.assertEqual([row.role for row in other.roles], ["Cowork Work"])


class EnsureLocalUser(unittest.TestCase):
    def run_with(self, exists):
        from pearnly_core import provisioning

        added = []
        user = types.SimpleNamespace(roles=[], add_roles=lambda role: added.append((role, bool(FRAPPE.flags.get(
            "pearnly_grant_system_manager")))), insert=lambda **kw: None)
        db = types.SimpleNamespace(exists=lambda doctype, filters=None: exists if doctype == "User" else True,
                                   commit=lambda: None)
        with mock.patch.object(provisioning.frappe, "db", db, create=True), \
                mock.patch.object(provisioning.frappe, "get_doc", lambda *args, **kw: user, create=True), \
                mock.patch.object(provisioning, "identity", types.SimpleNamespace(issuer=lambda: "iss")), \
                mock.patch.object(provisioning, "tenant_default_language", lambda: "th"):
            result = provisioning.ensure_local_user("Boss@X.co", "Boss", "sub-1", "DEP-1")
        return result, added

    def test_only_a_new_account_gets_the_temporary_system_manager(self):
        result, added = self.run_with(exists=False)
        self.assertEqual((result["created"], added), ("created", [("System Manager", True)]))
        self.assertFalse(FRAPPE.flags.get("pearnly_grant_system_manager"))
        result, added = self.run_with(exists=True)  # 换号池时重绑已有管理员：不加角色
        self.assertEqual((result["created"], added), ("exists", []))


class HeadEntries(unittest.TestCase):
    def test_the_product_declaration_is_the_single_source(self):
        from pearnly_core.desk import navigation

        declared = {"cowork": {"product": "cowork", "actions": {"Form/User": ["Save"], "*": ["selection"]},
                               "shortcuts": ["ctrl+s"]}}
        with mock.patch.object(navigation, "_declarations", lambda: declared):
            self.assertEqual(navigation.head_entries("cowork"),
                             ({"Form/User": ["Save"], "*": ["selection"]}, ["ctrl+s"]))
            self.assertEqual(navigation.head_entries("erp"), ({}, []))  # 没声明 = 一个都不放


if __name__ == "__main__":
    unittest.main()
