"""旧代录任务补建会话（统一对话框架 14.2 / 14.8，主会话 11-04 审定）：代录任务就是一段会话，进直写同一个会话列表。
还没有会话的任务（没在任务里打过字的）补建一行 ``op_<任务号>``（只建本站这一行，不调引擎、不调模型；远端会话第一次发话时再开），
挂在任务的公司、月份上，标题、星标、最后活动时间照任务；任务的 ``session_id`` 指回来。已经有会话的（任务里打过字的）不动。
幂等：再跑一次只补还没补的。没配引擎的站点（不列会话）不补。"""
import frappe

TASK = "Pearnly Express Operator Task"
SESSION = "Pearnly Assistant Session"


def missing(tasks, sessions) -> list:
    """还要补的任务：没有会话编号，或编号指的会话不在了。``sessions``：已有会话编号的集合。"""
    return [task for task in tasks if not task.get("session_id") or task.get("session_id") not in sessions]


def execute():
    if not frappe.db.table_exists(TASK) or not frappe.db.table_exists(SESSION):
        return
    from pearnly_core.assistant import config, sessions

    if not config.engine_code():
        return
    rows = frappe.get_all(TASK, fields=["name", "owner", "company", "company_title", "period", "title", "starred",
                                        "session_id", "creation", "modified"], limit_page_length=0)  # bounded: 一次性迁移
    known = set(frappe.get_all(SESSION, filters={"session_id": ["in", [r.session_id for r in rows if r.session_id] or [""]]},
                               pluck="session_id", limit_page_length=0))
    for task in missing(rows, known):
        sid = sessions.open_operator(task)
        if not sid:
            continue
        frappe.db.set_value(SESSION, {"session_id": sid}, {"creation": task.creation, "modified": task.modified},
                            update_modified=False)
        frappe.db.set_value(TASK, task.name, "session_id", sid, update_modified=False)
