"""Bounded, read-only DMS snapshot collection in the caller's logged-in session.

No framework, login, cache, filesystem or publishing lives here. The caller supplies
the five bank catalogues read in the same authoritative session and publishes only
a successful *combined* result. Existing live write-time checks are unchanged.

Customer paging uses the native count (sd only -> dt::N) and sdtamt/sdtpage
protocol, verified read-only on DMS-TEST on 2026-09-24. Beyond the final page the
native endpoint returns an empty body, which is NOT by itself completeness proof.
The final ID-list recheck detects pagination drift, not a transactional DMS view.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from html.parser import HTMLParser
import json
import math
import re
import time

from pearnly_dms.client.client_intake import (
    _ADDR_MAP, _ADDR_SUFFIXES, _BRANCH_CODE, _BRANCH_VAL,
    _GEO_LABEL_MAP, _IDENTITY_MAP, _IdentitySearchRows,
)
from pearnly_dms.contracts.client_base import DmsClientError


ERROR = "ERR_DMS_SNAPSHOT_INCOMPLETE"
BANK_KEYS = ("company_banks", "source_banks", "cheque_banks", "cashier_banks", "card_banks")
# DMS-TEST native protocol rechecked on 2026-09-24: these optional bank
# selectors return HTTP 200 with exactly zero bytes for no matches. The native
# payment contract permits manual names; receipt accounts do not share this rule.
EMPTY_BANK_SELECTORS = frozenset({
    "txtbanknametffrom", "txtbanknamecheque", "txtbanknamecashiercq", "txtbanknamecddbc",
})
MASTER_KEYS = ("cars", "place_books", "term_sales", "branches", "regis_behalfs", "advisors", "prefixes")
GEO_PATHS = {
    "districts": ("cus/component/listdistricts.php", "selprovinces"),
    "subdistricts": ("cus/component/listsubdistricts.php", "seldistricts"),
    "zipcodes": ("cus/component/listzipcodes.php", "selsubdistricts"),
}
READ_PATHS = frozenset({
    "cus/form.php", "drfcbc/form.php", "cus/component/showdata.php",
    "drfcbc/component/bshsd.php", "users/component/showdata.php",
    "car/view.php", "car/component/showdata.php", "bank/view.php", "bank/component/showdata.php",
    *(path for path, _ in GEO_PATHS.values()),
})


@dataclass(frozen=True)
class SnapshotLimits:
    page_size: int = 100
    max_pages: int = 100
    max_customers: int = 5000
    max_requests: int = 20000
    max_rows: int = 50000
    max_response_bytes: int = 4 * 1024 * 1024
    max_total_bytes: int = 128 * 1024 * 1024
    max_seconds: float = 240

    def __post_init__(self):
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(value) or value <= 0
               for value in self.__dict__.values()):
            raise ValueError("Snapshot limits must be positive")
        if any(not isinstance(getattr(self, key), int) for key in self.__dict__ if key != "max_seconds"):
            raise ValueError("Snapshot count limits must be integers")
        if self.page_size > 500:
            raise ValueError("Snapshot page size exceeds native collection bound")


def _fail(reason):
    # Never include response bodies or customer fields in errors/logs.
    raise DmsClientError("DMS snapshot incomplete: " + reason, ERROR)


class _Budget:
    def __init__(self, limits):
        self.limits = limits
        self.started = time.monotonic()
        self.requests = self.bytes = self.rows = 0
        self.failed = False

    def check(self):
        if self.failed or time.monotonic() - self.started > self.limits.max_seconds:
            self.failed = True
            _fail("collection budget exceeded")

    def add_rows(self, count):
        self.check()
        self.rows += count
        if self.rows > self.limits.max_rows:
            self.failed = True
            _fail("row budget exceeded")

    @contextmanager
    def bind(self, client):
        """Bound all nested client reads; restore the request-local client on exit."""
        original = client._post_text
        had_override = "_post_text" in client.__dict__
        previous = client.__dict__.get("_post_text")

        def read(path, data):
            self.check()
            # This collector must never acquire a write capability accidentally.
            if path not in READ_PATHS or (path.endswith("/form.php") and data.get("status") not in {"n", "e"}):
                self.failed = True
                _fail("unexpected read endpoint")
            self.requests += 1
            if self.requests > self.limits.max_requests:
                self.failed = True
                _fail("request budget exceeded")
            try:
                body = original(path, data)
            except Exception:
                # Prefix/employee legacy helpers can catch errors and return a
                # partial fallback. Such a read must still poison the whole batch.
                self.failed = True
                raise
            self.check()
            if not isinstance(body, str):
                self.failed = True
                _fail("non-text response")
            size = len(body.encode("utf-8"))
            self.bytes += size
            if size > self.limits.max_response_bytes or self.bytes > self.limits.max_total_bytes:
                self.failed = True
                _fail("response budget exceeded")
            if path.endswith("/bshsd.php"):
                if body == "" and data.get("elemname") in EMPTY_BANK_SELECTORS:
                    return body  # Known native empty, not whitespace/error HTML/null.
                try:
                    value = json.loads(body)
                except ValueError:
                    self.failed = True
                    _fail("invalid catalogue response")
                if not isinstance(value, list):
                    self.failed = True
                    _fail("invalid catalogue response")
            return body

        client._post_text = read
        try:
            yield
            self.check()  # Nested legacy helpers may catch a read exception.
        finally:
            if had_override:
                client._post_text = previous
            else:
                del client._post_text


def _rows(value, budget, *, required=False):
    if not isinstance(value, list) or (required and not value):
        _fail("missing catalogue")
    seen = set()
    for row in value:
        if (not isinstance(row, (list, tuple)) or len(row) < 2
                or any(not isinstance(cell, (str, int, float, type(None))) for cell in row)):
            _fail("invalid catalogue row")
        key = str(row[0] or "").strip()
        if not key or key in seen:
            _fail("duplicate or missing catalogue identity")
        seen.add(key)
    budget.add_rows(len(value))
    return [list(row) for row in value]


class _Options(HTMLParser):
    """Read native option fragments without turning malformed/empty HTML into []."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.value, self.label = [], None, []
        self.count = 0
        self.invalid = False

    def handle_starttag(self, tag, attrs):
        if tag != "option" or self.value is not None:
            self.invalid = True
            return
        values = [value for name, value in attrs if name == "value"]
        if len(values) != 1 or values[0] is None or len(attrs) != len(dict(attrs)):
            self.invalid = True
            return
        self.value, self.label = values[0], []
        self.count += 1

    def handle_data(self, data):
        if self.value is None:
            if data.strip():
                self.invalid = True
        else:
            self.label.append(data)

    def handle_endtag(self, tag):
        if tag != "option" or self.value is None:
            self.invalid = True
            return
        if self.value:
            self.rows.append([self.value, "".join(self.label).strip()])
        self.value = None


def _options(body, budget):
    parser = _Options()
    parser.feed(body)
    parser.close()
    if parser.invalid or parser.value is not None or not parser.count:
        _fail("invalid address options")
    return _rows(parser.rows, budget)


def _province_rows(client, budget):
    page = client._post_text("cus/form.php", {"status": "n"})
    matches = re.findall(r'<select\b([^>]*)>(.*?)</select>', page, re.I | re.S)
    selected = [body for attrs, body in matches if client._attr(attrs, "name") == "selprovinces"]
    if len(selected) != 1:
        _fail("province selector missing or repeated")
    rows = _options(selected[0], budget)
    if not rows:
        _fail("province catalogue empty")
    return rows


def _business_catalog(client, bank_masters, budget):
    masters = client.fetch_masters(strict=True)
    if not isinstance(masters, dict) or not isinstance(bank_masters, dict):
        _fail("missing catalogues")
    masters = dict(masters)
    for key in MASTER_KEYS:
        masters[key] = _rows(masters.get(key), budget, required=key == "prefixes")
    for key in BANK_KEYS:
        masters[key] = _rows(bank_masters.get(key), budget)
    employees = masters.get("employees")
    if not isinstance(employees, list):
        _fail("employee catalogue unavailable")
    ids = [str(row.get("id") or "").strip() for row in employees if isinstance(row, dict)]
    if len(ids) != len(employees) or any(not key for key in ids) or len(set(ids)) != len(ids):
        _fail("invalid employee catalogue")
    budget.add_rows(len(employees))
    paints = {
        str(row[0]): _rows(client._bshsd_all("txtcarpaint", idcar=str(row[0])), budget)
        for row in masters["cars"]
    }
    return {"masters": masters, "paints": paints}


def _catalog(client, bank_masters, budget):
    business = _business_catalog(client, bank_masters, budget)
    geo = {"provinces": {"": _province_rows(client, budget)},
           "districts": {}, "subdistricts": {}, "zipcodes": {}}
    parents = geo["provinces"][""]
    for level, (path, field) in GEO_PATHS.items():
        children, owners = [], set()
        for parent in parents:
            rows = _options(client._post_text(path, {field: str(parent[0])}), budget)
            geo[level][str(parent[0])] = rows
            # IDs name a unique parent in this native tree; never merge wrong branches.
            if level != "zipcodes":
                ids = {str(row[0]) for row in rows}
                if owners & ids:
                    _fail("address identity has multiple parents")
                owners.update(ids)
            children.extend(rows)
        parents = children
    return {**business, "geo": geo}


def _geo_stage(stage, budget):
    """Validate checkpoint topology, not just its claimed pending queue."""
    if (not isinstance(stage, dict) or set(stage) != {"version", "geo", "todo", "processed"}
            or type(stage["version"]) is not int or stage["version"] != 1
            or type(stage["processed"]) is not int or stage["processed"] < 0):
        _fail("invalid address checkpoint")
    geo, todo = stage["geo"], stage["todo"]
    if (not isinstance(geo, dict) or set(geo) != {"provinces", *GEO_PATHS}
            or not isinstance(geo["provinces"], dict) or set(geo["provinces"]) != {""}
            or not isinstance(todo, list)):
        _fail("invalid address checkpoint shape")
    parents = _rows(geo["provinces"][""], budget, required=True)
    missing, processed = set(), 0
    for level in GEO_PATHS:
        chunks = geo[level]
        parent_ids = {str(row[0]) for row in parents}
        if (not isinstance(chunks, dict) or any(not isinstance(key, str) for key in chunks)
                or not set(chunks).issubset(parent_ids)):
            _fail("address checkpoint has unknown parent")
        missing.update((level, key) for key in parent_ids - set(chunks))
        children, owners = [], set()
        for rows in chunks.values():
            rows = _rows(rows, budget)
            if level != "zipcodes":
                ids = {str(row[0]) for row in rows}
                if owners & ids:
                    _fail("address identity has multiple parents")
                owners.update(ids)
            children.extend(rows)
        processed += len(chunks)
        parents = children
    if (any(not isinstance(node, list) or len(node) != 2
            or not all(isinstance(value, str) for value in node) for node in todo)
            or len({tuple(node) for node in todo}) != len(todo)
            or {tuple(node) for node in todo} != missing or processed != stage["processed"]):
        _fail("address checkpoint skips or repeats a parent")


def start_geo(client, *, limits=None):
    """Read the province root; the caller persists a private, unpublished checkpoint."""
    def begin(budget):
        provinces = _province_rows(client, budget)
        return {"version": 1,
                "geo": {"provinces": {"": provinces}, "districts": {}, "subdistricts": {}, "zipcodes": {}},
                "todo": [["districts", str(row[0])] for row in provinces], "processed": 0}
    return _collect(client, begin, limits)


def advance_geo(client, stage, *, max_nodes=300, limits=None):
    """Advance complete parent blocks only; never mutate the supplied checkpoint.

    A bounded time/request slice returns its remaining queue. HTTP/protocol errors
    return no checkpoint. Empty todo is only returned after re-reading the root.
    The caller owns checkpoint age/scope and publishes only that completed tree.
    """
    if type(max_nodes) is not int or not 1 <= max_nodes <= 1000:
        _fail("invalid address batch size")
    def advance(budget):
        if budget.limits.max_requests < 2:
            _fail("address batch requires root recheck budget")
        candidate = deepcopy(stage)
        _geo_stage(candidate, budget)
        geo, todo = candidate["geo"], candidate["todo"]
        completed = 0
        owners = {level: {str(row[0]) for rows in geo[level].values() for row in rows}
                  for level in ("districts", "subdistricts")}
        while todo and completed < max_nodes:
            remaining = budget.limits.max_seconds - (time.monotonic() - budget.started)
            # Leave room for the next native response and final root verification.
            if remaining <= min(5, budget.limits.max_seconds / 10):
                break
            if budget.requests + 2 > budget.limits.max_requests:
                break
            level, parent = todo[0]
            path, field = GEO_PATHS[level]
            rows = _options(client._post_text(path, {field: parent}), budget)
            ids = {str(row[0]) for row in rows}
            if level in owners:
                if owners[level] & ids:
                    _fail("address identity has multiple parents")
                owners[level].update(ids)
            geo[level][parent] = rows
            todo.pop(0)
            next_level = {"districts": "subdistricts", "subdistricts": "zipcodes"}.get(level)
            if next_level:
                todo.extend([[next_level, str(row[0])] for row in rows])
            completed += 1
            candidate["processed"] += 1
        if not todo and _province_rows(client, budget) != geo["provinces"][""]:
            _fail("province catalogue changed during address collection")
        return candidate
    return _collect(client, advance, limits)


def _customer_count(client, budget):
    body = client._post_text("cus/component/showdata.php", {"sd": ""})
    match = re.fullmatch(r"dt::(0|[1-9][0-9]*)", body.strip())
    if not match or len(match[1]) > 8:
        _fail("invalid customer count")
    count = int(match[1])
    if count > budget.limits.max_customers:
        _fail("customer count exceeds bound")
    return count


def _customer_ids(client, budget):
    count = _customer_count(client, budget)
    page_count = (count + budget.limits.page_size - 1) // budget.limits.page_size
    if page_count > budget.limits.max_pages:
        _fail("customer page limit reached before end")
    ids, seen = [], set()
    for page in range(1, page_count + 1):
        body = client._post_text("cus/component/showdata.php", {
            "sdtamt": str(budget.limits.page_size), "sdtpage": str(page), "sd": "",
            "selcolsort": "1", "selcolsorttype": "1",
        })
        parser = _IdentitySearchRows()
        parser.feed(body)
        parser.close()
        if (parser.invalid or parser.depth
                or len(parser.ids) != len(re.findall(r"\bdata-val\s*=", body, re.I))
                or not body.lstrip().startswith("dt::") or not parser.ids):
            _fail("invalid customer list")
        expected = min(budget.limits.page_size, count - len(ids))
        if len(parser.ids) != expected:
            _fail("customer page does not match native count")
        if len(set(parser.ids)) != len(parser.ids) or seen.intersection(parser.ids):
            _fail("customer page repeats")
        seen.update(parser.ids)
        ids.extend(parser.ids)
    if _customer_count(client, budget) != count:
        _fail("customer count changed during collection")
    return ids


def _customer_id(cid):
    if not isinstance(cid, str) or not cid or cid != cid.strip() or len(cid) > 128:
        _fail("invalid customer identity")
    return cid


def _read_customer_complete(client, cid, budget):
    cid = _customer_id(cid)
    required = set(_IDENTITY_MAP.values()) | {"txtcuscode", _BRANCH_VAL, _BRANCH_CODE}
    required.update(field + suffix for field in _ADDR_MAP.values() for suffix in _ADDR_SUFFIXES)
    page = client._post_text("cus/form.php", {"status": "e", "id": cid})
    data = client._identity_form_data(page)
    controls = [client._attr(attrs, "name")
                for attrs in re.findall(r"<(?:input|select|textarea)\b([^>]*)>", page, re.I | re.S)]
    if any(field not in data or controls.count(field) != 1 for field in required):
        _fail("incomplete customer detail")
    if any(name and name not in required | {"stsel", "idsel"} for name in controls):
        _fail("unrecognized customer field")
    if data.get("stsel") != "e" or data.get("idsel") != cid:
        _fail("customer form identity mismatch")
    budget.add_rows(1)
    # The source ID is the explicit full-form read target, never a list code,
    # visible name, or an untrusted field in the response/defaults.
    return {**client._extract_customer_fields(data, page), "customer_id": cid}


def _customers(client, budget, extra_customer_ids):
    # Locally confirmed writes may not yet appear in the native list. Their IDs
    # are hints only: read complete current forms, never publish old overlay fields
    # as freshly read. Keep this bounded even for an accidental infinite iterable.
    if not isinstance(extra_customer_ids, (tuple, list, set, frozenset)):
        _fail("invalid extra customer identities")
    if len(extra_customer_ids) > budget.limits.max_customers:
        _fail("extra customer count exceeds bound")
    extras = []
    for cid in extra_customer_ids:
        _customer_id(cid)
        if cid not in extras:
            extras.append(cid)
    ids = _customer_ids(client, budget)
    detail_ids = list(dict.fromkeys(ids + extras))
    if len(detail_ids) > budget.limits.max_customers:
        _fail("combined customer count exceeds bound")
    customers = {cid: _read_customer_complete(client, cid, budget) for cid in detail_ids}
    if _customer_ids(client, budget) != ids:
        _fail("customer list changed during collection")
    return customers


def _collect(client, operation, limits):
    budget = _Budget(limits or SnapshotLimits())
    try:
        with budget.bind(client):
            return operation(budget)
    except DmsClientError as exc:
        if exc.error_code == ERROR:
            raise
        _fail("native read rejected")


def collect_catalog(client, *, bank_masters, limits=None):
    """Complete known native catalogues only; not a publishable directory by itself."""
    return _collect(client, lambda budget: _catalog(client, bank_masters, budget), limits)


def collect_masters(client, *, bank_masters, limits=None):
    """Independent complete options and colours; no customer or address scan."""
    return _collect(client, lambda budget: _business_catalog(client, bank_masters, budget), limits)


def collect_vehicle_options(client, *, catalogues, limits=None):
    """Preserve the native booking projection, separate from complete car forms."""
    def collect(budget):
        cars = _rows(client._bshsd_all("txtcar"), budget)
        paints = {}
        for car in cars:
            cid = str(car[0])
            if cid not in catalogues["car"]:
                _fail("car picker changed during collection")
            rows = _rows(client._bshsd_all("txtcarpaint", idcar=cid), budget)
            expected = set(catalogues["car"][cid]["fields"]["chkcarpaint[]"])
            if {str(row[0]) for row in rows} != expected or not expected.issubset(catalogues["carpaint"]):
                _fail("car colour relationships changed during collection")
            paints[cid] = rows
        return {"masters": {"cars": cars}, "paints": paints}
    return _collect(client, collect, limits)


def collect_company_options(client, *, catalogues, bank_reader, limits=None):
    """Operational selectors retain native IDs and their distinct bank scopes."""
    def collect(budget):
        masters = {key: _rows(client._bshsd_all(elem), budget) for key, elem in (
            ("place_books", "txtplacebook"), ("term_sales", "txttermsale"),
            ("branches", "txtbranch_book"), ("regis_behalfs", "txtregisbehalf"))}
        masters["advisors"] = _rows(client._advisor_rows(), budget)
        banks = bank_reader()
        for key in BANK_KEYS:
            masters[key] = _rows(banks.get(key), budget)
        for key, table in (("branches", "branch"), ("advisors", "users"), ("company_banks", "bank")):
            if any(str(row[0]) not in catalogues[table] for row in masters[key]):
                _fail("company picker changed during collection")
        masters["employees"] = [{"id": cid, "code": row["fields"]["txtuserscode"],
            "login": row["fields"]["txtusers"], "name": row["fields"]["txtusersname"]}
            for cid, row in catalogues["users"].items()]
        return {"masters": masters}
    return _collect(client, collect, limits)


def start_customers(client, *, extra_customer_ids=(), limits=None):
    """Begin a private, resumable full scan, not an incrementally fresh result."""
    def begin(budget):
        extras = list(dict.fromkeys(_customer_id(cid) for cid in extra_customer_ids))
        count = _customer_count(client, budget)
        if len(extras) > budget.limits.max_customers:
            _fail("extra customer count exceeds bound")
        return {"version": 1, "phase": "list", "count": count, "ids": [],
                "extras": extras, "customers": {}, "verified": [], "page": 1}
    return _collect(client, begin, limits)


def _customer_page(client, budget, count, page):
    body = client._post_text("cus/component/showdata.php", {
        "sdtamt": str(budget.limits.page_size), "sdtpage": str(page), "sd": "",
        "selcolsort": "1", "selcolsorttype": "1"})
    parser = _IdentitySearchRows()
    parser.feed(body)
    parser.close()
    expected = min(budget.limits.page_size, count - (page - 1) * budget.limits.page_size)
    if (expected <= 0 or parser.invalid or parser.depth or not body.lstrip().startswith("dt::")
            or len(parser.ids) != expected or len(set(parser.ids)) != expected
            or len(parser.ids) != len(re.findall(r"\bdata-val\s*=", body, re.I))):
        _fail("invalid customer page")
    return parser.ids


def _validate_customer_checkpoint_records(customers):
    # Match _read_customer_complete/_extract_customer_fields, including all three
    # address blocks and their selected labels. A persisted ID alone is not a
    # complete customer. Empty native values are legal; missing or typed values
    # are not repaired with defaults or treated as a fresh "not found" result.
    required = set(_IDENTITY_MAP) | {"customer_id", "prefix_name", "cuscode", "branch_id", "branch_code"}
    required.update(field + suffix for field in (*_ADDR_MAP, *_GEO_LABEL_MAP) for suffix in _ADDR_SUFFIXES)
    for cid, fields in customers.items():
        if (not isinstance(fields, dict) or set(fields) != required
                or fields.get("customer_id") != cid
                or any(not isinstance(value, str) for value in fields.values())):
            _fail("incomplete customer checkpoint fields")


def advance_customers(client, stage, *, max_nodes=20, limits=None):
    """List, full details, then list/count verification across short sessions."""
    if type(max_nodes) is not int or not 1 <= max_nodes <= 100:
        _fail("invalid customer chunk size")
    def advance(budget):
        candidate = deepcopy(stage)
        if (not isinstance(candidate, dict) or candidate.get("version") != 1
                or candidate.get("phase") not in {"list", "details", "verify", "done"}
                or type(candidate.get("count")) is not int or not 0 <= candidate["count"] <= budget.limits.max_customers
                or not all(isinstance(candidate.get(key), list) for key in ("ids", "extras", "verified"))
                or not isinstance(candidate.get("customers"), dict)
                or type(candidate.get("page")) is not int or candidate["page"] < 1):
            _fail("invalid customer checkpoint")
        count = candidate["count"]
        ids, extras, verified, customers = (candidate[key] for key in ("ids", "extras", "verified", "customers"))
        if (len(ids) > count or len(set(ids)) != len(ids) or len(set(extras)) != len(extras)
                or len(set(verified)) != len(verified) or verified != ids[:len(verified)]
                or not set(customers).issubset(set(ids + extras))):
            _fail("invalid customer checkpoint coverage")
        for cid in ids + extras + verified:
            _customer_id(cid)
        detail_ids = list(dict.fromkeys(ids + extras))
        if len(detail_ids) > budget.limits.max_customers:
            _fail("combined customer count exceeds bound")
        if candidate["phase"] != "list" and len(ids) != count:
            _fail("incomplete customer checkpoint list")
        _validate_customer_checkpoint_records(customers)
        if candidate["phase"] in {"verify", "done"} and set(customers) != set(detail_ids):
            _fail("incomplete customer checkpoint details")
        if candidate["phase"] == "done" and verified != ids:
            _fail("incomplete customer checkpoint verification")
        for _ in range(max_nodes):
            if budget.limits.max_seconds - (time.monotonic() - budget.started) < 5:
                break
            phase = candidate["phase"]
            if phase in {"list", "verify"}:
                target = ids if phase == "list" else verified
                if len(target) < count:
                    page = len(target) // budget.limits.page_size + 1
                    if page > budget.limits.max_pages:
                        _fail("customer page bound exceeded")
                    rows = _customer_page(client, budget, count, page)
                    if set(target).intersection(rows):
                        _fail("duplicate customer page")
                    target.extend(rows)
                    if phase == "verify" and verified != ids[:len(verified)]:
                        _fail("customer list changed during capture")
                    candidate["page"] = page + 1
                else:
                    if _customer_count(client, budget) != count:
                        _fail("customer count changed during capture")
                    candidate["phase"] = "details" if phase == "list" else "done"
                    candidate["page"] = 1
                    detail_ids = list(dict.fromkeys(ids + extras))
                    if len(detail_ids) > budget.limits.max_customers:
                        _fail("combined customer count exceeds bound")
            elif phase == "details":
                pending = [cid for cid in detail_ids if cid not in customers]
                if pending:
                    cid = pending[0]
                    customers[cid] = _read_customer_complete(client, cid, budget)
                else:
                    candidate["phase"] = "verify"
            else:
                if set(customers) != set(detail_ids) or verified != ids:
                    _fail("incomplete customer checkpoint result")
                break
        if candidate["phase"] == "done" and (set(customers) != set(detail_ids) or verified != ids):
            _fail("incomplete customer checkpoint result")
        return candidate
    return _collect(client, advance, limits)


def read_customer_complete(client, cid, *, limits=None):
    """A bounded full native read for write-through, never default-filled truncation."""
    return _collect(client, lambda budget: _read_customer_complete(client, cid, budget), limits)


def collect_customers(client, *, extra_customer_ids=(), limits=None):
    """Read the count-backed native list plus fresh forms for confirmed local IDs."""
    return _collect(client, lambda budget: _customers(client, budget, extra_customer_ids), limits)


def collect_business(client, *, bank_masters, extra_customer_ids=(), limits=None):
    """Legacy combined reader; manual jobs use independent datasets instead.

    The native customer new form is still read for its prefix options. No province
    extraction or address-cascade request is part of this collector.
    """
    def collect(budget):
        catalog = _business_catalog(client, bank_masters, budget)
        return {**catalog, "customers": _customers(client, budget, extra_customer_ids)}
    return _collect(client, collect, limits)


def collect_directory(client, *, bank_masters, extra_customer_ids=(), limits=None):
    """Return all four sections only after every bounded read and recheck succeeds."""
    def collect(budget):
        catalog = _catalog(client, bank_masters, budget)
        return {**catalog, "customers": _customers(client, budget, extra_customer_ids)}
    return _collect(client, collect, limits)
