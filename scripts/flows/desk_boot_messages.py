"""流程用例：整份翻译表单独取（`pearnly_core/desk/boot_messages.py`）后，Frappe 加载时就翻译的文字照样是当前语言（11-14）。

页面脚本跑之前注入一段（`PRELOAD`）：挂钩 `__()` / `frappe._()`，记下词表整表还没到时被翻译的原文和当时拿到的结果。页面起来后：
- 整表已到（上千条）、`__("Save")` 是当前语言（实验室账号界面中文 →「保存」）；
- 早期被翻译的原文里，没有一条「整表里有译文、当时却拿到原文」的——有就说明 `boot_messages.EARLY` 漏了（升级 Frappe 后最常见）；
- 早期调用确实被记到了（钩子生效，不是空跑）。
只读页面、不写库。
"""
from __future__ import annotations

TITLE = "Desk 翻译表单独加载：加载时就翻译的文字不丢"
PRELOAD = r"""
(() => {
  window.__pmEarly = []; window.__pmLoaded = false;
  let wrap = null, w = null;
  function make(f) {
    return function (txt, ...a) {
      if (!window.__pmLoaded) {
        const m = window.frappe && frappe._messages;
        if (m && Object.keys(m).length > 200) window.__pmLoaded = true;
        else if (typeof txt === "string") { const out = f.apply(this, [txt, ...a]); window.__pmEarly.push([txt, out]); return out; }
      }
      return f.apply(this, [txt, ...a]);
    };
  }
  const fr = window.frappe || (window.frappe = {});
  Object.defineProperty(fr, "_", {configurable: true, enumerable: true,
    get() { return wrap; }, set(f) { if (f && f !== wrap) wrap = make(f); }});
  Object.defineProperty(window, "__", {configurable: true,
    get() { return w; }, set(f) { w = (f === wrap || !f) ? f : make(f); }});
})();
"""


def prepare(lab, manifest):
    return {"cases": []}


def steps(params, camera_code=None):
    return [
        {"name": "页面起来", "js":
         "for(let k=0;k<100;k++){if(window.frappe&&frappe.container&&frappe.container.page)break;await new Promise(r=>setTimeout(r,200));}"
         "const msgs=frappe._messages||{};const early=window.__pmEarly||[];"
         "const lost=[...new Set(early.filter(([t,out])=>msgs[t]&&msgs[t]!==t&&out===t).map(([t])=>t))];"
         "return {n:Object.keys(msgs).length, save:__('Save'), lang:frappe.boot.lang, early:new Set(early.map(([t])=>t)).size, lost};", "shot": True},
    ]


def judge(params, results, camera_code=None):
    seen = next((r.get("value") or {} for r in results if r.get("step") == "页面起来"), {})
    return [
        {"case": "the whole table arrived and is in the user's language", "seen": {k: seen.get(k) for k in ("n", "save", "lang")},
         "ok": (seen.get("n") or 0) > 1000 and seen.get("save") == "保存"},
        {"case": "no text translated while loading falls back to the source", "seen": seen.get("lost"),
         "ok": seen.get("lost") == []},
        {"case": "the hook saw the loading-time calls", "seen": seen.get("early"), "ok": (seen.get("early") or 0) > 0},
    ]


def verify(lab, manifest, params, camera_code=None):
    return []
