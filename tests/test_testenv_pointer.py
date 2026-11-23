"""测试站指向（scripts/testenv_pointer.py，主会话 11-23）：按解析认线上 / 测试环境；切换后自动登记实时表。只用标准库、不出网。"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import testenv_pointer as pointer  # noqa: E402


def resolver(table):
    def resolve(host, port, proto=0):
        if host not in table:
            raise OSError("no such host")
        return [(2, 1, 6, "", (address, port)) for address in table[host]]
    return resolve


TABLE = """# 实时表

| 资源 | 当前 | 使用者 | 起始 | 备注 |
|---|---|---|---|---|
| testerp.pearnly.com | 线上 | — | 13:4x | 留着 |
| testdms.pearnly.com | **测试环境（测试机1）** | DMS 窗口 | 14:xx 起 | |

## 切换流水
- 11:xx testerp → 测试环境
"""


class Pointer(unittest.TestCase):
    def test_production_is_the_direct_address_and_anything_else_is_the_lab(self):
        resolve = resolver({pointer.WILDCARD_PROBE: ["198.51.100.7"], "testerp.pearnly.com": ["198.51.100.7"], "testdms.pearnly.com": ["104.21.1.1", "172.67.1.1"]})
        self.assertEqual(pointer.where("testerp.pearnly.com", resolve), pointer.PROD)
        self.assertEqual(pointer.where("testdms.pearnly.com", resolve), pointer.LAB)
        self.assertEqual(pointer.where("testcowork.pearnly.com", resolve), pointer.UNKNOWN)

    def test_the_first_line_shouts_when_a_test_site_is_not_on_production(self):
        resolve = resolver({pointer.WILDCARD_PROBE: ["198.51.100.7"], "testerp.pearnly.com": ["198.51.100.7"], "testdms.pearnly.com": ["104.21.1.1"]})
        lines = pointer.report(["testerp.pearnly.com", "testdms.pearnly.com"], resolve)
        self.assertTrue(lines[0].startswith("!!!") and "testdms.pearnly.com" in lines[0], lines)
        calm = pointer.report(["testerp.pearnly.com"], resolve)
        self.assertEqual(calm[0], "测试站指向：全部线上")

    def test_switches_update_the_row_and_append_to_the_log(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "table.md"
            path.write_text(TABLE, encoding="utf-8")
            import datetime
            moment = datetime.datetime(2026, 11, 23, 15, 30)
            self.assertTrue(pointer.record("erp", "lab", "", path, moment))
            text = path.read_text(encoding="utf-8")
            self.assertIn("| testerp.pearnly.com | **测试环境（测试机1）** | — | 11-23 15:30 自动登记 | 留着 |", text)
            self.assertTrue(text.rstrip().endswith("- 11-23 15:30 testerp.pearnly.com → 测试环境（testenv.sh lab-start 自动登记）"))
            self.assertTrue(pointer.record("dms", "prod", "", path, moment))
            text = path.read_text(encoding="utf-8")
            self.assertIn("| testdms.pearnly.com | 线上 | — | 11-23 15:30 自动登记 | |", text)
            self.assertIn("- 11-23 15:30 testdms.pearnly.com → 线上（testenv.sh lab-stop 自动登记）", text)
            self.assertFalse(pointer.record("erp", "lab", "", Path(folder) / "missing.md", moment), "no table: only a notice")


if __name__ == "__main__":
    unittest.main()
