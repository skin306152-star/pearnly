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
        # ⚠️ 只有**本命名空间**的 `_t` 才算 `dms.card.*`。本仓库现在有四个命名空间
        # （`dms.card.*` / `.qa.*` / `.query.*` / `.approval.*`），各自的模块都叫
        # `dms_<ns>_cards.py`，每个都有自己那个 `_t`。不分青红皂白地收进来，就会拿一批
        # 住在别的命名空间的键去 `dms.card.*` 里找，报一整屏假"缺键"（**踩了三次**：
        # qa_cards、query_cards、approval_cards）。
        # 判据：文件里只要出现 `dms.card.<子命名空间>.` 就说明它用的是那套，跳过。
        # 各命名空间自己的"防漏键"由各自的验收脚本负责（qa-cards / query-cards / approval-flow）。
        if not re.search(r"dms\.card\.[a-z_]+\.", src):
            used |= set(re.findall(r'_t\(\s*"([A-Za-z_][A-Za-z0-9_]*)"', src))
        # ⚠️ 前缀 lookbehind 不能少：**本仓库有三套命名空间**（`dms.card.*` /
        # `dms.card.qa.*` / `dms.card.query.*`），而它们的模块分别叫 `cards` / `qa_cards` /
        # `query_cards`。只写 `cards\.` 会把后两个也算进来，于是拿别的命名空间的键去
        # `dms.card.*` 里找 → 假报缺键（真踩了两次：先 `qa_cards`、后 `query_cards`）。
        used |= set(re.findall(
            r"(?<![\w.])cards\.(TXT_[A-Z0-9_]+|BTN_[A-Z0-9_]+|MSG_[A-Z0-9_]+)", src))
        # `qa_cards.` 那条路也扫（那是另一套命名空间 `dms.card.qa.*`）
        used |= {f"qa.{m}" for m in re.findall(
            r"qa_cards\.(TXT_[A-Z0-9_]+|BTN_[A-Z0-9_]+|LBL_[A-Z0-9_]+|VAL_[A-Z0-9_]+)", src)}
    used |= {f"edit.{f}" for f, _ in C.EDIT_FIELDS}
    used |= {f"diff.{f}" for f in C.FIELD_LABELS_TH}
    used |= {f"sum.{f}" for f in C._SUMMARY_FIELD_NAMES}
    from pearnly_dms import dms_booking_car_details as _car_details

    used |= {f"car.{name}" for _, name in _car_details._FIELD_NAMES}
    # 订车逐问那两族（`dms_qa_cards` / `dms_qa_payment_cards`）另有一套取词：
    # 显式常量与键同名、值一律 `qat("KEY")`，所以扫 `qat(...)` 字面量就够；
    # 渠道名是 `qat(f"pay.{渠道}")` 拼出来的，**动态那条单独按渠道表补**
    # （不然 6 条 `qa.pay.*` 会从守卫里溜过去）。
    from pearnly_dms import dms_qa_cards as _qa

    for path in glob.glob(os.path.join(os.path.dirname(C.__file__), "*.py")):
        src = open(path, encoding="utf-8").read()
        used |= {f"qa.{m}" for m in re.findall(
            r"""qat\(\s*["']([A-Za-z_][A-Za-z0-9_]*)["']""", src)}
    used |= {f"qa.pay.{ch}" for ch in _qa.PAY_LABELS}
    # 文档串里举的例子，不是真引用（`dms_qa_cards.__getattr__` 的说明里也举了一个）
    used.discard("TXT_XXX")
    used.discard("qa.TXT_XXX")
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

    # ── 10. 车型规格卡 + 提交前权威复核 ────────────────────────────────
    from pearnly_dms import dms_booking_car_details, dms_booking_preflight
    from pearnly_dms.dms_client_base import DmsClientError
    from pearnly_dms.dms_models import DMSMasterRef

    full_car = ["3", "C", "Camry", "", "Toyota", "", "รถเก๋ง", "", "2.5 HV", "",
                "G", "", "อัตโนมัติ", "2568", "", "2.5L", "1,599,000", "X"]
    qa = {"answers": {"car": {"id": "3"}},
          "master_snapshot": {"rows": {"cars": [full_car]}}}
    rows10 = dms_booking_car_details.rows(qa)
    labels = [r["contents"][0]["text"] for r in rows10]
    check("车型规格卡：标签取自语言包（不是写死的泰文）",
          labels == [i18n.t("dms.card.car." + n, lang="th")
                     for _, n in dms_booking_car_details._FIELD_NAMES],
          labels[:3])
    check("车型规格卡：正文不露裸 key", "dms.card." not in str(rows10))
    check("车型规格卡：取不到的列直接不出，不留空槽",
          all(r["contents"][1]["text"] for r in rows10) and len(rows10) == 8, len(rows10))
    short_car = ["3", "C", "Camry", "", "Toyota", "", "รถเก๋ง"]
    got = dms_booking_car_details.rows(
        {"answers": {"car": {"id": "3"}},
         "master_snapshot": {"rows": {"cars": [short_car]}}})
    # 只列到 index 6：能出的就是 4(品牌) 与 6(类型) 两行
    check("车型规格卡：只有前几列时只出这两行", len(got) == 2
          and [r["contents"][0]["text"] for r in got]
          == [i18n.t("dms.card.car.brand", lang="th"), i18n.t("dms.card.car.type", lang="th")],
          len(got))
    check("车型规格卡：车不在快照里 → 空（不编）",
          dms_booking_car_details.rows({"answers": {"car": {"id": "999"}}}) == [])

    # 提交前权威复核：主档没变 → ok；变了 → 报回字段；颜色取不到 → 明确失败
    unchanged = {"answers": {"car": {"id": "3"}, "paint": {"id": "P1"}},
                 "master_snapshot": {"rows": {"cars": [full_car], "paints_by_car": {"3": []}}}}
    live = {"cars": [full_car], "advisors": [], "place_books": [], "term_sales": [],
            "branches": [], "regis_behalfs": [], "company_banks": []}

    class _Pre:
        def _bshsd_all(self, elem, **kw):
            return [["P1", "", "ขาว"]]

    # 复核的结论**照 reconcile 的判定透传**，并带上"是哪个字段变了"
    with_advisor = {**unchanged, "advisor": {"id": "7", "name": "สมชาย"}}
    live_full = {**live, "advisors": [["7", "S07", "สมชาย"]]}
    gone = {"advisor": {"id": "7", "name": "สมชาย"}, "answers": {"car": {"id": "999"}},
            "master_snapshot": {"rows": {"cars": []}}}
    res = dms_booking_preflight.readonly_preflight(
        _Pre(), object(), gone,
        fetch_masters=lambda cl: live_full, fetch_payment_banks=lambda ad: {})
    # 注意：reconcile 是按它自己的顺序逐项查（顾问 → 店 → … → 车），所以这里断言的是
    # "preflight 把 reconcile 的结论**原样透传**并带上字段与错误码"，不是"一定报车"。
    check("主档对不上 → 透传 reconcile 的结论（非 ok + 字段 + 错误码）",
          res["status"] != "ok" and bool(res.get("field")) and bool(res.get("code")),
          {k: res.get(k) for k in ("status", "field", "code")})
    no_advisor = {"answers": {"car": {"id": "3"}}, "master_snapshot": {"rows": {}}}
    res = dms_booking_preflight.readonly_preflight(
        _Pre(), object(), no_advisor,
        fetch_masters=lambda cl: live_full, fetch_payment_banks=lambda ad: {})
    check("顾问没了 → 报的是 advisor（不把根因说成车）",
          res["status"] != "ok" and res.get("field") == "advisor",
          {k: res.get(k) for k in ("status", "field", "code")})
    check("复核函数本身不吃 qa 的空值（不炸）",
          isinstance(dms_booking_preflight.readonly_preflight(
              _Pre(), object(), with_advisor,
              fetch_masters=lambda cl: live_full, fetch_payment_banks=lambda ad: {}), dict))

    class _NoPaint:
        def _bshsd_all(self, elem, **kw):
            return None  # 取数失败，不是"这车没颜色"

    try:
        dms_booking_preflight.readonly_preflight(
            _NoPaint(), object(), unchanged,
            fetch_masters=lambda cl: live, fetch_payment_banks=lambda ad: {})
        check("颜色取数失败 → 明确失败（不拿空表冒充「这车没颜色」）", False, "没抛")
    except DmsClientError as exc:
        check("颜色取数失败 → 明确失败（不拿空表冒充「这车没颜色」）",
              exc.error_code == "ERR_DMS_MASTER_UNAVAILABLE", exc.error_code)

    failed = [x for x in results if not x[1]]
    print("")
    print("合计: 通过 %d / 失败 %d" % (len(results) - len(failed), len(failed)))
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAIL"))


if __name__ == "__main__":
    main()
