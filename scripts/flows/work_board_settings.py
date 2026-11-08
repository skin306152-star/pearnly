"""流程用例：任务管理看板「…」菜单逐项实点，每项在原生弹窗里改一处、库里跟着变（ERP、Cowork 同一份页面写法，按站点认产品；
清存量基线 11-08）。

管理员打开任务管理（默认看板）：
- 「提醒」：prepare 让同事在默认看板一张卡上提了管理员一句（未读）→ 弹窗主按钮「全部已读」→ 库里管理员的未读是 0；
- 「已归档」：prepare 在默认看板建一张卡并归档 → 弹窗里这张卡那行点「恢复」→ 库里这张卡不再归档；
- 「新建看板」：填名称、选私有建板 → 库里多一块这个名称的看板，页面换到它（以下几项都在这块新板上；团队看板人人都算成员，没有可加的人）；
- 「看板设置」：名称改掉 → 库里这块板的名称是改后的；
- 「标签」：「添加」一行、填名 →「保存」→ 库里这块板有这个标签；
- 「自定义字段」：「添加」一行、填名 →「保存」→ 库里这块板有这个字段；
- 「自动化」：「添加」一条（默认：进第一个列表就分派给自己）→「保存」→ 库里这块板有一条规则；
- 「成员」：选一位同事、点「添加」→ 库里这块板的成员有他。
cleanup：新建的看板归档（页面同一个接口），prepare 的两张卡归档。
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from desk_list import ACTION  # noqa: E402
from erp_lib import JS, js_value  # noqa: E402

TITLE = "任务管理看板菜单：提醒、已归档、新建看板、看板设置、标签、自定义字段、自动化、成员"
PRODUCTS = {
    "erp": {"app": "pearnly_erp_portal", "root": "pearnly-tasks", "prefix": "erp.work.", "admin": "admin@erp.regression",
            "mate": "tasks@erp.regression", "table": "ERP Work"},
    "cowork": {"app": "pearnly_cowork", "root": "pearnly-work", "prefix": "cowork.work.", "admin": "admin@cowork.regression",
               "mate": "work@cowork.regression", "table": "Cowork Work"},
}


def _spec(manifest):
    return PRODUCTS[manifest.get("product") or "erp"]


def prepare(lab, manifest):
    spec = _spec(manifest)
    tag = uuid.uuid4().hex[:6]
    out = lab.python(manifest["site"], f"""
from {spec['app']}.work import access, api, notify
frappe.set_user({spec['admin']!r})
state = api.board()
first = state["lists"][0]["name"]
archived = api.create("flow archived {tag}", first)["name"]
api.archive(archived)
mentioned = api.create("flow mention {tag}", first)["name"]
frappe.db.commit()
# 同事在这张卡上提了管理员一句：管理员多一条未读
frappe.set_user({spec['mate']!r})
sent = notify.send([{spec['admin']!r}], frappe.get_doc(access.CARD, mentioned), "mention", {{"text": "flow {tag}"}})
frappe.db.commit()
frappe.set_user({spec['admin']!r})
print(json.dumps({{"board": state["board"]["name"], "archived": archived, "mentioned": mentioned, "sent": sent,
                  "unread": frappe.db.count(access.NOTIFICATION, {{"user": {spec['admin']!r}, "is_read": 0}})}}))
""")
    return {**out, **spec, "tag": tag, "board_title": f"Flow board {tag}", "renamed": f"Flow board {tag} set",
            "label": f"flow label {tag}", "field": f"flow field {tag}", "cases": []}


def cleanup(lab, manifest, params):
    lab.python(manifest["site"], f"""
from {params['app']}.work import access, api
frappe.set_user({params['admin']!r})
for name in frappe.get_all(access.BOARD, filters={{"title": ["in", [{params['board_title']!r}, {params['renamed']!r}]], "archived": 0}}, pluck="name"):
    api.archive_board(name)
for name in ({params['archived']!r}, {params['mentioned']!r}):
    if frappe.db.exists(access.CARD, name) and not frappe.db.get_value(access.CARD, name, "archived"):
        api.archive(name)
frappe.db.commit()
print(json.dumps({{}}))
""")


SETUP = JS + ACTION + """
const W=await frappe.pearnly.texts(PREFIX);
const dlg=async()=>await until(()=>window.cur_dialog&&cur_dialog.$wrapper.is(':visible')&&cur_dialog, 15000);
const shut=async(d)=>{await until(()=>!d.$wrapper.is(':visible'), 20000); await idle();};
const menu=async(key)=>{await until(()=>pageOf(ROOT)&&$.active===0, 25000); await idle(); return await action(W(PREFIX+key));};
const save=async(d)=>{d.get_primary_btn().click(); await shut(d); return !d.$wrapper.is(':visible');};
"""


def _setup(params):
    return SETUP.replace("PREFIX", js_value(params["prefix"])).replace("ROOT", js_value(params["root"]))


def steps(params, camera_code=None):
    v = {k: js_value(params[k]) for k in ("tag", "board_title", "renamed", "label", "field", "archived")}
    return [
        {"name": "提醒", "js": _setup(params) + """
const via=await menu('notify.title'); if(!via) return {error:'no notifications item'};
const d=await dlg(); if(!d) return {error:'no notifications dialog'};
await until(()=>d.$body.find('[data-notice]').length, 15000); const listed=d.$body.find('[data-notice]').length;
d.get_primary_btn().click(); await sleep(500); await idle();
const unread=d.$body.find('.pearnly-work-notice.unread').length; d.hide(); await shut(d);
return {via, listed, unread};"""},
        {"name": "已归档", "js": _setup(params) + f"""
const via=await menu('archived.title'); if(!via) return {{error:'no archived item'}};
const d=await dlg(); if(!d) return {{error:'no archived dialog'}};
const b=await until(()=>d.$body.find('[data-restore="'+{v['archived']}+'"]')[0], 15000); if(!b) return {{error:'archived card not listed'}};
b.click(); const gone=await until(()=>!d.$body.find('[data-restore="'+{v['archived']}+'"]').length, 15000); await idle();
d.hide(); await shut(d);
return {{via, gone: !!gone}};"""},
        {"name": "新建看板", "js": _setup(params) + f"""
const via=await menu('board.create'); if(!via) return {{error:'no new board item'}};
const d=await dlg(); if(!d) return {{error:'no new board dialog'}};
// 私有看板：团队看板人人都算成员，「成员」里就没有可加的人
await d.set_values({{title: {v['board_title']}, visibility: 'private'}}); await sleep(200); const ok=await save(d);
return {{via, closed: ok}};"""},
        {"name": "看板设置", "js": _setup(params) + f"""
const via=await menu('board.settings'); if(!via) return {{error:'no settings item'}};
const d=await dlg(); if(!d) return {{error:'no settings dialog'}};
const was=d.get_value('title'); await d.set_value('title', {v['renamed']}); await sleep(200); const ok=await save(d);
return {{via, was, closed: ok}};"""},
        {"name": "标签", "js": _setup(params) + f"""
const via=await menu('labels.manage'); if(!via) return {{error:'no labels item'}};
const d=await dlg(); if(!d) return {{error:'no labels dialog'}};
d.fields_dict.add.$input.click(); await sleep(300);
const input=d.$body.find('.pearnly-work-label-edit').last().find('input')[0]; if(!input) return {{error:'no new label row'}};
input.value={v['label']}; input.dispatchEvent(new Event('input',{{bubbles:true}})); await sleep(200);
const ok=await save(d); return {{via, closed: ok}};"""},
        {"name": "自定义字段", "js": _setup(params) + f"""
const via=await menu('fields.manage'); if(!via) return {{error:'no fields item'}};
const d=await dlg(); if(!d) return {{error:'no fields dialog'}};
d.fields_dict.add.$input.click(); await sleep(300);
const box=d.$body.find('.pearnly-work-field-edit').last(); const input=box.find('[data-fieldname="title"] input, [data-fieldname="title"] textarea')[0];
if(!input) return {{error:'no new field row'}};
input.value={v['field']}; input.dispatchEvent(new Event('input',{{bubbles:true}})); input.dispatchEvent(new Event('change',{{bubbles:true}})); await sleep(300);
const ok=await save(d); return {{via, closed: ok}};"""},
        {"name": "自动化", "js": _setup(params) + """
const via=await menu('rules.manage'); if(!via) return {error:'no rules item'};
const d=await dlg(); if(!d) return {error:'no rules dialog'};
await idle(); d.fields_dict.add.$input.click(); await sleep(300);
const rows=d.$body.find('.pearnly-work-field-edit').length; const ok=await save(d); return {via, rows, closed: ok};"""},
        {"name": "成员", "js": _setup(params) + """
const via=await menu('board.members'); if(!via) return {error:'no members item'};
const d=await dlg(); if(!d) return {error:'no members dialog'};
// 选择框在外壳里画成可搜索的输入框（不是 <select>）：候选人取字段自己的选项
const opt=await until(()=>(d.fields_dict.user.df.options||[]).map(o=>o.value||o).find(Boolean), 15000);
if(!opt) return {error:'no member to add'};
await d.set_value('user', opt); await sleep(200); const before=d.$body.find('.pearnly-work-member').length;
d.fields_dict.add.$input.click(); await until(()=>d.$body.find('.pearnly-work-member').length>before, 15000); await idle();
const after=d.$body.find('.pearnly-work-member').length; d.hide(); await shut(d);
return {via, added: opt, before, after};"""},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    note, arch, new, sets = (by.get(k, {}) for k in ("提醒", "已归档", "新建看板", "看板设置"))
    labels, fields, rules, members = (by.get(k, {}) for k in ("标签", "自定义字段", "自动化", "成员"))
    return [
        {"case": "notifications list the mention and Read all clears the unread marks", "seen": note,
         "ok": bool(note.get("via")) and (note.get("listed") or 0) >= 1 and note.get("unread") == 0},
        {"case": "the archived dialog restores the archived card", "seen": arch, "ok": bool(arch.get("via")) and arch.get("gone") is True},
        {"case": "new board dialog closes", "seen": new, "ok": bool(new.get("via")) and new.get("closed") is True},
        # 设置弹窗带出的是新板的名称：页面已换到新建的那块板
        {"case": "the settings dialog opens on the new board and saves", "seen": sets,
         "ok": bool(sets.get("via")) and sets.get("was") == params["board_title"] and sets.get("closed") is True},
        {"case": "labels, fields and rules dialogs save and close", "seen": {"labels": labels, "fields": fields, "rules": rules},
         "ok": all(bool(x.get("via")) and x.get("closed") is True for x in (labels, fields, rules)) and (rules.get("rows") or 0) >= 1},
        {"case": "the members dialog adds a member", "seen": members,
         "ok": bool(members.get("via")) and (members.get("after") or 0) == (members.get("before") or 0) + 1},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
from {params['app']}.work import access
admin = {params['admin']!r}
boards = frappe.get_all(access.BOARD, filters={{"title": {params['renamed']!r}}}, pluck="name")
board = boards[0] if boards else ""
print(json.dumps({{
    "unread": frappe.db.count(access.NOTIFICATION, {{"user": admin, "is_read": 0}}),
    "archived": frappe.db.get_value(access.CARD, {params['archived']!r}, "archived"),
    "boards": boards, "old_title": frappe.db.count(access.BOARD, {{"title": {params['board_title']!r}}}),
    "labels": frappe.get_all(access.LABEL, filters={{"board": board}}, pluck="title") if board else [],
    "fields": frappe.get_all(access.FIELD, filters={{"board": board}}, pluck="title") if board else [],
    "rules": frappe.db.count(access.RULE, {{"board": board}}) if board else 0,
    "members": frappe.get_all(access.BOARD_MEMBER, filters={{"parenttype": access.BOARD, "parent": board}}, pluck="user") if board else [],
}}, default=str))
""")
    return [
        {"case": "Read all left the admin no unread notification", "saved": got["unread"], "ok": params["unread"] >= 1 and got["unread"] == 0},
        {"case": "Restore took the card out of the archive", "saved": got["archived"], "ok": not got["archived"]},
        {"case": "the new board exists under the name the settings dialog gave it", "saved": {"boards": got["boards"], "old": got["old_title"]},
         "ok": len(got["boards"]) == 1 and got["old_title"] == 0},
        {"case": "the new board has the added label and field", "saved": {"labels": got["labels"], "fields": got["fields"]},
         "ok": params["label"] in got["labels"] and params["field"] in got["fields"]},
        {"case": "the new board has the added rule", "saved": got["rules"], "ok": got["rules"] >= 1},
        {"case": "the new board has the creator and the added member", "saved": got["members"],
         "ok": params["admin"] in got["members"] and len(got["members"]) >= 2},
    ]
