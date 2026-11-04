#!/usr/bin/env python3
"""部署前租户配置对比（只读）：每个产品的正式租户与同产品的测试租户，按产品地图的配置分类逐键比。

为什么有它（2026-11-03 事故）：mrerp 的 LIFF 落在子路径、测试只在 testerp 上做过——正式租户和测试租户配置不一样，
测试再全也测不到客户真正在用的那套。规定见 scripts/impact/product-map/config.toml，审定见 docs/work/2026-11-04-product-map.md：
- same（应一致）：正式租户的值与测试租户不同 → 拦部署；
- shape（形状规则）：配了这个键的租户，值不满足 pattern → 拦部署；
- presence（只核有没有）：有没有与测试租户不同 → 只报告；secret 的只看有没有，不取值、不打印；
- varies / infra：不读。
没有绕过开关：要放行就改配置，或者在地图里改分类（走评审）。

站点配置在各产品 web 容器里读（sites/common_site_config.json 垫底、站点自己的 site_config.json 覆盖），
不进数据库容器；容器里只跑一段读文件的脚本，密钥类在容器里就换成「有」，值不出容器。
测试租户 = 站点名以 test 开头（testerp / testcowork / testdms）。

用法：
  生产机上（deploy-run.sh 激活前）：python3 check-tenant-config.py --map /opt/pearnly/deploy/scripts/production/product-map.json
  本机经 SSH（deploy.sh 演练与正式部署构建前）：ssh … "python3 - --map-b64 <导出的 base64>" < check-tenant-config.py
  离线：python3 check-tenant-config.py --map 导出.json --sites 读出的.json
  --dump F：把读出的（已去敏）站点配置写成 JSON（check-tenant-entries.py 复用同一个读法）
退出码：0 没有要拦的差异（可能有只报告的）；1 有要拦的差异；2 用法不对或读不到配置。
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys

PRODUCTS = ("erp", "cowork", "dms")
WEB = "pearnly-production-{}-web-1"
BENCH = "/home/frappe/frappe-bench"
TEST_PREFIX = "test"
READ = ("same", "shape", "presence")

# 在 web 容器里跑：只输出 spec 里的键；secret 的只给 true，前缀族只给键名
DUMPER = r'''
import json, os, sys
spec = json.loads(sys.argv[1])
try:
    common = json.load(open("sites/common_site_config.json"))
except Exception:
    common = {}
out = []
for site in sorted(os.listdir("sites")):
    path = os.path.join("sites", site, "site_config.json")
    if not os.path.isfile(path):
        continue
    try:
        conf = dict(common, **json.load(open(path)))
    except Exception as exc:
        out.append({"site": site, "error": type(exc).__name__})
        continue
    keys = {}
    for name, rule in spec.items():
        if name.endswith("*"):
            keys[name] = sorted(k for k in conf if k.startswith(name[:-1]) and conf[k] not in (None, ""))
        elif conf.get(name) not in (None, ""):
            keys[name] = True if rule.get("secret") else conf[name]
        else:
            keys[name] = None
    out.append({"site": site, "keys": keys})
print(json.dumps(out))
'''


def spec_of(product_map: dict) -> dict:
    return {name: {"secret": bool(rule.get("secret"))} for name, rule in product_map["config"].items()
            if rule.get("class") in READ}


def collect(product_map: dict, run=subprocess.run) -> dict:
    """{产品: [{site, keys} …]}；读不到的产品记 {"error": …}。"""
    spec = json.dumps(spec_of(product_map))
    result = {}
    for product in PRODUCTS:
        web = WEB.format(product)
        done = run(["docker", "exec", "-i", "-w", BENCH, web, "python3", "-", spec], input=DUMPER,
                   capture_output=True, text=True, timeout=60)
        if done.returncode != 0:
            result[product] = {"error": (done.stderr or done.stdout or "").strip()[-200:] or f"exit {done.returncode}"}
            continue
        try:
            result[product] = json.loads(done.stdout)
        except ValueError:
            result[product] = {"error": "unreadable output"}
    return result


def is_test(site: str) -> bool:
    return site.startswith(TEST_PREFIX)


def _show(value, rule) -> str:
    if value is None:
        return "（没配）"
    if rule.get("secret"):
        return "（有）" if not isinstance(value, list) else "、".join(value) or "（没配）"
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= 80 else text[:77] + "…"


def compare(product_map: dict, dumps: dict) -> tuple[list, list]:
    """(要拦的差异, 只报告的)。"""
    block, note = [], []
    config = product_map["config"]
    for product in PRODUCTS:
        sites = dumps.get(product)
        if sites is None:
            continue
        if isinstance(sites, dict):
            block.append(f"{product}：读不到站点配置（{sites.get('error')}）")
            continue
        for row in sites:
            if "error" in row:
                block.append(f"{product} {row['site']}：site_config.json 读不出（{row['error']}）")
        rows = [row for row in sites if "keys" in row]
        tests = [row for row in rows if is_test(row["site"])]
        formal = [row for row in rows if not is_test(row["site"])]
        if not tests:
            note.append(f"{product}：没有测试租户，正式租户之间不比")
        reference = tests[0] if tests else None
        for name, rule in sorted(config.items()):
            kind = rule.get("class")
            if kind == "same" and reference:
                want = reference["keys"].get(name)
                for row in formal:
                    have = row["keys"].get(name)
                    if have != want:
                        block.append(f"{product} {row['site']} {name}：{_show(have, rule)}，测试租户 {reference['site']} 是 "
                                     f"{_show(want, rule)}（应一致）")
            elif kind == "shape":
                pattern = re.compile(rule.get("pattern") or "")
                for row in rows:
                    have = row["keys"].get(name)
                    if have is not None and not pattern.search(str(have)):
                        block.append(f"{product} {row['site']} {name}：{_show(have, rule)} 不满足「{rule.get('rule') or rule.get('pattern')}」")
            elif kind == "presence" and reference:
                want = reference["keys"].get(name)
                for row in formal:
                    have = row["keys"].get(name)
                    if (sorted(have) if isinstance(have, list) else bool(have)) != \
                            (sorted(want) if isinstance(want, list) else bool(want)):
                        note.append(f"{product} {row['site']} {name}：{_show(have, rule)}，测试租户 {reference['site']} "
                                    f"{_show(want, rule)}（只报告）")
    return block, note


def load_map(args) -> dict:
    if args.map_b64:
        return json.loads(base64.b64decode(args.map_b64).decode("utf-8"))
    with open(args.map, encoding="utf-8") as handle:
        return json.load(handle)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--map", help="产品地图导出（check-product-map.py --export）")
    source.add_argument("--map-b64", help="同上，base64（经 SSH 交过来）")
    parser.add_argument("--sites", help="离线：已读出的站点配置 JSON，不跑 docker")
    parser.add_argument("--dump", help="把读出的站点配置写到这个文件")
    args = parser.parse_args(argv)
    try:
        product_map = load_map(args)
    except (OSError, ValueError) as exc:
        print(f"  PROBLEM cannot read the product map export: {exc}")
        return 2
    if args.sites:
        with open(args.sites, encoding="utf-8") as handle:
            dumps = json.load(handle)
    else:
        dumps = collect(product_map)
    if args.dump:
        with open(args.dump, "w", encoding="utf-8") as handle:
            json.dump(dumps, handle, ensure_ascii=False)
    block, note = compare(product_map, dumps)
    counted = {p: len([r for r in s if "keys" in r]) for p, s in dumps.items() if isinstance(s, list)}
    print("  tenants read: " + ", ".join(f"{p} {n}" for p, n in counted.items()))
    for line in note:
        print(f"  NOTE {line}")
    for line in block:
        print(f"  BLOCK {line}")
    if block:
        print(f"  {len(block)} difference(s) block the deployment: fix the tenant config, or change the class in "
              "scripts/impact/product-map/config.toml (reviewed)")
        return 1
    print("  tenant config check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
