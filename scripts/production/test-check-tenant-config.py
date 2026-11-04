#!/usr/bin/env python3
"""部署前租户配置对比（check-tenant-config.py）：按地图分类哪些差异拦部署、哪些只报告；容器里读配置时密钥不出容器。离线。"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("check_tenant_config", HERE / "check-tenant-config.py")
cfg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cfg)

MAP = {"version": 1, "rows": [], "config": {
    "pearnly_engine": {"class": "same"},
    "pearnly_entry": {"class": "shape", "pattern": "^https://fatcc\\.com/?$", "rule": "CC 入口统一走 fatcc.com"},
    "pearnly_liff": {"class": "presence"},
    "pearnly_key": {"class": "presence", "secret": True},
    "pearnly_line_*": {"class": "presence", "secret": True},
    "pearnly_company": {"class": "varies"},
}}


def site(name, **keys):
    base = {"pearnly_engine": "claude", "pearnly_entry": None, "pearnly_liff": "1-a", "pearnly_key": True,
            "pearnly_line_*": ["pearnly_line_channel_id", "pearnly_line_channel_secret"]}
    base.update(keys)
    return {"site": name, "keys": base}


class Compare(unittest.TestCase):
    def test_same_and_shape_block_presence_only_reports(self):
        dumps = {"erp": [site("testerp.pearnly.com"), site("mrerp.pearnly.com", pearnly_engine="google"),
                         site("polymer.pearnly.com", pearnly_entry="https://mrerp.pearnly.com/assistant"),
                         site("acme.pearnly.com", pearnly_liff=None, pearnly_key=None, **{"pearnly_line_*": []})]}
        block, note = cfg.compare(MAP, dumps)
        self.assertEqual(len(block), 2, block)
        self.assertIn('mrerp.pearnly.com pearnly_engine："google"，测试租户 testerp.pearnly.com 是 "claude"（应一致）', block[0])
        self.assertIn("polymer.pearnly.com pearnly_entry", block[1])
        self.assertIn("不满足「CC 入口统一走 fatcc.com」", block[1])
        self.assertEqual(len(note), 3, note)
        self.assertTrue(all("（只报告）" in line for line in note))
        # 密钥只说有没有：不出现 True / 值
        self.assertTrue(any("pearnly_key：（没配），测试租户 testerp.pearnly.com （有）" in line for line in note), note)

    def test_identical_tenants_pass_and_varies_is_not_read(self):
        dumps = {"cowork": [site("testcowork.pearnly.com"), site("erpcowork.pearnly.com", pearnly_entry="https://fatcc.com")]}
        self.assertEqual(cfg.compare(MAP, dumps), ([], []))
        self.assertNotIn("pearnly_company", cfg.spec_of(MAP))
        self.assertEqual(cfg.spec_of(MAP)["pearnly_key"], {"secret": True})

    def test_no_test_tenant_unreadable_product_or_site(self):
        block, note = cfg.compare(MAP, {"dms": [site("ris.pearnly.com")]})
        self.assertEqual(block, [])
        self.assertEqual(note, ["dms：没有测试租户，正式租户之间不比"])
        block, _ = cfg.compare(MAP, {"erp": {"error": "No such container"},
                                     "cowork": [{"site": "x.pearnly.com", "error": "JSONDecodeError"}, site("testcowork.pearnly.com")]})
        self.assertEqual(len(block), 2)
        self.assertIn("erp：读不到站点配置", block[0])
        self.assertIn("x.pearnly.com：site_config.json 读不出", block[1])

    def test_the_container_reader_keeps_secrets_inside(self):
        with tempfile.TemporaryDirectory() as folder:
            sites = Path(folder) / "sites"
            for name, conf in {"testerp.pearnly.com": {"pearnly_engine": "claude", "pearnly_key": "sk-secret-value",
                                                        "pearnly_line_channel_secret": "line-secret"},
                               "mrerp.pearnly.com": {"pearnly_engine": "", "pearnly_entry": "https://fatcc.com"}}.items():
                (sites / name).mkdir(parents=True)
                (sites / name / "site_config.json").write_text(json.dumps(conf))
            (sites / "assets").mkdir()
            (sites / "common_site_config.json").write_text(json.dumps({"pearnly_engine": "common", "pearnly_liff": "1-common", "db_host": "mariadb"}))
            done = subprocess.run([sys.executable, "-", json.dumps(cfg.spec_of(MAP))], input=cfg.DUMPER, cwd=folder,
                                  capture_output=True, text=True, check=True)
        self.assertNotIn("sk-secret-value", done.stdout)
        self.assertNotIn("line-secret", done.stdout)
        self.assertNotIn("mariadb", done.stdout)
        rows = {row["site"]: row["keys"] for row in json.loads(done.stdout)}
        self.assertEqual(sorted(rows), ["mrerp.pearnly.com", "testerp.pearnly.com"])
        self.assertEqual(rows["testerp.pearnly.com"]["pearnly_key"], True)
        self.assertEqual(rows["testerp.pearnly.com"]["pearnly_line_*"], ["pearnly_line_channel_secret"])
        # 同 frappe.conf：站点自己的值覆盖公共配置（空串也算覆盖，读出来就是没配）；站点没有的键用公共配置
        self.assertIsNone(rows["mrerp.pearnly.com"]["pearnly_engine"])
        self.assertEqual(rows["mrerp.pearnly.com"]["pearnly_liff"], "1-common")
        self.assertEqual(rows["mrerp.pearnly.com"]["pearnly_entry"], "https://fatcc.com")

    def test_collect_reads_each_product_web_container_with_docker_exec(self):
        calls = []

        def run(argv, **kwargs):
            calls.append(argv)
            product = argv[5].split("-")[2]
            out = json.dumps([site(f"test{product}.pearnly.com")]) if product != "dms" else ""
            return subprocess.CompletedProcess(argv, 0 if out else 1, out, "Error: No such container")

        dumps = cfg.collect(MAP, run=run)
        self.assertEqual([c[:6] for c in calls], [["docker", "exec", "-i", "-w", cfg.BENCH, f"pearnly-production-{p}-web-1"]
                                                  for p in ("erp", "cowork", "dms")])
        self.assertTrue(all("mariadb" not in " ".join(c) for c in calls))
        self.assertEqual(dumps["dms"], {"error": "Error: No such container"})

    def test_main_exit_codes(self):
        with tempfile.TemporaryDirectory() as folder:
            mapped, sites = Path(folder) / "map.json", Path(folder) / "sites.json"
            mapped.write_text(json.dumps(MAP))
            sites.write_text(json.dumps({"erp": [site("testerp.pearnly.com"), site("mrerp.pearnly.com")]}))
            script = HERE / "check-tenant-config.py"
            ok = subprocess.run([sys.executable, str(script), "--map", str(mapped), "--sites", str(sites)],
                                capture_output=True, text=True)
            self.assertEqual(ok.returncode, 0, ok.stdout)
            self.assertIn("tenant config check passed", ok.stdout)
            sites.write_text(json.dumps({"erp": [site("testerp.pearnly.com"), site("mrerp.pearnly.com", pearnly_engine="x")]}))
            bad = subprocess.run([sys.executable, str(script), "--map", str(mapped), "--sites", str(sites)],
                                 capture_output=True, text=True)
            self.assertEqual(bad.returncode, 1)
            self.assertIn("BLOCK erp mrerp.pearnly.com pearnly_engine", bad.stdout)
            import base64
            b64 = base64.b64encode(json.dumps(MAP).encode()).decode()
            via_ssh = subprocess.run([sys.executable, "-", "--map-b64", b64, "--sites", str(sites)],
                                     input=script.read_text(), capture_output=True, text=True, cwd=folder,
                                     env=dict(os.environ))
            self.assertEqual(via_ssh.returncode, 1, via_ssh.stdout + via_ssh.stderr)
            self.assertEqual(subprocess.run([sys.executable, str(script), "--map", str(Path(folder) / "none.json")],
                                            capture_output=True, text=True).returncode, 2)


if __name__ == "__main__":
    unittest.main()
