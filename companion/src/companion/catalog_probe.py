# -*- coding: utf-8 -*-
"""商品/客户目录 + 记账指纹上报(只读 · best-effort · 喂云端 catalog_resolver / posting_profile)。

- 商品(STMAS):{code=STKCOD, name=STKDES, kind}·kind = stock(STKTYP=0)| non_stock(其它)。
- 客户(ARMAS):{code=CUSCOD, name=CUSNAM, tax_id=TAXID}。
- 记账指纹:{stock_master_count, stcrd_lines, stcrd_lines_moving_stock}·供云端推断这家永续/非库存
  (动库存行占比高 = 永续)。moving = not stcrd_no_stock_impact(见 dbf_schema · MREMBAL/LOTBAL 等非零)。
- 存货科目组候选(ISACC):账套没配存货科目组是「选库存」唯一还会 escalate 的口子,云端要能就地
  渲染下拉让会计选一个,本机就得先把候选连同科目名/在用品数报上去,否则那个 escalate 是死胡同。

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
    """ARMAS → [{code,name,tax_id,branch}]。branch=ORGNUM 归一 5 位(0/空=总店 00000)。"""
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
            }
        )
        if len(out) >= _MAX_CATALOG:
            break
    return out


def probe_suppliers(account_dir) -> List[Dict[str, str]]:
    """APMAS → [{code,name,tax_id,branch}](同 probe_customers 口径)。

    云端复核时让会计从这里选供应商档(同税号多分店 / 名称对不上时),选一次记住;不报就只能靠小助手按名猜。
    """
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
    lines = 0
    moving = 0
    for r in _iter_records(account_dir, "STCRD"):
        lines += 1
        if not S.stcrd_no_stock_impact(r):
            moving += 1
        if lines >= _MAX_STCRD_SCAN:
            break
    return lines, moving


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
    """best-effort:{products, customers, fingerprint, isacc_candidates}。逐块隔离,任一失败不影响其余。"""
    products, customers, stock_n = _catalog_masters(account_dir)
    lines = moving = 0
    try:
        lines, moving = probe_stcrd_fingerprint(account_dir)
    except Exception:
        log.exception("probe_stcrd_fingerprint failed: %s", account_dir)
    candidates: List[Dict[str, Any]] = []
    try:
        ranked = probe_isacc_candidates(account_dir)
        # 只报能选的:不合格的组选中了照样被建档那关挡下,搬上云只是让人白点一次
        candidates = [c for c in ranked if c["fit"]][:_MAX_ISACC_CANDIDATES]
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
        "fingerprint": {
            "stock_master_count": stock_n,
            "stcrd_lines": lines,
            "stcrd_lines_moving_stock": moving,
        },
        "isacc_candidates": candidates,
    }
