"""定时入口：每分钟推进文件夹录入（没有待办文件时 `pump.run` 几条小查询就返回）；每 5 分钟发定时任务的前一天提醒与
完成简报（`notices`）。"""


def tick():
    from pearnly_cowork.folder_intake import pump

    pump.run()


def notices(now=None):
    """前一天提醒（`reminders`）与完成简报（`briefs`），两样各自出错只记日志、互不影响。

    ``now``：站点时区的时间字符串，测试环境手动触发用（例如
    ``bench --site <site> execute pearnly_cowork.folder_intake.schedule.notices --kwargs "{'now': '2026-10-14 08:05'}"``）。
    用将来的时刻触发会真的记下并推送提醒，真实时间到了同一时段不会再发（去重键相同）。"""
    import frappe

    from pearnly_cowork.folder_intake import briefs, reminders

    for job in (reminders.run, briefs.run):
        try:
            job(now)
        except Exception:
            frappe.db.rollback()
            frappe.log_error(title="Cowork folder notices failed", message=frappe.get_traceback()[-1500:])
