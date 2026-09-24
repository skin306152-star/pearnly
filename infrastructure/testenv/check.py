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
        frappe.destroy()


if __name__ == "__main__":
    main()
