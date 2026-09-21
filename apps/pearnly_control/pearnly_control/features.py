# -*- coding: utf-8 -*-
"""功能模版目录：控制面按产品可勾选的功能清单。

为什么目录写在控制面、而不是直接读各产品的 `product_manifest.py`：
控制面站点**只装了 frappe + pearnly_core + pearnly_control**，三个产品的 App
并不装在它上面，所以 import 不到它们的清单。控制面必须自己知道"在卖什么"。

这份目录是各产品清单的**镜像**，对应关系写在下面每条注释里；产品清单改了，
这里要跟着改。`sync_features()` 在每次 migrate 后把目录同步进
`Pearnly Feature` 表，作为勾选时的候选项。

用户 2026-09-17 的要求：每个产品都要有**首页**，首页就是现在的订阅与计费系统，
所以三个产品都带 `home_subscription`。
"""

import frappe

# 顺序就是这个顺序在表单里出现的顺序
PRODUCTS = ("cowork", "erp", "dms")

PRODUCT_LABELS = {"cowork": "Cowork", "erp": "ERP", "dms": "DMS"}

# 三个产品共有的首页（= 订阅与计费）
# 文案一律写**英文**：渲染时会走 __()，中文/泰文/日文由翻译包给（见
# docs/i18n.md）。msgid 写中文 = 换语言时永远是中文。
_HOME = {
    "code": "home_subscription",
    "label": "Home (Subscription & Billing)",
    "default_on": True,
    "description": "Product home: current plan, usage and billing",
}

CATALOG = {
    "cowork": [
        _HOME,
        # 以下逐条对应 apps/pearnly_cowork/pearnly_cowork/product_manifest.py 的 features
        {"code": "intake", "label": "Intake Workbench", "default_on": True,
         "description": "Upload receipts and sheets, extract fields, review each item, then push"},
        {"code": "ocr_history", "label": "Recognition Records", "default_on": True,
         "description": "Batches and results of every recognition run"},
        {"code": "push_log", "label": "Push Log", "default_on": True,
         "description": "How each push to the external ERP went"},
        {"code": "recon", "label": "Reconciliation", "default_on": True,
         "description": "Reconcile documents against the ledger"},
        {"code": "master_data", "label": "Master Data", "default_on": True,
         "description": "Customers, suppliers, accounts and other reference data"},
        {"code": "stocktake", "label": "Stock Count", "default_on": False,
         "description": "Count tasks and variance records"},
        {"code": "boards", "label": "Collaboration Boards (WeKan)", "default_on": False,
         "description": "A board instance is created only when this is on"},
        {"code": "companion", "label": "Companion", "default_on": False,
         "description": "Windows app on the merchant's accounting PC: installer, pairing, outbound pull"},
        {"code": "express_guide", "label": "Express Push Guide", "default_on": False,
         "description": "Install, pair, upgrade and uninstall guide for Companion"},
    ],
    "erp": [
        _HOME,
        # 对应 apps/pearnly_erp_portal/pearnly_erp_portal/product_manifest.py
        {"code": "products", "label": "Products", "default_on": True,
         "description": "Product master data"},
        {"code": "warehouses", "label": "Stock In / Out", "default_on": True,
         "description": "Warehouses, stock-in and stock-out documents"},
        {"code": "stockcard", "label": "Stock Card Report", "default_on": True,
         "description": "Stock in/out summary (kept item, see the ERP manifest)"},
        {"code": "purchase", "label": "Purchasing (Records / Suppliers / Settings)", "default_on": True,
         "description": "Purchase records, suppliers and purchasing settings"},
        {"code": "sales", "label": "Sales", "default_on": True,
         "description": "Sales documents and customer dealings"},
        {"code": "clients", "label": "Customers", "default_on": True,
         "description": "Customer master data"},
        # 用户 2026-09-21 定：收银含在 ERP 里，不作为单独购买的功能。
        # 开通不分功能模版（选了产品就把该产品功能全开），所以列进目录即等于送给
        # 每个 ERP 租户；租户管理员会拿到这个角色，团队管理里也就勾得到「收银员」。
        {"code": "cashier", "label": "Cashier (POS)", "default_on": True,
         "description": "Cashier counter, shifts and the cashier's own sales"},
        {"code": "erp_push", "label": "External ERP Push", "default_on": False,
         "description": "Push to the external ERP; the push log is the only source of status"},
    ],
    "dms": [
        _HOME,
        # 对应 apps/pearnly_dms/pearnly_dms/product_manifest.py
        {"code": "id_card_ocr", "label": "ID Card Intake", "default_on": True,
         "description": "Read the ID card, match it against the DMS customer base, then confirm"},
        {"code": "customer_match", "label": "Customer Match", "default_on": True,
         "description": "Same ID number means the same customer; avoids duplicate records"},
        {"code": "push_record", "label": "Push Records", "default_on": True,
         "description": "The result of each push"},
        {"code": "roster", "label": "Operator Roster", "default_on": True,
         "description": "Only the owner can manage it"},
        {"code": "quota", "label": "Plan & Balance", "default_on": True,
         "description": "DMS usage and balance"},
    ],
}

FEATURE = "Pearnly Feature"

# ── 权限落地：功能 → 角色 → DocType ────────────────────────────────────────
#
# 光把功能"记在开单上"是没有约束力的 —— 用户要能感到差别，必须变成**权限**。
# 做法：每个功能对应一个角色，角色拿到它那个 DocType 的读写权限；开通时按勾选
# 把角色发给租户管理员。没勾的功能 → 角色没发 → 那个 DocType 访问不了。
#
# 只列**有实际可挡对象**的功能。没有 DocType 的功能（例如首页、套餐与余额）
# 目前没有可挡的东西，就不在这里假装有权限 —— 等产品界面做出来再补。
ENFORCEMENT = {
    ("cowork", "intake"): ("Cowork Intake", ["Intake Record"]),
    ("erp", "purchase"): ("ERP Purchase", ["Purchase Document", "Purchase Line",
                                           "ERP Supplier", "ERP Purchase Settings"]),
    ("erp", "products"): ("ERP Products", ["ERP Product", "ERP Product Unit"]),
    # 库存与收发存报表是同一个岗位（ERP Stock）。它要看得见商品才能做入库/出库，
    # 但商品主数据只读（权限覆盖见 READ_ONLY）。
    ("erp", "warehouses"): ("ERP Stock", ["ERP Product"]),
    ("erp", "stockcard"): ("ERP Stock", []),
    ("erp", "sales"): ("ERP Sales", []),
    ("erp", "clients"): ("ERP Sales", []),
    # 空 DocType 列表 = 只建角色、暂不授权（表在后续批次落地）。岗位要先于表存在，
    # 否则团队管理里勾不到这个岗位。
    ("erp", "cashier"): ("ERP Cashier", []),
    ("dms", "id_card_ocr"): ("DMS ID Card Intake", ["DMS ID Card Intake"]),
    ("dms", "customer_match"): ("DMS Customer Match", ["DMS Customer Link"]),
    ("dms", "roster"): ("DMS Operator", ["DMS Operator"]),
}

# 产品管理员角色：能管本产品的员工账号，但**不含功能权限** ——
# 功能权限一律来自上面的功能角色，这样"没买的功能"对管理员同样是关着的。
#
# 角色名是**库里的记录**，按 Frappe 惯例用英文（框架自己的角色也是英文），
# 界面上由 `__(role)` 翻（`frappe/public/js/frappe/roles_editor.js:45` 实测会翻）。
# 改这里的名字必须同时改：
#   · apps/pearnly_core/pearnly_core/billing_www.py 的 ADMIN_ROLES（按名字判权限）
#   · apps/pearnly_core/pearnly_core/patches/ 里把老站点已有角色改名的那个补丁
PRODUCT_ADMIN_ROLE = {
    "cowork": "Cowork Admin",
    "erp": "ERP Admin",
    "dms": "DMS Admin",
}

# 租户管理员必须有的基础角色（否则连 Desk 都进不去）
BASE_ROLES = ["Desk User"]


def feature_name(product: str, code: str) -> str:
    """功能项的主键。与 `Pearnly Feature` 的 autoname 规则一致。"""
    return f"{product}-{code}"


def all_codes(product: str) -> set[str]:
    return {f["code"] for f in CATALOG.get(product, [])}


def codes_of(product: str) -> list[str]:
    """该产品的**全部**功能代码，顺序与目录一致。

    用户 2026-09-18 定：开通**不分功能模版**了 —— 选了哪个产品，就把那个产品的
    功能全开。所以这里给的是全集，调用方不再需要"勾选项"这个概念。
    """
    return [f["code"] for f in CATALOG.get(product, [])]


def counts() -> dict:
    """各产品的功能项数。给界面显示与验证脚本读，避免两边各写一份数字。"""
    return {p: len(CATALOG.get(p, [])) for p in PRODUCTS}


def enforcement_of(product: str, code: str) -> tuple[str, list[str]]:
    """该功能对应的 (角色名, DocType 列表)。没配就是没有可挡对象。"""
    role, doctypes = ENFORCEMENT.get((product, code), ("", []))
    return role, list(doctypes)


# 按岗位**只读**的表。
#
# 为什么需要：DMS 的身份证录入作业在 LINE 上完成（用户 2026-09-17 定："DMS 身份证
# 录入网页端不要了……网页只负责绑定第三方 dms，只取消网页前端的录入入口"）。
# 所以这张表在网页端只能看、不能新建 —— Frappe 原生就是按 `create` 权限显示"新增"
# 按钮的，禁掉它就等于取消了网页端的录入入口，不需要改前端、也不需要另造开关。
#
# 这**不影响将来的 LINE 录入**：webhook 是 `allow_guest` 的独立通道，走的不是这个
# 角色（今天 LINE 侧还只做绑定，没有录入分支）。
#
# ERP 同样用得上：库存岗位做入库/出库要**看得到**商品，但商品主数据归商品岗位，
# 所以 ERP Stock 在 ERP Product 上只读。键带上角色，避免把同一个 DocType 上
# 别的岗位（ERP Products）也一起降成只读。
READ_ONLY: set[tuple[str, str, str]] = {
    ("dms", "DMS ID Card Intake", "DMS ID Card Intake"),
    ("erp", "ERP Product", "ERP Stock"),
}
READ_ONLY_PERMS = {"read": 1, "report": 1}


def perms_override(product: str, doctype: str, role: str = "") -> dict | None:
    """该表是否要用"只读"覆盖默认的功能权限；None = 用默认（可读可写可建）。"""
    if (product, doctype, role) in READ_ONLY:
        return dict(READ_ONLY_PERMS)
    return None


def roles_payload(product: str, codes: list[str]) -> dict:
    """把"勾了哪些功能"翻成站点侧要建的角色与权限。

    额外带上产品管理员角色与基础角色：租户管理员**不再给 System Manager**
    （那是框架超管，能改系统设置、装应用），只给"产品管理员 + 买了的功能角色"。
    """
    roles = []
    seen_roles: set[str] = set()
    for code in codes or []:
        role, doctypes = enforcement_of(product, code)
        # 有角色就进 payload：即使暂时没有可挡的表，角色也要建出来（岗位先于表存在）。
        if role:
            # 多个功能码可以共用一个岗位（库存与收发存报表都是 ERP Stock）。
            # 合并成一条，权限表取并集，避免清单里出现重复岗位。
            if role in seen_roles:
                for entry in roles:
                    if entry["role"] == role:
                        entry["doctypes"] = sorted(set(entry["doctypes"]) | set(doctypes))
                continue
            seen_roles.add(role)
            entry = {"role": role, "doctypes": doctypes, "feature": code}
            overrides = {}
            for doctype in doctypes:
                perms = perms_override(product, doctype, role)
                if perms is not None:
                    overrides[doctype] = perms
            if overrides:
                entry["perms_by_doctype"] = overrides
            roles.append(entry)
    return {
        "product": product,
        "admin_role": PRODUCT_ADMIN_ROLE.get(product, ""),
        "base_roles": BASE_ROLES,
        "roles": roles,
    }


def workspace_payload(product: str, codes: list[str], title: str = "") -> dict:
    """该租户的工作区：只放**他买了的**功能对应的入口。

    为什么要建这个：租户管理员不再有 System Manager（那是框架超管），
    于是 Frappe 自带那些工作区对他不可见 —— 打开 Desk 会是**一片空白**（实测）。
    产品的界面还没做，所以先给一个"只列他买了什么"的工作区，既有落点，
    也让他一眼看出自己买了哪些功能。
    """
    entries = []
    seen = set()
    for code in codes or []:
        _role, doctypes = enforcement_of(product, code)
        for doctype in doctypes:
            if doctype in seen:
                continue
            seen.add(doctype)
            # 入口标题：先看有没有按"网页端职责"起的名字，没有才用功能名。
            # DMS 那张表在网页端是「推送记录」而不是「身份证录入」—— 名字本身
            # 就在告诉租户"这里不是录入的地方，录入在 LINE 里做"。
            label = WORKSPACE_LABEL.get((product, doctype)) or next(
                (f["label"] for f in CATALOG.get(product, []) if f["code"] == code), code
            )
            entries.append({"doctype": doctype, "label": label})
    return {
        "product": product,
        "title": title or PRODUCT_LABELS.get(product, product),
        "icon": ICON_BY_PRODUCT.get(product, "folder"),
        "module": MODULE_BY_PRODUCT.get(product, ""),
        "entries": entries,
    }


# 工作区图标（lucide 短名，与桌面图标同一套）
ICON_BY_PRODUCT = {"cowork": "clipboard-list", "erp": "store", "dms": "id-card"}

# 工作区入口标题的覆盖表（键 = (产品, DocType)）。没配就用功能自己的名字。
# 这张表写进工作区的内容块（是**数据**），渲染时走 `__(title)`
# （`frappe/public/js/frappe/widgets/base_widget.js:set_title`），所以同样必须是英文。
WORKSPACE_LABEL: dict[tuple[str, str], str] = {
    ("dms", "DMS ID Card Intake"): "Push Records",
}

# 工作区所属模块 = 该产品 App 的模块名（各产品 modules.txt 里的值）。
#
# **必须用产品的模块，不能用核心层的**：Frappe 判断工作区可见性时会检查
# `workspace.module in user.allowed_modules`，而租户用户被允许的模块是他所用
# 产品 App 的那几个（实测该用户有 "Pearnly DMS"，没有 "Pearnly Core"）。
# 用核心层模块会被直接判无权限丢掉 —— 表现为"数据都建好了，桌面还是空的"。
MODULE_BY_PRODUCT = {
    "cowork": "Pearnly Cowork",
    "erp": "Pearnly ERP Portal",
    "dms": "Pearnly DMS",
}


def sync_features() -> dict:
    """把目录同步进 `Pearnly Feature`。幂等，可重复执行。

    只做新增与更新，**不删除**：某个功能从目录里去掉后，历史发号单仍然引用得到它，
    否则老单子的勾选项会变成悬空链接。
    """
    created = updated = 0
    for product in PRODUCTS:
        for item in CATALOG[product]:
            name = feature_name(product, item["code"])
            payload = {
                "product": product,
                "feature_code": item["code"],
                "feature_label": item["label"],
                "default_on": 1 if item.get("default_on") else 0,
                "description": item.get("description") or "",
            }
            role, doctypes = enforcement_of(product, item["code"])
            payload["role_name"] = role
            payload["grant_doctypes"] = ",".join(doctypes)
            if frappe.db.exists(FEATURE, name):
                doc = frappe.get_doc(FEATURE, name)
                changed = False
                for key, value in payload.items():
                    if doc.get(key) != value:
                        doc.set(key, value)
                        changed = True
                if changed:
                    doc.save(ignore_permissions=True)
                    updated += 1
            else:
                doc = frappe.get_doc({"doctype": FEATURE, "name": name, **payload})
                doc.insert(ignore_permissions=True, set_name=name)
                created += 1
    if created or updated:
        frappe.db.commit()
    return {"created": created, "updated": updated,
            "total": frappe.db.count(FEATURE)}
