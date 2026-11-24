"""DMS 订车验收场景（scripts/acceptance/scenarios/dms_booking.py）的判定：站点换成替身，只验场景自己——正确世界通过；
赠品行丢了、作废没成、建单后某步出错、建单被拒都判不通过；testdms 没切到测试机记没测到。真环境没跑过。"""
import copy
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


GOOD = {
    "ok": True, "dms_host_ok": True, "endpoint": "EP-1",
    "booked": {"ok": True, "booking_no": "BK000002611000009", "booking_id": "140"},
    "readback": {"docno": "BK000002611000009", "arrays": {"fbproductval[]": ["4", "18"], "txtfbquantity[]": ["1.00", "2.00"],
                                                          "txtfbamount[]": ["250.00", "200.00"]}},
    "cancel": {"guard": {"stsel": "c", "idsel": "140", "docno": "BK000002611000009"}, "sent": True, "status": 200, "body_len": 0},
    "after": {"rows": [["140", ["BK000002611000009", "ยกเลิก"]]], "active_id": None},
}


class Site:
    def __init__(self, out):
        self.out, self.calls = out, []

    def run(self, product, script, name, params, timeout=600):
        self.calls.append((product, script.name, name, json.loads(params)))
        return "ACC " + json.dumps(self.out, ensure_ascii=False)


class DmsBookingScenarioTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        keys = {"PEARNLY_LOCK_DIR": self.folder.name + "/locks", "PEARNLY_ACC_DMS_LAB": "1", "PEARNLY_ACC_DMS_USER": "sale02@test",
                "PEARNLY_ACC_DMS_HOST": "dms.example.test", "PEARNLY_ACC_DMS_PEOPLE_ID": "9990000000014"}
        self.old = {key: os.environ.get(key) for key in keys}
        os.environ.update(keys)
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in self.old.items()])
        self.runner = load("acceptance_run_for_dms", "scripts/acceptance/run.py")
        sys.modules.pop("acc_erp_chain", None)

    def go(self, out):
        site = Site(out)
        targets = types.SimpleNamespace(site=lambda target: site)
        scenario = {"id": "acc.dms.line.booking", "module": "scripts/acceptance/scenarios/dms_booking.py", "resources": [], "budget_usd": 0}
        return self.runner.run_one(scenario, pathlib.Path(self.folder.name) / "run", "win-test", False, targets=targets), site

    def test_correct_world_passes_on_the_testdms_site(self):
        result, site = self.go(GOOD)
        self.assertEqual((result["outcome"], result["detail"]), (self.runner.PASS, ""))
        product, script, name, params = site.calls[0]
        self.assertEqual((product, script, name), ("dms", "dms_booking.py", "testdms.pearnly.com"))
        self.assertEqual((params["freebies"], params["car_id"]), ([["4", "1"], ["18", "2"]], "2"))

    def test_each_broken_world_fails(self):
        def broken(path, value):
            out = copy.deepcopy(GOOD)
            *head, last = path
            node = out
            for key in head:
                node = node[key]
            node[last] = value
            return out
        cases = [(broken(["readback", "arrays", "fbproductval[]"], ["4"]), "赠品商品 id"),
                 (broken(["readback", "arrays", "txtfbquantity[]"], ["1.00", "1.00"]), "赠品数量"),
                 (broken(["cancel", "body_len"], 12), "原生成功"),
                 (broken(["cancel", "sent"], False), "原生成功"),
                 (broken(["after", "rows"], [["140", ["BK000002611000009", "ใช้งาน"]]]), "ยกเลิก"),
                 (broken(["after", "active_id"], "140"), "搜不到"),
                 ({**GOOD, "after_error": "作废：timeout"}, "每一步都走完"),
                 (broken(["booked"], {"ok": False, "error_code": "ERR_DMS_IMPORT"}), "建单成功")]
        for out, words in cases:
            result, _ = self.go(out)
            self.assertEqual(result["outcome"], self.runner.FAIL, words)
            self.assertIn(words, result["detail"])

    def test_wrong_dms_host_is_refused_before_anything_is_written(self):
        result, _ = self.go({"ok": False, "why": "refuse: 端点 EP-1 登录的 DMS 主机不是登记的测试账号（x）"})
        self.assertEqual(result["outcome"], self.runner.NOT_RUN)
        self.assertIn("refuse", result["detail"])

    def test_not_run_until_testdms_is_switched_to_the_test_machine(self):
        os.environ.pop("PEARNLY_ACC_DMS_LAB")
        result, site = self.go(GOOD)
        self.assertEqual(result["outcome"], self.runner.NOT_RUN)
        self.assertEqual(site.calls, [])


if __name__ == "__main__":
    unittest.main()
