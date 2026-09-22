# -*- coding: utf-8 -*-
"""功能模版目录：控制面把各产品**自己声明**的功能清单汇总起来。

每个产品把自己卖什么写在自己的目录里（`apps/<app>/<app>/features.json`：功能码、
标签、默认开关、对应岗位、可挡的 DocType、只读表）。控制面只做汇总和跨产品策略，
不再各留一份镜像 —— 产品加功能只改自己的文件，两边不会因为同一张表打架。

为什么是**读文件**而不是 import 各产品：控制面站点只装了 frappe + pearnly_core +
pearnly_control，三个产品的 App 并不装在它上面，import 不到。文件在同一个 bench 里
（生产镜像与开发挂载都带全部 App），所以读文件是稳的；文件缺失会**明确报错**，
不会静默变成"没有功能可卖"。

`sync_features()` 在每次 migrate 后把目录同步进 `Pearnly Feature` 表，作为勾选时的
候选项。

用户 2026-09-17 的要求：每个产品都要有**首页**，首页就是现在的订阅与计费系统，
所以三个产品都带 `home_subscription`（这一条是跨产品的，写在控制面）。
"""

import json
import os

import frappe
from frappe.utils import get_bench_path

# 顺序就是这个顺序在表单里出现的顺序
PRODUCTS = ("cowork", "erp", "dms")

# 产品 → 它的 App 名（目录名与包名一致）
PRODUCT_APPS = {
    "cowork": "pearnly_cowork",
    "erp": "pearnly_erp_portal",
    "dms": "pearnly_dms",
}

# 三个产品共有的首页（= 订阅与计费）
# 文案一律写**英文**：渲染时会走 __()，中文/泰文/日文由翻译包给（见
# docs/i18n.md）。msgid 写中文 = 换语言时永远是中文。
_HOME = {
    "code": "home_subscription",
    "label": "Home (Subscription & Billing)",
    "default_on": True,
    "description": "Product home: current plan, usage and billing",
}

FEATURE = "Pearnly Feature"


def catalog_path(product: str, bench: str = "") -> str:
    """该产品自己那份功能清单的绝对路径。"""
    app = PRODUCT_APPS[product]
    return os.path.join(bench or get_bench_path(), "apps", app, app, "features.json")


def read_catalog(product: str, bench: str = "") -> dict:
    """读一份产品清单。缺文件或形状不对就报错，不静默降级成空目录。"""
    path = catalog_path(product, bench)
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        raise RuntimeError(
            f"Missing {product} feature catalog at {path}; product apps must be present in the control bench"
        ) from None
    if not isinstance(data, dict) or data.get("product") != product \
            or not isinstance(data.get("features"), list):
        raise RuntimeError(f"{path}: invalid catalog shape (product and features required)")
    return data


DECLARATIONS = {product: read_catalog(product) for product in PRODUCTS}

PRODUCT_LABELS = {product: str(DECLARATIONS[product].get("label") or product)
                  for product in PRODUCTS}

CATALOG = {product: [_HOME] + [
    {"code": item["code"], "label": item["label"], "default_on": bool(item.get("default_on")),
     "description": item.get("description") or ""}
    for item in DECLARATIONS[product]["features"]
] for product in PRODUCTS}

# ── 权限落地：功能 → 角色 → DocType ────────────────────────────────────────
#
# 光把功能"记在开单上"是没有约束力的 —— 用户要能感到差别，必须变成**权限**。
# 做法：每个功能对应一个角色，角色拿到它那个 DocType 的读写权限；开通时按勾选
# 把角色发给租户管理员。没勾的功能 → 角色没发 → 那个 DocType 访问不了。
#
# 只列**有实际可挡对象**的功能（产品清单里 `role` 为空的就是这种）。没有 DocType 的
# 功能（例如首页、套餐与余额）目前没有可挡的东西，就不在这里假装有权限。
ENFORCEMENT = {
    (product, item["code"]): (item.get("role") or "", list(item.get("doctypes") or []))
    for product in PRODUCTS for item in DECLARATIONS[product]["features"]
    if item.get("role")
}

# 产品管理员角色：能管本产品的员工账号，但**不含功能权限** ——
# 功能权限一律来自上面的功能角色，这样"没买的功能"对管理员同样是关着的。
#
# 角色名是**库里的记录**，按 Frappe 惯例用英文（框架自己的角色也是英文），
# 界面上由 `__(role)` 翻（`frappe/public/js/frappe/roles_editor.js:45` 实测会翻）。
# 改产品清单里的名字必须同时改：
#   · apps/pearnly_core/pearnly_core/billing_www.py 的 ADMIN_ROLES（按名字判权限）
#   · apps/pearnly_core/pearnly_core/patches/ 里把老站点已有角色改名的那个补丁
PRODUCT_ADMIN_ROLE = {product: str(DECLARATIONS[product].get("admin_role") or "")
                      for product in PRODUCTS}

# 产品管理员（租户管理员）在这张**标准表清单之外**还要能管的表。
#
# 为什么需要单独一份：标准表清单（站点侧的 `User` / `Contact`）是"管本产品员工账号"，
# 与产品业务无关，所以写在站点侧；而公司资料不是买来的功能，也不该发给岗位 ——
# 它是租户自己的主体资料，第一次打开资料页就要能填、能存，否则页面只能看不能改。
# 站点侧把这份清单**追加**在自己的默认清单后面（`ensure_feature_roles`）。
ADMIN_EXTRA_DOCTYPES = {product: list(DECLARATIONS[product].get("admin_extra_doctypes") or [])
                        for product in PRODUCTS}

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


# 按岗位**只读**的表（键带上角色，避免把同一个 DocType 上别的岗位一起降成只读）。
#
# "每一处在挡什么"写在产品清单那一条里（`read_only` + `notes`）：例如 DMS 的身份证
# 录入作业在 LINE 上完成，网页端只能看不能新建；ERP 的库存岗位要看得见商品，但商品
# 主数据归商品岗位；公司资料是租户自己的主体资料，所有业务岗位只读，写权限只从
# ADMIN_EXTRA_DOCTYPES 走。
READ_ONLY: set[tuple[str, str, str]] = {
    (product, doctype, item["role"])
    for product in PRODUCTS for item in DECLARATIONS[product]["features"]
    for doctype in item.get("read_only") or []
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
        "admin_extra_doctypes": list(ADMIN_EXTRA_DOCTYPES.get(product, [])),
        "roles": roles,
        # Field-level grants belong to the product, just like DocType grants.
        "field_permissions": [dict(rule) for rule in DECLARATIONS[product].get("field_permissions", [])
                              if rule["role"] in seen_roles | {PRODUCT_ADMIN_ROLE.get(product)}],
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
ICON_BY_PRODUCT = {product: str(DECLARATIONS[product].get("icon") or "folder")
                   for product in PRODUCTS}

# 工作区入口标题的覆盖表（键 = (产品, DocType)）。没配就用功能自己的名字。
# 这张表写进工作区的内容块（是**数据**），渲染时走 `__(title)`
# （`frappe/public/js/frappe/widgets/base_widget.js:set_title`），所以同样必须是英文。
WORKSPACE_LABEL: dict[tuple[str, str], str] = {
    (product, doctype): label
    for product in PRODUCTS
    for doctype, label in (DECLARATIONS[product].get("workspace_labels") or {}).items()
}

# 工作区所属模块 = 该产品 App 的模块名（各产品 modules.txt 里的值）。
#
# **必须用产品的模块，不能用核心层的**：Frappe 判断工作区可见性时会检查
# `workspace.module in user.allowed_modules`，而租户用户被允许的模块是他所用
# 产品 App 的那几个（实测该用户有 "Pearnly DMS"，没有 "Pearnly Core"）。
# 用核心层模块会被直接判无权限丢掉 —— 表现为"数据都建好了，桌面还是空的"。
MODULE_BY_PRODUCT = {product: str(DECLARATIONS[product].get("module") or "")
                     for product in PRODUCTS}


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
