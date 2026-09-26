"""Cowork 工作协调看板：在真实 Frappe Site 上走一遍服务端（整段在一个事务里，最后回滚）。

副作用：只写指定的本地 / 测试 Site；建两个临时用户、若干卡片 / 标签 / 活动和一个附件文件，
结束时 `frappe.db.rollback()` 全部撤掉（附件的磁盘文件由原生 File 的回滚清理）。不连外部、不发消息。
退出码非 0 = 有断言失败。

用法（在装有本工作树代码的 bench 容器里，站点目录下）：
    ../env/bin/python /tmp/test-cowork-work-native.py <site>
"""
import io
import sys

import frappe
from werkzeug.datastructures import FileStorage


def expect_error(label, function, *args, error=Exception):
    try:
        function(*args)
    except error:
        frappe.clear_last_message()
        frappe.local.message_log = []
        return
    raise AssertionError(f"{label}: expected {error.__name__}")


def user(email, roles):
    doc = frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0],
                          "send_welcome_email": 0, "enabled": 1,
                          "roles": [{"role": role} for role in roles]})
    doc.flags.no_welcome_mail = True
    return doc.insert(ignore_permissions=True).name


def main(site):
    frappe.init(site=site)
    frappe.connect()
    try:
        run()
        print("RESULT: PASS")
    finally:
        frappe.db.rollback()
        frappe.destroy()


def run():
    from pearnly_cowork.team import setup
    from pearnly_cowork.work import access, api, service
    from pearnly_cowork.work.contracts import Invalid

    assert frappe.conf.get("pearnly_product") == "cowork", "site is not a Cowork site"
    setup.sync_roles()
    worker = user("work-worker@example.invalid", ["Desk User", "Cowork Work"])
    other = user("work-other@example.invalid", ["Desk User", "Cowork Work"])
    admin = user("work-admin@example.invalid", ["Desk User", "Cowork Admin"])
    outsider = user("work-outsider@example.invalid", ["Desk User", "Cowork Intake"])

    # 没勾「工作协调」的人什么都拿不到。
    frappe.set_user(outsider)
    expect_error("outsider board", service.board, error=frappe.PermissionError)
    expect_error("outsider native list", frappe.get_list, access.CARD, error=frappe.PermissionError)

    frappe.set_user(worker)
    board = service.board()
    members = {row["user"] for row in board["members"]}
    assert {worker, other, admin} <= members and outsider not in members, members
    before = len(board["cards"])

    a = service.create("  First   card ", "todo")
    b = service.create("Second", "todo")
    c = service.create("Third", "doing")
    assert a["title"] == "First card" and a["position"] < b["position"], (a, b)
    expect_error("blank title", service.create, "  ", "todo", error=Invalid)
    expect_error("bad status", service.create, "x", "review", error=Invalid)

    # 原生写入口被写入闸拦住（即使调用方自己忽略权限）。
    expect_error("native insert", lambda: frappe.get_doc({"doctype": access.CARD, "title": "x",
        "workspace": frappe.get_value(access.CARD, a["name"], "workspace")}).insert(ignore_permissions=True))
    doc = frappe.get_doc(access.CARD, a["name"])
    doc.title = "hijack"
    expect_error("native save", doc.save, error=frappe.PermissionError)
    # 原生读只读得到本站的卡。
    assert {a["name"], b["name"], c["name"]} <= set(frappe.get_list(access.CARD, pluck="name"))

    # 拖动：插到 a 之前；再拖到另一列两卡之间。
    moved = service.move(b["name"], "todo", None, a["name"])
    assert moved["position"] < a["position"]
    moved = service.move(a["name"], "doing", None, c["name"])
    assert moved["status"] == "doing" and moved["position"] < c["position"]
    done = service.move(c["name"], "done")
    card = frappe.get_doc(access.CARD, c["name"])
    assert done["status"] == "done" and card.completed_at, card.completed_at
    service.move(c["name"], "todo")
    assert not frappe.db.get_value(access.CARD, c["name"], "completed_at")

    # 标签：新建两个、改一个、删一个（卡上同时摘掉）。
    labels = service.save_labels([{"title": "Urgent", "color": "red"}, {"title": "", "color": "blue"}])
    red = next(row for row in labels if row.color == "red")
    blue = next(row for row in labels if row.color == "blue")
    expect_error("bad color", service.save_labels, [{"title": "x", "color": "#fff"}], error=Invalid)

    detail = service.card(a["name"])
    updated = service.update(a["name"], detail["modified"], {
        "title": "Renamed", "due_date": "2026-10-01", "assignees": [other, worker],
        "labels": [red.name, blue.name], "description": "<p>Hello</p>",
        "checklist": [{"title": "one", "done": 1}, {"title": "two"}, {"title": " "}], "status": "done"})
    assert updated["title"] == "Renamed" and updated["status"] == "done"
    assert updated["assignees"] == [other, worker] and len(updated["checklist"]) == 2
    actions = [row.action for row in updated["activity"]]
    assert {"create", "move", "due", "assign"} <= set(actions), actions
    summary = service.summary(a["name"])
    assert summary["checklist_done"] == 1 and summary["checklist_total"] == 2 and summary["has_description"]
    # 旧版本号保存 → 冲突。
    expect_error("conflict", service.update, a["name"], detail["modified"], {"title": "stale"}, error=Invalid)
    expect_error("bad member", service.update, a["name"], updated["modified"], {"assignees": [outsider]}, error=Invalid)
    # 富文本经原生清洗。
    fresh = service.card(a["name"])
    fresh = service.update(a["name"], fresh["modified"], {"description": '<p onclick="x()">hi</p><script>bad()</script>'})
    assert "script" not in fresh["description"] and "onclick" not in fresh["description"], fresh["description"]

    service.save_labels([{"name": red.name, "title": "Now", "color": "orange"}])
    assert service.card(a["name"])["labels"] == [red.name]

    # 评论：只能删自己的。
    rows = service.comment(a["name"], "  hello\nworld ")
    mine = next(row for row in rows if row.action == "comment")
    assert mine.content == "hello\nworld"
    frappe.set_user(other)
    expect_error("delete others' comment", service.delete_comment, mine.name, error=frappe.PermissionError)
    frappe.set_user(worker)
    rows = service.delete_comment(mine.name)
    assert not any(row.action == "comment" for row in rows)

    # 附件：存成挂在卡片上的私有 File；别的成员能按卡片读权限读到。
    files = service.attach(a["name"], FileStorage(stream=io.BytesIO(b"hello"), filename="note.txt"))
    assert len(files) == 1 and files[0].file_url.startswith("/private/files/"), files
    file_doc = frappe.get_doc("File", files[0].name)
    frappe.set_user(other)
    assert frappe.has_permission("File", "read", file_doc), "member cannot read attachment"
    frappe.set_user(outsider)
    try:
        leaked = frappe.has_permission("File", "read", file_doc)
    except frappe.PermissionError:
        leaked = False
    assert not leaked, "outsider can read attachment"
    frappe.set_user(worker)
    assert service.summary(a["name"])["attachments"] == 1
    assert service.detach(a["name"], files[0].name) == []

    # 完成圆圈：进行中 → 已完成 → 回到原来那一列。
    mid = service.create("Circle", "doing")
    assert service.toggle_done(mid["name"])["status"] == "done"
    assert service.toggle_done(mid["name"])["status"] == "doing"

    # 复制：带清单 / 标签 / 成员，放到目标列列尾。
    copied = service.copy(a["name"], "Renamed (copy)", "todo", ["checklist", "labels", "members"])
    card = service.card(copied["name"])
    assert copied["status"] == "todo" and len(card["checklist"]) == 2 and card["labels"] == [red.name]
    assert set(card["assignees"]) == {other, worker}
    assert any(row.action == "copy" for row in card["activity"])

    # 列菜单：按名称排序、整列移到另一列。
    service.create("aaa", "todo")
    service.sort_column("todo", "title")
    todo = [row["title"] for row in sorted(service.board()["cards"], key=lambda row: row["position"])
            if row["status"] == "todo"]
    assert todo == sorted(todo, key=str.casefold), todo
    moved = service.move_column("todo", "doing")["moved"]
    assert moved == len(todo) and not [row for row in service.board()["cards"] if row["status"] == "todo"]
    expect_error("same column", service.move_column, "doing", "doing", error=Invalid)

    # 封面：颜色、图片附件（非本卡 / 非图片拒绝）、删附件时封面一起清掉。
    fresh = service.card(a["name"])
    fresh = service.update(a["name"], fresh["modified"], {"cover_color": "green"})
    assert service.summary(a["name"])["cover_color"] == "green"
    image = service.attach(a["name"], FileStorage(stream=io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"0" * 32), filename="cover.png"))[0]
    text = service.attach(a["name"], FileStorage(stream=io.BytesIO(b"x"), filename="x.txt"))[0]
    expect_error("text cover", service.update, a["name"], service.card(a["name"])["modified"], {"cover_file": text.name},
                 error=Invalid)
    service.update(a["name"], service.card(a["name"])["modified"], {"cover_file": image.name})
    summary = service.summary(a["name"])
    assert summary["cover_url"] and not summary["cover_color"], summary
    service.detach(a["name"], image.name)
    assert not service.card(a["name"])["cover_file"]

    # 关注、@、提醒：自己做的不提醒自己；只看得到自己的提醒。
    NOTE = access.NOTIFICATION
    service.watch(a["name"], True)
    assert service.summary(a["name"])["watching"]
    frappe.set_user(other)
    service.comment(a["name"], "@Work please check", [worker, outsider])
    frappe.set_user(worker)
    kinds = {row.kind for row in service.notifications()["rows"] if row.card == a["name"]}
    assert "mention" in kinds and "comment" not in kinds, kinds
    assert not frappe.db.exists(NOTE, {"user": outsider}), "outsider notified"
    assert not frappe.db.exists(NOTE, {"user": other, "kind": "mention"}), "actor notified"
    mine = service.notifications()
    assert mine["unread"] >= 1
    frappe.set_user(other)
    theirs = {row.name for row in service.notifications()["rows"]}
    assert not theirs & {row.name for row in mine["rows"]}, "notifications leak between users"
    expect_error("read others", lambda: frappe.get_doc(NOTE, mine["rows"][0].name).check_permission("read"),
                 error=frappe.PermissionError)
    frappe.set_user(worker)
    assert service.read_notifications()["unread"] == 0

    # 到期提醒：过期 / 快到期各发一次，重跑不重复。
    from frappe.utils import add_days, today
    from pearnly_cowork.work import reminders
    fresh = service.card(a["name"])
    service.update(a["name"], fresh["modified"], {"due_date": add_days(today(), -1), "status": "doing"})
    frappe.set_user("Administrator")
    reminders.run()
    reminders.run()
    frappe.set_user(worker)
    overdue = [row for row in service.notifications()["rows"] if row.kind == "overdue" and row.card == a["name"]]
    assert len(overdue) == 1, overdue

    # 列表颜色（全队共用、可移除）与整列归档。
    assert service.column_color("doing", "yellow")["color"] == "yellow"
    assert service.board()["column_colors"]["doing"] == "yellow"
    service.column_color("doing", "")
    assert service.board()["column_colors"]["doing"] == ""
    expect_error("bad column color", service.column_color, "doing", "#fff", error=Invalid)
    extra = service.create("to archive", "done")
    in_done = {row["name"] for row in service.board()["cards"] if row["status"] == "done"}
    assert service.archive_column("done")["archived"] == len(in_done)
    assert in_done <= {row.name for row in service.archived()}
    for name in in_done:  # 站点上原有的卡也一起放回去，下面的计数才对得上。
        service.restore(name)

    # 归档 / 恢复 / 彻底删除（只给管理员、只删已归档的）。
    service.archive(b["name"])
    assert b["name"] not in {row["name"] for row in service.board()["cards"]}
    assert b["name"] in {row.name for row in service.archived()}
    expect_error("worker delete", service.delete, b["name"], error=frappe.PermissionError)
    restored = service.restore(b["name"])
    assert restored["name"] == b["name"]
    frappe.set_user(admin)
    expect_error("delete unarchived", service.delete, b["name"], error=Invalid)
    service.archive(b["name"])
    service.delete(b["name"])
    assert not frappe.db.exists(access.CARD, b["name"])
    assert not frappe.get_all(access.ACTIVITY, filters={"card": b["name"]})

    # 接口层把输入错误翻成用户可读的 ValidationError。
    frappe.set_user(worker)
    expect_error("api invalid", api.create, "  ", error=frappe.ValidationError)
    board = service.board()
    assert len(board["cards"]) == before + 6, (before, len(board["cards"]))


if __name__ == "__main__":
    try:
        main(sys.argv[1])
    except AssertionError as error:
        print(f"RESULT: FAIL {error}")
        raise SystemExit(1)
