#!/usr/bin/env python3
"""部署后逐个正式租户走「上线必验」入口（只读、不登录、不写、不要密钥）。

为什么有它（2026-11-03 事故）：mrerp 卡片「แก้ไข」404 只出在正式租户；测试租户上测得再全也看不到。产品地图
（scripts/impact/product-map/，审定见 docs/work/2026-11-04-product-map.md）里标了上线必验的行带着探针，
本脚本对每个正式租户 × 它那个产品的每个上线必验行跑一遍：
- liff：照 LINE 打开这个入口的走法——本站入口 `line_login.enter?to=<路径>` 用 LINE 普通内置浏览器的标识请求，
  必须转到本站配置的 LIFF（一个 pearnly_line_* 都没有的租户算没开 LINE，跳过并记 NOTE；有频道却没有 LIFF ID 才算问题）；再把 LINE 会打开的两种地址（LIFF 落地地址接路径、落地地址带 liff.state）各走一遍，
  本站内每一跳都不能是 4xx / 5xx（check-liff-endpoints.py 的同一套读法，那边核落地地址本身）；路径里的 `{doc}` 换成不存在的单号；
- http：直接请求本站一个地址（agent = line / plain），本站内每一跳都不能是 4xx / 5xx；
- assistant-gate：不登录打开 /assistant，必须转到这家配置的 CC 入口（默认 https://fatcc.com）并带上 `?site=<本站>`，
  入口那一跳也不能是 4xx / 5xx（11-03 切换账号落到登录页）。
测不到的：登录后的页面与权限、LINE App 里面的行为，仍按地图的真机清单做。

站点列表与配置用 check-tenant-config.py 的同一读法（web 容器里读文件）；测试租户（test 开头）不在这里走。

用法：
  生产机上（deploy-readback.sh）：python3 check-tenant-entries.py --map /opt/pearnly/deploy/scripts/production/product-map.json
  离线：python3 check-tenant-entries.py --map 导出.json --sites 读出的.json
退出码：0 全部通过；1 有问题（逐项打印 PROBLEM）；2 用法不对或读不到。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


liff = _load("check_liff_endpoints", "check-liff-endpoints.py")
config = _load("check_tenant_config", "check-tenant-config.py")

PRODUCT_CODES = {"ERP": "erp", "Cowork": "cowork", "DMS": "dms"}
PROBE_DOC = "PEARNLY-PROBE-0"
DEFAULT_ENTRY = "https://fatcc.com"
AGENTS = {"line": liff.LINE_BROWSER_UA, "plain": liff.PLAIN_UA}


def rows_for(product_map: dict, product: str) -> list:
    out = []
    for row in product_map["rows"]:
        products = {PRODUCT_CODES.get(p) for p in ([row["product"]] + list(row.get("products") or []))}
        if row.get("must_verify") and row.get("probe") and product in products:
            out.append(row)
    return out


def _path(template: str) -> str:
    return template.replace("{doc}", PROBE_DOC)


def _walk(url: str, site: str, agent: str, get) -> str:
    """本站内跟着跳：每一跳都不能是 4xx / 5xx；离开本站或停在页面就结束。"""
    for _ in range(liff.MAX_HOPS):
        status, location, _ = get(url, agent)
        if status >= 400:
            raise liff.CheckError(f"{url} answered {status}")
        if not 300 <= status < 400:
            return f"stays on {liff._path(url)} ({status})"
        if not location:
            raise liff.CheckError(f"{url} answered {status} without a Location")
        last, url = url, urllib.parse.urljoin(url, location)
        if not liff._on_site(url, site):
            return f"last site page {liff._path(last)}, then {urllib.parse.urlsplit(url).netloc}"
    raise liff.CheckError(f"more than {liff.MAX_HOPS} redirects from {url}")


class Skip(Exception):
    """这一行在这个租户上不适用：跳过，记一条 NOTE，不算问题。"""


def probe_liff(site: str, keys: dict, probe: dict, get) -> list:
    liff_id = keys.get("pearnly_line_liff_id")
    if not liff_id and not keys.get("pearnly_line_*"):
        # 一个 pearnly_line_* 都没有 = 这家没开 LINE（如录教程的演示租户）：LINE 入口不存在，跳过（主会话 11-04）
        raise Skip("no LINE channel on this tenant")
    if not liff_id:
        return ["no pearnly_line_liff_id: the tenant has a LINE channel but LINE links cannot open through LIFF"]
    entry = f"https://{site}{liff.ENTER}?" + urllib.parse.urlencode({"to": _path(probe["path"])})
    status, location, _ = get(entry, liff.LINE_BROWSER_UA)
    prefix = f"https://liff.line.me/{urllib.parse.quote(str(liff_id), safe='')}/"
    if not 300 <= status < 400 or not location.startswith(prefix):
        return [f"{entry} from the LINE browser answered {status} {location}".rstrip() + f", expected a redirect to {prefix}"]
    endpoint = liff.liff_endpoint(str(liff_id), get)
    lines, problems = [], []
    for url in liff.composed(endpoint, "/" + location[len(prefix):]):
        try:
            lines.append(f"{url} -> {_walk(url, site, liff.PLAIN_UA, get)}")
        except liff.CheckError as exc:
            problems.append(str(exc))
    for line in lines:
        print(f"    {line}")
    return problems


def probe_http(site: str, keys: dict, probe: dict, get) -> list:
    url = f"https://{site}{_path(probe['path'])}"
    try:
        print(f"    {url} -> {_walk(url, site, AGENTS[probe.get('agent', 'plain')], get)}")
    except liff.CheckError as exc:
        return [str(exc)]
    return []


def probe_assistant_gate(site: str, keys: dict, probe: dict, get) -> list:
    entry = str(keys.get("pearnly_assistant_entry_url") or DEFAULT_ENTRY).rstrip("/")
    url = f"https://{site}/assistant"
    status, location, _ = get(url, liff.PLAIN_UA)
    target = urllib.parse.urljoin(url, location or "")
    parts = urllib.parse.urlsplit(target)
    want = urllib.parse.urlsplit(entry)
    sent = urllib.parse.parse_qs(parts.query).get("site", [""])[0]
    if not 300 <= status < 400 or (parts.scheme, parts.netloc) != (want.scheme, want.netloc) or sent != site:
        return [f"{url} as a guest answered {status} {location}".rstrip() + f", expected a redirect to {entry}/?site={site}"]
    status, _, _ = get(target, liff.PLAIN_UA)
    if status >= 400:
        return [f"{target} (CC entry) answered {status}"]
    print(f"    {url} -> {target} ({status})")
    return []


PROBES = {"liff": probe_liff, "http": probe_http, "assistant-gate": probe_assistant_gate}


def check(product_map: dict, dumps: dict, get=None) -> list:
    get = get or liff.fetch
    problems = []
    for product in config.PRODUCTS:
        sites = dumps.get(product)
        rows = rows_for(product_map, product)
        if not rows:
            continue
        if not isinstance(sites, list):
            problems.append(f"{product}: cannot list tenants ({(sites or {}).get('error', 'missing')})")
            continue
        for tenant in sites:
            if "keys" not in tenant or config.is_test(tenant["site"]):
                continue
            for row in rows:
                runner = PROBES.get(row["probe"].get("type"))
                if runner is None:
                    problems.append(f"{tenant['site']} {row['id']}: unknown probe {row['probe']}")
                    continue
                try:
                    found = runner(tenant["site"], tenant["keys"], row["probe"], get)
                except Skip as exc:
                    print(f"  {tenant['site']} {row['id']} ({row['probe']['type']}) NOTE skipped: {exc}")
                    continue
                except liff.CheckError as exc:
                    found = [str(exc)]
                status = "ok" if not found else "PROBLEM"
                print(f"  {tenant['site']} {row['id']} ({row['probe']['type']}) {status}")
                problems += [f"{tenant['site']} {row['id']}: {problem}" for problem in found]
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--map", required=True, help="产品地图导出（check-product-map.py --export）")
    parser.add_argument("--sites", help="离线：已读出的站点配置 JSON（check-tenant-config.py --dump），不跑 docker")
    args = parser.parse_args(argv)
    try:
        product_map = json.loads(Path(args.map).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"  PROBLEM cannot read the product map export: {exc}")
        return 2
    dumps = json.loads(Path(args.sites).read_text(encoding="utf-8")) if args.sites else config.collect(product_map)
    problems = check(product_map, dumps)
    for problem in problems:
        print(f"  PROBLEM {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
