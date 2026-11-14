"""CC 引导卡工具 ``pearnly_guide``（共用层，CC 引导式录入第 1 期；评估 ``docs/work/2026-11-12-cc-guided-entry.md`` 6.2、6.4）。

录单流程里「问什么由后台定」：模型只给要写的单（``{company, doc_ids, hint?}``），**服务端**按写账前置判定（``write_precheck.api.card``，
CC 对话里共用规则自动走引导模式）出题——题目、选项、推荐、已定项、四语文字都由服务端出好，存成这次调用的输入；页面照它渲染、
用户点选后随确认交回（``sessions.confirm`` → ``toolrun.decide``，同 ``ask_user``），本站记下（``write_precheck.api.record(guided=True)``，
卡上没点过的预选不生效），模型只收到「答了几项、哪些单能写、哪些留下」。全部已定就不出卡，直接回可写的单。

- :func:`prepare`：出卡前（``toolrun.execute``）把模型的输入换成卡；不用问回 ``{"result": 文本}``，要问回 ``{"input": 卡}``。
- :func:`check`：抢占卡片前核答案，对不上抛 ``ask.AnswerError``（不占掉卡片）；:func:`result` / :func:`skipped`：抢到之后记下 / 关掉卡片，交回模型的结果。
- :func:`refuse_ask`：``ask_user`` 出卡前的核对（替换原来的纯形状核对）：引导卡挂着、或题目带业务搜索（科目 / 物料 / 对方）时拒绝，
  回给模型「用 pearnly_guide」——业务题只能由后台出（评估 6.4 第 2 条）。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

import frappe
from frappe import _

from pearnly_core.assistant import ask, guided
from pearnly_core.write_precheck import api as precheck_api
from pearnly_core.write_precheck import contracts as card_contracts
from pearnly_core.write_precheck import guide

TOOL = "pearnly_guide"
TOOL_RUN = "Pearnly Assistant Tool Run"

# 给模型看的话（英文，不经 _()）
NOTHING_TO_ASK = ("Nothing to ask the user. Make the write plan (pearnly_write_plan) next with the documents in writable; "
                  "documents in waiting or not_writable are left out: tell the user why in plain words.")
# 不用问、但一张都写不了（都在等电脑读主档 / 写不了）：不叫模型建写入计划（真模型 11-14：销售单在等科目表时说明叫它去建计划）
NOTHING_WRITABLE = ("Nothing to ask the user, and none of these documents can be written now: waiting and not_writable say "
                    "why. Tell the user that in plain words and do not make a write plan for them.")
ASK_REFUSED = ("Questions about writing documents (accounts, items, parties, payment, account sets, whether to enter "
               "a document) are asked by the server: call pearnly_guide with the documents instead of ask_user.")
REFUSED_INSTRUCTION = ("Some answers no longer match Express (a chosen code was removed or changed) and nothing was saved. "
                       "Tell the user briefly and call pearnly_guide again for these documents.")
GUIDE_WAITING = ("A guide card is waiting for the user. Do not ask anything else now; wait for their answer or their "
                 "next message.")


def _text(key: str, params: Dict[str, Any]) -> str:
    """按本轮语言翻一个引导卡词条；``{label}`` / ``{count}`` 换成参数。没有译文（_() 原样回键）时回空，调用方用兜底。"""
    found = _(key)
    if found == key:
        return ""
    for name, value in (params or {}).items():
        found = found.replace("{" + name + "}", str(value))
    return found


def _translate(built: Dict[str, Any]) -> Dict[str, Any]:
    for question in built["questions"]:
        params = question.get("title_params") or {}
        question["title"] = _text(question["title_key"], params) or str(params.get("label") or "")
        for option in question.get("options") or ():
            option["label"] = option.get("name") or _text(option.get("label_key") or "", {}) or option["code"]
            if not option.get("note"):
                # 说明行：推荐来由 / 这个选项是什么（文字键按顺序，翻得到的第一个）；都没有就不出说明行
                found = next((text for text in (_text(key, {}) for key in option.get("note_keys") or ()) if text), "")
                if found:
                    option["note"] = found
            option.pop("note_keys", None)
    for item in built["decided"]:
        item["source_label"] = _text(item.get("source_key") or "", {})
        item["title"] = _text(f"guide.q.{item['kind']}", {"label": item.get("label") or "",
                                                          "count": len(item.get("docs") or ())}) or item.get("label") or ""
    return built


LEDGER_PREFIX = "ledger|"
MISMATCH_PREFIX = "business|"


def _ledger_question(session: str, doc_ids: List[str]) -> Dict[str, Any] | None:
    """本对话账套还没定：卡最前面一道「做不做」题（A 线接缝约定，docs/work/2026-11-12-cc-book-identity.md「账套题改由引导卡承担」）。
    候选就是共用层 ``ledger.view`` 那一份（同右栏、同 pearnly_conversation_ledger），不预选；已定、或产品没有本对话账套的不出。"""
    if not session:
        return None
    from pearnly_core.assistant import ledger

    try:
        found = ledger.view(session)
    except frappe.DoesNotExistError:
        return None
    if found.get("current") or not found.get("options"):
        return None
    options = [{"code": item["id"], "name": item.get("label") or item["id"],
                **({"note": _text("guide.why.covers", {})} if item.get("covers") and _text("guide.why.covers", {}) else {})}
               for item in found["options"]]
    return {"key": LEDGER_PREFIX + session, "kind": "account_set", "group": "book", "tier": "whether",
            "title_key": "guide.q.account_set", "title_params": {"count": len(doc_ids)}, "docs": list(doc_ids),
            "count": len(doc_ids), "options": options, "preselected": "", "remember": False, "free": False}
FOLLOW, SKIP = "follow", "skip"


def _side(direction: str) -> str:
    return "sales" if direction == "sales" else "purchase" if direction in ("purchase", "expense") else ""


def _mismatch(found: Dict[str, Any], business: str) -> Dict[str, Any] | None:
    """选了采购却传了销售票（或反过来，评估 6.3 第 2 行）：这几张一道「做不做」类的题，不预选——按票面写入 / 不写入。
    没选业务、收款、或方向判不出的不出。（方案里的「按另一种业务录」要产品能强制改方向，两产品还没有，第 1 期不出这一项）"""
    if business not in ("purchase", "sales"):
        return None
    docs = [doc for doc, direction in (found.get("directions") or {}).items()
            if _side(direction) and _side(direction) != business]
    if not docs:
        return None
    other = "sales" if business == "purchase" else "purchase"
    params = {"count": len(docs), "label": _text(f"guide.option.direction.{other}", {}) or other}
    return {"key": MISMATCH_PREFIX + other, "kind": "direction", "group": "book", "tier": "whether",
            "title_key": "guide.q.business_mismatch", "title_params": params, "docs": docs, "count": len(docs),
            "options": [{"code": FOLLOW, "label_key": f"guide.option.business.{FOLLOW}"},
                        {"code": SKIP, "label_key": f"guide.option.business.{SKIP}"}],
            "preselected": "", "remember": False, "free": False}


def prepare(tool_input) -> Dict[str, Any]:
    """出卡前：模型给的 ``{company, doc_ids, hint?}`` → 要问 ``{"input": 卡}`` / 不用问 ``{"result": 文本}``。"""
    company, doc_ids = precheck_api._tool_ids(tool_input)
    found = precheck_api.card(company, doc_ids, str((tool_input or {}).get("hint") or ""))
    built = guide.build(found)
    session = str((getattr(frappe.local, "pearnly_assistant", None) or {}).get("session_id") or "")
    mismatch = _mismatch(found, guided.business_of(session))
    if mismatch:
        built["questions"].insert(0, mismatch)
        built["counts"]["questions"] += 1
    book = _ledger_question(session, doc_ids)
    if book:
        built["questions"].insert(0, book)
        built["counts"]["questions"] += 1
    if not guide.needs_card(built):
        writable = guide.writable(found, [])
        return {"result": json.dumps({"status": "nothing_to_ask", "writable": writable,
                                      "not_writable": built["facts"]["not_writable"],
                                      "waiting": built["facts"]["waiting"], "planned": found.get("planned") or {},
                                      "instruction": NOTHING_TO_ASK if writable else NOTHING_WRITABLE},
                                     ensure_ascii=False, default=str)}
    card = _translate(built)
    return {"input": {"company": company, "company_title": found.get("company_title") or "", "doc_ids": doc_ids,
                      "card": card}}


def _check_answers(card: Dict[str, Any], answers) -> List[Dict[str, str]]:
    """页面交来的答案 ``[{key, value, doc_id?}]`` 核对卡片：键要在卡上（题或已定项），值要在选项里（有选项且不是搜索的题）。"""
    if isinstance(answers, dict):
        answers = answers.get("answers")
    if not isinstance(answers, list) or len(answers) > card_contracts.MAX_ANSWERS:
        raise ask.AnswerError("answers must be a list")
    by_key = {q["key"]: q for q in card.get("questions") or ()}
    by_key.update({d["key"]: d for d in card.get("decided") or ()})
    out = []
    for answer in answers:
        if not isinstance(answer, dict):
            raise ask.AnswerError("bad answer")
        key, value = str(answer.get("key") or ""), str(answer.get("value") or "").strip()
        item = by_key.get(key)
        if item is None or not value:
            raise ask.AnswerError("answer not on the card")
        codes = {str(o.get("code") or "") for o in item.get("options") or ()}
        if codes and not item.get("search") and value not in codes:
            raise ask.AnswerError("value not offered")
        row = {"key": key, "value": value}
        if answer.get("doc_id"):
            row["doc_id"] = str(answer["doc_id"])
        out.append(row)
    return out


def check(tool_input, answers) -> List[Dict[str, str]]:
    """抢占卡片前核答案（只核、不记）：对不上卡抛 ``ask.AnswerError``，卡片不被占掉、页面可以重答。"""
    return _check_answers((tool_input or {}).get("card") or {}, answers)


def result(tool_input, rows) -> str:
    """作答（已核过的 ``rows``）：记下（引导口径），回给模型 ``{status, answered, writable, held}``。产品那边不收（选过的值在 Express 里
    刚被删 / 改了）：不记，回 ``refused`` 与原因码，模型照实告诉用户、再调 pearnly_guide 重出卡。"""
    company, doc_ids = str(tool_input.get("company") or ""), list(tool_input.get("doc_ids") or ())
    # 业务不一致那一题是引导卡自己出的（不在产品判定里）：答「不写入」或没答的那几张留下，答「按票面写入」的照常；不交给产品记
    mismatch = next((q for q in ((tool_input or {}).get("card") or {}).get("questions") or ()
                     if str(q.get("key") or "").startswith(MISMATCH_PREFIX)), None)
    kept_back: List[str] = []
    # 账套题（引导卡自己出的）：答了只经 ledger.choose 存进会话（页面上本人点的）；没答或没存上，这批单都留下（写入要先有本对话账套）
    book = next((q for q in ((tool_input or {}).get("card") or {}).get("questions") or ()
                 if str(q.get("key") or "").startswith(LEDGER_PREFIX)), None)
    if book:
        chosen = next((r["value"] for r in rows if r["key"] == book["key"]), "")
        rows = [r for r in rows if r["key"] != book["key"]]
        from pearnly_core.assistant import ledger

        session = book["key"][len(LEDGER_PREFIX):]
        if not chosen or ledger.choose(session, chosen, by="page").get("status") != ledger.SET:
            kept_back = list(doc_ids)
    if mismatch:
        chosen = next((r["value"] for r in rows if r["key"] == mismatch["key"]), "")
        if chosen != FOLLOW:
            kept_back = list(dict.fromkeys([*kept_back, *(mismatch.get("docs") or ())]))
        rows = [r for r in rows if r["key"] != mismatch["key"]]
    found = precheck_api.record(company, doc_ids, rows, guided=True)
    if found.get("errors"):
        return json.dumps({"status": "refused", "errors": found["errors"], "instruction": REFUSED_INSTRUCTION},
                          ensure_ascii=False, default=str)
    writable = [doc for doc in found.get("writable") or [] if doc not in kept_back]
    held = list(dict.fromkeys([*(found.get("held") or []), *kept_back]))
    return json.dumps({"status": "answered", "answered": len(rows),
                       "writable": writable, "held": held}, ensure_ascii=False, default=str)


def skipped(tool_input) -> str:
    """关掉卡片：一项都不记，卡上的单这次都留下（「做不做」与没点过的预选都不生效）。"""
    card = (tool_input or {}).get("card") or {}
    held = list(dict.fromkeys(doc for q in card.get("questions") or () for doc in q.get("docs") or ()))
    return json.dumps({"status": "skipped", "answered": 0, "writable": [], "held": held}, ensure_ascii=False)


def stored_card(session_id: str, tool_use_id: str) -> Dict[str, Any]:
    """页面取这次引导卡（服务端出卡前存进执行记录的输入，`toolrun.execute`）：只认本人、本对话、这一次 pearnly_guide 调用。
    页面拿到的调用参数是模型给的 ``{company, doc_ids}``，卡在这里取。没有回空卡。"""
    from pearnly_core.assistant import sessions

    row = sessions.owned(session_id)
    found = frappe.db.get_value(TOOL_RUN, {"session_id": row.session_id, "tool_use_id": str(tool_use_id or ""),
                                           "user": frappe.session.user, "tool": TOOL}, "tool_input")
    try:
        stored = json.loads(found or "{}")
    except ValueError:
        stored = {}
    return {"card": stored.get("card") or {"questions": [], "decided": []},
            "company_title": stored.get("company_title") or ""}


def _guide_waiting() -> bool:
    session = str((getattr(frappe.local, "pearnly_assistant", None) or {}).get("session_id") or "")
    return bool(session and frappe.db.exists(TOOL_RUN, {"session_id": session, "tool": TOOL,
                                                        "status": "awaiting_approval"}))


def refuse_ask(tool_input) -> str | None:
    """``ask_user`` 出卡前：形状不合（``ask.problem``）照旧拒；引导卡挂着时拒；题目带业务搜索（科目 / 物料 / 对方）时拒并叫用
    pearnly_guide——业务题由后台出，模型不出（自由聊天里的非业务提问不受影响）。"""
    shape = ask.problem(tool_input)
    if shape:
        return shape
    if _guide_waiting():
        return GUIDE_WAITING
    questions = (tool_input or {}).get("questions") or []
    if any(isinstance(q, dict) and isinstance(q.get("search"), dict) for q in questions):
        return ASK_REFUSED
    return None


__all__ = ["ASK_REFUSED", "GUIDE_WAITING", "NOTHING_TO_ASK", "NOTHING_WRITABLE", "REFUSED_INSTRUCTION", "TOOL", "check", "prepare", "refuse_ask",
           "result", "skipped", "stored_card"]
