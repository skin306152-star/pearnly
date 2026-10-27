"""10-27 回归基线 B 查出的无守卫接口修正（主会话）：内存替身，不连库。

- DMS 推送记录 `push_log.api_list / api_status`、共用层连接清单 `endpoints.api_list_endpoints / api_test_connection`：对外入口删掉
  （仓库里没有调用方；页面走各产品自己的接口），服务端内部函数照旧可用；
- 控制面 LINE 渠道 `line_admin.upsert_channel / webhook_url_of`：只给运维（System Manager），别人拒、运维照常；
- 出入库单号预览：只给能开出入库单 / 盘点的人（有没有守卫由 `test_unguarded_endpoints` 管；有权限照常用在实验室里实测）。
"""
import pathlib
import sys
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "apps/pearnly_core"), str(ROOT / "apps/pearnly_erp_portal"),
                str(ROOT / "apps/pearnly_dms"), str(ROOT / "apps/pearnly_control")]

import cc_desktop_fake as fake  # noqa: E402

frappe = fake.install()
utils = sys.modules.setdefault("frappe.utils", types.ModuleType("frappe.utils"))
utils.flt = lambda value, *a: float(value or 0)
utils.cint = lambda value, *a: int(value or 0)
utils.nowdate = lambda: "2026-10-27"
utils.getdate = lambda value=None: value
utils.now = lambda: "2026-10-27 00:00:00"
frappe.utils = utils


class Refused(Exception):
    pass


def _refuse(message, *args, **kwargs):
    raise Refused(message)


class GuardFixes(unittest.TestCase):
    def test_the_leaking_endpoints_are_no_longer_exposed(self):
        import ast

        for path, gone, kept in (("apps/pearnly_dms/pearnly_dms/records/push_log.py", ("api_list", "api_status"), ("status_of",)),
                                 ("apps/pearnly_core/pearnly_core/endpoints.py", ("api_list_endpoints", "api_test_connection"),
                                  ("list_for",))):
            tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
            defined = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
            for name in gone:
                self.assertNotIn(name, defined, f"{path}: {name} 又对外了")
            for name in kept:  # 服务端内部照旧
                self.assertIn(name, defined)
                self.assertFalse(any("whitelist" in ast.dump(deco) for deco in defined[name].decorator_list), name)

    def test_control_line_channel_endpoints_need_an_operator(self):
        from pearnly_control import line_admin

        calls = []
        with mock.patch.object(line_admin, "_require_operator", side_effect=Refused("Not permitted")):
            with self.assertRaises(Refused):
                line_admin.upsert_channel("DEP-1", channel_secret="x", channel_id="1")
            with self.assertRaises(Refused):
                line_admin.webhook_url_of("DEP-1")
        db = types.SimpleNamespace(get_value=lambda doctype, filters, field=None, *a, **k: calls.append(doctype) or "")
        with mock.patch.object(line_admin, "_require_operator", lambda: "ops@x"), \
                mock.patch.object(line_admin.frappe, "db", db, create=True):
            self.assertEqual(line_admin.webhook_url_of("DEP-1"), "")  # 运维照常（这个部署没有渠道：空）
        self.assertEqual(calls, ["Pearnly LINE Channel"])


if __name__ == "__main__":
    unittest.main()
