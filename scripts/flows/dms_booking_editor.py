"""流程用例：LINE 订车单编辑页 —— 保存被拒时提示看得见、出错那一栏标红，改对后保存、新版预览发回聊天（DMS 第一条流程）。

LINE 里的 LIFF 验签走不了实验室，这里走同一个页面的「本站点已登录账号自己的绑定」回落路径：操作员账号在 `datasets.py dms`
里已绑了一个合成 LINE 用户（只在实验室）。外部 DMS 不连：编辑页只读本地完整快照，数据集发布的就是它。保存后的新版预览卡
由交接页走推送回落，站点开着 LINE 本地镜像，「本该发出的卡」记进推送台账（`trigger=line_mirror`），不发出去。

prepare 给操作员造一张待确认的订车草稿（新开户：新客户；老数据：DMS 里已有的那位客户）。页面上：先滚到中间、把交车日期
改成越界的 01/01/2026 点保存 → 被拒，提示在贴底操作条里、看得见，页面滚到交车日期那一栏、光标在里面、那一栏标红（10-30 真机
「第一次点保存像没反应」）→ 改成两周后再保存 → 跳到交接页。库里：草稿换了新 nonce、交车日期是改后的那天；推送台账多一张镜像卡，
是这张草稿的预览卡、写着改后的交车日期。
"""
from __future__ import annotations

import json
import uuid
from datetime import date, timedelta

TITLE = "LINE 订车单编辑页：保存被拒时提示看得见、改对后保存"
BAD_DATE = "01/01/2026"  # 公历当佛历：已经过去的日子（10-30 真机测的就是它）


def be(day: date) -> str:
    return f"{day.day:02d}/{day.month:02d}/{day.year + 543}"


def prepare(lab, manifest):
    good = be(date.today() + timedelta(days=14))
    customer = (manifest.get("customers") or [None])[0]
    nonce = uuid.uuid4().hex[:16]
    out = lab.python(manifest["site"], f"""
from pearnly_core import line_session as store
from pearnly_dms.editor import booking_edit
line_user, endpoint = {manifest['line_user_id']!r}, {manifest['endpoint']!r}
customer = {customer!r}
if customer:
    draft = {{"people_id": customer["people_id"], "prefix_id": "prefix-1", "name": customer["name"], "birthday_be": "01/01/2530",
              "phone": "0800000001", "house_no": "9/9", "province_id": "P2", "district_id": "D2", "subdistrict_id": "S2", "zipcode_id": "Z2"}}
    who = {{"id": customer["customer_id"], "name": customer["name"]}}
else:
    draft = {{"people_id": "1000000730325", "prefix_id": "prefix-1", "name": "ลูกค้าใหม่ ทดสอบ", "birthday_be": "15/05/2535",
              "phone": "0811111111", "house_no": "1", "province_id": "P1", "district_id": "D1", "subdistrict_id": "S1", "zipcode_id": "Z1"}}
    who = {{"id": "", "name": draft["name"]}}
qa = {{"endpoint_id": endpoint, "step": "pay_more", "customer": who, "advisor": {{"id": "advisors-1", "name": "Lab advisor"}},
       "draft": draft,
       "answers": {{"place": {{"id": "place_books-1", "name": "Lab place_books"}}, "car": {{"id": "cars-1", "label": "LAB Lab cars"}},
                   "paint": {{"id": "paint-1", "name": "ขาวมุก"}}, "delivery_date_be": {be(date.today() + timedelta(days=7))!r},
                   "term": {{"id": "term_sales-1", "name": "Lab term_sales"}}, "regis": {{"id": "regis_behalfs-1", "name": "Lab regis_behalfs"}},
                   "regis_name": draft["name"]}},
       "files": {{"id_card_mid": None, "slip_mid": None}}, "payments": [{{"channel": "cash", "amount": "5000.00", "extra": {{}}}}]}}
store.set_session(line_user, booking_edit.STATE, {{"nonce": {nonce!r}, "qa": qa, "endpoint_id": endpoint}})
# 线上站点问过一次 LINE 就记住这人手机的语言（一小时）；实验室没有 LINE 通道，先替它记上，不让保存时去问 LINE
frappe.cache.set_value("pearnly:line:user-language:" + line_user, "zh", expires_in_sec=3600)
mirrored = frappe.db.count("Pearnly Push Log", {{"trigger": "line_mirror", "business_key": line_user}})
frappe.db.commit()
print(json.dumps({{"mirrored_before": mirrored}}))
""")
    return {**out, "nonce": nonce, "good": good, "line_user_id": manifest["line_user_id"], "cases": []}


def steps(params, camera_code=None):
    wait = ("const until=async(f,n=80)=>{for(let k=0;k<n;k++){const v=f();if(v)return v;await new Promise(r=>setTimeout(r,250));}return null;};"
            # 改一个文本框：照真人输入，让页面的数据跟上（Vue 的 v-model 听 input 事件）
            "const type=(el,v)=>{const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;"
            "set.call(el,v);el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));};"
            "const box=()=>document.querySelector('.pn-field-delivery_date_be input');"
            "const inView=(el)=>{if(!el)return null;const r=el.getBoundingClientRect();return r.height>0&&r.top>=0&&r.bottom<=innerHeight;};")
    return [
        {"name": "打开编辑页", "js": wait +
         "const f=await until(()=>box(),160); return f?{opened:true,date:f.value}:{error:'editor not opened: '+document.body.innerText.slice(0,200)};"},
        {"name": "越界日期保存被拒", "js": wait +
         f"type(box(),{json.dumps(BAD_DATE)}); window.scrollTo(0,0); await new Promise(r=>setTimeout(r,300));"
         "document.querySelector('.pn-editor-actions button[type=submit]').click();"
         "const msg=await until(()=>document.querySelector('.pn-editor-actions [role=alert]')); await new Promise(r=>setTimeout(r,500));"
         "const field=document.querySelector('.pn-editor-invalid');"
         "return {message: msg?msg.textContent.trim():'', message_in_view: inView(msg),"
         " invalid: field?[...field.classList].find(c=>c.startsWith('pn-field-')):'', field_in_view: field?(()=>{"
         "const r=field.getBoundingClientRect();return r.top<innerHeight&&r.bottom>0;})():false,"
         " focused_in_field: !!(field&&field.contains(document.activeElement)), url: location.href};", "shot": True},
        # 保存成功后页面会跳到交接页：点保存这一步立刻返回，下一步在新页面里等
        {"name": "改对后保存", "js": wait +
         f"type(box(),{json.dumps(params['good'])});"
         "setTimeout(()=>document.querySelector('.pn-editor-actions button[type=submit]').click(),50); return {clicked:true};", "wait_ms": 8000},
        {"name": "回到聊天交接页", "js": wait +
         "await until(()=>location.pathname.startsWith('/liff/dms-booking'),80); await new Promise(r=>setTimeout(r,2000));"
         "return {handoff: location.pathname.startsWith('/liff/dms-booking'), url: location.pathname+location.search,"
         " text: document.body.innerText.trim().slice(0,200)};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    refused, saved = by.get("越界日期保存被拒", {}), by.get("回到聊天交接页", {})
    return [
        {"case": "editor opens with the draft's delivery date", "seen": by.get("打开编辑页"), "ok": (by.get("打开编辑页") or {}).get("opened") is True},
        {"case": "refused save shows its message where it can be seen", "seen": refused,
         "ok": "ส่งมอบ" in refused.get("message", "") or "交车" in refused.get("message", "") or "delivery" in refused.get("message", "").lower()},
        {"case": "message is inside the visible screen", "seen": refused.get("message_in_view"), "ok": refused.get("message_in_view") is True},
        {"case": "page scrolls to the delivery date field, marks it and puts the cursor in it", "seen": refused,
         "ok": refused.get("invalid") == "pn-field-delivery_date_be" and refused.get("field_in_view") is True
         and refused.get("focused_in_field") is True},
        {"case": "corrected save hands back to the chat page", "seen": saved, "ok": saved.get("handoff") is True},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
from pearnly_core import line_session as store
sess = store.get_session({params['line_user_id']!r}) or {{}}
qa = (sess.get("payload") or {{}}).get("qa") or {{}}
rows = frappe.get_all("Pearnly Push Log", filters={{"trigger": "line_mirror", "business_key": {params['line_user_id']!r}}},
                      fields=["response_body"], order_by="creation desc", limit_page_length=1)
body = json.loads(rows[0].response_body) if rows else {{}}
store.clear_session({params['line_user_id']!r})  # 下一轮从新草稿开始
frappe.db.commit()
print(json.dumps({{"state": sess.get("state"), "nonce": (sess.get("payload") or {{}}).get("nonce"),
                  "delivery": (qa.get("answers") or {{}}).get("delivery_date_be"),
                  "mirrored_after": frappe.db.count("Pearnly Push Log", {{"trigger": "line_mirror", "business_key": {params['line_user_id']!r}}}),
                  "card": json.dumps(body.get("messages") or [], ensure_ascii=False)}}, ensure_ascii=False))
""")
    card = got["card"]
    return [
        {"case": "draft saved with the corrected date under a new nonce", "saved": {k: got[k] for k in ("state", "nonce", "delivery")},
         "ok": got["state"] == "booking_review" and got["nonce"] not in ("", None, params["nonce"]) and got["delivery"] == params["good"]},
        {"case": "the refused date was never stored", "saved": got["delivery"], "ok": got["delivery"] != BAD_DATE},
        {"case": "exactly one preview card went back to the chat (mirrored, not sent)", "saved": [params["mirrored_before"], got["mirrored_after"]],
         "ok": got["mirrored_after"] == params["mirrored_before"] + 1},
        {"case": "the preview card is this draft's, with the corrected date and the new nonce", "saved": card[:300],
         "ok": '"flex"' in card and params["good"] in card and str(got["nonce"]) in card and BAD_DATE not in card},
    ]
