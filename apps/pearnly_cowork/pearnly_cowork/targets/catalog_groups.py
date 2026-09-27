"""账套目录 → 录入时要选的两级（网页「新建录入」与 LINE 同一份），纯函数，不做 IO。

Express 照真实用法（用户 2026-09-27）：先选**年度** = 数据文件夹（69EXP、68EXP…，打开哪一年的
Express），再选**账套** = 该文件夹登录列表里的一行，名字与顺序原样（`row` 是登录列表行号）。
文件夹名不是会计年度的精确说法，但会计就是这样找账的：同一文件夹里常有几份同一公司的账套
（审计版 / 数据版、分厂），只有登录列表上的名字分得开。公司法定名只作第二行说明。

MR.ERP：公司 → 年度（年度就是 MR.ERP 的账套名），与原来一致。
"""
from __future__ import annotations

import ntpath
import re
from typing import Any, Iterable, Mapping

_UNORDERED = 10**9


def year_rank(label: object) -> int:
    """标签里最大的那个数字（69EXP → 69、2568 → 2568）；没有数字的排到最后。"""
    numbers = [int(value) for value in re.findall(r"\d+", str(label or ""))]
    return max(numbers) if numbers else -1


def _text(value: object) -> str:
    return str(value or "").strip()


def _row_number(value: object) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return _UNORDERED
    return number if number > 0 else _UNORDERED


def _usable(adapter: str, row: Mapping[str, Any]) -> bool:
    attributes = row.get("attributes") if isinstance(row.get("attributes"), Mapping) else {}
    if row.get("active") is False or not _text(row.get("source_id")):
        return False
    # 小助手够不到、写不进的账套选了也推不进去（旧版同样不给选）。
    return not (adapter == "express" and attributes.get("writable") is False)


def _express(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    folders: dict[str, dict[str, Any]] = {}
    for row in rows:
        attributes = row.get("attributes") if isinstance(row.get("attributes"), Mapping) else {}
        root = _text(attributes.get("root"))
        label = _text(attributes.get("root_label")) or ntpath.basename(root.rstrip("\\/")) or root
        key = ntpath.normcase(ntpath.normpath(root)) if root else label
        folder = folders.setdefault(key, {"value": key, "label": label, "items": [], "_root": root})
        name = _text(row.get("label")) or _text(row.get("source_id"))
        company = _text(attributes.get("company"))
        folder["items"].append({
            "value": _text(row.get("source_id")),
            "label": name,
            "description": company if company and company != name else "",
            "_order": (_row_number(attributes.get("row")), name.casefold()),
        })
    result = sorted(folders.values(), key=lambda folder: (-year_rank(folder["label"]), folder["label"].casefold()))
    _tell_same_names_apart(result)
    for folder in result:
        folder["items"].sort(key=lambda item: item.pop("_order"))
        # 下拉按名字认选项：同一文件夹里重名的账套补上它自己的目录名来区分。
        names = [item["label"].casefold() for item in folder["items"]]
        for item in folder["items"]:
            if names.count(item["label"].casefold()) > 1:
                item["label"] = f"{item['label']} ({ntpath.basename(item['value'].rstrip(chr(92) + '/'))})"
    return result


def _tell_same_names_apart(folders: list[dict[str, Any]]) -> None:
    """同名的年度文件夹（例如 `ACCOUNT\\63EXP` 和嵌在 `ACCOUNT\\64EXP\\63EXP` 的副本）显示到能区分的那一段路径。"""
    by_label: dict[str, list[dict[str, Any]]] = {}
    for folder in folders:
        by_label.setdefault(folder["label"].casefold(), []).append(folder)
    for same in by_label.values():
        paths = [[part for part in re.split(r"[\\/]+", folder["_root"]) if part] for folder in same]
        common = 0
        while len(same) > 1 and all(len(path) > common + 1 for path in paths) \
                and len({path[common].casefold() for path in paths}) == 1:
            common += 1
        for folder, path in zip(same, paths):
            if len(same) > 1 and path[common:]:
                folder["label"] = "\\".join(path[common:])
    for folder in folders:
        folder.pop("_root", None)


def _mrerp(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    companies: dict[str, dict[str, Any]] = {}
    for row in rows:
        attributes = row.get("attributes") if isinstance(row.get("attributes"), Mapping) else {}
        source_id = _text(row.get("source_id"))
        key = _text(attributes.get("company")) or _text(attributes.get("company_name")) or source_id
        label = " · ".join(value for value in (_text(attributes.get("company")), _text(attributes.get("company_name")))
                           if value) or _text(row.get("label")) or key
        company = companies.setdefault(key, {"value": key, "label": label, "items": []})
        company["items"].append({"value": source_id, "label": _text(row.get("label")) or source_id, "description": ""})
    result = sorted(companies.values(), key=lambda company: company["label"].casefold())
    for company in result:
        company["items"].sort(key=lambda item: year_rank(item["label"]), reverse=True)
    return result


def groups(adapter: object, account_sets: Iterable[Any] | None) -> list[dict[str, Any]]:
    """目录 → `[{value, label, items: [{value: 账套键, label, description}]}]`。

    Express 一级是数据文件夹（新的在上），二级是登录列表原样；MR.ERP 一级是公司，二级是年度。
    """
    kind = _text(adapter).lower()
    rows = [row for row in account_sets or [] if isinstance(row, Mapping) and _usable(kind, row)]
    if kind == "express":
        return _express(rows)
    if kind == "mrerp":
        return _mrerp(rows)
    return []


__all__ = ["groups", "year_rank"]
