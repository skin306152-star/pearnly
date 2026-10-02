"""Menu-5 snapshot regressions in the pinned Frappe runtime; zero external IO.

Runs real parsers, collectors, refresh orchestration and interaction entrypoints.
Only DMS transport/login, local persistence and permission IO are replaced here;
the separate task-owned Site suite verifies the real snapshot DocType contract.
"""
import copy
from contextlib import nullcontext
import json
import unittest
from unittest.mock import Mock, patch

import frappe
from pearnly_dms.contracts.client_base import DmsClientError
from pearnly_dms.query import sales_readback as rb, snapshot_sync as sync
from pearnly_dms.snapshots import manual, store


HOME = ('<input id="idusers" value="employee-only">'
        '<select id="carmaxsellbranch"><option value="0">All</option>'
        '<option value="7">Branch</option><option value="7">Branch</option></select>'
        '<select id="carmaxsellteam"><option value="0">All</option><option value="3">Team</option></select>')
CELLS = ["", "active", "BK-LAB", "24/09/2569", "30/09/2569", "Lab employee", "Lab customer",
         "Lab model", "Lab color", "cash", "unallocated", "SO-LAB", "ENGINE-LAB"]


def markup(rid, **changes):
    fields = ["cancel_status", "record_status", "booking_no", "booking_date", "delivery_date", "advisor",
              "customer", "vehicle", "color", "finance_status", "allocation_status", "sales_doc_no", "engine_no"]
    cells = [changes.get(field, value) for field, value in zip(fields, CELLS)]
    return '<div data-val="' + str(rid) + '">' + ''.join('<p>' + value + '</p>' for value in cells) + '</div>'


class NativeClient:
    def __init__(self, *, count=3):
        self.calls = []
        self.initialized = False
        self.rows = {str(i): markup(i, customer="MATCH" if i in (0, 2) else "other",
                                     vehicle="MATCH", booking_no="BK-" + str(i)) for i in range(count)}
        self.top = [("model-" + str(i), "Model " + str(i), i) for i in range(31)]

    def _post_text(self, path, payload):
        self.calls.append((path, copy.deepcopy(payload)))
        if path == "drfcbc/view.php":
            assert payload == {"idmenu": "25", "menulv": "2"}
            self.initialized = True
            return "native initialized view"
        if path == rb.LIST_PATH:
            assert self.initialized, "Fresh login must initialize the native list"
            assert payload["selcolsorttype"] == "2"
            ids = [rid for rid, body in self.rows.items() if payload["sd"].casefold() in body.casefold()]
            # An intentionally non-Python/native ordering: the reader must
            # preserve the captured order rather than resort translated text.
            if payload["selcolsort"] == "5":
                ids.reverse()
            if payload["ftd"] != "1":
                ids = [rid for rid in ids if int(rid) % 2 == 0]
            if "sdtamt" not in payload:
                return "dt::" + str(len(ids))
            size, page = int(payload["sdtamt"]), int(payload["sdtpage"])
            selected = ids[(page - 1) * size:page * size]
            return ''.join(self.rows[rid] for rid in selected) or "ndt::empty"
        assert path == rb.TOP_SALES_PATH
        assert payload["idusers"] == "employee-only"
        assert payload["carmaxsellbranch"] == "7" and payload["carmaxsellteam"] == "3"
        if payload["status"] == "all":
            return str(len(self.top))
        size, page = int(payload["carmaxsellshow"]), int(payload["carmaxsellpage"])
        rows = self.top[(page - 1) * size:page * size]
        return json.dumps([[row[1] for row in rows], [row[0] for row in rows], [row[2] for row in rows]])


def adapter():
    value = Mock(base_url="https://dms.invalid/")
    value.transport.return_value.get.return_value.text = HOME
    value.transport.return_value.get.return_value.status_code = 200
    return value


def envelope(data, **metadata):
    return {"data": data, "as_of": 123.0, "completed_at": 124.0,
            "generation": "lab-generation", "stale": False, **metadata}


class Collectors(unittest.TestCase):
    def setUp(self):
        self.client = NativeClient()
        self.scope = sync.dashboard_scope(adapter())

    def test_native_constants_and_parser_fail_closed(self):
        self.assertEqual(list(rb.SEARCH_COLUMNS.values()), [str(i) for i in range(1, 12)])
        self.assertEqual(list(rb.STATUS_FILTERS.values()), [str(i) for i in range(1, 16)])
        row = rb.parse_sales_rows(markup(1, engine_no="-", customer=" ก&amp;ข  ค "))[0]
        self.assertEqual(row["customer"], "ก&ข ค")
        self.assertEqual(row["engine_no"], "")
        self.assertEqual(row["booking_date"], "24/09/2569")
        for body in (markup(1) + '<div data-val="2"><p>partial</p></div>', markup(1, booking_no="")):
            with self.assertRaises(DmsClientError):
                rb.parse_sales_rows(body)

    def test_only_current_order_status_query_is_captured(self):
        params = rb.query_parameters(field="customer", query="match", status="all")
        result = sync.collect_sales(self.client, params)
        self.assertEqual([row["id"] for row in result["rows"]], ["2", "0"])
        self.assertEqual(result["params"], params)
        calls = [payload for path, payload in self.client.calls if path == rb.LIST_PATH]
        self.assertEqual(len(calls), 3)  # One count/page/count, not 75 prefetch calls.
        self.assertEqual({(p["selcolsort"], p["ftd"], p["sd"]) for p in calls}, {("5", "1", "match")})

    def test_complete_current_query_pagination_not_first_page_only(self):
        self.client = NativeClient(count=201)
        result = sync.collect_sales(self.client, rb.query_parameters(status="all"))
        self.assertEqual(len(result["rows"]), 201)
        self.assertEqual(result["rows"][-1]["id"], "200")
        self.assertEqual([p["sdtpage"] for _, p in self.client.calls if "sdtpage" in p], ["1", "2"])

    def test_repeated_or_over_bound_pages_are_rejected(self):
        rows = rb.parse_sales_rows(markup(1) + markup(2))
        with patch.object(rb, "_sales_page", return_value=rows), patch.object(sync, "PAGE_SIZE", 2):
            with self.assertRaises(DmsClientError):
                sync._all_sales(Mock(_post_text=Mock(return_value="dt::4")), "1", "1")
        with patch.object(sync, "PAGE_SIZE", 2), patch.object(sync, "MAX_PAGES", 2):
            with self.assertRaises(DmsClientError):
                sync._all_sales(Mock(_post_text=Mock(return_value="dt::5")), "1", "1")

    def test_invalid_empty_and_duplicate_rows_do_not_mean_no_records(self):
        for body in ("", "<form>login</form>", markup(1) + markup(1)):
            self.client._post_text = Mock(return_value=body)
            with self.assertRaises(DmsClientError):
                sync._all_sales(self.client, "1", "1")

    def test_sales_count_truncation_and_mid_scan_count_change_rejected(self):
        rows = rb.parse_sales_rows(markup(1))
        for counts, batch in ((["dt::2"], rows), (["dt::1", "dt::2"], rows)):
            with patch.object(rb, "_sales_page", return_value=batch), self.assertRaises(DmsClientError):
                sync._all_sales(Mock(_post_text=Mock(side_effect=counts)), "1", "1")

    def test_verified_zero_sales_count_needs_no_ambiguous_empty_page(self):
        client = Mock(_post_text=Mock(return_value="dt::0"))
        with patch.object(rb, "_sales_page", side_effect=AssertionError("No rows advertised")):
            self.assertEqual(sync._all_sales(client, "1", "1"), [])
        self.assertEqual(client._post_text.call_count, 2)

    def test_top_complete_pages_count_contract_and_native_parameters(self):
        params = rb.top_parameters("type", "amount", "1/9/2569", "24/9/2569")
        result = sync.collect_top(self.client, self.scope, params)
        self.assertEqual(len(result["rows"]), 31)
        self.assertEqual(result["rows"][-1], {"id": "model-30", "label": "Model 30", "value": 30})
        self.assertEqual(result["branches"], ["Branch", "Branch"])
        payloads = [p for _, p in self.client.calls]
        self.assertEqual([p["status"] for p in payloads], ["all", "page", "page", "all"])
        self.assertEqual([p["carmaxsellpage"] for p in payloads if p["status"] == "page"], ["1", "2"])
        self.assertTrue(all(p["carmaxselltypedata"] == "2" and p["carmaxsellqnt"] == "2" for p in payloads))
        self.assertTrue(all(p["txtcarmaxselldate_bg"] == "01/09/2569" for p in payloads))

    def test_top_zero_is_a_verified_empty_result(self):
        self.client.top = []
        self.assertEqual(sync.collect_top(self.client, self.scope, rb.top_parameters())["rows"], [])
        self.assertEqual(len(self.client.calls), 3)

    def test_top_incomplete_duplicate_changed_count_or_malformed_rejected(self):
        cases = [
            ["2", '[["A", "B"],["1"],[1,2]]'],
            ["2", '[["A", "B"],["1","1"],[1,2]]'],
            ["2", '[["A"],["1"],[1]]'],
            ["1", '[["A"],["1"],[1]]', "2"],
            [""], ["-1"], ["4001"], ["1", "bad json"],
            ["1", '[["A"],["1"],[NaN]]'], ["1", '[["A"],["1"],[{}]]'],
        ]
        for outputs in cases:
            with self.subTest(outputs=outputs), self.assertRaises(DmsClientError):
                sync.collect_top(Mock(_post_text=Mock(side_effect=outputs)), self.scope, rb.top_parameters())

    def test_native_scope_is_employee_only_and_missing_scope_denied(self):
        no_scope = adapter()
        no_scope.transport.return_value.get.return_value.text = '<input id="idusers" value="employee-only">'
        scope = sync.dashboard_scope(no_scope)
        self.assertFalse(scope["top_sales"])
        with self.assertRaises(DmsClientError) as failure:
            sync.collect_top(self.client, scope, rb.top_parameters())
        self.assertEqual(failure.exception.error_code, "ERR_DMS_QUERY_SCOPE")
        self.assertEqual(self.client.calls, [])
        no_scope.transport.return_value.get.return_value.text = ""
        with self.assertRaises(DmsClientError):
            sync.dashboard_scope(no_scope)

    def test_scan_time_request_and_byte_budgets_fail_closed(self):
        with patch.object(sync.time, "monotonic", return_value=0):
            budget = sync.ScanBudget(0)
        client = sync._BoundedClient(Mock(_post_text=Mock(return_value="safe")), budget)
        with patch.object(sync.time, "monotonic", return_value=181), self.assertRaises(DmsClientError):
            client._post_text("safe", {})
        client.client._post_text.assert_not_called()
        with patch.object(sync.time, "monotonic", return_value=0), patch.object(sync, "MAX_READS", 1):
            client._post_text("safe", {})
            with self.assertRaises(DmsClientError):
                client._post_text("safe", {})
        with patch.object(sync.time, "monotonic", return_value=0), patch.object(sync, "MAX_RESPONSE_BYTES", 5):
            with self.assertRaises(DmsClientError):
                client._post_text("safe", {})
        self.assertEqual(client.client._post_text.call_count, 2)

    def test_budget_checks_actual_home_transport_before_and_after(self):
        with patch.object(sync.time, "monotonic", return_value=0):
            budget = sync.ScanBudget(0)
            value = adapter()
            with patch.object(sync, "MAX_RESPONSE_BYTES", 1), self.assertRaises(DmsClientError):
                sync.dashboard_scope(sync._BoundedAdapter(value, budget))
            value.transport.return_value.get.assert_called_once()


class LocalReads(unittest.TestCase):
    def setUp(self):
        self.params = rb.query_parameters(field="customer", query="match")
        self.data = sync.collect_sales(NativeClient(), self.params)
        self.scope = sync.dashboard_scope(adapter())
        self.caps = sync._capture(NativeClient(), adapter(), {"kind": "capabilities"})
        self.permission = self.enterContext(patch.object(rb, "can_query", return_value=True))
        self.endpoint = self.enterContext(patch.object(rb, "_enabled_endpoint", return_value="EP-own"))
        self.read = self.enterContext(patch.object(store, "read", return_value=envelope(self.data)))
        self.request = self.enterContext(patch.object(store, "request", side_effect=AssertionError("No registration")))
        self.login = self.enterContext(patch.object(sync, "run_logged_in", side_effect=AssertionError("No interactive login")))
        self.enqueue = self.enterContext(patch.object(frappe, "enqueue", side_effect=AssertionError("No interactive enqueue")))

    def test_local_pages_preserve_native_order_and_original_time(self):
        for page, ids in ((1, ["2"]), (2, ["0"])):
            result = rb.fetch_sales_records("own", field="customer", query="match", limit=1, page=page)
            self.assertEqual([r["id"] for r in result["rows"]], ids)
            self.assertEqual(result["has_more"], page == 1)
            self.assertEqual(result["as_of"], 123.0)
        self.read.assert_called_with("EP-own", rb.query_dataset(self.params), reader="sales")

    def test_only_conditions_define_cache_identity(self):
        key = rb.query_dataset(self.params)
        self.assertEqual(key, rb.query_dataset({**self.params, "page": 2, "limit": 1, "_binding": {"revision": "other"}}))
        for change in ({"status": "cash"}, {"query": "other"}, {"field": "vehicle"}):
            self.assertNotEqual(key, rb.query_dataset({**self.params, **change}))

    def test_permission_and_endpoint_are_rechecked(self):
        for getter in (rb.fetch_sales_records, rb.query_capabilities, rb.fetch_top_sales):
            self.permission.return_value = False
            self.read.reset_mock()
            self.assertEqual(getter("own")["error_code"], "ERR_DMS_QUERY_FORBIDDEN")
            self.read.assert_not_called()
            self.permission.return_value = True
            self.endpoint.return_value = None
            self.assertEqual(getter("own")["error_code"], "ERR_NO_CREDS")
            self.endpoint.return_value = "EP-own"

    def test_missing_expired_denied_wrong_conditions_never_mean_zero(self):
        for code in ("ERR_DMS_SNAPSHOT_PENDING", "ERR_DMS_SNAPSHOT_STALE", "ERR_NO_CREDS"):
            self.read.side_effect = store.SnapshotUnavailable(code)
            self.assertEqual(rb.fetch_sales_records("own")["error_code"], code)
        self.read.side_effect = None
        self.assertEqual(rb.fetch_sales_records("own")["error_code"], "ERR_DMS_SNAPSHOT_PENDING")
        self.read.return_value = envelope({"ok": False, "error_code": "ERR_DMS_AUTH"})
        self.assertEqual(rb.fetch_sales_records("own")["error_code"], "ERR_DMS_AUTH")

    def test_capability_is_local_and_scope_loss_blocks_old_top(self):
        self.read.return_value = envelope(self.caps)
        self.assertTrue(rb.query_capabilities("own")["top_sales"])
        self.caps["capabilities"]["top_sales"] = False
        top = sync.collect_top(NativeClient(), self.scope, rb.top_parameters())
        self.read.side_effect = [envelope(top), envelope(self.caps, as_of=124)]
        self.assertEqual(rb.fetch_top_sales("own")["error_code"], "ERR_DMS_QUERY_SCOPE")

    def test_custom_top_never_registers_or_logs_in(self):
        params = rb.top_parameters("subtype", "amount", "01/01/2569", "24/09/2569")
        top = sync.collect_top(NativeClient(), self.scope, params)
        self.read.side_effect = [envelope(top), envelope(self.caps)]
        result = rb.fetch_top_sales("own", **params, limit=30, page=2)
        self.assertEqual(result["rows"], [{"label": "Model 30", "value": 30}])
        self.assertEqual(result["total"], 31)
        self.request.assert_not_called()
        self.login.assert_not_called()
        self.enqueue.assert_not_called()

    def test_new_period_or_changed_dashboard_scope_cannot_reuse_old_top(self):
        params = rb.top_parameters()
        top = sync.collect_top(NativeClient(), self.scope, params)
        for bad in ({**top, "dashboard_scope": "old"}, {**top, "params": {**top["params"], "metric": "amount"}}):
            self.read.side_effect = [envelope(bad), envelope(self.caps)]
            self.assertEqual(rb.fetch_top_sales("own")["error_code"], "ERR_DMS_SNAPSHOT_PENDING")
        self.read.side_effect = store.SnapshotUnavailable()
        self.assertEqual(rb.fetch_top_sales("own", date_from="01/01/2569")["error_code"], "ERR_DMS_SNAPSHOT_PENDING")
        self.request.assert_not_called()

    def test_new_top_uses_its_own_scope_but_newer_denial_blocks_old_top(self):
        top = sync.collect_top(NativeClient(), self.scope, rb.top_parameters())
        self.caps["dashboard_scope"] = "older-scope"
        self.caps["capabilities"]["top_sales"] = False
        self.read.side_effect = [envelope(top, as_of=200), envelope(self.caps, as_of=100)]
        self.assertTrue(rb.fetch_top_sales("own")["ok"])
        self.read.side_effect = [envelope(top, as_of=100), envelope(self.caps, as_of=200)]
        self.assertEqual(rb.fetch_top_sales("own")["error_code"], "ERR_DMS_QUERY_SCOPE")
        self.read.side_effect = [envelope(top), store.SnapshotUnavailable()]
        self.assertTrue(rb.fetch_top_sales("own")["ok"])

    def test_period_normalization_and_invalid_dates(self):
        self.assertEqual(rb.top_parameters(date_from="1/9/2569", date_to="24/9/2569")["date_from"], "01/09/2569")
        for start, end in (("31/02/2569", "01/03/2569"), ("02/03/2569", "01/03/2569"), ("text", "01/03/2569")):
            with self.assertRaises(DmsClientError):
                rb.top_parameters(date_from=start, date_to=end)


class Refresh(unittest.TestCase):
    def setUp(self):
        self.client, self.adapter = NativeClient(), adapter()
        self.params = {**rb.query_parameters(field="customer", query="match"),
                       "_binding": {"user_id": "employee", "line_user_id": "U-lab", "binding_revision": "current"}}
        self.record = {"actor": "employee", "params": self.params, "expected_scope": "start-scope"}
        self.db = self.enterContext(patch.object(frappe, "db", Mock()))
        self.enterContext(patch.object(rb, "_query_endpoint", return_value="EP-own"))
        self.guard = self.enterContext(patch.object(manual, "assert_active", side_effect=lambda *a, **k: self.record))
        self.publish_guard = self.enterContext(patch.object(manual, "publish_guard", side_effect=lambda *a, **k: nullcontext(self.record)))
        self.binding = self.enterContext(patch.object(sync, "current_binding", return_value=True))
        self.publish = self.enterContext(patch.object(store, "publish", return_value=True))
        self.failed = self.enterContext(patch.object(store, "failed"))
        self.login = self.enterContext(patch.object(sync, "run_logged_in", side_effect=self.run_login))

    def run_login(self, endpoint, fn, **kwargs):
        self.assertEqual(endpoint, "EP-own")
        # 操作员自己按出来的那一次查询：做完顺手把使用记录里的增量补回缓存。
        self.assertEqual(kwargs, {"authoritative_read": False, "background": True,
                                  "read_only_batch": True, "catch_up": True})
        try:
            return fn(self.client, self.adapter)
        except DmsClientError as exc:
            return {"ok": False, "error_code": exc.error_code}

    def run_refresh(self):
        return sync.refresh("EP-own", request_id="token", dataset=rb.query_dataset(self.params), params=self.params)

    def test_no_token_retired_without_db_login_or_publish(self):
        self.assertTrue(sync.refresh("EP-own")["retired"])
        self.guard.assert_not_called()
        self.login.assert_not_called()
        self.publish.assert_not_called()

    def test_one_query_one_login_and_one_scoped_publication(self):
        self.assertEqual(self.run_refresh(), {"ok": True, "complete": True, "count": 2})
        self.login.assert_called_once()
        self.publish.assert_called_once()
        self.assertEqual(self.publish.call_args.kwargs, {"reader": "sales", "expected_scope": "start-scope"})
        self.assertEqual(self.guard.call_count, 2)
        self.publish_guard.assert_called_once_with("EP-own", rb.query_dataset(self.params), "token", reader="sales")
        self.assertEqual(self.binding.call_count, 2)
        self.adapter.transport.return_value.get.assert_not_called()
        self.assertFalse(any(path == rb.TOP_SALES_PATH for path, _ in self.client.calls))

    def test_capability_only_home_and_top_only_selected_condition(self):
        for condition in ({"kind": "capabilities"}, rb.query_parameters("top", group="type", metric="amount",
                          date_from="01/01/2569", date_to="24/09/2569")):
            self.params = {**condition, "_binding": self.params["_binding"]}
            self.record["params"] = self.params
            self.client.calls.clear()
            self.publish.reset_mock()
            self.assertTrue(self.run_refresh()["ok"])
            self.publish.assert_called_once()
            self.assertTrue(all(path == rb.TOP_SALES_PATH for path, _ in self.client.calls))
            if condition["kind"] == "capabilities":
                self.assertEqual(self.client.calls, [])

    def test_invalid_token_actor_binding_revision_or_dataset_prevents_io(self):
        for mutation in ("token", "actor", "binding", "revision", "dataset"):
            with self.subTest(mutation=mutation):
                self.login.reset_mock()
                self.guard.side_effect = lambda *a, **k: self.record
                self.record["actor"] = "employee"
                self.params["_binding"]["binding_revision"] = "current"
                self.binding.return_value = True
                if mutation == "token":
                    self.guard.side_effect = store.SnapshotUnavailable("ERR_DMS_SNAPSHOT_SCOPE_CHANGED")
                if mutation == "actor": self.record["actor"] = "another"
                if mutation == "binding": self.binding.return_value = False
                if mutation == "revision": self.params["_binding"]["binding_revision"] = ""
                dataset = "query.invalid" if mutation == "dataset" else rb.query_dataset(self.params)
                self.assertFalse(sync.refresh("EP-own", request_id="token", dataset=dataset, params=self.params)["ok"])
                self.login.assert_not_called()
        self.publish.assert_not_called()

    def test_changed_binding_or_request_after_io_never_publishes(self):
        self.binding.side_effect = [True, False]
        self.assertEqual(self.run_refresh()["error_code"], "ERR_DMS_QUERY_FORBIDDEN")
        self.publish.assert_not_called()
        self.binding.side_effect = None
        self.guard.side_effect = [self.record, store.SnapshotUnavailable("ERR_DMS_SNAPSHOT_SCOPE_CHANGED")]
        self.assertFalse(self.run_refresh()["ok"])
        self.publish.assert_not_called()

    def test_superseded_token_at_publication_lock_cannot_overwrite_cache(self):
        self.publish_guard.side_effect = store.SnapshotUnavailable("ERR_DMS_SNAPSHOT_SCOPE_CHANGED")
        self.assertEqual(self.run_refresh()["error_code"], "ERR_DMS_SNAPSHOT_SCOPE_CHANGED")
        self.login.assert_called_once()
        self.publish.assert_not_called()
        self.failed.assert_not_called()
        self.db.rollback.assert_called_once()

    def test_busy_keeps_old_data_without_marking_failed(self):
        self.login.side_effect = None
        self.login.return_value = {"ok": False, "error_code": "ERR_DMS_SNAPSHOT_BUSY"}
        self.assertEqual(self.run_refresh(), {"ok": False, "busy": True})
        self.publish.assert_not_called()
        self.failed.assert_not_called()

    def test_partial_failure_keeps_old_and_scope_change_rolls_back(self):
        self.login.side_effect = None
        self.login.return_value = {"ok": False, "error_code": "ERR_DMS_UNREACHABLE"}
        self.assertFalse(self.run_refresh()["ok"])
        self.publish.assert_not_called()
        self.failed.assert_called_once()
        self.login.side_effect = self.run_login
        self.publish.side_effect = store.SnapshotUnavailable("ERR_DMS_SNAPSHOT_SCOPE_CHANGED")
        self.assertEqual(self.run_refresh()["error_code"], "ERR_DMS_SNAPSHOT_SCOPE_CHANGED")
        self.db.rollback.assert_called_once()


class QueryInteraction(unittest.TestCase):
    def setUp(self):
        from pearnly_dms.query import query_flow
        self.flow = query_flow
        self.session = None
        self.enterContext(patch.object(frappe, "db", Mock()))
        self.enterContext(patch.object(frappe, "enqueue", side_effect=AssertionError("Local read must not queue")))
        self.permission = self.enterContext(patch.object(query_flow, "_can_query", return_value=True))
        self.binding = self.enterContext(patch.object(query_flow, "current_binding", return_value=True))
        self.enterContext(patch.object(query_flow.store, "set_session", side_effect=self.set_session))
        self.enterContext(patch.object(query_flow.store, "get_session", side_effect=lambda _: self.session))
        self.send = self.enterContext(patch.object(query_flow, "_send"))
        self.push = self.enterContext(patch.object(query_flow, "_push"))
        self.loading = self.enterContext(patch.object(query_flow, "start_loading"))
        self.records = self.enterContext(patch.object(rb, "fetch_sales_records", return_value={"ok": True, "rows": []}))
        self.top = self.enterContext(patch.object(rb, "fetch_top_sales", return_value={"ok": True, "rows": []}))
        self.capabilities = self.enterContext(patch.object(rb, "query_capabilities", return_value={"ok": True, "top_sales": True}))
        self.enterContext(patch.object(rb, "_query_endpoint", return_value="EP-own"))
        self.request = self.enterContext(patch.object(manual, "request", return_value={"status": "queued"}))
        self.status = self.enterContext(patch.object(manual, "status", return_value={"status": "idle"}))
        self.identity = {"user_id": "employee", "binding_revision": "current"}

    def set_session(self, _, state, payload):
        self.session = {"state": state, "payload": payload}

    def test_capabilities_records_top_are_direct_local_calls_with_reply_token(self):
        self.flow._pick_type(self.identity, "U-lab", "reply", "sales")
        self.capabilities.assert_called_once_with("employee")
        self.send.assert_called_once()
        self.assertEqual(self.send.call_args.args[-1], "reply")
        self.send.reset_mock()
        self.flow._begin_records(self.identity, "U-lab", field="customer", query="match", reply_token="reply")
        self.records.assert_called_once()
        self.send.assert_called_once()
        self.assertEqual(self.send.call_args.args[-1], "reply")
        self.send.reset_mock()
        self.flow._begin_top(self.identity, "U-lab", group="model", metric="quantity", date_from="01/09/2569", date_to="24/09/2569", limit=10, reply_token="reply")
        self.top.assert_called_once()
        self.send.assert_called_once()
        self.assertEqual(self.send.call_args.args[-1], "reply")

    def test_superseded_session_never_receives_old_query_results(self):
        def supersede(*args, **kwargs):
            self.session = {"state": "booking_qa", "payload": {}}
            return {"ok": True, "rows": []}
        self.records.side_effect = supersede
        self.flow._begin_records(self.identity, "U-lab", reply_token="reply")
        self.send.assert_not_called()
        self.push.assert_not_called()
        self.top.side_effect = supersede
        self.flow._begin_top(self.identity, "U-lab", group="model", metric="quantity", date_from="01/09/2569", date_to="24/09/2569", limit=10, reply_token="reply")
        self.send.assert_not_called()
        self.push.assert_not_called()

    def test_cold_read_enqueues_by_itself_and_never_shows_zero(self):
        """冷缓存时**服务端自己**把这次读取登记出去：菜单 5 只有一个功能按钮
        （按类查记录），操作员不需要再点"读取"（用户 2026-09-25）。

        契约变了，但严格性不变：冷数据绝不能显示成"零记录"；登记的这次读取
        必须带当前绑定与本人 actor；状态提示里**不能再出现任何按钮**。
        """
        from pearnly_dms.query import query_cards
        self.records.return_value = {"ok": False, "error_code": "ERR_DMS_SNAPSHOT_PENDING"}
        self.flow._begin_records(self.identity, "U-lab", reply_token="reply")
        # 等待用 LINE 原生的加载动画（用户 2026-10-02），不发「正在读」文字——冷数据更不会显示成「零记录」
        self.loading.assert_called_once_with("U-lab", 60)
        self.send.assert_not_called()
        self.push.assert_not_called()
        self.assertTrue(query_cards._t("EMPTY_RECORDS_PREFIX"))
        self.request.assert_called_once()
        args, kwargs = self.request.call_args
        self.assertEqual(args[:2], ("EP-own", rb.query_dataset(rb.query_parameters())))
        self.assertEqual(kwargs["reader"], "sales")
        self.assertEqual(kwargs["actor"], "employee")
        self.assertEqual(kwargs["params"]["_binding"]["line_user_id"], "U-lab")
        # 绑定版本必须原样带上：后台任务靠它判断"这张卡还是不是当前绑定"。
        self.assertEqual(kwargs["params"]["_binding"]["binding_revision"],
                         self.identity["binding_revision"])

    def test_invalid_custom_date_reports_failure_without_session_or_read(self):
        self.flow._begin_top(self.identity, "U-lab", group="model", metric="quantity",
                            date_from="31/02/2569", date_to="01/03/2569", limit=10, reply_token="reply")
        self.assertIsNone(self.session)
        self.top.assert_not_called()
        self.request.assert_not_called()
        self.push.assert_called_once()

    def test_explicit_refresh_enqueues_current_condition_actor_and_binding_only(self):
        from pearnly_dms.query import query_cards
        self.flow._begin_records(self.identity, "U-lab", field="customer", query="match", reply_token="reply")
        params = rb.query_parameters(field="customer", query="match")
        key = rb.query_dataset(params)
        self.flow._manual_action(self.identity, "U-lab", "reply", query_cards.ACT_QUERY_REFRESH, {"key": key})
        self.request.assert_called_once_with("EP-own", key, reader="sales", actor="employee", params={**params,
            "_binding": {**self.identity, "line_user_id": "U-lab"}})
        self.assertEqual(self.send.call_args.args[-1], "reply")

    def test_stale_button_wrong_binding_or_missing_revision_never_enqueue(self):
        from pearnly_dms.query import query_cards
        self.flow._begin_records(self.identity, "U-lab", reply_token="reply")
        key = rb.query_dataset(rb.query_parameters())
        self.flow._manual_action(self.identity, "U-lab", "reply", query_cards.ACT_QUERY_REFRESH, {"key": "query.old"})
        with patch.object(self.flow, "_reply"):
            self.binding.return_value = False
            self.flow._manual_action(self.identity, "U-lab", "reply", query_cards.ACT_QUERY_REFRESH, {"key": key})
            self.binding.return_value = True
            self.flow._manual_action({"user_id": "employee"}, "U-lab", "reply", query_cards.ACT_QUERY_REFRESH, {"key": key})
        self.request.assert_not_called()

    def test_the_status_card_has_no_buttons_left_to_press(self):
        """没有"查看结果／状态"这条路了：状态只当一句话说，按钮一个都不给。"""
        from pearnly_dms.query import query_cards
        self.assertFalse(hasattr(query_cards, "ACT_QUERY_RESULT"))
        for state in ("idle", "queued", "running", "waiting", "failed"):
            message = query_cards.manual_status_message({"state": state, "as_of": 123})
            self.assertEqual(message["type"], "text", state)
            self.assertNotIn("quickReply", message, state)

    def test_expired_completed_result_does_not_promise_available_result(self):
        self.records.return_value = {"ok": False, "error_code": "ERR_DMS_SNAPSHOT_STALE"}
        self.status.return_value = {"state": "succeeded", "as_of": 123, "stale": True}
        self.flow._begin_records(self.identity, "U-lab", reply_token="reply")
        # 读已经过期 → **自动重读一次**（菜单 5 没有"读取"按钮）；等待只放加载动画，不承诺"点一下就能看到结果"。
        self.loading.assert_called_once_with("U-lab", 60)
        self.send.assert_not_called()
        self.request.assert_called_once()

    def test_local_pagination_does_not_refresh_or_reuse_different_conditions(self):
        from pearnly_dms.query import query_cards
        self.flow._begin_records(self.identity, "U-lab", reply_token="reply")
        self.flow._paginate(self.identity, "U-lab", "reply", self.session, {"kind": "records", "direction": "next"})
        self.assertEqual(self.records.call_args.kwargs["page"], 2)
        self.records.reset_mock()
        with patch.object(self.flow, "_reply"):
            self.flow._paginate(self.identity, "U-lab", "reply", self.session,
                                {"kind": "records", "direction": "next", "key": "query.old"})
        self.records.assert_not_called()
        self.request.assert_not_called()

    def test_snapshot_cards_show_source_time_not_response_time(self):
        from pearnly_dms.query import query_cards
        from datetime import datetime
        from zoneinfo import ZoneInfo
        timestamp = datetime(2026, 9, 24, 11, 15, tzinfo=ZoneInfo("Asia/Bangkok")).timestamp()
        for result, render in (({"rows": [], "as_of": timestamp}, query_cards.sales_board),
                               ({"rows": [{"booking_no": "BK-LAB"}], "as_of": timestamp}, query_cards.sales_board),
                               ({"rows": [], "as_of": timestamp}, query_cards.top_sales_board)):
            with patch.object(query_cards, "_now_label", side_effect=AssertionError("Response time is not source time")):
                self.assertIn("24/09/2026 11:15", json.dumps(render(result)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
