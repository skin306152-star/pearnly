"""POS 日汇总票的付款方式转账凭证(载荷 3 `pos.transfers`)。

Express 原生记法(2026-10-13 定):现销 HS 照常借现金(账套现金科目 ACCNUM00),之后每一种非现金付款方式(转账、QR、
刷卡、外卖平台)另记一张普通日记账凭证「借 收款科目 / 贷 现金」,金额 = 当天这种方式收的钱。凭证与销售单同一次写入:
同一把账套锁、同一份写前备份、任何一步失败一起回滚、写完逐张读回核对。

单据簿:账套 ISRUN 里普通日记账(JNLTYP '00')那一本的单别(PREFIX)。只有一本就用它;几本时用 JV;一本都没有、
或几本里没有 JV → 停下报 JOURNAL_BOOK_NOT_FOUND(不猜)。凭证号 = 单别 + 佛历年月日 + '-' + 当日序号(与销售单同一写法)。
收款科目必须在科目表里且可记账(ACCTYP '0'),否则报 ACCOUNT_NOT_POSTABLE。
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import dbf

from . import account_rules
from . import dbf_schema as S
from .dbf_writer import ERR_VERIFY, _WriteError, _bump_isrun, _gl_line, _open

ERR_JOURNAL_BOOK = "JOURNAL_BOOK_NOT_FOUND"
ERR_ACCOUNT = "ACCOUNT_NOT_POSTABLE"
GENERAL_JOURNAL = "00"
PREFERRED_PREFIX = "JV"
SEQIT_DEBIT = "1"
SEQIT_CREDIT = "2"
VAT_DESCRIPTION = "ใบกำกับภาษีอย่างย่อ"  # 销项税记录摘要:报税草稿据「อย่างย่อ」认出简易税票日汇总
_ISVAT_DESCRP = 60
_GL_DESCRP = 50


def fit(text: str, width: int) -> str:
    """按 cp874 字节截到定宽列(泰文一个字一个字节;编不了的字已由云端净化)。"""
    raw = str(text or "").encode("cp874", "replace")[:width]
    return raw.decode("cp874", "replace")


def range_text(first: str, last: str) -> str:
    return first if not last or last == first else f"{first}-{last}"


def vat_description(first: str, last: str) -> str:
    """销项税记录(ISVAT.DESCRP C(60))的摘要:「ใบกำกับภาษีอย่างย่อ 起-止」,放不下就截。"""
    return fit(f"{VAT_DESCRIPTION} {range_text(first, last)}", _ISVAT_DESCRP)


def sale_description(first: str, last: str) -> str:
    """日汇总销售单的分录摘要(GLJNL.DESCRP C(50))。"""
    return fit(f"ขายสด {range_text(first, last)}", _GL_DESCRP)


def journal_prefix(account_dir) -> Optional[str]:
    """普通日记账单据簿的单别;定不下来(没有、或几本里没有 JV)回 None。"""
    try:
        t = account_rules._open(account_dir, "ISRUN")
    except Exception:  # noqa: BLE001 — 读不到当没有
        return None
    if t is None:
        return None
    try:
        found: List[str] = []
        for r in S.live_rows(t):
            prefix = str(account_rules._gv(r, "PREFIX"))
            if str(account_rules._gv(r, "JNLTYP")) == GENERAL_JOURNAL and prefix and prefix not in found:
                found.append(prefix)
    finally:
        t.close()
    if PREFERRED_PREFIX in found:
        return PREFERRED_PREFIX
    return found[0] if len(found) == 1 else None


def check(account_dir, transfers: Iterable[Dict]) -> Tuple[Optional[str], Optional[str], str]:
    """写前核对 → ``(单别, 错误码, 说明)``;没问题时错误码为 None。没有转账凭证时不查单据簿。"""
    transfers = list(transfers or [])
    if not transfers:
        return None, None, ""
    prefix = journal_prefix(account_dir)
    if not prefix:
        return None, ERR_JOURNAL_BOOK, "no single general journal book (JNLTYP 00) in ISRUN"
    for row in transfers:
        if not account_rules.postable_account(account_dir, row.get("acc")):
            return None, ERR_ACCOUNT, f"receiving account {row.get('acc')!r} is not a postable account"
    return prefix, None, ""


def _next_voucher(table, prefix: str, docdate_be: str) -> str:
    """凭证号 = 单别 + 佛历年月日 + '-' + 当日序号(3 位)。连软删行一起数(删掉的号在会计手上仍是用过的号)。"""
    head = f"{prefix}{docdate_be}-"
    seq = 0
    for r in table:
        voucher = (r.VOUCHER or "").strip()
        if voucher.startswith(head):
            try:
                seq = max(seq, int(voucher[len(head):]))
            except ValueError:
                continue
    return f"{head}{seq + 1:03d}"


def sizes(account_dir) -> Dict[str, int]:
    """写前两张分录表的行数:读回只核对这之后追加的行。"""
    out = {}
    for name in ("GLJNL", "GLJNLIT"):
        t = _open(Path(account_dir), name, dbf.READ_ONLY)
        try:
            out[name] = len(t)
        finally:
            t.close()
    return out


def write(account_dir, prefix: str, *, docnum: str, docdat, docdate_be: str, cash_acc: str,
          transfers: Iterable[Dict], userid: str, depcod: str = "", departmental=frozenset()) -> List[Dict]:
    """逐种付款方式记一张凭证「借 收款科目 / 贷 现金」(与销售单同一套开表、分录行与计数器写法);
    返回 ``[{voucher, acc, amount}]``。"""
    written: List[Dict] = []
    gj = _open(Path(account_dir), "GLJNL", dbf.READ_WRITE)
    try:
        for row in transfers:
            voucher = _next_voucher(gj, prefix, docdate_be)
            descrp = fit(f"{row.get('label') or ''} {docnum}".strip(), _GL_DESCRP)
            gj.append({"JNLTYP": GENERAL_JOURNAL, "VOUDAT": docdat, "VOUCHER": voucher, "DESCRP": descrp,
                       "TRNSTAT": "P", "DOCSTAT": "N", "CREBY": userid})
            written.append({"voucher": voucher, "acc": str(row["acc"]), "amount": Decimal(str(row["amount"])),
                            "descrp": descrp})
    finally:
        gj.close()
    gi = _open(Path(account_dir), "GLJNLIT", dbf.READ_WRITE)
    try:
        for item in written:
            gi.append(_gl_line(item["voucher"], SEQIT_DEBIT, docdat, item["acc"], item["descrp"], S.TRNTYP_DEBIT,
                               item["amount"], depcod, departmental))
            gi.append(_gl_line(item["voucher"], SEQIT_CREDIT, docdat, cash_acc, item["descrp"], S.TRNTYP_CREDIT,
                               item["amount"], depcod, departmental))
    finally:
        gi.close()
    for _item in written:
        _bump_isrun(Path(account_dir), prefix, GENERAL_JOURNAL)
    return [{"voucher": item["voucher"], "acc": item["acc"], "amount": item["amount"]} for item in written]


def verify(account_dir, written: List[Dict], cash_acc: str, since: Dict[str, int]) -> None:
    """读回:每张凭证的表头在(普通日记账)、恰好两行分录、借收款科目 = 贷现金 = 这种方式的金额。不符抛写盘错误(回滚)。"""
    if not written:
        return
    gj = _open(Path(account_dir), "GLJNL", dbf.READ_ONLY)
    try:
        heads = {(r.VOUCHER or "").strip(): (r.JNLTYP or "").strip()
                 for r in S.live_rows(gj) if dbf.recno(r) >= since.get("GLJNL", 0)}
    finally:
        gj.close()
    gi = _open(Path(account_dir), "GLJNLIT", dbf.READ_ONLY)
    try:
        lines: Dict[str, List] = {}
        for r in S.live_rows(gi):
            if dbf.recno(r) >= since.get("GLJNLIT", 0):
                lines.setdefault((r.VOUCHER or "").strip(), []).append(
                    ((r.TRNTYP or "").strip(), (r.ACCNUM or "").strip(), Decimal(str(r.AMOUNT))))
    finally:
        gi.close()
    for item in written:
        voucher, amount = item["voucher"], Decimal(item["amount"]).quantize(Decimal("0.01"))
        rows = lines.get(voucher) or []
        debit = [(acc, amt) for side, acc, amt in rows if side == S.TRNTYP_DEBIT]
        credit = [(acc, amt) for side, acc, amt in rows if side != S.TRNTYP_DEBIT]
        if (heads.get(voucher) != GENERAL_JOURNAL or len(rows) != 2
                or debit != [(item["acc"], amount)] or credit != [(cash_acc, amount)]):
            raise _WriteError(
                f"POS transfer voucher {voucher} not written as Dr {item['acc']} / Cr {cash_acc} {amount}",
                ERR_VERIFY,
                {"kind": "readback_verify", "table": "GLJNLIT", "docnum": voucher, "expected_total": float(amount),
                 "rows": len(rows)},
            )
