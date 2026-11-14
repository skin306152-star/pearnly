"""流程用例：没有「收款」权限的人打不开收款页（收款 11-11；11-14 一页一个权限）。

采购岗（没勾收款、不是管理员）：打开「销售 → 收款」是原生无权限页，旧地址「我的收款」转过去同样是无权限页；收款页的接口（列表、导出、
点开一笔、新建）服务端也拒（403），不是只把菜单藏起来。
"""
from __future__ import annotations

from erp_lib import JS

TITLE = "没有收款权限：收款页（含旧地址）打不开、接口拒"
API = "pearnly_erp_portal.collection.api."


def prepare(lab, manifest):
    return {"cases": []}


def steps(params, camera_code=None):
    return [{"name": "打开两页", "js": JS + f"""
const out={{}};
for (const route of ['pearnly-collections', 'pearnly-my-collections']) {{
  frappe.set_route(route); await sleep(600); await idle();
  const page=await until(()=>pageOf('pearnly-collections'), 15000);  // 旧地址转到收款页
  out[route]={{opened: !!page, not_permitted: !!(page&&page.querySelector('.message-page'))}};
}}
const status=async(method, post)=>(await fetch('/api/method/{API}'+method, {{method: post ? 'POST' : 'GET',
  headers: {{'X-Frappe-CSRF-Token': frappe.csrf_token, 'Accept': 'application/json'}}}})).status;
out.api={{list: await status('api_collections'), export: await status('api_collections_export', true),
          options: await status('api_collection_new_options')}};
return out;"""}]


def judge(params, results, camera_code=None):
    seen = next((r.get("value") or {} for r in results if r.get("step") == "打开两页"), {})
    checks = [{"case": f"{route} is not permitted", "seen": seen.get(route),
               "ok": bool((seen.get(route) or {}).get("not_permitted"))} for route in ("pearnly-collections", "pearnly-my-collections")]
    checks.append({"case": "the list, export and new-collection calls are refused", "seen": seen.get("api"),
                   "ok": bool(seen.get("api")) and all(code == 403 for code in seen["api"].values())})
    return checks


def verify(lab, manifest, params, camera_code=None):
    return []

