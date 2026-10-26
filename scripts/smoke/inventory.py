#!/usr/bin/env python3
"""页面入口盘点（回归基线用）：按「产品 × 角色账号」建一次 Desk 会话，用 inventory.mjs 打开登记表里该产品的全部页面并顺着站内链接
走一遍，逐页记下用户实际看得到的入口（页头按钮、菜单项、侧栏 / 导航、顶栏下拉、链接、快捷键），输出 JSONL。

    # 实验栈（经 Mac 侧端口转发打开，不切域名；先 `ssh -N -L 8181:… lenovo-office-lan`）
    python3 scripts/smoke/inventory.py --target lab --product cowork --user owner=Administrator --user member=m@x --out /tmp/inv.jsonl
    # 线上测试租户只读（只用 Administrator，不给真实成员建会话）
    python3 scripts/smoke/inventory.py --target production --product cowork --user admin=Administrator --out /tmp/inv.jsonl

只读：探针只读 DOM，不点按钮；会话跑完即删。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("smoke_run", HERE / "run.py")
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)

LAB_PORTS = {"cowork": 8181, "erp": 8182, "dms": 8183}
# 登记表之外、每个产品都要看的页：桌面首页、个人页
EXTRA = ["/desk", "/desk/user"]
# 框架自带、我们没有承接的常见路由：直接输地址能不能打开（应是无权限页 / 跳回）也是基线的一部分
FRAMEWORK = ["/desk/role", "/desk/user-permission", "/desk/todo", "/desk/file", "/desk/workspace", "/desk/system-settings",
             "/desk/error-log", "/desk/notification-log", "/desk/activity-log", "/desk/report", "/desk/print-format",
             "/desk/email-account", "/desk/contact", "/desk/event", "/desk/note", "/desk/dashboard-view", "/desk/website",
             "/desk/data-import", "/desk/permission-manager", "/desk/user-profile"]


def pages(product: str) -> list[str]:
    _parts, entries, _errors = run.impact.load()
    urls = [entry.url for entry in entries.values()
            if getattr(entry, "url", "") and (entry.site or entry.product.lower()) == product and entry.url.startswith("/desk")]
    return list(dict.fromkeys(EXTRA + urls + FRAMEWORK))


class Local:
    """本机回归实验室：容器里跑 session.py；脚本经 /workspace/scripts 挂进去。"""
    container = os.environ.get("REGRESSION_LAB_CONTAINER", "pearnly-regression-lab-bench-1")

    def run(self, product, script, *args, timeout=600):
        rel = Path(script).resolve().relative_to(HERE.parent)
        return subprocess.run(["docker", "exec", "-w", "/home/frappe/frappe-bench/sites", self.container,
                               "../env/bin/python", f"/workspace/scripts/{rel}", *args],
                              capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL).stdout

    def doctypes(self, product, site):
        app = {"erp": "pearnly_erp_portal", "cowork": "pearnly_cowork", "dms": "pearnly_dms"}[product]
        code = ("import frappe,json; frappe.init(site=%r); frappe.connect(); "
                "mods=frappe.get_all('Module Def', filters={'app_name': ['in', [%r, 'pearnly_core']]}, pluck='name'); "
                "print(json.dumps([frappe.scrub(d).replace('_','-') for d in frappe.get_all('DocType', filters={'module':['in',mods],'istable':0}, pluck='name')]))") % (site, app)
        out = subprocess.run(["docker", "exec", "-w", "/home/frappe/frappe-bench/sites", self.container, "../env/bin/python", "-c", code],
                             capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout
        return json.loads(out.strip().splitlines()[-1])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", choices=("lab", "production", "local"), default="lab",
                        help="local = 本机回归实验室（infrastructure/compose/regression-lab.yml，站点 <产品>.regression.localhost）")
    parser.add_argument("--doctypes", action="store_true", help="另把本产品全部 DocType 的列表页加进来（直达地址也是入口）")
    parser.add_argument("--product", required=True, choices=sorted(LAB_PORTS))
    parser.add_argument("--user", action="append", required=True, help="角色名=账号，可多个")
    parser.add_argument("--max-pages", type=int, default=80)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if args.target == "production" and any(spec.split("=", 1)[1] != "Administrator" for spec in args.user):
        print("线上只用 Administrator（不给真实成员建会话，免得挤掉他们的登录）", file=sys.stderr)
        return 2
    if args.target == "local":
        site = f"{args.product}.regression.localhost"
        remote = Local()
        base = f"http://{site}:{os.environ.get('REGRESSION_LAB_PORT', '18771')}"
    else:
        site = run.SITES[args.product]
        remote = run.Remote(args.target)
        base = f"https://{site}" if args.target == "production" else f"http://{site}:{LAB_PORTS[args.product]}"
    targets = pages(args.product)
    if args.doctypes:
        targets += [f"/desk/{slug}" for slug in remote.doctypes(args.product, site)]
    with open(args.out, "w", encoding="utf-8") as out:
        for spec_ in args.user:
            role, user = spec_.split("=", 1)
            output = remote.run(args.product, HERE / "session.py", site, user)
            sid = next((line.split()[1] for line in output.splitlines() if line.startswith("SID ")), "")
            if not sid:
                print(f"建会话失败（{site} {user}）：{output[-300:]}", file=sys.stderr)
                return 2
            try:
                job = {"base": base, "pages": targets + ([f"/desk/user/{user}"] if user != "Administrator" else ["/desk/user/Administrator"]), "cookie_domain": site, "sid": sid, "secure": args.target == "production",
                       "crawl": True, "max_pages": args.max_pages}
                if args.target == "lab":
                    os.environ.setdefault("SMOKE_CHROME_RULES", f"MAP {site} 127.0.0.1")
                proc = subprocess.run(["node", str(HERE / "inventory.mjs"), json.dumps(job)], capture_output=True, text=True,
                                      timeout=60 * 60, env=os.environ)
                count = 0
                for line in proc.stdout.splitlines():
                    if line.startswith("{"):
                        row = json.loads(line)
                        row.update({"product": args.product, "role": role, "account": user, "target": args.target})
                        out.write(json.dumps(row, ensure_ascii=False) + "\n")
                        count += 1
                print(f"{args.product} {role}（{user}）：{count} 页", file=sys.stderr)
            finally:
                remote.run(args.product, HERE / "session.py", site, user, "drop", sid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
