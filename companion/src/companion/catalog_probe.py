# -*- coding: utf-8 -*-
"""商品/客户目录 + 记账指纹上报(只读 · best-effort · 喂云端 catalog_resolver / posting_profile)。

- 商品(STMAS):{code=STKCOD, name=STKDES, kind}·kind = stock(STKTYP=0)| non_stock(其它)。
- 客户(ARMAS):{code=CUSCOD, name=CUSNAM, tax_id=TAXID}。
- 记账指纹:{stock_master_count, stcrd_lines, stcrd_lines_moving_stock}·供云端推断这家永续/非库存
  (动库存行占比高 = 永续)。moving = not stcrd_no_stock_impact(见 dbf_schema · MREMBAL/LOTBAL 等非零)。
- 存货科目组候选(ISACC):账套没配存货科目组是「选库存」唯一还会 escalate 的口子,云端要能就地
  渲染下拉让会计选一个,本机就得先把候选连同科目名/在用品数报上去,否则那个 escalate 是死胡同。
- 仓库(1.12.18 起,评估 docs/work/2026-11-07-cowork-express-warehouse-评估.md):代码表 ISTAB(TABTYP=21)的全部仓 + 主仓
  ISINFO.MAINLOC,{code,name,main,used}。used = 这套账 STCRD 里动库存的进货 / 销货行(POSOPR 0 / 9,Cowork 推的就是这两种)
  出现过的仓(与记账指纹同一遍扫,不多读一次)或主仓;只做调拨、领用、调整用到的仓不算(11-08 测试机 615 本副本:按全部动库存行
  算 275 本两个以上,只按进货 / 销货算 175 本)。云端只在「在用的仓」有两个以上时才让人选仓(Express 模板自带 01、02,按代码表判
  等于每家都问)。main / used 是 "1",否则不带。

全程只读;任何读档异常都吞掉、返已得部分(心跳隔离 · 绝不拖垮直写)。目录可上万条 → 限量。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

import dbf

from . import dbf_schema as S
from . import stock_lookup, stock_template
from .logger import get_logger

log = get_logger(__name__)

_MAX_CATALOG = 20000
_MAX_STCRD_SCAN = 100000  # 指纹只需算比例 · 超大账套扫够即止(防拖垮心跳)
_MAX_ISACC_CANDIDATES = 20  # 候选是给会计点的下拉,不是把整张 ISACC 搬上云
_FIXED_ASSET_ACCTYP = "F"  # ISACC.ACCTYP:S=商品/服务科目组 · F=固定资产模块自己的组


def _open_ro(account_dir, name: str):
    """只读打开账套下的 <name>.DBF(大小写兼容);不存在返 None。"""
    base = Path(account_dir)
    for cand in (f"{name}.DBF", f"{name.lower()}.dbf"):
        p = base / cand
        if p.exists():
            t = dbf.Table(str(p), codepage=S.CODEPAGE, unicode_errors=S.UNICODE_ERRORS)
            t.open(dbf.READ_ONLY)
            return t
    return None


def _gv(rec, field: str) -> str:
    try:
        v = rec[field]
    except Exception:
        return ""
    if isinstance(v, str):
        return v.strip()
    return "" if v is None else str(v)


def _iter_records(account_dir, name: str):
    """只读逐行 yield 非删除记录(统一 open / 表缺跳过 / close / finally · 三个 probe 共用)。"""
    t = _open_ro(account_dir, name)
    if t is None:
        return
    try:
        for r in t:
            if not dbf.is_deleted(r):
                yield r
    finally:
        t.close()


def probe_products(account_dir) -> Tuple[List[Dict[str, str]], int]:
    """STMAS → ([{code,name,kind}], stock_master_count)。kind 按 STKTYP 是否 "0"。"""
    out: List[Dict[str, str]] = []
    stock_n = 0
    for r in _iter_records(account_dir, "STMAS"):
        code = _gv(r, "STKCOD")
        name = _gv(r, "STKDES")
        if not code and not name:
            continue
        is_stock = str(_gv(r, "STKTYP")) == "0"
        if is_stock:
            stock_n += 1
        if len(out) < _MAX_CATALOG:
            out.append(
                {
                    "code": code,
                    "name": name,
                    "kind": "stock" if is_stock else "non_stock",
                }
            )
    return out, stock_n


def probe_customers(account_dir) -> List[Dict[str, str]]:
    """ARMAS → [{code,name,tax_id,branch}]。branch=ORGNUM 归一 5 位(0/空=总店 00000)。
    每家另带 ``srv``:这家在本账套赊销单上的服务品销项税口径串(同 probe_suppliers,11-12 晚);ARTRN 读不了就不带。"""
    from . import service_vat

    known, read = service_vat.customer_report(account_dir)
    out: List[Dict[str, str]] = []
    for r in _iter_records(account_dir, "ARMAS"):
        code = _gv(r, "CUSCOD")
        name = _gv(r, "CUSNAM")
        if not code and not name:
            continue
        out.append(
            {
                "code": code,
                "name": name,
                "tax_id": _gv(r, "TAXID"),
                "branch": S.orgnum_to_branch5(_gv(r, "ORGNUM")),
                **({"srv": known.get(code) or service_vat.NO_HISTORY} if read else {}),
            }
        )
        if len(out) >= _MAX_CATALOG:
            break
    return out


def probe_suppliers(account_dir) -> List[Dict[str, str]]:
    """APMAS → [{code,name,tax_id,branch}](同 probe_customers 口径)。

    云端复核时让会计从这里选供应商档(同税号多分店 / 名称对不上时),选一次记住;不报就只能靠小助手按名猜。
    每家另带 ``srv``:这家在本账套赊购单上的服务品进项税口径串(``service_vat``,10-29;"0" = 没有历史),服务端据此判
    写之前要不要问人;APTRN 读不了就不带(服务端当旧版,不问)。
    """
    from . import service_vat

    known, read = service_vat.supplier_report(account_dir)
    out: List[Dict[str, str]] = []
    for r in _iter_records(account_dir, "APMAS"):
        code = _gv(r, "SUPCOD")
        name = _gv(r, "SUPNAM")
        if not code and not name:
            continue
        out.append(
            {
                "code": code,
                "name": name,
                "tax_id": _gv(r, "TAXID"),
                "branch": S.orgnum_to_branch5(_gv(r, "ORGNUM")),
                **({"srv": known.get(code) or service_vat.NO_HISTORY} if read else {}),
            }
        )
        if len(out) >= _MAX_CATALOG:
            break
    return out


# Express 部门表:ISTAB 里 TABTYP='50' 的行(真账 69SINCER:原账部门码 SUTI 在这里,单头 DEPCOD 4 位)。
_DEPARTMENT_TABTYP = "50"
_MAX_DEPARTMENTS = 500


def probe_departments(account_dir) -> List[Dict[str, str]]:
    """ISTAB(TABTYP=50)→ [{code,name}]:账套设置里选一次部门,随载荷下发(`depcod`)。"""
    out: List[Dict[str, str]] = []
    for r in _iter_records(account_dir, "ISTAB"):
        if _gv(r, "TABTYP") != _DEPARTMENT_TABTYP:
            continue
        code = _gv(r, "TYPCOD")
        if not code:
            continue
        out.append({"code": code, "name": _gv(r, "TYPDES") or _gv(r, "SHORTNAM")})
        if len(out) >= _MAX_DEPARTMENTS:
            break
    return out


def probe_stcrd_fingerprint(account_dir) -> Tuple[int, int]:
    """STCRD → (stcrd_lines, stcrd_lines_moving_stock)。moving = 有真实库存影响的明细行。"""
    lines, moving, _locations = _scan_stcrd(account_dir)
    return lines, moving


# 算「在用的仓」只看这两种行:进货(0)、销货(9),同 dbf_schema 写这两种单时的 POSOPR
_TRADE_POSOPR = (S.STCRD_POSOPR_PURCHASE, S.STCRD_POSOPR)


def _scan_stcrd(account_dir) -> Tuple[int, int, set]:
    """STCRD 一遍 → (行数, 动库存的行数, 动库存的进货 / 销货行用过的仓码)。同记账指纹限量 `_MAX_STCRD_SCAN`。"""
    lines = 0
    moving = 0
    locations: set = set()
    for r in _iter_records(account_dir, "STCRD"):
        lines += 1
        if not S.stcrd_no_stock_impact(r):
            moving += 1
            code = _gv(r, "LOCCOD")
            if code and _gv(r, "POSOPR") in _TRADE_POSOPR:
                locations.add(code)
        if lines >= _MAX_STCRD_SCAN:
            break
    return lines, moving, locations


# Express 仓库代码表:ISTAB 里 TABTYP='21' 的行(写入时校验仓库码、ERP 按需读主档用的也是这一段)。
_WAREHOUSE_TABTYP = "21"
_MAX_WAREHOUSES = 200


def probe_warehouses(account_dir, used=()) -> List[Dict[str, str]]:
    """ISTAB(TABTYP=21)+ 主仓 → [{code,name,main?,used?}]。``used``:STCRD 动库存的进货 / 销货行出现过的仓码(`_scan_stcrd`)。
    主仓不在代码表里(少见)也列上、标 main / used:写入时不带仓码就是写它。"""
    main = _main_location(account_dir)
    used = set(used or ()) | ({main} if main else set())
    out: List[Dict[str, str]] = []
    seen = set()
    for r in _iter_records(account_dir, "ISTAB"):
        if _gv(r, "TABTYP") != _WAREHOUSE_TABTYP:
            continue
        code = _gv(r, "TYPCOD")
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(_warehouse(code, _plain(_gv(r, "TYPDES") or _gv(r, "SHORTNAM")), main, used))
        if len(out) >= _MAX_WAREHOUSES:
            break
    if main and main not in seen:
        out.insert(0, _warehouse(main, "", main, used))
    return out


def _main_location(account_dir) -> str:
    """主仓 ISINFO.MAINLOC(写入时没带仓库码就写它;只读、读不到回空)。"""
    for r in _iter_records(account_dir, "ISINFO"):
        return _gv(r, "MAINLOC")
    return ""


def _warehouse(code: str, name: str, main: str, used: set) -> Dict[str, str]:
    row = {"code": code, "name": name}
    if code == main:
        row["main"] = "1"
    if code in used:
        row["used"] = "1"
    return row


def probe_isacc_candidates(account_dir) -> List[Dict[str, Any]]:
    """ISACC → 存货科目组候选(全量 · 带 fit 判据)。排序:fit 优先 · used_by 降序。

    fit 直接问 stock_template.assert_inventory_account —— 判据只留一份:下拉里能选中、
    自动建档却过不了的组,等于让会计白选一次再撞同一堵墙。
    ACCTYP='F' 是固定资产模块自己的组(FA01..FA13),70EXP 全 162 条 STKTYP=0 无一挂在其下,
    放进存货下拉只会诱人选错,故不列。

    逐条复判会重扫 ISACC/GLACC(ISACC 是配置表 · 百来行,70EXP 实测 90 条 1.3 秒),
    挂在 30 分钟节流的心跳上值这个代价 —— 省这几秒不值得让判据分叉成两份。
    """
    groups = _isacc_groups(account_dir)
    if not groups:
        return []
    names = _glacc_names(account_dir)
    used = _stock_acccod_usage(account_dir)
    out = [
        {
            "code": code,
            "desc": desc,
            "inv_acc": inv,
            "inv_acc_name": names.get(inv, ""),
            "cogs_acc": cogs,
            "cogs_acc_name": names.get(cogs, ""),
            "used_by": used.get(code, 0),
            "fit": _fits_inventory(account_dir, code),
        }
        for code, desc, inv, cogs in groups
    ]
    # 稳定排序:并列时保持 ISACC 物理行序 → 同一账套每次上报的顺序一致
    out.sort(key=lambda c: (not c["fit"], -c["used_by"]))
    return out


def _isacc_groups(account_dir) -> List[Tuple[str, str, str, str]]:
    """ISACC → [(ACCCOD, 描述, 存货科目 ACCNUM01, 成本科目 ACCNUM02)],去重保持物理行序。"""
    out: List[Tuple[str, str, str, str]] = []
    seen = set()
    for r in _iter_records(account_dir, "ISACC"):
        code = _gv(r, "ACCCOD")
        if not code or code in seen or _gv(r, "ACCTYP") == _FIXED_ASSET_ACCTYP:
            continue
        seen.add(code)
        desc = _plain(_gv(r, "ACCDES") or _gv(r, "ACCDES2"))
        out.append((code, desc, _gv(r, "ACCNUM01"), _gv(r, "ACCNUM02")))
    return out


def _glacc_names(account_dir) -> Dict[str, str]:
    """科目码 → 显示名:泰文 ACCNAM 优先,空则退英文 ACCNAM2。"""
    out: Dict[str, str] = {}
    for r in _iter_records(account_dir, "GLACC"):
        num = _gv(r, "ACCNUM")
        if num:
            out[num] = _plain(_gv(r, "ACCNAM") or _gv(r, "ACCNAM2"))
    return out


def _stock_acccod_usage(account_dir) -> Dict[str, int]:
    """各 ACCCOD 下挂着多少 STKTYP=0 库存品 —— 会计据此认哪个组是这套账真在用的。"""
    out: Dict[str, int] = {}
    for r in _iter_records(account_dir, "STMAS"):
        if str(_gv(r, "STKTYP")) != stock_lookup.STOCK_TYPE:
            continue
        code = _gv(r, "ACCCOD")
        if code:
            out[code] = out.get(code, 0) + 1
    return out


def _fits_inventory(account_dir, acccod: str) -> bool:
    """这个组的存货科目过不过自动建档那关(GLACC 资产类 + 存货类名)。"""
    try:
        stock_template.assert_inventory_account(account_dir, acccod)
        return True
    except stock_template.InventoryAccountUnfit:
        return False


def _plain(text: str) -> str:
    """名称里混着 NBSP(Express 录入残留)→ 归一成普通空格,免得云端按空格切词搜不着。"""
    return (text or "").replace("\xa0", " ").strip()


def _catalog_masters(
    account_dir,
) -> Tuple[List[Dict[str, str]], List[Dict[str, str]], int]:
    products: List[Dict[str, str]] = []
    customers: List[Dict[str, str]] = []
    stock_n = 0
    try:
        products, stock_n = probe_products(account_dir)
    except Exception:
        log.exception("probe_products failed: %s", account_dir)
    try:
        customers = probe_customers(account_dir)
    except Exception:
        log.exception("probe_customers failed: %s", account_dir)
    return products, customers, stock_n


def _optional(probe, account_dir) -> List[Dict[str, str]]:
    try:
        return probe(account_dir)
    except Exception:
        log.exception("%s failed: %s", probe.__name__, account_dir)
        return []


def master_catalog_payload(account_dir) -> Dict[str, Any]:
    """Read only fields that can change a new-document master selection."""
    products, customers, _ = _catalog_masters(account_dir)
    return {"products": products, "customers": customers,
            "suppliers": _optional(probe_suppliers, account_dir),
            "departments": _optional(probe_departments, account_dir)}


def catalog_payload(account_dir) -> Dict[str, Any]:
    """best-effort:{products, customers, suppliers, departments, warehouses, fingerprint, isacc_candidates}。逐块隔离,
    任一失败不影响其余;仓库读坏了不带这个键(云端当旧版、照写主仓),不报空表冒充「没有仓」。"""
    products, customers, stock_n = _catalog_masters(account_dir)
    lines = moving = 0
    locations: set = set()
    stcrd_read = False
    try:
        lines, moving, locations = _scan_stcrd(account_dir)
        stcrd_read = True
    except Exception:
        log.exception("probe_stcrd_fingerprint failed: %s", account_dir)
    warehouses = None
    if stcrd_read:
        try:
            warehouses = probe_warehouses(account_dir, locations)
        except Exception:
            log.exception("probe_warehouses failed: %s", account_dir)
    candidates: List[Dict[str, Any]] = []
    try:
        ranked = probe_isacc_candidates(account_dir)
        # 合格的在前、不合格的也报（带 fit 标记，1.12.23）：服务端自动定组只认合格的；人在卡片上可以明确选不合格的，CC 照写
        # （`stock_template.GUARD_CHOSEN_GROUP`）。以前只报合格的
        candidates = ranked[:_MAX_ISACC_CANDIDATES]
        # 两个数都记:可选 0 个时,日志要能分清「没配存货科目组」和「这块压根没跑」
        log.info(
            "[catalog] 存货科目组 %d 个 · 可选 %d 个", len(ranked), len(candidates)
        )
    except Exception:
        log.exception("probe_isacc_candidates failed: %s", account_dir)
    return {
        "products": products,
        "customers": customers,
        "suppliers": _optional(probe_suppliers, account_dir),
        "departments": _optional(probe_departments, account_dir),
        **({"warehouses": warehouses} if warehouses is not None else {}),
        "fingerprint": {
            "stock_master_count": stock_n,
            "stcrd_lines": lines,
            "stcrd_lines_moving_stock": moving,
        },
        "isacc_candidates": candidates,
    }
