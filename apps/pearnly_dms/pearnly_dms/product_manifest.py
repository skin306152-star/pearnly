# -*- coding: utf-8 -*-
"""DMS 产品清单。合同见 docs/p1/04-model-contracts.md。"""

MANIFEST = {
    "product": "dms",
    "version": 1,
    "bench_group": "dms",
    "entry_prefixes": ["/api/v1/dms/", "/api/v1/me/", "/pearnly-password"],
    "apps": ["pearnly_core", "pearnly_dms"],
    "features": [
        "id_card_ocr",      # 身份证录入
        "customer_match",   # 客户匹配（相同证件号视为同一客户）
        "push_record",      # 推送记录
        "roster",           # 操作员花名册（仅责任人可管）
        "quota",            # 套餐与余额
    ],
    "modules": [],
    "must_not": [
        "抹平操作员只读与管理员可写的区别",
        "访问其它 deployment 的数据库",
        "使用清单之外的权限码",
    ],
}
