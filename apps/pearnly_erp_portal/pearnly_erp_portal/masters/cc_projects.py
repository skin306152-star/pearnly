"""ERP 交给共用层的 provider（ADR-020，hooks `pearnly_assistant_projects`，2026-11-02）：ERP 版 CC 的对话按 ERP 公司归组。

共用层（`pearnly_core.assistant.projects`）只认这里的 `KIND / PERIOD / SCOPE / API / SECTIONS / allowed / view / context`：
- 公司就是 `ERP Company` 的一行（编号 = 文档名），不另建主档；`SCOPE = False`：ERP 一家公司已经是「税号 + 分店」，不再分店。
- `allowed`：能看公司（`company.read`），且这家在本人可见公司里（`company.allowed_companies`，只含启用的）。
- `view`：侧栏组头的名称与副标题（税号，不是总公司的带分店号）。
- `context`：给 CC 的公司说明（`cc_service.context_text`），出错记日志、回空串，不挡发话。
- `LEDGER` / `ledgers`：本对话账套（共用层 `pearnly_core.assistant.ledger`，A 线 11-12 D2，同 Cowork）：这家的 Express 账套（税号 +
  分店，`cc_desktop.books.for_company`）按月取覆盖的（`account_match.for_month`，同单据日期的次年段规则）在前；多本时按记过的
  「这家这一年用哪套」（`express.choices.ACCOUNT_SET`，同网页选一次），恰好一本即默认；一本都不覆盖用最新一本可写的。编号 = 账套键；
  名称 = 账套名 · 位置（`target.option_name`）。不接 Express 的公司（MR.ERP 账本、纯 ERP）回 None：不用本对话账套。写入见
  `express.conversation`。
- `API` / `SECTIONS`：给页面的声明（共用层 `projects.listing` 原样转交）：公司接口前缀与 ERP 有的分区——待处理、知识库、
  从账套添加公司（`create`，同 Cowork 的弹窗）、公司在 ERP 里改（`desk`：公司页「在浏览器中打开」）、记住的选择（`choices`：能看能删，
  欠账 B51）。Cowork 的月结台账、单据文件夹、定时任务、申报草稿、改名、删除、
  刷新账套 ERP 没有，页面就不出。
"""
import frappe

from pearnly_erp_portal.access import guard

KIND = "company"
PERIOD = True
SCOPE = False
LEDGER = True
LEDGER_OPTIONS_MAX = 50
API = "pearnly_erp_portal.masters.cc_companies."
SECTIONS = ("pending", "notes", "choices", "create", "desk")
REFS_MAX = 500


def sections() -> tuple:
    """本人看到的分区：「添加公司」（`create`）只给能建公司的人（`cc_service.can_build`，候选与建公司在服务端同样拦）。"""
    from pearnly_erp_portal.masters import cc_service

    return tuple(item for item in SECTIONS if item != "create" or cc_service.can_build())


def connect() -> dict:
    """设置 → 连接：电脑列表（`computers`，只读，欠账 B50）都声明；MR.ERP 一行只在 MR.ERP 账本开着时出（`MRERP_LEDGER_ENABLED`，
    主会话 11-07 定案 #13），开着也只给 ERP 管理员（这一行调的连接接口只限管理员，服务端判；别人不出这一行）。"""
    from pearnly_erp_portal import mrerp
    from pearnly_erp_portal.mrerp import connection

    # 浏览器里「连接」那一栏的电脑列表（只读，欠账 B50）：能看公司的人都有，按声明出、不按产品名写死
    found = {"computers": API + "computers"}
    if mrerp.enabled() and connection.is_admin():
        found["mrerp"] = "pearnly_erp_portal.mrerp.api."
    return found


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


def _this_month() -> str:
    from frappe.utils import nowdate

    return str(nowdate())[:7]


def _span(item):
    return item.get("period_start"), item.get("period_end"), item.get("next_end")


def ledgers(ref, period, work_scope, user) -> dict | None:
    """这家这个月可选的账套与默认（见文件头）；不接 Express 的公司回 None。只读。"""
    import calendar

    from pearnly_core.cc_desktop import books
    from pearnly_core.express_rules.targets import account_match
    from pearnly_erp_portal.express import choices, contracts, target
    from pearnly_erp_portal.masters import cc_service

    row = frappe.db.get_value(cc_service.COMPANY, ref, ["name", "tax_id", "branch", "express_enabled"], as_dict=True) \
        if isinstance(ref, str) and ref else None
    if not row or not row.get("express_enabled"):
        return None  # 账本是 MR.ERP（`mrerp.push.handles` 要求没勾 Express）或纯 ERP 公司
    tax = contracts.tax13(row.get("tax_id"))
    every = books.for_company(tax, row.get("branch")) if tax else []
    month = str(period or "")[:7] or _this_month()
    year, number = int(month[:4]), int(month[5:7])
    first, last = f"{month}-01", f"{month}-{calendar.monthrange(year, number)[1]:02d}"
    covering = account_match.for_month(every, first, last, _span)
    if len(covering) > 1:
        wanted = choices.chosen(row.name, choices.ACCOUNT_SET, target.year_subject(first, covering[0]))
        chosen = next((item for item in covering if item["key"] == wanted), None)
    elif covering:
        chosen = covering[0]
    else:
        writable = [item for item in every if item.get("writable")]
        chosen = max(writable, key=lambda item: str(item.get("period_end") or ""), default=None)
    keys = {item["key"] for item in covering}
    rest = sorted((item for item in every if item["key"] not in keys and item.get("writable")),
                  key=lambda item: str(item.get("period_start") or ""), reverse=True)
    options = [{"id": item["key"], "label": target.option_name(item), "covers": item["key"] in keys}
               for item in [*covering, *rest][:LEDGER_OPTIONS_MAX]]
    return {"options": options, "default": chosen["key"] if chosen else ""}


def context(ref, period, work_scope, user) -> str:
    from pearnly_erp_portal.masters import cc_service

    try:
        return cc_service.context_text(ref, period or "", user)
    except Exception:
        frappe.log_error(title="ERP company context failed")
        return ""
