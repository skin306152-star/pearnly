# -*- coding: utf-8 -*-
"""把控制面需要的前端常量塞进 desk 的 boot。

前端要用"基础域名"来即时拼出 `前缀.pearnly.com` 给用户看。写死在 JS 里的话，
换域名时会出现前后端两套规则；所以从服务端同一个来源给出去。
"""

import frappe


def boot_session(bootinfo):
    try:
        from pearnly_control import features as features_mod
        from pearnly_control.domains import base_domain

        bootinfo.pearnly_base_domain = base_domain()
        bootinfo.pearnly_target_domains = {
            name: base_domain(name) for name in frappe.get_all("Pearnly Target", pluck="name")
        }
        bootinfo.pearnly_products = {
            p: features_mod.PRODUCT_LABELS.get(p, p) for p in features_mod.PRODUCTS
        }
    except Exception:
        # boot 失败会让整个 desk 打不开，这里绝不往外抛
        frappe.log_error(title="pearnly_control.boot_session 失败")
