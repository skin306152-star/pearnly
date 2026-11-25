"""流程用例：Cowork 版 CC「任务看板」引导（直写）真页面：选「添加卡片」→ 看板（有默认的照默认，没有选这块）→ 列表 → 标题 → 复核 → 确定 → 结果卡「成功 · 标题」；
看板上真多了这张卡（标题一致）。备数：建一块默认看板（看板页同一个 `work.boards.create`，带默认列表）。"""
from __future__ import annotations

import cowork_cc_guide_form_lib as form
import cowork_cc_guide_form_result_lib as lib

TITLE = "Cowork 版 CC 任务看板引导（直写）：添加卡片 → 复核 → 确定 → 结果卡成功带标题；看板上真有这张卡"

EXTRA = """
from pearnly_cowork.work import boards
frappe.set_user(%r)
made = boards.create({"title": "Flow board " + %r})
frappe.db.commit()
extra = {"board": made["name"]}
"""


def prepare(lab, manifest):
    params = form.prepare(lab, manifest, extra=EXTRA % (lib.USER, "w"), tag_prefix="gwk")
    return {**params, "title": f"flow card {params['tag']}"}


def steps(params, camera_code=None):
    return [*lib.steps_head(params, "work"), lib.answer({"action": "create", "board": params["board"], "title": params["title"]}),
            lib.confirm(shot="1-result")]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    opened, answered, done = (by.get(k) or {} for k in ("点菜单", "作答", "确定"))
    return [
        {"case": "业务菜单有「任务看板」，点了出引导卡、先问操作", "seen": opened,
         "ok": bool(opened.get("clicked")) and opened.get("business") == "work" and opened.get("keys") == ["action"]},
        {"case": "确定 → 结果卡「成功」、名字是卡片标题（不出内部编号）", "seen": {**done, "answered": answered},
         "ok": done.get("status") == "done" and done.get("result") == "entered" and "成功" in (done.get("text") or "")
         and params["title"] in (done.get("text") or "")},
        lib.no_internal_case(done),
    ]


def verify(lab, manifest, params, camera_code=None):
    runs = lib.run_row(lab, manifest, params, "work")
    titles = lab.python(manifest["site"], f"""
print(json.dumps(frappe.get_all("Cowork Work Card", filters={{"title": {params['title']!r}}},
                                pluck="title", limit_page_length=5)))
""")
    return [lib.done_case(runs), {"case": "看板上真多了这张卡（标题一致、只一张）", "seen": titles, "ok": titles == [params["title"]]}]


def cleanup(lab, manifest, params):
    """看板表只经看板服务写（on_trash 拒原生删除）：实验栈里按表直接删掉本流程建的卡、列表、标签、看板与成员。"""
    if params.get("board"):
        lab.python(manifest["site"], f"""
for card in frappe.get_all("Cowork Work Card", filters={{"title": {params['title']!r}}}, pluck="name", limit_page_length=5):
    frappe.db.delete("Cowork Work Card", {{"name": card}})
frappe.db.delete("Cowork Work Column", {{"board": {params['board']!r}}})
frappe.db.delete("Cowork Work Label", {{"board": {params['board']!r}}})
frappe.db.delete("Cowork Work Board Member", {{"parent": {params['board']!r}}})
frappe.db.delete("Cowork Work Board", {{"name": {params['board']!r}}})
frappe.db.commit()
print("{{}}")
""")
    form.cleanup(lab, manifest, params)
