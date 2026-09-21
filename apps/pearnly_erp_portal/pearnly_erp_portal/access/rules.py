"""ERP 门户的岗位与能力规则（纯逻辑，不依赖框架、IO 或上层流程）。

角色名与平台开通约定保持一致：`apps/pearnly_control/pearnly_control/features.py`
的 `PRODUCT_ADMIN_ROLE` 与 `ENFORCEMENT`。功能角色只在功能开通时才发给账号，
所以这里的映射同时就是「这个租户买了什么」的边界 —— 没开通就没有角色，
没有角色就既看不到入口，也取不到数据。
"""

# 产品管理员：能管本产品的员工账号，但**不含功能权限**（与开通侧一致）。
ADMIN_ROLE = "ERP Admin"
# 平台管理员：框架自有角色，用于开通、运维与测试。
SYSTEM_ADMIN_ROLE = "System Manager"
# 租户管理员进 Desk 的基础角色。
BASE_ROLE = "Desk User"

# 功能 → 角色。名字必须与开通侧 ENFORCEMENT 完全一致，否则开通发了角色、
# 这里认不出来，等于静默失效。
FEATURE_ROLES = {
    "purchase": "ERP Purchase",
}

# 能力 → 所需功能。能力码属于 ERP 自己，不复用旧平台的 pos.* / accounting.* /
# workorder.* 码（`product_manifest.py` 的 must_not 仍禁止那些）。
CAPABILITY_FEATURES = {
    "purchase.read": "purchase",
    "purchase.write": "purchase",
}

# 能看全部记录的角色：产品管理员与平台管理员。
# 其余（也就是被授予功能角色的员工）只看本人录入的记录 —— 这是旧线上团队成员的
# 既有口径（`erp-team-copy.ts`：成员只看到自己录入的记录，数据仍落老板的账套）。
ALL_RECORDS_ROLES = (ADMIN_ROLE, SYSTEM_ADMIN_ROLE)

ALL_RECORDS = "all"
OWN_RECORDS = "own"


def required_roles(capability: str) -> tuple[str, ...]:
    """某能力需要的角色。未知能力返回空元组，调用方据此一律拒绝（fail-closed）。"""
    feature = CAPABILITY_FEATURES.get(capability)
    role = FEATURE_ROLES.get(feature) if feature else None
    return (role,) if role else ()


def can(roles, capability: str) -> bool:
    return bool(set(required_roles(capability)) & set(roles))


def record_scope(roles) -> str:
    """返回 `all` 或 `own`，决定列表与汇总能看到谁的记录。"""
    return ALL_RECORDS if set(ALL_RECORDS_ROLES) & set(roles) else OWN_RECORDS
