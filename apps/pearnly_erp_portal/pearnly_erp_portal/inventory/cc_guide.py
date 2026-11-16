"""ERP 版 CC 的引导声明：盘点（总表 ERP-D-ST；ADR-009：产品自己声明；形状见 ``pearnly_core.assistant.guide.declaration``）。
``GUIDES`` 是纯字面量：闸门脚本不导入本模块，直接按源码读它。

业务 id、菜单字、图标、顺序、步骤 key 与 Cowork 的盘点引导（`pearnly_cowork.assistant.guided.stocktake`）相同。第一题「做什么」：
新建（仓 → 名称）/ 记数（盘点单 → 物料 + 数量，一行一行加，0 有效 → 记法）/ 结束 / 按盘点结果调整物料（只建草稿）/ 删除（只限进行中）。
产品函数在 `inventory.guide`，只转调盘点页同一组服务函数。结束、删除要画成允许卡：等引擎按动作区分确定样式
（评估 docs/work/2026-11-16-cc-guided-stocktake-评估.md 第 12 节第 1 条，主会话定后在这里补 ``consent``）。
"""

WRITE = "count.write"  # 同 `count.create_count / add_entry / close_count`：勾了「盘点」功能的人（与管理员）


def allowed(user) -> bool:
    """菜单上出不出「盘点」：只看这个人有没有盘点功能（`access.guard`）；哪家公司、哪个仓在每一步由盘点服务自己核。"""
    from pearnly_erp_portal.access import guard

    return bool(user) and user != "Guest" and guard.has(WRITE, user)


GUIDES = [
    {"id": "stocktake", "label_key": "guide.business.stocktake", "icon": "clipboard-list", "order": 60,
     "modes": ["chat"], "ledgers": ["express_local", "express_cloud", "mrerp"],
     "allowed": "pearnly_erp_portal.inventory.cc_guide.allowed",
     "opening_key": "guide.open.stocktake", "source": "form", "writes": True,
     "steps": [
         {"key": "action", "input": "choice", "rule": "always", "options": "pearnly_erp_portal.inventory.guide.action_options"},
         {"key": "warehouse", "input": "choice", "rule": "auto", "search": True, "after": ["action"],
          "when": {"key": "action", "value": "new"}, "options": "pearnly_erp_portal.inventory.guide.warehouse_options",
          "default": "pearnly_erp_portal.inventory.guide.warehouse_default"},
         {"key": "title", "input": "text", "rule": "always", "after": ["action"], "when": {"key": "action", "value": "new"}},
         {"key": "count", "input": "choice", "rule": "always", "search": True, "after": ["action"],
          "when": {"key": "action", "in": ["count", "close", "adjust", "delete"]},
          "options": "pearnly_erp_portal.inventory.guide.count_options"},
         {"key": "rows", "input": "amounts", "rule": "always", "search": True, "zero": True, "after": ["count"],
          "when": {"key": "action", "value": "count"}, "options": "pearnly_erp_portal.inventory.guide.item_options"},
         {"key": "mode", "input": "choice", "rule": "auto", "after": ["count"], "when": {"key": "action", "value": "count"},
          "options": "pearnly_erp_portal.inventory.guide.mode_options", "default": "pearnly_erp_portal.inventory.guide.mode_default"},
     ],
     "draft": "pearnly_erp_portal.inventory.guide.draft", "review": "pearnly_erp_portal.inventory.guide.review",
     "confirm": "pearnly_erp_portal.inventory.guide.confirm",
     "tool": "erp_stocktake_count", "expires": ["company"]},
]
