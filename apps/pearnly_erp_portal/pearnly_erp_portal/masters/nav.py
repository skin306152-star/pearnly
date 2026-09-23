"""侧栏页面过滤：**这家公司不做收银，收银那三页就不出现**。

用户 2026-09-24 的口径："新增是否启用 POS 收银台 …… 不勾选就不显示 pos、记录、交接班，
以及团队管理里的收银"。收银台是**公司级**开关，而"哪家公司开了"只有本产品知道，
所以共用层只负责调产品声明里的 `page_filter`（见 `pearnly_core.desk.navigation`），
本模块就是那个回调：只做显隐，**真正的授权在服务端**（`access.guard.require` 对
`cashier.*` 会再过一遍 `require_pos`）。
"""

from pearnly_erp_portal.masters import company as companies

# 与 `hooks.py` 里 `pearnly_navigation` 那三个收银页声明的 feature 一致。
POS_FEATURE = "cashier"


def filter_pages(offered: dict) -> dict:
    """`(offered) -> offered`：本账号能用的公司里只要有一家开了收银就全留，否则摘掉收银页。"""
    if companies.pos_enabled_any():
        return offered
    return {route: value for route, value in offered.items()
            if (value[3] if isinstance(value, (list, tuple)) and len(value) > 3 else None)
            != POS_FEATURE}
