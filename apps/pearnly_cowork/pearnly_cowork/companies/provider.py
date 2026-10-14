"""Cowork 交给共用层的 provider（ADR-020，hooks `pearnly_assistant_projects`）：CC 的对话按公司主档归组。

共用层（`pearnly_core.assistant.projects`）只认这里的 `KIND / PERIOD / SCOPE / allowed / view / context`：
- `allowed`：主档在，且这个人能录入（`scope.allowed("intake")`）。
- `view`：侧栏组头显示的名称与副标题（税号）。
- `context`：给 CC 的公司说明（`service.context_text`），出错记日志、回空串，不挡发话。
- `SCOPE = True`：对话可以再指定一个分店（5 位分店号，空 = 不指定，补充 甲.2）。
- `work_branch`：推送前核对分店用（hooks `cowork_push_work_branch`，补充 甲.3）：CC 对话里推送、对话挂的公司包含这份
  账套时，对话指定的分店。
"""
import frappe

from pearnly_cowork.access import scope
from pearnly_cowork.companies import identity
from pearnly_cowork.companies.access import COMPANY

KIND = "company"
PERIOD = True
SCOPE = True


def allowed(ref, user) -> bool:
    if not scope.allowed("intake", user):
        return False
    return bool(isinstance(ref, str) and ref and len(ref) <= 140 and frappe.db.exists(COMPANY, ref))


def valid_scope(value) -> bool:
    return identity.scope_value(value) is not None


def view(refs, user) -> dict:
    """`{编号: {title, subtitle}}`；不存在的、这个人不能用的不给（那些对话显示在「最近」）。"""
    names = [ref for ref in dict.fromkeys(refs or ()) if isinstance(ref, str) and ref][:500]
    if not names or not scope.allowed("intake", user):
        return {}
    rows = frappe.get_all(COMPANY, filters={"name": ["in", names]}, fields=["name", "title", "tax_id"],
                          limit_page_length=0)
    return {row.name: {"title": row.title or "", "subtitle": row.tax_id or ""} for row in rows}


def context(ref, period, work_scope, user) -> str:
    from pearnly_cowork.companies import service

    try:
        return service.context_text(ref, period or "", work_scope if identity.scope_value(work_scope) else "")
    except Exception:
        frappe.log_error(title="Cowork company context failed")
        return ""


def work_branch(connection, account_set) -> str:
    """CC 对话里推送时这段对话指定的分店（`project_scope`），只在推往这段对话所挂公司的账套时给；不在 CC 里、对话没指定
    分店、账套不属于这家都回空串。出错回空串（只是少一道核对，不挡推送）。"""
    from pearnly_core.assistant import projects

    from pearnly_cowork.companies import service
    from pearnly_cowork.targets.identity import normalize_express_account_key

    session = (getattr(frappe.local, "pearnly_assistant", None) or {}).get("session_id") or ""
    if not session:
        return ""
    try:
        project, _period, work_scope = projects.of_session(session)
        work_scope = identity.scope_value(work_scope)
        if not project or not work_scope or not service.exists(project):
            return ""
        key = (normalize_express_account_key(account_set) or str(account_set or "")).casefold()
        sets = service.books(service.get(project))
        if any(item["connection"] == connection and item["key"].casefold() == key for item in sets):
            return work_scope
    except Exception:
        frappe.log_error(title="Cowork company work branch failed")
    return ""
