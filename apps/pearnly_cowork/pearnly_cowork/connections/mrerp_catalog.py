"""MR.ERP 账套目录：把选公司页读成「公司 → 年度」，再写成标准目录快照。纯函数，无 IO。

来源（旧线上最后版本 `ec2e5c957e6b`，旧云 2026-09-24 退役后冻结）：
  · `services/erp/mrerp_http/session.py::_parse_account_sets` —— 选公司页每个年度账套是一条
    `mainmenu.php?comidyear=N&seldb=M` 链接，链接文字是 MR.ERP 的「Database name」；
  · `services/erp/mrerp_target_projection.py::_account_sets/_observation` —— 目录键
    `comidyear:seldb`，采集器身份 `cloud / mrerp-live-v1`，整站目录不带主档与表单。

**有意差异**（用户 2026-09-26：录入要分「公司」「年度」两个选项）：旧版只抓链接文字，
一家公司的几个年度混成一张平铺列表。选公司页本身是按行排的 —— 每行一家公司
（Comname / Comfullname / Remark / Database name），行里每个链接是这家公司的一个年度账套 ——
所以这里按行读出公司，链接文字仍是年度。实测（test01，2026-09-26）行是 `<p>`、格是 `<span>`；
也认 `<tr>` / `<td>` 的写法。页面认不出行时退回旧版的平铺读法（公司留空），不丢任何一个年度。
"""
from __future__ import annotations

import html as _html
import re
from datetime import date, datetime
from typing import Any

from pearnly_core.tax_documents.amounts import valid_tax_id

COLLECTOR = {"kind": "cloud", "adapter_version": "mrerp-live-v1"}
#: 可以原样重试一次的失败（网络/对方超时）；账号密码错误重试也没用。
TRANSIENT_ERRORS = frozenset({"ERR_TECHNICAL", "ERR_UNEXPECTED"})

_LINK = re.compile(
    r"<a\b[^>]*?href=[\"'][^\"']*mainmenu\.php\?comidyear=(\d+)&(?:amp;)?seldb=(\d+)[\"'][^>]*>(.*?)</a>",
    re.IGNORECASE | re.DOTALL)
_ROW = re.compile(r"<(tr|p)\b[^>]*>(.*?)</\1>", re.IGNORECASE | re.DOTALL)
_CELL = re.compile(r"<(td|th|span)\b[^>]*>(.*?)</\1>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_LABEL_LIMIT = 80


def _text(fragment: str) -> str:
    return " ".join(_html.unescape(_TAG.sub(" ", fragment or "")).split())


def _company(row_html: str) -> tuple[str, str]:
    """一行里链接之前的单元格：第一格是公司简称，第二格是全称（MR.ERP 的列序）。"""
    cells = [_text(cell) for _, cell in _CELL.findall(row_html) if not _LINK.search(cell)]
    cells = [cell for cell in cells if cell]
    return (cells[0] if cells else ""), (cells[1] if len(cells) > 1 else "")


def parse_account_sets(page: str) -> list[dict[str, str]]:
    """选公司页 → `[{comidyear, seldb, name, company, company_name}]`，按页面顺序、同键去重。"""
    found: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(match, company="", company_name=""):
        key = (match.group(1), match.group(2))
        if key in seen:
            return
        seen.add(key)
        found.append({"comidyear": key[0], "seldb": key[1], "name": _text(match.group(3)),
                      "company": company, "company_name": company_name})

    for _, row in _ROW.findall(page or ""):
        links = list(_LINK.finditer(row))
        if links:
            company, company_name = _company(row)
            for match in links:
                add(match, company, company_name)
    # 没有表格行（或行外还有链接）时按旧版平铺读：一个年度都不能漏。
    for match in _LINK.finditer(page or ""):
        add(match)
    return found


# ── 每个年度库的会计期间与公司税号（2026-10-19：让 MR.ERP 账套也有起止日期、公司主档有税号） ─────────────
# 期间：年度库的「รอบบัญชี」页 `period/allview.php`（`tbl_othperiod`，每期一行：起、止，泰历 dd/mm/yyyy，
# `component/php/formatdates.php::dateeng2thai`）；本年起止 = 最早的起、最晚的止。页面不带 idmenu 就不改会话里的当前菜单，
# 只查登录（`checkuseruse.php`）。期间表在年度库里（`connectdb`），所以每个年度库要先切进去再读。
PERIOD_PAGE = "period/allview.php"
_SHOWDATA = re.compile(r"<div\b[^>]*\bid=[\"']showdata[\"']", re.IGNORECASE)
_THAI_DATE = re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})/(\d{4})(?!\d)")


def _iso(match: re.Match) -> str:
    day, month, year = (int(part) for part in match.groups())
    year = year - 543 if year > 2400 else year  # dateeng2thai 一律 +543；万一是公历照收
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return ""


def parse_periods(page: str) -> tuple[str, str]:
    """期间页 → (本年起, 本年止) ISO 日期；页上没有期间表、一期都读不出回 ("", "")。每行头两个日期是起、止（后面是
    制单 / 复核 / 核准日期）；页头「เงื่อนไขรอบบัญชีเพิ่มเติม」那块的今天日期不在期间表里，不算。"""
    found = _SHOWDATA.search(page or "")
    if not found:
        return "", ""
    starts, ends = [], []
    for _, row in _ROW.findall(page[found.end():]):
        dates = [day for day in (_iso(m) for m in _THAI_DATE.finditer(_text(row))) if day]
        if len(dates) >= 2 and dates[0] <= dates[1]:
            starts.append(dates[0])
            ends.append(dates[1])
    return (min(starts), max(ends)) if starts else ("", "")


def company_tax(value: Any) -> str:
    """总店登记的税号（`targets.mrerp_masters.parse_company_tax` 读出的数字）：13 位且校验位对才算，其余是空串。"""
    digits = re.sub(r"\D", "", str(value or ""))
    return digits if len(digits) == 13 and valid_tax_id(digits) else ""


def account_sets(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """目录行：键 `comidyear:seldb`，标签是年度账套名，公司放在属性里给两级选择用；读到了的本年起止
    （``period_start`` / ``period_end``）与公司税号（``tax_id``）也放在属性里（`companies.identity.book` 照 Express 读它们）。"""
    result = []
    for row in rows:
        comidyear = str(row.get("comidyear") or "").strip()
        seldb = str(row.get("seldb") or "").strip()
        if not comidyear or not seldb:
            continue
        key = f"{comidyear}:{seldb}"
        attributes = {"comidyear": comidyear, "seldb": seldb}
        for name in ("company", "company_name"):
            value = str(row.get(name) or "").strip()[:200]
            if value:
                attributes[name] = value
        start, end = str(row.get("period_start") or "")[:10], str(row.get("period_end") or "")[:10]
        if start and end:
            attributes.update(period_start=start, period_end=end)
        tax = company_tax(row.get("tax_id"))
        if tax:
            attributes["tax_id"] = tax
        result.append({"source_id": key,
                       "label": str(row.get("name") or "").strip()[:_LABEL_LIMIT] or key,
                       "attributes": attributes})
    return result


def observation(observed_at: datetime, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """整站（`@endpoint`）目录：只有账套，主档与表单属于选定账套那一层，这里不带。"""
    return {"adapter": "mrerp", "observed_at": observed_at, "collector": dict(COLLECTOR),
            "account_sets": account_sets(rows), "masters": {}, "form_schema": {"fields": []},
            "capabilities": {"actions": []}}
