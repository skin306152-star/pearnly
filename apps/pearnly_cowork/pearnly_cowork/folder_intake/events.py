"""识别一结束就接着推进文件夹录入（`cowork_ocr_recognized` 钩子，排在 LINE 之后）；推送状态一变就刷新那份文件的结果
（`cowork_push_state_changed` 钩子）。

只对文件夹交来的识别单（请求键 `folder:` 开头）排一个推进任务，不查库、不写库；推进本身照旧读本功能自己的表，
所以别处的请求键碰巧也这样开头，最多多跑一次推进，不会改动任何东西。定时器每分钟的推进照旧兜底。
"""
import json

HISTORY = "Cowork OCR History"
FILE = "Cowork Folder Intake File"
# 推送状态变了要马上刷新的文件：推送中（看能不能完成）、已完成（结果与结果表格跟着变）；其余状态由后台推进自己走
REFRESHED = ("pushing", "done")


def recognized(batch):
    if str(batch.get("request_key") or "").startswith("folder:"):
        from pearnly_cowork.folder_intake import pump

        pump.kick()


def pushed(history):
    """一张票的推送状态 / 原因变了（推送账本投影时，任何入口推的：后台、网页、手机、CC）：它是定时任务的票，就在事务
    提交后排一次刷新（`pump.refresh`），那份文件的结果与结果表格马上更新，不等每 10 分钟的跟进（2026-10-13 测试机：
    CC 重推三天 POS，一份文件的结果表格和运行结果停在重推之前）。这里只读、不锁、不写，在推送的事务里调用。"""
    import frappe

    source = frappe.db.get_value(HISTORY, history, "source_file")
    if not source:
        return
    files = [row.name for row in frappe.get_all(FILE, filters={"source_file": source, "state": ["in", REFRESHED]},
                                                 fields=["name", "histories_json"], ignore_permissions=True)
             if history in json.loads(row.histories_json or "[]")]
    if files:
        frappe.enqueue("pearnly_cowork.folder_intake.pump.refresh", queue="default", enqueue_after_commit=True,
                       files=files)
