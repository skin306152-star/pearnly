"""科目建议 B 的纯规则：请电脑读一本账套近 12 个月「供应商 → 常用科目」的参数，CC 桌面端交回的汇总怎么校验、存什么，
以及一张票的卖方对上哪家供应商、这家常用哪些科目。不连库、不依赖框架。

交回的内容与字段名见 CC 桌面端 ``companion/src/companion/account_history.py``（``RESULT_VERSION``）。供应商按税号对
（13 位，同税号几个档的次数相加），没有税号按名称对（去掉法人前后缀，与推送选择记「这家」同一种归一
`push.express.choices.norm_name`）。
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List

from pearnly_core.express_rules.push.choices import digits, norm_name
from pearnly_cowork.readback.contracts import PATH_MAX, ContractError

RESULT_VERSION = 3
# 收得下的版本：v1 是 CC 桌面版 1.8.0 之前（Pearnly 自己写的单也算进去），v2 排除了它们（数字会计员第 3 期第 3、11 条）。
# 只有 v2 起的「已定」才算规则定（`push.express.automation`），v1 的照旧作建议、自动用，最高算 CC 判断。
# v3（CC 桌面版 1.9.0 起）每家供应商另带可选的 ``posting``：商品单走库存 / 记费用各几张（读 STCRD / STMAS，`posting_settled`）；
# 旧桌面版交 v2 照收，没有 ``posting``，商品单入账方式照旧算 CC 判断
VERSIONS = (1, 2, RESULT_VERSION)
# 带 ``posting`` 的最低版本
POSTING_VERSION = 3
# 商品单入账方式的两种记法（`contracts.document_policy.GOODS_POSTING_VALUES` 同值）：有库存明细（走库存）/ 没有（记费用）
POSTING_KINDS = ("stock", "service")
# 近 12 个月
SINCE_DAYS = 365
SUPPLIERS_MAX = 5000
ACCOUNTS_PER_SUPPLIER = 5
CODE_MAX = 20
TEXT_MAX = 300
INVALID = "cowork.account_history.data_invalid"
# 历史很一致就直接用（用户 2026-10-15 同意）：同一供应商（按税号合并各分店）近 12 个月至少这么多张、而且全记同一个科目
SETTLED_DOCS = 3
# 读到的那份超过这么久就不再当「近 12 个月」直接用（只作建议，等下次要选科目时重读）
SETTLED_FRESH_DAYS = 31


def since(today: dt.date) -> str:
    return (today - dt.timedelta(days=SINCE_DAYS)).isoformat()


def read_params(account_set: Any, account_root: Any, today: dt.date) -> Dict[str, Any]:
    """交给电脑的参数（``account_history``）：账套文件夹、所在的 Express 根目录、从哪天起。"""
    account_set, account_root = str(account_set or "").strip(), str(account_root or "").strip()
    if not account_set or len(account_set) > PATH_MAX or len(account_root) > PATH_MAX:
        raise ContractError("cowork.readback.account_missing")
    return {"account_set": account_set, "account_root": account_root, "since": since(today)}


def _text(value: Any, limit: int = TEXT_MAX) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > limit:
        raise ContractError(INVALID)
    return value.strip()


def _count(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise ContractError(INVALID)
    return value


def _day(value: Any) -> str:
    try:
        return dt.date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError as error:
        raise ContractError(INVALID) from error


def _list(value: Any, limit: int) -> list:
    if not isinstance(value, list) or len(value) > limit:
        raise ContractError(INVALID)
    return value


def _posting(value: Any) -> Dict[str, Dict[str, Any]] | None:
    """一家供应商的 ``posting``（v3，可选）→ ``{"stock": {docs, last}, "service": {docs, last}}``（缺的记法按 0 张、``last`` 空串）；
    没给回 None；形状不对抛 ``ContractError``。"""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - set(POSTING_KINDS):
        raise ContractError(INVALID)
    out = {}
    for kind in POSTING_KINDS:
        row = value.get(kind, {"docs": 0, "last": ""})
        if not isinstance(row, dict):
            raise ContractError(INVALID)
        docs = _count(row.get("docs"))
        out[kind] = {"docs": docs, "last": _day(row.get("last")) if docs else ""}
    return out


def parse(raw: Any) -> Dict[str, Any]:
    """交回的汇总 → ``{"version", "since", "read_at", "suppliers": [{name, tax_id, branch, accounts: [{code, docs, last}],
    posting?}]}``（``posting`` 只收 v3 起的，`_posting`；没有常用科目、只有入账方式的供应商也留下）；形状不对抛 ``ContractError``。"""
    if not isinstance(raw, dict) or type(raw.get("version")) is not int or raw.get("version") not in VERSIONS:
        raise ContractError(INVALID)
    try:
        read_at = dt.datetime.strptime(str(raw.get("read_at") or ""), "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise ContractError(INVALID) from error
    suppliers = []
    for item in _list(raw.get("suppliers"), SUPPLIERS_MAX):
        if not isinstance(item, dict):
            raise ContractError(INVALID)
        accounts = []
        for row in _list(item.get("accounts"), ACCOUNTS_PER_SUPPLIER):
            if not isinstance(row, dict):
                raise ContractError(INVALID)
            code = _text(row.get("code"), CODE_MAX)
            if code:
                accounts.append({"code": code, "docs": _count(row.get("docs")), "last": _day(row.get("last"))})
        posting = _posting(item.get("posting")) if raw["version"] >= POSTING_VERSION else None
        name, tax = _text(item.get("name")), digits(_text(item.get("tax_id"), 40))
        if (accounts or posting) and (name or tax):
            suppliers.append({"name": name, "tax_id": tax, "branch": _text(item.get("branch"), 5),
                              "accounts": accounts, **({"posting": posting} if posting else {})})
    return {"version": raw["version"], "since": _day(raw.get("since")), "read_at": read_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "suppliers": suppliers}


def supplier_key(tax: Any, name: Any) -> str:
    """一家供应商的键：13 位税号（各分店合并），没有税号用「name:归一名称」；都没有回空串。与推送选择「按对方记」同一种。"""
    tax = digits(tax)
    if len(tax) == 13:
        return tax
    name = norm_name(name)
    return f"name:{name}" if name else ""


def settled(accounts: Any, today: dt.date, read_at: Any = None) -> Dict[str, Any] | None:
    """这家供应商的历史是否一致到可以直接用（`SETTLED_DOCS`）：合并后只有一个科目、近 12 个月至少 3 张、最近一次在
    12 个月内，且这份历史是 `SETTLED_FRESH_DAYS` 天内读的。是就回 ``{code, docs, last}``，否则 None（仍作建议、问一次）。
    推送用时另带上这份汇总的版本与读的日子（`stamp`）。"""
    rows = [row for row in accounts or [] if isinstance(row, dict) and row.get("code")]
    if len(rows) != 1:
        return None
    row = rows[0]
    try:
        last = dt.date.fromisoformat(str(row.get("last") or "")[:10])
    except ValueError:
        return None
    if int(row.get("docs") or 0) < SETTLED_DOCS or last < today - dt.timedelta(days=SINCE_DAYS):
        return None
    if read_at is not None:
        try:
            taken = dt.date.fromisoformat(str(read_at)[:10])
        except ValueError:
            return None
        if taken < today - dt.timedelta(days=SETTLED_FRESH_DAYS):
            return None
    return {"code": str(row["code"]), "docs": int(row["docs"]), "last": last.isoformat()}


def posting_settled(posting: Any, today: dt.date, read_at: Any = None) -> Dict[str, Any] | None:
    """这家供应商在这本账套里的商品单入账方式是否一致到可以定档（用户 10-17：近 12 个月会计亲手录、不含 CC 推的，至少 3 张，
    全部走库存或全部没有库存明细）。``posting``：``{"stock": {docs, last}, "service": {docs, last}}``（各记法的单据数、最后一张的
    单据日期；缺一种、或那一种没有 ``docs`` 都按 0 张）。口径同 `settled`：只有一种记法有单、另一种为 0，张数 ≥ `SETTLED_DOCS`，
    最后一张在 12 个月内，这份汇总在 `SETTLED_FRESH_DAYS` 天内读的。是就回 ``{kind, docs, last}``，否则 None（``docs`` 不是非负
    整数的坏数据也回 None）。

    这里只看数，用户口径里另两条由调用方负责：输入必须是已排除 Pearnly 自己写入的单（USERID=PEARNLY）的汇总（CC 桌面版
    ``account_history`` v3 读 STCRD 时排除，同 v2 的科目历史）；「与公司选的入账方式一致才 A、相反交人 C、没定下仍 B」在
    `push.express.automation.posting_by_history`（用户 10-17 批准接线上）。线上经 `supplier_posting` 取这家的汇总（`readback.history
    .for_push`），回放（`scripts/da-replay.py` 的 ``posting_history``）用同一个函数。"""
    posting = posting if isinstance(posting, dict) else {}
    used = []
    for kind in POSTING_KINDS:
        row = posting.get(kind)
        docs = row.get("docs", 0) if isinstance(row, dict) else 0
        if type(docs) is not int or docs < 0:
            return None
        if docs:
            used.append((kind, row))
    if len(used) != 1:
        return None
    kind, row = used[0]
    try:
        last = dt.date.fromisoformat(str(row.get("last") or "")[:10])
    except ValueError:
        return None
    if row["docs"] < SETTLED_DOCS or last < today - dt.timedelta(days=SINCE_DAYS):
        return None
    if read_at is not None:
        try:
            taken = dt.date.fromisoformat(str(read_at)[:10])
        except ValueError:
            return None
        if taken < today - dt.timedelta(days=SETTLED_FRESH_DAYS):
            return None
    return {"kind": kind, "docs": row["docs"], "last": last.isoformat()}


def suppliers_of(data: Any) -> Dict[str, Dict[str, Any]]:
    """一份历史里按供应商键（`supplier_key`）合并的科目与商品单入账方式：``{键: {name, accounts: [{code, docs, last}],
    posting}}``（``posting`` 同 `supplier_posting` 的形状，各分店相加；这份历史没有的回空 dict，`posting_settled` 判不出）。"""
    out: Dict[str, Dict[str, Any]] = {}
    for row in (data or {}).get("suppliers") or [] if isinstance(data, dict) else []:
        if not isinstance(row, dict):
            continue
        key = supplier_key(row.get("tax_id"), row.get("name"))
        if not key:
            continue
        found = out.setdefault(key, {"name": str(row.get("name") or ""), "accounts": {}, "posting": {}})
        for account in row.get("accounts") or []:
            current = found["accounts"].setdefault(account["code"], {"code": account["code"], "docs": 0, "last": ""})
            current["docs"] += int(account.get("docs") or 0)
            current["last"] = max(current["last"], str(account.get("last") or ""))
        posting = row.get("posting") if isinstance(row.get("posting"), dict) else None
        for kind in POSTING_KINDS if posting else ():
            part = posting.get(kind) if isinstance(posting.get(kind), dict) else {}
            current = found["posting"].setdefault(kind, {"docs": 0, "last": ""})
            current["docs"] += int(part.get("docs") or 0)
            current["last"] = max(current["last"], str(part.get("last") or ""))
    for found in out.values():
        found["accounts"] = sorted(found["accounts"].values(), key=lambda item: (-item["docs"], item["code"]))
    return out


def supplier_accounts(data: Any, tax: Any, name: Any) -> List[Dict[str, Any]]:
    """这张票的卖方在这本账套里常用的科目 ``[{code, docs, last}]``（用过的单据多的在前，一样多时最近用过的在前）；
    对不上回空。税号 13 位按税号对（同税号几个档相加），否则按名称对。"""
    rows = (data or {}).get("suppliers") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    tax = digits(tax)
    if len(tax) == 13:
        found = [row for row in rows if row.get("tax_id") == tax]
    else:
        wanted = norm_name(name)
        found = [row for row in rows if wanted and norm_name(row.get("name")) == wanted]
    merged: Dict[str, Dict[str, Any]] = {}
    for row in found:
        for account in row.get("accounts") or []:
            current = merged.setdefault(account["code"], {"code": account["code"], "docs": 0, "last": ""})
            current["docs"] += int(account.get("docs") or 0)
            current["last"] = max(current["last"], str(account.get("last") or ""))
    out = sorted(merged.values(), key=lambda item: item["code"])
    out.sort(key=lambda item: item["last"], reverse=True)
    out.sort(key=lambda item: item["docs"], reverse=True)
    return out


def supplier_posting(data: Any, tax: Any, name: Any) -> Dict[str, Dict[str, Any]]:
    """这张票的卖方在这本账套里商品单走库存 / 记费用各几张（v3 的 ``posting``，`posting_settled` 收的形状）：对法同
    `supplier_accounts`（13 位税号按税号对、同税号几个档相加，否则按名称对）。对不上、或这份历史没有 ``posting``（v2 及以前、
    CC 桌面版读不到存货主档）回空 dict（`posting_settled` 判不出）。"""
    rows = (data or {}).get("suppliers") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return {}
    tax = digits(tax)
    if len(tax) == 13:
        found = [row for row in rows if isinstance(row, dict) and row.get("tax_id") == tax]
    else:
        wanted = norm_name(name)
        found = [row for row in rows if isinstance(row, dict) and wanted and norm_name(row.get("name")) == wanted]
    found = [row["posting"] for row in found if isinstance(row.get("posting"), dict)]
    if not found:
        return {}
    out = {kind: {"docs": 0, "last": ""} for kind in POSTING_KINDS}
    for posting in found:
        for kind in POSTING_KINDS:
            row = posting.get(kind) if isinstance(posting.get(kind), dict) else {}
            out[kind]["docs"] += int(row.get("docs") or 0)
            out[kind]["last"] = max(out[kind]["last"], str(row.get("last") or ""))
    return out


def stamp(found: Dict[str, Any] | None, stored: Any) -> Dict[str, Any] | None:
    """「已定」带上这份汇总的版本（存下的旧汇总没有版本 = 1）与读的日子（``read``，`YYYY-MM-DD`）：自动推只认 v2 的「已定」为
    规则定，各层说法不一只比近 31 天内读的历史（数字会计员第 3 期第 1、11 条）。"""
    if not found:
        return found
    stored = stored if isinstance(stored, dict) else {}
    version = stored.get("version")
    return {**found, "version": version if type(version) is int else 1,
            "read": str(stored.get("read_at") or stored.get("taken_at") or "")[:10]}
