"""Cowork 读回的引导式（CC 对话里点「读回」，总表 CW-D-RB）：声明 :data:`GUIDES` 与它点名的产品函数。

``GUIDES`` 是纯字面量（闸门按源码读）。科目前缀不问（主会话 11-16）。

只转调读回已有的服务函数（`readback.service`，CC 工具 `cowork_express_readback` 同一组），不另查库、不另写规则：

- 步骤：账套（本对话账套是 Express 就不问；没定、或定的是 MR.ERP 才问，选项只列这家的 Express 账套，选了记进本对话，同右栏选择器）
  → 月份（每次明选，推荐本对话月份）→ 确定 → 结果（在读 / 电脑没连 / 已读回 + 四本表放进对话）。
- 只读：不写账本、不扣费；读回按账套走（账套属本对话公司由写入闸 `assistant.write_scope` 核，别家的拒）。
- 确定时再核一遍本对话账套还是卡上选的那本（之后在右栏换了就退回账套那一步）。

选项函数签名同引擎（`pearnly_core.assistant.guide.run`）：``options(ctx, txt, start, page_length, codes)``；``ctx`` 带本对话
``session``、开始时的 ``company`` / ``ledger`` / ``period``、前面各步的 ``answers``。
"""
from __future__ import annotations

import contextlib

import frappe
from frappe import _

EXPRESS = "express"
DRAFT_SEP = "@"
# 读回服务回的「电脑没来领」原因 → 结果卡上一行事实（已有词条）
WAITING = {"offline": "CC Desktop offline or needs update",
           "update_needed": "CC Desktop offline or needs update",
           "unreachable": "None of your computers with CC Desktop can open this account set"}
READING = "Reading back the accounting books"


@contextlib.contextmanager
def _conversation(ctx):
    """调读回 / 对话账套时带上这段对话（本对话公司与账套都按它认），出来还原。"""
    before = getattr(frappe.local, "pearnly_assistant", None)
    frappe.local.pearnly_assistant = {**(before or {}), "session_id": str(ctx.get("session") or "")}
    try:
        yield
    finally:
        frappe.local.pearnly_assistant = before


def allowed(user) -> bool:
    """菜单上出不出「读回」：有 Cowork 录入功能的人（同 `readback.service.request` 的 `scope.require("intake")`）。"""
    from pearnly_cowork.access import scope

    if not user or user == "Guest":
        return False
    try:
        scope.require("intake")
    except frappe.PermissionError:
        frappe.clear_messages()
        return False
    return True


# ── 账套 ─────────────────────────────────────────────────────────────────────


def _express_ids() -> set:
    """本对话公司里 Express 的账套编号（同本对话账套的编号写法）。"""
    from pearnly_cowork.assistant import write_scope
    from pearnly_cowork.companies import provider
    from pearnly_cowork.connections import service as connections

    found = write_scope.allowed() or {}
    out = set()
    for item in found.get("sets") or ():
        connection = str(item.get("connection") or "")
        if connection and connections.adapter_of(connection) == EXPRESS:  # loop-query: 一家公司的账套（有界小表）
            out.add(str(item.get("identity") or provider.ledger_id(connection, item.get("key"))))
    return out


def _books(ctx) -> dict:
    """``{"current": 本对话账套编号, "options": [{code, name}]}``：只列 Express 的。"""
    from pearnly_core.assistant import ledger

    with _conversation(ctx):
        view = ledger.view(str(ctx.get("session") or ""))
        express = _express_ids()
    return {"current": view.get("current") or "",
            "options": [{"code": item["id"], "name": item["label"]} for item in view.get("options") or ()
                        if item["id"] in express]}


def book_default(ctx):
    """本对话账套是 Express 就不问。"""
    found = _books(ctx)
    current = next((row for row in found["options"] if row["code"] == found["current"]), None)
    return {"value": current["code"], "value_label": current["name"], "source": "conversation"} if current else None


def book_options(ctx, txt="", start=0, page_length=20, codes=None):
    rows = _books(ctx)["options"]
    if codes is not None:
        wanted = {str(code) for code in codes}
        return {"options": [row for row in rows if row["code"] in wanted], "more": False}
    if not rows:
        return {"options": [], "more": False, "facts": [_("Only Express account sets can be read back.")]}
    text = str(txt or "").strip().casefold()
    if text:
        rows = [row for row in rows if text in row["name"].casefold()]
    start, size = max(0, int(start or 0)), max(1, int(page_length or 20))
    return {"options": rows[start:start + size], "more": len(rows) > start + size}


def book_apply(ctx, value) -> None:
    """选了账套：记进本对话（同右栏账套选择器，`assistant.ledger.choose(by="page")`）。"""
    from pearnly_core.assistant import ledger

    chosen = ledger.choose(str(ctx.get("session") or ""), value, by="page")
    if chosen.get("status") != "set":
        frappe.throw(_("Invalid value"))


# ── 月份 ─────────────────────────────────────────────────────────────────────


def period_default(ctx):
    """推荐本对话月份（月份每次明选：推荐预选、点了才算）。"""
    period = str(ctx.get("period") or "")
    return {"value": period, "value_label": period, "source": "conversation"} if period else None


def period_check(ctx, value) -> str:
    from pearnly_cowork.readback import contracts

    try:
        contracts.period(value)
    except contracts.ContractError as error:
        return error.code
    return ""


# ── 草稿（只记卡上选了什么，不读）、确定、结果 ──────────────────────────────────────────


def draft(ctx, values) -> dict:
    """答完了：没有要建的草稿，只把选定的「账套@月份」记下给确定与结果用（不调电脑、不建任何记录）。"""
    return {"draft": f"{values['book']}{DRAFT_SEP}{values['period']}"}


def place(ctx, book):
    """本对话账套此刻写在哪一处 (连接, 账套键)；已不是卡上选的那本回 None。"""
    from pearnly_cowork.assistant import write_scope

    with _conversation(ctx):
        pin = write_scope.pinned()
        if not pin or not pin.get("key") or pin.get("id") != book:
            return None
        return write_scope.place(pin)


def confirm(ctx, values, draft, consent) -> dict:
    """点了「确定」：请电脑读这本账套这个月（`readback.service.request`）。本对话账套之后换了就退回账套那一步；
    服务拒绝（账套不在目录、不是 Express）也退回账套那一步，说明照服务的原因。"""
    from pearnly_cowork.readback import service as readback

    book, period = values["book"], values["period"]
    found = place(ctx, book)
    if found is None:
        return {"returned": {"book": {"reason": "not_this_conversation_book"}}}
    with _conversation(ctx):
        try:
            made = readback.request(found[0], found[1], period, None)
        except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
            frappe.db.rollback()
            frappe.clear_messages()
            return {"returned": {"book": {"reason": "readback_refused", "text": str(error)[:300]}}}
    return {key: made[key] for key in ("readback", "status", "reason") if made.get(key)}


def result(ctx, draft):
    """结果卡：在读（页面过一会儿再取）/ 电脑没来领（一行事实）/ 读完（四本表放进对话）/ 失败（原因）。
    确定时的回包由引擎放在 ``ctx["done"]``；还没有（引擎未传）回 None，引擎照存下的确定结果显示。"""
    from pearnly_core.assistant import files as chat_files

    from pearnly_cowork.assistant import readback_files
    from pearnly_cowork.readback import service as readback

    done = ctx.get("done") or {}
    if not isinstance(done, dict) or not done.get("status"):
        return None
    if done["status"] == "waiting_for_cc":
        return {"status": "failed", "reason_text": _(WAITING.get(done.get("reason"), WAITING["offline"]))}
    name = str(done.get("readback") or "")
    if not name:
        return None
    with _conversation(ctx):
        try:
            state = readback.status(name)
        except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
            frappe.clear_messages()
            return {"status": "failed", "reason_text": str(error)[:300]}
        if state["status"] == "reading":
            out = {"status": "queued", "wait": True, "reason_text": _(READING)}
            if state.get("reason"):
                out["reason_text"] = _(WAITING.get(state["reason"], WAITING["offline"]))
            return out
        if state["status"] != "ready":
            return {"status": "failed", "reason_text": _(state.get("code") or "failed")}
        added = readback_files.into_conversation(state, str(ctx.get("session") or ""))
        files = [{"file": chat_files.resolve(path), "name": path.rsplit("/", 1)[-1]}  # loop-query: 最多四本表
                 for path in added.values()]
    return {"status": "ready", "account_set": state.get("account_set") or "", "period": state.get("period") or "",
            "files": files}


# ── 声明 ─────────────────────────────────────────────────────────────────────

GUIDES = [
    {"id": "readback", "label_key": "guide.business.readback", "icon": "download", "order": 40,
     "modes": ["chat"], "ledgers": ["express_local", "express_cloud"],
     "allowed": "pearnly_cowork.assistant.guided.readback.allowed",
     "opening_key": "guide.open.readback", "source": "form", "writes": False,
     "steps": [
         {"key": "book", "input": "choice", "rule": "auto", "search": True,
          "options": "pearnly_cowork.assistant.guided.readback.book_options", "default": "pearnly_cowork.assistant.guided.readback.book_default",
          "apply": "pearnly_cowork.assistant.guided.readback.book_apply"},
         {"key": "period", "input": "month", "rule": "always", "after": ["book"],
          "default": "pearnly_cowork.assistant.guided.readback.period_default", "check": "pearnly_cowork.assistant.guided.readback.period_check"},
     ],
     "draft": "pearnly_cowork.assistant.guided.readback.draft", "confirm": "pearnly_cowork.assistant.guided.readback.confirm",
     "result": "pearnly_cowork.assistant.guided.readback.result",
     "tool": "cowork_express_readback", "expires": ["ledger", "company"]},
]
