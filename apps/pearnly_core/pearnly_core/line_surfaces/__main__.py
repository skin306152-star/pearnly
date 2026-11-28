"""在某个产品网页容器的 sites 目录里，对这个产品的全部站点跑一遍站外表面对照 / 同步 / 回退。

    python -m pearnly_core.line_surfaces check   --product dms        只读对照（演练、部署后回读）
    python -m pearnly_core.line_surfaces sync    --product dms        有漂移才发布，发布后回读（部署）
    python -m pearnly_core.line_surfaces restore --product dms --site ris.pearnly.com --surface rich_menu --before '<JSON>'

输出：每站一行 ``SITE <JSON>``，发布前 ``BEFORE <JSON>``（可原样交回 restore），最后 ``SUMMARY <JSON>``。
退出码：0 全部一致（跳过的照列）；1 有站点出错、发布后仍不一致，或 check 发现漂移；2 用法错。
"""
import argparse
import json
import os
import sys

import frappe

from pearnly_core import line_surfaces


def _sites(only):
    for site in sorted(os.listdir(".")):
        if os.path.isfile(os.path.join(site, "site_config.json")) and (not only or site in only):
            yield site


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pearnly_core.line_surfaces")
    parser.add_argument("mode", choices=("check", "sync", "restore"))
    parser.add_argument("--product", required=True, choices=("erp", "cowork", "dms"))
    parser.add_argument("--site", action="append", default=[])
    parser.add_argument("--surface", default="")
    parser.add_argument("--before", default="")
    args = parser.parse_args(argv)
    if args.mode == "restore" and not (len(args.site) == 1 and args.surface and args.before):
        parser.error("restore needs one --site, --surface and --before")

    summary = {"product": args.product, "mode": args.mode, "ok": [], "drift": [], "published": [],
               "skipped": {}, "failed": {}, "changes": {}}
    for site in _sites(args.site):
        try:
            frappe.init(site=site, sites_path=".")
            frappe.connect()
        except Exception as exc:
            summary["failed"][site] = "connect: " + repr(exc)[:200]
            continue
        try:
            if frappe.conf.get("pearnly_product") != args.product:
                continue
            if args.mode == "restore":
                rows = {args.surface: line_surfaces.restore_site(args.product, args.surface, json.loads(args.before))}
            elif args.mode == "sync":
                rows = line_surfaces.sync_site(args.product, emit=lambda text: print(text, flush=True))  # loop-query: 逐站——每个站点是自己的库，只能 init 进去查本站
            else:
                rows = line_surfaces.check_site(args.product)
            print("SITE " + json.dumps({"site": site, "surfaces": rows}, ensure_ascii=False, sort_keys=True), flush=True)
            for surface, row in rows.items():
                label = f"{site} {surface}"
                if row.get("changes"):
                    summary["changes"][label] = row["changes"]
                if "skip" in row:
                    summary["skipped"][label] = row["skip"]
                elif args.mode == "restore" or row.get("ok"):
                    summary["ok"].append(label)
                    if row.get("published"):
                        summary["published"].append(label)
                else:
                    summary["drift"].append(label)
            if not rows:
                # 三个产品都声明了富菜单：一条都没有说明声明丢了（hooks 改坏 / 被删），不能当跳过让回读静默通过
                summary["failed"][site] = f"no {line_surfaces.HOOK} declared for {args.product}"
        except Exception as exc:
            frappe.db.rollback()
            summary["failed"][site] = repr(exc)[:400]
        finally:
            frappe.destroy()
    print("SUMMARY " + json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
    return 1 if summary["failed"] or summary["drift"] else 0


if __name__ == "__main__":
    sys.exit(main())
