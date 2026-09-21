# -*- coding: utf-8 -*-
"""ERP 门户产品清单。合同见 docs/p1/04-model-contracts.md。"""

MANIFEST = {
    "product": "erp",
    "version": 1,
    "bench_group": "erp",
    "entry_prefixes": ["/api/v1/erp/", "/api/v1/me/", "/pearnly-password"],
    "apps": ["pearnly_core", "pearnly_erp_portal"],
    # 可售卖的功能目录（角色、授权表、默认开关）在同目录的 features.json：
    # 那张表由控制面读文件汇总，这里是产品的入口/依赖/禁止事项。
    "features": [
        "products",        # 商品数据
        "warehouses",      # 库存、入库、出库
        "stockcard",       # 收发存报表（权限码已从 accounting 改挂 inventory）
        "purchase",        # 采购记录、供应商、采购设置
        "sales",           # 销售系统
        "clients",         # 客户名录
        "cashier",         # 收银台与交接班（用户 2026-09-21 定：含在 ERP，不单独购买）
        "erp_push",        # 对外 ERP 推送（状态以 erp_push_logs 为唯一来源）
    ],
    "modules": ["sales", "expense", "inventory", "receivable"],
    "must_not": [
        "默认安装 ERPNext 全套业务",
        "生成会计凭证（会计不迁移）",
        "使用 accounting / tax / pos / workorder 相关权限码",
        "访问其它 deployment 的数据库",
    ],
}
