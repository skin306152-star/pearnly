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
    # 可售卖的功能目录（角色、授权表、默认开关）在同目录的 features.json：
    # 那张表由控制面读文件汇总，这里是产品的入口/依赖/禁止事项。
    "features": [
        "intake",          # 录入工作台
        "ocr_history",     # 识别记录
        "push_log",        # 推送日志
        "recon",           # 对账中心
        "stocktake",       # 库存盘点
        "master_data",     # 主数据
        # 小助手（Companion）：装在商家会计电脑上的 Windows 程序。
        # 用户 2026-09-17 专门提醒不要漏迁。它做的事：
        #   安装包下载、配对、心跳上报套账与科目、出站拉取任务、
        #   在本机会计软件里落库（写角色校验在本机做）。
        "companion",
        "companion_pairing",
        "companion_account_reporting",   # 套账与科目映射上报
        "companion_outbound_pull",       # 出站拉取（云端不主动连入）
        "express_guide",                 # Express 推送手册（安装/配对/升级/卸载）
    ],
    # 模块开关（沿用 tenant_modules 的键）
    "modules": ["expense", "recon", "inventory"],
    "must_not": [
        "访问其它 deployment 的数据库",
        "使用清单之外的权限码",
        "接受平台身份直接通行",
        "把库存盘点等业务改挂到别的产品路径下",
        "把配对密钥或本机会计数据明文存到云端",
        "让云端主动连入小助手（只允许小助手出站拉取）",
    ],
}
