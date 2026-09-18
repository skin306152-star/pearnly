# -*- coding: utf-8 -*-
"""把早期的**中文角色名**改名为英文（用户 2026-09-19：不许写死任何文案和语言）。

角色名是库里的记录，按 Frappe 惯例用英文（框架自己的 `System Manager`、
`Desk User` 也是英文），显示时由框架的 `__(role)` 翻成各语言。

为什么必须改名、不能"建一个新的、旧的留着"：
控制面的 `roles_payload` 现在是按新名字发角色的，`ensure_feature_roles` 把
"不在 want 里的角色"从用户身上收回。留着旧名字的话，用户身上那条会被收走、
再发一条新的 —— 看起来能用，但**旧角色还挂在 DocType 权限上**，越积越多。

改名用 `rename_doc`：它会把所有指向 Role 的 Link 一起更新（`Has Role.role`、
`DocPerm.role`），所以用户身上的角色和权限都跟着走，不需要重发。

幂等：名字已经是新的就直接跳过；旧新都存在（理论上不会）时不动，交给
`ensure_feature_roles` 按当前目录收敛。
"""

import frappe

# 用 `frappe.model.rename_doc.rename_doc`，不用 `frappe.rename_doc`：
# 后者是给 HTTP 用的壳（`frappe/__init__.py:804`），**不收** `ignore_permissions`，
# 传了会报 `TypeError` 而不是被忽略（实测踩过）。
from frappe.model.rename_doc import rename_doc

RENAMES = {
    "Cowork 录入": "Cowork Intake",
    "ERP 采购": "ERP Purchase",
    "DMS 身份证录入": "DMS ID Card Intake",
    "DMS 客户匹配": "DMS Customer Match",
    "DMS 操作员": "DMS Operator",
    "Cowork 管理员": "Cowork Admin",
    "ERP 管理员": "ERP Admin",
    "DMS 管理员": "DMS Admin",
}


def execute() -> None:
    renamed, skipped = [], []
    for old, new in RENAMES.items():
        if not frappe.db.exists("Role", old):
            continue
        if frappe.db.exists("Role", new):
            skipped.append("%s（%s 已存在）" % (old, new))
            continue
        rename_doc("Role", old, new, force=True, ignore_permissions=True, show_alert=False)
        renamed.append("%s → %s" % (old, new))

    if renamed or skipped:
        frappe.logger().info("pearnly_core 角色改名：%s%s", renamed, skipped)
    frappe.clear_cache()
