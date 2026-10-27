"""白名单接口必须有守卫（回归基线 B 的接口权限矩阵接进 CI 的那一半，主会话 10-27）：从每个非匿名的 ``@frappe.whitelist()`` 接口出发，
沿本仓代码的调用找得到守卫（`tests/endpoint_guards.py`）；找不到的只许是登记在 `tests/baseline/unguarded_endpoints.toml` 的
本人数据 / 公开信息，新增即失败；登记了的也必须真的还没守卫（加了守卫就删掉那一条）。离线、不起站点。
10-27 查出并修掉：DMS 推送记录 `push_log.api_list / api_status`、共用层连接清单 `endpoints.api_list_endpoints / api_test_connection`
（删掉对外入口）、控制面 LINE 渠道 `line_admin.upsert_channel / webhook_url_of`（加运维守卫）、出入库单号预览（加库存权限）。"""
import tomllib
import unittest
from pathlib import Path

import endpoint_guards

REGISTRY = Path(__file__).resolve().parent / "baseline" / "unguarded_endpoints.toml"


class UnguardedEndpoints(unittest.TestCase):
    def test_every_unguarded_endpoint_is_own_data_or_public_and_registered(self):
        rows = tomllib.loads(REGISTRY.read_text(encoding="utf-8"))["endpoint"]
        registered = {row["method"]: row for row in rows}
        found = set(endpoint_guards.unguarded_endpoints())
        self.assertEqual(sorted(found - set(registered)), [],
                         "新的接口找不到守卫：加守卫，或确属本人数据 / 公开信息时登记（tests/baseline/unguarded_endpoints.toml）")
        self.assertEqual(sorted(set(registered) - found), [], "登记表里的接口已经有守卫或不在了，删掉那一条")
        for method, row in registered.items():
            self.assertIn(row.get("kind"), ("own", "public"), f"{method} 只许 own / public")
            self.assertTrue(str(row.get("reason") or "").strip(), f"{method} 没写理由")

    def test_the_two_reported_leaks_and_their_kind_stay_closed(self):
        """10-27 查出的几处不能以别的名字回来：这几个模块里没有无守卫的对外接口。"""
        found = set(endpoint_guards.unguarded_endpoints())
        for module in ("pearnly_dms.records.push_log", "pearnly_core.endpoints", "pearnly_control.line_admin"):
            self.assertEqual([name for name in found if name.startswith(module + ".")], [], module)


if __name__ == "__main__":
    unittest.main()
