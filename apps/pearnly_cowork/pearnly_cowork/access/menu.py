"""Cowork 团队管理里逐人勾选的功能权限（纯数据，不依赖框架与 IO）。

用户 2026-09-26 定的清单：录入工作台、对账中心、库存盘点、工作协调、个人信息、LINE；
对账中心 2026-09-27 拆成银行对账 / 收入对账 / 销项税报告核查三项，可单独勾选。
做法照 ERP 的团队管理：一勾就是一个原生 Role，菜单显隐与服务端判据都认这个角色。

业务那四项直接用 Cowork 各域**已有**的操作角色（`access.scope`、`stocktake.access`、
`reconciliation.recon_access` 判的就是它们），不另造一套"菜单角色"再映射回去——
两套角色迟早对不上。个人信息与 LINE 放在「用户菜单」一组，与 ERP 相同。
"""

ADMIN_ROLE = "Cowork Admin"
BASE_ROLE = "Desk User"
LINE_ROLE = "Cowork LINE"
PROFILE_ROLE = "Cowork Profile"

# 键 → (功能码, 角色, 页面路由)。功能码为 None 的不看开通（没有可售卖的功能码）。
CATALOG = {
    "intake": ("intake", "Cowork Intake", "pearnly-intake"),
    "recon_bank": ("recon", "Cowork Bank Reconciliation", "pearnly-recon-bank"),
    "recon_income": ("recon", "Cowork Income Reconciliation", "pearnly-recon-income"),
    "recon_vat": ("recon", "Cowork VAT Check", "pearnly-recon-vat"),
    # 报税草稿（2026-10-13）：没有页面，入口是 CC 工具（`tax`），团队管理里显示角色名
    "tax": ("tax_drafts", "Cowork Tax Filing", None),
    "stocktake": ("stocktake", "Cowork Stocktake", "pearnly-stocktake"),
    "work": (None, "Cowork Work", "pearnly-work"),
    "profile": (None, PROFILE_ROLE, "user"),
}
# LINE 不是页面，是个人信息里那一块的开关；排在个人信息后面。
LINE_ENTRY = (None, LINE_ROLE, None)

# 站点上要由本 App 自己确保存在的角色：业务那几项的角色随各自 DocType 的权限行生成，
# 这几个没有 DocType 挂着，需要 `team.setup.sync_roles` 建。
OWN_ROLES = ("Cowork Work", PROFILE_ROLE, LINE_ROLE)


def roles() -> tuple:
    return tuple(spec[1] for spec in CATALOG.values()) + (LINE_ROLE,)


def offered(features, present) -> tuple:
    """能发给用户的角色：本租户买了的功能 + 站点上真实存在的角色，按目录顺序。"""
    features, present = set(features), set(present)
    entries = list(CATALOG.values()) + [LINE_ENTRY]
    return tuple(role for feature, role, _route in entries
                 if (feature is None or feature in features) and role in present)
