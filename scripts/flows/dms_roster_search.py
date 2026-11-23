"""流程用例：DMS「操作员」名册页按名字搜索（客户 2026-11-23：员工近 100 人，翻页找人太慢）。

备数据：在本站点名册里放 25 个同一前缀的合成操作员（实验栈没有 DMS 连接，按表直接落行，跳过「先连 DMS」的开户校验）。
页面：管理员打开名册页 → 页头搜索框输入前缀 → 列表只剩这 25 个里的第一页（20 / 25），「加载更多」后 25 / 25（分页照旧生效）；
输入其中一个的显示名 → 只剩 1 行；输入另一个的 DMS 用户名 → 只剩那 1 行；清空 → 回到全部。
落库：搜索只读，不写；只落名册行、不建账号，收尾按主键删掉这 25 行（并行几格不撞锁）；失败时结果里带 Error Log 标题。
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
const names=()=>[...new Set([...document.querySelectorAll('.list-row-container .list-subject')].filter(e=>!e.closest('.list-row-head')).map(e=>e.innerText.trim()).filter(Boolean))];
const count=()=>((document.querySelector('.list-count')||{}).innerText||'').trim();
const search=async(text,expect)=>{const i=await until(box);i.focus();i.value=text;
  i.dispatchEvent(new Event('input',{bubbles:true}));i.dispatchEvent(new Event('change',{bubbles:true}));
  await until(()=>count()===expect,20000);await sleep(300);return {count:count(),names:names()};};
"""


def prepare(lab, manifest):
    tag = "Fs" + uuid.uuid4().hex[:6]
    rows = [{"display_name": f"{tag} Op{i:02d}", "dms_username": f"{tag.lower()}u{i:02d}",
             "user": f"{tag.lower()}{i:02d}@operators.local"} for i in range(COUNT)]
    # 只落名册行、不建 User：名册列表不读 User；建 / 删 User 会走 `User.on_trash` 里按非主键条件的批量 update
    # （ToDo.assigned_by、Contact.user 等），同一站点几格并行时互相撞锁（train-1123c old/desktop、本分支 new/desktop 各一次）。
    seeded = lab.python(manifest["site"], f"""
frappe.set_user("Administrator")
since = str(frappe.utils.now_datetime())
names = []
for row in {rows!r}:
    doc = frappe.new_doc("DMS Operator")
    doc.update({{"display_name": row["display_name"], "user": row["user"], "dms_username": row["dms_username"],
                "dms_role": "sales", "status": "active"}})
    doc.set_new_name(); doc.set_user_and_timestamp(); doc.db_insert()
    names.append(doc.name)
frappe.db.commit()
print(json.dumps({{"total": frappe.db.count("DMS Operator"), "names": names, "since": since}}))
""")
    return {"tag": tag, "rows": rows, "total": seeded["total"], "names": seeded["names"], "since": seeded["since"]}


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
    # 收尾只按主键删本格落的那 25 行（不碰 User，不做按条件的批量更新）；出错时把原文和这段时间的 Error Log 标题一起带回结果。
    left = lab.python(manifest["site"], f"""
frappe.set_user("Administrator")
error = ""
try:
    for name in {params["names"]!r}:
        frappe.db.delete("DMS Operator", {{"name": name}})
    frappe.db.commit()
except Exception as exc:
    frappe.db.rollback()
    error = f"{{type(exc).__name__}}: {{exc}}"[:600]
titles = frappe.get_all("Error Log", filters={{"creation": [">=", {params["since"]!r}]}}, pluck="method",
                        order_by="creation asc", limit_page_length=20)
print(json.dumps({{"left": frappe.db.count("DMS Operator", {{"name": ["in", {params["names"]!r}]}}), "error": error,
                  "error_logs": titles}}, default=str))
""")
    # Error Log 标题只随结果带回（同一站点上并行的别的流程也会写，不拿它判这一格）
    return [{"case": "the 25 seeded operators are removed again", "seen": left,
             "ok": left.get("left") == 0 and not left.get("error")}]
