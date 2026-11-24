"""引导的 documents 出题源：按单据办的业务（采购、销售）怎么问、怎么复核、确定之后怎么分叉（多产品共用；约定
「引导式-documents源-约定-11-16」，总设计 2.4 / 2.5）。

两产品、直写 / 代录、点菜单 / 打字**同一套**：

1. 出卡与记答（第 1 期 ``pearnly_guide`` 那张卡，原样搬来，打字路径 ``assistant.guide_tool`` 与引导运行都调这里）：
   :func:`card_for` 按写账前置判定（``write_precheck.api.card``）出题，``write_precheck.guide.build`` 分三档，卡前加「本对话账套」
   与「业务对不上」两题，按本轮语言翻好；:func:`check_answers` 核答案（键在卡上、值在选项里）；:func:`record` 记下
   （账套 ``ledger.choose(by="page")``、业务题本地判、其余 ``write_precheck.api.record(guided=True)``：卡上没点过的预选不生效）。
2. 引导运行里的一次（``guide.run`` 按声明的 ``source`` 分到这里）：交文件（声明唯一那步 ``file``）→ 产品 ``intake`` 交识别 →
   产品 ``uploads`` 报识别到哪 → 题卡（1）→ 复核（产品 ``review`` / ``edit``，票面形状照 ``write_precheck.review``）→ 确定 →
   结果（产品 ``result``）。阶段由记录现算（:func:`stage`），读的时候不写库（GET 不提交）。
3. 确定（:func:`confirm`）：同意凭证是本人在页面上点的这一下（``guide:<运行>:<版本>``）；放行走写入计划同一份代码
   （``write_plan.from_page``：逐张重算判定、只写判定可写的）；**之后按会话模式分叉**——直写调声明的 ``confirm``（产品提交 / 推送），
   代录交 ``express_operator.api.hand_in``（声明不分叉，总设计 2.5）。

产品函数签名（声明点名，``ctx`` 同引擎）::

    intake(ctx, file_ids) -> {"batches": [..], "refused"?: {文件: 原因码}}
    uploads(ctx, batches) -> {"files": [{batch, state, reason?}], "doc_ids": [..], "wait": bool}
    review(ctx, doc_ids) -> {"rows": [{doc_id, version, face, marks, cells, blocking}]}
    edit(ctx, doc_id, field, value) -> 改后的一行 | {"refused": 原因码}
    confirm(ctx, doc_ids, consent) -> {"status", "docs": [{doc_id, status, reason?, reason_text?}]}
    result(ctx, doc_ids) -> {"rows": [{doc_id, status, docnum?, reason_text?}], "wait": bool}
"""
from __future__ import annotations

import functools
import hashlib
import json
from typing import Any, Dict, List, Mapping

import frappe
from frappe import _

from pearnly_core.assistant import guided
from pearnly_core.write_precheck import api as precheck_api
from pearnly_core.write_precheck import contracts as card_contracts
from pearnly_core.write_precheck import guide

FILE_KEY = "documents"          # 声明里交文件那一步的 key（documents 声明只列这一步）
FILES, READING, ASKING, REVIEW = "files", "reading", "asking", "review"
LEDGER_PREFIX = "ledger|"
MISMATCH_PREFIX = "business|"
FOLLOW, SKIP = "follow", "skip"
BLOCKING = "blocking"           # 复核里有写不进账本的格（单号空、日期不是日期、金额不是数字）：这一张这次不写


def in_conversation(fn):
    """引导运行里的一步（``fn(guide_decl, doc, ctx, ...)``）在这次引导的那段对话里做（`assistant.guided.within`）：写账判定与写入
    都按引导口径（没点过的预选不生效、判不出的出题）。页面接口（看卡、作答、改一格、确定、搜索）不是工具调用，不进对话就走网页口径——
    卡上判「可写」不出题，确定后在对话里推送却要人选（11-23 Cowork 直写销售：付款方式、收入科目被跳过）。"""
    @functools.wraps(fn)
    def wrapped(guide_decl, doc, ctx, *args, **kwargs):
        with guided.within(str((ctx or {}).get("session") or "")):
            return fn(guide_decl, doc, ctx, *args, **kwargs)
    return wrapped


class AnswerError(ValueError):
    """答案对不上卡（键不在卡上、值不在选项里）：不记、不占卡，页面可以重答。"""


# ── 文字 ─────────────────────────────────────────────────────────────────────


def _text(key: str, params: Mapping[str, Any]) -> str:
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


def reason_text(code: str) -> str:
    """留下 / 不收的原因码 → 按本轮语言的一句（共用原因表 `guide.reasons.text`）；没有现成说法回空（页面只显示状态，不按码造字）。"""
    from pearnly_core.assistant.guide import reasons

    return reasons.text(code)


# ── 卡前两题 ─────────────────────────────────────────────────────────────────


def ledger_question(session: str, doc_ids: List[str]) -> Dict[str, Any] | None:
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


def _side(direction: str) -> str:
    return "sales" if direction == "sales" else "purchase" if direction in ("purchase", "expense") else ""


def mismatch_question(found: Dict[str, Any], business: str) -> Dict[str, Any] | None:
    """选了采购却传了销售票（或反过来，评估 6.3 第 2 行）：这几张一道「做不做」类的题，不预选——按票面写入 / 不写入。
    没选业务、收款、或方向判不出的不出。（方案里的「按另一种业务录」要产品能强制改方向，两产品还没有，不出这一项）"""
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


# ── 出卡、核答、记答（打字路径与引导运行共用）────────────────────────────────


def card_for(company: str, doc_ids: List[str], session: str, business: str, hint: str = "",
             mismatch_answered: bool = False) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """这批单的引导卡：(判定原样, 翻好的卡)。卡的每题带 ``input: choice``（引擎九种输入之一）。``mismatch_answered``：
    这次引导里「业务对不上」已经答过，不再出。"""
    with guided.within(session):  # 出题与之后的记答案、开始前判定、写账同一口径（引导口径）
        found = precheck_api.card(company, doc_ids, hint)
    built = guide.build(found)
    mismatch = None if mismatch_answered else mismatch_question(found, business)
    if mismatch:
        built["questions"].insert(0, mismatch)
        built["counts"]["questions"] += 1
    book = ledger_question(session, doc_ids)
    if book:
        built["questions"].insert(0, book)
        built["counts"]["questions"] += 1
    for question in built["questions"]:
        question.setdefault("input", "choice")
    return found, _translate(built)


def needs_card(built: Mapping[str, Any]) -> bool:
    return guide.needs_card(built)


def check_answers(card: Mapping[str, Any], answers) -> List[Dict[str, str]]:
    """页面交来的答案 ``[{key, value, doc_id?}]`` 核对卡片：键要在卡上（题或已定项），值要在选项里（有选项且不是搜索的题）。"""
    if isinstance(answers, dict):
        answers = answers.get("answers")
    if not isinstance(answers, list) or len(answers) > card_contracts.MAX_ANSWERS:
        raise AnswerError("answers must be a list")
    by_key = {q["key"]: q for q in card.get("questions") or ()}
    by_key.update({d["key"]: d for d in card.get("decided") or ()})
    out = []
    for answer in answers:
        if not isinstance(answer, dict):
            raise AnswerError("bad answer")
        key, value = str(answer.get("key") or ""), str(answer.get("value") or "").strip()
        item = by_key.get(key)
        if item is None or not value:
            raise AnswerError("answer not on the card")
        codes = {str(o.get("code") or "") for o in item.get("options") or ()}
        # 带 entry 的选项必须连数一起答「码:数」（数的口径同共用记答 `contracts.entry_answer`）；标了 free 的题收自己填的字
        plain = codes - {str(o.get("code") or "") for o in item.get("options") or () if isinstance(o.get("entry"), Mapping)}
        entered = card_contracts.entry_answer(item.get("options"), value)
        if codes and not item.get("search") and value not in plain and entered is None and not item.get("free"):
            raise AnswerError("value not offered")
        row = {"key": key, "value": entered or value}
        if answer.get("doc_id"):
            row["doc_id"] = str(answer["doc_id"])
        out.append(row)
    return out


def record(card: Mapping[str, Any], company: str, doc_ids: List[str], rows: List[Dict[str, str]]) -> Dict[str, Any]:
    """记下已核过的答案（引导口径）：``{status: answered, answered, writable, held, mismatch?}``；产品那边不收（选过的值在账本里
    刚被删 / 改了）回 ``{status: refused, errors}``，一项都不记。"""
    questions = card.get("questions") or ()
    mismatch = next((q for q in questions if str(q.get("key") or "").startswith(MISMATCH_PREFIX)), None)
    kept_back: List[str] = []
    # 账套题：答了只经 ledger.choose 存进会话（页面上本人点的）；没答或没存上，这批单都留下（写入要先有本对话账套）
    book = next((q for q in questions if str(q.get("key") or "").startswith(LEDGER_PREFIX)), None)
    if book:
        chosen = next((r["value"] for r in rows if r["key"] == book["key"]), "")
        rows = [r for r in rows if r["key"] != book["key"]]
        from pearnly_core.assistant import ledger

        session = book["key"][len(LEDGER_PREFIX):]
        if not chosen or ledger.choose(session, chosen, by="page").get("status") != ledger.SET:
            kept_back = list(doc_ids)
        # 选的账套当场提交：下面交产品记答案要先判卡片，产品「只是看」的回滚会把没提交的账套冲掉（11-19 回滚审计 P1）
        frappe.db.commit()
    answered_mismatch = ""
    if mismatch:
        # 业务不一致那一题是卡自己出的（不在产品判定里）：答「不写入」或没答的那几张留下，答「按票面写入」的照常；不交给产品记
        answered_mismatch = next((r["value"] for r in rows if r["key"] == mismatch["key"]), "")
        if answered_mismatch != FOLLOW:
            kept_back = list(dict.fromkeys([*kept_back, *(mismatch.get("docs") or ())]))
        rows = [r for r in rows if r["key"] != mismatch["key"]]
    found = precheck_api.record(company, doc_ids, rows, guided=True)
    if found.get("errors"):
        return {"status": "refused", "errors": found["errors"]}
    writable = [doc for doc in found.get("writable") or [] if doc not in kept_back]
    held = list(dict.fromkeys([*(found.get("held") or []), *kept_back]))
    out = {"status": "answered", "answered": len(rows) + (1 if book else 0) + (1 if answered_mismatch else 0),
           "writable": writable, "held": held}
    if mismatch:
        out["mismatch"] = answered_mismatch or SKIP
        out["mismatch_docs"] = list(mismatch.get("docs") or ())
    return out


def held_on_card(card: Mapping[str, Any]) -> List[str]:
    """关掉卡片（跳过）时这次都留下的单。"""
    return list(dict.fromkeys(doc for q in card.get("questions") or () for doc in q.get("docs") or ()))


# ── 引导运行里的一次 ───────────────────────────────────────────────────────────


AT = (ASKING, REVIEW)            # 已有单据交进来时从哪一步接着走（:func:`initial` 的 ``at``）
DOCS_MAX = 200                   # 一次引导最多几张（同写入计划 DIRECT_MAX）
OPERATOR_DOCS_MAX = 1000         # 代录开始前：这个月的完整待录清单（同 `express_operator.api.SOURCES_MAX`）


def initial(doc_ids=None, at: str = ASKING, operator: Mapping[str, Any] | None = None) -> str:
    """新开一次的 ``documents`` 列：文件、识别批次、问过的题、留下的单、业务题答了什么。

    ``doc_ids``（公开入口，别的引导 / 代录开始前交来已有的单：待处理改票面、收银日汇总建好的票、这个月的待录清单）：不交文件、
    不等识别，从判定（``at=asking``：出题卡）或复核（``at=review``：不出题，题没定的那几张这次留下）接着走；不出「业务对不上」题
    （单据不是这次按菜单交的票）。

    ``operator``（代录开始前那张卡，`express_operator.guide_start`）：``{task, period}``；单据可以是空的（这个月没有可录的，卡上问月份）。"""
    found = {"files": [], "batches": [], "asked": [], "held": [], "mismatch": ""}
    if doc_ids is not None:
        ids = list(dict.fromkeys(str(doc_id) for doc_id in doc_ids if doc_id))
        if (not ids and not operator) or len(ids) > (OPERATOR_DOCS_MAX if operator else DOCS_MAX) or at not in AT:
            frappe.throw(_("Invalid request"))
        found.update(doc_ids=ids, preset=True, at=at, mismatch=SKIP)
    if operator:
        found.update(operator={"task": str(operator.get("task") or ""), "period": str(operator.get("period") or "")}, again=[])
    return json.dumps(found)


def check_handed(company: str, doc_ids: List[str], session: str = "") -> None:
    """交进来的已有单据先核（`run.start` 开运行之前，核不过开着的那次不动）：写账判定只回本人能写、这家公司的单，判定里一张都
    没出现的（不是本人的、不是这家公司的、找不到）拒，抛 ``PermissionError``。已推送 / 等电脑 / 写不了的照开，卡上的事实行说明。
    ``session``：这次引导的对话（判定在对话里做，同出题）。"""
    if not doc_ids:
        return
    with guided.within(session):
        found = precheck_api.card(company, list(doc_ids))
    seen = {str(doc_id) for doc_id in found.get("ready") or ()}
    for item in card_contracts.items_of(found):
        seen.update(str(doc_id) for doc_id in item.get("docs") or ())
    for key in (card_contracts.WAITING, card_contracts.NOT_WRITABLE, card_contracts.UNKNOWN):
        seen.update(str(fact.get("doc_id")) for fact in found.get(key) or () if isinstance(fact, Mapping))
    if any(str(doc_id) not in seen for doc_id in doc_ids):
        frappe.throw(_("Not permitted"), frappe.PermissionError)


def state(doc) -> Dict[str, Any]:
    try:
        found = json.loads(doc.get("documents") or "{}")
    except ValueError:
        found = {}
    return {"files": [], "batches": [], "asked": [], "held": [], "mismatch": "", **(found if isinstance(found, dict) else {})}


def _save_state(doc, value: Mapping[str, Any]) -> None:
    doc.documents = json.dumps(dict(value), ensure_ascii=False, default=str)


def _call(path: str, *args, **kwargs):
    return frappe.get_attr(path)(*args, **kwargs)


def _fingerprint(review_rows: List[Mapping[str, Any]], versions: Mapping[str, str]) -> str:
    """复核时看到的样子（每张票面版本 + 写账判定摘要）：确定时再算一次，不一样就不办、刷新复核卡（别处改过）。"""
    raw = json.dumps({"rows": [[row.get("doc_id"), row.get("version")] for row in review_rows],
                      "judged": dict(sorted(versions.items()))}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def stage(guide_decl: Mapping[str, Any], doc, ctx: Dict[str, Any]) -> Dict[str, Any]:
    """这次此刻走到哪（现算，不写库）：``{stage, uploads?, doc_ids, card?, found?, review_docs?, held}``。"""
    st = state(doc)
    if st.get("preset"):
        uploads, doc_ids = None, list(st["doc_ids"])   # 别的引导交来的已有单据：不交文件、不等识别
    elif not st["batches"]:
        return {"stage": FILES, "doc_ids": [], "held": []}
    else:
        uploads = _call(guide_decl["uploads"], ctx, st["batches"]) or {}
        doc_ids = [str(item) for item in uploads.get("doc_ids") or () if item]
        if uploads.get("wait") or not doc_ids:
            return {"stage": READING, "uploads": uploads, "doc_ids": doc_ids, "held": []}
    held = [doc_id for doc_id in st["held"] if doc_id in doc_ids]
    if doc_ids:
        found, built = card_for(ctx["company"], doc_ids, ctx["session"], ctx["business"],
                                mismatch_answered=bool(st.get("mismatch")))
    else:
        found, built = {}, {"questions": [], "decided": [], "counts": {"questions": 0}, "facts": {"waiting": [], "not_writable": []}}
    if st.get("operator"):
        # 代录开始前：这次代录自己的题（再录不录、入没入账、这个月没单时改月份）排最前；还挂着这几题的单这次先不录
        from pearnly_core.express_operator import guide_start

        own = guide_start.questions(ctx, doc_ids, st["operator"], st.get("again") or [], st.get("follow") or [])
        built["questions"][:0] = own
        built["counts"]["questions"] = len(built["questions"])
        held = list(dict.fromkeys([*held, *(d for q in own for d in q["docs"])]))
    elif doc_ids and ctx.get("mode") != "chat":
        # 代录对话里按菜单交的票：票面不是本对话月份的那几张先问照录不照录（P4 11-24；原来确定时交不进任务，整批「请求无效」）
        from pearnly_core.express_operator import guide_start

        task = guide_start.conversation_task(ctx)
        own = guide_start.other_month(ctx, doc_ids, task, st.get("follow") or []) if task else []
        built["questions"][:0] = own
        built["counts"]["questions"] = len(built["questions"])
        held = list(dict.fromkeys([*held, *(d for q in own for d in q["docs"])]))
    # 还有没问过的题就接着问：记录自己的题（付款、行、仓）定了以后账本那边的题（科目、对方……）才判得出来，会是第二轮新题；
    # 问过、这次没点的题不再追问（第 1 期口径：没点过的那几张这次留下）
    fresh = [q for q in built["questions"] if q["key"] not in set(st["asked"])]
    if fresh and (needs_card(built) or built["questions"]) and st.get("at") != REVIEW:
        return {"stage": ASKING, "uploads": uploads, "doc_ids": doc_ids, "card": built, "found": found, "held": held}
    writable = [doc_id for doc_id in guide.writable(found, []) if doc_id in doc_ids and doc_id not in held] if found else []
    return {"stage": REVIEW, "uploads": uploads, "doc_ids": doc_ids, "card": built, "found": found,
            "review_docs": writable, "held": [doc_id for doc_id in doc_ids if doc_id not in writable]}


def _facts(found: Mapping[str, Any]) -> Dict[str, Any]:
    return {key: list(found.get(key) or ()) for key in (card_contracts.WAITING, card_contracts.NOT_WRITABLE) if found.get(key)}


@in_conversation
def view(guide_decl: Mapping[str, Any], doc, ctx: Dict[str, Any]) -> Dict[str, Any]:
    """给页面的那一段（``guide.run.view`` 并进去）：交文件 / 识别中 / 题卡 / 复核。"""
    now = stage(guide_decl, doc, ctx)
    out: Dict[str, Any] = {"stage": now["stage"]}
    operator = state(doc).get("operator")
    if operator:
        from pearnly_core.express_operator import guide_start

        out["operator"] = guide_start.card_head(ctx, now["doc_ids"], operator)
    if now["stage"] == FILES:
        out["card"] = {"version": 2, "source": "documents", "ready": False, "decided": [],
                       "questions": [{"key": FILE_KEY, "input": "file", "rule": "always", "tier": "how", "required": True,
                                      "title_key": guide_decl["opening_key"]}]}
        return out
    if now.get("uploads"):
        out["uploads"] = {"files": list(now["uploads"].get("files") or ()), "wait": bool(now["uploads"].get("wait"))}
    if now["stage"] == READING:
        out["card"] = {"version": 2, "source": "documents", "ready": False, "decided": [], "questions": [], "wait": True}
        return out
    facts = _facts(now.get("found") or {})
    if now["stage"] == ASKING:
        out["card"] = {**now["card"], "version": 2, "source": "documents", "ready": False}
        return out
    rows = (_call(guide_decl["review"], ctx, now["review_docs"]) or {}).get("rows") or [] if now["review_docs"] else []
    _card, versions = _judge(ctx["company"], now["review_docs"])
    out["review"] = {"rows": rows, "held": now["held"], "seen": _fingerprint(rows, versions),
                     "ready": any(not row.get("blocking") for row in rows), **({"facts": facts} if facts else {})}
    return out


def _judge(company: str, doc_ids: List[str]):
    if not doc_ids:
        return {}, {}
    from pearnly_core.assistant import write_plan

    return write_plan._judge(company, list(doc_ids))


@in_conversation
def answer(guide_decl: Mapping[str, Any], doc, ctx: Dict[str, Any], given: Mapping[str, Any]) -> Dict[str, Any]:
    """作答（锁着这一行调）：交文件 ``{"documents": [文件 id]}`` 或题卡 ``{"answers": [{key, value, doc_id?}]}``。
    回 ``{}`` = 收下；``{"refused": {键: 原因}}`` = 不收。改的是 ``doc.documents``，版本由调用方 +1、存。"""
    st = state(doc)
    if FILE_KEY in given:
        files = [str(item) for item in given.get(FILE_KEY) or () if item]
        if not files or st["batches"]:
            return {"refused": {FILE_KEY: "not offered"}}
        from pearnly_core.assistant import files as chat_files, sessions

        chat_files.commit(sessions.owned(ctx["session"]), files, via="guide")
        made = _call(guide_decl["intake"], ctx, files) or {}
        if not made.get("batches"):
            return {"refused": {FILE_KEY: "not accepted"}, "detail": made.get("refused") or {}}
        _save_state(doc, {**st, "files": files, "batches": list(made["batches"]),
                          **({"refused": made["refused"]} if made.get("refused") else {})})
        return {}
    now = stage(guide_decl, doc, ctx)
    if now["stage"] != ASKING:
        return {"refused": {"answers": "not offered"}}
    try:
        rows = check_answers(now["card"], given.get("answers") or [])
    except AnswerError as error:
        return {"refused": {"answers": str(error)}}
    extra: Dict[str, Any] = {"again": [], "held": [], "follow": []}
    if st.get("operator"):
        from pearnly_core.express_operator import guide_start

        mine = [row for row in rows if guide_start.own(row["key"])]
        rows = [row for row in rows if not guide_start.own(row["key"])]
        extra = guide_start.apply(ctx, mine, st["operator"])
        if extra.get("month"):
            return {"restart": True}  # 改了月份：按新月份重开这张卡（引擎重开）
    elif any(row["key"].startswith("operator-") for row in rows):
        # 代录对话里按菜单交的票：卡上只会有别月那一题（:func:`stage`），照开始卡同一个落法
        from pearnly_core.express_operator import guide_start

        mine = [row for row in rows if guide_start.own(row["key"])]
        rows = [row for row in rows if not guide_start.own(row["key"])]
        task = guide_start.conversation_task(ctx)
        if task:
            extra = guide_start.apply(ctx, mine, task)
    card = {**now["card"], "questions": [q for q in now["card"].get("questions") or ()
                                          if not str(q.get("key") or "").startswith(("operator-", ))]}
    found = record(card, ctx["company"], now["doc_ids"], rows) if now["doc_ids"] else {"status": "answered", "held": []}
    if found["status"] == "refused":
        return {"refused": {"answers": "changed"}, "errors": found["errors"]}
    asked = list(dict.fromkeys([*st["asked"], *(q["key"] for q in now["card"].get("questions") or ())]))
    _save_state(doc, {**st, "asked": asked, "held": list(dict.fromkeys([*found["held"], *extra["held"]])),
                      "follow": list(dict.fromkeys([*(st.get("follow") or []), *(extra.get("follow") or [])])),
                      "again": list(dict.fromkeys([*(st.get("again") or []), *extra["again"]])),
                      "mismatch": found.get("mismatch") or st.get("mismatch")})
    return {}


@in_conversation
def edit(guide_decl: Mapping[str, Any], doc, ctx: Dict[str, Any], doc_id: str, field: str, value: Any) -> Dict[str, Any]:
    """复核卡就地改一格（锁着这一行调）：只收复核里列着的票；产品写回自己的单。回产品给的这一行或 ``{"refused": 码}``。"""
    now = stage(guide_decl, doc, ctx)
    if now["stage"] != REVIEW or str(doc_id) not in now["review_docs"]:
        return {"refused": "not offered"}
    return _call(guide_decl["edit"], ctx, str(doc_id), str(field or ""), value) or {}


@in_conversation
def confirm(guide_decl: Mapping[str, Any], doc, ctx: Dict[str, Any], consent: str, seen: str | None = None) -> Dict[str, Any]:
    """确定之后（引擎已核过本人、版本、功能、停止；锁着这一行、已落下「已确定」）：

    1. 复核看到的样子此刻还在（``seen``，页面带回来的复核指纹；不一样回 ``{"outcome": "changed"}``，引擎刷新复核卡）；
    2. 写入计划放行（``write_plan.from_page``，逐张重算判定，只写可写的；账套变了不写）；
    3. 按会话模式分叉：直写调声明的 ``confirm``，代录交 ``hand_in``。

    回存进运行记录的结果：``{status, docs, write, held: [{doc_id, reason}], plan, mode}``。"""
    from pearnly_core.assistant import write_plan

    now = stage(guide_decl, doc, ctx)
    if now["stage"] != REVIEW:
        return {"outcome": "refused"}
    rows = (_call(guide_decl["review"], ctx, now["review_docs"]) or {}).get("rows") or [] if now["review_docs"] else []
    _card, versions = _judge(ctx["company"], now["review_docs"])
    if seen is not None and str(seen) != _fingerprint(rows, versions):
        return {"outcome": "changed"}
    # 复核里有写不进账本的格的这次不写；产品没给票面的（例如代录里已经是 ERP 单据的 P: / S:，票面在单据上）照写
    blocked = [row["doc_id"] for row in rows if row.get("blocking")]
    docs = [doc_id for doc_id in now["review_docs"] if doc_id not in blocked]
    held = [{"doc_id": doc_id, "reason": BLOCKING} for doc_id in blocked]
    held += [{"doc_id": doc_id, "reason": "held"} for doc_id in now["held"]]
    if not docs:
        return {"status": "nothing_to_write", "docs": now["doc_ids"], "write": [], "held": held}
    plan = write_plan.from_page(ctx["session"], ctx["company"], docs, consent)
    if plan.get("status") != write_plan.STARTED:
        return {"status": plan.get("status") or "failed", "docs": now["doc_ids"], "write": [], "held": held,
                **({"reason": plan["reason"]} if plan.get("reason") else {})}
    held += [{"doc_id": item["doc_id"], "reason": item.get("reason") or "skipped"} for item in plan.get("skipped") or ()]
    write = list(plan.get("documents") or ())
    out = {"status": "started", "docs": now["doc_ids"], "write": write, "held": held, "plan": plan["plan"],
           "mode": plan["mode"]}
    if not write:
        return {**out, "status": "nothing_to_write"}
    if plan["mode"] == "operator":
        from pearnly_core.express_operator import api as operator_api

        again = [doc_id for doc_id in state(doc).get("again") or () if doc_id in write]
        out["handed"] = operator_api.hand_in(ctx["company"], plan["operator_task"], write, again=again)
    else:
        out["confirmed"] = _call(guide_decl["confirm"], ctx, write, consent)
    return out


@in_conversation
def result(guide_decl: Mapping[str, Any], doc, ctx: Dict[str, Any], stored: Mapping[str, Any] | None) -> Dict[str, Any]:
    """结果卡：这次交出去的每张向产品要写账状态（已入账带单号 / 在写 / 没成带原因）；留下的按确定时记的原因；
    还有在写的 ``wait``（页面过一会儿再取）。顺序同识别出的票。"""
    stored = dict(stored or {})
    docs = [str(item) for item in stored.get("docs") or () if item]
    write = [str(item) for item in stored.get("write") or () if item]
    found = (_call(guide_decl["result"], ctx, write) or {}) if write else {}
    rows = {str(row.get("doc_id")): dict(row) for row in found.get("rows") or () if row.get("doc_id") in write}
    for item in stored.get("held") or ():
        rows.setdefault(str(item["doc_id"]), {"doc_id": str(item["doc_id"]), "status": "held",
                                               "reason": str(item.get("reason") or "")})
    for row in rows.values():
        if not row.get("reason_text") and row.get("reason"):
            text = reason_text(str(row["reason"]))
            if text:
                row["reason_text"] = text
    out = {"status": stored.get("status") or "", "rows": [rows[doc_id] for doc_id in docs if doc_id in rows],
           "wait": bool(found.get("wait"))}
    # 写入计划没建成（确定时 ``reason``）：整次的原因也带上，页面在逐张那几行上面显示
    text = reason_text(str(stored.get("reason") or "")) if stored.get("reason") else ""
    return {**out, "reason_text": text} if text else out


@in_conversation
def search(guide_decl: Mapping[str, Any], doc, ctx: Dict[str, Any], key: str, txt: str = "", start: int = 0,
           page_length: int = 20) -> Dict[str, Any]:
    """题卡上带服务端搜索的那一题（科目、物料、对方）翻页 / 搜索：转写账判定的搜索（同第 1 期卡）。"""
    now = stage(guide_decl, doc, ctx)
    question = next((q for q in (now.get("card") or {}).get("questions") or () if q.get("key") == key and q.get("search")), None)
    if now["stage"] != ASKING or question is None:
        frappe.throw(_("Invalid request"))
    found = precheck_api.search(ctx["company"], (question.get("docs") or [""])[0], question["search"], txt, start, page_length)
    return found if isinstance(found, dict) else {"options": list(found or ())}


__all__ = ["ASKING", "AnswerError", "FILES", "FILE_KEY", "FOLLOW", "LEDGER_PREFIX", "MISMATCH_PREFIX", "READING", "REVIEW",
           "SKIP", "answer", "card_for", "check_answers", "confirm", "edit", "held_on_card", "initial", "ledger_question",
           "mismatch_question", "needs_card", "record", "result", "search", "stage", "state", "view"]
