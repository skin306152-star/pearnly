"""给 ERP 站点补上"盘点"这个功能码。

功能码写在各站点自己的 `site_config.json`（`pearnly_features`），开通时由控制面写一次。
盘点是从旧项目 Cowork 那边迁过来的功能，早就开好的站点那份名单里没有它 ——
不补的话侧栏不会出现这一项（`navigation.visible_pages` 会把它筛掉）。
"""

import frappe
from frappe.installer import update_site_config

FEATURE = "stocktake"


def execute():
    if str(frappe.conf.get("pearnly_product") or "").strip() != "erp":
        return
    features = frappe.conf.get("pearnly_features")
    if isinstance(features, str):
        features = [part.strip() for part in features.split(",") if part.strip()]
    if not isinstance(features, (list, tuple)):
        return
    features = [str(code) for code in features]
    if FEATURE in features:
        return
    update_site_config("pearnly_features", features + [FEATURE])
    frappe.conf["pearnly_features"] = features + [FEATURE]
    print("[pearnly_erp_portal] 功能码补上：" + FEATURE)
