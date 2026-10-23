"""一张 Express 单的载荷实际用到的那几行主档（ADR-021，方案 2.5「准备证据 v2」，D5）。纯函数。

排队的单在电脑来领时不再整份重算比对准备时的全部资料（日期、电脑、版本、整张科目表……），只核与这张单有关的：
服务端事实，加上这里挑出的主档行。挑法：

- 科目：分录行的 ``acc``、明细挂的 ``items_account``、存货科目 ``stock_acccod``、POS 收款科目（载荷里任何叫 ``acc``
  的值）；
- 对方档（供应商 / 客户）：有代码按代码；没有代码（要新建）按税号，税号也没有按名字——这样一份同税号的档后来出现了，
  也算「用到的变了」，不会再新建一份重复的；
- 存货：明细行有代码按代码，没有按名字（电脑按名字找、找不到才新建）；
- 部门：``depcod``；存货科目组：``stock_acccod``。

每一类取主档里匹配的整行（按代码排好），一行都没匹配上也记一个空表：后来出现了同样算变。

服务端事实也只取本单用到的（:func:`used_facts`，第 3 批审查）：科目映射只取映射到本单载荷里那几个科目的行（本站的映射表
不分工作空间，排队几小时里改一条无关映射不该把整队单转人工）；客户映射本就只取这条连接的客户那一行、供应商画像本就只取
本单卖方那一条，原样；银行流水窗口不核（方案第 8 节第 30 条：D5 不含银行流水，载荷已冻结）。
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

_ACCOUNT_FIELDS = frozenset(("acc", "items_account", "stock_acccod"))


def _text(value) -> str:
    return str(value or "").strip()


def _name(value) -> str:
    return " ".join(_text(value).casefold().split())


def _rows(masters: Mapping[str, Any], key: str) -> list:
    rows = (masters or {}).get(key)
    return [row for row in rows if isinstance(row, Mapping)] if isinstance(rows, list) else []


def _account_codes(value, found: set) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in _ACCOUNT_FIELDS and isinstance(item, (str, int)) and _text(item):
                found.add(_text(item))
            else:
                _account_codes(item, found)
    elif isinstance(value, list):
        for item in value:
            _account_codes(item, found)


def _pick(rows: Iterable[Mapping], match) -> list:
    picked = [dict(row) for row in rows if match(row)]
    return sorted(picked, key=lambda row: (_text(row.get("code") or row.get("acccod")), repr(sorted(row.items()))))


def _party(block: Mapping[str, Any], rows: list) -> list:
    code, tax, name = _text(block.get("code")), _text(block.get("tax_id")), _name(block.get("name"))
    if code:
        return _pick(rows, lambda row: _text(row.get("code")) == code)
    if tax:
        return _pick(rows, lambda row: _text(row.get("tax_id")) == tax)
    if name:
        return _pick(rows, lambda row: _name(row.get("name")) == name)
    return []


def used_masters(payload: Mapping[str, Any], masters: Mapping[str, Any]) -> dict:
    """``payload`` 用到的 ``masters``（账套资料，`connections.pool.facts`）里的行：``{类别: [行…]}``，键固定。"""
    payload = payload if isinstance(payload, Mapping) else {}
    accounts: set = set()
    _account_codes(payload, accounts)
    result = {"accounts": _pick(_rows(masters, "reported_accounts"), lambda row: _text(row.get("code")) in accounts)}
    for side, key in (("supplier", "reported_suppliers"), ("customer", "reported_customers")):
        block = payload.get(side)
        result[side] = _party(block, _rows(masters, key)) if isinstance(block, Mapping) else []
    codes, names = set(), set()
    for item in payload.get("items") or []:
        if not isinstance(item, Mapping):
            continue
        if _text(item.get("code")):
            codes.add(_text(item.get("code")))
        elif _name(item.get("name")):
            names.add(_name(item.get("name")))
    result["products"] = _pick(_rows(masters, "reported_products"),
                               lambda row: _text(row.get("code")) in codes or _name(row.get("name")) in names)
    department = _text(payload.get("depcod"))
    result["departments"] = _pick(_rows(masters, "reported_departments"),
                                  lambda row: bool(department) and _text(row.get("code")) == department)
    stock = _text(payload.get("stock_acccod"))
    result["stock_acc_groups"] = _pick(_rows(masters, "reported_stock_acc_groups"),
                                       lambda row: bool(stock) and _text(row.get("acccod")) == stock)
    return result


def used_mappings(payload: Mapping[str, Any], mappings: Mapping[str, Any]) -> dict:
    """``mappings``（`masters.push_inputs.mappings` 的投影）里本单用得上的：科目映射只留 ``erp_code`` 是载荷里某个科目的行
    （映射改到别的科目、删掉，或者又有一条映射到同一科目，都算变）；客户映射原样（本就只有这条连接的客户那一行）。"""
    payload = payload if isinstance(payload, Mapping) else {}
    mappings = mappings if isinstance(mappings, Mapping) else {}
    codes: set = set()
    _account_codes(payload, codes)
    accounts = [row for row in mappings.get("accounts") or [] if isinstance(row, Mapping)]
    clients = [dict(row) for row in mappings.get("clients") or [] if isinstance(row, Mapping)]
    picked = [dict(row) for row in accounts if _text(row.get("erp_code")) in codes]
    return {"accounts": sorted(picked, key=lambda row: repr(sorted(row.items()))),
            "clients": sorted(clients, key=lambda row: repr(sorted(row.items())))}


def used_facts(payload: Mapping[str, Any], facts: Mapping[str, Any]) -> dict:
    """电脑来领时要核的那一份（准备证据 v2 的 ``basis_hash`` 按它算）：本单用到的映射、本单卖方的供应商画像、本单用到的
    主档行。``facts`` 是准备时收集的服务端事实（`connections.preparation.collect`）。键固定。"""
    facts = facts if isinstance(facts, Mapping) else {}
    profiles = facts.get("supplier_profiles")
    return {"mappings": used_mappings(payload, facts.get("mappings") or {}),
            "supplier_profiles": dict(profiles) if isinstance(profiles, Mapping) else {},
            "masters": used_masters(payload, facts.get("reported_config") or {})}


__all__ = ["used_facts", "used_mappings", "used_masters"]
