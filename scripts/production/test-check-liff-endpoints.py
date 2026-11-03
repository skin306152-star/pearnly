#!/usr/bin/env python3
"""No network: check-liff-endpoints.py reads each tenant's LIFF endpoint from liff.line.me and walks the LINE entry.

2026-11-03: mrerp's LIFF landed on /stocktake, LINE glued the hop path after it (/stocktake/api/method/...) and every
card in LINE opened a 404. The check must report a sub-path endpoint and the 404 it leads to, and must never pass
when it cannot read LINE's page.
"""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = load("check_liff_endpoints", ROOT / "scripts/production/check-liff-endpoints.py")
SITE = "acme-erp.pearnly.com"
LIFF = "2010411313-abcd1234"
ENTER = "/api/method/pearnly_core.account.line_login.enter"
HOP_PATH = ENTER + "?to=%2Fdesk%3Fpearnly_liff%3D1"


def liff_page(endpoint):
    """Shape of the page liff.line.me serves a plain browser (read 2026-11-03)."""
    return ('<html><head><script type="text/javascript">\n'
            '        function redirectToTargetPage() {\n'
            f'            const liffEndpointUrl = {json.dumps(endpoint)}\n'
            '            let liffFullUrlForBrowser = "x"\n'
            '        }\n</script></head></html>')


class FakeWeb:
    """liff.line.me plus one tenant site behaving like production (pearnly_core line_login / login_redirect)."""

    def __init__(self, endpoint=f"https://{SITE}/", liff=LIFF, hop=True, page=None, broken=()):
        self.endpoint, self.liff, self.hop, self.page, self.broken = endpoint, liff, hop, page, broken
        self.calls = []

    def __call__(self, url, agent):
        self.calls.append((url, agent))
        if url.startswith("https://liff.line.me/"):
            if url != f"https://liff.line.me/{self.liff}/":
                return 404, "", ""
            return 200, "", self.page if self.page is not None else liff_page(self.endpoint)
        assert url.startswith(f"https://{SITE}/"), url
        path = url[len(f"https://{SITE}"):]
        if any(path.startswith(b) for b in self.broken):
            return 500, "", ""
        if path == ENTER + "?to=%2Fdesk" and "Line/" in agent:
            return (302, f"https://liff.line.me/{self.liff}{HOP_PATH}", "") if self.hop else (302, "/desk", "")
        if path.startswith(ENTER + "?to="):
            return 302, "/desk", ""
        if path.startswith("/?liff.state="):
            return 302, HOP_PATH, ""
        if path == "/desk":
            return 302, "/api/method/pearnly_core.identity.begin_login?redirect_to=/desk", ""
        if path.startswith("/api/method/pearnly_core.identity.begin_login"):
            return 302, "https://auth.pearnly.com/realms/pearnly-business/protocol/openid-connect/auth?x=1", ""
        return 404, "", ""


def run(product, site=SITE, liff="", web=None):
    web = web or FakeWeb()
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        problems = check.check(product, site, liff, get=web)
    return problems, out.getvalue(), web


class Rule(unittest.TestCase):
    def test_same_rule_as_provisioning_and_the_site_step(self):
        liff = load("line_liff", ROOT / "apps/pearnly_control/pearnly_control/onboarding/line_liff.py")
        step = load("site_step", ROOT / "infrastructure/production/site-step.py")
        for product in ("erp", "cowork", "dms"):
            want = check.expected_endpoint(SITE, product)
            self.assertEqual(want, liff.endpoint(SITE, product))
            self.assertEqual(want, "https://" + SITE + step.LIFF_PATHS.get(product, "/"))
        self.assertEqual(check.expected_endpoint(SITE, "erp"), f"https://{SITE}/")


class Check(unittest.TestCase):
    def test_root_endpoint_and_a_reachable_entry_pass(self):
        for product in ("erp", "cowork"):
            with self.subTest(product=product):
                problems, out, web = run(product, liff=LIFF)
                self.assertEqual(problems, [])
                self.assertIn(f"endpoint https://{SITE}/ ok", out)
                self.assertIn("last site page /api/method/pearnly_core.identity.begin_login?redirect_to=/desk, then auth.pearnly.com", out)
                # Both addresses LINE may open: endpoint + path, and endpoint + liff.state.
                self.assertIn((f"https://{SITE}{HOP_PATH}", check.PLAIN_UA), web.calls)
                self.assertIn((f"https://{SITE}/?liff.state=%2Fapi%2Fmethod%2Fpearnly_core.account.line_login.enter"
                               "%3Fto%3D%252Fdesk%253Fpearnly_liff%253D1", check.PLAIN_UA), web.calls)

    def test_liff_id_is_read_from_the_site_hop_when_not_given(self):
        problems, out, _ = run("erp")
        self.assertEqual(problems, [])
        self.assertIn(f"LIFF {LIFF} endpoint", out)

    def test_sub_path_endpoint_is_reported_with_the_404_it_leads_to(self):
        # 2026-11-03: /stocktake and /desk/pearnly-intake.
        for product, path in (("erp", "/stocktake"), ("cowork", "/desk/pearnly-intake")):
            with self.subTest(product=product):
                problems, _, _ = run(product, liff=LIFF, web=FakeWeb(endpoint=f"https://{SITE}{path}"))
                self.assertIn(f"{SITE}: LIFF {LIFF} endpoint is https://{SITE}{path}, must be https://{SITE}/", problems)
                self.assertIn(f"{SITE}: LINE entry https://{SITE}{path}{HOP_PATH} answered 404", problems)

    def test_endpoint_without_trailing_slash_is_the_root(self):
        problems, _, _ = run("erp", liff=LIFF, web=FakeWeb(endpoint=f"https://{SITE}"))
        self.assertEqual(problems, [])

    def test_hop_to_another_liff_than_the_site_config_is_reported(self):
        problems, _, _ = run("erp", liff="2010411313-other000")
        self.assertIn(f"{SITE}: hops to LIFF {LIFF}, site config has 2010411313-other000", problems)

    def test_site_that_does_not_hop_to_liff_is_reported(self):
        problems, _, _ = run("cowork", liff=LIFF, web=FakeWeb(hop=False))
        self.assertEqual(len(problems), 1)
        self.assertIn("expected a redirect to this site's LIFF", problems[0])

    def test_unreadable_line_page_is_a_problem_not_a_pass(self):
        for page in ("<html>LINE changed this page</html>", ""):
            with self.subTest(page=page):
                problems, _, _ = run("erp", liff=LIFF, web=FakeWeb(page=page))
                self.assertEqual(len(problems), 1)
                self.assertIn("without an endpoint URL", problems[0])
        problems, _, _ = run("dms", liff="2010411313-unknown0")
        self.assertIn("without an endpoint URL", problems[0])

    def test_server_error_on_the_way_is_reported(self):
        problems, _, _ = run("erp", liff=LIFF, web=FakeWeb(broken=("/desk",)))
        self.assertTrue(problems)
        self.assertTrue(all("answered 500" in p for p in problems), problems)

    def test_dms_checks_only_the_booking_editor_endpoint(self):
        good = FakeWeb(endpoint=f"https://{SITE}/liff/dms-booking")
        problems, out, web = run("dms", liff=LIFF, web=good)
        self.assertEqual(problems, [])
        self.assertEqual([url for url, _ in web.calls], [f"https://liff.line.me/{LIFF}/"])
        problems, _, _ = run("dms", liff=LIFF, web=FakeWeb(endpoint=f"https://{SITE}/"))
        self.assertIn(f"must be https://{SITE}/liff/dms-booking", problems[0])
        problems, _, _ = run("dms")
        self.assertIn("no LIFF ID to check", problems[0])


class Main(unittest.TestCase):
    def test_exit_codes(self):
        with contextlib.redirect_stdout(io.StringIO()):
            with patch.object(check, "fetch", FakeWeb()):
                self.assertEqual(check.main([f"erp:{SITE}:{LIFF}", f"cowork:{SITE}"]), 0)
            with patch.object(check, "fetch", FakeWeb(endpoint=f"https://{SITE}/stocktake")):
                self.assertEqual(check.main([f"erp:{SITE}"]), 1)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(check.main([]), 2)
            self.assertEqual(check.main([f"pos:{SITE}"]), 2)
            self.assertEqual(check.main([SITE]), 2)


if __name__ == "__main__":
    unittest.main()
