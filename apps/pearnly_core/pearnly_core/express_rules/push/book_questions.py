# -*- coding: utf-8 -*-
"""开始前问完的四题（A3 / P2-2，主会话 11-16 审定）：单位、负库存、出库成本口径、现金科目。纯函数：不依赖 Frappe、不读库、不做 IO。

CC 桌面版写单时（载荷 21，``guided: true``）遇到这四种情形会停下：负库存（``STOCK_INSUFFICIENT``）、单位（``UNIT_CHOICE``）、
成本（``COST_BASIS_CHOICE``）、现金科目（``CASH_ACCOUNT_CHOICE``）。这里用**同一判法**在开始前把它们判出来，变成题卡上的题；
人答了由 :func:`apply` 写进载荷对应的键，CC 照写、不再停。判法逐条对齐 CC 桌面版（``tests/test_companion_contract.py`` 比常量，
``companion/tests/test_seam_book_questions.py`` 拿同一本合成账套两边真跑比结论）：

- 负库存 ← ``companion/stock_guard.shortages``：只看卖出库存品的行；物料级 ``NEGALLOW`` 盖过账套（Y 不核、N 核、空跟账套）；账套不许
  （``NEGALLOW = N``）或要批准（``NEGAUTH`` 非空）都算不许；``CHKNEGLEV`` 0 按全仓总结存、1 / 2 / 空按行实际写入的仓（没带仓 = 主仓）。
  已有物料不够 → **事实**（不是选择题），出口只有「这张先留下」；随单新建的物料（结存 0）在账套不许负库存时 → 一题
  「设为允许负库存 / 这张先留下」，账套要批准时只有「先留下」（批准只能在 Express 里做）。选了「允许」按账套记住。
- 单位 ← ``companion/guided._unit_line``（引导模式）：票面单位能对上这件物料的某个单位、行上有来源自己的换算率、票面没写单位 → 不问；
  对不上 → 选项 = 这件物料的各单位（带系数）+「填换算」；随单新建的物料单位解析不出 → 选项 = 账套单位表。按「账套 × 方向 × 物料 × 票面单位」记住。
- 成本 ← ``companion/guided._cost_line``：卖出库存品、没有成本基础（新物料；结存 ≤ 0 且没有标准成本）→「不结转 / 填单位成本」，
  **不预选、不记**（11-12 D-2，主会话 11-16 再定）。负库存这一关先过，过不了的单不出成本题。
- 现金科目 ← ``companion/guided.choices_needed``：现购 HP / 现销 HS（不是收银、不是退货），账套现金科目 ``ACCNUM00`` 不能记账 → 选可记账科目，
  名称含 เงินสด 的排前，只有一个这样的科目就自动用。按账套记住。

代录（``mode = operator``）只问单位与负库存：成本与现金科目由 Express 界面自己定（``required_fields.MODE_GAPS``），不出这两题。

账套资料（:func:`facts_of`）缺某一项时（旧版 CC 桌面版的主档读回没带）**不猜**：判不了的那一题不在开始前出，交 CC 桌面版写时按原样核
（它会回 ``*_CHOICE`` / ``STOCK_INSUFFICIENT``）。只有标准成本缺时成本题照问（多问一次无害：CC 有标准成本时照标准成本写，回执标 ``cost_ignored``）。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

UNIT, NEGATIVE, COST, CASH = "unit", "negative_stock", "cost_basis", "cash_account"
KINDS = (NEGATIVE, UNIT, COST, CASH)  # 出题顺序 = CC 桌面版停下的顺序：负库存 → 单位 → 成本 → 现金科目
OPERATOR_KINDS = (NEGATIVE, UNIT)  # 代录：成本、现金科目由 Express 界面自己定
DIRECT, OPERATOR = "direct", "operator"

STOCK_IN, STOCK_OUT = "stock_item", "stock_sale"  # 同 CC 桌面版 ``guided_keys.STOCK_IN / STOCK_OUT``
CASH_DOCTYPES = ("HP", "HS")  # 同 CC 桌面版 ``guided_keys.CASH_DOCTYPES``
COST_GIVEN, COST_NONE = "given", "none"  # 同 CC 桌面版 ``guided_keys.COST_GIVEN / COST_NONE``
ALLOW, HOLD = "Y", "hold"  # 负库存题的两个答案：新物料设为允许负库存 / 这张先留下
FACTOR = "factor"  # 单位题「填换算」那个选项：答案 ``factor:<1 票面单位 = 几库存单位>``
FACTOR_PREFIX = FACTOR + ":"
GIVEN_PREFIX = COST_GIVEN + ":"  # 成本题「填单位成本」的答案：``given:<每库存单位成本>``
# 选项上「选它还要填一个数」：与引导引擎表单子题同一形状（P3 窗口 11-16 定）
NUMBER_ENTRY = {"input": "number", "min_exclusive": "0"}
NEW_ITEM_SUBJECT = "new-item"  # 负库存题按账套记住的那一项（只记「允许」）
CASH_SUBJECT = "cash"
CASH_WORD = "เงินสด"
_YES, _NO = "Y", "N"
ALL_LOCATIONS = "all"  # 同 CC 桌面版 ``stock_guard.ALL_LOCATIONS``

# OCR 单位文字 → Express 单位码的后备映射：与 CC 桌面版 ``dbf_schema._UNIT_MAP`` 逐条相同（tests/test_companion_contract.py 比对），
# 结果仍须是这本账套单位表里的码
UNIT_FALLBACK = {
    "ลิตร": "ลต", "ลต": "ลต", "l": "ลต", "liter": "ลต", "litre": "ลต",
    "ขวด": "ขด", "bottle": "ขด",
    "ถัง": "ถง", "ถง": "ถง",
    "ชิ้น": "ชน", "pcs": "ชน", "piece": "ชน", "pc": "ชน",
    "อัน": "อน",
    "กล่อง": "กล", "box": "กล",
    "แกลลอน": "กน", "gallon": "กน",
    "ชุด": "ชด", "set": "ชด",
    "กระป๋อง": "กป", "can": "กป",
}


# ---------------- 小工具 ----------------


def _dec(value) -> Decimal:
    try:
        number = Decimal(str(value).strip()) if value not in (None, "") else Decimal(0)
    except (InvalidOperation, ValueError):
        return Decimal(0)
    return number if number.is_finite() else Decimal(0)


def _num(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return "0" if text in ("-0", "") else text


def _text(value) -> str:
    return str(value or "").strip()


def norm(text) -> str:
    """品名 / 单位比对键：去所有空白（含不换行空格）+ 小写（同 CC 桌面版 ``stock_lookup.norm`` 与 ``unit_code._norm``）。"""
    s = _text(text).lower()
    return "".join(ch for ch in s if not ch.isspace() and ch != "\xa0")


# ---------------- 账套资料 ----------------


def facts_of(data: Any) -> Dict[str, Any]:
    """ERP 主档镜像（CC 桌面版按需请求 ``express_masters`` 的结果）→ 这里认的账套资料。缺的项给 None（= 判不了），不给假值。

    ``{"products": {码: {...}}, "by_name": {规范品名: [码…]}, "stock": {(码, 仓): 数量} | None, "units": [{code, name, short}] | None,
    "settings": {negallow, negauth, chkneglev, mainloc}, "cash_account": str | None, "postable": set | None,
    "account_names": {码: 名}, "std_cost": bool}``"""
    data = data if isinstance(data, Mapping) else {}
    products: Dict[str, Dict[str, Any]] = {}
    by_name: Dict[str, List[str]] = {}
    std_cost = False
    for row in data.get("products") or []:
        if not isinstance(row, Mapping) or _text(row.get("kind")) != "stock" or not _text(row.get("code")):
            continue
        code = _text(row["code"])
        std_cost = std_cost or "std_cost" in row
        products[code] = {
            "code": code, "name": _text(row.get("name")), "unit": _text(row.get("unit")),
            "packs": [(_text(row.get(unit)), _dec(row.get(factor)), role) for unit, factor, role in
                      (("sale_unit", "sale_factor", "sale"), ("purchase_unit", "purchase_factor", "purchase"),
                       ("count_unit", "count_factor", "count"))],
            "negallow": _text(row.get("negallow")).upper(), "balance": _dec(row.get("balance")),
            "std_cost": _dec(row.get("std_cost")) if "std_cost" in row else None,
        }
        by_name.setdefault(norm(row.get("name")), []).append(code)
    stock = None
    if isinstance(data.get("stock"), list):
        stock = {}
        for row in data["stock"]:
            if isinstance(row, Mapping) and _text(row.get("code")):
                stock[(_text(row["code"]), _text(row.get("loccod")))] = _dec(row.get("qty"))
    units = None
    if isinstance(data.get("units"), list):
        units = [{"code": _text(row.get("code")), "name": _text(row.get("name")), "short": _text(row.get("short"))}
                 for row in data["units"] if isinstance(row, Mapping) and _text(row.get("code"))]
    raw = data.get("settings") if isinstance(data.get("settings"), Mapping) else {}
    settings = {key: (_text(raw[key]).upper() if key in raw else None) for key in ("negallow", "negauth", "chkneglev")}
    settings["mainloc"] = _text(raw.get("mainloc"))
    facts = data.get("facts") if isinstance(data.get("facts"), Mapping) else {}
    accounts = facts.get("accounts") if isinstance(facts.get("accounts"), list) else None
    postable = {_text(row.get("code")) for row in accounts if isinstance(row, Mapping) and _text(row.get("code"))} \
        if accounts is not None else None
    names = {_text(row.get("code")): _text(row.get("name")) for row in accounts or () if isinstance(row, Mapping)}
    cash = _text(data.get("cash_account")) if "cash_account" in data else None
    return {"products": products, "by_name": by_name, "stock": stock, "units": units, "settings": settings,
            "cash_account": cash, "postable": postable, "account_names": names, "std_cost": std_cost,
            "has_products": isinstance(data.get("products"), list)}


# ---------------- 找物料、单位 ----------------


def _found(facts, item) -> Tuple[Optional[Dict[str, Any]], bool]:
    """这一行对到的 Express 库存品：``(物料, 是否确定)``。给了码而码不在 → ``(None, False)``（写单时照旧停下交人重选，这里不管）；
    没给码按品名找不到 → ``(None, True)`` = 要随单新建。同名几件时优先有正结存的（同 CC ``stock_sale.find_stock_item``）。"""
    code = _text(item.get("code"))
    if code:
        found = facts["products"].get(code)
        return (found, True) if found else (None, False)
    rows = [facts["products"][c] for c in facts["by_name"].get(norm(item.get("name")), ())]
    if not rows:
        return None, True
    return next((row for row in rows if row["balance"] > 0), rows[0]), True


def resolve_unit(facts, text) -> str:
    """票面单位文字 → 这本账套的单位码（同 CC ``unit_code.resolve``）；解析不出回 ''。单位表没报上来也回 ''。"""
    key = norm(text)
    rows = facts.get("units") or []
    if not key or not rows:
        return ""
    for row in rows:
        if key in (norm(row["code"]), norm(row["short"]), norm(row["name"])):
            return row["code"]
    fallback = UNIT_FALLBACK.get(_text(text).lower(), "")
    return fallback if fallback and any(fallback == row["code"] for row in rows) else ""


def item_units(product) -> List[Dict[str, str]]:
    """这件物料的各单位与系数 ``[{code, factor, role}]``（同 CC ``guided.item_units``）。"""
    out = [{"code": product["unit"], "factor": "1", "role": "stock"}] if product["unit"] else []
    for code, factor, role in product["packs"]:
        if code:
            out.append({"code": code, "factor": _num(factor), "role": role})
    return out


def _has_factor(item) -> bool:
    given = _dec(item.get("factor"))
    return given > 0 and given != 1


def _stock_lines(items, mode):
    for index, item in enumerate(items or [], start=1):
        if isinstance(item, Mapping) and _text(item.get("item_mode")) == mode:
            yield index, item


# ---------------- 四题 ----------------


def _negative_allowed_book(settings) -> Optional[bool]:
    """账套许不许把库存写成负数；资料不全回 None。要批准（NEGAUTH 非空）算不许（同 CC ``stock_guard.negative_allowed``）。"""
    if settings.get("negallow") is None:
        return None
    if settings["negallow"] == _NO:
        return False
    if settings.get("negauth") is None:
        return None
    return not settings["negauth"]


def shortages(facts, items, header_loc: str = "") -> Dict[str, List[Dict[str, Any]]]:
    """开始前按 Express 的负库存规则核这张单的出库行：``{"short": 已有物料不够 [...], "new": 随单新建、账套不许 [...]}``。
    ``header_loc``：表头仓（行没带仓时用它，同 CC ``stock_location.plan``）。判不了的（资料不全）不列：交 CC 桌面版写时核。"""
    out: Dict[str, List[Dict[str, Any]]] = {"short": [], "new": []}
    if not facts.get("has_products"):
        return out
    settings = facts["settings"]
    book_ok = _negative_allowed_book(settings)
    need: Dict[Tuple[str, str], Decimal] = {}
    fresh: Dict[str, Dict[str, Any]] = {}
    for index, item in _stock_lines(items, STOCK_OUT):
        found, sure = _found(facts, item)
        if not sure:
            continue
        loc = _text(item.get("loccod")) or _text(header_loc)
        if found is None:
            name = _text(item.get("name"))
            flag = _text(item.get("negallow")).upper()
            if flag == _YES:
                continue
            given = _dec(item.get("factor"))
            qty = abs(_dec(item.get("qty") or "1")) * (given if given > 0 else Decimal(1))
            row = fresh.setdefault(name, {"name": name, "lines": [], "need": Decimal(0), "flag": flag})
            row["lines"].append(index)
            row["need"] += qty
            continue
        if found["negallow"] == _YES:
            continue
        units = _base_qty(facts, found, item)
        need[(found["code"], loc)] = need.get((found["code"], loc), Decimal(0)) + abs(units)
    for name, row in fresh.items():
        if row["need"] <= 0:
            continue
        if row["flag"] == _NO or book_ok is False:
            approval = settings.get("negauth") not in (None, "") and settings.get("negallow") != _NO
            out["new"].append({"name": name, "lines": row["lines"], "need": _num(row["need"]), "have": "0",
                               "approval": approval})
    level = settings.get("chkneglev")
    for (code, loc), qty in sorted(need.items()):
        product = facts["products"][code]
        checks = product["negallow"] == _NO or book_ok is False
        if not checks:
            continue  # 物料空 / A 跟账套而账套允许，或账套资料不全：不在开始前判
        total = product["balance"]
        # 各仓结存只报非 0 的行：一行都没有却有总结存，分不清是没有 STLOC（CC 按全仓核）还是各仓都是 0，当作不知道
        stock_known = facts.get("stock") is not None and bool(facts["stock"] or not any(
            row["balance"] for row in facts["products"].values()))
        if level is None or not stock_known:
            # 不知道按仓还是全仓：两种都不够才算不够
            located = (facts.get("stock") or {}).get((code, loc or settings.get("mainloc") or ""), Decimal(0))
            if qty > total and (not stock_known or qty > located):
                out["short"].append({"code": code, "name": product["name"], "loccod": ALL_LOCATIONS,
                                     "need": _num(qty), "have": _num(total)})
            continue
        by_warehouse = level != "0"
        if by_warehouse:
            where = loc or settings.get("mainloc") or ""
            have = facts["stock"].get((code, where), Decimal(0))
        else:
            where, have = ALL_LOCATIONS, total
        if qty > have:
            out["short"].append({"code": code, "name": product["name"], "loccod": where, "need": _num(qty), "have": _num(have)})
    return out


def _base_qty(facts, product, item) -> Decimal:
    """这一行折成库存单位的数量（同 CC ``stock_units.resolve`` 的换法）。"""
    qty = _dec(item.get("qty") or "1")
    code = _text(item.get("unit_code")) or resolve_unit(facts, item.get("unit"))
    given = _dec(item.get("factor"))
    if code and code == product["unit"]:
        return qty
    if code:
        for unit, factor, _role in product["packs"]:
            if unit == code and factor > 0 and factor != 1:
                return qty * factor
    if given > 0 and given != 1:
        return qty * given
    return qty


def unit_questions(facts, items, direction) -> List[Dict[str, Any]]:
    """要人定单位的行（同 CC ``guided._unit_line`` 的引导模式）：``[{line, name, code, unit, options, new}]``。
    ``options`` 已有物料是它的各单位 ``[{code, factor, role}]``；新物料是账套单位表 ``[{code, name}]``。"""
    if not facts.get("has_products") or facts.get("units") is None:
        return []
    mode = STOCK_OUT if direction == "sales" else STOCK_IN
    out = []
    for index, item in _stock_lines(items, mode):
        explicit = _text(item.get("unit_code"))
        found, sure = _found(facts, item)
        if not sure:
            continue
        text = _text(item.get("unit"))
        code = explicit or (resolve_unit(facts, text) if text else "")
        if found is None:
            if not code:
                out.append({"line": index, "name": _text(item.get("name")), "code": "", "key": _text(item.get("code")),
                            "unit": text, "new": True,
                            "options": [{"code": row["code"], "name": row["name"] or row["short"]} for row in facts["units"]]})
            continue
        if not (explicit or text):
            continue
        units = item_units(found)
        if (code and code in {row["code"] for row in units}) or _has_factor(item):
            continue
        out.append({"line": index, "name": _text(item.get("name")), "code": found["code"], "key": _text(item.get("code")),
                    "unit": text, "new": False, "options": units})
    return out


def cost_questions(facts, items) -> List[Dict[str, Any]]:
    """出库行没有成本基础（同 CC ``guided._cost_line``）：``[{line, name, code, balance}]``。已给 ``cost_choice`` 的行不问。"""
    if not facts.get("has_products"):
        return []
    out = []
    for index, item in _stock_lines(items, STOCK_OUT):
        if item.get("cost_choice") in (COST_GIVEN, COST_NONE):
            continue
        found, sure = _found(facts, item)
        if not sure:
            continue
        if found is None:
            out.append({"line": index, "name": _text(item.get("name")), "code": "", "balance": "0"})
            continue
        if found["balance"] > 0 or (found["std_cost"] or Decimal(0)) > 0:
            continue
        out.append({"line": index, "name": _text(item.get("name")), "code": found["code"], "balance": _num(found["balance"])})
    return out


def cash_question(facts, payload) -> Optional[Dict[str, Any]]:
    """现购 / 现销而账套现金科目不能记账（同 CC ``guided.choices_needed`` 最后一段）：``{cash_account, options, auto}``；不用问回 None。
    ``auto``：可记账科目里名称含 เงินสด 的只有一个时就是它（不问、直接用）。"""
    doctype = _text(payload.get("doctype")).upper()
    if doctype not in CASH_DOCTYPES or "pos" in payload or "refund" in payload or payload.get("cash_account") is not None:
        return None
    postable, cash = facts.get("postable"), facts.get("cash_account")
    if postable is None or cash is None:
        return None
    if cash and cash in postable:
        return None
    names = facts.get("account_names") or {}
    cashlike = sorted(code for code in postable if CASH_WORD in names.get(code, ""))
    rest = sorted(code for code in postable if code not in cashlike)
    options = [{"code": code, "name": names.get(code, "")} for code in cashlike + rest]
    return {"cash_account": cash, "options": options, "auto": cashlike[0] if len(cashlike) == 1 else ""}


# ---------------- 一张单：出题与下发 ----------------


def unit_subject(direction, row) -> str:
    """单位题按「方向 × 物料 × 票面单位」记（账套由调用方的记忆分开）。物料取载荷行自己带的 Express 码，没带取规范品名——
    :func:`apply` 只看载荷、不看账套资料，两边按同一个键对上。"""
    return "|".join(("unit", "sales" if direction == "sales" else "purchase", _text(row.get("key")) or norm(row["name"]),
                     norm(row["unit"])))


def questions(facts, payload, *, mode: str = DIRECT, remembered: Optional[Mapping[str, Mapping[str, str]]] = None
              ) -> Dict[str, Any]:
    """这张单开始前要问的题 → ``{"needed": [...], "facts": [...], "auto": {kind: {subject: value}}}``。

    - ``needed``：每题 ``{kind, subject, lines, options, preselected?, context}``；选项上可带 ``entry`` = 选它还要填一个数
      （``{"input": "number", "min_exclusive": "0"}``，与引导引擎表单子题同一形状，P3 窗口 11-16 定），答案编码「码:数」，
      没有 ``entry`` 的选项答案就是码本身；答案用 :func:`answer_problem` 核；
    - ``facts``：已有物料库存不够这类**不是选择题**的事实 ``[{kind: negative_stock, rows, exit: hold}]``——这张单写不进，出口只有「先留下」；
    - ``auto``：只有一个合格选项、自动定下的值（与记住的值一样由 :func:`apply` 下发）。
    ``remembered``：这本账套记过的 ``{kind: {subject: value}}``，记过的不再问。代录不出成本、现金科目两题。"""
    remembered = remembered or {}
    direction = "sales" if _text(payload.get("direction")) == "sales" else "purchase"
    items = payload.get("items") if str(payload.get("items_status") or "").strip() == "ok" else []
    needed: List[Dict[str, Any]] = []
    facts_out: List[Dict[str, Any]] = []
    auto: Dict[str, Dict[str, str]] = {}

    def known(kind, subject) -> str:
        return _text((remembered.get(kind) or {}).get(subject))

    short = shortages(facts, items, _text(payload.get("loccod"))) if direction == "sales" else {"short": [], "new": []}
    if short["short"]:
        facts_out.append({"kind": NEGATIVE, "rows": short["short"], "exit": HOLD})
    for row in short["new"]:
        if row["approval"]:
            facts_out.append({"kind": NEGATIVE, "rows": [{**row, "new": True}], "exit": HOLD, "approval": True})
            continue
        if known(NEGATIVE, NEW_ITEM_SUBJECT) == ALLOW:
            auto.setdefault(NEGATIVE, {})[row["name"]] = ALLOW
            continue
        needed.append({"kind": NEGATIVE, "subject": row["name"], "lines": row["lines"], "context": row,
                       "options": [{"code": ALLOW}, {"code": HOLD}]})
    blocked = bool(facts_out) or any(q["kind"] == NEGATIVE for q in needed)
    for row in unit_questions(facts, items, direction):
        subject = unit_subject(direction, row)
        value = known(UNIT, subject)
        if value:
            auto.setdefault(UNIT, {})[subject] = value
            continue
        question = {"kind": UNIT, "subject": subject, "lines": [row["line"]], "context": row,
                    "options": [{"code": opt["code"], **({"factor": opt["factor"], "role": opt["role"]} if "factor" in opt
                                                         else {"name": opt.get("name", "")})} for opt in row["options"]]}
        if not row["new"]:
            question["options"].append({"code": FACTOR, "entry": dict(NUMBER_ENTRY)})
            role = "sale" if direction == "sales" else "purchase"
            packs = [opt["code"] for opt in row["options"] if opt["role"] == role and opt["factor"] not in ("0", "1")]
            if len(packs) == 1:
                question["preselected"] = packs[0]
        needed.append(question)
    if mode != OPERATOR and not blocked:
        for row in cost_questions(facts, items):
            needed.append({"kind": COST, "subject": f"line-{row['line']}", "lines": [row["line"]], "context": row,
                           "options": [{"code": COST_NONE}, {"code": COST_GIVEN, "entry": dict(NUMBER_ENTRY)}]})
    if mode != OPERATOR:
        cash = cash_question(facts, payload)
        if cash:
            value = known(CASH, CASH_SUBJECT) or cash["auto"]
            if value and value in {opt["code"] for opt in cash["options"]}:
                auto.setdefault(CASH, {})[CASH_SUBJECT] = value
            else:
                needed.append({"kind": CASH, "subject": CASH_SUBJECT, "lines": [], "context": {"cash_account": cash["cash_account"]},
                               "options": cash["options"]})
    return {"needed": needed, "facts": facts_out, "auto": auto}


def apply(payload: Dict[str, Any], decided: Mapping[str, Mapping[str, str]], *, direction: str = "",
          guided: bool = True, mode: str = DIRECT) -> Dict[str, Any]:
    """把答案（人答的 + 记住的 + 自动定的，``{kind: {subject: value}}``）写进载荷（就地改并返回）。

    单位 → 行键 ``unit_code``（选了物料的单位或单位表里的码）或 ``factor``（填换算）；负库存 → 新物料行 ``negallow = Y``；
    成本 → 行键 ``cost_choice`` / ``cost``；现金科目 → 表头 ``cash_account``。``guided`` 给人在场的入口：CC 桌面版再遇到没判出的情形
    停下回 ``*_CHOICE``，不静默用默认（LINE / 定时 / 自动推不给，行为不变）。版本号由 ``common.finalize_payload`` 按新键盖 21。
    答「这张先留下」的单不该再交写——调用方据 :func:`held` 判。

    代录（``mode = operator``）：CC 桌面版的代录单只认四个行键（``guided_keys.OPERATOR_LINE_KEYS``），带了表头 ``guided`` / ``cash_account``
    或 ``cost_choice`` 整单拒收——所以只写单位与新物料的负库存选项，成本、现金科目、``guided`` 一律不写（契约测试守着）。"""
    direction = direction or ("sales" if _text(payload.get("direction")) == "sales" else "purchase")
    operator = mode == OPERATOR
    mode = STOCK_OUT if direction == "sales" else STOCK_IN
    items = payload.get("items") or []
    units = decided.get(UNIT) or {}
    negative = decided.get(NEGATIVE) or {}
    costs = decided.get(COST) or {}
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict) or _text(item.get("item_mode")) != mode:
            continue
        value = _text(units.get(unit_subject(direction, {"key": item.get("code"), "name": item.get("name"), "unit": item.get("unit")})))
        if value.startswith(FACTOR_PREFIX):
            factor = _dec(value[len(FACTOR_PREFIX):])
            if factor > 0:
                item["factor"] = _num(factor)
        elif value:
            item["unit_code"] = value
        if mode == STOCK_OUT:
            if _text(negative.get(_text(item.get("name")))) == ALLOW and not _text(item.get("code")):
                item["negallow"] = ALLOW
            cost = "" if operator else _text(costs.get(f"line-{index}"))
            if cost == COST_NONE:
                item["cost_choice"] = COST_NONE
            elif cost.startswith(GIVEN_PREFIX) and _dec(cost[len(GIVEN_PREFIX):]) > 0:
                item["cost_choice"], item["cost"] = COST_GIVEN, _num(_dec(cost[len(GIVEN_PREFIX):]))
    if operator:
        return payload
    cash = _text((decided.get(CASH) or {}).get(CASH_SUBJECT))
    if cash and _text(payload.get("doctype")).upper() in CASH_DOCTYPES:
        payload["cash_account"] = cash
    if guided:
        payload["guided"] = True
    return payload


def answer_problem(question: Mapping[str, Any], value) -> str:
    """核一题的答案：码在选项里；选项带 ``entry`` 的必须是「码:数」且数满足 ``min_exclusive``；没带的就是码本身。合格回 ''，否则回原因。"""
    text = _text(value)
    code, sep, number = text.partition(":")
    option = next((opt for opt in question.get("options") or () if _text(opt.get("code")) == code), None)
    if option is None:
        return "not_an_option"
    entry = option.get("entry")
    if not entry:
        return "" if not sep else "no_entry"
    if not sep or not number.strip():
        return "entry_missing"
    try:
        amount = Decimal(number.strip())
    except (InvalidOperation, ValueError):
        return "entry_not_a_number"
    if not amount.is_finite() or ("min_exclusive" in entry and amount <= _dec(entry["min_exclusive"])):
        return "entry_out_of_range"
    return ""


def held(decided: Mapping[str, Mapping[str, str]]) -> bool:
    """有一题答了「这张先留下」：这张单不交写。"""
    return any(_text(value) == HOLD for value in (decided.get(NEGATIVE) or {}).values())


def remember_of(answers: Mapping[str, Mapping[str, str]]) -> Dict[str, Dict[str, str]]:
    """人答的值里哪些按账套记住：单位、现金科目照记；负库存只记「允许」（记成账套的新物料默认），「先留下」不记；成本不记。"""
    out: Dict[str, Dict[str, str]] = {}
    for subject, value in (answers.get(UNIT) or {}).items():
        if _text(value):
            out.setdefault(UNIT, {})[subject] = _text(value)
    if any(_text(value) == ALLOW for value in (answers.get(NEGATIVE) or {}).values()):
        out.setdefault(NEGATIVE, {})[NEW_ITEM_SUBJECT] = ALLOW
    cash = _text((answers.get(CASH) or {}).get(CASH_SUBJECT))
    if cash:
        out.setdefault(CASH, {})[CASH_SUBJECT] = cash
    return out


__all__ = ["ALLOW", "CASH", "CASH_DOCTYPES", "COST", "COST_GIVEN", "COST_NONE", "DIRECT", "FACTOR", "FACTOR_PREFIX", "GIVEN_PREFIX",
           "HOLD", "KINDS", "NEGATIVE", "NEW_ITEM_SUBJECT", "NUMBER_ENTRY", "OPERATOR", "OPERATOR_KINDS", "UNIT", "UNIT_FALLBACK", "answer_problem", "apply",
           "cash_question", "cost_questions", "facts_of", "held", "item_units", "norm", "questions", "remember_of",
           "resolve_unit", "shortages", "unit_questions", "unit_subject"]
