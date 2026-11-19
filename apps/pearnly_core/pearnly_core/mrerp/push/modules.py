# 来源：旧线上 ec2e5c957e6b · services/erp/mrerp_http/modules.py；搬来时删去的库存进出两项 11-19 按本仓口径加回（ERP 库存单直写）
# 有意变化（2026-09-28，MR.ERP 推送假成功）：交易单据不再写死 selmenu（旧 118 / 125 / 67 / 453）与回读列表菜单号。
# 菜单号是各公司自己的，按这次读到的导入页选项 + 这份账套选过的决定（`mrerp/menus.py`），回读也在那个菜单里查。
"""MR.ERP 导入模块表 · doc_type → 端点/xlsx 模板/回读列表模块。

所有单据(销/采/库存/主数据)走**同一 xlsx 导入机制**
(formupload → component/uploadexcel → formrdpc → component/importpc → component/report),
仅 path / idmenu / selmenu / sheet_kind 不同。本表是端点与模板的单一事实源；selmenu 不在这里。

端点来源:docs/integrations/mrerp-known-facts.md §4-§5 +
2026-07-01 test01 实测捕获(scratchpad/mrerp_import_endpoint_map_2026-07-01.md)。
`verified` = 已在真站点端到端跑通;False 者端点已知但模板待补(S3)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class MrErpModule:
    doc_type: str
    path: str  # 导入模块目录:impartran / imparse / impaptran / impstk* ...
    idmenu: int  # formupload.php 路由 id(MR.ERP 程序页号,各公司相同)
    sheet_kind: str  # 交给 mrerp_xlsx_generator.generate_xlsx 的模板名
    listing_module: str  # 回读用的 allview 模块(菜单号 = 这次导入用的 selmenu;主数据不回读)
    verified: bool = False
    # 费用档单据形态(นอกระบบ 单行含税全额 · 生成器/校验器/自建按此分流)· 唯一定义点,
    # 消费方读 m.expense,别再散写 doc_type == "purchase_expense" 比较。
    expense: bool = False


# 交易单据
MODULES: Dict[str, MrErpModule] = {
    "sales_credit": MrErpModule(
        "sales_credit", "impartran", 370, "sales_credit", "artran", verified=True
    ),
    # 现金销售(ใบขายเงินสด)· 列表 arse · 模板克隆见 mrerp_xlsx_sales_cash。
    # 2026-07-01 真站点端到端跑通(HTTP 直写→report 回读→arse 列表 UI 命中 107.00→删)。
    # 收款槽(รับอื่นๆ)填 GL 科目码 · 3 槽必填 · 详见生成器与 known-facts §6.4。
    "sales_cash": MrErpModule(
        "sales_cash", "imparse", 371, "sales_cash", "arse", verified=True
    ),
    # 采购(AP tran)· 货品 / 费用各推这家公司的哪个菜单由人选一次 · 3-sheet 模板克隆见 mrerp_xlsx_purchase(账套=买方)
    "purchase": MrErpModule(
        "purchase", "impaptran", 363, "purchase", "aptran", verified=True
    ),
    # 费用档(ค่าใช้จ่าย)· 与货品共用 impaptran 路径 + 3-sheet 模板(见上一条注释)。
    # judge_direction=expense 的采购票由 routing.choose_doc_type 常开分流到此(F2)。
    # 2026-07-09 真机端到端验(test01/TEST2019 · 完整路径混批 3/3 落库 PNPGCC5F-1/2/3)·
    # 展示层单据号前缀 PN(67=PG)。453 不强制物料类型;费用/服务型物料是会计口径(费用
    # 过账走物料科目)。历史 alert("Error : ") 假成功的根因=缺 formupload GET 会话上下文
    # (adapter._upload_xlsx 已修)· 见 known-facts §15。
    "purchase_expense": MrErpModule(
        "purchase_expense",
        "impaptran",
        363,
        "purchase",
        "aptran",
        verified=True,
        expense=True,
    ),
    # 收款(รับชำระหนี้)· ERP 收款服务端直写(2026-11-17)· 3-sheet:表头 20 列 + 冲哪几张应收 + 支票(只表头)·
    # 回读列表 farev · 菜单照单据按公司选一次(menus.RECEIPT)· 11-16 测试账套真写确认(收款单前缀 FR、记日记账)
    "receipt": MrErpModule("receipt", "impfarev", 375, "receipt", "farev", verified=True),
    # 库存入库 / 出库(รับสินค้า / จ่ายสินค้า)· ERP 库存单服务端直写(2026-11-19)· 2-sheet:单头 6 列 + 明细(入库 8 列带单价金额、
    # 出库 6 列成本系统算)· 回读列表 stktranrec / stktraniss · 菜单入库一个、出库按用途各选一次(menus.STOCK_IN / stock_out_kind)
    "stock_in": MrErpModule("stock_in", "impstktranrec", 356, "stock_in", "stktranrec", verified=True),
    "stock_out": MrErpModule("stock_out", "impstktraniss", 357, "stock_out", "stktraniss", verified=True),
    # 主数据自建(走导入,非 Playwright 表单)· S2
    "master_customer": MrErpModule(
        "master_customer", "imparmas", 367, "customer_master", "armas", verified=False
    ),
    "master_supplier": MrErpModule(
        "master_supplier", "impapmas", 359, "supplier_master", "apmas", verified=False
    ),
    "master_product": MrErpModule(
        "master_product", "impstkmas", 353, "product_master", "stkmas", verified=False
    ),
}


def get_module(doc_type: str) -> MrErpModule:
    m = MODULES.get(doc_type)
    if not m:
        raise ValueError(f"unknown mrerp doc_type={doc_type!r}; known={list(MODULES)}")
    return m
