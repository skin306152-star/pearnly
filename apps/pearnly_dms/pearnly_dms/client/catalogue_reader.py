"""Bounded native master records in an already authenticated read-only session.

No login, framework, database, HTML persistence or publishing. A checkpoint is
private until phase == 'done'. All HTTP paths and form fields come from source
registries, never from the checkpoint or a caller's endpoint argument. Final
count/ID passes detect list drift; they are not a third-party transaction lock.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass
from hashlib import sha256
from html.parser import HTMLParser
import json
import math
import re
import time

from pearnly_dms.client.catalogue_schema import REGISTRY as VEHICLES
from pearnly_dms.client.client_intake import _IdentitySearchRows
from pearnly_dms.client.snapshot_reader import SnapshotLimits, _fail
from pearnly_dms.contracts.client_base import DmsClientError

RETRY = "ERR_DMS_SNAPSHOT_RETRY"


def _retry(reason):
    """可续读的失败：下一步接着读，不丢掉已经读到的断点。

    传输抖动或单次时间租约用完，并不说明已经读到的内容作废；最终 verify 阶段会
    逐页核对数量与 ID，真正的数据漂移在那里被抓住。2026-09-25 实测：数据量大的
    租户一组全量要跑很多步，一次偶发失败就把断点清掉，会让它**永远读不完**。
    """
    # `from None` 与原来的 transport 分支一致：绝不把原生错误体或异常链带进
    # 会被持久化的错误里（上游的报错可能含凭据）。
    raise DmsClientError("DMS snapshot retry: " + reason, RETRY) from None


VERSION = 1
TOTAL_SECONDS = 1800
LIST_PARAMS = {"sd": "", "ftd": "1", "selcolsort": "1", "selcolsorttype": "1"}
INPUT_TYPES = frozenset(("text", "hidden", "number", "date", "datetime-local", "tel", "email", "url"))
_SECRET = re.compile(r"password|passwd|secret|token|credential", re.I)


@dataclass(frozen=True)
class CatalogueLimits(SnapshotLimits):
    """A per-job time slice plus a cumulative execution-time ceiling."""
    max_total_seconds: int = TOTAL_SECONDS


def _registry(dataset):
    if dataset == "vehicles":
        source = VEHICLES
    elif dataset in ("company", "customer_aux", "geo_aux"):
        from pearnly_dms.client.company_catalogue_schema import REGISTRY
        source = REGISTRY
    else:
        _fail("unknown catalogue dataset")
    result = {kind: spec for kind, spec in source.items() if spec["dataset"] == dataset}
    if not result or any(not re.fullmatch(r"[a-z][a-z0-9_]{0,49}", kind) for kind in result):
        _fail("invalid catalogue registry")
    for spec in result.values():
        fields, excluded = set(spec["fields"]), set(spec.get("excluded", ()))
        if fields & excluded or any(_SECRET.search(name) for name in fields):
            _fail("unsafe catalogue field registry")
        if not set(spec.get("optional", ())) <= fields:
            _fail("invalid optional catalogue field registry")
        groups = [set(spec.get(key, ())) for key in ("arrays", "radios", "selects", "textareas", "checkboxes", "repeat_fields")]
        if any(not group <= fields for group in groups) or sum(map(len, groups)) != len(set().union(*groups)):
            _fail("invalid catalogue control registry")
    return result


def _canonical(value):
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def _signature(registry):
    return sha256(json.dumps(_canonical(registry), sort_keys=True).encode()).hexdigest()


def _policy(limits):
    # max_seconds is a per-invocation lease; other limits and cumulative time
    # constrain the entire generation even when split into many queue jobs.
    values = asdict(limits)
    values.pop("max_seconds")
    values.pop("max_customers")
    values.setdefault("max_total_seconds", TOTAL_SECONDS)
    return values


def _id(value):
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 128:
        _fail("invalid catalogue identity")
    return value


class _Controls(HTMLParser):
    """Only registered input/select/textarea values; never execute inline JS."""
    def __init__(self, spec):
        super().__init__(convert_charrefs=True)
        self.spec = spec
        self.items = {}
        self.current = None
        self.option = None
        self.invalid = False
        self.divs = []
        self.regions = {name: 0 for name in spec.get("regions", {})}

    def handle_starttag(self, tag, attrs):
        attrs_map = dict(attrs)
        if tag == "div":
            region = attrs_map.get("id")
            if region in self.regions:
                self.regions[region] += 1
            self.divs.append(region if region in self.regions else (self.divs[-1] if self.divs else None))
        if tag == "option" and self.current and self.current["tag"] == "select":
            if self.option is not None or len(attrs) != len(attrs_map):
                self.invalid = True
            self.option = {"value": attrs_map.get("value"), "label": "",
                           "selected": "selected" in attrs_map}
            return
        if tag not in ("input", "select", "textarea"):
            return
        name = attrs_map.get("name")
        if not name:
            return  # Unnamed native buttons do not participate in form data.
        if len(attrs) != len(attrs_map) or self.current is not None:
            self.invalid = True
        if name not in self.spec["fields"] and name not in self.spec.get("excluded", ()) and not _indexed(self.spec, name):
            self.invalid = True
            return
        excluded = name in self.spec.get("excluded", ())
        # Never retain even transient parsed values for credential/upload controls.
        context = name in ("stsel", "idsel", "status")
        input_type = attrs_map.get("type", "text").lower()
        default_value = "on" if tag == "input" and input_type in ("checkbox", "radio") else ""
        item = {"tag": tag, "type": input_type,
                "value": attrs_map.get("value", default_value) if not excluded or context else "",
                "checked": "checked" in attrs_map, "options": [], "text": "",
                "multiple": "multiple" in attrs_map, "region": self.divs[-1] if self.divs else None}
        self.items.setdefault(name, []).append(item)
        if tag != "input":
            self.current = item

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag != "input":
            self.handle_endtag(tag)

    def handle_data(self, value):
        if self.option is not None:
            self.option["label"] += value
        elif self.current and self.current["tag"] == "textarea":
            self.current["text"] += value

    def handle_endtag(self, tag):
        if tag == "div":
            if not self.divs:
                self.invalid = True
            else:
                self.divs.pop()
        if tag == "option" and self.option is not None:
            self.current["options"].append(self.option)
            self.option = None
        if tag in ("select", "textarea"):
            if not self.current or self.current["tag"] != tag or self.option is not None:
                self.invalid = True
            self.current = None


def _indexed(spec, name):
    rule = spec.get("indexed") or {}
    for group in ("parents", "columns", "checked_one", "checked_many"):
        for base in rule.get(group, ()):
            suffix = r"\[([1-9][0-9]{0,3})\]" + (r"\[\]" if group in ("columns", "checked_many") else "")
            match = re.fullmatch(re.escape(base) + suffix, name)
            if match:
                return group, match[1], base
    return None


def _group_names(spec, index):
    rule = spec.get("indexed") or {}
    for group in ("parents", "columns", "checked_one", "checked_many"):
        for base in rule.get(group, ()):
            yield group, base + "[" + index + "]" + ("[]" if group in ("columns", "checked_many") else "")


def _children(parser, spec, fields):
    if parser.divs or any(count != 1 for count in parser.regions.values()):
        _fail("missing or repeated catalogue child region")
    for region, names in spec.get("regions", {}).items():
        if len({len(fields[name]) for name in names}) > 1:
            _fail("catalogue child columns have different lengths")
        for name in names:
            if any(item["region"] != region for item in parser.items.get(name, ())):
                _fail("catalogue child is outside its native region")
    rule = spec.get("indexed") or {}
    indexes = {_indexed(spec, name)[1] for name in parser.items if _indexed(spec, name)}
    for index in sorted(indexes, key=int):
        lengths = []
        for group, name in _group_names(spec, index):
            items = parser.items.get(name, [])
            checkbox = group in ("checked_one", "checked_many")
            types = ("checkbox",) if checkbox else ("text", "hidden")
            if (any(item["tag"] != "input" or item["type"] not in types
                    or item["region"] != rule["region"] for item in items)
                    or (group in ("parents", "checked_one") and len(items) != 1)):
                _fail("incomplete catalogue child group")
            values = [item["value"] for item in items]
            if checkbox:
                if len(set(values)) != len(values):
                    _fail("duplicate catalogue child checkbox option")
                values = [item["value"] for item in items if item["checked"]]
            fields[name] = ((values[0] if values else "") if group in ("parents", "checked_one") else values)
            if group == "columns":
                lengths.append(len(values))
        if len(set(lengths)) > 1:
            _fail("catalogue indexed child columns have different lengths")


def _parse_form(body, spec, record_id):
    parser = _Controls(spec)
    parser.feed(body)
    parser.close()
    if parser.invalid or parser.current or parser.option or body.lstrip().startswith("err::"):
        _fail("invalid catalogue form")
    singleton = bool(spec.get("singleton"))
    # A singleton's request action is n, but the native hidden response status
    # may be e. Its presence is mandatory; never infer response context from the
    # submitted action. Explicit verified context values may tighten a registry.
    contexts = {"status": spec.get("context_values", {}).get("status")} if singleton else {"stsel": "e", "idsel": record_id}
    for name, expected in contexts.items():
        controls = parser.items.get(name, [])
        if (len(controls) != 1 or controls[0]["tag"] != "input"
                or (expected is not None and controls[0]["value"] != expected)):
            _fail("catalogue form identity mismatch")
        if singleton and controls[0]["value"] not in ("n", "e"):
            _fail("unknown singleton form context")
    fields, labels = {}, {}
    for name in spec["fields"]:
        controls = parser.items.get(name, [])
        if not controls and name in spec.get("optional", ()):
            continue  # Absent optional status is unknown, never active/zero/empty.
        if name in spec.get("repeat_fields", ()):
            if any(item["tag"] != "input" or item["type"] not in ("text", "hidden") for item in controls):
                _fail("invalid catalogue repeated scalar")
            fields[name] = [item["value"] for item in controls]
            continue
        if name in spec.get("arrays", ()):
            if any(item["tag"] != "input" or item["type"] != "checkbox" for item in controls):
                _fail("invalid catalogue checkbox group")
            values = [item["value"] for item in controls]
            if len(set(values)) != len(values):
                _fail("duplicate catalogue checkbox option")
            fields[name] = [item["value"] for item in controls if item["checked"]]
            continue
        if name in spec.get("checkboxes", ()):
            if len(controls) != 1 or controls[0]["tag"] != "input" or controls[0]["type"] != "checkbox":
                _fail("invalid catalogue checkbox")
            fields[name] = controls[0]["value"] if controls[0]["checked"] else ""
            continue
        if name in spec.get("radios", ()):
            chosen = [item for item in controls if item["checked"]]
            if (not controls or len(chosen) > 1
                    or any(item["tag"] != "input" or item["type"] != "radio" for item in controls)):
                _fail("invalid catalogue radio group")
            fields[name] = chosen[0]["value"] if chosen else ""
            continue
        if len(controls) != 1:
            _fail("missing or repeated catalogue field")
        item = controls[0]
        if name in spec.get("selects", ()):
            if item["tag"] != "select" or item["multiple"] or not item["options"]:
                _fail("invalid catalogue selector")
            options = item["options"]
            values = [option["value"] if option["value"] is not None else option["label"] for option in options]
            selected = [index for index, option in enumerate(options) if option["selected"]]
            if len(selected) > 1 or len(set(values)) != len(values):
                _fail("ambiguous catalogue selector")
            index = selected[0] if selected else 0
            fields[name], labels[name] = values[index], options[index]["label"].strip()
        elif name in spec.get("textareas", ()):
            if item["tag"] != "textarea":
                _fail("invalid catalogue text area")
            fields[name] = item["text"]
        else:
            if item["tag"] != "input" or item["type"] not in INPUT_TYPES:
                _fail("invalid catalogue scalar")
            fields[name] = item["value"]
    _children(parser, spec, fields)
    return {"id": record_id, "fields": fields, "labels": labels}


def _page_ids(body, expected):
    parser = _IdentitySearchRows()
    parser.feed(body)
    parser.close()
    if (not body.lstrip().startswith("dt::") or parser.invalid or parser.depth
            or len(parser.ids) != len(re.findall(r"\bdata-val\s*=", body, re.I))
            or len(parser.ids) != expected or len(set(parser.ids)) != expected):
        _fail("catalogue page is incomplete or repeated")
    return [_id(value) for value in parser.ids]


def start_catalogues(client, dataset, limits=None):
    """Create a private bounded checkpoint. No network request is made here."""
    limits = limits or CatalogueLimits()
    registry = _registry(dataset)
    return {"version": VERSION, "dataset": dataset, "schema": _signature(registry),
            "phase": "collect", "done": False, "cursor": 0, "step": "start", "page": 1,
            "index": 0, "ids": {kind: [] for kind in registry}, "counts": {},
            "data": {"catalogues": {kind: {} for kind in registry}},
            "count": 0, "processed": 0, "total": None, "limits": _policy(limits),
            "usage": {"requests": 0, "bytes": 0, "rows": 0, "seconds": 0.0}}


def _validate(state, registry):
    required = {"version", "dataset", "schema", "phase", "done", "cursor", "step", "page", "index",
                "ids", "counts", "data", "count", "processed", "total", "limits", "usage"}
    if (not isinstance(state, dict) or set(state) != required or state["version"] != VERSION
            or state["schema"] != _signature(registry) or state["phase"] not in ("collect", "verify", "done")
            or type(state["done"]) is not bool or state["done"] != (state["phase"] == "done")):
        _fail("invalid catalogue checkpoint")
    if (set(state["ids"]) != set(registry) or set(state["data"]) != {"catalogues"}
            or set(state["data"]["catalogues"]) != set(registry)
            or not set(state["counts"]) <= set(registry)):
        _fail("invalid catalogue checkpoint tables")
    for key in ("cursor", "page", "index", "count", "processed"):
        if type(state[key]) is not int or state[key] < (1 if key == "page" else 0):
            _fail("invalid catalogue checkpoint position")
    if state["cursor"] > len(registry) or state["count"] != state["processed"]:
        _fail("invalid catalogue checkpoint progress")
    rows = 0
    for kind, spec in registry.items():
        ids, records = state["ids"][kind], state["data"]["catalogues"][kind]
        if not isinstance(ids, list) or len(set(ids)) != len(ids) or not isinstance(records, dict):
            _fail("invalid catalogue checkpoint records")
        for value in ids:
            _id(value)
        if not set(records) <= set(ids) or list(records) != ids[:len(records)]:
            _fail("catalogue checkpoint skips a detail")
        count = state["counts"].get(kind)
        if count is None and (ids or records):
            _fail("catalogue checkpoint has records without a native count")
        if count is not None and (type(count) is not int or not 0 <= len(ids) <= count <= state["limits"]["max_rows"]):
            _fail("invalid catalogue checkpoint count")
        for identity, record in records.items():
            if (not isinstance(record, dict) or set(record) != {"id", "fields", "labels"}
                    or record["id"] != identity
                    or not (set(spec["fields"]) - set(spec.get("optional", ()))) <= set(record["fields"])
                    or any(name not in spec["fields"] and not _indexed(spec, name) for name in record["fields"])
                    or set(record["labels"]) != (set(spec.get("selects", ())) & set(record["fields"]))):
                _fail("invalid catalogue checkpoint fields")
            for name, value in record["fields"].items():
                dynamic = _indexed(spec, name)
                if name in spec.get("arrays", ()) or name in spec.get("repeat_fields", ()) or (dynamic and dynamic[0] in ("columns", "checked_many")):
                    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                        _fail("invalid catalogue checkpoint array")
                    if dynamic and dynamic[0] == "checked_many" and len(value) != len(set(value)):
                        _fail("duplicate catalogue checkpoint checkbox option")
                elif not isinstance(value, str):
                    _fail("invalid catalogue checkpoint scalar")
            indexes = {_indexed(spec, name)[1] for name in record["fields"] if _indexed(spec, name)}
            for index in indexes:
                names = list(_group_names(spec, index))
                if any(name not in record["fields"] for _, name in names):
                    _fail("incomplete catalogue checkpoint child group")
                if len({len(record["fields"][name]) for group, name in names if group == "columns"}) > 1:
                    _fail("incomplete catalogue checkpoint child columns")
            if any(not isinstance(value, str) for value in record["labels"].values()):
                _fail("invalid catalogue checkpoint label")
        rows += len(records)
    if rows != state["count"] or set(state["usage"]) != {"requests", "bytes", "rows", "seconds"}:
        _fail("invalid catalogue checkpoint accounting")
    if any(not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0
           for value in state["usage"].values()):
        _fail("invalid catalogue checkpoint budget")
    policy, usage = state["limits"], state["usage"]
    if (usage["requests"] > policy["max_requests"] or usage["bytes"] > policy["max_total_bytes"]
            or usage["seconds"] > policy["max_total_seconds"] or usage["rows"] > 2 * policy["max_rows"]
            or state["count"] > policy["max_rows"]):
        _fail("catalogue checkpoint exceeds cumulative budget")
    if state["total"] != (sum(state["counts"].values()) if len(state["counts"]) == len(registry) else None):
        _fail("invalid catalogue checkpoint total")
    kinds = list(registry)
    if state["cursor"] >= len(kinds):
        _fail("invalid catalogue cursor")
    for position, kind in enumerate(kinds):
        ids, records, count = state["ids"][kind], state["data"]["catalogues"][kind], state["counts"].get(kind)
        complete = count is not None and count == len(ids) == len(records)
        if state["phase"] != "collect" or position < state["cursor"]:
            if not complete:
                _fail("catalogue checkpoint skipped an earlier table")
        elif position > state["cursor"] and (count is not None or ids or records):
            _fail("catalogue checkpoint table order changed")
        elif position == state["cursor"]:
            step = state["step"]
            if step in ("start", "count", "singleton") and (count is not None or ids or records):
                _fail("invalid initial catalogue checkpoint")
            if step == "page" and (count is None or records or len(ids) != (state["page"] - 1) * policy["page_size"]):
                _fail("catalogue checkpoint skips a page")
            if step in ("detail", "end_count") and (count is None or len(ids) != count):
                _fail("catalogue checkpoint has an incomplete list")
            if step == "detail" and state["index"] != len(records):
                _fail("catalogue checkpoint skips a detail")
    if state["done"] and (set(state["counts"]) != set(registry)
            or any(state["counts"][kind] != len(state["data"]["catalogues"][kind]) for kind in registry)):
        _fail("incomplete catalogue checkpoint marked done")


def advance_catalogues(client, state, limits=None, max_nodes=20):
    """At most max_nodes native reads; failed batches never mutate input state."""
    limits = limits or CatalogueLimits()
    if type(max_nodes) is not int or not 1 <= max_nodes <= 1000:
        _fail("invalid catalogue batch size")
    registry = _registry(state.get("dataset") if isinstance(state, dict) else None)
    candidate = deepcopy(state)
    _validate(candidate, registry)
    if _policy(limits) != candidate["limits"]:
        _fail("catalogue limits changed during collection")
    if candidate["done"]:
        return candidate
    started = time.monotonic()
    usage, policy = candidate["usage"], candidate["limits"]
    kinds = list(registry)
    allowed = {kind + suffix for kind in kinds for suffix in ("/form.php", "/component/showdata.php")}

    def read(kind, form=False, identity=None, page=None):
        path = kind + ("/form.php" if form else "/component/showdata.php")
        if path not in allowed:
            _fail("unexpected catalogue endpoint")
        if form:
            params = {"status": "n"} if registry[kind].get("singleton") else {"status": "e", "id": _id(identity)}
        else:
            if registry[kind].get("singleton"):
                _fail("unexpected singleton list request")
            params = dict(LIST_PARAMS)
            if page is not None:
                params.update(sdtamt=str(policy["page_size"]), sdtpage=str(page))
        usage["requests"] += 1
        if usage["requests"] > policy["max_requests"]:
            _fail("catalogue total request budget exceeded")
        try:
            body = client._post_text(path, params)
        except Exception:
            # Native errors can contain HTML/credentials. Keep neither their
            # body nor chained exception in a persisted task error or log.
            _retry("catalogue transport failed")
        elapsed = time.monotonic() - started
        if elapsed > limits.max_seconds:
            # 只是这一步的时间租约用完，断点还能接着读。
            _retry("catalogue read lease exceeded")
        if usage["seconds"] + elapsed > policy["max_total_seconds"]:
            _fail("catalogue time budget exceeded")
        if not isinstance(body, str):
            _fail("non-text catalogue response")
        size = len(body.encode("utf-8"))
        usage["bytes"] += size
        if size > policy["max_response_bytes"] or usage["bytes"] > policy["max_total_bytes"]:
            _fail("catalogue response budget exceeded")
        if body.lstrip().startswith("err::"):
            _fail("native catalogue read rejected")
        return body

    def count(body):
        match = re.fullmatch(r"dt::(0|[1-9][0-9]{0,8})", body.strip())
        if not match:
            _fail("invalid native catalogue count")
        value = int(match[1])
        if value > policy["max_rows"] or math.ceil(value / policy["page_size"]) > policy["max_pages"]:
            _fail("native catalogue count exceeds bound")
        return value

    def next_kind():
        candidate["cursor"] += 1
        candidate.update(step="start", page=1, index=0)
        if candidate["cursor"] == len(kinds):
            candidate["cursor"] = 0
            if candidate["phase"] == "collect":
                candidate["phase"] = "verify"
            else:
                candidate.update(phase="done", done=True)

    reads = 0
    while not candidate["done"] and reads < max_nodes:
        elapsed = time.monotonic() - started
        if reads and limits.max_seconds - elapsed <= min(5, limits.max_seconds / 10):
            break
        kind = kinds[candidate["cursor"]]
        spec, phase, step = registry[kind], candidate["phase"], candidate["step"]
        ids, records = candidate["ids"][kind], candidate["data"]["catalogues"][kind]
        if step == "start":
            candidate["step"] = "singleton" if spec.get("singleton") else "count"
            continue
        if step == "singleton":
            record = _parse_form(read(kind, form=True, identity="singleton"), spec, "singleton")
            reads += 1
            if phase == "collect":
                ids.append("singleton"); records["singleton"] = record
                candidate["counts"][kind] = 1
                candidate["count"] += 1
            elif records.get("singleton") != record:
                _fail("singleton catalogue changed during collection")
            next_kind()
        elif step == "count":
            value = count(read(kind)); reads += 1
            if phase == "collect":
                candidate["counts"][kind] = value
            elif value != candidate["counts"].get(kind):
                _fail("catalogue count changed during collection")
            candidate["step"] = "page" if value else "end_count"
        elif step == "page":
            value = candidate["counts"][kind]
            page = candidate["page"]
            offset = (page - 1) * policy["page_size"]
            expected = min(policy["page_size"], value - offset)
            if expected <= 0:
                _fail("catalogue checkpoint page exceeds count")
            got = _page_ids(read(kind, page=page), expected); reads += 1
            usage["rows"] += len(got)
            if phase == "collect":
                if set(got) & set(ids):
                    _fail("catalogue list repeats an earlier page")
                ids.extend(got)
            elif got != ids[offset:offset + expected]:
                _fail("catalogue identities changed during collection")
            candidate["page"] += 1
            if offset + expected == value:
                candidate["step"] = "end_count"
        elif step == "end_count":
            value = count(read(kind)); reads += 1
            if value != candidate["counts"][kind]:
                _fail("catalogue count changed during pagination")
            if phase == "collect" and value:
                candidate["step"] = "detail"
            else:
                next_kind()
        elif step == "detail" and phase == "collect":
            index = candidate["index"]
            if index >= len(ids) or index != len(records):
                _fail("catalogue checkpoint skips a detail")
            identity = ids[index]
            records[identity] = _parse_form(read(kind, form=True, identity=identity), spec, identity)
            reads += 1
            candidate["index"] += 1
            candidate["count"] += 1
            if candidate["index"] == len(ids):
                next_kind()
        else:
            _fail("invalid catalogue checkpoint step")
        candidate["processed"] = candidate["count"]
        candidate["total"] = sum(candidate["counts"].values()) if len(candidate["counts"]) == len(kinds) else None
        if candidate["count"] > policy["max_rows"] or usage["rows"] > 2 * policy["max_rows"]:
            _fail("catalogue total row budget exceeded")
    usage["seconds"] += time.monotonic() - started
    _validate(candidate, registry)
    return candidate
