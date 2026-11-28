"""流程用例：ERP 版 CC「盘点」引导（直写）真页面：选「新建」→ 仓（推荐或第一个）→ 填名称 → 复核 → 确定 → 结果卡「成功 · 名称」；
这家公司真多了这份盘点（名称一致），结果卡不出盘点单号以外的内部编号。"""
from __future__ import annotations

import erp_cc_guide_form_lib as lib

TITLE = "ERP 版 CC 盘点引导（直写）：新建 → 仓 → 名称 → 复核 → 确定 → 结果卡成功带名称；库里真有这份盘点"


def prepare(lab, manifest):
    params = lib.prepare(lab, manifest, tag_prefix="est")
    return {**params, "title": f"flow count {params['tag']}"}


def steps(params, camera_code=None):
    return [*lib.steps_head(params, "stocktake"), lib.answer({"action": "new", "title": params["title"]}),
            lib.confirm(shot="1-result")]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    opened, done = by.get("点菜单") or {}, by.get("确定") or {}
    return [
        {"case": "业务菜单有「盘点」，点了出引导卡、先问操作", "seen": opened,
         "ok": bool(opened.get("clicked")) and opened.get("business") == "stocktake" and opened.get("keys") == ["action"]},
        {"case": "确定 → 结果卡「成功」、名字是这份盘点的名称", "seen": done,
         "ok": done.get("status") == "done" and done.get("result") == "entered" and "成功" in (done.get("text") or "")
         and params["title"] in (done.get("text") or "")},
        lib.no_internal_case(done),
    ]


def verify(lab, manifest, params, camera_code=None):
    runs = lib.run_row(lab, manifest, params, "stocktake")
    counts = lab.python(manifest["site"], f"""
rows = frappe.get_all("ERP Stock Count", filters={{"title": {params['title']!r}}}, fields=["name", "status", "warehouse"],
                      limit_page_length=5)
for row in rows:
    row["company"] = frappe.db.get_value("ERP Warehouse", row.warehouse, "company")  # 盘点按仓归公司
print(json.dumps(rows, default=str))
""")
    return [lib.done_case(runs),
            {"case": "这家公司真多了这一份盘点（名称一致、一份）", "seen": counts,
             "ok": len(counts) == 1 and counts[0].get("company") == params["company"]}]


def cleanup(lab, manifest, params):
    # 盘点表只经盘点服务删（`count_state.before_delete` 拒原生删）：照页面同一个 delete_count，在这家公司下执行
    lib.cleanup(lab, manifest, params, extra="""
from pearnly_erp_portal.inventory import count as counts
from pearnly_erp_portal.masters import company as companies
with companies.acting(params.get("company") or ""):
    for name in frappe.get_all("ERP Stock Count", filters={"title": params.get("title") or "-", "status": "Active"}, pluck="name",
                               limit_page_length=5):
        counts.delete_count(name)
""")
