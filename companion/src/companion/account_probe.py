# -*- coding: utf-8 -*-
"""账套真名单:读 Express 公司登记表 `secure/SCCOMP.DBF`(驱动登录公司 grid 的注册文件)。

★为什么不扫文件夹(原 bug):扫 express_root 子目录会捞进 Express 演示/空模板 + 未登记目录,
且真账套路径可能跨盘(SCCOMP.PATH 写绝对路径如 `S:\\2558\\EXP58\\58ASIASP`)→ 扫一个目录
既捞垃圾又漏真账套;客户若选了未登记的,直写能写但 PACK 在 grid 里找不到 → 发票永不显示。
SCCOMP 是 Express 自己读来建 grid 的同一张表 → 读它得到与登录 grid **完全一致**的名单。

SCCOMP.DBF(cp874)字段:`COMPNAM`(grid 显示名·含排序前缀如 "9."/"Z."·grid 按它升序排)·
`COMPCOD`(账套**槽位**代号)·`PATH`(存储路径·相对则相对 express_root·绝对则跨盘原样)·
`GENDAT`·`CANDEL`。`account_set_row` = 按 `COMPNAM` 升序后的 1-based 位置(= grid 行号·确定性)。

★grid 过滤 = **非删除行**(`dbf.is_deleted` 为否):真机实证 70EXP SCCOMP **107 行总 / 104 删除 /
3 非删除**,3 非删除逐一 == 登录 grid(PDATAT 跨盘 S:\… / DATAT→TEST / DATAZ→DAT)。
★账套唯一身份 = **PATH**,不是 `COMPCOD` —— COMPCOD 是槽位,跨(含已删)行不唯一(104 删除行多复用
'DATAT')→ 选定/锁定的唯一键统一用 PATH(account_dir),与端侧 C2 复核对齐(见 pairing)。

铁律:全程只读;登记表缺/坏 → 抛 `RegistryUnavailable`(配对窗报错),**绝不回退扫文件夹捞垃圾**。
"""

from __future__ import annotations

import os
import ntpath
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Tuple

import dbf

from . import dbf_schema as S
from .express_locate import sccomp_path
from .logger import get_logger

log = get_logger(__name__)

_INFO_DBF = "ISINFO.DBF"


class RegistryUnavailable(Exception):
    """读不到 Express 公司登记表(SCCOMP)→ 配对窗报错,绝不回退扫文件夹。"""


@dataclass(frozen=True)
class AccountSet:
    code: str  # COMPCOD · 槽位代号(信息用 · 非唯一身份 · 身份看 path)
    name: str  # COMPNAM · grid 显示名(含排序前缀 · 下拉与 Express grid 逐字一致)
    company: str  # ISINFO.THINAM · 公司法定名(account_company / PACK 公司硬闸期望值)
    path: str  # 解析后账套存储路径(相对→express_root · 绝对→跨盘原样)
    tax_id: str  # ISINFO.TAXID
    writable: bool  # 该路径 OS 可写
    row: int  # grid 行号(COMPNAM 升序 · 1-based · PACK/RPA 登录后导航用)


def _clean(v) -> str:
    return str(v or "").replace("\xa0", " ").strip()


def _read_isinfo(account_dir: Path) -> Tuple[str, str]:
    """只读账套 ISINFO 首行 → (公司名 THINAM, 税号 TAXID);无/坏档返 ('','')。"""
    for cand in (_INFO_DBF, _INFO_DBF.lower()):
        p = account_dir / cand
        if not p.exists():
            continue
        try:
            t = dbf.Table(str(p), codepage=S.CODEPAGE)
            t.open(dbf.READ_ONLY)
            try:
                if len(t):
                    r = t[0]
                    return _clean(getattr(r, "THINAM", "")), _clean(
                        getattr(r, "TAXID", "")
                    )
            finally:
                t.close()
        except Exception:
            log.exception("ISINFO read failed: %s", p)
    return "", ""


def _resolve_path(raw: str, express_root: str | Path) -> str:
    """SCCOMP.PATH → 绝对路径:绝对(盘符/UNC)原样;相对则相对 express_root。"""
    raw = (raw or "").strip()
    if not raw:
        return ""
    return raw if os.path.isabs(raw) else os.path.join(str(express_root), raw)


def probe_account_sets(
    express_root: str | Path, *, include_details: bool = True
) -> List[AccountSet]:
    """读 `express_root/secure/SCCOMP.DBF` → 登记账套名单(与 Express 登录 grid 一致)。

    按 COMPNAM 升序定行号;逐条按解析路径读 ISINFO 补公司名/税号 + 查 OS 可写。
    登记表缺/坏 → 抛 RegistryUnavailable(不回退扫文件夹)。
    """
    reg = sccomp_path(express_root)
    if not Path(reg).exists():
        raise RegistryUnavailable(f"未找到 Express 公司登记表: {reg}")
    try:
        t = dbf.Table(str(reg), codepage="cp874")
        t.open(dbf.READ_ONLY)
    except Exception as e:
        raise RegistryUnavailable(f"读取 Express 公司登记表失败: {e}") from e
    raw_rows: List[Tuple[str, str, str]] = []
    try:
        for r in t:
            if dbf.is_deleted(r):
                continue
            code = _clean(getattr(r, "COMPCOD", ""))
            if not code:
                continue
            raw_rows.append(
                (
                    _clean(getattr(r, "COMPNAM", "")),
                    code,
                    _clean(getattr(r, "PATH", "")),
                )
            )
    finally:
        t.close()

    raw_rows.sort(key=lambda x: x[0])  # grid 排序 = COMPNAM 升序
    out: List[AccountSet] = []
    for i, (name, code, raw_path) in enumerate(raw_rows, start=1):
        path = _resolve_path(raw_path, express_root)
        company, tax_id = (
            _read_isinfo(Path(path)) if include_details and path else ("", "")
        )
        writable = False
        if include_details:
            try:
                writable = bool(path) and os.access(path, os.W_OK)
            except OSError:
                writable = False
        out.append(
            AccountSet(
                code=code,
                name=name,
                company=company,
                path=path,
                tax_id=tax_id,
                writable=writable,
                row=i,
            )
        )
    return out


def account_sets_payload(
    express_root: str | Path, *, include_details: bool = True
) -> List[dict]:
    """heartbeat 上报用(best-effort):登记表缺/坏 → 返空(心跳不因此中断;配对路径才严格报错)。"""
    try:
        root = str(express_root or "").strip()
        root_label = ntpath.basename(root.rstrip("\\/")) or root
        rows = []
        for account in probe_account_sets(
            express_root, include_details=include_details
        ):
            row = asdict(account)
            row["root"] = root
            row["root_label"] = root_label
            if include_details:
                row["mapping"] = probe_default_accounts(account.path)
            else:
                for key in ("company", "tax_id", "writable"):
                    row.pop(key, None)
            rows.append(row)
        return rows
    except RegistryUnavailable:
        return []


# ── 科目表(chart of accounts)· 读所选账套 GLACC.DBF → 供 Pearnly「科目映射」下拉按名字选 ──
# 真机 schema(\\accserver\…\TEST\GLACC.DBF · cp874):ACCNUM=科目码 / ACCNAM=泰文名 /
# ACCTYP='0'=可记账(postable)·'1'=汇总(summary·不可贴记)/ STATUS='A'=启用 / GROUP=科目大类。
# 只报【可记账 + 启用】科目(汇总科目贴记会错账,不进下拉)。账套可自定义→必须按账套读,不写死。
_GLACC_DBF = "GLACC.DBF"
_MAX_CHART = 3000


@dataclass(frozen=True)
class ChartAccount:
    code: str  # ACCNUM
    name: str  # ACCNAM(泰文·下拉按名字选)
    type: str  # GROUP 科目大类(1资产/2负债/3权益/4收入/5费用·供 FE 分组过滤)


def probe_chart_of_accounts(account_dir: str | Path) -> List[ChartAccount]:
    """读账套目录下 GLACC.DBF → 可记账(ACCTYP='0')且启用(STATUS='A')科目。

    全程只读;文件缺/坏 → 抛 RegistryUnavailable(由 best-effort 包装吞掉,不拖垮心跳)。
    """
    if not account_dir:
        raise RegistryUnavailable("account_dir 为空")
    base = Path(account_dir)
    path = None
    for cand in (_GLACC_DBF, _GLACC_DBF.lower()):
        if (base / cand).exists():
            path = base / cand
            break
    if path is None:
        raise RegistryUnavailable(f"未找到科目表: {base / _GLACC_DBF}")
    try:
        t = dbf.Table(str(path), codepage=S.CODEPAGE)
        t.open(dbf.READ_ONLY)
    except Exception as e:
        raise RegistryUnavailable(f"读取科目表失败: {e}") from e
    out: List[ChartAccount] = []
    try:
        for r in t:
            if dbf.is_deleted(r):
                continue
            if _clean(getattr(r, "STATUS", "")).upper() not in ("", "A"):
                continue
            if _clean(getattr(r, "ACCTYP", "")) != "0":  # 只要可贴记科目(非汇总)
                continue
            code = _clean(getattr(r, "ACCNUM", ""))
            if not code:
                continue
            name = _clean(getattr(r, "ACCNAM", "")) or _clean(getattr(r, "ACCNAM2", ""))
            out.append(
                ChartAccount(code=code, name=name, type=_clean(getattr(r, "GROUP", "")))
            )
            if len(out) >= _MAX_CHART:
                break
    finally:
        t.close()
    return out


def chart_accounts_payload(account_dir: str | Path) -> List[dict]:
    """heartbeat 上报科目表用(best-effort):缺/坏 → 返空(心跳不中断)。"""
    try:
        return [asdict(a) for a in probe_chart_of_accounts(account_dir)]
    except RegistryUnavailable:
        return []
    except Exception:  # noqa: BLE001 — 心跳隔离,任何读档异常都不该拖垮直写
        log.exception("chart_accounts_payload failed: %s", account_dir)
        return []


# ── 默认科目向量(G/L Link Account)· 读账套 ISINFO.ACCNUMxx → 6 角色默认科目 ──
# Express 自己自动过账即用 ISINFO 这排默认科目(官方帮助 10linkac「G/L Link Account」)。
# 索引→角色跨 4 账套(57ASCRD/dat/korn/test)实证一致:科目【代码】每账套不同
# (57ASCRD `1130-01` vs test `11-02-01-00`),但同一【索引】永远指向同一角色(泰文名一致)。
# → 必须按账套读 ISINFO,绝不硬编码代码。证据与映射见
#   docs/integrations/express-push/28-account-resolution-closed-loop.md §1.2/§1.4。
_ISINFO_DEFAULT_SLOTS = {
    "revenue_acc": "ACCNUM11",  # รายได้จากการขาย 销售收入(销项)
    "ar_acc": "ACCNUM02",  # ลูกหนี้การค้า 应收(销项·客户主档缺时回落)
    "vat_output_acc": "ACCNUM10",  # ภาษีขาย 销项税
    "fallback_acc": "ACCNUM04",  # 采购/存货 默认(进项·费用按性质由云端逐张精修)
    "ap_acc": "ACCNUM07",  # เจ้าหนี้การค้า 应付(进项·供应商主档缺时回落)
    "vat_input_acc": "ACCNUM06",  # ภาษีซื้อ 进项税
}


def probe_default_accounts(account_dir: str | Path) -> dict:
    """读账套 ISINFO.ACCNUMxx 默认科目向量 → {config_key: 科目码}(配对窗自动预填)。

    全程只读;文件缺/坏或某槽为空则该键略过(返回的 dict 只含读到的)。索引语义见模块顶注。
    """
    if not account_dir:
        return {}
    base = Path(account_dir)
    path = None
    for cand in (_INFO_DBF, _INFO_DBF.lower()):
        if (base / cand).exists():
            path = base / cand
            break
    if path is None:
        return {}
    out: dict = {}
    try:
        t = dbf.Table(str(path), codepage=S.CODEPAGE)
        t.open(dbf.READ_ONLY)
        try:
            if not len(t):
                return {}
            r = t[0]
            for cfg_key, slot in _ISINFO_DEFAULT_SLOTS.items():
                code = _clean(getattr(r, slot, ""))
                if code:
                    out[cfg_key] = code
        finally:
            t.close()
    except Exception:  # noqa: BLE001 — 只读探测,任何异常都不该拖垮配对/心跳
        log.exception("probe_default_accounts failed: %s", path)
        return {}
    return out
