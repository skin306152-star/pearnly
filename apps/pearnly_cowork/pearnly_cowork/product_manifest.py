# -*- coding: utf-8 -*-
"""Cowork 产品清单。合同见 docs/p1/04-model-contracts.md。"""

MANIFEST = {
    "product": "cowork",
    "version": 1,
    "bench_group": "cowork",
    # 入口：只允许这些前缀的路由被挂载
    "entry_prefixes": ["/api/v1/cowork/", "/api/v1/me/", "/pearnly-password"],
    "apps": ["pearnly_core", "pearnly_cowork"],
    # 功能（对应旧系统界面里的菜单）
    "features": [
        "intake",          # 录入工作台
        "ocr_history",     # 识别记录
        "push_log",        # 推送日志
        "recon",           # 对账中心
        "stocktake",       # 库存盘点
        "master_data",     # 主数据
        "boards",          # WeKan 协作看板（启用才创建实例）
    ],
    # 模块开关（沿用 tenant_modules 的键）
    "modules": ["expense", "recon", "inventory"],
    "must_not": [
        "访问其它 deployment 的数据库",
        "使用清单之外的权限码",
        "接受平台身份直接通行",
        "把库存盘点等业务改挂到别的产品路径下",
    ],
}
