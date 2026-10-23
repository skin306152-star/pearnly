"""给已开通的 ERP 站点补上「任务管理」这个功能码（2026-10-23）。

功能码写在各站点自己的 `site_config.json`（`pearnly_features`），开通时由控制面写一次；早就开好的站点那份名单里没有它，
不补的话侧栏不出现这一项、服务端也一律拒绝（`work.access.allowed`）。同 `v1_1_enable_stocktake_feature`。
只补功能码：员工照旧要管理员在团队管理里逐人勾「任务管理」才用得上。
"""

import frappe
from frappe.installer import update_site_config

FEATURE = "tasks"


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
