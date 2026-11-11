"""实验栈造出在线的电脑（`scripts/flows/lab_online.py`；11-11 已知问题「实验栈造的电脑永远判离线」）。

锁住：按它的写法报到的电脑，两个产品的在线判据都判在线（Cowork `credentials.record_seen` + `_fresh`，ERP `devices.now()` +
`cc_desktop.contracts.online`）；11-11 那种写成站点本地时间（曼谷 +7）的判离线；后台报到的间隔小于在线窗口的一半；进 with 报不上
就抛错、出 with 就停。判据函数从产品源码里原样取出来跑（不靠替身改写），产品改了判据这里跟着变。
"""
import ast
import sys
import textwrap
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "flows"))
import lab_online  # noqa: E402

CREDENTIALS = ROOT / "apps/pearnly_cowork/pearnly_cowork/companion/credentials.py"
CONTRACTS = ROOT / "apps/pearnly_core/pearnly_core/cc_desktop/contracts.py"
DEVICES = ROOT / "apps/pearnly_core/pearnly_core/cc_desktop/devices.py"
BANGKOK = timedelta(hours=7)


def _pick(path, names) -> dict:
    """产品源码里的这几个顶层函数 / 常量原样取出来（只要标准库），放进一个命名空间跑。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parts = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.Assign)):
            name = node.name if isinstance(node, ast.FunctionDef) else getattr(node.targets[0], "id", "")
            if name in names:
                parts.append(ast.get_source_segment(path.read_text(encoding="utf-8"), node))
    space = {"datetime": datetime, "timezone": timezone, "timedelta": timedelta, "Optional": Optional}
    exec(compile("\n\n".join(textwrap.dedent(p) for p in parts), str(path), "exec"), space)
    missing = [name for name in names if name not in space]
    if missing:
        raise AssertionError(f"{path.name}: {missing} not found")
    return space


class JudgedOnlineTests(unittest.TestCase):
    def test_a_cowork_computer_reported_the_way_the_helper_does_is_online(self):
        product = _pick(CREDENTIALS, ["_now", "ONLINE_SECONDS", "_fresh"])
        seen = product["_now"]()  # `record_seen` 不给 now 时写的就是它（UTC 的裸时间）
        self.assertTrue(product["_fresh"](seen, 3, 3))
        self.assertFalse(product["_fresh"](seen, 2, 3))  # 换钥前的报到不算：所以按行上的 token_generation 报
        # 11-11：写成站点本地时间（曼谷 +7）= 按 UTC 读是 7 小时后的未来，判离线
        self.assertFalse(product["_fresh"](seen + BANGKOK, 3, 3))
        self.assertEqual(product["ONLINE_SECONDS"], lab_online.ONLINE_SECONDS)
        self.assertIn("credentials.record_seen(row.name, int(row.token_generation or 0))", lab_online.cowork_code("C1"))

    def test_an_erp_computer_reported_the_way_the_helper_does_is_online(self):
        rules = _pick(CONTRACTS, ["ONLINE_SECONDS", "online"])
        clock = _pick(DEVICES, ["now"])
        moment = clock["now"]()
        self.assertTrue(rules["online"](moment, clock["now"]()))
        self.assertFalse(rules["online"](moment - timedelta(seconds=rules["ONLINE_SECONDS"] + 5), clock["now"]()))
        self.assertEqual(rules["ONLINE_SECONDS"], lab_online.ONLINE_SECONDS)
        self.assertIn('"last_seen_at", devices.now()', lab_online.erp_code("D1"))

    def test_beats_come_more_often_than_half_the_online_window(self):
        self.assertLess(lab_online.BEAT_SECONDS * 2, lab_online.ONLINE_SECONDS)
        with self.assertRaises(ValueError):
            lab_online.KeepOnline(object(), "s", cowork_connection="C1", every=lab_online.ONLINE_SECONDS)


class _Lab:
    def __init__(self, found=1):
        self.calls, self.found = [], found

    def python(self, site, code):
        self.calls.append((site, code))
        return {"beat": self.found}


class KeepOnlineTests(unittest.TestCase):
    def test_it_reports_at_once_then_in_the_background_and_stops_on_leaving(self):
        lab = _Lab()
        with lab_online.KeepOnline(lab, "cowork.lab", cowork_connection="C1", every=0.02) as keeper:
            self.assertEqual(len(lab.calls), 1)  # 进来先同步报一次
            time.sleep(0.15)
        during = len(lab.calls)
        self.assertGreaterEqual(during, 3)
        time.sleep(0.1)
        self.assertEqual(len(lab.calls), during)  # 出来就停
        self.assertEqual(keeper.beats, during)
        self.assertTrue(all(site == "cowork.lab" for site, _code in lab.calls))

    def test_nothing_to_keep_online_fails_before_the_run(self):
        with self.assertRaises(RuntimeError):
            with lab_online.KeepOnline(_Lab(found=0), "erp.lab", erp_device="nope", every=0.02):
                pass
        with self.assertRaises(ValueError):
            lab_online.KeepOnline(_Lab(), "s")


if __name__ == "__main__":
    unittest.main()
