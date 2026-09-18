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


def _t(key: str, lang: str = "th", **kw) -> str:
    """按键取卡片文案。默认泰文（LINE 通道的语言）。

    首参叫 `key` 不叫 `name`：回执模板本身就有 `name=` 这个占位符
    （客户姓名），参数名撞了会直接 `TypeError`。
    """
    return i18n.t(f"dms.card.{key}", lang=lang, **kw)


def __getattr__(name: str) -> Any:
    """让 `cards.TXT_XXX` 这种老写法继续可用（外部 28 处在用）。

    文案在包里，所以这里动态解析 —— 模块里不再有同名常量，
    Python 找不到全局名就会走到这儿（PEP 562）。
    """
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
ACT_CONTINUE_BOOKING = "continue_booking"
ACT_RETAKE = "retake"
ACT_CONFIRM_BOOKING = "confirm_booking"
ACT_CANCEL_BOOKING = "cancel_booking"
ACT_RETRY_BOOKING = "retry_booking"
ACT_SUBMIT_APPROVAL = "approval_submit"
ACT_APPROVAL_TARGET = "approval_target"
ACT_APPROVAL_APPROVE = "approval_approve"
ACT_APPROVAL_REJECT = "approval_reject"
ACT_APPROVAL_RETARGET = "approval_retarget"

# 差异卡上的字段名 —— 只列会出现在 diff 里的那些；标签从语言包取
_DIFF_FIELD_NAMES = ("prefix_id", "name", "birthday_be", "phone", "house_no",
                     "moo", "soi", "road", "province_id", "district_id",
                     "subdistrict_id", "zipcode_id")
FIELD_LABELS_TH: Dict[str, str] = {f: _t(f"diff.{f}") for f in _DIFF_FIELD_NAMES}

_MAX_CANDIDATES = 4  # 候选卡按钮上限（LINE bubble 体积限制）


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
    from pearnly_dms import dms_edit_link

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
    """逐条「字段标签（粗）+ 旧 → 新（红）」行。差异卡与审批申请卡共用同一渲染。"""
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


def edit_entry_card(nonce: str) -> Dict[str, Any]:
    """点 [แก้ไข] 之后回的那张卡：就一个"打开编辑页"的按钮。

    线上 `customer_edit_entry._open` 就是这个形状（`cards._bubble(BTN_EDIT, [],
    [cards._edit_button(nonce)], BTN_EDIT)`）—— 点编辑不再展开字段菜单，
    而是给一个能开浏览器编辑页的入口。正文故意是空的：多一句话就多一处要翻译、
    要维护的文案，而这个按钮本身已经把事说清楚了。
    """
    label = _t("BTN_EDIT")
    return _bubble(label, [], [_edit_button(nonce)], label)


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

    · `primary='approval'`（信用授权类 · 策略要审）→ [ส่งขออนุมัติ]
    · `primary='update'`（有改写权）→ [อัปเดตข้อมูล]
    · `primary='none'`（无改写权且没配 admin）→ 只给一句提示，**不给主按钮**
      （让用户点了才发现"要管理员"是最差体验）
    """
    rows: List[Dict[str, Any]] = _summary_rows(summary)
    rows.append({"type": "separator", "margin": "sm"})
    rows.append({"type": "text", "text": _t("TXT_DIFF_HEADER"), "size": "xs", "color": "#8a8a8a"})
    rows += diff_rows(display_diffs)
    footer: List[Dict[str, Any]] = []
    if primary == "approval":
        footer.append(_btn(_t("BTN_SUBMIT_APPROVAL"), _data(ACT_SUBMIT_APPROVAL, nonce=nonce), "primary"))
    elif primary == "update":
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
    footer.append(_btn(_t("BTN_EDIT"), _data(ACT_EDIT, nonce=nonce), "secondary"))
    return _bubble(_t("TXT_DIFF_HEAD"), rows, footer, _t("TXT_DIFF_ALT"))


def candidates_card(candidates: List[Dict[str, str]], nonce: str) -> Dict[str, Any]:
    """场景④相似：候选按钮（码 · 姓名 · 证号尾 4）+ [ลูกค้าใหม่] 兜底。人工认领。"""
    rows: List[Dict[str, Any]] = []
    for c in candidates[:_MAX_CANDIDATES]:
        cid = str(c.get("customer_id") or "")
        tail = (str(c.get("people_id") or ""))[-4:]
        label = f"{c.get('cuscode') or '—'} · {c.get('name') or ''}"
        if tail:
            label += f" · x{tail}"
        rows.append(_btn(label[:40], _data(ACT_PICK, nonce=nonce, cid=cid), "link"))
    footer = [_btn(_t("BTN_NEW_CUSTOMER"), _data(ACT_CREATE, nonce=nonce), "primary")]
    return _bubble(
        _t("TXT_CANDIDATES_HEAD"), rows, footer, _t("TXT_CANDIDATES_ALT")
    )


def receipt_text(customer_id: str, name: str, mode: str) -> str:
    """写档成功回执：客户码 + 姓名 + **实际做了什么**（诚实告知，不谎称新建）。"""
    action = _t("TXT_ACTION_CREATE") if mode == "create" else _t("TXT_ACTION_UPDATE")
    return _t(
        "TXT_RECEIPT_SAVE",
        action=action,
        customer_id=customer_id or "—",
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
