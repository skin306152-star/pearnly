# -*- coding: utf-8 -*-
"""库存停放报表：读 MR.ERP 原生导出 → 按「车型 + 颜色」汇总（纯计算，不碰 IO）。

来源报表：MR.ERP DMS「รายงานสต๊อกคงค้างแสดงสถานที่จอดรถ」（`rpcarallocate/form.php`）。评估与实查见
`docs/work/2026-11-26-dms-line-stock-report-assessment.md`：

- 表单打开时 MR.ERP 已按登录账号的权限预填起止范围；原样提交 = 这个账号能看到的全部库存。
- 「ออกรายงาน」= 同一表单 POST `component/report/1.php`（xlsx）或 `2.php`（PDF）。
- 导出一台车一行、9 列：รุ่นรถ（代码）/ สีรถ（代码）/ หมายเลขเครื่อง / หมายเลขตัวถัง / สถานะรถ / สถานที่จอดรถ (คลัง) /
  เลขที่ใบจอง / ชื่อที่ปรึกษาการขาย / ทีมสาขา；前两行是标题与日期，末行「รวม n รายการ」。
- 「ว่าง」取自 สถานะรถ（MR.ERP 自己的车辆状态），与 `selallocate`（是否分给团队）无关。
"""

from __future__ import annotations

import io
import re
from collections import OrderedDict
from typing import Dict, Iterable, List, Optional

FORM_PATH = "rpcarallocate/form.php"
EXPORT_PATHS = {"xlsx": "rpcarallocate/component/report/1.php", "pdf": "rpcarallocate/component/report/2.php"}
READ_PATHS = frozenset({FORM_PATH, *EXPORT_PATHS.values()})

# 三个范围（快捷按钮顺序 = 这里的顺序）。`allocate` = MR.ERP 表单的 selallocate：1 ทั้งหมด / 2 ยังไม่จัดสรรทีมสาขา。
# 「空车」MR.ERP 不能按状态筛，原件两份是它的「全部」，转换表再按状态取空车（主会话 11-26 定）。
SCOPES = OrderedDict((
    ("available", {"allocate": "1", "available_only": True}),
    ("all", {"allocate": "1", "available_only": False}),
    ("unallocated", {"allocate": "2", "available_only": False}),
))

# 导出列（按表头文字认，不按位置死认：MR.ERP 加列时不至于读错）。
_COLUMNS = {
    "model": "รุ่นรถ",
    "color": "สีรถ",
    "engine": "หมายเลขเครื่อง",
    "chassis": "หมายเลขตัวถัง",
    "status": "สถานะรถ",
    "location": "สถานที่จอดรถ",
    "booking": "เลขที่ใบจอง",
    "advisor": "ชื่อที่ปรึกษาการขาย",
    "team": "ทีมสาขา",
}
_REQUIRED = ("model", "color", "status", "location")
_TOTAL_RE = re.compile(r"^\s*รวม\s+([\d,]+)\s+รายการ\s*$")


class ExportChanged(ValueError):
    """导出的格式和实查时不一样（缺列、合计对不上）：宁可报失败，不出一张错表。"""


def is_available(status: str) -> bool:
    """「空车」的唯一判据：MR.ERP 车辆状态以 รถว่าง 开头（含 รถว่าง-โชว์ 展示车；用户 11-26 经主会话定）。
    岳父若说展示车不算，只改这一处。"""
    return str(status or "").strip().startswith("รถว่าง")


def _clean(value) -> str:
    return "" if value is None else " ".join(str(value).split())


def parse_export(content: bytes) -> List[Dict[str, str]]:
    """MR.ERP 导出的 xlsx → 每台车一个字典。表头在哪一行、有没有合计行都按内容认；合计对不上抛 `ExportChanged`。"""
    from openpyxl import load_workbook

    try:
        sheet = load_workbook(io.BytesIO(content), read_only=True, data_only=True).worksheets[0]
    except Exception as exc:  # 不是 xlsx（登录过期时 MR.ERP 回的是网页）
        raise ExportChanged("export is not a workbook") from exc
    header: Optional[Dict[str, int]] = None
    rows: List[Dict[str, str]] = []
    total = None
    for raw in sheet.iter_rows(values_only=True):
        cells = [_clean(value) for value in raw]
        if not any(cells):
            continue
        if header is None:
            found = {}
            for key, title in _COLUMNS.items():
                for index, cell in enumerate(cells):
                    if cell.startswith(title):
                        found[key] = index
                        break
            if all(key in found for key in _REQUIRED):
                header = found
            continue
        match = _TOTAL_RE.match(cells[0]) if cells[0] and not any(cells[1:]) else None
        if match:
            total = int(match.group(1).replace(",", ""))
            continue
        rows.append({key: (cells[index] if index < len(cells) else "") for key, index in header.items()})
    if header is None:
        raise ExportChanged("export header not found")
    if total is not None and total != len(rows):
        raise ExportChanged(f"export total {total} != {len(rows)} rows")
    return rows


def _year_label(value) -> str:
    """车型主档 ปีที่ผลิต（如 2026）→ 段头的「26」；没填 / 认不出 → 空（段头不写年款）。"""
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) != 4:
        return ""
    year = int(digits)
    if year > 2400:  # 佛历
        year -= 543
    return f"{year % 100:02d}"


def summarize(rows: Iterable[Dict[str, str]], scope: str, cars: Dict[str, Dict[str, str]]) -> Dict:
    """按「车型 + 颜色」汇总，按车型主档的年款分段。

    ``cars``：车型代码 → ``{"name": ชื่อรถ, "model_name": ชื่อรุ่นรถ, "year": ปีที่ผลิต}``（本地快照；缺了就回落）。
    返回 ``{"sections": [{"year": "26", "rows": [...]}], "total": n}``，每行
    ``{"name", "code", "count", "locations": [(地点, 台数)]}``；段与行都按代码排序，地点按台数多到少。"""
    spec = SCOPES[scope]
    groups: Dict[tuple, Dict] = {}
    for row in rows:
        if spec["available_only"] and not is_available(row.get("status")):
            continue
        model, color = row.get("model") or "", row.get("color") or ""
        car = cars.get(model) or {}
        year = _year_label(car.get("year"))
        group = groups.setdefault((year, model, color), {
            "name": car.get("name") or car.get("model_name") or model,
            "code": " ".join(part for part in (model, color) if part),
            "count": 0,
            "places": {},
        })
        group["count"] += 1
        place = row.get("location") or "-"
        group["places"][place] = group["places"].get(place, 0) + 1
    sections: Dict[str, List[Dict]] = {}
    for (year, _model, _color), group in sorted(groups.items(), key=lambda item: (-int(item[0][0] or -1), item[0][1:])):
        places = sorted(group.pop("places").items(), key=lambda item: (-item[1], item[0]))
        sections.setdefault(year, []).append({**group, "locations": places})
    return {"sections": [{"year": year, "rows": section} for year, section in sections.items()],
            "total": sum(group["count"] for group in groups.values())}


def locations_text(locations) -> str:
    """样表的「ปลวกแดง 1 / ระยอง 1」。"""
    return " / ".join(f"{place} {count}" for place, count in locations)


def form_values(page: str) -> Dict[str, str]:
    """报表表单的输入框（含 MR.ERP 按账号预填的隐藏范围），原样带回去提交。"""
    import html

    values = {}
    for tag in re.findall(r"<input\b[^>]*>", page or "", re.I):
        name = re.search(r'\bname="([^"]+)"', tag)
        kind = (re.search(r'\btype="([^"]+)"', tag) or [None, "text"])[1].lower()
        if not name or kind in {"button", "submit"}:
            continue
        value = re.search(r'\bvalue="([^"]*)"', tag)
        values[name.group(1)] = html.unescape(value.group(1)) if value else ""
    if "branchval_bg" not in values or "carval_bg" not in values:
        raise ExportChanged("report form changed")
    return values


__all__ = ["EXPORT_PATHS", "ExportChanged", "FORM_PATH", "READ_PATHS", "SCOPES", "form_values", "is_available",
           "locations_text", "parse_export", "summarize"]
