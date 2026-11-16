"""开始前四题（单位、负库存、出库成本、现金科目）在 ERP 这一侧的接法（A3 / P2-2，主会话 11-16 审定）。判据在共用层
`pearnly_core.express_rules.push.book_questions`（与 CC 桌面版写时停下同一判法）；这里只做 ERP 的三件事：

1. **什么时候问**：只在有人看着的地方问——开始前确认卡片 / 代录预检（`precheck.collect` 包在 :func:`asking` 里）。提交后排的直写
   （`push.send` 在后台跑，分不出是网页、CC 对话还是 LINE）只**套用**记住的与自动定下的值，不新增停下：LINE 这次不出这四题、行为不变
   （主会话 11-16）。没答的照旧交 CC 桌面版按原样写。
2. **问什么、记在哪**（``ERP Express Choice``，按公司，对象前面都带账套键）：
   - 单位 ``{账套}|unit|方向|物料|票面单位``、现金科目 ``{账套}|cash``：按账套记住；
   - 新物料负库存：选「允许」记 ``{账套}|new-item``（这本账套之后的新物料都按允许写）；「这张先留下」**不记**——这张单这次不写，下次再问；
   - 出库成本：只对这一张单生效，对象带单据号 ``{账套}|doc:{单据}|line-N``（不跨单复用，主会话 11-16「不预选、不记，每次问」）。
3. **代录**：代录任务 / 代录对话里只问单位与负库存，下发也只写 CC 代录单认的行键（成本、现金科目由 Express 界面自己定）。

已有物料库存不够这类不是选择题的事实：开始前判出时这张停在 ``STOCK_INSUFFICIENT``（同 CC 写时回的码，已有译文），写不进。

网页提交前的弹窗（`api.preflight` → `precheck.collect_form`，单据还没存，单据号是 :data:`FORM_REF`）：不问出库成本（答案只对一张单，
单据还没号、挂不到存好的单上）、负库存不给「先留下」（关掉弹窗就是不提交）。出库成本在 CC 直写的开始前确认卡片上问（单据已存）。
"""
from __future__ import annotations

import contextlib
import contextvars

from pearnly_core.express_rules.push import book_questions as rules

KINDS = frozenset(rules.KINDS)
FORM_REF = "preflight"  # `push.form_document` 给没存的表单的单据号
STOCK_INSUFFICIENT = "STOCK_INSUFFICIENT"
_ASKING = contextvars.ContextVar("erp_book_questions_asking", default=False)
_OPERATING = contextvars.ContextVar("erp_book_questions_operating", default=False)


@contextlib.contextmanager
def asking():
    """这个范围里准备载荷时把四题判出来要人答（开始前确认卡片、代录预检）；范围外只套用记住的值。"""
    token = _ASKING.set(True)
    try:
        yield
    finally:
        _ASKING.reset(token)


@contextlib.contextmanager
def operating():
    """这个范围里是代录（代录预检 `operator.precheck` 显式标，不靠对话上下文猜）。"""
    token = _OPERATING.set(True)
    try:
        yield
    finally:
        _OPERATING.reset(token)


def ask_now() -> bool:
    return bool(_ASKING.get())


def operator_now() -> bool:
    """此刻是代录：交单时 `push.send(operator=…)` 设了任务（`conversation.for_task`），或 CC 正在代录对话里执行工具。"""
    import frappe

    from pearnly_erp_portal.express import conversation

    if _OPERATING.get() or conversation._TASK.get():
        return True
    session = conversation.session()
    return bool(session and frappe.db.get_value(conversation.SESSION, {"session_id": session}, "operator_task"))


def _subject(book_key, kind, subject, doc_ref) -> str:
    if kind == rules.COST:
        return f"{book_key}|doc:{doc_ref}|{subject}"
    if kind == rules.NEGATIVE:
        return f"{book_key}|new-item|{subject}"
    return f"{book_key}|{subject}"


def remembered(all_remembered, book_key, doc_ref) -> dict:
    """这家公司记过的（含判定范围里人刚答 / 预选的，`choices.assuming`）→ 判据认的 ``{kind: {subject: value}}``（去掉账套前缀）。"""
    out = {}
    prefix = f"{book_key}|"
    for kind in (rules.UNIT, rules.CASH):
        for subject, value in (all_remembered.get(kind) or {}).items():
            if subject.startswith(prefix) and value:
                out.setdefault(kind, {})[subject[len(prefix):]] = value
    doc = f"{book_key}|doc:{doc_ref}|"
    for subject, value in (all_remembered.get(rules.COST) or {}).items():
        if subject.startswith(doc) and value:
            out.setdefault(rules.COST, {})[subject[len(doc):]] = value
    negative = all_remembered.get(rules.NEGATIVE) or {}
    if negative.get(f"{book_key}|{rules.NEW_ITEM_SUBJECT}") == rules.ALLOW:
        out.setdefault(rules.NEGATIVE, {})[rules.NEW_ITEM_SUBJECT] = rules.ALLOW
    new_items = f"{book_key}|new-item|"
    for subject, value in negative.items():
        if subject.startswith(new_items) and value == rules.ALLOW:
            # 判定范围里人刚选的「允许」（还没记成账套默认）：这张单的这件新物料按允许算
            out.setdefault(rules.NEGATIVE, {})[subject[len(new_items):]] = rules.ALLOW
    return out


def apply(payload, data, book_key, doc_ref, all_remembered) -> dict:
    """准备好的载荷 → ``{"payload"}``（套用记住的 / 自动定的）或 ``{"hold", "needed"}``（判定范围里还要问）或 ``{"hold"}``（库存不够）。"""
    mode = rules.OPERATOR if operator_now() else rules.DIRECT
    facts = rules.facts_of(data)
    known = remembered(all_remembered, book_key, doc_ref) if doc_ref != FORM_REF else {
        kind: values for kind, values in remembered(all_remembered, book_key, doc_ref).items() if kind != rules.COST}
    found = rules.questions(facts, payload, mode=mode, remembered=known)
    decided = {kind: dict(values) for kind, values in known.items()}
    for kind, values in found["auto"].items():
        decided.setdefault(kind, {}).update(values)
    if ask_now():
        form = doc_ref == FORM_REF
        needed = [question for question in found["needed"] if not (form and question["kind"] == rules.COST)]
        if found["facts"]:
            return {"hold": STOCK_INSUFFICIENT}
        if needed:
            return {"needed": [_needed(book_key, doc_ref, question, form) for question in needed]}
    rules.apply(payload, decided, guided=False, mode=mode)
    return {"payload": payload}


# 固定选项的字（四语在 ERP 术语表 ``express.book.*``）
OPTION_TEXT = {rules.ALLOW: "express.book.allowNegative", rules.HOLD: "express.book.hold",
               rules.COST_NONE: "express.book.costNone", rules.COST_GIVEN: "express.book.costGiven",
               rules.FACTOR: "express.book.unitFactor"}


def _option(option) -> dict:
    """选项给卡片：固定选项按术语表取字；物料的单位写「码 × 系数」（库存单位只写码）；带数字输入的保留 ``entry``。"""
    import frappe

    code = str(option.get("code") or "")
    if code in OPTION_TEXT:
        name = frappe._(OPTION_TEXT[code])
    elif "factor" in option:
        name = code if option["factor"] in ("1", "0", "") else f"{code} × {option['factor']}"
    else:
        name = str(option.get("name") or "")
    return {"code": code, "name": name, **({"entry": dict(option["entry"])} if option.get("entry") else {})}


def _needed(book_key, doc_ref, question, form=False) -> dict:
    """判据的一题 → ERP 卡片 / 单据页的一项（``{kind, subject, label, options, current, preselected?}``）。"""
    context = question.get("context") or {}
    item = {"kind": question["kind"], "subject": _subject(book_key, question["kind"], question["subject"], doc_ref),
            "label": str(context.get("name") or context.get("cash_account") or ""),
            "options": [_option(option) for option in question["options"]
                        if not (form and option.get("code") == rules.HOLD)], "current": "",
            "lines": question.get("lines") or []}
    if question.get("preselected"):
        item["preselected"] = question["preselected"]
    return item


def value_problem(item, value) -> str:
    """单据页 / 卡片答这四题的值合不合格（选项码，带数字输入的选项要「码:数」）；合格回 ''。"""
    return rules.answer_problem(item, value)


def keep(item, value) -> bool:
    """这一项答了要不要写进记忆：负库存答「这张先留下」不记（这次不写、下次再问）；其余照记（成本的对象只对这张单）。"""
    return not (item.get("kind") == rules.NEGATIVE and str(value or "").strip() == rules.HOLD)


def remember_extra(company, item, value, remember) -> None:
    """负库存答「允许」时另记一条账套默认（``{账套}|new-item`` = Y），之后这本账套的新物料都按允许写、不再问。"""
    if item.get("kind") != rules.NEGATIVE or str(value or "").strip() != rules.ALLOW:
        return
    book_key = str(item.get("subject") or "").split("|new-item|")[0]
    if book_key:
        remember(company, rules.NEGATIVE, f"{book_key}|{rules.NEW_ITEM_SUBJECT}", rules.ALLOW, "")


def probe(item) -> str:
    """没预选时判定临时往下算用的值：第一个不用填数的选项；负库存按「允许」试（「先留下」后面就没有要判的了）。"""
    if item.get("kind") == rules.NEGATIVE:
        return rules.ALLOW
    return next((str(opt.get("code") or "") for opt in item.get("options") or () if not opt.get("entry")), "")


__all__ = ["KINDS", "STOCK_INSUFFICIENT", "apply", "ask_now", "asking", "keep", "operating", "operator_now", "probe",
           "remember_extra",
           "remembered", "value_problem"]
