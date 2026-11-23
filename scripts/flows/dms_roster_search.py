"""流程用例：DMS「操作员」名册页按名字搜索（客户 2026-11-23：员工近 100 人，翻页找人太慢）。

备数据：在本站点名册里放 25 个同一前缀的合成操作员（实验栈没有 DMS 连接，按表直接落行，跳过「先连 DMS」的开户校验）。
页面：管理员打开名册页 → 页头搜索框输入前缀 → 列表只剩这 25 个里的第一页（20 / 25），「加载更多」后 25 / 25（分页照旧生效）；
输入其中一个的显示名 → 只剩 1 行；输入另一个的 DMS 用户名 → 只剩那 1 行；清空 → 回到全部。
落库：搜索只读，不写；收尾删掉备的 25 行与它们的账号。
"""
from __future__ import annotations

import json
import uuid

TITLE = "DMS 名册：按名字 / DMS 用户名搜索，分页照旧"
COUNT = 25

JS = r"""
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const until=async(f,ms=20000)=>{const end=Date.now()+ms;while(Date.now()<end){const v=f();if(v)return v;await sleep(200);}return null;};
const box=()=>document.querySelector('.page-form [data-fieldname="search"] input');
const names=()=>[...new Set([...document.querySelectorAll('.list-row-container .list-subject')].map(e=>e.innerText.trim()).filter(Boolean))];
const count=()=>((document.querySelector('.list-count')||{}).innerText||'').trim();
const search=async(text,expect)=>{const i=await until(box);i.focus();i.value=text;
  i.dispatchEvent(new Event('input',{bubbles:true}));i.dispatchEvent(new Event('change',{bubbles:true}));
  await until(()=>count()===expect,20000);await sleep(300);return {count:count(),names:names()};};
"""


def prepare(lab, manifest):
    tag = "Fs" + uuid.uuid4().hex[:6]
    rows = [{"display_name": f"{tag} Op{i:02d}", "dms_username": f"{tag.lower()}u{i:02d}",
             "user": f"{tag.lower()}{i:02d}@operators.local"} for i in range(COUNT)]
    total = lab.python(manifest["site"], f"""
frappe.set_user("Administrator")
for row in {rows!r}:
    if not frappe.db.exists("User", row["user"]):
        frappe.get_doc({{"doctype": "User", "email": row["user"], "first_name": row["display_name"],
                        "user_type": "Website User", "send_welcome_email": 0}}).insert(ignore_permissions=True)
    doc = frappe.new_doc("DMS Operator")
    doc.update({{"display_name": row["display_name"], "user": row["user"], "dms_username": row["dms_username"],
                "dms_role": "sales", "status": "active"}})
    doc.set_new_name(); doc.set_user_and_timestamp(); doc.db_insert()
frappe.db.commit()
print(json.dumps({{"total": frappe.db.count("DMS Operator")}}))
""")["total"]
    return {"tag": tag, "rows": rows, "total": total}


def steps(params, camera_code=None):
    tag, total = params["tag"], params["total"]
    one, user = params["rows"][7]["display_name"], params["rows"][13]["dms_username"]
    return [
        {"name": "按前缀搜", "js": JS + f"""
await until(box,30000);
const first=await search({json.dumps(tag)}, '20 / {COUNT}');
const more=document.querySelector('.list-paging-area .btn-more');if(more)more.click();
await until(()=>count()==='{COUNT} / {COUNT}',20000);await sleep(300);
return {{first, after_more:{{count:count(),names:names()}}}};""", "shot": True},
        {"name": "按显示名搜", "js": JS + f"return await search({json.dumps(one)}, '1 / 1');"},
        {"name": "按用户名搜", "js": JS + f"return await search({json.dumps(user)}, '1 / 1');", "shot": True},
        {"name": "清空", "js": JS + f"return await search('', '20 / {total}');"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    tag, total = params["tag"], params["total"]
    first = (by.get("按前缀搜") or {}).get("first") or {}
    more = (by.get("按前缀搜") or {}).get("after_more") or {}
    one, user = by.get("按显示名搜") or {}, by.get("按用户名搜") or {}
    cleared = by.get("清空") or {}
    expect_one, expect_user = params["rows"][7]["display_name"], params["rows"][13]["display_name"]
    return [
        {"case": "search keeps paging: first page 20 of the 25 matches, all of them ours", "seen": {"count": first.get("count"), "n": len(first.get("names") or [])},
         "ok": first.get("count") == f"20 / {COUNT}" and len(first.get("names") or []) == 20
         and all(n.startswith(tag) for n in first.get("names") or [])},
        {"case": "load more brings the remaining matches", "seen": {"count": more.get("count"), "n": len(more.get("names") or [])},
         "ok": more.get("count") == f"{COUNT} / {COUNT}" and len(more.get("names") or []) == COUNT},
        {"case": "a display name finds only that operator", "seen": one, "ok": one.get("count") == "1 / 1" and one.get("names") == [expect_one]},
        {"case": "a DMS username finds only that operator", "seen": user, "ok": user.get("count") == "1 / 1" and user.get("names") == [expect_user]},
        {"case": "clearing the box shows the whole roster again", "seen": {"count": cleared.get("count")}, "ok": cleared.get("count") == f"20 / {total}"},
    ]


def verify(lab, manifest, params, camera_code=None):
    users = [row["user"] for row in params["rows"]]
    left = lab.python(manifest["site"], f"""
frappe.set_user("Administrator")
for name in frappe.get_all("DMS Operator", filters={{"user": ["in", {users!r}]}}, pluck="name"):
    frappe.db.delete("DMS Operator", {{"name": name}})
for user in {users!r}:
    if frappe.db.exists("User", user):
        frappe.delete_doc("User", user, ignore_permissions=True, force=True)
frappe.db.commit()
print(json.dumps({{"left": frappe.db.count("DMS Operator", {{"user": ["in", {users!r}]}})}}))
""")
    return [{"case": "the 25 seeded operators are removed again", "seen": left, "ok": left.get("left") == 0}]
