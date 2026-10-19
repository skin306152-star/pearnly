"""定时任务跟随公司当年账套对 MR.ERP 生效（2026-10-19）：旧的 MR.ERP 任务补上公司主档。

同 `v0_0_23_folder_task_company`（那次只补 Express）：只补 MR.ERP 任务里 ``company`` 还空着的，按任务的 ``account_set_key``
在任务负责人能用的连接目录上认公司（`companies.tasks.company_of`，与保存任务同一判据：连接 + 公司代码），唯一对上一家主档才写
``company``（MR.ERP 没有分店，``company_branch`` 留空）；对不上、负责人读不到目录的留空 —— 照旧推往固定账套。
补上以后，年度库还没读到会计期间时（连接 2026-10-19 之后没刷新过账套）也照旧推往固定账套（`tasks.books_of`），不会停下。
``account_set_key`` 原样保留，不升 ``rule_revision``。幂等：已补的不再动，重跑同一结果。
"""
import frappe

CONNECTION = "Cowork ERP Connection"
RULE = "Cowork Folder Intake Rule"


def execute():
    if not frappe.db.has_column(RULE, "company") or not frappe.db.has_column(RULE, "company_branch"):
        return
    from pearnly_cowork.companies import pending, tasks

    rows = frappe.db.sql(f"""SELECT r.name, r.connection, r.account_set_key, r.rule_owner FROM `tab{RULE}` r
        INNER JOIN `tab{CONNECTION}` c ON c.name = r.connection
        WHERE c.adapter = 'mrerp' AND IFNULL(r.company, '') = '' AND IFNULL(r.account_set_key, '') != ''
            AND IFNULL(r.rule_owner, '') != ''
        ORDER BY r.creation, r.name""", as_dict=True)
    for row in rows:
        try:
            with pending._as(row.rule_owner):
                found = tasks.company_of(row.connection, row.account_set_key)
        except Exception:
            frappe.clear_messages()
            frappe.log_error(title="Cowork MR.ERP task company backfill skipped", message=row.name)
            continue
        if found and found.get("company"):
            frappe.db.set_value(RULE, row.name, {"company": found["company"][:140],
                                                 "company_branch": (found.get("branch") or "")[:10]},
                                update_modified=False)
