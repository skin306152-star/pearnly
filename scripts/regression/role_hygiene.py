"""账号角色卫生检查（回归基线 B，只读）：每个业务站点里
- 除 Administrator 外不得有带 System Manager 的账号（框架超管，能开全部框架页面、调全部框架接口；开户本应收走）；
- 每个启用的业务账号（System User）都得有 Desk User（没有就进不了产品页面）。

在站点网页容器里跑，扫 sites/ 下全部站点（跳过不是业务站点的：site_config 没有 pearnly_product），只读，不改任何东西：

    ../env/bin/python /workspace/scripts/regression/role_hygiene.py [站点…]

输出每站一行 JSON；有问题的站点 ok=false，账号只打前三个字符。
"""
import json
import sys
from pathlib import Path

import frappe

SKIP_USERS = ("Administrator", "Guest")


def check(site):
    frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
    frappe.connect()
    try:
        product = frappe.conf.get("pearnly_product") or ""
        if not product:
            return None
        users = frappe.db.sql("""select name from tabUser where enabled=1 and user_type='System User'
            and name not in %(skip)s""", {"skip": SKIP_USERS}, pluck=True)
        roles = {}
        for user, role in frappe.db.sql("""select parent, role from `tabHas Role`
                where parenttype='User' and parent in %(users)s""", {"users": tuple(users) or ("",)}):
            roles.setdefault(user, set()).add(role)
        managers = sorted(u for u in users if "System Manager" in roles.get(u, set()))
        no_desk = sorted(u for u in users if "Desk User" not in roles.get(u, set()))
        mask = lambda names: [n[:3] + "***" for n in names]  # noqa: E731
        return {"site": site, "product": product, "users": len(users), "system_manager": mask(managers),
                "missing_desk_user": mask(no_desk), "ok": not managers and not no_desk}
    finally:
        frappe.destroy()


def main(argv):
    root = Path("/home/frappe/frappe-bench/sites")
    sites = argv or sorted(p.parent.name for p in root.glob("*/site_config.json"))
    for site in sites:
        result = check(site)
        if result:
            print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
