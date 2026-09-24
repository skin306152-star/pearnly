"""Address catalogue read from the native **list** endpoints; no framework or IO.

替代旧的 `test_dms_geo_catalogue.py` + `test_dms_geo_resume.py`：那两个文件的前提是
「逐条读详情页 + 分片断点续跑」。2026-09-24 改成列表读取（详情方式全国要 15,729 次请求、
约 33 分钟，且一次 30 秒超时就让整代作废），分片断点随之退役，所以这里按新契约重写：

- 读取器：行形状、计数锚点、父级唯一性、完整树校验、限额；
- 适配器：整代成功才发布、失败保留上一代、忙碌不误判、不发无凭据的读。

断言的意图（严格拒绝、绝不猜、不发布残缺代）一条没少，只是换了协议。
"""
from contextlib import nullcontext
from copy import deepcopy
import importlib.util
from pathlib import Path
import re
import sys
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_dms"))

from pearnly_dms.client.geo_list_reader import (
    COLUMNS, GeoListLimits, LIST_PARAMS, build_tree, fetch_table, read)
from pearnly_dms.contracts.client_base import DmsClientError
from pearnly_dms.contracts.geo_matching import complete_tree


# 合成数据：两省 / 三区 / 四镇 / 四邮编。每行给"本行及其父级的显示名"，与原生一致。
def synthetic():
    return {
        "provinces": [("P1", ["หนึ่ง"]), ("P2", ["สอง"])],
        "districts": [("D1", ["อำเภอหนึ่ง", "หนึ่ง"]), ("D2", ["อำเภอสอง", "สอง"]),
                      ("D3", ["อำเภอสาม", "สอง"])],
        "subdistricts": [("S1", ["ตำบลหนึ่ง", "อำเภอหนึ่ง", "หนึ่ง"]),
                         ("S2", ["ตำบลสอง", "อำเภอสอง", "สอง"]),
                         ("S3", ["ตำบลสาม", "อำเภอสอง", "สอง"]),
                         ("S4", ["ตำบลสี่", "อำเภอสาม", "สอง"])],
        "zipcodes": [("Z1", ["10000", "ตำบลหนึ่ง", "อำเภอหนึ่ง", "หนึ่ง"]),
                     ("Z2", ["20000", "ตำบลสอง", "อำเภอสอง", "สอง"]),
                     ("Z3", ["20001", "ตำบลสาม", "อำเภอสอง", "สอง"]),
                     ("Z4", ["20002", "ตำบลสี่", "อำเภอสาม", "สอง"])],
    }


class FakeClient:
    """严格的原生传输替身：只认计数与列表两个形状，页大小可被测试改小。"""

    def __init__(self, tables=None, *, fail_on=None, page_size=1000, shape_break=None,
                 repeat_id=False, truncate=None):
        self.tables = deepcopy(synthetic() if tables is None else tables)
        self.fail_on = fail_on
        self.page_size = page_size
        self.shape_break = shape_break
        self.repeat_id = repeat_id
        # 计数按全表报，分页只给前 n 行 —— 模拟"总量与分页对不上"。
        self.truncate = truncate
        self.calls = []
        self.per_kind = {}

    def _post_text(self, path, params):
        kind, suffix = path.split("/", 1)
        rows = self.tables[kind]
        self.calls.append((kind, dict(params)))
        self.per_kind[kind] = self.per_kind.get(kind, 0) + 1
        assert suffix == "component/showdata.php", path
        if self.fail_on and self.fail_on == (kind, self.per_kind[kind]):
            raise RuntimeError("synthetic transport failure")
        if "sdtpage" not in params:
            assert params == LIST_PARAMS
            return "dt::" + str(len(rows))
        assert set(params) == set(LIST_PARAMS) | {"sdtamt", "sdtpage"}
        if self.truncate is not None:
            rows = rows[:self.truncate]
        amount = min(int(params["sdtamt"]), self.page_size)
        page = int(params["sdtpage"])
        chunk = rows[(page - 1) * amount:page * amount]
        body = ["dt::"]
        for identity, cells in chunk:
            if self.shape_break == (kind, identity):
                cells = cells + ["extra"]
            body.append(f'<div data-val="{identity}" data-allo="" data-cf="">'
                        '<div class="detaildata"><div><p>'
                        + "</p><p>".join(cells) + "</p></div></div>"
                        '<div class="statuscf"><p><span>ผู้จัดทำ</span></p></div></div>')
        if self.repeat_id and page > 1 and chunk:
            body.append(f'<div data-val="{rows[0][0]}" data-allo="">'
                        '<div class="detaildata"><div><p>x</p></div></div></div>')
        return "".join(body)


class ReaderTests(unittest.TestCase):
    def test_reads_a_complete_four_level_tree(self):
        tree, tables, usage = read(FakeClient())
        self.assertEqual(len(tree["provinces"][""]), 2)
        self.assertEqual(sum(len(v) for v in tree["districts"].values()), 3)
        self.assertEqual(sum(len(v) for v in tree["subdistricts"].values()), 4)
        self.assertEqual(sum(len(v) for v in tree["zipcodes"].values()), 4)
        self.assertTrue(complete_tree({key: tree[key] for key in COLUMNS}))
        # 邮编那一行的标签是**邮编文字**（界面与匹配都用它），值仍是原生 ID。
        zips = [row for rows in tree["zipcodes"].values() for row in rows]
        self.assertIn(["Z1", "10000"], zips)
        self.assertEqual(usage.rows, 13)
        self.assertEqual(set(tables), set(COLUMNS))

    def test_parents_are_resolved_by_display_name_not_position(self):
        # 把镇的父省写错 → 必须整体失败，而不是挂到某个省下面。
        tables = synthetic()
        tables["districts"] = [("D1", ["อำเภอหนึ่ง", "不存在"])]
        with self.assertRaises(DmsClientError) as caught:
            build_tree(tables)
        self.assertEqual(caught.exception.error_code, "ERR_DMS_SNAPSHOT_INCOMPLETE")

    def test_ambiguous_parent_name_is_rejected_instead_of_guessed(self):
        tables = synthetic()
        tables["provinces"] = [("P1", ["ซ้ำ"]), ("P2", ["ซ้ำ"])]
        with self.assertRaises(DmsClientError) as caught:
            build_tree(tables)
        self.assertEqual(caught.exception.error_code, "ERR_DMS_SNAPSHOT_INCOMPLETE")

    def test_missing_parent_table_or_empty_provinces_is_rejected(self):
        with self.assertRaises(DmsClientError):
            build_tree({"provinces": [("P1", ["หนึ่ง"])]})
        with self.assertRaises(DmsClientError):
            build_tree({**synthetic(), "provinces": []})

    def test_a_page_shorter_than_the_native_count_never_publishes(self):
        # 计数说 4、分页只给得出 3：宁可失败，绝不发布残缺代。
        client = FakeClient(page_size=2, truncate=3)
        limits = GeoListLimits(page_size=2)
        with self.assertRaises(DmsClientError) as caught:
            fetch_table(client, "subdistricts", limits, _usage(limits))
        self.assertEqual(caught.exception.error_code, "ERR_DMS_SNAPSHOT_INCOMPLETE")

    def test_repeated_identity_across_pages_is_rejected(self):
        limits = GeoListLimits(page_size=2)
        client = FakeClient(page_size=2, repeat_id=True)
        with self.assertRaises(DmsClientError):
            fetch_table(client, "subdistricts", limits, _usage(limits))

    def test_row_shape_change_is_rejected(self):
        limits = GeoListLimits()
        client = FakeClient(shape_break=("districts", "D1"))
        with self.assertRaises(DmsClientError):
            fetch_table(client, "districts", limits, _usage(limits))

    def test_transport_failure_is_reported_as_incomplete(self):
        limits = GeoListLimits()
        client = FakeClient(fail_on=("districts", 2))
        with self.assertRaises(DmsClientError) as caught:
            fetch_table(client, "districts", limits, _usage(limits))
        self.assertEqual(caught.exception.error_code, "ERR_DMS_SNAPSHOT_INCOMPLETE")

    def test_empty_table_reads_as_empty(self):
        client = FakeClient({**synthetic(), "zipcodes": []})
        self.assertEqual(fetch_table(client, "zipcodes", GeoListLimits(), _usage()), [])

    def test_native_count_beyond_the_row_budget_is_rejected(self):
        limits = GeoListLimits(max_rows=3)
        with self.assertRaises(DmsClientError):
            fetch_table(FakeClient(), "subdistricts", limits, _usage(limits))

    def test_page_size_outside_the_native_bound_is_rejected(self):
        for value in (0, 501):
            with self.subTest(page_size=value), self.assertRaises(ValueError):
                GeoListLimits(page_size=value)


def _usage(limits=None):
    return importlib.import_module("pearnly_dms.client.geo_list_reader")._Usage(
        limits or GeoListLimits())


class GeoAdapterTests(unittest.TestCase):
    """只替换框架/登录/存储边界；读取器与发布校验都是真的。"""

    def setUp(self):
        self.client = FakeClient()
        self.stage = "legacy-detail-checkpoint"
        self.scope = "synthetic-current-credentials"
        self.published = None
        self.manual = types.ModuleType("pearnly_dms.snapshots.manual")
        self.manual.assert_active = Mock(return_value={})
        self.manual.publish_guard = Mock(side_effect=lambda *a, **kw: nullcontext())
        self.store = types.ModuleType("pearnly_dms.snapshots.store")
        self.store.scope = Mock(side_effect=lambda endpoint: self.scope)
        self.store.checkpoint = Mock(side_effect=self.checkpoint)
        self.store.publish = Mock(side_effect=self.publish)
        self.store.failed = Mock()
        self.frappe = types.ModuleType("frappe")
        self.frappe.db = types.SimpleNamespace(commit=Mock())
        self.operations = types.ModuleType("pearnly_dms.adapter.operations")
        self.operations.run_logged_in = Mock(side_effect=self.capture)
        with patch.dict(sys.modules, {"frappe": self.frappe,
                "pearnly_dms.adapter.operations": self.operations,
                "pearnly_dms.snapshots.manual": self.manual,
                "pearnly_dms.snapshots.store": self.store}):
            spec = importlib.util.spec_from_file_location(
                "geo_list_adapter_under_test",
                ROOT / "apps/pearnly_dms/pearnly_dms/adapter/geo_sync.py")
            self.adapter = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.adapter)

    def capture(self, endpoint, callback, **kwargs):
        self.assertEqual(kwargs, {"authoritative_read": True, "background": True,
                                  "read_only_batch": True})
        try:
            return callback(self.client, None)
        except DmsClientError as exc:
            return {"ok": False, "error_code": exc.error_code}

    def checkpoint(self, endpoint, value, *, expected_scope):
        self.assertEqual(expected_scope, self.scope)
        self.stage = deepcopy(value)

    def publish(self, endpoint, dataset, value, stamp, *, expected_scope):
        if expected_scope != self.scope:
            raise DmsClientError("scope changed", "ERR_DMS_SNAPSHOT_SCOPE_CHANGED")
        self.assertEqual(dataset, "geo")
        self.assertTrue(complete_tree({key: value[key] for key in COLUMNS}))
        self.assertEqual(set(value["catalogues"]), set(COLUMNS))
        self.published = deepcopy(value)
        return True

    def test_retired_and_denied_token_never_read_or_publish(self):
        self.assertEqual(self.adapter.refresh("ep"), {"ok": False, "retired": True})
        self.operations.run_logged_in.assert_not_called()
        self.manual.assert_active.side_effect = PermissionError("inactive request")
        with self.assertRaises(PermissionError):
            self.adapter.refresh("ep", request_id="request")
        self.operations.run_logged_in.assert_not_called()
        self.store.publish.assert_not_called()

    def test_complete_read_publishes_once_and_clears_the_legacy_stage(self):
        result = self.adapter.refresh("ep", request_id="request")
        self.assertTrue(result["ok"])
        self.assertTrue(result["complete"])
        self.assertTrue(result["published"])
        self.assertEqual(result["count"], 13)
        self.assertIsNotNone(self.published, "a complete tree must be published")
        self.assertIsNone(self.stage, "the retired detail checkpoint must be cleared")
        self.store.failed.assert_not_called()

    def test_incomplete_read_never_publishes_and_keeps_the_old_generation(self):
        self.client.fail_on = ("subdistricts", 2)
        result = self.adapter.refresh("ep", request_id="request")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "ERR_DMS_SNAPSHOT_INCOMPLETE")
        self.assertIsNone(self.published, "a partial tree must never replace the old one")
        self.store.failed.assert_called_once()
        self.assertEqual(self.store.failed.call_args.args[2], "ERR_DMS_SNAPSHOT_INCOMPLETE")
        self.assertIsNone(self.stage, "a failed read leaves no resumable detail stage")

    def test_busy_account_is_not_reported_as_a_failed_generation(self):
        self.client.fail_on = ("provinces", 1)
        self.operations.run_logged_in.side_effect = lambda *a, **kw: {
            "ok": False, "error_code": "ERR_DMS_SNAPSHOT_BUSY"}
        result = self.adapter.refresh("ep", request_id="request")
        self.assertTrue(result["busy"])
        self.store.failed.assert_not_called()
        self.store.publish.assert_not_called()

    def test_login_failure_is_recorded_without_publishing(self):
        self.operations.run_logged_in.side_effect = lambda *a, **kw: {
            "ok": False, "error_code": "ERR_DMS_UNREACHABLE"}
        result = self.adapter.refresh("ep", request_id="request")
        self.assertFalse(result["ok"])
        self.store.failed.assert_called_once()
        self.store.publish.assert_not_called()
        self.assertIsNone(self.published)

    def test_a_newer_generation_blocks_publication_without_failing(self):
        self.store.publish.side_effect = lambda *a, **kw: False
        result = self.adapter.refresh("ep", request_id="request")
        self.assertTrue(result["ok"])
        self.assertFalse(result["published"])
        self.store.failed.assert_not_called()

    def test_rotated_credentials_cannot_publish_into_the_old_scope(self):
        self.store.scope.side_effect = lambda endpoint: "rotated-credentials"
        with self.assertRaises(DmsClientError) as caught:
            self.adapter.refresh("ep", request_id="request")
        self.assertEqual(caught.exception.error_code, "ERR_DMS_SNAPSHOT_SCOPE_CHANGED")
        self.assertIsNone(self.published)


if __name__ == "__main__":
    unittest.main()
