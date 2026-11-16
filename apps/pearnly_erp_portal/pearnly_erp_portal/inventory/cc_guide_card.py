"""ERP 版 CC 的引导声明：收发存报表（总表 ERP-D-SC；ADR-009：产品自己声明）。``GUIDES`` 是纯字面量，闸门按源码读。

同收发存页「导出」弹窗问的那几格；产品函数在 `inventory.guide_card`，只调 `card_export.build`（网页导出、CC 工具同一个）。只读。
"""

GUIDES = [
    {"id": "stock_card", "label_key": "guide.business.stock_card", "icon": "file-spreadsheet", "order": 70,
     "modes": ["chat"], "ledgers": ["express_local", "express_cloud", "mrerp"],
     "allowed": "pearnly_erp_portal.inventory.guide_card.allowed",
     "opening_key": "guide.open.stock_card", "source": "form", "writes": False,
     "steps": [
         {"key": "report", "input": "choice", "rule": "always", "options": "pearnly_erp_portal.inventory.guide_card.report_options"},
         {"key": "format", "input": "choice", "rule": "auto", "options": "pearnly_erp_portal.inventory.guide_card.format_options",
          "default": "pearnly_erp_portal.inventory.guide_card.format_default"},
         {"key": "date_from", "input": "date", "rule": "always", "default": "pearnly_erp_portal.inventory.guide_card.date_from_default"},
         {"key": "date_to", "input": "date", "rule": "always",
          "default": "pearnly_erp_portal.inventory.guide_card.date_to_default", "check": "pearnly_erp_portal.inventory.guide_card.to_check"},
         {"key": "product_from", "input": "choice", "rule": "always", "search": True, "required": False,
          "options": "pearnly_erp_portal.inventory.guide_card.product_options"},
         {"key": "product_to", "input": "choice", "rule": "always", "search": True, "required": False,
          "options": "pearnly_erp_portal.inventory.guide_card.product_options"},
         {"key": "warehouse_from", "input": "choice", "rule": "always", "required": False, "after": ["report"],
          "when": {"key": "report", "value": "by_warehouse"}, "options": "pearnly_erp_portal.inventory.guide_card.warehouse_options"},
         {"key": "warehouse_to", "input": "choice", "rule": "always", "required": False, "after": ["report"],
          "when": {"key": "report", "value": "by_warehouse"}, "options": "pearnly_erp_portal.inventory.guide_card.warehouse_options"},
     ],
     "draft": "pearnly_erp_portal.inventory.guide_card.draft", "confirm": "pearnly_erp_portal.inventory.guide_card.confirm",
     "tool": "erp_stock_card_export", "expires": ["company"]},
]
