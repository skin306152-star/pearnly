"""Explicit one-condition native query using one employee login per request.

No administrator scope and no reconstructed ranking: list order/status membership
and ranking values are copied from the employee's native DMS responses. Bounds,
malformed pages, changing row sets and repeated pages fail the entire dataset.
"""
import hashlib
import json
import logging
import math
import re
import time

import frappe

logger = logging.getLogger(__name__)

from pearnly_dms.contracts.client_base import DmsClientError
from pearnly_dms.contracts.endpoint import endpoint_name
from pearnly_dms.adapter.operations import run_logged_in
from pearnly_dms.query import sales_readback as rb
from pearnly_dms.line.identity import matches as current_binding
from pearnly_dms.snapshots import manual, store

PAGE_SIZE = 200
MAX_PAGES = 20
TOP_PAGE_SIZE = 30
MAX_TOP_ROWS = 4000
MAX_SCAN_SECONDS = 240
MAX_READS = 600
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
# Existing transport: 30-second read timeout, at most one read retry. Do not
# start a request without room for both attempts. Leave cleanup time in the
# manual worker's 420-second hard limit; collector keeps a smaller read budget.
READ_RESERVE_SECONDS = 60


def _incomplete(message):
    return DmsClientError(message, "ERR_DMS_SNAPSHOT_INCOMPLETE")


class ScanBudget:
    def __init__(self, job_started):
        self.deadline = min(time.monotonic() + MAX_SCAN_SECONDS, job_started + 280)
        self.requests = 0
        self.bytes = 0

    def before(self):
        if time.monotonic() + READ_RESERVE_SECONDS > self.deadline or self.requests >= MAX_READS:
            raise _incomplete("DMS query capture budget exhausted")
        self.requests += 1

    def after(self, body):
        self.bytes += len(str(body or "").encode("utf-8"))
        if time.monotonic() > self.deadline or self.bytes > MAX_RESPONSE_BYTES:
            raise _incomplete("DMS query capture budget exhausted")


class _BoundedClient:
    def __init__(self, client, budget):
        self.client, self.budget = client, budget

    def _post_text(self, path, payload):
        self.budget.before()
        body = self.client._post_text(path, payload)
        self.budget.after(body)
        return body


class _BoundedAdapter:
    def __init__(self, adapter, budget):
        self.adapter, self.budget = adapter, budget
        self.base_url = adapter.base_url

    def transport(self):
        return self

    def get(self, url):
        self.budget.before()
        response = self.adapter.transport().get(url)
        self.budget.after(response.text)
        return response


def dashboard_scope(adapter):
    response = adapter.transport().get(adapter.base_url + "home/home.php")
    if response.status_code != 200:
        code = "ERR_DMS_QUERY_SCOPE" if response.status_code in (401, 403) else "ERR_DMS_TECHNICAL"
        raise DmsClientError("DMS dashboard request failed", code)
    user, branches, teams = rb._dashboard_scope(response.text)
    branch_ids, team_ids = rb._concrete_ids(branches), rb._concrete_ids(teams)
    # Scope changes (including a reduction of branch/team access) invalidate
    # old ranking data even when refreshing a particular period failed.
    identity = [user, sorted(branch_ids.split(",")), sorted(team_ids.split(","))]
    return {"user": user, "branch_ids": branch_ids, "team_ids": team_ids,
            "branches": [row["name"] for row in branches if row.get("id") != "0"],
            "teams": [row["name"] for row in teams if row.get("id") != "0"],
            "top_sales": bool(branch_ids and team_ids),
            "key": hashlib.sha256(json.dumps(identity).encode()).hexdigest()}


def _all_sales(client, column, status, query=""):
    def count():
        # Native count request intentionally omits sdtamt/sdtpage. DMS-TEST
        # sale02 was verified to return dt::32 while page200 contained 32 rows;
        # a page beyond that count returned a bare empty body, not ndt::.
        body = str(client._post_text(rb.LIST_PATH, {"sd": query, "ftd": status,
            "selcolsort": column, "selcolsorttype": "2"}) or "").strip()
        if body.startswith("err::"):
            raise DmsClientError("DMS rejected the sales count", "ERR_DMS_QUERY_SCOPE")
        match = re.fullmatch(r"dt::(\d+)", body)
        if not match:
            raise _incomplete("DMS sales count response changed")
        total = int(match.group(1))
        if total > PAGE_SIZE * MAX_PAGES:
            raise _incomplete("DMS sales snapshot exceeds the bounded scan")
        return total

    total = count()
    rows, seen = [], set()
    for page in range(1, math.ceil(total / PAGE_SIZE) + 1):
        batch = rb._sales_page(client, column, status, query, PAGE_SIZE, page)
        ids = {row["id"] for row in batch}
        expected = min(PAGE_SIZE, total - len(rows))
        if len(batch) != expected or len(ids) != len(batch) or ids & seen:
            raise _incomplete("DMS sales pages repeat or exceed requested size")
        seen.update(ids)
        rows.extend(batch)
    if count() != total:
        raise _incomplete("DMS sales count changed during pagination")
    return rows


def collect_sales(client, params):
    # Required for a fresh salesperson session; direct showdata reads can
    # silently hide rows until native list visibility has been initialized.
    body = client._post_text("drfcbc/view.php", {"idmenu": "25", "menulv": "2"})
    if str(body or "").lstrip().startswith("err::"):
        raise DmsClientError("DMS rejected the sales view", "ERR_DMS_QUERY_SCOPE")
    rows = _all_sales(client, rb.SEARCH_COLUMNS[params["field"]],
                      rb.STATUS_FILTERS[params["status"]], params["query"])
    # DMS's sd is global search, while the selected menu dimension is a field.
    # Filter the complete native-ordered set before local pagination; do not
    # sort it or derive ranking/financial statistics in Python.
    needle = params["query"].casefold()
    if needle:
        rows = [row for row in rows if needle in row[params["field"]].casefold()]
    return {"ok": True, "version": rb.SNAPSHOT_VERSION, "params": params, "rows": rows}


def _top_post(client, payload):
    body = client._post_text(rb.TOP_SALES_PATH, payload)
    if str(body or "").lstrip().startswith("err::"):
        raise DmsClientError("DMS rejected the dashboard", "ERR_DMS_QUERY_SCOPE")
    return body


def _top_count(client, payload):
    raw = str(_top_post(client, {**payload, "status": "all"}) or "").strip()
    if not raw.isdigit():
        raise _incomplete("DMS dashboard count is invalid")
    total = int(raw)
    if total > MAX_TOP_ROWS:
        raise _incomplete("DMS dashboard exceeds the bounded scan")
    return total


def collect_top(client, scope, params):
    if not scope["top_sales"]:
        raise DmsClientError("This DMS login has no sales dashboard", "ERR_DMS_QUERY_SCOPE")
    payload = {"idusers": scope["user"], "carmaxselltypedata": rb.TOP_GROUPS[params["group"]],
        "carmaxsellbranch": scope["branch_ids"], "carmaxsellteam": scope["team_ids"],
        "txtcarmaxselldate_bg": params["date_from"], "txtcarmaxselldate_to": params["date_to"],
        "carmaxsellqnt": rb.TOP_METRICS[params["metric"]],
        "carmaxsellshow": str(TOP_PAGE_SIZE), "carmaxsellpage": "1"}
    total = _top_count(client, payload)
    rows, seen = [], set()
    for page in range(1, max(1, math.ceil(total / TOP_PAGE_SIZE)) + 1):
        raw = _top_post(client, {**payload, "carmaxsellpage": str(page), "status": "page"})
        try:
            labels, ids, values = json.loads(raw)
            if not all(isinstance(items, list) for items in (labels, ids, values)):
                raise ValueError("dashboard arrays are not lists")
            expected = min(TOP_PAGE_SIZE, total - len(rows))
            if not len(labels) == len(ids) == len(values) == expected:
                raise ValueError("dashboard page length differs from total")
            if (any(not isinstance(value, (str, int, float)) or isinstance(value, bool) for value in labels + ids + values)
                    or any(isinstance(value, float) and not math.isfinite(value) for value in values)):
                raise ValueError("dashboard cells must be finite scalar values")
            normalized_ids = [str(value) for value in ids]
            if any(not value for value in normalized_ids) or len(set(normalized_ids)) != len(ids) or seen.intersection(normalized_ids):
                raise ValueError("dashboard IDs repeat")
        except (ValueError, TypeError) as exc:
            raise _incomplete("DMS dashboard page shape changed") from exc
        seen.update(normalized_ids)
        rows.extend({"id": rid, "label": str(label), "value": value}
                    for rid, label, value in zip(normalized_ids, labels, values))
    if _top_count(client, payload) != total:
        raise _incomplete("DMS dashboard total changed during pagination")
    return {"ok": True, "version": rb.SNAPSHOT_VERSION, "params": {"kind": "top", **params},
            "dashboard_scope": scope["key"], "branches": scope["branches"], "teams": scope["teams"],
            "rows": rows}


def _capture(client, adapter, params):
    if params["kind"] == "records":
        return collect_sales(client, params)
    scope = dashboard_scope(adapter)
    if params["kind"] == "top":
        return collect_top(client, scope, params)
    return {"ok": True, "version": rb.SNAPSHOT_VERSION, "params": params,
            "capabilities": {"top_sales": scope["top_sales"]}, "dashboard_scope": scope["key"]}


def _guard(endpoint, dataset, request_id, params):
    request = manual.assert_active(endpoint, dataset, request_id, reader="sales")
    saved = request["params"]
    binding = saved.get("_binding") or {}
    if (params != saved or request["actor"] != binding.get("user_id")
            or not binding.get("binding_revision") or not binding.get("line_user_id")
            or not current_binding(binding, binding["line_user_id"])):
        raise DmsClientError("Query binding has changed", "ERR_DMS_QUERY_FORBIDDEN")
    conditions = rb.query_parameters(**{key: value for key, value in saved.items() if key != "_binding"})
    if rb.query_dataset(conditions) != dataset:
        raise DmsClientError("Query conditions do not match request", "ERR_DMS_TECHNICAL")
    if endpoint_name(rb._query_endpoint(request["actor"])) != endpoint:
        raise DmsClientError("Employee endpoint is no longer current", "ERR_NO_CREDS")
    return conditions, request["expected_scope"]


def _push_result(endpoint, dataset, request_id, conditions):
    """读取落地后，把结果推给发起这次查询的那位操作员（失败绝不影响读取结果）。

    `request_id` 此刻可能已经被下一次登记替换掉 —— 那就**不推**：那份结果属于
    旧的条件。推送失败只记日志：数据已经安全落库，界面动作不该因为一条消息回退。
    """
    try:
        request = manual._load(endpoint, dataset, "sales")[2]
        if request.get("id") != request_id:
            return
        binding = (request.get("params") or {}).get("_binding") or {}
        if not current_binding(binding, binding.get("line_user_id")):
            return
        from pearnly_dms.query import query_flow

        kind = conditions["kind"]
        if kind == "capabilities":
            query_flow._run_dimensions(binding, binding["line_user_id"])
        else:
            # 条件形状与 `_begin_records` 当初写进会话的那份逐字一致 —— 否则
            # `_same_query_session` 会认为"操作员已经换条件了"，结果就不该推。
            params = {"query_kind": kind, **{key: value for key, value in conditions.items()
                                             if key != "kind"}}
            runner = query_flow._run_records if kind == "records" else query_flow._run_top
            runner(binding, binding["line_user_id"], params)
    except Exception:
        logger.warning("DMS query result push failed", exc_info=True)


def refresh(endpoint, *, request_id=None, dataset="sales_query", params=None):
    """Token-gated one-shot collector. Retired scheduler calls do no IO."""
    if not request_id:
        return {"ok": False, "retired": True}
    name = endpoint_name(endpoint)
    started = time.time()
    job_started = time.monotonic()
    expected_scope = None
    try:
        conditions, expected_scope = _guard(name, dataset, request_id, params)
        def capture(client, adapter):
            budget = ScanBudget(job_started)
            return _capture(_BoundedClient(client, budget), _BoundedAdapter(adapter, budget), conditions)

        # 这是操作员自己按出来的那一次查询：做完顺手把使用记录里的增量补回缓存。
        result = run_logged_in(name, capture, authoritative_read=False, background=True,
                               read_only_batch=True, catch_up=True)
        # Capture only reads local state. End MariaDB's repeatable-read view
        # before rechecking credentials/permission in publish(expected_scope).
        # Otherwise a revocation in another connection can remain invisible.
        frappe.db.commit()
        if result.get("busy") or result.get("error_code") in {"ERR_DMS_BUSY", "ERR_DMS_SNAPSHOT_BUSY"}:
            return {"ok": False, "busy": True}
        with manual.publish_guard(name, dataset, request_id, reader="sales"):
            _guard(name, dataset, request_id, params)
            if not result.get("ok"):
                store.failed(name, dataset, result.get("error_code") or "ERR_DMS_SNAPSHOT_INCOMPLETE",
                             reader="sales", expected_scope=expected_scope)
                frappe.db.commit()
                return result
            published = store.publish(name, dataset, result, started, reader="sales", expected_scope=expected_scope)
            frappe.db.commit()
        if not published:
            return {"ok": False, "error_code": "ERR_DMS_SNAPSHOT_SCOPE_CHANGED"}
        # 读完就把结果推回给发起这次查询的操作员：菜单 5 上**没有**"查看结果"按钮，
        # 老产品也是选完条件由后台读完直接推卡（`cloud_dispatch.spawn`）。
        _push_result(name, dataset, request_id, conditions)
        return {"ok": True, "complete": True, "count": len(result.get("rows") or [])}
    except Exception as exc:
        frappe.db.rollback()
        return rb._failure(exc)
