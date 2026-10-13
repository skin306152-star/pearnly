"""盘点页的时间按站点时区显示（2026-10-13：网页盘点页的盘点时间比曼谷时间早 7 小时）。

Cowork 盘点库里的 *_at（`service.now()`）是 UTC 裸时间；页面是 ERP 盘点页的副本，`p.datetime_text`
把收到的时间当站点时区原样显示，所以给页面的每个时间都要经 `service.site_time` 换成站点时区。
原生接口与导出表照旧是 UTC（导出表头写着 UTC）。只加载真实模块、不连库：Frappe 与权限用最小替身。
"""
import importlib
import re
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "apps" / "pearnly_cowork"
DESK_API = APP / "pearnly_cowork" / "stocktake" / "desk_api.py"
SITE_ZONE = "Asia/Bangkok"

# 同一笔库里的 UTC 时间：曼谷 10:15；另一笔跨过曼谷的午夜。
STORED = datetime(2026, 10, 13, 3, 15, 42, 123456)
SHOWN = datetime(2026, 10, 13, 10, 15, 42, 123456)
LATE = datetime(2026, 10, 13, 20, 30)
LATE_SHOWN = datetime(2026, 10, 14, 3, 30)


class Error(Exception):
    pass


def _throw(message, exc=None):
    raise (exc or Error)(message)


def _convert_utc_to_system_timezone(value):
    # 与 Frappe `frappe.utils.convert_utc_to_system_timezone` 同口径：裸时间当 UTC，换到站点时区（带时区返回）。
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ZoneInfo(SITE_ZONE))


def _get_datetime(value):
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def _modules():
    db = types.SimpleNamespace(get_value=lambda *a, **k: None, count=lambda *a, **k: 0)
    frappe = types.SimpleNamespace(
        _=lambda text: text, throw=_throw,
        whitelist=lambda *a, **k: (lambda fn: fn), db=db, session=types.SimpleNamespace(user="counter@example.com"),
        local=types.SimpleNamespace(), conf={}, QueryDeadlockError=Error, DoesNotExistError=Error,
        DuplicateEntryError=Error, PermissionError=Error, ValidationError=Error)
    utils = types.SimpleNamespace(convert_utc_to_system_timezone=_convert_utc_to_system_timezone,
                                  get_datetime=_get_datetime)
    access_log = types.SimpleNamespace(make_access_log=lambda **kw: None)
    package = types.ModuleType("pearnly_cowork")
    package.__path__ = [str(APP / "pearnly_cowork")]
    scope = types.SimpleNamespace(default_workspace=lambda: "WS-1", contains=lambda workspace: True)
    modules = {
        "frappe": frappe, "frappe.utils": utils, "frappe.model": types.SimpleNamespace(),
        "frappe.model.document": types.SimpleNamespace(Document=object),
        "frappe.core": types.SimpleNamespace(), "frappe.core.doctype": types.SimpleNamespace(),
        "frappe.core.doctype.access_log": types.SimpleNamespace(),
        "frappe.core.doctype.access_log.access_log": access_log,
        "pearnly_core": types.ModuleType("pearnly_core"),
        "pearnly_core.shell": types.SimpleNamespace(entitlements=lambda: ["stocktake"]),
        "pearnly_cowork": package, "pearnly_cowork.access": types.SimpleNamespace(scope=scope),
    }
    try:
        import openpyxl  # noqa: F401  # 候选镜像里有；开发闸门的系统 Python 没有，导出模块只需要能导入
    except ModuleNotFoundError:
        styles = types.SimpleNamespace(**{name: object for name in ("Alignment", "Border", "Font", "PatternFill", "Side")})
        modules.update({"openpyxl": types.SimpleNamespace(Workbook=object), "openpyxl.styles": styles})
    return frappe, modules


def _load():
    frappe, modules = _modules()
    with mock.patch.dict(sys.modules, modules):
        for name in list(sys.modules):
            if name.startswith("pearnly_cowork.stocktake"):
                del sys.modules[name]
        desk_api = importlib.import_module("pearnly_cowork.stocktake.desk_api")
        export = importlib.import_module("pearnly_cowork.stocktake.export")
    return frappe, desk_api, export


class SiteTime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frappe, cls.desk_api, cls.export = _load()
        cls.service = cls.desk_api.service

    def test_stored_utc_is_shown_in_the_site_time_zone(self):
        site_time = self.service.site_time
        self.assertEqual(site_time(STORED), SHOWN)
        self.assertIsNone(site_time(STORED).tzinfo)  # 与库里其它 Datetime 同形状：站点时区的裸时间
        self.assertEqual(site_time(LATE), LATE_SHOWN)  # 跨午夜日期也跟着走
        self.assertEqual(site_time("2026-10-13 03:15:42.123456"), SHOWN)
        self.assertIsNone(site_time(None))
        self.assertEqual(site_time(""), "")

    def test_task_list_shows_created_time_in_site_time(self):
        tasks = [{"id": "T1", "name": "October", "status": "active", "created_at": STORED, "created_by": "a",
                  "total": 2, "counted": 1, "differences": 0},
                 {"id": "T2", "name": "Night", "status": "closed", "created_at": LATE, "created_by": "a",
                  "total": 0, "counted": 0, "differences": 0}]
        with mock.patch.object(self.desk_api.upload, "records", return_value={"tasks": tasks, "total": 2}):
            rows = self.desk_api.api_list_stock_counts()
        self.assertEqual([row["creation"] for row in rows], [SHOWN, LATE_SHOWN])
        self.assertEqual(tasks[0]["created_at"], STORED)  # 列表按库里的 UTC 排序，只换显示值

    def _snapshot(self):
        entry = {"id": "E1", "item_id": "I1", "voided": False, "warehouse": "WH", "location": "A-1",
                 "quantity": "3.000000", "version": 0, "counted_by": "counter@example.com",
                 "counted_by_name": "Counter", "counted_at": STORED, "updated_by": "counter@example.com",
                 "updated_by_name": "Counter", "updated_at": STORED, "product_code": "P1",
                 "product_name": "Apple", "barcode": "", "unit": "pcs"}
        item = {"id": "I1", "product_code": "P1", "product_name": "Apple", "barcode": "", "warehouse": "WH",
                "location": "A-1", "unit": "pcs", "book_qty": "3.000000", "actual_qty": "3.000000",
                "difference": "0.000000", "version": 1, "counted_by": "counter@example.com", "counted_at": STORED}
        return {"items": [item], "entries": [entry], "total": 1, "counted": 1, "differences": 0}

    def test_review_shows_count_and_close_times_in_site_time(self):
        task = types.SimpleNamespace(name="T1", title="October", status="closed", closed_at=LATE,
                                     closed_by="counter@example.com")
        snapshot = self._snapshot()
        with mock.patch.object(self.service, "_task", return_value=task), \
                mock.patch.object(self.service, "report", return_value=snapshot), \
                mock.patch.object(self.desk_api.access, "allowed", return_value=True):
            review = self.desk_api._review("T1")
        self.assertEqual(review["task"]["closed_at"], LATE_SHOWN)
        self.assertEqual([entry["counted_at"] for entry in review["entries"]], [SHOWN])
        # 页面那份换了，服务快照（导出也读它）不动：库里与导出仍是 UTC。
        self.assertEqual(snapshot["entries"][0]["counted_at"], STORED)

    def test_item_photos_show_count_time_in_site_time(self):
        photos = [{"file_url": "/private/files/a.jpg", "file_name": "a.jpg", "entry_id": "E1",
                   "quantity": "3.000000", "warehouse": "WH", "location": "A-1", "voided": 0, "counted_at": STORED}]
        with mock.patch.object(self.desk_api.counting, "item_entries", return_value={"photos": photos}):
            rows = self.desk_api.api_stock_count_item_photos("T1", "I1")
        self.assertEqual([row["counted_at"] for row in rows], [SHOWN])

    def test_the_export_keeps_utc_under_its_utc_header(self):
        # 导出表是旧线上原样（来源一致性测试守着）：同一笔库里的时间按 UTC 写、表头标 UTC，不换站点时区。
        self.assertEqual(self.export._utc_iso(STORED), "2026-10-13T03:15:42.123456+00:00")
        self.assertTrue(self.export.THAI["counted_at"].endswith("(UTC)"))
        self.assertTrue(self.export.THAI["updated_at"].endswith("(UTC)"))

    def test_every_time_the_page_gets_goes_through_site_time(self):
        # 以后给页面加时间字段也得走同一个入口，不能把库里的 UTC 直接交给页面。
        source = DESK_API.read_text(encoding="utf-8")
        values = re.findall(r'"(\w+_at|creation)":\s*([^,}\n]+)', source)
        self.assertTrue(values)
        for field, value in values:
            if value.startswith('"'):
                continue  # 排序列名映射（"creation": "created_at"），不是给页面的值
            with self.subTest(field=field):
                self.assertTrue(value.startswith("service.site_time("), value)


if __name__ == "__main__":
    unittest.main()
