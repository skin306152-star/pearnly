"""智能体入口 fatcc.com（ADR-019）的离线检查：纯约定、计费查询、入口站点封口、Keycloak 登录配置、生成的登录页。

不连库、不连 Keycloak：Frappe 用最小替身。
"""
import importlib.util
import json
import pathlib
import re
import shutil
import subprocess
import sys
import types
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONTROL = ROOT / "apps/pearnly_control/pearnly_control"
ISSUER = "https://auth.pearnly.com/realms/pearnly-business"
COWORK = "https://auth.pearnly.com/realms/pearnly-cowork"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


contracts = _load("entry_contracts", CONTROL / "assistant_entry/contracts.py")


class ContractTests(unittest.TestCase):
    def test_login_site_issuer(self):
        self.assertEqual(contracts.login("  Somchai@Example.COM "), "somchai@example.com")
        for bad in ("", "a b", "x" * 300):
            with self.assertRaises(contracts.ContractError):
                contracts.login(bad)
        self.assertEqual(contracts.site("AcMe-Cowork.pearnly.com"), "acme-cowork.pearnly.com")
        for bad in ("evil.com/x", "-a.pearnly.com", "localhost", "a.pearnly.com:8000"):
            with self.assertRaises(contracts.ContractError):
                contracts.site(bad)
        self.assertEqual(contracts.issuer(ISSUER + "/"), ISSUER)
        with self.assertRaises(contracts.ContractError):
            contracts.issuer("https://evil.com/not-a-realm")

    def test_language_defaults_to_thai(self):
        self.assertEqual(contracts.lang("zh-CN,zh;q=0.9,en;q=0.8"), "zh")
        self.assertEqual(contracts.lang("en-US"), "en")
        self.assertEqual(contracts.lang("ja-JP,fr"), "th")
        self.assertEqual(contracts.lang(""), "th")

    def test_entry_token_is_derived_not_the_root(self):
        token = contracts.entry_token("root-secret")
        self.assertEqual(token, contracts.entry_token("root-secret"))
        self.assertNotEqual(token, contracts.entry_token("other"))
        self.assertNotIn("root-secret", token)
        with self.assertRaises(contracts.ContractError):
            contracts.entry_token("")

    def test_issuer_order_puts_last_used_first(self):
        self.assertEqual(contracts.ordered_issuers([ISSUER, COWORK, ISSUER], COWORK), [COWORK, ISSUER])
        self.assertEqual(contracts.ordered_issuers([], COWORK), [])

    def test_authorize_url(self):
        url = contracts.authorize_url(COWORK, redirect_uri="https://fatcc.com/cb", state="s", nonce="n",
                                      challenge="c", login_hint="somchai", locale="zh", silent=True)
        parts = urlsplit(url)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        self.assertEqual(parts.path, "/realms/pearnly-cowork/protocol/openid-connect/auth")
        self.assertEqual(query["client_id"], "pearnly-ai")
        self.assertEqual(query["code_challenge_method"], "S256")
        self.assertEqual(query["ui_locales"], "zh-CN")
        self.assertEqual(query["prompt"], "none")
        self.assertEqual(query["login_hint"], "somchai")
        self.assertEqual(contracts.site_entry_url("acme-cowork.pearnly.com"),
                         "https://acme-cowork.pearnly.com/api/method/pearnly_core.identity.begin_login?redirect_to=/assistant")
        # 入口页这次显示的语言带给站点（按系统语言自动显示的也带），点选的另注明；不认识的不带
        self.assertEqual(contracts.site_entry_url("acme-cowork.pearnly.com", "en"),
                         "https://acme-cowork.pearnly.com/api/method/pearnly_core.identity.begin_login"
                         "?redirect_to=/assistant&locale=en")
        self.assertEqual(contracts.site_entry_url("acme-cowork.pearnly.com", "zh", picked=True),
                         "https://acme-cowork.pearnly.com/api/method/pearnly_core.identity.begin_login"
                         "?redirect_to=/assistant&locale=zh&picked=1")
        self.assertNotIn("locale", contracts.site_entry_url("acme-cowork.pearnly.com", "fr&x=1", picked=True))



class GoTests(unittest.TestCase):
    """交给公司站点登录时带上入口页这次显示的语言（用户 2026-10-13：泰文系统的新电脑，登录页是泰文，
    进去却是账号里存的中文）。点选过的只算这一次登录。"""

    def setUp(self):
        self.cookies, self.deleted = {}, []
        native = types.ModuleType("frappe")
        native._ = lambda text: text
        native.conf = {}
        native.local = types.SimpleNamespace(
            request=types.SimpleNamespace(cookies=self.cookies, headers={"Accept-Language": "th-TH,th;q=0.9,en;q=0.8"}),
            response={}, cookie_manager=types.SimpleNamespace(set_cookie=lambda *a, **kw: None,
                                                              delete_cookie=self.deleted.append))
        package = types.ModuleType("pearnly_control.assistant_entry")
        package.contracts = contracts
        modules = {"frappe": native, "jwt": types.ModuleType("jwt"), "requests": types.ModuleType("requests"),
                   "pearnly_control": types.ModuleType("pearnly_control"), "pearnly_control.assistant_entry": package,
                   "pearnly_control.assistant_entry.contracts": contracts}
        patcher = mock.patch.dict(sys.modules, modules)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.native = native
        self.entry = _load("entry_go", CONTROL / "assistant_entry/entry.py")

    def go(self):
        self.entry.go("acme-cowork.pearnly.com")
        location = urlsplit(self.native.local.response["location"])
        self.assertEqual(location.path, "/api/method/pearnly_core.identity.begin_login")
        return {k: v[0] for k, v in parse_qs(location.query).items()}

    def test_the_language_shown_by_the_system_goes_along(self):
        self.assertEqual(self.go(), {"redirect_to": "/assistant", "locale": "th"})
        self.assertEqual(self.deleted, [])

    def test_a_click_counts_for_this_sign_in_only(self):
        self.cookies.update({"pearnly_locale": "zh", "pearnly_locale_picked": "1"})
        self.assertEqual(self.go(), {"redirect_to": "/assistant", "locale": "zh", "picked": "1"})
        self.assertEqual(self.deleted, ["pearnly_locale_picked"])


class _Frappe(types.ModuleType):
    class ValidationError(Exception):
        pass

    class PermissionError(Exception):
        pass

    def __init__(self, rows, members, tenants):
        super().__init__("frappe")
        self.rows, self.members, self.tenants = rows, members, tenants
        self.db = types.SimpleNamespace(get_value=self._get_value, exists=lambda *a: False)

    @staticmethod
    def _match(row, filters):
        return all(row.get(k) == v for k, v in filters.items())

    def get_all(self, doctype, filters=None, fields=None, pluck=None, as_list=False):
        if doctype == "Pearnly Tenant":
            names = filters["name"][1]
            return [(n, self.tenants[n]) for n in names if n in self.tenants]
        source = self.rows if doctype == "Pearnly Tenant Site" else self.members
        found = [r for r in source if self._match(r, filters or {})]
        if pluck:
            return [r[pluck] for r in found]
        return [types.SimpleNamespace(**{k: r.get(k) for k in fields}) for r in found]

    def _get_value(self, doctype, filters, fields, as_dict=False):
        found = [r for r in self.members if self._match(r, filters)]
        return types.SimpleNamespace(**found[0]) if found else None


class RegistryTests(unittest.TestCase):
    def setUp(self):
        rows = [
            {"site": "acme-erp.pearnly.com", "tenant": "T-1", "product": "erp", "issuer": ISSUER,
             "owner_username": "somchai", "owner_email": "s@acme.co", "owner_subject": "sub-b"},
            {"site": "acme-cowork.pearnly.com", "tenant": "T-1", "product": "cowork", "issuer": COWORK,
             "owner_username": "somchai", "owner_email": "s@acme.co", "owner_subject": "sub-c"},
            {"site": "other-erp.pearnly.com", "tenant": "T-2", "product": "erp", "issuer": ISSUER,
             "owner_username": "boss", "owner_email": "b@other.co", "owner_subject": "sub-x"},
        ]
        members = [{"tenant": "T-2", "product": "erp", "username": "clerk", "email": "c@other.co", "subject": "sub-m"}]
        sys.modules["frappe"] = _Frappe(rows, members, {"T-1": "Acme Co.", "T-2": "Other Co."})
        sys.modules.setdefault("pearnly_control", types.ModuleType("pearnly_control"))
        sys.modules.setdefault("pearnly_control.assistant_entry", types.ModuleType("pearnly_control.assistant_entry"))
        sys.modules["pearnly_control.assistant_entry"].contracts = contracts
        sys.modules["pearnly_control.assistant_entry.contracts"] = contracts
        self.registry = _load("entry_registry", CONTROL / "assistant_entry/registry.py")

    def test_before_password_only_the_account_pool_is_revealed(self):
        self.assertEqual(self.registry.issuers_for("Somchai"), [ISSUER, COWORK])
        self.assertEqual(self.registry.issuers_for("c@other.co"), [ISSUER])
        self.assertEqual(self.registry.issuers_for("nobody"), [])

    def test_after_password_sites_follow_the_verified_identity_and_pool(self):
        self.assertEqual(self.registry.sites_for(ISSUER, "sub-b"),
                         [{"site": "acme-erp.pearnly.com", "product": "erp", "company": "Acme Co."}])
        self.assertEqual([s["site"] for s in self.registry.sites_for(COWORK, "sub-c")], ["acme-cowork.pearnly.com"])
        self.assertEqual(self.registry.sites_for(COWORK, "sub-b"), [])  # 另一个账号池的身份不算
        self.assertEqual([s["site"] for s in self.registry.sites_for(ISSUER, "sub-m")], ["other-erp.pearnly.com"])
        self.assertEqual(self.registry.sites_for(ISSUER, "sub-unknown", "somchai"), [])  # 有身份登记时不按账号名


class RouteTests(unittest.TestCase):
    """生产引擎回调只登记入口 fatcc.com：租户站点登记会话归属，入口按它转发（ADR-019）。"""

    def setUp(self):
        sites = {"acme-cowork.pearnly.com": "T-1", "other-erp.pearnly.com": "T-2"}
        self.routes = {}
        outer = self

        class Doc:
            def __init__(self, values):
                self.values = values

            def insert(self, ignore_permissions=False):
                outer.routes[self.values["session_id"]] = dict(self.values)

        def get_value(doctype, name, field):
            if doctype == "Pearnly Tenant Site":
                return sites.get(name)
            return (outer.routes.get(name) or {}).get(field)

        fake = types.ModuleType("frappe")
        fake.db = types.SimpleNamespace(get_value=get_value, exists=lambda doctype, name: name in outer.routes,
                                        set_value=lambda doctype, name, values: outer.routes[name].update(values))
        fake.get_doc = Doc
        sys.modules["frappe"] = fake
        sys.modules.setdefault("pearnly_control", types.ModuleType("pearnly_control"))
        sys.modules.setdefault("pearnly_control.assistant_entry", types.ModuleType("pearnly_control.assistant_entry"))
        sys.modules["pearnly_control.assistant_entry"].contracts = contracts
        sys.modules["pearnly_control.assistant_entry.contracts"] = contracts
        self.module = _load("entry_routes", CONTROL / "assistant_entry/routes.py")

    def test_a_tenant_can_only_route_its_own_registered_sites(self):
        self.module.record("T-1", "acme-cowork.pearnly.com", "sesn_abcd1234")
        self.assertEqual(self.module.site_for("sesn_abcd1234"), "acme-cowork.pearnly.com")
        for tenant, site in (("T-1", "other-erp.pearnly.com"), ("T-1", "unknown.pearnly.com")):
            with self.assertRaises(contracts.ContractError):
                self.module.record(tenant, site, "sesn_wxyz9876")
        self.assertEqual(self.module.site_for("sesn_never"), "")

    def test_sites_register_sessions_and_the_entry_forwards_callbacks(self):
        sessions = (ROOT / "apps/pearnly_core/pearnly_core/assistant/sessions.py").read_text(encoding="utf-8")
        for name in ("def create(", "def segment_for_turn("):
            self.assertIn("_route(", sessions.split(name, 1)[1].split("\ndef ", 1)[0], name)
        guard = (CONTROL / "billing_control.py").read_text(encoding="utf-8")
        self.assertIn('"service_assistant_route"', guard.split("SERVICE_METHODS", 1)[1].split("})", 1)[0])
        entry = (CONTROL / "assistant_entry/entry.py").read_text(encoding="utf-8")
        self.assertIn('"/api/method/pearnly_control.assistant_entry.webhook.receive"', entry.split("ALLOWED_PATHS", 1)[1])
        hook = (CONTROL / "assistant_entry/webhook.py").read_text(encoding="utf-8")
        for needle in ("verify_webhook(", "engines.wake(", 'entry._billing("route"', "allow_redirects=False",
                       '"X-Pearnly-Forwarded": "1"'):
            self.assertIn(needle, hook)
        plan = (CONTROL / "provisioning.py").read_text(encoding="utf-8")
        self.assertIn('secret = (frappe.conf.get("pearnly_assistant_webhook_secret") or "") if settings["engine"] else ""', plan)


class WiringTests(unittest.TestCase):
    def test_billing_stays_private_except_the_entry_lookup(self):
        guard = (CONTROL / "billing_control.py").read_text(encoding="utf-8")
        self.assertIn('"/api/method/pearnly_control.assistant_entry.billing.resolve"', guard)
        billing = (CONTROL / "assistant_entry/billing.py").read_text(encoding="utf-8")
        self.assertIn('methods=["POST"]', billing)
        self.assertIn("compare_digest", billing)

    def test_entry_site_only_opens_its_page_and_sign_in_endpoints(self):
        entry = (CONTROL / "assistant_entry/entry.py").read_text(encoding="utf-8")
        allowed = entry.split("ALLOWED_PATHS = ", 1)[1].split("\n\n", 1)[0]
        for name in ("start", "callback", "choose"):
            self.assertIn(name, allowed)
        self.assertIn("pearnly_control.assistant_entry.entry.guard_entry_site", (CONTROL / "hooks.py").read_text())
        api = (CONTROL / "assistant_entry/api.py").read_text(encoding="utf-8")
        self.assertIn("rate_limit", api)
        # 入口页的样式、脚本、插图都内联，不放行任何静态目录（睡觉的猫视频 2026-10-10 晚已删）
        self.assertNotIn("/assets/", entry)

    def test_registration_carries_site_and_pool(self):
        plan = (CONTROL / "provisioning.py").read_text(encoding="utf-8")
        # 账号池按本单实际建号的池，不按产品推算（2026-10-11 testcowork：拆池前开的户账号在业务池）
        self.assertIn("pool = identity_pools.site_pool(issue, dep.product)", plan)
        self.assertIn("site=dep.site_name, issuer=_issuer_url(issue.target, pool)", plan)
        self.assertIn("registry.record(", (CONTROL / "billing_control.py").read_text(encoding="utf-8"))

    def test_existing_tenants_are_registered_the_same_way_as_onboarding(self):
        # 入口上线前开通的云端部署：补登记用开户计划同一个调用（可重复），只补登记不重跑开户
        backfill = (CONTROL / "assistant_entry/backfill.py").read_text(encoding="utf-8")
        self.assertIn("billing_remote.provision_scope(", backfill)
        self.assertIn("pool = identity_pools.site_pool(issue, dep.product)", backfill)
        self.assertIn("site=dep.site_name, issuer=provisioning._issuer_url(issue.target, pool)", backfill)
        self.assertIn("owner_subject=identity_pools.subject_of(issue, pool)", backfill)
        self.assertIn("infra.is_cloud(issue.target)", backfill)
        # 生产入口站点只建 fatcc.com，令牌不打印
        entry = (ROOT / "infrastructure/production/bootstrap-ai-entry.py").read_text(encoding="utf-8")
        self.assertIn('if SITE != "fatcc.com":', entry)
        self.assertIn("contracts.entry_token(root)", entry)
        self.assertNotIn("print(root", entry)

    def test_ai_prefixes_are_reserved(self):
        agent = (ROOT / "infrastructure/production/provision-agent.py").read_text(encoding="utf-8")
        issue = (CONTROL / "pearnly_control/doctype/pearnly_issue/pearnly_issue.py").read_text(encoding="utf-8")
        for name in ('"ai"', '"testai"'):
            self.assertIn(name, agent.split("RESERVED =", 1)[1].split("\n", 1)[0])
            self.assertIn(name, issue.split("RESERVED_PREFIXES", 1)[1].split("}", 1)[0])


class KeycloakTests(unittest.TestCase):
    def test_ai_client_is_public_pkce_with_fixed_callbacks(self):
        sys.path.insert(0, str(ROOT / "infrastructure/production"))
        module = _load("configure_ai_entry", ROOT / "infrastructure/production/configure-ai-entry.py")
        rep = module.representation(["https://fatcc.com", "https://testai.pearnly.com"])
        self.assertTrue(rep["publicClient"])
        self.assertFalse(rep["directAccessGrantsEnabled"] or rep["implicitFlowEnabled"] or rep["serviceAccountsEnabled"])
        self.assertEqual(rep["redirectUris"], [
            "https://fatcc.com/api/method/pearnly_control.assistant_entry.api.callback",
            "https://testai.pearnly.com/api/method/pearnly_control.assistant_entry.api.callback"])
        self.assertEqual(rep["attributes"]["pkce.code.challenge.method"], "S256")
        self.assertEqual(rep["attributes"]["login_theme"], "pearnly-ai")
        compose = (ROOT / "infrastructure/production/compose.yml").read_text(encoding="utf-8")
        self.assertIn("../keycloak/themes/pearnly-ai:/opt/keycloak/themes/pearnly-ai:ro", compose)


class LogoutBackTests(unittest.TestCase):
    """从对话页退出回到智能体入口（ADR-019）：只追加不删，只动开户建的站点客户端。"""

    def setUp(self):
        sys.path.insert(0, str(ROOT / "infrastructure/production"))
        self.module = _load("configure_ai_entry_logout", ROOT / "infrastructure/production/configure-ai-entry.py")

    def test_existing_tenant_clients_only_gain_the_entry(self):
        rows = {
            "1": {"id": "1", "clientId": "acme-erp.pearnly.com", "attributes": {
                "backchannel.logout.url": "x", "post.logout.redirect.uris": "https://acme-erp.pearnly.com/"}},
            "2": {"id": "2", "clientId": "testcowork.pearnly.com", "attributes": {"backchannel.logout.url": "x"}},
            "3": {"id": "3", "clientId": "pearnly-control", "attributes": {}},
            "4": {"id": "4", "clientId": "done.pearnly.com", "attributes": {
                "backchannel.logout.url": "x", "post.logout.redirect.uris": "https://done.pearnly.com/##https://fatcc.com/*"}},
        }

        class Api:
            realm = "pearnly-business"

            def request(self, path, method="GET", data=None):
                if method == "PUT":
                    rows[path.rsplit("/", 1)[1]] = data
                    return None
                if path.endswith("?max=1000"):
                    return list(rows.values())
                return rows[path.rsplit("/", 1)[1]]

        self.assertEqual(self.module.allow_logout_back(Api()), 2)
        self.assertEqual(rows["1"]["attributes"]["post.logout.redirect.uris"],
                         "https://acme-erp.pearnly.com/##https://fatcc.com/*")
        self.assertEqual(rows["2"]["attributes"]["post.logout.redirect.uris"],
                         "https://fatcc.com/*##https://testai.pearnly.com/*")
        self.assertNotIn("post.logout.redirect.uris", rows["3"]["attributes"])

    def test_chat_sign_out_and_new_tenants_go_back_to_the_entry(self):
        identity = (ROOT / "apps/pearnly_core/pearnly_core/identity.py").read_text(encoding="utf-8")
        self.assertIn('if to == "assistant":', identity)
        self.assertIn('ASSISTANT_ENTRY = "https://fatcc.com"', identity)
        page = (ROOT / "frontend/assistant/src/assistant.tsx").read_text(encoding="utf-8")
        self.assertIn("pearnly_core.identity.sign_out?to=assistant", page)
        plan = (CONTROL / "provisioning.py").read_text(encoding="utf-8")
        self.assertIn('"post.logout.redirect.uris": base + "/##https://fatcc.com/*"', plan)


class PageTests(unittest.TestCase):
    def test_generated_files_match_the_generator(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/build-ai-entry.py"), "--check"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_the_dots_background_keeps_at_most_one_draw_loop(self):
        # 10-18：点阵原来每次从看不见变回看得见、每次改窗口大小都多排一条 requestAnimationFrame 循环（看不见时排着的那一帧
        # 回来照样续上），CC 桌面版登录窗口最小化 / 还原几次就越画越费。浏览器看不见时不跑 rAF 回调但留着；这里照此模拟。
        script = next((ROOT / "infrastructure/keycloak/themes/pearnly-ai/login/resources/js").glob("ai.*.js"))
        page = (CONTROL / "www/ai.html").read_text(encoding="utf-8")
        dots = script.read_text(encoding="utf-8").split("var scene=", 1)[0]
        self.assertIn(dots, page)   # 入口第一页用的是同一段
        runner = r"""
const vm = require("vm"), fs = require("fs");
const queue = new Map(); let id = 0, hidden = false, intervals = 0, drawn = 0;
const on = {}, listen = (t, f) => (on[t] = on[t] || []).push(f), fire = (t) => (on[t] || []).forEach((f) => f({}));
const ctx = new Proxy({}, { get: (t, k) => (k in t ? t[k] : () => { if (k === "clearRect") drawn++; }), set: (t, k, v) => ((t[k] = v), true) });
const list = { forEach() {} };
const document = { get hidden() { return hidden; }, addEventListener: listen, querySelectorAll: () => list, getElementById: () => null,
  querySelector: (s) => (s === "canvas.dots" ? { getContext: () => ctx, style: {} } : null),
  body: { classList: { contains: () => false, add() {}, remove() {} } }, documentElement: { classList: { remove() {} } } };
const g = { document, matchMedia: () => ({ matches: false }), performance: { now: () => 0 }, innerWidth: 120, innerHeight: 80,
  devicePixelRatio: 1, addEventListener: listen, setTimeout: () => 0, setInterval: () => ++intervals,
  requestAnimationFrame: (cb) => (queue.set(++id, cb), id), cancelAnimationFrame: (n) => queue.delete(n),
  location: { hash: "", pathname: "/", search: "" }, history: { replaceState() {} } };
g.window = g;
vm.runInNewContext(fs.readFileSync(process.argv[1], "utf8"), g);
const frame = () => { if (hidden) return; const due = [...queue.values()]; queue.clear(); due.forEach((cb) => cb(16)); };
const out = { start: queue.size, hidden: [], visible: [] };
for (let i = 0; i < 3; i++) frame();
for (let i = 0; i < 20; i++) {
  hidden = true; fire("visibilitychange"); frame(); out.hidden.push(queue.size);
  hidden = false; fire("visibilitychange"); frame(); frame(); out.visible.push(queue.size);
}
for (let i = 0; i < 5; i++) { fire("resize"); frame(); }
out.end = queue.size; out.intervals = intervals;
const before = drawn; for (let i = 0; i < 10; i++) frame(); out.drawsPerFrame = (drawn - before) / 10;
console.log(JSON.stringify(out));
"""
        result = subprocess.run(["node", "-e", runner, str(script)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out["start"], 1)
        self.assertEqual(out["hidden"], [0] * 20)    # 看不见时停画：没有排着的帧
        self.assertEqual(out["visible"], [1] * 20)   # 变回看得见：只有一条
        self.assertEqual(out["end"], 1)              # 改窗口大小只重画，不另起
        self.assertEqual(out["drawsPerFrame"], 1)
        self.assertEqual(out["intervals"], 1)        # 小人的随机动作只有一个定时器

    def test_page_strings_have_translations(self):
        page = (CONTROL / "www/ai.html").read_text(encoding="utf-8")
        source = page + (CONTROL / "www/ai.py").read_text(encoding="utf-8")
        used = set(re.findall(r'_\("((?:[^"\\]|\\.)+)"\)', source))
        terms = {line.split("\t", 1)[0] for line in (CONTROL / "i18n/messages.tsv").read_text(encoding="utf-8").splitlines()}
        self.assertTrue(used)
        self.assertEqual(sorted(used - terms), [])
        self.assertIn("| safe", page)
        self.assertIn("{{ s.company | e }}", page)

    def test_theme_messages_cover_the_templates(self):
        theme = ROOT / "infrastructure/keycloak/themes/pearnly-ai/login"
        self.assertIn("parent=pearnly", (theme / "theme.properties").read_text())
        keys = set(re.findall(r'msg\("(pearnlyAi\w+)"\)', (theme / "template.ftl").read_text() + (theme / "login.ftl").read_text()))
        for code in ("th", "zh_CN", "en"):
            defined = {line.split("=", 1)[0] for line in (theme / f"messages/messages_{code}.properties").read_text().splitlines()
                       if "=" in line and not line.startswith("#")}
            self.assertEqual(sorted(keys - defined), [], code)


if __name__ == "__main__":
    unittest.main()
