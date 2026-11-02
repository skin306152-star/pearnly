"""手机键盘流程（`*_keyboard_*.py`）共用的几样东西（不是流程，不登记）：模拟键盘弹出，量正在填的格在不在可见区里、有没有被盖住。

无头 Chrome 弹不出软键盘，按两种浏览器的做法各模拟一遍：
- 安卓（`interactive-widget=resizes-content`，三个产品的页面都带）：键盘弹出时整页变矮。步骤带 `height`（`ui_steps.mjs`）
  在这一步之前把手机视口高度改成可见高度，布局与可见区一起变矮——共用的 `desk/keyboard.js` 从可见区变矮认出键盘、把格摆上来。
- iOS：只缩可见区（`visualViewport`）、布局不变。`PRELOAD` 在页面脚本之前把 `visualViewport` 换成可控的：`__keyboard(高度)` 缩到
  这个高度并发 resize，`__keyboard(null)` 收起（真可见区的 resize 照样转发）。只用在弹窗上：整页滚动时 iOS 自己会平移可见区
  露出页面最底下的格，这个平移模拟不了，整页类只按安卓跑，iOS 写「没测到」。
- iOS App 内嵌（LINE 等内置浏览器的 WKWebView，11-01 iPhone LINE 调拨单备注没滚上来）：可见区变矮，`offsetTop` 报几百，
  元素坐标（`getBoundingClientRect` / `elementFromPoint`）的原点却是可见区顶端——弹窗 CSS `top` 写多少，矩形都从 0 起。
  `__keyboard(高度, 上沿)` 在 iOS 式之外再把两者按这个上沿换算，模拟这种宿主。同样只用在弹窗上。
可见高度取 844 的 45%（三星 A14 Chrome 泰文键盘弹出后可见区约剩 45%）。

`KEYBOARD_BASELINE=1` 时在页面脚本前占住 `window.pearnlyKeyboard`，共用模块不生效：同一条流程跑一遍看没有它时会不会被挡（只在对照时用）。
"""
from __future__ import annotations

import os

VISIBLE = 380
EMBEDDED_TOP = 420  # iOS App 内嵌：键盘弹起后 offsetTop 报的上沿（iPhone LINE 实测可见区 351、offsetTop 363，按 844 高等比）

PRELOAD = (
    "(()=>{const real=window.visualViewport;const fake=new EventTarget();let h=null;let t=0;"
    "const full=()=>real?real.height:innerHeight;const shift=()=>h==null?0:t;"
    "Object.defineProperties(fake,{height:{get:()=>h==null?full():h},width:{get:()=>real?real.width:innerWidth},"
    "offsetTop:{get:shift},offsetLeft:{get:()=>0},scale:{get:()=>1},pageTop:{get:()=>scrollY+shift()},pageLeft:{get:()=>scrollX}});"
    "const rect=Element.prototype.getBoundingClientRect;Element.prototype.getBoundingClientRect=function(){const r=rect.call(this);"
    "return shift()?new DOMRect(r.x,r.y-shift(),r.width,r.height):r;};"
    "const at=Document.prototype.elementFromPoint;Document.prototype.elementFromPoint=function(x,y){return at.call(this,x,y+shift());};"
    "if(real){real.addEventListener('resize',()=>fake.dispatchEvent(new Event('resize')));"
    "real.addEventListener('scroll',()=>fake.dispatchEvent(new Event('scroll')));}"
    "Object.defineProperty(window,'visualViewport',{configurable:true,get:()=>fake});"
    "window.__keyboard=(v,top)=>{h=v;t=top||0;fake.dispatchEvent(new Event('resize'));};})();"
    + ("window.pearnlyKeyboard=true;" if os.environ.get("KEYBOARD_BASELINE") else "")
)

PROBE = """
const kbSleep=ms=>new Promise(r=>setTimeout(r,ms));
const kbUntil=async(f,n=80)=>{for(let k=0;k<n;k++){try{const v=f();if(v)return v;}catch(e){}await kbSleep(250);}return null;};
const kbTyping=el=>el.getClientRects().length>0&&!el.readOnly&&!el.disabled&&(el.tagName==='TEXTAREA'||el.isContentEditable||
  (el.tagName==='INPUT'&&!['button','checkbox','color','date','file','hidden','image','radio','range','reset','submit','time'].includes(el.type)));
const kbFields=(root,n)=>[...(root||document).querySelectorAll('input, textarea, [contenteditable=true]')].filter(kbTyping).slice(-n);
const kbName=el=>(el.closest('[data-fieldname]')||{}).dataset?.fieldname||el.id||el.name||el.placeholder||el.tagName;
// 量一格：可见区里、中心点就是它；在弹窗里时弹窗外框缩成可见区高、滚得到按钮栏
const kbMeasure=async(el)=>{
  const H=visualViewport.height; const r=el.getBoundingClientRect();
  const hit=document.elementFromPoint(r.left+Math.min(r.width/2,40), r.top+r.height/2);
  const row={field:kbName(el), top:Math.round(r.top), bottom:Math.round(r.bottom), visible:Math.round(H), focused:document.activeElement===el,
    open:document.documentElement.classList.contains('pearnly-keyboard-open'),
    clear:r.height>0&&r.top>=0&&r.bottom<=H&&!!hit&&(hit===el||el.contains(hit)||hit.contains(el))};
  const modal=document.querySelector('.modal.show');
  if(modal){
    row.dialog=Math.round(modal.getBoundingClientRect().height); const keep=modal.scrollTop; modal.scrollTop=1e6; await kbSleep(100);
    const foot=modal.querySelector('.modal-footer'); const f=foot&&foot.getBoundingClientRect();
    row.footer=!foot||!f.height||(f.bottom<=H+1&&f.top>=0); modal.scrollTop=keep;
  }
  return row;
};
const kbIos=async(fields,H,T)=>{const out=[];
  for(const el of fields){el.focus(); await kbSleep(150); window.__keyboard(H,T); await kbSleep(800); out.push(await kbMeasure(el));
    el.blur(); window.__keyboard(null); await kbSleep(300);}
  return {fields:out};};
"""


def probe_steps(name: str, prepare_js: str, ios: bool, close_js: str = "", count: int = 3) -> list[dict]:
    """一组页面步骤：`prepare_js` 打开页面 / 弹窗并给出 `root`（在哪里找最下面几格）；iOS 式（Safari、App 内嵌各一步）一步量完，
    安卓式每格聚焦、变矮量、还原三步。"""
    out = [{"name": f"{name}：准备", "js": PROBE + prepare_js +
            f"window.__kbList=kbFields(root,{count}); return {{fields: window.__kbList.map(kbName)}};"}]
    if ios:
        out.append({"name": f"{name}：iOS", "js": PROBE + f"return kbIos(window.__kbList,{VISIBLE},0);"})
        out.append({"name": f"{name}：iOS 内嵌", "js": PROBE + f"return kbIos(window.__kbList,{VISIBLE},{EMBEDDED_TOP});"})
    for i in range(count):
        out += [
            {"name": f"{name}：安卓 {i} 聚焦", "js": PROBE + f"const el=window.__kbList[{i}]; if(!el) return null; el.focus(); await kbSleep(300); return kbName(el);"},
            {"name": f"{name}：安卓 {i}", "height": VISIBLE, "js": PROBE +
             f"const el=window.__kbList[{i}]; if(!el) return null; await kbSleep(700); return kbMeasure(el);"},
            {"name": f"{name}：安卓 {i} 收起", "height": 0, "js": PROBE +
             f"const el=window.__kbList[{i}]; if(el) el.blur(); await kbSleep(400); "
             "return {open: document.documentElement.classList.contains('pearnly-keyboard-open'), modal_style: (document.querySelector('.modal.show')||{style:{}}).style.cssText||''};"},
        ]
    if close_js:
        out.append({"name": f"{name}：关闭", "js": PROBE + close_js})
    return out


def judge_probes(results, names: list[str], ios: bool) -> list[dict]:
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    checks = []
    for name in names:
        prepared = (by.get(f"{name}：准备") or {}).get("fields") or []
        rows = {"安卓": [by.get(f"{name}：安卓 {i}") or {} for i in range(len(prepared))]}
        if ios:
            rows["iOS"] = (by.get(f"{name}：iOS") or {}).get("fields") or []
            rows["iOS 内嵌"] = (by.get(f"{name}：iOS 内嵌") or {}).get("fields") or []
        for mode, fields in rows.items():
            checks.append({"case": f"{name}（{mode}）：键盘弹起时正在填的格在可见区里、没被盖住", "seen": fields,
                           "ok": len(prepared) > 0 and len(fields) == len(prepared)
                           and all(f.get("focused") and f.get("clear") and f.get("open") for f in fields)})
            dialogs = [f for f in fields if "dialog" in f]
            if dialogs:
                checks.append({"case": f"{name}（{mode}）：弹窗缩进可见区、滚得到按钮栏",
                               "seen": [{k: f.get(k) for k in ("field", "dialog", "visible", "footer")} for f in dialogs],
                               "ok": all(abs(f["dialog"] - f["visible"]) <= 2 and f.get("footer") for f in dialogs)})
        closed = [by.get(f"{name}：安卓 {i} 收起") or {} for i in range(len(prepared))]
        checks.append({"case": f"{name}：键盘收起后类名去掉、弹窗没留内联样式", "seen": closed,
                       "ok": all(c.get("open") is False and "height" not in (c.get("modal_style") or "") for c in closed)})
    return checks
