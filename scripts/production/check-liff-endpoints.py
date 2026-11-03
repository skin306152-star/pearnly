#!/usr/bin/env python3
"""核对每个租户的 LIFF 落地地址，并把 LINE 打开网页端的那条路从外面走一遍（只读，不登录，不需要任何密钥）。

为什么有它（2026-11-03 事故）：mrerp 的 LIFF 落在 `/stocktake`、erpcowork 落在 `/desk/pearnly-intake`，
10-23 起所有 LINE 链接先经本站 LIFF（`https://liff.line.me/<id>/api/method/…`），LINE 把这段路径接在落地地址
后面，拼成 `/stocktake/api/method/…`，LINE 里点卡片「แก้ไข」全是 404。规定见
`apps/pearnly_control/pearnly_control/onboarding/line_liff.py`：ERP / Cowork 必须是站点根，DMS 是 `/liff/dms-booking`。

为什么不用 LINE Login 频道接口读：那要 LINE Login 频道密钥，它只存在控制面（开户目标，加密），站点和生产机上都没有
（只在 set_line 步骤里过一下）。而 `https://liff.line.me/<id>` 对普通浏览器返回的页面里就写着落地地址
（`liffEndpointUrl`），这正是 LINE 自己拿去拼地址的那个值，不要密钥、在生产机和本机都能跑。这个页面格式不是 LINE
公开承诺的接口：读不出来按问题报（退出码 1），不会当通过。

每个租户查两件事：
1. LIFF 落地地址（liff.line.me 上读）必须是规定的那一个（末尾斜杠可有可无）；
2. 只对 ERP / Cowork：入口可达。用 LINE 普通内置浏览器的 User-Agent 不登录请求
   `line_login.enter?to=/desk`，本站应 302 转到本站的 LIFF（ID 与站点配置一致）；再把 LINE 会打开的两种地址
   （落地地址直接接路径、落地地址带 `liff.state`）各请求一次，一路跟着本站内的跳转，必须先 302、
   本站内每一跳都不能是 4xx / 5xx，直到离开本站（去统一登录）或停在本站页面。

测不到的（只能部分代替真机）：LINE App 里面的行为——LIFF 是否真按这样拼地址、`liff.init`、原生相机授权、
LINE 登录、已登录用户真正打开的单据页和它的权限；也测不到 LINE 改了内部规则。这些仍要真机。

用法：
  本机或任何地方单独跑（LIFF ID 从站点的转 LIFF 跳转里读出；DMS 没有这一跳，要自己给 ID）：
    python3 scripts/production/check-liff-endpoints.py erp:mrerp.pearnly.com cowork:erpcowork.pearnly.com \\
        dms:ris.pearnly.com:2010411313-KiXV2tco
  部署回读（deploy-readback.sh）：从站点配置读出每行「产品 站点 LIFF ID」，经标准输入交给 --stdin。
退出码：0 全部通过，1 有问题（逐项打印），2 用法不对。
"""
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DMS_PATH = "/liff/dms-booking"
ENTER = "/api/method/pearnly_core.account.line_login.enter"
ENTRY_PRODUCTS = ("erp", "cowork")
PRODUCTS = ("erp", "cowork", "dms")
# LINE 普通内置浏览器（不是 LIFF）：站点只在这里转 LIFF（`line_login.in_line_browser`）。
LINE_BROWSER_UA = ("Mozilla/5.0 (Linux; Android 13; SM-A145F) AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Mobile Safari/537.36 Line/14.1.0/IAB")
PLAIN_UA = "Mozilla/5.0 (X11; Linux x86_64) pearnly-liff-check"
TIMEOUT = 15
ATTEMPTS = 3
MAX_HOPS = 8


class CheckError(Exception):
    pass


def expected_endpoint(site: str, product: str) -> str:
    """规定的落地地址；与 `line_liff.endpoint`、`site-step.check_liff` 同一口径（tests 里互相核对）。"""
    return f"https://{site}{DMS_PATH if product == 'dms' else '/'}"


def same_url(a, b) -> bool:
    return str(a or "").rstrip("/") == str(b or "").rstrip("/")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def fetch(url: str, agent: str):
    """GET 一次，不跟跳转：返回 (状态码, Location, 正文)。连不上、5xx 重试两次。"""
    error = ""
    for attempt in range(ATTEMPTS):
        if attempt:
            time.sleep(5)
        request = urllib.request.Request(url, headers={"User-Agent": agent})
        try:
            with _OPENER.open(request, timeout=TIMEOUT) as response:
                return response.status, response.headers.get("Location") or "", response.read(200_000).decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                return exc.code, exc.headers.get("Location") or "", ""
            error = f"HTTP {exc.code}"
        except (urllib.error.URLError, OSError) as exc:
            error = str(getattr(exc, "reason", exc))
    raise CheckError(f"{url} unreachable after {ATTEMPTS} attempts: {error}")


_ENDPOINT = re.compile(r'liffEndpointUrl\s*=\s*("(?:[^"\\]|\\.)*")')


def liff_endpoint(liff_id: str, get=fetch) -> str:
    """LINE 对普通浏览器返回的 LIFF 页面里写的落地地址。"""
    status, _, body = get(f"https://liff.line.me/{urllib.parse.quote(liff_id, safe='')}/", PLAIN_UA)
    match = _ENDPOINT.search(body) if status == 200 else None
    if not match:
        raise CheckError(f"LIFF {liff_id}: liff.line.me answered {status} without an endpoint URL "
                         "(unknown LIFF ID, or LINE changed the page)")
    try:
        return json.loads(match.group(1))
    except ValueError:
        raise CheckError(f"LIFF {liff_id}: cannot read the endpoint URL on liff.line.me") from None


def _path(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return parts.path + ("?" + parts.query if parts.query else "")


def _on_site(url: str, site: str) -> bool:
    return urllib.parse.urlsplit(url).netloc.lower() == site.lower()


def walk(url: str, site: str, get=fetch) -> str:
    """不登录走一遍本站内的跳转：第一跳必须是 3xx，本站内每一跳都不能是 4xx / 5xx。返回走到哪里。"""
    first = True
    for _ in range(MAX_HOPS):
        status, location, _ = get(url, PLAIN_UA)
        if status >= 400:
            raise CheckError(f"{url} answered {status}")
        if first and not 300 <= status < 400:
            raise CheckError(f"{url} answered {status}, expected a redirect")
        first = False
        if not 300 <= status < 400:
            return f"stays on {_path(url)} ({status})"
        if not location:
            raise CheckError(f"{url} answered {status} without a Location")
        last, url = url, urllib.parse.urljoin(url, location)
        if not _on_site(url, site):
            return f"last site page {_path(last)}, then {urllib.parse.urlsplit(url).netloc}"
    raise CheckError(f"more than {MAX_HOPS} redirects from {url}")


def hop_of(site: str, get=fetch):
    """本站对 LINE 普通内置浏览器给出的转 LIFF 跳转：(LIFF ID, LINE 要接在落地地址后面的路径)。"""
    entry = f"https://{site}{ENTER}?to=%2Fdesk"
    status, location, _ = get(entry, LINE_BROWSER_UA)
    match = re.match(r"https://liff\.line\.me/([^/?#]+)(/.*)$", location or "")
    if not 300 <= status < 400 or not match:
        raise CheckError(f"{entry} from the LINE browser answered {status} {location or ''}".rstrip()
                         + ", expected a redirect to this site's LIFF")
    return urllib.parse.unquote(match.group(1)), match.group(2)


def composed(endpoint: str, path: str) -> list:
    """LINE 打开 `liff.line.me/<id><path>` 时会去的两种地址：落地地址接上路径；落地地址带 `liff.state`。"""
    if not urllib.parse.urlsplit(endpoint).path:
        endpoint += "/"
    sep = "&" if "?" in endpoint else "?"
    return [endpoint.rstrip("/") + path, endpoint + sep + urllib.parse.urlencode({"liff.state": path})]


def check(product: str, site: str, liff_id: str = "", get=None) -> list:
    """一个租户：返回问题列表（空 = 通过），通过的项打印出来。"""
    get = get or fetch
    problems = []
    path = ""
    if product in ENTRY_PRODUCTS:
        try:
            hopped, path = hop_of(site, get)
        except CheckError as exc:
            return [f"{site}: {exc}"]
        if liff_id and hopped != liff_id:
            problems.append(f"{site}: hops to LIFF {hopped}, site config has {liff_id}")
        liff_id = liff_id or hopped
    if not liff_id:
        return [f"{site}: no LIFF ID to check (give it as {product}:{site}:<LIFF ID>)"]
    try:
        endpoint = liff_endpoint(liff_id, get)
    except CheckError as exc:
        return problems + [f"{site}: {exc}"]
    want = expected_endpoint(site, product)
    if same_url(endpoint, want):
        print(f"  {site} LIFF {liff_id} endpoint {endpoint} ok")
    else:
        problems.append(f"{site}: LIFF {liff_id} endpoint is {endpoint or 'empty'}, must be {want}")
    for url in composed(endpoint, path) if path else ():
        try:
            print(f"  {site} {url} -> {walk(url, site, get)}")
        except CheckError as exc:
            problems.append(f"{site}: LINE entry {exc}")
    return problems


def _rows(args) -> list:
    rows = []
    if args.stdin:
        for line in sys.stdin:
            parts = line.split()
            if parts:
                rows.append(parts)
    for spec in args.tenants:
        rows.append(spec.split(":"))
    for row in rows:
        if not 2 <= len(row) <= 3 or row[0] not in PRODUCTS or not row[1]:
            raise SystemExit(f"bad tenant {' '.join(row)!r}: expected product site [liff_id], product one of {PRODUCTS}")
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("tenants", nargs="*", help="product:site[:liff_id], e.g. erp:mrerp.pearnly.com")
    parser.add_argument("--stdin", action="store_true", help="also read 'product site liff_id' lines from stdin")
    args = parser.parse_args(argv)
    try:
        rows = _rows(args)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 2
    if not rows:
        parser.print_usage(sys.stderr)
        return 2
    problems = []
    for row in rows:
        problems += check(*row)
    for problem in problems:
        print(f"  PROBLEM {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
