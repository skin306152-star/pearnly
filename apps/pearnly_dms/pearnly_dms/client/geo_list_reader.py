# -*- coding: utf-8 -*-
"""Complete four-level address tree from the native **list** endpoints.

为什么不用详情页：泰国全国 15,729 条（省 77 / 区 928 / 镇 7,364 / 邮编 7,360），
详情页是"一条一次请求"，按实测速率约 33 分钟；而采集器要求**整代完整才发布**，
中途任何一页 30 秒超时都会让整代作废（2026-09-24 真实环境实测：第 31 轮被打断）。

列表接口一次能返回数百行，且每行自带**本行与各级父级的显示名**：

    provinces    [省]
    districts    [区, 省]
    subdistricts [镇, 区, 省]
    zipcodes     [邮编, 镇, 区, 省]

于是同一棵完整树只要几十个请求、约 8 秒（实测 22 个请求 / 6.2 秒 / 15,729 行，
与已知总数逐表吻合）。

**有意不用**详情页的英文字段（`txtprovincesengname` / `txtdistrictsengname` /
`txtsubdistrictsengname`）：抽查 12 行全为空，邮编表连该控件都没有——15,729 次详情
请求换不到任何东西（见任务记录的本轮调查）。

父级身份由**显示名**推导，必须唯一；未知父级或重名歧义一律判整代失败，绝不猜。
列表列是**按位置**解析的（详情页才按字段名），所以对行形状做严格校验：列数不符、
ID 重复、响应被截断都立即失败，不出错数据。

本模块不 import frappe、不落盘、不发布；调用方负责会话与发布。
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re

from pearnly_dms.contracts.client_base import DmsClientError
from pearnly_dms.contracts.geo_matching import complete_tree


#: 列表请求的原生固定参数（与页面里的 `showdata()` 同形）。
LIST_PARAMS = {"sd": "", "ftd": "1", "selcolsort": "1", "selcolsorttype": "1"}

#: 每张表的列表列（行内 `<p>` 的顺序，不含"制单人/复核"那一段）。顺序即契约。
COLUMNS = {
    "provinces": ("province",),
    "districts": ("district", "province"),
    "subdistricts": ("subdistrict", "district", "province"),
    "zipcodes": ("zipcode", "subdistrict", "district", "province"),
}

_ROW = re.compile(r'<div data-val="([^"]*)"[^>]*>(.*?)(?=<div data-val="|$)', re.S)
_PARAGRAPH = re.compile(r"<p[^>]*>(.*?)</p>", re.S)
_TAG = re.compile(r"<[^>]+>")
_COUNT = re.compile(r"dt::(0|[1-9][0-9]{0,8})")


@dataclass(frozen=True)
class GeoListLimits:
    """一次采集的总量上限。任一项超限即整代失败（宁可失败，不发布可疑数据）。"""

    page_size: int = 500
    max_pages: int = 200
    max_rows: int = 40000
    max_requests: int = 200
    max_response_bytes: int = 8 * 1024 * 1024
    max_total_bytes: int = 128 * 1024 * 1024
    max_seconds: float = 600

    def __post_init__(self):
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(value) or value <= 0 for value in self.__dict__.values()):
            raise ValueError("Geo list limits must be positive")
        if not 1 <= self.page_size <= 500:
            raise ValueError("Geo list page size outside the native bound")


def _fail(reason):
    # 绝不把响应正文、HTML 或凭据放进错误与日志。
    raise DmsClientError("DMS address catalogue incomplete: " + reason,
                         "ERR_DMS_SNAPSHOT_INCOMPLETE")


def _text(value):
    return re.sub(r"\s+", " ", _TAG.sub("", value)).strip()


def _identity(value):
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 128:
        _fail("invalid address identity")
    return value


def _rows(body, kind):
    """一页 → `[(id, [列文本...])]`。形状不对立即失败。"""
    columns = COLUMNS[kind]
    if not body.lstrip().startswith("dt::"):
        _fail("unexpected address list response")
    rows, seen = [], set()
    for identity, block in _ROW.findall(body):
        # 只解析 `<div class="detaildata">`：后面的 `statuscf` 是制单人/复核戳。
        cells = [_text(item) for item in _PARAGRAPH.findall(block.split('class="statuscf"')[0])]
        if len(cells) != len(columns) or any(not cell for cell in cells):
            _fail("address list row shape changed")
        key = _identity(identity)
        if key in seen:
            _fail("repeated address identity within a page")
        seen.add(key)
        rows.append((key, cells))
    return rows


class _Usage:
    def __init__(self, limits):
        self.limits = limits
        self.requests = 0
        self.bytes = 0
        self.rows = 0

    def note(self, size):
        self.bytes += size
        if size > self.limits.max_response_bytes or self.bytes > self.limits.max_total_bytes:
            _fail("address response budget exceeded")


def fetch_table(client, kind, limits, usage):
    """整张表：先取原生总数，再按页读满。

    不以"读到空页"作为结束条件 —— 翻过最后一页时对方返回的并不是列表正文，把它当成
    结束会让"少读了一页"和"正常结束"长得一样。用原生计数做锚点，读满才成立：
    行数少于计数、或某页行数不足一页却还没读满，都判整代失败。
    """
    usage.requests += 1
    if usage.requests > limits.max_requests:
        _fail("address request budget exceeded")
    try:
        body = client._post_text(kind + "/component/showdata.php", dict(LIST_PARAMS))
    except Exception:
        _fail("address count transport failed")
    if not isinstance(body, str):
        _fail("non-text address response")
    match = _COUNT.fullmatch(body.strip())
    if not match:
        _fail("unexpected address count response")
    total = int(match.group(1))
    if total > limits.max_rows or math.ceil(total / limits.page_size) > limits.max_pages:
        _fail("native address count exceeds bound")
    if not total:
        return []

    rows, seen = [], set()
    page = 1
    while len(rows) < total:
        if page > limits.max_pages:
            _fail("address page budget exceeded")
        usage.requests += 1
        if usage.requests > limits.max_requests:
            _fail("address request budget exceeded")
        try:
            body = client._post_text(kind + "/component/showdata.php",
                                     {**LIST_PARAMS, "sdtamt": str(limits.page_size),
                                      "sdtpage": str(page)})
        except Exception:
            _fail("address list transport failed")
        if not isinstance(body, str):
            _fail("non-text address response")
        usage.note(len(body.encode("utf-8")))
        if body.lstrip().startswith("err::"):
            _fail("native address read rejected")
        page_rows = _rows(body, kind)
        if not page_rows:
            _fail("address table ended before its native count")
        for identity, cells in page_rows:
            if identity in seen:
                _fail("repeated address identity across pages")
            seen.add(identity)
            rows.append((identity, cells))
        usage.rows += len(page_rows)
        if usage.rows > limits.max_rows:
            _fail("address row budget exceeded")
        if len(page_rows) < limits.page_size and len(rows) < total:
            # 不足一页却没读满 = 计数与分页不一致，宁可失败也不发布残缺代。
            _fail("address page shorter than its native count")
        page += 1
    if len(rows) != total:
        _fail("address row count differs from its native count")
    return rows


def _index(rows, kind):
    """显示名 → 原生 ID。重名即歧义，整代失败。"""
    index = {}
    for identity, cells in rows:
        name = cells[0]
        if name in index:
            _fail(f"ambiguous {kind} name")
        index[name] = identity
    return index


def _parent(index, key, kind):
    parent = index.get(key)
    if parent is None:
        _fail(f"unknown parent for {kind}")
    return parent


def build_tree(tables):
    """四张表 → 完整四级树（键名与既有投影一致）。任一级对不上即失败。"""
    if not isinstance(tables, dict) or set(tables) != set(COLUMNS):
        _fail("missing or unknown address table")
    provinces, districts = tables["provinces"], tables["districts"]
    subdistricts, zipcodes = tables["subdistricts"], tables["zipcodes"]
    if not provinces:
        _fail("empty address table")

    province_by_name = _index(provinces, "province")
    district_by_key, subdistrict_by_key = {}, {}
    for identity, cells in districts:
        key = (cells[0], cells[1])
        if key in district_by_key:
            _fail("ambiguous district name within a province")
        district_by_key[key] = identity
    for identity, cells in subdistricts:
        key = (cells[0], cells[1], cells[2])
        if key in subdistrict_by_key:
            _fail("ambiguous subdistrict name within a district")
        subdistrict_by_key[key] = identity

    tree = {
        "provinces": {"": [[identity, cells[0]] for identity, cells in provinces]},
        "districts": {parent: [] for parent in province_by_name.values()},
        "subdistricts": {parent: [] for parent in district_by_key.values()},
        "zipcodes": {parent: [] for parent in subdistrict_by_key.values()},
    }
    for identity, cells in districts:
        parent = _parent(province_by_name, cells[1], "district")
        tree["districts"][parent].append([identity, cells[0]])
    for identity, cells in subdistricts:
        parent = _parent(district_by_key, (cells[1], cells[2]), "subdistrict")
        tree["subdistricts"][parent].append([identity, cells[0]])
    for identity, cells in zipcodes:
        parent = _parent(subdistrict_by_key, (cells[1], cells[2], cells[3]), "zipcode")
        tree["zipcodes"][parent].append([identity, cells[0]])

    if not complete_tree(tree):
        _fail("invalid complete tree")
    return tree


def read(client, limits=None):
    """读全四张表并返回 `(tree, 原始行)`。原始行只进私有快照，不对外展示。"""
    limits = limits or GeoListLimits()
    usage = _Usage(limits)
    tables = {kind: fetch_table(client, kind, limits, usage) for kind in COLUMNS}
    return build_tree(tables), tables, usage


__all__ = ["COLUMNS", "GeoListLimits", "LIST_PARAMS", "build_tree", "fetch_table", "read"]
