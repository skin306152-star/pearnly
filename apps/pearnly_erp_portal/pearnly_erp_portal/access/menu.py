"""ERP menu permissions, backed by native roles. No framework/IO in this catalog."""
# key: feature, legacy role, business capabilities, writable/readable doctypes.
# 按台记录（车辆按台）：开单选台、看台的菜单都读得到，写只走 `inventory.serials` 的过账
SERIALS = ("ERP Serial No", "ERP Serial Movement")
CATALOG = {
    "home": (None, "ERP Admin", (), (), ()),
    # 物料分类（11-01）：物料岗位与盘点岗位可在原生 Link 下拉里新建（`products.categories` 与控制器再按能力判：盘点岗只能建、
    # 不能改名 / 停用 / 删），其余业务岗位只读（见 setup.sync_roles 的 dependencies）
    "products": ("products", "ERP Products", ("products.view", "products.manage"),
                 ("ERP Product", "ERP Product Category"), SERIALS),
    "inventory": ("warehouses", "ERP Stock", ("inventory.read",), (), ("ERP Stock Balance", *SERIALS)),
    "stock-in": ("warehouses", "ERP Stock", ("stock.in",), ("ERP Stock Entry",), SERIALS),
    "stock-out": ("warehouses", "ERP Stock", ("stock.out",), ("ERP Stock Entry",), SERIALS),
    # 调拨（多仓闭环 P2）：单独一项，团队管理里可单独勾；只勾出库的人不能调拨
    "stock-transfer": ("warehouses", "ERP Stock", ("stock.transfer",), ("ERP Stock Entry",), SERIALS),
    # 物料调整（岳父 11-06，号 IA）：正负混录；历史盘点生成的 AD 单也在这一页
    "stock-adjust": ("warehouses", "ERP Stock", ("stock.adjust",), ("ERP Stock Entry",), SERIALS),
    "stock-card": ("stockcard", "ERP Stock", ("report.read", "report.write"), ("ERP Stock Opening",), ("ERP Stock Movement", *SERIALS)),
    "stock-count": ("stocktake", "ERP Stock", ("count.read", "count.write"),
                    ("ERP Stock Count", "ERP Stock Count Item", "ERP Stock Count Entry", "ERP Product Category"), SERIALS),
    "purchase": ("purchase", "ERP Purchase", ("purchase.read", "purchase.write"),
                 ("Purchase Document", "ERP Purchase Attachment", "ERP Purchase Payment", "ERP LINE Intake"),
                 ("ERP Supplier", "ERP Purchase Settings", "ERP Purchase Category", *SERIALS)),
    "suppliers": ("purchase", "ERP Purchase", ("suppliers.read", "suppliers.write", "tax.lookup"), ("ERP Supplier",), ()),
    "sales": ("sales", "ERP Sales", ("sales.read", "sales.write"),
              ("ERP Sales Document", "ERP Sales Attachment", "ERP LINE Intake"), ("ERP Customer", *SERIALS)),
    "cashier": ("cashier", "ERP Cashier", ("cashier.read", "cashier.write", "customers.read"), ("ERP Sale", "ERP Shift"),
                ("ERP Customer", *SERIALS)),
    "pos-receipts": ("cashier", "ERP Cashier", ("receipts.read", "receipts.write"), ("ERP Sale",), ("ERP Customer", "ERP Shift", *SERIALS)),
    "pos-shifts": ("cashier", "ERP Cashier", ("shifts.read",), (), ("ERP Shift", "ERP Sale")),
    "clients": ("clients", "ERP Sales", ("customers.read", "customers.manage", "tax.lookup"), ("ERP Customer",), ()),
    "profile": (None, "ERP Admin", (), (), ()),
    "team": (None, "ERP Admin", ("team.manage",), (), ()),
    "company": (None, "ERP Admin", ("company.read", "company.manage"), ("ERP Company", "ERP POS Terminal"), ()),
    "warehouses": ("warehouses", "ERP Stock", ("warehouses.read", "warehouses.manage"), ("ERP Warehouse",), ()),
    # 任务管理只读得到看板这几张表（且按看板过滤），写一律走 `work` 的专用接口；不带商品、库存等业务权限。
    "tasks": ("tasks", "ERP Tasks", ("tasks.use",), (),
              ("ERP Work Board", "ERP Work Card", "ERP Work Label", "ERP Work Activity", "ERP Work Notification",
               "ERP Work Column", "ERP Work Field", "ERP Work Mirror", "ERP Work Rule")),
}

# 不是业务单据岗位的菜单：只勾这些的人不因此看得到商品、库存。
NON_BUSINESS = ("home", "profile", "team", "tasks")


# 产品管理员（同 `rules.ADMIN_ROLE`）。它不含功能权限（rules.py），所以侧栏只在接口本身也认管理员的业务页上为它放行：
# 任务管理（`work.access.allowed` 放行 ERP 管理员）。其余业务页按岗位 / 旧功能角色判，与接口一致（主会话 11-09）。
ADMIN = "ERP Admin"
ADMIN_PAGES = ("tasks",)


def role(key):
    return "ERP Menu " + key


def page_roles(key):
    """这一页在侧栏 / 路由上放行的角色：本菜单岗位、旧功能角色；接口认管理员的页再加 ERP 管理员。
    hooks 的 `pearnly_navigation` permissions 用它，与接口判据（`rules.can` / `work.access`）同一口径。"""
    roles = [role(key), CATALOG[key][1]]
    if key in ADMIN_PAGES and ADMIN not in roles:
        roles.append(ADMIN)
    return roles


def roles():
    return tuple(role(key) for key in CATALOG)


def capabilities(held):
    selected = [key for key in CATALOG if role(key) in held]
    result = {cap for key in selected for cap in CATALOG[key][2]}
    if selected:
        result.add("company.read")
    if any(key not in NON_BUSINESS for key in selected):
        result.update(("products.view", "stock.read"))
    if any(key in selected for key in ("stock-in", "stock-out", "stock-transfer", "stock-adjust")):
        result.add("stock.write")
    return result


def route(key):
    return "user" if key == "profile" else "pearnly-" + key
