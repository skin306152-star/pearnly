"""Cowork 的智能体工具（ADR-019）：对话里发来的票据交给录入工作台识别、复核与修改、小助手状态与账套、
单据推送到 Express、文件夹定时任务、推送结果、报税草稿（ภ.พ.30、ภ.ง.ด.3 / 53）。

- 工具以当前登录用户身份在服务端执行，只调用各域现有的公开服务（与网页同一入口），权限、作用域与连接的允许名单
  都由那些服务自己校验，这里不另开口子、不直接写表。
- 推送与网页「提交」同一顺序（同 `folder_intake.push.push_one`）：先看还缺哪项要人选 → 准备 → Express 确认发送；
  每张单据各自提交，一张失败不连累前面的；同一张单据同一版本用固定请求号，重复调用不会推两次。
- 回给模型的是简短 JSON（不带原件、不带凭据）。
"""
import hashlib
import json
import re
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import frappe
from frappe import _

from pearnly_cowork.access import scope
from pearnly_cowork.connections import api as connections_api
from pearnly_cowork.connections import service as connections
from pearnly_cowork.folder_intake import destinations, rules
from pearnly_cowork.folder_intake import contracts as folder_contracts
from pearnly_cowork.folder_intake import readers as folder_readers
from pearnly_cowork.history import push_state, storage
from pearnly_cowork.push.application import api as application_api
from pearnly_cowork.push.application import service as application
from pearnly_cowork.tax import wht as tax_contracts

HISTORY = "Cowork OCR History"
CONNECTION = "Cowork ERP Connection"
PUSH_MAX = 20
LIST_MAX = 50
# 按方向筛、算合计时要看每张的主页面（方向、税前额、税额只在识别结果里）：在库里按页取、取一页丢一页的原文，
# 最多看最新的这么多张；再多就说「只看了最新的」，请用日期缩小。
LIST_SCAN_MAX = 2000
LIST_SCAN_PAGE = 200
# 不给 staged（DocType 的「草稿」）与 quality_status（识别完整度）：录入没有「确认」这一步（2026-09-26），草稿标记只有不用的
# `history.api.complete` 会清，推送成功的票也一直是 1；识别完整度的 pending 是「识别不全」，不是推送在途。网页列表两样都不显示，
# 交给模型只会被读成「还在等」（2026-10-13 录屏：已推送成功的 ABB6909-00007 被当成卡住的票）。推送状态看 last_push_status。
DOC_FIELDS = ["name", "invoice_no", "invoice_date", "seller_name", "seller_tax", "buyer_name", "buyer_tax",
              "total_amount", "posting_kind", "intake_target", "push_connection", "push_account_set",
              "last_push_status", "last_push_reason", "last_pushed_at", "filename", "creation"]
PAGE_SHOWN = ("document_type", "direction", "subtotal", "vat")
SUMMED = ("subtotal", "vat", "total_amount")
# 「等着录入」（push_state waiting）：还没进 ERP、也不在路上 —— 没推过，或推送失败 / 停下等人选。推送成功（success）与
# 在途（pending）的不算；同网页状态列、文件夹任务结果（`folder_intake.contracts.ticket_state`）同一口径。
PUSHED_OR_ON_ITS_WAY = ["pending", "success"]

# ---------------- 给模型的时间 ----------------

# 工具结果里的时间点一律是用户当地时间（站点时区，同每次发话附给模型的「现在」`now_note`），写成 YYYY-MM-DD HH:MM
# （同产品界面 `p.datetime_text`）；只有日期的照旧。2026-10-13 录屏：推送时间存的是 UTC（字段名就叫 Last Pushed At (UTC)），
# 原样交出去，CC 把曼谷 15:40 推的说成「今天 08:40 推送成功」。
# 库里两种存法：Frappe 的日期时间（creation、modified 与 now_datetime() 写的）是站点时区的裸时间，`_json` 直接换格式；
# 推送账本、盘点、看板归档、MR.ERP 读取时刻、回读时刻等存的是 UTC（裸时间或带 Z 的字符串），由各工具在 `_json(utc=…)`
# 里点名，换算一次，不会两次偏移。
LOCAL_TIME = "%Y-%m-%d %H:%M"


def _local_time(value, utc=False):
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


def _local_times(value, utc=(), local=(), key=None):
    """整份结果里的时间点都过 `_local_time`：datetime 一律换（键在 ``utc`` 里的当 UTC）；字符串只换键在 ``utc`` 或
    ``local`` 里的（别的字符串可能要原样交回，如看板卡片当版本号用的 modified）。"""
    if isinstance(value, dict):
        return {field: _local_times(item, utc, local, field) for field, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_local_times(item, utc, local, key) for item in value]
    if isinstance(value, datetime) or (isinstance(value, str) and (key in utc or key in local)):
        return _local_time(value, key in utc)
    return value


def _json(value, utc=(), local=()) -> str:
    """交给模型的 JSON：时间点换成当地时间（见上）；``utc`` 点名存的是 UTC 的字段，``local`` 点名已是站点时区的时间字符串。"""
    return json.dumps(_local_times(value, tuple(utc), tuple(local)), ensure_ascii=False, default=str)


# ---------------- 账套给人看的名字 ----------------

# 已入账的单 CC 改不了时怎么说（第 5 轮测试：让 CC 把已入账的 SD-6909-1010 改科目，它答改不了，又给了「自己去 Express
# 改 HP690920-001，我再回读」的选项）。用户在 F19 验收时定的口径：不叫人去 Express（或 MR.ERP、待处理页、复核页）改已入账
# 的单；只说明改不到，给这张的单号、泰铢金额、当地时间；回读照常可以提。GUIDANCE、WRITTEN_NOTE、改单 / 推送 / 回读的说明与
# 结果提示共用这一段，免得几处说法走样。
# 第 6 轮测试：CC 说了改不了、给了单号泰铢时间，最后一句仍是「ถ้าต้องการให้ใบนี้ลงบัญชี 53-05-28-00 ต้องแก้ใน Express
# โดยตรง」（要记到那个科目就得在 Express 里直接改）——条件句、收尾句同样不许说；可以提议把以后的同类单记成规则（要用户允许）。
# 三种语言的反例只是给模型看的说明（不做输出过滤）。
# 存公司规则时规则的对象（2026-10-16 测试：SD-6909-1010 的卖方是สะอาดดี，CC 提议存规则时却说成说明里另一条规则的ซ่อมไว）：
# 对象就是正说着的那张单自己的对方，按那张单上的名字说；document 只传那一张，expect_party 写出说给用户的对方（服务端核对）。
RULE_SUBJECT_NOTE = ("Such a rule is always for that document's own party: its seller (its buyer for a sales "
                     "customer), named exactly as on that document; never name the party of another document or of a "
                     "rule in <client_choices>. To save it, pass that same document as document and that party's tax "
                     "ID (or its name when it has none) as expect_party.")
ENTERED_NOTE = ("CC cannot change a document that is already entered into Express or MR.ERP (its account, cash or "
                "credit, amounts or anything else). When the user asks to change one, say plainly that it cannot be "
                "changed from here and give that document's number there (docnum), amounts in baht and times in local "
                "time. Do not suggest, as an option or a next step, that the user changes it in Express or MR.ERP, on "
                "the pending page or on the review page, nor offer to check it after they have; just say it cannot be "
                "changed. Never say either that it needs to be, has to be or can be changed in Express or MR.ERP or "
                "anywhere else, not even as a condition or a closing sentence ('if you want it under account X, it "
                "has to be changed in Express directly' is just as wrong), and never point to any place, program, page "
                "or person to change it. That holds in every language: nothing like '" + "ต้องแก้ใน Express โดยตรง"
                + "' or '" + "ไปแก้ใน Express" + "' in Thai, nothing like '" + "需要在 Express 里直接改" + "' or '"
                + "去 Express 改" + "' in Chinese. What you may offer instead is to save a rule so that later "
                "documents of the same kind go the way the user wants (cowork_company_choice_save for the whole "
                "company; the user allows it first). " + RULE_SUBJECT_NOTE + " Offering a readback of that month "
                "(cowork_express_readback) to show what Express holds is fine.")
# 改单、选项工具碰到已入账的单时随结果带上的提示（同一口径的短句）
ENTERED_RESULT = ("This document is already entered into Express or MR.ERP and stays as entered there: CC cannot "
                  "change it, and choices saved now apply only to later documents. Tell the user it cannot be "
                  "changed, with its docnum, amounts in baht and times in local time; do not suggest changing it in "
                  "Express or MR.ERP, on the pending page or on the review page. Never say it needs to be or can be "
                  "changed in Express, MR.ERP or anywhere else, not even as a condition or a closing sentence, in any "
                  "language. You may offer to save a rule for later documents of the same kind "
                  "(cowork_company_choice_save; the user allows it first). " + RULE_SUBJECT_NOTE)


def _entered_note(status) -> dict:
    """已入账（推送成功）的单在改单 / 选项工具的结果里带上 `entered_note`；别的状态不带。"""
    return {"entered_note": ENTERED_RESULT} if status == "success" else {}


# Windows 端的正式名字，给模型说一次（四种语言；中文、泰文两段登记在 scripts/i18n/keep-cjk.tsv）：模型照用户的语言说
# 这个名字，不自己意译。放在每轮都带的 GUIDANCE 里（第 7 批 B8，2026-10-18 lab：只写在 cowork_companion_status 的说明里时，
# 排队回复照抄了结果提示里的英文「CC Desktop」），不再放在 cowork_companion_status 的说明里。
CC_DESKTOP = ("The Windows program on the user's computer that reads their folders and writes into Express is "
              "CC Desktop. Call it by its name in the user's language: CC Desktop in English, "
              + "CC 桌面版" + " in Chinese, " + "CC เดสก์ท็อป" + " in Thai and CC デスクトップ in Japanese; "
              "never by a generic description.")


# 给模型的「怎么说话」：Cowork 自己的几条，接在共用说明后面（hooks `pearnly_assistant_guidance`）。
# 2026-10-13 录屏：CC 对会计说账套「demo02」（Express 数据文件夹名）；会计认公司，不认文件夹代码。
GUIDANCE = [
    "When you talk to the user, name an account set by its company name (the label or account_label in tool "
    "results), never by its folder code, path or key; use keys only in tool calls.",
    # 2026-10-13 录屏：推送时间按 UTC 交出去被说成当地时间；现在工具一律给当地时间（`_local_time`），说清楚别再换算
    "Times in tool results are already the user's local time, on the same clock as the current time you are given; "
    "say them as they are.",
    # 2026-10-13 录屏：对话刚谈完 9 月，会计问「这个月还有哪些票卡着」，CC 按 10 月查、答「没有」。会计说的这个月是在做的那期
    "'This month' in accounting work is the month being filed, not always the calendar month: a month already being "
    "discussed in this conversation is the one meant; with none, early in a month it is usually last month's filing. "
    "When unsure, confirm the month in one short question before looking anything up.",
    # 2026-10-13 录屏：「9 月，所有公司」时 CC 沿用了上文那家公司的筛选，只回了一家（ทุกเจ้า 也能指所有商家，这里点明是所有公司）
    "When the user asks about all companies (" + "ทุกเจ้า" + ", all clients), leave out connection, account_set_key "
    "and search even if the conversation was about one company, pass all_companies true (a conversation filed under "
    "a company otherwise covers only that company), and answer company by company from by_account_set, including "
    "those with none.",
    # 2026-10-13 录屏：拍了简易税票说「这张是某公司的，文具费」，CC 只识别，说票上没有这家公司的名字、要推送请告诉公司名
    # 测试窗口第 3 轮：CC 把金额说成「元」。金额都是泰铢
    "Amounts in tool results are Thai baht. Say the currency in the user's language (" + "บาท" + " in Thai, "
    + "泰铢" + " in Chinese, THB or baht in English); never call it yuan or " + "元" + ".",
    # 第 5 轮测试 8：推到 Express 后 CC 说「เลขที่เอกสารใน ERP」；入账后又叫人去待处理页检查或修改（已入账的不在待处理里）
    "Call the accounting program by its name, Express or MR.ERP (the one the account set uses); never say 'the ERP' or "
    "'ERP' for it. A document already entered there is done: never suggest checking or changing it on the pending "
    "page or the review page. " + ENTERED_NOTE,
    # 测试窗口第 3 轮：「交给 CC」的预填只写单号、对方、金额、日期（不写内部记录名），CC 要自己按单号找到这几张
    "When the user names documents by invoice number, counterparty, amount and date (for example a message "
    "prefilled from the pending list or the month card), find each one with cowork_list_documents search set to "
    "its invoice number (or the counterparty when there is no number, or the file name when only a file name is "
    "given), and use the amount and date to tell apart documents with the same number.",
    "When the user says which company a document belongs to (for example 'this one is X's'), enter it into that "
    "company's account set (find it with cowork_intake_options search) without asking for the company again. "
    "Abbreviated tax invoices and receipts often name no buyer: the user's word is enough. Point out a conflict "
    "only when the document names a different buyer.",
    CC_DESKTOP,
]


def _catalog_names(catalog) -> dict:
    """账套目录 → {账套键（比较用）: 给人看的名字}：同录入下拉与 cowork_intake_options，Express「年度文件夹 · 登录列表名
    （公司法定名）」，MR.ERP「公司 · 年度」；下拉里没有的（停用、写不进）用目录里的账套名。"""
    names = {}
    for row in (catalog or {}).get("account_sets") or []:
        if isinstance(row, dict) and row.get("source_id") and row.get("label"):
            names[_account_key(row["source_id"])] = str(row["label"])
    for group in (catalog or {}).get("groups") or []:
        for item in group.get("items") or []:
            head = " · ".join(str(part) for part in (group.get("label"), item.get("label")) if part)
            company = item.get("description")
            if item.get("value") and head:
                names[_account_key(item["value"])] = f"{head}（{company}）" if company else head
    return names


def _account_names(cache: dict, connection) -> dict:
    """这条连接的账套名字（一次调用里每条连接只读一次目录）；读不到（没有权限、目录没读过）是空的。"""
    if connection not in cache:
        try:
            cache[connection] = _catalog_names(connections.targets(connection)) if connection else {}
        except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError):
            frappe.clear_messages()
            cache[connection] = {}
    return cache[connection]


def _account_named(out, connection, key_field="account_set_key", cache=None):
    """结果里点了某份账套（键）：在键前面放上给人看的名字 `account_label`（已有的挪到键前面）；名字读不到就只留键。
    键仍要留着：模型调工具要用它。"""
    if not isinstance(out, dict) or not out.get(key_field):
        return out
    label = out.get("account_label") or _account_names({} if cache is None else cache, connection).get(
        _account_key(out[key_field]))
    if not label:
        return out
    named = {}
    for field, value in out.items():
        if field == key_field:
            named["account_label"] = label
        if field != "account_label":
            named[field] = value
    return named


# ---------------- 小助手状态与账套 ----------------

def companion_status(tool_input) -> str:
    """连接与小助手在不在线、账套目录读到没有。选录入账套用 `intake_options`（只列能选的、可搜），这里不再给清单，
    免得两份数字（全部 / 能选的）和两条搜索让模型挑错（2026-10-10 测试）。"""
    out = []
    for row in connections.records(None, None, 0, 100) or []:
        item = {"connection": row["name"], "label": row.get("label"), "adapter": row.get("adapter"),
                "enabled": row.get("enabled")}
        if row.get("adapter") == "express":
            item["cc_desktop_online"] = destinations.online("express", row["name"])
        try:
            targets = connections.targets(row["name"])
            item["account_sets_status"] = targets.get("status")
            item["refresh_status"] = targets.get("refresh_status")
        except (frappe.ValidationError, frappe.PermissionError):
            frappe.clear_messages()
        out.append(item)
    return _json({"connections": out})


def refresh_account_sets(tool_input) -> str:
    connection = str(tool_input.get("connection") or "")
    result = connections_api.request_refresh(connection, "assistant:" + uuid.uuid4().hex)
    return _json({"connection": connection, "status": result.get("status"), "request_id": result.get("request_id")})


# ---------------- 单据与推送结果 ----------------

def _number(value):
    """识别结果里的金额 → Decimal；空的、读不出的为 None（不当 0 算）。"""
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _with_page(row) -> dict:
    """列表一行补上主页面的单据类型、方向、税前额、税额（识别结果；同复核页读的那一页），去掉整份页面原文。"""
    from pearnly_cowork.contracts import history as history_contracts
    from pearnly_cowork.contracts.push import normalize_direction

    pages = row.pop("pages", None)
    try:
        pages = json.loads(pages) if isinstance(pages, str) else pages
    except ValueError:
        pages = None
    page = history_contracts.primary_page_fields(pages if isinstance(pages, list) else [])
    row.update({key: page.get(key) for key in PAGE_SHOWN})
    row["direction"] = normalize_direction(page.get("direction"))
    return row


def _totals(rows) -> dict:
    """合计（Decimal）：贷项单另算一组；某项没读到值的张数单独报，不当 0。"""
    groups = {}
    for row in rows:
        group = groups.setdefault("credit_notes" if row.get("document_type") == "credit_note" else "documents",
                                  {"count": 0, **{key: Decimal(0) for key in SUMMED}, "without": {}})
        group["count"] += 1
        for key in SUMMED:
            value = _number(row.get(key))
            if value is None:
                group["without"][key] = group["without"].get(key, 0) + 1
            else:
                group[key] += value
    out = {}
    for name, group in groups.items():
        out[name] = {"count": group["count"], **{key: f"{group[key]:.2f}" for key in SUMMED}}
        if group["without"]:
            out[name]["without_value"] = group["without"]
    return out


def _reason(value):
    """存的推送原因 → 按用户语言的一句话，同网页 `p.intake_push_reason` 与结果表格 `export_facts.reason_text`。
    原样交给模型的话，码（如 push.companion_account_unreported）和 10-12 前存下的旧英文原句里还是旧称，
    模型会照着转述成「小助手」；筛选和请求号仍用库里的原值。"""
    if not value:
        return value
    from pearnly_cowork.push import export_facts

    return export_facts.reason_text(value)


# 「所有公司」逐家回答（2026-10-13 录屏「เดือน 9 ทุกเจ้าเลย」：CC 沿用上文那家公司的筛选，只回了 แก้วกานดา 三张，没说只查了
# 一家，另两家卡着的贷项单、外币票都没出现）。没限定公司时列表另给 by_account_set：同一时间范围（与连接）里有单据的账套
# 都列、各有几张符合这次条件（0 张也列，说得出「这家没有」），没选账套的另算一行；按全部符合的单据算，不只这一页。
BY_ACCOUNT_SHOWN = 30


def _tally(counts, connection, account_set, count) -> None:
    """按（连接、账套键归一）累加张数；没选账套的都记在 ("", "")。"""
    key = (str(connection or ""), _account_key(account_set)) if account_set else ("", "")
    entry = counts.setdefault(key, [str(connection or ""), str(account_set or ""), 0])
    entry[2] += int(count or 0)


def _grouped(field, filters, or_filters) -> list:
    return frappe.get_list(HISTORY, filters=filters, or_filters=or_filters,
                           fields=[field, {"COUNT": "name", "as": "count"}], group_by=field, order_by="",
                           limit_page_length=0)


def _account_counts(filters, or_filters=None) -> dict:
    """符合条件的单据按推送账套数张数（同列表的权限判据）：先按连接分组，再在每条连接里按账套分组，
    都是单列分组；连接通常只有一两条。"""
    counts = {}
    for row in _grouped("push_connection", filters, or_filters):
        connection = row.get("push_connection")
        if not connection:
            _tally(counts, "", "", row.get("count"))
            continue
        for item in _grouped("push_account_set", {**filters, "push_connection": connection}, or_filters):
            _tally(counts, connection, item.get("push_account_set"), item.get("count"))
    return counts


def _by_account_set(dated, filters, or_filters, matched, names, complete=True, only=None) -> dict:
    """{"by_account_set": [...]}：``dated`` 圈出这段时间有单据的账套，``filters`` / ``or_filters`` 是这次的条件；
    ``matched`` 是取回后再筛过的全部行（方向、合计那条路），给了就按它数。账套名同列表（``names`` 是同一份缓存）。
    一份账套都没有（全是没选账套的）就不给。

    ``complete`` 为 False：取回后再筛只看了最新的 LIST_SCAN_MAX 张（审查 2026-10-15：没看到的账套原来记 0，模型会答
    「这家没有」）。这时每行标 partial，看到的张数只是下限，没看到的账套 count 为空（不是 0），整组另标
    by_account_set_complete: false。

    ``only``：公司对话的默认范围按连接、账套分别筛时多圈进来的（别的连接上同名的账套）不列，只留这些（连接、账套键归一）。"""
    known = _account_counts(dated)
    if matched is not None:
        hits = {}
        for row in matched:
            _tally(hits, row.get("push_connection"), row.get("push_account_set"), 1)
    elif filters == dated and not or_filters:
        hits = known  # 除了时间范围没有别的条件：同一组数，不再查一遍
    else:
        hits = _account_counts(filters, or_filters)
    if not any(key[1] for key in [*known, *hits]):
        return {}
    listed = []
    for key in dict.fromkeys([*hits, *known]):
        if only is not None and key not in only:
            continue
        connection, account_set, _count = hits.get(key) or known[key]
        count = hits[key][2] if key in hits else (0 if complete else None)
        partial = {} if complete else {"partial": True}
        if not key[1]:
            if count:
                listed.append({"push_target": "not chosen yet", "count": count, **partial})
            continue
        label = _account_names(names, connection).get(key[1]) if connection else None
        listed.append({**({"account_label": label} if label else {}), "connection": connection,
                       "account_set_key": account_set, "count": count, **partial})
    listed.sort(key=lambda item: (-(item["count"] or 0), item["count"] is None, "account_set_key" not in item,
                                  str(item.get("account_label") or item.get("account_set_key") or "")))
    out = {"by_account_set": listed[:BY_ACCOUNT_SHOWN]}
    if not complete:
        out["by_account_set_complete"] = False
    if len(listed) > BY_ACCOUNT_SHOWN:
        out["by_account_set_total"] = len(listed)
    return out


# ---------------- 公司对话的默认范围 ----------------

def _company_scope(tool_input, explicit=("account_set_key",)):
    """按公司可筛的读工具这次看哪些账套（`company_tools.default_scope`）。调用自己点了账套的照旧按点的取（None）；
    只给了连接不算放开——模型常把说明里的连接照抄进来（2026-10-15 测试机：公司对话里带着连接查，列出了别家 128 张），
    这时在这条连接上仍只取这家的账套。不在 CC 对话里、也没给 company 的不碰主档（照旧不限）。"""
    if any(tool_input.get(key) for key in explicit):
        return None
    session = (getattr(frappe.local, "pearnly_assistant", None) or {}).get("session_id")
    if not session and not tool_input.get("company"):
        return None
    from pearnly_cowork.assistant import company_tools

    return company_tools.default_scope(tool_input)


def _scope_pairs(sets) -> set:
    """账套 → {(连接, 账套键归一)}，同 `_tally` 的比法。"""
    return {(str(item.get("connection") or ""), _account_key(item.get("key"))) for item in sets}


def _written_of(status, record):
    """一张推送成功的票怎么写进 ERP 的（`written`，验收 F19：现赊、科目，加上单号；MR.ERP 只有单号）；没成功、没记录、
    什么都没有的回 None。"""
    if status != "success" or not record:
        return None
    return application.written([record]).get(record)


def _attach_written(rows) -> None:
    """推送成功的行补上怎么写进 Express 的（`written`：现购 / 赊购、哪层定的、记的科目，验收 F19）；
    推送记录名只用来查，不交给模型（同 `_push_one`）。"""
    records = {row.get("last_push_record"): row for row in rows
               if row.get("last_push_status") == "success" and row.get("last_push_record")}
    found = application.written(list(records)) if records else {}
    for row in rows:
        record = row.pop("last_push_record", None)
        if record in found:
            row["written"] = found[record]


def list_documents(tool_input) -> str:
    scope.require_read()
    company = _company_scope(tool_input)
    if company and company.get("failed"):
        return _json({"status": "failed", "reason": company["failed"]})
    filters, or_filters = {}, None
    # waiting_review 是旧版工具说明的参数（按「草稿」筛，推送成功的也算在内）：已开着的对话仍按旧说明调，true 按「等着录入」办
    state = tool_input.get("push_state") or ("waiting" if tool_input.get("waiting_review") else None)
    if state == "not_pushed":
        filters.update(last_push_status=["is", "not set"], last_push_reason=["is", "not set"])
    elif state == "waiting":
        # 空的推送状态也算（框架给 not in 套 IFNULL）
        filters["last_push_status"] = ["not in", PUSHED_OR_ON_ITS_WAY]
    elif state == "failed":
        or_filters = [["last_push_status", "=", "manual"], ["last_push_reason", "is", "set"]]
    elif state in ("pending", "success"):
        filters["last_push_status"] = state
    if tool_input.get("uploaded_from"):
        filters["creation"] = [">=", tool_input["uploaded_from"]]
    if tool_input.get("invoice_date_from") or tool_input.get("invoice_date_to"):
        filters["invoice_date"] = ["between", [tool_input.get("invoice_date_from") or "1900-01-01",
                                               tool_input.get("invoice_date_to") or "2999-12-31"]]
    # 推往哪条连接、哪份账套（键同 cowork_intake_options；库里比较不分大小写）
    if tool_input.get("connection"):
        filters["push_connection"] = str(tool_input["connection"])
    if tool_input.get("account_set_key"):
        filters["push_account_set"] = str(tool_input["account_set_key"])
    limit = max(1, min(int(tool_input.get("limit") or 20), LIST_MAX))
    offset = max(0, int(tool_input.get("offset") or 0))
    # 公司对话的默认范围：库里按这家的连接与账套键筛（库里比较不分大小写）。只有一条连接、或各连接上的账套都一样时，两列
    # 分别筛就是精确的；否则会多圈进别的连接上同名的账套，改走取回后逐行核对（`exact` 为 False）。
    pairs, exact = None, True
    if company and company["sets"] is not None:
        pairs = _scope_pairs(company["sets"])
        if tool_input.get("connection"):  # 点了连接：这家在这条连接上的账套
            pairs = {pair for pair in pairs if pair[0] == str(tool_input["connection"])}
        if not pairs:
            return _json({"documents": [], "count": 0, "total": 0, "offset": offset, "next_offset": None,
                          "scope": company["scope"]})
        linked = sorted({connection for connection, _key in pairs})
        keys = sorted({str(item["key"]) for item in company["sets"] if str(item.get("connection") or "") in linked})
        filters["push_connection"] = ["in", linked]
        filters["push_account_set"] = ["in", keys]
        exact = all({key for connection, key in pairs if connection == link} == {_account_key(key) for key in keys}
                    for link in linked)
    direction = tool_input.get("direction") if tool_input.get("direction") in ("purchase", "sales") else None
    search = str(tool_input.get("search") or "").strip()
    failed_only = or_filters is not None
    if search:
        # 按单号、双方名称或税号找（税号去掉分隔符比），不限最近几张；「失败」那组条件改在取回后筛
        digits = "".join(ch for ch in search if ch.isdigit())
        # 文件名也算（复审第 3 轮：「交给 CC」预填里单号、对方、金额、日期都没有的单写文件名）
        or_filters = [[field, "like", f"%{search}%"] for field in ("invoice_no", "seller_name", "buyer_name", "filename")]
        if len(digits) >= 5:
            or_filters += [[field, "like", f"%{digits}%"] for field in ("seller_tax", "buyer_tax", "invoice_no")]
    # 走 DocType 自己的权限查询条件（与录入列表同一判据），看不见的行取不到。
    query = {"filters": filters, "or_filters": or_filters, "fields": DOC_FIELDS + ["pages", "last_push_record"],
             "order_by": "modified desc, name desc"}
    out, matched, complete = {}, None, True
    if exact and not (search and failed_only) and not direction and not tool_input.get("totals"):
        rows = [_with_page(r) for r in frappe.get_list(HISTORY, limit_start=offset, limit_page_length=limit, **query)]
        if len(rows) < limit and (rows or not offset):
            total = offset + len(rows)
        else:
            counted = frappe.get_list(HISTORY, filters=filters, or_filters=or_filters,
                                      fields=[{"COUNT": "name", "as": "total"}], order_by="", limit_page_length=0)
            total = max(int((counted[0].get("total") if counted else 0) or 0), offset + len(rows))
    else:
        # 要在取回后筛（失败 + 搜索、方向）或要合计：按页看最新的最多 LIST_SCAN_MAX 张，筛完再切这一页
        matched, scanned, complete, no_direction = [], 0, False, 0
        while scanned < LIST_SCAN_MAX:
            page = frappe.get_list(HISTORY, limit_start=scanned,
                                   limit_page_length=min(LIST_SCAN_PAGE, LIST_SCAN_MAX - scanned), **query)
            scanned += len(page)
            for row in page:
                if not exact and (str(row.get("push_connection") or ""),
                                  _account_key(row.get("push_account_set"))) not in pairs:
                    continue
                row = _with_page(row)
                if search and failed_only and not (row.get("last_push_status") == "manual"
                                                   or row.get("last_push_reason")):
                    continue
                if direction and row.get("direction") != direction:
                    no_direction += not row.get("direction")
                    continue
                matched.append(row)
            if len(page) < LIST_SCAN_PAGE:
                complete = True
                break
        rows, total = matched[offset:offset + limit], len(matched)
        if tool_input.get("totals"):
            out["totals"] = _totals(matched)
        if no_direction:
            out["without_direction"] = no_direction
        if not complete:
            out["complete"] = False
            out["note"] = (f"Only the newest {LIST_SCAN_MAX} documents were checked; narrow the dates to cover "
                           "the rest. Counts in by_account_set are only what was checked: never say a company has "
                           "none from it; narrow the dates or ask about that company with its account_set_key.")
    next_offset = offset + len(rows)
    _attach_written(rows)
    names = {}
    for row in rows:
        row["last_push_reason"] = _reason(row.get("last_push_reason"))
    rows = [_account_named(row, row.get("push_connection"), "push_account_set", names) for row in rows]
    if not tool_input.get("account_set_key") and not search:
        # 没限定公司：每份账套各几张，同一时间范围里有单据的账套都列（0 张也列），见 `_by_account_set`
        dated = {key: value for key, value in filters.items()
                 if key in ("creation", "invoice_date", "push_connection", "push_account_set")}
        out.update(_by_account_set(dated, filters, or_filters, matched, names, complete,
                                   None if exact else pairs))
    if company:
        out["scope"] = company["scope"]  # 这次按哪家公司的范围取的（或 all_companies）
    # 推送时刻存的是 UTC（推送账本 `push.native` 的 now_utc）；上传时刻 creation 是站点时区
    return _json({"documents": rows, "count": len(rows), "total": total, "offset": offset,
                  "next_offset": next_offset if next_offset < total else None, **out}, utc=("last_pushed_at",))


# 推送要证明这份账套的目录刚由小助手读过（`targets/freeze.py`）；网页选定账套时会先请小助手读一次，对话里没有这一步，
# 没读过的账套就报这几种。遇到时请小助手读这份账套、等它读完再试一次（同网页「刷新」，2026-10-10 测试 t9pdem2）。
CATALOG_STALE = ("catalog_refresh_invalid", "catalog_refresh_required", "catalog_refresh_stale", "catalog_refresh_pending")
REFRESH_WAIT_SECONDS = 60


def _catalog_stale(error) -> bool:
    text = str(error)
    return any(code in text or _(code) in text for code in CATALOG_STALE)


def _refresh_account(connection, account_set) -> tuple[str, str]:
    """请小助手读这份账套的目录与主档，等它读完（最多一分钟）；返回（最后的状态, 失败原因代码）。"""
    try:
        request = connections_api.request_refresh(connection, "assistant:" + uuid.uuid4().hex, account_set)
        frappe.db.commit()
    except (frappe.ValidationError, frappe.PermissionError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return "failed", str(error)[:120]
    status, code = request.get("status"), request.get("error_code") or ""
    deadline = time.monotonic() + REFRESH_WAIT_SECONDS
    while status not in ("succeeded", "failed") and time.monotonic() < deadline:
        time.sleep(3)
        frappe.db.rollback()  # 小助手读完是在别的请求里提交的
        try:
            latest = connections_api.refresh_status(connection, request["request_id"])
        except (frappe.ValidationError, frappe.PermissionError) as error:
            frappe.clear_messages()
            return "failed", str(error)[:120]
        status, code = latest.get("status"), latest.get("error_code") or ""
    return status or "failed", code or ("timeout" if status not in ("succeeded", "failed") else "")


def _choices_view(name, connection, account_set, posting_kind, hint=None):
    """同复核页的「要人选的项」；目录没读过就先请小助手读一次再看。``hint``：用户在对话里说的费用类别（科目建议排第一）。"""
    frappe.db.commit()  # choices_view 只看不记、结束时整笔回滚：先把前面的落掉
    extra = {"hint": hint} if hint else {}
    try:
        return application.choices_view(name, connection, account_set, posting_kind, **extra)
    except (frappe.ValidationError, frappe.PermissionError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        if not _catalog_stale(error):
            raise
        status, code = _refresh_account(connection, account_set)
        if status != "succeeded":
            # 小助手也读不到（账套在 Express 里删了 / 改名了、所在的共享盘连不上）：说清楚，别让用户再去「刷新」
            frappe.throw("The computer could not read this account set ({0}). It may have been removed or renamed in "
                         "Express, or its folder is offline. Choose another account set.".format(code or status))
    return application.choices_view(name, connection, account_set, posting_kind, **extra)


# 这份账套的科目表还没读到时（`push.application.service.masters_wait`）给模型的说法：已请电脑读，别拿空的科目表请人选。
MASTERS_WAIT_SECONDS = 45
MASTERS_WAITING = {
    "reading": "CC Desktop is still reading this account set's chart of accounts from the computer. Check again "
               "shortly; do not ask the user to choose an account yet.",
    "offline": "The computer with CC Desktop is offline, so this account set's chart of accounts cannot be read. "
               "Ask the user to turn it on or open CC Desktop there.",
    "update_needed": "CC Desktop on that computer must be updated to read this account set's chart of accounts.",
    "failed": "The computer could not read this account set's chart of accounts. It may have been removed or "
              "renamed in Express, or its folder is offline.",
    "unavailable": "This account set is not on the computer's list of account sets. Choose another account set.",
}


def _masters_note(state) -> str:
    return MASTERS_WAITING.get(str(state or ""), MASTERS_WAITING["reading"])


# 这本账套近 12 个月的常用科目正在由电脑读（`choices_view` 的 ``history``，科目建议 B）：一般几秒读完，最多等这么久，
# 读到了建议里就有「这家常用的科目」；没读完照样给按名称 / 用户说的类别的建议，不干等。
HISTORY_WAIT_SECONDS = 20


def _choices_waited(name, connection, account_set, posting_kind, hint=None):
    """同 `_choices_view`；科目表正在读时最多等 `MASTERS_WAIT_SECONDS`（一般十几秒读完），读到了就给可选的科目；
    常用科目的历史正在读时最多等 `HISTORY_WAIT_SECONDS`。"""
    view = _choices_view(name, connection, account_set, posting_kind, hint)
    started = time.monotonic()
    deadline, history_deadline = started + MASTERS_WAIT_SECONDS, started + HISTORY_WAIT_SECONDS
    while True:
        now = time.monotonic()
        if not (((view.get("wait") or {}).get("state") == "reading" and now < deadline)
                or (view.get("history") == "reading" and not view.get("wait") and now < history_deadline)):
            return view
        time.sleep(3)
        frappe.db.rollback()  # 电脑读完是在别的请求里提交的
        view = _choices_view(name, connection, account_set, posting_kind, hint)


def _key(name, version, last) -> str:
    seed = f"{name}\0{version}\0{last or ''}"
    return "assistant:" + hashlib.sha256(seed.encode()).hexdigest()[:56]


def _push_one(name, connection=None, account_set_key=None, posting_kind=None, route_device="") -> dict:
    """推一张票（CC 的 `cowork_push_documents` 每一张、待处理页当场补选都走这里）。``route_device``：定时任务的票由读文件夹的
    那台电脑写（待处理页按 `folder_intake.push.reader_of` 给，ADR-021 第 51 条）；空 = 录入人自己的电脑写。"""
    try:
        doc = storage.read(name)
    except (frappe.DoesNotExistError, frappe.PermissionError):
        frappe.clear_messages()
        return {"document": name, "status": "not_found"}
    if doc.last_push_status in ("pending", "success"):
        out = {"document": name, "status": "already_" + doc.last_push_status}
        # 已入账的重推不再推，但照样说清当时怎么写的（现 / 赊、哪层定的），免得 CC 又叫用户去 Express 核对（验收 F19）
        written = _written_of(doc.last_push_status, getattr(doc, "last_push_record", None))
        if written:
            out["written"] = written
        return out
    target = connection or doc.push_connection
    if not target:
        return {"document": name, "status": "failed", "reason": "no_push_target"}
    adapter, revision = frappe.db.get_value(CONNECTION, target, ["adapter", "record_revision"]) or (None, None)
    if not adapter:
        return {"document": name, "status": "failed", "reason": "no_push_target"}
    # 推到哪个账套：用户这次说的优先（例如仅识别的单据要录进某个账套），否则用识别时选的口径
    account_set = account_set_key or (doc.push_account_set if not connection or connection == doc.push_connection
                                      else None) or None
    posting_kind = posting_kind or doc.posting_kind or None
    try:
        view = _choices_view(name, target, account_set, posting_kind)
    except (frappe.ValidationError, frappe.PermissionError) as error:
        reason = str(error)[:300]
        application.submission_failed(name, reason)
        frappe.db.commit()
        return {"document": name, "status": "failed", "reason": reason}
    needed = view.get("needed") or []
    wait = view.get("wait")
    if wait and wait.get("state") in ("reading", "offline"):
        # 要选的科目还没有可选值（已请电脑读这份账套的科目表）：不记成「要人选」，读到后再推
        return {"document": name, "status": "waiting_for_cc", "reason": wait["state"],
                "note": _masters_note(wait["state"])}
    # 停下时把这次要推往的连接与账套抄到票上（账套用推送服务解析出的那份），待处理页才能当场选（验收 F17）
    going = {"connection": target, "account_set": view.get("account_set") or account_set}
    if wait:
        push_state.record_failure(name, wait["message"], **going)
        frappe.db.commit()
        return {"document": name, "status": "failed", "reason": wait["state"], "note": _masters_note(wait["state"])}
    if needed:
        reason = "choice_needed:" + ",".join(dict.fromkeys(item["kind"] for item in needed))
        push_state.record_failure(name, reason, **going)
        frappe.db.commit()
        return {"document": name, "status": "needs_choice", "reason": reason}
    key = _key(name, doc.record_version, doc.last_push_record or doc.last_push_reason)
    result = application.submit(name, target, key, doc.record_version, revision, account_set, None, None, posting_kind,
                                route_device=route_device if adapter == "express" else "")
    frappe.db.commit()
    if result.get("status") in ("failed", "cancelled", "manual"):
        return {"document": name, "status": result.get("status"), "reason": str(result.get("reason") or "")[:300]}
    if result.get("status") == "prepared":
        try:
            destinations.of(adapter).confirm(target, result, "confirm:" + key)
            frappe.db.commit()
        except (frappe.ValidationError, frappe.PermissionError, frappe.AuthenticationError) as error:
            frappe.db.rollback()
            frappe.clear_messages()
            try:
                application.cancel_prepared(result["name"], result["revision"])
                frappe.db.commit()
            except Exception:
                frappe.db.rollback()
                frappe.clear_messages()
            application.submission_failed(name, str(error))
            frappe.db.commit()
            return {"document": name, "status": "failed", "reason": str(error)[:300]}
    # 账本记录名只给下面等结论用（`_settle`），不交给模型
    sent = {"document": name, "status": "sent", "adapter": adapter, "record": result.get("name")}
    if adapter == "express":
        # Express 由那台电脑上的 CC 桌面版写入：不在线就先排着，连上后自动写。
        sent["cc_desktop_online"] = destinations.online(adapter, target)
    return sent


# ---------------- 推送后等写账结论（用户 2026-10-13 第 9 条） ----------------

# 发出去以后在工具里等一会儿结论再回：CC 桌面版每 3 秒取一次队列，写一张 Express 一般几秒（教程录制：只晚了几秒，
# CC 却先答了「已排队、还没确认」就收尾）。最多等 PUSH_WAIT_SECONDS，每 PUSH_WAIT_STEP 秒读一次账本（本人这几条记录，
# 一条主键查询，不加锁）；到点还没结论的照实说「已排队」，让模型之后用 cowork_push_results 查，不自己 sleep。
# 只在推送线程里等（`toolrun.may_wait`）：网页请求线程、long 队列进程是全站共用的，那里执行时只读一次就回。
# 为什么不做「结论到了再回填工具结果」：到点还没结论时要有人回「已排队」，那就得另起定时器（新轮询）或占着一个进程等，
# 与这里等价却多一套状态；写账回报（ACK）的请求里也不该去调模型厂商的接口。
PUSH_WAIT_SECONDS = 25
PUSH_WAIT_STEP = 2
PUSH_QUEUED_NOTE = ("queued: sent, but Express or MR.ERP has not reported a result yet (or CC Desktop on that computer is "
                    "offline: it is entered once CC Desktop is back). Tell the user it is queued; check later with "
                    "cowork_push_results when they ask. Do not wait or sleep. A queued document is not entered yet: "
                    "never say it was entered or recorded into Express or MR.ERP, not even at the start of the reply.")


def _may_wait() -> bool:
    """执行这次工具的地方能不能等（共用层 `toolrun.may_wait` 判定后放在对话信息里）；拿不到就不等。"""
    return bool((getattr(frappe.local, "pearnly_assistant", None) or {}).get("may_wait"))


def _concluded(item, outcome) -> bool:
    """按账本结论改这一张的结果；还在路上的（prepared / pending / leased）不改，回 False。"""
    status = outcome.get("status")
    if status == "success":
        item["status"] = "entered"
        if outcome.get("docnum"):
            item["docnum"] = outcome["docnum"]
        if outcome.get("written"):
            item["written"] = outcome["written"]  # 现购 / 赊购、哪层定的、记的科目（验收 F19）
    elif status == "manual":
        # 结果不明（写没写进 ERP 说不准）不说失败：要人去 ERP 核对后用 cowork_resolve_push_result 记下，别重推
        item.update(status="unconfirmed" if outcome.get("result_unknown") else "failed",
                    reason=_reason(outcome.get("reason")))
    elif status == "cancelled":
        item.update(status="failed", reason=_reason(outcome.get("reason")) or status)
    else:
        if outcome.get("reason"):
            item["last_attempt_failed"] = _reason(outcome["reason"])  # 这一次没写成，CC 桌面版会再试
        return False
    item.pop("last_attempt_failed", None)
    return True


def _settle(results) -> None:
    """发出去的几张（`sent`）等写账结论：入账（`entered`，带 ERP 单号）、失败（带原因）、结果不明，或到点仍在排队
    （`queued`）。CC 桌面版不在线的不等（连上前不会有结论）；所有张共用一个时限。"""
    waiting = {}
    for item in results:
        record = item.pop("record", None)
        if item.get("status") != "sent":
            continue
        item["status"] = "queued"  # 到点还没结论就是这个
        if record:
            waiting[record] = item
    live = {name for name, item in waiting.items() if item.get("cc_desktop_online", True)}
    deadline = time.monotonic() + PUSH_WAIT_SECONDS
    first = True
    if waiting:
        frappe.db.commit()  # 上面各张的推送都已落库；等的时候不带着本事务
    while waiting and (first or (live and _may_wait() and time.monotonic() < deadline)):
        if not first:
            time.sleep(max(0.0, min(PUSH_WAIT_STEP, deadline - time.monotonic())))
        first = False
        frappe.db.rollback()  # 结论是 CC 桌面版回报时在别的请求里提交的：换一份新快照再读
        for name, outcome in application.outcomes(list(waiting)).items():
            if name in waiting and _concluded(waiting[name], outcome):
                waiting.pop(name)
                live.discard(name)


def push_documents(tool_input) -> str:
    scope.require("intake")
    names = [str(n) for n in (tool_input.get("documents") or [])][:PUSH_MAX]
    if not names:
        frappe.throw(_("No documents were given."))
    results = [_push_one(name, tool_input.get("connection") or None, tool_input.get("account_set_key") or None,
                         tool_input.get("posting_kind") or None) for name in names]
    _settle(results)
    out = {"results": results}
    if any(item.get("status") == "queued" for item in results):
        out["note"] = PUSH_QUEUED_NOTE
    return _json(out)


# ---------------- 对话里的票据：上传识别 ----------------

RECOGNIZE_MAX = 10
RECOGNIZE_WAIT_SECONDS = 90
FINISHED = ("saved", "not_invoice")
BROKEN = ("failed", "uncertain", "cancelled")
# 结束了却没出结果（谷歌报错、回放库里没有、结果不明）：同一个附件再调就用新的请求号重新识别一次（2026-10-13 测试机：
# 原先永远拿回那份失败的）。出了结果、还在跑的、被取消的照旧拿回原来那份。失败的不扣费，新的那次照常扣。
RETRY_STATES = ("failed", "uncertain")
RECOGNIZE_ATTEMPTS = 5  # 同一个附件最多识别几次（第 2 次起请求号带「:次数」）
ITEMS_SHOWN = 30
# 对话里能交给识别的：票据照片与 PDF（HEIC 照片在对话上传时已转成 JPEG），以及表格（改好发回的复核工作簿走同一条
# 识别路，同网页上传）。文本、Word 等不在这里识别：说不清是什么单据，识别服务只会回「文件是空的」这种误导的话。
RECOGNIZABLE = (".pdf", ".png", ".jpg", ".jpeg", ".webp", ".tiff", ".tif", ".bmp", ".gif", ".xlsx", ".xls", ".xlsm",
                ".csv")


def _recognition():
    from pearnly_cowork.ocr.execution import service

    return service


def _admit(file_id, choices) -> dict:
    """一个附件 → 录入工作台的原件 + 识别任务，与 LINE 发票据同一条路（`line/intake._admit`）：扣费口径与网页一致；
    请求号按附件固定，同一个附件再调一次拿回原来的任务，不重复扣费。上一次结束了却没出结果（``RETRY_STATES``）的，
    换下一个请求号（「assistant:附件:2」…）重新识别，最多 ``RECOGNIZE_ATTEMPTS`` 次。"""
    from pearnly_core.assistant import files as chat_files
    from pearnly_core.execution.errors import Deferred, QueueFull

    from pearnly_cowork.files import service as originals

    try:
        filename, content = chat_files.attachment(file_id)
        # 请求号按本对话里的文件编号：给编号、挂载路径还是文件名都是同一个附件（接续新上下文后模型按文件名称呼旧附件）
        base = "assistant:" + chat_files.resolve(file_id)
    except frappe.ValidationError:
        frappe.clear_messages()
        return {"file": file_id, "status": "not_found", "attachments_in_this_chat": chat_files.attachments()}
    if not filename.lower().endswith(RECOGNIZABLE):
        return {"file": file_id, "filename": filename, "status": "unsupported_type",
                "reason": "Only document photos, PDF files and spreadsheets (such as a returned review workbook) "
                          "can be recognized."}
    # 工具自己生成的文件（原件页图、复核工作簿、回读的账……）不再送识别：那不是票据，识别还要照常扣费。
    # 从文件夹取来的（`cowork_folder_fetch`）与用户附件一样可以识别。
    if chat_files.origin(file_id) == "tool":
        return {"file": file_id, "filename": filename, "status": "not_started",
                "reason": "made_by_tool: this file was made by a tool, not a document to recognize"}
    try:
        for attempt in range(1, RECOGNIZE_ATTEMPTS + 1):
            key = base if attempt == 1 else f"{base}:{attempt}"
            source = originals.create(scope.default_workspace(), filename, content, key)
            frappe.db.commit()
            batch, earlier = _submit(source, key, choices)
            frappe.db.commit()
            if batch.get("state") not in RETRY_STATES:
                break
    except (Deferred, QueueFull) as exc:
        frappe.db.rollback()
        return {"file": file_id, "status": "not_started",
                "reason": exc.reason if isinstance(exc, Deferred) else "queue_full"}
    except frappe.ValidationError as exc:
        frappe.db.rollback()
        frappe.clear_messages()
        return {"file": file_id, "status": "not_started", "reason": str(exc)[:300]}
    admitted = {"file": file_id, "filename": filename, "batch": batch["name"]}
    if attempt > 1:
        admitted["attempt"] = attempt
    if earlier:
        admitted.update(recognized_before=True, note=RECOGNIZED_BEFORE)
    return admitted


# 同一个附件只按第一次交的口径识别一次（识别服务拒收不同参数）。2026-10-13 录屏：照片先「仅识别」，会计员说了是哪家公司
# 再交一次带账套的，原先回「参数不同」的拒收；现在拿回原来那份，并说清按会计员说的账套推送（推送可指定连接与账套）。
RECOGNIZED_BEFORE = ("Recognized from this attachment earlier with other choices; not recognized or charged again. To "
                     "enter its documents into an account set, push them with cowork_push_documents with connection "
                     "and account_set_key.")


def _submit(source, key, choices) -> tuple:
    """交一次识别 → (识别任务, 是否拿回的是这个附件早先按别的口径交的那份)。"""
    try:
        return _recognition().submit(source["name"], key, **choices), False
    except frappe.ValidationError:
        frappe.db.rollback()
        earlier = _recognition().recognized(source["name"])
        if not earlier:
            raise
        frappe.clear_messages()
        return earlier, True


def _push_target(doc) -> dict:
    """录入口径里「仅识别」在库里记作 intake_target=none，模型会说成「推送目标：不推送」；只说还没选推送去哪。"""
    return {} if doc.get("push_connection") else {"push_target": "not chosen yet"}


def _filled(values: dict) -> dict:
    """空值不给：模型会把「币种：未识别到」之类的空项当成要说的事。"""
    return {key: value for key, value in values.items() if value not in (None, "", [], {})}


FILES_ADDED_NOTE = ("Added to this conversation: the user opens them in the chat. They were not saved to any folder on "
                    "the user's computer.")


def _added_to_chat(out: dict, added) -> dict:
    """工具把文件放进了这段对话：结果里明说（2026-10-13 测试机：CC 对用户说放进了「结果文件夹」，用户去电脑上找）。
    ``added`` 为空（这次没放文件）就原样返回。"""
    if not added:
        return out
    return {**out, "files_added_to_chat": True, "files_note": FILES_ADDED_NOTE}


def _choices(tool_input) -> dict:
    target = tool_input.get("target") or None
    push = target in ("express", "mrerp") and tool_input.get("connection")
    choices = {"direction": tool_input.get("direction") or None, "posting_kind": tool_input.get("posting_kind") or None,
               "target": target, "connection": tool_input.get("connection") if push else None,
               "account_set_key": tool_input.get("account_set_key") if push else None}
    if tool_input.get("payment"):
        choices["payment"] = tool_input["payment"]
    return choices


def _document(name, names=None) -> dict:
    from pearnly_cowork.contracts import document_policy as policy_rules
    from pearnly_cowork.contracts import history as history_contracts

    doc = storage.project(storage.read(name))
    page = history_contracts.primary_page_fields(doc.get("pages") or [])
    out = {key: doc.get(key) for key in ("name", "invoice_no", "invoice_date", "seller_name", "total_amount",
                                          "posting_kind", "push_connection", "push_account_set", "duplicate_warning")}
    out = _account_named(out, doc.get("push_connection"), "push_account_set", names)
    out.update(_push_target(doc))
    out.update({key: page.get(key) for key in ("document_type", "seller_tax", "seller_branch", "buyer_name",
                                                "buyer_tax", "subtotal", "vat", "discount", "wht_amount", "currency")})
    # 50 ทวิ 的 document_type 是 other：说清是预扣税凭证（转账凭条的票种本身已说明），同存档、推送门槛同一条规则
    if policy_rules.document_class(page) == policy_rules.CERTIFICATE:
        out["document_kind"] = policy_rules.CERTIFICATE
    out["items"] = (page.get("items") or [])[:ITEMS_SHOWN]
    return _filled(out)


def _result(batch, names=None) -> dict:
    # 扣费等内部状态不交给模型（识别刚存完那一刻还是「待扣」，模型会误报成扣了两次）；扣费口径写在工具说明里。
    detail = _recognition().detail(batch)
    state = detail.get("state")
    out = {"batch": batch, "state": state, "pages": detail.get("page_count")}
    if state == "saved":
        names = {} if names is None else names
        out["documents"] = [_document(name, names) for name in detail.get("history_records") or []]
        out["needs_review"] = bool(detail.get("requires_review"))
    elif state in BROKEN:
        out["reason"] = detail.get("last_code") or state
    if state in FINISHED:
        out.update(_certificates(batch, state))
    return out


def _certificates(batch, state) -> dict:
    """识别出的 50 ทวิ（2026-10-13 测试机：两张凭证只回了「不是发票」，CC 说不清是什么）：整份没成票的说清是预扣税凭证；
    选了「不录入」、存给 ภ.ง.ด.3 / 53 草稿的参考记录给出条数（与票扫在一起的凭证也算）。"""
    found = _recognition().certificates(batch)
    out = {}
    if state == "not_invoice" and found.get("pages"):
        out["document_kind"] = "withholding_certificate"
    if found.get("kept"):
        out["kept_for_wht_draft"] = found["kept"]
    return out


def _selectable(choices) -> bool:
    """识别时选的账套还在可选清单里（同网页下拉：在用、Express 可写），免得把不存在的账套写进单据口径。"""
    from pearnly_cowork.connections import account_search

    try:
        catalog = connections.targets(choices["connection"])
    except (frappe.ValidationError, frappe.PermissionError):
        frappe.clear_messages()
        return False
    usable = account_search.usable(choices.get("target"), catalog.get("account_sets") or [])
    wanted = str(choices["account_set_key"]).casefold()
    return any(str(row["source_id"]).casefold() == wanted for row in usable)


def recognize_attachments(tool_input) -> str:
    scope.require("intake")
    file_ids = [str(f) for f in (tool_input.get("files") or [])][:RECOGNIZE_MAX]
    if not file_ids:
        frappe.throw(_("No attachments were given."))
    choices = _choices(tool_input)
    if choices.get("account_set_key") and not _selectable(choices):
        return _json({"results": [{"file": f, "status": "not_started", "reason": "account_set_unavailable: this account "
                                   "set is not in the current list; choose one from cowork_intake_options"}
                                  for f in file_ids]})
    if choices.get("target") == "express" and choices.get("account_set_key"):
        # 同网页选定 Express 账套后：请小助手趁识别时读这份账套，推送前就有它的目录与主档（读不到不拦识别）
        try:
            connections_api.ensure_account_masters(choices["connection"], choices["account_set_key"])
            frappe.db.commit()
        except (frappe.ValidationError, frappe.PermissionError):
            frappe.db.rollback()
            frappe.clear_messages()
    admitted = [_admit(file_id, choices) for file_id in file_ids]
    waiting = [a for a in admitted if a.get("batch")]
    deadline = time.monotonic() + RECOGNIZE_WAIT_SECONDS
    names = {}
    while waiting:
        frappe.db.rollback()  # 识别在队列里另一进程做：每次重新读，看得到它刚提交的结果
        for item in waiting:
            item.update(_result(item["batch"], names))
        waiting = [a for a in waiting if a.get("state") not in FINISHED + BROKEN]
        if not waiting or time.monotonic() >= deadline:
            break
        time.sleep(3)
    for item in admitted:
        _declare_direction(item, choices.get("direction"))
    return _json({"results": admitted})


def _declare_direction(item, direction):
    """拿回早先按别的口径识别的那份时，会计员这次说明的进 / 销项写到识别出的票上（识别服务只认第一次的口径，
    2026-10-15 测试环境：先「仅识别」、再说是费用，票上方向空着）。票上已有方向的不改（`storage.declare_direction`）；
    还没识别完的不写，推送时按账套税号判。"""
    if not direction or not item.get("recognized_before") or item.get("state") != "saved":
        return
    records = _recognition().detail(item["batch"]).get("history_records") or []
    try:
        if records and storage.declare_direction(records, direction):
            frappe.db.commit()
    except (frappe.ValidationError, frappe.PermissionError):
        frappe.db.rollback()  # 写不上（票正被人改着等）不影响识别结果，推送时照样按账套税号判
        frappe.clear_messages()


def recognition_results(tool_input) -> str:
    scope.require_read()
    batches = [str(b) for b in (tool_input.get("batches") or [])][:RECOGNIZE_MAX]
    if not batches:
        frappe.throw(_("No recognition tasks were given."))
    out, names = [], {}
    for batch in batches:
        try:
            out.append(_result(batch, names))
        except (frappe.DoesNotExistError, frappe.PermissionError):
            frappe.clear_messages()
            out.append({"batch": batch, "state": "not_found"})
    return _json({"results": out})


# ---------------- POS 导出按天录销售（交接 L9：不识别、不扣费） ----------------

POS_FILES_MAX = 5
POS_ANSWER_KEYS = ("columns", "date_order", "amount_basis", "status_values")
POS_ANSWERS_SCHEMA = {
    "type": "object", "description": "The user's answers to layout_needed questions.",
    "properties": {
        "columns": {"type": "object", "additionalProperties": {"type": "string"},
                    "description": "Field to header text, fields: date, receipt (receipt number), amount (paid "
                                   "amount), vat, payment (payment method), kind (sale or refund), status, item, "
                                   "sku, qty. An empty header leaves an optional field out."},
        "date_order": {"type": "string", "enum": ["dmy", "mdy", "ymd"]},
        "amount_basis": {"type": "string", "enum": ["inclusive", "exclusive"],
                         "description": "Whether the amount column already includes VAT."},
        "status_values": {"type": "object", "additionalProperties": {"type": "string",
                                                                      "enum": ["sale", "void", "refund"]},
                          "description": "What each listed status value means."}}}


def _pos():
    from pearnly_cowork.pos_import import service

    return service


def _pos_text(result) -> dict:
    """POS 服务的结果交给模型：失败码附上一句人话（`pos.*` 词条）；版式修订是文件夹任务内部用的，不给。"""
    result = {key: value for key, value in result.items() if key != "layout_revision"}
    if result.get("status") == "failed":
        result = {**result, "reason": _(result.get("code") or "pos.file_unreadable")}
    return result


def _pos_answers(tool_input):
    answers = tool_input.get("answers") if isinstance(tool_input.get("answers"), dict) else {}
    return {key: answers[key] for key in POS_ANSWER_KEYS if key in answers} or None


def _pos_source(file_id) -> dict:
    """对话附件 → 录入工作台的原件（请求号按附件固定：同一个附件再调拿回同一份原件、同一批票）。"""
    from pearnly_core.assistant import files as chat_files

    from pearnly_cowork.files import service as originals
    from pearnly_cowork.pos_import import tables

    try:
        filename, content = chat_files.attachment(file_id)
        key = "assistant:pos:" + chat_files.resolve(file_id)  # 同识别：按本对话里的文件编号
    except frappe.ValidationError:
        frappe.clear_messages()
        return {"file": file_id, "status": "not_found", "attachments_in_this_chat": chat_files.attachments()}
    kind = tables.kind_of(filename)
    if kind in ("", "legacy"):
        code = "pos.xls_not_supported" if kind == "legacy" else "pos.type_unsupported"
        return {"file": file_id, "filename": filename, "status": "failed", "code": code, "reason": _(code)}
    try:
        source = originals.create(scope.default_workspace(), filename, content, key)
        frappe.db.commit()
    except frappe.ValidationError as exc:
        frappe.db.rollback()
        frappe.clear_messages()
        return {"file": file_id, "filename": filename, "status": "failed", "reason": str(exc)[:300]}
    return {"file": file_id, "filename": filename, "source": source["name"], "key": key}


def pos_import(tool_input) -> str:
    """POS 导出（收银机的销售明细 Excel / CSV）按天建成录入工作台的销售票：现金客户的简易税票日汇总，推往这份 Express
    账套。不识别、不扣费；版式（哪列是日期、票号、金额……）按公司问一次、记住。"""
    scope.require("intake")
    file_ids = [str(f) for f in (tool_input.get("files") or [])][:POS_FILES_MAX]
    connection, account_set = tool_input.get("connection") or "", tool_input.get("account_set_key") or ""
    if not file_ids:
        frappe.throw(_("No attachments were given."))
    choices = {"target": "express", "connection": connection, "account_set_key": account_set}
    if not connection or not account_set or not _selectable(choices):
        return _json({"results": [{"file": f, "status": "failed", "reason": "account_set_unavailable: choose an "
                                   "Express account set from cowork_intake_options"} for f in file_ids]})
    try:
        # 同识别：请小助手趁这时读这份账套，推送前就有它的科目（收款科目要从里面选）
        connections_api.ensure_account_masters(connection, account_set)
        frappe.db.commit()
    except (frappe.ValidationError, frappe.PermissionError):
        frappe.db.rollback()
        frappe.clear_messages()
    posting_kind = tool_input.get("posting_kind") if tool_input.get("posting_kind") in ("service", "stock") else None
    answers, results = _pos_answers(tool_input), []
    for file_id in file_ids:
        found = _pos_source(file_id)
        if not found.get("source"):
            results.append(found)
            continue
        try:
            if answers:
                remembered = _pos().preview(found["source"], workspace=scope.default_workspace(), connection=connection,
                                            account_set_key=account_set, answers=answers, posting_kind=posting_kind)
                if remembered.get("status") != "ready":
                    frappe.db.commit()
                    results.append({"file": file_id, "filename": found["filename"], **_pos_text(remembered)})
                    continue
            result = _pos().admit(found["source"], found["key"], workspace=scope.default_workspace(),
                                  connection=connection, account_set_key=account_set, posting_kind=posting_kind)
            frappe.db.commit()
        except (frappe.ValidationError, frappe.PermissionError) as exc:
            frappe.db.rollback()
            frappe.clear_messages()
            result = {"status": "failed", "reason": str(exc)[:300]}
        results.append({"file": file_id, "filename": found["filename"], **_pos_text(result)})
    return _json(_account_named({"account_set_key": account_set, "results": results}, connection))


def pos_layout(tool_input) -> str:
    """回答 POS 导出的认列问题并记在这家公司名下（以后同版式直接用）：对话附件，或文件夹任务里停着等认列的那份
    （`folder_file`，答好后它接着自动录入）。不建票。"""
    scope.require("intake")
    answers = _pos_answers(tool_input)
    folder_file = str(tool_input.get("folder_file") or "")
    if folder_file:
        try:
            held = rules.pos_file(folder_file)
        except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as exc:
            frappe.clear_messages()
            return _json({"folder_file": folder_file, "status": "not_found", "reason": str(exc)[:200]})
        result = _pos().preview(held["source_file"], workspace=held["workspace"], connection=held["connection"],
                                account_set_key=held["account_set_key"], answers=answers,
                                posting_kind=held["posting_kind"])
        resumed = result.get("status") == "ready" and rules.resume_pos(folder_file)
        frappe.db.commit()
        return _json(_account_named({"folder_file": folder_file, "filename": held["filename"], "task": held["title"],
                                     "account_set_key": held["account_set_key"], "resumed": bool(resumed),
                                     **_pos_text(result)}, held["connection"]))
    file_id = str(tool_input.get("file") or "")
    connection, account_set = tool_input.get("connection") or "", tool_input.get("account_set_key") or ""
    if not file_id or not connection or not account_set:
        return _json({"status": "failed", "reason": "Give file with connection and account_set_key, or folder_file."})
    found = _pos_source(file_id)
    if not found.get("source"):
        return _json(found)
    result = _pos().preview(found["source"], workspace=scope.default_workspace(), connection=connection,
                            account_set_key=account_set, answers=answers)
    frappe.db.commit()
    return _json(_account_named({"file": file_id, "filename": found["filename"], "account_set_key": account_set,
                                 **_pos_text(result)}, connection))


# ---------------- 录入口径（同网页「新建录入」与 LINE；问一次、记住） ----------------

BATCH = "Cowork OCR Batch"
OPTIONS_SHOWN = 30
MRERP_READ_WAIT_SECONDS = 60


# 不能选的 Express 状态给模型的说明（不是界面文案）：分清「没有能写的电脑」与「未连接电脑」（ADR-021 第 53 条，10-16 审查）
TARGET_NOTES = {
    "no_writer": "None of the user's own computers with CC Desktop can open this program's account sets (documents "
                 "are written only by the user's own computers, never a colleague's). Tell the user and ask them to "
                 "sign in to CC Desktop on a computer that can open the account set.",
    "cc_desktop_not_connected": "The user has not connected CC Desktop on any computer yet. Tell the user to install "
                                "CC Desktop on their own computer and sign in there.",
}


def _target_state(row) -> str:
    """同 LINE `flow._state`：没连、停用、当前用户自己的电脑一台都没有（``cc_desktop_not_connected``）、有电脑却都够不到
    能写的账套（``no_writer``，不转给同事的电脑），都不能选。能写的电脑关着照样能选（排队，开机后写，D4，ADR-021）。"""
    if not row:
        return "not_connected"
    if not row.get("enabled"):
        return "disabled"
    if row.get("adapter") == "express":
        found = destinations.intake_state("express", row["name"])
        if found == destinations.MISSING:
            return "cc_desktop_not_connected"
        if found == destinations.UNREACHABLE:
            return "no_writer"
    return "ready"


def _blank_template(row) -> bool:
    """Express 每份数据文件夹里自带的空白模板账套（DAT 文件夹）排到最后：没人往里录。"""
    return str(row.get("source_id") or "").replace("/", "\\").rstrip("\\").rsplit("\\", 1)[-1].casefold() == "dat"


def _year(row) -> int:
    """账套所在年度（Express 数据文件夹名里的两位年份，如 70EXP；MR.ERP 的年度名），排序用：新的在前。"""
    import re

    attributes = row.get("attributes") or {}
    text = " ".join(str(attributes.get(key) or "") for key in ("root_label", "root", "year", "year_name"))
    years = [int(y) for y in re.findall(r"(?<!\d)(\d{2}|25\d{2})(?!\d)", text)]
    return max(years, default=-1)


def _last_used() -> dict:
    """上一次录进 ERP 的识别用的口径（不论在网页、LINE 还是这里；仅识别的不算）：问过一次就沿用，不另存一份。
    识别批次的读权限不对普通用户开放列表，这里按「本人提交的」明确过滤（actor 即提交人）。"""
    rows = frappe.get_all(BATCH, filters={"actor": frappe.session.user, "intake_target": ["in", ["express", "mrerp"]]},
                           fields=["intake_target", "target_connection", "target_account_set", "direction",
                                   "posting_kind", "payment"], order_by="creation desc", limit_page_length=1)
    if not rows:
        return {}
    row = rows[0]
    return _filled({"target": row.get("intake_target"), "connection": row.get("target_connection"),
                    "account_set_key": row.get("target_account_set"), "direction": row.get("direction"),
                    "posting_kind": row.get("posting_kind"), "payment": row.get("payment")})


def _still_available(last, targets) -> bool:
    for item in targets:
        if item.get("connection") == last.get("connection"):
            if item.get("state") != "ready":
                return False
            return item.get("_keys") is None or str(last.get("account_set_key") or "").casefold() in item["_keys"]
    return False


def intake_options(tool_input) -> str:
    from pearnly_cowork.connections import account_search

    scope.require("intake")
    search = str(tool_input.get("search") or "").strip()
    rows = connections.records(None, None, 0, 100) or []
    # 每种 ERP 只认主连接（最早建、启用、不挂客户），同 LINE、网页与电脑登记（ADR-021）
    first = {adapter: row for adapter in ("express", "mrerp") if (row := connections.main_of(rows, adapter))}
    last = _last_used()
    targets, names = [], {}
    for adapter in ("express", "mrerp"):
        row = first.get(adapter)
        item = {"target": adapter, "state": _target_state(row)}
        if item["state"] in TARGET_NOTES:
            item["note"] = TARGET_NOTES[item["state"]]
        if row:
            item["connection"] = row["name"]
            item["label"] = row.get("label")
        if item["state"] == "ready" and adapter == "express":
            # 能写的电脑此刻都关着（第 6 批「电脑离线（仍可选）」）：照样能选、确认，排队等开机后写，CC 照实告诉用户
            item["cc_desktop_online"] = destinations.online("express", row["name"])
        if item["state"] == "ready":
            try:
                catalog = connections.targets(row["name"])
            except (frappe.ValidationError, frappe.PermissionError):
                frappe.clear_messages()
                catalog = {}
            usable = account_search.usable(adapter, catalog.get("account_sets") or [])
            # 上次用的那份也按清单里的名字说（2026-10-13 录屏：只给了键，CC 说成文件夹名「demo02」）
            names[row["name"]] = {_account_key(r["source_id"]): account_search.label(adapter, r)
                                  for r in catalog.get("account_sets") or [] if r.get("source_id") and r.get("label")}
            item["_keys"] = {str(r["source_id"]).casefold() for r in usable}
            item["account_sets_total"] = len(usable)
            if search:
                found = account_search.search(adapter, usable, search)
            else:
                remembered = str(last.get("account_set_key") or "").casefold() \
                    if last.get("connection") == row["name"] else ""
                ranked = sorted(usable, key=lambda r: (str(r["source_id"]).casefold() != remembered,
                                                       _blank_template(r), -_year(r)))
                found = [{"value": r["source_id"], "label": account_search.label(adapter, r)} for r in ranked]
            item["account_sets"] = [{"label": r["label"], "key": r["value"]} for r in found[:OPTIONS_SHOWN]]
            if not usable:
                item["account_sets_status"] = catalog.get("status") or "missing"
        targets.append(item)
    targets.append({"target": "none", "state": "ready"})
    extra = {}
    last = _account_named(last, last.get("connection"), cache=names)
    if last and not _still_available(last, targets):
        # 上次用的账套已不在可选清单（删了、改名、不可写或那条连接停了）：不推荐，告诉用户另选
        extra["last_used_unavailable"] = last
        last = {}
    for item in targets:
        item.pop("_keys", None)
    return _json({"suggested": last or None, **extra, "targets": targets,
                  "modes": {"express": "posting_kind: service or stock", "mrerp": "payment: cash or credit "
                            "(purchases are credit only)", "none": "recognize only, nothing else to choose"}})


def _connection_call(fn, *args):
    try:
        return fn(*args)
    except (frappe.ValidationError, frappe.PermissionError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return {"status": "failed", "reason": str(error)[:300]}


def stock_group(tool_input) -> str:
    """Express 记成存货时的存货科目组：每个账套选一次、记住（大家共用，同网页）。"""
    scope.require("intake")
    name, key = str(tool_input.get("connection") or ""), str(tool_input.get("account_set_key") or "")
    if tool_input.get("acccod"):
        result = _connection_call(connections_api.choose_stock_acc_group, name, key, str(tool_input["acccod"]))
        frappe.db.commit()
        return _json(_account_named(result, name))
    view = _connection_call(connections_api.stock_acc_group, name, key)
    if view.get("known") is False:
        # 这份账套还没读过：请电脑读（同一时刻只排一个），过一会儿再看就有候选了
        masters = _connection_call(connections_api.ensure_account_masters, name, key)
        frappe.db.commit()
        if masters.get("state"):
            view = {**view, "status": masters["state"], "note": _masters_note(masters["state"])}
    return _json(_account_named(view, name))


def mrerp_setup(tool_input) -> str:
    """MR.ERP 推送用的导入菜单与默认值：每个账套每类单据选一次、记住（同网页推送前那一步）。"""
    scope.require("intake")
    name, key = str(tool_input.get("connection") or ""), str(tool_input.get("account_set_key") or "")
    if tool_input.get("read_again"):
        request = _connection_call(connections_api.request_mrerp_menu, name, key)
        frappe.db.commit()
        if request.get("status") == "failed":
            return _json(request)
        deadline = time.monotonic() + MRERP_READ_WAIT_SECONDS
        while time.monotonic() < deadline:
            frappe.db.rollback()  # 后台读完提交的结果要重新读才看得到
            view = _connection_call(connections_api.mrerp_menu, name, key)
            stamps = [view.get("observed_at"), view.get("error_at")]
            if any(str(stamp or "") >= str(request["requested_at"]) for stamp in stamps):
                break
            time.sleep(2)
    for doc_type, idmenu in (tool_input.get("menus") or {}).items():
        result = _connection_call(connections_api.choose_mrerp_menu, name, key, doc_type, str(idmenu))
        if result.get("status") == "failed":
            return _json(result)
    for field, code in (tool_input.get("defaults") or {}).items():
        result = _connection_call(connections_api.choose_mrerp_default, name, key, field, str(code))
        if result.get("status") == "failed":
            return _json(result)
    frappe.db.commit()
    # 读到菜单 / 读失败的时刻是连接服务按 UTC 记的 ISO 字符串
    return _json(_account_named(_connection_call(connections_api.mrerp_menu, name, key), name),
                 utc=("observed_at", "error_at"))


# ---------------- 复核与修改（同录入工作台复核页） ----------------

# 复核页能改的字段（intake-upload.js FIELD_KEYS）；日期按原文写进 date_raw，由单据服务解析（泰历、两位年份都认）。
EDITABLE = ("seller_name", "seller_tax", "seller_addr", "seller_branch", "buyer_name", "buyer_tax", "buyer_addr",
            "buyer_branch", "invoice_number", "date", "document_type", "subtotal", "vat", "discount", "total_amount",
            "wht_amount", "currency", "notes", "direction", "posting_kind")
AMOUNTS = ("subtotal", "vat", "discount", "total_amount", "wht_amount")
TAX_IDS = ("seller_tax", "buyer_tax")
CHOICES = {"document_type": ("tax_invoice", "simplified_tax_invoice", "receipt", "credit_note", "payment_evidence",
                             "order_evidence", "other"),
           "direction": ("purchase", "sales"), "posting_kind": ("service", "stock")}
ITEM_COLUMNS = ("name", "qty", "price", "subtotal")
DETAIL_FIELDS = ("document_type", "invoice_number", "date", "date_raw", "seller_name", "seller_tax", "seller_branch",
                 "seller_addr", "buyer_name", "buyer_tax", "buyer_branch", "buyer_addr", "subtotal", "vat", "discount",
                 "total_amount", "wht_amount", "currency", "notes", "direction", "posting_kind")


def _amount(value) -> str:
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except InvalidOperation:
        number = Decimal("NaN")
    if not number.is_finite():
        raise ValueError(f"{value!r} is not an amount")
    return str(number)


def _tax_id(value) -> str:
    from pearnly_core.tax_documents.amounts import valid_tax_id

    raw = "".join(ch for ch in str(value) if ch.isdigit())
    if len(raw) != 13 or not valid_tax_id(raw):
        raise ValueError(f"{value!r} is not a valid 13-digit Thai tax ID")
    return raw


def _clean(key, value) -> str:
    if key in AMOUNTS:
        return _amount(value)
    if key in TAX_IDS:
        return _tax_id(value)
    if key in CHOICES and value not in CHOICES[key]:
        raise ValueError(f"{key} must be one of {', '.join(CHOICES[key])}")
    return str(value).strip()


def _primary(pages) -> int:
    from pearnly_cowork.contracts import history as history_contracts

    page = history_contracts.primary_page(pages)
    return next((i for i, p in enumerate(pages) if p is page), 0)


def _detail(doc) -> dict:
    pages = doc.get("pages") or []
    fields = (pages[_primary(pages)].get("fields") or {}) if pages else {}
    out = {"document": doc.get("name"), "version": doc.get("record_version"), "filename": doc.get("filename"),
           "push_status": doc.get("last_push_status"), "push_reason": _reason(doc.get("last_push_reason")),
           "push_connection": doc.get("push_connection"), "push_account_set": doc.get("push_account_set"),
           "duplicate_warning": doc.get("duplicate_warning"), "pages": len(pages), **_push_target(doc)}
    out = _account_named(out, doc.get("push_connection"), "push_account_set")
    out.update({key: fields.get(key) for key in DETAIL_FIELDS})
    out["items"] = [_filled({key: row.get(key) if key != "subtotal" else row.get("subtotal", row.get("amount"))
                             for key in ITEM_COLUMNS}) for row in (fields.get("items") or [])[:ITEMS_SHOWN]
                    if isinstance(row, dict)]
    reason = str(doc.get("last_push_reason") or "")
    if reason.startswith("document_policy_needed_"):
        out["handling_choice_needed"] = reason[len("document_policy_needed_"):]
    # 用户问「这张是不是现金」时 CC 最常查的就是详情：入账的票带上怎么写进去的（验收 F19 复审 M1），记录名不给
    written = _written_of(doc.get("last_push_status"), doc.get("last_push_record"))
    if written:
        out["written"] = written
    return _filled(out)


def _read(name):
    try:
        return storage.project(storage.read(name))
    except (frappe.DoesNotExistError, frappe.PermissionError):
        frappe.clear_messages()
        return None


def document_detail(tool_input) -> str:
    scope.require_read()
    name = str(tool_input.get("document") or "")
    doc = _read(name)
    return _json(_detail(doc) if doc else {"document": name, "status": "not_found"})


def edit_document(tool_input) -> str:
    """按用户说的改复核字段：只动主页（同复核页），其余页原样；版本号对不上不覆盖别人刚改的。"""
    scope.require("intake")
    name = str(tool_input.get("document") or "")
    changes = dict(tool_input.get("changes") or {})
    items = tool_input.get("items")
    if not changes and items is None:
        frappe.throw(_("No changes were given."))
    if not tool_input.get("version"):
        # 不带版本就等于跳过「别人刚改过不覆盖」：先查详情拿版本
        return _json({"document": name, "status": "not_saved",
                      "reason": "Read the document with cowork_document_detail first and pass its version."})
    unknown = [key for key in changes if key not in EDITABLE]
    if unknown:
        return _json({"status": "not_saved", "reason": f"Not editable: {', '.join(unknown)}", "editable": EDITABLE})
    try:
        cleaned = {key: (None if value in (None, "") else _clean(key, value)) for key, value in changes.items()}
        rows = None if items is None else [
            _filled({"name": str(row.get("name") or "").strip(),
                     **{key: _amount(row[key]) for key in ("qty", "price", "subtotal") if row.get(key) not in (None, "")}})
            for row in items if isinstance(row, dict)]
    except ValueError as error:
        return _json({"status": "not_saved", "reason": str(error)})
    doc = _read(name)
    if not doc:
        return _json({"document": name, "status": "not_found"})
    pages = doc.get("pages") or []
    if not pages:
        return _json({"document": name, "status": "not_saved", "reason": "This document has no recognized page."})
    index = _primary(pages)
    fields = dict(pages[index].get("fields") or {})
    for key, value in cleaned.items():
        target = "date_raw" if key == "date" else key
        if value is None:
            fields.pop(target, None)
            if key == "date":
                fields.pop("date", None)
        else:
            fields[target] = value
    if rows is not None:
        fields["items"] = rows
    pages[index] = {**pages[index], "fields": fields}
    try:
        saved = storage.update_pages(name, pages, str(tool_input["version"]))
        frappe.db.commit()
    except (frappe.ValidationError, frappe.PermissionError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return _json({"document": name, "status": "not_saved", "reason": str(error)[:300]})
    out = _detail(saved)
    return _json({"status": "saved", **out, **_entered_note(out.get("push_status"))})


ORIGINAL_PAGES_MAX = 3


def document_original(tool_input) -> str:
    """同复核页右侧的原件：把单据的原件页图放进对话（智能体能看着原件核对字段，用户能点开看）。
    取图与网页同一入口（`files.service.page_source` 判权限 → 存好的预览图或现画一页），并照网页记访问日志。"""
    import requests
    from frappe.core.doctype.access_log.access_log import make_access_log
    from pearnly_core.assistant import files as chat_files

    from pearnly_cowork.files import access as file_access
    from pearnly_cowork.files import service as file_service
    from pearnly_core.originals import checks as originals

    name = str(tool_input.get("document") or "")
    try:
        doc = storage.read(name)
        pages = sorted(file_access.page_indices(doc.source_page_indices))
        wanted = [int(tool_input["page"])] if tool_input.get("page") else pages[:ORIGINAL_PAGES_MAX]
        shown = []
        for number in wanted:
            source, number = file_service.page_source(name, number)
            link = file_service.preview_link(source, number)
            data = requests.get(link, timeout=30).content if link else file_service.render_page(source, number)
            make_access_log(doctype="Cowork OCR History", document=name, file_type="page_png")
            kind = originals.preview_type(data)
            label = re.sub(r"[^\w.-]+", "-", str(doc.invoice_no or name)).strip("-") or "document"
            shown.append(chat_files.add_to_conversation(f"{label}-p{number}.{'jpg' if kind == 'image/jpeg' else 'png'}",
                                                        data, kind))
        frappe.db.commit()
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError, requests.RequestException) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return _json({"document": name, "status": "not_available", "reason": str(error)[:300]})
    return _json(_added_to_chat({"document": name, "pages_total": len(pages), "files": shown}, shown))


def choose_document_policy(tool_input) -> str:
    from pearnly_cowork.history import document_policy

    scope.require("intake")
    name = str(tool_input.get("document") or "")
    try:
        result = document_policy.choose(name, tool_input.get("value"))
        frappe.db.commit()
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return _json({"document": name, "status": "not_saved", "reason": str(error)[:300]})
    return _json({"status": "saved", **result})


# ---------------- 推送前要人选的项、结果不明的确认（同复核页） ----------------

CHOICE_OPTIONS_SHOWN = 30


def _push_target_of(doc, tool_input):
    connection = tool_input.get("connection") or doc.push_connection
    account_set = tool_input.get("account_set_key") or doc.push_account_set or None
    return connection, account_set


def push_choices(tool_input) -> str:
    """Express 推送前这张单据在这份账套里要人选一次的（对方档 / 商品 / 费用科目 / 付款 / 抵进项 / 部门）与可选值。"""
    scope.require("intake")
    name = str(tool_input.get("document") or "")
    try:
        doc = storage.read(name)
    except (frappe.DoesNotExistError, frappe.PermissionError):
        frappe.clear_messages()
        return _json({"document": name, "status": "not_found"})
    connection, account_set = _push_target_of(doc, tool_input)
    if not connection:
        return _json({"document": name, "status": "failed", "reason": "no_push_target"})
    try:
        view = _choices_waited(name, connection, account_set, tool_input.get("posting_kind") or doc.posting_kind,
                               str(tool_input.get("hint") or "").strip()[:120] or None)
    except (frappe.ValidationError, frappe.PermissionError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return _json({"document": name, "status": "failed", "reason": str(error)[:300]})
    wait = view.get("wait")
    if wait:
        return _json(_account_named({"document": name, "connection": connection,
                                     "account_set": view.get("account_set") or account_set,
                                     "status": wait["state"], "note": _masters_note(wait["state"])},
                                    connection, "account_set"))
    search = str(tool_input.get("search") or "").strip().casefold()
    options = {}
    for kind, rows in (view.get("options") or {}).items():
        found = [r for r in rows if not search or search in f"{r.get('code')} {r.get('name')}".casefold()]
        options[kind] = {"total": len(found), "shown": found[:CHOICE_OPTIONS_SHOWN]}
    out = _account_named({"document": name, "connection": connection,
                          "account_set": view.get("account_set") or account_set,
                          "needed": view.get("needed") or [], "options": options}, connection, "account_set")
    if view.get("fixed"):
        # 来自定时任务、推往该任务账套的票：任务默认处理已定的类别（推送服务按任务取，不再问、不记进账套）
        out["task_defaults"] = view["fixed"]
    return _json({**out, **_entered_note(getattr(doc, "last_push_status", None))})


def save_push_choices(tool_input) -> str:
    scope.require("intake")
    name = str(tool_input.get("document") or "")
    try:
        doc = storage.read(name)
    except (frappe.DoesNotExistError, frappe.PermissionError):
        frappe.clear_messages()
        return _json({"document": name, "status": "not_found"})
    connection, account_set = _push_target_of(doc, tool_input)
    try:
        result = application_api.save_choices(name, connection, account_set, json.dumps(tool_input.get("items") or []))
        frappe.db.commit()
    except (frappe.ValidationError, frappe.PermissionError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return _json({"document": name, "status": "not_saved", "reason": str(error)[:300]})
    return _json({"document": name, "status": "saved", "result": result, **_entered_note(getattr(doc, "last_push_status", None))})


def resolve_push_result(tool_input) -> str:
    """推送结果不明：用户在 ERP 里核对后告诉 CC 已入账（可带 ERP 单号）或未入账（之后可重推）。"""
    scope.require("intake")
    name = str(tool_input.get("document") or "")
    try:
        doc = storage.read(name)
    except (frappe.DoesNotExistError, frappe.PermissionError):
        frappe.clear_messages()
        return _json({"document": name, "status": "not_found"})
    if not doc.last_push_record:
        return _json({"document": name, "status": "not_saved", "reason": "This document has no push to confirm."})
    try:
        result = application_api.resolve_unknown(doc.last_push_record, 1 if tool_input.get("entered") else 0,
                                                 str(tool_input.get("docnum") or "") or None)
        frappe.db.commit()
    except (frappe.ValidationError, frappe.PermissionError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return _json({"document": name, "status": "not_saved", "reason": str(error)[:300]})
    return _json({"document": name, "status": "saved", "push": _filled(
        {key: result.get(key) for key in ("status", "docnum", "reason")} if isinstance(result, dict) else {})})


# ---------------- 删除记录、导出复核工作簿（同录入工作台列表） ----------------

DELETE_MAX = 500
EXPORT_MAX = 100
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def delete_documents(tool_input) -> str:
    """同列表「删除」：真删记录与原件，已推送的跳过（由删除服务判断）。"""
    from pearnly_cowork.history import removal

    scope.require("intake")
    names = [str(n) for n in (tool_input.get("documents") or [])][:DELETE_MAX]
    if not names:
        frappe.throw(_("No documents were given."))
    try:
        result = removal.delete(json.dumps(names))
        frappe.db.commit()
    except (frappe.ValidationError, frappe.PermissionError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return _json({"status": "not_deleted", "reason": str(error)[:300]})
    return _json(result)


def export_review_workbook(tool_input) -> str:
    """同列表「导出复核工作簿」（仅识别的单据给会计在 Excel 里核对）；文件放进对话，智能体再放进结果目录。"""
    from pearnly_core.assistant import files as chat_files

    from pearnly_cowork.roundtrip import service as roundtrip
    from pearnly_cowork.roundtrip.export import ExportError

    scope.require_read()
    names = list(dict.fromkeys(str(n) for n in (tool_input.get("documents") or [])))[:EXPORT_MAX]
    if not names:
        frappe.throw(_("No documents were given."))
    try:
        content = roundtrip.export(json.dumps(names))
    except ExportError as error:
        return _json({"status": "not_exported", "reason": _(error.code)})
    except (frappe.ValidationError, frappe.PermissionError) as error:
        frappe.clear_messages()
        return _json({"status": "not_exported", "reason": str(error)[:300]})
    # 每次导出一个新名字：同名会盖掉上一份，对话里就不再出新的文件卡片
    # 站点时区的时间（容器里的 time.strftime 是 UTC，2026-10-11 测试：凌晨 2 点导出的文件名写成前一天 19 点）
    stamp = frappe.utils.now_datetime().strftime("%Y%m%d-%H%M%S")
    path = chat_files.add_to_conversation(f"cowork-review-{stamp}.xlsx", content, XLSX)
    return _json(_added_to_chat({"status": "exported", "documents": len(names), "file": path}, path))


# ---------------- 对账：银行对账、收入对账、销项税核查（同一套 recon_center，按 kind） ----------------

RECON_KINDS = ("bank", "income", "vat")
RECON_WAIT_SECONDS = 90
RECON_DEDUP_SECONDS = 600
RECON_STILL_RUNNING = ("Still running in the background. Tell the user it is still being reconciled and that you "
                       "will give the result when they ask; do not wait with sleep.")
RECON_ROWS_SHOWN = 30


def _recon_kind(tool_input) -> str:
    kind = str(tool_input.get("kind") or "")
    if kind not in RECON_KINDS:
        frappe.throw(_("reconciliation.type_invalid"))
    return kind


def _recon_status(job) -> dict:
    from pearnly_cowork.recon_center import jobs

    state = jobs.status(job)
    files = state.get("files") or []
    return _filled({"job": job, "state": state.get("state"), "stage": state.get("stage"), "task": state.get("task"),
                    "error": state.get("error"), "title": state.get("title"),
                    "pages": sum(int(f.get("pages") or 0) for f in files) or None,
                    "pages_done": sum(int(f.get("done") or 0) for f in files) or None})


def _recon_wait(job) -> dict:
    """等到对账结束或等满 RECON_WAIT_SECONDS；每次都重新读（后台进程刚写的状态）。"""
    from pearnly_core.assistant import toolrun

    state = {}
    deadline = time.monotonic() + RECON_WAIT_SECONDS
    while True:
        toolrun.fresh()
        state = _recon_status(job)
        if state.get("state") not in ("queued", "running") or time.monotonic() >= deadline:
            break
        time.sleep(4)
    if state.get("state") in ("queued", "running"):
        state["note"] = RECON_STILL_RUNNING
    return state


def _recon_started_key(kind, sides) -> str:
    conversation = (getattr(frappe.local, "pearnly_assistant", None) or {}).get("session_id") or ""
    files = "|".join(",".join(sides.get(side) or []) for side in ("left", "right"))
    return f"pearnly_assistant:recon:{frappe.local.site}:{conversation}:{kind}:{files}"


def recon_start(tool_input) -> str:
    """同对账页「新建」：两口文件取自对话附件（银行：账单 / 总账；收入：总账 / 销项税报告；销项核查：报告 / 发票）。
    同一对话同一组附件只开一套（模型一次发两个同样的调用时，第二个接着等第一套，不重复对账）。"""
    from pearnly_core.assistant import files as chat_files

    from pearnly_cowork.recon_center import jobs

    kind = _recon_kind(tool_input)
    ids = {side: [str(f) for f in tool_input.get(side + "_files") or []] for side in ("left", "right")}
    key = _recon_started_key(kind, ids)
    if not frappe.cache.set(key, "", nx=True, ex=RECON_DEDUP_SECONDS):
        job = ""
        for _attempt in range(30):  # 第一个调用还在存文件、排任务：等它记下任务号
            job = (frappe.cache.get(key) or b"").decode()
            if job:
                break
            time.sleep(1)
        if job:
            return _json(_recon_wait(job))
    sides = {}
    for side in ("left", "right"):
        sides[side] = []
        for file_id in ids[side]:
            try:
                sides[side].append(chat_files.attachment(file_id))
            except frappe.ValidationError:
                frappe.clear_messages()
                frappe.cache.delete(key)
                return _json({"status": "not_started", "reason": f"attachment {file_id} is not in this conversation",
                              "attachments_in_this_chat": chat_files.attachments()})
    try:
        started = jobs.start(kind, str(tool_input.get("title") or ""), sides, {"lang": frappe.local.lang or "th"})
        frappe.db.commit()
    except (frappe.ValidationError, frappe.PermissionError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        frappe.cache.delete(key)
        return _json({"status": "not_started", "reason": str(error)[:300]})
    frappe.cache.set(key, started["job"], ex=RECON_DEDUP_SECONDS)
    return _json(_recon_wait(started["job"]))


def recon_status(tool_input) -> str:
    try:
        return _json(_recon_wait(str(tool_input.get("job") or "")))
    except frappe.DoesNotExistError:
        frappe.clear_messages()
        return _json({"job": tool_input.get("job"), "state": "not_found"})


def recon_list(tool_input) -> str:
    from pearnly_cowork.recon_center import api as recon_api

    kind = _recon_kind(tool_input)
    # 页面列表接口回 {"tasks", "total"}（2026-10-10 测试：读成 data，三种对账都列成空的）
    page = recon_api.tasks(kind, 0, max(1, min(int(tool_input.get("limit") or 20), LIST_MAX))) or {}
    rows = page.get("tasks") if isinstance(page, dict) else page
    return _json({"kind": kind, "total": page.get("total") if isinstance(page, dict) else None,
                  "tasks": [_filled(dict(r)) for r in rows or []]})


def recon_result(tool_input) -> str:
    """结果页的要点：标题、状态、系统提示、要定一次的问题；银行 / 收入另给没对上的行（最多 30 行）。"""
    from pearnly_cowork.recon_center import api as recon_api
    from pearnly_cowork.recon_center import sheets

    kind = _recon_kind(tool_input)
    task = str(tool_input.get("task") or "")
    try:
        view = sheets.load(kind, task, frappe.local.lang or "th")
    except (frappe.DoesNotExistError, frappe.PermissionError):
        frappe.clear_messages()
        return _json({"task": task, "status": "not_found"})
    if view.get("moved"):
        return _json({"task": task, "moved_to": view["moved"]})
    head = view.get("task") or {}
    out = {key: head.get(key) for key in ("name", "title", "status", "notes", "questions")}
    if kind in ("bank", "income"):
        # 汇总用对账自己存的数（与结果页同一份）；没对上的行翻完全部明细再数（一页最多 200 行，2026-10-10 测试只看了
        # 第一页：368 笔流水说成 200 行、未匹配说成 0）。
        rows, total, summary = [], None, None
        while total is None or len(rows) < min(total, RECON_ROWS_READ):
            page = recon_api.detail(kind, task, len(rows), RECON_PAGE) or {}
            total, summary = int(page.get("total") or 0), page.get("summary")
            rows += page.get("rows") or []
            if not page.get("rows"):
                break
        unmatched = [r for r in rows if not _recon_row_matched(kind, r)]
        out.update({"summary": summary, "rows_total": total, "unmatched_total": len(unmatched),
                    "unmatched": [_recon_row(r) for r in unmatched[:RECON_ROWS_SHOWN]]})
        if kind == "bank":
            out["matched_by_kind"] = _recon_matched_by_kind(rows)
    else:
        # 销项核查：逐张对照由存下的两边输入现算（同结果页、导出）；只给有问题的（2026-10-10 测试：没有明细，
        # 智能体只好先导出工作簿再读）
        from pearnly_cowork.reconciliation import vat_check_service

        doc = vat_check_service.task(task)
        _content, summary = vat_check_service.compute(vat_check_service.stored_inputs(doc),
                                                       frappe.local.lang or "th")
        rows = summary.get("rows") or []
        issues = [r for r in rows if r.get("status_key") not in VAT_FINE]
        counted = {}
        for r in issues:
            counted[r.get("status") or r.get("status_key")] = counted.get(r.get("status") or r.get("status_key"), 0) + 1
        out.update({"summary": {key: summary.get(key) for key in ("n_total", "n_ok", "n_diff", "n_void",
                                                                    "diff_amount_total")},
                    "problems_by_type": counted,
                    "rows_total": len(rows), "unmatched_total": len(issues),
                    "unmatched": [_filled({"status": r.get("status"), "customer": r.get("customer"),
                                           "invoice_no": r.get("invoice_no"), "amount_invoice": r.get("amount_inv"),
                                           "amount_report": r.get("amount_rep"),
                                           "differences": _filled(dict(r.get("dims") or {}))})
                                  for r in issues[:RECON_ROWS_SHOWN]]})
    return _json(_filled(out))


# 销项核查里不算问题的：一致、现金单、作废（含报告里作废、没有发票的）
VAT_FINE = ("st_ok", "st_cash", "st_void", "st_void_no_inv")


RECON_PAGE = 200
RECON_ROWS_READ = 5000
RECON_ROW_SKIP = ("name", "owner", "creation", "modified", "modified_by", "docstatus", "idx", "record_id", "workspace",
                  "task", "row_index", "parent", "parentfield", "parenttype")


def _recon_row_matched(kind, row) -> bool:
    """银行：配上了才算；收入：总账里有、且金额没差额。"""
    if kind == "bank":
        return str(row.get("match_status") or "") == "matched"
    return bool(row.get("has_gl_match")) and not Decimal(str(row.get("diff") or 0))


RECON_SIDES = (("bank_deposits", "stmt_deposit"), ("bank_withdrawals", "stmt_withdrawal"),
               ("gl_debits", "gl_debit"), ("gl_credits", "gl_credit"))


def _recon_matched_by_kind(rows) -> list:
    """银行对账配上的行按匹配依据（L1–L6，同结果页「匹配依据」栏）分类：每类几组、几行、两边各多少钱。
    两边合计对不上的数，多半就是某一类组里另一边的金额：2026-10-11 TTB 总账贷方比银行支出多 5,306.04，是 L6 组里
    总账实际记的刷卡手续费；只给没配上的行时，智能体看不到这一层，猜成冲销或转账。"""
    from pearnly_cowork.reconciliation.export import labels

    kinds = {}
    for row in rows:
        if str(row.get("match_status") or "") != "matched":
            continue
        layer = row.get("match_layer")
        item = kinds.setdefault(layer, {"groups": set(), "lines": 0, **{name: Decimal(0) for name, _f in RECON_SIDES}})
        item["lines"] += 1
        if row.get("group_no"):
            item["groups"].add(row["group_no"])
        for name, field in RECON_SIDES:
            item[name] += Decimal(str(row.get(field) or 0))
    out = []
    for layer in sorted(kinds, key=lambda value: (value is None, value or 0)):
        item = kinds[layer]
        out.append({"kind": labels.layer_label(layer, frappe.local.lang or "th") or str(layer or ""),
                    "groups": len(item["groups"]), "lines": item["lines"],
                    **{name: f"{item[name]:.2f}" for name, _f in RECON_SIDES if item[name]}})
    return out


def _recon_row(row) -> dict:
    return _filled({key: value for key, value in dict(row).items() if key not in RECON_ROW_SKIP})


def recon_answer(tool_input) -> str:
    from pearnly_cowork.recon_center import presets as recon_presets

    kind = _recon_kind(tool_input)
    try:
        result = recon_presets.answer(kind, str(tool_input.get("task") or ""), str(tool_input.get("rule") or ""),
                                      str(tool_input.get("value") or ""), tool_input.get("subject") or None,
                                      frappe.local.lang or "th")
        frappe.db.commit()
    except (frappe.ValidationError, frappe.PermissionError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return _json({"status": "not_saved", "reason": str(error)[:300]})
    if isinstance(result, dict) and result.get("job"):
        return _json({"status": "saved", "rerun_job": result["job"],
                      "note": "The reconciliation runs again from the original files; check it with cowork_recon_status."})
    head = (result or {}).get("task") or {}
    return _json({"status": "saved", "task": head.get("name"), "questions": head.get("questions"),
                  "notes": head.get("notes")})


def recon_presets(tool_input) -> str:
    from pearnly_cowork.recon_center import presets as recon_presets

    kind = _recon_kind(tool_input)
    action = tool_input.get("action") or "list"
    try:
        if action == "change":
            rows = recon_presets.change(kind, tool_input.get("scope_key"), tool_input.get("rule"), tool_input.get("value"))
        elif action == "remove":
            rows = recon_presets.remove(kind, tool_input.get("scope_key"), tool_input.get("rule"))
        else:
            rows = recon_presets.listing(kind)
        frappe.db.commit()
    except (frappe.ValidationError, frappe.PermissionError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return _json({"status": "not_saved", "reason": str(error)[:300]})
    return _json({"kind": kind, "presets": rows}, local=("set_at",))  # 记下的时刻：站点时区（服务转成了字符串）


def recon_export(tool_input) -> str:
    """结果工作簿（须已完成），放进对话、结果面板下载。"""
    from pearnly_core.assistant import files as chat_files

    from pearnly_cowork.reconciliation import bank_recon_service, income_recon_service, vat_check_service

    kind = _recon_kind(tool_input)
    service = {"bank": bank_recon_service, "income": income_recon_service, "vat": vat_check_service}[kind]
    task = str(tool_input.get("task") or "")
    try:
        built = service.export(task, frappe.local.lang or "th")
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
        frappe.clear_messages()
        return _json({"status": "not_exported", "reason": str(error)[:300]})
    if isinstance(built, dict):
        filename, content = built.get("filename"), built.get("content")
    elif isinstance(built, (tuple, list)):
        filename, content = built[0], built[1]
    else:
        filename, content = None, built
    filename = str(filename or f"recon-{kind}-{task}.xlsx")
    path = chat_files.add_to_conversation(filename, content, XLSX)
    return _json(_added_to_chat({"status": "exported", "file": path}, path))


def recon_delete(tool_input) -> str:
    from pearnly_cowork.recon_center import api as recon_api

    kind = _recon_kind(tool_input)
    names = [str(n) for n in (tool_input.get("tasks") or [])]
    if not names:
        frappe.throw(_("No tasks were given."))
    try:
        result = recon_api.delete(kind, json.dumps(names))
        frappe.db.commit()
    except (frappe.ValidationError, frappe.PermissionError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return _json({"status": "not_deleted", "reason": str(error)[:300]})
    return _json(result if isinstance(result, dict) else {"deleted": result})


# ---------------- 报税草稿：ภ.พ.30 与进项核查、ภ.ง.ด.3 / ภ.ง.ด.53（`tax.service`，确定性、零费用） ----------------

TAX_ISSUES_SHOWN = 30
TAX_LINES_SHOWN = 40
TAX_ISSUE_KEYS = ("code", "level", "side", "docnum", "refnum", "tax_id", "name", "amount", "vat", "date", "period",
                  "history", "detail")
TAX_LINE_KEYS = ("row", "source", "payee_tax_id", "payee_name", "date", "date_source", "income_type", "income_text",
                 "rate", "base", "tax", "condition", "form", "category", "edited")
TAX_LEVELS = {"error": 0, "warn": 1, "info": 2}


def _tax_call(fn, *args, refused="not_drafted"):
    """调报税服务；业务拒绝回 ``refused`` 与原因（已翻译），不抛给模型。"""
    try:
        result = fn(*args)
        frappe.db.commit()
        return result
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return {"status": refused, "reason": str(error)[:300]}


def _tax_file(summary, wanted) -> str:
    """草稿工作簿放进对话（结果面板下载）；wanted 为 False 时不出（反复改的时候）。是工具产出（``origin="tool"``）：
    对账等工具能直接取，识别工具不收。"""
    if wanted is False:
        return ""
    from pearnly_core.assistant import files as chat_files

    from pearnly_cowork.tax import service as tax_service

    filename, content = tax_service.workbook(summary, frappe.local.lang or "th")
    return chat_files.add_to_conversation(filename, content, XLSX, origin="tool")


def _tax_issues(issues) -> list:
    ordered = sorted(issues or [], key=lambda item: TAX_LEVELS.get(item.get("level"), 3))
    return [_filled({key: item.get(key) for key in TAX_ISSUE_KEYS}) for item in ordered[:TAX_ISSUES_SHOWN]]


def _tax_line(line) -> dict:
    out = _filled({key: line.get(key) for key in TAX_LINE_KEYS})
    if line.get("issues"):
        out["issues"] = [item["code"] for item in line["issues"]]
        related = [ref for item in line["issues"] for ref in item.get("candidates") or []]
        if related:
            out["check_with"] = list(dict.fromkeys(related))
    return out


def _vat_boxes(boxes) -> list:
    """ภ.พ.30 各栏连同表上的栏名（泰文照表样、同工作簿；另附英文）：只给数字时模型把第 9 栏「ภาษีที่ชำระเกินเดือนนี้」说成了
    应纳税额（2026-10-13 测试机）。空着的栏（13–16 只在逾期 / 补报时填）不给。"""
    out = []
    for key in sorted(boxes or {}, key=lambda value: int(value) if str(value).isdigit() else 99):
        if boxes[key] in (None, ""):
            continue
        message = f"cowork.tax.pp30.box.{key}"
        out.append({"box": str(key), "th": _(message, lang="th"), "en": _(message, lang="en"), "amount": boxes[key]})
    return out


def vat_draft(tool_input) -> str:
    from pearnly_cowork.tax import service as tax_service

    options = {key: tool_input.get(key) for key in ("exempt_sales", "excess_brought_forward", "zero_vat_as")
               if tool_input.get(key) not in (None, "")}
    summary = _tax_call(tax_service.vat_draft, tool_input.get("connection"), tool_input.get("account_set_key"),
                        tool_input.get("period"), options)
    if summary.get("status") == "readback_needed":
        return _json({**_account_named(summary, summary.get("connection")),
                      "note": "There is no Express readback of this account set and month yet. Run "
                              "cowork_express_readback for it first, then call this again."})
    if summary.get("status") != "drafted":
        return _json(summary)
    # readback.read_at 是回读服务按 UTC 写的「…Z」
    out = {key: summary.get(key) for key in ("draft", "period", "company", "readback", "outcome", "options",
                                             "sales", "purchases", "claimable_if_fixed", "previous_excess",
                                             "deadlines", "documents_checked", "issues_by_code")}
    out.update({"boxes": _vat_boxes(summary.get("boxes")),
                "issues_total": len(summary.get("issues") or []), "issues": _tax_issues(summary.get("issues")),
                "unreadable_rows": (summary.get("unreadable_rows") or [])[:10],
                "file": _tax_file(summary, tool_input.get("workbook"))})
    return _json(_added_to_chat(_filled(out), out["file"]), utc=("read_at",))


def wht_draft(tool_input) -> str:
    from pearnly_cowork.tax import service as tax_service

    summary = _tax_call(tax_service.wht_draft, tool_input.get("company_tax_id"), tool_input.get("period"),
                        tool_input.get("edits") or [], str(tool_input.get("company_name") or ""),
                        str(tool_input.get("company_branch") or ""))
    if summary.get("status") != "drafted":
        return _json(summary)
    forms = {}
    for form, data in (summary.get("forms") or {}).items():
        lines = data.get("lines") or []
        forms[form] = {"payees": data.get("payees"), "sheets": data.get("sheets"), "base": data.get("base"),
                       "tax": data.get("tax"), "lines_total": len(lines),
                       "lines": [_tax_line(line) for line in lines[:TAX_LINES_SHOWN]]}
    out = {key: summary.get(key) for key in ("draft", "period", "company", "totals", "questions", "issues_by_code",
                                             "deadlines", "edits_rejected", "truncated")}
    excluded = summary.get("excluded") or []
    by_reason = {}
    for item in excluded:
        by_reason[item["reason"]] = by_reason.get(item["reason"], 0) + 1
    out.update({"forms": forms, "review": [_tax_line(row) for row in (summary.get("review") or [])[:TAX_LINES_SHOWN]],
                "suggestions": [_tax_line(row) for row in (summary.get("suggestions") or [])[:TAX_LINES_SHOWN]],
                "notes": (summary.get("notes") or [])[:TAX_ISSUES_SHOWN],
                "excluded_total": len(excluded), "excluded_by_reason": by_reason,
                "excluded": excluded[:TAX_LINES_SHOWN],
                "file": _tax_file(summary, tool_input.get("workbook"))})
    return _json(_added_to_chat(_filled(out), out["file"]))


def wht_answer(tool_input) -> str:
    from pearnly_cowork.tax import service as tax_service

    action = tool_input.get("action") or "set"
    if action == "list":
        found = _tax_call(tax_service.remembered)
        # 记下的时刻：站点时区（服务转成了字符串）
        return _json(found if isinstance(found, dict) else {"remembered": found}, local=("set_at",))
    if action == "remove":
        return _json(_tax_call(tax_service.forget, tool_input.get("payee_tax_id") or "",
                               tool_input.get("category") or ""))
    result = _tax_call(tax_service.answer, tool_input.get("answers") or [], str(tool_input.get("draft") or ""))
    if result.get("status") == "saved":
        result["note"] = "Call cowork_wht_draft again to apply."
    return _json(result)


def tax_draft_delete(tool_input) -> str:
    from pearnly_cowork.tax import service as tax_service

    raw = tool_input.get("drafts") or []
    drafts = [raw] if isinstance(raw, str) else [str(name) for name in raw]
    return _json(_tax_call(tax_service.delete, drafts, refused="not_deleted"))


# ---------------- 库存盘点（同盘点页，`stocktake.desk_api`） ----------------

STOCK_ROWS_SHOWN = 40


def _call(fn, *args, **kwargs) -> dict:
    """调页面同一个接口；业务拒绝回 not_saved 与原因（已翻译），不抛给模型。"""
    try:
        result = fn(*args, **kwargs)
        frappe.db.commit()
        return result if isinstance(result, dict) else {"result": result}
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return {"status": "not_saved", "reason": str(error)[:300]}


def stocktake_list(tool_input) -> str:
    from pearnly_cowork.stocktake import desk_api

    # 盘点单的建立时刻（列表里叫 creation）是盘点服务按 UTC 记的
    return _json(_call(desk_api.api_list_stock_counts, 0, max(1, min(int(tool_input.get("limit") or 20), 100))),
                 utc=("creation",))


def stocktake_template(_input) -> str:
    """建盘点用的 Excel 模板（产品代码、名称、单位、账面数，可带实盘数），放进对话给用户下载填写。"""
    from pearnly_core.assistant import files as chat_files

    from pearnly_cowork.stocktake import upload

    path = chat_files.add_to_conversation("stocktake-template.xlsx", upload.template(), XLSX)
    return _json(_added_to_chat({"status": "ready", "file": path}, path))


def stocktake_create(tool_input) -> str:
    """同盘点页「新建」：用对话里发来的 Excel（账面数，可带实盘数）建一张盘点单。"""
    from pearnly_core.assistant import files as chat_files

    from pearnly_cowork.stocktake import upload

    try:
        _name, content = chat_files.attachment(str(tool_input.get("file") or ""))
        file_id = chat_files.resolve(str(tool_input.get("file") or ""))
    except frappe.ValidationError:
        frappe.clear_messages()
        return _json({"status": "not_saved", "reason": "This attachment is not in this conversation.",
                      "attachments_in_this_chat": chat_files.attachments()})
    # 盘点单号要 UUID：按对话 + 附件算一个固定的，同一份表重复建只得到同一张（页面接口的幂等）
    conversation = (getattr(frappe.local, "pearnly_assistant", None) or {}).get("session_id") or ""
    key = str(uuid.uuid5(uuid.NAMESPACE_URL, f"pearnly-assistant:{conversation}:{file_id}"))
    title = (str(tool_input.get("title") or "").strip() or _name.rsplit(".", 1)[0])[:120]
    return _json(_call(upload.create, title, key, content))


def stocktake_report(tool_input) -> str:
    """盘点结果：各项账面数、实盘数、差异；默认只列有差异或还没盘的。"""
    from pearnly_cowork.stocktake import desk_api

    count = str(tool_input.get("count") or "")
    data = _call(desk_api.api_stock_count_review, count)
    if data.get("status") == "not_saved":
        return _json(data)
    items = data.get("items") or []
    if not tool_input.get("all_items"):
        items = [r for r in items if not r.get("counted") or r.get("difference")]
    keep = ("code", "product_name", "unit", "warehouse", "location", "book_qty", "counted_qty", "difference")
    rows = [{k: r.get(k) for k in keep if r.get(k) not in (None, "")} | {"counted": bool(r.get("counted"))}
            for r in items[:STOCK_ROWS_SHOWN]]
    head = data.get("task") or {}
    return _json({"count": count, "title": head.get("title"), "status": head.get("status"),
                  "items_total": head.get("item_total"), "counted_total": head.get("counted_total"),
                  "difference_total": head.get("difference_total"), "listed": len(items), "items": rows})


def stocktake_count(tool_input) -> str:
    """按产品代码或条码记一笔实盘数（同扫码录入：同一产品多笔累加）；recount 先清掉这个产品已记的数再记。"""
    from pearnly_cowork.stocktake import desk_api

    count = str(tool_input.get("count") or "")
    found = _call(desk_api.api_stock_count_lookup, count, str(tool_input.get("code") or ""))
    if found.get("status") == "not_saved":
        return _json(found)
    item = found["product"]["name"]
    # 仓库必填（同页面录入框「实际仓库」）：没说就用这一项账面上的仓库、库位（页面也是默认带出账面位置）
    book = (found.get("rows") or [{}])[0]
    warehouse = str(tool_input.get("warehouse") or book.get("warehouse") or "").strip()
    location = str(tool_input.get("location") or book.get("location") or "").strip()
    if tool_input.get("recount"):
        cleared = _call(desk_api.api_stock_count_recount, count, item)
        if cleared.get("status") == "not_saved" or tool_input.get("qty") is None:
            return _json(cleared)
    if tool_input.get("qty") is None:
        return _json({"status": "not_saved", "reason": "qty is required."})
    return _json(_call(desk_api.api_stock_count_add_entry, count, tool_input.get("qty"), item=item,
                       warehouse=warehouse, location=location, request_id=str(uuid.uuid4())))


def stocktake_close(tool_input) -> str:
    from pearnly_cowork.stocktake import desk_api

    return _json(_call(desk_api.api_stock_count_close, str(tool_input.get("count") or "")))


def stocktake_export(tool_input) -> str:
    from pearnly_core.assistant import files as chat_files

    from pearnly_cowork.stocktake import service as stocktake_service

    count = str(tool_input.get("count") or "")
    try:
        content = stocktake_service.export(count)
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
        frappe.clear_messages()
        return _json({"status": "not_exported", "reason": str(error)[:300]})
    # 文件名用盘点名称 + 日期（不用内部单号，2026-10-10 测试）；文件名里不能有的字符换成「-」
    title = frappe.db.get_value("Cowork Stocktake Task", count, "title") or "stocktake"
    name = re.sub(r'[\\/:*?"<>|]+', "-", str(title)).strip(" .-")[:80] or "stocktake"
    stamp = frappe.utils.now_datetime().strftime("%Y%m%d")
    path = chat_files.add_to_conversation(f"{name}-{stamp}.xlsx", content, XLSX)
    return _json(_added_to_chat({"status": "exported", "file": path}, path))


def stocktake_delete(tool_input) -> str:
    from pearnly_cowork.stocktake import desk_api

    names = [str(n) for n in (tool_input.get("counts") or [])]
    if not names:
        frappe.throw(_("No tasks were given."))
    results = {name: _call(desk_api.api_stock_count_delete, name) for name in names}
    return _json({"deleted": [n for n, r in results.items() if r.get("status") != "not_saved"],
                  "not_deleted": {n: r["reason"] for n, r in results.items() if r.get("status") == "not_saved"}})


# ---------------- 任务管理（团队看板，同任务页，`work.api`） ----------------

WORK_CARDS_SHOWN = 100


def _card_brief(card, lists) -> dict:
    return _filled({"card": card.get("name"), "title": card.get("title"), "list": lists.get(card.get("board_list")),
                    "due": card.get("due_date"), "done": bool(card.get("completed")),
                    "assignees": card.get("assignees"), "labels": card.get("labels")})


def work_boards(_input) -> str:
    from pearnly_cowork.work import api as work_api

    return _json(_call(work_api.boards_list), utc=("archived_at",))  # 看板归档时刻按 UTC 存


def work_board(tool_input) -> str:
    """一块看板（不给就是默认看板）：各列表与卡片要点（标题、列表、截止、完成、负责人、标签）。"""
    from pearnly_cowork.work import api as work_api

    data = _call(work_api.board, tool_input.get("board") or None)
    if data.get("status") == "not_saved":
        return _json(data)
    lists = {row.get("name"): row.get("title") for row in data.get("lists") or []}
    cards = [c for c in data.get("cards") or [] if not c.get("is_template") and not c.get("archived")]
    if tool_input.get("mine"):
        cards = [c for c in cards if frappe.session.user in (c.get("assignees") or [])]
    return _json({"board": data.get("board"),
                  "lists": [_filled({"list": row.get("name"), "title": row.get("title"),
                                     "done_list": bool(row.get("is_done_list"))}) for row in data.get("lists") or []],
                  "cards_total": len(cards), "cards": [_card_brief(c, lists) for c in cards[:WORK_CARDS_SHOWN]]},
                 utc=("archived_at",))


def work_card(tool_input) -> str:
    from pearnly_cowork.work import api as work_api

    return _json(_call(work_api.card, str(tool_input.get("card") or "")))


def work_create_card(tool_input) -> str:
    from pearnly_cowork.work import api as work_api

    return _json(_call(work_api.create, str(tool_input.get("title") or ""), str(tool_input.get("list") or "")))


def work_update_card(tool_input) -> str:
    """改卡片（标题、描述、开始 / 截止、提醒、重复、负责人、标签、清单、完成）：带上查卡片时的 modified，别人刚改过就不覆盖。"""
    from pearnly_cowork.work import api as work_api

    return _json(_call(work_api.update, str(tool_input.get("card") or ""), str(tool_input.get("modified") or ""),
                       json.dumps(tool_input.get("values") or {})))


def work_move_card(tool_input) -> str:
    from pearnly_cowork.work import api as work_api

    if tool_input.get("toggle_done"):
        return _json(_call(work_api.toggle_done, str(tool_input.get("card") or "")))
    return _json(_call(work_api.move, str(tool_input.get("card") or ""), str(tool_input.get("list") or "")))


def work_comment(tool_input) -> str:
    from pearnly_cowork.work import api as work_api

    return _json(_call(work_api.comment, str(tool_input.get("card") or ""), str(tool_input.get("content") or "")))


def work_search(tool_input) -> str:
    from pearnly_cowork.work import api as work_api

    return _json(_call(work_api.search, str(tool_input.get("keyword") or ""), tool_input.get("board") or None))


# ---------------- 文件夹定时任务 ----------------

# 一次保存、启停或改提醒几个任务：同任务服务的批量上限（`rules.BATCH_MAX`），允许卡片上一个任务一行
FOLDER_BATCH_MAX = 20
TASK_KEYS = ("name", "title", "connection", "account_set_key", "direction", "posting_kind", "payment", "reader",
             "root_id", "folder", "run_day", "run_time")


def _conversation():
    """这次调用所在的对话：在 CC 里存或启用的任务，提醒发回这段对话（任务服务只从参数取，不从表单取）。"""
    return (getattr(frappe.local, "pearnly_assistant", None) or {}).get("session_id") or None


def _remind_view(row) -> dict:
    """任务的提醒：开着才给提醒时刻。任务服务关着提醒也回生效的时刻（没选过的是默认的前一天 08:00、14:00），
    CC 就把 remind=0 的任务说成提醒开着、列了时刻（2026-10-13 测试机）。"""
    remind = row.get("remind")
    return {"remind": remind, "remind_times": row.get("remind_times") if remind else None}


def folder_tasks(tool_input) -> str:
    company = _company_scope(tool_input or {}, explicit=())
    if company and company.get("failed"):
        return _json({"status": "failed", "reason": company["failed"]})
    pairs = _scope_pairs(company["sets"]) if company and company["sets"] is not None else None
    listing = rules.listing()
    keep = ("name", "title", "enabled", "connection_label", "account_label", "direction", "posting_kind", "payment",
            "folder", "root_label", "run_day", "run_time", "defaults", "remind", "remind_times", "include_existing",
            "last_run", "counts", "results", "waiting", "waiting_partial", "agent_fresh", "folder_state", "reader_gone")
    tasks = []
    for row in listing.get("rules") or []:
        if pairs is not None and (str(row.get("connection") or ""), _account_key(row.get("account_set_key"))) not in pairs:
            continue
        task = {**{k: row.get(k) for k in keep}, **_remind_view(row)}
        if task["remind_times"] is None:
            task.pop("remind_times")
        tasks.append(task)
    return _json({"tasks": tasks, **({"scope": company["scope"]} if company else {})})


def run_folder_task(tool_input) -> str:
    row = rules.run_now(str(tool_input.get("task") or ""))
    return _json({"task": row.get("name"), "title": row.get("title"), "requested": True,
                  "note": "CC Desktop picks this up on its next sync, usually within a minute."})


def folder_task_runs(tool_input) -> str:
    data = rules.runs(str(tool_input.get("task") or ""))
    keep = ("trigger", "started_at", "scanned_at", "found", "new_files", "skipped", "upload_failed", "note",
            "results", "states")
    task = data.get("rule")
    out = {"task": _account_named(task, (task or {}).get("connection")),
           "runs": [{k: r.get(k) for k in keep} for r in data.get("runs") or []][:10]}
    if data.get("pos_layout_needed"):
        # POS 导出停着等人认列（这家公司第一次见这种导出）：用 cowork_pos_layout 的 folder_file 回答
        out["pos_layout_needed"] = data["pos_layout_needed"]
    return _json(out)


FOLDERS_SHOWN = 50


def _task_call(fn, *args, **kwargs):
    try:
        result = fn(*args, **kwargs)
        frappe.db.commit()
        return result
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return {"status": "not_saved", "reason": str(error)[:300]}


def folder_task_options(tool_input) -> str:
    """同定时任务表单：能读文件夹的电脑与它们开放给网页的文件夹（只在那台电脑的 CC 里加减）、Express 账套的科目与部门。"""
    search = str(tool_input.get("search") or "").strip().casefold()
    data = _task_call(rules.options, str(tool_input.get("connection") or ""), tool_input.get("account_set_key") or None)
    if data.get("status") == "not_saved":
        return _json(data)
    readers = []
    for reader in data.get("readers") or []:
        roots = []
        for root in reader.get("roots") or []:
            folders = [f for f in root.get("folders") or [] if not search or search in str(f).casefold()]
            item = {"root_id": root.get("id"), "label": root.get("label"), "folders_total": len(folders),
                    "folders": folders[:FOLDERS_SHOWN]}
            # 小助手报的各文件夹（含子文件夹）现有几份会被录入的文件：报了计数（`counts` 键在，哪怕是空的）就是数过，
            # 根目录与列出的每个文件夹都给，计数里没有的是 0；没报过（没有这个键）就不给，不拿 0 冒充「不知道」
            counts = root.get("counts")
            if isinstance(counts, dict):
                item["files"] = {folder: int(counts.get(folder) or 0) for folder in [""] + item["folders"]}
                tables = root.get("table_counts")
                if isinstance(tables, dict) and tables:
                    # POS 导出（Excel / CSV）另数：只有推往 Express 的销售任务收它们
                    item["pos_files"] = {folder: int(tables.get(folder) or 0) for folder in [""] + item["folders"]}
                if root.get("partial"):
                    item["files_partial"] = True
            roots.append(item)
        # 读者编号「电脑|连接」（ADR-021 第 4 批）：同一个池里几台电脑各是一项，存任务、列文件、取文件都原样传回
        readers.append({"reader": reader.get("reader") or reader.get("connection"), "computer": reader.get("label"),
                        "online": reader.get("fresh"), "roots": roots})
    out = {"computers": readers, "stock_group": data.get("stock_group")}
    masters = data.get("masters") or {}
    if masters.get("state") not in (None, "ready") and data.get("accounts") is None:
        out["accounts_status"] = masters["state"]
        out["accounts_note"] = _masters_note(masters["state"])
    for kind in ("accounts", "departments"):
        rows = [r for r in data.get(kind) or [] if not search or search in f"{r.get('code')} {r.get('name')}".casefold()]
        if data.get(kind) is not None:
            out[kind] = {"total": len(rows), "shown": rows[:CHOICE_OPTIONS_SHOWN]}
    return _json(_filled(out))


# ---------------- 从电脑上已授权的文件夹列文件、取文件进对话（只读、不识别、不扣费） ----------------

FOLDER_LIST_WAIT_SECONDS = 45
FOLDER_FETCH_WAIT_SECONDS = 90
FOLDER_FILES_PAGE = 100
FOLDER_FILES_PAGE_MAX = 200
FOLDER_FETCH_MAX = 5
# 列出来的清单在这段对话里留 10 分钟：翻页不再请电脑重列
FOLDER_LISTING_TTL = 600
# 一次取几份、有的还在读时，已取进对话的那几份记 10 分钟（每次重调续期）：按提示重调不再取一遍
FOLDER_FETCHED_TTL = 600
FOLDER_STILL_READING = ("Still reading on the computer. Call again with the same input to get it; do not wait "
                        "with sleep.")
# 取进对话的文件类型（同对话附件那一组）：不靠系统的 mime 表（容器里可能没有 xlsx / docx）
FOLDER_FILE_TYPES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                     ".gif": "image/gif", ".webp": "image/webp", ".heic": "image/heic", ".heif": "image/heif",
                     ".xlsx": XLSX, ".xls": "application/vnd.ms-excel", ".csv": "text/csv",
                     ".tsv": "text/tab-separated-values", ".txt": "text/plain", ".md": "text/markdown",
                     ".json": "application/json",
                     ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
FOLDER_WAITING = {"offline": "The computer that reads this folder is offline. Nothing was read; ask the user to turn "
                             "it on or open CC Desktop there.",
                  "update_needed": "CC Desktop on that computer must be updated before files can be read from it."}


def _folder_request(start) -> dict:
    """建请求（权限、根目录、电脑在不在都由 `folder_intake.fetch` 判）；不行就回 not_started 与原因。"""
    try:
        made = start()
        frappe.db.commit()
        return made
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return {"status": "not_started", "reason": str(error)[:300]}


def _folder_settled(states) -> bool:
    return all(item.get("status") not in ("requested", "leased") for item in states)


def _folder_wait(requests, seconds) -> dict:
    """等电脑把这几件做完（或等满 ``seconds``），每次重新读（电脑交结果是别的请求提交的）：{请求号: 状态}。"""
    from pearnly_core.assistant import toolrun

    from pearnly_cowork.folder_intake import fetch

    deadline = time.monotonic() + seconds
    states = {}
    while True:
        toolrun.fresh()
        for request in requests:
            try:
                states[request] = fetch.state(request)
            except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError):
                frappe.clear_messages()
                states[request] = {"status": "failed", "code": "not_found"}
        if _folder_settled(states.values()) or time.monotonic() >= deadline:
            return states
        time.sleep(2)


def _folder_not_done(state) -> dict:
    if state.get("status") in ("requested", "leased"):
        return {"status": "reading", "note": FOLDER_STILL_READING}
    return {"status": "failed", "reason": state.get("code") or state.get("status")}


def _waiting_for_cc(made) -> dict:
    return {**made, "note": FOLDER_WAITING.get(made.get("reason"), "")}


def _listing_key(tool_input) -> str:
    seed = json.dumps([frappe.session.user, _conversation(), tool_input.get("reader"), tool_input.get("root_id"),
                       tool_input.get("folder") or "", tool_input.get("search") or "",
                       sorted(str(e) for e in tool_input.get("extensions") or [])], ensure_ascii=False)
    return f"pearnly_assistant:folder_files:{frappe.local.site}:" + hashlib.sha256(seed.encode()).hexdigest()[:40]


def folder_files(tool_input) -> str:
    """电脑上已授权的文件夹（含子文件夹）里能取进对话的文件：按修改时间新的在前，一页 100 份。第一页请电脑现列，
    翻页用这段对话里 10 分钟内列过的同一份清单。"""
    from pearnly_cowork.folder_intake import fetch

    offset = max(int(tool_input.get("offset") or 0), 0)
    limit = min(max(int(tool_input.get("limit") or FOLDER_FILES_PAGE), 1), FOLDER_FILES_PAGE_MAX)
    key = _listing_key(tool_input)
    data = frappe.cache.get_value(key) if offset else None
    if not isinstance(data, dict):
        made = _folder_request(lambda: fetch.files(str(tool_input.get("reader") or ""),
                                                   str(tool_input.get("root_id") or ""), tool_input.get("folder") or "",
                                                   search=tool_input.get("search") or "",
                                                   extensions=tool_input.get("extensions") or None,
                                                   scope=_conversation() or ""))
        if not made.get("request"):
            return _json(_waiting_for_cc(made) if made.get("status") == "waiting_for_cc" else made)
        state = _folder_wait([made["request"]], FOLDER_LIST_WAIT_SECONDS)[made["request"]]
        if state.get("status") != "done":
            return _json(_folder_not_done(state))
        try:
            data = fetch.listing(made["request"])
            frappe.db.commit()
        except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
            frappe.db.rollback()
            frappe.clear_messages()
            return _json({"status": "failed", "reason": str(error)[:300]})
        frappe.cache.set_value(key, data, expires_in_sec=FOLDER_LISTING_TTL)
    rows = data.get("files") or []
    page = [{"path": row["path"], "name": row["name"], "size_bytes": row["size"], "modified": row["modified"],
             **({"result_folder": True} if row.get("result_folder") else {})} for row in rows[offset:offset + limit]]
    out = {"files": page, "total": len(rows), "offset": offset,
           "next_offset": offset + limit if offset + limit < len(rows) else None}
    if data.get("truncated"):
        out["truncated"] = True
    return _json(out)


def _fetched_key(reader, root, path):
    """这段对话里取过的一份文件（路径按取文件的规则规整，`/` 与 `\\` 是同一份）；路径不合规的没有。"""
    try:
        normal = [folder_contracts.root_id(root), folder_contracts.fetch_path(path)]
    except folder_contracts.ContractError:
        return None
    seed = json.dumps([frappe.session.user, _conversation(), reader, *normal], ensure_ascii=False)
    return f"pearnly_assistant:folder_fetched:{frappe.local.site}:" + hashlib.sha256(seed.encode()).hexdigest()[:40]


def _remember_fetched(results, keys) -> None:
    """还有在读的：记下这次已取进对话的（按提示原样重调时直接回它们，电脑不再读、对话里不多一份）；都读完了就忘掉，
    之后再要同一份是重新取（文件可能改过）。"""
    reading = any(row.get("status") == "reading" for row in results.values())
    for path, key in keys.items():
        row = results[path]
        if reading and row.get("status") == "added":
            frappe.cache.set_value(key, {name: row[name] for name in ("status", "file", "filename", "size_bytes")},
                                   expires_in_sec=FOLDER_FETCHED_TTL)
        elif not reading:
            frappe.cache.delete_value(key)


def folder_fetch(tool_input) -> str:
    """把电脑上已授权文件夹里的几份文件取进这段对话（像用户附件一样给别的工具用）。只读，不识别、不扣费。"""
    from pearnly_core.assistant import files as chat_files

    from pearnly_cowork.folder_intake import fetch

    paths = list(dict.fromkeys(str(p) for p in (tool_input.get("paths") or []) if str(p).strip()))[:FOLDER_FETCH_MAX]
    if not paths:
        frappe.throw(_("No files were given."))
    reader, root = str(tool_input.get("reader") or ""), str(tool_input.get("root_id") or "")
    results, pending, keys = {}, {}, {}
    for path in paths:
        key = _fetched_key(reader, root, path)
        if key:
            keys[path] = key
            earlier = frappe.cache.get_value(key)
            if isinstance(earlier, dict) and earlier.get("file"):
                results[path] = {"path": path, **earlier}  # 上一次已取进对话
                continue
        made = _folder_request(lambda path=path: fetch.fetch(reader, root, path, scope=_conversation() or ""))
        if made.get("status") == "waiting_for_cc":
            return _json(_waiting_for_cc(made))
        if made.get("request"):
            # 写法不同的同一份文件（`/` 与 `\\`）是同一件请求：取一次，结果给每种写法
            pending.setdefault(made["request"], []).append(path)
        else:
            results[path] = {"path": path, **made}
    states = _folder_wait(list(pending), FOLDER_FETCH_WAIT_SECONDS) if pending else {}
    for request, asked in pending.items():
        state = states.get(request) or {}
        if state.get("status") != "done":
            outcome = _folder_not_done(state)
        else:
            try:
                filename, content = fetch.content(request)
                mime = FOLDER_FILE_TYPES.get("." + filename.rsplit(".", 1)[-1].lower(), "application/octet-stream")
                added = chat_files.add_to_conversation(filename, content, mime, origin="folder")
                frappe.db.commit()
                outcome = {"status": "added", "file": added, "filename": added.rsplit("/", 1)[-1],
                           "size_bytes": len(content)}
            except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
                frappe.db.rollback()
                frappe.clear_messages()
                outcome = {"status": "failed", "reason": str(error)[:300]}
        for path in asked:
            results[path] = {"path": path, **outcome}
    _remember_fetched(results, keys)
    files = [results[path] for path in paths]
    return _json(_added_to_chat({"files": files}, [item for item in files if item.get("file")]))


# ---------------- 回读 Express 账套（只读、不识别、不扣费） ----------------

READBACK_WAIT_SECONDS = 90
READBACK_ROWS_SHOWN = 20
# 回读的工作簿放进对话后记 7 天：同一段对话再查同一份回读不再放一遍
READBACK_FILES_TTL = 7 * 86400
READBACK_STILL_READING = ("Still reading the books on the computer. Check again with cowork_express_readback_status "
                          "when the user asks; do not wait with sleep.")
READBACK_WAITING = {"offline": "The computer with this account set is offline. Nothing was read; ask the user to turn "
                               "it on or open CC Desktop there.",
                    "update_needed": "CC Desktop on that computer must be updated before the books can be read back.",
                    "unreachable": "None of the user's own computers with CC Desktop can open this account set (a "
                                   "readback is only done by the requester's own computers, never a colleague's). "
                                   "Nothing was read; ask the user to sign in to CC Desktop on a computer that can "
                                   "open it.",
                    "missing": "The user has not connected CC Desktop on any computer yet (a readback is only done "
                               "by the requester's own computers). Nothing was read; ask the user to install CC Desktop "
                               "on a computer that can open this account set and sign in there."}


def _readback_call(fn, *args, failed="failed"):
    """调回读服务（权限、账套、电脑在不在都由 `readback.service` 判）；不行回 ``{status: failed, reason}``。"""
    try:
        result = fn(*args)
        frappe.db.commit()
        return result
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return {"status": failed, "reason": str(error)[:300]}


def _readback_wait(name) -> dict:
    """等这份回读读完并处理好（或等满 READBACK_WAIT_SECONDS）；每次重新读（电脑交结果是别的请求提交的）。
    别人发起、还在读的那份只由发起人推进（`readback.service.status`）：等不来，当场回现状。"""
    from pearnly_core.assistant import toolrun

    from pearnly_cowork.readback import service as readback

    deadline = time.monotonic() + READBACK_WAIT_SECONDS
    while True:
        toolrun.fresh()
        state = _readback_call(readback.status, name)
        if state.get("status") != "reading" or time.monotonic() >= deadline \
                or state.get("requested_by") not in (None, "", frappe.session.user):
            return state
        time.sleep(4)


def _readback_files(state) -> dict:
    """四份工作簿放进这段对话（同一段对话、同一份回读只放一次）：{kind: 智能体读到的位置}；之后的工具像附件一样取用。"""
    from pearnly_core.assistant import files as chat_files

    from pearnly_cowork.readback import service as readback

    key = f"pearnly_assistant:readback:{frappe.local.site}:{_conversation() or ''}:{state['readback']}"
    added = frappe.cache.get_value(key)
    added = dict(added) if isinstance(added, dict) else {}
    for kind in state.get("files") or ():
        if kind not in added:
            filename, content = readback.file(state["readback"], kind)
            added[kind] = chat_files.add_to_conversation(filename, content, XLSX, origin="tool")
            frappe.cache.set_value(key, added, expires_in_sec=READBACK_FILES_TTL)
    return added


def _readback_out(state) -> dict:
    out = {key: state[key] for key in ("readback", "status", "period", "account_set", "code", "reason", "version",
                                       "minimum") if state.get(key)}
    if state.get("status") == "waiting_for_cc":
        out["note"] = READBACK_WAITING.get(state.get("reason"), "")
    elif state.get("status") == "reading":
        out["note"] = READBACK_STILL_READING
    elif state.get("status") == "ready":
        try:
            out["files"] = _readback_files(state)
            frappe.db.commit()
            out = _added_to_chat(out, out["files"])
        except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
            frappe.db.rollback()
            frappe.clear_messages()
            out["files_error"] = str(error)[:300]
        out["summary"] = state.get("summary") or {}
        for kind in ("changes", "direct"):
            rows = state.get(kind) or []
            out[kind] = rows[:READBACK_ROWS_SHOWN]
            if len(rows) > READBACK_ROWS_SHOWN:
                out[kind + "_not_shown"] = len(rows) - READBACK_ROWS_SHOWN
    return out


def express_readback(tool_input) -> str:
    """请那台电脑只读读回一本 Express 账套的一个月，出总账、销项 / 进项税报与差异表四份工作簿放进对话。"""
    from pearnly_cowork.readback import service as readback

    made = _readback_call(readback.request, str(tool_input.get("connection") or ""),
                          str(tool_input.get("account_set_key") or ""), str(tool_input.get("period") or ""),
                          tool_input.get("accounts") or None, failed="not_started")
    if made.get("status") == "reading" and made.get("readback"):
        made = _readback_wait(made["readback"])
    return _json(_readback_out(made))


def express_readback_status(tool_input) -> str:
    return _json(_readback_out(_readback_wait(str(tool_input.get("readback") or ""))))


def express_readback_delete(tool_input) -> str:
    from pearnly_cowork.readback import service as readback

    return _json(_readback_call(readback.delete, str(tool_input.get("readback") or "")))


def _task_rows(items) -> dict:
    """要改的已有任务的现值（任务列表只读一次）；全是新任务就不读。"""
    if not any(isinstance(item, dict) and item.get("name") for item in items):
        return {}
    # 读者按编号「电脑|连接」比与回传（任务列表的 ``reader`` 仍是成员连接，给第 6 批之前的网页用）
    return {row.get("name"): {**row, "reader": row.get("reader_id") or row.get("reader")}
            for row in rules.listing().get("rules") or []}


def _given_defaults(item):
    """模型给的默认值：对象（或写成 JSON 字符串的对象）；没给为 None。"""
    raw = item.get("defaults")
    if isinstance(raw, str) and raw.strip():
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    return raw


def _task_values(item, rows) -> dict:
    """要存的一份任务表单。改已有任务：以库里现值为底，只换这次说的几项（保存接口要整份表单；2026-10-10 测试只传了
    账套，缺入账方式被拒）；默认值按类合并、空串清掉这一类；提醒与提醒时刻只在说了时带（不带由任务服务保留原值）。
    来源对话不进表单（由 `_conversation` 作参数交给任务服务）。"""
    current = rows.get(str(item.get("name") or ""), {}) if item.get("name") else {}
    values = {key: current[key] for key in TASK_KEYS if current.get(key) not in (None, "")}
    if "run_time" in values:
        values["run_time"] = _time(values["run_time"])
    values.update({key: item[key] for key in TASK_KEYS if key in item})
    given = _given_defaults(item)
    if isinstance(given, dict):
        merged = {**(current.get("defaults") or {}), **given}
        values["defaults"] = {kind: value for kind, value in merged.items() if value not in (None, "")}
    elif given not in (None, ""):
        values["defaults"] = given  # 形状不对：交给任务服务照现有口径拒绝
    elif current.get("defaults"):
        values["defaults"] = dict(current["defaults"])
    if item.get("remind") is not None:
        values["remind"] = 1 if item["remind"] else 0
    if item.get("remind_times") is not None:
        values["remind_times"] = item["remind_times"]
    return values


def _unknown_defaults(items) -> dict:
    """默认值里不认识的类（模型拼错的键）：任务服务会把它们静默丢掉，这里先拒，指出第几个任务、哪个键。"""
    failed = []
    for number, item in enumerate(items, 1):
        given = _given_defaults(item)
        unknown = [str(kind) for kind in given if kind not in folder_contracts.KINDS] if isinstance(given, dict) else []
        if unknown:
            failed.append({"task": number, "title": str(item.get("title") or item.get("name") or "")[:140],
                           "reason": "unknown default kind: " + ", ".join(unknown)})
    if not failed:
        return {}
    return {"status": "not_saved", "reason": "Nothing was saved. Default kinds are only "
                                            + ", ".join(folder_contracts.KINDS) + ".", "failed": failed}


def _saved_task(row, values) -> dict:
    """存下的任务回给模型：实际存下的默认值与提醒；说了却因不适用（方向、入账方式、ERP）没存的默认值另列。"""
    saved = row.get("defaults") or {}
    given = values.get("defaults") if isinstance(values.get("defaults"), dict) else {}
    out = {"task": row.get("name"), "title": row.get("title"), "enabled": row.get("enabled"), "defaults": saved,
           **_remind_view(row)}
    skipped = [kind for kind, value in given.items() if value not in (None, "") and kind not in saved]
    if skipped:
        out["defaults_not_applicable"] = skipped
    return {key: value for key, value in out.items() if value is not None}


def save_folder_task(tool_input) -> str:
    refused = _unknown_defaults([tool_input])
    if refused:
        return _json(refused)
    values = _task_values(tool_input, _task_rows([tool_input]))
    row = _task_call(rules.save, values, cc_session=_conversation())
    return _json(row if row.get("status") == "not_saved" else {"status": "saved", **_saved_task(row, values)})


def save_folder_tasks(tool_input) -> str:
    """一次建 / 改几个任务（一张允许卡片）：全有或全无，任一个不通过就一个都不存，回每个不通过的序号、名称、原因。"""
    items = tool_input.get("tasks")
    if not isinstance(items, list) or not items:
        frappe.throw(_("No tasks were given."))
    if len(items) > FOLDER_BATCH_MAX:
        return _json({"status": "not_saved", "reason": f"At most {FOLDER_BATCH_MAX} tasks at a time; split the list."})
    if not all(isinstance(item, dict) for item in items):
        return _json({"status": "not_saved", "reason": "Each task must be an object."})
    refused = _unknown_defaults(items)
    if refused:
        return _json(refused)
    rows = _task_rows(items)
    values = [_task_values(item, rows) for item in items]
    try:
        result = rules.save_many(values, cc_session=_conversation())
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return _json({"status": "not_saved", "reason": str(error)[:300]})
    if result.get("failed"):
        frappe.db.rollback()
        frappe.clear_messages()
        return _json({"status": "not_saved", "reason": "Nothing was saved. Fix the tasks listed in failed and call "
                                                       "again with the whole list.", "failed": result["failed"]})
    frappe.db.commit()
    return _json({"status": "saved", "tasks": [_saved_task(row, value)
                                               for row, value in zip(result.get("saved") or [], values)]})


def _switch_args(item):
    """启停 / 提醒这一项要改的：（启用, 提醒, 提醒时刻参数），没说的为 None；什么都没说回 None。
    提醒时刻只在说了时带（任务服务不带就保留原值）。"""
    enabled = None if item.get("enabled") is None else (1 if item["enabled"] else 0)
    remind = None if item.get("remind") is None else (1 if item["remind"] else 0)
    times = {} if item.get("remind_times") is None else {"remind_times": item["remind_times"]}
    if enabled is None and remind is None and not times:
        return None
    return enabled, remind, times


def _switched(row) -> dict:
    """启停 / 提醒改好后的任务（提醒关着不给时刻，见 `_remind_view`）。"""
    out = {"task": row.get("name"), "title": row.get("title"), "status": "saved", "enabled": row.get("enabled"),
           **_remind_view(row)}
    return {key: value for key, value in out.items() if value is not None}


def set_folder_task_enabled(tool_input) -> str:
    args = _switch_args(tool_input)
    if not args:
        return _json({"status": "not_saved", "reason": "Give enabled, remind or remind_times."})
    enabled, remind, times = args
    row = _task_call(rules.set_enabled, str(tool_input.get("task") or ""), enabled, remind=remind,
                     cc_session=_conversation(), **times)
    if row.get("status") == "not_saved":
        return _json(row)
    out = {"status": "saved", "task": row.get("name"), "enabled": row.get("enabled"), **_remind_view(row)}
    return _json({key: value for key, value in out.items() if value is not None})


def set_folder_tasks_enabled(tool_input) -> str:
    """一次启停几个任务、开关或改它们的提醒（一张「允许」卡片，用户 2026-10-13 第 5 条：改 3 个任务的提醒弹了 3 张卡片）。
    每个照 `set_folder_task_enabled` 同一套规则（任务服务 `rules.set_enabled`）逐个执行、逐个回结果。各任务的启停与提醒
    互不相干：一个不通过只退回它自己（保存点），别的照常生效，失败的照实列出（不同于批量保存的全有或全无）；最后一起提交，
    意外出错（死锁等）整笔回滚、一个都不算改过。"""
    items = tool_input.get("tasks")
    if not isinstance(items, list) or not items:
        frappe.throw(_("No tasks were given."))
    if len(items) > FOLDER_BATCH_MAX:
        return _json({"status": "not_saved", "reason": f"At most {FOLDER_BATCH_MAX} tasks at a time; split the list."})
    titles = {row.get("name"): row.get("title") for row in rules.listing().get("rules") or []}
    results, seen = [], set()
    for number, item in enumerate(items, 1):
        item = item if isinstance(item, dict) else {}
        name = _shown(item.get("task")).strip()
        args = _switch_args(item)
        reason = ("Each item needs task." if not name else "This task is already in the list." if name in seen
                  else None if args else "Give enabled, remind or remind_times.")
        if not reason:
            seen.add(name)
            enabled, remind, times = args
            point = f"cowork_folder_switch_{number}"
            frappe.db.savepoint(point)
            try:
                results.append(_switched(rules.set_enabled(name, enabled, remind=remind, cc_session=_conversation(),
                                                           **times)))
                continue
            except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
                frappe.db.rollback(save_point=point)
                frappe.clear_messages()
                reason = str(error)[:300]
        failed = {"task": name, "title": titles.get(name), "status": "failed", "reason": reason}
        results.append({key: value for key, value in failed.items() if value is not None})
    saved = sum(item["status"] == "saved" for item in results)
    if saved:
        frappe.db.commit()
    status = "saved" if saved == len(results) else "partly_saved" if saved else "not_saved"
    return _json({"status": status, "tasks": results})


def delete_folder_tasks(tool_input) -> str:
    names = [str(n) for n in (tool_input.get("tasks") or [])]
    if not names:
        frappe.throw(_("No tasks were given."))
    return _json(_task_call(rules.delete, json.dumps(names)))


# ---------------- 要「允许」的那一步：卡片上先说清改什么（现在 → 改后），不写任何东西 ----------------

DIRECTION_LABELS = {"purchase": "intake.dir.purchase", "sales": "intake.dir.sales"}
POSTING_LABELS = {"service": "Service", "stock": "Stock"}
PAYMENT_LABELS = {"cash": "Cash", "credit": "Credit"}
POLICY_CLASS_LABELS = {"payment_evidence": "Transfer slips", "withholding_certificate": "Withholding tax certificates"}
POLICY_VALUE_LABELS = {"enter": "cowork.policy.enter", "skip": "cowork.policy.skip"}


def _shown(value) -> str:
    return "" if value in (None, "") else str(value)


def _change(field, before, after) -> dict:
    return {"field": _(field), "before": _shown(before), "after": _shown(after)}


def _time(value) -> str:
    text = _shown(value)
    return text[:5] if len(text) >= 5 and text[2:3] == ":" else ("0" + text)[:5] if text[1:2] == ":" else text


def _task_row(name) -> dict:
    return next((r for r in rules.listing().get("rules") or [] if r.get("name") == name), {}) if name else {}


def _day(value) -> str:
    """每月第几天；0 是月末（同任务页）。"""
    text = _shown(value).strip()
    if not text.isdigit():
        return text
    return _("cowork.folder.day").format(int(text)) if int(text) else _("cowork.folder.lastDay")


def _task_shown(row, account=None, folder=None) -> dict:
    """定时任务各项的显示值（同任务页的叫法）。"""
    return {"cowork.folder.field.title": row.get("title"),
            "intake.target.accountSet": account if account is not None else row.get("account_label"),
            "intake.field.direction": _(DIRECTION_LABELS.get(row.get("direction"), _shown(row.get("direction")))),
            "intake.field.itemType": _(POSTING_LABELS[row["posting_kind"]]) if row.get("posting_kind") in POSTING_LABELS else _shown(row.get("posting_kind")),
            "cowork.choice.payment": _(PAYMENT_LABELS[row["payment"]]) if row.get("payment") in PAYMENT_LABELS else _shown(row.get("payment")),
            "cowork.folder.field.folder": folder if folder is not None else "\\".join(
                p for p in (row.get("root_label"), row.get("folder")) if p),
            "cowork.folder.field.day": _day(row.get("run_day")),
            "cowork.folder.field.time": _time(row.get("run_time"))}


# 默认处理各类的取值叫法（同任务页的选项）；科目、部门显示「代码 · 名称」
DEFAULT_VALUE_LABELS = {"payment": PAYMENT_LABELS,
                        "vat": {"claim": "cowork.folder.vat.claim", "expense": "cowork.folder.vat.expense"},
                        "item": {folder_contracts.NEW: "cowork.folder.item.new", "ask": "cowork.folder.item.ask"}}
REMIND_FIELD = "cowork.folder.field.remind"
PAIRED = tuple(f"cowork.folder.default.{kind}" for kind in folder_contracts.KINDS) + (REMIND_FIELD,)


def _cached(cache, key, read):
    if key not in cache:
        try:
            cache[key] = read()
        except Exception:
            # 卡片只是给人看：读不到名称就照原值显示，不让整张卡片退回逐个列参数
            frappe.clear_messages()
            cache[key] = None
    return cache[key]


def _folder_path(value) -> str:
    """文件夹相对路径统一成「\\」分隔（同任务服务的写法）。"""
    return "\\".join(part.strip() for part in re.split(r"[\\/]+", _shown(value)) if part.strip())


def _same_place(row) -> tuple:
    """读哪台电脑、哪个根目录下的哪个文件夹（比较用：分隔符、大小写不计）。"""
    return (_shown(row.get("reader") or row.get("connection")), _shown(row.get("root_id")),
            _folder_path(row.get("folder")).casefold())


def _account_key(value) -> str:
    return _shown(value).replace("/", "\\").rstrip("\\").casefold()


def _kept_defaults(values, adapter) -> dict:
    """会存下的默认值（同任务服务：只有 Express 有，只留这条任务用得到的类）。"""
    given = values.get("defaults")
    if adapter != "express" or not isinstance(given, dict):
        return {}
    kinds = folder_contracts.applicable(values.get("direction"), values.get("posting_kind"))
    return {kind: _shown(given.get(kind)).strip() for kind in folder_contracts.KINDS
            if kind in kinds and _shown(given.get(kind)).strip()}


def _default_shown(kind, value, names) -> str:
    if not value:
        return ""
    labels = DEFAULT_VALUE_LABELS.get(kind)
    if labels:
        return _(labels[value]) if value in labels else value
    name = ((names or {}).get("accounts" if kind == "account" else "departments") or {}).get(value)
    return f"{value} · {name}" if name else value


def _remind_day(days) -> str:
    """提醒日：当天 / 前 1 天 / 前 N 天（同任务页的选项）。"""
    if days == 0:
        return _("cowork.folder.remind.sameDay")
    return _("cowork.folder.remind.dayBefore") if days == 1 else _("cowork.folder.remind.daysBefore").format(days)


def _remind_shown(value, times=None) -> str:
    """提醒：关，或各提醒时刻（「前 1 天 08:00 / 前 1 天 14:00」，没选过的是默认的那两个）。"""
    if not value:
        return _("cowork.folder.remind.off")
    try:
        items = folder_contracts.remind_times(times)
    except folder_contracts.ContractError:
        return json.dumps(times, ensure_ascii=False)[:120]  # 形状不对：照原样给人看，保存时任务服务会拒
    return " / ".join(_remind_day(item["days_before"]) + " " + item["time"] for item in items)


def _waiting_text(values) -> str:
    """文件夹（含子文件夹）里现有几份会被录入的文件（小助手上报的数）：没报过不说，不拿 0 冒充；数到上限说「N+」。"""
    try:
        found = rules.waiting(values.get("reader") or values.get("connection"), values.get("root_id"),
                              _shown(values.get("folder")), values.get("direction"), values.get("connection"))
    except Exception:
        frappe.clear_messages()
        return ""
    if not found or found.get("files") is None:
        return ""
    return _("cowork.folder.waiting").format(f"{found['files']}+" if found.get("partial") else found["files"])


def _folder_text(values, current) -> str:
    """新建或换了文件夹的任务卡片上那一句：文件夹与停用的任务重叠时是接续它、只处理此后放入的（与任务服务同一判据，
    `rules.continues_from`），不说现有几份；否则说文件夹里现有几份。"""
    try:
        continued = rules.continues_from(values.get("reader") or values.get("connection"), values.get("root_id"),
                                         _shown(values.get("folder")), current=current.get("name") or None)
    except Exception:
        frappe.clear_messages()
        continued = None
    if continued:
        return _("cowork.folder.continues").format(continued)
    return _waiting_text(values)


def _root_labels(reader, root) -> dict:
    """卡片上的根目录名：读者那台电脑报的 ``{根目录编号: 显示路径}``。认不出是哪台（只给连接、几台都报过这个根目录）
    时不显示路径，存任务时由任务服务照常拒。"""
    try:
        return {r.get("id"): r.get("label")
                for r in folder_readers.state(*folder_readers.resolve(reader, root)).get("roots") or []}
    except frappe.ValidationError:
        frappe.clear_messages()
        return {}


def _task_lines(item, rows, cache) -> tuple:
    """卡片上一个任务要列的：（现值，[(字段, 现在, 改后)]，文件夹那一句）。新任务只列有值的项，改任务只列变了的项；
    文件夹那一句（现有几份，或接续哪个停用的任务）只在新建或换了文件夹时说（之后看任务列表的待处理数）。"""
    current = rows.get(str(item.get("name") or ""), {}) if item.get("name") else {}
    values = _task_values(item, rows)
    connection, key = values.get("connection"), values.get("account_set_key")
    if current and _account_key(key) == _account_key(current.get("account_set_key")):
        account = current.get("account_label")
    else:
        account = (_cached(cache, ("account", connection, key), lambda: rules.account_label(connection, key))
                   or _shown(key)) if key else ""
    before = _task_shown(current) if current else {}
    moved = not current or _same_place(values) != _same_place(current)
    if moved:
        reader, root = values.get("reader") or connection, values.get("root_id")
        roots = _cached(cache, ("roots", reader, root), lambda: _root_labels(reader, root)) or {}
        folder = "\\".join(p for p in (roots.get(values.get("root_id")), _folder_path(values.get("folder"))) if p)
    else:
        folder = before.get("cowork.folder.field.folder", "")
    after = _task_shown(values, account, folder)
    adapter = current.get("adapter") or _cached(cache, ("adapter", connection),
                                                lambda: frappe.db.get_value(CONNECTION, connection, "adapter"))
    kept, had = _kept_defaults(values, adapter), (current.get("defaults") or {}) if current else {}
    names = {}
    for side, chosen, row in (("after", kept, values), ("before", had, current)):
        if any(kind in chosen for kind in ("account", "department")):
            pair = (row.get("connection"), row.get("account_set_key"))
            names[side] = _cached(cache, ("names",) + pair, lambda: rules.master_names(*pair))
    for kind in folder_contracts.KINDS:
        field = f"cowork.folder.default.{kind}"
        before[field] = _default_shown(kind, _shown(had.get(kind)), names.get("before"))
        after[field] = _default_shown(kind, kept.get(kind, ""), names.get("after"))
    # 新任务提醒默认关：卡片写「关 → 各提醒时刻」；只给了提醒时刻也是打开（同任务服务）
    before[REMIND_FIELD] = _remind_shown(current.get("remind"), current.get("remind_times"))
    after[REMIND_FIELD] = _remind_shown(values.get("remind", 1 if "remind_times" in values else current.get("remind")),
                                        values.get("remind_times", current.get("remind_times")))
    lines = [(field, _shown(before.get(field)), _shown(text)) for field, text in after.items()
             if _shown(text) != _shown(before.get(field))]
    return current, lines, _folder_text(values, current) if moved else ""


def preview_save_folder_task(tool_input) -> list:
    current, lines, waiting = _task_lines(tool_input, _task_rows([tool_input]), {})
    rows = [_change(field, before, after) for field, before, after in lines]
    if current and not any(line[0] == "cowork.folder.field.title" for line in lines):
        rows = [_change("cowork.folder.field.title", "", current.get("title"))] + rows
    if waiting:
        rows.append({"field": "", "before": "", "after": waiting})
    return rows


def _part(field, text) -> str:
    """批量卡片一行里的一段：默认处理与提醒带上项名（单看取值分不清是哪一项）。"""
    if not text:
        return ""
    return _("cowork.folder.pair").format(_(field), text) if field in PAIRED else text


def preview_save_folder_tasks(tool_input) -> list:
    """一张卡片列全部任务，一个任务一行：新任务写全它的口径，改任务只写变了的（现在 → 改后）。"""
    items = [item for item in tool_input.get("tasks") or [] if isinstance(item, dict)][:FOLDER_BATCH_MAX]
    rows, cache, out = _task_rows(items), {}, []
    for item in items:
        current, lines, waiting = _task_lines(item, rows, cache)
        if current:
            title, shown = current.get("title"), lines
        else:
            title = next((after for field, _before, after in lines if field == "cowork.folder.field.title"), "")
            shown = [line for line in lines if line[0] != "cowork.folder.field.title"]
        before = [_part(field, text) for field, text, _after in shown] if current else []
        after = [_part(field, text) for field, _before, text in shown] + [waiting]
        out.append({"field": _shown(title) or _("cowork.folder.new"), "before": " · ".join(p for p in before if p),
                    "after": " · ".join(p for p in after if p)})
    return out


def _switch_lines(item, row) -> list:
    """启停 / 提醒这一项卡片上的（字段, 现在, 改后）；任务读不到（没有或看不到）时「现在」为空。"""
    state = lambda on: _("cowork.folder.on") if on else _("cowork.folder.off")
    lines = []
    if item.get("enabled") is not None:
        lines.append(("intake.col.status", state(row.get("enabled")) if row else "", state(item["enabled"])))
    times = item.get("remind_times")
    if item.get("remind") is not None or times is not None:
        on = item["remind"] if item.get("remind") is not None else True
        lines.append((REMIND_FIELD, _remind_shown(row.get("remind"), row.get("remind_times")) if row else "",
                      _remind_shown(on, row.get("remind_times") if times is None else times)))
    return lines


def preview_set_folder_task_enabled(tool_input) -> list:
    row = _task_row(str(tool_input.get("task") or ""))
    rows = [_change("cowork.folder.field.title", "", row.get("title") or tool_input.get("task"))]
    return rows + [_change(field, before, after) for field, before, after in _switch_lines(tool_input, row)]


def preview_set_folder_tasks_enabled(tool_input) -> list:
    """一张卡片列全部任务，一个任务一行：任务名 + 改什么（现在 → 改后），同批量保存的卡片。"""
    items = [item for item in tool_input.get("tasks") or [] if isinstance(item, dict)][:FOLDER_BATCH_MAX]
    rows = {row.get("name"): row for row in rules.listing().get("rules") or []} if items else {}
    out = []
    for item in items:
        name = _shown(item.get("task"))
        row = rows.get(name, {})
        lines = _switch_lines(item, row)
        before = [_part(field, text) for field, text, _after in lines]
        after = [_part(field, text) for field, _before, text in lines]
        out.append({"field": _shown(row.get("title")) or name, "before": " · ".join(p for p in before if p),
                    "after": " · ".join(p for p in after if p)})
    return out


def preview_delete_folder_tasks(tool_input) -> list:
    names = {r.get("name"): r.get("title") for r in rules.listing().get("rules") or []}
    return [_change("Delete", "", names.get(str(n)) or n) for n in tool_input.get("tasks") or []]


def preview_choose_document_policy(tool_input) -> list:
    from pearnly_cowork.contracts import document_policy as policy_rules
    from pearnly_cowork.contracts.history import primary_page_fields
    from pearnly_cowork.history import document_policy, storage

    doc = storage.read(str(tool_input.get("document") or ""))
    kind = policy_rules.document_class(primary_page_fields(json.loads(doc.pages or "[]")))
    now = document_policy.read(doc.workspace).get(kind)
    label = lambda value: _(POLICY_VALUE_LABELS[value]) if value in POLICY_VALUE_LABELS else ""
    return [_change(POLICY_CLASS_LABELS.get(kind, kind or "Document"), label(now), label(tool_input.get("value")))]


def preview_stock_group(tool_input) -> list:
    current = _connection_call(connections_api.stock_acc_group, str(tool_input.get("connection") or ""),
                               str(tool_input.get("account_set_key") or ""))
    names = {g.get("acccod"): g.get("label") for g in current.get("groups") or []}
    chosen = current.get("chosen")
    chosen = chosen.get("acccod") if isinstance(chosen, dict) else chosen
    shown = lambda code: names.get(str(code or "").strip()) or _shown(code)
    return [_change("Inventory account group", shown(chosen), shown(tool_input.get("acccod")))]


_OBJ = {"type": "object", "properties": {}}
# 按公司可筛的读工具（单据列表、推送结果、定时任务）共用的两个参数，见 `company_tools.default_scope`
SCOPE_PROPS = {
    "company": {"type": "string", "description": "Company id from cowork_companies: only that company's account sets "
                                                 "(all its years and branches). Leave out for the company this "
                                                 "conversation is filed under."},
    "all_companies": {"type": "boolean", "description": "true only when the user asks about all companies or other "
                                                        "companies (" + "ทุกเจ้า, เจ้าอื่น" + "). Without it, a "
                                                        "conversation filed under a company covers only that "
                                                        "company's account sets."},
}

# 入账结果里的 written（推送记录冻结的 Express 载荷：现 / 赊、哪层定的、科目及其来源，`pushed.written`）：推送与查推送
# 结果共用一段说明。验收 F19：CC 只拿到单号，答不出现购还是赊购，两次叫用户去 Express 核对；测试窗口第 2 轮：没报 ERP
# 单号、没说科目从哪来。
WRITTEN_NOTE = ("An entered document carries written: the facts of how it was written. written.docnum is the document "
                "number it got in the accounting program it went into (Express, or MR.ERP for an MR.ERP account set), "
                "on every entered document however it was pushed: in a chat, from the pending page or by a scheduled "
                "task. Call the program by its name, Express or MR.ERP, never 'the ERP'. For Express, payment cash = "
                "a cash purchase (HP, paid in cash) or a cash sale (HS); credit = a credit purchase (RR, owed to the "
                "supplier) or a credit sale (IV). payment_from = what decided it: chosen (a person's choice: one made "
                "for this document at upload or on review, or on older documents possibly the choice remembered for "
                "this supplier or a scheduled task's default, so do not claim it was picked for this one document), "
                "remembered (the choice saved for this supplier in this account set), company_rule (a client "
                "company choice, see written.company_rules), task_default (the "
                "scheduled folder task's default), document (written on the document), document_type (receipt or "
                "simplified tax invoice = paid, tax invoice = credit), supplier_profile, bank (a matching bank "
                "payment), pos_summary (POS daily sales, always cash), default (no signal: the account set's "
                "default). debit_account is the account the purchase lines were sent with (an account set on "
                "periodic inventory may post the default stock account to its purchases account instead); "
                "revenue_account is the account of the sales lines. account_from = what decided the account: chosen "
                "(a person picked or changed it after this document came in, on the pending page, the upload review, "
                "the company page or in this chat; kept for this supplier, so other documents of the same supplier "
                "waiting at that time count too), remembered (picked earlier for this supplier in this account set), "
                "task_default (the scheduled folder task's default), express_history (this supplier was posted to "
                "this account consistently in Express before), company_rule (a client company choice, see "
                "written.company_rules), matched (the document's category matched an account "
                "mapping), default (the account set's default account; worth a look). Older documents may have no "
                "payment_from or account_from: then say only what is there, never guess a source. vat is claimed, "
                "added_to_cost (not claimed, in the cost) or none. written.writer, when present, is the name of the "
                "computer whose CC Desktop wrote it into Express; when the user asks which computer wrote it, give "
                "that name. Whenever a document is entered, always give the "
                "user its document number (docnum), amounts in baht and times in local time. An entered document "
                "is done: it is no longer on the pending page, so never point the user to the pending page, the "
                "review page, Express or MR.ERP to check or change it. " + ENTERED_NOTE + " Say plainly whether it was entered as cash or credit and "
                "which account it went to, each with where that came from, described in the user's own language "
                "(for example 'you chose it for this supplier' or 'remembered from before'); never show the codes "
                "themselves (chosen, remembered, task_default, express_history, company_rule, matched, default and "
                "the like); never ask the user to check Express for the number, cash or credit, or the account, and "
                "never end with a suggestion to check or confirm in Express what you have just told them. "
                "decided_by, when present, says per kind what decided each choice with the same words (remembered, "
                "company_rule, express_history, task_default; mixed when the lines of one kind came from different "
                "places); describe those in the user's own words too. written.company_rules, when present, lists the client "
                "company choices this document was entered by: kind, for (the party, or the item, the choice is for), "
                "tax_id, value, branch and still_saved (false: that choice was removed or changed since; now_value is "
                "the value it has now). When a company_rule decided something, name the choice from "
                "written.company_rules only; when it is missing, say only that it was the company's choice. Never take "
                "a choice from <client_choices> or the team notes as the one that decided an entered document: they "
                "show today's choices, which may be for other parties.")
# 定时任务的各项：单个保存与批量保存共用一份，免得两份说明走样
FOLDER_TASK_PROPS = {
    "name": {"type": "string", "description": "Existing task to change; leave out to create one."},
    "title": {"type": "string"}, "connection": {"type": "string"}, "account_set_key": {"type": "string"},
    "direction": {"type": "string", "enum": ["purchase", "sales"]},
    "posting_kind": {"type": "string", "enum": ["service", "stock"], "description": "Express."},
    "payment": {"type": "string", "enum": ["cash", "credit"], "description": "MR.ERP; purchases are credit."},
    "reader": {"type": "string", "description": "The computer (reader) whose folder is used."},
    "root_id": {"type": "string"}, "folder": {"type": "string", "description": "Subfolder; empty for the root."},
    "run_day": {"type": "integer", "minimum": 0, "maximum": 31, "description": "Day of month; 0 = last day."},
    "run_time": {"type": "string", "description": "HH:MM, Bangkok time."},
    "defaults": {"type": "object", "additionalProperties": False,
                 "description": "Express only. How this task's documents are handled when the choice comes up; a kind "
                                "is used only where it applies (payment and vat: purchases; account: service "
                                "purchases; item: stock). A kind left out or empty stops and asks the first time. "
                                "When changing a task, only the kinds given change; an empty string clears one.",
                 "properties": {
                     "payment": {"type": "string", "enum": ["cash", "credit", ""],
                                 "description": "When the document does not show cash or credit; what the document "
                                                "shows still wins."},
                     "vat": {"type": "string", "enum": ["claim", "expense", ""],
                             "description": "VAT on receipts and abbreviated tax invoices: claim as input VAT, or "
                                            "record as expense."},
                     "account": {"type": "string",
                                 "description": "Expense account code from cowork_folder_task_options. It replaces "
                                                "the account remembered for each supplier for every document of this "
                                                "task (a category-to-account mapping still wins); set it only when the "
                                                "user says they all go to one account."},
                     "item": {"type": "string", "enum": ["__new__", "ask", ""],
                              "description": "A stock item not found: __new__ creates it from the document; ask "
                                             "stops for a choice."},
                     "department": {"type": "string", "description": "Department code from cowork_folder_task_options."}}},
    "remind": {"type": "boolean",
               "description": "Remind the task owner before each run, at remind_times, to put the documents in the "
                              "folder; the reminder is posted in the latest chat it was saved or turned on from. Left "
                              "out keeps the current setting; new tasks start off."},
    "remind_times": {"type": "array", "maxItems": folder_contracts.REMIND_MAX,
                     "description": "When to remind, Bangkok time: days_before the run day (0 = the run day itself, "
                                    "then the time must be earlier than run_time) and time HH:MM. Default: the day "
                                    "before at 08:00 and 14:00. Giving it turns the reminder on; left out keeps the "
                                    "current times.",
                     "items": {"type": "object", "additionalProperties": False,
                               "properties": {"days_before": {"type": "integer", "minimum": 0,
                                                              "maximum": folder_contracts.REMIND_DAYS},
                                              "time": {"type": "string", "description": "HH:MM"}},
                               "required": ["days_before", "time"]}}}
# 启停 / 提醒一个任务的参数（单个、批量两个工具同一份）
SWITCH_PROPS = {"task": {"type": "string"}, "enabled": {"type": "boolean"}, "remind": FOLDER_TASK_PROPS["remind"],
                "remind_times": FOLDER_TASK_PROPS["remind_times"]}
TOOLS = [
    {"name": "cowork_recognize_attachments", "label": "Recognized documents",
     "description": "Recognize receipts and invoices the user attached in this chat with Pearnly's own recognition, "
                    "exactly like uploading them on the Intake workbench (same billing, the documents then appear in "
                    "the workbench and can be reviewed and pushed). Use it whenever the user sends a document photo or "
                    "PDF and wants it recognized, read, entered or recorded, or sends back a review workbook (Excel) "
                    "they corrected (a cash register's POS export goes to cowork_pos_import instead); files brought "
                    "from a folder with cowork_folder_fetch too, but not files a tool made. Pass each attachment's "
                    "file id: the folder "
                    "name after /mnt/session/uploads/ in the attachment note. Choices are optional; leave them all out "
                    "to recognize only. target express or mrerp also needs connection and account_set_key, as listed "
                    "by cowork_intake_options; if the user wants the documents entered into Express or MR.ERP and has not said "
                    "where in this conversation, get the choices there first (a company the user names is where: "
                    "its account set, found there with search). Otherwise call it directly, without "
                    "listing documents or checking CC Desktop first. Waits up to about 90 seconds and returns the "
                    "recognized documents; anything still running comes back with its batch for "
                    "cowork_recognition_results. Each attachment is charged once like a web upload; calling again "
                    "for the same attachment returns the same result at no extra charge, so never tell the user it "
                    "was charged "
                    "twice; only when its last recognition failed does calling again recognize it anew. "
                    "needs_review true means the workbench flagged it for a person to check. document_kind "
                    "withholding_certificate is a 50 Tawi withholding tax certificate: in documents when the company "
                    "enters them or has not chosen yet, with state not_invoice when it chose not to; "
                    "kept_for_wht_draft counts those kept for cowork_wht_draft, which reads them either way. "
                    "Report the recognized values, not your own reading of the image, and leave out fields that are "
                    "not given.",
     "input_schema": {"type": "object", "properties": {
         "files": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": RECOGNIZE_MAX},
         "direction": {"type": "string", "enum": ["purchase", "sales"]},
         "target": {"type": "string", "enum": ["express", "mrerp", "none"]},
         "connection": {"type": "string"},
         "account_set_key": {"type": "string"},
         "posting_kind": {"type": "string", "enum": ["service", "stock"], "description": "Express only."},
         "payment": {"type": "string", "enum": ["cash", "credit"], "description": "MR.ERP only."}},
         "required": ["files"]},
     "handler": "pearnly_cowork.assistant.tools.recognize_attachments"},
    {"name": "cowork_intake_options", "label": "Checked intake choices",
     "description": "Where recognized documents can go, as on the web's New intake and in LINE: each accounting "
                    "program (express, mrerp) with its state and account sets (the last-used one first), recognize only "
                    "(none), and suggested: the choices of the user's previous recognition that went to Express or MR.ERP, from "
                    "the web, LINE or this chat. Ask once: offer suggested first (say which account set it is by its "
                    "account_label), and "
                    "once the user agrees reuse it for the rest of the conversation without asking again. Only ready targets can be chosen. Large lists are cut to 30; pass search (company "
                    "name, tax ID or folder) to narrow them.",
     "input_schema": {"type": "object", "properties": {"search": {"type": "string"}}},
     "handler": "pearnly_cowork.assistant.tools.intake_options"},
    {"name": "cowork_stock_group", "label": "Checked the stock account group",
     "description": "Express documents posted as stock need the account set's stock account group, chosen once per "
                    "account set and shared by everyone. Without acccod it shows the groups and the current choice "
                    "(needed only when there is no choice and two or more groups); with acccod it records the "
                    "user's choice.",
     "input_schema": {"type": "object", "properties": {"connection": {"type": "string"},
                                                        "account_set_key": {"type": "string"},
                                                        "acccod": {"type": "string"}},
                      "required": ["connection", "account_set_key"]},
     "approval": ["acccod"],
     "ask": "Change the inventory account group?",
     "preview": "pearnly_cowork.assistant.tools.preview_stock_group",
     "handler": "pearnly_cowork.assistant.tools.stock_group"},
    {"name": "cowork_mrerp_setup", "label": "Checked MR.ERP import settings",
     "description": "MR.ERP import menus per document kind and default values, chosen once per account set before "
                    "pushing (shared by everyone, like on the web). read_again reads the menus and defaults from "
                    "MR.ERP first (up to a minute). menus maps document kind to idmenu, defaults maps field to code; "
                    "both only with values listed in the result, and only after the user chose them. Items with "
                    "needed true still need a choice.",
     "input_schema": {"type": "object", "properties": {
         "connection": {"type": "string"}, "account_set_key": {"type": "string"},
         "read_again": {"type": "boolean"},
         "menus": {"type": "object", "additionalProperties": {"type": "string"}},
         "defaults": {"type": "object", "additionalProperties": {"type": "string"}}},
         "required": ["connection", "account_set_key"]},
     "approval": ["menus", "defaults"],
     "ask": "Change the MR.ERP import settings?",
     "handler": "pearnly_cowork.assistant.tools.mrerp_setup"},
    {"name": "cowork_document_detail", "label": "Viewed a document",
     "description": "All recognized fields of one document on the Intake workbench (the review page): seller and "
                    "buyer, tax IDs, invoice number, date, amounts, line items, push status, and the version needed "
                    "to edit it. handling_choice_needed means the push waits for cowork_choose_document_policy. "
                    + WRITTEN_NOTE,
     "input_schema": {"type": "object", "properties": {"document": {"type": "string"}}, "required": ["document"]},
     "handler": "pearnly_cowork.assistant.tools.document_detail"},
    {"name": "cowork_edit_document", "label": "Edited a document",
     "description": "Change recognized fields of one document, like correcting it on the review page. Only when the "
                    "user asked for the change; change only what they asked. changes maps field to new value (an "
                    "empty value clears it); date takes the date as written (Thai Buddhist years are fine); amounts "
                    "are plain numbers; tax IDs are 13 digits. items replaces all line items. Pass the version from "
                    "cowork_document_detail so someone else's newer edit is not overwritten; if it was, read it again "
                    "and tell the user. This changes the record in Pearnly only: a document already entered into "
                    "Express or MR.ERP is not changed there and is not pushed again; only when the user asks, say "
                    "plainly that this edit does not reach it. " + ENTERED_NOTE,
     "input_schema": {"type": "object", "properties": {
         "document": {"type": "string"},
         "version": {"type": "integer"},
         "changes": {"type": "object", "properties": {key: {"type": ["string", "number", "null"]} for key in EDITABLE},
                     "additionalProperties": False},
         "items": {"type": "array", "items": {"type": "object", "properties": {
             "name": {"type": "string"}, "qty": {"type": ["string", "number"]},
             "price": {"type": ["string", "number"]}, "subtotal": {"type": ["string", "number"]}}}}},
         "required": ["document", "version"]},
     "handler": "pearnly_cowork.assistant.tools.edit_document"},
    {"name": "cowork_choose_document_policy", "label": "Document handling set",
     "description": "For a transfer slip or withholding-tax certificate whose push waits for a handling choice: enter "
                    "records documents of this kind, skip leaves them out. It applies from now on to every document "
                    "of this kind in this workspace, for all of its companies, not one company (the user allows it in "
                    "the chat); say it that way. Every document waiting for this choice is settled with it (settled "
                    "is how many): after skip they are left out and not pushed, so do not push them; after enter, "
                    "push the document again.",
     # 第 7 批 B7（2026-10-18 lab）：document 没写说明，CC 先后交了「transfer slip」「payment_evidence」（详情里
     # handling_choice_needed 的类别名），两次都找不到单据、规则没存上
     "input_schema": {"type": "object", "properties": {
         "document": {"type": "string", "description": "The record name of the document whose push waits for this "
                                                       "choice (the document value in tool results, such as from "
                                                       "cowork_list_documents), not the kind of document."},
         "value": {"type": "string", "enum": ["enter", "skip"]}},
                      "required": ["document", "value"]},
     "approval": True,
     "ask": "Change how this kind of document is handled?",
     "preview": "pearnly_cowork.assistant.tools.preview_choose_document_policy",
     "handler": "pearnly_cowork.assistant.tools.choose_document_policy"},
    {"name": "cowork_document_original", "label": "Opened the original",
     "description": "Put the original page image(s) of a document into this conversation, like the original beside "
                    "the review page: look at it to check recognized fields against the original, or when the user "
                    "wants to see it (the user can open it from the chat). The first 3 pages unless page is given.",
     "input_schema": {"type": "object", "properties": {"document": {"type": "string"},
                                                        "page": {"type": "integer", "minimum": 1}},
                      "required": ["document"]},
     "handler": "pearnly_cowork.assistant.tools.document_original"},
    {"name": "cowork_recognition_results", "label": "Checked recognition results",
     "description": "Status and recognized documents of recognition tasks started by cowork_recognize_attachments.",
     "input_schema": {"type": "object", "properties": {
         "batches": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": RECOGNIZE_MAX}},
         "required": ["batches"]},
     "handler": "pearnly_cowork.assistant.tools.recognition_results"},
    {"name": "cowork_pos_import", "label": "Imported POS sales",
     "description": "Enter a POS export (the cash register's sales by receipt or by item, Excel .xlsx or .csv, from "
                    "Ocha, Loyverse or any other POS) as daily sales: one simplified tax invoice per day to the cash "
                    "customer with the day's first to last receipt numbers, VAT split out, totals equal to the POS to "
                    "the satang; non-cash payments (transfer, QR, card, delivery apps) are moved from cash to the "
                    "account chosen for each payment method when pushed. Use it, not cowork_recognize_attachments, "
                    "for POS exports: no recognition and no charge. Needs connection and account_set_key of an Express "
                    "account set (from cowork_intake_options). The first time a company sends a new export layout it "
                    "may come back layout_needed with the headers, sample rows and questions (which column is which, "
                    "day/month order, whether amounts include VAT, unknown status values): ask the user, then call "
                    "again with answers, or answer with cowork_pos_layout; the answer is remembered for this company. "
                    "Returns the documents created, one per day, to push with cowork_push_documents without "
                    "posting_kind (each day keeps its own; a push may need the receiving account of each payment "
                    "method once, kind receipt in cowork_push_choices). "
                    "posting_kind stock writes item lines with cost when the export has items. .xls is not supported. "
                    "Importing the same export again enters it again.",
     "input_schema": {"type": "object", "properties": {
         "files": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": POS_FILES_MAX},
         "connection": {"type": "string"}, "account_set_key": {"type": "string"},
         "posting_kind": {"type": "string", "enum": ["service", "stock"]},
         "answers": POS_ANSWERS_SCHEMA},
         "required": ["files", "connection", "account_set_key"]},
     "handler": "pearnly_cowork.assistant.tools.pos_import"},
    {"name": "cowork_pos_layout", "label": "Set the POS export columns",
     "description": "Answer the layout questions of a POS export and remember them for this company, without entering "
                    "anything: an attachment (file with connection and account_set_key), or a file a scheduled sales "
                    "task is holding until its columns are known (folder_file, listed as pos_layout_needed by "
                    "cowork_folder_task_runs; it continues on its own once answered). Without answers it shows the "
                    "headers, sample rows and questions; with answers it returns the daily totals it will enter, or "
                    "the questions still open.",
     "input_schema": {"type": "object", "properties": {
         "file": {"type": "string"}, "folder_file": {"type": "string"}, "connection": {"type": "string"},
         "account_set_key": {"type": "string"}, "answers": POS_ANSWERS_SCHEMA}},
     "handler": "pearnly_cowork.assistant.tools.pos_layout"},
    {"name": "cowork_companion_status", "label": "Checked CC Desktop",
     "description": "The user's ERP connections (Express / MR.ERP): enabled or not, whether CC Desktop is online "
                    "(cc_desktop_online), and whether the account set list has been read. To find "
                    "or choose an account set for recognition or pushing, use cowork_intake_options instead.",
     "input_schema": {"type": "object", "properties": {}},
     "handler": "pearnly_cowork.assistant.tools.companion_status"},
    {"name": "cowork_refresh_account_sets", "label": "Refreshed account sets",
     "description": "Ask a connection to re-read its account sets from the accounting program. Check the result later "
                    "with cowork_companion_status.",
     "input_schema": {"type": "object", "properties": {"connection": {"type": "string"}}, "required": ["connection"]},
     "handler": "pearnly_cowork.assistant.tools.refresh_account_sets"},
    {"name": "cowork_list_documents", "label": "Listed documents",
     "description": "List recognized documents (receipts, tax invoices) the user can see, newest first, with both "
                    "parties' names and tax IDs, document type, direction, the recognized subtotal, VAT and "
                    "total, and the push status. Filter by push state (waiting = not in Express or MR.ERP yet: never pushed or "
                    "the push failed, so a document pushed successfully or on its way is never waiting; not_pushed, "
                    "pending, success, failed), upload date, invoice date, where they are pushed (connection and "
                    "account_set_key as listed by "
                    "cowork_intake_options) or direction; search finds documents by invoice number, seller or buyer "
                    "name, tax ID or file name across all documents, not only the latest ones. Up to 50 per call: total is how "
                    "many match; pass next_offset as offset for the next page. totals adds the sums of all matching "
                    "documents (credit notes summed separately; without_value counts documents with no recognized "
                    "value). Without account_set_key and search, by_account_set gives every account set (company) "
                    "with documents in the same dates and how many of them match, 0 included: answer about all "
                    "companies from it. When complete is false (only the newest documents were checked), its "
                    "rows are marked partial and a company with an empty count was not reached: never say it has "
                    "none; narrow the dates or ask about that company with its account_set_key. In a conversation "
                    "filed under a company, a call without account_set_key or company covers only that "
                    "company's account sets (scope names it); all_companies true lifts that. Amounts are as "
                    "recognized, not the figures in Express or MR.ERP.",
     "input_schema": {"type": "object", "properties": {
         "search": {"type": "string"},
         "push_state": {"type": "string", "enum": ["waiting", "not_pushed", "pending", "success", "failed"]},
         "uploaded_from": {"type": "string", "description": "YYYY-MM-DD"},
         "invoice_date_from": {"type": "string", "description": "YYYY-MM-DD"},
         "invoice_date_to": {"type": "string", "description": "YYYY-MM-DD"},
         "connection": {"type": "string"}, "account_set_key": {"type": "string"},
         "direction": {"type": "string", "enum": ["purchase", "sales"]},
         "totals": {"type": "boolean"},
         "offset": {"type": "integer", "minimum": 0},
         "limit": {"type": "integer", "minimum": 1, "maximum": LIST_MAX}, **SCOPE_PROPS}},
     "handler": "pearnly_cowork.assistant.tools.list_documents"},
    {"name": "cowork_push_documents", "label": "Pushed documents",
     "description": "Push recognized documents to their accounting target (Express through CC Desktop, or MR.ERP), "
                    "the same as Submit on the web page. Only when the user asked to push. Up to 20 at a time. After "
                    f"sending it waits up to about {PUSH_WAIT_SECONDS} seconds for the result from Express or MR.ERP (writing "
                    "usually takes a few seconds), so do not wait or check again yourself. Each result says "
                    "entered (written into Express or MR.ERP; docnum is its document number there, tell it to the user), queued "
                    "(no result yet, or CC Desktop is offline: it is entered later; check with cowork_push_results "
                    "when the user asks; last_attempt_failed means a try did not go through and it is tried again), "
                    "failed with a reason, unconfirmed with a reason (Express or MR.ERP may or may not have it: the user checks "
                    "there, then record it with cowork_resolve_push_result; do not push it again), needs_choice "
                    "(get the choices with cowork_push_choices, ask the user, save them and push again), "
                    "waiting_for_cc (the computer is still reading that account set's chart; push again shortly), "
                    "or already_success / already_pending (already entered or on its way; it is not pushed again and "
                    "CC cannot change it, see below). connection and account_set_key choose where to push (from "
                    "cowork_intake_options), e.g. for a recognize-only document; left out, the target chosen at "
                    "upload is used. " + WRITTEN_NOTE,
     "input_schema": {"type": "object", "properties": {
         "documents": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": PUSH_MAX},
         "connection": {"type": "string"}, "account_set_key": {"type": "string"},
         "posting_kind": {"type": "string", "enum": ["service", "stock"], "description": "Express."}},
         "required": ["documents"]},
     "handler": "pearnly_cowork.assistant.tools.push_documents"},
    {"name": "cowork_push_choices", "label": "Checked push choices",
     "description": "Before pushing to Express, what this document still needs a person to choose once in this "
                    "account set (party, product, expense account, payment, VAT claim, department, receiving account "
                    "of a POS payment method; kinds in needed) "
                    "and the allowed values (up to 30 per kind; pass search to narrow by code or name). Use it when a "
                    "push came back needs_choice. Ask the user, then save with cowork_save_push_choices and push "
                    "again. status reading means the computer is still reading that account set's chart: check again "
                    "shortly. For a document from a scheduled folder task pushed to that task's account set, the "
                    "task's default handling (task_defaults) is applied and not asked; to change it, change the task. "
                    "An expense account item may come with suggested accounts (up to 2, best first; source hint = "
                    "matches what the user said, history = this supplier's usual account in this account set over "
                    "the last 12 months, name = the account name matches the document; reason says why): name the "
                    "first one and ask the user to confirm it (a short yes is enough) or pick another; never save a "
                    "suggestion the user has not confirmed. When the user said in the chat what kind of expense this "
                    "document is (e.g. stationery), pass it as hint in the user's own words.",
     "input_schema": {"type": "object", "properties": {
         "document": {"type": "string"}, "connection": {"type": "string"}, "account_set_key": {"type": "string"},
         "posting_kind": {"type": "string", "enum": ["service", "stock"]}, "search": {"type": "string"},
         "hint": {"type": "string", "description": "The expense category the user stated for this document, "
                                                   "in their words."}},
         "required": ["document"]},
     "handler": "pearnly_cowork.assistant.tools.push_choices"},
    {"name": "cowork_save_push_choices", "label": "Saved push choices",
     "description": "Record what the user chose for the items listed by cowork_push_choices: each item is the needed "
                    "entry's kind and subject with the chosen value (a listed code; party and product also accept "
                    "the NEW value given there). The account set reuses them for later documents of the same kind. "
                    "Only values the user chose or confirmed in this chat.",
     "input_schema": {"type": "object", "properties": {
         "document": {"type": "string"}, "connection": {"type": "string"}, "account_set_key": {"type": "string"},
         "items": {"type": "array", "minItems": 1, "items": {"type": "object", "properties": {
             "kind": {"type": "string"}, "subject": {"type": "string"}, "value": {"type": "string"},
             "label": {"type": "string"}}, "required": ["kind", "value"]}}},
         "required": ["document", "items"]},
     "handler": "pearnly_cowork.assistant.tools.save_push_choices"},
    {"name": "cowork_resolve_push_result", "label": "Confirmed a push result",
     "description": "When a push result is unknown (Express or MR.ERP may or may not have it), record what the user "
                    "found after checking there: entered true (with its document number there if they have it) or "
                    "false, so it can be pushed again. Only with the user's own answer; never guess.",
     "input_schema": {"type": "object", "properties": {
         "document": {"type": "string"}, "entered": {"type": "boolean"}, "docnum": {"type": "string"}},
         "required": ["document", "entered"]},
     "handler": "pearnly_cowork.assistant.tools.resolve_push_result"},
    {"name": "cowork_delete_documents", "label": "Deleted documents",
     "description": "Permanently delete documents from the Intake workbench with their original files, like Delete "
                    "in the list; documents already pushed are skipped. When the user clearly named the documents, "
                    "delete them right away (no extra confirmation step); only when it is unclear which ones they "
                    "mean, list them by number, seller and amount and ask first. Returns deleted and skipped counts.",
     "input_schema": {"type": "object", "properties": {
         "documents": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": DELETE_MAX}},
         "required": ["documents"]},
     "handler": "pearnly_cowork.assistant.tools.delete_documents"},
    {"name": "cowork_export_review_workbook", "label": "Exported the review workbook",
     "description": "Export documents (up to 100) to the review workbook, the Excel file accountants check and send "
                    "back on the Intake workbench. The file is added to this conversation at the returned path; copy "
                    "it into the results folder so the user can open and download it, and name it only by file name.",
     "input_schema": {"type": "object", "properties": {
         "documents": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": EXPORT_MAX}},
         "required": ["documents"]},
     "handler": "pearnly_cowork.assistant.tools.export_review_workbook"},
    {"name": "cowork_folder_task_options", "label": "Checked scheduled task options",
     "description": "What a scheduled folder task can use, like the task form: each computer (reader) that reads "
                    "folders, whether it is online, the folders opened to the website in CC Desktop on it (root_id and "
                    "subfolders; up to 50, pass search), and for Express with account_set_key the accounts, departments "
                    "and stock account group for defaults (accounts_status reading: still being read from the computer, "
                    "check again shortly). files is how many photos and PDFs each folder holds now, "
                    "subfolders included (\"\" is the root, 0 means empty; files_partial: at least that many), "
                    "pos_files the POS exports (Excel / CSV) that a sales task into Express also enters; a root "
                    "without files means that computer has not reported counts, so the number is not known. Folders "
                    "are chosen per company in CC Desktop on that computer (the company page); if the one the user "
                    "wants is missing, tell them to choose it there.",
     "input_schema": {"type": "object", "properties": {
         "connection": {"type": "string"}, "account_set_key": {"type": "string"}, "search": {"type": "string"}},
         "required": ["connection"]},
     "handler": "pearnly_cowork.assistant.tools.folder_task_options"},
    {"name": "cowork_folder_files", "label": "Listed folder files",
     "description": "List the files in a folder the user's computer opened to the website (reader and root_id from "
                    "cowork_folder_task_options; folder is a subfolder, empty for the whole root; subfolders "
                    "included): path (relative to the root), name, size_bytes and modified (Bangkok time), newest "
                    "first, 100 per page (offset for the next page). search matches the path; extensions narrows the "
                    "types; truncated means there were more than could be listed, so narrow with folder, search or "
                    "extensions. Only file types that can be brought into the chat are listed. Read-only: nothing is "
                    "recognized or charged. waiting_for_cc means that computer is offline or its CC Desktop needs an "
                    "update (tell the user); reading means it is still listing: call again with the same input, never "
                    "wait with sleep.",
     "input_schema": {"type": "object", "properties": {
         "reader": {"type": "string"}, "root_id": {"type": "string"}, "folder": {"type": "string"},
         "search": {"type": "string"}, "extensions": {"type": "array", "items": {"type": "string"}},
         "offset": {"type": "integer", "minimum": 0},
         "limit": {"type": "integer", "minimum": 1, "maximum": FOLDER_FILES_PAGE_MAX}},
         "required": ["reader", "root_id"]},
     "handler": "pearnly_cowork.assistant.tools.folder_files"},
    {"name": "cowork_folder_fetch", "label": "Brought files from a folder",
     "description": "Bring up to 5 files from a folder the user's computer opened to the website into this chat "
                    "(reader, root_id and paths as listed by cowork_folder_files). Use each returned file like a user "
                    "attachment: for cowork_recon_start (a bank statement, ledger or VAT report), "
                    "cowork_recognize_attachments or cowork_stocktake_create. Up to 30 MB each, the same types as chat "
                    "attachments. Bringing a file in does not recognize it and is not charged. waiting_for_cc as in "
                    "cowork_folder_files; reading means that file is still coming: call again with the same input "
                    "(files already added are not brought in twice), never wait with sleep.",
     "input_schema": {"type": "object", "properties": {
         "reader": {"type": "string"}, "root_id": {"type": "string"},
         "paths": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": FOLDER_FETCH_MAX}},
         "required": ["reader", "root_id", "paths"]},
     "handler": "pearnly_cowork.assistant.tools.folder_fetch"},
    {"name": "cowork_express_readback", "label": "Read back the accounting books",
     "description": "Read one month of an Express account set back from the computer through CC Desktop and add four "
                    "workbooks in the Express report layout to this chat: general_ledger (bank reconciliation "
                    "right_files; income reconciliation left_files), sales_vat (income reconciliation right_files; "
                    "output VAT check left_files), purchase_vat (the input VAT report) and changes (documents Pearnly "
                    "pushed that were changed, cancelled or deleted in Express, and entries made directly in Express). "
                    "Read-only: Express may stay open, nothing is recognized or charged. Use it for Express's own "
                    "figures, after the user says they changed Express themselves, and before reconciling or "
                    "checking tax. connection and account_set_key as listed by cowork_intake_options; period is "
                    "YYYY-MM; accounts narrows the general ledger to those account codes (prefixes), e.g. the bank "
                    "account. Waits up to 90 seconds; reading means the computer is still reading: check with "
                    "cowork_express_readback_status when the user asks, never wait with sleep. waiting_for_cc means "
                    "that computer is offline, its CC Desktop needs an update, or (unreachable) none of the user's own "
                    "computers can open the account set. Only the latest readback of an "
                    "account set and month is kept.",
     "input_schema": {"type": "object", "properties": {
         "connection": {"type": "string"}, "account_set_key": {"type": "string"},
         "period": {"type": "string", "description": "YYYY-MM"},
         "accounts": {"type": "array", "items": {"type": "string"}, "maxItems": 30}},
         "required": ["connection", "account_set_key", "period"]},
     "handler": "pearnly_cowork.assistant.tools.express_readback"},
    {"name": "cowork_express_readback_status", "label": "Checked a readback",
     "description": "A readback started by cowork_express_readback: reading, waiting_for_cc, failed with a reason, or "
                    "ready with the summary, the changes and direct entries (the first 20; all are in the changes "
                    "workbook) and the four workbooks, added to this chat once. Waits up to 90 seconds.",
     "input_schema": {"type": "object", "properties": {"readback": {"type": "string"}}, "required": ["readback"]},
     "handler": "pearnly_cowork.assistant.tools.express_readback_status"},
    {"name": "cowork_express_readback_delete", "label": "Deleted a readback",
     "description": "Delete a readback and its stored workbooks when the user asks. Files already added to a chat stay "
                    "there.",
     "input_schema": {"type": "object", "properties": {"readback": {"type": "string"}}, "required": ["readback"]},
     "handler": "pearnly_cowork.assistant.tools.express_readback_delete"},
    {"name": "cowork_save_folder_task", "label": "Saved a scheduled task",
     "description": "Create or change one scheduled folder task: on run_day at run_time it recognizes, with these "
                    "intake choices, the photos and PDFs in the folder (subfolders included) that it has not "
                    "processed yet, including those already there when it is created; a sales task into Express also "
                    "enters POS exports (Excel / CSV) there as daily sales without recognition; a task whose folder overlaps a "
                    "paused task continues it and only handles files put in after it is saved. Same rules as the web "
                    "form: Express needs posting_kind, MR.ERP needs payment; folders of enabled tasks cannot overlap; the "
                    "accounting program of an existing task cannot change. When a task is changed (name given), values left "
                    "out keep their current ones. Change only the fields the user asked to change; any other value "
                    "you pass must be the task's current one. For two or more tasks use cowork_save_folder_tasks.",
     "input_schema": {"type": "object", "properties": FOLDER_TASK_PROPS,
                      "required": ["title", "connection", "direction", "root_id", "run_day", "run_time"]},
     "approval": True,
     "ask": "Save this scheduled task?",
     "preview": "pearnly_cowork.assistant.tools.preview_save_folder_task",
     "handler": "pearnly_cowork.assistant.tools.save_folder_task"},
    {"name": "cowork_save_folder_tasks", "label": "Saved scheduled tasks",
     "description": "Create or change several scheduled folder tasks at once with one Allow from the user, for example "
                    "one task per folder (purchases, expenses, sales) for each company. Use it whenever two or more "
                    "tasks are saved together. Each task follows the same rules as cowork_save_folder_task: a new "
                    "task needs title, connection, direction, root_id, run_day and run_time; one folder holds one "
                    "direction and one posting kind; folders of enabled tasks, including the others in this list, "
                    "cannot be the same or inside each other. When changing tasks, change only the fields the user "
                    "asked to change; any other value you pass must be the task's current one. All or nothing: if "
                    "any task is refused, nothing is "
                    "saved and failed lists each refused task (its position, title and reason); fix them and call "
                    "again with the whole list. Up to 20 tasks.",
     "input_schema": {"type": "object", "properties": {
         "tasks": {"type": "array", "minItems": 1, "maxItems": FOLDER_BATCH_MAX,
                   "items": {"type": "object", "properties": FOLDER_TASK_PROPS}}},
         "required": ["tasks"]},
     "approval": True,
     "ask": "Save these scheduled tasks?",
     "preview": "pearnly_cowork.assistant.tools.preview_save_folder_tasks",
     "handler": "pearnly_cowork.assistant.tools.save_folder_tasks"},
    # 只改提醒时说「启用或停用」不对（2026-10-13 测试机）：步骤名用中性的「更新了定时任务」
    {"name": "cowork_set_folder_task_enabled", "label": "Updated a scheduled task",
     "description": "Pause or resume a scheduled folder task, turn its reminder on or off, or change its reminder "
                    "times (give enabled, remind, remind_times or several; give only what the user asked to "
                    "change). After resuming, the next run handles the "
                    "files in the folder it has not processed yet, including those put in while it was paused; a task "
                    "with include_existing false only handles files put in after it is resumed. For two or more "
                    "tasks use cowork_set_folder_tasks_enabled.",
     "input_schema": {"type": "object", "properties": SWITCH_PROPS, "required": ["task"]},
     "approval": True,
     # 只改提醒（remind / remind_times）时不问「启用还是停用」，带了 enabled 才问（2026-10-13）
     "ask": "Change this scheduled task?",
     "ask_when": {"enabled": "Turn this scheduled task on or off?"},
     "preview": "pearnly_cowork.assistant.tools.preview_set_folder_task_enabled",
     "handler": "pearnly_cowork.assistant.tools.set_folder_task_enabled"},
    # 一句话改几个任务的提醒或启停只弹一张「允许」卡片（用户 2026-10-13 第 5 条），同批量保存（F4）
    {"name": "cowork_set_folder_tasks_enabled", "label": "Updated scheduled tasks",
     "description": "Pause or resume several scheduled folder tasks, turn their reminders on or off, or change their "
                    "reminder times, with one Allow from the user: one item per task with enabled, remind, "
                    "remind_times or several (give only what the user asked to change). Use it whenever two or more "
                    "tasks change, even when they all get the same change. Each task follows the same rules as "
                    "cowork_set_folder_task_enabled and is done on its own: tasks lists each result (saved, or failed "
                    "with a reason); one failing does not undo the others, so report each failure. Up to "
                    f"{FOLDER_BATCH_MAX} tasks.",
     "input_schema": {"type": "object", "properties": {
         "tasks": {"type": "array", "minItems": 1, "maxItems": FOLDER_BATCH_MAX,
                   "items": {"type": "object", "properties": SWITCH_PROPS, "required": ["task"]}}},
         "required": ["tasks"]},
     "approval": True,
     "ask": "Change these scheduled tasks?",
     "preview": "pearnly_cowork.assistant.tools.preview_set_folder_tasks_enabled",
     "handler": "pearnly_cowork.assistant.tools.set_folder_tasks_enabled"},
    {"name": "cowork_delete_folder_tasks", "label": "Deleted scheduled tasks",
     "description": "Delete scheduled folder tasks with their run history; documents they already created stay on the "
                    "Intake workbench. Only ask which ones when it is unclear.",
     "input_schema": {"type": "object", "properties": {"tasks": {"type": "array", "items": {"type": "string"},
                                                                 "minItems": 1}}, "required": ["tasks"]},
     "approval": True,
     "ask": "Delete these scheduled tasks?",
     "preview": "pearnly_cowork.assistant.tools.preview_delete_folder_tasks",
     "handler": "pearnly_cowork.assistant.tools.delete_folder_tasks"},
    {"name": "cowork_recon_start", "label": "Started a reconciliation",
     "description": "Start a reconciliation like New on the reconciliation pages, with files in this chat: the "
                    "user's attachments, files brought from a folder with cowork_folder_fetch, or workbooks a tool "
                    "added (such as those from cowork_express_readback). bank: left_files = bank statements, "
                    "right_files = general ledger. income: left_files = one general ledger, right_files = output VAT "
                    "reports. vat: left_files = output VAT reports, "
                    "right_files = sales invoices. Excel/CSV and text PDFs are read free; scanned PDFs and photos are "
                    "read with paid recognition per page like invoices, so say so before starting when files are "
                    "scanned. The bank, account and period are worked out from the files. Waits up to 90 seconds; "
                    "if it is still running, tell the user and check again with cowork_recon_status when they ask "
                    "(never wait with sleep). Use this rather than reconciling yourself.",
     "input_schema": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["bank", "income", "vat"], "description": "bank = bank reconciliation, income = income reconciliation, vat = output VAT report check."},
         "left_files": {"type": "array", "items": {"type": "string"}, "minItems": 1},
         "right_files": {"type": "array", "items": {"type": "string"}, "minItems": 1},
         "title": {"type": "string"}}, "required": ["kind", "left_files", "right_files"]},
     "handler": "pearnly_cowork.assistant.tools.recon_start"},
    {"name": "cowork_recon_status", "label": "Checked a reconciliation's progress",
     "description": "Progress of a reconciliation started by cowork_recon_start or a preset answer that re-runs it: "
                    "state, stage, pages read, and the finished task. Waits up to 90 seconds for it to finish.",
     "input_schema": {"type": "object", "properties": {"job": {"type": "string"}}, "required": ["job"]},
     "handler": "pearnly_cowork.assistant.tools.recon_status"},
    {"name": "cowork_recon_list", "label": "Listed reconciliations",
     "description": "The user's reconciliations of one kind, newest first.",
     "input_schema": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["bank", "income", "vat"], "description": "bank = bank reconciliation, income = income reconciliation, vat = output VAT report check."},
         "limit": {"type": "integer", "minimum": 1, "maximum": LIST_MAX}}, "required": ["kind"]},
     "handler": "pearnly_cowork.assistant.tools.recon_list"},
    {"name": "cowork_recon_result", "label": "Viewed a reconciliation",
     "description": "A finished reconciliation: title, status (needs_review means the statement was read from a "
                    "scan and must be checked on the page before export), notes, questions to answer once "
                    "(cowork_recon_answer), the summary counts and amounts, rows_total, unmatched_total and the first 30 "
                    "rows that did not match (for vat: invoices and report lines with a difference or missing on one "
                    "side, with which fields differ). For bank, matched_by_kind sums the matched lines by how they "
                    "were matched (groups, lines and the amounts on each side); when the two sides' totals differ, explain the "
                    "difference from it (for example ledger credits above bank withdrawals that are card fees the ledger "
                    "recorded in the card net-of-fee groups) instead of guessing. The reconciliation compares the "
                    "bank with one GL account only; report its figures as they are and do not tell the user to post "
                    "entries from them (for example fees the user says are booked in another account).",
     "input_schema": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["bank", "income", "vat"], "description": "bank = bank reconciliation, income = income reconciliation, vat = output VAT report check."}, "task": {"type": "string"}},
                      "required": ["kind", "task"]},
     "handler": "pearnly_cowork.assistant.tools.recon_result"},
    {"name": "cowork_recon_answer", "label": "Answered a reconciliation question",
     "description": "Answer one of the result's questions (rule and the allowed values come from the question; for "
                    "example grouped yes/no, gl_account = one of the offered account codes, card_fee = a rate or "
                    "no). It is remembered for that account or company and the result is recomputed; a gl_account "
                    "answer re-runs from the original files (returns rerun_job).",
     "input_schema": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["bank", "income", "vat"], "description": "bank = bank reconciliation, income = income reconciliation, vat = output VAT report check."}, "task": {"type": "string"},
         "rule": {"type": "string"}, "value": {"type": "string"}, "subject": {"type": "string"}},
         "required": ["kind", "task", "rule", "value"]},
     "handler": "pearnly_cowork.assistant.tools.recon_answer"},
    {"name": "cowork_recon_presets", "label": "Checked reconciliation presets",
     "description": "The remembered reconciliation answers of one kind (action list), or change / remove one by "
                    "scope_key and rule as listed.",
     "input_schema": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["bank", "income", "vat"], "description": "bank = bank reconciliation, income = income reconciliation, vat = output VAT report check."},
         "action": {"type": "string", "enum": ["list", "change", "remove"]}, "scope_key": {"type": "string"},
         "rule": {"type": "string"}, "value": {"type": "string"}}, "required": ["kind"]},
     "handler": "pearnly_cowork.assistant.tools.recon_presets"},
    {"name": "cowork_recon_export", "label": "Exported a reconciliation",
     "description": "The reconciliation workbook (Excel) of a completed task, added to this conversation at the "
                    "returned path; copy it into the results folder for the user, naming it only by file name.",
     "input_schema": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["bank", "income", "vat"], "description": "bank = bank reconciliation, income = income reconciliation, vat = output VAT report check."}, "task": {"type": "string"}},
                      "required": ["kind", "task"]},
     "handler": "pearnly_cowork.assistant.tools.recon_export"},
    {"name": "cowork_recon_delete", "label": "Deleted reconciliations",
     "description": "Delete reconciliations the user clearly named (ask only when it is unclear which).",
     "input_schema": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["bank", "income", "vat"], "description": "bank = bank reconciliation, income = income reconciliation, vat = output VAT report check."},
         "tasks": {"type": "array", "items": {"type": "string"}, "minItems": 1}}, "required": ["kind", "tasks"]},
     "handler": "pearnly_cowork.assistant.tools.recon_delete"},
    {"name": "cowork_vat_draft", "label": "Drafted a VAT return",
     "description": "Draft the P.P.30 VAT return of one Express account set and tax month from that month's "
                    "Express readback (its input and output tax records), with the input tax check: seller tax ID "
                    "missing or invalid, claimed before the invoice month or past the 6-month window, abbreviated "
                    "tax invoices and non tax invoices (not claimable), buyer missing or another company, duplicates, "
                    "voided, deleted or other-month rows, and output VAT against the rate. Boxes 1-12 equal the "
                    "Express records; the checks never change them (claimable_if_fixed is the input tax without the "
                    "flagged rows) and boxes 13-16 are only for late or additional filing. When it returns "
                    "readback_needed, run cowork_express_readback for that account set and month first. "
                    "exempt_sales, excess_brought_forward (box 10) and zero_vat_as come from the user and are kept "
                    "for this draft; previous_excess is box 12 of last month's draft. Deterministic and free. Adds "
                    "the workbook (form, output and input tax reports, checks) to this conversation; copy it into "
                    "the results folder. It is a draft: never say it was filed.",
     "input_schema": {"type": "object", "properties": {
         "connection": {"type": "string"}, "account_set_key": {"type": "string"},
         "period": {"type": "string", "description": "Tax month, YYYY-MM."},
         "exempt_sales": {"type": "string", "description": "Exempt sales of the month (box 3), in baht."},
         "excess_brought_forward": {"type": "string", "description": "Box 10, in baht."},
         "zero_vat_as": {"type": "string", "enum": ["zero_rated", "exempt"],
                         "description": "Where output rows with zero VAT go: box 2 (default) or box 3."},
         "workbook": {"type": "boolean", "description": "false skips the workbook; default true."}},
         "required": ["connection", "account_set_key", "period"]},
     "handler": "pearnly_cowork.assistant.tools.vat_draft"},
    {"name": "cowork_wht_draft", "label": "Drafted a withholding tax return",
     "description": "Draft the P.N.D.53 (juristic payees) and P.N.D.3 (individuals) withholding tax "
                    "returns of one paying company (its 13-digit tax ID) for the month the payments were made, from "
                    "recognized expense invoices with tax withheld and 50 Tawi certificates (including those kept "
                    "only as references when certificates are set not to be entered). A payee tax ID starting with 0 "
                    "goes to P.N.D.53, except 0991/0992 which go to P.N.D.3. Invoices and certificates of one payee "
                    "are matched into one payment (one to one, several invoices to one certificate, one invoice to "
                    "several certificates). The payment date is the certificate's, else the invoice date "
                    "(payment_date_assumed: it may have been paid later). questions lists "
                    "payees whose income type is not known: ask the user once, answer with cowork_wht_answer, then "
                    "draft again. review lists rows left out until the user confirms them with include true: "
                    "certificate_direction_unclear (ask whether the company paid them), pairing_unclear (an invoice "
                    "and a certificate of the same payee that do not add up; check_with names the rows that may be "
                    "the same payment: include only one side), buyer_unconfirmed (filed under the company or naming it "
                    "as buyer, but its tax ID is not on the document); amount_unreadable and payee_tax_missing need "
                    "base/tax or payee_tax_id first. excluded lists this month's documents that were read but are "
                    "not on the forms, each with its reason. suggestions are invoices from known payees "
                    "with nothing withheld; include one only when the user says so. ewht_check: 1% applies only to "
                    "payments through e-Withholding Tax, which are not filed on these forms; ask the user. edits "
                    "change rows of this draft and are kept: row (as listed), include, payment_date, income_type, "
                    "income_text, rate, base, tax, condition, payee_tax_id, form; reset true drops a row's edits. "
                    "Deterministic and free. Adds the workbook (summary, both attachments with 6 payees a sheet, "
                    "rows to check) to this conversation. It is a draft: never say it was filed.",
     "input_schema": {"type": "object", "properties": {
         "company_tax_id": {"type": "string", "description": "The paying company's 13-digit tax ID."},
         "period": {"type": "string", "description": "Month paid, YYYY-MM."},
         "company_name": {"type": "string"}, "company_branch": {"type": "string", "description": "00000 = head office."},
         "edits": {"type": "array", "items": {"type": "object", "properties": {
             "row": {"type": "string"}, "reset": {"type": "boolean"}, "include": {"type": "boolean"},
             "payment_date": {"type": "string", "description": "YYYY-MM-DD"},
             "income_type": {"type": "string", "enum": list(tax_contracts.INCOME_CODES)},
             "income_text": {"type": "string"}, "rate": {"type": "string"}, "base": {"type": "string"},
             "tax": {"type": "string"}, "condition": {"type": "string", "enum": ["1", "2", "3"]},
             "payee_tax_id": {"type": "string"}, "form": {"type": "string", "enum": ["pnd3", "pnd53"]}},
             "required": ["row"]}},
         "workbook": {"type": "boolean", "description": "false skips the workbook; default true."}},
         "required": ["company_tax_id", "period"]},
     "handler": "pearnly_cowork.assistant.tools.wht_draft"},
    {"name": "cowork_wht_answer", "label": "Answered a withholding tax question",
     "description": "Remember payees' income types for withholding tax drafts (action set): answers with "
                    "payee_tax_id (or category for payees without one), income_type, income_text for other, form to "
                    "override pnd3/pnd53, condition (1 withheld, 2 paid by the payer every time, 3 paid by the payer "
                    "once; P.N.D.53 has only 1 and 2). Remembered for the whole company workspace; call "
                    "cowork_wht_draft again to apply. action list shows what is remembered; remove forgets one by "
                    "payee_tax_id or category.",
     "input_schema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["set", "list", "remove"]},
         "answers": {"type": "array", "items": {"type": "object", "properties": {
             "payee_tax_id": {"type": "string"}, "category": {"type": "string"}, "payee_name": {"type": "string"},
             "income_type": {"type": "string", "enum": list(tax_contracts.INCOME_CODES)},
             "income_text": {"type": "string"}, "form": {"type": "string", "enum": ["pnd3", "pnd53"]},
             "condition": {"type": "string", "enum": ["1", "2", "3"]}}, "required": ["income_type"]}},
         "draft": {"type": "string"}, "payee_tax_id": {"type": "string"}, "category": {"type": "string"}},
         "required": []},
     "handler": "pearnly_cowork.assistant.tools.wht_answer"},
    {"name": "cowork_tax_draft_delete", "label": "Deleted tax drafts",
     "description": "Delete VAT or withholding tax drafts the user clearly named (the draft from cowork_vat_draft or "
                    "cowork_wht_draft) with their kept options and row edits; ask only when unclear which.",
     "input_schema": {"type": "object", "properties": {
         "drafts": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 50}},
         "required": ["drafts"]},
     "handler": "pearnly_cowork.assistant.tools.tax_draft_delete"},
    {"name": "cowork_stocktake_list", "label": "Listed stock counts",
     "description": "The company's stock counts (stocktakes), newest first.",
     "input_schema": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}}, "required": []},
     "handler": "pearnly_cowork.assistant.tools.stocktake_list"},
    {"name": "cowork_stocktake_template", "label": "Prepared a stock count template",
     "description": "The Excel template for a new stock count (product code, name, unit, book quantity, optional counted quantity), added to this conversation; copy it into the results folder for the user to fill in.",
     "input_schema": {"type": "object", "properties": {}, "required": []},
     "handler": "pearnly_cowork.assistant.tools.stocktake_template"},
    {"name": "cowork_stocktake_create", "label": "Created a stock count",
     "description": "Create a stock count from an Excel file the user attached in this chat (the template's columns; book quantities, counted quantities optional), like New on the stocktake page. Returns the count and rows that were skipped with reasons.",
     "input_schema": {"type": "object", "properties": {"file": {"type": "string"}, "title": {"type": "string"}}, "required": ["file"]},
     "handler": "pearnly_cowork.assistant.tools.stocktake_create"},
    {"name": "cowork_stocktake_report", "label": "Viewed a stock count",
     "description": "Book quantity, counted quantity and difference per item of a stock count; by default only items with a difference or not counted yet (up to 40), all_items for everything.",
     "input_schema": {"type": "object", "properties": {"count": {"type": "string"}, "all_items": {"type": "boolean"}}, "required": ["count"]},
     "handler": "pearnly_cowork.assistant.tools.stocktake_report"},
    {"name": "cowork_stocktake_count", "label": "Recorded a count",
     "description": "Record a counted quantity for a product by code or barcode, like scanning on the page: entries for the same product add up. Warehouse and location default to the product's book position; give them when it was found elsewhere. recount true clears that product's earlier counts first and then records qty.",
     "input_schema": {"type": "object", "properties": {"count": {"type": "string"}, "code": {"type": "string"}, "qty": {"type": "number", "minimum": 0}, "warehouse": {"type": "string"}, "location": {"type": "string"}, "recount": {"type": "boolean"}}, "required": ["count", "code"]},
     "handler": "pearnly_cowork.assistant.tools.stocktake_count"},
    {"name": "cowork_stocktake_close", "label": "Finished a stock count",
     "description": "Finish a stock count; it becomes read-only (export only). Only when the user says counting is done.",
     "input_schema": {"type": "object", "properties": {"count": {"type": "string"}}, "required": ["count"]},
     "handler": "pearnly_cowork.assistant.tools.stocktake_close"},
    {"name": "cowork_stocktake_export", "label": "Exported a stock count",
     "description": "The variance workbook of a stock count, added to this conversation; copy it into the results folder for the user.",
     "input_schema": {"type": "object", "properties": {"count": {"type": "string"}}, "required": ["count"]},
     "handler": "pearnly_cowork.assistant.tools.stocktake_export"},
    {"name": "cowork_stocktake_delete", "label": "Deleted stock counts",
     "description": "Delete stock counts the user clearly named (ask only when unclear which).",
     "input_schema": {"type": "object", "properties": {"counts": {"type": "array", "items": {"type": "string"}, "minItems": 1}}, "required": ["counts"]},
     "handler": "pearnly_cowork.assistant.tools.stocktake_delete"},
    {"name": "cowork_work_boards", "label": "Listed task boards",
     "description": "The team task boards the user can see.",
     "input_schema": {"type": "object", "properties": {}, "required": []},
     "handler": "pearnly_cowork.assistant.tools.work_boards"},
    {"name": "cowork_work_board", "label": "Viewed a task board",
     "description": "One task board (default board when left out): its lists and cards with title, list, due date, done, assignees and labels (up to 100). mine true keeps only cards assigned to the user.",
     "input_schema": {"type": "object", "properties": {"board": {"type": "string"}, "mine": {"type": "boolean"}}, "required": []},
     "handler": "pearnly_cowork.assistant.tools.work_board"},
    {"name": "cowork_work_card", "label": "Viewed a task",
     "description": "A task card's full detail: description, dates, reminder, recurrence, assignees, labels, checklists, attachments, recent activity, and modified (needed to change it).",
     "input_schema": {"type": "object", "properties": {"card": {"type": "string"}}, "required": ["card"]},
     "handler": "pearnly_cowork.assistant.tools.work_card"},
    {"name": "cowork_work_create_card", "label": "Created a task",
     "description": "Create a task card at the end of a list (list id from cowork_work_board).",
     "input_schema": {"type": "object", "properties": {"title": {"type": "string"}, "list": {"type": "string"}}, "required": ["title", "list"]},
     "handler": "pearnly_cowork.assistant.tools.work_create_card"},
    {"name": "cowork_work_update_card", "label": "Updated a task",
     "description": "Change a task card: values may hold title, description, start_date, due_date, due_time, reminder (none, at_due, 5m, 10m, 15m, 1h, 2h, 1d, 2d), recurrence (never, daily, weekly, monthly, yearly), assignees (user ids of board editors), labels, checklists, completed. Pass modified from cowork_work_card so a newer edit by someone else is not overwritten.",
     "input_schema": {"type": "object", "properties": {"card": {"type": "string"}, "modified": {"type": "string"}, "values": {"type": "object"}}, "required": ["card", "modified", "values"]},
     "handler": "pearnly_cowork.assistant.tools.work_update_card"},
    {"name": "cowork_work_move_card", "label": "Moved a task",
     "description": "Move a task card to another list (moving into the done list completes it), or toggle_done to mark it done / not done (a recurring task rolls its due date forward instead).",
     "input_schema": {"type": "object", "properties": {"card": {"type": "string"}, "list": {"type": "string"}, "toggle_done": {"type": "boolean"}}, "required": ["card"]},
     "handler": "pearnly_cowork.assistant.tools.work_move_card"},
    {"name": "cowork_work_comment", "label": "Commented on a task",
     "description": "Add a comment to a task card.",
     "input_schema": {"type": "object", "properties": {"card": {"type": "string"}, "content": {"type": "string"}}, "required": ["card", "content"]},
     "handler": "pearnly_cowork.assistant.tools.work_comment"},
    {"name": "cowork_work_search", "label": "Searched tasks",
     "description": "Search task cards by keyword (title, description, comments, checklist items, field values; at least 2 characters).",
     "input_schema": {"type": "object", "properties": {"keyword": {"type": "string"}, "board": {"type": "string"}}, "required": ["keyword"]},
     "handler": "pearnly_cowork.assistant.tools.work_search"},
    {"name": "cowork_push_results", "label": "Checked push results",
     "description": "Show recent push results: documents whose push failed (with the reason), is pending (on its way "
                    "to Express or MR.ERP), or succeeded (entered). Documents still waiting to be entered (never pushed or "
                    "failed) are push_state waiting in cowork_list_documents. In a conversation filed under a company "
                    "it covers only that company's account sets (scope names it) unless all_companies is true. "
                    + WRITTEN_NOTE,
     "input_schema": {"type": "object", "properties": {
         "push_state": {"type": "string", "enum": ["pending", "success", "failed"]},
         "offset": {"type": "integer", "minimum": 0},
         "limit": {"type": "integer", "minimum": 1, "maximum": LIST_MAX}, **SCOPE_PROPS}, "required": ["push_state"]},
     "handler": "pearnly_cowork.assistant.tools.list_documents"},
    {"name": "cowork_folder_tasks", "label": "Checked scheduled tasks",
     "description": "List the scheduled folder tasks (scan a folder on the user's PC, recognize new files and push them), "
                    "with their schedule, default handling, reminder, last run and whether CC Desktop on the computer "
                    "that reads the folder is online. waiting is how many files in the folder the task has not processed yet "
                    "(waiting_partial: at least that many; null when not known). results counts invoices entered, "
                    "invoices needing attention, and files still moving. reader_gone: the computer that read the "
                    "folder was removed, so the task needs another computer's folder (cowork_folder_task_options). "
                    "In a conversation filed under a company it "
                    "lists only that company's tasks (scope names it) unless all_companies is true.",
     "input_schema": {"type": "object", "properties": dict(SCOPE_PROPS)},
     "handler": "pearnly_cowork.assistant.tools.folder_tasks"},
    {"name": "cowork_run_folder_task", "label": "Started a scheduled task",
     "description": "Run one folder task now. Only when the user asked.",
     "input_schema": {"type": "object", "properties": {"task": {"type": "string"}}, "required": ["task"]},
     "handler": "pearnly_cowork.assistant.tools.run_folder_task"},
    {"name": "cowork_folder_task_runs", "label": "Checked scheduled task runs",
     "description": "Show the recent runs of one folder task: files found, uploaded, skipped and failed, and per run "
                    "how many invoices were entered, need attention, or are still moving (results), and the files in "
                    "each step (states). pos_layout_needed lists POS exports waiting until their columns are known: "
                    "answer with cowork_pos_layout folder_file.",
     "input_schema": {"type": "object", "properties": {"task": {"type": "string"}}, "required": ["task"]},
     "handler": "pearnly_cowork.assistant.tools.folder_task_runs"},
]
