"""Cowork 交给共用层的 provider（ADR-020，hooks `pearnly_assistant_projects`）：CC 的对话按公司主档归组。

共用层（`pearnly_core.assistant.projects`）只认这里的 `KIND / PERIOD / SCOPE / allowed / view / context`：
- `allowed`：主档在，且这个人能录入（`scope.allowed("intake")`）。
- `view`：侧栏组头显示的名称与副标题（税号）。
- `context`：给 CC 的公司说明（`service.context_text`），出错记日志、回空串，不挡发话。
- `SCOPE = True`：对话可以再指定一个分店（5 位分店号，空 = 不指定，补充 甲.2）。
- `API` / `SECTIONS`：给页面的声明（共用层 `projects.listing` 原样转交）：公司接口的方法前缀，与 Cowork 有的全部分区
  （页面只出声明了的；`sections()` 按本人收窄：改名、删除只给管理员；ERP 声明自己的一份，见 `pearnly_erp_portal.masters.cc_projects`）。
"""
import frappe

from pearnly_cowork.access import scope
from pearnly_cowork.companies import identity
from pearnly_cowork.companies.access import COMPANY

KIND = "company"
PERIOD = True
SCOPE = True
API = "pearnly_cowork.companies.api."
# 待处理、月结台账、知识库、单据文件夹、定时任务、记住的选择、申报草稿；从账套目录添加公司、改名、删除、刷新账套
SECTIONS = ("pending", "month", "notes", "folders", "tasks", "choices", "drafts", "create", "rename", "delete", "catalog")
MANAGE = ("rename", "delete")  # 改名、删除公司只给 Cowork 管理员（11-07 定案 #2）


def connect() -> dict:
    """设置 → 连接最上面的 MR.ERP 一行（11-07）：Cowork 的 MR.ERP 主连接接口（`connections.api`，谁能改网址、授权由它判）。"""
    return {"mrerp": "pearnly_cowork.connections.api."}


def sections() -> tuple:
    """本人看到的分区：改名、删除只给 Cowork 管理员（`service.require_manage` 在服务端同样拦）。"""
    admin = scope.administrator()
    return tuple(item for item in SECTIONS if admin or item not in MANAGE)


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
