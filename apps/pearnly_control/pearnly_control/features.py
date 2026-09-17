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


def feature_name(product: str, code: str) -> str:
    """功能项的主键。与 `Pearnly Feature` 的 autoname 规则一致。"""
    return f"{product}-{code}"


def all_codes(product: str) -> set[str]:
    return {f["code"] for f in CATALOG.get(product, [])}


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
