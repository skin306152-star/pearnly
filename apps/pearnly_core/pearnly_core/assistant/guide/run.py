"""引导运行：一次按表单的引导在服务端的记录与走法（多产品共用；总设计 2.4）。

``Pearnly Assistant Guide Run`` 一行 = 本人在一段对话里办一项业务的这一次：当时的公司 / 账套 / 月份、每步的答案、走到哪一步。
题目、已定项、选项、复核、确定后的动作都由声明（:mod:`declaration`）点名的产品函数给，这里只管先后、核对与抢占：

- :func:`start`：点菜单上的业务。同一段对话里还开着的引导先取消（换业务、重开同一业务，旧的作废）。
- :func:`answer`：页面交来这张卡的答案。只收此刻可以问的 / 已定的 / 答过的步骤，值合形状、在选项里；改了前一步，依赖它的后面各步作废。
  适用的步骤都有值了，调声明的 ``draft`` 生成草稿，进复核（没有复核的业务停在可确定）。
- :func:`confirm`：复核后点「确定」。**同意凭证是本人在页面上点的这一下**（``guide:<运行>:<版本>``）：点的人是会话本人、此刻仍有这项功能、
  版本与看到的一致（不一致回 ``changed`` 与最新的卡，不办）、上下文没失效、点过停止不办；同一确定再来一次回上次的结果，不办第二次。
- :func:`cancel`、失效（:func:`_stale`：本对话的公司 / 账套 / 月份变了，按声明的 ``expires``）。
- :func:`follow`：确定之后结果里冒出来的问题（结果带 ``questions``，例：对账结果要定一次的事）作答，交声明的 ``follow`` 重算结果。
- 子题（输入 ``questions``，约定 11-16 N1a）：题目由 ``options`` 函数按前面步骤的值算出（``{"questions": [子题…]}``），逐个子题按它的
  输入核形状、按它的选项核码；结果里的追问（N1b）是同一个子题形状。

记住的选择（``rule: remember``）：引擎不写任何记忆；被取消、失效、停止的引导不留记录。产品要记，原则上在确定成功后记；
**账套选择是例外**：选了当场记（产品的 ``apply``，例：收款 ``book_apply`` → ``service.choose_book``），与网页 / LINE 是同一份记忆、
同一口径「问一次按公司记住」，用户确实选过，引导没走完也不算错记（主会话 11-16 定）。

每个写操作都先锁这一行再核（两台设备同时点，后到的拿到最新状态）。选项函数的统一签名::

    options(ctx, txt="", start=0, page_length=20, codes=None) -> {"options": [{code, name, ...}], "more": bool}

``ctx = {session, user, business, company, ledger, period, answers, run}``；``codes`` 给了就只回这几个里还在的（核答案用）。
默认值函数 ``default(ctx) -> {value, value_label?, source?} | None``；``draft(ctx, values) -> 草稿号``；``review(ctx, draft) -> 复核视图``；
``confirm(ctx, values, draft, consent) -> 结果``；``result(ctx, draft) -> 结果行``（``ctx["done"]`` = 确定时存下的回包：没有草稿的
业务据此找到确定时才建的对象，约定 N10；回 None 照存下的显示）；``follow(ctx, draft, answers) -> 新的结果``。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping

import frappe
from frappe import _

from pearnly_core.assistant.guide import declaration as d, reasons, registry, steps

DOCTYPE = "Pearnly Assistant Guide Run"
TYPE = "pearnly.guide"
ASKING, REVIEWING, CONFIRMED, DONE, FAILED, CANCELLED, EXPIRED = (
    "asking", "reviewing", "confirmed", "done", "failed", "cancelled", "expired")
OPEN = (ASKING, REVIEWING)
PAGE = 20
IDLE_MINUTES = 120  # 放了这么久没人动就失效（同 LINE 收款确认卡 `line_collection.CARD_MINUTES`，开工方案 11-15 定）
# 交回页面的状态（不是记录的状态）
CHANGED, REFUSED, STOPPED = "changed", "refused", "stopped"
CONTEXT_FIELDS = {"company": "company", "ledger": "ledger", "period": "period"}   # 声明的 expires 名 → 记录的列
DOCUMENT_HOOKS = d.DOCUMENT_HOOKS     # documents 源要声明点名的产品函数


# ── 存取（测试换成内存里的一份）────────────────────────────────────────────────────────────────────────────────
def _insert(values: Dict[str, Any]):
    doc = frappe.get_doc({"doctype": DOCTYPE, **values})
    doc.insert(ignore_permissions=True)
    return doc


def _locked(name: str):
    """锁住这一行再读（两台设备同时点：后到的等前一个提交、读到最新的）；不是本人的回 None。
    整行都由带锁的那一次读取（``for_update``）给：先锁再普通读的话，这个请求早先的查询已经定了快照，会读到对方提交前的旧行。"""
    name = str(name or "")
    if not name or not frappe.db.exists(DOCTYPE, name):
        return None
    try:
        doc = frappe.get_doc(DOCTYPE, name, for_update=True)
    except frappe.QueryDeadlockError:  # conflict-ok: 加锁读本身的 1020，回滚再锁一次；这一段不保存，关隔离后读到最新、不再抛
        # 等锁的时候对方提交了：数据库（快照隔离）不让这个旧快照的事务锁改过的行。这里还没写过任何东西，丢掉旧快照重读一次，
        # 读到的就是对方提交后的那一行，后面照常判「卡变了 / 已经确定过」（11-14 实验栈两连接同时点查出）
        frappe.db.rollback()
        doc = frappe.get_doc(DOCTYPE, name, for_update=True)
    return doc if doc.user == frappe.session.user else None


def _save(doc) -> None:
    doc.save(ignore_permissions=True)


def _open_runs(session_id: str) -> List[str]:
    return frappe.get_all(DOCTYPE, filters={"session_id": session_id, "user": frappe.session.user, "status": ["in", OPEN]},
                          pluck="name", limit_page_length=20)


def _conversation(session_id: str) -> Dict[str, Any]:
    """本人这段对话此刻的上下文：``{session_id, mode, company, ledger, period, stopped_at}``（对话选择器定的，引导只读不重判）。"""
    from pearnly_core.assistant import ledger, sessions

    row = sessions.owned(session_id)
    try:
        book = (ledger.view(row.session_id) or {}).get("current") or ""
    except frappe.DoesNotExistError:
        book = ""
    return {"session_id": row.session_id, "mode": "operator" if row.get("operator_task") else "chat",
            "company": str(row.get("project") or ""), "ledger": str(book), "period": str(row.get("period") or ""),
            "stopped_at": sessions.last_stop(row.session_id)}


# ── 产品函数 ─────────────────────────────────────────────────────────────────────────────────────────────────
def _commit_files(session_id: str, by_step: Mapping[str, List[str]]) -> str:
    """引导卡上交的文件（``{步骤 key: 文件编号}``）：登记成用户在这段对话里交出的（`assistant.files.commit`，唯一入口）；
    本对话只取一次。回第一个有文件没认下的步骤 key（都认下回空串）。"""
    if not by_step:
        return ""
    from pearnly_core.assistant import files, sessions

    row = sessions.owned(session_id)
    for key, ids in by_step.items():
        accepted = files.commit(row, list(ids), via="guide")  # loop-query: 一张卡上交文件的步骤（声明里有界，通常一步），每步一次登记
        if set(accepted) != set(ids):
            return key
    return ""


def _call(path: str, *args, **kwargs):
    return frappe.get_attr(path)(*args, **kwargs)


def _guide(doc) -> Dict[str, Any]:
    found = registry.find(doc.business)
    if not found or found["source"] not in (d.FORM, d.DOCUMENTS):
        frappe.throw(_("Invalid request"))
    return found


def _documents(guide: Mapping[str, Any]) -> bool:
    """按单据办的业务（采购、销售）：走 :mod:`documents` 出题源（交文件 → 识别 → 题卡 → 复核 → 确定按模式分叉）。"""
    return guide.get("source") == d.DOCUMENTS


def _docs():
    from pearnly_core.assistant.guide import documents

    return documents


def _answers(doc) -> Dict[str, Any]:
    try:
        found = json.loads(doc.answers or "{}")
    except ValueError:
        found = {}
    return found if isinstance(found, dict) else {}


def _ctx(doc, answers: Mapping[str, Any]) -> Dict[str, Any]:
    return {"session": doc.session_id, "user": doc.user, "business": doc.business, "company": doc.company or "",
            "ledger": doc.ledger or "", "period": doc.period or "", "answers": dict(answers), "run": doc.name,
            "draft": doc.draft or ""}


def _reopened(doc) -> Dict[str, Any]:
    """点了「改」回去重问的那一步：``{步骤: 原来的值}``（:func:`reopen`）。后台定的值在这期间不算定（照常问），原值作为题的 ``value``。"""
    try:
        found = json.loads(doc.get("reopened") or "{}")
    except ValueError:
        found = {}
    return found if isinstance(found, dict) else {}


def _decided(guide: Mapping[str, Any], doc, answers: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    """后台此刻能定的各步（声明了 ``default`` 的）：依赖的步骤有值了才去问产品；问不出、出错都当没有（降成问）。
    一步定了可能让下一步的依赖满足，所以转到不再有新值为止。"""
    found: Dict[str, Dict[str, Any]] = {}
    pending = [step for step in guide["steps"] if step.get("default")]
    for _round in range(len(pending) + 1):
        progressed = False
        blocked = set(steps.blocked(guide, answers, found))
        for step in pending:
            if step["key"] in found or step["key"] in blocked:
                continue
            try:
                value = _call(step["default"], _ctx(doc, {**{k: v["value"] for k, v in found.items() if v}, **answers}))
            except Exception:  # noqa: BLE001 — 默认值算不出不挡引导：这一步降成问
                frappe.log_error(title="Assistant guide default failed")
                value = None
            if isinstance(value, Mapping) and value.get("value") not in (None, "", [], {}):
                found[step["key"]] = dict(value)
                progressed = True
            else:
                found[step["key"]] = {}
        if not progressed:
            break
    reopened = _reopened(doc)
    return {key: value for key, value in found.items() if value and key not in reopened}


def _options(step: Mapping[str, Any], ctx: Dict[str, Any], txt="", start=0, page_length=PAGE, codes=None) -> Dict[str, Any]:
    out = _call(step["options"], ctx, txt=str(txt or ""), start=int(start or 0), page_length=int(page_length or PAGE), codes=codes)
    rows = [dict(row) for row in (out or {}).get("options") or () if isinstance(row, Mapping) and row.get("code")]
    found = {"options": rows, "more": bool((out or {}).get("more"))}
    # ``max_rows``：多行输入（金额多选 / 分摊）一次最多几行（按此刻的上下文给，例：MR.ERP 账本 1 行，折扣选项带 ``free`` 不占名额）
    max_rows = (out or {}).get("max_rows")
    if isinstance(max_rows, int) and not isinstance(max_rows, bool) and max_rows > 0 and step["input"] in ("amounts", "split"):
        found["max_rows"] = max_rows
    # 选项函数可以带两样：``facts``（题目下的事实行，例：读回还没到、电脑没连）、``wait``（选项还在读，页面过一会儿再取这张卡）
    facts = [str(item) for item in (out or {}).get("facts") or () if str(item or "").strip()]
    if facts:
        found["facts"] = facts
    if (out or {}).get("wait"):
        found["wait"] = True
    # 分摊题要分摊的合计可以由选项函数自己给（``total``，两位小数的字符串）：前一道金额多选里有要扣的行时（收款的抵减单），
    # 引擎照各行相加不对；没给照旧由 :func:`_split_total` 加
    total = (out or {}).get("total")
    if step["input"] == "split" and isinstance(total, str) and steps.amount(total) is not None:
        found["total"] = f"{steps.amount(total):.2f}"
    return found


def _codes_of(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value]
    return [str(row.get("code")) if isinstance(row, Mapping) else str(row) for row in value or ()]


def _not_offered(step: Mapping[str, Any], ctx: Dict[str, Any], value: Any) -> str:
    """这个值哪里不在此刻的选项里（空串 = 都在）：码不在（客户停用了、发票已收清、科目被删）回 ``not offered``；金额多选 / 分摊
    的一行超过选项给的上限（``max``：此刻还能收多少）回 ``over max``。码按原样比，不做空白归一（编码里的不换行空格是另一件档）。"""
    if step["input"] not in d.OPTION_INPUTS:
        return ""
    wanted = _codes_of(value)
    found = _options(step, ctx, codes=wanted, page_length=len(wanted) or 1)
    offered = {row["code"]: row for row in found["options"]}
    if any(code not in offered for code in wanted):
        return "not offered"
    if step["input"] in ("amounts", "split"):
        for row in value:
            top = offered[str(row["code"])].get("max")
            if top not in (None, "") and steps.amount(row["amount"]) > steps.amount(top):
                return "over max"
        # 一次最多几行（选项函数此刻给，例：MR.ERP 账本一笔只收一种方式）；带 ``free`` 的选项不占名额（折扣）
        if found.get("max_rows") is not None:
            counted = [row for row in value if not offered[str(row["code"])].get("free") and steps.amount(row["amount"]) != 0]
            if len(counted) > int(found["max_rows"]):
                return TOO_MANY_ROWS
    return ""


TOO_MANY_ROWS, NOT_HERE = "too_many_rows", "not in this conversation"


def _refusal(found: Mapping[str, str]) -> Dict[str, Dict[str, str]]:
    """拒收的那几题 → ``{key: {code, text}}``：码照给（页面 / 测试按码认），``text`` 是按本次请求语言翻好的一句事实（术语表
    ``guide.refused.<码>``，引擎自己的码在 pearnly_core，产品核对函数的码由产品在自己的术语表写）；没有译文回空串。"""
    return {key: {"code": str(code), "text": _t(f"guide.refused.{code}")} for key, code in found.items()}


def _subs(raw: Any) -> List[Dict[str, Any]]:
    """产品给的子题（``{key, input, title, options?, preselected?, required?, facts?}``）：形状不对的那一题不出、记一笔日志。"""
    out: List[Dict[str, Any]] = []
    for item in raw or ():
        ok = isinstance(item, Mapping) and isinstance(item.get("key"), str) and item.get("input") in d.SUB_INPUTS \
            and isinstance(item.get("title"), str) and item["key"] not in {sub["key"] for sub in out}
        if not ok:
            frappe.log_error(title="Assistant guide question rejected", message=repr(item)[:500])
            continue
        sub = {"key": item["key"], "input": item["input"], "title": item["title"], "required": item.get("required", True) is not False}
        if item.get("options") is not None:
            sub["options"] = [{"code": str(row["code"]), "name": str(row.get("name") or row["code"])}
                              for row in item["options"] if isinstance(row, Mapping) and row.get("code") not in (None, "")]
        for key in ("preselected", "facts", "note"):
            if item.get(key) not in (None, "", [], {}):
                sub[key] = item[key]
        out.append(sub)
    return out


def _sub_problem(subs: List[Dict[str, Any]], given: Any) -> str:
    """一组子题的答案哪里不收（空串 = 都收）：问的里没有这一题、形状不对、码不在这一题的选项里、要答的没答。"""
    found = steps.value_problem({"input": "questions"}, given)
    if found:
        return found
    by_key = {sub["key"]: sub for sub in subs}
    for key, value in given.items():
        sub = by_key.get(key)
        if sub is None:
            return "not offered"
        found = steps.value_problem({"input": sub["input"]}, value)
        if found:
            return found
        if "options" in sub and any(code not in {row["code"] for row in sub["options"]} for code in _codes_of(value)):
            return "not offered"
    if any(sub["required"] and sub["key"] not in given for sub in subs):
        return "incomplete"
    return ""


def _fill_context(doc, now: Mapping[str, Any]) -> bool:
    """开始时还没定、之后定下了的上下文（例：引导里选了账套、记进本对话）：补进这次引导，不算失效。回有没有补。"""
    filled = False
    for column in CONTEXT_FIELDS.values():
        if not str(doc.get(column) or "") and str(now.get(column) or ""):
            setattr(doc, column, str(now[column]))
            filled = True
    return filled


def _t(key: str) -> str:
    """术语表键按本次请求的语言翻成字（页面不认键）；没有译文回空串。"""
    found = _(key) if key else ""
    return "" if found == key else found


# ── 给页面的样子 ─────────────────────────────────────────────────────────────────────────────────────────────
def _question(guide: Mapping[str, Any], step: Mapping[str, Any], ctx: Dict[str, Any], answers: Mapping[str, Any],
              decided: Mapping[str, Mapping[str, Any]], reopened: Mapping[str, Any] | None = None,
              query: str = "") -> Dict[str, Any]:
    reopened = reopened or {}
    out = {"key": step["key"], "input": step["input"], "rule": step["rule"], "tier": step.get("tier") or "how",
           "title_key": f"guide.step.{guide['id']}.{step['key']}", "required": step.get("required", True)}
    out["title"] = _t(out["title_key"])
    facts: List[str] = []
    if step["input"] in d.QUESTION_INPUTS:
        found = _call(step["options"], ctx) or {}
        out["questions"] = _subs(found.get("questions"))
        facts.extend(str(item) for item in found.get("facts") or () if str(item or "").strip())
        if found.get("wait"):
            out["wait"] = True
    hint = (decided.get(step["key"]) or {}).get("value")
    if step["input"] in d.OPTION_INPUTS:
        found = _options(step, ctx, txt=query)
        rows = _recommended_first(step, ctx, found["options"], hint if out["tier"] == "how" else None, txt=query)
        out.update(options=rows, more=found["more"], search=bool(step.get("search")))
        if query:
            out["query"] = query   # 搜索框的初值（打字开卡时说的客户名 / 单号），选项已按它搜过
        if "max_rows" in found:
            out["max_rows"] = found["max_rows"]
        if step.get("zero"):
            out["zero"] = True   # 金额多选的行上可以是 0（盘点数到 0），页面据此收 0
        if step["input"] == "split":
            total = found.get("total") or _split_total(guide, step, answers)
            if total is not None:
                out["total"] = total
        facts.extend(found.get("facts") or ())
        if found.get("wait"):
            out["wait"] = True
    if step.get("facts"):
        facts.extend(str(item) for item in _call(step["facts"], ctx) or () if str(item or "").strip())
    if facts:
        out["facts"] = facts
    # ``always`` 的步骤后台给了值：只作推荐（页面标「推荐」、排第一，不当初始值；钱的事不做默认勾选），点了才算
    if hint not in (None, "", [], {}) and out["tier"] == "how":
        out["preselected"] = hint
    if answers.get(step["key"]) not in (None, "", [], {}):
        out["value"] = answers[step["key"]]
    elif step["key"] in reopened:
        out["value"] = reopened[step["key"]]   # 点「改」回来重问：原来的值放进 value（不当推荐）
    return out


def _split_total(guide: Mapping[str, Any], step: Mapping[str, Any], answers: Mapping[str, Any]) -> str | None:
    """分摊题要分摊的合计：声明里它前面最近一道金额多选题此刻答的各行之和（收款：各张发票收多少 → 收款方式要分摊的数）。
    那一道还没答回 None（页面不显示「应分」）。两位小数的字符串，同金额的写法。"""
    keys = [item["key"] for item in guide["steps"]]
    before = [item for item in guide["steps"][:keys.index(step["key"])] if item["input"] == "amounts"]
    rows = answers.get(before[-1]["key"]) if before else None
    if not isinstance(rows, list) or not rows:
        return None
    found = [steps.amount(row.get("amount")) if isinstance(row, Mapping) else None for row in rows]
    if any(value is None for value in found):
        return None
    return f"{sum(found):.2f}"


def _recommended_first(step: Mapping[str, Any], ctx: Dict[str, Any], rows: List[Dict[str, Any]], hint: Any,
                       start: int = 0, txt: str = "") -> List[Dict[str, Any]]:
    """推荐项（后台给的单选值）排在第一页第一个；不在这一页的从产品按码取回来插到最前（搜索时只在结果里有它才挪）；
    翻到后面的页不再出现它（第一页已经有了）。不是单选码的推荐值（多选、金额）照产品的顺序。"""
    if not isinstance(hint, str) or not hint:
        return rows
    others = [row for row in rows if str(row.get("code")) != hint]
    if start:
        return others
    found = next((row for row in rows if str(row.get("code")) == hint), None)
    if found is None and not txt:
        found = next(iter(_options(step, ctx, codes=[hint])["options"]), None)
    return [found, *others] if found is not None else rows


def _searchable(step: Mapping[str, Any]) -> bool:
    return step["input"] == "choice" and bool(step.get("search"))


def _query_key(guide: Mapping[str, Any], doc, asked: List[Mapping[str, Any]], answers: Mapping[str, Any]) -> str:
    """打字开卡时说的那几个字（``hint``）预填到哪一题：此刻要问的第一道带搜索的单选题；用户答过任何一道带搜索的单选题后就不再预填
    （那几个字只管开卡那一下，不跟到后面的题上）。"""
    if not str(doc.get("hint") or "").strip():
        return ""
    if any(_searchable(step) and answers.get(step["key"]) not in (None, "", [], {}) for step in guide["steps"]):
        return ""
    return next((step["key"] for step in asked if _searchable(step)), "")


def view(doc) -> Dict[str, Any]:
    """这次引导此刻给页面的全部：状态、版本、这张卡的题与已定项；复核中带复核视图，办完带结果。"""
    out = {"run": doc.name, "business": doc.business, "status": doc.status, "version": int(doc.version or 0),
           "company": doc.company or "", "ledger": doc.ledger or "", "period": doc.period or ""}
    if doc.status in (CANCELLED, EXPIRED):
        return {**out, "reason": doc.ended_reason or ""}
    guide = registry.find(doc.business)
    if not guide:
        return out
    answers = _answers(doc)
    ctx = _ctx(doc, answers)
    if _documents(guide):
        if doc.status in OPEN:
            return {**out, "source": d.DOCUMENTS, **_docs().view(guide, doc, ctx)}
        stored = json.loads(doc.result or "null") or {}
        return {**out, "source": d.DOCUMENTS,
                "result": _docs().result(guide, doc, ctx, stored) if doc.status != FAILED else stored}
    out["consent"] = _consent(guide, doc, answers)
    if doc.status in OPEN:
        # 确定卡的问句与下面那行事实（同线上允许卡）：问句按约定取术语表 ``guide.consent.<id>``，没有译文回空串（页面退回业务名）
        out["consent_title_key"] = _consent_key(guide, doc, answers)
        out["consent_title"] = _t(out["consent_title_key"])
        out["consent_note"] = _note(guide, ctx)
    if doc.status == ASKING:
        decided = _decided(guide, doc, answers)
        asked = steps.to_ask(guide, answers, decided)
        settled = steps.settled(guide, answers, decided)
        reopened = _reopened(doc)
        query_key = _query_key(guide, doc, asked, answers)
        out["card"] = {"version": 2, "questions": [_question(guide, step, ctx, answers, decided, reopened,
                                                             str(doc.get("hint") or "") if step["key"] == query_key else "")
                                                   for step in asked],
                       "decided": [{**item, "title_key": f"guide.step.{guide['id']}.{item['key']}",
                                    "title": _t(f"guide.step.{guide['id']}.{item['key']}")} for item in settled],
                       "ready": steps.state(guide, answers, decided) == steps.READY,
                       "answers": dict(answers)}  # 已答的值（分摊那一步的合计等由页面按它算；页面只回显，值以服务端为准）
        returned = _returned(doc)
        if returned:
            out["card"]["returned"] = returned
    elif doc.status == REVIEWING and guide.get("review"):
        review = dict(_call(guide["review"], ctx, doc.draft or "") or {})
        for part in ("lines", "tables"):
            if part in review:
                review[part] = [{**item, "label": _t(item.get("label_key") or "")} for item in review[part] or ()]
        out["review"] = review
    elif doc.status in (CONFIRMED, DONE, FAILED):
        out["result"] = json.loads(doc.result or "null")
        if guide.get("result") and doc.status != FAILED:
            out["result"] = _call(guide["result"], {**ctx, "done": out["result"]}, doc.draft or "") or out["result"]
        if isinstance(out["result"], Mapping) and out["result"].get("questions") is not None:
            out["result"] = {**out["result"], "questions": _subs(out["result"]["questions"])}
    return out


def _shown(doc) -> Dict[str, Any]:
    """改过这次引导之后画卡：先提交。画卡要调产品的选项、判定、说明，产品那边有的会回滚（Cowork 判卡片「只是看」、说明出错时），
    没提交的改动会被整笔冲掉（11-19 回滚审计 P4 / _note）。"""
    frappe.db.commit()
    return view(doc)


def _consent(guide: Mapping[str, Any], doc, answers: Mapping[str, Any]) -> str:
    """确定画成什么：``allow``（拒绝 / 允许）或 ``confirm``。声明写成与 ``when`` 同形的字典时按那一步此刻的值判。"""
    consent = guide.get("consent")
    if not isinstance(consent, Mapping):
        return consent or "confirm"
    values = steps._values(guide, answers, _decided(guide, doc, answers))
    return "allow" if steps.applies({"when": consent}, values) else "confirm"


def _consent_key(guide: Mapping[str, Any], doc, answers: Mapping[str, Any]) -> str:
    """问句的术语键：``guide.consent.<id>``；按答案换允许卡的（``consent`` 是字典，例：盘点「关闭」与「删除」问法不同）
    此刻用允许卡时按那一步的值 ``guide.consent.<id>.<值>``。"""
    base = f"guide.consent.{guide['id']}"
    consent = guide.get("consent")
    if not isinstance(consent, Mapping) or _consent(guide, doc, answers) != "allow":
        return base
    value = steps._values(guide, answers, _decided(guide, doc, answers)).get(consent.get("key"))
    return f"{base}.{value}" if isinstance(value, str) and value else base


def _note(guide: Mapping[str, Any], ctx: Dict[str, Any]) -> str:
    """问句下面那一行事实（声明的 ``note(ctx, draft)``：会动到哪家公司、哪本账套）。没声明、算不出或回空都是空串（那一行不出）；
    出错不挡这张卡（同允许卡 `tools._note`）。"""
    if not guide.get("note"):
        return ""
    try:
        text = _call(guide["note"], ctx, ctx.get("draft") or "")
    except Exception:  # noqa: BLE001 — 事实行算不出不挡卡
        frappe.db.rollback()
        frappe.log_error(title="Assistant guide note failed")
        return ""
    return str(text or "").strip()


def _returned(doc) -> Dict[str, Any]:
    """业务函数退回来的那几步（``{步骤 key: {reason, text?}}``：生成草稿或放行时规则此刻不过，例如发票刚被别人收了）。"""
    try:
        found = json.loads(doc.result or "null")
    except ValueError:
        return {}
    return dict(found.get("returned") or {}) if isinstance(found, dict) and doc.status == ASKING else {}


def _send_back(guide: Mapping[str, Any], doc, answers: Dict[str, Any], returned: Mapping[str, Any]) -> None:
    """把业务函数退回的那几步（与依赖它们的后面各步）作废、回到问的状态，原因留在卡上；其余答案保留。"""
    keys = [key for key in returned if any(step["key"] == key for step in guide["steps"])]
    answers = steps.without_dependents(guide, answers, keys)
    for key in keys:
        answers.pop(key, None)
    doc.answers = json.dumps(answers, ensure_ascii=False, default=str)
    doc.result = json.dumps({"returned": {key: dict(returned[key]) for key in keys}}, ensure_ascii=False, default=str)
    doc.status, doc.consent = ASKING, ""


# ── 失效 ─────────────────────────────────────────────────────────────────────────────────────────────────────
def _idle(doc) -> bool:
    """这次引导放太久没人动（:data:`IDLE_MINUTES`）。"""
    moment = frappe.utils.get_datetime(doc.modified) if doc.get("modified") else None
    return bool(moment) and (frappe.utils.now_datetime() - moment).total_seconds() > IDLE_MINUTES * 60


def _stale(guide: Mapping[str, Any], doc, now: Mapping[str, Any]) -> str:
    """这次引导开始之后，本对话的公司 / 账套 / 月份变了没有（按声明的 ``expires``），或放太久了；回是哪一项。"""
    if _idle(doc):
        return "idle"
    for name in guide.get("expires") or ():
        column = CONTEXT_FIELDS.get(name)
        if column and str(doc.get(column) or "") != str(now.get(column) or ""):
            return name
    return ""


def _end(doc, status: str, reason: str = "") -> None:
    doc.status = status
    doc.ended_reason = reason
    doc.version = int(doc.version or 0) + 1
    _save(doc)
    _announce(doc)


def _stopped_since(doc, now: Mapping[str, Any]) -> bool:
    """这次引导最后一次改动之后，本对话点过停止（停止时刻是 UTC 的裸时间，记录的改动时间是站点时区的：换成 UTC 再比，
    同 `write_plan.tool_start`）。"""
    from pearnly_core.assistant import write_plan

    stopped = now.get("stopped_at")
    return bool(stopped and doc.get("modified")
                and frappe.utils.get_datetime(stopped) >= write_plan._to_utc(doc.modified))


def _checked(name: str):
    """锁住并核过的 (记录, 声明, 本对话此刻)。不是本人的 / 找不到抛未找到；已失效的当场标失效。"""
    doc = _locked(name)
    if doc is None:
        frappe.throw(_("Not found"), frappe.DoesNotExistError)
    guide = _guide(doc)
    now = _conversation(doc.session_id)
    if doc.status in OPEN and _fill_context(doc, now):
        _save(doc)
        # 补上的上下文当场提交：接下来作答要画卡、判单据，产品那边的回滚会把它冲掉，之后再存就撞时间戳（11-19 回滚审计 P3）
        frappe.db.commit()
    if doc.status in OPEN:
        stale = _stale(guide, doc, now)
        if stale:
            _end(doc, EXPIRED, stale)
    return doc, guide, now


# ── 走法 ─────────────────────────────────────────────────────────────────────────────────────────────────────
def start(session_id: str, business: str, doc_ids=None, at: str = "asking", operator=None, hint: str = "") -> Dict[str, Any]:
    """点菜单上的业务开一次引导。``doc_ids``（只对按单据的业务，公开入口）：别的引导 / 代录开始前把已有单据交进来，不交文件、不等识别，
    从判定（``at="asking"``）或复核（``at="review"``）接着走（`documents.initial`）。``operator``：代录开始前那张卡
    （`express_operator.guide_start.start` 调，``{task, period}``）。``hint``：打字说要办这件事时提到的客户名 / 单号（CC 工具开卡时给，
    最多 140 字），出卡时预填到第一道带搜索的单选题（:func:`_query_key`），只当搜索词，不当答案。"""
    now = _conversation(session_id)
    guide = registry.find(business)
    if not guide or guide["source"] not in (d.FORM, d.DOCUMENTS) or now["mode"] not in guide["modes"] \
            or business not in registry.offered().get(now["mode"], []) \
            or not registry.fits_ledger(guide, registry.ledger_of(session_id)):
        frappe.throw(_("Invalid request"))  # 账本种类不在声明 ledgers 里：菜单上本来就没有，不信页面（11-19）
    if _documents(guide) and not all(guide.get(key) for key in DOCUMENT_HOOKS):
        frappe.throw(_("Invalid request"))  # 声明还没点名 documents 源要的产品函数（这一格还不能在页面上办）
    if doc_ids is not None and not _documents(guide):
        frappe.throw(_("Invalid request"))
    documents_column = _docs().initial(doc_ids, at, operator) if _documents(guide) else None  # 先核单据，核不过不作废旧的
    if doc_ids:
        _docs().check_handed(now["company"], list(json.loads(documents_column)["doc_ids"]), now["session_id"])
    for name in _open_runs(now["session_id"]):
        old = _locked(name)  # loop-query: 一段对话同一时刻最多一条开着的引导（开新的先把旧的取消），按行锁逐条取
        if old is not None and old.status in OPEN:
            _end(old, CANCELLED, "replaced")
    doc = _insert({"session_id": now["session_id"], "user": frappe.session.user, "business": business, "status": ASKING,
                   "company": now["company"], "ledger": now["ledger"], "period": now["period"], "answers": "{}", "version": 1,
                   **({"hint": " ".join(str(hint).split())[:140]} if str(hint or "").strip() else {}),
                   **({"documents": documents_column} if documents_column else {})})
    _announce(doc)
    return _shown(doc)


def _settle(guide: Mapping[str, Any], doc, answers: Dict[str, Any]) -> None:
    """答完一轮之后：适用的步骤都有值了就生成草稿、进复核（没有复核的留在 asking，卡上 ``ready``）；否则接着问。"""
    decided = _decided(guide, doc, answers)
    ready = steps.state(guide, answers, decided) == steps.READY
    doc.answers = json.dumps(answers, ensure_ascii=False, default=str)
    doc.version = int(doc.version or 0) + 1
    doc.result = ""
    doc.status = ASKING
    if ready and guide.get("draft"):
        made = _call(guide["draft"], _ctx(doc, answers), steps.final_values(guide, answers, decided))
        if isinstance(made, Mapping) and made.get("returned"):
            # 规则此刻不过（发票刚被别人收了、金额对不上、日期不在期间）：退回那几步重问，其余答案保留
            _send_back(guide, doc, answers, made["returned"])
            _save(doc)
            _announce(doc)
            return
        doc.draft = str((made.get("draft") if isinstance(made, Mapping) else made) or "")
    doc.status = REVIEWING if ready and guide.get("review") else ASKING
    _save(doc)
    _announce(doc)


def answer(name: str, given: Mapping[str, Any], version: int | None = None) -> Dict[str, Any]:
    doc, guide, _now = _checked(name)
    if doc.status not in OPEN:
        return {**_shown(doc), "outcome": REFUSED}
    if version is not None and int(version) != int(doc.version or 0):
        return {**_shown(doc), "outcome": CHANGED}
    if not isinstance(given, Mapping) or not given:
        frappe.throw(_("Invalid request"))
    if _documents(guide):
        return _answer_documents(guide, doc, given)
    answers = _answers(doc)
    decided = _decided(guide, doc, answers)
    refused = steps.problems_in(guide, answers, decided, given)
    by_key = {step["key"]: step for step in guide["steps"]}
    ctx = _ctx(doc, answers)
    for key, value in given.items():
        if key not in refused and by_key[key]["input"] in d.QUESTION_INPUTS:
            missing = _sub_problem(_subs((_call(by_key[key]["options"], ctx) or {}).get("questions")), value)
            if missing:
                refused[key] = missing
        if key not in refused:
            missing = _not_offered(by_key[key], ctx, value)
            if missing:
                refused[key] = missing
        if key not in refused and by_key[key].get("check"):
            code = _call(by_key[key]["check"], {**ctx, "answers": {**answers, **given}}, value)
            if code:
                refused[key] = str(code)
    if refused:
        return {**_shown(doc), "outcome": REFUSED, "refused": _refusal(refused)}
    # 交文件的步骤：别的题都收下了，才把这几份登记成用户交出的（唯一入口 `files.commit(via="guide")`，幂等）；
    # 不是这段对话里本人上传的，这一步不收
    not_here = _commit_files(doc.session_id, {key: list(value) for key, value in given.items()
                                              if by_key[key]["input"] == "file"})
    if not_here:
        return {**_shown(doc), "outcome": REFUSED, "refused": _refusal({not_here: NOT_HERE})}
    changed = [key for key, value in given.items() if answers.get(key) != value]
    answers = steps.without_dependents(guide, answers, changed)
    answers.update(given)
    for key in changed:
        if by_key[key].get("apply"):
            _call(by_key[key]["apply"], _ctx(doc, answers), given[key])
    reopened = _reopened(doc)
    if any(key in reopened for key in given):
        doc.reopened = json.dumps({key: value for key, value in reopened.items() if key not in given}, ensure_ascii=False, default=str)
    if changed and any(by_key[key].get("apply") for key in changed):
        _fill_context(doc, _conversation(doc.session_id))
    _settle(guide, doc, answers)
    return _shown(doc)


def _answer_documents(guide: Mapping[str, Any], doc, given: Mapping[str, Any]) -> Dict[str, Any]:
    """按单据的一次作答（交文件或题卡）：收下了版本 +1、存、推事件；不收回 ``refused``（卡不变，页面可重答）。"""
    found = _docs().answer(guide, doc, _ctx(doc, _answers(doc)), given)
    if found.get("restart"):
        # 代录开始前改了月份：对话月份变了，这次失效；按新月份重开这张卡
        from pearnly_core.express_operator import guide_start

        operator = _docs().state(doc).get("operator") or {}
        _end(doc, EXPIRED, "period")
        # 这次失效落定再重开（11-25）：重开的卡要按新月份进只读判定（`check_handed`），没提交的失效与月份会让 Cowork 判定拒绝
        frappe.db.commit()
        return guide_start.start(operator.get("task") or "")
    if found.get("refused"):
        return {**_shown(doc), "outcome": REFUSED, "refused": found["refused"],
                **({"errors": found["errors"]} if found.get("errors") else {})}
    doc.version = int(doc.version or 0) + 1
    _save(doc)
    _announce(doc)
    return _shown(doc)


def edit(name: str, doc_id: str, field: str, value: Any, version: int) -> Dict[str, Any]:
    """复核卡就地改一格（页面接口 ``guide_edit``）：本人、版本与看到的一致、运行没失效。按单据的业务：产品写回自己的单（``doc_id``
    是单据号）；表单业务：声明点名的 ``edit(ctx, draft, row, field, value)``（``doc_id`` 是复核表的行码，预扣税逐行复核），只在复核中。
    收下版本 +1。"""
    doc, guide, _now = _checked(name)
    if doc.status not in OPEN:
        return {**_shown(doc), "outcome": REFUSED}
    if int(version) != int(doc.version or 0):
        return {**_shown(doc), "outcome": CHANGED}
    if not _documents(guide):
        return _edit_form(guide, doc, str(doc_id or ""), str(field or ""), value)
    found = _docs().edit(guide, doc, _ctx(doc, _answers(doc)), str(doc_id or ""), str(field or ""), value)
    if isinstance(found, Mapping) and found.get("refused"):
        # 拒收形状同表单与作答：{格: {code, text}}；refused_text 照旧带着（页面旧写法也认）
        code, text = str(found["refused"]), _docs().reason_text(str(found["refused"]))
        return {**_shown(doc), "outcome": REFUSED, "refused": {str(field or ""): {"code": code, "text": text}},
                **({"refused_text": {str(field or ""): text}} if text else {})}
    doc.version = int(doc.version or 0) + 1
    _save(doc)
    _announce(doc)
    return _shown(doc)


def _edit_form(guide: Mapping[str, Any], doc, row: str, field: str, value: Any) -> Dict[str, Any]:
    """表单业务的就地改一格（版本已在 :func:`edit` 核过）：只在复核中、声明点名了 ``edit``、行与格都给了才收。产品回
    ``{"draft": 新草稿}`` 换成新草稿、版本 +1；回 ``{"refused": {格: 码}, "refused_text"?: {格: 已翻译}}`` 什么都不改，拒收照引擎的
    形状 ``{格: {code, text}}``、产品的说明在 ``refused_text`` 原样带上。回包不合这两种（产品写错了）报错，不当成用户被拒。"""
    if doc.status != REVIEWING or not guide.get("edit") or not row or not field:
        return {**_shown(doc), "outcome": REFUSED}
    made = _call(guide["edit"], _ctx(doc, _answers(doc)), doc.draft or "", row, field, value)
    if not isinstance(made, Mapping):
        frappe.throw(_("Invalid request"))
    if made.get("refused"):
        codes = {str(key): str(code) for key, code in dict(made["refused"]).items()}
        texts = {str(key): str(text) for key, text in dict(made.get("refused_text") or {}).items() if text}
        return {**_shown(doc), "outcome": REFUSED, "refused": _refusal(codes), **({"refused_text": texts} if texts else {})}
    if not str(made.get("draft") or ""):
        frappe.throw(_("Invalid request"))
    doc.draft = str(made["draft"])
    doc.version = int(doc.version or 0) + 1
    _save(doc)
    _announce(doc)
    return _shown(doc)


def _confirm_documents(guide: Mapping[str, Any], doc, consent: str, seen) -> Dict[str, Any]:
    """按单据的确定（引擎的核对已过）：先落下「已确定」，再放行、按模式分叉（:func:`documents.confirm`）。"""
    doc.status, doc.consent = CONFIRMED, consent
    doc.version = int(doc.version or 0) + 1
    _save(doc)
    frappe.db.commit()      # 先落下「已确定」再办：办到一半出错，这次确定不会被当成没点过而办第二次
    name = doc.name
    doc = _locked(name)
    try:
        result = _docs().confirm(guide, doc, _ctx(doc, _answers(doc)), consent, seen)
        if isinstance(result, Mapping) and result.get("outcome") in (CHANGED, REFUSED):
            # 复核之后票在别处改过、或此刻已不在复核：不办，回到复核（页面刷新复核卡再让人确定）
            doc.status, doc.consent = REVIEWING, ""
            doc.version = int(doc.version or 0) + 1
            _save(doc)
            _announce(doc)
            return {**_shown(doc), "outcome": result["outcome"]}
        doc.status = DONE
    except Exception as exc:  # noqa: BLE001 — 放行 / 产品函数拒了、出错：记失败与原因码，交回页面（结果卡说明）
        frappe.db.rollback()
        doc = _locked(name)
        # 结果卡带原因（`reasons.failure`：产品 frappe.throw 的正式文案 → 原因码表 → 通用一句）；错误编号只进 Error Log。
        # 那句已经写进结果卡：清掉消息队列，接口回包不再带一条服务端弹窗
        code, text = reasons.failure(exc)
        frappe.clear_messages()
        result = {"status": "failed", "reason": code, **({"reason_text": text} if text else {})}
        doc.status = FAILED
        frappe.log_error(title="Assistant guide confirm failed")
    doc.result = json.dumps(result, ensure_ascii=False, default=str)
    doc.version = int(doc.version or 0) + 1
    _save(doc)
    _announce(doc)
    return _shown(doc)


def confirm(name: str, version: int, seen: str | None = None) -> Dict[str, Any]:
    """``seen``：按单据的业务，页面带回复核卡上的指纹（``review.seen``）；与此刻不一致（票在别处改过）不办。表单业务不用。"""
    doc, guide, now = _checked(name)
    consent = f"guide:{doc.name}:{int(version)}"
    if doc.status in (CONFIRMED, DONE, FAILED):
        # 同一确定再来一次（双击、重发、另一台设备）：回上次的结果，不办第二次
        return {**_shown(doc), "outcome": "" if doc.consent == consent else REFUSED}
    if doc.status not in OPEN:
        return {**_shown(doc), "outcome": REFUSED}
    if int(version) != int(doc.version or 0):
        return {**_shown(doc), "outcome": CHANGED}
    if doc.business not in registry.offered().get(now["mode"], []):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    if _stopped_since(doc, now):
        return {**_shown(doc), "outcome": STOPPED}
    if _documents(guide):
        return _confirm_documents(guide, doc, consent, seen)
    answers = _answers(doc)
    decided = _decided(guide, doc, answers)
    if steps.state(guide, answers, decided) != steps.READY or (guide.get("review") and doc.status != REVIEWING):
        return {**_shown(doc), "outcome": REFUSED}
    doc.status, doc.consent = CONFIRMED, consent
    doc.version = int(doc.version or 0) + 1
    _save(doc)
    frappe.db.commit()      # 先落下「已确定」再办：办到一半出错，这次确定不会被当成没点过而办第二次
    doc = _locked(doc.name)
    try:
        result = _call(guide["confirm"], _ctx(doc, answers), steps.final_values(guide, answers, decided), doc.draft or "", consent)
        if isinstance(result, Mapping) and result.get("outcome") == CHANGED:
            # 复核之后草稿在别处改过（另一台设备、网页、LINE）：不办，复核卡刷新成新的再让人确定
            doc.status, doc.consent = REVIEWING, ""
            doc.draft = str(result.get("draft") or doc.draft or "")
            doc.version = int(doc.version or 0) + 1
            _save(doc)
            _announce(doc)
            return {**_shown(doc), "outcome": CHANGED}
        if isinstance(result, Mapping) and result.get("returned"):
            _send_back(guide, doc, answers, result["returned"])
            doc.version = int(doc.version or 0) + 1
            _save(doc)
            _announce(doc)
            return {**_shown(doc), "outcome": REFUSED}
        doc.status = DONE
    except Exception as exc:  # noqa: BLE001 — 业务函数拒了 / 出错：记失败与原因码，交回页面（结果卡说明）
        frappe.db.rollback()
        doc = _locked(name)
        # 结果卡带原因（`reasons.failure`：产品 frappe.throw 的正式文案 → 原因码表 → 通用一句）；错误编号只进 Error Log。
        # 那句已经写进结果卡：清掉消息队列，接口回包不再带一条服务端弹窗
        code, text = reasons.failure(exc)
        frappe.clear_messages()
        result = {"status": "failed", "reason": code, **({"reason_text": text} if text else {})}
        doc.status = FAILED
        frappe.log_error(title="Assistant guide confirm failed")
    doc.result = json.dumps(result, ensure_ascii=False, default=str)
    doc.version = int(doc.version or 0) + 1
    _save(doc)
    _announce(doc)
    return _shown(doc)


def end_open(session_id: str, reason: str, status: str = EXPIRED) -> List[str]:
    """结束本人这段对话里还开着的引导（点了停止 → ``expired / stopped``；说了不做了 → ``cancelled / user``），每条发一次事件，
    页面把卡读成失效一行。回结束了的那几条。"""
    ended = []
    for name in _open_runs(session_id):
        doc = _locked(name)  # loop-query: 一段对话同一时刻最多一条开着的引导（开新的先把旧的取消），按行锁逐条取
        if doc is not None and doc.status in OPEN:
            _end(doc, status, reason)
            ended.append(doc.name)
    return ended


# 交回模型的结果（只给模型）
ENDED, NO_OPEN = "ended", "no_open_guide"


def tool_end(_tool_input) -> str:
    """对话工具 ``pearnly_guide_end``：用户在输入框里明确说这一项不做了，结束本对话开着的引导（``cancelled / user``）。
    只改这次引导自己的状态，什么都不写进账本；没有开着的回 ``no_open_guide``。"""
    session = str((getattr(frappe.local, "pearnly_assistant", None) or {}).get("session_id") or "")
    if not session:
        return json.dumps({"status": NO_OPEN})
    _conversation(session)  # 只认本人的对话
    ended = end_open(session, "user", CANCELLED)
    return json.dumps({"status": ENDED if ended else NO_OPEN})


def reopen(name: str, key: str, version: int) -> Dict[str, Any]:
    """复核卡或「已定」上点某一步「改」（页面接口 ``guide_reopen``）：回到那一步重问。本人、版本与看到的一致、运行还开着；那一步要是这次
    引导此刻适用、已有值（答过的或后台定的）。原值记进 ``reopened``，题上作为 ``value``；依赖它的后面各步作废；回 asking、版本 +1；
    草稿号保留，交给下一次 ``draft``（产品据它作废上一张）。
    按单据的业务（复核就地改一格，:func:`edit`）不走这里。"""
    doc, guide, _now = _checked(name)
    if _documents(guide) or doc.status not in OPEN:
        return {**_shown(doc), "outcome": REFUSED}
    if int(version) != int(doc.version or 0):
        return {**_shown(doc), "outcome": CHANGED}
    key = str(key or "")
    answers = _answers(doc)
    decided = _decided(guide, doc, answers)
    values = steps.final_values(guide, answers, decided)
    if key not in {step["key"] for step in guide["steps"]} or values.get(key) in (None, "", [], {}):
        return {**_shown(doc), "outcome": REFUSED, "refused": _refusal({key or "step": "not offered"})}
    kept = steps.without_dependents(guide, answers, [key])
    kept.pop(key, None)
    doc.reopened = json.dumps({**_reopened(doc), key: values[key]}, ensure_ascii=False, default=str)
    doc.answers = json.dumps(kept, ensure_ascii=False, default=str)
    # 草稿号留着：下一次生成草稿时产品据 ``ctx["draft"]`` 作废上一张（收款：作废旧草稿再建新的），清掉就留下孤儿草稿（A2 11-19）
    doc.status, doc.result = ASKING, ""
    doc.version = int(doc.version or 0) + 1
    _save(doc)
    _announce(doc)
    return _shown(doc)


def cancel(name: str) -> Dict[str, Any]:
    doc, _guide_found, _now = _checked(name)
    if doc.status in OPEN:
        _end(doc, CANCELLED, "user")
    return _shown(doc)


def follow(name: str, given: Mapping[str, Any], version: int | None = None) -> Dict[str, Any]:
    """结果里的问题作答（约定 N1b）：本人、已办完、版本与看到的一致、答案合结果里此刻的子题，交声明的 ``follow``（``ctx["done"]`` =
    此刻存下的结果）算出新的结果存下。业务函数拒了（``{"returned": …}`` 或抛错）回 ``refused``、什么都不改。"""
    doc, guide, _now = _checked(name)
    if doc.status != DONE or not guide.get("follow"):
        return {**_shown(doc), "outcome": REFUSED}
    if version is not None and int(version) != int(doc.version or 0):
        return {**_shown(doc), "outcome": CHANGED}
    current = view(doc)
    asked = _subs(((current.get("result") or {}) if isinstance(current.get("result"), Mapping) else {}).get("questions"))
    problem = _sub_problem(asked, given) if asked else "not asked now"
    if problem:
        return {**current, "outcome": REFUSED, "refused": _refusal({"questions": problem})}
    ctx = {**_ctx(doc, _answers(doc)), "done": json.loads(doc.result or "null")}
    try:
        made = _call(guide["follow"], ctx, doc.draft or "", dict(given))
    except Exception as exc:  # noqa: BLE001 — 业务函数拒了：原因码交回页面，结果不动
        frappe.db.rollback()
        frappe.log_error(title="Assistant guide follow failed")
        return {**view(_locked(name)), "outcome": REFUSED,
                "refused": _refusal({"questions": str(getattr(exc, "code", "") or type(exc).__name__)})}
    if not isinstance(made, Mapping) or made.get("returned"):
        reason = next(iter((made or {}).get("returned") or {"questions": {"reason": "refused"}}.values()), {})
        return {**current, "outcome": REFUSED, "refused": _refusal({"questions": str((reason or {}).get("reason") or "refused")})}
    doc.result = json.dumps(dict(made), ensure_ascii=False, default=str)
    doc.version = int(doc.version or 0) + 1
    _save(doc)
    _announce(doc)
    return _shown(doc)


def view_of(name: str) -> Dict[str, Any]:
    """页面按 run 取这次引导此刻的卡（只读，GET：不在这里标失效——对话上下文变了只回 ``stale``，下一次作答 / 确定时由
    :func:`_checked` 标失效并告诉页面）。只给本人。"""
    found = frappe.db.get_value(DOCTYPE, {"name": str(name or ""), "user": frappe.session.user}, "name")
    if not found:
        frappe.throw(_("Not found"), frappe.DoesNotExistError)
    doc = frappe.get_doc(DOCTYPE, found)
    out = view(doc)
    if doc.status in OPEN:
        guide = registry.find(doc.business)
        if guide and _stale(guide, doc, _conversation(doc.session_id)) and not _fill_context_needed(doc):
            out["stale"] = True
    return out


def _fill_context_needed(doc) -> bool:
    """开始时没定、现在定下了的上下文（引导里选了账套）不算变了：作答时会补进记录。"""
    now = _conversation(doc.session_id)
    return any(not str(doc.get(column) or "") and str(now.get(column) or "") for column in CONTEXT_FIELDS.values())


def search(name: str, key: str, txt: str = "", start: int = 0, page_length: int = PAGE) -> Dict[str, Any]:
    """卡上一题的选项搜索 / 翻页（服务端分页，不一次取全）。只读，不锁。"""
    found = frappe.db.get_value(DOCTYPE, {"name": str(name or ""), "user": frappe.session.user}, "name")
    if not found:
        frappe.throw(_("Not found"), frappe.DoesNotExistError)
    doc = frappe.get_doc(DOCTYPE, found)
    guide = _guide(doc)
    if _documents(guide):
        if doc.status not in OPEN:
            frappe.throw(_("Invalid request"))
        return _docs().search(guide, doc, _ctx(doc, _answers(doc)), str(key or ""), txt, int(start or 0),
                              min(int(page_length or PAGE), 50))
    step = next((s for s in guide["steps"] if s["key"] == str(key or "") and s["input"] in d.OPTION_INPUTS), None)
    if step is None or doc.status not in OPEN:
        frappe.throw(_("Invalid request"))
    answers = _answers(doc)
    ctx = _ctx(doc, answers)
    found = _options(step, ctx, txt, start, min(int(page_length or PAGE), 50))
    hint = (_decided(guide, doc, answers).get(step["key"]) or {}).get("value") if (step.get("tier") or "how") == "how" else None
    return {**found, "options": _recommended_first(step, ctx, found["options"], hint, int(start or 0), str(txt or ""))}


# ── 事件 ─────────────────────────────────────────────────────────────────────────────────────────────────────
def event(doc) -> Dict[str, Any]:
    """一次引导 → 本站事件（编号带版本：同一次引导每走一步是新的一条，页面按编号去重；卡的内容页面按 ``run`` 另取）。

    ``started_at`` 是这次引导建起来的时刻（不随每一步变）：对话页按它排引导卡、定卡在哪条消息前面。11-26 回归：原来页面拿「第一次收到这次
    引导的事件」的修改时间当开始时间，待处理确定的同一笔里先开销售引导、再把待处理标完成，页面确定前没补拉到待处理时，待处理的开始时间
    成了完成那刻、晚于它开出来的销售引导，销售复核卡排到了待处理结果卡上面。"""
    moment = frappe.utils.get_datetime(doc.modified) if doc.get("modified") else None
    created = frappe.utils.get_datetime(doc.creation) if doc.get("creation") else None
    return {"id": f"pgd_{doc.name}_{int(doc.version or 0)}", "type": TYPE, "run": doc.name, "business": doc.business,
            "status": doc.status, "version": int(doc.version or 0),
            "processed_at": _iso(moment) if moment else "", "started_at": _iso(created) if created else ""}


def _iso(moment) -> str:
    from pearnly_core.assistant import write_plan

    return write_plan._iso(moment)


def _announce(doc) -> None:
    from pearnly_core.assistant import contracts

    try:
        frappe.publish_realtime(contracts.REALTIME_EVENT, {"session_id": doc.session_id, "event": event(doc)},
                                user=doc.user, after_commit=True)
    except Exception:  # noqa: BLE001 — 推不出去不影响业务：页面下一次补拉照样拿到
        pass


def feed(session_id: str, since=None) -> List[Dict[str, Any]]:
    """这段对话的引导事件（整段时每次引导此刻的一条，增量时锚点之后改过的）；只给本人。读不出不挡取事件。"""
    try:
        filters = {"session_id": session_id, "user": frappe.session.user}
        if since is not None:
            filters["modified"] = [">=", since]
        rows = frappe.get_all(DOCTYPE, filters=filters, fields=["name", "business", "status", "version", "modified", "creation"],
                              order_by="modified asc", limit_page_length=50)
        return [event(row) for row in rows]
    except Exception:  # noqa: BLE001
        frappe.log_error(title="Assistant guide feed failed")
        return []


def forget(session_id: str) -> None:
    """删对话时一起删。"""
    frappe.db.delete(DOCTYPE, {"session_id": session_id})


__all__ = ["ASKING", "CANCELLED", "CHANGED", "CONFIRMED", "DOCTYPE", "DOCUMENT_HOOKS", "DONE", "EXPIRED", "FAILED", "OPEN",
           "REFUSED", "REVIEWING", "STOPPED", "TYPE", "answer", "cancel", "confirm", "edit", "end_open", "event", "feed",
           "follow", "forget", "search", "start", "tool_end", "view", "view_of"]
