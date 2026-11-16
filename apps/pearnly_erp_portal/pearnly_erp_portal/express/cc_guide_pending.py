"""ERP 版 CC 的引导声明：待处理（总表 ERP-D-PD；ADR-009：产品自己声明）。``GUIDES`` 是纯字面量，闸门按源码读。

业务 id、菜单字、图标、顺序与 Cowork 的待处理（`pearnly_cowork.assistant.guided.pending`）相同。待处理卡只做「选哪一项」，确定后把那张
采购 / 销售单交给单据引导（documents 源，`assistant.guide.run.start(doc_ids)`，判定卡一份：再写一次、结果不明核对、要人选的项都在那里）。
产品函数在 `express.guide_pending`。ERP 的单据票面在单据页改（documents 源复核行对 P: / S: 只读），所以没有「改票面」；也没有导出 / 删除。
"""


def allowed(user) -> bool:
    """菜单上出不出「待处理」：能开采购或销售单的人（同对话收单 `integrations.intake.DIRECTION_CAPABILITY`）。"""
    from pearnly_erp_portal.access import guard
    from pearnly_erp_portal.integrations import intake as chat_intake

    return bool(user) and user != "Guest" and any(guard.has(capability, user)
                                                  for capability in chat_intake.DIRECTION_CAPABILITY.values())


GUIDES = [
    {"id": "pending", "label_key": "guide.business.pending", "icon": "list-checks", "order": 100,
     "modes": ["chat"], "ledgers": ["express_local", "express_cloud", "mrerp"],
     "allowed": "pearnly_erp_portal.express.cc_guide_pending.allowed",
     "opening_key": "guide.open.pending", "source": "form", "writes": True,
     "steps": [
         {"key": "item", "input": "choice", "rule": "always", "search": True,
          "options": "pearnly_erp_portal.express.guide_pending.item_options"},
     ],
     "draft": "pearnly_erp_portal.express.guide_pending.draft", "review": "pearnly_erp_portal.express.guide_pending.review",
     "confirm": "pearnly_erp_portal.express.guide_pending.confirm",
     "tool": "erp_pending", "expires": ["company"]},
]
