"""真跑前置自检（`scripts/scenarios/real_run_preflight.py`，A 线 11-10）：两个产品同一套，缺一项 ``ok`` 就是假（场景不开跑）。

11-09 第 1 批真模型场景 7 次里 6 次白花在缺配置上：识别准入、合成电脑、容量 Redis、ocr 队列、识别进程、ERP 识别配置。这里用替身实验室
（站点里查到什么由测试给）逐项拿掉，核对每一项都拦得住、干跑与真跑对引擎的要求不同。
"""
import importlib.util
import json
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("real_run_preflight", ROOT / "scripts/scenarios/real_run_preflight.py")
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)

RUNNING = "python -m pearnly_core.execution.worker --queue ocr\npython -m pearnly_core.execution.dispatch\n"


def seen(product, mode="fake", **change):
    fake = mode == "fake"
    out = {"engine": {"assistant_key": "fake" if fake else "real",
                      "base_url": preflight.AGENTS_URL if fake else preflight.REAL_URL,
                      "ocr_key": "fake" if fake else "real", "ocr_engine": "claude"},
           "capacity": True, "ocr_queue": True, "billing": True}
    if product == "cowork":
        out.update(recognition="economy", computer="ready")
    else:
        out.update(recognition=True, computer={"linked": True, "book": "K1", "reachers": ["CCD-1"]})
    out.update(change)
    return out


class Lab:
    def __init__(self, answer, running=RUNNING):
        self.answer, self.running, self.bodies = answer, running, []

    def python(self, site, body):
        self.bodies.append((site, body))
        return json.loads(json.dumps(self.answer))

    def exec(self, *args, **kwargs):
        return types.SimpleNamespace(stdout=self.running)


class Check(unittest.TestCase):
    def test_everything_in_place_passes_for_both_products_and_modes(self):
        for product in ("cowork", "erp"):
            for mode in ("fake", "real"):
                found = preflight.check(Lab(seen(product, mode)), product, mode, connection="C1", company="CO1")
                self.assertEqual((found["ok"], found["missing"]), (True, []), (product, mode))

    def test_each_missing_piece_stops_the_run(self):
        cases = {
            "cowork": [({"recognition": "refused: no admission"}, "recognition"), ({"computer": "offline"}, "computer"),
                       ({"capacity": "refused: capacity_not_configured"}, "capacity"), ({"ocr_queue": False}, "ocr_queue"),
                       ({"billing": False}, "billing")],
            "erp": [({"recognition": False}, "recognition"),
                    ({"computer": {"linked": True, "book": "K1", "reachers": []}}, "computer"),
                    ({"computer": {"linked": False, "book": "K1", "reachers": ["CCD-1"]}}, "computer")],
        }
        for product, rows in cases.items():
            for change, missing in rows:
                found = preflight.check(Lab(seen(product, **change)), product, "fake", connection="C1", company="CO1")
                self.assertFalse(found["ok"])
                self.assertIn(missing, found["missing"], (product, change))

    def test_recognition_processes_must_run_in_both_modes(self):
        for mode in ("fake", "real"):
            found = preflight.check(Lab(seen("cowork", mode), running="python -m pearnly_core.execution.dispatch\n"),
                                    "cowork", mode, connection="C1")
            self.assertEqual(found["missing"], ["processes"])
        # 启动它们的那行 bash 命令不算在跑
        found = preflight.check(Lab(seen("erp"), running="bash -lc ... -m pearnly_core.execution.worker ... "
                                                          "-m pearnly_core.execution.dispatch\n"), "erp", "fake", company="CO1")
        self.assertIn("processes", found["missing"])

    def test_the_engine_must_point_where_the_mode_says(self):
        # 干跑接着真引擎（会花钱）、真跑还是假引擎（白跑一遍）都拦
        found = preflight.check(Lab(seen("cowork", "real")), "cowork", "fake", connection="C1")
        self.assertEqual(found["missing"], ["engine", "recognition_engine"])
        found = preflight.check(Lab(seen("erp", "fake")), "erp", "real", company="CO1")
        self.assertEqual(found["missing"], ["engine", "recognition_engine"])
        no_claude = seen("erp", engine={**seen("erp")["engine"], "ocr_engine": ""})
        self.assertEqual(preflight.check(Lab(no_claude), "erp", "fake", company="CO1")["missing"], ["recognition_engine"])

    def test_the_checks_run_on_the_products_own_site_as_the_scenario_user(self):
        lab = Lab(seen("erp"))
        preflight.check(lab, "erp", "fake", company="CO1", pages=28, user="admin@erp.regression")
        site, body = lab.bodies[0]
        self.assertEqual(site, preflight.SITES["erp"])
        self.assertIn("frappe.set_user('admin@erp.regression')", body)
        self.assertIn("PAGES = 28", body)
        self.assertIn("ocr.is_configured()", body)

    def test_erp_recognition_config_passes_the_products_own_check(self):
        # ERP 的「已配置」按后端判（aistudio 看密钥、vertex 看凭据文件在不在）：实验栈给 aistudio + 合成密钥
        self.assertEqual(preflight.OCR_CONFIG["erp"]["pearnly_ocr_backend"], "aistudio")
        self.assertTrue(preflight.OCR_CONFIG["erp"]["pearnly_ocr_api_key"])
        source = (ROOT / "apps/pearnly_core/pearnly_core/ocr.py").read_text(encoding="utf-8")
        self.assertIn('return bool(api_key())', source)


if __name__ == "__main__":
    unittest.main()
