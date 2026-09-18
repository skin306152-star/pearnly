# -*- coding: utf-8 -*-
"""查资料卡片层验证：文案在包里、按钮结构对、看板卡片照线上。

这一层的错法有两种，都得钉：

· **文案漏进包**：老产品这份文件里内联字面量比常量还多（几十条），漏一条用户就看见
  裸 key 或半句泰文 —— 所以先扫"模块里还有没有泰文字符"，再扫"用到的键在不在包里"。
· **按钮结构错**：标签超过 LINE 的 20 字硬限会被拒；`_data` 把空值也写进去会让默认值
  看起来像用户选的。这两条都是静默的，用户只看到"按钮点不动"。

用法: scripts/sample-verify-dms-query-cards.sh <组> <站点>
"""


def main():
    import ast
    import csv
    import os
    import re

    import frappe

    from pearnly_dms import dms_query_cards as qc
    from pearnly_dms import i18n

    ok = []

    def ck(label, cond, detail=""):
        ok.append(bool(cond))
        print(("PASS " if cond else "FAIL ") + label + ((" - " + str(detail)) if detail else ""))

    # ── 1. 模块里不许留泰文 ──────────────────────────────────────────
    src = open(qc.__file__, encoding="utf-8").read()
    thai = sorted(set(re.findall(r"[\u0e00-\u0e7f]+", src)))
    ck("模块里一个泰文字符都不留（文案全在包里）", not thai, thai[:5])

    # ── 2. 用到的键都在包里（防漏键，含动态拼出来的那几族）────────────
    used = {m for m in re.findall(r'_t\(\s*"([A-Za-z_][A-Za-z0-9_]*)"', src)}
    used |= {key for key, _ in qc._DIMENSIONS}
    used |= set(qc._PROMPTS.values())
    used |= {key for pairs in qc._STATUS_CHOICES.values() for key, _ in pairs}
    used |= {"TXT_PICK_TYPE", "PICK_STATUS", "ASK_TOP_GROUP", "ASK_TOP_METRIC",
             "ASK_TOP_PERIOD", "STATUS_UNKNOWN", "NAV_PREV", "NAV_REFRESH", "NAV_NEXT",
             "LBL_CUSTOMER", "LBL_CAR", "LBL_ADVISOR", "LBL_DATE", "LBL_DELIVERY",
             "LBL_FINANCE", "LBL_ALLOCATION", "LBL_DOCS", "ALT_RECORDS",
             "EMPTY_RECORDS_PREFIX", "CARD_TITLE", "CARD_NOTE", "LBL_PAGE", "LBL_COUNT",
             "LBL_READ_AT", "UNIT_AMOUNT", "UNIT_QTY", "EMPTY_TOP", "ALT_TOP", "TITLE_TOP",
             "TOP_NOTE_PREFIX"}

    missing = sorted(k for k in used
                     if i18n.t("dms.card.query." + k, lang="th").startswith("dms.card.query."))
    ck("模块用到的键在包里都有（共查 %d 个）" % len(used), not missing, missing)

    # 包里那几条最可见的文案：**实值**比对（抽包是从线上源码 AST 抽的，这里钉住关键几条）
    ck("拒答话术是原文",
       qc.TXT_DENIED == i18n.t("dms.card.query.TXT_DENIED", lang="th")
       and "ยังไม่ได้รับสิทธิ์" in qc.TXT_DENIED, qc.TXT_DENIED[:24])
    ck("读数据中的话术是原文",
       "กำลังอ่านข้อมูลล่าสุด" in qc.TXT_LOADING, qc.TXT_LOADING)
    ck("读失败与无账号话术都有",
       "อ่านข้อมูลจาก DMS ไม่สำเร็จ" in qc.TXT_QUERY_FAILED
       and "ไม่พบบัญชี DMS" in qc.TXT_NO_ENDPOINT)

    # ── 3. 动作码与协议载荷 ─────────────────────────────────────────
    ck("七个动作码与线上一致",
       qc.QUERY_ACTION_NAMES == ("query_type", "query_dimension", "query_status",
                                 "query_top_group", "query_top_metric", "query_top_period",
                                 "query_page"), qc.QUERY_ACTION_NAMES)
    ck("载荷跳过空值（免得默认值看起来像用户选的）",
       qc._data("query_page", kind="records", direction="next") == "action=query_page&kind=records&direction=next"
       and qc._data("query_page", kind="records", direction="") == "action=query_page&kind=records",
       qc._data("query_page", kind="records", direction=""))
    long_label = qc._t("ASK_TOP_PERIOD")
    ck("quick reply 标签截到 20 字（LINE 硬限），完整文本进 displayText",
       len(qc._item(long_label, "query_page")["action"]["label"]) == 20
       and qc._item(long_label, "query_page")["action"]["displayText"] == long_label,
       len(qc._item(long_label, "query_page")["action"]["label"]))

    # ── 4. 问法卡的结构 ────────────────────────────────────────────
    t = qc.query_type_message()
    ck("类别卡三项且顺序照旧",
       [i["action"]["data"] for i in t["quickReply"]["items"]]
       == ["action=query_type&kind=sales", "action=query_type&kind=inventory",
           "action=query_type&kind=other"], t["quickReply"]["items"][0]["action"]["data"])
    d = qc.sales_dimension_message()
    dims = [i["action"]["data"] for i in d["quickReply"]["items"]]
    ck("维度卡 13 项，且顺序照旧（改顺序就是改产品）",
       len(dims) == 13 and dims[0].endswith("dimension=latest") and dims[-1].endswith("dimension=top"),
       dims[:2] + dims[-1:])
    ck("金融/分配/合同三组状态按钮数照旧",
       len(qc.status_message("finance")["quickReply"]["items"]) == 4
       and len(qc.status_message("allocation")["quickReply"]["items"]) == 3
       and len(qc.status_message("contract")["quickReply"]["items"]) == 4)
    ck("状态码用的是 DMS 的那套（不是我们自己编的）",
       [i["action"]["data"] for i in qc.status_message("allocation")["quickReply"]["items"]]
       == ["action=query_status&status=unallocated",
           "action=query_status&status=allocated_no_contract",
           "action=query_status&status=ready"])
    ck("要打字的维度各有提示，认不出就走兜底",
       qc.input_prompt("vehicle")["text"] == qc._t("ASK_VEHICLE")
       and qc.input_prompt("没这个维度")["text"] == qc._t("ASK_ANY"),
       qc.input_prompt("没这个维度")["text"])
    ck("看板条件卡：分组 3 项 / 指标 2 项 / 期间 6 项（期间+条数一次选完）",
       len(qc.top_group_message()["quickReply"]["items"]) == 3
       and len(qc.top_metric_message()["quickReply"]["items"]) == 2
       and len(qc.top_period_message()["quickReply"]["items"]) == 6)
    ck("期间卡把条数一起带上（month/5、year/10、custom 不带）",
       "period=month&limit=5" in qc.top_period_message()["quickReply"]["items"][0]["action"]["data"]
       and "period=custom" in qc.top_period_message()["quickReply"]["items"][-1]["action"]["data"]
       and "limit" not in qc.top_period_message()["quickReply"]["items"][-1]["action"]["data"])
    ck("自定义期间给的是带例子的提示（不是空话）",
       "01/08/2569" in qc.top_custom_prompt()["text"], qc.top_custom_prompt()["text"])

    # ── 5. 看板卡片 ────────────────────────────────────────────────
    empty = qc.sales_board({"rows": [], "page": 1})
    ck("查不到 → 一句'没找到 + 读到几点'，不是空白",
       empty["type"] == "text" and empty["text"].startswith(qc._t("EMPTY_RECORDS_PREFIX"))
       and qc._now_label() in empty["text"], empty["text"])
    # ⚠️ 前缀与时间之间**必须有一个空格**：线上那句以空格结尾，而语言包读取器会 strip，
    # 所以空格只能写在代码里。少了它就是 `เมื่อ19/09/2026`（粘在一起）。
    ck("前缀与时间之间有空格（行尾空格存不进语言包）",
       qc._t("EMPTY_RECORDS_PREFIX") + " " in empty["text"]
       and "เมื่อ " + qc._now_label() in empty["text"], empty["text"][-28:])
    ck("查不到也给了重读按钮", empty["quickReply"]["items"], len(empty["quickReply"]["items"]))

    row = {"booking_no": "BK001", "record_status": "จัดสรรแล้ว", "customer": "คุณสมชาย",
           "vehicle": "DMAX", "color": "ขาว", "advisor": "dmstest",
           "booking_date": "01/09/2569", "delivery_date": "20/09/2569",
           "finance_status": "อนุมัติ", "allocation_status": "จัดสรรแล้ว",
           "sales_doc_no": "SO-1", "engine_no": ""}
    board = qc.sales_board({"rows": [row], "page": 2, "limit": 10, "has_more": True})
    ck("记录看板是轮播：汇总卡 + 每行一张小卡",
       board["type"] == "flex" and board["contents"]["type"] == "carousel"
       and len(board["contents"]["contents"]) == 2, len(board["contents"]["contents"]))
    ck("汇总卡带页码/条数/读到几点", "หน้า" in str(board) and qc._now_label() in str(board))
    ck("行号按页码偏移（第 2 页第 1 行 = 11）", "11. BK001" in str(board))
    ck("翻了页才出'上一页'，有下一页才出'下一页'，重读永远在",
       [i["action"]["data"] for i in qc._navigation({"page": 2, "has_more": True}, "records")]
       == ["action=query_page&kind=records&direction=prev",
           "action=query_page&kind=records&direction=refresh",
           "action=query_page&kind=records&direction=next"]
       and len(qc._navigation({"page": 1, "has_more": False}, "records")) == 1)
    ck("单号/机号都没有时整行不出（不留空槽）",
       "SO-1" not in str(qc._record_bubble({**row, "sales_doc_no": "", "engine_no": ""}, 1)))
    ck("超长结果被轮播上限截住（LINE 最多 12 张）",
       len(qc.sales_board({"rows": [row] * 30, "page": 1, "limit": 30})["contents"]["contents"]) == 12)

    top = qc.top_sales_board({"metric": "amount", "date_from": "01/09/2569", "date_to": "18/09/2569",
                              "page": 1, "limit": 10, "has_more": False,
                              "rows": [{"label": "DMAX", "value": 12345.5},
                                       {"label": "HILUX", "value": 100}]})
    blob = str(top)
    ck("金额按两位小数带千分位", "12,345.50" in blob and "100.00" in blob)
    ck("条数指标换成'台'而不是'金额'",
       "คัน" in str(qc.top_sales_board({"metric": "quantity", "rows": [{"label": "A", "value": 1}]})))
    ck("排行是 序号. 名称  值 单位",
       "1. DMAX  12,345.50 บาท" in blob, blob[blob.find("1. DMAX"):][:30])
    ck("看板脚注的前缀与时间之间也有空格",
       qc._t("TOP_NOTE_PREFIX") + " " + qc._now_label() in blob, blob[-40:])
    ck("没有数据时给一句'这段时间没有销售'，不是空表",
       qc._t("EMPTY_TOP") in str(qc.top_sales_board({"metric": "quantity", "rows": []})))

    # ── 6. 时间口径 ────────────────────────────────────────────────
    label = qc._now_label()
    ck("'读到几点'用**公历**（同一张卡上期间才是佛历）",
       re.match(r"^\d{2}/\d{2}/\d{4} \d{2}:\d{2}$", label)
       and 2020 <= int(label.split("/")[2].split(" ")[0]) <= 2100, label)

    failed = [x for x in ok if not x]
    print("")
    print("合计: 通过 %d / 失败 %d" % (len(ok) - len(failed), len(failed)))
    print("RESULT: %s" % ("ALL PASS" if not failed else "FAIL"))


if __name__ == "__main__":
    main()
