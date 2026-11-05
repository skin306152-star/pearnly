#!/usr/bin/env python3
"""部署前站外设置对比的生产机段（只读）：读 Keycloak 两个账号池、各站点的 LINE 频道、fatcc.com 的租户站点登记，只交回推导出来的事实。

为什么有它：11-03 三起事故的根因都在站点配置之外（Keycloak、LINE 后台、入口登记），部署前的配置对比看不到。方案与审定见
docs/work/2026-11-05-product-map-next.md §3；分类登记在 scripts/impact/product-map/config.toml 的 [ext] 各节；比较与拦不拦在本机
scripts/production/check-external-config.py 里做（期望值从开户 / 配置脚本导入，这里只读不判）。

在生产机上以部署操作员（root）身份运行：deploy.sh 的 config_gate 同一次 SSH，代码压缩后经命令行参数传进来，生产机上不落文件。
- Keycloak：只读客户端 pearnly-config-reader（只有 view-realm / view-clients / view-identity-providers），密钥在
  /opt/pearnly/secrets/config-reader.env；只发 GET，127.0.0.1:8180。
- 站点与 LINE：在各产品 web 容器里跑一段脚本，读站点配置、用站点自己的频道凭据（没存令牌就用频道号 + 密钥现取 15 分钟的短时令牌，
  同 site-step.py 的 stateless_token）只读 webhook 地址与开关、机器人模式、默认富菜单，再请 LINE 发一次 webhook 测试事件（只报告）。
  令牌一律用频道号 + 密钥现取：LINE 认这对凭据，测试事件的签名才一定能被站点验过（空事件列表，站点不回消息、不记日志，
  tests/test_core_line_token.py 的 WebhookTestEvent）；LINE 不认就不发测试事件、按凭据不对拦。凭据和令牌不出容器。
- 入口登记：在 billing-web 容器里只读 billing.internal 的 Pearnly Tenant Site 与 fatcc.com 站点配置（不进数据库容器）。
- 站点客户端密钥两端一致（K3）：容器里用这次随机生成的盐对站点配置里的密钥取 HMAC，生产机上与 Keycloak 里那份的 HMAC 比，
  只交回「一致 / 不一致」；摘要也不出生产机。

输出：一行 ``__EXT <JSON>``。没有密钥、令牌、密钥摘要。读不到的部分在对应位置写 ``{"error": …}``，由本机判拦不拦。
退出码总是 0（读到什么交什么；判定在本机）。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

MARK = "__EXT "
KEYCLOAK = "http://127.0.0.1:8180"
READER = Path("/opt/pearnly/secrets/config-reader.env")
READER_CLIENT = "pearnly-config-reader"
REALMS = ("pearnly-business", "pearnly-cowork")
PRODUCTS = ("erp", "cowork", "dms")
WEB = "pearnly-production-{}-web-1"
BILLING_WEB = "pearnly-production-billing-web-1"
BENCH = "/home/frappe/frappe-bench"
TIMEOUT = 20
# 站点客户端（clientId = 站点域名）；同 configure-ai-entry.py 的 SITE_CLIENT
SITE_CLIENT = re.compile(r"^[a-z0-9-]+\.pearnly\.com$")
# 只交回这些字段（白名单）：realm、客户端、身份提供者的其余内容（含密钥）一律不出生产机
REALM_FIELDS = (
    "realm", "enabled", "displayName", "sslRequired", "registrationAllowed", "loginWithEmailAllowed",
    "duplicateEmailsAllowed", "editUsernameAllowed", "resetPasswordAllowed", "rememberMe", "passwordPolicy",
    "loginTheme", "emailTheme", "internationalizationEnabled", "supportedLocales", "defaultLocale",
    "bruteForceProtected", "permanentLockout", "failureFactor", "waitIncrementSeconds", "maxFailureWaitSeconds",
    "ssoSessionIdleTimeout", "ssoSessionMaxLifespan", "ssoSessionIdleTimeoutRememberMe", "ssoSessionMaxLifespanRememberMe",
)
CLIENT_FIELDS = (
    "clientId", "enabled", "protocol", "publicClient", "bearerOnly", "standardFlowEnabled", "directAccessGrantsEnabled",
    "implicitFlowEnabled", "serviceAccountsEnabled", "frontchannelLogout", "redirectUris", "webOrigins", "baseUrl",
    "rootUrl", "fullScopeAllowed", "defaultClientScopes",
)
CLIENT_ATTRIBUTES = ("pkce.code.challenge.method", "post.logout.redirect.uris", "backchannel.logout.url",
                     "backchannel.logout.session.required", "login_theme")
IDP_FIELDS = ("alias", "providerId", "displayName", "enabled", "hideOnLogin", "trustEmail", "storeToken", "linkOnly",
              "addReadTokenRoleOnCreate", "firstBrokerLoginFlowAlias", "postBrokerLoginFlowAlias")
# 身份提供者 config 里交回的键：configure-line-login.idp_representation 的 config 去掉 clientSecret（测试里核两边一致）
IDP_CONFIG = ("authorizationUrl", "tokenUrl", "userInfoUrl", "issuer", "clientId", "clientAuthMethod", "defaultScope",
              "pkceEnabled", "pkceMethod", "syncMode", "validateSignature", "useJwksUrl", "backchannelSupported",
              "disableUserInfo")
# configure-line-login.verify_realm 核的三个凭据类必需动作
REQUIRED_ACTIONS = ("UPDATE_PASSWORD", "CONFIGURE_TOTP", "delete_credential")
DENY_FLOW = "pearnly-line-deny"

# 在各产品 web 容器里跑（容器自带 python3，只用标准库）：argv[1] = 这次的盐。只输出推导事实与密钥的 HMAC。
SITE_DUMPER = r'''
import hashlib, hmac, json, os, sys, urllib.error, urllib.parse, urllib.request

LINE = "https://api.line.me"
salt = sys.argv[1].encode()


def digest(value):
    return hmac.new(salt, str(value).encode(), hashlib.sha256).hexdigest() if value else None


def call(method, path, token, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(LINE + path, data=data, method=method,
                                     headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, None


def token_of(conf):
    # 总用频道号 + 密钥现取（存了长期令牌的站也一样）：LINE 收下就证明站点验签用的密钥是对的，
    # 之后请 LINE 发的 webhook 测试事件才一定验签通过、被站点安静收下（不会记「验签失败」）。
    form = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": str(conf["pearnly_line_channel_id"]),
                                   "client_secret": str(conf.get("pearnly_line_channel_secret") or "")}).encode()
    request = urllib.request.Request(LINE + "/oauth2/v3/token", data=form, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read())["access_token"], None
    except urllib.error.HTTPError as exc:
        return None, {"error": f"LINE refused the channel ID and secret (HTTP {exc.code}); webhook signatures cannot verify",
                      "kind": "refused"}


def line_facts(conf):
    try:
        token, failed = token_of(conf)
        if failed:
            return failed
        out = {}
        status, body = call("GET", "/v2/bot/channel/webhook/endpoint", token)
        if status in (401, 403):
            return {"error": f"LINE refused the channel token (HTTP {status})", "kind": "refused"}
        out["webhook"] = {"endpoint": body.get("endpoint"), "active": body.get("active")} if body is not None else {"error": f"HTTP {status}"}
        status, body = call("GET", "/v2/bot/info", token)
        out["bot"] = {"chatMode": body.get("chatMode"), "markAsReadMode": body.get("markAsReadMode")} if body is not None else {"error": f"HTTP {status}"}
        status, body = call("GET", "/v2/bot/user/all/richmenu", token)
        out["richmenu"] = True if status == 200 and (body or {}).get("richMenuId") else False if status == 404 else {"error": f"HTTP {status}"}
        status, body = call("POST", "/v2/bot/channel/webhook/test", token, {})
        out["webhook_test"] = {"success": body.get("success"), "statusCode": body.get("statusCode")} if body is not None else {"error": f"HTTP {status}"}
        return out
    except Exception as exc:  # 连不上 LINE：只说类型，不带任何请求内容
        return {"error": type(exc).__name__, "kind": "unreachable"}


def load(path):
    with open(path) as handle:
        return json.load(handle)


try:
    common = load("sites/common_site_config.json")
except Exception:
    common = {}
out = []
for site in sorted(os.listdir("sites")):
    path = os.path.join("sites", site, "site_config.json")
    if not os.path.isfile(path):
        continue
    try:
        conf = dict(common, **load(path))
    except Exception as exc:
        out.append({"site": site, "error": type(exc).__name__})
        continue
    row = {"site": site, "issuer": str(conf.get("pearnly_oidc_issuer") or "").rstrip("/") or None,
           "client_id": conf.get("pearnly_oidc_client_id"), "redirect_uri": conf.get("pearnly_oidc_redirect_uri"),
           "secret_digest": digest(conf.get("pearnly_oidc_client_secret")),
           "line_login_idp": conf.get("pearnly_line_login_idp") or None, "liff_id": conf.get("pearnly_line_liff_id") or None,
           "line": line_facts(conf) if conf.get("pearnly_line_channel_id") else None}
    out.append(row)
print("__SITES " + json.dumps(out))
'''

# 在 billing-web 容器里跑（带 Frappe 的 python）：入口登记与 fatcc.com 入口站点配置，密钥只给有没有。
BILLING_DUMPER = r'''
import json, os
os.chdir("/home/frappe/frappe-bench/sites")
out = {}


def load(path):
    with open(path) as handle:
        return json.load(handle)


try:
    common = load("common_site_config.json")
except Exception:
    common = {}
try:
    conf = dict(common, **load("fatcc.com/site_config.json"))
    out["entry"] = {"entry": conf.get("pearnly_assistant_entry"), "url": conf.get("pearnly_assistant_entry_url"),
                    "default_issuer": conf.get("pearnly_assistant_entry_default_issuer"),
                    "billing_host": conf.get("pearnly_assistant_entry_billing_host"),
                    "token": bool(conf.get("pearnly_assistant_entry_token")),
                    "webhook_secret": bool(conf.get("pearnly_assistant_webhook_secret"))}
except Exception as exc:
    out["entry"] = {"error": type(exc).__name__}
try:
    import frappe
    frappe.init(site="billing.internal", sites_path=".")
    frappe.connect()
    try:
        out["sites"] = frappe.get_all("Pearnly Tenant Site", fields=["site", "product", "issuer"],
                                      limit_page_length=5000)  # bounded: 每个生产部署一条（11-05 共 10 条）
    finally:
        frappe.destroy()
except Exception as exc:
    out["sites"] = {"error": type(exc).__name__}
print("__BILLING " + json.dumps(out, default=str))
'''


def _pick(row: dict, fields) -> dict:
    return {key: row.get(key) for key in fields if key in row}


class Keycloak:
    """只读客户端的令牌 + GET。读不到抛 RuntimeError（消息里没有密钥与令牌）。"""

    def __init__(self, realm: str, secret: str, opener=urllib.request.urlopen):
        self.realm, self.opener = realm, opener
        form = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": READER_CLIENT,
                                       "client_secret": secret}).encode()
        answer = self._send(urllib.request.Request(f"{KEYCLOAK}/realms/{realm}/protocol/openid-connect/token", data=form,
                                                   method="POST",
                                                   headers={"Content-Type": "application/x-www-form-urlencoded"}),
                            "token")
        self.token = answer["access_token"]

    def _send(self, request, what):
        try:
            with self.opener(request, timeout=TIMEOUT) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Keycloak {self.realm} {what}: HTTP {exc.code}") from None
        except (urllib.error.URLError, OSError) as exc:
            raise RuntimeError(f"Keycloak {self.realm} {what}: {type(exc).__name__}") from None

    def get(self, path: str):
        request = urllib.request.Request(f"{KEYCLOAK}/admin/realms/{self.realm}{path}",
                                         headers={"Authorization": "Bearer " + self.token})
        return self._send(request, path.split("?")[0] or "/")


def read_reader(path: Path = READER) -> dict:
    if not path.is_file():
        return {}
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)


def read_realm(realm: str, reader: dict, salt: bytes, digests: dict, keycloak=Keycloak) -> dict:
    """一个账号池的事实；站点客户端密钥的 HMAC 记进 digests（只留在生产机内存里）。"""
    secret = reader.get("PEARNLY_CONFIG_READER_SECRET_" + realm.upper().replace("-", "_"))
    if not secret:
        return {"error": f"no {READER_CLIENT} secret for {realm} in {READER}"}
    try:
        api = keycloak(realm, secret)
        facts = {"realm": _pick(api.get("") or {}, REALM_FIELDS)}
        clients = []
        for row in api.get("/clients?max=1000") or []:
            client = _pick(row, CLIENT_FIELDS)
            client["attributes"] = _pick(row.get("attributes") or {}, CLIENT_ATTRIBUTES)
            clients.append(client)
            if SITE_CLIENT.match(str(row.get("clientId") or "")) and not row.get("publicClient"):
                try:
                    value = (api.get(f"/clients/{row['id']}/client-secret") or {}).get("value")
                except RuntimeError:
                    value = None
                if value:
                    digests[(realm, row["clientId"])] = hmac.new(salt, value.encode(), hashlib.sha256).hexdigest()
        facts["clients"] = clients
        idps = []
        for row in api.get("/identity-provider/instances") or []:
            idp = _pick(row, IDP_FIELDS)
            idp["config"] = _pick(row.get("config") or {}, IDP_CONFIG)
            idps.append(idp)
        facts["idps"] = idps
        facts["default_scopes"] = sorted(s.get("name") for s in api.get("/default-default-client-scopes") or [])
        facts["client_scopes"] = sorted(s.get("name") for s in api.get("/client-scopes") or [])
        actions = {}
        for alias in REQUIRED_ACTIONS:
            try:
                actions[alias] = ((api.get(f"/authentication/required-actions/{alias}/config") or {}).get("config")
                                  or {}).get("max_auth_age")
            except RuntimeError:
                actions[alias] = None
        facts["required_actions"] = actions
        try:
            facts["deny_flow"] = [[e.get("providerId"), e.get("requirement")]
                                  for e in api.get(f"/authentication/flows/{DENY_FLOW}/executions") or []]
        except RuntimeError:
            facts["deny_flow"] = None
        return facts
    except RuntimeError as exc:
        return {"error": str(exc)}


def read_sites(product: str, salt: str, run=subprocess.run):
    done = run(["docker", "exec", "-i", "-w", BENCH, WEB.format(product), "python3", "-", salt], input=SITE_DUMPER,
               capture_output=True, text=True, timeout=300)
    line = next((l for l in (done.stdout or "").splitlines() if l.startswith("__SITES ")), "")
    if done.returncode != 0 or not line:
        return {"error": ((done.stderr or "").strip().splitlines() or [f"exit {done.returncode}"])[-1][-200:]}
    return json.loads(line[len("__SITES "):])


def read_billing(run=subprocess.run) -> dict:
    done = run(["docker", "exec", "-i", "-w", BENCH + "/sites", BILLING_WEB, BENCH + "/env/bin/python", "-"],
               input=BILLING_DUMPER, capture_output=True, text=True, timeout=120)
    line = next((l for l in (done.stdout or "").splitlines() if l.startswith("__BILLING ")), "")
    if done.returncode != 0 or not line:
        return {"error": ((done.stderr or "").strip().splitlines() or [f"exit {done.returncode}"])[-1][-200:]}
    return json.loads(line[len("__BILLING "):])


def realm_of(issuer) -> str:
    prefix = "https://auth.pearnly.com/realms/"
    issuer = str(issuer or "")
    return issuer[len(prefix):] if issuer.startswith(prefix) else ""


def settle_secrets(sites: dict, digests: dict, realms: dict) -> None:
    """K3：每个站点的密钥 HMAC 与它那个池里 Keycloak 的比，换成 secret_match（True / False / None = 没比成），摘要去掉。"""
    for rows in sites.values():
        if not isinstance(rows, list):
            continue
        for row in rows:
            mine = row.pop("secret_digest", None)
            realm = realm_of(row.get("issuer"))
            theirs = digests.get((realm, row.get("client_id")))
            if not mine or not theirs or "error" in (realms.get(realm) or {"error": 1}):
                row["secret_match"] = None
            else:
                row["secret_match"] = hmac.compare_digest(mine, theirs)


def collect(run=subprocess.run, keycloak=Keycloak, reader=None) -> dict:
    salt = secrets.token_hex(16)
    reader = read_reader() if reader is None else reader
    digests: dict = {}
    realms = {realm: read_realm(realm, reader, salt.encode(), digests, keycloak) for realm in REALMS}
    sites = {}
    for product in PRODUCTS:
        try:
            sites[product] = read_sites(product, salt, run)
        except (subprocess.SubprocessError, ValueError, OSError) as exc:
            sites[product] = {"error": type(exc).__name__}
    try:
        billing = read_billing(run)
    except (subprocess.SubprocessError, ValueError, OSError) as exc:
        billing = {"error": type(exc).__name__}
    settle_secrets(sites, digests, realms)
    return {"version": 1, "keycloak": realms, "sites": sites, "billing": billing}


def main() -> int:
    print(MARK + json.dumps(collect(), ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    main()
