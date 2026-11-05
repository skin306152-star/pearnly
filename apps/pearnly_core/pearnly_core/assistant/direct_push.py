"""对话直写工具的共用部分（多产品共用：Cowork 与 ERP 版 CC 同用；评估 2026-11-02 §13 第 9 步）。

对话里直写（看不到界面、后台写进账本）的工具由各产品自己声明（ADR-009）：Cowork `pearnly_cowork.assistant.tools`，ERP
`pearnly_erp_portal.express.cc_tools`；读写的单据、账本、权限都是各产品自己的。两边真正相同的放在这里、引用同一份：

- **给模型的时间**：工具结果里的时间点一律是站点时区的 ``YYYY-MM-DD HH:MM``（`local_time` / `local_times` / `dumps`；
  2026-10-13 录屏：推送时间按 UTC 原样交出去，曼谷 15:40 被说成 08:40）。
- **结果的状态码与字段名**（`ENTERED` … `NOT_SAVED`）与几段参数形状：按公司的范围（`scope_props`）、翻页（`page_props`）、
  一次推几张（`documents_prop`）、推送结果的筛选（`RESULT_STATES`）。
- **「结果不明」怎么核对**：推送结果里怎么说（`unconfirmed_rule`）、核对工具的说明与参数（`resolve_description` /
  `RESOLVE_SCHEMA`）、核对的结果（`resolved` / `not_saved` / `not_found`）。
- **说话规矩里与产品无关的几段**（`GUIDANCE_TIMES` 等，`cc_desktop` 是 Windows 端的正式名字）。
- **推送后能不能在这次工具里等结论**（`may_wait`：共用层 `toolrun.may_wait` 判定后放在对话信息里）。

只有常量与纯函数；用 Frappe 只取站点时区与这次对话的信息，不读库、不写库。
"""
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import frappe

# ---------------- 上限与等结论 ----------------

PUSH_MAX = 20  # 一次最多推几张
LIST_MAX = 50  # 列单一页最多几张
# 发出去以后在工具里最多等这么久的写账结论、每隔几秒读一次（只在推送线程里等，见 `may_wait`）
PUSH_WAIT_SECONDS = 25
PUSH_WAIT_STEP = 2


def may_wait() -> bool:
    """执行这次工具的地方能不能等（共用层 `toolrun.may_wait` 判定后放在对话信息里）；拿不到就不等。"""
    return bool((getattr(frappe.local, "pearnly_assistant", None) or {}).get("may_wait"))


# ---------------- 结果的状态码 ----------------

# 推一张的结论（两产品同一组词，模型按说明认）
ENTERED = "entered"  # 写进账本了（带 docnum：它在账本里的单号）
QUEUED = "queued"  # 发出去了、账本还没回结论
FAILED = "failed"  # 没写成（带 reason）
UNCONFIRMED = "unconfirmed"  # 结果不明：账本里可能有、可能没有（带 reason；不再推，核对后记下）
NEEDS_CHOICE = "needs_choice"  # 要人选一次（先看要选的项、记下，再推）
WAITING_FOR_CC = "waiting_for_cc"  # 电脑还在读这份账套的资料：稍后再推
ALREADY_SUCCESS = "already_success"  # 早已入账：不再推
ALREADY_PENDING = "already_pending"  # 已在路上：不再推
NOT_FOUND = "not_found"  # 没有这张，或本人看不到
# 记下（选择、核对结论）的结论
SAVED = "saved"
NOT_SAVED = "not_saved"
# 推送结果工具按这三种筛：在路上、入账、没成
RESULT_STATES = ("pending", "success", "failed")


def not_found(document) -> dict:
    return {"document": document, "status": NOT_FOUND}


def not_saved(document, reason) -> dict:
    return {"document": document, "status": NOT_SAVED, "reason": reason}


def filled(values: dict) -> dict:
    """空值不给：模型会把「币种：未识别到」之类的空项当成要说的事。"""
    return {key: value for key, value in values.items() if value not in (None, "", [], {})}


# ---------------- 结果不明的核对 ----------------

NO_PUSH_TO_CONFIRM = "This document has no push to confirm."
RESOLVE_SCHEMA = {"type": "object", "properties": {
    "document": {"type": "string"}, "entered": {"type": "boolean"}, "docnum": {"type": "string"}},
    "required": ["document", "entered"]}


def resolve_description(programs: str, then: str = "so it can be pushed again.") -> str:
    """核对工具给模型的说明：``programs`` 是账本程序的叫法（"Express or MR.ERP" / "Express"），``then`` 说记下之后怎样。"""
    return (f"When a push result is unknown ({programs} may or may not have it), record what the user "
            "found after checking there: entered true (with its document number there if they have it) or "
            f"false, {then} Only with the user's own answer; never guess.")


def unconfirmed_rule(programs: str, resolve_tool: str) -> str:
    """推送结果里 ``unconfirmed`` 怎么说（推送工具的说明里那一项）。"""
    return (f"unconfirmed with a reason ({programs} may or may not have it: the user checks "
            f"there, then record it with {resolve_tool}; do not push it again)")


def resolved(document, push: dict) -> dict:
    """核对记下了：``push`` 是这张单此刻的推送结论（状态、单号、原因，空的不给）。"""
    return {"document": document, "status": SAVED, "push": filled(push)}


# ---------------- 参数形状 ----------------


def scope_props(companies_tool: str, covers: str, covers_short: str) -> dict:
    """按公司可筛的读工具共用的两个参数：``company``（缺省 = 本对话挂的公司）与 ``all_companies``。``covers`` 说这家公司
    的哪些（"account sets (all its years and branches)"），``covers_short`` 是简称（"account sets"）。"""
    return {
        "company": {"type": "string", "description": f"Company id from {companies_tool}: only that company's {covers}. "
                                                     "Leave out for the company this conversation is filed under."},
        "all_companies": {"type": "boolean", "description": "true only when the user asks about all companies or other "
                                                            "companies (" + "ทุกเจ้า, เจ้าอื่น" + "). Without it, a "
                                                            "conversation filed under a company covers only that "
                                                            f"company's {covers_short}."},
    }


def page_props(maximum: int = LIST_MAX) -> dict:
    """翻页：``offset`` 从第几张起（上一页结果的 next_offset）、``limit`` 这页几张。"""
    return {"offset": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": maximum}}


def documents_prop(maximum: int = PUSH_MAX) -> dict:
    return {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": maximum}


# ---------------- 给模型的时间 ----------------

# 库里两种存法：Frappe 的日期时间（creation、modified 与 now_datetime() 写的）是站点时区的裸时间，直接换格式；推送账本、写账回执
# 等存的是 UTC（裸时间或带 Z 的字符串），由各工具在 `dumps(utc=…)` 里点名，换算一次，不会两次偏移。
LOCAL_TIME = "%Y-%m-%d %H:%M"


def local_time(value, utc=False):
    """一个时间点 → 站点时区的 YYYY-MM-DD HH:MM。带时区的按自己的时区换算；裸时间在 ``utc`` 时当 UTC，否则本来就是站点时区。
    字符串按 ISO 写法读（T 或空格、可带秒、微秒、Z 或 ±hh:mm）；只有日期的、读不出的原样返回。"""
    if isinstance(value, str):
        text = value.strip()
        if len(text) <= 10:
            return value
        try:
            value = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
        except ValueError:
            return value
    if not isinstance(value, datetime):
        return value
    if value.tzinfo is None and utc:
        value = value.replace(tzinfo=timezone.utc)
    if value.tzinfo is not None:
        value = value.astimezone(ZoneInfo(frappe.utils.get_system_timezone())).replace(tzinfo=None)
    return value.strftime(LOCAL_TIME)


def local_times(value, utc=(), local=(), key=None):
    """整份结果里的时间点都过 `local_time`：datetime 一律换（键在 ``utc`` 里的当 UTC）；字符串只换键在 ``utc`` 或
    ``local`` 里的（别的字符串可能要原样交回，如看板卡片当版本号用的 modified）。"""
    if isinstance(value, dict):
        return {field: local_times(item, utc, local, field) for field, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [local_times(item, utc, local, key) for item in value]
    if isinstance(value, datetime) or (isinstance(value, str) and (key in utc or key in local)):
        return local_time(value, key in utc)
    return value


def dumps(value, utc=(), local=()) -> str:
    """交给模型的 JSON：时间点换成当地时间（见上）；``utc`` 点名存的是 UTC 的字段，``local`` 点名已是站点时区的时间字符串。"""
    return json.dumps(local_times(value, tuple(utc), tuple(local)), ensure_ascii=False, default=str)


# ---------------- 说话规矩里与产品无关的几段 ----------------

# 2026-10-13 录屏：推送时间按 UTC 交出去被说成当地时间；工具一律给当地时间（`local_time`），说清楚别再换算
GUIDANCE_TIMES = ("Times in tool results are already the user's local time, on the same clock as the current time you "
                  "are given; say them as they are.")
# 2026-10-13 录屏：对话刚谈完 9 月，会计问「这个月还有哪些票卡着」，CC 按 10 月查、答「没有」。会计说的这个月是在做的那期
GUIDANCE_THIS_MONTH = ("'This month' in accounting work is the month being filed, not always the calendar month: a month "
                       "already being discussed in this conversation is the one meant; with none, early in a month it is "
                       "usually last month's filing. When unsure, confirm the month in one short question before looking "
                       "anything up.")
# 测试窗口第 3 轮：CC 把金额说成「元」。金额都是泰铢
GUIDANCE_BAHT = ("Amounts in tool results are Thai baht. Say the currency in the user's language (" + "บาท" + " in Thai, "
                 + "泰铢" + " in Chinese, THB or baht in English); never call it yuan or " + "元" + ".")
# 2026-10-20 教程录制：表格里多出残行、单号与现赊照先前的说法写错。表格、清单里的每一格都从这一轮工具结果里这张单的那一项抄
GUIDANCE_TABLE_ROWS = ("When you list documents in a table or a list, build each row from that document's own entry in the "
                       "latest tool result: its number in Express (docnum), cash or credit, amounts and dates exactly as "
                       "given there, never from your earlier messages or from what you expected. Write one complete row "
                       "per document, in the order you choose before you start, and never leave a row unfinished or cut "
                       "off with '...'. When an entry differs from what you said earlier, say so plainly.")

# 2026-11-03「CC 通用疑难处理」（用户：不按情况加规则，CC 能查、能问、能答、能做）：只写原则，Cowork 与 ERP 共用。
# 代码只守管钱的底线（护栏拦下的单进待处理、不写账），找根因、想办法补交给 CC。``pending_tool``：本产品的待处理工具名
def guidance_troubleshooting(pending_tool: str) -> str:
    return ("When a document is stuck, or the user asks what needs attention, find the root cause before anything else: "
            f"look at the pending items ({pending_tool}), the document's checks and push reason, and its original pages, "
            "then say the cause in a sentence or two. If the original or the user's words settle it, fix it and say what "
            "the fix is based on; if not, ask the user for the missing page or file, or for their decision. Never guess "
            "which document a file belongs to (ask when unsure), never make up a number, and say how any number you "
            "worked out was calculated. Every change to an amount, tax ID, invoice number, date or items carries its "
            "basis. Once the user decides, carry it through: fix, push again and report the result. Pushing does not "
            "check for duplicates: you may point out two documents that look the same, but never hold back or delete "
            "one unless the user asks. In a conversation filed under a company, on the user's first message check its "
            f"pending items once with {pending_tool} and, if there are any, mention them briefly.")


def cc_desktop(does: str) -> str:
    """Windows 端的正式名字，给模型说一次（四种语言；中文、泰文两段登记在 scripts/i18n/keep-cjk.tsv）：模型照用户的语言说
    这个名字，不自己意译（第 7 批 B8）。``does`` 说它在这个产品里做什么（"reads their folders and writes into Express"）。"""
    return (f"The Windows program on the user's computer that {does} is "
            "CC Desktop. Call it by its name in the user's language: CC Desktop in English, "
            + "CC 桌面版" + " in Chinese, " + "CC เดสก์ท็อป" + " in Thai and CC デスクトップ in Japanese; "
            "never by a generic description.")
