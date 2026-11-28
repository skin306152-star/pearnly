"""LINE 识别后按行追问「这一行对哪件物料、动不动库存、放哪」（11-06 按行选物料与库存，评估
docs/work/2026-11-06-erp-line-item-stock-choice-评估.md 6.1）。

上传前不再整批问「服务 / 库存商品」和仓库。识别完每一行按 `products.line_memory` 预选（人记过的 → 认出的物料 → 识别给的类型），
预选结果存在这张票上（``ERP LINE Intake.line_choices``，不随会话过期），卡片每行标「入库 / 出库 / 只记账 / 新建 · 入库」。
只有定不了的才问，在卡片之前问（用户 11-19「所有问题问完 … 一气呵成」，`line_questions`）：

  <对方> · <单号> / 新物料：2. … 5. …   [全部新建并入库] [全部只记账] [逐项选择] [取消]

「逐项选择」一项一问 [新建并入库] [只记账] [选已有物料] [取消]，「选已有物料」打名称或编码找。有入库行、单头仓定不下来才问仓库
（一张票问一次，同一批后面几张沿用；11-19 不再问仓位）。这几步问完交 `line_questions` 接着问写账前置判定剩下的项，全部问完才出卡。
一批几张票一次只问一张（排队在 `line_questions`）。取消 = 这张作废（同卡片「取消」），接着问下一张。

追问到哪张哪一步存在本人的 LINE 会话里（与上传前的选择同一个会话，键 ``ASKING``）；按钮只认当前这一张、这一步，旧按钮点了无效。
权限：只有发这张票的本人能答（会话按 LINE 用户分、记录再核 ``sent_by``），找物料 / 仓库只在这家公司可用的档里找。
"""
import json
from urllib.parse import parse_qs, urlencode

import frappe
from frappe import _

from pearnly_core import line

from pearnly_erp_portal.integrations import line_flow, line_out
from pearnly_erp_portal.products import line_memory, line_rules

ACTION = "erp_item"
ASKING = "items"
MAX_CHOICES = line_flow.MAX_CHOICES
STALE = "This option is no longer current"


def is_step(data) -> bool:
    return (parse_qs(data or "").get("a") or [""])[0] == ACTION


def _button(label, **params):
    label = str(label)[:20]
    return {"type": "action", "action": {"type": "postback", "label": label, "displayText": label,
                                         "data": urlencode({"a": "erp_item", **params})}}  # = ACTION（字面量：产品地图按它认入口）


def _message(text, items, intake):
    return {"type": "text", "text": text,
            "quickReply": {"items": items[:MAX_CHOICES] + [_button(_("Cancel"), v="cancel", n=intake)]}}


def _text(text):
    return {"type": "text", "text": text}


def _reply(reply_token, messages):
    line_out.reply(reply_token, messages if isinstance(messages, list) else [messages])


# ── 这张票的选择 ──────────────────────────────────────────────────────────────


def choices_of(doc) -> dict | None:
    """这张票上存的每行选择 ``{"lines": [...], "warehouse"}``；改版前识别的票没有（照旧按 ``item_kind``）。"""
    try:
        value = json.loads(doc.get("line_choices") or "null")
    except ValueError:
        return None
    return value if isinstance(value, dict) and isinstance(value.get("lines"), list) else None


def _save_choices(name, value):
    frappe.db.set_value("ERP LINE Intake", name, "line_choices", json.dumps(value, ensure_ascii=False))


def _party(doc, result):
    """记忆键里的对方：LINE 里选的档 → 一般供应商 / 散客（按名称）→ 票面读出的。"""
    direction = doc.direction
    if doc.party == line_flow.FALLBACK:
        from pearnly_erp_portal.integrations import intake

        return "", intake._fallback_name(direction)
    if doc.party and doc.party != line_flow.FROM_DOCUMENT:
        master, title = (("ERP Supplier", "supplier_name") if direction == "purchase" else ("ERP Customer", "customer_name"))
        row = frappe.db.get_value(master, doc.party, ["tax_id", title], as_dict=True) or {}
        return row.get("tax_id") or "", row.get(title) or ""
    if direction == "purchase":
        return result.get("supplier_tax_id") or "", result.get("supplier_name") or ""
    return result.get("buyer_tax_id") or "", result.get("buyer_name") or ""


def _goods(value) -> bool:
    return any(row and row.get("item_type") == line_rules.GOODS for row in value["lines"])


def prepare(doc, result, line_user_id=None) -> dict:
    """识别完一张：每行预选并存到票上；单头仓按「这一批 LINE 里选过的 → 本人上次 → 卖货仓 → 唯一仓」预填（同网页）。"""
    tax, name = _party(doc, result)
    lines = []
    for row in result.get("lines") or []:
        if not isinstance(row, dict) or not str(row.get("description") or "").strip():
            lines.append(None)
            continue
        found = line_memory.suggest(row, company=doc.company, direction=doc.direction, party_tax=tax, party_name=name,
                                    expense=doc.direction == "purchase" and result.get("doc_kind") == "expense")
        lines.append({"item_type": found["item_type"], "product": found.get("product"),
                      "decided": bool(found["decided"]), "new": False})
    value = {"lines": lines, "warehouse": None}
    _place(doc, value, line_flow.choices(line_user_id) if line_user_id else {})
    _save_choices(doc.name, value)
    doc.line_choices = json.dumps(value, ensure_ascii=False)
    return value


def _place(doc, value, payload):
    """有入库行、还没定仓：这一批 LINE 里选过的 → 本人上次 → 卖货仓 → 唯一仓（同网页 `warehouses.prefill`）。
    一张票问一次仓，各行同仓（11-19 只问仓库、不问仓位；要逐行不同仓在「编辑」里改）。"""
    if _goods(value) and not value.get("warehouse"):
        batch = (payload or {}).get("batch_warehouse")
        value["warehouse"] = batch if _usable_warehouse(batch, doc.company) else _prefill(doc)


def _prefill(doc):
    from pearnly_erp_portal.inventory import warehouses

    # 公司在收单时已按发单人核过（`intake.receive`）：按这张票的公司和发单人取，不再按当前账号核（识别任务是系统路径）
    return warehouses.prefill_for(doc.direction, doc.company, user=doc.sent_by).get("warehouse") or None


def _usable_warehouse(name, company):
    if not name:
        return False
    row = frappe.db.get_value("ERP Warehouse", name, ["company", "is_active"], as_dict=True)
    return bool(row and row.company == company and row.is_active)


def _pending(value) -> str:
    """这张还差哪一步：``rows`` 有定不了的行 / ``warehouse`` 有入库行、仓定不下来 / 空 = 不问。"""
    if any(row and not row.get("decided") for row in value["lines"]):
        return "rows"
    if not _goods(value):
        return ""
    if not value.get("warehouse"):
        return "warehouse"
    return ""


# ── 提问 ─────────────────────────────────────────────────────────────────────


def _doc(name):
    return frappe.get_doc("ERP LINE Intake", name)


def _locked_doc(name):
    """加锁读这张（拿最新的、锁到本事务提交）。"""
    return frappe.get_doc("ERP LINE Intake", name, for_update=True)


def _result(doc) -> dict:
    return json.loads(doc.result or "{}")


def _title(doc, result) -> str:
    """提问开头「对方 · 单号」：分得清问的是哪张。"""
    tax, party = _party(doc, result)
    return " · ".join(part for part in (party or tax, result.get("doc_no")) if part)


def _undecided(value):
    return [index for index, row in enumerate(value["lines"]) if row and not row.get("decided")]


def _label(result, index) -> str:
    rows = result.get("lines") or []
    return str(rows[index].get("description") or "-") if index < len(rows) else "-"


def _moved(direction, new=False) -> str:
    if direction == "purchase":
        return _("erp.line.items.allNewIn") if new == "all" else _("erp.line.items.newIn")
    return _("erp.line.items.allNewOut") if new == "all" else _("erp.line.items.newOut")


def _question(doc, asking) -> dict | None:
    """这张票当前这一步的提问（没什么要问回 None）。"""
    value, result = choices_of(doc) or {"lines": []}, _result(doc)
    step = asking.get("step")
    name = doc.name
    if step == "rows":
        rows = _undecided(value)
        text = "\n".join(part for part in [_title(doc, result), _("erp.line.items.new")]
                         + [f"{index + 1}. {_label(result, index)}" for index in rows] if part)
        return _message(text, [_button(_moved(doc.direction, "all"), v="all_new", n=name),
                               _button(_("erp.line.items.allNone"), v="all_none", n=name),
                               _button(_("erp.line.items.each"), v="each", n=name)], name)
    if step == "row":
        index = asking["i"]
        return _message(f"{_title(doc, result)}\n{index + 1}. {_label(result, index)}", [
            _button(_moved(doc.direction), v="new", n=name, i=index),
            _button(_("erp.line.tag.none"), v="none", n=name, i=index),
            _button(_("erp.line.items.pick"), v="pick", n=name, i=index)], name)
    if step == "pick":
        return _message(_("erp.line.items.type"), [], name)
    if step == "warehouse":
        options = _warehouses(doc.company)
        if len(options) > MAX_CHOICES:
            return _message(f"{_title(doc, result)}\n{_('Choose the warehouse')}\n"
                            f"{_('Type a word from the warehouse name to find it')}", [], name)
        asking["offered"] = [key for key, _label_text in options]
        lines = [_title(doc, result), _("Choose the warehouse")]
        lines += [f"{index}. {label}" for index, (_key, label) in enumerate(options, 1)]
        return _message("\n".join(part for part in lines if part),
                        [_button(f"{index}. {label}", v="w", n=name, k=index - 1)
                         for index, (_key, label) in enumerate(options, 1)], name)
    return None


def _warehouses(company):
    rows = frappe.get_all("ERP Warehouse", filters={"company": company, "is_active": 1},
                          fields=["name", "warehouse_name"], order_by="warehouse_name asc", limit_page_length=MAX_CHOICES + 1)
    return [(row.name, row.warehouse_name or row.name) for row in rows]


def adopt(doc, payload) -> bool:
    """排着的这张还没定仓、这一批刚在 LINE 里选过仓：沿用，不再问（回 True = 选择变了）。"""
    value, batch = choices_of(doc), payload.get("batch_warehouse")
    if not value or not batch or value.get("warehouse") or not _goods(value) or not _usable_warehouse(batch, doc.company):
        return False
    value["warehouse"] = batch
    _save_choices(doc.name, value)
    doc.line_choices = json.dumps(value, ensure_ascii=False)
    return True


def start(doc, payload) -> dict | None:
    """这张从头问（`line_questions` 一次只交一张）：返回提问（并把进度记进会话 ``payload``），没什么要问回 None。"""
    value = choices_of(doc)
    step = _pending(value) if value else ""
    if not step:
        return None
    payload[ASKING] = {"intake": doc.name, "step": step, "i": None}
    return _question(doc, payload[ASKING])


def current(doc, asking) -> dict | None:
    """在问的这一步原样再出一次（`line_questions`：排队回「排队中」后、答题中打了无关的字）；按钮认这一张、这一步，照样能点。"""
    return _question(doc, asking)


def heading(doc) -> str:
    """提问开头那行「对方 · 单号」（写前判定的题同样用它）。"""
    return _title(doc, _result(doc))


def _finish(line_user_id, payload, reply_token, doc=None, cancelled=None):
    """这张的物料与仓库问完（``doc``）或在这几步点了「取消」（``cancelled``）：交 `line_questions` 接着问写前判定、出卡或作废，
    再接排着的下一张。"""
    from pearnly_erp_portal.integrations import line_questions

    payload.pop(ASKING, None)
    return line_questions.after_items(line_user_id, payload, reply_token, doc=doc, cancelled=cancelled)


def _advance(line_user_id, payload, reply_token, doc):
    """这张的选择改了：还有要问的接着问，问完回卡片。"""
    value = choices_of(doc)
    step = _pending(value)
    if not step:
        return _finish(line_user_id, payload, reply_token, doc)
    payload[ASKING].update(step=step, i=None)
    payload[ASKING].pop("offered", None)
    question = _question(doc, payload[ASKING])
    line_flow.keep(line_user_id, payload)
    return _reply(reply_token, question)


def _set_rows(doc, payload, indexes, **values):
    value = choices_of(doc)
    for index in indexes:
        row = value["lines"][index]
        row.update(values, decided=True)
    _place(doc, value, payload)
    _save_choices(doc.name, value)
    doc.line_choices = json.dumps(value, ensure_ascii=False)


def _current(line_user_id, user, params):
    """按钮点回来：只认当前在问的这一张（还没提交、是本人的）。"""
    payload = line_flow.choices(line_user_id)
    asking = payload.get(ASKING) or {}
    name = params.get("n") or ""
    if not asking.get("intake") or name != asking["intake"] or not frappe.db.exists("ERP LINE Intake", name):
        return payload, None
    # 加锁读这张（④ 第二期 L7）：每行选择整列写回（`_save_choices`），与网页编辑、CC 代录记付款写同一列，读的必须是最新的
    doc = _locked_doc(name)
    if doc.sent_by != user or doc.status != "ready" or not choices_of(doc):
        return payload, None
    return payload, doc


def answer(line_user_id, user, data, reply_token):
    """快捷回复点回来的一步。"""
    params = {key: values[0] for key, values in parse_qs(data or "").items()}
    value = params.get("v") or ""
    payload, doc = _current(line_user_id, user, params)
    if not doc:
        return _reply(reply_token, _text(_(STALE)))
    asking = payload[ASKING]
    if value == "cancel":
        return _finish(line_user_id, payload, reply_token, cancelled=doc)
    step = asking.get("step")
    if step == "rows" and value in ("all_new", "all_none", "each"):
        rows = _undecided(choices_of(doc))
        if value == "each":
            asking.update(step="row", i=rows[0] if rows else None)
            line_flow.keep(line_user_id, payload)
            return _reply(reply_token, _question(doc, asking))
        if value == "all_new":
            _set_rows(doc, payload, rows, item_type=line_rules.GOODS, product=None, new=True)
        else:
            _set_rows(doc, payload, rows, item_type=line_rules.SERVICE, product=None, new=False)
        return _advance(line_user_id, payload, reply_token, doc)
    if step in ("row", "pick") and str(params.get("i", "")) == str(asking.get("i")):
        if value == "pick":
            asking["step"] = "pick"
            line_flow.keep(line_user_id, payload)
            return _reply(reply_token, _question(doc, asking))
        if value in ("new", "none"):
            return _row_done(line_user_id, payload, reply_token, doc, item_type=line_rules.GOODS if value == "new"
                             else line_rules.SERVICE, product=None, new=value == "new")
        if value == "o":
            product = _offered(asking, params)
            if product:
                return _picked(line_user_id, payload, reply_token, doc, product)
    if step == "warehouse" and value == "w":
        warehouse = _offered(asking, params)
        if warehouse and _usable_warehouse(warehouse, doc.company):
            return _chose_warehouse(line_user_id, payload, reply_token, doc, warehouse)
    return _reply(reply_token, _text(_(STALE)))


def _offered(asking, params):
    try:
        return (asking.get("offered") or [])[int(params.get("k", ""))]
    except (ValueError, IndexError):
        return None


def _picked(line_user_id, payload, reply_token, doc, product):
    """选了已有物料：Express 非库存品（运费、服务费类主档）只记账，其余入库 / 出库。"""
    row = frappe.db.get_value("ERP Product", product, ["express_code", "express_stock"], as_dict=True) or {}
    service = bool(row.get("express_code")) and not row.get("express_stock")
    return _row_done(line_user_id, payload, reply_token, doc, product=product, new=False,
                     item_type=line_rules.SERVICE if service else line_rules.GOODS)


def _row_done(line_user_id, payload, reply_token, doc, **values):
    asking = payload[ASKING]
    _set_rows(doc, payload, [asking["i"]], **values)
    rows = _undecided(choices_of(doc))
    asking.pop("offered", None)
    if rows:
        asking.update(step="row", i=rows[0])
        line_flow.keep(line_user_id, payload)
        return _reply(reply_token, _question(doc, asking))
    return _advance(line_user_id, payload, reply_token, doc)


def _chose_warehouse(line_user_id, payload, reply_token, doc, warehouse):
    value = choices_of(doc)
    value["warehouse"] = warehouse
    _save_choices(doc.name, value)
    doc.line_choices = json.dumps(value, ensure_ascii=False)
    payload["batch_warehouse"] = warehouse  # 同一批后面几张沿用，不再问
    return _advance(line_user_id, payload, reply_token, doc)


# ── 打字找 ───────────────────────────────────────────────────────────────────


def search(line_user_id, user, text, reply_token) -> bool:
    """停在「选已有物料」「仓库」（选项放不下按钮时）这一步收到的文字＝名称或编码。不在这几步返回 False。"""
    payload = line_flow.choices(line_user_id)
    asking = payload.get(ASKING) or {}
    step, wanted = asking.get("step"), str(text or "").strip()
    if step not in ("pick", "warehouse") or not wanted or not asking.get("intake"):
        return False
    payload, doc = _current(line_user_id, user, {"n": asking["intake"]})
    if not doc:
        return False
    if step == "pick":
        found = _products(doc, wanted)
    else:
        exact, found = line_flow.find("warehouse", {"company": doc.company}, user, wanted)
        if exact:
            return _chose_warehouse(line_user_id, payload, reply_token, doc, exact) or True
    if not found:
        _reply(reply_token, [_text(_("No match for “{0}”").format(wanted[:40])), _question(doc, asking)])
        line_flow.keep(line_user_id, payload)
        return True
    if len(found) > MAX_CHOICES:
        _reply(reply_token, _message(_("Too many matches; type a few more letters to narrow it down"), [], doc.name))
        return True
    if step == "pick" and len(found) == 1 and found[0][2]:
        return _picked(line_user_id, payload, reply_token, doc, found[0][0]) or True  # 编码准确命中
    asking["offered"] = [row[0] for row in found]
    line_flow.keep(line_user_id, payload)
    value = {"pick": "o", "warehouse": "w"}[step]
    lines = [_("Found {0}. Tap the number to choose:").format(len(found))]
    lines += [f"{index}. {row[1]}" for index, row in enumerate(found, 1)]
    extra = {"i": asking.get("i")} if step == "pick" else {}
    _reply(reply_token, _message("\n".join(lines), [_button(str(index), v=value, n=doc.name, k=index - 1, **extra)
                                                    for index in range(1, len(found) + 1)], doc.name))
    return True


def _products(doc, text):
    """这家公司可用的物料里按名称（模糊）/ 编码、条码（准确）找 → ``[(物料, 名称 编码, 编码准确命中)]``。"""
    from pearnly_erp_portal.masters.selection import api_choices

    rows = api_choices("product", doc.direction, txt=text, company=doc.company)
    exact = [row for row in rows if text in (row.get("code"), row.get("express_code"), row.get("barcode"))]
    rows = exact if len(exact) == 1 else rows
    return [(row.name, " ".join(part for part in (row.get("code"), row.get("product_name")) if part),
             len(exact) == 1) for row in rows][:MAX_CHOICES + 1]


__all__ = ["ACTION", "ASKING", "adopt", "answer", "choices_of", "current", "heading", "is_step", "prepare", "search", "start"]
