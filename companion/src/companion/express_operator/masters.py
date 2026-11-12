# -*- coding: utf-8 -*-
"""代录开工前建主档（主会话 10-27 ③）：只建这张单要的对方档与商品档，单据仍由代录在 Express 界面里录。

直写的建档原样复用、一行不改，这里只是多一个调用方：
- 查找、疑似重复、建档、读回都是直写那几个函数（``master_create`` 的对方档、``product_master`` 的库存品 / 非库存品），
  口径一致：先按税号 / 名称找，找到就用；疑似重复不建、交人；人在预检里选了「新建」（``confirmed_new``）才跳过找；
- 写前同直写三道闸（账套名、写盘目录、账套公司），写前选择性备份，建档、重建索引（CDX 重建程序）、读回任一步失败
  整笔还原，账套被占用按「等账套」回报；只碰主档表（APMAS / ARMAS / STMAS / ISACC），不碰交易表与 ISRUN；
- 账套锁由写账线拿着（与直写同一把 PEARNLY.LCK）。

主档索引要独占重建：Express 开着这本账套时建不了。所以要建档时先让宿主正常退出这一批的 Express（``before_create``），
建完下一张再开；批里第一张在开 Express 之前就建好。建好的码写回载荷（``supplier`` / ``customer`` 的 ``code``、
明细行的 ``code``），代录照码录。直接科目行（``direct_account``，费用单的明细）：直写按科目记、不建档；但 Express 界面里录明细
一定要商品码，会计手录费用单用的是挂在费用科目上的服务品——这里照直写建非库存品的同一个函数（``ensure_item``）按这一行的科目建
（10-27 实机 PDM-INV-0926-018-OP 停在「Express 中没有此商品」）。
用到我们以前建的、形状不全的服务品（单位空、换算系数 0，Express 进货单按码查不到）时，在同一笔事务里只补空字段
（``product_master.shape_gaps`` / ``repair_shape``，10-28 主会话定）；不是我们建的主档不动。
"""
from __future__ import annotations

import copy
import datetime as _dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

PURCHASE_TYPES = ("RR", "HP")
SALES_TYPES = ("IV", "HS")
STOCK_MODES = ("stock_item", "stock_sale")
NON_STOCK_MODE = "non_stock_item"
MASTER_TABLES = ("APMAS", "ARMAS", "STMAS", "ISACC")
# 没开「按商品记增值税」的账套里免税、应税混着的单：明细免税码录不进去，交人
ERR_EXEMPT_MIXED_CLOSED = "OPERATOR_EXEMPT_LINES_NEED_PER_ITEM_VAT"


@dataclass
class Prepared:
    """``ok``：可以交给代录（``payload`` 已带上找到 / 建好的码）；否则 ``error_code`` / ``error`` / ``outcome`` 照直写回执。"""
    ok: bool
    payload: Dict
    created: List[Dict] = field(default_factory=list)  # [{"kind": party / item, "code", "name"}]
    error_code: str = ""
    error: str = ""
    outcome: Optional[str] = None


@dataclass
class _Plan:
    side: str
    entry: object
    party_code: str = ""
    party_create: bool = False
    lines: List[Dict] = field(default_factory=list)  # [{"index", "mode", "name", "unit", "account", "code"}]

    @property
    def creates(self) -> bool:
        return self.party_create or any(not line["code"] or line.get("repair") for line in self.lines)


def _side(payload: Dict) -> str:
    doc_type = str(payload.get("doctype") or "").strip().upper()
    return "purchase" if doc_type in PURCHASE_TYPES else "sales" if doc_type in SALES_TYPES else ""


def _per_item_vat(account_dir: Path) -> bool:
    """这本账套开没开「按商品记增值税」（ISINFO.VAT_INITEM = Y，Express 一般设置第 8 项）。读不出当没开。"""
    from ..account_rules import _first

    try:
        info = _first(account_dir, "ISINFO")
    except Exception:  # noqa: BLE001 — 读不出当没开：应税行照旧不看税码
        return False
    return info is not None and str(info.get("VAT_INITEM") or "").strip().upper() == "Y"


def _plan(payload: Dict, account_dir: Path, side: str) -> _Plan:
    """只读：这张单的对方档与明细商品在不在、哪些要建。同税号多档定不出唯一抛 ``AmbiguousPartyMatch``；疑似重复回 None。"""
    from .. import master_create, product_master
    from ..purchase_adapter import build_purchase_entry
    from ..sales_adapter import build_sales_entry

    if side == "purchase":
        entry = build_purchase_entry(payload)
        table, column = "APMAS", "SUPCOD"
        code, name, tax, branch = entry.supplier_code, entry.supplier_name, entry.supplier_tax_id, entry.supplier_branch
        confirmed, find, dup = (entry.supplier_confirmed_new, master_create.find_supplier_code,
                                master_create.suspected_supplier_dup)
    else:
        entry = build_sales_entry(payload)
        table, column = "ARMAS", "CUSCOD"
        code, name, tax, branch = entry.customer_code, entry.customer_name, entry.customer_tax_id, entry.customer_branch
        confirmed, find, dup = (entry.customer_confirmed_new, master_create.find_customer_code,
                                master_create.suspected_customer_dup)
    plan = _Plan(side, entry)
    if code:
        plan.party_code = code
        # 现金客户：直写在账套里没有这个码时会认这套账自己的现金客户或新建（ensure_customer）；其余给了码的照码
        plan.party_create = (side == "sales" and code == master_create.CASH_CUSTOMER
                             and not master_create._code_exists(account_dir, table, column, code))
    elif name:
        found = "" if confirmed else (find(account_dir, tax, name, branch=branch) or "")
        if found:
            plan.party_code = found
        elif not confirmed and dup(account_dir, entry):
            return None
        else:
            plan.party_create = True
    per_item_vat = _per_item_vat(account_dir)
    for index, item in enumerate(payload.get("items") or []):
        mode = str(item.get("item_mode") or "").strip()
        if mode not in STOCK_MODES:
            mode = NON_STOCK_MODE  # 非库存品与直接科目行都按科目建服务品（见文件头）
        given = str(item.get("code") or "").strip()
        if given:
            gaps = product_master.shape_gaps(account_dir, given) if mode == NON_STOCK_MODE else {}
            if gaps:  # 人选的是我们以前建的、形状不全的服务品：照样只补空字段
                plan.lines.append({"index": index, "mode": mode, "name": "", "unit": "", "account": "", "code": given,
                                   "repair": gaps})
            continue
        item_name = str(item.get("name") or "").strip()
        if not item_name:
            continue
        # 免税行落到免税的商品（VATCOD '0'）：服务品、库存品都一样（10-29 语料）。开了「按商品记增值税」的账套，明细的
        # 税码由商品主档带出，应税行也不能落到同名的免税品上（10-29 TEST2 实测 RR0000088）；没开的账套商品税码不起作用，
        # 应税行照旧不看税码（不为它多建一份商品）
        exempt = True if item.get("vat_exempt") is True else (False if per_item_vat else None)
        found = (product_master.find_stock_item_code(account_dir, item_name, exempt=exempt) if mode in STOCK_MODES
                 else product_master.find_item_code(account_dir, item_name, exempt=exempt))
        account = str(item.get("account") or entry.items_account or "")
        if mode == NON_STOCK_MODE and not account and not found:
            continue  # 没有科目可挂：不建，代录预检照旧报「商品主档没有」
        repair = product_master.shape_gaps(account_dir, found) if found and mode == NON_STOCK_MODE else {}
        plan.lines.append({"index": index, "mode": mode, "name": item_name, "unit": str(item.get("unit") or ""),
                           "account": account, "code": found or "", "repair": repair, "exempt": exempt,
                           # 载荷 21 的行键（版本 ≤ 20 的行上没有，guided.items 已去掉）：这一行自己的科目组盖过整单的，分类、允许负库存照给的
                           "acccod": str(item.get("acccod") or ""), "stkgrp": str(item.get("stkgrp") or ""),
                           "negallow": str(item.get("negallow") or "")})
    return plan


def _dup_error(side: str) -> str:
    from .. import dbf_sales, dbf_writer

    return dbf_writer.ERR_SUPPLIER_DUP if side == "purchase" else dbf_sales.ERR_CUSTOMER_DUP


def _ambiguous_error(side: str) -> str:
    from .. import dbf_sales, dbf_writer

    return dbf_writer.ERR_SUPPLIER_AMBIGUOUS if side == "purchase" else dbf_sales.ERR_CUSTOMER_AMBIGUOUS


def _create(plan: _Plan, account_dir: Path, docdat: _dt.date, userid: str) -> List[Dict]:
    """在备份-还原事务里建：对方档 → 商品档 → 重建这几张主档表的索引 → 回查。任一步抛错由调用方还原。"""
    from .. import cdx_reindex, dbf_writer, master_create, product_master

    created, touched = [], set()
    if plan.party_create:
        if plan.side == "purchase":
            plan.party_code = master_create.ensure_supplier(account_dir, plan.entry, created_date=docdat, userid=userid)
            touched.add("APMAS")
        else:
            plan.party_code = master_create.ensure_customer(account_dir, plan.entry, created_date=docdat, userid=userid)
            touched.add("ARMAS")
        created.append({"kind": "party", "code": plan.party_code})
    for line in plan.lines:
        if line["code"] and line.get("repair"):
            # 我们以前建的服务品单位空 / 换算系数 0（Express 进货单按码查不到）：只补空字段（10-28 主会话定）
            product_master.repair_shape(account_dir, line["code"], line["repair"])
            touched.add("STMAS")
            created.append({"kind": "repaired", "code": line["code"]})
            continue
        if line["code"]:
            continue
        if line["mode"] in STOCK_MODES:
            made = product_master.ensure_stock_item(account_dir, line["name"], userid=userid, unit=line["unit"],
                                                    acccod=line.get("acccod") or getattr(plan.entry, "stock_acccod", ""),
                                                    exempt=line.get("exempt"), stkgrp=line.get("stkgrp", ""),
                                                    negallow=line.get("negallow", ""))
            line["code"] = made.stkcod
        else:
            line["code"] = product_master.ensure_item(account_dir, line["name"], line["account"], created_date=docdat,
                                                      userid=userid, unit=line["unit"], exempt=line.get("exempt"))
            touched.add("ISACC")  # 非库存品可能连带建了科目映射
        touched.add("STMAS")
        created.append({"kind": "item", "code": line["code"], "name": line["name"]})
    if touched:
        # 建档函数里的重建对 Express 的 CDX 不起作用，照直写用随包的重建程序重建（Express 才查得到）；Express 开着这张表 → 等账套
        dbf_writer._reindex(account_dir, tuple(sorted(touched)))
        if "STMAS" in touched and cdx_reindex.runner_available():
            table, tag = cdx_reindex.TAG_STMAS_CODE
            for item in created:
                if item["kind"] in ("item", "repaired") and not cdx_reindex.seek(account_dir, table, tag, item["code"]).found:
                    raise dbf_writer._WriteError(f"STMAS index lookup missed {item['code']}", dbf_writer.ERR_INDEX)
    return created


def _with_codes(payload: Dict, plan: _Plan) -> Dict:
    out = copy.deepcopy(payload)
    if plan.party_code:
        key = "supplier" if plan.side == "purchase" else "customer"
        out[key] = {**(out.get(key) or {}), "code": plan.party_code}
    for line in plan.lines:
        if line["code"]:
            out["items"][line["index"]]["code"] = line["code"]
    return out


def prepare(payload: Dict, settings, *, before_create: Callable[[], None] = lambda: None) -> Prepared:
    """代录前把这张单要的主档备齐。``settings`` 是写账线选好的账套（目录、账套名、公司），``before_create`` 在真要建档前调
    （宿主借此退出这一批开着的 Express）。不认识的单种、什么都不用建：原样或只补上找到的码，不碰账套。"""
    from .. import dbf_writer
    from .. import dbf_schema as S
    from .. import master_create, stock_template
    from ..consistency import account_company_matches
    from ..purchase_adapter import allowed_account_sets, validate_purchase_payload
    from ..sales_adapter import validate_sales_payload

    side = _side(payload)
    if not side:
        return Prepared(True, payload)
    account_dir = Path(settings.account_dir)
    from .. import guided

    if guided.uses(payload):
        # 载荷 21 的四个行键（单位码、科目组、分类、允许负库存；写账线只放这四个进来，11-12 晚）：值先照直写同一套核
        #（不在这本账套里 → 整单交人，不回落众数），再照直写把 unit_code 换进 unit（建档、录单都按这个码）
        refusal = guided.invalid_values(account_dir, payload, guided.STOCK_IN if side == "purchase" else guided.STOCK_OUT,
                                        "supplier" if side == "purchase" else "customer")
        if refusal is not None:
            return Prepared(False, payload, error_code=refusal.code, error=refusal.detail, outcome="needs_review")
        payload = {**payload, "items": guided.items(payload)}
    items = [it for it in payload.get("items") or [] if isinstance(it, dict)]
    exempt = [it for it in items if it.get("vat_exempt") is True]
    if exempt and not _per_item_vat(account_dir):
        # 没开「按商品记增值税」的账套：明细的免税码 F 栏在界面上录不进去（10-30 实机：右键「แก้ไข」、Tab、点格都进不了，
        # 价格类型 2 下免税商品的明细税码也是空的）。整单免税照语料另一种原生记法「0 - ไม่มี VAT」录（明细不标免税、税额 0）；
        # 免税、应税混着的交人（主会话 10-30：关闭模式免税行只能整单处理或交人）
        if len(exempt) < len(items):
            return Prepared(False, payload, error_code=ERR_EXEMPT_MIXED_CLOSED,
                            error="exempt and taxable lines in a book without per-item VAT", outcome="needs_review")
        payload = {**payload, "items": [{**it, "vat_exempt": False} for it in items]}
    try:
        plan = _plan(payload, account_dir, side)
    except master_create.AmbiguousPartyMatch as exc:
        return Prepared(False, payload, error_code=_ambiguous_error(side), error=str(exc), outcome="needs_review")
    if plan is None:
        return Prepared(False, payload, error_code=_dup_error(side), error="dup", outcome="needs_review")
    if not plan.creates:
        return Prepared(True, _with_codes(payload, plan))
    # 同直写的三道闸：账套名在这台电脑选的账套里、写盘目录就是选的那个、目录里的公司就是选的公司
    validate = validate_purchase_payload if side == "purchase" else validate_sales_payload
    checked = validate(payload, allowed=allowed_account_sets(settings.account_set))
    if not checked.ok:
        return Prepared(False, payload, error_code=dbf_writer.ERR_VALIDATION,
                        error=f"{checked.error_code}: {checked.detail}", outcome="needs_review")
    if S.path_ambiguity(account_dir) or not S.account_dir_allowed(account_dir, settings.account_dir):
        return Prepared(False, payload, error_code=dbf_writer.ERR_ACCOUNT_DIR, error=str(account_dir),
                        outcome="needs_review")
    ok, reason = account_company_matches(account_dir, settings.account_company)
    if not ok:
        return Prepared(False, payload, error_code=dbf_writer.ERR_COMPANY, error=reason, outcome="needs_review")
    before_create()
    docdat = S.be_yymmdd_to_greg(str(payload["docdate_be"]))
    backup = dbf_writer._backup(account_dir, MASTER_TABLES)
    try:
        from .. import writer_id

        created = _create(plan, account_dir, docdat, writer_id.resolve(account_dir))  # 建档人同直写：授权的 Express 账户名
    except Exception as exc:  # noqa: BLE001 — 任一步失败：主档表整笔还原，照直写的错误码回执
        dbf_writer._restore(account_dir, backup)
        dbf_writer._discard_backup(backup)
        code = (stock_template.ERR_STOCK_GROUP if isinstance(exc, stock_template.InventoryAccountUnfit)
                else dbf_writer._error_code(exc))
        outcome = "waiting_lock" if code == dbf_writer.ERR_WAITING_LOCK else "needs_review"
        return Prepared(False, payload, error_code=code, error=str(exc)[:300], outcome=outcome)
    dbf_writer._discard_backup(backup)
    return Prepared(True, _with_codes(payload, plan), created=created)
