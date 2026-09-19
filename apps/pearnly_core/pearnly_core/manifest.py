# -*- coding: utf-8 -*-
"""产品清单（ProductManifest）的加载与自检。

合同见 docs/p1/04-model-contracts.md 第四节：
每个产品一份清单，是"这个产品允许做什么"的唯一来源；**运行时不得自报扩大权限**。

每个产品 App 在自己的 `product_manifest.py` 里给出 `MANIFEST` 字典，本模块负责
汇总、自检，并提供给页面/接口使用。
"""

import frappe
from frappe import _

REQUIRED_KEYS = ("product", "version", "apps", "entry_prefixes", "modules", "features", "must_not")


def load_all() -> dict[str, dict]:
    """收集所有已安装 App 的产品清单。"""
    found = {}
    for app in frappe.get_installed_apps():
        try:
            mod = frappe.get_module(f"{app}.product_manifest")
        except Exception:
            continue
        manifest = getattr(mod, "MANIFEST", None)
        if manifest:
            found[manifest["product"]] = manifest
    return found


def current() -> dict:
    """当前站点的产品清单。

    规则：一个业务站点应当**恰好**对应一个产品；控制站点用 control 清单。
    对不上就报错——宁可启动即失败，也不要跑到一半才发现装错了。
    """
    found = load_all()
    if not found:
        frappe.throw(_("This site has no product manifest installed; it cannot run as a business site."))
    if len(found) > 1:
        frappe.throw(
            _("This site has more than one product manifest installed: {0}. One deployment can host only one product.").format(
                ", ".join(sorted(found))
            )
        )
    return next(iter(found.values()))


def check() -> list[str]:
    """返回清单问题列表，空表示健康。"""
    problems: list[str] = []
    for product, manifest in load_all().items():
        # 注意：这里判断"字段是否存在"，不是"是否非空"。
        # modules 为空列表是合法值（DMS 与控制面本来就没有模块开关），
        # 早期用 bool 判断会把它们误报成缺字段。
        missing = [k for k in REQUIRED_KEYS if k not in manifest]
        if missing:
            problems.append(_("{0}: missing field {1}").format(product, missing))
        for key in ("product", "version"):
            if not manifest.get(key):
                problems.append(_("{0}: {1} cannot be empty").format(product, key))
        for app in manifest.get("apps", []):
            if app not in frappe.get_installed_apps():
                problems.append(
                    _("{0}: the manifest requires {1}, but this site does not have it").format(
                        product, app
                    )
                )
        for prefix in manifest.get("entry_prefixes", []):
            if not prefix.startswith("/"):
                problems.append(
                    _("{0}: entry prefixes must start with /: {1}").format(product, prefix)
                )
    return problems


@frappe.whitelist()
def describe():
    """给前端用：当前站点的产品清单。"""
    return current()
