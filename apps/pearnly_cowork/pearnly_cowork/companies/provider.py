"""Cowork 交给共用层的 provider（ADR-020，hooks `pearnly_assistant_projects`）：CC 的对话按公司主档归组。

共用层（`pearnly_core.assistant.projects`）只认这里的 `KIND / PERIOD / SCOPE / allowed / view / context`：
- `allowed`：主档在，且这个人能录入（`scope.allowed("intake")`）。
- `view`：侧栏组头显示的名称与副标题（税号）。
- `context`：给 CC 的公司说明（`service.context_text`），出错记日志、回空串，不挡发话。
- `SCOPE = True`：对话可以再指定一个分店（5 位分店号，空 = 不指定，补充 甲.2）。
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
