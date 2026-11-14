"""流程用例：CC 对话里删除、立即运行先出「允许 / 拒绝」卡片（用户 11-12；任务记录 docs/work/2026-11-14-cc-approval-cards.md）。

真页面、假智能体（`lab_agents.py` 的 ``[lab-tool]``），Cowork 直写对话：
1. 「删掉这两张」：假 CC 调 ``cowork_delete_documents``（两张）→ 出卡，卡上一张一行「单号 · 对方 · 金额」→ 点「拒绝」→ 两张都还在、
   模型收到 denied；再说一次 → 点「允许」→ 只删这两张，第三张还在。
2. 「立即运行」：假 CC 调 ``cowork_run_folder_task`` → 卡上任务名、账套、方向、文件夹与「新文件将识别（扣费）并推送」→ 点「允许」→
   任务记下一次运行请求（`run_request` 加 1）。实验室没有电脑来取，不会真的识别。
3. 卡片新文案四语：把账号语言依次换成泰、英、日，各让假 CC 再调一次立即运行，看卡上问句与「识别扣费」那一行是这种语言、不回退英文
   （英文本来就是英文）、不横向溢出；各截一张后点「拒绝」（不再加运行请求）。最后语言还原成中文。

电脑与手机宽度各跑一遍（手机上卡片浮在输入框上方）。造数据同 `cowork_operator_lib.py`（合成 Express 连接、识别单据），另在这条
连接上建一条定时任务（直接落表，读文件夹的电脑不存在：只为出卡与记运行请求）。`CLEANUP` 按连接删掉全部数据；定时任务另删。
"""
from __future__ import annotations

import json
import uuid

from cc_approval_lib import JS, set_language, tool
from cowork_operator_lib import BASE, CLEANUP

TITLE = "CC 对话里删单据、立即运行先出「允许 / 拒绝」卡片：卡上列清哪几张 / 哪个任务，拒绝不执行，允许只执行卡上那几条；新文案四语"
READY = "document.readyState === 'complete' && !!document.querySelector('textarea[aria-label]')"
USER = "admin@cowork.regression"
RULE = "Cowork Folder Intake Rule"
DELETE, RUN = "cowork_delete_documents", "cowork_run_folder_task"
# 卡上新文案按语言该是的样子（术语表 apps/pearnly_cowork/pearnly_cowork/i18n/messages.tsv）
WORDS = {
    "zh": {"ask": "确定要立即运行此定时任务吗？", "fact": "新文件将识别（扣费）并推送"},
    "th": {"ask": "เรียกใช้งานตามกำหนดเวลานี้ตอนนี้หรือไม่", "fact": "ไฟล์ใหม่จะถูกอ่าน (มีค่าบริการ) และส่งเข้าระบบ"},
    "en": {"ask": "Run this scheduled task now?", "fact": "New files are recognized (charged) and pushed"},
    "ja": {"ask": "この定期タスクを今すぐ実行しますか？", "fact": "新しいファイルを認識（課金）して送信します"},
}
DELETE_ASKS = {"zh": "确定要删除以下单据吗？", "th": "ลบเอกสารเหล่านี้หรือไม่", "en": "Delete these documents?",
               "ja": "これらの書類を削除しますか？"}


def prepare(lab, manifest):
    site = manifest["site"]
    set_language(lab, site, USER, "zh")
    tag = "appr" + uuid.uuid4().hex[:6]
    numbers = [f"FLOW-APPR-{tag}-{i}".upper() for i in (1, 2, 3)]
    out = lab.python(site, CLEANUP + BASE + f"""
docs = [recognized(n) for n in {numbers!r}]
from pearnly_cowork.folder_intake import access as folder_access
rule = frappe.get_doc({{"doctype": {RULE!r}, "title": "Flow approval {tag}", "workspace": ws, "connection": conn["name"],
                       "rule_owner": "admin@cowork.regression", "enabled": 1, "account_set_key": "FLOWOP",
                       "account_label": "Flow operator 69", "direction": "purchase", "posting_kind": "service",
                       "root_id": "flowroot", "folder": "Invoices\\\\{tag}", "run_day": 1, "run_time": "22:00",
                       "run_request": 0}})
folder_access.write(rule)  # 定时任务表只经本域写（原生接口一律拒）
frappe.db.commit()
print(json.dumps({{"docs": docs, "rule": rule.name, "company": company["id"]}}, default=str))
""")
    docs, rule = out["docs"], out["rule"]
    return {"tag": tag, "numbers": numbers, "docs": docs, "rule": rule, "cases": [],
            "delete": tool(tag, DELETE, {"documents": docs[:2]}), "run": tool(tag, RUN, {"task": rule}),
            "delete_third": tool(tag, DELETE, {"documents": docs[2:]})}


def steps(params, camera_code=None):
    head = JS
    count = ("const left=async()=>((await call('frappe.client.get_count',{doctype:'Cowork OCR History',"
             f"filters:JSON.stringify({{name:['in',{json.dumps(params['docs'])}]}})}}))); ")
    out = [
        {"name": "删两张：出卡", "shot": True, "file": "1-delete-card", "js": head + f"return await ask({json.dumps(params['delete'])}, {DELETE!r});"},
        {"name": "删两张：拒绝", "js": head + count + f"const r=await answer(false, {DELETE!r}); return {{result: r, left: await left()}};"},
        {"name": "删两张：再来一次并允许", "shot": True, "file": "2-delete-allowed", "js": head + count +
         f"const opened=await ask({json.dumps(params['delete'])}, {DELETE!r}); const r=await answer(true, {DELETE!r}); "
         "await sleep(600); return {card: opened, result: r, left: await left()};"},
        {"name": "立即运行：出卡", "shot": True, "file": "3-run-now-card-zh", "js": head + f"return await ask({json.dumps(params['run'])}, {RUN!r});"},
        {"name": "立即运行：允许", "js": head + f"return await answer(true, {RUN!r});"},
    ]
    for lang in ("th", "en", "ja"):
        out += [
            {"name": f"语言 {lang}", "wait_ms": 9000, "js": head + f"return await setLang({USER!r}, {lang!r});"},
            {"name": f"立即运行卡片 {lang}", "shot": True, "file": f"4-run-now-card-{lang}", "js": head +
             "await until(()=>!!q('textarea.aui-composer-input'),160); "
             f"return await ask({json.dumps(params['run'])}, {RUN!r});"},
            {"name": f"删单据卡片 {lang}", "shot": True, "file": f"5-delete-card-{lang}", "js": head +
             f"const r=await answer(false, {RUN!r}); const del=await ask({json.dumps(params['delete_third'])}, {DELETE!r}); "
             "return {...del, run_result: r};"},
            {"name": f"拒绝删单据 {lang}", "js": head + f"return await answer(false, {DELETE!r});"},
        ]
    out.append({"name": "语言还原", "wait_ms": 6000, "js": head + f"return await setLang({USER!r}, 'zh');"})
    out.append({"name": "删除会话", "js": head + f"const mine=(await listed('chat')).filter(s=>(s.title||'').includes({json.dumps(params['tag'])})); "
                "for(const s of mine) await call('pearnly_core.assistant.api.delete',{session_id:s.id},true); return {ids: mine.map(s=>s.id)};"})
    return out


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    first, denied, again, run_card, ran = (by.get(k) or {} for k in
                                           ("删两张：出卡", "删两张：拒绝", "删两张：再来一次并允许", "立即运行：出卡", "立即运行：允许"))
    numbers = params["numbers"]
    rows = first.get("rows") or []
    checks = [
        {"case": "说「删掉这两张」：出允许卡，问句是中文，一张一行写单号 · 对方 · 金额（不是内部编号），没有第三张", "seen": first,
         "ok": bool(first.get("shown")) and first.get("question") == DELETE_ASKS["zh"] and len(rows) == 2
         and all(numbers[i] in rows[i] and "Flow seller" in rows[i] and "107" in rows[i] for i in (0, 1))
         and not any(doc in " ".join(rows) for doc in params["docs"]) and numbers[2] not in " ".join(rows)
         and not first.get("overflow")},
        {"case": "点「拒绝」：模型收到 denied，两张都还在（连同第三张共 3 张）", "seen": denied,
         "ok": (denied.get("result") or {}).get("status") == "denied" and denied.get("left") == 3},
        {"case": "再说一次、点「允许」：删掉这两张（模型收到 deleted 2），库里只剩第三张", "seen": again,
         "ok": bool((again.get("card") or {}).get("shown")) and (again.get("result") or {}).get("deleted") == 2
         and again.get("left") == 1},
        {"case": "立即运行：出卡，卡上任务名、账套、方向、文件夹与「新文件将识别（扣费）并推送」", "seen": run_card,
         "ok": bool(run_card.get("shown")) and run_card.get("question") == WORDS["zh"]["ask"]
         and any(f"Flow approval {params['tag']}" in row for row in run_card.get("rows") or [])
         and any("Flow operator 69" in row for row in run_card.get("rows") or [])
         and any(params["tag"] in row and "Invoices" in row for row in run_card.get("rows") or [])
         and WORDS["zh"]["fact"] in (run_card.get("rows") or []) and not run_card.get("overflow")},
        # 结果里带文件夹路径（反斜杠按 Markdown 画会少一个，整段解析不了时只读到第一个字段 task）：认这次的任务号；记没记下请求看 verify
        {"case": "立即运行：点「允许」，模型收到这个任务的结果（requested）", "seen": ran,
         "ok": ran.get("requested") is True or (ran.get("partial") is True and ran.get("task") == params["rule"])},
    ]
    for lang in ("th", "en", "ja"):
        card, deleting = by.get(f"立即运行卡片 {lang}") or {}, by.get(f"删单据卡片 {lang}") or {}
        seen = {"run": card, "delete": deleting, "delete_result": by.get(f"拒绝删单据 {lang}")}
        checks.append({"case": f"{lang}：立即运行卡上问句与「识别扣费」一行、删单据卡上问句都是这种语言（不回退英文）、不横向溢出；"
                               "两张都点「拒绝」，不运行、不删", "seen": seen,
                       "ok": card.get("question") == WORDS[lang]["ask"] and WORDS[lang]["fact"] in (card.get("rows") or [])
                       and not card.get("overflow") and (deleting.get("run_result") or {}).get("status") == "denied"
                       and deleting.get("question") == DELETE_ASKS[lang] and not deleting.get("overflow")
                       and (seen.get("delete_result") or {}).get("status") == "denied"})
    checks.append({"case": "删掉这段会话", "seen": by.get("删除会话"), "ok": bool((by.get("删除会话") or {}).get("ids"))})
    return checks


def verify(lab, manifest, params, camera_code=None):
    out = lab.python(manifest["site"], CLEANUP + f"""
kept = frappe.db.exists("Cowork OCR History", {params['docs'][2]!r})
gone = [d for d in {params['docs'][:2]!r} if not frappe.db.exists("Cowork OCR History", d)]
runs = frappe.db.get_value({RULE!r}, {params['rule']!r}, "run_request")
frappe.db.delete({RULE!r}, {{"name": {params['rule']!r}}})
frappe.db.commit()
gone_all = cleanup()
print(json.dumps({{"kept": bool(kept), "gone": gone, "run_request": runs, "left": leftovers()}}, default=str))
""")
    return [
        {"case": "库里：允许后只删了卡上那两张，第三张还在", "seen": out, "ok": out.get("kept") is True and len(out.get("gone") or []) == 2},
        {"case": "库里：立即运行只在点「允许」那一次记了请求（三次「拒绝」没加）", "seen": out, "ok": out.get("run_request") == 1},
        {"case": "造的连接、公司、锁都清掉了", "seen": out.get("left"), "ok": not any((out.get("left") or {}).values())},
    ]


def cleanup(lab, manifest, params):
    """成功、失败、超时都跑：语言还原成中文、删掉定时任务与造的数据。"""
    set_language(lab, manifest["site"], USER, "zh")
    lab.python(manifest["site"], CLEANUP + f"""
frappe.db.delete({RULE!r}, {{"name": {params['rule']!r}}})
frappe.db.commit()
cleanup()
print("{{}}")
""")
