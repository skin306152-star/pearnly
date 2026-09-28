"""任务管理从"一个团队一块板"升级成多块看板（用户 2026-09-29，照 Trello）。

每个有看板数据的站点归属建一块默认看板（`is_default`、团队可见、评论 = 成员、成员管理 = 所有成员、没有显式成员），
把 `board` 还空着的列表 / 卡片 / 标签 / 字段 / 规则 / 镜像挂上去。团队可见 = 有任务管理权限的人照常能看能改，
现有用户无感；标签 / 字段 / 自动化从此只有看板管理员与 Cowork 管理员能改（用户口径 4、5）。
默认看板不存名字（空），页面按各人的界面语言显示「团队看板」，有人改名后才存。重跑不重复建、不改已经挂好的行。
"""
from datetime import timedelta

import frappe
from frappe.utils import now_datetime

BOARD = "Cowork Work Board"
TABLES = ("Cowork Work Column", "Cowork Work Card", "Cowork Work Label", "Cowork Work Field", "Cowork Work Rule",
          "Cowork Work Mirror")


def execute():
    if not frappe.db.table_exists(BOARD) or not all(frappe.db.has_column(table, "board") for table in TABLES):
        return
    workspaces = set()
    for table in TABLES:
        workspaces |= set(frappe.db.sql_list(f"SELECT DISTINCT workspace FROM `tab{table}`"))
    for workspace in sorted(filter(None, workspaces)):
        board = _default_board(workspace)
        for table in TABLES:
            frappe.db.sql(f"UPDATE `tab{table}` SET board=%s WHERE workspace=%s AND COALESCE(board, '')=''",
                          (board, workspace))


def _default_board(workspace):
    rows = frappe.get_all(BOARD, filters={"workspace": workspace, "is_default": 1}, pluck="name",
                          order_by="creation asc", limit_page_length=1)
    if rows:
        return rows[0]
    # 迁移不经过看板服务（没有登录用户）；直接落原生行，字段与服务写入的一致（同 v0_0_4）。
    doc = frappe.get_doc({"doctype": BOARD, "workspace": workspace, "title": "",
                          "visibility": "team", "commenting": "members", "member_management": "members",
                          "is_default": 1, "archived": 0})
    doc.name = frappe.generate_hash(length=10)
    # 默认看板排在所有看板之前（按建立时间取默认看板时也稳定）。
    doc.creation = doc.modified = now_datetime() - timedelta(seconds=1)
    doc.owner = doc.modified_by = "Administrator"
    doc.db_insert()
    return doc.name
