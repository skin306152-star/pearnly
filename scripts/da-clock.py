"""数字会计员压缩时间试点的模拟日子（只给测试租户）：设、撤、查 ``pearnly_da_today``（见 ``pearnly_cowork.push.automation.clock``，
第 3 期设计第 17 条第 3 点）。

在站点所在的 bench 里跑（测试机实验室栈的 backend 容器里，与其它站点脚本相同）：

    env/bin/python /candidate/scripts/da-clock.py --site testcowork.pearnly.com set 2025-09-16
    env/bin/python /candidate/scripts/da-clock.py --site testcowork.pearnly.com off
    env/bin/python /candidate/scripts/da-clock.py --site testcowork.pearnly.com status

站点名不在测试租户名单（``pearnly_core.execution.replay.TEST_SITES``）里直接拒绝（运行时也不认）。改的是站点配置，各进程最多 60 秒内
按新值走（Frappe 站点配置缓存 60 秒）：换日子后等一分钟再触发判定 / 回读。设了以后，自动推前的判定按这一天判单据日子（上传日也当作
这一天），升降档的「自何时」记这一天；拿来和库里真实时刻比的地方照真实时间（见 clock 模块文件头）。试点做完一定 ``off``。
"""
import argparse
import datetime as dt
import json
import os
import sys

KEY = "pearnly_da_today"


def main(argv=None):
    parser = argparse.ArgumentParser(prog="da-clock")
    parser.add_argument("--site", required=True)
    parser.add_argument("action", choices=("set", "off", "status"))
    parser.add_argument("day", nargs="?", help="YYYY-MM-DD（set 时必填）")
    args = parser.parse_args(argv)

    import frappe
    from frappe.installer import update_site_config
    from pearnly_core.execution.replay import TEST_SITES

    if not TEST_SITES.fullmatch(args.site):
        print(f"refused: {args.site} is not a test tenant", file=sys.stderr)
        return 2
    if args.action == "set":
        try:
            day = dt.date.fromisoformat(str(args.day or ""))
        except ValueError:
            print("refused: give the day as YYYY-MM-DD", file=sys.stderr)
            return 2
    os.chdir(os.path.join(os.environ.get("FRAPPE_BENCH", "/home/frappe/frappe-bench"), "sites"))
    frappe.init(site=args.site)
    try:
        if args.action == "set":
            update_site_config(KEY, day.isoformat())
        elif args.action == "off":
            update_site_config(KEY, "None")  # Frappe：值为 "None" 时删除这个键
        frappe.destroy()
        frappe.init(site=args.site)
        from pearnly_cowork.push.automation import clock

        print(json.dumps({"site": args.site, KEY: frappe.get_site_config().get(KEY),
                          "judging_day": clock.today().isoformat(), "simulated": clock.simulated() is not None},
                         ensure_ascii=False))
    finally:
        frappe.destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
