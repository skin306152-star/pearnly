"""ERP 版 CC 的引导声明：采购、销售（ADR-009：产品自己声明；形状见 ``pearnly_core.assistant.guide.declaration``，总设计
``docs/work/2026-11-14-cc-guided-master-design.md`` 2.2）。``GUIDES`` 是纯字面量：闸门脚本不导入本模块，直接按源码读它。

按单据出题（题目由写账前置判定与必填登记表算出，这里不列步骤）。现在只在代录的菜单上：ERP 直写的采购 / 销售整条链（对话收单 → 复核 → 办完）
还没走通过（主会话 11-14 核实），按「菜单只列真能办完的」先不放；总设计 P2 整链验过后把 ``chat`` 加回 ``modes``。收款的声明在 ``collection/cc_guide.py``。
"""

GUIDES = [
    {"id": "purchase", "label_key": "guide.business.purchase", "icon": "shopping-cart", "order": 20,
     "modes": ["operator"], "ledgers": ["express_local", "express_cloud", "mrerp"],
     "opening_key": "guide.open.purchase", "source": "documents", "writes": True,
     "steps": [{"key": "documents", "input": "file", "rule": "always"}],
     "tool": "erp_push_documents", "expires": ["ledger", "company", "period", "master"]},
    {"id": "sales", "label_key": "guide.business.sales", "icon": "tag", "order": 30,
     "modes": ["operator"], "ledgers": ["express_local", "express_cloud", "mrerp"],
     "opening_key": "guide.open.sales", "source": "documents", "writes": True,
     "steps": [{"key": "documents", "input": "file", "rule": "always"}],
     "tool": "erp_push_documents", "expires": ["ledger", "company", "period", "master"]},
]
