"""Cowork 交给共用层的 provider（ADR-020，hooks `pearnly_assistant_projects`）：CC 的对话按公司主档归组。

共用层（`pearnly_core.assistant.projects`）只认这里的 `KIND / PERIOD / SCOPE / allowed / view / context`：
- `allowed`：主档在，且这个人能录入（`scope.allowed("intake")`）。
- `view`：侧栏组头显示的名称与副标题（税号）。
- `context`：给 CC 的公司说明（`service.context_text`），出错记日志、回空串，不挡发话。
- `SCOPE = True`：对话可以再指定一个分店（5 位分店号，空 = 不指定，补充 甲.2）。
- `LEDGER` / `ledgers`：本对话账套（共用层 `pearnly_core.assistant.ledger`，A 线 11-12）：这家（对话指定的分店）的账套，
  覆盖本月的按 `identity.covering`，多本时按记住的现用账套 / 可写优先（`current_book.pick`，同定时任务与自动推），恰好一本即默认；
  一本都不覆盖用最新一本可写的（同原右栏兜底）。编号 = 账套与电脑无关的编号（`identity.book_id`，同 ERP 的 ``book_key``，
  A 线 11-12）；11-12 以前存下的 ``"<连接>|<账套键>"``（:func:`ledger_id` / :func:`ledger_parts`）读时经 :func:`canonical_ledger` 映射。
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
LEDGER = True
LEDGER_SEP = "|"
LEDGER_OPTIONS_MAX = 50
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


def ledger_id(connection, key) -> str:
    return f"{connection or ''}{LEDGER_SEP}{key or ''}"


def ledger_parts(value) -> tuple[str, str]:
    """本对话账套编号 → (连接, 账套键)；认不出回两个空串。"""
    connection, sep, key = str(value or "").partition(LEDGER_SEP)
    return (connection, key) if sep and connection and key else ("", "")


def _month(period) -> str:
    if period:
        return str(period)
    from frappe.utils import nowdate

    return str(nowdate())[:7]


def ledgers(ref, period, work_scope, user) -> dict:
    """这家这个月（对话指定的分店）可选的账套与默认（见文件头）。只读：读接口也调（`listed_books`，不补存主档税号）。"""
    from pearnly_cowork.companies import current_book, service

    if not service.exists(ref):
        return {"options": [], "default": ""}
    row = service.get(ref)
    sets = service.listed_books(row, None)
    branch = identity.scope_value(work_scope) or ""
    sets = (identity.scoped(sets, branch) or sets) if branch else sets
    month = _month(period)
    covering = identity.covering(sets, month)
    if len(covering) > 1:
        workspace = frappe.db.get_value(COMPANY, ref, "workspace")
        picked = current_book.pick(workspace, row.get("identity"), f"{month}-01", covering)
        chosen = picked[0] if len(picked) == 1 else None
    elif covering:
        chosen = covering[0]
    else:
        chosen = (identity.writable_first(sets) or [None])[0]  # 一本都不覆盖：最新一本可写的
    marked = {id(item) for item in covering}
    ordered = covering + [item for item in sets if id(item) not in marked and item.get("writable") is not False]
    options = [{"id": item["identity"], "label": item.get("label") or item.get("key"),
                "covers": id(item) in marked} for item in ordered[:LEDGER_OPTIONS_MAX]]
    return {"options": options, "default": chosen["identity"] if chosen else ""}


def book_of(ref, value, work_scope="") -> dict | None:
    """本对话账套编号（新编号，或 11-12 以前存下的「连接|键」）→ 这家的那本账套（`service` 的样子，带 ``places``）；
    不是这家的、认不出回 None。只读。"""
    from pearnly_cowork.companies import service

    text = str(value or "")
    if not text or not service.exists(ref):
        return None
    sets = service.listed_books(service.get(ref), None)
    branch = identity.scope_value(work_scope) or ""
    sets = (identity.scoped(sets, branch) or sets) if branch else sets
    connection, key = ledger_parts(text)
    for item in sets:
        if item["identity"] == text or (key and any(
                place[0] == connection and _same_key(place[1], key) for place in item.get("places") or ())):
            return item
    return None


def canonical_ledger(ref, value) -> str:
    """共用层读本对话账套 / 写入计划时的映射（``canonical_ledger`` 钩子）：旧编号「连接|键」换成这本现在的编号，
    认不出照原样（按失效处理，同账套从目录消失）。"""
    found = book_of(ref, value)
    return found["identity"] if found else str(value or "")


def _same_key(left, right) -> bool:
    from pearnly_core.express_rules.targets.identity import normalize_express_account_key as norm

    return norm(left).casefold() == norm(right).casefold()


def context(ref, period, work_scope, user) -> str:
    from pearnly_cowork.companies import service

    try:
        return service.context_text(ref, period or "", work_scope if identity.scope_value(work_scope) else "",
                                    ledger=True)
    except Exception:
        frappe.log_error(title="Cowork company context failed")
        return ""
