"""「开始前确认」卡片的契约（多产品共用，纯函数；评估 `docs/work/2026-11-04-precheck-card.md` 第 2、3 节）。

产品把一批单逐张判好（每张单要人定的项，字段同共用写账规则 `express_rules.push.choices.resolve` 的 ``needed``，
另带这一项现在的值与来源），交给 :func:`merge` 合成一张卡片：

- **按业务分组**：账套与单据、采购、销售、库存、服务、付款（:data:`GROUPS`，卡片上的顺序）。
- **跨单去重**：同一类、同一归类键（``kind`` + ``subject``）的项只问一次，记下影响哪几张单（``docs``）。
- **三种状态**（:func:`state_of`）：已定（记住的、公司规则、Express 历史、任务默认值、来源单带的码、票面写明、账套设置定的，
  照写，卡片里收进「已定 N 项」可改）；预选（规则、历史次数、已有建议、CC 的提议给了值，要人点「开始」才算数，主会话 11-04 Q2）；
  未定（没有值，排在本组最前；依赖它的单留下，其余照写，不硬拦）。
- 值不在可选范围里（选过的档在 Express 里被删 / 改了）当没有值，不静默换别的（同 `resolve`）。

卡片点「开始」交回的答案经 :func:`expand` 展开到每张单 ``[{kind, subject, value, label, remember}]``，产品按自己现有的
记法存（记在哪、谁能选都在产品里）。:func:`held_back` / :func:`writable` 算哪些单因未定项留下、哪些照写。

不依赖 Frappe、不读库、不做 IO。
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

# ── 业务分组（卡片上的顺序）──
BOOK, PURCHASE, SALES, STOCK, SERVICE, PAYMENT = "book", "purchase", "sales", "stock", "service", "payment"
GROUPS = (BOOK, PURCHASE, SALES, STOCK, SERVICE, PAYMENT)

# ── 三种状态（组内的顺序：未定在前）──
OPEN, PROPOSED, DECIDED = "open", "proposed", "decided"
STATES = (OPEN, PROPOSED, DECIDED)

# ── 值的来源 ──
# 已定：不用问、照写
PINNED = "pinned"                  # 来源单已带编码（ERP 前台从 Express 回读的主档里选的）
TASK_DEFAULT = "task_default"      # 定时任务 / 自动录入规则给的默认：只管这个任务的单，改了不进账套记忆（10-08）
REMEMBERED = "remembered"          # 账套记忆 / 按公司记的
COMPANY_RULE = "company_rule"      # Cowork 公司规则
EXPRESS_HISTORY = "express_history"  # Express 历史一贯（例：同一供应商近 12 个月 ≥3 次同科目）
BOOK_SETTING = "book_setting"      # 账套自己的设置定的（单别默认科目、逐行 VAT、只有一个可用值）
FACE = "face"                      # 票面写明（付款状态 / 方式）
DOCUMENT = "document"              # 这张单上人定的（只管这一张，不记进账套记忆；例：这家平时赊购，这一张现金付的）
DECIDED_SOURCES = frozenset({PINNED, TASK_DEFAULT, REMEMBERED, COMPANY_RULE, EXPRESS_HISTORY, BOOK_SETTING, FACE,
                             DOCUMENT})
# 预选：要人点「开始」才算数
SUGGESTED = "suggested"            # 已有的建议（费用科目建议的第一个）
HISTORY = "history"                # 按使用次数
RULE = "rule"                      # `preselect` 的固定判法
CC = "cc"                          # 直写时 CC 推送带的提议（主会话 11-04 Q4；定时与代录不调模型）
PROPOSED_SOURCES = frozenset({SUGGESTED, HISTORY, RULE, CC})
SOURCES = DECIDED_SOURCES | PROPOSED_SOURCES

# ── 每张单的状态（产品判出的，不是选择项）──
CHECK = "check"                    # 正常：看它的项
UNKNOWN = "unknown"                # 上次写入结果不明：要人核对已入账 / 没入账（流程锁，防重复入账）
NOT_WRITABLE = "not_writable"      # 写不了：Express 自己也拒（期间外等），卡片只列事实
WAITING = "waiting"                # 等电脑读账套（主档、科目表还没读到），读到后重判，不问人
DOC_STATES = (CHECK, UNKNOWN, NOT_WRITABLE, WAITING)

# ── 哪一类进哪一组 ──
# party 按方向进采购 / 销售；account 按归类键与方向：``stock:<单别>`` 是库存单科目，销项是收入科目，其余是服务类费用科目
KIND_GROUPS = {
    "account_set": BOOK, "current_book": BOOK, "direction": BOOK, "document_policy": BOOK, "department": BOOK,
    "mrerp_menu": BOOK, "mrerp_default": BOOK,
    "vat": PURCHASE,
    "refund": SALES, "vat_registered": SALES, "exempt_mode": SALES,
    "item": STOCK, "stock_group": STOCK, "goods_posting": STOCK,
    "service_vat": SERVICE, "posting_kind": SERVICE,
    "payment": PAYMENT, "receipt": PAYMENT,
}
KINDS = frozenset(KIND_GROUPS) | {"party", "account"}
STOCK_ACCOUNT_PREFIX = "stock:"
# 只管这一张单的类：改了不记住
PER_DOCUMENT_KINDS = frozenset({"posting_kind"})
# 能只答给其中一张单的类（答案带 ``doc_id``）：只写那一张自己的、不记住、不合并（主会话 11-05：「按供应商记住照旧是默认，
# 单张的例外 CC 在对话里也能改」）。产品把这一张自己定的列成单独一项，来源 :data:`DOCUMENT`
DOCUMENT_ANSWER_KINDS = frozenset({"payment"})

MAX_DOCUMENTS = 500                # 一张卡片最多几张单（代录一批 200 张，留余量）
MAX_ANSWERS = 500
SMALL_LIST = 20                    # 可选值超过这么多条的项不在卡片里给 options，只给预选值与搜索类型（服务端分页）
SUBJECT_MAX = 1000                 # 同 Cowork 账套记忆的归类键上限（`push/choices.record`）
LABEL_MAX = 200
VALUE_MAX = 200

# ── 答案出错的原因码（交回页面，页面按码出文案）──
ERR_UNKNOWN_KEY = "precheck.unknown_key"
ERR_DUPLICATE_KEY = "precheck.duplicate_key"
ERR_EMPTY_VALUE = "precheck.empty_value"
ERR_NOT_OFFERED = "precheck.value_not_offered"
ERR_TOO_MANY = "precheck.too_many_answers"
ERR_NOT_PER_DOCUMENT = "precheck.not_per_document"  # 这一类不能只答给一张单（不在 DOCUMENT_ANSWER_KINDS），或这张单不在这一项里


class PrecheckError(ValueError):
    """产品交来的数据不合契约（类、归类键、单号不对）：是程序错，不是要人选的事。"""


def item_key(kind: str, subject: str) -> str:
    """一项的稳定编号（页面拿它回答）：同一类、同一归类键得到同一个，跨请求、跨设备一致。"""
    raw = json.dumps([str(kind), str(subject)], ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def group_of(kind: str, subject: str = "", direction: str = "") -> str:
    """这一类进哪一组。不认得的类抛 :class:`PrecheckError`（新加一类要同时登记分组）。"""
    if kind == "party":
        return SALES if direction == "sales" else PURCHASE
    if kind == "account":
        if str(subject or "").startswith(STOCK_ACCOUNT_PREFIX):
            return STOCK
        return SALES if direction == "sales" else SERVICE
    if kind not in KIND_GROUPS:
        raise PrecheckError(f"unknown kind: {kind!r}")
    return KIND_GROUPS[kind]


def _codes(options) -> Optional[set]:
    if not isinstance(options, (list, tuple)) or not options:
        return None
    return {str(option.get("code") or "").strip() for option in options if isinstance(option, Mapping)}


def _text(value, limit) -> str:
    return str(value if value is not None else "").strip()[:limit]


def normalize_item(raw: Mapping[str, Any], direction: str = "") -> Dict[str, Any]:
    """产品交来的一项 → 卡片里的一项（还没合并）。

    入：``{kind, subject, label?, value?, value_label?, source?, options?, search?, direction?}``。
    ``options``：``[{code, name?, ...}]``，给了就是可选范围；``search``：可选范围太大时页面去服务端搜（例如 ``accounts`` /
    ``products``），值不在 ``options`` 里也不算错。"""
    if not isinstance(raw, Mapping):
        raise PrecheckError("item must be a mapping")
    kind = str(raw.get("kind") or "").strip()
    subject = raw.get("subject")
    if kind not in KINDS:
        raise PrecheckError(f"unknown kind: {kind!r}")
    if not isinstance(subject, str) or not subject or len(subject) > SUBJECT_MAX:
        raise PrecheckError(f"bad subject for {kind}")
    direction = str(raw.get("direction") or direction or "")
    source = str(raw.get("source") or "").strip()
    if source and source not in SOURCES:
        raise PrecheckError(f"unknown source: {source!r}")
    value = _text(raw.get("value"), VALUE_MAX)
    options = [dict(option) for option in raw.get("options") or () if isinstance(option, Mapping)]
    codes = _codes(options)
    if value and codes is not None and value not in codes and not raw.get("search"):
        value, source = "", ""  # 选过的值不在可选范围里（被删 / 改了）：当没选过，不静默换别的
    if not value:
        source = ""
    item = {"key": item_key(kind, subject), "kind": kind, "subject": subject,
            "group": group_of(kind, subject, direction), "label": _text(raw.get("label"), LABEL_MAX),
            "value": value, "value_label": _text(raw.get("value_label"), LABEL_MAX) if value else "",
            "source": source}
    item["state"] = state_of(item)
    item["remember"] = kind not in PER_DOCUMENT_KINDS and source not in (TASK_DEFAULT, DOCUMENT)
    if options:
        # 预选 / 已定的值标「推荐」并排第一（卡片照 Claude Code 的提问卡片，推荐项在第一个，主会话 11-05）；``note`` 是这个
        # 选项的依据（一句话，产品给的照带）
        if value:
            options = ([{**option, "recommended": True} for option in options if str(option.get("code") or "") == value]
                       + [option for option in options if str(option.get("code") or "") != value])
        item["options"] = options
    if raw.get("search"):
        item["search"] = _text(raw.get("search"), 40)
    return item


def state_of(item: Mapping[str, Any]) -> str:
    """已定 / 预选 / 未定。有值但来源不明的按预选算（宁可让人看一眼，不当已定照写）。"""
    if not str(item.get("value") or "").strip():
        return OPEN
    return DECIDED if item.get("source") in DECIDED_SOURCES else PROPOSED


_RANK = {DECIDED: 0, PROPOSED: 1, OPEN: 2}


def _pick(candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    """同一项在几张单上给的值不一样时取哪个：状态最强的（已定 > 预选 > 未定）；同状态取出现最多的值；再同取先出现的。"""
    best = min(_RANK[candidate["state"]] for candidate in candidates)
    pool = [candidate for candidate in candidates if _RANK[candidate["state"]] == best]
    counts = Counter(candidate["value"] for candidate in pool)
    top = max(counts.values())
    return next(candidate for candidate in pool if counts[candidate["value"]] == top)


def _doc_id(raw) -> str:
    doc_id = raw.get("doc_id") if isinstance(raw, Mapping) else None
    if not isinstance(doc_id, str) or not doc_id.strip():
        raise PrecheckError("document without doc_id")
    return doc_id.strip()


def _fact(doc: Mapping[str, Any], doc_id: str) -> Dict[str, Any]:
    fact = {"doc_id": doc_id, "reason": _text(doc.get("reason"), LABEL_MAX)}
    for field in ("ref", "party"):
        if doc.get(field):
            fact[field] = _text(doc.get(field), LABEL_MAX)
    if isinstance(doc.get("record"), str) and doc.get("record"):
        fact["record"] = doc["record"]
    return fact


def _target_key(target: Mapping[str, Any]) -> Tuple[str, str]:
    return str(target.get("connection") or ""), str(target.get("account_set") or "")


def merge(docs: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """一批单 → 一张卡片。

    入：``[{doc_id, state?, reason?, ref?, party?, record?, direction?, target?, items?: [...]}]``（``state`` 缺省 ``check``；
    ``target``：这张单写进哪份账套，字段同代录预检的写入目标 ``{connection, connection_title, account_set,
    account_set_title, account_set_path}``）。
    出：``{groups: [{group, items}], unknown, not_writable, waiting, ready, targets, counts}``：
    - ``groups`` 只含有项的组，按 :data:`GROUPS` 排；组内按 未定 → 预选 → 已定、再按名称；
    - 每项 ``{key, kind, subject, group, label, value, value_label, source, state, remember, docs, options?, search?}``；
    - ``ready``：没有未定、预选项的单（只剩已定的，照写）；
    - ``targets``：按（连接，账套）合并，带 ``count``；
    - ``counts``：``{open, proposed, decided}`` 各几项。"""
    if len(docs) > MAX_DOCUMENTS:
        raise PrecheckError("too many documents")
    seen: set = set()
    found: Dict[str, List[Dict[str, Any]]] = {}
    order: List[str] = []
    facts: Dict[str, List[Dict[str, Any]]] = {UNKNOWN: [], NOT_WRITABLE: [], WAITING: []}
    checked: List[str] = []
    targets: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for doc in docs:
        doc_id = _doc_id(doc)
        if doc_id in seen:
            raise PrecheckError(f"duplicate document: {doc_id}")
        seen.add(doc_id)
        state = str(doc.get("state") or CHECK)
        if state not in DOC_STATES:
            raise PrecheckError(f"unknown document state: {state!r}")
        target = doc.get("target")
        if isinstance(target, Mapping) and target.get("account_set"):
            entry = targets.setdefault(_target_key(target), {**{k: v for k, v in target.items() if k != "count"},
                                                             "count": 0})
            entry["count"] += 1
        if state != CHECK:
            facts[state].append(_fact(doc, doc_id))
            continue
        checked.append(doc_id)
        for raw in doc.get("items") or ():
            item = normalize_item(raw, str(doc.get("direction") or ""))
            item["doc_id"] = doc_id
            if item["key"] not in found:
                order.append(item["key"])
            found.setdefault(item["key"], []).append(item)
    items: List[Dict[str, Any]] = []
    pending_docs: set = set()
    for key in order:
        candidates = found[key]
        chosen = dict(_pick(candidates))
        chosen.pop("doc_id", None)
        docs_of = []
        for candidate in candidates:
            if candidate["doc_id"] not in docs_of:
                docs_of.append(candidate["doc_id"])
        chosen["docs"] = docs_of
        chosen.setdefault("label", "")
        if not chosen["label"]:
            chosen["label"] = next((c["label"] for c in candidates if c["label"]), "")
        if "options" not in chosen:
            options = next((c["options"] for c in candidates if c.get("options")), None)
            if options:
                chosen["options"] = options
        if chosen["state"] != DECIDED:
            pending_docs.update(docs_of)
        items.append(chosen)
    groups = []
    for group in GROUPS:
        members = sorted((item for item in items if item["group"] == group),
                         key=lambda item: (STATES.index(item["state"]), item["label"].casefold(), item["key"]))
        if members:
            groups.append({"group": group, "items": members})
    counts = Counter(item["state"] for item in items)
    return {"groups": groups, "unknown": facts[UNKNOWN], "not_writable": facts[NOT_WRITABLE],
            "waiting": facts[WAITING], "ready": [doc_id for doc_id in checked if doc_id not in pending_docs],
            "targets": list(targets.values()), "counts": {state: counts.get(state, 0) for state in STATES}}


def items_of(card: Mapping[str, Any]) -> List[Dict[str, Any]]:
    return [item for group in card.get("groups") or () for item in group.get("items") or ()]


def needs_card(card: Mapping[str, Any]) -> bool:
    """直写要不要出卡片（主会话 11-04 Q1）：有预选、未定的项，或有结果不明的单才出；全部已定就直接写。
    代录的开始卡不看这个，一直出。"""
    return bool(card.get("unknown")) or any(item["state"] != DECIDED for item in items_of(card))


def defaults(card: Mapping[str, Any]) -> List[Dict[str, str]]:
    """照预选全部接受时的答案（卡片初始值、LINE「ยืนยัน」一键确认用）：只含预选项，未定项没有值不在内，已定项不用答。"""
    return [{"key": item["key"], "value": item["value"]} for item in items_of(card) if item["state"] == PROPOSED]


def expand(card: Mapping[str, Any], answers: Iterable[Mapping[str, Any]]
           ) -> Tuple[Dict[str, List[Dict[str, Any]]], List[Dict[str, str]]]:
    """卡片交回的答案 ``[{key, value, doc_id?}]`` → ``({doc_id: [{kind, subject, value, label, remember, document?}]}, errors)``。

    答案可以改已定项（卡片上「已定」可改）。值要在这一项的可选范围里（给了 ``options`` 且没有 ``search`` 时）。
    带 ``doc_id`` 的只展开到那一张（这一类要在 :data:`DOCUMENT_ANSWER_KINDS`、这张单要在这一项里），记成 ``document: True``、
    ``remember: False``：产品只写那一张自己的；来源是 :data:`DOCUMENT` 的项（这张单上人定的）作答同样只写那一张。
    出错的答案逐条回 ``{key, code}``，不展开；其余照展开（调用方决定整批拒还是部分收）。"""
    answers = list(answers or ())
    errors: List[Dict[str, str]] = []
    if len(answers) > MAX_ANSWERS:
        return {}, [{"key": "", "code": ERR_TOO_MANY}]
    by_key = {item["key"]: item for item in items_of(card)}
    seen: set = set()
    result: Dict[str, List[Dict[str, Any]]] = {}
    for answer in answers:
        key = str((answer or {}).get("key") or "") if isinstance(answer, Mapping) else ""
        item = by_key.get(key)
        if item is None:
            errors.append({"key": key, "code": ERR_UNKNOWN_KEY})
            continue
        one = _text(answer.get("doc_id"), 200)
        if one and (item["kind"] not in DOCUMENT_ANSWER_KINDS or one not in item["docs"]):
            errors.append({"key": key, "code": ERR_NOT_PER_DOCUMENT})
            continue
        if (key, one) in seen:
            errors.append({"key": key, "code": ERR_DUPLICATE_KEY})
            continue
        seen.add((key, one))
        value = _text(answer.get("value"), VALUE_MAX)
        if not value:
            errors.append({"key": key, "code": ERR_EMPTY_VALUE})
            continue
        codes = _codes(item.get("options"))
        if codes is not None and not item.get("search") and value not in codes:
            errors.append({"key": key, "code": ERR_NOT_OFFERED})
            continue
        document = bool(one) or item["source"] == DOCUMENT
        for doc_id in ([one] if one else item["docs"]):
            row = {"kind": item["kind"], "subject": item["subject"], "value": value, "label": item["label"],
                   "remember": item["remember"] and not document}
            if document:
                row["document"] = True
            result.setdefault(doc_id, []).append(row)
    return result, errors


def _answered(answers) -> set:
    """答了的 ``(key, doc_id)``；不带 ``doc_id`` 的（整项都答了）记 ``(key, "")``。"""
    return {(str(answer.get("key") or ""), _text(answer.get("doc_id"), 200)) for answer in answers or ()
            if isinstance(answer, Mapping) and str(answer.get("value") or "").strip()}


def held_back(card: Mapping[str, Any], answers: Iterable[Mapping[str, Any]] = ()) -> List[str]:
    """答了这些以后，还因为未定项留下的单（有未定项没答）。预选项没答也照预选写，不留。只答给其中一张的，别的几张照旧留下。"""
    answered = _answered(answers)
    held: List[str] = []
    for item in items_of(card):
        if item["state"] != OPEN or (item["key"], "") in answered:
            continue
        held.extend(doc_id for doc_id in item["docs"] if doc_id not in held and (item["key"], doc_id) not in answered)
    return held


def writable(card: Mapping[str, Any], answers: Iterable[Mapping[str, Any]] = ()) -> List[str]:
    """点「开始」后照写的单：判过的单里去掉留下的（结果不明、写不了、等电脑的本来就不在内）。顺序同卡片里先出现的。"""
    held = set(held_back(card, answers))
    docs: List[str] = list(card.get("ready") or ())
    for item in items_of(card):
        docs.extend(doc_id for doc_id in item["docs"] if doc_id not in docs)
    return [doc_id for doc_id in docs if doc_id not in held]


def preview(card: Mapping[str, Any], limit: int = 4) -> Dict[str, Any]:
    """卡片的摘要（LINE 识别后那张卡上几行用）：前 ``limit`` 个没定下的项（未定在前）``{label, value, value_label, state}``，
    其余合成 ``more``（几项）。"""
    rows = [item for item in items_of(card) if item["state"] != DECIDED]
    rows.sort(key=lambda item: STATES.index(item["state"]))
    shown = [{"label": item["label"], "value": item["value"], "value_label": item["value_label"], "state": item["state"]}
             for item in rows[:max(0, int(limit))]]
    return {"rows": shown, "more": max(0, len(rows) - len(shown))}


__all__ = ["BOOK", "BOOK_SETTING", "CC", "CHECK", "COMPANY_RULE", "DECIDED", "DECIDED_SOURCES", "DOCUMENT", "DOCUMENT_ANSWER_KINDS", "DOC_STATES",
           "ERR_DUPLICATE_KEY", "ERR_EMPTY_VALUE", "ERR_NOT_OFFERED", "ERR_NOT_PER_DOCUMENT", "ERR_TOO_MANY", "ERR_UNKNOWN_KEY",
           "EXPRESS_HISTORY", "FACE", "GROUPS", "HISTORY", "KINDS", "KIND_GROUPS", "MAX_ANSWERS", "MAX_DOCUMENTS",
           "NOT_WRITABLE", "OPEN", "PAYMENT", "PER_DOCUMENT_KINDS", "PINNED", "PROPOSED", "PROPOSED_SOURCES",
           "PURCHASE", "PrecheckError", "REMEMBERED", "RULE", "SALES", "SERVICE", "SMALL_LIST", "SOURCES", "STATES", "STOCK",
           "STOCK_ACCOUNT_PREFIX", "SUGGESTED", "TASK_DEFAULT", "UNKNOWN", "WAITING", "defaults", "expand", "group_of",
           "held_back", "item_key", "items_of", "merge", "needs_card", "normalize_item", "preview", "state_of",
           "writable"]
