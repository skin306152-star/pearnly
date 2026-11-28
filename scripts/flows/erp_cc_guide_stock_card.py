"""流程用例：ERP 版 CC「收发存报表」引导（直写）真页面：报表、格式照推荐或第一项，日期填对话月份头尾，物料 / 仓库范围跳过 → 确定 →
结果卡「成功」带一份文件；这份文件真放进了这段对话（服务端同名）。"""
from __future__ import annotations

import erp_cc_guide_form_lib as lib

TITLE = "ERP 版 CC 收发存报表引导（直写）：照推荐作答 → 确定 → 结果卡成功带一份文件；对话里真有这份文件"


def prepare(lab, manifest):
    # 日期两题照推荐（对话月份的头尾）填上：推荐只带徽章、不预选，点了才算
    return lib.prepare(lab, manifest, tag_prefix="esc", extra="""
import calendar
day = frappe.utils.getdate(frappe.utils.today())
extra = {"date_from": f"{day.year:04d}-{day.month:02d}-01",
         "date_to": f"{day.year:04d}-{day.month:02d}-{calendar.monthrange(day.year, day.month)[1]:02d}"}
""")


def steps(params, camera_code=None):
    return [*lib.steps_head(params, "stock_card"), lib.answer({"date_from": params["date_from"], "date_to": params["date_to"]}),
            lib.confirm(shot="1-result")]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    opened, done = by.get("点菜单") or {}, by.get("确定") or {}
    return [
        {"case": "业务菜单有「收发存报表」，点了出引导卡、先问报表", "seen": opened,
         "ok": bool(opened.get("clicked")) and opened.get("business") == "stock_card" and (opened.get("keys") or [""])[0] == "report"},
        {"case": "确定 → 结果卡「成功」、一份文件", "seen": done,
         "ok": done.get("status") == "done" and done.get("result") == "entered" and "成功" in (done.get("text") or "")
         and len(done.get("files") or []) == 1},
        lib.no_internal_case(done),
    ]


def verify(lab, manifest, params, camera_code=None):
    runs = lib.run_row(lab, manifest, params, "stock_card")
    names = lib.chat_files(lab, manifest, params)
    return [lib.done_case(runs),
            {"case": "对话里服务端真放了一份报表文件", "seen": {"server": names},
             "ok": len(names or []) == 1 and str(names[0]).endswith((".xlsx", ".pdf"))}]


cleanup = lib.cleanup
