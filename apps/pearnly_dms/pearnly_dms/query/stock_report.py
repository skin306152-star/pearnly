# -*- coding: utf-8 -*-
"""库存停放报表：读 MR.ERP 原生导出 → 按「车型 + 颜色」汇总（纯计算，不碰 IO）。

来源报表：MR.ERP DMS「รายงานสต๊อกคงค้างแสดงสถานที่จอดรถ」（`rpcarallocate/form.php`）。评估与实查见
`docs/work/2026-11-26-dms-line-stock-report-assessment.md`：

- 表单打开时 MR.ERP 已按登录账号的权限预填起止范围；原样提交 = 这个账号能看到的全部库存。
- 「ออกรายงาน」= 同一表单 POST `component/report/1.php`（xlsx）或 `2.php`（PDF）。
- 导出一台车一行、9 列：รุ่นรถ（代码）/ สีรถ（代码）/ หมายเลขเครื่อง / หมายเลขตัวถัง / สถานะรถ / สถานที่จอดรถ (คลัง) /
  เลขที่ใบจอง / ชื่อที่ปรึกษาการขาย / ทีมสาขา；前两行是标题与日期，末行「รวม n รายการ」。
- 「ว่าง」= 订车单号为空（MR.ERP 自己「สต๊อกว่าง」的口径，见 `is_available`），与 `selallocate`（是否分给团队）无关。
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
# 「空车」MR.ERP 不能筛，原件两份是它的「全部」，转换表再取订车单号为空的车（`is_available`）。
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


def is_available(row: Dict[str, str]) -> bool:
    """「空车」= 还没配给任何订车单的车：订车单号为空。车辆状态不参与判断。

    依据是 MR.ERP 自己的口径：它的「รายงานคาดการณ์รับรถเทียบสต๊อกว่าง-สรุป」里「สต๊อกว่างคงค้างยังไม่จับคู่」一列，
    与同一时刻本报表「เลขที่ใบจอง 为空」的车逐个车型颜色对得上（测试库 11-27 实查 9 台：空闲 6 台，含一台状态
    「รถทีมสาขาจอง」但没有订车单号的；两台有订车单号的「รถว่าง-โชว์」「รถทีมสาขาจอง」不算）。状态名不是判据：
    11-26 上线时按「状态叫 รถว่าง」认，真实经销商 92 台里 91 台是「รถลงแล้ว」，只出了 1 台
    （`docs/work/2026-11-27-dms-stock-available-fix.md`）。"""
    return not str(row.get("booking") or "").strip()


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


def summarize(rows: Iterable[Dict[str, str]], scope: str, cars: Dict[str, Dict[str, str]]) -> Dict:
    """按「车型 + 颜色」汇总成一张表（用户 11-26「做法一」：不按年款分段，只留最上面一行表头，同样表）。

    ``cars``：车型代码 → ``{"name": ชื่อรถ, "model_name": ชื่อรุ่นรถ}``（本地快照；缺了就回落）。
    返回 ``{"rows": [...], "total": n}``：行按车型代码、颜色代码排序，每行
    ``{"name", "code", "count", "locations": [(地点, 台数)]}``，地点按台数多到少。表头不写年款（用户 11-27）。"""
    spec = SCOPES[scope]
    groups: Dict[tuple, Dict] = {}
    for row in rows:
        if spec["available_only"] and not is_available(row):
            continue
        model, color = row.get("model") or "", row.get("color") or ""
        car = cars.get(model) or {}
        group = groups.setdefault((model, color), {
            "name": car.get("name") or car.get("model_name") or model,
            "code": " ".join(part for part in (model, color) if part),
            "count": 0,
            "places": {},
        })
        group["count"] += 1
        place = row.get("location") or "-"
        group["places"][place] = group["places"].get(place, 0) + 1
    out = []
    for _key, group in sorted(groups.items()):
        places = sorted(group.pop("places").items(), key=lambda item: (-item[1], item[0]))
        out.append({**group, "locations": places})
    return {"rows": out, "total": sum(row["count"] for row in out)}


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
