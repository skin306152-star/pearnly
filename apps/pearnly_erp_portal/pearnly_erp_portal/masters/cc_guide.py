"""ERP 版 CC 的引导声明：公司资料（知识库笔记，总表 ERP-D-CO）与添加公司（从连接添加，ERP-D-ADD）。ADR-009：产品自己声明；
``GUIDES`` 是纯字面量，闸门按源码读。产品函数在 `masters.guide`，只转调公司页同一组函数（`masters.cc_service`）。

- 公司资料：业务 id `company` 与 Cowork 的公司资料同一个（菜单字、图标、步骤 key 相同）；做什么（加 / 改 / 删笔记）→ 笔记 → 正文。
  公司 = 本对话公司。删笔记画成允许卡（``consent`` 按「做什么」的答案区分，约定 11-16）。
- 添加公司：连接里还没接进 ERP 的公司（多选，服务端搜索）→ 复核 → 确定（同公司页「从连接添加」）；只给能建公司的人。
"""


def allowed_notes(user) -> bool:
    """菜单上出不出「公司资料」：能写公司知识库的人（`cc_service.require_write` 同一口径）。"""
    import frappe

    from pearnly_erp_portal.masters import cc_service

    if not user or user == "Guest":
        return False
    try:
        cc_service.require_write(user)
    except frappe.PermissionError:
        frappe.clear_messages()
        return False
    return True


def allowed_add(user) -> bool:
    """菜单上出不出「添加公司」：能建公司的人（`cc_service.can_build`，同公司页「从连接添加」）。"""
    from pearnly_erp_portal.masters import cc_service

    return bool(user) and user != "Guest" and cc_service.can_build(user)


GUIDES = [
    {"id": "company", "label_key": "guide.business.company", "icon": "building", "order": 80,
     "modes": ["chat"], "ledgers": ["express_local", "express_cloud", "mrerp"],
     "allowed": "pearnly_erp_portal.masters.cc_guide.allowed_notes",
     "opening_key": "guide.open.company", "source": "form", "writes": True,
     "consent": {"key": "action", "value": "note_delete"},
     "steps": [
         {"key": "action", "input": "choice", "rule": "always", "options": "pearnly_erp_portal.masters.guide.action_options"},
         {"key": "note", "input": "choice", "rule": "always", "after": ["action"],
          "when": {"key": "action", "in": ["note_edit", "note_delete"]}, "options": "pearnly_erp_portal.masters.guide.note_options"},
         {"key": "text", "input": "text", "rule": "always", "after": ["action"],
          "when": {"key": "action", "in": ["note_add", "note_edit"]},
          "default": "pearnly_erp_portal.masters.guide.text_default", "check": "pearnly_erp_portal.masters.guide.text_check"},
     ],
     "draft": "pearnly_erp_portal.masters.guide.draft", "review": "pearnly_erp_portal.masters.guide.review",
     "confirm": "pearnly_erp_portal.masters.guide.confirm", "result": "pearnly_core.assistant.guide.form_result.result",
     "tool": "erp_company_note_save", "expires": ["company"]},
    {"id": "company_add", "label_key": "guide.business.company_add", "icon": "building-2", "order": 90,
     "modes": ["chat"], "ledgers": ["express_local", "express_cloud", "mrerp"],
     "allowed": "pearnly_erp_portal.masters.cc_guide.allowed_add",
     "opening_key": "guide.open.company_add", "source": "form", "writes": True,
     "steps": [
         {"key": "companies", "input": "multi", "rule": "always", "search": True,
          "options": "pearnly_erp_portal.masters.guide.pending_options"},
     ],
     "draft": "pearnly_erp_portal.masters.guide.add_draft", "review": "pearnly_erp_portal.masters.guide.add_review",
     "confirm": "pearnly_erp_portal.masters.guide.add_confirm", "result": "pearnly_erp_portal.masters.guide.add_result",
     "tool": "erp_company_add"},
]
