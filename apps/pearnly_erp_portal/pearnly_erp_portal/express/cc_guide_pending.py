"""ERP 版 CC 的引导声明：待处理（总表 ERP-D-PD；ADR-009：产品自己声明）。``GUIDES`` 是纯字面量，闸门按源码读。

业务 id、菜单字、图标、顺序与 Cowork 的待处理（`pearnly_cowork.assistant.guided.pending`）相同。待处理卡只做「选哪一项」，确定后把那张
采购 / 销售单交给单据引导（documents 源，`assistant.guide.run.start(doc_ids)`，判定卡一份：再写一次、结果不明核对、要人选的项都在那里）。
产品函数在 `express.guide_pending`。ERP 的单据票面在单据页改（documents 源复核行对 P: / S: 只读），所以没有「改票面」；也没有导出 / 删除。

「单据」（总表第 14 行单据维护，主会话 11-24）：同一组函数，先选操作（处理 / 导出 / 删除）与方向（采购 / 销售），清单是对话选择器选的
公司与月份里这个方向的全部单据（列表同一个查询，服务端搜索翻页）；导出是这个月这个方向的整张列表；删除走允许卡、只删草稿（同网页）。
直写与代录都开（代录对话里清单本来就是对话月份）。ERP 改票面进 CC 是第二批。
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
    # 单据维护（总表第 14 行）：同一组函数，按 ctx 的业务分（`guide_pending._documents`）
    {"id": "documents", "label_key": "guide.business.documents", "icon": "file-spreadsheet", "order": 101,
     "modes": ["chat", "operator"], "ledgers": ["express_local", "express_cloud", "mrerp"],
     "allowed": "pearnly_erp_portal.express.cc_guide_pending.allowed",
     "opening_key": "guide.open.documents", "source": "form", "writes": True,
     "consent": {"key": "action", "value": "delete"},
     "steps": [
         {"key": "action", "input": "choice", "rule": "always",
          "options": "pearnly_erp_portal.express.guide_pending.action_options"},
         {"key": "direction", "input": "choice", "rule": "always", "after": ["action"],
          "options": "pearnly_erp_portal.express.guide_pending.direction_options"},
         {"key": "item", "input": "choice", "rule": "always", "search": True, "after": ["direction"],
          "when": {"key": "action", "in": ["handle"]}, "options": "pearnly_erp_portal.express.guide_pending.document_options"},
         {"key": "items", "input": "multi", "rule": "always", "search": True, "after": ["direction"],
          "when": {"key": "action", "in": ["delete"]}, "options": "pearnly_erp_portal.express.guide_pending.document_options"},
     ],
     "draft": "pearnly_erp_portal.express.guide_pending.draft", "review": "pearnly_erp_portal.express.guide_pending.review",
     "confirm": "pearnly_erp_portal.express.guide_pending.confirm", "result": "pearnly_erp_portal.express.guide_pending.result",
     "tool": "erp_list_documents", "expires": ["company"]},
]
