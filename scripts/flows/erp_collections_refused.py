"""流程用例：没有收款岗位的人打不开收款的两页（收款 11-11）。

采购岗（没有 ``collection.view``、不是收款员）：打开「销售 → 收款」与「我的收款」都是原生无权限页；两页的接口（列表、导出、
本人收款）服务端也拒（403），不是只把菜单藏起来。
"""
from __future__ import annotations

from erp_lib import JS

TITLE = "没有收款岗位：收款两页打不开、接口拒"
API = "pearnly_erp_portal.collection.api."


def prepare(lab, manifest):
    return {"cases": []}


def steps(params, camera_code=None):
    return [{"name": "打开两页", "js": JS + f"""
const out={{}};
for (const route of ['pearnly-collections', 'pearnly-my-collections']) {{
  frappe.set_route(route); await sleep(600); await idle();
  const page=await until(()=>pageOf(route), 15000);
  out[route]={{opened: !!page, not_permitted: !!(page&&page.querySelector('.message-page'))}};
}}
const status=async(method, post)=>(await fetch('/api/method/{API}'+method, {{method: post ? 'POST' : 'GET',
  headers: {{'X-Frappe-CSRF-Token': frappe.csrf_token, 'Accept': 'application/json'}}}})).status;
out.api={{list: await status('api_collections'), export: await status('api_collections_export', true),
          mine: await status('api_my_collections')}};
return out;"""}]


def judge(params, results, camera_code=None):
    seen = next((r.get("value") or {} for r in results if r.get("step") == "打开两页"), {})
    checks = [{"case": f"{route} is not permitted", "seen": seen.get(route),
               "ok": bool((seen.get(route) or {}).get("not_permitted"))} for route in ("pearnly-collections", "pearnly-my-collections")]
    checks.append({"case": "the list, export and own-collections calls are refused", "seen": seen.get("api"),
                   "ok": bool(seen.get("api")) and all(code == 403 for code in seen["api"].values())})
    return checks


def verify(lab, manifest, params, camera_code=None):
    return []

