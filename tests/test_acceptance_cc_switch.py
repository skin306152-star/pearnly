"""测试机上 CC 桌面版配置切换的离线单测（scripts/acceptance/cc_switch.py、win/cc_root.py、win/make_root.py）。不连任何机器。
依据 11-14 示范 1 首跑：切域名后解析没稳定就重启 CC，整份账套名单报到了旧指向；所以要连续多次解析到预期地址才动。
"""
import importlib.util
import json
import pathlib
import struct
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "acceptance"))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


switch = load("acc_cc_switch", "scripts/acceptance/cc_switch.py")
cc_root = load("acc_cc_root", "scripts/acceptance/win/cc_root.py")
make_root = load("acc_make_root", "scripts/acceptance/win/make_root.py")
LAB, PROD = ["104.21.28.241", "172.67.147.231"], [switch.PRODUCTION]


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def answers(sequence):
    left = list(sequence)
    return lambda host, domain: left.pop(0) if len(left) > 1 else left[0]


class SettledTest(unittest.TestCase):
    def test_a_flapping_answer_restarts_the_count(self):
        clock = Clock()
        seen = switch.settled("pc", "t.example", "production", resolve=answers([PROD, PROD, LAB, PROD, PROD, PROD]),
                              sleep=clock.sleep, clock=clock, need=3, gap=15, limit=600)
        self.assertEqual(seen, ["production", "production", "lab", "production", "production", "production"])

    def test_mixed_or_empty_answers_never_count(self):
        self.assertEqual(switch.points_at(LAB + PROD), "mixed")
        self.assertEqual(switch.points_at([]), "mixed")
        self.assertEqual(switch.points_at(LAB), "lab")
        self.assertEqual(switch.points_at(PROD), "production")

    def test_gives_up_without_touching_anything(self):
        clock = Clock()
        with self.assertRaises(switch.Infra):
            switch.settled("pc", "t.example", "lab", resolve=answers([PROD]), sleep=clock.sleep, clock=clock, need=5, gap=15, limit=60)


class SwitchedConfigTest(unittest.TestCase):
    CONFIG = {"global": {"express_places": ["P:\\69EXP", "P:\\70EXP"], "place_channels": [["P:\\69EXP", ["cowork"]], ["P:\\70EXP", ["core"]]]},
              "profiles": [{"channel": "cowork", "kind": "express", "paused": True, "express_root": "P:\\69EXP", "authorized_roots": ["P:\\69EXP"]},
                           {"channel": "core", "kind": "express", "paused": False, "express_root": "P:\\69EXP",
                            "authorized_roots": ["P:\\70EXP", "P:\\69EXP"], "token": "keep-me"}]}

    def test_only_the_dedicated_root_is_left_for_that_channel(self):
        before = json.dumps(self.CONFIG)
        out = cc_root.switched(self.CONFIG, "core", "C:\\PearnlyAcceptance\\root", "\\\\accserver\\d$\\ACCOUNT\\69EXP\\TEST2")
        self.assertEqual(json.dumps(self.CONFIG), before)  # 传入的那份不改
        self.assertEqual(out["global"]["place_channels"], [["P:\\69EXP", ["cowork"]], ["P:\\70EXP", ["cowork"]],
                                                           ["C:\\PearnlyAcceptance\\root", ["core"]]])
        core = out["profiles"][1]
        self.assertEqual((core["authorized_roots"], core["express_root"], core["account_set"], core["token"]),
                         (["C:\\PearnlyAcceptance\\root"], "C:\\PearnlyAcceptance\\root", "\\\\accserver\\d$\\ACCOUNT\\69EXP\\TEST2", "keep-me"))
        self.assertEqual(out["profiles"][0], self.CONFIG["profiles"][0])  # 另一种成员的档案不动

    def test_refuses_when_the_channel_has_no_single_profile(self):
        with self.assertRaises(ValueError):
            cc_root.switched({"global": {}, "profiles": []}, "core", "C:\\r", "\\\\s\\l")


def registry(rows):
    """最小的登记表：COMPNAM(10) + PATH(40)；rows = [(删除标记, 名字, 路径)]。"""
    fields = [(b"COMPNAM", 10), (b"PATH", 40)]
    head = bytearray(32)
    head[0] = 3
    descriptors = b"".join(name.ljust(11, b"\0") + b"C" + bytes(4) + bytes([size]) + bytes(15) for name, size in fields)
    hlen, rlen = 32 + len(descriptors) + 1, 1 + sum(size for _n, size in fields)
    head[4:12] = struct.pack("<IHH", len(rows), hlen, rlen)
    head[28] = 1
    body = b"".join(mark + name.encode().ljust(10) + path.encode().ljust(40) for mark, name, path in rows)
    return bytes(head) + descriptors + b"\r" + body + b"\x1a"


class OneRowRegistryTest(unittest.TestCase):
    LEDGER = "\\\\accserver\\d$\\ACCOUNT\\69EXP\\TEST2"

    def test_keeps_exactly_the_acceptance_row_with_the_full_path(self):
        raw = registry([(b" ", "real", "REALCO"), (b" ", "t", "TEST"), (b" ", "t2", "TEST2"), (b"*", "old", "TEST2")])
        built, hits = make_root.one_row(raw, self.LEDGER)
        count, hlen, rlen, fields = make_root.table(built)
        self.assertEqual((count, hits, built[28]), (1, [(2, "TEST2")], 0))
        self.assertEqual(len(built), hlen + rlen + 1)
        self.assertEqual(built[hlen + 11: hlen + 51].decode().strip(), self.LEDGER)
        self.assertNotIn(b"REALCO", built)

    def test_refuses_when_the_row_is_missing_or_doubled(self):
        for rows in ([(b" ", "t", "TEST")], [(b" ", "a", "TEST2"), (b" ", "b", "X\\TEST2")]):
            with self.assertRaises(ValueError):
                make_root.one_row(registry(rows), self.LEDGER)


if __name__ == "__main__":
    unittest.main()
