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

from pearnly_core.assistant.guide import declaration as d, registry, steps

DOCTYPE = "Pearnly Assistant Guide Run"
TYPE = "pearnly.guide"
ASKING, REVIEWING, CONFIRMED, DONE, FAILED, CANCELLED, EXPIRED = (
    "asking", "reviewing", "confirmed", "done", "failed", "cancelled", "expired")
OPEN = (ASKING, REVIEWING)
PAGE = 20
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
    except frappe.QueryDeadlockError:
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
    return {key: value for key, value in found.items() if value}


def _options(step: Mapping[str, Any], ctx: Dict[str, Any], txt="", start=0, page_length=PAGE, codes=None) -> Dict[str, Any]:
    out = _call(step["options"], ctx, txt=str(txt or ""), start=int(start or 0), page_length=int(page_length or PAGE), codes=codes)
    rows = [dict(row) for row in (out or {}).get("options") or () if isinstance(row, Mapping) and row.get("code")]
    found = {"options": rows, "more": bool((out or {}).get("more"))}
    # 选项函数可以带两样：``facts``（题目下的事实行，例：读回还没到、电脑没连）、``wait``（选项还在读，页面过一会儿再取这张卡）
    facts = [str(item) for item in (out or {}).get("facts") or () if str(item or "").strip()]
    if facts:
        found["facts"] = facts
    if (out or {}).get("wait"):
        found["wait"] = True
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
    offered = {row["code"]: row for row in _options(step, ctx, codes=wanted, page_length=len(wanted) or 1)["options"]}
    if any(code not in offered for code in wanted):
        return "not offered"
    if step["input"] in ("amounts", "split"):
        for row in value:
            top = offered[str(row["code"])].get("max")
            if top not in (None, "") and steps.amount(row["amount"]) > steps.amount(top):
                return "over max"
    return ""


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


# ── 给页面的样子 ─────────────────────────────────────────────────────────────────────────────────────────────
def _question(guide: Mapping[str, Any], step: Mapping[str, Any], ctx: Dict[str, Any], answers: Mapping[str, Any],
              decided: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    out = {"key": step["key"], "input": step["input"], "rule": step["rule"], "tier": step.get("tier") or "how",
           "title_key": f"guide.step.{guide['id']}.{step['key']}", "required": step.get("required", True)}
    facts: List[str] = []
    if step["input"] in d.QUESTION_INPUTS:
        found = _call(step["options"], ctx) or {}
        out["questions"] = _subs(found.get("questions"))
        facts.extend(str(item) for item in found.get("facts") or () if str(item or "").strip())
        if found.get("wait"):
            out["wait"] = True
    if step["input"] in d.OPTION_INPUTS:
        found = _options(step, ctx)
        out.update(options=found["options"], more=found["more"], search=bool(step.get("search")))
        facts.extend(found.get("facts") or ())
        if found.get("wait"):
            out["wait"] = True
    if step.get("facts"):
        facts.extend(str(item) for item in _call(step["facts"], ctx) or () if str(item or "").strip())
    if facts:
        out["facts"] = facts
    # ``always`` 的步骤后台给了值：只作推荐预选，点了才算
    hint = (decided.get(step["key"]) or {}).get("value")
    if hint not in (None, "", [], {}) and out["tier"] == "how":
        out["preselected"] = hint
    if answers.get(step["key"]) not in (None, "", [], {}):
        out["value"] = answers[step["key"]]
    return out


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
    if doc.status == ASKING:
        decided = _decided(guide, doc, answers)
        asked = steps.to_ask(guide, answers, decided)
        settled = steps.settled(guide, answers, decided)
        out["card"] = {"version": 2, "questions": [_question(guide, step, ctx, answers, decided) for step in asked],
                       "decided": [{**item, "title_key": f"guide.step.{guide['id']}.{item['key']}"} for item in settled],
                       "ready": steps.state(guide, answers, decided) == steps.READY}
        returned = _returned(doc)
        if returned:
            out["card"]["returned"] = returned
    elif doc.status == REVIEWING and guide.get("review"):
        out["review"] = _call(guide["review"], ctx, doc.draft or "")
    elif doc.status in (CONFIRMED, DONE, FAILED):
        out["result"] = json.loads(doc.result or "null")
        if guide.get("result") and doc.status != FAILED:
            out["result"] = _call(guide["result"], {**ctx, "done": out["result"]}, doc.draft or "") or out["result"]
        if isinstance(out["result"], Mapping) and out["result"].get("questions") is not None:
            out["result"] = {**out["result"], "questions": _subs(out["result"]["questions"])}
    return out


def _consent(guide: Mapping[str, Any], doc, answers: Mapping[str, Any]) -> str:
    """确定画成什么：``allow``（拒绝 / 允许）或 ``confirm``。声明写成与 ``when`` 同形的字典时按那一步此刻的值判。"""
    consent = guide.get("consent")
    if not isinstance(consent, Mapping):
        return consent or "confirm"
    values = steps._values(guide, answers, _decided(guide, doc, answers))
    return "allow" if steps.applies({"when": consent}, values) else "confirm"


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
def _stale(guide: Mapping[str, Any], doc, now: Mapping[str, Any]) -> str:
    """这次引导开始之后，本对话的公司 / 账套 / 月份变了没有（按声明的 ``expires``）；变了回是哪一项。"""
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


def _checked(name: str):
    """锁住并核过的 (记录, 声明, 本对话此刻)。不是本人的 / 找不到抛未找到；已失效的当场标失效。"""
    doc = _locked(name)
    if doc is None:
        frappe.throw(_("Not found"), frappe.DoesNotExistError)
    guide = _guide(doc)
    now = _conversation(doc.session_id)
    if doc.status in OPEN and _fill_context(doc, now):
        _save(doc)
    if doc.status in OPEN:
        stale = _stale(guide, doc, now)
        if stale:
            _end(doc, EXPIRED, stale)
    return doc, guide, now


# ── 走法 ─────────────────────────────────────────────────────────────────────────────────────────────────────
def start(session_id: str, business: str) -> Dict[str, Any]:
    now = _conversation(session_id)
    guide = registry.find(business)
    if not guide or guide["source"] not in (d.FORM, d.DOCUMENTS) or now["mode"] not in guide["modes"] \
            or business not in registry.offered().get(now["mode"], []):
        frappe.throw(_("Invalid request"))
    if _documents(guide) and not all(guide.get(key) for key in DOCUMENT_HOOKS):
        frappe.throw(_("Invalid request"))  # 声明还没点名 documents 源要的产品函数（这一格还不能在页面上办）
    for name in _open_runs(now["session_id"]):
        old = _locked(name)  # loop-query: 一段对话同一时刻最多一条开着的引导（开新的先把旧的取消），按行锁逐条取
        if old is not None and old.status in OPEN:
            _end(old, CANCELLED, "replaced")
    doc = _insert({"session_id": now["session_id"], "user": frappe.session.user, "business": business, "status": ASKING,
                   "company": now["company"], "ledger": now["ledger"], "period": now["period"], "answers": "{}", "version": 1,
                   **({"documents": _docs().initial()} if _documents(guide) else {})})
    _announce(doc)
    return view(doc)


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
        return {**view(doc), "outcome": REFUSED}
    if version is not None and int(version) != int(doc.version or 0):
        return {**view(doc), "outcome": CHANGED}
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
        return {**view(doc), "outcome": REFUSED, "refused": refused}
    changed = [key for key, value in given.items() if answers.get(key) != value]
    answers = steps.without_dependents(guide, answers, changed)
    answers.update(given)
    for key in changed:
        if by_key[key].get("apply"):
            _call(by_key[key]["apply"], _ctx(doc, answers), given[key])
    if changed and any(by_key[key].get("apply") for key in changed):
        _fill_context(doc, _conversation(doc.session_id))
    _settle(guide, doc, answers)
    return view(doc)


def _answer_documents(guide: Mapping[str, Any], doc, given: Mapping[str, Any]) -> Dict[str, Any]:
    """按单据的一次作答（交文件或题卡）：收下了版本 +1、存、推事件；不收回 ``refused``（卡不变，页面可重答）。"""
    found = _docs().answer(guide, doc, _ctx(doc, _answers(doc)), given)
    if found.get("refused"):
        return {**view(doc), "outcome": REFUSED, "refused": found["refused"],
                **({"errors": found["errors"]} if found.get("errors") else {})}
    doc.version = int(doc.version or 0) + 1
    _save(doc)
    _announce(doc)
    return view(doc)


def edit(name: str, doc_id: str, field: str, value: Any, version: int) -> Dict[str, Any]:
    """复核卡就地改一格（按单据的业务）：本人、版本与看到的一致、运行没失效；产品写回自己的单，版本 +1。"""
    doc, guide, _now = _checked(name)
    if not _documents(guide) or doc.status not in OPEN:
        return {**view(doc), "outcome": REFUSED}
    if int(version) != int(doc.version or 0):
        return {**view(doc), "outcome": CHANGED}
    found = _docs().edit(guide, doc, _ctx(doc, _answers(doc)), str(doc_id or ""), str(field or ""), value)
    if isinstance(found, Mapping) and found.get("refused"):
        return {**view(doc), "outcome": REFUSED, "refused": {str(field or ""): found["refused"]}}
    doc.version = int(doc.version or 0) + 1
    _save(doc)
    _announce(doc)
    return view(doc)


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
            return {**view(doc), "outcome": result["outcome"]}
        doc.status = DONE
    except Exception as exc:  # noqa: BLE001 — 放行 / 产品函数拒了、出错：记失败与原因码，交回页面（结果卡说明）
        frappe.db.rollback()
        doc = _locked(name)
        result = {"status": "failed", "reason": str(getattr(exc, "code", "") or type(exc).__name__)}
        doc.status = FAILED
        frappe.log_error(title="Assistant guide confirm failed")
    doc.result = json.dumps(result, ensure_ascii=False, default=str)
    doc.version = int(doc.version or 0) + 1
    _save(doc)
    _announce(doc)
    return view(doc)


def confirm(name: str, version: int, seen: str | None = None) -> Dict[str, Any]:
    """``seen``：按单据的业务，页面带回复核卡上的指纹（``review.seen``）；与此刻不一致（票在别处改过）不办。表单业务不用。"""
    doc, guide, now = _checked(name)
    consent = f"guide:{doc.name}:{int(version)}"
    if doc.status in (CONFIRMED, DONE, FAILED):
        # 同一确定再来一次（双击、重发、另一台设备）：回上次的结果，不办第二次
        return {**view(doc), "outcome": "" if doc.consent == consent else REFUSED}
    if doc.status not in OPEN:
        return {**view(doc), "outcome": REFUSED}
    if int(version) != int(doc.version or 0):
        return {**view(doc), "outcome": CHANGED}
    if doc.business not in registry.offered().get(now["mode"], []):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    if now.get("stopped_at") and doc.modified and now["stopped_at"] >= frappe.utils.get_datetime(doc.modified):
        return {**view(doc), "outcome": STOPPED}
    if _documents(guide):
        return _confirm_documents(guide, doc, consent, seen)
    answers = _answers(doc)
    decided = _decided(guide, doc, answers)
    if steps.state(guide, answers, decided) != steps.READY or (guide.get("review") and doc.status != REVIEWING):
        return {**view(doc), "outcome": REFUSED}
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
            return {**view(doc), "outcome": CHANGED}
        if isinstance(result, Mapping) and result.get("returned"):
            _send_back(guide, doc, answers, result["returned"])
            doc.version = int(doc.version or 0) + 1
            _save(doc)
            _announce(doc)
            return {**view(doc), "outcome": REFUSED}
        doc.status = DONE
    except Exception as exc:  # noqa: BLE001 — 业务函数拒了 / 出错：记失败与原因码，交回页面（结果卡说明）
        frappe.db.rollback()
        doc = _locked(name)
        result = {"status": "failed", "reason": str(getattr(exc, "code", "") or type(exc).__name__)}
        doc.status = FAILED
        frappe.log_error(title="Assistant guide confirm failed")
    doc.result = json.dumps(result, ensure_ascii=False, default=str)
    doc.version = int(doc.version or 0) + 1
    _save(doc)
    _announce(doc)
    return view(doc)


def cancel(name: str) -> Dict[str, Any]:
    doc, _guide_found, _now = _checked(name)
    if doc.status in OPEN:
        _end(doc, CANCELLED, "user")
    return view(doc)


def follow(name: str, given: Mapping[str, Any], version: int | None = None) -> Dict[str, Any]:
    """结果里的问题作答（约定 N1b）：本人、已办完、版本与看到的一致、答案合结果里此刻的子题，交声明的 ``follow``（``ctx["done"]`` =
    此刻存下的结果）算出新的结果存下。业务函数拒了（``{"returned": …}`` 或抛错）回 ``refused``、什么都不改。"""
    doc, guide, _now = _checked(name)
    if doc.status != DONE or not guide.get("follow"):
        return {**view(doc), "outcome": REFUSED}
    if version is not None and int(version) != int(doc.version or 0):
        return {**view(doc), "outcome": CHANGED}
    current = view(doc)
    asked = _subs(((current.get("result") or {}) if isinstance(current.get("result"), Mapping) else {}).get("questions"))
    problem = _sub_problem(asked, given) if asked else "not asked now"
    if problem:
        return {**current, "outcome": REFUSED, "refused": {"questions": problem}}
    ctx = {**_ctx(doc, _answers(doc)), "done": json.loads(doc.result or "null")}
    try:
        made = _call(guide["follow"], ctx, doc.draft or "", dict(given))
    except Exception as exc:  # noqa: BLE001 — 业务函数拒了：原因码交回页面，结果不动
        frappe.db.rollback()
        frappe.log_error(title="Assistant guide follow failed")
        return {**view(_locked(name)), "outcome": REFUSED,
                "refused": {"questions": str(getattr(exc, "code", "") or type(exc).__name__)}}
    if not isinstance(made, Mapping) or made.get("returned"):
        reason = next(iter((made or {}).get("returned") or {"questions": {"reason": "refused"}}.values()), {})
        return {**current, "outcome": REFUSED, "refused": {"questions": str((reason or {}).get("reason") or "refused")}}
    doc.result = json.dumps(dict(made), ensure_ascii=False, default=str)
    doc.version = int(doc.version or 0) + 1
    _save(doc)
    _announce(doc)
    return view(doc)


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
    return _options(step, _ctx(doc, _answers(doc)), txt, start, min(int(page_length or PAGE), 50))


# ── 事件 ─────────────────────────────────────────────────────────────────────────────────────────────────────
def event(doc) -> Dict[str, Any]:
    """一次引导 → 本站事件（编号带版本：同一次引导每走一步是新的一条，页面按编号去重；卡的内容页面按 ``run`` 另取）。"""
    moment = frappe.utils.get_datetime(doc.modified) if doc.get("modified") else None
    return {"id": f"pgd_{doc.name}_{int(doc.version or 0)}", "type": TYPE, "run": doc.name, "business": doc.business,
            "status": doc.status, "version": int(doc.version or 0),
            "processed_at": _iso(moment) if moment else ""}


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
        rows = frappe.get_all(DOCTYPE, filters=filters, fields=["name", "business", "status", "version", "modified"],
                              order_by="modified asc", limit_page_length=50)
        return [event(row) for row in rows]
    except Exception:  # noqa: BLE001
        frappe.log_error(title="Assistant guide feed failed")
        return []


def forget(session_id: str) -> None:
    """删对话时一起删。"""
    frappe.db.delete(DOCTYPE, {"session_id": session_id})


__all__ = ["ASKING", "CANCELLED", "CHANGED", "CONFIRMED", "DOCTYPE", "DOCUMENT_HOOKS", "DONE", "EXPIRED", "FAILED", "OPEN", "REFUSED",
           "REVIEWING", "STOPPED", "TYPE", "answer", "cancel", "confirm", "edit", "event", "feed", "follow", "forget", "search", "start",
           "view"]
