"""LINE 识别完：一张票要人定的题全部问完才出卡（用户 11-19「所有问题问完 … 然后保存编辑或者取消，一气呵成」；评估
docs/work/2026-11-19-line-ask-all-before-card-评估.md 第 1、3、4、7 节）。

一张票依次：

1. 每行物料与仓库（`line_items`：全部新建 / 全部只记账 / 逐项、选已有物料打字找、多仓选仓——原来随卡片追问，现在在卡片之前）；
2. 写账前置判定剩下要人定的项（共用出题器 `pearnly_core.write_precheck.line_ask`）：判定是 `express.precheck` 认的
   ``I:<收单记录>``（`express.operator.judged_intake`），与 CC 开始前卡片同一份题、同一套记答（按公司记住）——接 Express 的公司
   是 Express 要人选的项，没接的只有记录自己的（物料、仓库，已在第 1 步定完时不再问）；
3. 出卡（`line_cards.preview`：卡上的仓库、付款、每行标签是答过的值），卡上「保存 / 编辑 / 取消」。

没有题的票直接出卡。一批几张逐张问完、逐张出卡；答题中又来的新票排在后面（会话里的 :data:`WAITING`），一次只问一张，
答案只记给在问的那一张，不串单。

- 任何一题上的「取消」：这张照卡片「取消」的口径作废（`intake.cancel_card`），回「已取消」，接着问排着的下一张。
- 会话（`line_flow` 的 LINE 会话，30 分钟）过期：不出卡；之后点旧按钮回「此选项已失效」。
- 出题出错不挡卡：记日志，这张照出卡；卡片「保存」再按同一份判定兜底，题没答完不提交（`intake.save_card`）。

一次回复最多 5 条（LINE），多的用推送（:func:`send`）。
"""
import json

import frappe
from frappe import _

import copy

from pearnly_core.write_precheck import line_ask

from pearnly_erp_portal.integrations import intake, line_cards, line_flow, line_items, line_out

WAITING = "intake_wait"   # 会话里排着、还没开始问的票（收单记录名）
# 会话里这几项是识别后的提问：答题中又点菜单重选「ซื้อ / ขาย」、取消重选时不丢（`line_flow.start` / `cancel`）
KEPT = (line_items.ASKING, line_ask.KEY, WAITING)
# 出题器按钮的 postback 开头（= ``line_ask.ACTION``；字面量：产品地图按它认入口 line:erp:postback:precheck）
PREFIX = "a=precheck&"
STALE = "This option is no longer current"


def is_step(data) -> bool:
    return str(data or "").startswith(PREFIX) and line_ask.is_step(data)


def send(line_user_id, messages, reply_token=""):
    """一次回复最多 5 条：第一批用回复令牌，其余推送（`line_out`：末尾不是题时重带在问的那道题）。"""
    if messages:
        line_out.reply_or_push(line_user_id, messages, reply_token)


def _text(text):
    return {"type": "text", "text": text}


# ── 一张票 ───────────────────────────────────────────────────────────────────


def _doc(name):
    return frappe.get_doc(intake.DOCTYPE, name)


def _ready(name):
    """还在等出卡的这张（识别完、没提交没取消、是本人的）；别的回 None。"""
    if not name or not frappe.db.exists(intake.DOCTYPE, name):
        return None
    doc = _doc(name)
    return doc if doc.status == intake.READY and doc.sent_by == frappe.session.user else None


def _card(doc):
    doc.reload()
    result = json.loads(doc.result or "{}")
    return line_cards.preview(doc, result, intake.totals(doc, result))


def _cancel(name) -> list:
    """「取消」：这张作废（同卡片「取消」）。已入账、已不可用的不动。"""
    try:
        intake.cancel_card(name)
    except (frappe.ValidationError, frappe.PermissionError):
        frappe.db.rollback()
        frappe.clear_messages()
    return [_text(_("Cancelled"))]


def _busy(payload) -> bool:
    """此刻有一张在问（物料 / 仓库那几步，或写前判定的题）。"""
    return bool((payload.get(line_items.ASKING) or {}).get("intake")) or line_ask.asking(payload)


def _settle(payload, outcome) -> list:
    """出题器的结果 → 消息：问完的出卡、取消的作废，再接它给的下一题 / 重问 / 旧按钮提示。"""
    messages = []
    for row in outcome.get("finished") or ():
        doc = _ready(row.get("ref"))  # loop-query: 一次只问一张，出题器每次最多回一张问完的
        if doc is not None:
            messages.append(_card(doc))  # loop-query: 同上，最多一张
    for row in outcome.get("cancelled") or ():
        messages += _cancel(row.get("ref"))  # loop-query: 一次只问一张，最多取消一张
    return messages + list(outcome.get("messages") or ())


def _precheck(payload, doc) -> list:
    """物料与仓库定完的这张：交共用出题器问写账前置判定剩下的项；没有题回这张的卡。"""
    from pearnly_erp_portal.express import precheck

    frappe.db.commit()  # 判定只读、判完可能整笔回滚：先把这张刚定的行与仓落库
    try:
        outcome = line_ask.enqueue(payload, doc.company, [f"{precheck.INTAKE_PREFIX}:{doc.name}"], ref=doc.name,
                                   heading=line_items.heading(doc))
    except Exception:
        frappe.db.rollback()
        frappe.clear_messages()
        frappe.log_error(title="ERP LINE precheck question failed", message=frappe.get_traceback()[-1500:])
        payload.pop(line_ask.KEY, None)
        return [_card(doc)]
    return _settle(payload, outcome)


def _again(payload) -> list:
    """此刻在问的那道题原样再出一次（带快捷回复）：LINE 只在最后一条消息上挂快捷回复，回了「排队中」或别的话，原来那道题的按钮
    就看不见了（11-19 真机）。物料 / 仓库那几步按这一张、这一步重出（按钮照样认）；写前判定的题用共用出题器现有的出题函数重出
    （这一题的按钮换新的一组，之前那组作废）。"""
    asking = payload.get(line_items.ASKING) or {}
    if asking.get("intake"):
        doc = _ready(asking["intake"])
        question = line_items.current(doc, asking) if doc is not None else None
        return [question] if question else []
    batch = (payload.get(line_ask.KEY) or {}).get("current")
    if batch and batch.get("question"):
        return [line_ask._render(batch, line_ask._default())]
    return []


def open_question(line_user_id):
    """这个 LINE 用户此刻在等答的那道题（`line_out` 在别的消息末尾重带用）；没有回 None。只读：不改会话、不作废已发的按钮——
    物料 / 仓库题按这一张、这一步重出；写前判定的题在会话副本上按同一个序号重出（按钮与已发的那组一样，认同一题），正在等填数的重出
    填数提示，正在显示搜索结果的重出那组结果。"""
    payload = line_flow.choices(line_user_id)
    asking = payload.get(line_items.ASKING) or {}
    if asking.get("intake"):
        name = asking["intake"]
        if not frappe.db.exists(intake.DOCTYPE, name):
            return None
        doc = _doc(name)
        return line_items.current(doc, dict(asking)) if doc.status == intake.READY else None
    batch = copy.deepcopy((payload.get(line_ask.KEY) or {}).get("current"))
    if not batch or not batch.get("question"):
        return None
    backend = line_ask._default()
    batch["seq"] = int(batch.get("seq") or 0) - 1  # 下面的出题函数会加一：回到会话里的这一题的序号
    entry = batch.get("entry")
    if entry:
        return line_ask._entry_prompt(batch, {"code": entry["code"], "label": entry["label"], "entry": entry["entry"]}, backend)
    shown, codes = list(batch.get("shown") or ()), [option["code"] for option in batch["question"]["options"]]
    if shown and shown != codes:  # 正显示打字搜到的结果（标签是「编码 名称」）
        rows = [{"code": code, "name": label[len(code):].strip() if label.startswith(code) else label}
                for code, label in zip(shown, batch.get("shown_labels") or ())]
        return line_ask._results(batch, rows, False, backend)
    return line_ask._render(batch, backend)


def _drive(payload, messages=()) -> list:
    """没有在问的：排着的下一张开始（先物料与仓库，再写前判定，没有题就出卡），直到有一张在问或排空。"""
    messages = list(messages)
    while not _busy(payload) and payload.get(WAITING):
        doc = _ready(payload[WAITING].pop(0))  # loop-query: 一个文件里的几张票逐张开始，遇到要问的就停
        if doc is None:
            continue
        line_items.adopt(doc, payload)  # loop-query: 同上；这一批刚在 LINE 里选过的仓沿用
        question = line_items.start(doc, payload)  # loop-query: 同上
        if question:
            messages.append(question)
            break
        messages += _precheck(payload, doc)  # loop-query: 同上，没有题的票逐张出卡
    if not payload.get(WAITING):
        payload.pop(WAITING, None)
    return messages


# ── 入口 ─────────────────────────────────────────────────────────────────────


def begin(line_user_id, names):
    """识别完的几张票（`intake.run`）：回这次要发的消息（卡 / 第一题；正在问别的就排在后面、回「排队中」）。
    会话已过期或已换成别的流程（收款）回 None：调用方照旧直接出卡（卡片「保存」照判定兜底）。"""
    payload = line_flow.choices(line_user_id)
    if not payload:
        return None
    waiting = list(payload.get(WAITING) or [])
    payload[WAITING] = waiting + [name for name in names if name not in waiting]
    messages = _drive(payload)
    if not messages and _busy(payload):
        # 正在答别的票：这张排在后面，回一句「排队中」（现有词条），后面紧跟在问的那道题（快捷回复只挂在最后一条上）
        messages = [_text(_("Queued")), *_again(payload)]
    line_flow.keep(line_user_id, payload)
    return messages


def after_items(line_user_id, payload, reply_token, doc=None, cancelled=None):
    """`line_items` 问完一张（``doc``）：接着问写前判定，问完出卡；在物料 / 仓库那几步点了「取消」（``cancelled``）：这张作废。
    然后接排着的下一张。"""
    if doc is not None:
        messages = _precheck(payload, doc)
    else:
        messages = _cancel(cancelled.name) if cancelled is not None else [_text(_("Cancelled"))]
    messages = _drive(payload, messages)
    line_flow.keep(line_user_id, payload)
    send(line_user_id, messages, reply_token)


def _after(line_user_id, payload, outcome, reply_token):
    messages = _drive(payload, _settle(payload, outcome))
    line_flow.keep(line_user_id, payload)
    send(line_user_id, messages, reply_token)


def answer(line_user_id, user, data, reply_token):
    """出题器的按钮点回来（``a=precheck``）：只认在问的这一张、这一题（出题器判）；会话过期了回「此选项已失效」，不出卡。"""
    payload = line_flow.choices(line_user_id)
    if not payload:
        return send(line_user_id, [_text(_(STALE))], reply_token)
    _after(line_user_id, payload, line_ask.postback(payload, data, user=user), reply_token)


def text(line_user_id, user, text, reply_token) -> bool:
    """有题在等答时收到的文字。写前判定的题：打编号选、搜索题打名称或编码找，别的回没找到并重问（出题器判）；物料 / 仓库那几步
    （打名称找物料、找仓库已由 `line_items.search` 先认）：打的字与答题无关，重发这道题（不回「请点击下方菜单开始」，11-19 真机）。
    没题在等答、正在重选上传前的几步、打的是「ซื้อ / ขาย」这类开始词时返回 False，交给别的流程（菜单词在调用方之前已认）。"""
    payload = line_flow.choices(line_user_id)
    wanted = str(text or "").strip()
    if not _busy(payload) or not wanted:
        return False
    if payload.get("direction") and not line_flow.ready(payload):
        return False  # 答题中又点菜单重选上传前的几步：打的字是给那几步的
    if not wanted.isdigit() and wanted.casefold() in line_flow.DIRECTION_WORDS:
        return False
    if not line_ask.asking(payload):
        messages = _again(payload)
        if not messages:
            return False
        line_flow.keep(line_user_id, payload)
        send(line_user_id, messages, reply_token)
        return True
    outcome = line_ask.text(payload, wanted, user=user)
    if outcome is None:
        return False
    _after(line_user_id, payload, outcome, reply_token)
    return True


__all__ = ["KEPT", "PREFIX", "WAITING", "after_items", "answer", "begin", "is_step", "open_question", "send", "text"]
