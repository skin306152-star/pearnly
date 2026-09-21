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

# 功能码 → 岗位角色。键是**控制面目录里的功能码**（`pearnly_control/features.py`
# 的 CATALOG["erp"]），名字必须与开通侧 ENFORCEMENT 完全一致，否则开通发了角色、
# 这里认不出来，等于静默失效。
#
# 多个功能码可以共用一个岗位（库存与收发存报表同属库存岗位），这里同时就是
# 新平台的**岗位目录**：员工能拿到的就是这些功能角色。
# 平台角色（System Manager）与产品管理员角色（ERP Admin）永不外发，见 assignable_roles。
FEATURE_ROLES = {
    "products": "ERP Products",
    "warehouses": "ERP Stock",
    "stockcard": "ERP Stock",
    "purchase": "ERP Purchase",
    "sales": "ERP Sales",
    "clients": "ERP Sales",
    "cashier": "ERP Cashier",
}

# 能力 → 允许的功能码（可多个）。能力码属于 ERP 自己，不复用旧平台的
# pos.* / accounting.* / workorder.* 码（`product_manifest.py` 的 must_not 仍禁止那些）。
#
# 为什么是"多个功能码"：库存岗位做入库/出库必须先看得到商品，所以 `products.view`
# 同时认商品岗位与库存岗位；但**管**商品主数据仍只给商品岗位（库存岗位只读）。
CAPABILITY_FEATURES = {
    "purchase.read": ("purchase",),
    "purchase.write": ("purchase",),
    "products.view": ("products", "warehouses"),
    "products.manage": ("products",),
}

# 只有管理员能用的能力（不经功能角色发放）。
ADMIN_CAPABILITIES = ("team.manage",)

# 成本可见的岗位。
#
# 旧线上成本是单独一个字段级权限（`field.cost.view`），不在默认角色里 —— 员工
# 看不到进货价。新平台沿用同一口径：默认只有老板（管理员）与库存岗位能看到
# 参考成本/均价，其余岗位取到的成本字段一律为空，不靠前端隐藏。
COST_ROLES = (ADMIN_ROLE, FEATURE_ROLES["warehouses"])

# 能看全部记录的角色：产品管理员与平台管理员。
# 其余（也就是被授予功能角色的员工）只看本人录入的记录 —— 这是旧线上团队成员的
# 既有口径（`erp-team-copy.ts`：成员只看到自己录入的记录，数据仍落老板的账套）。
ALL_RECORDS_ROLES = (ADMIN_ROLE, SYSTEM_ADMIN_ROLE)

ALL_RECORDS = "all"
OWN_RECORDS = "own"


def required_roles(capability: str) -> tuple[str, ...]:
    """某能力需要的角色。未知能力返回空元组，调用方据此一律拒绝（fail-closed）。"""
    if capability in ADMIN_CAPABILITIES:
        return (ADMIN_ROLE,)
    roles = [FEATURE_ROLES.get(code) for code in CAPABILITY_FEATURES.get(capability, ())]
    return tuple(role for role in roles if role)


def can(roles, capability: str) -> bool:
    return bool(set(required_roles(capability)) & set(roles))


def record_scope(roles) -> str:
    """返回 `all` 或 `own`，决定列表与汇总能看到谁的记录。"""
    return ALL_RECORDS if set(ALL_RECORDS_ROLES) & set(roles) else OWN_RECORDS


def cost_visible(roles) -> bool:
    return bool(set(COST_ROLES) & set(roles))


def assignable_roles(available) -> tuple[str, ...]:
    """可以发给员工的岗位角色 = 本站点上真实存在的那些功能角色。

    没开通的功能不会在站点上建角色，因此这里天然只列出"买了的岗位"；
    反过来，不能把站点上不存在的角色发给员工（那等于发了一把空钥匙）。
    """
    present = set(available)
    # 去重但保持目录顺序：库存与收发存报表是同一个岗位，只该出现一次。
    ordered = dict.fromkeys(FEATURE_ROLES.values())
    return tuple(role for role in ordered if role in present)


def is_assignable(role: str, available) -> bool:
    return role in set(assignable_roles(available))
