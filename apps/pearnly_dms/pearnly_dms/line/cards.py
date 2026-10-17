# -*- coding: utf-8 -*-
"""DMS LINE 对话的泰语文案与字段标签。

## 文案不进代码，进语言包

这些文案在 `translations/th.csv` 里（key = `dms.card.<常量名>`）。
**代码里一个泰文字符都不留** —— 改文案是改数据文件，不是改代码发版。

为什么卡片只有泰文：LINE 官方号服务的是泰国经销商销售，这条通道本来就只有泰文
（老产品也是）。要加语言就往 CSV 里加行，不用动这里。

`ACT_*`（动作码）**不是文案**，是 postback 的载荷，必须留在代码里且不能翻译 ——
翻译了按钮就点不动了。同理下面 `FIELD_*` 里作为"字段标识"用的值也不进包。
"""

from __future__ import annotations

from typing import Any, Dict, List
from urllib.parse import urlencode

from pearnly_dms import i18n


def _t(key: str, lang: str | None = None, **kw) -> str:
    """按键取卡片文案。默认按当前 LINE 收件人的语言（`i18n.line_lang()`，块外是泰文）。

    首参叫 `key` 不叫 `name`：回执模板本身就有 `name=` 这个占位符
    （客户姓名），参数名撞了会直接 `TypeError`。
    """
    return i18n.t(f"dms.card.{key}", lang=lang or i18n.line_lang(), **kw)


def __getattr__(name: str) -> Any:
    """让 `cards.TXT_XXX` 这种老写法继续可用（外部 28 处在用）。

    文案在包里，所以这里动态解析 —— 模块里不再有同名常量，
    Python 找不到全局名就会走到这儿（PEP 562）。
    """
    if name == "FIELD_LABELS_TH":
        return field_labels()
    if name.startswith(("TXT_", "BTN_", "MSG_")):
        return _t(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ── 文案 ─────────────────────────────────────────────────────────────────
# 文案正文在 `translations/th.csv`（key = `dms.card.*`）。下面这些"拍板"结论
# 按 key 归类留着 —— 改文案时对照着看，别把当初为什么这么写话给改没了：
#
#   TXT_NO_CREDIT      402 指路：销售员自己治不了余额，话术要把动作交给公司
#                      管理员，并给自助入口（pearnly.com/dms），不留死胡同。
#   TXT_INTRO / RESET  菜单1（只建档）**不提订车**（泰方拍板）。
#   TXT_DONE_SAVED     真写档与零写入（TXT_DONE_SAME）分开话术 —— 不许谎称。
#   TXT_MENU_*         菜单层：欢迎语 → 选建档或订车；配 BTN_* 按钮。
#   TXT_BOOKING_*      订车阶段（确认 / 取消 / 重试）。

# 动作名（flow 侧 dispatch 用同一常量，避免拼写漂移）
ACT_CREATE = "create_new"
ACT_UPDATE = "update"
ACT_KEEP = "keep"
ACT_RESET = "reset"
ACT_PICK = "pick"
ACT_EDIT = "edit"
ACT_MENU_CUSTOMER = "menu_customer"
ACT_MENU_BOOKING = "menu_booking"
ACT_MENU_QUERY = "menu_query"
# 菜单第 6 项「คู่มือ」：打开子菜单 / 播放某一条教程视频（`item=` 条目码，见 `menu/manual.py`）。
ACT_MENU_MANUAL = "menu_manual"
ACT_MANUAL_VIDEO = "manual_video"
ACT_CONTINUE_BOOKING = "continue_booking"
# 慢任务做完后「查看结果」（`line.out.show_result`）。
ACT_SHOW_RESULT = "show_result"
ACT_RETAKE = "retake"
ACT_CONFIRM_BOOKING = "confirm_booking"
ACT_CANCEL_BOOKING = "cancel_booking"
ACT_RETRY_BOOKING = "retry_booking"
# 连不上对方 DMS 时那颗「重试」（同一件事重跑一次，每次把超时拉长）。
ACT_RETRY = "retry"
# 开单回执差异卡：按确认时的值改回 DMS / 以 DMS 为准（`booking.conversation.review_fix`）。
ACT_REVIEW_UPDATE = "review_update"
ACT_REVIEW_KEEP = "review_keep"

# 差异卡上的字段名 —— 只列会出现在 diff 里的那些；标签从语言包取
# 与 `intake/customer_diff._DIFF_FIELDS` 一一对应（楼/层/房/村 2026-09 补进比对后一直没标签，
# 差异卡上显示成 building / floor / room 原名，用户 2026-09-27 截图）。
_DIFF_FIELD_NAMES = ("prefix_id", "name", "birthday_be", "phone", "house_no",
                     "building", "floor", "room", "village",
                     "moo", "soi", "road", "province_id", "district_id",
                     "subdistrict_id", "zipcode_id")
def field_labels() -> Dict[str, str]:
    """差异卡的字段名，按当前 LINE 收件人的语言现取（外部旧写法 `cards.FIELD_LABELS_TH`
    经 `__getattr__` 仍可用；名字里的 `_TH` 是历史遗留，内容已跟随收件人语言）。"""
    return {f: _t(f"diff.{f}") for f in _DIFF_FIELD_NAMES}

_MAX_CANDIDATES = 4  # 候选上限（与原候选卡一致；quick reply 最多 13 项）


# ── 构造器 ───────────────────────────────────────────────────────────────
def _data(action: str, **kw: str) -> str:
    """postback 的 data 编码：action + 可选 nonce/cid。querystring 稳定、可核对。"""
    return urlencode({"action": action, **{k: v for k, v in kw.items() if v}})


def _qr_item(label: str, data: str) -> Dict[str, Any]:
    """quick reply 项（postback，data 里带 action + nonce + field）。"""
    return {
        "type": "action",
        "action": {"type": "postback", "label": label, "data": data, "displayText": label},
    }


def _btn(label: str, data: str, style: str = "primary") -> Dict[str, Any]:
    return {
        "type": "button",
        "style": style,
        "height": "sm",
        "action": {"type": "postback", "label": label, "data": data, "displayText": label},
    }


def _edit_button(nonce: str) -> Dict[str, Any]:
    """客户档的 `[แก้ไข]`（编辑）按钮 —— **能拿到深链就用深链**。

    线上（`services/line_dms/cards.py` 的 `_edit_button`，基准 501589f0）就是这个形状：
    默认是个 postback，能解析出本站点编辑页地址时**把 action 换成 uri**，于是点一下
    直接开浏览器编辑页。拿不到地址（没配 LIFF 也没站点地址）就退回 postback，
    由 `dms_flow` 的 `ACT_EDIT` 处理器回一句话术 —— 那样至少用户知道发生了什么，
    比一个按不动的按钮强。

    注意是**客户档**编辑器（`customer=True`）：LINE 里那张卡的 [แก้ไข] 改的就是客户资料。
    订车预览卡上那个编辑按钮走 `dms_qa_cards._edit_url`（不带 `editor`，即订车模式）。
    """
    from pearnly_dms.editor import edit_link as dms_edit_link

    label = _t("BTN_EDIT")
    button = _btn(label, _data(ACT_EDIT, nonce=nonce), "secondary")
    uri = dms_edit_link.url(nonce, customer=True)
    if uri:
        button["action"] = {"type": "uri", "label": label, "uri": uri}
    return button


def _kv_row(label: str, value: str) -> Dict[str, Any]:
    return {
        "type": "box",
        "layout": "baseline",
        "spacing": "sm",
        "contents": [
            {"type": "text", "text": label, "size": "sm", "color": "#8a8a8a", "flex": 2},
            {
                "type": "text",
                "text": value or "—",
                "size": "sm",
                "color": "#111111",
                "flex": 5,
                "wrap": True,
            },
        ],
    }


def diff_rows(display_diffs: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """逐条「字段标签（粗）+ 旧 → 新（红）」行。差异卡用。"""
    return [
        {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": d.get("label", ""),
                    "size": "sm",
                    "weight": "bold",
                    "color": "#111111",
                },
                {
                    "type": "text",
                    "text": f"{d.get('old') or '—'}  →  {d.get('new') or '—'}",
                    "size": "sm",
                    "color": "#c0392b",
                    "wrap": True,
                },
            ],
        }
        for d in display_diffs
    ]


def _bubble(
    header: str, body_rows: List[Dict[str, Any]], footer_btns: List[Dict[str, Any]], alt: str
) -> Dict[str, Any]:
    return {
        "type": "flex",
        "altText": alt,
        "contents": {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {"type": "text", "text": header, "weight": "bold", "size": "md", "wrap": True},
                    {"type": "separator", "margin": "sm"},
                    *body_rows,
                ],
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": footer_btns,
            },
        },
    }


_SUMMARY_FIELD_NAMES = ("people_id", "name", "birthday_be", "address", "phone")


def _summary_rows(summary: Dict[str, str]) -> List[Dict[str, Any]]:
    """五要素复述行（新客 / 一致 / 差异三张卡共用同一预览区）。

    为什么三张卡都要复述全信息：操作员（和泰方）拍板过 —— **每张卡先看全信息**，
    只回一句"一致"会把识别或手输出错的销售堵死。
    """
    s = summary or {}
    return [_kv_row(_t(f"sum.{f}"), s.get(f, "")) for f in _SUMMARY_FIELD_NAMES]


def edit_entry_message(nonce: str, *, notice: str = "") -> Dict[str, Any]:
    """点 [แก้ไข] 之后、或保存前缺项被拦下时：**一句文案 + LINE 原生快捷按钮**「แก้ไข」，不出卡片。

    用户 2026-10-06 口径：菜单 1、2 里只有客户主档卡和订车主档卡用卡片，其余一律文案加按钮，
    文案用正式说法。按钮能拿到编辑页深链就直接打开（uri），否则退回 postback（`ACT_EDIT`）。
    """
    return _text_with_buttons(notice or _t("TXT_EDIT_ENTRY"), [_edit_button(nonce)["action"]])


def _text_with_buttons(text: str, actions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """文案 + quick reply（LINE 原生按键，标签不超过 20 个字，最多 13 个）。"""
    return {"type": "text", "text": text,
            "quickReply": {"items": [{"type": "action", "action": action} for action in actions]}}


def new_customer_card(summary: Dict[str, str], nonce: str) -> Dict[str, Any]:
    """场景①新建：五要素复述 + [บันทึกลูกค้าใหม่][แก้ไข]。

    只有两个按钮是有意的（拍板瘦身）：重拍 = 直接再发照片，重来 = 打"เมนู"，
    不占按钮位。
    """
    footer = [
        _btn(_t("BTN_SAVE_NEW"), _data(ACT_CREATE, nonce=nonce), "primary"),
        _edit_button(nonce),
    ]
    return _bubble(
        _t("TXT_NEW_CARD_HEAD"),
        _summary_rows(summary),
        footer,
        _t("TXT_NEW_CARD_ALT"),
    )


def same_customer_card(summary: Dict[str, str], nonce: str) -> Dict[str, Any]:
    """场景②已有客户 · 资料一致（不分模式）：预览复述 + [ใช้ข้อมูลเดิม][แก้ไข]。

    只回一句「ตรงกัน」会把识别/手输出错的销售堵死 —— 预览和修正入口必须在。
    """
    footer = [
        _btn(_t("BTN_KEEP"), _data(ACT_KEEP, nonce=nonce), "primary"),
        _edit_button(nonce),
    ]
    return _bubble(_t("TXT_SAME"), _summary_rows(summary), footer, _t("TXT_SAME_ALT"))


def booking_customer_card(summary: Dict[str, str], nonce: str) -> Dict[str, Any]:
    """菜单2 认出"资料一致"的客户时，确认后进入订车逐问。"""
    footer = [
        _btn(_t("BTN_CONTINUE_BOOKING"), _data(ACT_KEEP, nonce=nonce), "primary"),
        _edit_button(nonce),
    ]
    return _bubble(
        _t("TXT_BOOKING_CUSTOMER"),
        _summary_rows(summary),
        footer,
        _t("TXT_BOOKING_CUSTOMER"),
    )


def diff_card(
    display_diffs: List[Dict[str, str]],
    nonce: str,
    *,
    primary: str = "none",
    summary: Dict[str, str],
) -> Dict[str, Any]:
    """场景③已有客户 + 有差异：新资料完整预览 + 逐条「字段：旧 → 新」放同一张卡
    （2026-07-19 拍板），按钮 [主键][ใช้ข้อมูลเดิม][แก้ไข]。

    **主按钮由调用方按角色解出，渲染层不藏优先级**：

    · `primary='update'`（有改写权）→ [อัปเดตข้อมูล]
    · `primary='none'`（无改写权且没配 admin）→ 只给一句提示，**不给主按钮**
      （让用户点了才发现"要管理员"是最差体验）
    """
    rows: List[Dict[str, Any]] = _summary_rows(summary)
    rows.append({"type": "separator", "margin": "sm"})
    rows.append({"type": "text", "text": _t("TXT_DIFF_HEADER"), "size": "xs", "color": "#8a8a8a"})
    rows += diff_rows(display_diffs)
    footer: List[Dict[str, Any]] = []
    if primary == "update":
        footer.append(_btn(_t("BTN_UPDATE"), _data(ACT_UPDATE, nonce=nonce), "primary"))
    else:
        rows.append({"type": "separator", "margin": "sm"})
        rows.append(
            {
                "type": "text",
                "text": _t("TXT_ADMIN_NEEDED"),
                "size": "xs",
                "color": "#8a8a8a",
                "wrap": True,
            }
        )
    footer.append(_btn(_t("BTN_KEEP"), _data(ACT_KEEP, nonce=nonce), "secondary"))
    # 用 `_edit_button`（能拿深链就是 uri 动作）—— 老基准这张卡也是这么写的。
    # 早先这里写成裸 postback，于是**只有差异卡**点 [แก้ไข] 会多弹一张"请再点一次"的卡，
    # 其它卡（新客户/资料一致/订车预览）都是一次点击直开编辑页（2026-09-21 用户报障）。
    footer.append(_edit_button(nonce))
    return _bubble(_t("TXT_DIFF_HEAD"), rows, footer, _t("TXT_DIFF_ALT"))


def retry_message(text: str) -> Dict[str, Any]:
    """连不上对方 DMS 时那条消息：**一句文案 + LINE 原生按键**（quick reply），不出卡片。

    用户 2026-09-23 口径："不要加卡片，就文案和 line 官方原生按钮触发"。

    按键是 postback、且**不消费 nonce**：重试要重跑的是同一件事，会话里记着是哪件
    （`intake/flow.py` 的 `dms_retry`），所以载荷只需要动作码。
    第三次仍失败时**不再给按键**，只回同一句话（见 `intake/flow.py: _dms_failed`）。
    """
    return {
        "type": "text",
        "text": text,
        "quickReply": {"items": [_qr_item(_t("BTN_RETRY"), _data(ACT_RETRY))]},
    }


def candidates_message(candidates: List[Dict[str, str]], nonce: str) -> Dict[str, Any]:
    """场景④相似：文案逐行列出候选（码 · 姓名 · 证号尾 4），快捷按钮按序号认领，[ลูกค้าใหม่] 兜底。

    按钮标签只放序号与客户代码（LINE 限 20 个字），完整信息在文案里。人工认领。
    """
    lines, actions = [_t("TXT_CANDIDATES_HEAD")], []
    for number, c in enumerate(candidates[:_MAX_CANDIDATES], 1):
        cid = str(c.get("customer_id") or "")
        tail = (str(c.get("people_id") or ""))[-4:]
        line = f"{number}. {c.get('cuscode') or '—'} · {c.get('name') or ''}"
        if tail:
            line += f" · x{tail}"
        lines.append(line)
        label = f"{number}. {c.get('cuscode') or ''}".strip()[:20]
        actions.append({"type": "postback", "label": label,
                        "data": _data(ACT_PICK, nonce=nonce, cid=cid), "displayText": line[:300]})
    new = _t("BTN_NEW_CUSTOMER")
    actions.append({"type": "postback", "label": new, "data": _data(ACT_CREATE, nonce=nonce), "displayText": new})
    return _text_with_buttons("\n".join(lines), actions)


def receipt_text(customer_code: str, name: str, mode: str) -> str:
    """写档成功回执：DMS 客户编号 + 姓名 + **实际做了什么**（诚实告知，不谎称新建）。

    `customer_code` 是 DMS 界面上的客户编号（`cuscode`），**不是**内部行号 `customer_id`
    （2026-10 录制：回执显示过内部行号 132，DMS 界面上没有这个号）。
    """
    action = _t("TXT_ACTION_CREATE") if mode == "create" else _t("TXT_ACTION_UPDATE")
    return _t(
        "TXT_RECEIPT_SAVE",
        action=action,
        customer_code=customer_code or "—",
        name=name or "—",
    )


def booking_receipt_text(
    booking_no: str, car: str, delivery_date_be: str, advisor_name: str
) -> str:
    """订车成功回执：BK 单号 + 车型 + 交车日 + 销售顾问。

    顾问那一行 = **提成归属的当场回执**，销售不用登 DMS 就能核对
    （名字缺就整行不出，不留空槽）。
    """
    text = _t(
        "TXT_RECEIPT_BOOKING",
        booking_no=booking_no or "—",
        car=car or "—",
        delivery_date_be=delivery_date_be or "—",
    )
    return f"{text}\n{_t('TXT_RECEIPT_ADVISOR', name=advisor_name)}" if advisor_name else text


def label(key: str) -> str:
    """汇总卡 / 差异卡上一项的标签（键形如 `sum.phone`、`qa.LBL_PLACE`、`diff.soi`）。"""
    return _t(key)


def booking_receipt_message(
    booking_no: str, car: str, delivery_date_be: str, advisor_name: str,
    *, review: Dict[str, Any] | None = None, attach_ok: bool = True,
    record: str = "", nonce: str = "",
) -> Dict[str, Any]:
    """订车成功回执（一条消息）：按开单后的逐项复核（`booking.booking_review`）定形状。

    · 没有复核结果（拿不到回读表单）→ 原回执文字，不加任何一行。
    · 全部一致且附件齐 → 原回执文字 + 「ผลการตรวจสอบกับ DMS: ถูกต้อง」一行。
      附件没挂齐时不加这一行（附件交给 `delivery` 后台补，补不上另有提示），不说"全部一致"。
    · 有不一致 → 换成差异卡（`booking_review_card`）。
    """
    text = booking_receipt_text(booking_no, car, delivery_date_be, advisor_name)
    diffs = (review or {}).get("diffs") or []
    if review is None or (not diffs and not attach_ok):
        return {"type": "text", "text": text}
    if not diffs:
        return {"type": "text", "text": f"{text}\n{_t('TXT_REVIEW_OK')}"}
    return booking_review_card(booking_no, diffs, record=record, nonce=nonce)


def booking_review_card(booking_no: str, diffs: List[Dict[str, Any]], *, record: str = "",
                        nonce: str = "") -> Dict[str, Any]:
    """差异卡：单号 + 逐条「确认时的值 → DMS 里的值」，按钮 [อัปเดตข้อมูล][ใช้ข้อมูลเดิม][แก้ไข]
    （与客户档差异卡同一组按钮，用户 2026-10-16）。没有卡片登记（`record`/`nonce`）时不出按钮，
    改成一句「请在 DMS 中修改」。
    """
    head = _t("TXT_REVIEW_DIFF_HEAD")
    rows: List[Dict[str, Any]] = [
        {"type": "text", "text": _t("TXT_REVIEW_BOOKING_NO", booking_no=booking_no or "—"),
         "size": "sm", "wrap": True},
        {"type": "separator", "margin": "sm"},
        {"type": "text", "text": _t("TXT_DIFF_HEADER"), "size": "xs", "color": "#8a8a8a"},
        *diff_rows([{"label": label(d.get("item") or ""), "old": d.get("sent"), "new": d.get("dms")}
                    for d in diffs]),
    ]
    if not (record and nonce):
        rows += [{"type": "separator", "margin": "sm"},
                 {"type": "text", "text": _t("TXT_REVIEW_FIX"), "size": "sm", "wrap": True}]
        return {"type": "flex", "altText": head, "contents": {"type": "bubble", "body": {
            "type": "box", "layout": "vertical", "spacing": "sm",
            "contents": [{"type": "text", "text": head, "weight": "bold", "size": "md", "wrap": True},
                         {"type": "separator", "margin": "sm"}, *rows]}}}
    from pearnly_dms.editor import edit_link as dms_edit_link

    footer = [
        _btn(_t("BTN_UPDATE"), _data(ACT_REVIEW_UPDATE, r=record, n=nonce), "primary"),
        _btn(_t("BTN_KEEP"), _data(ACT_REVIEW_KEEP, r=record, n=nonce), "secondary"),
    ]
    uri = dms_edit_link.url(f"{record}.{nonce}", review=True)
    if uri:
        footer.append({"type": "button", "style": "secondary", "height": "sm",
                       "action": {"type": "uri", "label": _t("BTN_EDIT"), "uri": uri}})
    return _bubble(head, rows, footer, head)


def review_updated_text(booking_no: str) -> str:
    """改单成功、逐项核对一致：已更新 + 单号 + 核对结果那一行。"""
    return "\n".join([_t("TXT_REVIEW_UPDATED"), _t("TXT_REVIEW_BOOKING_NO", booking_no=booking_no or "—"),
                      _t("TXT_REVIEW_OK")])
