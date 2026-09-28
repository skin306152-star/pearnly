"""Cowork 任务管理的多块看板与看板权限：在真实 Frappe Site 上逐个接口走一遍（整段一个事务，最后回滚）。

每个接口覆盖：成功、没有任务管理权限、观察者、私有看板的非成员、团队可见看板的非成员（只能看）、Cowork 管理员（口径见
`docs/work/2026-09-29-cowork-work-boards.md` 的权限矩阵）。另查实时刷新只推给能看的人、提醒按看板过滤、
原生 REST / 附件下载按看板判、跨看板移动 / 复制 / 镜像两边都要权限、升级补丁可重跑。

副作用：只写指定的本地 / 测试 Site；建几个临时用户、两块看板和若干列表 / 卡片 / 标签 / 附件，
结束时 `frappe.db.rollback()` 全部撤掉；过程中任何提交都会被拦下并判失败。不连外部、不发消息。
退出码非 0 = 有断言失败。

用法（在装有本工作树代码的 bench 容器里，站点目录下）：
    ../env/bin/python /tmp/test-cowork-work-boards-native.py <site>
"""
import io
import sys

import frappe
from werkzeug.datastructures import FileStorage

DENIED = frappe.PermissionError


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
    commit = frappe.db.commit

    def refuse_commit(*args, **kwargs):
        raise AssertionError("unexpected commit inside the test transaction")

    frappe.db.commit = refuse_commit
    try:
        run()
        print("RESULT: PASS")
    finally:
        frappe.db.commit = commit
        frappe.db.rollback()
        frappe.destroy()


def run():
    from pearnly_cowork.patches import v0_0_9_work_boards
    from pearnly_cowork.team import setup
    from pearnly_cowork.work import access, api, boards, notify, reminders, service
    from pearnly_cowork.work.contracts import Invalid

    assert frappe.conf.get("pearnly_product") == "cowork", "site is not a Cowork site"
    setup.sync_roles()
    owner = user("boards-owner@example.invalid", ["Desk User", "Cowork Work"])
    member = user("boards-member@example.invalid", ["Desk User", "Cowork Work"])
    observer = user("boards-observer@example.invalid", ["Desk User", "Cowork Work"])
    stranger = user("boards-stranger@example.invalid", ["Desk User", "Cowork Work"])
    cadmin = user("boards-cadmin@example.invalid", ["Desk User", "Cowork Admin"])
    outsider = user("boards-outsider@example.invalid", ["Desk User", "Cowork Intake"])
    access.forget()

    def as_user(who, function, *args, **kwargs):
        frappe.set_user(who)
        return function(*args, **kwargs)

    def denied(label, who, function, *args, error=DENIED):
        frappe.set_user(who)
        expect_error(f"{label} as {who}", function, *args, error=error)

    def matrix(label, function, *args, ok=(), no=()):
        """`no` 里的人都被拒绝（PermissionError）；`ok` 里的人都成功。返回最后一个成功的结果。"""
        for who in no:
            denied(label, who, function, *args)
        result = None
        for who in ok:
            result = as_user(who, function, *args)
        return result

    # 升级补丁可以重跑：不再建第二块默认看板。
    frappe.set_user("Administrator")
    default = boards.default_board(frappe.get_all("Cowork Workspace", filters={"enabled": 1}, pluck="name",
                                                  order_by="creation asc", limit_page_length=1)[0])
    v0_0_9_work_boards.execute()
    assert frappe.db.count(access.BOARD, {"is_default": 1}) == 1

    # ── 看板：新建、列表、设置 ──
    denied("boards", outsider, boards.boards)
    denied("create board", outsider, boards.create, {"title": "x"})
    frappe.set_user(owner)
    expect_error("blank board", boards.create, {"title": " "}, error=Invalid)
    expect_error("bad visibility", boards.create, {"title": "x", "visibility": "public"}, error=Invalid)
    private = boards.create({"title": "P-private", "visibility": "private"})
    team = boards.create({"title": "T-team"})
    P, T = private["name"], team["name"]
    assert private["role"] == "admin" and set(private["rights"]) == {"view", "edit", "comment", "members", "manage"}
    assert team["visibility"] == "team"
    assert len(service.lists(P)) == 3 and len(service.labels(P)) == 6, "new board needs default lists and labels"
    listed = {row["name"] for row in as_user(stranger, boards.boards)}
    assert T in listed and default in listed and P not in listed, listed
    assert P in {row["name"] for row in as_user(cadmin, boards.boards)}
    assert as_user(stranger, boards.boards)[0]["is_default"], "default board comes first"

    matrix("update board", boards.update, P, {"title": "P-private 2"}, ok=(owner, cadmin),
           no=(outsider, stranger))
    denied("default board archive", cadmin, boards.archive, default, error=Invalid)

    # ── 成员：看板管理员加人；「所有成员」时成员能加减成员 / 观察者；只有管理员改角色；自己能离开 ──
    frappe.set_user(owner)
    boards.save_members(P, [{"user": owner, "role": "admin"}, {"user": member, "role": "member"},
                            {"user": observer, "role": "observer"}])
    expect_error("add outsider", boards.save_members, P, [{"user": owner, "role": "admin"},
                                                          {"user": outsider, "role": "member"}], error=Invalid)
    expect_error("last admin", boards.save_members, P, [{"user": member, "role": "member"}], error=Invalid)
    current = [{"user": owner, "role": "admin"}, {"user": member, "role": "member"},
               {"user": observer, "role": "observer"}]
    denied("members read", stranger, boards.members, P)
    denied("members read", outsider, boards.members, P)
    assert {row["user"] for row in as_user(observer, boards.members, P)["members"]} == {owner, member, observer}
    assert not as_user(observer, boards.members, P)["candidates"], "observer cannot add people"
    assert stranger in {row["user"] for row in as_user(member, boards.members, P)["candidates"]}
    frappe.set_user(member)
    boards.save_members(P, [*current, {"user": stranger, "role": "observer"}])
    boards.save_members(P, current)
    for wanted in ([*current, {"user": stranger, "role": "admin"}],
                   [{**row, "role": "member"} if row["user"] == observer else row for row in current],
                   [row for row in current if row["user"] != owner]):
        expect_error("member limits", boards.save_members, P, wanted, error=Invalid)
    denied("observer adds", observer, boards.save_members, P, [*current, {"user": stranger, "role": "member"}],
           error=Invalid)
    denied("stranger adds", stranger, boards.save_members, P, [*current, {"user": stranger, "role": "member"}])
    as_user(observer, boards.save_members, P, [row for row in current if row["user"] != observer])
    assert not access.can(P, "view", observer), "left the board"
    as_user(cadmin, boards.save_members, P, current)
    as_user(owner, boards.update, P, {"member_management": "admins"})
    denied("admins only", member, boards.save_members, P, [*current, {"user": stranger, "role": "member"}],
           error=Invalid)

    # ── 读：整板、归档、搜索、导出、fallback ──
    for function in (service.board, service.archived):
        matrix(function.__name__, function, P, ok=(owner, member, observer, cadmin), no=(outsider, stranger))
    assert set(as_user(observer, service.board, P)["board"]["rights"]) == {"view"}
    assert as_user(stranger, service.board, P, True)["board"]["name"] == default, "fallback to the default board"
    denied("fallback outsider", outsider, service.board, P, True)

    # ── 列表 ──
    todo, doing, done = (row.name for row in service.lists(P))
    matrix("create list", service.create_list, "L-new", P, ok=(member, cadmin), no=(outsider, observer, stranger))
    extra = service.lists(P)[-1].name
    for label, function, args in (
            ("update list", service.update_list, (extra, {"color": "blue"})),
            ("move list", service.move_list, (extra, None, todo)),
            ("sort list", service.sort_list, (todo, "title")),
            ("move all", service.move_all, (extra, doing)),
            ("archive all", service.archive_all, (extra,)),
            ("copy list", service.copy_list, (extra, "L-copy"))):
        matrix(label, function, *args, ok=(member, cadmin), no=(outsider, observer, stranger))
    matrix("archive list", service.archive_list, extra, ok=(member,), no=(outsider, observer, stranger))
    matrix("restore list", service.restore_list, extra, ok=(member,), no=(outsider, observer, stranger))
    as_user(member, service.archive_list, extra)
    matrix("delete list", service.delete_list, extra, ok=(owner,), no=(outsider, observer, stranger, member))

    # ── 卡片 ──
    card = matrix("create card", service.create, "C-one", todo, ok=(member,), no=(outsider, observer, stranger))["name"]
    as_user(cadmin, service.create, "C-admin", todo)
    matrix("card", service.card, card, ok=(owner, member, observer, cadmin), no=(outsider, stranger))
    matrix("summary", service.summary, card, ok=(observer, cadmin), no=(outsider, stranger))
    assert as_user(observer, service.card, card)["rights"] == ["view"]
    modified = lambda: frappe.db.get_value(access.CARD, card, "modified")  # noqa: E731
    for who in (outsider, observer, stranger):
        frappe.set_user(who)
        expect_error(f"update as {who}", service.update, card, str(modified()), {"title": "x"}, error=DENIED)
    as_user(member, service.update, card, str(modified()), {"title": "C-one 2", "assignees": [member]})
    frappe.set_user(member)
    expect_error("assign stranger", service.update, card, str(modified()), {"assignees": [stranger]}, error=Invalid)
    # 负责人只能是能改这块板的人：观察者能看，但不能被分派。
    expect_error("assign observer", service.update, card, str(modified()), {"assignees": [observer]}, error=Invalid)
    for label, function, args in (("move", service.move, (card, doing)), ("toggle", service.toggle_done, (card,)),
                                  ("toggle back", service.toggle_done, (card,))):
        matrix(label, function, *args, ok=(member, cadmin), no=(outsider, observer, stranger))
    matrix("watch", service.watch, card, True, ok=(observer, member, cadmin), no=(outsider, stranger))
    matrix("archive card", service.archive, card, ok=(member,), no=(outsider, observer, stranger))
    matrix("restore card", service.restore, card, ok=(member,), no=(outsider, observer, stranger))
    as_user(member, service.archive, card)
    matrix("delete card", service.delete, card, ok=(owner,), no=(outsider, observer, stranger, member))
    card = as_user(member, service.create, "C-two", todo)["name"]
    as_user(member, service.update, card, str(modified()), {"assignees": [member], "labels": [service.labels(P)[0].name]})

    # 附件：能改的人才能加 / 删；原生下载按看板判（观察者能看、私有看板外的人不能）。
    matrix("attach", service.attach, card, None, no=(outsider, observer, stranger))
    files = as_user(member, service.attach, card, FileStorage(stream=io.BytesIO(b"hello"), filename="note.txt"))
    matrix("attach link", service.attach_link, card, "https://example.com/x", ok=(member,),
           no=(outsider, observer, stranger))
    file_doc = frappe.get_doc("File", files[0].name)
    for who, expected in ((observer, True), (cadmin, True), (stranger, False), (outsider, False)):
        frappe.set_user(who)
        try:
            readable = frappe.has_permission("File", "read", file_doc)
        except frappe.PermissionError:
            readable = False
        assert bool(readable) == expected, (who, readable)
    # 链接附件原生当公开文件：私有看板外的人也读不到；原生 File 列表不列出看不到的看板上的附件。
    link_file = next(row.name for row in service._attachments(card) if row.is_link)
    for who, expected in ((observer, True), (stranger, False), (outsider, False)):
        frappe.set_user(who)
        try:
            readable = frappe.has_permission("File", "read", frappe.get_doc("File", link_file))
        except frappe.PermissionError:
            readable = False
        assert bool(readable) == expected, ("link", who, readable)
    files_of = lambda: set(frappe.get_list("File", filters={"attached_to_doctype": access.CARD},  # noqa: E731
                                           pluck="name"))
    assert {file_doc.name, link_file} <= as_user(observer, files_of)
    assert not {file_doc.name, link_file} & as_user(stranger, files_of), "private board attachments listed"
    matrix("detach", service.detach, card, files[0].name, ok=(member,), no=(outsider, observer, stranger))

    # 评论：按「谁能评论」；删自己的评论只要能看。
    matrix("comment", service.comment, card, "hello", [], ok=(member, cadmin), no=(outsider, observer, stranger))
    as_user(owner, boards.update, P, {"commenting": "observers"})
    mine = as_user(observer, service.comment, card, "@member look", [member, stranger])
    note = next(row.name for row in mine if row.actor == observer)
    assert not frappe.db.exists(access.NOTIFICATION, {"user": stranger}), "stranger was notified"
    assert frappe.db.exists(access.NOTIFICATION, {"user": member, "card": card, "kind": "mention"})
    denied("delete others comment", member, service.delete_comment, note)
    as_user(observer, service.delete_comment, note)
    # 看板管理员（和 Cowork 管理员）能删别人的评论。
    for who in (owner, cadmin):
        mine = as_user(member, service.comment, card, f"by member for {who}", [])
        as_user(who, service.delete_comment, next(row.name for row in mine if row.actor == member))
    as_user(owner, boards.update, P, {"commenting": "disabled"})
    matrix("comment off", service.comment, card, "x", [], ok=(owner, cadmin), no=(member, observer))
    as_user(owner, boards.update, P, {"commenting": "members"})

    # ── 标签 / 字段 / 自动化：只有看板管理员与 Cowork 管理员 ──
    keep = [{"name": row.name, "title": row.title, "color": row.color} for row in service.labels(P)]
    matrix("save labels", service.save_labels, [*keep, {"title": "Hot", "color": "red"}], P, ok=(owner, cadmin),
           no=(outsider, member, observer, stranger))
    hot = next(row.name for row in service.labels(P) if row.title == "Hot")
    matrix("save fields", service.save_fields, [{"title": "Amount", "fieldtype": "number"}], P, ok=(owner, cadmin),
           no=(outsider, member, observer, stranger))
    frappe.set_user(member)
    service.update(card, str(modified()), {"labels": [hot]})  # 成员照常贴已有标签
    matrix("rules", service.rules, P, ok=(owner, cadmin), no=(outsider, member, observer, stranger))
    rule = [{"board_list": done, "action": "label", "label": hot}]
    matrix("save rules", service.save_rules, rule, P, ok=(owner,), no=(outsider, member, observer, stranger))
    frappe.set_user(owner)
    expect_error("rule stranger", service.save_rules, [{"board_list": done, "action": "assign", "user": stranger}],
                 P, error=Invalid)
    expect_error("rule list of another board", service.save_rules,
                 [{"board_list": service.lists(T)[0].name, "action": "complete"}], P, error=Invalid)
    ruled = as_user(member, service.create, "C-ruled", done)
    assert hot in ruled["labels"], "rule set up by the admin runs for members"
    # 原生：规则只给能管的人读。
    native = lambda doctype, **filters: frappe.get_list(doctype, filters=filters, pluck="name")  # noqa: E731
    assert not as_user(member, native, access.RULE, board=P)
    assert as_user(owner, native, access.RULE, board=P)

    # ── 搜索 / 导出 ──
    matrix("search", service.search, "C-two", P, ok=(observer, cadmin), no=(outsider, stranger))
    assert card in as_user(observer, service.search, "C-two", P)
    assert card not in as_user(stranger, service.search, "C-two", default)
    matrix("export", service.export, None, P, ok=(observer,), no=(outsider, stranger))

    # ── 原生 REST：列表查询与行权限按看板 ──
    assert card in as_user(observer, native, access.CARD, board=P)
    assert not as_user(stranger, native, access.CARD, board=P)
    assert P not in as_user(stranger, native, access.BOARD)
    denied("native read", stranger, lambda: frappe.get_doc(access.CARD, card).check_permission("read"))
    as_user(cadmin, lambda: frappe.get_doc(access.CARD, card).check_permission("read"))
    denied("native write", member, lambda: frappe.get_doc(access.BOARD, P).save())

    # ── 实时刷新只推给能看的人；提醒按看板过滤 ──
    sent = []
    publish = frappe.publish_realtime
    frappe.publish_realtime = lambda event, message=None, **kwargs: sent.append((event, kwargs.get("user")))
    try:
        as_user(member, service.update, card, str(modified()), {"title": "C-two 2"})
    finally:
        frappe.publish_realtime = publish
    changed = {who for event, who in sent if event == notify.CHANGED}
    assert None not in changed and stranger not in changed and {owner, member, observer, cadmin} <= changed, changed
    frappe.set_user(owner)
    as_user(observer, service.watch, card, True)
    as_user(member, service.comment, card, "news", [])
    assert any(row.card == card for row in as_user(observer, service.notifications)["rows"])
    as_user(owner, boards.save_members, P, [row for row in current if row["user"] != observer])
    assert not any(row.card == card for row in as_user(observer, service.notifications)["rows"]), \
        "notifications of a board you left must disappear"
    assert observer not in access.viewers(P) and stranger not in access.viewers(P)
    due = frappe.utils.add_days(frappe.utils.today(), -1)
    as_user(owner, service.update, card, str(modified()), {"due_date": due})
    as_user("Administrator", reminders.run)
    assert not frappe.db.exists(access.NOTIFICATION, {"user": observer, "card": card, "kind": "overdue"})
    as_user(owner, boards.save_members, P, current)

    # ── 团队可见的板（用户 2026-09-29 定 B）：没加进来的人只能看，评论按观察者算；加为成员后才能改 ──
    tlist = service.lists(T)[0].name
    first = service.lists(default)[0].name
    assert set(as_user(stranger, service.board, T)["board"]["rights"]) == {"view"}
    team_card = as_user(owner, service.create, "T-card", tlist)["name"]
    for label, function, args in (("team create", service.create, ("x", tlist)),
                                  ("team list", service.create_list, ("x", T)),
                                  ("team move", service.move, (team_card, tlist)),
                                  ("team comment", service.comment, (team_card, "x", []))):
        matrix(label, function, *args, ok=(owner, cadmin), no=(outsider, stranger, member))
    matrix("team archive", service.archive, team_card, ok=(owner,), no=(outsider, stranger, member))
    matrix("team restore", service.restore, team_card, ok=(owner,), no=(outsider, stranger, member))
    as_user(owner, boards.update, T, {"commenting": "observers"})
    as_user(stranger, service.comment, team_card, "non-member comment", [])
    as_user(owner, boards.update, T, {"commenting": "members"})
    frappe.set_user(owner)
    expect_error("assign non-member", service.update, team_card,
                 str(frappe.db.get_value(access.CARD, team_card, "modified")), {"assignees": [stranger]}, error=Invalid)
    expect_error("rule assigns non-member", service.save_rules,
                 [{"board_list": tlist, "action": "assign", "user": stranger}], T, error=Invalid)
    service.save_rules([{"board_list": tlist, "action": "watch", "user": stranger}], T)  # 能看就能被加关注
    service.save_rules([], T)
    assert not {row["user"]: row for row in service.board(T)["members"]}[stranger]["can_edit"]
    as_user(owner, boards.save_members, T, [{"user": owner, "role": "admin"}, {"user": member, "role": "member"}])
    as_user(member, service.create, "T-by-member", tlist)
    assert {row["user"]: row for row in service.board(T)["members"]}[member]["can_edit"]
    # 默认看板例外：有任务管理权限的人照常都能改。
    assert set(as_user(stranger, service.board, default)["board"]["rights"]) == {"view", "edit", "comment"}

    # ── 跨看板：移动、复制、镜像两边都要权限 ──
    denied("move into private", stranger, service.move, as_user(stranger, service.create, "S-card", first)["name"],
           todo)
    denied("observer moves out", observer, service.move, card, tlist)
    moved = as_user(member, service.move, card, tlist)
    assert moved["board"] == T and hot not in moved["labels"], "labels of the old board are dropped"
    as_user(member, service.move, card, todo)
    # 观察者能看源卡，但不是团队板成员：复制不进去。
    matrix("copy to team", service.copy, card, "copy", tlist, [], ok=(member,), no=(outsider, stranger, observer))
    denied("copy into private", stranger, service.copy, as_user(stranger, service.create, "S2", first)["name"], "c",
           todo)
    mirror = as_user(member, service.mirror, card, tlist)["name"]
    assert mirror not in {row.name for row in as_user(stranger, service.board, T)["mirrors"]}, "mirror leaked"
    team_view = as_user(owner, service.board, T)
    assert mirror in {row.name for row in team_view["mirrors"]} and card in {row["name"] for row in team_view["foreign"]}
    # 镜像在团队板上：只有能改那块板的人能移除。
    matrix("unmirror", service.unmirror, mirror, ok=(member,), no=(outsider, stranger, observer))

    # ── 归档看板：只读；默认看板不受影响 ──
    matrix("archive board", boards.archive, P, ok=(owner,), no=(outsider, member, observer, stranger))
    as_user(member, service.board, P)
    denied("create on archived", member, service.create, "x", todo)
    assert P in {row["name"] for row in as_user(owner, boards.boards, True)}
    assert P not in {row["name"] for row in as_user(member, boards.boards, True)}
    matrix("restore board", boards.restore, P, ok=(owner,), no=(outsider, member, stranger))
    as_user(member, service.create, "back", todo)
    # 默认看板：有任务管理权限的人照常能看能改，标签等设置只有 Cowork 管理员。
    as_user(stranger, service.create, "default card", first)
    denied("default labels", stranger, service.save_labels, [], default)

    # 接口层：输入错误翻成 ValidationError，权限错误原样抛。
    frappe.set_user(member)
    expect_error("api invalid", api.create_board, "{}", error=frappe.ValidationError)
    expect_error("api denied", api.rules, P, error=DENIED)


if __name__ == "__main__":
    try:
        main(sys.argv[1])
    except AssertionError as error:
        import traceback
        line = traceback.extract_tb(error.__traceback__)[-1]
        print(f"RESULT: FAIL line {line.lineno}: {line.line} {error}")
        raise SystemExit(1)
