"""报税草稿（2026-10-13，功能码 ``tax_drafts``）对所有 Cowork 租户生效（用户 10-07：功能不按租户开关）。

- 功能码写在各站点 ``site_config.json`` 的 ``pearnly_features``（开通时控制面写一次）：早就开好的站点名单里没有它，
  补上（照 ERP `v1_1_enable_stocktake_feature`）；
- 角色「Cowork Tax Filing」确保存在，并发给已有「Cowork VAT Check」的启用用户（做销项核查的就是报税的人），
  谁也不因升级要管理员再勾一次；Cowork Admin 本来就能用。重复执行不重复加。
"""
import frappe
from frappe.installer import update_site_config

FEATURE = "tax_drafts"
ROLE = "Cowork Tax Filing"
FROM_ROLE = "Cowork VAT Check"


def execute():
    if str(frappe.conf.get("pearnly_product") or "").strip() != "cowork":
        return
    features = frappe.conf.get("pearnly_features")
    if isinstance(features, str):
        features = [part.strip() for part in features.split(",") if part.strip()]
    if isinstance(features, (list, tuple)):
        features = [str(code) for code in features]
        if FEATURE not in features:
            update_site_config("pearnly_features", features + [FEATURE])
            frappe.conf["pearnly_features"] = features + [FEATURE]
    from pearnly_core.provisioning import _ensure_role

    _ensure_role(ROLE)
    users = set(frappe.get_all("Has Role", filters={"role": FROM_ROLE, "parenttype": "User"}, pluck="parent"))
    for name in sorted(users - {"Administrator", "Guest"}):
        if not frappe.db.get_value("User", name, "enabled"):
            continue
        user = frappe.get_doc("User", name)
        user.flags.ignore_permissions = True
        user.add_roles(ROLE)
        frappe.clear_cache(user=name)
