"""流程用例：ERP 新建商品撞码（商品码 / 内部编码 / 撞码与候选，fcb65cca 逐入口实测）。

物料岗按页面同一个接口建商品，内部编码填一件已有商品正在用的码（新开户：按编码认的那件的编码；老数据：第一件的编码）→ 被拒，
提示里说出撞了哪件；再填一个没人用的内部编码 → 建成。库里：撞码那次没建出来，没撞的那件建出来、内部编码就是填的值。
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from erp_lib import JS, js_value  # noqa: E402

TITLE = "ERP 新建商品撞码被拒"
API = "pearnly_erp_portal.products.service."


def prepare(lab, manifest):
    owner = manifest["products"]["by_code"] if manifest["dataset"] == "new" else {"product": manifest["products"][0]["name"]}
    info = lab.python(manifest["site"], f"""
print(json.dumps(frappe.db.get_value("ERP Product", {owner['product']!r}, ["code", "product_name"], as_dict=True)))
""")
    tag = uuid.uuid4().hex[:6]
    return {"taken": info["code"], "owner_name": info["product_name"], "clash_name": "ชนรหัส " + tag, "fresh_name": "ไม่ชน " + tag,
            "fresh_code": "FLOW-INT-" + tag.upper()}


def steps(params, camera_code=None):
    return [{"name": "撞码与不撞码", "js": JS + f"""
const attempt=args=>new Promise(resolve=>frappe.call({{method:'{API}api_create_product', args, type:'POST',
  callback:r=>resolve({{ok:true, name:r.message&&r.message.name}}),
  error:r=>resolve({{ok:false, message: JSON.stringify((r&&r._server_messages)||r||'')}})}}).fail(x=>resolve({{ok:false, message: JSON.stringify((x&&x.responseJSON&&x.responseJSON._server_messages)||'')}})));
const clash=await attempt({{product_name: {js_value(params['clash_name'])}, unit: 'ชิ้น', internal_code: {js_value(params['taken'])}}});
all('.modal.show .btn-modal-close').forEach(b=>b.click());
const fresh=await attempt({{product_name: {js_value(params['fresh_name'])}, unit: 'ชิ้น', internal_code: {js_value(params['fresh_code'])}}});
return {{clash, fresh}};"""}]


def judge(params, results, camera_code=None):
    v = next((r.get("value") for r in results if r.get("step") == "撞码与不撞码"), None) or {}
    clash, fresh = v.get("clash") or {}, v.get("fresh") or {}
    named = params["taken"] in clash.get("message", "") or params["owner_name"] in clash.get("message", "")
    return [{"case": "撞码被拒并说出撞了哪件", "seen": clash, "ok": clash.get("ok") is False and named},
            {"case": "没撞的建成", "seen": fresh, "ok": bool(fresh.get("ok") and fresh.get("name"))}]


def verify(lab, manifest, params, camera_code=None):
    row = lab.python(manifest["site"], f"""
print(json.dumps({{"clash": frappe.db.count("ERP Product", {{"product_name": {params['clash_name']!r}}}),
                  "fresh": frappe.db.get_value("ERP Product", {{"product_name": {params['fresh_name']!r}}}, "internal_code")}}))
""")
    return [{"case": "撞码那件没建出来", "seen": row["clash"], "ok": row["clash"] == 0},
            {"case": "没撞那件的内部编码", "seen": row["fresh"], "ok": row["fresh"] == params["fresh_code"]}]
