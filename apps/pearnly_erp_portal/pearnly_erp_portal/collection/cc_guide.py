"""ERP 版 CC 的引导声明：收款（ADR-009：产品自己声明；形状见 ``pearnly_core.assistant.guide.declaration``，总设计
``docs/work/2026-11-14-cc-guided-master-design.md`` 2.2）。``GUIDES`` 是纯字面量：闸门脚本不导入本模块，直接按源码读它。

收款只在 ERP、只做直写（用户 11-14）。现在是 ``chat``：菜单上有「收款」、CC 发一句开场，之后走打字（``erp_collection_*`` 工具）；
按表单一步步问（客户 → 未收清发票 → 方式 → 凭证 → 复核 → 确定）在总设计 P1 换成 ``form``，那时步骤与选项函数写在这里。
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
     "opening_key": "guide.open.collection", "source": "chat", "writes": True,
     "tool": "erp_collection_draft", "expires": ["ledger", "company", "master"]},
]
