#!/usr/bin/env python3
"""测试环境读回：每个站点的产品、已装 App、Frappe 版本。

只读，用来在每次验证前后留下"这台机器上到底是什么版本"的证据。
"""
import os
from pathlib import Path

SITES = Path("/home/frappe/frappe-bench/sites")


def main():
    os.chdir(SITES)
    import frappe
    version = frappe.__version__
    for config in sorted(Path(".").glob("*/site_config.json")):
        site = config.parent.name
        frappe.init(site=site)
        frappe.connect()
        print(f"{site}\tproduct={frappe.conf.get('pearnly_product')}\t"
              f"apps={','.join(frappe.get_installed_apps())}\tfrappe={version}")
        # 数据形状：证明导入的是真实租户数据，而不是空站点。
        top = frappe.db.sql(
            "SELECT table_name, table_rows FROM information_schema.tables "
            "WHERE table_schema=%s AND table_rows > 0 ORDER BY table_rows DESC LIMIT 5",
            (frappe.conf.db_name,))
        print("    top tables: " + ", ".join(f"{name}={rows}" for name, rows in top))
        frappe.destroy()


if __name__ == "__main__":
    main()
