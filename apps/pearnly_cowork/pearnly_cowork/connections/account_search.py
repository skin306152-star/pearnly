"""按用户输入的关键词找账套（LINE 里上传前选账套用；用户 2026-09-27），纯函数，不做 IO。

LINE 快捷回复一次最多 13 个按钮，列不下几十上百份账套：用户打几个字（公司名、账套名、年度文件夹、税号都认），
这里挑出匹配的，由用户从结果里点选。多个词之间是「都要有」，不分大小写。
Express 的结果按年度文件夹新的在前、同一文件夹按登录列表顺序；MR.ERP 按公司、年度。
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from pearnly_cowork.targets.catalog_groups import year_rank

_UNORDERED = 10**9


def _text(value: object) -> str:
    return str(value or "").strip()


def _attributes(row: Mapping[str, Any]) -> Mapping[str, Any]:
    value = row.get("attributes")
    return value if isinstance(value, Mapping) else {}


def usable(adapter: object, rows: Iterable[Any] | None) -> list[Mapping[str, Any]]:
    """能选的账套：在用、有键；Express 还要助手报告可写（写不进的选了也推不进去）。"""
    kind = _text(adapter).lower()
    result = []
    for row in rows or []:
        if not isinstance(row, Mapping) or row.get("active") is False or not _text(row.get("source_id")):
            continue
        if kind == "express" and _attributes(row).get("writable") is False:
            continue
        result.append(row)
    return result


def label(adapter: object, row: Mapping[str, Any]) -> str:
    """结果里显示的全名：Express「年度文件夹 · 登录列表名（公司法定名）」，MR.ERP「公司 · 年度」。"""
    attributes = _attributes(row)
    name = _text(row.get("label")) or _text(row.get("source_id"))
    if _text(adapter).lower() == "express":
        company = _text(attributes.get("company"))
        head = " · ".join(part for part in (_text(attributes.get("root_label")), name) if part)
        return f"{head}（{company}）" if company and company != name else head
    company = " · ".join(part for part in (_text(attributes.get("company")), _text(attributes.get("company_name")))
                         if part)
    return " · ".join(part for part in (company, name) if part)


def _haystack(adapter: object, row: Mapping[str, Any]) -> str:
    attributes = _attributes(row)
    return " ".join((label(adapter, row), _text(row.get("source_id")), _text(attributes.get("tax_id")))).casefold()


def _order(adapter: object, row: Mapping[str, Any]):
    attributes = _attributes(row)
    try:
        number = int(attributes.get("row"))
    except (TypeError, ValueError):
        number = _UNORDERED
    if _text(adapter).lower() == "express":
        return (-year_rank(attributes.get("root_label")), _text(attributes.get("root_label")).casefold(), number)
    return (_text(attributes.get("company")).casefold(), -year_rank(row.get("label")), number)


def search(adapter: object, rows: Iterable[Any] | None, query: object) -> list[dict[str, str]]:
    """→ 匹配的账套 `[{value: 账套键, label: 全名}]`，按上面的顺序；没有词就返空。"""
    terms = [term.casefold() for term in _text(query).split() if term]
    if not terms:
        return []
    found = [row for row in usable(adapter, rows) if all(term in _haystack(adapter, row) for term in terms)]
    found.sort(key=lambda row: _order(adapter, row))
    return [{"value": _text(row.get("source_id")), "label": label(adapter, row)} for row in found]


__all__ = ["label", "search", "usable"]
