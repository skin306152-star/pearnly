"""Cowork 版 CC 的引导声明（ADR-009：产品自己声明；形状见 ``pearnly_core.assistant.guide.declaration``，总设计
``docs/work/2026-11-14-cc-guided-master-design.md`` 2.2）。``GUIDES`` 是纯字面量：闸门脚本不导入本模块，直接按源码读它。

采购、销售：按单据出题（题目由写账前置判定与 Express 必填登记表算出，这里不列步骤；documents 出题源
`pearnly_core.assistant.guide.documents`，产品函数在 `assistant.guide_documents`）；直写与代录同一份。
Cowork 没有收款（只在 ERP）。
"""

GUIDES = [
    {"id": "purchase", "label_key": "guide.business.purchase", "icon": "shopping-cart", "order": 10,
     "modes": ["chat", "operator"], "ledgers": ["express_local", "express_cloud", "mrerp"],
     "opening_key": "guide.open.purchase", "source": "documents", "writes": True,
     "steps": [{"key": "documents", "input": "file", "rule": "always"}],
     "intake": "pearnly_cowork.assistant.guide_documents.intake",
     "uploads": "pearnly_cowork.assistant.guide_documents.uploads",
     "review": "pearnly_cowork.assistant.guide_documents.review",
     "edit": "pearnly_cowork.assistant.guide_documents.edit",
     "confirm": "pearnly_cowork.assistant.guide_documents.confirm",
     "result": "pearnly_cowork.assistant.guide_documents.result",
     "tool": "cowork_push_documents", "expires": ["ledger", "company", "period", "master"]},
    {"id": "sales", "label_key": "guide.business.sales", "icon": "tag", "order": 20,
     "modes": ["chat", "operator"], "ledgers": ["express_local", "express_cloud", "mrerp"],
     "opening_key": "guide.open.sales", "source": "documents", "writes": True,
     "steps": [{"key": "documents", "input": "file", "rule": "always"}],
     "intake": "pearnly_cowork.assistant.guide_documents.intake",
     "uploads": "pearnly_cowork.assistant.guide_documents.uploads",
     "review": "pearnly_cowork.assistant.guide_documents.review",
     "edit": "pearnly_cowork.assistant.guide_documents.edit",
     "confirm": "pearnly_cowork.assistant.guide_documents.confirm",
     "result": "pearnly_cowork.assistant.guide_documents.result",
     "tool": "cowork_push_documents", "expires": ["ledger", "company", "period", "master"]},
]
