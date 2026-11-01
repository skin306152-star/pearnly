"""流程用例：LINE 扫身份证取景页加载途中不露词条键（11-01 真机 D1：LINE 里弱网取词约 8 秒，正文和按钮一直显示 `dms.edit.loading`、
`dms.edit.scanBack`）。

LINE 里的 LIFF 验签走不了实验室，这里同订车编辑页走「本站点已登录账号自己的绑定」回落路径（操作员在 `datasets.py dms` 里绑了合成 LINE 用户）。
页面脚本跑之前注入一段（`PRELOAD`）：把取词接口 `browser_text` 放慢 2.5 秒模拟弱网，从第一帧起每 50 毫秒采一次页面文字。
词条到了之后：采样里没出现过任何 `dms.` 开头的键名，「返回聊天」按钮是正式文字，假摄像头起来后有取景提示与拍照按钮。只读页面，不传照片、不写库。
"""
from __future__ import annotations

TITLE = "DMS 扫身份证取景页：弱网加载途中不露词条键"
PRELOAD = (
    "(()=>{const f=window.fetch;window.fetch=function(u,...a){const s=String((u&&u.url)||u);"
    "if(s.includes('browser_text'))return new Promise(r=>setTimeout(r,2500)).then(()=>f.call(this,u,...a));return f.call(this,u,...a);};"
    "window.__keys=[];window.__samples=0;const t=setInterval(()=>{if(!document.body)return;window.__samples++;"
    "const m=document.body.innerText.match(/\\bdms\\.[a-zA-Z]+\\.[a-zA-Z.]+/g);if(m)window.__keys.push(...m);},50);"
    "setTimeout(()=>clearInterval(t),20000);})()"
)
# 取景页不是 Desk：页面挂上就开始走（采样已在跑），步骤里再等词条与相机
READY = "!!document.querySelector('.pn-scan')"


def prepare(lab, manifest):
    return {"cases": []}


def steps(params, camera_code=None):
    return [
        {"name": "加载完", "js":
         "for(let k=0;k<80;k++){if(document.querySelector('.pn-scan-actions button'))break;await new Promise(r=>setTimeout(r,250));}"
         "for(let k=0;k<40;k++){if(document.querySelector('.pn-scan-tip'))break;await new Promise(r=>setTimeout(r,250));}"
         "const buttons=[...document.querySelectorAll('.pn-scan-actions button')].map(b=>b.innerText.trim());"
         "return {keys:[...new Set(window.__keys||[])], samples: window.__samples||0, buttons,"
         " tip: (document.querySelector('.pn-scan-tip')||{}).innerText||'', text: document.body.innerText.slice(0,200)};", "shot": True},
    ]


def judge(params, results, camera_code=None):
    seen = next((r.get("value") or {} for r in results if r.get("step") == "加载完"), {})
    buttons = seen.get("buttons") or []
    return [
        {"case": "no text key shows while the texts load", "seen": {k: seen.get(k) for k in ("keys", "samples")},
         "ok": not seen.get("keys") and (seen.get("samples") or 0) > 20},
        {"case": "the back button and the camera tip are real text once loaded", "seen": {"buttons": buttons, "tip": seen.get("tip")},
         "ok": len(buttons) >= 1 and all(b and not b.startswith("dms.") for b in buttons)
         and bool(seen.get("tip")) and not str(seen.get("tip")).startswith("dms.")},
    ]


def verify(lab, manifest, params, camera_code=None):
    return []
