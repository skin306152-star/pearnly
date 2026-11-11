# -*- coding: utf-8 -*-
"""收付款单共用的三样（只读，按类通用）：Express 的收付款方式、方式行编号、单据簿的分录摘要表达式。

收款单 RE（``dbf_receipt``，载荷 20）先用；以后付款单 PS（ISRUN ``ZP`` 行、APRCPCQ）、现销 HS 收转账（ARRCPCQ 一行）照同一套。
口径来自 NLTTEST 2920 张原生完成的 RE（评估 ``docs/work/2026-11-11-collection-receipt-assessment.md`` 第 0 步）：

- 方式（:func:`types`）：ISRUN ``DOCTYP = ZR``（付款是 ``ZP``）的每一行一种，``PREFIX`` 是这一种的前缀。科目：``SHORTNAM`` 指向银行主档
  ``BKMAS.BNKACC`` 时取那家银行的 ``ACCNUM``（TB→B1→1112-01、KB→K1→1112-03，这两种自己的科目栏是空的），否则取本行 ``ACCNUM01``。
  两处都没有 → ``missing``；两处都有又不同、或同一前缀几行科目不同 → ``conflict``（写入按前缀认类型，DOCCOD 在真账套里会重复：NLTTEST
  ``22`` 有 SA 与 ``22`` 两种、``01`` QR 重复两行内容相同，内容相同不算冲突）。
- 方式行编号（:func:`line_number`）：前缀 + 单号，截到 12 字（``CHQNUM`` C(12)，例 ``TBRE2403013.``）。
- 摘要（:func:`journal_text`）：单据簿行 ``JNLEXP`` 是 xBase 表达式，例 ``'รับชำระหนี้   ' + (ARMAS->PRENAM - '\xa0' - ARMAS->CUSNAM)``。
  只认字符串常量、``+`` / ``-``、括号与 ``表->字段`` / 字段名；``-`` 照 xBase 把左边的尾部空格挪到结果末尾。认不出 → 用这份默认表达式。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Mapping, Optional

import dbf

from . import dbf_schema as S

RECEIPT_TYPES = "ZR"   # 收款方式（ISRUN.DOCTYP）
PAYMENT_TYPES = "ZP"   # 付款方式（付款单 PS 用，本批不写）
ISSUE_MISSING = "missing"
ISSUE_CONFLICT = "conflict"
LINE_NUMBER_WIDTH = 12
DEFAULT_RECEIPT_JNLEXP = "'รับชำระหนี้   ' + (ARMAS->PRENAM - '\xa0' - ARMAS->CUSNAM)"


def _open(account_dir, name: str):
    for candidate in (f"{name}.DBF", f"{name.lower()}.dbf"):
        path = Path(account_dir) / candidate
        if path.is_file():
            table = dbf.Table(str(path), codepage=S.CODEPAGE, unicode_errors=S.UNICODE_ERRORS)
            table.open(dbf.READ_ONLY)
            return table
    return None


def _text(rec, name: str) -> str:
    try:
        return str(rec[name] or "").strip()
    except (KeyError, dbf.FieldMissingError):
        return ""


def banks(account_dir) -> Dict[str, str]:
    """银行主档 BKMAS：BNKACC → 科目（ACCNUM）。表没有 → 空。"""
    table = _open(account_dir, "BKMAS")
    if table is None:
        return {}
    try:
        return {_text(r, "BNKACC"): _text(r, "ACCNUM") for r in S.live_rows(table) if _text(r, "BNKACC")}
    finally:
        table.close()


def types(account_dir, doctyp: str = RECEIPT_TYPES) -> List[dict]:
    """这本账套的收（付）款方式，ISRUN 里逐行照原样给（重复的行也给）：
    ``{code, prefix, name, name_en, bank, account, account_issue}``，``account_issue`` 空 / ``missing`` / ``conflict``。"""
    table = _open(account_dir, "ISRUN")
    if table is None:
        return []
    bank_accounts = banks(account_dir)
    try:
        rows = []
        for r in S.live_rows(table):
            if _text(r, "DOCTYP") != doctyp:
                continue
            prefix = _text(r, "PREFIX")
            if not prefix:
                continue
            own, bank = _text(r, "ACCNUM01"), _text(r, "SHORTNAM")
            via_bank = bank_accounts.get(bank, "") if bank else ""
            issue = ""
            if via_bank and own and via_bank != own:
                issue = ISSUE_CONFLICT
            account = via_bank or own
            if not account:
                issue = ISSUE_MISSING
            rows.append({"code": _text(r, "DOCCOD"), "prefix": prefix, "name": _text(r, "POSDES"),
                         "name_en": _text(r, "POSDES2"), "bank": bank, "account": account if not issue else "",
                         "account_issue": issue, "_resolved": account})
    finally:
        table.close()
    by_prefix: Dict[str, set] = {}
    for row in rows:
        by_prefix.setdefault(row["prefix"], set()).add(row["_resolved"])
    for row in rows:
        if len(by_prefix[row["prefix"]]) > 1:
            row["account_issue"], row["account"] = ISSUE_CONFLICT, ""
        row.pop("_resolved")
    return rows


def account_of(rows: List[dict], prefix: str) -> tuple:
    """按前缀认一种方式 → (科目, 问题)。问题：``not_found`` / ``missing`` / ``conflict``，有问题时科目为空。"""
    hits = [row for row in rows if row["prefix"] == prefix]
    if not hits:
        return "", "not_found"
    issue = next((row["account_issue"] for row in hits if row["account_issue"]), "")
    return ("", issue) if issue else (hits[0]["account"], "")


def line_number(prefix: str, docnum: str) -> str:
    """方式行编号 = 前缀 + 单号，截到 12 字（原生 ``CHRE2401367.``）。"""
    return f"{prefix}{docnum}"[:LINE_NUMBER_WIDTH]


# ── 单据簿摘要表达式（xBase 字符串运算的一小部分）─────────────────────────────────────────────

_TOKEN = re.compile(r"\s*(?:'([^']*)'|\"([^\"]*)\"|(\+|-|\(|\))|([A-Za-z_][A-Za-z0-9_]*)\s*->\s*([A-Za-z_][A-Za-z0-9_]*)"
                    r"|([A-Za-z_][A-Za-z0-9_]*))")


class _Unsupported(ValueError):
    pass


def _tokens(expr: str) -> list:
    out, pos = [], 0
    expr = expr.rstrip()
    while pos < len(expr):
        m = _TOKEN.match(expr, pos)
        if not m or m.end() == pos:
            raise _Unsupported(expr[pos:pos + 10])
        pos = m.end()
        single, double, op, alias, field, bare = m.groups()
        if single is not None or double is not None:
            out.append(("str", single if single is not None else double))
        elif op:
            out.append(("op", op))
        elif field:
            out.append(("field", alias.upper(), field.upper()))
        else:
            out.append(("field", "", bare.upper()))
    return out


def _minus(left: str, right: str) -> str:
    """xBase 的 ``a - b``：a 去尾部空格 + b + a 的尾部空格。"""
    core = left.rstrip(" ")
    return core + right + left[len(core):]


def _evaluate(tokens: list, values: Mapping[str, Mapping[str, str]]) -> str:
    pos = 0

    def atom():
        nonlocal pos
        if pos >= len(tokens):
            raise _Unsupported("end")
        token = tokens[pos]
        pos += 1
        if token[0] == "str":
            return token[1]
        if token[0] == "field":
            alias, field = token[1], token[2]
            table = values.get(alias) if alias else next((t for t in values.values() if field in t), None)
            if table is None or field not in table:
                raise _Unsupported(field)
            return table[field]
        if token == ("op", "("):
            value = expression()
            if pos >= len(tokens) or tokens[pos] != ("op", ")"):
                raise _Unsupported(")")
            pos += 1
            return value
        raise _Unsupported(str(token))

    def expression():
        nonlocal pos
        value = atom()
        while pos < len(tokens) and tokens[pos][0] == "op" and tokens[pos][1] in "+-":
            op = tokens[pos][1]
            pos += 1
            other = atom()
            value = value + other if op == "+" else _minus(value, other)
        return value

    result = expression()
    if pos != len(tokens):
        raise _Unsupported("trailing")
    return result


def journal_text(expr: str, values: Mapping[str, Mapping[str, str]], *, default: str = DEFAULT_RECEIPT_JNLEXP) -> str:
    """单据簿 ``JNLEXP`` 算出的摘要（未截宽）。``values``：表别名 → {字段: 定宽原值（带尾部空格）}。
    表达式认不出 / 用到没给的字段 → 用 ``default``；它也算不出 → 空串。"""
    for candidate in (expr, default):
        if not str(candidate or "").strip():
            continue
        try:
            return _evaluate(_tokens(str(candidate)), values)
        except _Unsupported:
            continue
    return ""


def padded(table, rec, names) -> Dict[str, str]:
    """一行里这几栏的定宽原值（字符栏按列宽补齐尾部空格，xBase 表达式按定宽算）。"""
    out: Dict[str, str] = {}
    widths = {}
    for spec in table.structure():
        parts = spec.split()
        if len(parts) >= 2 and parts[1].startswith("C("):
            widths[parts[0].upper()] = int(parts[1][2:-1])
    for name in names:
        key = name.upper()
        try:
            value = rec[key]
        except (KeyError, dbf.FieldMissingError):
            continue
        text = "" if value is None else str(value).rstrip(" ")
        out[key] = text.ljust(widths.get(key, len(text)))
    return out


def lookup_padded(account_dir, table_name: str, key_field: str, key: str, names) -> Optional[Dict[str, str]]:
    """主档里这一行（未删）几栏的定宽原值；找不到 None。"""
    table = _open(account_dir, table_name)
    if table is None:
        return None
    try:
        for rec in S.live_rows(table):
            if _text(rec, key_field) == key:
                return padded(table, rec, names)
    finally:
        table.close()
    return None


__all__ = ["DEFAULT_RECEIPT_JNLEXP", "ISSUE_CONFLICT", "ISSUE_MISSING", "PAYMENT_TYPES", "RECEIPT_TYPES", "account_of",
           "banks", "journal_text", "line_number", "lookup_padded", "padded", "types"]
