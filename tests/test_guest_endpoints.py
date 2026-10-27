"""允许匿名调用的白名单接口必须逐个登记理由（回归基线 B，主会话 10-26）：代码里每个 `allow_guest=True` 都要在
`tests/baseline/guest_endpoints.toml` 里有一条（写明 reason 与 guard），登记了的也必须真存在。离线、不起站点。"""
import ast
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPS = ("pearnly_core", "pearnly_erp_portal", "pearnly_cowork", "pearnly_dms")
REGISTRY = ROOT / "tests" / "baseline" / "guest_endpoints.toml"


def guest_methods():
    found = set()
    for app in APPS:
        base = ROOT / "apps" / app
        for path in (base / app).rglob("*.py"):
            if path.name.startswith("test") or "/tests/" in str(path):
                continue
            module = ".".join(path.relative_to(base).with_suffix("").parts)
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for deco in node.decorator_list:
                    if (isinstance(deco, ast.Call) and getattr(deco.func, "attr", "") == "whitelist"
                            and any(k.arg == "allow_guest" and getattr(k.value, "value", False) is True for k in deco.keywords)):
                        found.add(f"{module}.{node.name}")
    return found


class GuestEndpoints(unittest.TestCase):
    def test_every_guest_endpoint_is_registered_with_a_reason(self):
        rows = tomllib.loads(REGISTRY.read_text(encoding="utf-8"))["guest"]
        registered = {row["method"]: row for row in rows}
        code = guest_methods()
        self.assertEqual(sorted(code - set(registered)), [], "新的匿名接口没登记理由（tests/baseline/guest_endpoints.toml）")
        self.assertEqual(sorted(set(registered) - code), [], "登记表里的匿名接口在代码里已经没有了，删掉那一条")
        for method, row in registered.items():
            self.assertTrue(str(row.get("reason") or "").strip(), f"{method} 没写理由")
            self.assertTrue(str(row.get("guard") or "").strip(), f"{method} 没写匿名进来后靠什么认人")


if __name__ == "__main__":
    unittest.main()
