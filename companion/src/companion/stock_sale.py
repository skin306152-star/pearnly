# -*- coding: utf-8 -*-
"""V2-b 卖库存品:真扣库存 + COGS(移动均价制)。逆向 gold HS580101-001。

「选库存」的销售行走这里(item_mode='stock_sale'),按"账里这库存品有没有货"分支:

**① 有货(STKTYP=0 库存品·正库存)**:把货从客户真实库存里扣出去,按移动均价结转成本。
账务(逆向真账 gold HS580101-001,沙盒验证通过):
- 均价成本 = STMAS.MREMVAL / STMAS.MREMBAL;COGS = 数量 × 均价;
- STCRD 库存出行:XUNITPR=均价、XTRNVAL=COGS、MREMBAL/MREMVAL=卖后结存(带库存签名);
- STMAS 扣减:MREMBAL/TOTBAL−数量、MREMVAL/TOTVAL−COGS(均价不变);
- 额外分录:借 COGS 科目(ISACC.ACCNUM02)/ 贷 存货科目(ISACC.ACCNUM01)· 金额=COGS。

**② 有档没货(零/负库存)**:照卖,卖成更负 —— 负结存在真账里是常态(61EXP/61SKBAC 取证:
3930 条负结存出货行,91% 带真实成本)。成本取 STMAS.STNPR 标准成本(真账出货行 XUNITPR 恰等于
STNPR,不是移动均价);STNPR 为 0/空 = 没有成本基础 → 本行不结转 COGS,XUNITPR/XTRNVAL 写 0。

**③ 账里连档都没有**:先建 STKTYP=0 目录档(结构抄账里的库存品;一条都没有则抄服务品档 +
调用方传入的存货科目组),再同 ② 卖成负库存。全新品当次卖出没有任何成本基础(真账 994 个有
流水的库存品,没有一个是「没进过货先卖」的)→ 不结转 COGS,等进货票把成本滚进来。

三条诚实线:**绝不编成本**(没有成本基础就不结转,禁拿售价倒推)· **绝不静默**(新建档/未结转
成本逐行进 line_modes 的 created/reason,并落 warning 日志)· **可捞回**(reason 带
no_cost_basis / new_item_no_cost_basis,导出侧据此筛出当期毛利虚高的单)。

剩下的唯一 escalate 是账套没配存货科目组(stock_template.StockAccGroupRequired)。
"""

from __future__ import annotations

import os
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

import dbf

from . import dbf_detail
from . import dbf_schema as S
from . import product_master
from . import stock_lookup
from .logger import get_logger

log = get_logger(__name__)

# COGS 分录序号(逆向 gold HS580101-001:借 COGS=8 / 贷 存货=9,接在 asset=2/rev=5/vat=6 之后)。
COGS_SEQIT_DR = "8"
COGS_SEQIT_CR = "9"

ITEM_MODE = "stock_sale"  # 本模块处理的 item_mode(逐行 line_modes 也标它)

# 逐行 reason(回执/推送日志/导出筛选的口径,变一个字都要连着改导出侧)
REASON_STANDARD_COST = "standard_cost"  # 卖成负库存,按 STMAS.STNPR 标准成本结转
REASON_NO_COST = "no_cost_basis"  # 有档但零/负库存且无标准成本 → 未结转 COGS
REASON_NEW_ITEM = "new_item_no_cost_basis"  # 本次新建档 + 无成本基础 → 未结转 COGS
NO_COST_REASONS = (REASON_NO_COST, REASON_NEW_ITEM)  # 「当期毛利虚高」的捞回口径


class StockItemNotFound(Exception):
    """库存行落不了账 → 交会计。现只剩两种:ISACC 缺存货/COGS 科目、期初数量/成本非法。

    ⚠️ 名字是历史包袱:自「卖库存不走死胡同」拍板后,库存品没档/没货都不再抛它。
    改名要连着改云端错误码 STOCK_ITEM_NOT_FOUND,另派单。
    """


def _q2(x) -> float:
    return float(Decimal(str(x)).quantize(Decimal("0.01"), ROUND_HALF_UP))


def _gv(rec, field: str, default=""):
    try:
        v = rec[field]
    except Exception:
        return default
    return v.strip() if isinstance(v, str) else v


def find_stock_item(account_dir: Path, name: str, code: str = "") -> Optional[Dict]:
    """按品名匹配客户既有库存商品(STKTYP=0)。返回 {stkcod,mrembal,mremval,acccod} 或 None。

    **单次读取、返回真实结存**(不在此按 bal 过滤 —— 那会与建档侧的 find_stock_item_code 形成
    两次读取分歧:某刻 MREMBAL 瞬时读成 0 就把"复用"误判成"新建"、对真库存品建出重复档)。
    调用方(write_stock_lines)据结存三分支:bal>0 扣库存+均价 COGS / bal≤0 卖成更负 / None 才新建。
    优先返有正库存的行(可卖);同名多行仅存零/负库存时返首行(在它上面继续卖成负)。

    扫描与比对键都走 stock_lookup:与建档侧共用一份口径,长名不会一边截一边不截。
    """
    if (code or "").strip():
        # 复核时选定了 Express 存货:只认这个码,码不在(被删 / 改成非库存)就停下交人重选,
        # 不回头按品名找、更不新建(2026-09-28 压测:按名找不到就建了重复存货)。
        rows = stock_lookup.find_rows_by_code(account_dir, code, (stock_lookup.STOCK_TYPE,))
        if not rows:
            raise StockItemNotFound(f"所选存货 {code} 在 Express 里已不存在或不是库存品,请重新选择")
        return rows[0]
    rows = stock_lookup.find_rows(account_dir, name, (stock_lookup.STOCK_TYPE,))
    for info in rows:
        if info["mrembal"] > 0:
            return info  # 可卖 → 立即用
    # 没找到有正库存的同名品 → 诊断落盘(排查"读到 0/漏读"用:文件 stat + 所有匹配行 + PID)
    _diag_no_positive(
        name,
        Path(account_dir) / "STMAS.DBF",
        [(r["stkcod"], r["mrembal"]) for r in rows],
    )
    return rows[0] if rows else None


def _diag_no_positive(name: str, stmas: Path, matches: list) -> None:
    """卖库存品「无正库存匹配」诊断落盘(worker logger 不进 companion.log,故直写专用文件)。
    记 PID + STMAS 文件路径/大小/mtime + 所有同名匹配行的结存。网络账套上如出现「真库存品被
    读成 0/漏读」(SMB 客户端缓存陈旧视图),此处能一眼看出小助手读到的是哪个版本的文件。

    改判负库存放行后这条线索更重要:陈旧视图不再表现为「卡住不推」,而是「把有货的品卖成负」。"""
    from .config import LOG_DIR

    try:
        stt = os.stat(stmas)
        meta = f"size={stt.st_size} mtime={stt.st_mtime:.0f}"
    except OSError as e:
        meta = f"stat_err={e}"
    line = f"pid={os.getpid()} name={name!r} file={stmas} {meta} matches={matches}"
    try:
        with open(LOG_DIR / "stock_diag.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def stock_accounts(account_dir: Path, acccod: str) -> Tuple[str, str]:
    """ISACC[acccod] → (存货科目 ACCNUM01, COGS 科目 ACCNUM02)。缺一即抛(账套没配好)。

    采购入库侧(stock_purchase)只用得到存货科目,但同样要求 COGS 科目在位:少了它这批货
    日后卖出时结转不了成本,与其入库后才卡住,不如入库前就交会计把账套配全。
    """
    t = dbf.Table(str(Path(account_dir) / "ISACC.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_ONLY)
    try:
        for r in t:
            if dbf.is_deleted(r):
                continue
            if _gv(r, "ACCCOD") == acccod:
                inv, cogs = _gv(r, "ACCNUM01"), _gv(r, "ACCNUM02")
                if inv and cogs:
                    return inv, cogs
        raise StockItemNotFound(f"ISACC[{acccod}] 缺存货/COGS 科目(账套未配库存核算)")
    finally:
        t.close()


def standard_cost(account_dir: Path, stkcod: str) -> float:
    """STMAS.STNPR = 该品的标准成本。负库存出货就按它结转(真账 61EXP/61SKBAC 的负结存出货行
    XUNITPR 恰等于 STNPR,不是移动均价)。没配/为 0 → 返 0 = 没有成本基础,本行不结转。"""
    t = dbf.Table(str(Path(account_dir) / "STMAS.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_ONLY)
    try:
        for r in t:
            if not dbf.is_deleted(r) and _gv(r, "STKCOD") == stkcod:
                return float(_gv(r, "STNPR") or 0)
        return 0.0
    finally:
        t.close()


def has_stock_lines(line_modes: List[Dict]) -> bool:
    """本单写过库存出行?读回校验要据此放行库存影响行。

    判据不能再用「有没有 COGS 分录」:无成本基础的行照样动库存,却一分成本都不结转。
    """
    return any(m.get("mode") == ITEM_MODE for m in line_modes)


class _SaleBasis(NamedTuple):
    """②③ 这一行的落账基础:卖哪条档、起点结存/货值、单位成本,外加给回执的诚实标记。"""

    stkcod: str
    bal: float
    val: float
    acccod: str
    unit_cost: float
    created: bool
    reason: str
    unit_guessed: bool = False  # ③ 建档时票面没给单位,按账套众数填的
    unit_code: str = ""  # 猜出来的那个 QUCOD(没猜为空)


def _sale_basis(
    account_dir: Path,
    item: Optional[Dict],
    name: str,
    *,
    unit: str,
    userid: str,
    acccod: str,
) -> _SaleBasis:
    """② 有档没货 → 读标准成本;③ 账里没这品 → 建档,全新品没有任何成本基础一律 0。

    成本只认账里已经有的事实:绝不拿售价倒推、绝不套默认成本 —— 宁可当期不结转,也不编。
    """
    if item:
        cost = standard_cost(account_dir, item["stkcod"])
        return _SaleBasis(
            stkcod=item["stkcod"],
            bal=item["mrembal"],
            val=item["mremval"],
            acccod=item["acccod"],
            unit_cost=cost,
            created=False,
            reason=REASON_STANDARD_COST if cost > 0 else REASON_NO_COST,
        )
    made = product_master.ensure_stock_item(
        account_dir, name, userid=userid, unit=unit, acccod=acccod
    )
    return _SaleBasis(
        made.stkcod,
        0.0,
        0.0,
        "",
        0.0,
        made.created,
        REASON_NEW_ITEM,
        made.unit_guessed,
        made.unit_code,
    )


def write_stock_lines(
    account_dir: Path,
    docnum: str,
    docdat,
    items: List[Dict],
    party_code: str,
    *,
    depcod: str = "",
    userid: str = "BIT9",
    acccod: str = "",
) -> Tuple[List[Dict], List[Tuple[str, str, str, Decimal]]]:
    """写库存明细行 + 扣 STMAS,返回 (逐行 mode 结果, COGS 分录行)。

    items = item_mode=='stock_sale' 的行(已在备份-回滚事务内)。逐行三分支(据单次读取的结存):
    - 有正库存(bal>0)→ 库存出行 + 扣 STMAS + COGS 分录(移动均价);
    - 有档没货(bal≤0)→ 卖成更负;STNPR>0 按标准成本结转,否则不结转(reason=no_cost_basis);
    - 账里没这品(None)→ 建 STKTYP=0 档再卖成负,无成本基础(reason=new_item_no_cost_basis)。
    COGS 分录按 (COGS科目, 存货科目) 聚合:借 COGS 总额 / 贷 存货 总额(有成本的行才产生)。
    `acccod`=账套配置的存货科目组,只在账里一条库存品都没有、要建首个库存档时用得上。
    """
    line_modes: List[Dict] = []
    cogs_by_pair: Dict[Tuple[str, str], Decimal] = {}
    st = dbf.Table(str(Path(account_dir) / "STCRD.DBF"), codepage=S.CODEPAGE)
    st.open(dbf.READ_WRITE)
    try:
        for seq, it in enumerate(items, start=1):
            name = str(it.get("name") or "")
            qty = float(Decimal(str(it.get("qty") or "1")))
            unit_price = float(Decimal(str(it.get("unit_price") or "0")))
            amount = float(Decimal(str(it.get("amount") or "0")))
            unit = str(it.get("unit") or "")
            item = find_stock_item(account_dir, name, str(it.get("code") or ""))
            if item and item["mrembal"] > 0:
                # ① 复用(有正库存):真扣库存 + 移动均价结转 COGS
                avg = item["mremval"] / item["mrembal"]
                cogs = _q2(qty * avg)
                new_bal = item["mrembal"] - qty
                new_val = _q2(item["mremval"] - cogs)
                inv_acc, cogs_acc = stock_accounts(account_dir, item["acccod"])
                st.append(
                    S.stcrd_stock_out_row(
                        docnum,
                        seq,
                        docdat,
                        item["stkcod"],
                        party_code,
                        name,
                        qty,
                        unit_price,
                        amount,
                        avg_cost=_q2(avg),
                        cogs=cogs,
                        rem_bal=new_bal,
                        rem_val=new_val,
                        unit=unit,
                        depcod=depcod,
                    )
                )
                _reduce_stmas(account_dir, item["stkcod"], new_bal, new_val)
                key = (cogs_acc, inv_acc)
                cogs_by_pair[key] = cogs_by_pair.get(key, Decimal("0")) + Decimal(
                    str(cogs)
                )
                line_modes.append(
                    dbf_detail.line_mode(seq, name, item["stkcod"], mode=ITEM_MODE)
                )
            else:
                # ②③ 没货可扣:照卖,卖成更负(负结存在真账里是常态)。成本只按账里已有的
                # 事实结转,没有就不结转 —— 编一个成本出来,毛利就是假的。
                basis = _sale_basis(
                    account_dir, item, name, unit=unit, userid=userid, acccod=acccod
                )
                cost = basis.unit_cost
                cogs = _q2(qty * cost) if cost > 0 else 0.0
                new_bal = basis.bal - qty
                new_val = _q2(basis.val - cogs)
                st.append(
                    S.stcrd_stock_out_row(
                        docnum,
                        seq,
                        docdat,
                        basis.stkcod,
                        party_code,
                        name,
                        qty,
                        unit_price,
                        amount,
                        avg_cost=cost,
                        cogs=cogs,
                        rem_bal=new_bal,
                        rem_val=new_val,
                        unit=unit,
                        depcod=depcod,
                    )
                )
                _reduce_stmas(account_dir, basis.stkcod, new_bal, new_val)
                if cogs:
                    inv_acc, cogs_acc = stock_accounts(account_dir, basis.acccod)
                    key = (cogs_acc, inv_acc)
                    cogs_by_pair[key] = cogs_by_pair.get(key, Decimal("0")) + Decimal(
                        str(cogs)
                    )
                # 诚实线:绝不静默 —— 未结转成本的行进 warning(会计要能在推送日志里一眼捞出
                # 当期毛利虚高的单),带标准成本的负库存出货是常态,记 info 即可。
                write_log = log.warning if basis.reason in NO_COST_REASONS else log.info
                write_log(
                    "负库存出货 %s 行%d「%s」:结存 %s→%s · 单位成本 %s · %s",
                    docnum,
                    seq,
                    name,
                    basis.bal,
                    new_bal,
                    cost,
                    basis.reason,
                )
                line_modes.append(
                    dbf_detail.line_mode(
                        seq,
                        name,
                        basis.stkcod,
                        mode=ITEM_MODE,
                        reason=basis.reason,
                        created=basis.created,
                        unit_guessed=basis.unit_guessed,
                        unit_code=basis.unit_code,
                    )
                )
    finally:
        st.close()

    cogs_rows: List[Tuple[str, str, str, Decimal]] = []
    for (cogs_acc, inv_acc), amt in cogs_by_pair.items():
        cogs_rows.append((COGS_SEQIT_DR, cogs_acc, S.TRNTYP_DEBIT, amt))
        cogs_rows.append((COGS_SEQIT_CR, inv_acc, S.TRNTYP_CREDIT, amt))
    return line_modes, cogs_rows


def write_stmas_fields(
    account_dir: Path, stkcod: str, fields: Dict[str, float]
) -> None:
    """按 STKCOD 定位库存主档,写入 fields 指定字段。

    库存主档的唯一写入脚手架:卖(_reduce_stmas)、期初(_set_stmas_opening)、进货入库
    (stock_purchase)共用一份定位逻辑,避免三处各写一遍 STKCOD 匹配循环而口径漂移。

    只写还算数的行:软删的同码旧档若排在前面,扣库存就扣进了 Express 看不见的行 —— 真档
    余额一动不动,账面有货实物没货(或反过来),而且不报任何错。
    """
    t = dbf.Table(str(Path(account_dir) / "STMAS.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    try:
        for r in S.live_rows(t):
            if _gv(r, "STKCOD") == stkcod:
                with r as rec:
                    for k, v in fields.items():
                        rec[k] = v
                return
    finally:
        t.close()


def _reduce_stmas(
    account_dir: Path, stkcod: str, new_bal: float, new_val: float
) -> None:
    """扣客户库存主档:MREMBAL/TOTBAL=卖后余额,MREMVAL/TOTVAL=卖后货值(均价不变)。"""
    write_stmas_fields(
        account_dir,
        stkcod,
        {"MREMBAL": new_bal, "MREMVAL": new_val, "TOTBAL": new_bal, "TOTVAL": new_val},
    )


def _set_stmas_opening(account_dir: Path, stkcod: str, qty: float, val: float) -> None:
    """置 STMAS 期初 + 结存字段:OPNBAL/OPNVAL(期初)+ MREMBAL/MREMVAL/TOTBAL/TOTVAL(结存)。"""
    write_stmas_fields(
        account_dir,
        stkcod,
        {
            "OPNBAL": qty,
            "OPNVAL": val,
            "MREMBAL": qty,
            "MREMVAL": val,
            "TOTBAL": qty,
            "TOTVAL": val,
        },
    )


def set_opening_stock(
    account_dir: Path, items: List[Dict], docdat, *, userid: str = "BIT9"
) -> List[Dict]:
    """会计补的期初 → 建/复用 STKTYP=0 库存目录档 + 置 STMAS 期初/结存 + 写 STCRD 期初入库行。

    items=[{name, qty, unit_cost, [unit]}](成本由会计据真实单据传入,绝不自造)。已在备份-
    回滚事务内调用。已有正库存的品跳过(不冲既有库存)。写完商品有真实库存,随后走 stock_sale ①
    真扣库存+移动均价 COGS。返回逐项结果。

    ⚠️ 本函数只落库存台账(STMAS 期初/结存 + STCRD 期初入库行),让商品有真实可卖库存;
    GL 开账分录(借存货 / 贷 ทุน-ยกมา 期初权益)待真账 gold 逆向确认科目后补 —— 不凭空写 GL。
    """
    results: List[Dict] = []
    st = dbf.Table(str(Path(account_dir) / "STCRD.DBF"), codepage=S.CODEPAGE)
    st.open(dbf.READ_WRITE)
    try:
        for seq, it in enumerate(items, start=1):
            name = str(it.get("name") or "")
            qty = float(Decimal(str(it.get("qty") or "0")))
            cost = float(Decimal(str(it.get("unit_cost") or "0")))
            if qty <= 0 or cost < 0:
                raise StockItemNotFound(
                    f"期初「{name}」数量/成本非法(qty={qty} cost={cost})"
                )
            val = _q2(qty * cost)
            unit = str(it.get("unit") or "")
            # d = 会计填的**业务日期**(期初那天),逐行可填,缺则回落单据日期。它只管 STCRD
            # 期初行和单号,不碰主档 CREDAT —— 主档记的是"这条档什么时候建出来的"(今天)。
            d = it.get("date") or docdat
            stkcod = product_master.ensure_stock_item(
                account_dir, name, userid=userid, unit=unit
            ).stkcod
            cur = find_stock_item(account_dir, name)
            if cur and cur["mrembal"] > 0:
                results.append(
                    {"name": name, "stkcod": stkcod, "mode": "skip_has_stock"}
                )
                continue
            docnum = f"OB{d.strftime('%y%m%d')}-{seq:03d}"  # 期初单(OB=opening balance)
            st.append(
                S.stcrd_opening_row(
                    docnum,
                    seq,
                    d,
                    stkcod,
                    name,
                    qty,
                    cost,
                    rem_bal=qty,
                    rem_val=val,
                    unit=unit,
                )
            )
            _set_stmas_opening(account_dir, stkcod, qty, val)
            results.append(
                {
                    "name": name,
                    "stkcod": stkcod,
                    "mode": "opening_set",
                    "qty": qty,
                    "val": val,
                }
            )
    finally:
        st.close()
    return results
