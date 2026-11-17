"""ERP 版 CC 的引导声明：收款（ADR-009：产品自己声明；形状见 ``pearnly_core.assistant.guide.declaration``，总设计
``docs/work/2026-11-14-cc-guided-master-design.md`` 2.2）。``GUIDES`` 是纯字面量：闸门脚本不导入本模块，直接按源码读它。

收款只在 ERP、只做直写（用户 11-14）。按表单一步步问（总设计 P1，口径同 LINE 收款）：公司 → 收款日期 → 账套 → 客户 →
未收发票（逐张收多少）→ 现金 / 转账 / 折扣分摊 → 每种转账的凭证 → 复核 → 确定 → 结果。选项、校验、草稿、复核、放行、结果都在
`collection.guide`，只转调收款服务函数（网页、LINE、打字同一组）。钱和对象（客户、发票、金额、方式）每次明选，不记不预选。
"""

WRITE = "collection.write"  # 同 `service.WRITE`：勾了「收款」功能的人（与管理员）


def allowed(user) -> bool:
    """菜单上出不出「收款」：只看这个人有没有收款功能（`access.guard`）；哪家公司能收在开始时由收款服务自己核。"""
    from pearnly_erp_portal.access import guard

    return bool(user) and user != "Guest" and guard.has(WRITE, user)


GUIDES = [
    {"id": "collection", "label_key": "guide.business.collection", "icon": "wallet", "order": 10,
     "modes": ["chat"], "ledgers": ["express_local", "express_cloud", "mrerp"],
     "allowed": "pearnly_erp_portal.collection.cc_guide.allowed",
     "opening_key": "guide.open.collection", "source": "form", "writes": True,
     "steps": [
         {"key": "company", "input": "choice", "rule": "auto", "search": True,
          "options": "pearnly_erp_portal.collection.guide.company_options", "default": "pearnly_erp_portal.collection.guide.company_default"},
         {"key": "day", "input": "date", "rule": "auto", "after": ["company"],
          "default": "pearnly_erp_portal.collection.guide.day_default", "check": "pearnly_erp_portal.collection.guide.day_check", "facts": "pearnly_erp_portal.collection.guide.day_facts"},
         {"key": "book", "input": "choice", "rule": "auto", "after": ["company", "day"],
          "options": "pearnly_erp_portal.collection.guide.book_options", "default": "pearnly_erp_portal.collection.guide.book_default", "apply": "pearnly_erp_portal.collection.guide.book_apply"},
         {"key": "customer", "input": "choice", "rule": "always", "search": True, "after": ["book"],
          "options": "pearnly_erp_portal.collection.guide.customer_options"},
         {"key": "invoices", "input": "amounts", "rule": "always", "after": ["customer"],
          "options": "pearnly_erp_portal.collection.guide.invoice_options"},
         {"key": "methods", "input": "split", "rule": "always", "after": ["invoices"],
          "options": "pearnly_erp_portal.collection.guide.method_options", "check": "pearnly_erp_portal.collection.guide.method_check"},
         {"key": "voucher", "input": "file", "rule": "always", "when": {"key": "methods", "starts": "transfer:"},
          "check": "pearnly_erp_portal.collection.guide.voucher_check"},
     ],
     "draft": "pearnly_erp_portal.collection.guide.draft", "review": "pearnly_erp_portal.collection.guide.review", "confirm": "pearnly_erp_portal.collection.guide.confirm", "result": "pearnly_erp_portal.collection.guide.result", "note": "pearnly_erp_portal.collection.guide.note",
     "tool": "erp_collection_draft", "expires": ["ledger", "company", "master"]},
]
