#!/usr/bin/env python3
"""LINE 绑定即登录：业务 realm 的 Keycloak 配置（幂等；只加 LINE 这一类外部登录入口）。

两处调用同一份代码：

- 运维在生产主机上跑本脚本 → `configure_realm`：首次登录拒绝流程、登录来源作用域、
  改密前必须重新验证。**不动**登录页、注册、密码策略等既有设置。
- 开通步骤 `set_line`（`site-step.py`，渠道带 LINE Login 密钥时）→ `configure_site`：
  建/更新该 LINE Login 频道共用的隐藏 LINE 入口（别名 `line-<频道号>`）。入口上的关联只挂在
  控制面建的 LINE 专用身份上（每个 LINE 一个），站点按自己的有效绑定认人（2026-10-04）。

密钥只经标准输入或开通载荷进来，不进命令行、不打印。API 适配器可注入，供隔离的真实
Keycloak 实验复用（见 docs/work/2026-09-25-erp-line-intake.md 第 7 节）。
"""
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

REALM = "pearnly-business"
# 产品组 → 账号池（Cowork 用自己的 realm，用户 2026-09-26 与 ERP / DMS 分开）。
REALMS = {"erp": REALM, "dms": REALM, "cowork": "pearnly-cowork"}
DENY_FLOW = "pearnly-line-deny"
SCOPE = "pearnly-login-source"
# 会话备注 → ID token 声明（与 pearnly_core.account.line_login_contract 一致）。
SCOPE_CLAIMS = (("identity_provider", "pearnly_idp"), ("sub", "pearnly_line_user_id"))
# 改密等**凭据类**动作：离上次认证超过 1 秒就要求重新输入当前密码（LINE 登录后不能直接改密、
# 加两步验证或关联别的外部账号）。资料、语言、邮箱验证等非凭据动作保持 Keycloak 默认，不打扰。
MAX_AUTH_AGE = "1"
SENSITIVE_ACTIONS = ("UPDATE_PASSWORD", "UPDATE_EMAIL", "CONFIGURE_TOTP", "CONFIGURE_RECOVERY_AUTHN_CODES",
                     "webauthn-register", "webauthn-register-passwordless", "delete_credential", "idp_link")
LINE = {
    "authorizationUrl": "https://access.line.me/oauth2/v2.1/authorize",
    "tokenUrl": "https://api.line.me/oauth2/v2.1/token",
    "userInfoUrl": "https://api.line.me/oauth2/v2.1/userinfo",
    "issuer": "https://access.line.me",
}


class Keycloak:
    def __init__(self, base, realm, admin_user, admin_password):
        self.base, self.realm = base.rstrip("/"), realm
        self.token = None
        self.token = self.request("/realms/master/protocol/openid-connect/token", "POST", {
            "grant_type": "password", "client_id": "admin-cli", "username": admin_user,
            "password": admin_password,
        }, form=True)["access_token"]

    def request(self, path, method="GET", data=None, form=False, ok=(200, 201, 204)):
        headers = {"Content-Type": "application/x-www-form-urlencoded" if form else "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        body = (urlencode(data).encode() if form else json.dumps(data).encode()) if data is not None else None
        try:
            with urlopen(Request(self.base + path, data=body, headers=headers, method=method), timeout=30) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except HTTPError as exc:
            if exc.code in ok:
                return None
            raise RuntimeError(f"Keycloak {method} {path.split('?')[0]} failed: HTTP {exc.code}") from None

    def exists(self, path):
        try:
            self.request(path)
            return True
        except RuntimeError as error:
            if "HTTP 404" in str(error):
                return False
            raise


def _prefix(api):
    return f"/admin/realms/{api.realm}"


def configure_realm(api):
    prefix = _prefix(api)
    # 1. 首次登录流程：未关联的 LINE 一律拒绝（不建号、不按邮箱合并）。
    if not api.exists(f"{prefix}/authentication/flows/{DENY_FLOW}/executions"):
        api.request(f"{prefix}/authentication/flows", "POST", {
            "alias": DENY_FLOW, "providerId": "basic-flow", "topLevel": True, "builtIn": False,
            "description": "LINE sign-in only for identities linked by Pearnly; never create or link here"})
    executions = api.request(f"{prefix}/authentication/flows/{DENY_FLOW}/executions") or []
    if not any(e.get("providerId") == "deny-access-authenticator" for e in executions):
        api.request(f"{prefix}/authentication/flows/{DENY_FLOW}/executions/execution", "POST",
                    {"provider": "deny-access-authenticator"})
        executions = api.request(f"{prefix}/authentication/flows/{DENY_FLOW}/executions") or []
    if [e.get("providerId") for e in executions] != ["deny-access-authenticator"]:
        raise RuntimeError("LINE first-login flow must contain only deny-access")
    deny = executions[0]
    if deny.get("requirement") != "REQUIRED":
        deny["requirement"] = "REQUIRED"
        api.request(f"{prefix}/authentication/flows/{DENY_FLOW}/executions", "PUT", deny)

    # 2. 登录来源作用域：所有客户端的 ID token 都带上 pearnly_idp / pearnly_line_user_id。
    scopes = api.request(f"{prefix}/client-scopes") or []
    scope = next((s for s in scopes if s.get("name") == SCOPE), None)
    if not scope:
        api.request(f"{prefix}/client-scopes", "POST", {
            "name": SCOPE, "protocol": "openid-connect",
            "description": "Pearnly: which external sign-in (LINE) created this session",
            "attributes": {"include.in.token.scope": "false", "display.on.consent.screen": "false"}})
        scope = next(s for s in api.request(f"{prefix}/client-scopes") if s.get("name") == SCOPE)
    sid = scope["id"]
    mappers = {m["name"]: m for m in api.request(f"{prefix}/client-scopes/{sid}/protocol-mappers/models") or []}
    for note, claim in SCOPE_CLAIMS:
        body = {"name": claim, "protocol": "openid-connect", "protocolMapper": "oidc-usersessionmodel-note-mapper",
                "config": {"user.session.note": note, "claim.name": claim, "jsonType.label": "String",
                           "id.token.claim": "true", "access.token.claim": "false",
                           "userinfo.token.claim": "false", "introspection.token.claim": "false"}}
        if claim in mappers:
            api.request(f"{prefix}/client-scopes/{sid}/protocol-mappers/models/{mappers[claim]['id']}", "PUT",
                        {**body, "id": mappers[claim]["id"]})
        else:
            api.request(f"{prefix}/client-scopes/{sid}/protocol-mappers/models", "POST", body)
    already = (200, 201, 204, 409)  # 409 = 已经是默认作用域
    api.request(f"{prefix}/default-default-client-scopes/{sid}", "PUT", ok=already)
    for client in api.request(f"{prefix}/clients?max=1000") or []:
        if client.get("protocol", "openid-connect") == "openid-connect" and not client.get("bearerOnly"):
            api.request(f"{prefix}/clients/{client['id']}/default-client-scopes/{sid}", "PUT", ok=already)

    # 3. 凭据类必需动作：LINE 登录（或任何登录）之后不能不输密码就改密、加凭据。
    for action in api.request(f"{prefix}/authentication/required-actions") or []:
        alias = action["alias"]
        if alias not in SENSITIVE_ACTIONS:
            continue
        described = api.request(f"{prefix}/authentication/required-actions/{quote(alias)}/config-description",
                                ok=(200, 400, 404)) or {}
        if any(p.get("name") == "max_auth_age" for p in described.get("properties", [])):
            path = f"{prefix}/authentication/required-actions/{quote(alias)}/config"
            # 整份提交：只改 max_auth_age，其余配置原样保留（缺字段会被拒）。
            current = (api.request(path, ok=(200, 400, 404)) or {}).get("config") or {}
            for prop in described.get("properties", []):
                if prop.get("name") not in current and prop.get("defaultValue") not in (None, ""):
                    current[prop["name"]] = str(prop["defaultValue"])
            api.request(path, "PUT", {"config": {**current, "max_auth_age": MAX_AUTH_AGE}})
    return verify_realm(api)


def verify_realm(api):
    """回读：外部身份提供者只能是隐藏的、拒绝首次登录的 `line-*`；作用域是默认作用域。"""
    prefix = _prefix(api)
    for idp in api.request(f"{prefix}/identity-provider/instances") or []:
        if (not idp["alias"].startswith("line-") or not idp.get("hideOnLogin")
                or idp.get("firstBrokerLoginFlowAlias") != DENY_FLOW or idp.get("postBrokerLoginFlowAlias")
                or idp.get("linkOnly") or idp.get("trustEmail")):
            raise RuntimeError(f"Unexpected identity provider: {idp['alias']}")
    defaults = {s["name"] for s in api.request(f"{prefix}/default-default-client-scopes") or []}
    if SCOPE not in defaults:
        raise RuntimeError("Login source scope is not a realm default")
    for alias in ("UPDATE_PASSWORD", "CONFIGURE_TOTP", "delete_credential"):
        current = api.request(f"{prefix}/authentication/required-actions/{alias}/config", ok=(200, 400, 404)) or {}
        if (current.get("config") or {}).get("max_auth_age") != MAX_AUTH_AGE:
            raise RuntimeError(f"{alias} re-authentication age failed readback")
    return True


def configure_site(api, *, alias, login_channel_id, login_channel_secret, endpoints=LINE,
                   line_user_pattern="U[0-9a-f]{32}"):
    """该站点隐藏的 LINE 入口（OIDC）。LINE Login 频道 ID 即 LIFF ID 的前缀。"""
    if not alias.startswith("line-") or not str(login_channel_id).isdigit() or not login_channel_secret:
        raise RuntimeError("Invalid LINE login configuration")
    prefix = _prefix(api)
    body = {
        "alias": alias, "providerId": "oidc", "displayName": "LINE", "enabled": True,
        "hideOnLogin": True, "trustEmail": False, "storeToken": False, "linkOnly": False,
        "addReadTokenRoleOnCreate": False, "firstBrokerLoginFlowAlias": DENY_FLOW,
        "postBrokerLoginFlowAlias": None,
        "config": {
            **endpoints, "clientId": str(login_channel_id), "clientSecret": login_channel_secret,
            "clientAuthMethod": "client_secret_post", "defaultScope": "openid profile",
            "pkceEnabled": "true", "pkceMethod": "S256", "syncMode": "IMPORT",
            # LINE 网页登录的 ID token 用频道密钥 HS256 签名；它经后端 TLS 直接从 LINE 取得。
            "validateSignature": "false", "useJwksUrl": "false", "backchannelSupported": "false",
            "disableUserInfo": "true",
        },
    }
    path = f"{prefix}/identity-provider/instances/{quote(alias)}"
    if api.exists(path):
        api.request(path, "PUT", body)
    else:
        api.request(f"{prefix}/identity-provider/instances", "POST", body)
    mappers = {m["name"]: m for m in api.request(path + "/mappers") or []}
    mapper = {"name": "line-user-id", "identityProviderAlias": alias,
              "identityProviderMapper": "oidc-user-session-note-idp-mapper",
              "config": {"claims": json.dumps([{"key": "sub", "value": line_user_pattern}]),
                         "are.claim.values.regex": "true", "syncMode": "FORCE"}}
    if "line-user-id" in mappers:
        api.request(path + f"/mappers/{mappers['line-user-id']['id']}", "PUT",
                    {**mapper, "id": mappers["line-user-id"]["id"]})
    else:
        api.request(path + "/mappers", "POST", mapper)
    verify_realm(api)
    return {"alias": alias, "callback": f"/realms/{api.realm}/broker/{alias}/endpoint"}


def main():
    if os.geteuid() != 0:
        raise SystemExit("Run on the production host as the deployment operator")
    env = dict(line.split("=", 1) for line in
               Path("/opt/pearnly/secrets/runtime.env").read_text().splitlines() if "=" in line)
    # `--realm pearnly-cowork`：给 Cowork 的账号池做同样的 realm 级设置（默认仍是业务 realm）。
    realm = sys.argv[sys.argv.index("--realm") + 1] if "--realm" in sys.argv else REALM
    if realm not in set(REALMS.values()):
        raise SystemExit("Unknown realm")
    api = Keycloak("http://127.0.0.1:8180", realm, "pearnly-bootstrap", env["KEYCLOAK_ADMIN_PASSWORD"])
    configure_realm(api)
    print("LINE sign-in realm settings verified: deny-unlinked first login, login-source scope, re-auth age")


if __name__ == "__main__":
    main()
