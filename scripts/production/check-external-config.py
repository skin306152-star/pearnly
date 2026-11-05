#!/usr/bin/env python3
"""部署前站外设置对比（只读）：Keycloak、LINE、fatcc.com 入口登记、Cloudflare 解析、证书，按产品地图 [ext] 的分类判拦不拦。

为什么有它（11-03 三起事故，方案 docs/work/2026-11-05-product-map-next.md §3）：LIFF 落在子路径、切换账号落到登录页、
添加公司跳出去——根因都在站点配置之外，部署前的租户配置对比（check-tenant-config.py）看不到。

分两段读，判定都在这里：
- 生产机段：external-config-collect.py，与租户配置对比同一次 SSH（deploy.sh 的 config_gate），交回一份只有推导事实的 JSON；
- 本机段：Cloudflare 解析（令牌只发 GET）、证书剩余天数、LINE 登录频道是否登记了 Keycloak 的接收地址（公开请求，不要凭据）。

「应有」不另写一份，直接导入开户 / 配置脚本里的定义：bootstrap-auth.realm_settings / READBACK_KEYS（K1）、
configure-ai-entry.readback_failures / logout_targets（K2、K4）、configure-line-login.verify_realm / idp_representation（K5）、
site-step.webhook_url（L1）、line_login_contract.login_channel_of / idp_alias（K5、L5）、e2e run.where_is（C1）。

拦与不拦（同 11-04 审定口径，没有绕过开关）：
- 地图里 class 是 same / shape 的项不对 → 拦部署；presence → 只报告；
- 例外：缺入口登记（F1，那家进不了 CC）、站点客户端密钥两端不一致（K3，那家登录全挂）、该有的东西读不到（Keycloak、入口登记）→ 拦；
  LINE 一时连不上、测试租户切在测试机 → 只报告。
- 没有读取接口的（LINE redelivery 开关、Messaging 频道与登录频道的关联、LIFF 的尺寸 / 作用域 / 加好友提示——LINE Login 频道密钥
  只在控制面）每次列在「读不到」里，留在开户清单与真机单。

用法：
  deploy.sh 里：python3 check-external-config.py --facts <生产机交回的 JSON> --map <产品地图导出>
  离线：再加 --records <Cloudflare 记录 JSON> --certs <{主机: 剩余天数} JSON> --no-network（L5 只报告没测）
退出码：0 没有要拦的；1 有要拦的；2 用法不对或输入读不出。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
AUTH = "auth.pearnly.com"
ENTRY = "fatcc.com"
ISSUER_BASE = f"https://{AUTH}/realms/"
CALLBACK = "/api/method/pearnly_core.identity.callback"
BACKCHANNEL = "/api/method/pearnly_core.identity.backchannel_logout"
TLS_BLOCK_DAYS, TLS_NOTE_DAYS = 7, 21
LINE_AUTHORIZE = "https://access.line.me/oauth2/v2.1/authorize"
UNREADABLE = (
    "LINE 的 redelivery 开关：LINE 没有读取接口（开户清单 / 三星真机单）",
    "Messaging 频道与 LINE Login 频道的关联（加好友）：LINE 没有读取接口（真机单）",
    "LIFF 尺寸、作用域、加好友提示：要 LINE Login 频道密钥，只在控制面；落地地址由上一步「LIFF 落地地址」核（liff:endpoint）",
)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # e2e run.py 的 dataclass 要能从 sys.modules 找到自己的模块
    spec.loader.exec_module(module)
    return module


class Sources:
    """开户 / 配置脚本里的「应有」（按需导入，导入不连网络、不读密钥）。"""

    def __init__(self, root: Path = ROOT):
        production = root / "infrastructure" / "production"
        self.bootstrap = _load("bootstrap_auth", production / "bootstrap-auth.py")
        self.ai_entry = _load("configure_ai_entry", production / "configure-ai-entry.py")
        self.site_step = _load("site_step", production / "site-step.py")
        self.line_login = _load("configure_line_login", root / "scripts" / "production" / "configure-line-login.py")
        self.contract = _load("line_login_contract", root / "apps" / "pearnly_core" / "pearnly_core" / "account" /
                              "line_login_contract.py")
        self.tenant = _load("check_tenant_config", root / "scripts" / "production" / "check-tenant-config.py")
        self.collect = _load("external_config_collect", root / "scripts" / "production" / "external-config-collect.py")
        self.e2e = _load("e2e_run", root / "scripts" / "e2e" / "run.py")


class Findings:
    """按 [ext] 分类归成拦 / 只报告；force 覆盖分类（见文件头的例外）。"""

    def __init__(self, ext: dict):
        self.ext, self.block, self.note, self.info = ext, [], [], []

    def add(self, key: str, text: str, force: str | None = None) -> None:
        kind = force or ("block" if (self.ext.get(key) or {}).get("class") in ("same", "shape") else "note")
        (self.block if kind == "block" else self.note).append(f"[{key}] {text}")


# ---------------------------------------------------------------- Keycloak（K1–K5）

def _norm(value):
    if isinstance(value, list):
        return sorted(value, key=str)
    return "" if value is None else value


class Replay:
    """把生产机交回的一个账号池的事实，回放成 configure-line-login.verify_realm 要的 api.request。"""

    def __init__(self, realm: str, facts: dict):
        self.realm, prefix = realm, f"/admin/realms/{realm}"
        self.answers = {
            prefix + "/identity-provider/instances": facts.get("idps") or [],
            prefix + "/default-default-client-scopes": [{"name": n} for n in facts.get("default_scopes") or []],
        }
        for alias, age in (facts.get("required_actions") or {}).items():
            self.answers[prefix + f"/authentication/required-actions/{alias}/config"] = (
                {"config": {"max_auth_age": age}} if age is not None else {})

    def request(self, path, method="GET", data=None, form=False, ok=(200, 201, 204)):
        if method != "GET" or path not in self.answers:
            raise RuntimeError(f"not read: {method} {path}")
        return self.answers[path]


def check_realms(src: Sources, facts: dict, out: Findings) -> None:
    realms = facts.get("keycloak") or {}
    readable = {}
    for realm in src.collect.REALMS:
        key = f"kc:{realm}:remember-me"
        data = realms.get(realm) or {"error": "not collected"}
        if "error" in data:
            out.add(key, f"Keycloak {realm} 读不到（{data['error']}）", "block")
            continue
        readable[realm] = data
        have = data.get("realm") or {}
        want = src.bootstrap.realm_settings(realm)
        for name in sorted(set(want) - {"displayName"}):
            if _norm(have.get(name)) != _norm(want[name]):
                out.add(key, f"{realm} {name} 是 {json.dumps(have.get(name), ensure_ascii=False)}，"
                             f"应为 {json.dumps(want[name], ensure_ascii=False)}（bootstrap-auth.realm_settings）")
    if len(readable) == 2:
        (a, ra), (b, rb) = sorted(readable.items())
        for name in src.collect.REALM_FIELDS:
            if name in ("realm", "displayName"):
                continue
            x, y = (ra.get("realm") or {}).get(name), (rb.get("realm") or {}).get(name)
            if _norm(x) != _norm(y):
                out.add(f"kc:{b}:remember-me", f"两个池的 {name} 不一致：{a} {json.dumps(x)}，{b} {json.dumps(y)}（应一致）")
    for realm, data in readable.items():
        clients = {c.get("clientId"): c for c in data.get("clients") or []}
        ai = clients.get(src.ai_entry.CLIENT_ID)
        if not ai:
            out.add("kc:pearnly-ai", f"{realm} 里没有 {src.ai_entry.CLIENT_ID} 客户端", "block")
        else:
            failed = src.ai_entry.readback_failures(ai, src.ai_entry.PRODUCTION_ORIGINS)
            if failed:
                out.add("kc:pearnly-ai", f"{realm} {src.ai_entry.CLIENT_ID}：{', '.join(failed)} 不对"
                                         "（configure-ai-entry.readback_failures）")
        try:
            src.line_login.verify_realm(Replay(realm, data))
        except RuntimeError as error:
            out.add("kc:line-idp", f"{realm} LINE 登录的池级设置不对：{error}（configure-line-login.verify_realm）")
        if data.get("deny_flow") != [["deny-access-authenticator", "REQUIRED"]]:
            out.add("kc:line-idp", f"{realm} 首次登录流程 {src.line_login.DENY_FLOW} 应只有一步 deny-access 且必需，"
                                   f"读到 {json.dumps(data.get('deny_flow'))}")
        if src.line_login.SCOPE not in (data.get("client_scopes") or []):
            out.add("kc:line-idp", f"{realm} 没有作用域 {src.line_login.SCOPE}")


def _client_shape(client: dict, site: str, src: Sources) -> list[str]:
    """站点客户端的形状（开户 provisioning._ensure_oidc_client 建的样子，退出跳转用 configure-ai-entry.logout_targets）。"""
    base = f"https://{site}"
    attributes = client.get("attributes") or {}
    logout = [u for u in str(attributes.get("post.logout.redirect.uris") or "").split("##") if u]
    wrong = []
    if client.get("enabled") is not True:
        wrong.append("没启用")
    if client.get("publicClient") is not False or client.get("standardFlowEnabled") is not True:
        wrong.append("不是只开授权码的机密客户端")
    if client.get("directAccessGrantsEnabled") is not False:
        wrong.append("开着密码直传")
    if base + CALLBACK not in (client.get("redirectUris") or []):
        wrong.append(f"回调里没有 {base}{CALLBACK}")
    if base not in (client.get("webOrigins") or []):
        wrong.append(f"webOrigins 没有 {base}")
    missing = [u for u in [base + "/"] + src.ai_entry.logout_targets(site) if u not in logout]
    if missing:
        wrong.append(f"退出跳转缺 {'、'.join(missing)}")
    if attributes.get("backchannel.logout.url") != base + BACKCHANNEL:
        wrong.append("后台退出地址不是本站")
    if attributes.get("pkce.code.challenge.method") != "S256":
        wrong.append("PKCE 不是 S256")
    return wrong


def _client_structure(client: dict, site: str) -> str:
    """把站点名换成 {site}、去掉测试入口 testai 那条退出跳转后的结构（正式与同产品测试租户比）。"""
    client = json.loads(json.dumps(client).replace(site, "{site}"))   # 先换名再排序，排序结果才与站点名无关
    attributes = dict(client.get("attributes") or {})
    logout = [u for u in str(attributes.get("post.logout.redirect.uris") or "").split("##")
              if u and "testai.pearnly.com" not in u]
    attributes["post.logout.redirect.uris"] = sorted(logout)
    shaped = {k: _norm(v) for k, v in {**client, "attributes": None}.items() if k != "attributes"}
    shaped["attributes"] = {k: _norm(v) for k, v in sorted(attributes.items())}
    return json.dumps(shaped, sort_keys=True, ensure_ascii=False)


def _site_rows(facts: dict, out: Findings) -> dict:
    """{产品: [站点行]}；产品容器读不到要拦（站外设置没法逐站核）。"""
    rows = {}
    for product, data in (facts.get("sites") or {}).items():
        if isinstance(data, dict):
            out.add("kc:site-client", f"{product} 的站点读不到（{data.get('error')}）", "block")
            continue
        for row in data:
            if "error" in row:
                out.add("kc:site-client", f"{product} {row['site']} 站点配置读不出（{row['error']}）", "block")
        rows[product] = [r for r in data if "error" not in r]
    return rows


def realm_of(issuer) -> str:
    issuer = str(issuer or "")
    return issuer[len(ISSUER_BASE):] if issuer.startswith(ISSUER_BASE) else ""


def check_site_clients(src: Sources, facts: dict, rows: dict, out: Findings) -> None:
    realms = facts.get("keycloak") or {}
    used = set()
    for product, sites in rows.items():
        structures = {}
        for row in sites:
            site, realm = row["site"], realm_of(row.get("issuer"))
            data = realms.get(realm)
            if not realm or data is None:
                out.add("kc:site-client", f"{product} {site} 的签发方 {row.get('issuer')} 不是两个账号池之一", "block")
                continue
            if "error" in data:
                continue  # 账号池读不到已在 K1 拦过
            if row.get("client_id") != site or row.get("redirect_uri") != f"https://{site}{CALLBACK}":
                out.add("kc:site-client", f"{product} {site} 站点配置里的客户端 / 回调不是本站", "block")
            client = next((c for c in data.get("clients") or [] if c.get("clientId") == site), None)
            if not client:
                out.add("kc:site-client", f"{product} {site}：{realm} 池里没有这个站点的客户端（登录不了）", "block")
                continue
            used.add((realm, site))
            wrong = _client_shape(client, site, src)
            if wrong:
                out.add("kc:site-client", f"{product} {site} 客户端：{'；'.join(wrong)}", "block")
            structures[site] = _client_structure(client, site)
            match = row.get("secret_match")
            if match is False:
                out.add("kc:site-client-secret", f"{product} {site}：Keycloak 里的客户端密钥与站点配置里的不一致（那家登录全挂）",
                        "block")
            elif match is None:
                out.add("kc:site-client-secret", f"{product} {site}：客户端密钥两端没比成（有一端读不到）", "note")
        tests = [r for r in sites if src.tenant.is_test(r["site"]) and r["site"] in structures]
        reference = src.tenant.reference_of(product, tests)
        if reference:
            want = structures[reference["site"]]
            for site, have in sorted(structures.items()):
                if not src.tenant.is_test(site) and have != want:
                    out.add("kc:site-client", f"{product} {site} 客户端结构与测试租户 {reference['site']} 不同（站点名换成 {{site}}、"
                                              f"去掉 testai 后应一致）：{_diff(have, want)}")
    for realm, data in realms.items():
        for client in (data or {}).get("clients") or []:
            cid = str(client.get("clientId") or "")
            if src.collect.SITE_CLIENT.match(cid) and (realm, cid) not in used:
                out.add("kc:site-client", f"{realm} 池里有不对应任何站点的客户端 {cid}（只报告，不动它）", "note")


def _diff(have: str, want: str) -> str:
    a, b = json.loads(have), json.loads(want)
    keys = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
    if "attributes" in keys:
        keys.remove("attributes")
        keys += [f"attributes.{k}" for k in sorted(set(a["attributes"]) | set(b["attributes"]))
                 if a["attributes"].get(k) != b["attributes"].get(k)]
    return "、".join(keys) or "（无）"


def check_line_idps(src: Sources, facts: dict, rows: dict, out: Findings) -> list:
    """K5：站点配置指的 LINE 身份提供者在它的池里、设置照 idp_representation。返回 L5 要核的 (池, 别名, 频道)。"""
    realms = facts.get("keycloak") or {}
    callbacks = set()
    for product, sites in rows.items():
        for row in sites:
            alias, realm = row.get("line_login_idp"), realm_of(row.get("issuer"))
            if not alias:
                continue
            data = realms.get(realm) or {}
            if "error" in data or not data:
                continue
            channel = src.contract.login_channel_of(row.get("liff_id"))
            if not channel or alias != src.contract.idp_alias(channel):
                out.add("kc:line-idp", f"{product} {row['site']}：LINE 入口 {alias} 与 LIFF {row.get('liff_id')} 的登录频道对不上")
                continue
            idp = next((i for i in data.get("idps") or [] if i.get("alias") == alias), None)
            if not idp:
                out.add("kc:line-idp", f"{product} {row['site']}：{realm} 池里没有 LINE 入口 {alias}（LINE 登录用不了）")
                continue
            want = src.line_login.idp_representation(alias, channel, "-")
            wrong = [k for k in src.collect.IDP_FIELDS if k in want and _norm(idp.get(k)) != _norm(want[k])]
            wrong += [f"config.{k}" for k in src.collect.IDP_CONFIG
                      if str((idp.get("config") or {}).get(k)) != str(want["config"].get(k))]
            if wrong:
                out.add("kc:line-idp", f"{product} {row['site']}：{realm} 的 {alias} 这几项与 idp_representation 不同：{'、'.join(wrong)}")
            callbacks.add((realm, alias, channel))
    return sorted(callbacks)


# ---------------------------------------------------------------- LINE（L1、L2、L4、L6）

def check_line(src: Sources, rows: dict, out: Findings) -> None:
    for product, sites in rows.items():
        lines = {r["site"]: r["line"] for r in sites if r.get("line")}
        for site, line in sorted(lines.items()):
            if "error" in line:
                force = "block" if line.get("kind") == "refused" else "note"
                out.add("line:webhook", f"{product} {site}：LINE 读不到（{line['error']}）", force)
                continue
            hook = line.get("webhook") or {}
            want = src.site_step.webhook_url(site)
            if "error" in hook:
                out.add("line:webhook", f"{product} {site}：webhook 读不到（{hook['error']}）", "note")
            elif hook.get("endpoint") != want or hook.get("active") is not True:
                out.add("line:webhook", f"{product} {site}：webhook 地址 {hook.get('endpoint') or '（空）'}、"
                                        f"Use webhook {'开' if hook.get('active') else '关'}；应为 {want} 且开着")
            bot = line.get("bot") or {}
            if "error" in bot:
                out.add("line:bot-mode", f"{product} {site}：机器人模式读不到（{bot['error']}）", "note")
            elif bot.get("chatMode") != "bot":
                out.add("line:bot-mode", f"{product} {site}：chatMode 是 {bot.get('chatMode')}，应为 bot（聊天模式下自动回复失效）",
                        "block")
            test = line.get("webhook_test") or {}
            if "error" in test or test.get("success") is not True:
                out.add("line:webhook-test", f"{product} {site}：LINE → 服务器 webhook 测试没成功"
                                             f"（{test.get('error') or test.get('statusCode')}）")
        tests = [{"site": s} for s in lines if src.tenant.is_test(s) and "error" not in lines[s]]
        reference = src.tenant.reference_of(product, tests)
        if not reference:
            continue
        ref = lines[reference["site"]]
        for site, line in sorted(lines.items()):
            if src.tenant.is_test(site) or "error" in line:
                continue
            mine, theirs = (line.get("bot") or {}).get("markAsReadMode"), (ref.get("bot") or {}).get("markAsReadMode")
            if "error" not in (line.get("bot") or {}) and "error" not in (ref.get("bot") or {}) and mine != theirs:
                out.add("line:bot-mode", f"{product} {site}：markAsReadMode {mine}，测试租户 {reference['site']} 是 {theirs}（应一致）")
            if line.get("richmenu") is not ref.get("richmenu"):
                out.add("line:richmenu", f"{product} {site}：默认富菜单 {_has(line.get('richmenu'))}，"
                                         f"测试租户 {reference['site']} {_has(ref.get('richmenu'))}")


def _has(value) -> str:
    return "有" if value is True else "没有" if value is False else "读不到"


# ---------------------------------------------------------------- fatcc.com（F1、F2）

def check_entry(facts: dict, rows: dict, out: Findings) -> None:
    billing = facts.get("billing") or {"error": "not collected"}
    registered = billing.get("sites")
    if "error" in billing or not isinstance(registered, list):
        out.add("ext:fatcc.com", f"fatcc.com 的租户站点登记读不到（{billing.get('error') or (registered or {}).get('error')}）", "block")
        registered = None
    if registered is not None:
        by_site = {r.get("site"): r for r in registered}
        known = set()
        for product, sites in rows.items():
            for row in sites:
                site = row["site"]
                known.add(site)
                reg = by_site.get(site)
                if not reg:
                    out.add("ext:fatcc.com", f"{product} {site}：fatcc.com 没有这家的租户站点登记（进不了 CC）", "block")
                    continue
                if reg.get("product") != product:
                    out.add("ext:fatcc.com", f"{product} {site}：登记的产品是 {reg.get('product')}", "block")
                if str(reg.get("issuer") or "").rstrip("/") != str(row.get("issuer") or "").rstrip("/"):
                    out.add("ext:fatcc.com", f"{product} {site}：登记的签发方 {reg.get('issuer')}，站点用的是 {row.get('issuer')}",
                            "block")
        for site in sorted(set(by_site) - known):
            out.add("ext:fatcc.com", f"登记里有 {site}，生产上没有这个站点（只报告）", "note")
    entry = billing.get("entry") or {}
    if "error" in entry:
        out.add("ext:fatcc.com", f"fatcc.com 入口站点配置读不到（{entry['error']}）", "note")
        return
    problems = []
    if not entry.get("entry") or str(entry.get("url") or "").rstrip("/") != f"https://{ENTRY}":
        problems.append(f"入口开关 / 地址不对（{entry.get('url')}）")
    for name, label in (("token", "查询令牌"), ("webhook_secret", "回调签名密钥")):
        if not entry.get(name):
            problems.append(f"没有{label}")
    if problems:
        out.add("ext:fatcc.com", f"fatcc.com 入口站点：{'；'.join(problems)}（只核有没有）", "note")


# ---------------------------------------------------------------- 本机段（C1、C2、L5）

def zone_records(src: Sources, zone: str, get=None) -> list[dict]:
    """某个区的解析记录（e2e run.py 同一个令牌与读法，只发 GET）。"""
    token = (Path(src.e2e.CLOUDFLARE) / "cloudflare.token").read_text(encoding="utf-8").strip()
    get = get or src.e2e._cloudflare_get
    found = get(f"/zones?name={zone}", token)["result"]
    if not found:
        raise RuntimeError(f"no Cloudflare zone {zone}")
    return get(f"/zones/{found[0]['id']}/dns_records?per_page=500", token)["result"]


def check_dns(src: Sources, rows: dict, records: list[dict], prod_ip: str, out: Findings) -> list[str]:
    """C1；返回此刻在生产上的主机（证书只核这些）。"""
    on_production = []
    proxied = {}
    for product, sites in rows.items():
        for row in sites:
            site = row["site"]
            place = src.e2e.where_is(site, records, prod_ip)
            own = [r for r in records if r.get("name") == site] or \
                [r for r in records if r.get("name") == "*." + site.split(".", 1)[1]]
            proxied[site] = bool(own and own[0].get("proxied"))
            if place == "生产":
                on_production.append(site)
            elif src.tenant.is_test(site):
                out.add("dns:tenant", f"{product} {site} 现在在{place}（测试租户，登录后 e2e 记没测到）", "note")
            else:
                out.add("dns:tenant", f"{product} {site} 解析到{place}，不是生产")
        tests = [s for s in proxied if src.tenant.is_test(s) and any(r["site"] == s for r in sites)]
        for row in sites:
            site = row["site"]
            if tests and not src.tenant.is_test(site) and proxied[site] != proxied[tests[0]]:
                out.add("dns:tenant", f"{product} {site} 的 Cloudflare 代理{'开' if proxied[site] else '关'}，"
                                      f"测试租户 {tests[0]} {'开' if proxied[tests[0]] else '关'}（应一致）")
    for host, wanted_proxy in ((AUTH, None), (ENTRY, False)):
        own = [r for r in records if r.get("name") == host and r.get("type") == "A"]
        if not own or own[0].get("content") != prod_ip:
            out.add("dns:tenant", f"{host} 没有指向生产 {prod_ip} 的 A 记录")
            continue
        if wanted_proxy is not None and bool(own[0].get("proxied")) != wanted_proxy:
            out.add("dns:tenant", f"{host} 应只解析不代理（Cloudflare 代理开着）")
        on_production.append(host)
    out.info.append(f"解析：{len(on_production)} 个主机指向生产 {prod_ip}")
    return on_production


def cert_days(host: str, now=None) -> float:
    context = ssl.create_default_context()
    with socket.create_connection((host, 443), timeout=10) as raw, context.wrap_socket(raw, server_hostname=host) as tls:
        not_after = ssl.cert_time_to_seconds(tls.getpeercert()["notAfter"])
    return (not_after - (now or time.time())) / 86400


def check_certs(days: dict, out: Findings) -> None:
    known = {h: d for h, d in days.items() if not isinstance(d, str)}
    if known:
        host = min(known, key=known.get)
        out.info.append(f"证书：{len(known)} 个，最早到期 {host} 还剩 {known[host]:.0f} 天")
    for host, left in sorted(days.items()):
        if isinstance(left, str):
            out.add("tls:expiry", f"{host} 证书读不到（{left}）", "note")
        elif left < TLS_BLOCK_DAYS:
            out.add("tls:expiry", f"{host} 证书只剩 {left:.1f} 天（少于 {TLS_BLOCK_DAYS} 天）", "block")
        elif left < TLS_NOTE_DAYS:
            out.add("tls:expiry", f"{host} 证书剩 {left:.1f} 天（少于 {TLS_NOTE_DAYS} 天，续期该到了）", "note")


def login_callback_url(realm: str, alias: str, channel: str) -> str:
    redirect = f"https://{AUTH}/realms/{realm}/broker/{alias}/endpoint"
    return LINE_AUTHORIZE + "?" + urllib.parse.urlencode({"response_type": "code", "client_id": channel,
                                                           "redirect_uri": redirect, "state": "pearnlycheck",
                                                           "scope": "openid profile"})


def check_login_callbacks(callbacks: list, fetch, out: Findings) -> None:
    """L5：LINE 对登记过的回调 302 去登录页，没登记的直接 400（11-05 实测）。fetch(url) → (状态码, Location)。"""
    out.info.append(f"LINE 登录回调：核 {len(callbacks)} 个（池 × 入口）")
    for realm, alias, channel in callbacks:
        try:
            status, location = fetch(login_callback_url(realm, alias, channel))
        except Exception as error:  # noqa: BLE001  连不上 LINE 只报告
            out.add("line:login-callback", f"{realm} {alias}：没测到（{type(error).__name__}）", "note")
            continue
        if status == 302 and str(location or "").startswith("https://access.line.me/"):
            continue
        if status == 400:
            out.add("line:login-callback", f"LINE Login 频道 {channel} 没登记 {realm} 的接收地址"
                                           f"（https://{AUTH}/realms/{realm}/broker/{alias}/endpoint），LINE 登录回不来")
        else:
            out.add("line:login-callback", f"{realm} {alias}：LINE 回了 {status}（不是登记 / 没登记的已知反应，只报告）", "note")


def _fetch(url: str):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(urllib.request.Request(url, headers={"User-Agent": "pearnly-config-check"}), timeout=15) as response:
            return response.status, response.headers.get("Location")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Location")


# ---------------------------------------------------------------- 汇总

def compare(src: Sources, facts: dict, ext: dict, records=None, certs=None, fetch=None, prod_ip=None) -> Findings:
    out = Findings(ext)
    rows = _site_rows(facts, out)
    check_realms(src, facts, out)
    check_site_clients(src, facts, rows, out)
    callbacks = check_line_idps(src, facts, rows, out)
    check_line(src, rows, out)
    check_entry(facts, rows, out)
    if records is not None:
        hosts = check_dns(src, rows, records, prod_ip, out)
        if certs is None:
            certs = {}
            for host in hosts:
                try:
                    certs[host] = cert_days(host)
                except (OSError, ssl.SSLError) as error:
                    certs[host] = type(error).__name__
        check_certs(certs, out)
    if fetch is not None:
        check_login_callbacks(callbacks, fetch, out)
    return out


CHECKED = ("kc:pearnly-business:remember-me", "kc:pearnly-cowork:remember-me", "kc:site-client", "kc:site-client-secret",
           "kc:pearnly-ai", "kc:line-idp", "line:webhook", "line:bot-mode", "line:richmenu", "line:login-callback",
           "line:webhook-test", "ext:fatcc.com", "dns:tenant", "tls:expiry")
NOT_READ = {"liff:endpoint": UNREADABLE[2]}


def summary(facts: dict) -> list[str]:
    lines = []
    for realm, data in sorted((facts.get("keycloak") or {}).items()):
        if "error" not in data:
            lines.append(f"Keycloak {realm}：客户端 {len(data.get('clients') or [])} 个，身份提供者 "
                         f"{'、'.join(i.get('alias') for i in data.get('idps') or []) or '无'}")
    for product, rows in sorted((facts.get("sites") or {}).items()):
        if isinstance(rows, list):
            with_line = sum(1 for r in rows if r.get("line"))
            lines.append(f"{product}：站点 {len(rows)} 个（配了 LINE {with_line} 个）")
    registered = (facts.get("billing") or {}).get("sites")
    if isinstance(registered, list):
        lines.append(f"fatcc.com 登记：{len(registered)} 条")
    return lines


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--facts", required=True, help="生产机段交回的 JSON（external-config-collect.py 的 __EXT 行，或其中的 JSON）")
    parser.add_argument("--map", required=True, help="产品地图导出（check-product-map.py --export）")
    parser.add_argument("--records", help="离线：Cloudflare 记录 JSON（pearnly.com 与 fatcc.com 两个区合在一个列表里）")
    parser.add_argument("--certs", help="离线：{主机: 剩余天数} JSON")
    parser.add_argument("--no-network", action="store_true", help="不连 Cloudflare、证书与 LINE（离线）")
    args = parser.parse_args(argv)
    try:
        text = Path(args.facts).read_text(encoding="utf-8").strip()
        facts = json.loads(text[len("__EXT "):] if text.startswith("__EXT ") else text)
        ext = json.loads(Path(args.map).read_text(encoding="utf-8")).get("ext") or {}
    except (OSError, ValueError) as exc:
        print(f"  PROBLEM cannot read the inputs: {exc}")
        return 2
    src = Sources()
    records = json.loads(Path(args.records).read_text(encoding="utf-8")) if args.records else None
    certs = json.loads(Path(args.certs).read_text(encoding="utf-8")) if args.certs else None
    fetch = None if args.no_network else _fetch
    skipped = []
    if records is None and not args.no_network:
        try:
            records = zone_records(src, "pearnly.com") + zone_records(src, ENTRY)
        except Exception as error:  # noqa: BLE001  读不到解析只报告：租户入口另有部署后回读
            skipped.append(f"Cloudflare 解析读不到（{type(error).__name__}），解析与证书没核")
    elif records is None:
        skipped.append("离线：Cloudflare 解析与证书没核")
    if fetch is None:
        skipped.append("离线：LINE 登录回调没测")
    prod_ip = src.e2e.production_ip(ROOT / "infrastructure" / "production" / "target.json")
    out = compare(src, facts, ext, records=records, certs=certs, fetch=fetch, prod_ip=prod_ip)
    for line in skipped:
        out.add("dns:tenant" if "解析" in line else "line:login-callback", line, "note")
    for line in summary(facts) + out.info:
        print(f"  {line}")
    for key in sorted(set(ext) - set(CHECKED) - set(NOT_READ)):
        out.add(key, "地图里登记了这一节，本脚本还没有对应检查", "block")
    print("  读不到（留在开户清单 / 真机单）：" + "；".join(UNREADABLE))
    for line in out.note:
        print(f"  NOTE {line}")
    for line in out.block:
        print(f"  BLOCK {line}")
    if out.block:
        print(f"  {len(out.block)} 项站外设置不对，拦部署：改好 Keycloak / LINE / 入口登记 / 解析，"
              "或在 scripts/impact/product-map/config.toml 改分类（走评审）；没有绕过开关")
        return 1
    print("  站外设置对比通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
