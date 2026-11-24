"""登录后端到端执行器 scripts/e2e/run.py 的离线测试（方案 docs/work/2026-11-05-product-map-next.md §2）：
不起浏览器、不连网络；凭据、前置自检、重试口径、结果汇总与退出码。"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "e2e"))
import run as e2e  # noqa: E402
import scenarios  # noqa: E402

PROD = "107.155.65.74"
WILDCARD = {"name": "*.pearnly.com", "type": "A", "content": PROD}
SCENARIO = {"id": "e2e.erp.login", "product": "ERP", "runs": ["desktop", "iphone"]}


class FakePage:
    def __init__(self):
        self.filled, self.shots = [], []

    def query_selector_all(self, selector):
        page = self

        class Box:
            def fill(self, value):
                page.filled.append((selector, value))
        return [Box()] if selector == "input[type=password]" else []

    def screenshot(self, path, full_page):
        self.shots.append(path)


def pages():
    opened = []

    def open_page(run_name):
        page = FakePage()
        opened.append(page)
        return page, lambda: None
    return open_page, opened


class Credentials(unittest.TestCase):
    def write(self, text, mode=0o600):
        handle = tempfile.NamedTemporaryFile("w", delete=False, suffix=".env")
        handle.write(text)
        handle.close()
        os.chmod(handle.name, mode)
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_missing_file_open_permissions_or_keys_mean_not_tested(self):
        with self.assertRaisesRegex(scenarios.NotTested, "还没建"):
            e2e.load_accounts("/nonexistent/accounts.env")
        full = "E2E_ERP_USER=e2e-erp\nE2E_ERP_PASSWORD=s3cret-erp\nE2E_COWORK_USER=e2e-cowork\nE2E_COWORK_PASSWORD=s3cret-cw\n"
        with self.assertRaisesRegex(scenarios.NotTested, "600"):
            e2e.load_accounts(self.write(full, 0o644))
        with self.assertRaisesRegex(scenarios.NotTested, "E2E_COWORK_PASSWORD") as caught:
            e2e.load_accounts(self.write("E2E_ERP_USER=e2e-erp\nE2E_ERP_PASSWORD=s3cret-erp\nE2E_COWORK_USER=x\n"))
        self.assertNotIn("s3cret", str(caught.exception))

    def test_passwords_never_show_in_repr(self):
        accounts = e2e.load_accounts(self.write(
            "E2E_ERP_USER=e2e-erp\nE2E_ERP_PASSWORD=s3cret-erp\nE2E_COWORK_USER=e2e-cowork\nE2E_COWORK_PASSWORD=s3cret-cw\n"))
        self.assertEqual(accounts["erp"].password, "s3cret-erp")
        self.assertNotIn("s3cret", repr(accounts) + str(accounts))


class Preflight(unittest.TestCase):
    def test_where_a_test_tenant_points(self):
        tunnel = {"name": "testerp.pearnly.com", "type": "CNAME", "content": "abc.cfargotunnel.com"}
        self.assertEqual(e2e.where_is("testerp.pearnly.com", [WILDCARD], PROD), "生产")
        self.assertEqual(e2e.where_is("testerp.pearnly.com", [WILDCARD, tunnel], PROD), "测试机")
        self.assertEqual(e2e.where_is("testerp.pearnly.com", [{"name": "*.pearnly.com", "type": "A", "content": "1.2.3.4"}], PROD),
                         "别处（A 1.2.3.4）")
        self.assertEqual(e2e.where_is("testerp.pearnly.com", [], PROD), "没有解析记录")

    def test_a_product_whose_test_tenant_is_on_the_test_pc_is_not_tested(self):
        tunnel = {"name": "test01.pearnly.com", "type": "CNAME", "content": "abc.cfargotunnel.com"}
        problems = e2e.preflight({"erp", "cowork"}, records_of=lambda: [WILDCARD, tunnel], prod_ip=PROD)
        self.assertEqual(set(problems), {"erp"})
        self.assertIn("test01.pearnly.com 现在在测试机", problems["erp"])

        def broken():
            raise OSError("offline")
        self.assertIn("读不到 Cloudflare", e2e.preflight({"cowork"}, records_of=broken, prod_ip=PROD)["cowork"])

    def test_cloudflare_reads_only_with_get(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "cloudflare.token").write_text("tok\n")
            Path(tmp, "cloudflare.env").write_text("CF_ZONE_ID=zone1\n")
            asked = []

            def get(path, token):
                asked.append((path, token))
                return {"result": [WILDCARD]}
            self.assertEqual(e2e.cloudflare_records(tmp, get), [WILDCARD])
        self.assertEqual(asked, [("/zones/zone1/dns_records?per_page=500", "tok")])

    def test_playwright_must_match_the_product_image(self):
        pinned = e2e.pinned_playwright()
        self.assertRegex(pinned, r"^\d+\.\d+\.\d+$")
        e2e.check_playwright(installed=pinned, pinned=pinned)
        with self.assertRaisesRegex(scenarios.NotTested, "产品镜像锁的是"):
            e2e.check_playwright(installed="1.0.0", pinned=pinned)

    def test_production_ip_comes_from_the_target_file(self):
        self.assertEqual(e2e.production_ip(), PROD)


class Cells(unittest.TestCase):
    def setUp(self):
        self.out = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.out, ignore_errors=True))

    def cell(self, step):
        open_page, opened = pages()
        result = e2e.run_cell(SCENARIO, "desktop", {}, open_page, self.out, steps={"e2e.erp.login": step} if step else {})
        return result, opened

    def test_a_scenario_without_steps_is_not_tested(self):
        result, opened = self.cell(None)
        self.assertEqual((result.outcome, opened), (e2e.NOT_TESTED, []))

    def test_pass_and_business_failure_with_a_screenshot_and_cleared_password(self):
        self.assertEqual(self.cell(lambda page, ctx: None)[0].outcome, e2e.PASSED)

        def wrong(page, ctx):
            raise scenarios.Failed("跨池切换落到了 Pearnly 登录页")
        result, opened = self.cell(wrong)
        self.assertEqual(result.outcome, e2e.FAILED)
        self.assertIn("Pearnly 登录页", result.detail)
        self.assertEqual(opened[0].filled, [("input[type=password]", "")])
        self.assertTrue(result.screenshot.endswith("e2e.erp.login-desktop.png"))

    def test_login_rejection_is_never_retried_but_a_navigation_timeout_is_retried_once(self):
        calls = []

        def rejected(page, ctx):
            calls.append(1)
            raise scenarios.LoginRejected("密码不对")
        self.assertEqual(self.cell(rejected)[0].outcome, e2e.FAILED)
        self.assertEqual(len(calls), 1)

        attempts = []

        def flaky(page, ctx):
            attempts.append(1)
            if len(attempts) == 1:
                raise scenarios.NavigationTimeout("fatcc.com 30 秒没响应")
        self.assertEqual(self.cell(flaky)[0].outcome, e2e.PASSED)
        self.assertEqual(len(attempts), 2)

        def always(page, ctx):
            raise scenarios.NavigationTimeout("x")
        result, opened = self.cell(always)
        self.assertEqual((result.outcome, len(opened)), (e2e.FAILED, 2))
        self.assertIn("导航两次超时", result.detail)

    def test_the_step_sees_the_product_site_and_entry(self):
        seen = {}

        def look(page, ctx):
            seen.update(site=ctx.site, entry=ctx.entry, run=ctx.run)
        self.cell(look)
        self.assertEqual(seen, {"site": "testerp.pearnly.com", "entry": "https://fatcc.com", "run": "desktop"})


class Summary(unittest.TestCase):
    def test_browser_proxy_only_when_the_variable_is_set(self):
        self.assertEqual(e2e.launch_options({}), {"headless": True})
        self.assertEqual(e2e.launch_options({"PEARNLY_E2E_PROXY": "  "}), {"headless": True})
        self.assertEqual(e2e.launch_options({"PEARNLY_E2E_PROXY": "http://127.0.0.1:18888"}),
                         {"headless": True, "proxy": {"server": "http://127.0.0.1:18888"}})

    def test_exit_codes(self):
        r = e2e.Result
        self.assertEqual(e2e.exit_code([r("a", "desktop", e2e.PASSED)]), 0)
        self.assertEqual(e2e.exit_code([r("a", "desktop", e2e.PASSED), r("b", "mobile", e2e.FAILED)]), 1)
        self.assertEqual(e2e.exit_code([r("a", "desktop", e2e.PASSED), r("b", "mobile", e2e.NOT_TESTED)]), 3)
        self.assertEqual(e2e.exit_code([]), 3)
        text = e2e.report([r("a", "desktop", e2e.FAILED, "落错站", 3.2, "/x.png")])
        self.assertIn("不通过  a [desktop]  3.2 秒  —— 落错站（截图 /x.png）", text)
        self.assertIn("通过 0，不通过 1，没测到 0", text)

    def test_selection_by_id_product_and_run(self):
        reg = [SCENARIO, {"id": "e2e.cowork.login", "product": "Cowork", "runs": ["desktop", "mobile"]}]
        self.assertEqual([(s["id"], r) for s, r in e2e.select(reg, product="cowork")],
                         [("e2e.cowork.login", "desktop"), ("e2e.cowork.login", "mobile")])
        self.assertEqual([(s["id"], r) for s, r in e2e.select(reg, ids=["e2e.erp.login"], run="iphone")], [("e2e.erp.login", "iphone")])

    def test_the_real_registry_covers_both_products(self):
        products = {s["product"] for s in e2e.registry()}
        self.assertEqual(products, {"ERP", "Cowork"})

    def test_prune_only_removes_old_run_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, new, other = Path(tmp, "20261001T010101"), Path(tmp, "20261104T010101"), Path(tmp, "keep-me")
            for d in (old, new, other):
                d.mkdir()
            past = time.time() - 30 * 86400
            os.utime(old, (past, past))
            os.utime(other, (past, past))
            self.assertEqual(e2e.prune(tmp), ["20261001T010101"])
            self.assertTrue(new.exists() and other.exists() and not old.exists())


if __name__ == "__main__":
    unittest.main()
