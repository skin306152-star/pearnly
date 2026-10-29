# -*- coding: utf-8 -*-
"""服务品进项税口径（APTRN.SRV_VATTYP）按这本账套的历史定（10-29）。

规则是服务端共用写账规则 ``pearnly_core.express_rules.push.service_vat`` 的副本（CC 桌面版不依赖服务端包），
``tests/test_companion_contract.py`` 逐例比对两边结果一致；改规则两边一起改。

历史：本账套 APTRN 里 Express 的人录的赊购单（RECTYP 3、价格类型 1 / 2、没作废、不是 Pearnly 写的），按供应商记口径
``1`` / ``2``。只读几栏的原始字节（不经 dbf 库逐行解析）；按「账套路径 + 表头」缓存，表只往后追加时只读新增的行
（写一张单要查一次，网络盘上的大表不整表重读）；表头变了、行数变少（PACK）就整表重读。Express 里改动旧单的口径不会
反映进缓存，直到 CC 重启——历史只用来定「一贯哪一种」，个别旧单改动不改变结论。
"""
from __future__ import annotations

import struct
import threading
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

from .dbf_schema import PEARNLY_USERID

# ── 规则（与共用写账规则同一份，见文件头）────────────────────────────────────────
ACCRUAL = "1"   # เกณฑ์สิทธิ
CASH_BASIS = "2"  # เกณฑ์เงินสด
NO_VAT = "-"
VALUES = (ACCRUAL, CASH_BASIS)
OPTIONS = {"accrual": ACCRUAL, "cash_basis": CASH_BASIS}
CHOICE_CODE = "SERVICE_VAT_BASIS_CHOICE"
PAYLOAD_KEY = "srv_vattyp"
NO_HISTORY = "0"  # 随供应商主档上报：读过、这家没有历史（没有这个键 = 旧版没读）


def verdict(values: Iterable[str]) -> str:
    seen = {str(value or "").strip() for value in values or ()}
    return "".join(value for value in VALUES if value in seen)


def book_verdict(suppliers: Iterable[str]) -> str:
    return verdict("".join(str(item or "") for item in suppliers or ()))


def chosen_value(code) -> str:
    code = str(code or "").strip()
    return code if code in VALUES else OPTIONS.get(code, "")


def basis(*, has_vat: bool, cash: bool, service: bool, supplier: str = "", book: str = "",
          chosen: str = "") -> Optional[str]:
    if not has_vat:
        return NO_VAT
    if cash or not service:
        return ACCRUAL
    supplier, book = verdict(supplier), verdict(book)
    if supplier:
        decided = supplier if len(supplier) == 1 else ""
    else:
        decided = book if len(book) == 1 else ""
    return decided or chosen_value(chosen) or None


# ── 读历史 ──────────────────────────────────────────────────────────────────
_FIELDS = ("RECTYP", "SUPCOD", "FLGVAT", "SRV_VATTYP", "DOCSTAT", "USERID")
_CREDIT = "3"
_lock = threading.Lock()
# 账套路径 → (表头签名, 已读行数, {供应商: {口径}})
_cache: Dict[str, Tuple[tuple, int, Dict[str, set]]] = {}


def _aptrn(account_dir) -> Optional[Path]:
    for cand in ("APTRN.DBF", "aptrn.dbf"):
        path = Path(account_dir) / cand
        if path.is_file():
            return path
    return None


def _layout(head: bytes, f) -> Tuple[tuple, int, int, int, dict]:
    count, hlen, rlen = struct.unpack("<IHH", head[4:12])
    f.seek(32)
    fields, off = {}, 1
    raw = f.read(max(hlen - 32, 0))
    for pos in range(0, len(raw), 32):
        if raw[pos] == 0x0D:
            break
        name = raw[pos:pos + 11].split(b"\0")[0].decode("ascii", "replace").upper()
        size = raw[pos + 16]
        if name in _FIELDS:
            fields[name] = (off, size)
        off += size
    return (hlen, rlen, tuple(sorted(fields.items()))), count, hlen, rlen, fields


def _scan(f, start: int, count: int, hlen: int, rlen: int, fields: dict, found: Dict[str, set]) -> None:
    if "SRV_VATTYP" not in fields or "SUPCOD" not in fields:
        return
    chunk = 2000
    for first in range(start, count, chunk):
        f.seek(hlen + first * rlen)
        data = f.read(rlen * min(chunk, count - first))
        for base in range(0, len(data) - rlen + 1, rlen):
            rec = data[base:base + rlen]
            if rec[:1] == b"*":
                continue

            def get(name):
                off, size = fields.get(name, (0, 0))
                return rec[off:off + size].decode("cp874", "replace").strip() if size else ""

            value = get("SRV_VATTYP")
            if (value not in VALUES or get("RECTYP") != _CREDIT or get("FLGVAT") not in ("1", "2")
                    or get("DOCSTAT") == "C" or get("USERID").upper() == PEARNLY_USERID):
                continue
            found.setdefault(get("SUPCOD"), set()).add(value)


def history(account_dir) -> Dict[str, str]:
    """{供应商代码: 口径串}（只含有历史的）。表不在、读不了回 {}（当作整本没有历史）。"""
    path = _aptrn(account_dir)
    if path is None:
        return {}
    key = str(path)
    try:
        with open(path, "rb") as f:
            head = f.read(32)
            signature, count, hlen, rlen, fields = _layout(head, f)
            with _lock:
                cached = _cache.get(key)
            if cached and cached[0] == signature and cached[1] <= count:
                start, found = cached[1], {code: set(values) for code, values in cached[2].items()}
            else:
                start, found = 0, {}
            _scan(f, start, count, hlen, rlen, fields, found)
    except (OSError, struct.error, IndexError):
        return {}
    with _lock:
        _cache[key] = (signature, count, found)
    return {code: verdict(values) for code, values in found.items()}


def supplier_report(account_dir) -> Tuple[Dict[str, str], bool]:
    """随供应商主档上报用：({代码: 口径串}, 读没读成)。"""
    path = _aptrn(account_dir)
    if path is None:
        return {}, True
    try:
        return history(account_dir), True
    except Exception:  # noqa: BLE001 — 读坏了就不报这一项，服务端按旧版对待（不问，写时再定）
        return {}, False


def for_purchase(account_dir, *, has_vat: bool, cash: bool, service: bool, supplier_code: str,
                 chosen: str = "") -> Optional[str]:
    """一张进货单写进 Express 的 SRV_VATTYP；定不了回 None（调用方不写、回 :data:`CHOICE_CODE`）。"""
    if not has_vat or cash or not service:
        return basis(has_vat=has_vat, cash=cash, service=service)
    known = history(account_dir)
    return basis(has_vat=True, cash=False, service=True, supplier=known.get(str(supplier_code or "").strip(), ""),
                 book=book_verdict(known.values()), chosen=chosen)
