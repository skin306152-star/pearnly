"""科目建议 B 的纯规则：请电脑读一本账套近 12 个月「供应商 → 常用科目」的参数，CC 桌面端交回的汇总怎么校验、存什么，
以及一张票的卖方对上哪家供应商、这家常用哪些科目。不连库、不依赖框架。

交回的内容与字段名见 CC 桌面端 ``companion/src/companion/account_history.py``（``RESULT_VERSION``）。供应商按税号对
（13 位，同税号几个档的次数相加），没有税号按名称对（去掉法人前后缀，与推送选择记「这家」同一种归一
`push.express.choices.norm_name`）。
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List

from pearnly_cowork.push.express.choices import digits, norm_name
from pearnly_cowork.readback.contracts import PATH_MAX, ContractError

RESULT_VERSION = 1
# 近 12 个月
SINCE_DAYS = 365
SUPPLIERS_MAX = 5000
ACCOUNTS_PER_SUPPLIER = 5
CODE_MAX = 20
TEXT_MAX = 300
INVALID = "cowork.account_history.data_invalid"


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


def parse(raw: Any) -> Dict[str, Any]:
    """交回的汇总 → ``{"since", "read_at", "suppliers": [{name, tax_id, branch, accounts: [{code, docs, last}]}]}``；
    形状不对抛 ``ContractError``。"""
    if not isinstance(raw, dict) or raw.get("version") != RESULT_VERSION:
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
        name, tax = _text(item.get("name")), digits(_text(item.get("tax_id"), 40))
        if accounts and (name or tax):
            suppliers.append({"name": name, "tax_id": tax, "branch": _text(item.get("branch"), 5),
                              "accounts": accounts})
    return {"since": _day(raw.get("since")), "read_at": read_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "suppliers": suppliers}


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
