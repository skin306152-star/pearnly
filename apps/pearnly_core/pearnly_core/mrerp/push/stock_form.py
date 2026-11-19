"""一次 MR.ERP 调拨 / 调整网页表单原生保存（纯网络，不碰数据库；多产品共用，现在只有 ERP 库存单用，2026-11-19）。

MR.ERP 没有调拨（ย้ายคลังสินค้า，``stktrant``）与调整（ปรับปรุงสินค้า，``stktranmod``）的导入，只有网页表单（11-19 读源码与测试账套实录，
共享笔记「MR.ERP-库存网页表单协议-源码-11-19.md」）。这里照人在网页上录一张的同一串请求走，结果就是原生的单据：

1. 共用入口拿会话（`mrerp.account.use`：同账号锁、被踢标记、接上缓存会话），选账套；从主菜单页读出这类单据的菜单（``allview.php?idmenu=``），
   选过的或唯一一个；
2. ``GET {模块}/allview.php?idmenu=`` 让会话处在这个菜单（单号前缀、权限、调整单的日记账关联都按会话里的菜单取）；调整单页上提示
   「กรุณากำหนดการเชื่อมโยง "สมุดรายวัน"」= 没关联日记账，不录（同导入只用已关联的菜单）；
3. ``GET allform.php``（新单）取隐藏值（自动编号三项、单号前缀、默认分店）；``bshlistboxdata.php`` 取物料 / 部门 / งาน / 仓 / 分店的
   数据库 id（表单传 id，不传码）；
4. 前端在提交前做、服务端不复查的，这里先做：会计期间（``checkothperiod.php``：``dt||Y`` 已关账、``dt||N||天数`` 不早于今天减天数）、单号
   查重（``checkac.php``，查到同号先按单号回读认领）、调拨两仓不同、数量不为 0、同物料并成一行（调拨）/ 同物料同仓轧差（调整）；
   出库方向的行单价取系统成本（``baprice.php``，网页同样取它；保存后 MR.ERP 按成本法重算）；
5. ``POST allsave.php``：页上出现 ``Save Success`` 算保存成功；之后按单号在这个菜单的列表里查到才算写成（记 MR.ERP 单号 = 前缀 + 单号），
   查不到 = 结果不明（``unknown``，不当失败重推）。

日记账：调拨 MR.ERP 自己不记；调整由 MR.ERP 按账套「成本会计制度」与菜单关联记（同原生）。

载荷（产品冻结、纯 JSON）::

    {"kind": "transfer" | "adjust", "number", "date", "notes": [备注1, 备注2, 备注3],
     "from_store", "to_store"（只调拨，MR.ERP 仓库码）,
     "lines": [{"code": MR.ERP 物料码, "qty"（调整带正负）, "store"（只调整）, "price"（只调整增加行，可无）}],
     "menu": 选过的菜单号, "defaults": {主档字段: 选过的代码}}
"""
from __future__ import annotations

import html as _html
import json
import logging
import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

TRANSFER, ADJUST = "transfer", "adjust"
MODULES = {TRANSFER: "stktrant", ADJUST: "stktranmod"}
LOCK_WAIT_SECONDS = 30
NUMBER_MAX, NOTE_MAX = 18, 50
CENT, PRICE = Decimal("0.01"), Decimal("0.0001")
SAVED = "Save Success"
NOT_LINKED_TEXT = "การเชื่อมโยง"  # 「กรุณากำหนดการเชื่อมโยง "สมุดรายวัน"」
SIGNED_OUT = ("กรุณา Login เข้าสู่ระบบ", "มีผู้ Login ซ้ำ")

# 结论里的原因码（`mrerp.error.<码>` 四语；与导入共用的沿用导入那边的）
ITEM_UNMAPPED = "ERR_MRERP_ITEM_UNMAPPED"
WAREHOUSE_UNMAPPED = "ERR_MRERP_WAREHOUSE_UNMAPPED"
NUMBER_INVALID = "ERR_MRERP_STOCK_NUMBER"
LINES_EMPTY = "ERR_MRERP_STOCK_LINES"
QTY_INVALID = "ERR_MRERP_STOCK_QTY"
SAME_STORE = "ERR_MRERP_STOCK_SAME_STORE"        # 调出仓与调入仓相同
PERIOD_CLOSED = "ERR_MRERP_PERIOD_CLOSED"        # 日期不在 MR.ERP 的会计期间里（已关账或早于允许的天数）
SAVE_FAILED = "ERR_MRERP_SAVE_FAILED"            # 保存页没有回 Save Success（带页上 alert 原文）
NUMBER_TAKEN = "ERR_MRERP_STOCK_NUMBER_TAKEN"    # 同号的单已在 MR.ERP（别的菜单或别的单），按单号又认不出是这一张
READBACK_NOT_FOUND = "ERR_MRERP_READBACK_NOT_FOUND"

_MENU_LINK = re.compile(r'href="(?:\.\./)?(stktrant|stktranmod)/allview\.php\?idmenu=(\d+)"[^>]*>\s*(?:<span>)?([^<]*)')
_HIDDEN = re.compile(r'<input[^>]*\bname="(statusautono|userdefinedno|runautono|othcombrhval|txtdoctype)"[^>]*>', re.I)
_VALUE = re.compile(r'\bvalue="([^"]*)"')
_ALERT = re.compile(r'alert\(\s*["\'](.*?)["\']\s*\)', re.S)
_DETAIL = re.compile(r'allform\.php\?id=(\d+)&(?:amp;)?status=vie')
_TAG = re.compile(r"<[^>]+>")


# ── 纯函数（离线测试） ───────────────────────────────────────────────────────

def _num(value) -> Decimal:
    try:
        number = Decimal(str(value if value is not None else "").replace(",", "").strip())
    except InvalidOperation:
        raise ValueError(QTY_INVALID) from None
    if not number.is_finite():
        raise ValueError(QTY_INVALID)
    return number


def _plain(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return "0" if text in ("-0", "") else text


def menus_of(page: str, module: str) -> List[Dict[str, str]]:
    """主菜单页 → 这类单据的菜单 ``[{idmenu, label}]``（页面顺序、去重）。"""
    seen, out = set(), []
    for found_module, idmenu, label in _MENU_LINK.findall(page or ""):
        if found_module == module and idmenu not in seen:
            seen.add(idmenu)
            out.append({"idmenu": idmenu, "label": " ".join(_html.unescape(label).split())})
    return out


def hidden_values(form: str) -> Dict[str, str]:
    out = {}
    for tag in _HIDDEN.finditer(form or ""):
        value = _VALUE.search(tag.group(0))
        out[tag.group(1)] = _html.unescape(value.group(1)) if value else ""
    return out


def be_date(day: str) -> str:
    """``YYYY-MM-DD`` → 网页表单的 ``dd/mm/佛历年``。"""
    year, month, dd = str(day)[:10].split("-")
    return f"{dd}/{month}/{int(year) + 543}"


def period_problem(answer: str, day: str, today: date) -> bool:
    """``checkothperiod.php`` 的回答 → 这一天能不能录：``dt||Y`` 已关账；``dt||N||天数`` 不能早于今天减天数。"""
    parts = str(answer or "").strip().split("||")
    if len(parts) >= 2 and parts[1] == "Y":
        return True
    if len(parts) >= 3 and parts[1] == "N" and parts[2].strip().isdigit():
        return date.fromisoformat(str(day)[:10]) < today - timedelta(days=int(parts[2]))
    return False


def merged_lines(entry: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], str]:
    """（行, 问题码）：调拨同物料并成一行（数量为正）；调整同物料同仓轧差（带正负，净 0 的不写，增加行的单价取同格第一个给了价的）。"""
    kind = entry.get("kind")
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for line in entry.get("lines") or []:
        code = str(line.get("code") or "").strip()
        store = str(line.get("store") or "").strip() if kind == ADJUST else ""
        if not code:
            return [], ITEM_UNMAPPED
        if kind == ADJUST and not store:
            return [], WAREHOUSE_UNMAPPED
        try:
            qty = _num(line.get("qty"))
        except ValueError:
            return [], QTY_INVALID
        if kind == TRANSFER and qty <= 0:
            return [], QTY_INVALID
        slot = out.setdefault((code, store), {"code": code, "store": store, "qty": Decimal("0"), "price": None})
        slot["qty"] += qty
        if line.get("price") not in (None, "") and slot["price"] is None:
            try:
                slot["price"] = _num(line["price"])
            except ValueError:
                return [], QTY_INVALID
    rows = [row for row in out.values() if row["qty"]]
    return rows, ("" if rows else LINES_EMPTY)


def check(entry: Dict[str, Any]) -> str:
    """表头规则（发请求之前）：单号、仓。"""
    number = str(entry.get("number") or "").strip()
    if not number or len(number) > NUMBER_MAX:
        return NUMBER_INVALID
    if entry.get("kind") == TRANSFER:
        source, target = str(entry.get("from_store") or "").strip(), str(entry.get("to_store") or "").strip()
        if not source or not target:
            return WAREHOUSE_UNMAPPED
        if source == target:
            return SAME_STORE
    return merged_lines(entry)[1]


def ids(listbox: Dict[str, Any], key: str) -> Dict[str, list]:
    """``bshlistboxdata.php`` 的一项 → {码: 整行}（行是 ``[id, 码, 名, …]``）。"""
    return {str(row[1]).strip(): row for row in (listbox or {}).get(key) or [] if isinstance(row, list) and len(row) >= 2}


def form_fields(entry: Dict[str, Any], hidden: Dict[str, str], listbox: Dict[str, Any], head: Dict[str, str],
                prices: Dict[str, Decimal]) -> Tuple[List[Tuple[str, str]], str]:
    """保存要 POST 的字段（同网页表单的名字与顺序；明细是 ``txtxxx[]`` 数组）→（字段, 问题码）。``head``：分店 / 部门 / งาน 的码；
    ``prices``：出库方向的行按物料码取到的系统成本单价。"""
    kind = entry["kind"]
    rows, problem = merged_lines(entry)
    if problem:
        return [], problem
    items = ids(listbox, "txtcode[]")
    depts, jobs, branches = ids(listbox, "txtdept[]"), ids(listbox, "txtsuborderd[]"), ids(listbox, "txtothcombrh")
    stores = ids(listbox, "txtstorefrom" if kind == TRANSFER else "txtstore[]")
    dept, job = depts.get(head.get("dept") or ""), jobs.get(head.get("job") or "")
    branch = branches.get(head.get("branch") or "")
    if not dept or not job or not branch:
        return [], "ERR_MRERP_STOCK_FIELD"
    fields: List[Tuple[str, str]] = [
        ("id", ""), ("statusautono", hidden.get("statusautono", "")), ("userdefinedno", hidden.get("userdefinedno", "")),
        ("runautono", hidden.get("runautono", "")), ("txtdoctype", hidden.get("txtdoctype", "")),
        ("txtdocnum", str(entry["number"]).strip()), ("txtdate", be_date(entry["date"])), ("chktran", "1"),
        ("othcombrhval", str(branch[0])), ("txtothcombrh", str(branch[1]))]
    notes = [str(note or "")[:NOTE_MAX] for note in list(entry.get("notes") or [])[:3]] + ["", "", ""]
    fields += [(f"txtremark{i}", notes[i - 1]) for i in (1, 2, 3)]
    if kind == TRANSFER:
        source, target = stores.get(str(entry.get("from_store") or "").strip()), stores.get(str(entry.get("to_store") or "").strip())
        if not source or not target:
            return [], WAREHOUSE_UNMAPPED
        fields += [("txtstorefrom", str(source[1])), ("txtstorefromval", str(source[0])),
                   ("txtstoreto", str(target[1])), ("txtstoretoval", str(target[0]))]
    total = Decimal("0")
    lines: List[Tuple[str, str]] = []
    for number, row in enumerate(rows, start=1):
        item = items.get(row["code"])
        if not item:
            return [], ITEM_UNMAPPED
        store = stores.get(row["store"]) if kind == ADJUST else None
        if kind == ADJUST and not store:
            return [], WAREHOUSE_UNMAPPED
        # 调整的物料行多一位成本法：[id, 码, 名, 成本法, 单位 id, 单位名, …]；调拨 [id, 码, 名, 单位 id, 单位名, …]
        unit_id, unit_name = (item[4], item[5]) if kind == ADJUST and len(item) >= 6 else (item[3], item[4]) \
            if len(item) >= 5 else ("", "")
        qty = row["qty"]
        price = row["price"] if kind == ADJUST and qty > 0 and row["price"] is not None else prices.get(row["code"], Decimal("0"))
        price = price.quantize(PRICE, rounding=ROUND_HALF_UP)
        amount = (qty * price).quantize(CENT, rounding=ROUND_HALF_UP)
        total += amount
        lines += [("txtno[]", str(number)), ("txtcode[]", str(item[1])), ("txtcodeval[]", str(item[0])),
                  ("txtname[]", str(item[2])), ("txtitemdesc[]", str(item[2]))]
        if kind == ADJUST:
            lines += [("txtstore[]", str(store[1])), ("txtstoreval[]", str(store[0])), ("txtstkmet[]", str(item[3]))]
        lines += [("txtdept[]", str(dept[1])), ("txtdeptval[]", str(dept[0])),
                  ("txtsuborderd[]", str(job[1])), ("txtsuborderdval[]", str(job[0])),
                  ("txtamount[]", _plain(qty)), ("txtunit[]", str(unit_name)), ("txtunitval[]", str(unit_id)),
                  ("txtprice[]", _plain(price)), ("txttotal[]", _plain(amount))]
        # 第二单位（txtamount_pg2[] / txtunitval_pg2[]）不传：MR.ERP 把空串按整数 0 绑进外键（单位表没有 0 号，整张回滚，
        # 11-19 测试账套实录）；不传时取到 NULL，同网页上没填第二单位
        if kind == ADJUST:
            lines += [("txtcodematchval_pg3[]", ""), ("txttrandate_pg3[]", "")]
    return fields + lines + [("txtgrandtotal", _plain(total))], ""


def saved_problem(page: str) -> str:
    """保存页 → 空串（``Save Success``）或问题（页上 alert 原文；被踢回登录页同样算失败）。"""
    text = page or ""
    if SAVED in text:
        return ""
    alert = _ALERT.search(text)
    return f"{SAVE_FAILED}; {' '.join(_html.unescape(alert.group(1)).split())[:200]}" if alert else SAVE_FAILED


def listed(page: str, docnum: str) -> Optional[str]:
    """列表页 / ``showdata.php`` 里按完整单号（前缀 + 单号）找这一张：回明细 id；没有回 None（列表是模糊匹配，逐行比完整单号）。"""
    text = page or ""
    for match in _DETAIL.finditer(text):
        # 调拨的 showdata 一张单一个 <p>，调整的列表页一张单一行 <tr>（11-19 测试账套实录）
        start = max(text.rfind("<p>", 0, match.start()), text.rfind("<p ", 0, match.start()), text.rfind("<tr", 0, match.start()))
        ends = [found for found in (text.find("</p>", match.end()), text.find("</tr>", match.end())) if found >= 0]
        row = _TAG.sub(" ", text[max(start, 0):min(ends) if ends else len(text)])
        if docnum in row.split():
            return match.group(1)
    return None


# ── 联网（导入任务里用；凭据由调用方交进来） ─────────────────────────────────────────────────

def _outcome(status, reason="", docnum="", meta=None, menus=None, masters=None) -> Dict[str, Any]:
    return {"status": status, "reason": str(reason or "")[:500], "docnum": docnum, "meta": meta or {},
            "menus": menus or {}, "masters": masters or {}}


def execute(entry: Dict[str, Any], target: Dict[str, Any], secrets: Dict[str, str], system_url: str,
            today: Optional[date] = None) -> Dict[str, Any]:
    """→ ``{status: success | failed | unknown, reason, docnum, meta, menus, masters}``（同 `push.stock.execute`）。永不抛。
    ``menus``：这次在主菜单页读到的这类单据菜单 ``{模块: [{idmenu, label, linked}]}``（存回连接给「问一次」用）。"""
    from pearnly_core.session_lock import SessionLockUnavailable
    from pearnly_core.mrerp import account as mrerp_account
    from pearnly_core.mrerp.session import SessionError

    comidyear, _, seldb = str(target.get("account_set") or "").partition(":")
    state: Dict[str, Any] = {"sent": False, "menus": {}, "masters": {}}
    problem = check(entry) if entry.get("kind") in MODULES else "ERR_MRERP_MODULE_UNAVAILABLE"
    if problem:
        return _outcome("failed", problem)
    try:
        with mrerp_account.use(system_url, secrets.get("username") or "", secrets.get("password") or "", manual=False,
                               lock_wait=LOCK_WAIT_SECONDS) as session:
            session.select(comidyear, seldb, probe="impstktranrec/formupload.php?idmenu=356")
            return _run(session, entry, comidyear, seldb, state, today or date.today())
    except mrerp_account.Marked:
        return _outcome("failed", mrerp_account.IN_USE)
    except SessionLockUnavailable:
        return _outcome("failed", "ERR_MRERP_ACCOUNT_IN_USE")
    except SessionError as error:
        return _outcome("unknown" if state["sent"] else "failed", f"{error.code}: {error}", menus=state["menus"])
    except Exception as error:  # noqa: BLE001 — 契约：任何异常都要落到单据上
        logger.exception("MR.ERP stock form save raised")
        return _outcome("unknown" if state["sent"] else "failed", f"ERR_UNEXPECTED: {type(error).__name__}: {error}",
                        menus=state["menus"])


def _signed_out(text: str) -> bool:
    return any(mark in (text or "") for mark in SIGNED_OUT)


def _run(session, entry, comidyear, seldb, state, today) -> Dict[str, Any]:
    from pearnly_core.mrerp.master_reader import MasterReader
    from pearnly_core.mrerp.push.defaults import AccountDefaults, Missing
    from pearnly_core.mrerp.session import SessionError

    kind, module, base = entry["kind"], MODULES[entry["kind"]], session.base
    home = {"Referer": f"{base}/login/mainmenu.php"}
    xhr = {"X-Requested-With": "XMLHttpRequest", "Origin": base}

    def get(path, **kwargs):
        response = session.request("GET", path, **kwargs)
        if _signed_out(response.text):
            raise SessionError("ERR_AUTH", "signed out while saving a stock form")
        return response.text or ""

    def post(path, data, referer):
        response = session.request("POST", path, data=data, headers={**xhr, "Referer": referer})
        if _signed_out(response.text):
            raise SessionError("ERR_AUTH", "signed out while saving a stock form")
        return response.text or ""

    # 分店 / 部门 / งาน：同导入，按这份账套的主档与选过的（主档读取会改写会话里的当前菜单，先读）
    reader = MasterReader(session.request, base)
    defaults = AccountDefaults(reader, entry.get("defaults") or {})
    reader.head_branch()
    try:
        head = {field: defaults.value(field)["code"] for field in ("branch", "dept", "job")}
    except Missing as missing:
        return _outcome("failed", missing.reason, masters=defaults.observed)
    state["masters"] = defaults.observed

    # 菜单：主菜单页（要带账套，不带会清掉会话里的账套）；调拨不记日记账都可用，调整逐个看列表页有没有「未关联日记账」的提示。
    # 判定照导入那套（`menus.resolve`：只用可用的、一个自动用、多个问一次、选过的不在了重选）
    from pearnly_core.mrerp import menus as mrerp_menus

    main = get("login/mainmenu.php", params={"comidyear": comidyear, "seldb": seldb},
               headers={"Referer": f"{base}/login/selectdb.php"})
    options = []
    for option in menus_of(main, module):
        usable = True
        if kind == ADJUST:
            usable = NOT_LINKED_TEXT not in get(f"{module}/allview.php", params={"idmenu": option["idmenu"]},  # loop-query: 这类单据的几个菜单
                                                headers=home)
        options.append({**option, "linked": usable})
    state["menus"] = {module: options}
    menu, reason = mrerp_menus.resolve(options or None, entry.get("menu"))
    if reason:
        return _outcome("failed", reason, menus=state["menus"], masters=state["masters"])
    listing = f"{base}/{module}/allview.php?idmenu={menu['idmenu']}"
    get(f"{module}/allview.php", params={"idmenu": menu["idmenu"]}, headers=home)  # 会话处在这个菜单（前缀、权限、日记账关联）

    # 会计期间（网页提交前查，服务端不查）
    if period_problem(post("component/php/checkothperiod.php", {"eleinput": be_date(entry["date"])}, listing),
                      entry["date"], today):
        return _outcome("failed", PERIOD_CLOSED, menus=state["menus"], masters=state["masters"])

    form = get(f"{module}/allform.php", headers={"Referer": listing})
    hidden = hidden_values(form)
    prefix = hidden.get("txtdoctype", "")
    number = str(entry["number"]).strip()
    docnum = f"{prefix}{number}"
    meta = {"doc_type": f"stock_{kind}", "mrerp_menu": menu.get("label") or ""}

    # 单号查重（网页提交前查，服务端不查、全年度不分菜单）：同号已在 → 先按单号回读认领（重推同一张单）
    taken = post(f"{module}/component/checkac.php", {"txtdocnum": number, "txtdoctype": prefix, "id": ""}, listing).strip()
    if taken.isdigit() and int(taken) > 0:
        found = _read_back(session, module, menu["idmenu"], docnum, home)
        if found:
            return _outcome("success", docnum=docnum, meta={**meta, "already_pushed": True, "mrerp_id": found},
                            menus=state["menus"], masters=state["masters"])
        return _outcome("failed", NUMBER_TAKEN, menus=state["menus"], masters=state["masters"])

    listbox = json.loads(post(f"{module}/component/bshlistboxdata.php", {}, listing) or "{}")
    rows, problem = merged_lines(entry)
    if problem:
        return _outcome("failed", problem, menus=state["menus"], masters=state["masters"])
    items = ids(listbox, "txtcode[]")
    prices: Dict[str, Decimal] = {}
    for row in rows:
        if kind == TRANSFER or row["qty"] < 0 or row["price"] is None:
            item = items.get(row["code"])
            if not item:
                return _outcome("failed", ITEM_UNMAPPED, menus=state["menus"], masters=state["masters"])
            answer = post(f"{module}/component/baprice.php", {"idstkmas": str(item[0]), "date": str(entry["date"])[:10]},  # loop-query: 一张单的几行
                          listing)
            try:
                prices[row["code"]] = abs(_num((json.loads(answer) or ["0"])[0]))
            except (ValueError, TypeError, IndexError):
                prices[row["code"]] = Decimal("0")
    fields, problem = form_fields(entry, hidden, listbox, head, prices)
    if problem:
        return _outcome("failed", problem, menus=state["menus"], masters=state["masters"])

    state["sent"] = True
    page = session.request("POST", f"{module}/allsave.php", data=fields,
                           headers={"Referer": f"{base}/{module}/allform.php", "Origin": base}).text or ""
    problem = saved_problem(page)
    if problem:
        if _signed_out(page):
            return _outcome("unknown", f"{problem}; signed out", meta=meta, menus=state["menus"], masters=state["masters"])
        return _outcome("failed", problem, meta=meta, menus=state["menus"], masters=state["masters"])
    found = _read_back(session, module, menu["idmenu"], docnum, home)
    if not found:
        return _outcome("unknown", READBACK_NOT_FOUND, meta=meta, menus=state["menus"], masters=state["masters"])
    return _outcome("success", docnum=docnum, meta={**meta, "mrerp_id": found}, menus=state["menus"],
                    masters=state["masters"])


def _read_back(session, module, idmenu, docnum, home) -> Optional[str]:
    """按完整单号在这个菜单的列表里找：调拨走 ``showdata.php``，调整的列表页本身按 ``searchdata`` 出（不分页）。"""
    base = session.base
    page = session.request("GET", f"{module}/allview.php", params={"idmenu": idmenu}, headers=home).text or ""
    if module == MODULES[TRANSFER]:
        page = session.request("POST", f"{module}/component/showdata.php",
                               data={"showdata_numrows": "30", "showdata_pages": "1", "searchdataval": docnum},
                               headers={"X-Requested-With": "XMLHttpRequest", "Origin": base,
                                        "Referer": f"{base}/{module}/allview.php?idmenu={idmenu}"}).text or ""
    else:
        page = session.request("GET", f"{module}/allview.php", params={"searchdata": docnum},
                               headers={"Referer": f"{base}/{module}/allview.php?idmenu={idmenu}"}).text or ""
    return listed(page, docnum)


__all__ = ["ADJUST", "MODULES", "TRANSFER", "execute", "be_date", "check", "form_fields", "hidden_values", "ids", "listed",
           "menus_of", "merged_lines", "period_problem", "saved_problem"]
