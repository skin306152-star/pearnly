"""流程用例：字段说明收在标题旁的说明图标里（用户 11-02，ERP / Cowork；共用层 `pearnly_core/public/desk/field-help.js`）。

ERP 打开新建物料弹窗，Cowork 打开新建定时任务与新建录入两个弹窗：
- 带说明的字段标题旁有图标、`aria-label` 是说明原文，字段下方不写（每一步后 `ui_steps.mjs` 另有全页检查）；
- 电脑上鼠标移上去显示、移开收起，触屏上点一下显示、再点收起，显示的就是那句说明；
- 登记的例外（运行时状态 / 数值，`scripts/field-help-inline.json`）照原生留在下方，控件带 `data-pearnly-description="inline"`。
只开弹窗不保存，库里不变。
"""
from __future__ import annotations

from desk_list import IDLE

TITLE = "字段说明：标题旁的说明图标，悬停 / 点按显示，例外留在下方"
# 每个产品打开哪个弹窗、哪几格的说明要在图标里、哪几格是登记的例外
CASES = {
    "erp": [{"name": "新建物料", "open": "const b=[...document.querySelectorAll('.page-head .primary-action')].find(e=>e.offsetParent); b&&b.click();",
             "icons": ["code", "base_unit"], "inline": []}],
    "cowork": [{"name": "新建定时任务", "route": "pearnly-intake/folders",
                "open": "const b=[...document.querySelectorAll('.page-head .primary-action')].find(e=>e.offsetParent); b&&b.click();",
                "icons": ["folder"], "inline": ["year", "account_set_key", "d_account"]},
               {"name": "新建录入", "route": "pearnly-intake", "open": "frappe.pearnly.intake_create(k=>__(k), ()=>{});",
                "icons": [], "inline": ["company", "year"]}],
}
CHECK = r"""
const m=[...document.querySelectorAll('.modal.show')].pop(); if(!m) return {opened:false};
const icons={}; m.querySelectorAll('.pearnly-field-help').forEach(i=>{ const c=i.closest('.frappe-control'); if(c&&i.getClientRects().length) icons[c.dataset.fieldname]=i.getAttribute('aria-label'); });
const inline=[...m.querySelectorAll('.frappe-control[data-pearnly-description="inline"]')].map(c=>c.dataset.fieldname);
const below=[...m.querySelectorAll('.frappe-control .help-box')].filter(e=>e.getClientRects().length&&e.innerText.trim()&&!e.closest('[data-pearnly-description="inline"]')).map(e=>e.closest('.frappe-control').dataset.fieldname);
const touch=matchMedia('(pointer: coarse)').matches, tips=[];
for (const name of WANT) {
  const i=m.querySelector(`.frappe-control[data-fieldname="${name}"] .pearnly-field-help`); if(!i) continue;
  if (touch) i.click(); else $(i).trigger('mouseenter');
  await new Promise(r=>setTimeout(r,450));
  const shown=document.querySelector('.tooltip.show')?.innerText||'';
  if (touch) i.click(); else $(i).trigger('mouseleave');
  await new Promise(r=>setTimeout(r,450));
  tips.push({name, shown, label:i.getAttribute('aria-label'), after:document.querySelector('.tooltip.show')?.innerText||''});
}
document.querySelectorAll('.modal.show').forEach(x=>$(x).modal('hide')); await idle();
return {opened:true, icons, inline, below, tips};
"""


def prepare(lab, manifest):
    product = "cowork" if "cowork" in manifest["site"] else "erp"
    return {"product": product, "cases": CASES[product]}


def steps(params, camera_code=None):
    out = []
    for case in params["cases"]:
        route = f"if(frappe.get_route_str()!=={case['route']!r}){{frappe.set_route({case['route']!r}); await idle();" \
                "await new Promise(r=>setTimeout(r,1200)); await idle();}" if case.get("route") else ""
        out.append({"name": case["name"], "js": IDLE + "await idle();" + route + case["open"]
                    + "for(let k=0;k<40&&!document.querySelector('.modal.show');k++) await new Promise(r=>setTimeout(r,250));"
                    + "await idle(); await new Promise(r=>setTimeout(r,800));"
                    + f"const WANT={case['icons']!r};" + CHECK})
    return out


def judge(params, results, camera_code=None):
    checks = []
    for case in params["cases"]:
        seen = next((r.get("value") for r in results if r.get("step") == case["name"]), None) or {}
        icons, tips = seen.get("icons") or {}, seen.get("tips") or []
        checks += [
            {"case": f"{case['name']}: dialog opened", "seen": seen.get("opened"), "ok": seen.get("opened") is True},
            {"case": f"{case['name']}: descriptions sit in the info icon", "seen": sorted(icons), "expect": case["icons"],
             "ok": all(icons.get(name) for name in case["icons"])},
            {"case": f"{case['name']}: hover or tap shows the description, leaving or tapping again hides it", "seen": tips,
             "ok": len(tips) == len(case["icons"]) and all(t["shown"] and t["shown"] == t["label"] and not t["after"] for t in tips)},
            {"case": f"{case['name']}: nothing below a field but the registered runtime states", "seen": seen.get("below"),
             "ok": seen.get("below") == []},
            {"case": f"{case['name']}: runtime states stay below the field", "seen": seen.get("inline"), "expect": case["inline"],
             "ok": set(case["inline"]) <= set(seen.get("inline") or [])},
        ]
    return checks


def verify(lab, manifest, params, camera_code=None):
    return []
