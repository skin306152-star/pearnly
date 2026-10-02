#!/usr/bin/env python3
"""导出改后台任务（`pearnly_core.exports`）：共用层的当场 / 排队 / 跑任务 / 下载 / 过期，加上三个产品每个导出入口的
成功、失败、无权限三条路径，以及每个入口登记的计数与生成函数都能解析到真函数。

在产品镜像里跑（要能 import Frappe 与各 App）；数据库、队列、推送都换成内存里的假的，不连站点。
"""
from pathlib import Path
import importlib
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps" / app) for app in ("pearnly_core", "pearnly_cowork", "pearnly_erp_portal")]
# 框架导入 File 时按 `../logs` 开日志：镜像里从 bench 根目录跑时那里没有，先进 sites 目录。
if Path("/home/frappe/frappe-bench/sites").is_dir():
    os.chdir("/home/frappe/frappe-bench/sites")

import frappe  # noqa: E402

from pearnly_core.exports import service  # noqa: E402

USER, OTHER = "owner@example.com", "other@example.com"


class Row(dict):
    __getattr__ = dict.get


class FakeDB:
    def __init__(self):
        self.rows = {}
        self.commits = 0

    def get_value(self, doctype, name, fields, as_dict=False, **kwargs):
        row = self.rows.get(name)
        if row is None:
            return None
        return Row({field: row.get(field) for field in fields}) if as_dict else row.get(fields)

    def set_value(self, doctype, name, field, value=None, **kwargs):
        self.rows[name].update(field if isinstance(field, dict) else {field: value})

    def delete(self, doctype, filters):
        self.rows.pop(filters["name"], None)

    def commit(self):
        self.commits += 1

    def rollback(self, **kwargs):
        pass


class Harness(unittest.TestCase):
    """假站点：Pearnly Export 记在内存，文件写进临时目录，队列和推送只记下来。"""

    def setUp(self):
        frappe.local.response = frappe._dict()
        frappe.local.flags = frappe._dict()  # `frappe.throw` 要用
        frappe.local.lang = "th"
        frappe.local.message_log = []
        frappe.local.pearnly_export_response = False
        self.session = SimpleNamespace(user=USER)
        self.db = FakeDB()
        self.queued, self.pushed, self.committed_at_enqueue = [], [], []
        self.folder = tempfile.mkdtemp()
        self.conf = {}

        def get_doc(body):
            db = self.db

            class Doc(dict):
                def insert(self, **kwargs):
                    db.rows[self["name"]] = dict(self)
                    return self
            return Doc(body)

        def get_all(doctype, filters=None, fields=None, **kwargs):
            limit = filters["expires_at"][1]
            return [Row(name=name, file_key=row.get("file_key")) for name, row in self.db.rows.items()
                    if row["expires_at"] < limit]

        patches = {
            "session": self.session, "db": self.db, "get_doc": get_doc, "get_all": get_all,
            "enqueue": lambda method, **kwargs: self.queued.append((method, kwargs)) or self.committed_at_enqueue.append(self.db.commits),
            "publish_realtime": lambda event, message, user=None, **kwargs: self.pushed.append((event, message, user)),
            "set_user": lambda user: setattr(self.session, "user", user),
            "get_site_path": lambda *parts: os.path.join(self.folder, *parts[1:]),
            "log_error": lambda **kwargs: None, "conf": self.conf,
        }
        for name, value in patches.items():
            p = patch.object(service.frappe, name, value, create=True)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(service, "now_datetime", datetime.now)  # 系统时区要读站点设置
        p.start()
        self.addCleanup(p.stop)
        self.functions = {}
        real = frappe.get_attr
        p = patch.object(service.frappe, "get_attr", lambda path: self.functions.get(path) or real(path))
        p.start()
        self.addCleanup(p.stop)


def denied(*args, **kwargs):
    frappe.throw("No permission", frappe.PermissionError)


class CoreExports(Harness):
    def register(self, size=3, content=b"PK-file", fail=None):
        def count(**kwargs):
            self.seen_count = kwargs
            return size

        def build(**kwargs):
            self.seen_build = kwargs
            if fail:
                raise fail
            return content
        self.functions.update({"t.count": count, "t.build": build, "t.denied": denied})

    def deliver(self, count="t.count"):
        return service.deliver(count=count, build="t.build", args={"task_id": "T-1"}, filename="a.xlsx",
                               content_type="application/x-test")

    def test_a_small_export_answers_with_the_file_right_away(self):
        self.register(size=service.DEFAULT_SYNC_ROWS)
        self.assertIsNone(self.deliver())
        self.assertEqual((frappe.local.response.filecontent, frappe.local.response.filename,
                          frappe.local.response.type), (b"PK-file", "a.xlsx", "download"))
        self.assertEqual((self.seen_count, self.seen_build), ({"task_id": "T-1"}, {"task_id": "T-1"}))
        self.assertEqual((self.queued, self.db.rows), ([], {}))

    def test_the_threshold_comes_from_the_site(self):
        self.conf["pearnly_export_sync_rows"] = 2
        self.register(size=3)
        self.assertIn("export_job", self.deliver())

    def test_a_large_export_is_queued_for_its_user_and_language(self):
        self.register(size=service.DEFAULT_SYNC_ROWS + 1)
        job = self.deliver()["export_job"]
        self.assertRegex(job, r"^[0-9a-f]{32}$")
        row = self.db.rows[job]
        self.assertEqual((row["user"], row["status"], row["lang"], json.loads(row["arguments"])),
                         (USER, "queued", "th", {"task_id": "T-1"}))
        self.assertEqual(self.queued, [("pearnly_core.exports.service.run",
                                        {"queue": "default", "timeout": service.TIMEOUT_SECONDS, "job": job,
                                         "enqueue_after_commit": False})])
        # 导出接口是 GET，请求结束不提交：任务行必须在排队前自己提交，否则被回滚、页面问状态只得到「没有权限」
        self.assertEqual(self.committed_at_enqueue, [1])
        self.assertNotIn("filecontent", frappe.local.response)

    def test_no_permission_means_no_file_and_no_job(self):
        self.register()
        with self.assertRaises(frappe.PermissionError):
            self.deliver(count="t.denied")
        self.assertEqual((self.queued, self.db.rows), ([], {}))
        self.assertNotIn("filecontent", frappe.local.response)

    def queued_job(self, **kwargs):
        self.register(size=service.DEFAULT_SYNC_ROWS + 1, **kwargs)
        job = self.deliver()["export_job"]
        self.session.user = "Administrator"  # 后台任务开头是管理员身份
        service.run(job)
        self.session.user = USER
        return job

    def test_the_background_job_builds_as_the_user_and_pushes_to_them(self):
        seen = []
        self.functions["t.build"] = lambda **kwargs: seen.append(self.session.user) or b"big-file"
        self.register(size=service.DEFAULT_SYNC_ROWS + 1)
        self.functions["t.build"] = lambda **kwargs: seen.append(self.session.user) or b"big-file"
        job = self.deliver()["export_job"]
        service.run(job)
        self.assertEqual(seen, [USER])
        row = self.db.rows[job]
        self.assertEqual((row["status"], row["size"]), ("done", 8))
        self.assertEqual(self.pushed, [("pearnly_export", {"job": job, "status": "done"}, USER)])
        path = os.path.join(self.folder, "pearnly-exports", row["file_key"])
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
        self.assertEqual(service.status(job), {"job": job, "status": "done", "error": ""})
        frappe.local.response = frappe._dict()
        service.download(job)
        self.assertEqual((frappe.local.response.filecontent, frappe.local.response.filename),
                         (b"big-file", "a.xlsx"))
        self.assertTrue(frappe.local.pearnly_export_response)  # 下载不进缓存

    def test_a_failed_build_is_reported_to_the_user(self):
        job = self.queued_job(fail=frappe.ValidationError("reconciliation.review_required"))
        self.assertEqual(service.status(job), {"job": job, "status": "failed",
                                               "error": "reconciliation.review_required"})
        self.assertEqual(self.pushed[-1], ("pearnly_export", {"job": job, "status": "failed"}, USER))
        with self.assertRaises(frappe.PermissionError):
            service.download(job)

    def test_a_program_error_is_not_shown_raw(self):
        job = self.queued_job(fail=KeyError("secret internals"))
        self.assertEqual(service.status(job)["error"], "The export could not be created.")

    def test_only_the_owner_sees_or_downloads_the_file(self):
        job = self.queued_job()
        self.session.user = OTHER
        for call in (service.status, service.download):
            with self.assertRaises(frappe.PermissionError):
                call(job)
        self.session.user = "Guest"
        with self.assertRaises(frappe.PermissionError):
            service.status(job)
        self.session.user = USER
        with self.assertRaises(frappe.PermissionError):
            service.status("0" * 32)  # 没有的与别人的同一句

    def test_download_checks_the_permission_again(self):
        job = self.queued_job()
        self.db.rows[job]["count_method"] = "t.denied"  # 生成后权限被收回
        with self.assertRaises(frappe.PermissionError):
            service.download(job)

    def test_expired_files_cannot_be_fetched_and_are_swept(self):
        job = self.queued_job()
        row = self.db.rows[job]
        path = os.path.join(self.folder, "pearnly-exports", row["file_key"])
        row["expires_at"] = datetime.now() - timedelta(seconds=1)
        with self.assertRaises(frappe.PermissionError):
            service.download(job)
        service.sweep()
        self.assertEqual((self.db.rows, os.path.exists(path)), ({}, False))

    def test_a_job_runs_once(self):
        job = self.queued_job()
        before = dict(self.db.rows[job])
        service.run(job)
        self.assertEqual(self.db.rows[job], before)

    def test_a_forged_file_key_never_leaves_the_folder(self):
        for key in ("../site_config.json", "", "ABC"):
            with self.assertRaises(frappe.PermissionError):
                service.path_of(key)


# 每个导出入口：(接口, 调用参数, 计数函数, 生成函数, 计数 / 生成收到的参数, 需要换掉的访问日志模块)
ENDPOINTS = [
    ("pearnly_erp_portal.sales.api.api_sales_export", {"params": '{"q": "A"}'},
     "pearnly_erp_portal.sales.output.export_size", "pearnly_erp_portal.sales.output.workbook",
     {"params": {"q": "A"}}, None),
    ("pearnly_erp_portal.purchasing.api.api_purchase_export", {"params": '{"q": "B"}'},
     "pearnly_erp_portal.purchasing.output.export_size", "pearnly_erp_portal.purchasing.output.workbook",
     {"params": {"q": "B"}}, None),
    ("pearnly_erp_portal.inventory.api.api_stock_export", {"warehouse": "W1", "q": "x", "cmd": "ignored"},
     "pearnly_erp_portal.inventory.api.stock_export_size", "pearnly_erp_portal.inventory.api.stock_export_file",
     {"warehouse": "W1", "q": "x"}, None),
    ("pearnly_erp_portal.inventory.api.api_stock_count_export", {"name": "SC-1"},
     "pearnly_erp_portal.inventory.api.stock_count_export_size",
     "pearnly_erp_portal.inventory.api.stock_count_export_file", {"name": "SC-1"}, None),
    ("pearnly_erp_portal.cashier.api.api_shifts_export", {"names": '["S-1", "S-2"]'},
     "pearnly_erp_portal.cashier.output.export_size", "pearnly_erp_portal.cashier.output.workbook",
     {"names": ["S-1", "S-2"]}, None),
    ("pearnly_cowork.reconciliation.bank_recon_api.export", {"task_id": "CBR-1", "lang": "zh"},
     "pearnly_cowork.reconciliation.bank_recon_service.export_size",
     "pearnly_cowork.reconciliation.bank_recon_service.export", {"task_id": "CBR-1", "lang": "zh"},
     "pearnly_cowork.reconciliation.bank_recon_api"),
    ("pearnly_cowork.reconciliation.income_recon_api.export", {"task_id": "CIR-1", "lang": "en"},
     "pearnly_cowork.reconciliation.income_recon_service.export_size",
     "pearnly_cowork.reconciliation.income_recon_service.export", {"task_id": "CIR-1", "lang": "en"},
     "pearnly_cowork.reconciliation.income_recon_api"),
    ("pearnly_cowork.recon_center.api.export_vat", {"task_id": "CVC-1", "lang": "th"},
     "pearnly_cowork.reconciliation.vat_check_service.export_size",
     "pearnly_cowork.reconciliation.vat_check_service.export", {"task_id": "CVC-1", "lang": "th"},
     "frappe.core.doctype.access_log.access_log"),
    ("pearnly_cowork.stocktake.api.export", {"task_id": "CST-1"},
     "pearnly_cowork.stocktake.service.export_size", "pearnly_cowork.stocktake.service.export",
     {"task_id": "CST-1"}, "pearnly_cowork.stocktake.api"),
    ("pearnly_cowork.stocktake.desk_api.api_stock_count_export", {"name": "CST-2"},
     "pearnly_cowork.stocktake.service.export_size", "pearnly_cowork.stocktake.service.export",
     {"task_id": "CST-2"}, "pearnly_cowork.stocktake.desk_api"),
    ("pearnly_cowork.work.api.export", {"names": "C-1,C-2", "board": "B-1"},
     "pearnly_cowork.work.api.export_size", "pearnly_cowork.work.api.export_file",
     {"names": ["C-1", "C-2"], "board": "B-1"}, None),
    ("pearnly_core.billing_www.export_records", {"date_from": "2026-09-01"},
     "pearnly_core.billing_www.export_records_size", "pearnly_core.billing_www.export_records_file",
     {"date_from": "2026-09-01", "date_to": ""}, None),
]


def resolve(path):
    module, _, attribute = path.rpartition(".")
    return getattr(importlib.import_module(module), attribute)


class EveryEndpoint(Harness):
    """每个入口三条路径：成功（小的当场给、大的排队后跑完能下载）、失败（生成出错推给本人）、无权限（不出文件不建任务）。"""

    def run_endpoint(self, endpoint, kwargs, logger):
        stops = []
        if logger:
            p = patch.object(importlib.import_module(logger), "make_access_log", lambda **kw: None)
            p.start()
            stops.append(p)
        try:
            return resolve(endpoint)(**dict(kwargs))
        finally:
            for p in stops:
                p.stop()

    def test_the_registered_functions_exist(self):
        for endpoint, _kwargs, count, build, _args, _logger in ENDPOINTS:
            with self.subTest(endpoint=endpoint):
                self.assertTrue(callable(resolve(endpoint)))
                self.assertTrue(callable(resolve(count)))
                self.assertTrue(callable(resolve(build)))

    def test_success_small_and_large(self):
        for endpoint, kwargs, count, build, args, logger in ENDPOINTS:
            with self.subTest(endpoint=endpoint):
                seen = []
                self.functions[count] = lambda size=[1], **kw: seen.append(("count", kw)) or size[0]
                self.functions[build] = lambda **kw: seen.append(("build", kw)) or b"FILE"
                frappe.local.response = frappe._dict()
                self.assertIsNone(self.run_endpoint(endpoint, kwargs, logger))
                self.assertEqual(frappe.local.response.filecontent, b"FILE")
                self.assertEqual(seen, [("count", args), ("build", args)])
                self.functions[count] = lambda **kw: service.DEFAULT_SYNC_ROWS + 1
                frappe.local.response = frappe._dict()
                job = self.run_endpoint(endpoint, kwargs, logger)["export_job"]
                self.assertNotIn("filecontent", frappe.local.response)
                self.assertEqual(json.loads(self.db.rows[job]["arguments"]), json.loads(json.dumps(args)))
                service.run(job)
                self.assertEqual(service.status(job)["status"], "done")
                frappe.local.response = frappe._dict()
                service.download(job)
                self.assertEqual(frappe.local.response.filecontent, b"FILE")

    def test_failure(self):
        for endpoint, kwargs, count, build, _args, logger in ENDPOINTS:
            with self.subTest(endpoint=endpoint):
                self.functions[build] = lambda **kw: frappe.throw("broken input")
                self.functions[count] = lambda **kw: 1
                with self.assertRaises(frappe.ValidationError):  # 小的：当场报原因
                    self.run_endpoint(endpoint, kwargs, logger)
                self.functions[count] = lambda **kw: service.DEFAULT_SYNC_ROWS + 1
                job = self.run_endpoint(endpoint, kwargs, logger)["export_job"]
                service.run(job)
                self.assertEqual(service.status(job), {"job": job, "status": "failed", "error": "broken input"})

    def test_no_permission(self):
        for endpoint, kwargs, count, build, _args, logger in ENDPOINTS:
            with self.subTest(endpoint=endpoint):
                built = []
                self.functions[count] = denied
                self.functions[build] = lambda **kw: built.append(kw) or b"FILE"
                frappe.local.response = frappe._dict()
                jobs = dict(self.db.rows)
                with self.assertRaises(frappe.PermissionError):
                    self.run_endpoint(endpoint, kwargs, logger)
                self.assertEqual((built, self.db.rows), ([], jobs))
                self.assertNotIn("filecontent", frappe.local.response)


if __name__ == "__main__":
    unittest.main()
