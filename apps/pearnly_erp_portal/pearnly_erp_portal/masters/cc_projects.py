"""ERP 交给共用层的 provider（ADR-020，hooks `pearnly_assistant_projects`，2026-11-02）：ERP 版 CC 的对话按 ERP 公司归组。

共用层（`pearnly_core.assistant.projects`）只认这里的 `KIND / PERIOD / SCOPE / API / SECTIONS / allowed / view / context`：
- 公司就是 `ERP Company` 的一行（编号 = 文档名），不另建主档；`SCOPE = False`：ERP 一家公司已经是「税号 + 分店」，不再分店。
- `allowed`：能看公司（`company.read`），且这家在本人可见公司里（`company.allowed_companies`，只含启用的）。
- `view`：侧栏组头的名称与副标题（税号，不是总公司的带分店号）。
- `context`：给 CC 的公司说明（`cc_service.context_text`），出错记日志、回空串，不挡发话。
- `API` / `SECTIONS`：给页面的声明（共用层 `projects.listing` 原样转交）：公司接口前缀与 ERP 有的分区——待处理、知识库、
  从账套添加公司（`create`，同 Cowork 的弹窗）、公司在 ERP 里改（`desk`：公司页「在浏览器中打开」）。Cowork 的月结台账、单据文件夹、定时任务、记住的选择、申报草稿、改名、删除、
  刷新账套 ERP 没有，页面就不出。
"""
import frappe

from pearnly_erp_portal.access import guard

KIND = "company"
PERIOD = True
SCOPE = False
API = "pearnly_erp_portal.masters.cc_companies."
SECTIONS = ("pending", "notes", "create", "desk")
REFS_MAX = 500


def sections() -> tuple:
    """本人看到的分区：「添加公司」（`create`）只给能建公司的人（`cc_service.can_build`，候选与建公司在服务端同样拦）。"""
    from pearnly_erp_portal.masters import cc_service

    return tuple(item for item in SECTIONS if item != "create" or cc_service.can_build())


def _visible(user) -> set:
    from pearnly_erp_portal.masters import cc_service

    if not user or user == "Guest" or not guard.has("company.read", user=user):
        return set()
    return set(cc_service.visible(user))


def allowed(ref, user) -> bool:
    return isinstance(ref, str) and bool(ref) and len(ref) <= 140 and ref in _visible(user)


def view(refs, user) -> dict:
    """`{编号: {title, subtitle}}`；不存在的、停用的、这个人看不到的不给（那些对话显示在「最近」）。"""
    from pearnly_erp_portal.masters import cc_service

    seen = _visible(user)
    names = [ref for ref in dict.fromkeys(refs or ()) if isinstance(ref, str) and ref in seen][:REFS_MAX]
    if not names:
        return {}
    rows = frappe.get_all(cc_service.COMPANY, filters={"name": ["in", names], "is_active": 1},
                          fields=["name", "company_name", "tax_id", "branch"], limit_page_length=REFS_MAX)
    return {row.name: {"title": row.company_name or row.name, "subtitle": cc_service.subtitle(row)} for row in rows}


def context(ref, period, work_scope, user) -> str:
    from pearnly_erp_portal.masters import cc_service

    try:
        return cc_service.context_text(ref, period or "", user)
    except Exception:
        frappe.log_error(title="ERP company context failed")
        return ""
