"""Menu-5 snapshot regressions in the pinned Frappe runtime; zero external IO.

Runs real parsers, collectors, refresh orchestration and interaction entrypoints.
Only DMS transport/login, local persistence and permission IO are replaced here;
the separate task-owned Site suite verifies the real snapshot DocType contract.
"""
import copy
import json
import unittest
from unittest.mock import Mock, patch

import frappe
from pearnly_dms.contracts.client_base import DmsClientError
from pearnly_dms.query import sales_readback as rb, snapshot_sync as sync
from pearnly_dms.snapshots import store


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
            assert payload["sd"] == "", "The snapshot must not inherit a user's search"
            assert payload["selcolsorttype"] == "2"
            ids = list(self.rows)
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

    def test_complete_native_orders_and_all_status_memberships(self):
        result = sync.collect_sales(self.client, self.scope)
        self.assertEqual(result["orders"]["customer"], ["2", "1", "0"])
        self.assertEqual(result["orders"]["booking_no"], ["0", "1", "2"])
        self.assertEqual(set(result["statuses"]), set(rb.STATUS_FILTERS))
        self.assertEqual(result["statuses"]["active"], ["0", "2"])
        calls = [payload for path, payload in self.client.calls if path == rb.LIST_PATH]
        self.assertEqual(len(calls), 75)
        self.assertEqual({p["selcolsort"] for p in calls if p["ftd"] == "1"}, set(rb.SEARCH_COLUMNS.values()))
        self.assertEqual({p["ftd"] for p in calls}, set(rb.STATUS_FILTERS.values()))
        self.assertTrue(result["capabilities"]["top_sales"])

    def test_complete_sales_pagination_not_first_page_only(self):
        self.client = NativeClient(count=201)
        result = sync.collect_sales(self.client, self.scope)
        self.assertEqual(len(result["rows"]), 201)
        self.assertEqual(result["orders"]["booking_no"][-1], "200")
        pages = [p["sdtpage"] for path, p in self.client.calls if path == rb.LIST_PATH and p["ftd"] == "1" and p["selcolsort"] == "1" and "sdtpage" in p]
        self.assertEqual(pages, ["1", "2"])

    def test_repeated_page_bound_and_cross_order_mutation_are_rejected(self):
        rows = rb.parse_sales_rows(markup(1) + markup(2))
        with patch.object(rb, "_sales_page", return_value=rows), patch.object(sync, "PAGE_SIZE", 2):
            with self.assertRaises(DmsClientError):
                sync._all_sales(Mock(_post_text=Mock(return_value="dt::4")), "1", "1")
        with patch.object(rb, "_sales_page", side_effect=[rows, [{**rows[0], "id": "3"}, {**rows[1], "id": "4"}]]), \
                patch.object(sync, "PAGE_SIZE", 2), patch.object(sync, "MAX_PAGES", 2):
            with self.assertRaises(DmsClientError):
                sync._all_sales(Mock(_post_text=Mock(return_value="dt::5")), "1", "1")
        with patch.object(sync, "_all_sales", side_effect=[rows, [{**rows[0], "customer": "changed"}, rows[1]]]):
            with self.assertRaises(DmsClientError):
                sync.collect_sales(self.client, self.scope)

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

    def test_status_row_change_rejects_whole_sales_snapshot(self):
        rows = rb.parse_sales_rows(markup(1))
        with patch.object(sync, "_all_sales", side_effect=[rows] * 11 + [[{**rows[0], "finance_status": "changed"}]]):
            with self.assertRaises(DmsClientError):
                sync.collect_sales(self.client, self.scope)

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
        self.scope = sync.dashboard_scope(adapter())
        self.client = NativeClient()
        self.data = sync.collect_sales(self.client, self.scope)
        self.permission = self.enterContext(patch.object(rb, "can_query", return_value=True))
        self.endpoint = self.enterContext(patch.object(rb, "_enabled_endpoint", return_value="EP-own"))
        self.read = self.enterContext(patch.object(store, "read", return_value=envelope(self.data)))
        self.request = self.enterContext(patch.object(store, "request"))
        self.login = self.enterContext(patch.object(sync, "run_logged_in", side_effect=AssertionError("Interactive login forbidden")))
        self.enqueue = self.enterContext(patch.object(frappe, "enqueue", side_effect=AssertionError("Interactive enqueue forbidden")))

    def test_local_filter_before_pagination_and_preserved_native_order(self):
        result = rb.fetch_sales_records("own", field="customer", query="match", limit=1, page=1)
        self.assertEqual([r["id"] for r in result["rows"]], ["2"])
        self.assertTrue(result["has_more"])
        result = rb.fetch_sales_records("own", field="customer", query="match", limit=1, page=2)
        self.assertEqual([r["id"] for r in result["rows"]], ["0"])
        self.assertFalse(result["has_more"])
        self.assertEqual(result["source"], "mrerp_dms_snapshot")
        self.assertEqual(result["as_of"], 123.0)
        self.read.assert_called_with("EP-own", rb.SALES_DATASET, reader="sales")
        self.login.assert_not_called()
        self.enqueue.assert_not_called()

    def test_permissions_and_endpoint_rechecked_even_if_snapshot_exists(self):
        for getter in (rb.fetch_sales_records, rb.query_capabilities, rb.fetch_top_sales):
            with self.subTest(getter=getter.__name__):
                self.permission.return_value = False
                self.read.reset_mock()
                self.assertEqual(getter("own")["error_code"], "ERR_DMS_QUERY_FORBIDDEN")
                self.read.assert_not_called()
                self.permission.return_value = True
                self.endpoint.return_value = None
                self.assertEqual(getter("own")["error_code"], "ERR_NO_CREDS")
                self.read.assert_not_called()
                self.endpoint.return_value = "EP-own"

    def test_missing_stale_or_denial_never_becomes_empty_success(self):
        for code in ("ERR_DMS_SNAPSHOT_PENDING", "ERR_DMS_SNAPSHOT_STALE", "ERR_NO_CREDS"):
            self.read.side_effect = store.SnapshotUnavailable(code)
            self.assertEqual(rb.fetch_sales_records("own")["error_code"], code)
        self.read.side_effect = None
        self.read.return_value = envelope({"ok": False, "error_code": "ERR_DMS_AUTH"})
        self.assertEqual(rb.fetch_sales_records("own")["error_code"], "ERR_DMS_AUTH")

    def test_capability_is_local_and_scope_loss_blocks_old_top(self):
        self.assertTrue(rb.query_capabilities("own")["top_sales"])
        self.data["capabilities"]["top_sales"] = False
        self.read.return_value = envelope(self.data)
        self.read.reset_mock()
        self.assertEqual(rb.fetch_top_sales("own")["error_code"], "ERR_DMS_QUERY_SCOPE")
        self.assertEqual(self.read.call_count, 1)
        self.request.assert_not_called()

    def test_custom_top_registers_only_and_is_paginated_locally(self):
        params = rb.top_parameters("subtype", "amount", "01/01/2569", "24/09/2569")
        top = sync.collect_top(self.client, self.scope, params)
        self.read.side_effect = [envelope(self.data), envelope(top)]
        result = rb.fetch_top_sales("own", **params, limit=30, page=2)
        self.assertTrue(result["ok"])
        self.assertEqual(result["rows"], [{"label": "Model 30", "value": 30}])
        self.assertEqual(result["total"], 31)
        self.assertFalse(result["has_more"])
        self.request.assert_called_once_with("EP-own", rb.top_dataset(params), params, reader="sales")
        self.login.assert_not_called()
        self.enqueue.assert_not_called()

    def test_default_top_does_not_consume_custom_slots_or_fail_when_full(self):
        top = sync.collect_top(self.client, self.scope, rb.top_parameters())
        self.read.side_effect = [envelope(self.data), envelope(top)]
        self.request.side_effect = store.SnapshotUnavailable("ERR_DMS_SNAPSHOT_REQUEST_LIMIT")
        self.assertTrue(rb.fetch_top_sales("own")["ok"])
        self.request.assert_not_called()

    def test_new_period_pending_has_no_external_read_and_same_dataset_across_pages(self):
        self.read.side_effect = [envelope(self.data), store.SnapshotUnavailable(), envelope(self.data), store.SnapshotUnavailable()]
        for page in (1, 3):
            self.assertEqual(rb.fetch_top_sales("own", date_from="01/01/2569", page=page)["error_code"], "ERR_DMS_SNAPSHOT_PENDING")
        self.assertEqual(self.request.call_args_list[0], self.request.call_args_list[1])
        self.login.assert_not_called()

    def test_changed_external_scope_or_wrong_period_cannot_reuse_old_top(self):
        params = rb.top_parameters()
        top = sync.collect_top(self.client, self.scope, params)
        for bad in ({**top, "dashboard_scope": "old-scope"}, {**top, "params": {**params, "metric": "amount"}}):
            self.read.side_effect = [envelope(self.data), envelope(bad)]
            self.assertEqual(rb.fetch_top_sales("own")["error_code"], "ERR_DMS_SNAPSHOT_PENDING")

    def test_period_normalization_and_invalid_dates(self):
        self.assertEqual(rb.top_parameters(date_from="1/9/2569", date_to="24/9/2569")["date_from"], "01/09/2569")
        for start, end in (("31/02/2569", "01/03/2569"), ("02/03/2569", "01/03/2569"), ("text", "01/03/2569")):
            self.assertFalse(rb.fetch_top_sales("own", date_from=start, date_to=end)["ok"])
        self.request.assert_not_called()


class Refresh(unittest.TestCase):
    def setUp(self):
        self.client, self.adapter = NativeClient(), adapter()
        self.db = self.enterContext(patch.object(frappe, "db", Mock()))
        self.db.get_value.return_value = "employee"
        self.enterContext(patch.object(rb, "_query_endpoint", return_value="EP-own"))
        self.enterContext(patch.object(store, "scope", return_value="start-scope"))
        self.requests = self.enterContext(patch.object(store, "requested", return_value=[]))
        self.publish = self.enterContext(patch.object(store, "publish", return_value=True))
        self.failed = self.enterContext(patch.object(store, "failed"))
        self.login = self.enterContext(patch.object(sync, "run_logged_in", side_effect=self.run_login))

    def run_login(self, endpoint, fn, **kwargs):
        self.assertEqual(endpoint, "EP-own")
        self.assertEqual(kwargs, {"authoritative_read": False, "background": True})
        try:
            return fn(self.client, self.adapter)
        except DmsClientError as exc:
            return {"ok": False, "error_code": exc.error_code}

    def test_one_employee_login_collects_defaults_and_registered_period(self):
        params = rb.top_parameters("type", "amount", "01/01/2569", "24/09/2569")
        self.requests.return_value = [{"dataset": rb.top_dataset(params), "params": params}]
        result = sync.refresh("EP-own")
        self.assertTrue(result["ok"])
        self.assertEqual(self.login.call_count, 1)
        self.assertEqual(len(result["published"]), 8)
        self.assertEqual(self.publish.call_count, 8)
        for call in self.publish.call_args_list:
            self.assertEqual(call.kwargs, {"reader": "sales", "expected_scope": "start-scope"})
        self.assertEqual(self.db.commit.call_count, 2)
        self.db.rollback.assert_not_called()

    def test_partial_sales_not_published_while_independent_tops_complete(self):
        with patch.object(sync, "collect_sales", side_effect=sync._incomplete("bounded")):
            result = sync.refresh("EP-own")
        self.assertFalse(result["ok"])
        self.assertEqual(result["failed"][rb.SALES_DATASET], "ERR_DMS_SNAPSHOT_INCOMPLETE")
        self.assertNotIn(rb.SALES_DATASET, result["published"])
        self.failed.assert_called_once_with("EP-own", rb.SALES_DATASET, "ERR_DMS_SNAPSHOT_INCOMPLETE", reader="sales", expected_scope="start-scope")

    def test_one_top_failure_does_not_block_complete_sales(self):
        with patch.object(sync, "collect_top", side_effect=sync._incomplete("malformed")):
            result = sync.refresh("EP-own")
        self.assertEqual(result["published"], [rb.SALES_DATASET])
        self.assertEqual(len(result["failed"]), 6)

    def test_auth_loss_overwrites_old_authorized_results_but_timeout_does_not(self):
        self.login.side_effect = None
        self.login.return_value = {"ok": False, "error_code": "ERR_DMS_AUTH"}
        result = sync.refresh("EP-own")
        self.assertEqual(len(result["published"]), 7)
        self.assertTrue(all(call.args[2] == {"ok": False, "error_code": "ERR_DMS_AUTH"} for call in self.publish.call_args_list))
        self.publish.reset_mock()
        self.login.return_value = {"ok": False, "error_code": "ERR_DMS_UNREACHABLE"}
        result = sync.refresh("EP-own")
        self.assertEqual(result["published"], [])
        self.publish.assert_not_called()
        self.assertEqual(self.failed.call_count, 7)

    def test_scope_change_before_publish_rolls_back_entire_batch(self):
        self.publish.side_effect = store.SnapshotUnavailable("ERR_DMS_SNAPSHOT_SCOPE_CHANGED")
        result = sync.refresh("EP-own")
        self.assertEqual(result["error_code"], "ERR_DMS_SNAPSHOT_SCOPE_CHANGED")
        self.db.commit.assert_called_once()  # Discard the capture's read view only.
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
        self.records = self.enterContext(patch.object(rb, "fetch_sales_records", return_value={"ok": True, "rows": []}))
        self.top = self.enterContext(patch.object(rb, "fetch_top_sales", return_value={"ok": True, "rows": []}))
        self.capabilities = self.enterContext(patch.object(rb, "query_capabilities", return_value={"ok": True, "top_sales": True}))
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
