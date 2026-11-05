"""产品怎么接写账前置判定（多产品共用，ADR-009）：产品在自己的 ``hooks.py`` 里声明 ``pearnly_write_precheck``，共用层只读它。

    pearnly_write_precheck = {
        "allowed": "<点路径>",   # (user, company) -> bool：谁能对这家公司取卡片、作答、搜可选值（各用自己的写账权限；共用接口先过这一道，
                                 # 产品在 check / remember / search 里仍按单据核）
        "check": "<点路径>",     # (company, doc_ids) -> {"company_title"?, "documents": [...]}：逐张判这批单（与写入同一套判断，
                                 # 不建写账件、不改单据），每张的形状是 ``contracts.merge`` 的输入：``{doc_id, state, reason?,
                                 # ref?, party?, record?, direction?, target?, items: [{kind, subject, label, value, value_label,
                                 # source, options?, search?, direction?}]}``。要人定的项一次收齐（录不录、入账方式、方向、
                                 # 账套、科目组、对方 / 存货 / 科目 / 现赊……），每项带现在的值与来源（记住的 = 已定，规则 /
                                 # 历史 / 建议 = 预选，没有 = 未定）。权限由产品核：只回当前用户能写的单，别人的单不报存在与否
        "remember": "<点路径>",  # (company, answers) -> {"saved": int}：``answers`` = ``{doc_id: [{kind, subject, value, label,
                                 # remember}]}``（``contracts.expand`` 的结果），按各类现有的记法记下（账套记忆、公司规则、
                                 # 租户口径……），值的校验与谁能选照现有写入函数，不合法的 frappe.throw；``remember`` 为假的只管这一张
        "search": "<点路径>",    # (company, doc_id, search, txt, start, page_length) -> {"options": [{code, name}], "more": bool}：
                                 # 大列表（科目 accounts、存货 products、对方 parties……）按这张单写进的账套服务端分页；超过
                                 # ``contracts.SMALL_LIST`` 条的项不在 item 里给 options，只给预选值与 ``search``
        "resume": "<点路径>",    # 可选：(company, doc_ids) -> [{"doc_id", "status", "reason"}]：待处理里停着等人定的单，
                                 # 记下答案后按产品原来的写入路径重新排队（定时任务、自动推停下的那些）；没声明 = 不重排
    }

没声明的站点（DMS、控制面）：没有可判的单。一个站点只装一个产品，取最后声明的那一份。
"""
import frappe

HOOK = "pearnly_write_precheck"


def _path(key: str) -> str:
    found = frappe.get_hooks(HOOK) or {}
    values = found.get(key) if isinstance(found, dict) else None
    values = values if isinstance(values, list) else [values]
    values = [value for value in values if isinstance(value, str) and value]
    return values[-1] if values else ""


def enabled() -> bool:
    return bool(_path("allowed") and _path("check") and _path("remember"))


def allowed(user, company) -> bool:
    """没声明的产品一律不能用；游客、管理员（不是租户里的人）不能用。"""
    path = _path("allowed")
    return bool(path and user and user not in ("Guest", "Administrator") and company
                and frappe.get_attr(path)(user, company))


def check(company, doc_ids) -> dict:
    path = _path("check")
    if not path:
        return {"documents": []}
    found = frappe.get_attr(path)(company, list(doc_ids))
    return found if isinstance(found, dict) else {"documents": []}


def remember(company, answers: dict) -> dict:
    path = _path("remember")
    if not path or not answers:
        return {"saved": 0}
    return frappe.get_attr(path)(company, answers) or {"saved": 0}


def search(company, doc_id, kind, txt, start, page_length) -> dict:
    path = _path("search")
    if not path:
        return {"options": [], "more": False}
    found = frappe.get_attr(path)(company, doc_id, kind, txt, start, page_length) or {}
    options = [{"code": str(row.get("code") or ""), "name": str(row.get("name") or "")}
               for row in found.get("options") or () if isinstance(row, dict) and row.get("code")]
    return {"options": options[:page_length], "more": bool(found.get("more"))}


def resume(company, doc_ids) -> list:
    path = _path("resume")
    if not path or not doc_ids:
        return []
    return list(frappe.get_attr(path)(company, list(doc_ids)) or ())
