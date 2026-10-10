"""智能体入口 ai.pearnly.com（ADR-019）的离线检查：纯约定、计费查询、入口站点封口、Keycloak 登录配置、生成的登录页。

不连库、不连 Keycloak：Frappe 用最小替身。
"""
import importlib.util
import json
import pathlib
import re
import subprocess
import sys
import types
import unittest
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
        url = contracts.authorize_url(COWORK, redirect_uri="https://ai.pearnly.com/cb", state="s", nonce="n",
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

    def test_registration_carries_site_and_pool(self):
        plan = (CONTROL / "provisioning.py").read_text(encoding="utf-8")
        self.assertIn("site=dep.site_name, issuer=_issuer_url(issue.target, dep.product)", plan)
        self.assertIn("registry.record(", (CONTROL / "billing_control.py").read_text(encoding="utf-8"))

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
        rep = module.representation(["https://ai.pearnly.com", "https://testai.pearnly.com"])
        self.assertTrue(rep["publicClient"])
        self.assertFalse(rep["directAccessGrantsEnabled"] or rep["implicitFlowEnabled"] or rep["serviceAccountsEnabled"])
        self.assertEqual(rep["redirectUris"], [
            "https://ai.pearnly.com/api/method/pearnly_control.assistant_entry.api.callback",
            "https://testai.pearnly.com/api/method/pearnly_control.assistant_entry.api.callback"])
        self.assertEqual(rep["attributes"]["pkce.code.challenge.method"], "S256")
        self.assertEqual(rep["attributes"]["login_theme"], "pearnly-ai")
        compose = (ROOT / "infrastructure/production/compose.yml").read_text(encoding="utf-8")
        self.assertIn("../keycloak/themes/pearnly-ai:/opt/keycloak/themes/pearnly-ai:ro", compose)


class PageTests(unittest.TestCase):
    def test_generated_files_match_the_generator(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/build-ai-entry.py"), "--check"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

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
