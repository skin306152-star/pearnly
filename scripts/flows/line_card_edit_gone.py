"""流程用例：LINE 旧卡片上的「编辑」打开的记录已不在 / 已失效（11-15 线上：Cowork 旧识别卡点「编辑」整页空白、没有提示）。

ERP 与 Cowork 共用这一个模块（登记表里各一行，按 ``product`` 跑在各自站点）：
- ERP：`/desk/pearnly-purchase/line/<不存在>`、`/desk/pearnly-sales/line/<不存在>`（LINE 识别单据卡的「编辑」）；
- Cowork：`/desk/pearnly-intake/new/<不存在>`（LINE 识别卡的「编辑」）。
每一处都应弹框架原生的那句（找不到 / 已不可用），并回到这一页的列表，不留一张空页面。
"""
from __future__ import annotations

from erp_lib import JS

TITLE = "LINE 旧卡片「编辑」打开的记录不在了：弹原生提示、回列表"
ROUTES = {"erp": [["pearnly-purchase", "line", "NOPE-LINE-0001"], ["pearnly-sales", "line", "NOPE-LINE-0001"]],
          "cowork": [["pearnly-intake", "new", "NOPE-INTAKE-0001"]]}


def prepare(lab, manifest):
    return {"product": manifest.get("product") or ("cowork" if "cowork" in str(manifest.get("site") or "") else "erp")}


def steps(params, camera_code=None):
    routes = ROUTES["cowork" if params.get("product") == "cowork" else "erp"]
    return [{"name": "打开已不在的记录", "js": JS + f"""
const out=[];
for (const route of {routes!r}) {{
  for (let i=0; i<4 && window.cur_dialog && cur_dialog.display; i++) {{ cur_dialog.hide(); await sleep(300); }}
  frappe.msg_dialog?.hide?.();
  frappe.set_route(...route); await sleep(800); await idle();
  const said=await until(()=>msg(), 15000);
  const back=await until(()=>frappe.get_route().length===1&&frappe.get_route()[0]===route[0], 15000);
  const page=pageOf(route[0]);
  out.push({{route: route.join('/'), said: said||'', now: frappe.get_route(), back: !!back,
            listed: !!(page&&page.querySelector('.frappe-list, .result, .list-row-head, .pearnly-list, .page-form'))}});
  frappe.msg_dialog?.hide?.(); await sleep(300);
}}
return out;"""}]


def judge(params, results, camera_code=None):
    seen = next((r.get("value") or [] for r in results if r.get("step") == "打开已不在的记录"), [])
    return [{"case": f"{row.get('route')} says it is gone and goes back to the list", "seen": row,
             "ok": bool(row.get("said")) and bool(row.get("back"))} for row in seen] or [
        {"case": "every route was opened", "seen": seen, "ok": False}]


def verify(lab, manifest, params, camera_code=None):
    return []
