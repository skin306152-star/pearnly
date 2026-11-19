"""LINE 里问写账前置判定的题（多产品共用：ERP、Cowork 各自接；评估 ``docs/work/2026-11-19-line-ask-all-before-card-评估.md`` 第 3、10 节）。

识别完一张票，出卡之前把这张票此刻要人定的项在 LINE 里一题一题问完。题目、选项、预选、推荐、四语文字与 CC 引导式同一份
（``write_precheck.api.card`` → ``guide.build``），答案经 ``api.record(guided=True)`` 按公司记住（同 CC 卡片）；这里只做两件事：
把一题渲染成 LINE 消息、把点选 / 打字推进到下一题。卡、回复、会话的存取都在产品。

- **题型**：选项 ≤ 12 个：文字题 + 快捷回复；13–20 个：正文编号列表 + 前 12 个快捷回复，也认打编号；搜索类（``search``：对方、存货、
  科目）：预选值作第一个按钮 + 提示打名称或编码 → 打字走 ``search``（服务端分页）→ 编号结果 + 快捷回复。
- **推荐**：推荐项排第一个按钮，只是推荐，点了才算；「做不做」类不预选——都照 ``guide`` 现有的分档，这里不另判。
- **按钮**：标签 ≤ 20 字（长名截短、全名写在正文编号列表里），快捷回复最多 13 个（含「取消」）。postback 带动作、这一批的标识、
  当前题的序号与键、候选值的序号（不带值本身）；不是当前这一批、这一题的旧按钮点了无效。
- **一批**：一张票（产品判定认的单号，一张票可以有几个单号）问完回 ``finished``，产品接着出卡；答题中又来新票，新票排在后面
  （``queued``），答案只记给当前这一批的单号，不串到别的票上。取消只停当前这一批（回 ``cancelled``），接着问排着的下一批。
- **会话**：存在产品给的 LINE 会话载荷 ``payload[KEY]`` 里（每次调用后由产品存回；会话 30 分钟过期同现有，过期后旧按钮自然无效）。
  键与 ERP ``line_items`` / Cowork ``line.items`` 的 ``items`` 不同。

要另填数的题也在 LINE 问完（主会话 11-19「不许有 LINE 问不了、只能进编辑页」）：点了带 ``entry`` 的选项（单位「填换算」、出库成本
「填单位成本」）接着请用户直接打数字，按识别数字那套规整（``tax_documents.amounts.money``：逗号、฿、括号负数），照 ``entry`` 的下限核
（同 CC 引导卡 ``assistant.guide.documents._entry_ok``），读不懂 / 不在范围当场再问；答案交「码:数」（同 CC 引导卡与
``express_rules.push.book_questions`` 的 ``factor:`` / ``given:``）。可自己填的题（``free``）打的字原样作答（同 CC 引导卡「其他」）。
只有既没选项、不能搜、也不能自己填（或 ``entry`` 不是数字）的题才问不了，记进 ``left``。结果不明、写不了、等电脑的单不是题，也不问。

每个对外函数回一个结果 ``{status, messages, finished, cancelled}``：产品先按 ``finished`` 逐张出卡、处理 ``cancelled``，再发 ``messages``
（下一题、重问、旧按钮无效的提示）。``status``：``asking``（有题在等答）/ ``idle``（没有在问的）/ ``queued``（新票排在后面）/
``stale``（不是当前的按钮）。

本模块不导入 Frappe：取卡、记答、搜索、权限、翻译经 ``backend``（缺省 :mod:`line_backend`，测试换替身）。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import parse_qs, urlencode

from pearnly_core.line_ui.labels import QUICK_LABEL_MAX, choice_labels
from pearnly_core.tax_documents.amounts import money
from pearnly_core.write_precheck import contracts as c
from pearnly_core.write_precheck import guide

KEY = "precheck_ask"             # 产品 LINE 会话载荷里的键
ACTION = "precheck"              # postback ``a=``
MAX_CHOICES = 12                 # 快捷回复最多 13 个：选项 12 个 + 「取消」
LIST_MAX = c.SMALL_LIST          # 正文编号列表最多列几个（超过的题 guide 不给选项、只给搜索）
DATA_MAX = 300                   # LINE postback data 上限（字节）
TEXT_MAX = 100                   # 打字搜索最多取几个字（同 ``api.search``）

ASKING, IDLE, QUEUED, STALE = "asking", "idle", "queued", "stale"

# 文字（键 / 英文原文 msgid，按本轮语言翻）
SEARCH_PROMPT = "guide.line.search"
NUMBER_PROMPT = "guide.line.number"
NUMBER_ABOVE = "guide.line.numberAbove"
OTHER_PROMPT = "guide.line.other"
STALE_TEXT = "This option is no longer current"
NO_MATCH = "No match for “{0}”"
TOO_MANY = "Too many matches; type a few more letters to narrow it down"
CANCEL = "Cancel"


# ── 结果 ─────────────────────────────────────────────────────────────────────


def _outcome(state: Dict[str, Any], messages=(), finished=(), cancelled=(), status: str = "") -> Dict[str, Any]:
    return {"status": status or (ASKING if state.get("current") else IDLE), "messages": list(messages),
            "finished": list(finished), "cancelled": list(cancelled)}


def _summary(batch: Mapping[str, Any]) -> Dict[str, Any]:
    """交回产品的一批：``{id, ref, company, doc_ids, answers, left}``；``answers`` 每项 ``{key, kind, title, label, value, value_label}``
    （卡上显示答过的结果用）。"""
    return {"id": batch["id"], "ref": batch.get("ref"), "company": batch["company"], "doc_ids": list(batch["doc_ids"]),
            "answers": list(batch.get("answers") or ()), "left": list(batch.get("left") or ())}


# ── 会话 ─────────────────────────────────────────────────────────────────────


def _state(payload: Dict[str, Any]) -> Dict[str, Any]:
    state = payload.get(KEY)
    if not isinstance(state, dict):
        state = payload[KEY] = {"current": None, "queue": []}
    state.setdefault("current", None)
    state.setdefault("queue", [])
    return state


def asking(payload: Mapping[str, Any]) -> bool:
    """这个 LINE 用户此刻有没有题在等答（产品据此决定打字交给 :func:`text` 还是走自己的流程）。"""
    state = (payload or {}).get(KEY)
    return bool(isinstance(state, dict) and state.get("current"))


def pending(payload: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """在问的这一批与排着的几批（``{id, ref, company, doc_ids}``），产品要整个放弃时用。"""
    state = (payload or {}).get(KEY)
    if not isinstance(state, dict):
        return []
    rows = ([state["current"]] if state.get("current") else []) + list(state.get("queue") or ())
    return [_summary(row) for row in rows]


def stop(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """放弃全部（在问的与排着的），回被放弃的几批；之后旧按钮都无效。"""
    dropped = pending(payload)
    payload.pop(KEY, None)
    return dropped


def is_step(data) -> bool:
    return (parse_qs(data or "").get("a") or [""])[0] == ACTION


# ── 出题 ─────────────────────────────────────────────────────────────────────


def _say(backend, key: str, params: Optional[Mapping[str, Any]] = None) -> str:
    """一个词条按本轮语言；``{label}`` / ``{count}`` 换成参数。没有译文（翻译原样回键）回空。"""
    found = str(backend.text(key) or "")
    if found == key:
        return ""
    for name, value in (params or {}).items():
        found = found.replace("{" + name + "}", str(value))
    return found


def askable(question: Mapping[str, Any], item: Optional[Mapping[str, Any]] = None) -> bool:
    """LINE 里能问的题：有选项、能搜或能自己填；要另填的数（``entry``，选项上或判定卡同一项的选项上）只认数字。"""
    if any(entry.get("input", "number") != "number" for entry in _entries(question, item or {}).values()):
        return False
    return bool(question.get("options") or question.get("search") or question.get("free"))


def number(entry: Mapping[str, Any], typed) -> Optional[str]:
    """用户打的数 → 交答案用的数字串（``1,234.50`` → ``1234.50``、``฿100`` → ``100``）；读不懂、不是有限数、不合 ``entry`` 的下限
    （``min_exclusive`` 大于、``min`` 不小于，同 CC 引导卡）回 None。"""
    amount = money(" ".join(str(typed or "").split()))
    if amount is None or not amount.is_finite():
        return None
    if entry.get("min_exclusive") not in (None, "") and not amount > Decimal(str(entry["min_exclusive"])):
        return None
    if entry.get("min") not in (None, "") and amount < Decimal(str(entry["min"])):
        return None
    return format(amount, "f")


def _entries(question: Mapping[str, Any], item: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    """选项码 → 要另填的数（``entry``）。``guide.build`` 出题时不带选项上的 ``entry``，按判定卡上同一项的选项补回。"""
    found = {}
    for option in [*(item.get("options") or ()), *(question.get("options") or ())]:
        if isinstance(option, Mapping) and isinstance(option.get("entry"), Mapping):
            found[str(option.get("code") or "")] = dict(option["entry"])
    return found


def _snapshot(question: Mapping[str, Any], item: Mapping[str, Any], backend) -> Dict[str, Any]:
    """一题存进会话的样子（按本轮语言翻好）：``{key, kind, title, label, docs, search, options: [{code, label}]}``。
    推荐项照 guide 排第一；搜索题的预选值不在选项里时补成第一个（名称取判定给的 ``value_label``）。"""
    params = question.get("title_params") or {}
    entries = _entries(question, item)
    options = [{"code": str(option.get("code") or ""),
                "label": str(option.get("name") or _say(backend, option.get("label_key") or "") or option.get("code") or ""),
                **({"entry": entries[option.get("code")]} if option.get("code") in entries else {})}
               for option in question.get("options") or ()]
    preselected = str(question.get("preselected") or "")
    if preselected and question.get("search") and preselected not in {option["code"] for option in options}:
        options.insert(0, {"code": preselected, "label": str(item.get("value_label") or preselected)})
    title = _say(backend, question.get("title_key") or "", params) or str(params.get("label") or "")
    label = str(params.get("label") or "")
    return {"key": question["key"], "kind": question.get("kind") or "", "title": title,
            "label": "" if label == title else label, "docs": list(question.get("docs") or ()),
            "search": str(question.get("search") or ""), "free": bool(question.get("free")), "options": options[:LIST_MAX]}


def _next(card: Mapping[str, Any], batch: Dict[str, Any], backend) -> Optional[Dict[str, Any]]:
    """这批单此刻还要问的第一题（判定重算后的卡 ``card``）：跳过这一批答过的、问不了的（记进 ``left``）、条件不成立的（``when``）。"""
    built = guide.build(card)
    items = {item["key"]: item for item in c.items_of(card)}
    answered = [{"key": row["key"], "value": row["value"]} for row in batch.get("answers") or ()]
    done = {row["key"] for row in answered}
    for question in built["questions"]:
        if question["key"] in done or question["key"] in batch["left"]:
            continue
        if not c.applies(question, items, answered):
            continue
        if not askable(question, items.get(question["key"]) or {}):
            batch["left"].append(question["key"])
            continue
        return _snapshot(question, items.get(question["key"]) or {}, backend)
    return None


def _data(batch: Mapping[str, Any], **params) -> str:
    data = urlencode({"a": "precheck", "b": batch["id"], "n": batch["seq"], **params})  # = ACTION（字面量）
    if len(data.encode("utf-8")) > DATA_MAX:
        raise ValueError("postback data too long")
    return data


def _button(label: str, data: str) -> Dict[str, Any]:
    label = str(label)[:QUICK_LABEL_MAX]
    return {"type": "action", "action": {"type": "postback", "label": label, "displayText": label, "data": data}}


def _message(batch: Mapping[str, Any], lines: List[str], choices: List[Dict[str, str]], backend) -> Dict[str, Any]:
    """一条文字题：正文 + 快捷回复（前 12 个选项 + 取消）。名称放不下按钮（超过 20 字）或选项超过 12 个时正文列编号全名、按钮用编号短名。"""
    labels = [choice["label"] for choice in choices]
    numbered = len(choices) > MAX_CHOICES or any(len(label) > QUICK_LABEL_MAX for label in labels)
    if numbered:
        lines = lines + [f"{index}. {label}" for index, label in enumerate(labels, 1)]
        shown = choice_labels(labels[:MAX_CHOICES])
    else:
        shown = labels
    batch["numbered"] = numbered
    items = [_button(text, _data(batch, q=batch["question"]["key"], k=index)) for index, text in enumerate(shown)]
    items.append(_button(backend.text(CANCEL), _data(batch, c=1)))
    return {"type": "text", "text": "\n".join(line for line in lines if line)[:5000], "quickReply": {"items": items}}


def _render(batch: Dict[str, Any], backend) -> Dict[str, Any]:
    """当前这一题的消息（每出一次题序号加一，之前的按钮作废）。"""
    batch["seq"] = int(batch.get("seq") or 0) + 1
    batch.pop("entry", None)
    question = batch["question"]
    batch["shown"] = [option["code"] for option in question["options"]]
    batch["shown_labels"] = [option["label"] for option in question["options"]]
    lines = [batch.get("heading") or "", question["title"], question["label"]]
    if question["search"]:
        lines.append(_say(backend, SEARCH_PROMPT))
    elif question.get("free"):
        lines.append(_say(backend, OTHER_PROMPT))
    return _message(batch, lines, question["options"], backend)


def _entry_prompt(batch: Dict[str, Any], option: Mapping[str, Any], backend) -> Dict[str, Any]:
    """点了要另填数的选项：请用户直接打数字（有下限的写上下限）。这时只认打的数与「取消」，之前的按钮作废。"""
    batch["seq"] = int(batch.get("seq") or 0) + 1
    batch["entry"] = {"code": option["code"], "label": option["label"], "entry": dict(option["entry"])}
    batch["shown"], batch["shown_labels"], batch["numbered"] = [], [], False
    question, entry = batch["question"], option["entry"]
    above = entry.get("min_exclusive")
    prompt = _say(backend, NUMBER_ABOVE).replace("{0}", str(above)) if above not in (None, "") else ""
    lines = [batch.get("heading") or "", question["title"], question["label"], option["label"],
             prompt or _say(backend, NUMBER_PROMPT)]
    return _message(batch, lines, [], backend)


def _results(batch: Dict[str, Any], rows: List[Dict[str, str]], more: bool, backend) -> Dict[str, Any]:
    """打字搜到的结果：编号「编码 名称」+ 快捷回复；还有更多时加一句缩小范围。"""
    batch["seq"] = int(batch.get("seq") or 0) + 1
    choices = [{"code": row["code"], "label": " ".join(part for part in (row["code"], row.get("name")) if part)} for row in rows]
    batch["shown"] = [choice["code"] for choice in choices]
    batch["shown_labels"] = [choice["label"] for choice in choices]
    question = batch["question"]
    lines = [batch.get("heading") or "", question["title"], question["label"]]
    lines += [f"{index}. {choice['label']}" for index, choice in enumerate(choices, 1)]
    if more:
        lines.append(backend.text(TOO_MANY))
    return _message(batch, lines, [{"code": choice["code"], "label": str(index)} for index, choice in enumerate(choices, 1)],
                    backend)


# ── 一批的开始与结束 ──────────────────────────────────────────────────────────


def _batch(company, doc_ids, ref, heading, backend) -> Dict[str, Any]:
    ids = list(dict.fromkeys(str(doc_id) for doc_id in doc_ids or () if doc_id))
    if not ids or len(ids) > 200:
        raise ValueError("doc_ids")
    return {"id": backend.token(), "company": str(company or ""), "doc_ids": ids, "ref": ref, "heading": str(heading or ""),
            "answers": [], "left": [], "seq": 0}


def _first(batch: Dict[str, Any], user, backend) -> Optional[Dict[str, Any]]:
    """这一批此刻要问的第一题（没有回 None）。不能对这家公司写账的人不问。"""
    if not backend.allowed(user, batch["company"]):
        return None
    return _next(backend.card(batch["company"], batch["doc_ids"]), batch, backend)


def _advance(state: Dict[str, Any], user, backend, finished: List, cancelled: List, messages: List) -> Dict[str, Any]:
    """当前这一批结束后：排着的下一批逐个开始，有题的出第一题，没题的直接算问完。"""
    state["current"] = None
    while state["queue"] and not state["current"]:
        batch = state["queue"].pop(0)
        question = _first(batch, user, backend)
        if question is None:
            finished.append(_summary(batch))
            continue
        batch["question"] = question
        state["current"] = batch
        messages.append(_render(batch, backend))
    return _outcome(state, messages, finished, cancelled)


def enqueue(payload: Dict[str, Any], company: str, doc_ids, ref: Any = None, heading: str = "", user=None,
            backend=None) -> Dict[str, Any]:
    """识别完一张票交进来：``doc_ids`` 是产品判定认的单号，``ref`` 原样带回（产品认是哪张票），``heading`` 每题开头那行（对方 · 单号）。

    没题：回 ``finished``（这一张直接出卡）；没在问别的：回第一题（``asking``）；正在问别的：排在后面（``queued``），轮到时再按那时的
    判定出题（前一张记住的答案可能让它不用再问）。"""
    backend = backend or _default()
    user = user or backend.user()
    state = _state(payload)
    batch = _batch(company, doc_ids, ref, heading, backend)
    question = _first(batch, user, backend)
    if question is None:
        return _outcome(state, finished=[_summary(batch)])
    if state.get("current"):
        state["queue"].append(batch)
        return _outcome(state, status=QUEUED)
    batch["question"] = question
    state["current"] = batch
    return _outcome(state, [_render(batch, backend)])


# ── 作答 ─────────────────────────────────────────────────────────────────────


def _choose(state: Dict[str, Any], index: int, user, backend) -> Dict[str, Any]:
    """选中当前显示的第 ``index`` 个（从 0 起）：要另填数的选项先请用户打数字，其余直接作答。"""
    batch = state["current"]
    code, label = batch["shown"][index], batch["shown_labels"][index]
    option = next((option for option in batch["question"]["options"] if option["code"] == code), None)
    if option is not None and option.get("entry"):
        return _outcome(state, [_entry_prompt(batch, option, backend)])
    return _answer(state, code, label, user, backend)


def _answer(state: Dict[str, Any], code: str, label: str, user, backend) -> Dict[str, Any]:
    """交 ``record``（``code`` 是选项码、「码:数」或自己填的字）；答错回原因并重问这一题；答对接下一题，这批问完接下一批。"""
    batch = state["current"]
    question = batch["question"]
    found = backend.record(batch["company"], batch["doc_ids"], [{"key": question["key"], "value": code}])
    if found.get("refused") or found.get("errors"):
        reason = str(found.get("refused") or "") or backend.text(STALE_TEXT)
        return _outcome(state, [{"type": "text", "text": reason}, _render(batch, backend)])
    batch["answers"].append({"key": question["key"], "kind": question["kind"], "title": question["title"],
                             "label": question["label"], "value": code, "value_label": label})
    following = _next(found, batch, backend)
    if following is not None:
        batch["question"] = following
        return _outcome(state, [_render(batch, backend)])
    return _advance(state, user, backend, [_summary(batch)], [], [])


def _current(state: Dict[str, Any], params: Mapping[str, str], user, backend) -> Optional[Dict[str, Any]]:
    batch = state.get("current")
    if not batch or params.get("b") != batch["id"] or not backend.allowed(user, batch["company"]):
        return None
    return batch


def postback(payload: Dict[str, Any], data: str, user=None, backend=None) -> Dict[str, Any]:
    """快捷回复点回来：只认当前这一批（``b``）的当前这一题（``n`` 序号、``q`` 键）；「取消」认这一批即可。"""
    backend = backend or _default()
    user = user or backend.user()
    state = _state(payload)
    params = {key: values[0] for key, values in parse_qs(data or "").items()}
    batch = _current(state, params, user, backend)
    stale = _outcome(state, [{"type": "text", "text": backend.text(STALE_TEXT)}], status=STALE)
    if batch is None or params.get("a") != ACTION:
        return stale
    if params.get("c"):
        return _advance(state, user, backend, [], [_summary(batch)], [])
    if params.get("n") != str(batch.get("seq")) or params.get("q") != batch["question"]["key"]:
        return stale
    try:
        index = int(params.get("k", ""))
    except ValueError:
        return stale
    if not 0 <= index < len(batch.get("shown") or ()):
        return stale
    return _choose(state, index, user, backend)


def text(payload: Dict[str, Any], message: str, user=None, backend=None) -> Optional[Dict[str, Any]]:
    """答题中收到的文字：打编号 = 选正文列出的那一个；搜索题打名称或编码 = 去搜（服务端分页）；别的回没找到并重问。
    没有题在等答回 None（产品照自己的流程处理）。"""
    if not asking(payload):
        return None
    backend = backend or _default()
    user = user or backend.user()
    state = _state(payload)
    batch = state["current"]
    if not backend.allowed(user, batch["company"]):
        return None
    wanted = " ".join(str(message or "").split())[:TEXT_MAX]
    question = batch["question"]
    entry = batch.get("entry")
    if entry:
        found = number(entry["entry"], wanted)
        if found is None:  # 读不懂 / 不在范围：当场再问这个数
            option = {"code": entry["code"], "label": entry["label"], "entry": entry["entry"]}
            return _outcome(state, [_entry_prompt(batch, option, backend)])
        return _answer(state, f"{entry['code']}:{found}", f"{entry['label']} {found}", user, backend)
    shown = batch.get("shown") or []
    if wanted.isdigit() and 1 <= int(wanted) <= len(shown) and (batch.get("numbered") or not question.get("free")):
        return _choose(state, int(wanted) - 1, user, backend)
    if question.get("free") and wanted:  # 可自己填的题：打的字原样作答（同 CC 引导卡「其他」）
        return _answer(state, wanted[:c.VALUE_MAX], wanted[:c.VALUE_MAX], user, backend)
    if question["search"] and wanted:
        doc_id = (question["docs"] or batch["doc_ids"])[0]
        found = backend.search(batch["company"], doc_id, question["search"], wanted, 0, MAX_CHOICES)
        rows = [row for row in found.get("options") or () if row.get("code")][:MAX_CHOICES]
        if rows:
            return _outcome(state, [_results(batch, rows, bool(found.get("more")), backend)])
    miss = {"type": "text", "text": backend.text(NO_MATCH).replace("{0}", wanted[:40])}
    return _outcome(state, [miss, _render(batch, backend)])


def _default():
    from pearnly_core.write_precheck import line_backend

    return line_backend.BACKEND


__all__ = ["ACTION", "ASKING", "DATA_MAX", "IDLE", "KEY", "MAX_CHOICES", "QUEUED", "STALE", "askable", "asking", "enqueue", "is_step",
           "number", "pending", "postback", "stop", "text"]
