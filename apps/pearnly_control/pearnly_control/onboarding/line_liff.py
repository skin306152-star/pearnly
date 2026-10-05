# -*- coding: utf-8 -*-
"""每个部署一个 LIFF：开户时由系统在全平台共用的 LINE Login 频道下建好、按规定校正（2026-10-05 起不再手建手填）。

为什么要系统建：LIFF 以前照手册在 LINE 后台手建，再把 ID 抄进开户单。10-04 查到 testcowork 的落地地址少了
`.com`、testerp 填成了 DMS 的地址，LINE 免密登录和菜单深链都因此打不开。现在规定写在这里，每次开户、每次重排
「配 LINE 渠道」都按它对齐一次；站点上的 `check_liff`（infrastructure/production/site-step.py）再读回核对同一口径。

规定：
- Size Full；Endpoint：DMS 是 `/liff/dms-booking`（改单编辑器直接打开它），ERP / Cowork 是站点根
  （`liff.init` 要求页面在落地地址之下，盘点页 `/stocktake`、Cowork 页面都在根下）；
- Scope：openid（页面只用 `liff.getIDToken()` 证明是谁，代码里没有读 profile）；DMS 再加 chat_message.write
  （扫身份证的结果走免费回复）；
- 加好友提示关，扫码关。

只改"用不了"的：已有 LIFF 多勾的权限、ERP / Cowork 落在站点下某个子路径（10-05 生产 erpcowork 是
`/desk/pearnly-intake`、mrerp 是 `/stocktake`）都照旧，和站点上 `check_liff` 的认定一致；缺必需权限、地址不在本站、
尺寸不是 Full、开了加好友提示才整条改回规定。

哪个算"这个部署的 LIFF"：渠道上已记的 LIFF ID 优先（10-05 之前手建的租户），其次按落地地址找，都没有才新建。
一个 LINE Login 频道最多 30 个 LIFF（LINE 的硬限制），满了如实报错，不删别人的。

纯 HTTP，不依赖 Frappe（`http` 默认取 requests，测试传假的）：控制面用 `provisioning.ensure_liff` 包一层（读开户目标上的频道与密钥、报错给开户人看）。
"""
from urllib.parse import quote

TOKEN_API = "https://api.line.me/oauth2/v3/token"
APPS_API = "https://api.line.me/liff/v1/apps"
LIMIT = 30
DMS_PATH = "/liff/dms-booking"
TIMEOUT = 20


class LiffError(Exception):
    """LINE 接口拒绝或返回了看不懂的结果。消息不含任何凭据。"""


class LiffFull(LiffError):
    """共用 LINE Login 频道的 LIFF 已到 LINE 的上限。"""


def _requests():
    import requests

    return requests


def endpoint(site: str, product: str) -> str:
    return f"https://{site}{DMS_PATH}" if product == "dms" else f"https://{site}/"


def desired(site: str, product: str) -> dict:
    """这个部署的 LIFF 应有的设置（新建与校正用同一份）。"""
    # LINE 不允许 LIFF 名称里出现 "LINE"；域名前缀碰巧含这几个字母时换成产品名。
    name = site if "line" not in site.lower() else f"Pearnly {product.upper()}"
    return {
        "view": {"type": "full", "url": endpoint(site, product)},
        "description": name,
        "features": {"qrCode": False},
        "permanentLinkPattern": "concat",
        "scope": ["openid"] + (["chat_message.write"] if product == "dms" else []),
        "botPrompt": "none",
    }


def _same_url(a, b) -> bool:
    return str(a or "").rstrip("/") == str(b or "").rstrip("/")


def _usable_url(url, site: str, product: str) -> bool:
    """与站点开户步骤 `check_liff` 同一口径：DMS 一字不差，ERP / Cowork 在本站下面即可。"""
    if product == "dms":
        return _same_url(url, endpoint(site, product))
    return (str(url or "").rstrip("/") + "/").startswith(f"https://{site}/")


def _drifted(app: dict, site: str, product: str, want: dict) -> bool:
    view = app.get("view") or {}
    return (view.get("type") != want["view"]["type"]
            or not _usable_url(view.get("url"), site, product)
            or not set(want["scope"]) <= set(app.get("scope") or [])
            or (app.get("botPrompt") or "normal") != want["botPrompt"])


def _token(channel: str, secret: str, http) -> str:
    response = http.post(TOKEN_API, data={"grant_type": "client_credentials", "client_id": channel,
                                          "client_secret": secret}, timeout=TIMEOUT)
    if response.status_code != 200:
        raise LiffError(f"LINE Login channel {channel} refused its credentials (HTTP {response.status_code})")
    token = str((response.json() or {}).get("access_token") or "")
    if not token:
        raise LiffError(f"LINE Login channel {channel} issued no token")
    return token


def _call(http, method: str, url: str, token: str, body=None) -> dict:
    response = http.request(method, url, json=body, timeout=TIMEOUT,
                            headers={"Authorization": "Bearer " + token})
    if response.status_code not in (200, 201):
        raise LiffError(f"LINE LIFF API {method} HTTP {response.status_code}: {response.text[:200]}")
    return response.json() if response.content else {}


def ensure(channel: str, secret: str, site: str, product: str, liff_id: str = "", *, http=None) -> str:
    """找到或建好这个部署的 LIFF，设置不对就改对；返回 LIFF ID。幂等。"""
    http = http or _requests()
    token = _token(channel, secret, http)
    apps = _call(http, "GET", APPS_API, token).get("apps") or []
    want = desired(site, product)
    app = next((a for a in apps if liff_id and a.get("liffId") == liff_id), None)
    if app is None:
        app = next((a for a in apps if _same_url((a.get("view") or {}).get("url"), want["view"]["url"])), None)
    if app is not None:
        if _drifted(app, site, product, want):
            _call(http, "PUT", f"{APPS_API}/{quote(app['liffId'], safe='')}", token, want)
        return app["liffId"]
    if len(apps) >= LIMIT:
        raise LiffFull(f"LINE Login channel {channel} already has {len(apps)} LIFF apps")
    created = str(_call(http, "POST", APPS_API, token, want).get("liffId") or "")
    if not created.startswith(channel + "-"):
        raise LiffError(f"LINE returned an unexpected LIFF ID: {created or 'empty'}")
    return created


def remove(channel: str, secret: str, liff_id: str, *, http=None) -> bool:
    """删掉这个部署的 LIFF（拆租户时腾出名额）。不属于这个频道或已不存在返回 False。"""
    if not liff_id or not liff_id.startswith(channel + "-"):
        return False
    http = http or _requests()
    token = _token(channel, secret, http)
    response = http.request("DELETE", f"{APPS_API}/{quote(liff_id, safe='')}", timeout=TIMEOUT,
                            headers={"Authorization": "Bearer " + token})
    if response.status_code == 404:
        return False
    if response.status_code != 200:
        raise LiffError(f"LINE LIFF API DELETE HTTP {response.status_code}: {response.text[:200]}")
    return True
