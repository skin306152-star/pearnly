#!/usr/bin/env python3
"""部署后逐租户入口检查（check-tenant-entries.py）：三种探针在假 HTTP 应答上判对判错；只走正式租户、按产品取行。离线。"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("check_tenant_entries", HERE / "check-tenant-entries.py")
entries = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entries)
liff = entries.liff

LIFF_ID = "2010411313-aaaa1111"
MAP = {"version": 1, "config": {}, "rows": [
    {"id": "erp.line.card-edit", "product": "ERP", "must_verify": True,
     "probe": {"type": "liff", "path": "/desk/pearnly-purchase/line/{doc}"}},
    {"id": "erp.cc.action.switch-account", "product": "ERP", "must_verify": True, "probe": {"type": "assistant-gate"}},
    {"id": "cowork.desk.ping", "product": "Cowork", "must_verify": True, "probe": {"type": "http", "path": "/api/method/ping"}},
    {"id": "erp.line.device-only", "product": "ERP", "must_verify": False, "probe": {}, "device_only": "要真 LINE App"},
]}


class FakeWeb:
    """站点按 LINE 规矩：入口转 LIFF、LIFF 落地地址是站点根（或坏的子路径）；/assistant 转 CC 入口。"""

    def __init__(self, site="mrerp.pearnly.com", endpoint=None, entry="https://fatcc.com", entry_status=200, ping=200):
        self.site, self.endpoint = site, endpoint or f"https://{site}/"
        self.entry, self.entry_status, self.ping = entry, entry_status, ping
        self.calls = []

    def __call__(self, url, agent):
        self.calls.append((url, agent))
        parts = urllib.parse.urlsplit(url)
        if parts.netloc == "liff.line.me":
            return 200, "", f'<script>var liffEndpointUrl = "{self.endpoint}";</script>'
        if parts.netloc == urllib.parse.urlsplit(self.entry).netloc:
            return self.entry_status, "", ""
        path = parts.path
        if path == liff.ENTER and agent == liff.LINE_BROWSER_UA:
            to = urllib.parse.parse_qs(parts.query)["to"][0]
            return 302, f"https://liff.line.me/{LIFF_ID}{liff.ENTER}?" + urllib.parse.urlencode({"to": to + "?pearnly_liff=1"}), ""
        if path == liff.ENTER or "liff.state" in parts.query:
            return 302, "/api/method/pearnly_core.account.login.begin", ""
        if path == "/api/method/pearnly_core.account.login.begin":
            return 302, "https://auth.pearnly.com/realms/x/protocol/openid-connect/auth", ""
        if path == "/assistant":
            return 302, f"{self.entry}/?site={self.site}", ""
        if path == "/api/method/ping":
            return self.ping, "", ""
        return 404, "", ""


def tenant(site, **keys):
    return {"site": site, "keys": {"pearnly_line_liff_id": LIFF_ID, **keys}}


class Probes(unittest.TestCase):
    def test_rows_by_product_only_must_verify_with_probe(self):
        self.assertEqual([r["id"] for r in entries.rows_for(MAP, "erp")], ["erp.line.card-edit", "erp.cc.action.switch-account"])
        self.assertEqual([r["id"] for r in entries.rows_for(MAP, "cowork")], ["cowork.desk.ping"])
        shared = {"rows": [{"id": "s", "product": "共用", "products": ["ERP", "Cowork"], "must_verify": True, "probe": {"type": "http"}}]}
        self.assertEqual(len(entries.rows_for(shared, "cowork")), 1)
        self.assertEqual(entries.rows_for(shared, "dms"), [])

    def test_liff_probe_walks_both_line_addresses_with_the_doc_placeholder_filled(self):
        web = FakeWeb()
        self.assertEqual(entries.probe_liff("mrerp.pearnly.com", tenant("mrerp.pearnly.com")["keys"], MAP["rows"][0]["probe"], web), [])
        first = web.calls[0][0]
        self.assertIn("to=%2Fdesk%2Fpearnly-purchase%2Fline%2FPEARNLY-PROBE-0", first)
        walked = [url for url, _ in web.calls if url.startswith("https://mrerp.pearnly.com/") and url != first]
        self.assertTrue(any("liff.state" in url for url in walked))
        self.assertTrue(any(url.startswith(f"https://mrerp.pearnly.com{liff.ENTER}") for url in walked))

    def test_liff_probe_catches_the_sub_path_endpoint_of_11_03(self):
        web = FakeWeb(endpoint="https://mrerp.pearnly.com/stocktake")
        problems = entries.probe_liff("mrerp.pearnly.com", tenant("mrerp.pearnly.com")["keys"], MAP["rows"][0]["probe"], web)
        self.assertTrue(problems)
        self.assertIn("/stocktake/api/method/", problems[0])
        self.assertIn("answered 404", problems[0])
        self.assertEqual(entries.probe_liff("x.pearnly.com", {}, MAP["rows"][0]["probe"], web),
                         ["no pearnly_line_liff_id: LINE links on this site cannot open through LIFF"])

    def test_assistant_gate_needs_the_entry_and_this_site(self):
        probe = MAP["rows"][1]["probe"]
        self.assertEqual(entries.probe_assistant_gate("mrerp.pearnly.com", {}, probe, FakeWeb()), [])
        # 配了别的入口：转去的不是它 → 问题；入口本身 5xx → 问题
        self.assertTrue(entries.probe_assistant_gate("mrerp.pearnly.com", {"pearnly_assistant_entry_url": "https://other.com"},
                                                     probe, FakeWeb()))
        self.assertTrue(entries.probe_assistant_gate("mrerp.pearnly.com", {}, probe, FakeWeb(entry_status=502)))
        wrong_site = FakeWeb()
        wrong_site.site = "polymer.pearnly.com"   # 带错 ?site=
        self.assertTrue(entries.probe_assistant_gate("mrerp.pearnly.com", {}, probe, wrong_site))

    def test_http_probe(self):
        probe = MAP["rows"][2]["probe"]
        self.assertEqual(entries.probe_http("erpcowork.pearnly.com", {}, probe, FakeWeb("erpcowork.pearnly.com")), [])
        self.assertEqual(entries.probe_http("erpcowork.pearnly.com", {}, probe, FakeWeb("erpcowork.pearnly.com", ping=500)),
                         ["https://erpcowork.pearnly.com/api/method/ping answered 500"])

    def test_check_runs_formal_tenants_only_and_reports_each(self):
        dumps = {"erp": [tenant("testerp.pearnly.com"), tenant("mrerp.pearnly.com")],
                 "cowork": [tenant("erpcowork.pearnly.com")], "dms": {"error": "No such container"}}
        web = FakeWeb()

        def get(url, agent):
            host = urllib.parse.urlsplit(url).netloc
            if host.endswith("pearnly.com") and host != "auth.pearnly.com":
                web.site = host
                web.endpoint = f"https://{host}/"
            return web(url, agent)

        self.assertEqual(entries.check(MAP, dumps, get), [])
        self.assertFalse(any("testerp" in url for url, _ in web.calls))
        dumps["cowork"] = {"error": "No such container"}
        self.assertEqual(entries.check(MAP, dumps, get), ["cowork: cannot list tenants (No such container)"])

    def test_main_reads_the_export_and_the_dump(self):
        with tempfile.TemporaryDirectory() as folder:
            mapped, sites = Path(folder) / "map.json", Path(folder) / "sites.json"
            mapped.write_text(json.dumps({"version": 1, "config": {}, "rows": []}))
            sites.write_text(json.dumps({"erp": [tenant("mrerp.pearnly.com")]}))
            ok = subprocess.run([sys.executable, str(HERE / "check-tenant-entries.py"), "--map", str(mapped), "--sites", str(sites)],
                                capture_output=True, text=True)
            self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
            missing = subprocess.run([sys.executable, str(HERE / "check-tenant-entries.py"), "--map", str(Path(folder) / "x.json")],
                                     capture_output=True, text=True)
            self.assertEqual(missing.returncode, 2)


if __name__ == "__main__":
    unittest.main()
