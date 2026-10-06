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
    from pathlib import Path
    from urllib.parse import parse_qs

    from pearnly_dms.line import cards as C

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
        """卡片上每个按钮的 postback 动作名。

        ⚠️ 编辑按钮现在是 **uri** 深链（线上行为），没有 `data` —— 所以这里必须用
        `.get("data", "")` 而不是直接取键，否则一取就 KeyError（真踩过一次）。
        """
        out = []
        for button in buttons(msg):
            data = button["action"].get("data", "")
            out.append(parse_qs(data).get("action", [""])[0] if data else "")
        return out

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
    # 第二个按钮是**编辑深链**（uri，没有 postback 的 data），所以只比较动作名会得到空串 ——
    # 线上就是这么发的（`cards._edit_button`：能拿到地址就把 action 换成 uri）。
    check("新客卡按钮 = 保存/修改，只有两个（拍板瘦身）",
          actions(card) == ["create_new", ""]
          and [b["action"].get("type") for b in buttons(card)] == ["postback", "uri"],
          [b["action"].get("type") for b in buttons(card)])
    check("缺失字段显示占位符 — 而不是空白",
          C._kv_row("x", "")["contents"][1]["text"] == "—")

    # ── 3. 一致卡 ─────────────────────────────────────────────────────
    same = C.same_customer_card(summary, "n2")
    check("一致卡按钮 = 用旧资料/修改（修改同样是深链）",
          actions(same)[0] == "keep"
          and [b["action"].get("type") for b in buttons(same)][1] == "uri",
          [b["action"].get("type") for b in buttons(same)])
    check("一致卡仍带完整预览（不然会把手输出错的销售堵死）",
          any("9990000000014" in t for t in body_texts(same)),
          [t for t in body_texts(same) if "9990" in t])

    # ── 4. 差异卡：主按钮由权限决定 ────────────────────────────────────
    diffs = [{"label": "เบอร์โทร", "old": "1", "new": "0812345678"}]
    upd = C.diff_card(diffs, "n3", primary="update", summary=summary)
    # 第三个按钮是**编辑深链**（uri，没有 postback 的 data）—— 与老基准一致
    # （`501589f0:services/line_dms/cards.py` 的 `diff_card` 收尾就是 `_edit_button(nonce)`），
    # 所以动作名是空串，但类型必须是 uri：这一条挡的就是"点编辑又多弹一张卡"的回归。
    check("有改写权 → 主按钮是更新",
          actions(upd) == ["update", "keep", ""]
          and [b["action"].get("type") for b in buttons(upd)] == ["postback", "postback", "uri"],
          actions(upd))
    none = C.diff_card(diffs, "n4", primary="none", summary=summary)
    check("无改写权 → 不给主按钮", "update" not in actions(none), actions(none))
    check("无改写权 → 卡上写清要先配管理员",
          C.TXT_ADMIN_NEEDED in body_texts(none), body_texts(none)[-1][:40])
    check("差异卡始终保留用旧资料 / 修改两条退路",
          actions(upd)[-2:] == ["keep", ""]
          and [b["action"].get("type") for b in buttons(upd)][-1] == "uri",
          actions(upd))
    check("差异卡的 [แก้ไข] 是深链，点一下直接进编辑页（不多弹卡）",
          buttons(upd)[-1]["action"]["uri"].startswith("http")
          and "editor=customer" in buttons(upd)[-1]["action"]["uri"],
          buttons(upd)[-1]["action"].get("uri", ""))
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
    cc = C.candidates_message(cands, "n6")
    qr = [item["action"] for item in cc["quickReply"]["items"]]
    picks = [a for a in qr if parse_qs(a.get("data", "")).get("action", [""])[0] == "pick"]
    check("候选是文案 + 原生快捷按钮，不是卡片（用户 2026-10-06）", cc["type"] == "text", cc["type"])
    check("候选按钮最多 4 个", len(picks) == 4, len(picks))
    check("候选按钮标签带序号与客户码，不超过 LINE 的 20 字",
          picks[0]["label"] == "1. C0" and all(len(a["label"]) <= 20 for a in qr), [a["label"] for a in qr])
    check("候选文案逐行列出客户码 + 姓名 + 证号尾 4", "1. C0 · ชื่อ0 · x0123" in cc["text"], cc["text"])
    check("最后一个按钮是新建客户兜底",
          parse_qs(qr[-1]["data"]).get("action", [""])[0] == "create_new", qr[-1])

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

    # ── 8. 编辑入口：**一个深链按钮**，不是逐字段菜单 ──────────────────
    # ⚠️ 线上（基准 501589f0）早把"在 LINE 里逐字段改"退役了：`customer_edit_entry.py`
    #    写明 "retired text-edit events cannot change customer fields"，点 [แก้ไข]
    #    改成「回一个按钮 → 打开浏览器编辑页」。新平台这次跟着改齐 ——
    #    `edit_menu_message` / `edit_prompt` / `EDIT_FIELDS` / `ACT_EDIT_FIELD` /
    #    `ACT_EDIT_CANCEL` 全部删掉（线上也没有这些）。
    import frappe
    from unittest import mock

    for gone in ("edit_menu_message", "edit_prompt", "EDIT_FIELDS",
                 "ACT_EDIT_FIELD", "ACT_EDIT_CANCEL"):
        check("逐字段编辑已退役，模块里不再有 %s" % gone, not hasattr(C, gone))

    before = frappe.conf.get("pearnly_line_liff_id")

    def edit_buttons(msg):
        return [b for b in buttons(msg) if b["action"].get("type") == "uri"]

    try:
        frappe.conf["pearnly_line_liff_id"] = "2006123456-AbCdEf"
        card = C.new_customer_card(summary, "n7")
        uris = edit_buttons(card)
        check("配了 LIFF → [แก้ไข] 是**深链按钮**（uri），不是 postback",
              len(uris) == 1, [b["action"].get("type") for b in buttons(card)])
        uri = uris[0]["action"]["uri"] if uris else ""
        check("深链进的是**客户档**编辑器（带 editor=customer）",
              "editor=customer" in uri and "draft=n7" in uri, uri)
        check("按钮文案取自语言包（BTN_EDIT）",
              bool(uris) and uris[0]["action"]["label"] == C.BTN_EDIT,
              uris[0]["action"] if uris else "")

        # 没配 LIFF 也**不是不给按钮**：回落本站点网页路径（与老 `edit_link` 同规则）。
        frappe.conf["pearnly_line_liff_id"] = ""
        fallback = edit_buttons(C.same_customer_card(summary, "n7"))
        check("没配 LIFF → 深链落到本站点网页路径（不是空链接、也不是报错）",
              len(fallback) == 1 and "/liff/dms-booking" in fallback[0]["action"]["uri"],
              fallback[0]["action"] if fallback else "")

        # 连站点地址都取不到（极端情形）→ 退回 postback，由 `ACT_EDIT` 处理器说话。
        with mock.patch("frappe.utils.get_url", return_value=""):
            bare = C.same_customer_card(summary, "n7")
        check("拿不到任何地址 → 退回 postback（不生成按不动的空链接）",
              not edit_buttons(bare)
              and any(b["action"].get("type") == "postback" for b in buttons(bare)),
              [b["action"].get("type") for b in buttons(bare)])

        # 「点编辑之后回的那张卡」与卡片上的按钮同源（线上 `customer_edit_entry._open`）。
        entry = C.edit_entry_message("n7")
        entry_actions = [item["action"] for item in entry["quickReply"]["items"]]
        check("点编辑回的是文案 + 一个原生快捷按钮（不是卡片）",
              entry["type"] == "text" and len(entry_actions) == 1, entry)
        check("且它就是同一条深链",
              entry_actions[0].get("type") == "uri" and "draft=n7" in entry_actions[0]["uri"],
              entry_actions)
    finally:
        if before is None:
            frappe.conf.pop("pearnly_line_liff_id", None)
        else:
            frappe.conf["pearnly_line_liff_id"] = before

    # ── 9. 文案必须在语言包里（防漏键）────────────────────────────────
    # 教训：卡片文案从代码搬进 `translations/th.csv` 时漏了两个键，
    # 界面上直接把 `dms.card.BTN_EDIT_CANCEL` 露给泰国销售看。
    # 这里扫一遍**全 app**：代码里引用到的每个卡片键，包里都必须有。
    import glob
    import os
    import re

    from pearnly_dms import i18n

    used = set()
    for path in glob.glob(os.path.join(os.path.dirname(i18n.__file__), "**", "*.py"), recursive=True):
        src = Path(path).read_text(encoding="utf-8")
        # ⚠️ 只有**本命名空间**的 `_t` 才算 `dms.card.*`。本仓库现在有三个命名空间
        # （`dms.card.*` / `.qa.*` / `.query.*`；`.approval.*` 已随审批删除），各自的模块都叫
        # `dms_<ns>_cards.py`，每个都有自己那个 `_t`。不分青红皂白地收进来，就会拿一批
        # 住在别的命名空间的键去 `dms.card.*` 里找，报一整屏假"缺键"（**踩了三次**：
        # qa_cards、query_cards、approval_cards）。
        # 判据：文件里只要出现 `dms.card.<子命名空间>.` 就说明它用的是那套，跳过。
        # 各命名空间自己的"防漏键"由各自的验收脚本负责（qa-cards / query-cards）。
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
    used |= {f"diff.{f}" for f in C.FIELD_LABELS_TH}
    used |= {f"sum.{f}" for f in C._SUMMARY_FIELD_NAMES}
    from pearnly_dms.booking import booking_car_details as _car_details

    used |= {f"car.{name}" for _, name in _car_details._FIELD_NAMES}
    # 订车逐问那两族（`dms_qa_cards` / `dms_qa_payment_cards`）另有一套取词：
    # 显式常量与键同名、值一律 `qat("KEY")`，所以扫 `qat(...)` 字面量就够；
    # 渠道名是 `qat(f"pay.{渠道}")` 拼出来的，**动态那条单独按渠道表补**
    # （不然 6 条 `qa.pay.*` 会从守卫里溜过去）。
    from pearnly_dms.booking.conversation import qa_cards as _qa

    for path in glob.glob(os.path.join(os.path.dirname(i18n.__file__), "**", "*.py"), recursive=True):
        src = Path(path).read_text(encoding="utf-8")
        used |= {f"qa.{m}" for m in re.findall(
            r"""qat\(\s*["']([A-Za-z_][A-Za-z0-9_]*)["']""", src)}
    used |= {f"qa.pay.{ch}" for ch in _qa.PAY_LABELS}
    # 文档串里举的例子，不是真引用（`dms_qa_cards.__getattr__` 的说明里也举了一个）
    used.discard("TXT_XXX")
    used.discard("qa.TXT_XXX")
    # Explicit QA constants may deliberately use the canonical error catalog.
    # Check their actual source namespace, not a duplicate card translation.
    import ast
    error_aliases = {}
    for node in ast.parse(Path(_qa.__file__).read_text(encoding="utf-8")).body:
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name) and node.value.func.id == "_err_text"
                and len(node.value.args) == 1 and isinstance(node.value.args[0], ast.Constant)):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    error_aliases["qa." + target.id] = "dms.err." + node.value.args[0].value
        # 按收件人语言现取的那张表（`_COMPOSED = {"TXT_X": lambda: _err_text("ERR_Y"), ...}`）同理。
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict)
                and any(isinstance(t, ast.Name) and t.id == "_COMPOSED" for t in node.targets)):
            for key, value in zip(node.value.keys, node.value.values):
                body = value.body if isinstance(value, ast.Lambda) else None
                if (isinstance(key, ast.Constant) and isinstance(body, ast.Call)
                        and isinstance(body.func, ast.Name) and body.func.id == "_err_text"
                        and len(body.args) == 1 and isinstance(body.args[0], ast.Constant)):
                    error_aliases["qa." + key.value] = "dms.err." + body.args[0].value
    error_keys = {error_aliases[k] for k in used if k in error_aliases}
    def missing_error_keys(packs):
        return [(key, lang) for key in error_keys for lang in i18n.LANGUAGES
                if not packs[lang].get(key) or packs[lang].get(key) == key]

    packs = {lang: i18n.pack(lang) for lang in i18n.LANGUAGES}
    missing_errors = missing_error_keys(packs)
    check("共享错误话术在四语目录中存在", not missing_errors, missing_errors)
    if error_keys:
        key = sorted(error_keys)[0]
        incomplete = {lang: dict(pack) for lang, pack in packs.items()}
        incomplete["en"].pop(key, None)
        check("缺少英文时守卫不能用泰语回落冒充通过", (key, "en") in missing_error_keys(incomplete))
    used -= error_aliases.keys()
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
        ("候选消息", C.candidates_message([], "n4")),
    ) if "dms.card." in str(msg)]
    check("卡片正文不露裸 key", not leaked, leaked)

    # ── 10. 车型规格卡 + 提交前权威复核 ────────────────────────────────
    from pearnly_dms.booking import booking_car_details as dms_booking_car_details
    from pearnly_dms.booking import booking_preflight as dms_booking_preflight
    from pearnly_dms.contracts.client_base import DmsClientError
    from pearnly_dms.contracts.models import DMSMasterRef

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
