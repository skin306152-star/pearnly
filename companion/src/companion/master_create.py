# -*- coding: utf-8 -*-
"""新供应商/客户自动建档(直写 APMAS/ARMAS 前置)。

真客户的票绝大多数是新户;writer 原本无码即拒(ERR_NEW_SUPPLIER/CUSTOMER)。本模块在
直写前 find-or-create 主档,拿到 code 再走原过账。字段逐列对真 DATAT gold 锁定
(APMAS 481 行 / ARMAS 540 行):

  APMAS  SUPCOD(首字符+3位序号·按字母段自增)· SUPTYP='00' · PRENAM · SUPNAM ·
         VATRAT=7 · FLGVAT · ACCNUM(应付·gold 全表 21-02-01-00)· TAXID · USERID ·
         CREBY/CREDAT/CHGDAT · STATUS='A'
  ARMAS  CUSCOD · CUSTYP='00' · PRENAM · CUSNAM · ACCNUM(应收·gold 11-02-01-00)·
         TABPR='0' · TAXID · USERID · CREBY/CREDAT/CHGDAT · STATUS='A'

可见性关键 = STATUS='A'(类比交易的 CMPLAPP='Y')。默认科目从分录推:进项 AP=贷方行 acc,
销项 AR=借方行 acc(与 gold 一致),config 可兜底覆盖。

幂等:先按 TAXID 精确、再按名字精确查;命中即复用,绝不重复建。建档在 writer 的备份-回滚
事务内调用(失败整体还原)。账套白名单沿用 dbf_writer.account_dir_allowed。
"""

from __future__ import annotations

import datetime as _dt
import difflib
from pathlib import Path
from typing import List, Optional, Tuple

import dbf

from . import dbf_schema as S

_PRENAMES = (
    "บริษัทจำกัด",
    "บริษัท",
    "ห้างหุ้นส่วนจำกัด",
    "ห้างหุ้นส่วนสามัญ",
    "หจก.",
    "หจก",
    "หสน.",
    "ร้าน",
)

_MAX_NAME = 60
_MAX_TAXID = 15
_MAX_PRENAM = 15
# 地址列宽逐列对真 DATAT gold(ADDR01/02 C50 · ADDR03 C30)。
_ADDR_WIDTHS = (50, 50, 30)

# gold 默认科目(分录推不出时兜底)
_DEFAULT_AP_ACC = "21-02-01-00"  # 应付账款(供应商)
_DEFAULT_AR_ACC = "11-02-01-00"  # 应收账款(客户)

# 疑似重复阈值:核心名归一后相似度 ≥ 此值 + 税号不同/缺 → 不静默建,转人工。
# 偏保守(宁可多问一次也不建重复户·Owner 拍板)。
_DUP_RATIO = 0.90


class AmbiguousPartyMatch(Exception):
    """同税号命中多档且分店号/名字都定不出唯一 → 上层转人工。

    连锁客户(Big C/Lotus's/7-11)全部分店共用一个税号,取第一条会把分店单
    静默记到总店头上(2026-08-06 真账实锤),错记比转人工贵得多。
    """

    def __init__(self, table: str, tax_id: str, codes: List[str]):
        self.table = table
        self.tax_id = tax_id
        self.codes = codes
        super().__init__(f"{table} tax {tax_id} matches {len(codes)} parties: {codes}")


def _branch_no(s: str) -> str:
    """载荷分店号 → 5 位数字串。空/非数字/超长 → ''(=未提供)。

    未提供必须跳过分店判据而不是当总店 00000 硬配 —— 没填≠总店,
    否则没填分店号的分店票会全部错配到总店档。
    """
    digits = "".join(ch for ch in str(s or "") if ch.isdigit())
    if not digits or len(digits) > 5:
        return ""
    return digits.zfill(5)


def _row_branch(r) -> str:
    """ARMAS/APMAS 行 ORGNUM → 5 位分店号串。空/0 = 总店 '00000'(Express 现实:总店档普遍不填)。"""
    return S.orgnum_to_branch5(getattr(r, "ORGNUM", 0))


def party_orgnum(account_dir: Path, table: str, codefield: str, code: str) -> int:
    """对方档(APMAS/ARMAS)这条码的 ORGNUM(分店号,0 = 总店);查不到按总店 0。

    凭证表头与进 / 销项税记录的分店号跟着对方档走(真账:表头 = 档 99%+,税记录 = 表头 99%+),
    税表(ภ.พ.30)上的分店一栏就是它。
    """
    code = str(code or "").strip()
    if not code:
        return 0
    t = _open(account_dir, table, dbf.READ_ONLY)
    try:
        for r in S.live_rows(t):
            if (getattr(r, codefield) or "").strip() == code:
                return int(S.orgnum_to_branch5(getattr(r, "ORGNUM", 0)))
    finally:
        t.close()
    return 0


def _supplier_flgvat(doctype: str, has_vat: bool) -> str:
    """供应商主档默认 VAT 类型(与交易/单别口径一致·真账实测语义):
    无税=0;现购 HP=价含税 '1';其余(赊购 RR / 默认税票)=价不含税 '2'。
    真账主流是 '2'(317/481·标准税票 base 与 VAT 分开);旧写死 '1' 对赊购供应商是错的默认。"""
    if not has_vat:
        return "0"
    return "1" if str(doctype or "").upper() == "HP" else "2"


def _split_address(addr: str) -> Tuple[str, str, str]:
    """单行地址 → ADDR01/02/03。优先在列宽边界前的空白处断行(不切断词);无空白则硬切。
    与真账录入一致:地址进主档,开全额税票时随名字/税号带出。空地址 → 三空串。"""
    s = " ".join((addr or "").split())  # 归一空白
    if not s:
        return "", "", ""
    parts: List[str] = []
    for i, width in enumerate(_ADDR_WIDTHS):
        if not s:
            parts.append("")
            continue
        if len(s) <= width or i == len(_ADDR_WIDTHS) - 1:
            parts.append(s[:width])
            s = s[width:]
            continue
        cut = s.rfind(" ", 0, width + 1)
        cut = cut if cut > 0 else width
        parts.append(s[:cut].rstrip())
        s = s[cut:].lstrip()
    return parts[0], parts[1], parts[2]


def _norm_space(s: str) -> str:
    """精确比名前的空白归一:不换行空格(\\xa0)→ 普通空格,连续空白压一个,去首尾。

    真账套里大量泰文名字带 \\xa0(Express 录入习惯 —— 实测该账套 550 个客户约 1/3 带),
    而票面 OCR 出来是普通空格。不归一就永远比不中:同一个客户会被判「疑似重复」整票转人工,
    复用形同虚设。只归一空白、不动标点大小写,避免把不同客户误并。
    """
    return " ".join((s or "").replace("\xa0", " ").split())


def _norm_match(name: str) -> str:
    """名字归一(模糊查重用):去法人前缀 + 去空白标点 + 小写 → 只留字母数字泰字。"""
    s = _strip_prename(name).lower()
    return "".join(ch for ch in s if ch.isalnum())


def _open(account_dir: Path, name: str, mode):
    t = dbf.Table(str(account_dir / f"{name}.DBF"), codepage=S.CODEPAGE)
    t.open(mode)
    return t


def _strip_prename(name: str) -> str:
    """去掉法人前缀,取核心名(SUPNAM/CUSNAM 存核心名,PRENAM 单列)。"""
    s = (name or "").strip()
    for p in _PRENAMES:
        if s.startswith(p):
            return s[len(p) :].strip()
    return s


def _code_prefix(core_name: str) -> str:
    """编码首字符 = 核心名首字符(gold:อีซูซุ→อ / ปิยะจิต→ป)。空名兜底 'X'。"""
    c = (core_name or "").strip()
    return c[0] if c else "X"


def _next_code(existing: List[str], prefix: str) -> str:
    """该前缀下一个可用 3 位序号码,且不与任何现有码冲突。"""
    seen = set(existing)
    maxn = 0
    for code in existing:
        if code.startswith(prefix):
            suf = code[len(prefix) :]
            if suf.isdigit():
                maxn = max(maxn, int(suf))
    n = maxn + 1
    while f"{prefix}{n:03d}" in seen:
        n += 1
    return f"{prefix}{n:03d}"


def _all_codes(account_dir: Path, table: str, codefield: str) -> List[str]:
    """已用过的码(**含软删行**)—— 只给 _next_code 发新码用。

    删掉的客户 ก001 在旧单据、旧对账单上仍是这个码;跳过软删行发号就会把它再发给别的客户,
    PACK 前索引里同码两条、PACK 后历史单据指向新客户。发号问"用没用过",与查重问
    "还算不算数"(_find_code 走 S.live_rows)是两个判据。
    """
    t = _open(account_dir, table, dbf.READ_ONLY)
    try:
        return [
            (getattr(r, codefield) or "").strip()
            for r in t
            if (getattr(r, codefield) or "").strip()
        ]
    finally:
        t.close()


def _find_code(
    account_dir: Path,
    table: str,
    codefield: str,
    namefield: str,
    tax_id: str,
    core_name: str,
    branch: str = "",
) -> Optional[str]:
    """幂等查重(级联):
    ① TAXID 唯一命中 → 复用;例外:载荷明确分店号且与该档 ORGNUM 不符 → 当未命中走建档。
    ② 同税号多档(连锁分店):载荷带分店号则配 ORGNUM 取唯一(同分店多档再按核心名取唯一);
       这家分店还没有档 → 当未命中走建档;载荷没带分店号 → 核心名精确取唯一;
       仍定不出 → raise AmbiguousPartyMatch 转人工,绝不静默取第一条。
    ③ 税号零命中 → 核心名精确后备(不变)。

    **分店号明确时不拿名字挂回别的分店**(用户 2026-09-27 · 旧项目 Big C 分店票记到总店名下):连锁
    各分店的档名往往一模一样,按名字复用就等于把分店票记到总店 / 别的分店头上。真账里连锁客户
    本来就是一个分店一条档(Moritomo 一个客户几十条分店档),凭证与税记录的分店号跟着档走。

    只认还算数的档:会计删掉一个客户后复用它的码,新单就挂在 Express 里根本列不出来的
    对手方上 —— 单据落地了却查无此客户,比多建一个档难查得多。
    """
    tax = (tax_id or "").strip()
    name = _norm_space(core_name)
    want_branch = _branch_no(branch)
    t = _open(account_dir, table, dbf.READ_ONLY)
    try:
        tax_hits: List[Tuple[str, str, str]] = []  # (code, 归一名, 分店号)
        by_name = None
        for r in S.live_rows(t):
            code = (getattr(r, codefield) or "").strip()
            if not code:
                continue
            if tax and (getattr(r, "TAXID", "") or "").strip() == tax:
                row_name = _norm_space(getattr(r, namefield, "") or "")
                tax_hits.append((code, row_name, _row_branch(r)))
                continue
            # 名字归一逐行有真开销(泰文全表几千行 × 每单一次),只在还没有后备命中时算。
            if name and by_name is None:
                if _norm_space(getattr(r, namefield, "") or "") == name:
                    by_name = code
    finally:
        t.close()
    if not tax_hits:
        return by_name
    if len(tax_hits) == 1:
        code, row_name, row_branch = tax_hits[0]
        if not want_branch or row_branch == want_branch:
            return code
        # 载荷明确分店号,与唯一命中档不符 → 不是同一分店,当未命中走建档(新档落正确 ORGNUM;
        # 同税号建档不触发疑似重复拦截)。名字相同也不复用:连锁各分店档名往往一样。
        return None
    if want_branch:
        branch_hits = [h for h in tax_hits if h[2] == want_branch]
        if len(branch_hits) == 1:
            return branch_hits[0][0]
        if not branch_hits:
            # 这家分店还没有档 → find-or-create 建新分店档,不拿名字挂到别的分店。
            return None
        if name:
            name_hits = [c for c, n, _ in branch_hits if n == name]
            if len(name_hits) == 1:
                return name_hits[0]
        raise AmbiguousPartyMatch(table, tax, [c for c, _, _ in branch_hits])
    if name:
        name_hits = [c for c, n, _ in tax_hits if n == name]
        if len(name_hits) == 1:
            return name_hits[0]
    raise AmbiguousPartyMatch(table, tax, [c for c, _, _ in tax_hits])


def _ap_acc_from_lines(lines, fallback: str) -> str:
    """进项:供应商默认科目 = 贷方(应付)行 acc。"""
    for ln in lines or []:
        if str(ln.get("side", "")).upper() == "C":
            acc = str(ln.get("acc") or "").strip()
            if acc:
                return acc
    return fallback


def _ar_acc_from_lines(lines, fallback: str) -> str:
    """销项:客户默认科目 = 借方(应收/资产)行 acc。"""
    for ln in lines or []:
        if str(ln.get("side", "")).upper() == "D":
            acc = str(ln.get("acc") or "").strip()
            if acc:
                return acc
    return fallback


def _reindex(account_dir: Path, table: str) -> None:
    t = _open(account_dir, table, dbf.READ_WRITE)
    try:
        t.reindex()
    finally:
        t.close()


def find_supplier_code(
    account_dir: Path, tax_id: str, name: str, branch: str = ""
) -> Optional[str]:
    """只读查已有供应商码(备份前幂等查重用)。同税号多档定不出唯一时 raise AmbiguousPartyMatch。"""
    return _find_code(
        account_dir, "APMAS", "SUPCOD", "SUPNAM", tax_id, _strip_prename(name), branch
    )


def find_customer_code(
    account_dir: Path, tax_id: str, name: str, branch: str = ""
) -> Optional[str]:
    """只读查已有客户码。同税号多档定不出唯一时 raise AmbiguousPartyMatch。"""
    return _find_code(
        account_dir, "ARMAS", "CUSCOD", "CUSNAM", tax_id, _strip_prename(name), branch
    )


def _find_suspected_dup(
    account_dir: Path,
    table: str,
    codefield: str,
    namefield: str,
    tax_id: str,
    name: str,
) -> Optional[Tuple[str, str]]:
    """精确(税号/名字)未命中时查"疑似重复":核心名归一相似度 ≥ 阈值,但税号不同/缺、
    且非完全同名(完全同名/同税号 _find_code 已处理)。命中返回 (code, 现有名),否则 None。
    给"疑似重复转人工",绝不静默建出重复供应商/客户。"""
    target = _norm_match(name)
    if len(target) < 3:  # 太短的核心名不做模糊(误报高)
        return None
    tax = (tax_id or "").strip()
    t = _open(account_dir, table, dbf.READ_ONLY)
    try:
        # 已删掉的档不该再拦人:拿它判"疑似重复"会把整票推去人工,而会计打开 Express 压根
        # 看不到那个重复户,只能干瞪眼。
        for r in S.live_rows(t):
            code = (getattr(r, codefield, "") or "").strip()
            if not code:
                continue
            other_tax = (getattr(r, "TAXID", "") or "").strip()
            if tax and other_tax and tax == other_tax:
                continue  # 同税号=真匹配
            cand = _norm_match(getattr(r, namefield, "") or "")
            if not cand:
                continue
            # 能走到这=_find_code 未命中(税号不同/缺)且本行税号≠来件税号。归一同名
            # (cand==target·OCR 声调符/空格变体)是最强疑似重复信号,直接命中、省去相似度计算;
            # 名近(ratio≥阈值)再算。两者都该转人工,绝不静默建重复。
            if (
                cand == target
                or difflib.SequenceMatcher(None, target, cand).ratio() >= _DUP_RATIO
            ):
                return code, (getattr(r, namefield, "") or "").strip()
        return None
    finally:
        t.close()


def find_suspected_supplier_dup(
    account_dir: Path, tax_id: str, name: str
) -> Optional[Tuple[str, str]]:
    return _find_suspected_dup(account_dir, "APMAS", "SUPCOD", "SUPNAM", tax_id, name)


def find_suspected_customer_dup(
    account_dir: Path, tax_id: str, name: str
) -> Optional[Tuple[str, str]]:
    return _find_suspected_dup(account_dir, "ARMAS", "CUSCOD", "CUSNAM", tax_id, name)


def suspected_supplier_dup(account_dir: Path, entry) -> bool:
    """新供应商建前的疑似重复判定(writer 用 · entry 接口收口)。"""
    if entry.supplier_code:
        return False
    return (
        find_suspected_supplier_dup(
            account_dir, entry.supplier_tax_id, entry.supplier_name
        )
        is not None
    )


def suspected_customer_dup(account_dir: Path, entry) -> bool:
    """新客户建前的疑似重复判定(销项 writer 用 · 镜像 suspected_supplier_dup)。"""
    if entry.customer_code:
        return False
    return (
        find_suspected_customer_dup(
            account_dir, entry.customer_tax_id, entry.customer_name
        )
        is not None
    )


def _verify_row(
    account_dir: Path, table: str, codefield: str, code: str, tax_id: str
) -> Optional[str]:
    """读回校验新建的主档行确实落了(code 在 + 税号对得上)。ok 返 None;否则返错误描述。

    只看活行:同码的软删旧档排在前面时,会拿旧档的税号来判新档,对错两个方向都可能误判。
    """
    t = _open(account_dir, table, dbf.READ_ONLY)
    try:
        for r in S.live_rows(t):
            if (getattr(r, codefield, "") or "").strip() == code:
                want = (tax_id or "").strip()
                got = (getattr(r, "TAXID", "") or "").strip()
                if want and got != want:
                    return f"{table} {code} TAXID readback mismatch ({got!r}!={want!r})"
                return None
        return f"{table} row {code} not found after create"
    finally:
        t.close()


def verify_supplier_row(
    account_dir: Path, code: str, tax_id: str = ""
) -> Optional[str]:
    return _verify_row(account_dir, "APMAS", "SUPCOD", code, tax_id)


def verify_customer_row(
    account_dir: Path, code: str, tax_id: str = ""
) -> Optional[str]:
    return _verify_row(account_dir, "ARMAS", "CUSCOD", code, tax_id)


def ensure_supplier(
    account_dir: Path,
    entry,
    *,
    created_date: _dt.date,
    userid: str = "BIT9",
    ap_acc: str = "",
) -> str:
    """find-or-create 供应商,返回 SUPCOD。已在备份-回滚事务内调用。"""
    if entry.supplier_code:
        return entry.supplier_code
    core = _strip_prename(entry.supplier_name)
    # entry 是 duck-typed 接口(测试桩/旧调用方可能没有 branch 字段),getattr 不是防御过度。
    branch = _branch_no(getattr(entry, "supplier_branch", ""))
    found = None if getattr(entry, "supplier_confirmed_new", False) else _find_code(
        account_dir, "APMAS", "SUPCOD", "SUPNAM", entry.supplier_tax_id, core, branch
    )
    if found:
        return found

    codes = _all_codes(account_dir, "APMAS", "SUPCOD")
    code = _next_code(codes, _code_prefix(core))
    accnum = _ap_acc_from_lines(entry.lines, ap_acc or _DEFAULT_AP_ACC)
    has_vat = str(entry.vat_rate or "0") not in ("0", "")
    addr1, addr2, addr3 = _split_address(getattr(entry, "supplier_address", ""))

    t = _open(account_dir, "APMAS", dbf.READ_WRITE)
    try:
        t.append(
            {
                "SUPCOD": code,
                "SUPTYP": "00",
                "PRENAM": entry.supplier_prename[:_MAX_PRENAM],
                "SUPNAM": core[:_MAX_NAME],
                "ADDR01": addr1,
                "ADDR02": addr2,
                "ADDR03": addr3,
                "VATRAT": 7.0 if has_vat else 0.0,
                "FLGVAT": _supplier_flgvat(getattr(entry, "doctype", ""), has_vat),
                "ACCNUM": accnum,
                "TAXID": entry.supplier_tax_id[:_MAX_TAXID],
                "ORGNUM": int(branch or 0),
                "USERID": userid,
                "CREBY": userid,
                "CREDAT": created_date,
                "CHGDAT": created_date,
                "STATUS": "A",
            }
        )
    finally:
        t.close()
    _reindex(account_dir, "APMAS")
    err = verify_supplier_row(account_dir, code, tax_id=entry.supplier_tax_id)
    if err:  # 建完读不回 → 抛错,由 writer 备份回滚
        raise ValueError(err)
    return code


def ensure_customer(
    account_dir: Path,
    entry,
    *,
    created_date: _dt.date,
    userid: str = "BIT9",
    ar_acc: str = "",
) -> str:
    """find-or-create 客户,返回 CUSCOD。已在备份-回滚事务内调用。"""
    if entry.customer_code:
        return entry.customer_code
    core = _strip_prename(entry.customer_name)
    # 同 ensure_supplier:entry 是 duck-typed 接口,getattr 兼容无 branch 字段的调用方。
    branch = _branch_no(getattr(entry, "customer_branch", ""))
    found = None if getattr(entry, "customer_confirmed_new", False) else _find_code(
        account_dir, "ARMAS", "CUSCOD", "CUSNAM", entry.customer_tax_id, core, branch
    )
    if found:
        return found

    codes = _all_codes(account_dir, "ARMAS", "CUSCOD")
    code = _next_code(codes, _code_prefix(core))
    accnum = _ar_acc_from_lines(entry.lines, ar_acc or _DEFAULT_AR_ACC)
    addr1, addr2, addr3 = _split_address(getattr(entry, "customer_address", ""))

    t = _open(account_dir, "ARMAS", dbf.READ_WRITE)
    try:
        t.append(
            {
                "CUSCOD": code,
                "CUSTYP": "00",
                "PRENAM": entry.customer_prename[:_MAX_PRENAM],
                "CUSNAM": core[:_MAX_NAME],
                "ADDR01": addr1,
                "ADDR02": addr2,
                "ADDR03": addr3,
                "ACCNUM": accnum,
                "TABPR": "0",
                "TAXID": entry.customer_tax_id[:_MAX_TAXID],
                "ORGNUM": int(branch or 0),
                "USERID": userid,
                "CREBY": userid,
                "CREDAT": created_date,
                "CHGDAT": created_date,
                "STATUS": "A",
            }
        )
    finally:
        t.close()
    _reindex(account_dir, "ARMAS")
    err = verify_customer_row(account_dir, code, tax_id=entry.customer_tax_id)
    if err:  # 建完读不回 → 抛错,由 writer 备份回滚(与 ensure_supplier 同口径)
        raise ValueError(err)
    return code
