# -*- coding: utf-8 -*-
"""DMS LINE 卡片构造验证（纯逻辑，零 IO）。

卡片是操作员**唯一看得到的东西** —— 它错了，用户就会按错按钮、确认错数据。
所以这里重点验几个"防坑"行为：

· 没有改写权时**不给主按钮**（让人点了才知道「要管理员」是最差体验）；
· 回执**不谎称新建**（create 被幂等转成 update 就要说 update）；
· 卡片上**不藏优先级**（主按钮由调用方按角色解出）；
· 顾问名字缺就整行不出，不留空槽。
"""


def main():
    from urllib.parse import parse_qs

    from pearnly_dms import dms_cards as C

    results = []

    def check(label, ok, detail=""):
        results.append((label, bool(ok), detail))
        print(("PASS " if ok else "FAIL ") + label + ((" — " + str(detail)) if detail else ""))

    def buttons(msg):
        return msg["contents"]["footer"]["contents"]

    def body_buttons(msg):
        """卡片**主体**里的按钮 —— 候选选择就放这儿（footnote 只留兜底动作）。"""
        out = []
        for row in msg["contents"]["body"]["contents"]:
            if row.get("type") == "button":
                out.append(row)
        return out

    def body_texts(msg):
        out = []
        for row in msg["contents"]["body"]["contents"]:
            if row.get("type") == "text":
                out.append(row.get("text", ""))
            for cell in row.get("contents") or []:
                if isinstance(cell, dict) and cell.get("type") == "text":
                    out.append(cell.get("text", ""))
        return out

    def actions(msg):
        return [parse_qs(b["action"]["data"]).get("action", [""])[0] for b in buttons(msg)]

    summary = {
        "people_id": "9990000000014", "name": "สมชาย ใจดี", "birthday_be": "01/01/2530",
        "address": "99/1 หมู่ 3 ต.บางรัก อ.บางรัก จ.กรุงเทพมหานคร 10500",
        "zipcode": "10500", "phone": "0812345678",
    }

    # ── 1. postback data 编码 ──────────────────────────────────────────
    data = C._data(C.ACT_CREATE, nonce="n1")
    parsed = parse_qs(data)
    check("postback data 可解析", parsed["action"][0] == "create_new", data)
    check("nonce 编在 data 里", parsed["nonce"][0] == "n1", data)
    check("空值不进 data（不留空槽）",
          "cid" not in parse_qs(C._data("x", cid="")), C._data("x", cid=""))
    check("cid 有值就带上",
          parse_qs(C._data(C.ACT_PICK, nonce="n", cid="59"))["cid"][0] == "59")

    # ── 2. 新客卡 ─────────────────────────────────────────────────────
    card = C.new_customer_card(summary, "n1")
    check("新客卡是 flex（altText 不能空）",
          card["type"] == "flex" and bool(card["altText"]), card["altText"])
    check("新客卡复述五要素（每张卡先看全信息）",
          sum(1 for r in card["contents"]["body"]["contents"] if r.get("type") == "box") == 5,
          sum(1 for r in card["contents"]["body"]["contents"] if r.get("type") == "box"))
    check("新客卡按钮 = 保存/修改，只有两个（拍板瘦身）",
          actions(card) == ["create_new", "edit"], actions(card))
    check("缺失字段显示占位符 — 而不是空白",
          C._kv_row("x", "")["contents"][1]["text"] == "—")

    # ── 3. 一致卡 ─────────────────────────────────────────────────────
    same = C.same_customer_card(summary, "n2")
    check("一致卡按钮 = 用旧资料/修改", actions(same) == ["keep", "edit"], actions(same))
    check("一致卡仍带完整预览（不然会把手输出错的销售堵死）",
          any("9990000000014" in t for t in body_texts(same)),
          [t for t in body_texts(same) if "9990" in t])

    # ── 4. 差异卡：主按钮由权限决定 ────────────────────────────────────
    diffs = [{"label": "เบอร์โทร", "old": "1", "new": "0812345678"}]
    upd = C.diff_card(diffs, "n3", primary="update", summary=summary)
    check("有改写权 → 主按钮是更新", actions(upd) == ["update", "keep", "edit"], actions(upd))
    none = C.diff_card(diffs, "n4", primary="none", summary=summary)
    check("无改写权 → 不给主按钮", "update" not in actions(none), actions(none))
    check("无改写权 → 卡上写清要先配管理员",
          C.TXT_ADMIN_NEEDED in body_texts(none), body_texts(none)[-1][:40])
    appr = C.diff_card(diffs, "n5", primary="approval", summary=summary)
    check("信用授权类 → 主按钮是提交审核",
          actions(appr)[0] == "approval_submit", actions(appr))
    check("差异卡始终保留用旧资料 / 修改两条退路",
          actions(appr)[-2:] == ["keep", "edit"], actions(appr))
    check("差异行空值渲染成 —",
          C.diff_rows([{"label": "x", "old": "", "new": ""}])[0]["contents"][1]["text"]
          == "—  →  —",
          C.diff_rows([{"label": "x", "old": "", "new": ""}])[0]["contents"][1]["text"])

    # ── 5. 相似候选卡 ─────────────────────────────────────────────────
    cands = [
        {"customer_id": str(i), "cuscode": f"C{i}", "name": f"ชื่อ{i}",
         "people_id": "1234567890123"}
        for i in range(6)
    ]
    cc = C.candidates_card(cands, "n6")
    picks = [b for b in body_buttons(cc)
             if parse_qs(b["action"]["data"]).get("action", [""])[0] == "pick"]
    check("候选按钮最多 4 个（LINE bubble 体积限制）", len(picks) == 4, len(picks))
    check("候选选择按钮在卡片主体（footer 只留兜底）",
          len(body_buttons(cc)) == 4 and len(buttons(cc)) == 1,
          (len(body_buttons(cc)), len(buttons(cc))))
    check("候选标签带客户码 + 证号尾 4",
          "C0" in picks[0]["action"]["label"] and "x0123" in picks[0]["action"]["label"],
          picks[0]["action"]["label"])
    check("候选卡有新建客户兜底", actions(cc) == ["create_new"], actions(cc))

    # ── 6. 回执：不谎称 ────────────────────────────────────────────────
    r_new = C.receipt_text("126", "สมชาย", "create")
    r_upd = C.receipt_text("126", "สมชาย", "update")
    check("新建回执说新建", "สร้างลูกค้าใหม่" in r_new, r_new.replace("\n", " / "))
    check("更新回执说更新（被幂等转了就不许说新建）",
          "อัปเดตข้อมูลลูกค้า" in r_upd and "สร้างลูกค้าใหม่" not in r_upd,
          r_upd.replace("\n", " / "))
    check("回执带客户码", "126" in r_new)

    # ── 7. 订车回执：顾问行 ────────────────────────────────────────────
    with_adv = C.booking_receipt_text("BK001", "Camry", "18/09/2569", "dmstest")
    no_adv = C.booking_receipt_text("BK001", "Camry", "18/09/2569", "")
    check("订车回执带单号/车型/交车日",
          all(x in with_adv for x in ("BK001", "Camry", "18/09/2569")),
          with_adv.replace("\n", " / "))
    check("有顾问 → 出顾问行（当场核对提成归属）", "ที่ปรึกษา: dmstest" in with_adv)
    check("没顾问 → 整行不出，不留空槽",
          "ที่ปรึกษา" not in no_adv, no_adv.replace("\n", " / "))

    # ── 8. 修改菜单 ────────────────────────────────────────────────────
    # ⚠️ 这套「选字段 → 输入新值」的逐字段文字编辑，**线上已退役**
    #    （线上 `services/line_dms/customer_edit_entry.py` 写明 "retired text-edit
    #    events cannot change customer fields"，点 [แก้ไข] 改成直接开浏览器编辑页）。
    #    新平台这段目前**没有任何调用方**，本节只保证它在被删掉之前不自相矛盾。
    #    第 8 批返工（customer_edit / customer_edit_entry / edit_link）时，本节连同
    #    `edit_menu_message` / `edit_prompt` / `EDIT_FIELDS` 一起替换成「编辑按钮产出
    #    URI 深链」的验证。
    menu = C.edit_menu_message("n7")
    qr = menu["quickReply"]["items"]
    check("修改菜单列出全部可改字段 + 取消",
          len(qr) == len(C.EDIT_FIELDS) + 1, len(qr))
    fields = [parse_qs(i["action"]["data"]).get("field", [""])[0] for i in qr]
    check("每项都带 field", all(f for f in fields[:-1]), fields)
    check("末项是取消", parse_qs(qr[-1]["action"]["data"])["action"][0] == "edit_cancel")
    check("修改提示用泰文标签", "ชื่อ-นามสกุล" in C.edit_prompt("name"), C.edit_prompt("name"))
    check("地址四级 id 不进可改字段（改文本后由级联重解）",
          all(k not in dict(C.EDIT_FIELDS)
              for k in ("province_id", "district_id", "zipcode_id")),
          [k for k, _ in C.EDIT_FIELDS])

    # ── 9. 文案必须在语言包里（防漏键）────────────────────────────────
    # 教训：卡片文案从代码搬进 `translations/th.csv` 时漏了两个键，
    # 界面上直接把 `dms.card.BTN_EDIT_CANCEL` 露给泰国销售看。
    # 这里扫一遍**全 app**：代码里引用到的每个卡片键，包里都必须有。
    import glob
    import os
    import re

    from pearnly_dms import i18n

    used = set()
    for path in glob.glob(os.path.join(os.path.dirname(C.__file__), "*.py")):
        src = open(path, encoding="utf-8").read()
        used |= set(re.findall(r'_t\(\s*"([A-Za-z_][A-Za-z0-9_]*)"', src))
        used |= set(re.findall(r"cards\.(TXT_[A-Z0-9_]+|BTN_[A-Z0-9_]+|MSG_[A-Z0-9_]+)", src))
    used |= {f"edit.{f}" for f, _ in C.EDIT_FIELDS}
    used |= {f"diff.{f}" for f in C.FIELD_LABELS_TH}
    used |= {f"sum.{f}" for f in C._SUMMARY_FIELD_NAMES}
    used.discard("TXT_XXX")  # i18n.py 文档串里举的例子，不是真引用
    missing = sorted(k for k in used if i18n.t("dms.card." + k, lang="th").startswith("dms.card."))
    check("代码引用的卡片键在语言包里都有（防漏键）", not missing,
          "共查 %d 个键，缺 %s" % (len(used), missing))

    pack = i18n.pack("th")
    check("语言包里没有空文案", all(str(x).strip() for x in pack.values()),
          [k for k, x in pack.items() if not str(x).strip()])

    # 卡片正文里不该出现 `dms.card.` —— 出现就说明取词失败、把 key 露给用户了
    leaked = [name for name, msg in (
        ("新客卡", C.new_customer_card({"people_id": "1", "name": "n", "birthday_be": "b",
                                        "address": "a", "phone": "p"}, "n1")),
        ("一致卡", C.same_customer_card({}, "n2")),
        ("差异卡", C.diff_card([{"label": "ชื่อ", "old": "a", "new": "b"}], "n3",
                               primary="none", summary={})),
        ("候选卡", C.candidates_card([], "n4")),
    ) if "dms.card." in str(msg)]
    check("卡片正文不露裸 key", not leaked, leaked)

    failed = [x for x in results if not x[1]]
    print("")
    print("合计: 通过 %d / 失败 %d" % (len(results) - len(failed), len(failed)))
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAIL"))


if __name__ == "__main__":
    main()
