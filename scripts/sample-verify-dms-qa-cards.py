# -*- coding: utf-8 -*-
"""订车逐问卡片验证（纯逻辑；需站点上下文，因为要读站点配置里的 LIFF）。

钉住四件事：

1. **文案在包里**：卡片正文不出现裸 key；错误话术走 `dms.err.*` 而不另存一份。
2. **分页是硬的**：LINE quick reply 上限 13，本层按 11 + 2 个导航做；页号越界要夹住，
   既不空转也不静默丢选项。
3. **目录空 vs 读取失败**分得开：目录权威为空 → 手工问法；有行 → 选银行。
4. **预览卡**：顾问（提成归属）要露出来；编辑按钮的有无取决于本站点配没配 LIFF
   （没配就回落本站点网页编辑器，**不是消失、也不是空链接**）。

用法: scripts/sample-verify-dms-qa-cards.sh <组> <站点>
"""


def main():
    from pearnly_dms.booking.conversation import qa_cards as q
    from pearnly_dms.booking.conversation import qa_payment_cards as pc

    ok = []

    def ck(label, cond, detail=""):
        ok.append(bool(cond))
        print(("PASS " if cond else "FAIL ") + label + ((" — " + str(detail)) if detail else ""))

    ck("两个模块 import 不成环（互相引用只在函数体内）", True)
    ck("显式常量可用", q.PAY_LABELS["cash"] == "เงินสด" and q.LBL_TOTAL == "ยอดเงินจองรวม",
       q.PAY_LABELS["cash"])
    ck("模块 __getattr__ 能给外部取到未显式定义的文案",
       q.TXT_ASK_CAR == "รุ่นรถ — พิมพ์ชื่อรุ่นสั้น ๆ เพื่อค้นหา เช่น dmax", q.TXT_ASK_CAR[:20])
    ck("错误话术走 dms.err（不另存一份）",
       q.TXT_MASTER_CHANGED
       == __import__("pearnly_dms.line.errors", fromlist=["x"]).friendly("ERR_DMS_MASTER_CHANGED")["th"])
    # 这一条**必须**查 `dms.card.` —— 前缀原本是线上函数体里的内联字面量，
    # 只查「结尾是重试尾」的话，取不到词时整串会变成 `<裸 key> + 错误原文 + 重试尾`，
    # 照样通过（真漏过一次，靠防漏键守卫才发现）。
    ck("开局闸话术 = 前缀 + 错误原文 + 重试尾（不在 LINE 里另说一套）",
       q.TXT_ADVISOR_BLOCK.endswith("แล้วลองใหม่อีกครั้ง")
       and "ERR_DMS" not in q.TXT_ADVISOR_BLOCK
       and "dms.card." not in q.TXT_ADVISOR_BLOCK
       and "ยังเปิดใบจองไม่ได้ครับ" in q.TXT_ADVISOR_BLOCK)
    ck("认不出账号的通用版同样有前缀（不落到裸 key）",
       "dms.card." not in q.TXT_ADVISOR_BLOCK_NO_USER
       and "ระบบยังอ่านบัญชี DMS" in q.TXT_ADVISOR_BLOCK_NO_USER)
    # 语言包读取器对每条值做 `strip()`，行尾换行存不进 CSV —— 线上那句换行在字面量尾部，
    # 所以要由代码补，否则会连成一行。
    ck("前缀与错误原文之间有换行（行尾换行存不进语言包）",
       q.TXT_ADVISOR_BLOCK.split("\n")[0].endswith("»")
       and q.TXT_ADVISOR_BLOCK_NO_USER.split("\n")[0].endswith("ได้"),
       q.TXT_ADVISOR_BLOCK.split("\n")[0][-12:])
    ck("带占位符的模板能套", q.ask_amount("เงินสด")["text"].startswith("ยอดเงิน (เงินสด)"))

    ck("分页常量：LINE 硬限 13、每页 11", q.QR_LIMIT == 13 and q.QR_PAGE_SIZE == 11)
    rows = [[str(i), "C%02d" % i, "车 %02d" % i] for i in range(1, 24)]
    items = q._pick_rows(rows, "car", lambda r: f"{r[1]} {r[2]}")
    ck("第一页 11 项 + 下一页 = 12 项（≤13）", len(items) == 12, len(items))
    items2 = q._pick_rows(rows, "car", lambda r: f"{r[1]} {r[2]}", 1)
    ck("第二页带上一页/下一页", len(items2) == 13, len(items2))
    items3 = q._pick_rows(rows[:3], "car", lambda r: f"{r[1]} {r[2]}", 9)
    ck("页号越界夹到最后一页（不空、不死循环）", len(items3) == 3, len(items3))
    ck("正文列出完整值（补 quick reply 标签的 20 字限制）",
       q._option_text("T", rows[:2], lambda r: r[2]).count("\n") == 2)

    ck("付款问法：目录权威为空 → 手工填银行名",
       "ชื่อธนาคาร" in pc.ask_pay_ref("cheque", manual_bank=True)["text"])
    ck("付款问法：目录有行 → 选银行",
       pc.ask_payment_bank([["7", "SCB", "ไทยพาณิชย์", "", ""]])["text"].startswith("เลือกธนาคาร"))
    ck("目录为空 + 已知渠道 → 手工问法",
       "ชื่อธนาคาร" in pc.ask_payment_bank([], channel="cheque")["text"])
    ck("目录为空 + 渠道未知 → 通用补充问法（不冒充手工银行问法）",
       pc.ask_payment_bank([])["text"] == q.TXT_ASK_OTHER_REF)

    qa = {
        "answers": {"place": {"name": "店 1"}, "car": {"label": "D-Max", "id": "1"},
                    "paint": {"name": "ขาว"}, "delivery_date_be": "01/10/2569",
                    "term": {"name": "现金"}, "regis": {"name": "自己"},
                    "regis_name": "สมชาย"},
        "files": {"id_card_mid": "m1"},
        "payments": [{"channel": "cash", "amount": "1000"}],
        "advisor": {"name": "สมชาย ใจดี"},
        "summary": {"people_id": "1", "name": "n", "birthday_be": "b", "address": "a",
                    "zipcode": "10500", "phone": "08"},
    }
    card = q.preview_card(qa, "n1")
    blob = str(card)
    ck("预览卡是 flex 且不露裸 key", card["type"] == "flex" and "dms.card." not in blob)
    wanted = ("สมชาย ใจดี", "店 1", "D-Max", "01/10/2569", "现金", "10500")
    ck("预览卡含顾问/店/车色/交车日/销售条件/邮编", all(x in blob for x in wanted),
       [x for x in wanted if x not in blob])
    ck("预览卡含订金合计（两位小数）", "1,000.00" in blob)
    ck("确认/丢弃走既有 ACT_*（订车流程不用改就能接住）",
       "action=confirm_booking" in blob and "action=cancel_booking" in blob)

    import frappe

    saved = frappe.conf.get("pearnly_line_liff_id")
    try:
        ck("本站点配了 LIFF → 有编辑按钮（uri）",
           any(b.get("action", {}).get("type") == "uri"
               for b in card["contents"]["footer"]["contents"]))
        frappe.conf["pearnly_line_liff_id"] = ""
        card2 = q.preview_card(qa, "n1")
        uris = [b["action"]["uri"] for b in card2["contents"]["footer"]["contents"]
                if b.get("action", {}).get("type") == "uri"]
        ck("没配 LIFF → 编辑按钮改指本站点网页编辑器（不消失、也不给空链接）",
           len(uris) == 1 and "/liff/dms-booking?draft=n1" in uris[0], uris)
    finally:
        frappe.conf["pearnly_line_liff_id"] = saved

    rcard = q.booking_retry_card("", "n2")
    ck("重试卡有标题/提示/重试按钮且不露 key",
       "dms.card." not in str(rcard) and "สร้างใบจองไม่สำเร็จ" in str(rcard))
    ck("连不上/系统中断的重试卡不叫人去别处退出 DMS；被顶下线的才叫",
       "ออกจากระบบ" not in str(rcard)
       and "ออกจากระบบ" in str(q.booking_retry_card("", "n3", code="ERR_DMS_CONCURRENT_LOGIN")))

    # ── 裸名守卫 ──────────────────────────────────────────────────────
    # PEP 562 的模块 `__getattr__` 只覆盖「模块属性访问」，**管不到模块内函数里的
    # 全局名查找**。踩过：函数里写裸 `BTN_CONFIRM` → 直接 NameError。
    # 这里静态扫：函数体里引用的全大写名字，必须在模块级（或函数内 import/赋值）有定义。
    import ast as _ast

    from pearnly_dms.booking.conversation import qa_payment_cards as _pc

    def _naked(mod):
        tree = _ast.parse(open(mod.__file__, encoding="utf-8").read())
        top = set()
        for node in tree.body:
            if isinstance(node, _ast.Assign):
                top |= {t.id for t in node.targets if isinstance(t, _ast.Name)}
            elif isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
                top.add(node.name)
            elif isinstance(node, (_ast.Import, _ast.ImportFrom)):
                top |= {(a.asname or a.name.split(".")[0]) for a in node.names}
        found = []
        for fn in [n for n in _ast.walk(tree)
                   if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef))]:
            local = {a.arg for a in fn.args.args} | {a.arg for a in fn.args.kwonlyargs}
            for sub in _ast.walk(fn):
                if isinstance(sub, (_ast.Import, _ast.ImportFrom)):
                    local |= {(a.asname or a.name.split(".")[0]) for a in sub.names}
                elif isinstance(sub, _ast.Assign):
                    local |= {t.id for t in sub.targets if isinstance(t, _ast.Name)}
            found += [(fn.name, s.id) for s in _ast.walk(fn)
                      if isinstance(s, _ast.Name) and isinstance(s.ctx, _ast.Load)
                      and s.id.isupper() and s.id not in top and s.id not in local]
        return found

    naked = _naked(q) + _naked(_pc)
    ck("函数体里的全大写名字都在模块内有定义（模块 __getattr__ 管不到函数内）", not naked, naked)

    # ── 语言包首尾空白守卫 ────────────────────────────────────────────
    # 教训：frappe 的语言包读取器对每条值做 `strip()`，**行尾换行/首尾空格存不住**。
    # 线上有 4 条正是把分隔符写在字面量首尾（`"…| 01\n" + TXT_SEP_HINT`、
    # `" แล้วลองใหม่อีกครั้ง"`）—— 只把文字搬进 CSV，分隔符会静默消失，
    # 拼接结果与线上差一个换行/空格（肉眼在 LINE 里几乎看不出来）。
    # 规矩：**分隔符放代码里，语言包只存文字**；CSV 里出现首尾空白一律当错。
    import csv as _csv

    import frappe as _frappe

    csv_path = _frappe.get_app_path("pearnly_dms", "translations", "th.csv")
    with open(csv_path, encoding="utf-8", newline="") as fh:
        padded = [(i, row[0], repr(row[1]))
                  for i, row in enumerate(_csv.reader(fh), 1)
                  if len(row) >= 2 and row[1].replace("\\n", "\n") != row[1].replace("\\n", "\n").strip()]
    ck("语言包里没有首尾空白（读取器会 strip，分隔符必须放代码里）", not padded, padded)

    failed = [x for x in ok if not x]
    print("")
    print("合计: 通过 %d / 失败 %d" % (len(ok) - len(failed), len(failed)))
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAIL"))


if __name__ == "__main__":
    main()
