"""流程用例：CC 对话页「设置」——外观（主题）刷新后保留、语言切换落库并整页换语言、再切回。

入口照用户点法：电脑宽度点侧栏底部的齿轮「设置」；手机宽度打开抽屉「菜单」→ 点头像 → 菜单里的「设置」。
主题只存本机浏览器（不落库），断言刷新后仍是选中的；语言写 `User.language`（`pearnly_core.account.preferences.set_language`），
断言整页刷新后界面换成英文、站点启动数据的语言是 en，再切回中文后库里是 zh。实验室站点没开 `pearnly_native_account`，
改语言不调身份服务。用例结束时主题、语言都还原。
"""
from __future__ import annotations

TITLE = "CC 设置：主题刷新后保留、语言切换落库与界面换语言、还原"
READY = "document.readyState === 'complete' && !!document.querySelector('textarea[aria-label]')"
USER = "admin@erp.regression"

WAIT = ("const until=async(f,n=80)=>{for(let k=0;k<n;k++){try{if(f())return true;}catch(e){} await new Promise(r=>setTimeout(r,250));} return false;};"
        "const shown=e=>!!(e&&e.getClientRects().length);"
        "const pressed=()=>Object.fromEntries([...document.querySelectorAll('[aria-pressed]')].filter(shown)"
        ".map(b=>[(b.getAttribute('aria-label')||b.innerText.trim()), b.getAttribute('aria-pressed')==='true']));"
        "const btn=t=>[...document.querySelectorAll('[aria-pressed]')].filter(shown).find(b=>(b.getAttribute('aria-label')||b.innerText.trim())===t);"
        # 打开设置：电脑上齿轮；手机上抽屉底部的头像 → 菜单「设置」/「Settings」。头像菜单是 Radix 下拉，只认指针按下，
        # 所以按真实点按的事件序列发（pointerdown → mousedown → pointerup → mouseup → click）
        "const press=e=>{const o={bubbles:true,cancelable:true,pointerType:'touch',isPrimary:true,button:0}; "
        "e.dispatchEvent(new PointerEvent('pointerdown',o)); e.dispatchEvent(new MouseEvent('mousedown',o)); "
        "e.dispatchEvent(new PointerEvent('pointerup',o)); e.dispatchEvent(new MouseEvent('mouseup',o)); e.click();};"
        "const openSettings=async(label)=>{ if(btn('中文')||btn('English')) return true; "
        "const gear=[...document.querySelectorAll('[data-slot=\"cc-settings-button\"]')].find(shown); "
        "if(gear){gear.click();} else { const me=[...document.querySelectorAll('[data-slot=\"cc-drawer-foot\"] button')].find(shown); "
        "if(!me) return false; press(me); await until(()=>[...document.querySelectorAll('[role=menuitem]')].some(shown),20); "
        "const item=[...document.querySelectorAll('[role=menuitem]')].filter(shown).find(e=>e.innerText.trim()===label); if(item) press(item); } "
        "return await until(()=>btn('中文')||btn('English'),40); };")


def prepare(lab, manifest):
    lang = lab.python(manifest["site"], f'print(json.dumps({{"lang": frappe.db.get_value("User", {USER!r}, "language")}}))')["lang"]
    if lang != "zh":  # 上一轮中途断了：先还原，判定以中文界面为起点
        lab.python(manifest["site"], f'frappe.db.set_value("User", {USER!r}, "language", "zh")\nfrappe.db.commit()\nprint("{{}}")')
    return {"lang_before": lang, "cases": []}


def steps(params, camera_code=None):
    return [
        {"name": "打开设置、选深色", "js": WAIT + "const open=await openSettings('设置'); if(!open) return {error:'settings not opened'}; "
         "const before=pressed(); btn('深色').click(); await until(()=>pressed()['深色']); "
         "return {before, after: pressed(), stored: localStorage.getItem('cc.theme')};"},
        {"name": "刷新", "js": "location.reload(); return 1;", "wait_ms": 4000},
        {"name": "刷新后主题", "js": WAIT + "await openSettings('设置'); return {pressed: pressed(), dark: document.documentElement.classList.contains('dark')};"},
        {"name": "切到 English", "js": WAIT + "btn('跟随系统')?.click(); await until(()=>pressed()['跟随系统']); btn('English').click(); return 1;", "wait_ms": 6000},
        {"name": "英文界面", "js": WAIT + "await until(()=>document.readyState==='complete'&&document.querySelector('textarea[aria-label]'),80); "
         "const t=document.querySelector('textarea[aria-label]').getAttribute('aria-label'); "
         "const open=await openSettings('Settings'); const now=pressed(); "
         "return {placeholder: t, lang: (window.frappe&&frappe.boot&&frappe.boot.lang)||document.documentElement.lang, english: !!now['English'], open};"},
        {"name": "切回中文", "js": WAIT + "await openSettings('Settings'); const zh=btn('中文'); if(!zh) return {error:'no 中文 button'}; zh.click(); return 1;", "wait_ms": 6000},
        {"name": "中文界面", "js": WAIT + "await until(()=>document.readyState==='complete'&&document.querySelector('textarea[aria-label]'),80); "
         "return {placeholder: document.querySelector('textarea[aria-label]').getAttribute('aria-label'), theme: localStorage.getItem('cc.theme')};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    dark, after, en, zh = (by.get(k) or {} for k in ("打开设置、选深色", "刷新后主题", "英文界面", "中文界面"))
    return [
        {"case": "选深色当场生效", "seen": dark, "ok": bool((dark.get("after") or {}).get("深色"))},
        {"case": "刷新后仍是深色", "seen": after, "ok": bool((after.get("pressed") or {}).get("深色"))},
        {"case": "切到 English：整页换成英文", "seen": en,
         "ok": bool(en.get("english")) and str(en.get("lang", "")).startswith("en") and not any("一" <= ch <= "鿿" for ch in en.get("placeholder") or "中")},
        {"case": "切回中文：界面中文、主题已还原为跟随系统", "seen": zh,
         "ok": any("一" <= ch <= "鿿" for ch in zh.get("placeholder") or "") and zh.get("theme") in (None, "system", "")},
    ]


def verify(lab, manifest, params, camera_code=None):
    lang = lab.python(manifest["site"], f'print(json.dumps({{"lang": frappe.db.get_value("User", {USER!r}, "language")}}))')["lang"]
    return [{"case": "库里语言回到 zh", "saved": lang, "ok": lang == "zh"}]
