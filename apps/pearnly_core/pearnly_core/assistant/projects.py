"""对话归在哪家（ADR-020，同 Claude Code 按项目分；Cowork 按公司主档）：共用层只记对话挂在哪一项、处理哪个月。

- 会话表 `project`（产品给的不透明编号，空 = 「最近」）、`period`（`YYYY-MM`，空 = 不指定）、`project_scope`（产品自定的
  范围，Cowork 是分店号；空 = 不指定）。`sessions.create(project, period, scope)` / `sessions.move` 写，`release` 清。
- 这些编号是什么、谁能用、给 CC 的说明写什么，由产品在 hooks 声明的 provider 决定（`pearnly_assistant_projects`，
  ADR-009；Cowork：`pearnly_cowork.companies.provider`）。共用层只调它的 `KIND / PERIOD / SCOPE / allowed / view /
  context`，不解析产品数据、不建表。没有声明的产品（ERP、DMS）`kind` 为 None：侧栏与接口都与以前一样，`project`
  一律当空。`view` / `context` 出错记日志、当作没有：不挡侧栏、不挡发话。
- 实时事件 `pearnly.project` 经 `sidebar.project` 在提交后逐人推（`announce`）：对话改月份、分店（`move`，用户 10-17 起不能
  换公司）、删公司时回到「最近」只推会话主人；产品那一项建、改、删由产品挑要推的人。
- 依赖方向：`sessions` → `projects` → `sidebar`；本模块不导入 `sessions`、`relay`。
"""
import importlib

import frappe
from frappe import _

from pearnly_core.assistant import config, context, contracts, sidebar

SESSION = context.SESSION
HOOK = "pearnly_assistant_projects"
ANNOUNCE_MAX = 500  # 一次最多推给这么多人


def _user() -> str:
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("Not signed in"), frappe.AuthenticationError)
    return user


def provider():
    """产品声明的 provider 模块（取第一个声明）；没有声明回 None。"""
    names = frappe.get_hooks(HOOK) or []
    return importlib.import_module(names[0]) if names else None


def kind():
    found = provider()
    return getattr(found, "KIND", None) if found else None


def takes_period(found=None) -> bool:
    found = found if found is not None else provider()
    return bool(found and getattr(found, "PERIOD", False))


def takes_scope(found=None) -> bool:
    found = found if found is not None else provider()
    return bool(found and getattr(found, "SCOPE", False))


def allowed(project, user) -> bool:
    """这一项此刻能挂对话（产品判：存在、这个人能用）。没有 provider、编号不合形状一律不能。"""
    found = provider()
    if not found:
        return False
    try:
        ref = contracts.project_ref(project)
    except contracts.ContractError:
        return False
    if not ref:
        return False
    try:
        return bool(found.allowed(ref, user))
    except Exception:
        frappe.log_error(title="Assistant project check failed")
        return False


def placement(project, period, scope, user) -> tuple[str, str, str]:
    """新建 / 移动时落在哪：产品不认的编号落回「最近」（月份、范围一并清空）；产品不收月份、范围的照样丢掉。
    月份、范围形状不对按请求错误拒绝（页面只会传合法值）。"""
    period = contracts.period(period)
    scope = contracts.project_scope(scope)
    ref = str(project or "").strip()
    if not ref or not allowed(ref, user):
        return "", "", ""
    found = provider()
    scope = scope if takes_scope(found) else ""
    check = getattr(found, "valid_scope", None)
    if scope and check and not check(scope):
        raise contracts.ContractError("invalid scope")
    return ref, period if takes_period(found) else "", scope


def _view(refs, user) -> dict:
    found = provider()
    if not found or not refs:
        return {}
    try:
        return found.view(list(refs), user) or {}
    except Exception:
        frappe.log_error(title="Assistant project view failed")
        return {}


def mine(user, refs=None) -> dict:
    """本人当前引擎、没归档的对话按项统计：`{编号: {"last_active", "count"}}`（一条 GROUP BY）；`refs` 只看这些项。"""
    values = {"user": user, "engine": config.engine_code()}
    only = ""
    if refs is not None:
        refs = [ref for ref in dict.fromkeys(str(r or "") for r in refs) if ref]
        if not refs:
            return {}
        values["refs"] = tuple(refs)
        only = "AND project IN %(refs)s"
    found = frappe.db.sql(f"""SELECT project, COUNT(*) AS count, MAX(modified) AS last FROM `tab{SESSION}`
        WHERE user = %(user)s AND engine_code = %(engine)s AND archived = 0 AND IFNULL(project, '') != '' {only}
        GROUP BY project""", values, as_dict=True)
    return {row.project: {"last_active": str(row.last or ""), "count": int(row.count or 0)} for row in found}


def listing() -> dict:
    """侧栏要的：`kind`（None = 产品没有声明，侧栏照旧）、收不收月份 / 范围，与本人有对话的各项（按最近活动排）。
    产品不认的编号不列出，那些对话显示在「最近」。"""
    user = _user()
    found = provider()
    out = {"kind": getattr(found, "KIND", None) if found else None, "period": takes_period(found),
           "scope": takes_scope(found), "projects": []}
    if not found:
        return out
    stats = mine(user)
    views = _view(stats, user)
    rows = []
    for ref, stat in stats.items():
        view = views.get(ref)
        if not view:
            continue
        rows.append({"id": ref, "title": str(view.get("title") or ""), "subtitle": str(view.get("subtitle") or ""),
                     "last_active": stat["last_active"], "count": stat["count"]})
    rows.sort(key=lambda row: row["last_active"], reverse=True)
    out["projects"] = rows
    return out


def of_session(session_id) -> tuple[str, str, str]:
    """这段对话挂在哪项、哪个月、哪个范围（只认本人的会话，不是本人的回三个空串）。产品的工具缺省取本对话的公司用。"""
    try:
        sid = contracts.session_id(session_id)
    except contracts.ContractError:
        return "", "", ""
    row = frappe.db.get_value(SESSION, {"session_id": sid, "user": frappe.session.user},
                              ["project", "period", "project_scope"], as_dict=True)
    if not row:
        return "", "", ""
    return row.project or "", row.period or "", row.project_scope or ""


def announce(users, op, after_commit=True, **fields) -> None:
    """按人逐个推一条 `pearnly.project`（`contracts.project_change`）；每人只进自己的房间，不广播。"""
    for user in list(dict.fromkeys(u for u in users or () if u and u != "Guest"))[:ANNOUNCE_MAX]:
        sidebar.project(user, op, after_commit=after_commit, **fields)


def release(ref) -> list:
    """这一项被删了：所有人挂在这项的会话（含归档、别的引擎的）归回「最近」，月份、范围一并清空，不改最后活动时间；
    提交后按受影响的人、每段对话各推一次 `move`。返回受影响的会话主人。"""
    ref = contracts.project_id(ref)
    rows = frappe.get_all(SESSION, filters={"project": ref}, fields=["name", "session_id", "user"],
                          limit_page_length=0)
    if not rows:
        return []
    frappe.db.sql(f"""UPDATE `tab{SESSION}` SET project = '', period = '', project_scope = ''
        WHERE project = %s""", (ref,))
    for row in rows:
        sidebar.project(row.user, "move", session=row.session_id, project="")
    return sorted({row.user for row in rows})


def note(row, user) -> tuple[str, str | None]:
    """这一批要带的说明与发出后记进 `project_noted` 的值（`contracts.project_note`）。说明由产品现算（`context`），
    里面不放会变的状态（电脑在不在线），否则状态一变就重带。产品不认这项（已删）按不在任何项处理；产品出错记日志、
    这次不带，不挡发话。"""
    project = row.get("project") or ""
    text = ""
    found = provider() if project else None
    if project and not found:
        project = ""
    elif project:
        try:
            if found.allowed(project, user):
                text = found.context(project, row.get("period") or "", row.get("project_scope") or "", user) or ""
            else:
                project = ""
        except Exception:
            frappe.log_error(title="Assistant project context failed")
            text = ""
    return contracts.project_note(project, row.get("project_noted"), text)
