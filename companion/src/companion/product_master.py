# -*- coding: utf-8 -*-
r"""V2 非库存商品主档 find-or-create(STCRD 明细行引用主档前置)。

V1 把明细当"直接科目行"(STKCOD=账码)写。V2 默认走 Express 原生的非库存商品主档:
匹配同名非库存主档(有就复用),没有则**从同账套真实非库存主档克隆结构**新建,
拿到 STKCOD 再写 STCRD。账务正确性不依赖主档(GL 由 companion 显式写 GLJNLIT),
主档只为"商品明细可见 + 复用匹配 + Express 报表归类",故建档失败可回落 direct_account。

逆向真账(\\accserver\ACCOUNT\70EXP\test · doc31):
- 非库存主档 STKTYP∈{3,4,5}(0=真库存·禁默认建)· `ACCCOD→ISACC.ACCNUM01` 联动科目 ·
  ISSAL/ISPUR 是空字符(非准入开关,不按方向筛模板)。
- 字段禁硬编码:STKTYP/STKGRP/QUCOD/VATCOD/STKLEV/NEGALLOW… 全从同账套模板克隆,
  仅覆写品名/编码/ACCCOD/审计。列宽同理从表头现取(dbf_meta),不写死数字。

幂等:按归一品名精确匹配,命中即复用,绝不重复建。建档在 writer 备份-回滚事务内调用
(失败抛错整体还原)。品名口径在 stock_lookup、单位码在 unit_code、库存模板与存货科目
守门在 stock_template —— 本模块只管"建档"这件事本身。
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

import dbf

from . import dbf_meta
from . import dbf_schema as S
from . import stock_defaults
from . import stock_lookup
from . import stock_template
from . import unit_code
from . import writer_id
from .logger import get_logger

log = get_logger(__name__)

_NONSTOCK_TYPES = stock_lookup.NONSTOCK_TYPES  # 匹配既存主档时认这几种非库存型
# 新建商品一律用 3(=สินค้าบริการ 服务型)。Express 三个商品主档列表屏里,只有 3 型会显示在
# [4.สินค้า→รายละเอียดสินค้าบริการ];4/5 型(账套里的费用项/特殊型)虽落进 STMAS、单据也能引用,
# 但在任何商品列表屏都隐身——克隆模板恰好撞上 5 型费用项时,商品就"在库却查不到"。故 STKTYP 不
# 跟模板,强制 3,确保 Owner 能在商品列表看到/管理自动建的商品。
_VISIBLE_NONSTOCK_TYPE = "3"

# 克隆要照搬的结构字段(禁硬编码·全取自模板)。STKTYP 不在内(强制 _VISIBLE_NONSTOCK_TYPE),
# 品名/编码/ACCCOD/审计另行覆写。
_CLONE_FIELDS = (
    "STKGRP",
    "QUCOD",
    "CQUCOD",
    "CFACTOR",
    "PQUCOD",
    "PFACTOR",
    "SQUCOD",
    "SFACTOR",
    "VATCOD",
    "STKLEV",
    "STKCLASS",
    "NEGALLOW",
    "ISINV",
    "ISSAL",
    "ISPUR",
)

_STKCOD_PREFIX = "PN"  # Pearnly 非库存自动建档码前缀(与账码样式/泰文名码不撞)


def _open(account_dir: Path, name: str, mode):
    t = dbf.Table(str(account_dir / f"{name}.DBF"), codepage=S.CODEPAGE, unicode_errors=S.UNICODE_ERRORS)
    t.open(mode)
    return t


def _gv(rec, field, default=""):
    try:
        v = rec[field]
    except Exception:
        return default
    return v.strip() if isinstance(v, str) else v


def find_item_code(account_dir: Path, name: str, *, exempt: Optional[bool] = None) -> Optional[str]:
    """只读:同名非库存主档(STKTYP∈3/4/5)的 STKCOD,归一精确匹配。命中返其码。

    落档形与比对键都走 stock_lookup(列宽从表头现取),与库存侧共用同一口径 —— 建档侧和
    卖货侧曾各截各的,长名于是永远比不中,重复建档 + 销售死锁。
    ``exempt``:``True`` 只认免税的(VATCOD '0')、``False`` 只认不是免税的——开了「按商品记增值税」的账套,Express 的单据行
    按商品主档的税码记免税,免税行不能落到同名的应税品上,应税行也不能落到同名的免税品上(10-29 真实语料与 TEST2 实测
    RR0000088)。不给(``None``)时照旧,不看税码。
    """
    if exempt is None:
        return stock_lookup.first_code(account_dir, name, _NONSTOCK_TYPES)
    return _first_by_vatcod(stock_lookup.find_rows(account_dir, name, _NONSTOCK_TYPES), exempt)


def _first_by_vatcod(rows, exempt: bool) -> Optional[str]:
    return next((row["stkcod"] for row in rows if row["stkcod"] and (row.get("vatcod") == EXEMPT_VATCOD) == exempt), None)


def _pick_template(account_dir: Path) -> Optional[Dict]:
    """取同账套一条真实非库存主档作克隆模板(只读)。无则 None(调用方兜底)。"""
    t = _open(account_dir, "STMAS", dbf.READ_ONLY)
    try:
        for r in t:
            if dbf.is_deleted(r) or str(_gv(r, "STKTYP")) not in _NONSTOCK_TYPES:
                continue
            return {f: _gv(r, f) for f in _CLONE_FIELDS}
        return None
    finally:
        t.close()


def _all_values(account_dir: Path, table: str, field: str) -> List[str]:
    """某列已用过的全部值(**含软删行**)—— 只给 _next_stkcod / _next_acccod 发新码用。

    删掉的商品 PN00007 在旧库存卡、旧单据明细上仍是这个码;跳过软删行发号就是把它再发一次。
    查商品/科目"还在不在"另有 find_item_code / _find_acccod(都过 dbf.is_deleted)。
    """
    t = _open(account_dir, table, dbf.READ_ONLY)
    try:
        return [(_gv(r, field) or "") for r in t if _gv(r, field)]
    finally:
        t.close()


def _next_stkcod(account_dir: Path) -> str:
    """生成不与现有 STKCOD 冲突的自动建档码(PN+5位序号·C(20) 容得下)。"""
    seen = set(_all_values(account_dir, "STMAS", "STKCOD"))
    n = 1
    while f"{_STKCOD_PREFIX}{n:05d}" in seen:
        n += 1
    return f"{_STKCOD_PREFIX}{n:05d}"


def _find_acccod(account_dir: Path, target_account: str) -> Optional[str]:
    """ISACC 里 ACCNUM01==目标点号账码 的 ACCCOD(主档科目联动键)。"""
    tgt = (target_account or "").strip()
    if not tgt:
        return None
    t = _open(account_dir, "ISACC", dbf.READ_ONLY)
    try:
        for r in t:
            if dbf.is_deleted(r):
                continue
            if _gv(r, "ACCNUM01") == tgt:
                code = _gv(r, "ACCCOD")
                if code:
                    return code
        return None
    finally:
        t.close()


def _fit_isacc(account_dir: Path, field: str, desc: str) -> str:
    """科目说明按 ISACC 该列的真实宽度裁(真表 C(50)·合成表曾是 C(40),写死必有一头对不上)。"""
    return dbf_meta.fit(desc, dbf_meta.field_width(account_dir, "ISACC", field))


def _next_acccod(account_dir: Path) -> str:
    """生成不冲突的 ACCCOD(C(4))。Pxx 段·与现有 00NN/003X/STxx 不撞。"""
    seen = set(_all_values(account_dir, "ISACC", "ACCCOD"))
    n = 1
    while f"P{n:03d}" in seen:
        n += 1
    return f"P{n:03d}"


def _ensure_acccod(
    account_dir: Path, target_account: str, desc: str, created: _dt.date, userid: str
) -> str:
    """find-or-create 目标科目的 ACCCOD。无则建 ISACC 映射(METHOD='-' 简单型)。
    ACCNUM01=成本科目(บัญชีต้นทุน)、ACCNUM03=收入科目(บัญชีรายได้),均=目标账码——照搬
    账套原生服务项的写法(两者同账码),否则商品主档"收入科目"列空白。reindex + 读回。"""
    found = _find_acccod(account_dir, target_account)
    if found:
        return found
    code = _next_acccod(account_dir)
    acc = target_account.strip()
    t = _open(account_dir, "ISACC", dbf.READ_WRITE)
    try:
        t.append(
            {
                "ACCTYP": "S",
                "ACCCOD": code,
                "ACCDES": _fit_isacc(account_dir, "ACCDES", desc),
                "ACCDES2": _fit_isacc(account_dir, "ACCDES2", desc),
                "METHOD": "-",
                "ACCNUM01": acc,  # 成本科目
                "ACCNUM03": acc,  # 收入科目(บัญชีรายได้)·缺它则主档收入科目列空
                "USERID": userid,
                "CHGDAT": created,
            }
        )
    finally:
        t.close()
    _reindex(account_dir, "ISACC")
    if _find_acccod(account_dir, target_account) != code:
        raise ValueError(f"ISACC {code}→{target_account} readback failed")
    return code


def _reindex(account_dir: Path, table: str) -> None:
    t = _open(account_dir, table, dbf.READ_WRITE)
    try:
        t.reindex()
    finally:
        t.close()


# 服务品的形状(Express 原生服务品逐字段核对,10-28):商品组有值、三种单位一致、进货 / 销货换算系数 1
EXEMPT_VATCOD = "0"  # STMAS / STCRD 的免税税码
NORMAL_VATCOD = "1"  # 正常税率（Express 商品「ประเภท VAT」：ได้รับยกเว้นVAT = '0'，อัตราปกติ = '1'，ไม่ระบุ = 空）
_SHAPE_UNITS = ("QUCOD", "CQUCOD", "PQUCOD", "SQUCOD")
_SHAPE_FACTORS = ("PFACTOR", "SFACTOR")


def _shape_defaults(account_dir: Path, unit: str = "") -> Dict:
    """没有非库存品可抄时的结构字段:单位按票面(解析成本账套合法码),没有就取本账库存品的众数;商品组取本账库存品的
    众数(同库存品建档 `stock_defaults`);进货 / 销货换算系数 1。取不到的单位、商品组留空,不编。"""
    defaults = stock_defaults.read(account_dir)
    row: Dict = {f: 1.0 for f in _SHAPE_FACTORS}
    qucod = unit_code.resolve(account_dir, unit) or defaults.qucod
    if qucod:
        row.update({f: qucod for f in _SHAPE_UNITS})
    if defaults.stkgrp:
        row["STKGRP"] = defaults.stkgrp
    return row


def shape_gaps(account_dir: Path, stkcod: str) -> Dict:
    """我们建的(CREBY = PEARNLY)非库存品缺的形状字段 → 该补成什么;不是我们建的、不缺的回空(不动人家的主档)。只读。"""
    t = _open(account_dir, "STMAS", dbf.READ_ONLY)
    try:
        row = next((r for r in S.live_rows(t) if (_gv(r, "STKCOD") or "").strip() == stkcod), None)
        if row is None or str(_gv(row, "STKTYP")) not in _NONSTOCK_TYPES \
                or (_gv(row, "CREBY") or "").strip().upper() != S.PEARNLY_USERID.upper():
            return {}
        have = {f: _gv(row, f) for f in ("STKGRP",) + _SHAPE_UNITS + _SHAPE_FACTORS}
    finally:
        t.close()
    return _fill_shape(account_dir, have)


def _fill_shape(account_dir: Path, row: Dict, unit: str = "") -> Dict:
    """``row`` 缺的形状字段(空 / 系数 0)→ 该补成什么。已有基本单位时其余单位列跟它走,不混进本账众数单位。"""
    wanted = _shape_defaults(account_dir, unit)
    if row.get("QUCOD"):
        wanted.update({f: row["QUCOD"] for f in _SHAPE_UNITS})
    return {f: v for f, v in wanted.items() if not row.get(f)}


def repair_shape(account_dir: Path, stkcod: str, gaps: Dict) -> None:
    """只把 ``gaps``(`shape_gaps` 的结果)写进这一条主档。已在备份-回滚事务内调用;索引由调用方重建。"""
    if not gaps:
        return
    t = _open(account_dir, "STMAS", dbf.READ_WRITE)
    try:
        for r in S.live_rows(t):
            if (_gv(r, "STKCOD") or "").strip() == stkcod:
                with r as rec:
                    for field, value in gaps.items():
                        rec[field] = value
                return
    finally:
        t.close()


def _apply_unit(account_dir: Path, row: Dict, unit: str) -> None:
    """按账套 ISTAB 把来件单位解析成合法 QUCOD 再覆写;解析不出就保留模板克隆来的单位。

    此前直接写 `unit[:2]`,把 `ถัง` 写成 `ถั` —— 该账套 39 个合法单位码里没有这个,主档单位
    对 Express 就是个无效值,而同商品 STCRD.TQUCOD 走映射写的是 `ถง`,主档与卡片自相矛盾。
    """
    code = unit_code.resolve(account_dir, unit)
    if code:
        # 四列一起写（同库存品建档、同 Express 原生服务品）：只改 QUCOD 时进货 / 销货单位还是模板那一行的，主档自相矛盾
        row.update({f: code for f in _SHAPE_UNITS})


def verify_item_row(account_dir: Path, stkcod: str) -> Optional[str]:
    """读回新建主档:码在 + STKTYP 是非库存。ok 返 None;否则返错误描述。

    只看活行 —— 同码的软删旧档会让校验读到旧的 STKTYP,把新建的档判错(或放过错的)。
    """
    t = _open(account_dir, "STMAS", dbf.READ_ONLY)
    try:
        for r in S.live_rows(t):
            if _gv(r, "STKCOD") == stkcod:
                typ = str(_gv(r, "STKTYP"))
                if typ not in _NONSTOCK_TYPES:
                    return f"STMAS {stkcod} STKTYP={typ!r} 非非库存(禁默认建库存)"
                return None
        return f"STMAS row {stkcod} not found after create"
    finally:
        t.close()


def ensure_item(
    account_dir: Path,
    name: str,
    target_account: str,
    *,
    created_date: _dt.date,
    userid: str = S.PEARNLY_USERID,
    unit: str = "",
    vatcod: str = "",
    exempt: Optional[bool] = None,
) -> str:
    """find-or-create 非库存商品主档,返回 STKCOD。已在备份-回滚事务内调用。

    禁硬编码:STKTYP/STKGRP/QUCOD/VATCOD… 从同账套模板克隆;仅覆写品名/编码/ACCCOD/审计。
    target_account=点号账码(销项→收入 / 采购→采购或费用·调用方按方向给)。
    """
    nm = (name or "").strip()
    if not nm:
        raise ValueError("ensure_item: 空品名")

    found = find_item_code(account_dir, nm, exempt=exempt)
    if found:
        return found
    if exempt:
        vatcod = EXEMPT_VATCOD  # 免税行要的服务品：建成免税的

    tpl = _pick_template(account_dir)
    acccod = _ensure_acccod(account_dir, target_account, nm, created_date, userid)
    stkcod = _next_stkcod(account_dir)

    # 克隆结构(禁硬编码)。只取非空值,空字段交 dbf 用列默认(避免把 "" 塞进数值列)。
    # 账里一个非库存品都没有可抄时补齐 Express 原生服务品必有的形状(_shape_defaults):10-27 实机试点账套
    # 只有库存品,建出的服务品单位空、换算系数 0,Express 进货单按码查不到它。
    # 抄来的模板自己也常缺商品组、换算系数为 0(10-28 百本回归):同样按原生形状只补空的,建出来就完整。
    row = {f: tpl[f] for f in _CLONE_FIELDS if tpl.get(f) not in ("", None)} if tpl else {}
    row.update(_fill_shape(account_dir, row, unit))
    row.update(
        {
            "STKCOD": stkcod,
            "STKDES": stock_lookup.storage_des(account_dir, nm),
            "STKDES2": stock_lookup.storage_des(account_dir, nm),
            "ACCCOD": acccod,
            "USERID": userid,
            "CREBY": userid,
            "CREDAT": created_date,
            "CHGDAT": created_date,
            # 商品可见性与客户/供应商相反:真账套商品 STATUS 一律空白,写 'A' 反被
            # Express 商品库列表过滤掉(只在单据按编号引用得到)。客户/供应商才用 'A'。
            "STATUS": "",
        }
    )
    _apply_unit(account_dir, row, unit)
    if vatcod:
        row["VATCOD"] = vatcod[:1]
    elif exempt is False and row.get("VATCOD") == EXEMPT_VATCOD:
        row["VATCOD"] = NORMAL_VATCOD  # 应税行要的商品，抄来的模板却是免税的：按正常税率建
    # STKTYP 强制可见服务型 3(不跟模板):见 _VISIBLE_NONSTOCK_TYPE 注释——只有 3 型在商品列表
    # 屏显示,4/5 型会"在库却隐身",0 型则是库存货品(会误启库存核算)。绝不默认建库存。
    row["STKTYP"] = _VISIBLE_NONSTOCK_TYPE

    t = _open(account_dir, "STMAS", dbf.READ_WRITE)
    try:
        t.append(row)
    finally:
        t.close()
    _reindex(account_dir, "STMAS")
    err = verify_item_row(account_dir, stkcod)
    if err:  # 建完读不回 → 抛错,由 writer 备份回滚
        raise ValueError(err)
    return stkcod


# ─── 直接记科目的明细行要的主档商品(编码 = 科目码) ────────────────────────────
# Express 原生「直接记科目」:会计先建一件编码就是科目码的非库存商品(ACCCOD 经 ISACC 联到这个科目),单据行引用它。
# S 盘副本 1037 本只读扫(11-02):2,597 万行原生明细除 1,174 行外商品码都在 STMAS;编码 = 科目码的商品 20,318 件
# (STKTYP 3 有 10,809、5 有 9,158、0 有 322、4 有 29)。只写科目码、主档里没有这件商品的行,Express 原生作废按码查商品
# 报 Message#. 2101「找不到商品编码」,单子停在 E(作废不完整,11-02 69EXP\TEST 车架号窗口实录)。
ERR_ACCOUNT_ITEM_STOCK = "ACCOUNT_CODE_IS_STOCK_ITEM"


class AccountItemIsStock(ValueError):
    """编码 = 这个科目码的已是一件库存品:不能拿它记科目(会动库存),也不能再建同码的服务品。交人处理(整单回滚)。"""

    code = ERR_ACCOUNT_ITEM_STOCK

    def __init__(self, account: str):
        super().__init__(f"the item with code {account} is a stock item, it cannot carry a direct account line")


def _account_name(account_dir: Path, account: str) -> str:
    """科目码 → 科目名(泰文 ACCNAM 优先,空则英文 ACCNAM2);读不到回空。"""
    t = _open(account_dir, "GLACC", dbf.READ_ONLY)
    try:
        for r in S.live_rows(t):
            if (_gv(r, "ACCNUM") or "") == account:
                return _gv(r, "ACCNAM") or _gv(r, "ACCNAM2") or ""
        return ""
    finally:
        t.close()


def account_item_type(account_dir: Path, account: str) -> Optional[str]:
    """编码 = ``account`` 的活商品的 STKTYP;没有回 None。只读。"""
    t = _open(account_dir, "STMAS", dbf.READ_ONLY)
    try:
        for r in S.live_rows(t):
            if (_gv(r, "STKCOD") or "") == account:
                return str(_gv(r, "STKTYP"))
        return None
    finally:
        t.close()


def ensure_account_item(
    account_dir: Path,
    account: str,
    *,
    created_date: _dt.date,
    userid: str = S.PEARNLY_USERID,
    unit: str = "",
    name: str = "",
) -> Tuple[str, bool]:
    """直接记科目的明细行前,保证主档里有编码 = ``account`` 的非库存商品(照 Express 原生)。回 ``(编码, 是否本次新建)``。

    已有就复用(3 / 4 / 5 型都算,人家建的不动);同码的是库存品抛 :class:`AccountItemIsStock`;没有就照 :func:`ensure_item`
    的建法建:结构从同账套非库存品克隆、缺的按原生服务品形状补,STKTYP 3(商品列表看得见),品名取科目名(取不到用行上的品名),
    ACCCOD 经 ISACC 联到同一科目。已在写前备份-回滚事务内调用;建完重建 STMAS 索引并读回。"""
    code = (account or "").strip()
    if not code:
        raise ValueError("ensure_account_item: 空科目码")
    kind = account_item_type(account_dir, code)
    if kind is not None:
        if kind not in _NONSTOCK_TYPES:
            raise AccountItemIsStock(code)
        return code, False
    desc = _account_name(account_dir, code) or (name or "").strip() or code
    tpl = _pick_template(account_dir)
    acccod = _ensure_acccod(account_dir, code, desc, created_date, userid)
    row = {f: tpl[f] for f in _CLONE_FIELDS if tpl.get(f) not in ("", None)} if tpl else {}
    row.update(_fill_shape(account_dir, row, unit))
    row.update({
        "STKCOD": code,
        "STKDES": stock_lookup.storage_des(account_dir, desc),
        "STKDES2": stock_lookup.storage_des(account_dir, desc),
        "ACCCOD": acccod,
        "USERID": userid,
        "CREBY": userid,
        "CREDAT": created_date,
        "CHGDAT": created_date,
        "STATUS": "",  # 同 ensure_item:商品 STATUS 写 'A' 会被 Express 商品列表滤掉
    })
    _apply_unit(account_dir, row, unit)
    row["STKTYP"] = _VISIBLE_NONSTOCK_TYPE
    t = _open(account_dir, "STMAS", dbf.READ_WRITE)
    try:
        t.append(row)
    finally:
        t.close()
    _reindex(account_dir, "STMAS")
    err = verify_item_row(account_dir, code)
    if err:
        raise ValueError(err)
    return code, True


# 存量补建:1.12.11 以前 CC 写的直接科目行只写了科目码。每本账套第一次写带明细的单时,在同一张单的写前备份里把 CC 写过
# (单头 USERID = PEARNLY)的这种行要的商品补建上(Express 作废时现查主档,补上后旧单就能原生作废);这张单提交成功才记
# 「这本账套补过了」,回滚了下次再补。只看 CC 写的单,会计自己录的、商品后来被删了的旧单不碰。
BACKFILL_FILE: Optional[Path] = None  # 本机记哪些账套补过(测试改到临时目录);None = %APPDATA%\Pearnly\account_items.json
_BACKFILL_VERSION = 1
_backfill_pending: set = set()


def _backfill_file() -> Path:
    if BACKFILL_FILE is not None:
        return BACKFILL_FILE
    from .config import APPDATA_DIR

    return APPDATA_DIR / "account_items.json"


def _backfill_key(account_dir) -> str:
    return str(Path(account_dir)).rstrip("\\/").lower()


def _backfill_done() -> Dict:
    import json

    try:
        found = json.loads(_backfill_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def backfill_due(account_dir) -> bool:
    return int(_backfill_done().get(_backfill_key(account_dir)) or 0) < _BACKFILL_VERSION


def backfill_account_items(account_dir: Path, *, created_date: _dt.date, userid: str = S.PEARNLY_USERID) -> List[str]:
    """把 CC 写过的直接科目行(STKCOD 是科目表里的科目码、主档里没有这件商品)要的商品补建上,回补建的编码。
    同码已是库存品的跳过(不动人家的库存品,那几张旧单要人处理)。已在写前备份-回滚事务内调用;只读扫单头与明细。"""
    account_dir = Path(account_dir)
    _backfill_pending.add(_backfill_key(account_dir))

    def live(name, *fields):
        if not (account_dir / f"{name}.DBF").exists():
            return []
        t = _open(account_dir, name, dbf.READ_ONLY)
        try:
            return [{f: (_gv(r, f) or "") for f in fields} for r in S.live_rows(t)]
        finally:
            t.close()

    # CC 写的单：旧版单头录入人记 PEARNLY；11-07 起单头记 Express 账户名，看凭证头的机器标记（``writer_id.cc_vouchers``）
    marked = writer_id.cc_vouchers(account_dir)
    ours = {r["DOCNUM"] for name in ("APTRN", "ARTRN") for r in live(name, "DOCNUM", "USERID")
            if S.is_pearnly(r["USERID"]) or r["DOCNUM"] in marked}
    if not ours:
        return []
    accounts = {r["ACCNUM"] for r in live("GLACC", "ACCNUM") if r["ACCNUM"]}
    items = {r["STKCOD"] for r in live("STMAS", "STKCOD")}
    wanted: Dict[str, Dict] = {}
    for r in live("STCRD", "DOCNUM", "STKCOD", "STKDES", "TQUCOD"):
        code = r["STKCOD"]
        if r["DOCNUM"] in ours and code in accounts and code not in items and code not in wanted:
            wanted[code] = r
    made = []
    for code, r in sorted(wanted.items()):
        try:
            _code, created = ensure_account_item(account_dir, code, created_date=created_date, userid=userid,
                                                 name=r["STKDES"])
        except AccountItemIsStock:
            log.warning("存量补建跳过 %s:同码已是库存品", code)
            continue
        if created:
            made.append(code)
    return made


def backfill_committed(account_dir) -> None:
    """这张单提交成功(写前备份已丢弃):这本账套补过了,记下;没在这张单里补过的什么都不做。读写失败只记日志。"""
    import json

    key = _backfill_key(account_dir)
    if key not in _backfill_pending:
        return
    _backfill_pending.discard(key)
    done = _backfill_done()
    done[key] = _BACKFILL_VERSION
    path = _backfill_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError as e:
        log.warning("记不下账套补建标记 %s: %s", key, e)


# ─── 真库存商品建档(STKTYP=0)· 「选库存」但账里无此品时用 ────────────────────
_STOCK_TYPE = stock_lookup.STOCK_TYPE
# 建全新库存目录档要照搬的结构字段(禁硬编码·全取自同账套真库存品模板)。含 ACCCOD——库存/
# COGS/收入科目联动键(与非库存不同:非库存自建 ISACC 简单映射,库存直接复用真库存品的 ST 段
# 映射,拿到正确的存货/成本科目)。品名/编码/余额(全 0)/审计另行覆写。
# 单位/类别列 **不在**克隆之列:模板只是「挂众数存货科目的那一行」,它的单位/类别是那一行商品
# 的属性,不是这套账的默认 —— 改从账套众数取(见 stock_defaults)。换算**系数**仍克隆模板。
# NEGALLOW 不在克隆之列 —— 它不是「结构」而是「这个品准不准卖成负」的策略,见下。
_STOCK_CLONE_FIELDS = (
    "CFACTOR",
    "PFACTOR",
    "SFACTOR",
    "VATCOD",
    "STKLEV",
    "STKCLASS",
    "ISINV",
    "ISSAL",
    "ISPUR",
    "ACCCOD",
)

# 我们自动建的库存品一律允许负库存(Express 字段 ส/ค ติดลบได้? ∈ {Y,N,A})。
# 理由:这些品按设计**就是**要被卖成负的(新建品当次销售没有成本基础,规则是"没档就建、
# 没货卖负、没成本不结转")。档上却写着"不许负",数据状态与商品档策略自相矛盾 —— 会计事后
# 在 Express 界面上动这个品时会被 GUI 拦住,而拦的正是我们自己写进去的库存。
# 只作用于**我们新建**的档;客户既有商品的策略一律不碰(那是人家的账,不擅改)。
_NEG_ALLOW_YES = "Y"


def find_stock_item_code(account_dir: Path, name: str, *, exempt: Optional[bool] = None) -> Optional[str]:
    """只读:同名真库存主档(STKTYP=0·含 0 余额)的 STKCOD·归一精确。给新建前幂等查重。

    与 stock_sale.find_stock_item 的区别只在返回什么:那个还要结存和科目联动键来结转 COGS。
    **比对口径同一份**(stock_lookup),两边不许各截各的 —— 那正是长名重复建档的根。
    ``exempt``:只认免税的(VATCOD '0'),同 :func:`find_item_code`(免税的库存品,如生鲜农产品)。
    """
    if exempt is None:
        return stock_lookup.first_code(account_dir, name, (_STOCK_TYPE,))
    return _first_by_vatcod(stock_lookup.find_rows(account_dir, name, (_STOCK_TYPE,)), exempt)


def _verify_stock_item_row(account_dir: Path, stkcod: str) -> Optional[str]:
    """读回新建库存主档:码在 + STKTYP==0 + 余额为 0(新品)。ok 返 None;否则返错误。"""
    t = _open(account_dir, "STMAS", dbf.READ_ONLY)
    try:
        for r in S.live_rows(t):  # 同 verify_item_row:软删旧档不算数
            if _gv(r, "STKCOD") == stkcod:
                typ = str(_gv(r, "STKTYP"))
                if typ != _STOCK_TYPE:
                    return f"STMAS {stkcod} STKTYP={typ!r} 非库存型(建库存档应为 0)"
                if float(_gv(r, "MREMBAL") or 0) != 0:
                    return f"STMAS {stkcod} 新库存品 MREMBAL≠0"
                return None
        return f"STMAS row {stkcod} not found after create"
    finally:
        t.close()


class StockCodeMissing(ValueError):
    """云端下发的存货编码在账套里找不到(或不是库存品)· 交人重选,不回落按名找 / 新建。"""


class StockItem(NamedTuple):
    """find-or-create 库存主档的结果 —— 单返 STKCOD 说不出「这次替客户猜了什么」。

    unit_guessed=票面没给单位、单位是按账套众数填的:调用方必须把它带进 line_modes,推送日志
    才标得出「这个单位是系统猜的,去 Express 核一眼」。静默猜等于替客户做主还不告诉他。
    """

    stkcod: str
    created: bool = False
    unit_guessed: bool = False
    unit_code: str = ""


def _stock_unit(account_dir: Path, unit: str, fallback: str) -> Tuple[str, bool]:
    """新建库存档写哪个 QUCOD → (单位码, 是不是猜的)。

    票面给了就按票面的(先解析成这套账认的合法码);票面没给/这套账不认这个说法 → 退账套众数。
    两头都拿不到 → 返空:硬塞一个 Express 认不出的单位,比主档单位留白更难查。
    """
    code = unit_code.resolve(account_dir, unit)
    if code:
        return code, False
    if unit:  # 票面写了单位却解析不出 —— 记因,别让它无声无息地变成众数
        log.warning(
            "单位「%s」在本账套 ISTAB 里解析不出合法码 · 回落账套众数 %r",
            unit,
            fallback,
        )
    if not fallback:
        log.warning("账套没有可取众数的库存品单位 · 新建库存档 QUCOD 留空(不猜)")
    return fallback, bool(fallback)


def ensure_stock_item(
    account_dir: Path,
    name: str,
    *,
    userid: str = S.PEARNLY_USERID,
    unit: str = "",
    acccod: str = "",
    code: str = "",
    exempt: Optional[bool] = None,
) -> StockItem:
    """find-or-create 真库存商品主档(STKTYP=0),返回 StockItem。已在备份-回滚事务内调用。

    「选库存」但客户账里没这品时用:建一条 **0 余额** 的真库存目录档(克隆同账套真库存品的结构
    + 库存/成本科目联动),让商品出现在 Express 库存目录、且日后进货可复用/走 V2-b。全新品当次
    销售没有任何成本基础 → 调用方按无成本卖(不结转 COGS·卖成负库存),日后进货再滚出真成本。
    `acccod`=账套配置的存货科目组,只在账里一条库存品都没有、要抄服务品档时用得上。
    禁硬编码:结构字段全从模板克隆,单位/类别取账套众数,仅覆写品名/编码/余额/审计。
    """
    if (code or "").strip():
        # 复核时选定了 Express 存货:只认这个码,不在就报错交人重选(不按名找、不新建)。
        if stock_lookup.find_rows_by_code(account_dir, code, (stock_lookup.STOCK_TYPE,)):
            return StockItem(code.strip())
        raise StockCodeMissing(f"所选存货 {code} 在 Express 里已不存在或不是库存品,请重新选择")
    nm = (name or "").strip()
    if not nm:
        raise ValueError("ensure_stock_item: 空品名")

    found = find_stock_item_code(account_dir, nm, exempt=exempt)
    if found:
        return StockItem(found)

    # 模板取「账套里用得最多的存货科目」那一类,且该科目须经 GLACC 判定确是存货科目;
    # 账里没库存品则抄服务品档 + 传入的存货科目组。判不出存货科目就抛,交会计 —— 见 stock_template。
    tpl = stock_template.pick(account_dir, _STOCK_CLONE_FIELDS, acccod=acccod)
    defaults = stock_defaults.read(account_dir)
    qucod, guessed = _stock_unit(account_dir, unit, defaults.qucod)
    stkcod = _next_stkcod(account_dir)
    # 主档审计列记**真实建档日**,不是发票日:这条档就是今天在这台机器上建出来的,写成发票日
    # (真机实测写成 2026-05-31,而档是 07-25 建的)等于伪造建档时间,查「谁什么时候建的」全乱。
    today = _dt.date.today()

    row = {f: tpl[f] for f in _STOCK_CLONE_FIELDS if tpl.get(f) not in ("", None)}
    row.update(
        {
            "STKCOD": stkcod,
            "STKDES": stock_lookup.storage_des(account_dir, nm),
            "STKDES2": stock_lookup.storage_des(account_dir, nm),
            "STKTYP": _STOCK_TYPE,
            # 全新品:结存/货值全 0,日后进货再滚(移动均价)。
            "TOTBAL": 0.0,
            "TOTVAL": 0.0,
            "MREMBAL": 0.0,
            "MREMVAL": 0.0,
            "USERID": userid,
            "CREBY": userid,
            "CREDAT": today,
            "CHGDAT": today,
            "STATUS": "",  # 商品 STATUS 一律空白(写 'A' 反被 Express 商品列表过滤掉)
            "NEGALLOW": _NEG_ALLOW_YES,  # 这个品按设计就是要卖成负的 · 见常量处
        }
    )
    # 单位在真账套里是四列联动的:70EXP 162 个库存品 QUCOD==CQUCOD==PQUCOD==SQUCOD 无一例外。
    # 只改 QUCOD 会造出真账里不存在的形状(主档单位 พค、采购/销售单位却是模板那行的 กง),
    # 正是 unit_code 那条"主档与卡片自相矛盾"的旧伤。换算系数不跟着动,克隆模板的 1.0。
    row.update({f: qucod for f in ("QUCOD", "CQUCOD", "PQUCOD", "SQUCOD")})
    if defaults.stkgrp:  # 取不到就不写,交 DBF 用列默认(别把 '' 塞进去当"填过了")
        row["STKGRP"] = defaults.stkgrp
    if exempt:  # 免税行要的库存品(10-29 真实语料:生鲜等免税行的库存品主档 VATCOD '0'):建成免税的
        row["VATCOD"] = EXEMPT_VATCOD
    elif exempt is False and row.get("VATCOD") == EXEMPT_VATCOD:
        row["VATCOD"] = NORMAL_VATCOD  # 应税行要的库存品，抄来的模板却是免税的：按正常税率建

    t = _open(account_dir, "STMAS", dbf.READ_WRITE)
    try:
        t.append(row)
    finally:
        t.close()
    _reindex(account_dir, "STMAS")
    err = _verify_stock_item_row(account_dir, stkcod)
    if err:  # 建完读不回 → 抛错,由 writer 备份回滚
        raise ValueError(err)
    return StockItem(stkcod, True, guessed, qucod if guessed else "")
