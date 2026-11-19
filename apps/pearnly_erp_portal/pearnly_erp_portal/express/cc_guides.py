"""ERP 版 CC 的引导声明：采购、销售（ADR-009：产品自己声明；形状见 ``pearnly_core.assistant.guide.declaration``，总设计
``docs/work/2026-11-14-cc-guided-master-design.md`` 2.2）。``GUIDES`` 是纯字面量：闸门脚本不导入本模块，直接按源码读它。

按单据出题（题目由写账前置判定与必填登记表算出，这里不列步骤；documents 出题源 `pearnly_core.assistant.guide.documents`，
交识别、识别到哪、复核、复核里改一格、直写的确定、结果是 `express.guide_documents` 的产品函数）。直写、代录都在菜单上（直写整条链：
对话收单 → 题卡 → 复核 → 确定提交 ERP 单、由它排直写 → 结果）。菜单顺序：采购、销售、收款、盘点排前四（主会话 11-19）。
收款的声明在 ``collection/cc_guide.py``。
"""

GUIDES = [
    {"id": "purchase", "label_key": "guide.business.purchase", "icon": "shopping-cart", "order": 10,
     "modes": ["chat", "operator"], "ledgers": ["express_local", "express_cloud", "mrerp"],
     "opening_key": "guide.open.purchase", "source": "documents", "writes": True,
     "steps": [{"key": "documents", "input": "file", "rule": "always"}],
     "intake": "pearnly_erp_portal.express.guide_documents.intake",
     "uploads": "pearnly_erp_portal.express.guide_documents.uploads",
     "review": "pearnly_erp_portal.express.guide_documents.review",
     "edit": "pearnly_erp_portal.express.guide_documents.edit",
     "confirm": "pearnly_erp_portal.express.guide_documents.confirm",
     "result": "pearnly_erp_portal.express.guide_documents.result",
     "tool": "erp_push_documents", "expires": ["ledger", "company", "period", "master"]},
    {"id": "sales", "label_key": "guide.business.sales", "icon": "tag", "order": 20,
     "modes": ["chat", "operator"], "ledgers": ["express_local", "express_cloud", "mrerp"],
     "opening_key": "guide.open.sales", "source": "documents", "writes": True,
     "steps": [{"key": "documents", "input": "file", "rule": "always"}],
     "intake": "pearnly_erp_portal.express.guide_documents.intake",
     "uploads": "pearnly_erp_portal.express.guide_documents.uploads",
     "review": "pearnly_erp_portal.express.guide_documents.review",
     "edit": "pearnly_erp_portal.express.guide_documents.edit",
     "confirm": "pearnly_erp_portal.express.guide_documents.confirm",
     "result": "pearnly_erp_portal.express.guide_documents.result",
     "tool": "erp_push_documents", "expires": ["ledger", "company", "period", "master"]},
]
