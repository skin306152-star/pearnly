# -*- coding: utf-8 -*-
"""收付款单共用的三样（只读，按类通用）：Express 的收付款方式、方式行编号、单据簿的分录摘要表达式。

收款单 RE（``dbf_receipt``，载荷 20）先用；以后付款单 PS（ISRUN ``ZP`` 行、APRCPCQ）、现销 HS 收转账（ARRCPCQ 一行）照同一套。
口径来自 NLTTEST 2920 张原生完成的 RE（评估 ``docs/work/2026-11-11-collection-receipt-assessment.md`` 第 0 步）：

- 方式（:func:`types`）：ISRUN ``DOCTYP = ZR``（付款是 ``ZP``）的每一行一种，``PREFIX`` 是这一种的前缀。科目：``SHORTNAM`` 指向银行主档
  ``BKMAS.BNKACC`` 时取那家银行的 ``ACCNUM``（TB→B1→1112-01、KB→K1→1112-03，这两种自己的科目栏是空的），否则取本行 ``ACCNUM01``。
  两处都没有 → ``missing``；两处都有又不同、或同一前缀几行科目不同 → ``conflict``（写入按前缀认类型，DOCCOD 在真账套里会重复：NLTTEST
  ``22`` 有 SA 与 ``22`` 两种、``01`` QR 重复两行内容相同，内容相同不算冲突）。
- 科目类别（:func:`account_classes`，11-12 按类放宽「只列收钱类」：服务端以科目是不是银行存款为准、名称只作辅助）：只按这本账套
  自己的科目表结构判，不看泰文名称。统计见 ``docs/work/2026-11-12-collection-transfer-rule-stats.md``（101 本离线测试账套）。
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
# 科目类别（服务端 ``collection.contracts.ACCOUNT_CLASSES`` 同值，契约测试守住）
ACCOUNT_BANK = "bank"
ACCOUNT_CASH_GROUP = "cash_group"
ACCOUNT_CASH = "cash"
ACCOUNT_OTHER = "other"
ACCOUNT_CLASSES = (ACCOUNT_BANK, ACCOUNT_CASH_GROUP, ACCOUNT_CASH, ACCOUNT_OTHER)
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


def bank_rows(account_dir) -> Dict[str, Dict[str, str]]:
    """银行主档 BKMAS：BNKACC → ``{code, bank, short, number, branch, account}``（户头名称、简称、账号、分行、科目；栏不在给空）。"""
    table = _open(account_dir, "BKMAS")
    if table is None:
        return {}
    try:
        return {_text(r, "BNKACC"): {"code": _text(r, "BNKACC"), "bank": _text(r, "BNKNAM"), "short": _text(r, "SHORTNAM"),
                                     "number": _text(r, "BNKNUM"), "branch": _text(r, "BRANCH"), "account": _text(r, "ACCNUM")}
                for r in S.live_rows(table) if _text(r, "BNKACC")}
    finally:
        table.close()


def bank_account_of(row: dict, accounts: Dict[str, Dict[str, str]]) -> Optional[dict]:
    """一种收款方式（:func:`types` 的一行）进哪个户头（收款按钮标签用，接口见共享笔记「收款-LINE侧-交接-11-13.md」§4）：
    ``SHORTNAM`` 指到 BKMAS 里有的户头 → ``via shortnam``；否则科目恰好是**唯一**一个户头的科目 → ``via account``；
    方式有问题（没科目 / 冲突）、找不到或几个户头同科目 → ``None``（服务端照旧显示方式名）。"""
    if row.get("account_issue") or not row.get("account"):
        return None
    hit, via = accounts.get(row.get("bank") or ""), "shortnam"
    if hit is None:
        same = [item for item in accounts.values() if item["account"] and item["account"] == row["account"]]
        hit, via = (same[0], "account") if len(same) == 1 else (None, "")
    if hit is None:
        return None
    return {"code": hit["code"], "bank": hit["bank"], "short": hit["short"], "number": hit["number"], "branch": hit["branch"],
            "via": via}


def method_uses(account_dir, book_prefix: str) -> Dict[str, int]:
    """这本账套里原生收款单用过每种方式几次：ARRCPCQ 里单号以 RE 单据簿前缀开头的方式行，按行编号的前缀（前 2 字）计。读不到 → 空。"""
    table = _open(account_dir, "ARRCPCQ")
    if table is None or not book_prefix:
        if table is not None:
            table.close()
        return {}
    counts: Dict[str, int] = {}
    try:
        for r in S.live_rows(table):
            if _text(r, "RCPNUM").startswith(book_prefix):
                prefix = _text(r, "CHQNUM")[:2]
                counts[prefix] = counts.get(prefix, 0) + 1
    finally:
        table.close()
    return counts


def account_classes(account_dir) -> Optional[Dict[str, str]]:
    """科目 → 类别（:data:`ACCOUNT_CLASSES`），只按这本账套自己的结构判（GLACC 的 PARENT、BKMAS 的科目、ISINFO.ACCNUM00）：

    - 现金组：现金科目往上找，第一个底下有 BKMAS 科目（现金科目本身不算）的上级；BKMAS 里没有组内科目就取现金科目的上级
      （例 NLTTEST 1110-00「เงินสดและเงินฝากธนาคาร」）；
    - ``cash``：现金科目本身；
    - ``bank``：组里不是现金科目，且在 BKMAS 里，或挂在组下的分组里（上级不是组本身、也不在现金那一支：1112-00 活期、1113-00
      储蓄、1114-00 定期这类分组底下的科目，BKMAS 漏登的存款也算）；
    - ``cash_group``：组里其余的（零用金、另一个现金科目这类直接挂在组下又不在 BKMAS 的）；
    - 不在表里的科目 = ``other``（费用、借款、退票应收、关联公司往来……BKMAS 里登了也一样）。

    判不出结构（没有 GLACC、没有 PARENT 栏、没有现金科目或它不在科目表里）回 ``None``：不报类别，服务端照旧按名称判。"""
    table = _open(account_dir, "GLACC")
    if table is None:
        return None
    try:
        if "PARENT" not in [name.upper() for name in table.field_names]:
            return None
        parent = {_text(r, "ACCNUM"): _text(r, "PARENT") for r in S.live_rows(table) if _text(r, "ACCNUM")}
    finally:
        table.close()
    info = _open(account_dir, "ISINFO")
    cash = ""
    if info is not None:
        try:
            cash = next((_text(r, "ACCNUM00") for r in S.live_rows(info)), "")
        finally:
            info.close()
    if not cash or cash not in parent:
        return None

    ups: Dict[str, List[str]] = {}

    def chain(acc: str) -> List[str]:
        if acc not in ups:
            out, seen, up = [], {acc}, parent.get(acc, "")
            while up and up not in seen:
                out.append(up)
                seen.add(up)
                up = parent.get(up, "")
            ups[acc] = out
        return ups[acc]

    banked = {acc for acc in banks(account_dir).values() if acc and acc != cash}
    group = next((up for up in chain(cash) if any(up in chain(acc) for acc in banked)), "") or parent.get(cash, "")
    if not group:
        return None
    path = [cash] + chain(cash)
    cash_branch = path[path.index(group) - 1]  # 组的直接下级里通往现金科目的那一个（现金直接挂在组下时就是它自己）
    out = {cash: ACCOUNT_CASH}
    for acc in parent:
        if acc == cash or group not in chain(acc):
            continue
        nested = parent.get(acc) != group and acc != cash_branch and cash_branch not in chain(acc)
        out[acc] = ACCOUNT_BANK if acc in banked or nested else ACCOUNT_CASH_GROUP
    return out


def types(account_dir, doctyp: str = RECEIPT_TYPES) -> List[dict]:
    """这本账套的收（付）款方式，ISRUN 里逐行照原样给（重复的行也给）：
    ``{code, prefix, name, name_en, bank, account, account_issue}``，``account_issue`` 空 / ``missing`` / ``conflict``。
    判得出科目结构时每行另带 ``account_class``（:func:`account_classes`；没有可用科目的行为空串）；判不出不带这一栏。"""
    table = _open(account_dir, "ISRUN")
    if table is None:
        return []
    bank_accounts = banks(account_dir)
    try:
        classes = account_classes(account_dir)
    except Exception:  # noqa: BLE001 — 科目表读坏了只是不报类别，不挡收款类型
        classes = None
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
        if classes is not None:
            row["account_class"] = classes.get(row["account"], ACCOUNT_OTHER) if row["account"] else ""
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


__all__ = ["ACCOUNT_CLASSES", "DEFAULT_RECEIPT_JNLEXP", "ISSUE_CONFLICT", "ISSUE_MISSING", "PAYMENT_TYPES", "RECEIPT_TYPES",
           "account_classes", "account_of", "bank_account_of", "bank_rows", "banks", "journal_text", "line_number", "lookup_padded", "method_uses", "padded", "types"]
