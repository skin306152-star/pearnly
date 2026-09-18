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
_HOME = {
    "code": "home_subscription",
    "label": "首页（订阅与计费）",
    "default_on": True,
    "description": "产品首页，承载当前的订阅套餐、用量与账单",
}

CATALOG = {
    "cowork": [
        _HOME,
        # 以下逐条对应 apps/pearnly_cowork/pearnly_cowork/product_manifest.py 的 features
        {"code": "intake", "label": "录入工作台", "default_on": True,
         "description": "上传票据与表格、识别字段、逐项核对与推送"},
        {"code": "ocr_history", "label": "识别记录", "default_on": True,
         "description": "每次识别批次的记录与结果"},
        {"code": "push_log", "label": "推送日志", "default_on": True,
         "description": "推送到外部 ERP 的过程与结果"},
        {"code": "recon", "label": "对账中心", "default_on": True,
         "description": "单据与账套的对账"},
        {"code": "master_data", "label": "主数据", "default_on": True,
         "description": "客户、供应商、科目等基础资料"},
        {"code": "stocktake", "label": "库存盘点", "default_on": False,
         "description": "盘点任务与差异记录"},
        {"code": "boards", "label": "协作看板（WeKan）", "default_on": False,
         "description": "启用才创建看板实例，不启用不占资源"},
        {"code": "companion", "label": "小助手（Companion）", "default_on": False,
         "description": "装在商家会计电脑上的 Windows 程序，负责安装包、配对、出站拉取任务"},
        {"code": "express_guide", "label": "Express 推送手册", "default_on": False,
         "description": "小助手的安装、配对、升级与卸载说明"},
    ],
    "erp": [
        _HOME,
        # 对应 apps/pearnly_erp_portal/pearnly_erp_portal/product_manifest.py
        {"code": "products", "label": "商品数据", "default_on": True,
         "description": "商品主数据"},
        {"code": "warehouses", "label": "库存（入库/出库）", "default_on": True,
         "description": "仓库、入库与出库单据"},
        {"code": "stockcard", "label": "收发存报表", "default_on": True,
         "description": "库存收发存汇总（保留项，见 ERP 清单）"},
        {"code": "purchase", "label": "采购（记录/供应商/设置）", "default_on": True,
         "description": "采购记录、供应商与采购设置"},
        {"code": "sales", "label": "销售系统", "default_on": True,
         "description": "销售单据与客户往来"},
        {"code": "clients", "label": "客户名录", "default_on": True,
         "description": "客户基础资料"},
        {"code": "erp_push", "label": "对外 ERP 推送", "default_on": False,
         "description": "推送到外部 ERP；状态以推送日志为唯一来源"},
    ],
    "dms": [
        _HOME,
        # 对应 apps/pearnly_dms/pearnly_dms/product_manifest.py
        {"code": "id_card_ocr", "label": "身份证录入", "default_on": True,
         "description": "读身份证、与 DMS 客户库比对后确认资料"},
        {"code": "customer_match", "label": "客户匹配", "default_on": True,
         "description": "证件号相同视为同一客户，避免重复建档"},
        {"code": "push_record", "label": "推送记录", "default_on": True,
         "description": "每次推送的结果"},
        {"code": "roster", "label": "操作员花名册", "default_on": True,
         "description": "仅责任人可管理"},
        {"code": "quota", "label": "套餐与余额", "default_on": True,
         "description": "DMS 侧的用量与余额"},
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
    ("cowork", "intake"): ("Cowork 录入", ["Intake Record"]),
    ("erp", "purchase"): ("ERP 采购", ["Purchase Document", "Purchase Line"]),
    ("dms", "id_card_ocr"): ("DMS 身份证录入", ["DMS ID Card Intake"]),
    ("dms", "customer_match"): ("DMS 客户匹配", ["DMS Customer Link"]),
}

# 产品管理员角色：能管本产品的员工账号，但**不含功能权限** ——
# 功能权限一律来自上面的功能角色，这样"没买的功能"对管理员同样是关着的。
PRODUCT_ADMIN_ROLE = {
    "cowork": "Cowork 管理员",
    "erp": "ERP 管理员",
    "dms": "DMS 管理员",
}

# 租户管理员必须有的基础角色（否则连 Desk 都进不去）
BASE_ROLES = ["Desk User"]


def feature_name(product: str, code: str) -> str:
    """功能项的主键。与 `Pearnly Feature` 的 autoname 规则一致。"""
    return f"{product}-{code}"


def all_codes(product: str) -> set[str]:
    return {f["code"] for f in CATALOG.get(product, [])}


def enforcement_of(product: str, code: str) -> tuple[str, list[str]]:
    """该功能对应的 (角色名, DocType 列表)。没配就是没有可挡对象。"""
    role, doctypes = ENFORCEMENT.get((product, code), ("", []))
    return role, list(doctypes)


# 网页端**只读**的表。
#
# 为什么需要：DMS 的身份证录入作业在 LINE 上完成（用户 2026-09-17 定："DMS 身份证
# 录入网页端不要了……网页只负责绑定第三方 dms，只取消网页前端的录入入口"）。
# 所以这张表在网页端只能看、不能新建 —— Frappe 原生就是按 `create` 权限显示"新增"
# 按钮的，禁掉它就等于取消了网页端的录入入口，不需要改前端、也不需要另造开关。
#
# 这**不影响将来的 LINE 录入**：webhook 是 `allow_guest` 的独立通道，走的不是这个
# 角色（今天 LINE 侧还只做绑定，没有录入分支）。
READ_ONLY_ON_WEB: set[tuple[str, str]] = {("dms", "DMS ID Card Intake")}
READ_ONLY_PERMS = {"read": 1, "report": 1}


def perms_override(product: str, doctype: str) -> dict | None:
    """该表是否要用"只读"覆盖默认的功能权限；None = 用默认（可读可写可建）。"""
    if (product, doctype) in READ_ONLY_ON_WEB:
        return dict(READ_ONLY_PERMS)
    return None


def roles_payload(product: str, codes: list[str]) -> dict:
    """把"勾了哪些功能"翻成站点侧要建的角色与权限。

    额外带上产品管理员角色与基础角色：租户管理员**不再给 System Manager**
    （那是框架超管，能改系统设置、装应用），只给"产品管理员 + 买了的功能角色"。
    """
    roles = []
    for code in codes or []:
        role, doctypes = enforcement_of(product, code)
        if role and doctypes:
            entry = {"role": role, "doctypes": doctypes, "feature": code}
            overrides = {}
            for doctype in doctypes:
                perms = perms_override(product, doctype)
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
WORKSPACE_LABEL: dict[tuple[str, str], str] = {
    ("dms", "DMS ID Card Intake"): "推送记录",
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
