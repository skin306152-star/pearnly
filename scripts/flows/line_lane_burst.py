"""流程用例：同一个 LINE 用户几乎同时发来多条事件（LINE 按用户一条道，11-28）。

ERP 与 Cowork 共用这一个模块（登记表里各一行）。站点里的那一大段在 `line_lane_lib.py`：一个进程模拟 LINE 平台，
每个 webhook 请求一个线程、各自连库，调真实的 webhook，回应之后跑 ``after_response``（道的排空线程与线上同一段代码）；
出站与取图换记录器、识别换假引擎（零费用）。格与断言见那个文件的模块说明。页面这一步只打开首页，结果全在库与出站记录里。
"""
from __future__ import annotations

import json
from pathlib import Path

LIB = Path(__file__).with_name("line_lane_lib.py")
TITLE = "LINE 同一个人连发：每条都收下、都出卡、不撞冲突"


def prepare(lab, manifest):
    product = manifest.get("product") or ("cowork" if "cowork" in str(manifest.get("site") or "") else "erp")
    company = (manifest.get("company") or (manifest.get("companies") or [""])[0]) if product == "erp" else ""
    return {"product": product, "site": manifest["site"], "company": company,
            "user": "purchase@erp.regression" if product == "erp" else "intake@cowork.regression"}


def steps(params, camera_code=None):
    return [{"name": "打开首页", "js": "return frappe.get_route();"}]


def judge(params, results, camera_code=None):
    return []


def verify(lab, manifest, params, camera_code=None):
    argv = [params["product"], params["site"], params["user"]] + ([params["company"]] if params["company"] else [])
    body = (f"__lane = {{'__name__': 'line_lane_lib'}}\n"
            f"exec(compile({LIB.read_text(encoding='utf-8')!r}, 'line_lane_lib.py', 'exec'), __lane)\n"
            f"print(json.dumps(__lane['run']({argv!r}), ensure_ascii=False, default=str))")
    results = lab.python(params["site"], body)
    return [{"case": cell, "seen": seen, "ok": bool(seen.get("ok"))} for cell, seen in results.items()] or [
        {"case": "the lane flow ran", "seen": results, "ok": False}]


def cleanup(lab, manifest, params):
    return None
