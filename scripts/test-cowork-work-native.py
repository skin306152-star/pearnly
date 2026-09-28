"""Cowork 任务管理（默认看板）：在真实 Frappe Site 上走一遍服务端（整段在一个事务里，最后回滚）。

多块看板与看板权限另见 `test-cowork-work-boards-native.py`；这里的标签 / 字段 / 自动化设置用 Cowork 管理员做
（2026-09-29 起只有看板管理员与 Cowork 管理员能改），普通成员照常贴标签、填字段值。

副作用：只写指定的本地 / 测试 Site；建几个临时用户、若干列表 / 卡片 / 标签 / 字段 / 活动 / 提醒和附件，
结束时 `frappe.db.rollback()` 全部撤掉（附件的磁盘文件由原生 File 的回滚清理）。不连外部、不发消息。
站点上原有的看板数据只读不改（脚本里改到的都随回滚撤回）。退出码非 0 = 有断言失败。

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
    from frappe.utils import add_days, add_to_date, now_datetime, today

    from pearnly_cowork.team import setup
    from pearnly_cowork.work import access, api, reminders, service
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
    assert board["lists"], "default lists missing"
    before_cards = len(board["cards"])

    # ── 列表：新建、改名、颜色、完成列表、排序 ──
    todo = service.create_list("T-todo")["name"]
    doing = service.create_list("T-doing")["name"]
    done = service.create_list("T-done")["name"]
    service.update_list(done, {"is_done_list": 1, "color": "green"})
    service.update_list(doing, {"title": "  T-doing 2 "})
    names = {row.name: row for row in service.board()["lists"]}
    assert names[doing].title == "T-doing 2" and names[done].is_done_list and names[done].color == "green"
    expect_error("blank list", service.create_list, "  ", error=Invalid)
    expect_error("bad list color", service.update_list, doing, {"color": "#fff"}, error=Invalid)
    service.move_list(done, None, todo)  # 放到 T-todo 前面
    order = [row.name for row in service.board()["lists"]]
    assert order.index(done) < order.index(todo), order

    # ── 卡片：新建、拖动、完成列表、完成圆圈 ──
    a = service.create("  First   card ", todo)
    b = service.create("Second", todo)
    c = service.create("Third", doing)
    assert a["title"] == "First card" and a["position"] < b["position"] and not a["completed"]
    expect_error("blank title", service.create, "  ", todo, error=Invalid)
    expect_error("bad list", service.create, "x", "nope", error=Invalid)
    expect_error("native insert", lambda: frappe.get_doc({"doctype": access.CARD, "title": "x", "board_list": todo,
        "workspace": frappe.get_value(access.CARD, a["name"], "workspace")}).insert(ignore_permissions=True))
    doc = frappe.get_doc(access.CARD, a["name"])
    doc.title = "hijack"
    expect_error("native save", doc.save, error=frappe.PermissionError)

    moved = service.move(b["name"], todo, None, a["name"])
    assert moved["position"] < a["position"]
    moved = service.move(c["name"], done)
    assert moved["board_list"] == done and moved["completed"], moved
    moved = service.move(c["name"], doing)
    assert not moved["completed"]
    toggled = service.toggle_done(c["name"])
    assert toggled["completed"] and toggled["board_list"] == doing, "circle must not move the card"
    assert not service.toggle_done(c["name"])["completed"]

    # 重复到期：完成 → 到期日滚到下一次，仍未完成；拖进完成列表也一样、卡留在原列表。
    detail = service.card(a["name"])
    service.update(a["name"], detail["modified"], {"due_date": "2026-01-31", "recurrence": "monthly"})
    rolled = service.toggle_done(a["name"])
    assert str(rolled["due_date"]) == "2026-02-28" and not rolled["completed"], rolled
    rolled = service.move(a["name"], done)
    assert str(rolled["due_date"]) == "2026-03-28" and rolled["board_list"] == todo, rolled
    detail = service.card(a["name"])
    service.update(a["name"], detail["modified"], {"recurrence": "never", "due_date": ""})

    # ── 详情：日期、提醒、成员、标签、多个清单、自定义字段、封面、模板 ──
    expect_error("member saves labels", service.save_labels, [], error=frappe.PermissionError)
    frappe.set_user(admin)
    labels = service.save_labels([*[{"name": row.name, "title": row.title, "color": row.color}
                                    for row in service.labels(service.board()["board"]["name"])],
                                  {"title": "Urgent", "color": "red"}, {"title": "", "color": "blue"}])
    red = next(row for row in labels if row.color == "red")
    fields = service.save_fields([
        {"title": "Amount", "fieldtype": "number", "show_on_card": 1},
        {"title": "Priority", "fieldtype": "select", "options": ["High", "Low"], "show_on_card": 1},
        {"title": "Paid", "fieldtype": "checkbox"}])
    amount, priority, paid = (next(row.name for row in fields if row.title == title)
                              for title in ("Amount", "Priority", "Paid"))
    expect_error("select without options", service.save_fields,
                 [{"title": "X", "fieldtype": "select", "options": []}], error=Invalid)
    frappe.set_user(worker)

    detail = service.card(a["name"])
    updated = service.update(a["name"], detail["modified"], {
        "title": "Renamed", "start_date": add_days(today(), -1), "due_date": add_days(today(), 1),
        "due_time": "10:30", "reminder": "1h", "assignees": [other, worker], "labels": [red.name],
        "description": "<p>Hello</p>",
        "checklists": [{"title": "Prepare", "items": [{"title": "one", "done": 1}, {"title": "two"}]},
                       {"title": "Empty", "items": []}],
        "values": {amount: "12.50", priority: "High", paid: True}})
    assert updated["title"] == "Renamed" and updated["reminder"] == "1h" and updated["due_time"].startswith("10:30")
    assert [group["title"] for group in updated["checklists"]] == ["Prepare", "Empty"]
    assert updated["values"] == {amount: "12.5", priority: "High", paid: "1"}, updated["values"]
    summary = service.summary(a["name"])
    assert summary["checklist_done"] == 1 and summary["checklist_total"] == 2 and summary["has_description"]
    expect_error("start after due", service.update, a["name"], updated["modified"],
                 {"start_date": add_days(today(), 5)}, error=Invalid)
    expect_error("bad reminder", service.update, a["name"], updated["modified"], {"reminder": "3d"}, error=Invalid)
    expect_error("bad field value", service.update, a["name"], updated["modified"],
                 {"values": {priority: "Nope"}}, error=Invalid)
    expect_error("conflict", service.update, a["name"], detail["modified"], {"title": "stale"}, error=Invalid)
    expect_error("bad member", service.update, a["name"], updated["modified"], {"assignees": [outsider]}, error=Invalid)
    # 下拉少了的选项：卡片上的值一起清掉；删字段：值也清掉。
    frappe.set_user(admin)
    service.save_fields([{"name": amount, "title": "Amount", "fieldtype": "number", "show_on_card": 1},
                         {"name": priority, "title": "Priority", "fieldtype": "select", "options": ["Low"]}])
    frappe.set_user(worker)
    assert service.card(a["name"])["values"] == {amount: "12.5"}, service.card(a["name"])["values"]

    # 模板：设为模板 → 照模板建卡（带清单、标签、成员、字段；清单项不带完成状态）。
    fresh = service.card(a["name"])
    service.update(a["name"], fresh["modified"], {"is_template": 1})
    made = service.create("From template", doing, a["name"])
    card = service.card(made["name"])
    assert card["labels"] == [red.name] and set(card["assignees"]) == {other, worker}
    assert [item["done"] for item in card["checklists"][0]["items"]] == [0, 0]
    assert card["values"] == {amount: "12.5"} and not card["is_template"]
    expect_error("not a template", service.create, "x", doing, b["name"], error=Invalid)
    fresh = service.card(a["name"])
    service.update(a["name"], fresh["modified"], {"is_template": 0})

    # 复制卡片、镜像。
    copied = service.copy(a["name"], "Renamed (copy)", doing, ["checklist", "labels", "members", "fields"])
    assert copied["board_list"] == doing and copied["labels"] == [red.name]
    mirror = service.mirror(a["name"], doing)["name"]
    expect_error("mirror twice", service.mirror, a["name"], doing, error=Invalid)
    expect_error("mirror same list", service.mirror, a["name"], todo, error=Invalid)
    assert any(row.name == mirror for row in service.board()["mirrors"])
    service.move_mirror(mirror, done)
    service.unmirror(mirror)
    assert not any(row.card == a["name"] for row in service.board()["mirrors"])

    # 附件：文件（私有、按卡片读权限）+ 链接；封面只认本卡图片。
    files = service.attach(a["name"], FileStorage(stream=io.BytesIO(b"hello"), filename="note.txt"))
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
    linked = service.attach_link(a["name"], "https://example.com/doc", "Spec")
    assert any(row.is_link and row.file_name == "Spec" for row in linked)
    expect_error("bad link", service.attach_link, a["name"], "javascript:alert(1)", error=Invalid)
    link_file = next(row.name for row in linked if row.is_link)
    expect_error("link cover", service.update, a["name"], service.card(a["name"])["modified"],
                 {"cover_file": link_file}, error=Invalid)
    image = service.attach(a["name"], FileStorage(stream=io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"0" * 32),
                                                  filename="cover.png"))
    image = next(row for row in image if row.file_name.startswith("cover"))
    service.update(a["name"], service.card(a["name"])["modified"], {"cover_file": image.name})
    assert service.summary(a["name"])["cover_url"]
    service.detach(a["name"], image.name)
    assert not service.card(a["name"])["cover_file"]

    # 关注、@、提醒：自己做的不提醒自己；只看得到自己的提醒；完成也提醒相关的人。
    NOTE = access.NOTIFICATION
    service.watch(a["name"], True)
    assert service.summary(a["name"])["watching"]
    frappe.set_user(other)
    service.comment(a["name"], "@Work please check", [worker, outsider])
    frappe.set_user(worker)
    kinds = {row.kind for row in service.notifications()["rows"] if row.card == a["name"]}
    assert "mention" in kinds and "comment" not in kinds, kinds
    assert not frappe.db.exists(NOTE, {"user": outsider}), "outsider notified"
    mine = service.notifications()
    frappe.set_user(other)
    assert not {row.name for row in service.notifications()["rows"]} & {row.name for row in mine["rows"]}
    expect_error("read others", lambda: frappe.get_doc(NOTE, mine["rows"][0].name).check_permission("read"),
                 error=frappe.PermissionError)
    frappe.set_user(worker)
    service.toggle_done(a["name"])
    assert frappe.db.exists(NOTE, {"user": other, "card": a["name"], "kind": "complete"}), "complete not notified"
    service.toggle_done(a["name"])
    assert service.read_notifications()["unread"] == 0

    # 到期提醒：按提醒时间发一次；过期发一次；重跑不重复。
    soon = add_to_date(now_datetime(), minutes=30)
    fresh = service.card(a["name"])
    service.update(a["name"], fresh["modified"], {"due_date": str(soon.date()), "due_time": soon.strftime("%H:%M"),
                                                  "reminder": "1h"})
    frappe.set_user("Administrator")
    reminders.run()
    reminders.run()
    frappe.set_user(worker)
    reminded = [row for row in service.notifications()["rows"] if row.kind == "reminder" and row.card == a["name"]]
    assert len(reminded) == 1, reminded
    fresh = service.card(a["name"])
    service.update(a["name"], fresh["modified"], {"due_date": add_days(today(), -1), "due_time": ""})
    frappe.set_user("Administrator")
    reminders.run()
    reminders.run()
    frappe.set_user(worker)
    overdue = [row for row in service.notifications()["rows"] if row.kind == "overdue" and row.card == a["name"]]
    assert len(overdue) == 1, overdue

    # ── 自动化：卡片进入列表（拖入 / 新建）时分派、加标签、到期日、关注、完成 ──
    ruled = service.create_list("T-ruled")["name"]
    expect_error("member reads rules", service.rules, error=frappe.PermissionError)
    frappe.set_user(admin)
    saved = service.save_rules([
        {"board_list": ruled, "action": "assign", "user": other},
        {"board_list": ruled, "action": "label", "label": red.name},
        {"board_list": ruled, "action": "due_days", "days": 3},
        {"board_list": ruled, "action": "watch", "user": worker}])
    assert len(saved) == 4
    expect_error("rule outsider", service.save_rules, [{"board_list": ruled, "action": "assign", "user": outsider}],
                 error=Invalid)
    expect_error("rule days", service.save_rules, [{"board_list": ruled, "action": "due_days", "days": 999}],
                 error=Invalid)
    frappe.set_user(worker)
    auto = service.create("Auto card", todo)
    service.move(auto["name"], ruled)
    card = service.card(auto["name"])
    assert other in card["assignees"] and red.name in card["labels"] and worker in card["watchers"], card
    assert str(card["due_date"]) == add_days(today(), 3), card["due_date"]
    assert any(row.action == "rule" for row in card["activity"])
    created = service.card(service.create("Born here", ruled)["name"])
    assert other in created["assignees"], "rules must run on create too"
    frappe.set_user(admin)
    service.save_rules([*[{"name": row.name, "board_list": row.board_list, "action": row.action, "user": row.user,
                           "label": row.label, "days": row.days, "enabled": 0} for row in saved],
                        {"board_list": ruled, "action": "complete"}])
    frappe.set_user(worker)
    completed = service.create("Completes", ruled)
    assert completed["completed"] and not completed["assignees"], completed

    # ── 全文搜索（标题 / 描述 / 评论 / 清单项 / 字段值）与导出 ──
    assert a["name"] in service.search("hello")          # 描述
    assert a["name"] in service.search("please check")   # 评论
    assert a["name"] in service.search("Prepare") or a["name"] in service.search("one")  # 清单项
    assert auto["name"] in service.search("auto CARD")    # 标题、不区分大小写
    assert service.search("x") == []
    content = service.export([a["name"], auto["name"]])
    assert content[:2] == b"PK" and len(content) > 1000, len(content)

    # ── 列表菜单：排序、复制列表、整列移动、归档 / 恢复列表、整列归档 ──
    service.create("aaa", todo)
    service.sort_list(todo, "title")
    titles = [row["title"] for row in sorted(service.board()["cards"], key=lambda row: row["position"])
              if row["board_list"] == todo]
    assert titles == sorted(titles, key=str.casefold), titles
    copied_list = service.copy_list(todo, "T-todo copy")["name"]
    assert len([row for row in service.board()["cards"] if row["board_list"] == copied_list]) == len(titles)
    service.move_all(copied_list, doing)
    assert not [row for row in service.board()["cards"] if row["board_list"] == copied_list]
    service.archive_list(copied_list)
    assert copied_list in {row.name for row in service.archived()["lists"]}
    assert copied_list not in {row.name for row in service.board()["lists"]}
    expect_error("worker delete list", service.delete_list, copied_list, error=frappe.PermissionError)
    service.restore_list(copied_list)
    service.archive_list(copied_list)
    extra = service.create("to archive", done)
    in_done = {row["name"] for row in service.board()["cards"] if row["board_list"] == done}
    assert service.archive_all(done)["archived"] == len(in_done)
    for name in in_done:
        service.restore(name)

    # ── 归档 / 恢复 / 彻底删除（只给管理员、只删已归档的）──
    service.archive(b["name"])
    assert b["name"] in {row.name for row in service.archived()["cards"]}
    expect_error("worker delete", service.delete, b["name"], error=frappe.PermissionError)
    service.restore(b["name"])
    frappe.set_user(admin)
    expect_error("delete unarchived", service.delete, b["name"], error=Invalid)
    service.archive(b["name"])
    service.delete(b["name"])
    assert not frappe.db.exists(access.CARD, b["name"])
    service.delete_list(copied_list)
    assert not frappe.db.exists(access.COLUMN, copied_list)

    frappe.set_user(worker)
    expect_error("api invalid", api.create, "  ", todo, error=frappe.ValidationError)
    # 站点上原有的卡片数量不受影响（新建的都在本脚本的列表里）。
    ours = {todo, doing, done, ruled}
    assert len([row for row in service.board()["cards"] if row["board_list"] not in ours]) == before_cards
    assert extra["name"]


if __name__ == "__main__":
    try:
        main(sys.argv[1])
    except AssertionError as error:
        import traceback
        line = traceback.extract_tb(error.__traceback__)[-1]
        print(f"RESULT: FAIL line {line.lineno}: {line.line} {error}")
        raise SystemExit(1)
