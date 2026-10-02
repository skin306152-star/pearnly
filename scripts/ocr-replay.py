"""回放压测开关（只给测试租户）：打开、关掉、查看 ``pearnly_ocr_replay``（见 ``pearnly_core.execution.replay``）。

在站点所在的 bench 里跑（生产机上用一次性容器，与其它站点脚本相同）：

    env/bin/python /candidate/scripts/ocr-replay.py --site testcowork.pearnly.com on [--profile normal|peak]
                   [--median 秒] [--p95 秒]
    env/bin/python /candidate/scripts/ocr-replay.py --site testcowork.pearnly.com off
    env/bin/python /candidate/scripts/ocr-replay.py --site testcowork.pearnly.com status

站点名不在测试租户名单里直接拒绝（运行时也会拒绝）。改的是站点配置，各进程最多 60 秒内按新值走（Frappe 站点配置缓存 60 秒）。
开关请在这个租户没有在途识别时切：关掉后已开始回放的那几份照样回放完（不会转去真识别）；打开时已交给 Cloud Run 的那几份会改成回放。
"""
import argparse
import json
import os
import sys

KEY = "pearnly_ocr_replay"


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ocr-replay")
    parser.add_argument("--site", required=True)
    parser.add_argument("action", choices=("on", "off", "status"))
    parser.add_argument("--profile", choices=("normal", "peak"), default="normal")
    parser.add_argument("--median", type=float)
    parser.add_argument("--p95", type=float)
    args = parser.parse_args(argv)

    import frappe
    from frappe.installer import update_site_config
    from pearnly_core.execution import replay

    if not replay.TEST_SITES.fullmatch(args.site):
        print(f"refused: {args.site} is not a test tenant", file=sys.stderr)
        return 2
    os.chdir(os.path.join(os.environ.get("FRAPPE_BENCH", "/home/frappe/frappe-bench"), "sites"))
    frappe.init(site=args.site)
    try:
        if args.action == "on":
            value = {"enabled": 1, "profile": args.profile}
            if args.median:
                value["median"] = args.median
            if args.p95:
                value["p95"] = args.p95
            update_site_config(KEY, value)
        elif args.action == "off":
            update_site_config(KEY, "None")  # Frappe：值为 "None" 时删除这个键
        frappe.destroy()
        frappe.init(site=args.site)
        current = frappe.get_site_config().get(KEY)
        print(json.dumps({"site": args.site, KEY: current, "active": replay.settings()}, ensure_ascii=False))
    finally:
        frappe.destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
