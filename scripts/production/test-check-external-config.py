#!/usr/bin/env python3
"""部署前站外设置对比（check-external-config.py + external-config-collect.py）：拦与不拦、形状规则、密钥不出生产机。离线。

样例按 11-05 生产只读核查的样子搭（docs/work/2026-11-05-product-map-next.md §14 B0）：两个池设置一致、站点客户端形状一致、
test 开头的多一条 testai 退出跳转、两个池各一个 line-2010411313、10 个站点都登记在 fatcc.com。
"""
import contextlib
import copy
import hashlib
import hmac
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ext = _load("check_external_config", HERE / "check-external-config.py")
col = _load("external_config_collect_under_test", HERE / "external-config-collect.py")
SRC = ext.Sources()
PROD_IP = "107.155.65.74"
CHANNEL = "2010411313"
ALIAS = "line-" + CHANNEL
BUSINESS, COWORK = "https://auth.pearnly.com/realms/pearnly-business", "https://auth.pearnly.com/realms/pearnly-cowork"
SITES = {"erp": ["gize", "mrerp", "polymer", "test01", "testerp"], "cowork": ["ccdemo", "erpcowork", "testcowork"],
         "dms": ["ris", "testdms"]}
EXT = {key: {"class": cls} for key, cls in (
    ("kc:pearnly-business:remember-me", "shape"), ("kc:pearnly-cowork:remember-me", "shape"), ("kc:site-client", "same"),
    ("kc:site-client-secret", "presence"), ("kc:pearnly-ai", "shape"), ("kc:line-idp", "shape"), ("line:webhook", "shape"),
    ("line:bot-mode", "same"), ("liff:endpoint", "shape"), ("line:richmenu", "presence"), ("line:login-callback", "shape"),
    ("line:webhook-test", "presence"), ("ext:fatcc.com", "shape"), ("dns:tenant", "shape"), ("tls:expiry", "shape"))}


def site_client(host):
    base = f"https://{host}"
    logout = [base + "/"] + SRC.ai_entry.logout_targets(host)
    return {"clientId": host, "enabled": True, "protocol": "openid-connect", "publicClient": False, "bearerOnly": False,
            "standardFlowEnabled": True, "directAccessGrantsEnabled": False, "implicitFlowEnabled": False,
            "serviceAccountsEnabled": False, "frontchannelLogout": True, "redirectUris": [base + ext.CALLBACK],
            "webOrigins": [base], "fullScopeAllowed": True, "defaultClientScopes": ["pearnly-login-source", "profile"],
            "attributes": {"pkce.code.challenge.method": "S256", "post.logout.redirect.uris": "##".join(logout),
                           "backchannel.logout.url": base + ext.BACKCHANNEL, "backchannel.logout.session.required": "true"}}


def ai_client():
    rep = SRC.ai_entry.representation(list(SRC.ai_entry.PRODUCTION_ORIGINS))
    return {**{k: rep[k] for k in col.CLIENT_FIELDS if k in rep}, "attributes": dict(rep["attributes"])}


def idp():
    want = SRC.line_login.idp_representation(ALIAS, CHANNEL, "secret-never-read")
    return {**{k: want[k] for k in col.IDP_FIELDS if k in want and want[k] is not None},
            "config": {k: want["config"][k] for k in col.IDP_CONFIG}}


def realm_facts(realm, sites):
    settings = SRC.bootstrap.realm_settings(realm)
    return {"realm": {**{k: settings[k] for k in col.REALM_FIELDS if k in settings}, "passwordPolicy": None,
                      "ssoSessionIdleTimeout": 1800, "ssoSessionMaxLifespan": 36000},
            "clients": [site_client(s) for s in sites] + [ai_client(), {"clientId": "pearnly-control", "publicClient": False}],
            "idps": [idp()], "default_scopes": ["pearnly-login-source", "profile"],
            "client_scopes": ["pearnly-login-source", "profile"],
            "required_actions": {a: "1" for a in col.REQUIRED_ACTIONS},
            "deny_flow": [["deny-access-authenticator", "REQUIRED"]]}


def line_facts(host):
    return {"webhook": {"endpoint": SRC.site_step.webhook_url(host), "active": True},
            "bot": {"chatMode": "bot", "markAsReadMode": "auto"}, "richmenu": True,
            "webhook_test": {"success": True, "statusCode": 200}}


def good_facts():
    sites, business, cowork = {}, [], []
    for product, names in SITES.items():
        rows = []
        for name in names:
            host = f"{name}.pearnly.com"
            issuer = COWORK if product == "cowork" else BUSINESS
            (cowork if product == "cowork" else business).append(host)
            with_line = name != "ccdemo"
            rows.append({"site": host, "issuer": issuer, "client_id": host, "redirect_uri": f"https://{host}{ext.CALLBACK}",
                         "line_login_idp": ALIAS if with_line and product != "dms" else None,
                         "liff_id": f"{CHANNEL}-{name}" if with_line else None,
                         "line": line_facts(host) if with_line else None, "secret_match": True})
        sites[product] = rows
    registered = [{"site": r["site"], "product": p, "issuer": r["issuer"]} for p, rows in sites.items() for r in rows]
    return {"version": 1, "keycloak": {"pearnly-business": realm_facts("pearnly-business", business),
                                       "pearnly-cowork": realm_facts("pearnly-cowork", cowork)},
            "sites": sites, "billing": {"sites": registered, "entry": {
                "entry": 1, "url": "https://fatcc.com", "default_issuer": BUSINESS, "billing_host": "billing.internal",
                "token": True, "webhook_secret": True}}}


def records():
    return [{"type": "A", "name": "*.pearnly.com", "content": PROD_IP, "proxied": False},
            {"type": "A", "name": "auth.pearnly.com", "content": PROD_IP, "proxied": False},
            {"type": "A", "name": "fatcc.com", "content": PROD_IP, "proxied": False},
            {"type": "CNAME", "name": "testai.pearnly.com", "content": "x.cfargotunnel.com", "proxied": True}]


def certs(**override):
    hosts = [f"{n}.pearnly.com" for names in SITES.values() for n in names] + ["auth.pearnly.com", "fatcc.com"]
    return {**{h: 60.0 for h in hosts}, **override}


def line_ok(url):
    return 302, "https://access.line.me/oauth2/v2.1/login?returnUri=x"


def run(facts=None, recs=None, cert=None, fetch=line_ok, ext_map=EXT):
    return ext.compare(SRC, facts or good_facts(), ext_map, records=records() if recs is None else recs,
                       certs=certs() if cert is None else cert, fetch=fetch, prod_ip=PROD_IP)


def site_row(facts, host):
    return next(r for rows in facts["sites"].values() for r in rows if r["site"] == host)


class Compare(unittest.TestCase):
    def test_production_as_read_on_11_05_passes(self):
        out = run()
        self.assertEqual(out.block, [])
        self.assertEqual(out.note, [])

    def test_each_misconfiguration_blocks_with_its_key(self):
        def remember_me_off(f):
            f["keycloak"]["pearnly-business"]["realm"].update(rememberMe=False, ssoSessionIdleTimeoutRememberMe=0)

        def pools_differ(f):
            f["keycloak"]["pearnly-cowork"]["realm"]["ssoSessionIdleTimeout"] = 3600

        def missing_registration(f):
            f["billing"]["sites"] = [r for r in f["billing"]["sites"] if r["site"] != "mrerp.pearnly.com"]

        def wrong_registration_issuer(f):
            next(r for r in f["billing"]["sites"] if r["site"] == "erpcowork.pearnly.com")["issuer"] = BUSINESS

        def webhook_off(f):
            site_row(f, "mrerp.pearnly.com")["line"]["webhook"]["active"] = False

        def webhook_elsewhere(f):
            site_row(f, "gize.pearnly.com")["line"]["webhook"]["endpoint"] = "https://gize.pearnly.com/stocktake/hook"

        def chat_mode(f):
            site_row(f, "erpcowork.pearnly.com")["line"]["bot"]["chatMode"] = "chat"

        def read_mode_differs(f):
            site_row(f, "gize.pearnly.com")["line"]["bot"]["markAsReadMode"] = "manual"

        def secret_mismatch(f):
            site_row(f, "polymer.pearnly.com")["secret_match"] = False

        def client_missing(f):
            clients = f["keycloak"]["pearnly-cowork"]["clients"]
            f["keycloak"]["pearnly-cowork"]["clients"] = [c for c in clients if c["clientId"] != "erpcowork.pearnly.com"]

        def logout_without_entry(f):
            client = next(c for c in f["keycloak"]["pearnly-business"]["clients"] if c["clientId"] == "mrerp.pearnly.com")
            client["attributes"]["post.logout.redirect.uris"] = "https://mrerp.pearnly.com/"

        def formal_differs_from_test(f):
            client = next(c for c in f["keycloak"]["pearnly-business"]["clients"] if c["clientId"] == "gize.pearnly.com")
            client["frontchannelLogout"] = False

        def ai_without_pkce(f):
            ai = next(c for c in f["keycloak"]["pearnly-cowork"]["clients"] if c["clientId"] == "pearnly-ai")
            ai["attributes"]["pkce.code.challenge.method"] = ""

        def idp_shown(f):
            f["keycloak"]["pearnly-business"]["idps"][0]["hideOnLogin"] = False

        def idp_missing(f):
            f["keycloak"]["pearnly-cowork"]["idps"] = []

        def deny_flow_open(f):
            f["keycloak"]["pearnly-business"]["deny_flow"] = []

        def keycloak_unreadable(f):
            f["keycloak"]["pearnly-cowork"] = {"error": "Keycloak pearnly-cowork token: HTTP 401"}

        def billing_unreadable(f):
            f["billing"] = {"error": "ImportError"}

        def line_refused(f):
            site_row(f, "ris.pearnly.com")["line"] = {"error": "LINE refused the channel credentials (HTTP 400)",
                                                      "kind": "refused"}

        def container_unreadable(f):
            f["sites"]["dms"] = {"error": "No such container"}

        cases = {
            "记住我没开": (remember_me_off, "kc:pearnly-business:remember-me"),
            "两个池普通会话不同": (pools_differ, "kc:pearnly-cowork:remember-me"),
            "缺入口登记": (missing_registration, "ext:fatcc.com"),
            "登记的签发方不对": (wrong_registration_issuer, "ext:fatcc.com"),
            "webhook 关着": (webhook_off, "line:webhook"),
            "webhook 地址不是本站": (webhook_elsewhere, "line:webhook"),
            "聊天模式": (chat_mode, "line:bot-mode"),
            "已读模式与测试租户不同": (read_mode_differs, "line:bot-mode"),
            "客户端密钥两端不一致": (secret_mismatch, "kc:site-client-secret"),
            "池里没有站点客户端": (client_missing, "kc:site-client"),
            "退出跳转缺 fatcc.com": (logout_without_entry, "kc:site-client"),
            "正式租户客户端结构与测试租户不同": (formal_differs_from_test, "kc:site-client"),
            "pearnly-ai 没有 PKCE": (ai_without_pkce, "kc:pearnly-ai"),
            "LINE 入口没隐藏": (idp_shown, "kc:line-idp"),
            "池里没有 LINE 入口": (idp_missing, "kc:line-idp"),
            "首次登录不拒绝": (deny_flow_open, "kc:line-idp"),
            "Keycloak 读不到": (keycloak_unreadable, "kc:pearnly-cowork:remember-me"),
            "入口登记读不到": (billing_unreadable, "ext:fatcc.com"),
            "LINE 不认频道凭据": (line_refused, "line:webhook"),
            "产品容器读不到": (container_unreadable, "kc:site-client"),
        }
        for name, (mutate, key) in cases.items():
            with self.subTest(case=name):
                facts = good_facts()
                mutate(facts)
                out = run(facts)
                self.assertTrue(any(line.startswith(f"[{key}]") for line in out.block), (out.block, out.note))

    def test_report_only_items_do_not_block(self):
        def richmenu_missing(f):
            site_row(f, "gize.pearnly.com")["line"]["richmenu"] = False

        def webhook_test_failed(f):
            site_row(f, "mrerp.pearnly.com")["line"]["webhook_test"] = {"success": False, "statusCode": 500}

        def line_unreachable(f):
            site_row(f, "polymer.pearnly.com")["line"] = {"error": "TimeoutError", "kind": "unreachable"}

        def orphan_client(f):
            f["keycloak"]["pearnly-business"]["clients"].append(site_client("testcowork.pearnly.com"))

        def secret_not_compared(f):
            site_row(f, "gize.pearnly.com")["secret_match"] = None

        def extra_registration(f):
            f["billing"]["sites"].append({"site": "gone.pearnly.com", "product": "erp", "issuer": BUSINESS})

        def entry_without_webhook_secret(f):
            f["billing"]["entry"]["webhook_secret"] = False

        cases = {"默认富菜单与测试租户不同": (richmenu_missing, "line:richmenu"),
                 "webhook 测试没成功": (webhook_test_failed, "line:webhook-test"),
                 "LINE 一时连不上": (line_unreachable, "line:webhook"),
                 "池里多出的客户端（9-26 拆池留下的 testcowork）": (orphan_client, "kc:site-client"),
                 "密钥没比成": (secret_not_compared, "kc:site-client-secret"),
                 "登记里多一个已拆的站": (extra_registration, "ext:fatcc.com"),
                 "入口站点缺回调签名密钥": (entry_without_webhook_secret, "ext:fatcc.com")}
        for name, (mutate, key) in cases.items():
            with self.subTest(case=name):
                facts = good_facts()
                mutate(facts)
                out = run(facts)
                self.assertEqual(out.block, [])
                self.assertTrue(any(line.startswith(f"[{key}]") for line in out.note), out.note)
        out = run(copy.deepcopy(good_facts()) | {}, recs=None)
        self.assertEqual(out.block, [])

    def test_dns_and_certificates(self):
        moved = records() + [{"type": "CNAME", "name": "testerp.pearnly.com", "content": "t.cfargotunnel.com", "proxied": True}]
        out = run(recs=moved)
        self.assertEqual(out.block, [])
        self.assertTrue(any("testerp.pearnly.com 现在在测试机" in n for n in out.note), out.note)
        formal_moved = records() + [{"type": "A", "name": "mrerp.pearnly.com", "content": "1.2.3.4", "proxied": False}]
        self.assertTrue(any("[dns:tenant] erp mrerp.pearnly.com 解析到别处" in b for b in run(recs=formal_moved).block))
        proxied = [dict(r, proxied=True) if r["name"] == "fatcc.com" else r for r in records()]
        self.assertTrue(any("fatcc.com 应只解析不代理" in b for b in run(recs=proxied).block))
        proxy_differs = records() + [{"type": "A", "name": "gize.pearnly.com", "content": PROD_IP, "proxied": True}]
        self.assertTrue(any("代理开" in b and "应一致" in b for b in run(recs=proxy_differs).block))
        no_auth = [r for r in records() if r["name"] != "auth.pearnly.com"]
        self.assertTrue(any("auth.pearnly.com 没有指向生产" in b for b in run(recs=no_auth).block))
        out = run(cert=certs(**{"mrerp.pearnly.com": 5.0, "fatcc.com": 15.0, "ris.pearnly.com": "SSLError"}))
        self.assertEqual([b for b in out.block if "tls" in b], ["[tls:expiry] mrerp.pearnly.com 证书只剩 5.0 天（少于 7 天）"])
        self.assertTrue(any("fatcc.com 证书剩 15.0 天" in n for n in out.note))
        self.assertTrue(any("ris.pearnly.com 证书读不到" in n for n in out.note))

    def test_line_login_callback_follows_what_line_answers(self):
        asked = []

        def unregistered(url):
            asked.append(url)
            return 400, None

        out = run(fetch=unregistered)
        self.assertTrue(any(b.startswith("[line:login-callback]") and "没登记 pearnly-business" in b for b in out.block))
        self.assertEqual(len(asked), 2)   # 两个池各一个入口，同一频道
        self.assertIn("redirect_uri=https%3A%2F%2Fauth.pearnly.com%2Frealms%2Fpearnly-cowork%2Fbroker%2Fline-2010411313%2Fendpoint",
                      asked[1])
        out = run(fetch=lambda url: (_ for _ in ()).throw(OSError("down")))
        self.assertEqual(out.block, [])
        self.assertTrue(any("没测到" in n for n in out.note))

    def test_every_ext_section_in_the_map_has_a_check(self):
        exported = subprocess.run([sys.executable, str(ROOT / "scripts/check-product-map.py"), "--export", "/dev/stdout"],
                                  capture_output=True, text=True, check=True).stdout
        keys = set(json.loads(exported[exported.index("{"):])["ext"])
        self.assertEqual(keys, set(ext.CHECKED) | set(ext.NOT_READ))
        out = ext.Findings({"kc:new": {"class": "same"}})
        out.add("kc:new", "x")
        self.assertEqual(out.block, ["[kc:new] x"])

    def test_expectations_come_from_the_provisioning_scripts(self):
        # 身份提供者交回的 config 键 = 开户写入的键去掉密钥；两边不一致说明有一边改了没跟
        want = SRC.line_login.idp_representation(ALIAS, CHANNEL, "s")
        self.assertEqual(set(col.IDP_CONFIG), set(want["config"]) - {"clientSecret"})
        self.assertTrue(set(col.IDP_FIELDS) <= set(want))
        self.assertTrue(set(SRC.bootstrap.realm_settings("x")) <= set(col.REALM_FIELDS))
        self.assertEqual(col.SITE_CLIENT.pattern, SRC.ai_entry.SITE_CLIENT.pattern)
        self.assertEqual(col.REALMS, tuple(sorted(set(SRC.line_login.REALMS.values()))))


class Cli(unittest.TestCase):
    def call(self, facts, *extra):
        with tempfile.TemporaryDirectory() as folder:
            f, m, r, c = (Path(folder) / n for n in ("facts", "map.json", "records.json", "certs.json"))
            f.write_text("__EXT " + json.dumps(facts))
            m.write_text(json.dumps({"ext": EXT, "config": {}, "rows": []}))
            r.write_text(json.dumps(records()))
            c.write_text(json.dumps(certs()))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = ext.main(["--facts", str(f), "--map", str(m), "--records", str(r), "--certs", str(c), "--no-network",
                                 *extra])
            return code, out.getvalue()

    def test_exit_codes_and_output(self):
        code, text = self.call(good_facts())
        self.assertEqual(code, 0, text)
        self.assertIn("站外设置对比通过", text)
        self.assertIn("读不到（留在开户清单 / 真机单）", text)
        self.assertIn("NOTE [line:login-callback] 离线：LINE 登录回调没测", text)
        self.assertIn("erp：站点 5 个（配了 LINE 5 个）", text)
        facts = good_facts()
        facts["billing"]["sites"] = facts["billing"]["sites"][1:]
        code, text = self.call(facts)
        self.assertEqual(code, 1)
        self.assertIn("BLOCK [ext:fatcc.com] erp gize.pearnly.com：fatcc.com 没有这家的租户站点登记", text)
        self.assertIn("没有绕过开关", text)
        with tempfile.TemporaryDirectory() as folder:
            bad = Path(folder) / "facts"
            bad.write_text("not json")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(ext.main(["--facts", str(bad), "--map", str(bad), "--no-network"]), 2)


# ---------------------------------------------------------------- 生产机段：密钥不出容器、不出生产机

SITE_SECRET, CHANNEL_SECRET, STORED_TOKEN, FRESH_TOKEN = "oidc-secret-AAAA", "channel-secret-BBBB", "stored-token-CCCC", "fresh-DDDD"
KC_SECRET, READER_SECRET, LINE_LOGIN_SECRET = "oidc-secret-AAAA", "reader-secret-EEEE", "line-login-secret-FFFF"


class _Response(io.BytesIO):
    def __init__(self, status, body):
        super().__init__(json.dumps(body).encode() if body is not None else b"")
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_line(calls):
    import urllib.error

    def urlopen(request, timeout=None):
        url, auth = request.full_url, request.get_header("Authorization") or ""
        calls.append((request.get_method(), url, auth))
        if url.endswith("/oauth2/v3/token"):
            return _Response(200, {"access_token": FRESH_TOKEN})
        if url.endswith("/webhook/endpoint"):
            return _Response(200, {"endpoint": "https://x/api/method/pearnly_core.line.webhook", "active": True})
        if url.endswith("/v2/bot/info"):
            return _Response(200, {"chatMode": "bot", "markAsReadMode": "auto", "userId": "Uxxx", "basicId": "@x"})
        if url.endswith("/richmenu"):
            raise urllib.error.HTTPError(url, 404, "no", {}, None)
        if url.endswith("/webhook/test"):
            return _Response(200, {"success": True, "statusCode": 200, "reason": "OK", "detail": "200"})
        raise AssertionError(url)
    return urlopen


def run_site_dumper(sites_dir: Path, salt: str, calls: list) -> str:
    """在本进程里跑容器内那段脚本（假 LINE），返回它的标准输出。"""
    out = io.StringIO()
    cwd = os.getcwd()
    try:
        os.chdir(sites_dir.parent)
        with mock.patch("urllib.request.urlopen", fake_line(calls)), mock.patch.object(sys, "argv", ["-", salt]), \
                contextlib.redirect_stdout(out):
            exec(compile(col.SITE_DUMPER, "site-dumper", "exec"), {"__name__": "__main__"})
    finally:
        os.chdir(cwd)
    return out.getvalue()


class FakeKeycloak:
    def __init__(self, realm, secret):
        assert secret == READER_SECRET
        self.realm = realm

    def get(self, path):
        if path == "":
            return {"realm": self.realm, "rememberMe": True, "smtpServer": {"password": "smtp-GGGG"}}
        if path.startswith("/clients?"):
            return [{"id": "1", "clientId": "testerp.pearnly.com", "secret": KC_SECRET, "publicClient": False,
                     "attributes": {"client.secret.creation.time": "1", "pkce.code.challenge.method": "S256"}},
                    {"id": "2", "clientId": "gize.pearnly.com", "secret": "other-HHHH", "publicClient": False}]
        if path.endswith("/client-secret"):
            return {"type": "secret", "value": KC_SECRET if "/1/" in path else "other-HHHH"}
        if path == "/identity-provider/instances":
            return [{"alias": ALIAS, "hideOnLogin": True, "config": {"clientId": CHANNEL, "clientSecret": LINE_LOGIN_SECRET}}]
        if path.endswith("/config"):
            return {"config": {"max_auth_age": "1"}}
        if path.endswith("/executions"):
            return [{"providerId": "deny-access-authenticator", "requirement": "REQUIRED", "id": "e"}]
        return [{"name": "pearnly-login-source"}]


class Collector(unittest.TestCase):
    def test_secrets_tokens_and_digests_never_leave_the_host(self):
        calls = []
        with tempfile.TemporaryDirectory() as folder:
            sites = Path(folder) / "sites"
            for name, extra in (("testerp.pearnly.com", {"pearnly_line_channel_access_token": STORED_TOKEN}),
                                ("gize.pearnly.com", {})):
                (sites / name).mkdir(parents=True)
                (sites / name / "site_config.json").write_text(json.dumps({
                    "pearnly_oidc_issuer": BUSINESS + "/", "pearnly_oidc_client_id": name,
                    "pearnly_oidc_redirect_uri": f"https://{name}{ext.CALLBACK}", "pearnly_oidc_client_secret": SITE_SECRET,
                    "pearnly_line_channel_id": "1650000000", "pearnly_line_channel_secret": CHANNEL_SECRET,
                    "pearnly_line_liff_id": CHANNEL + "-abc", "pearnly_line_login_idp": ALIAS, **extra}))
            (sites / "common_site_config.json").write_text(json.dumps({"db_password": "db-IIII"}))

            def fake_run(argv, input=None, **kwargs):
                if col.BILLING_WEB in argv:
                    return subprocess.CompletedProcess(argv, 0, '__BILLING {"entry": {"token": true}, "sites": []}\n', "")
                if "pearnly-production-erp-web-1" in argv:
                    return subprocess.CompletedProcess(argv, 0, run_site_dumper(sites, argv[-1], calls), "")
                return subprocess.CompletedProcess(argv, 1, "", "Error: No such container")

            facts = col.collect(run=fake_run, keycloak=FakeKeycloak,
                                reader={"PEARNLY_CONFIG_READER_SECRET_PEARNLY_BUSINESS": READER_SECRET,
                                        "PEARNLY_CONFIG_READER_SECRET_PEARNLY_COWORK": READER_SECRET})
        text = json.dumps(facts)
        for secret in (SITE_SECRET, CHANNEL_SECRET, STORED_TOKEN, FRESH_TOKEN, KC_SECRET, READER_SECRET, LINE_LOGIN_SECRET,
                       "other-HHHH", "smtp-GGGG", "db-IIII"):
            self.assertNotIn(secret, text)
        self.assertIsNone(re.search(r"[0-9a-f]{64}", text), "密钥摘要不出生产机")
        rows = {r["site"]: r for r in facts["sites"]["erp"]}
        self.assertIs(rows["testerp.pearnly.com"]["secret_match"], True)
        self.assertIs(rows["gize.pearnly.com"]["secret_match"], False)
        self.assertEqual(rows["gize.pearnly.com"]["issuer"], BUSINESS)
        self.assertEqual(rows["testerp.pearnly.com"]["line"], {
            "webhook": {"endpoint": "https://x/api/method/pearnly_core.line.webhook", "active": True},
            "bot": {"chatMode": "bot", "markAsReadMode": "auto"}, "richmenu": False,
            "webhook_test": {"success": True, "statusCode": 200}})
        self.assertEqual(facts["sites"]["cowork"], {"error": "Error: No such container"})
        # 一律用频道号 + 密钥现取（证明验签密钥对，测试事件才不会被站点当成验签失败）；存的长期令牌不用
        self.assertEqual(sum(1 for m, url, _ in calls if url.endswith("/oauth2/v3/token")), 2)
        self.assertTrue(all(auth in ("", f"Bearer {FRESH_TOKEN}") for _, _, auth in calls))
        # 只读：除了取令牌与 LINE 的 webhook 测试，没有别的写请求
        self.assertEqual(sorted({urlsplit(url).path for method, url, _ in calls if method != "GET"}),
                         ["/oauth2/v3/token", "/v2/bot/channel/webhook/test"])
        self.assertNotIn("smtpServer", facts["keycloak"]["pearnly-business"]["realm"])
        self.assertEqual(facts["keycloak"]["pearnly-business"]["idps"][0]["config"], {"clientId": CHANNEL})

    def test_no_test_event_is_sent_when_line_refuses_the_channel_secret(self):
        # 密钥不对时站点验不过签、会记「验签失败」：这种站一个请求都不发给它的 webhook，按凭据不对拦
        import urllib.error
        calls = []
        line = fake_line(calls)

        def refusing(request, timeout=None):
            if request.full_url.endswith("/oauth2/v3/token"):
                calls.append((request.get_method(), request.full_url, ""))
                raise urllib.error.HTTPError(request.full_url, 400, "bad", {}, None)
            return line(request, timeout)

        with tempfile.TemporaryDirectory() as folder:
            sites = Path(folder) / "sites"
            (sites / "mrerp.pearnly.com").mkdir(parents=True)
            (sites / "mrerp.pearnly.com" / "site_config.json").write_text(json.dumps({
                "pearnly_line_channel_id": "1650000000", "pearnly_line_channel_secret": "wrong",
                "pearnly_line_channel_access_token": STORED_TOKEN}))
            out = io.StringIO()
            cwd = os.getcwd()
            try:
                os.chdir(folder)
                with mock.patch("urllib.request.urlopen", refusing), mock.patch.object(sys, "argv", ["-", "salt"]), \
                        contextlib.redirect_stdout(out):
                    exec(compile(col.SITE_DUMPER, "site-dumper", "exec"), {"__name__": "__main__"})
            finally:
                os.chdir(cwd)
        row = json.loads(out.getvalue().split("__SITES ", 1)[1])[0]
        self.assertEqual(row["line"]["kind"], "refused")
        self.assertEqual([urlsplit(url).path for _, url, _ in calls], ["/oauth2/v3/token"])
        facts = good_facts()
        site_row(facts, "mrerp.pearnly.com")["line"] = row["line"]
        self.assertTrue(any(b.startswith("[line:webhook] erp mrerp.pearnly.com：LINE 读不到") for b in run(facts).block))

    def test_missing_reader_secret_is_reported_not_guessed(self):
        facts = col.collect(run=lambda argv, **k: subprocess.CompletedProcess(argv, 1, "", "down"), keycloak=FakeKeycloak,
                            reader={})
        self.assertIn("no pearnly-config-reader secret", facts["keycloak"]["pearnly-business"]["error"])
        self.assertIn("error", facts["billing"])
        out = ext.compare(SRC, facts, EXT)
        self.assertTrue(any(b.startswith("[kc:pearnly-business:remember-me] Keycloak pearnly-business 读不到") for b in out.block))
        self.assertTrue(any(b.startswith("[ext:fatcc.com]") for b in out.block))

    def test_the_collector_runs_from_the_command_line_with_nothing_but_the_standard_library(self):
        # deploy.sh 经 SSH 传压缩后的源码进来执行：不能 import 仓库里的其它文件
        source = (HERE / "external-config-collect.py").read_text(encoding="utf-8")
        imported = set(re.findall(r"^(?:import|from) ([\w.]+)", source, re.M))
        self.assertTrue(imported <= {"__future__", "hashlib", "hmac", "json", "re", "secrets", "subprocess", "urllib.error",
                                     "urllib.parse", "urllib.request", "pathlib"}, imported)


if __name__ == "__main__":
    unittest.main()
