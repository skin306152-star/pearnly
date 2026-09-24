"""Pure native catalogue collection, real DmsClient and fake outer HTTP only.

No credentials, Site, database, browser, requests or external writes. Every value
below is synthetic. Run PYTHONDONTWRITEBYTECODE=1 python3 tests/test_dms_catalogue_reader.py.
"""
from copy import deepcopy
from dataclasses import replace
from html import escape
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/pearnly_dms"))

from pearnly_dms.client.catalogue_reader import (
    CatalogueLimits, LIST_PARAMS, _parse_form, _registry, advance_catalogues, start_catalogues,
)
from pearnly_dms.client.catalogue_schema import REGISTRY, LOAN_COLUMNS
from pearnly_dms.client.gateway import DmsClient
from pearnly_dms.contracts.client_base import DmsClientError

LIMITS = CatalogueLimits(page_size=2, max_pages=100, max_rows=50000, max_requests=2000, max_seconds=30)


def inp(name, value, kind="text", checked=False):
    return '<input name="{}" type="{}" value="{}"{}>'.format(
        escape(name, quote=True), kind, escape(str(value), quote=True), " checked" if checked else "")


def form(spec, identity):
    context = inp("status", "e", "hidden") if spec.get("singleton") else (
        inp("stsel", "e", "hidden") + inp("idsel", identity, "hidden"))
    parts = ["<form><div>", context]
    for name in sorted(spec["fields"]):
        if name in spec.get("repeat_fields", ()):
            continue
        if name in spec.get("arrays", ()):
            parts.extend(inp(name, value, "checkbox", checked=checked)
                         for value, checked in (("1", True), ("2", False), ("3", True)))
        elif name in spec.get("radios", ()):
            parts.extend((inp(name, "A", "radio", True), inp(name, "B", "radio")))
        elif name in spec.get("checkboxes", ()):
            parts.append(inp(name, "1", "checkbox", True))
        elif name in spec.get("selects", ()):
            parts.append('<select name="{}"><option value="0">Active</option>'
                         '<option value="1" selected>Cancelled</option></select>'.format(name))
        elif name in spec.get("textareas", ()):
            parts.append('<textarea name="{}">Synthetic &amp; note\nsecond line</textarea>'.format(name))
        else:
            parts.append(inp(name, "Synthetic-" + name))
    for name in spec.get("excluded", ()) - {"stsel", "idsel", "status"}:
        parts.append(inp(name, "DO-NOT-CACHE-SYNTHETIC-SECRET", "password" if "password" in name else "hidden"))
    for region, columns in spec.get("regions", {}).items():
        parts.append('<div id="{}">'.format(region))
        for index in range(2 if columns else 0):
            parts.extend(inp(name, "R" + str(index) + "-" + name) for name in columns)
        if region == (spec.get("indexed") or {}).get("region"):
            # Native deleted-group gaps must survive; IDs are not renumbered.
            for group in ("1", "3"):
                if region == 'showcartype':
                    parts.append(campaign_group(group, checked_all=group == '1'))
                    continue
                for base in spec["indexed"]["parents"]:
                    parts.append(inp(base + "[" + group + "]", "Finance-" + group))
                for index in range(2):
                    parts.extend(inp(base + "[" + group + "][]", str(index + 1)) for base in LOAN_COLUMNS)
        parts.append("</div>")
    return "".join(parts) + "</div></form>"


def page(ids):
    return "dt::" + "".join('<div onclick="ctllistdata(this);" data-val="{}"><div><p>Synthetic</p></div></div>'.format(value)
                            for value in ids)


def campaign_group(index="1", *, checked_all=True, children=True):
    # The real edit form nests these regions and omits cbctall's value attribute.
    return ('<div id="listcartype{0}"><div id="listcartypedt{0}">'.format(index)
            + inp('typecarval[' + index + ']', 'Type-' + index, 'hidden')
            + inp('txttypecar[' + index + ']', 'Synthetic type ' + index)
            + '<input type="checkbox" name="cbctall[{}]"{}>'.format(index, ' checked' if checked_all else '')
            + '<div id="listsubcartype{}">'.format(index)
            + (''.join(inp('cbct[' + index + '][]', value, 'checkbox', checked)
                       for value, checked in (("11", True), ("12", False), ("13", True))) if children else '')
            + '</div></div></div>')


def campaign_form(children):
    spec = dict(REGISTRY['carcampaign'])
    spec.pop('regions', None)
    spec.pop('indexed', None)
    return form(spec, '1').replace('</div></form>', '<div id="showcartype">' + children + '</div></div></form>')


class Transport:
    def __init__(self, dataset="vehicles", records=None):
        self.registry = _registry(dataset)
        self.records = {kind: list((records or {}).get(kind, ("1",))) for kind, spec in self.registry.items()
                        if not spec.get("singleton")}
        self.calls = []
        self.override = None
        self.counts = {}

    def post(self, url, *, data, **kwargs):
        path = url.removeprefix("https://catalogue.invalid/dms/")
        self.calls.append((path, dict(data)))
        kind = path.split("/", 1)[0]
        assert kind in self.registry, "Unregistered endpoint"
        if path == kind + "/component/showdata.php":
            ids = self.records[kind]
            if "sdtamt" not in data:
                assert data == LIST_PARAMS
                self.counts[kind] = self.counts.get(kind, 0) + 1
                body = "dt::" + str(len(ids))
            else:
                assert set(data) == set(LIST_PARAMS) | {"sdtamt", "sdtpage"}
                offset = (int(data["sdtpage"]) - 1) * int(data["sdtamt"])
                body = page(ids[offset:offset + int(data["sdtamt"])])
        elif path == kind + "/form.php":
            spec = self.registry[kind]
            assert data == ({"status": "n"} if spec.get("singleton") else {"status": "e", "id": data["id"]})
            body = form(spec, data.get("id", "singleton"))
        else:
            raise AssertionError("Attempted unregistered/write endpoint")
        if self.override:
            body = self.override(path, data, body)
        return SimpleNamespace(status_code=200, text=body)


def setup(dataset="vehicles", records=None):
    transport = Transport(dataset, records)
    return DmsClient(transport, "https://catalogue.invalid/dms/"), transport


def collect(client, dataset="vehicles", *, limits=LIMITS, nodes=3):
    state = start_catalogues(client, dataset, limits)
    for _ in range(1000):
        state = advance_catalogues(client, state, limits, max_nodes=nodes)
        if state["done"]:
            return state
    raise AssertionError("Collector did not terminate")


class CatalogueTests(unittest.TestCase):
    def setUp(self):
        self.no_network = patch.object(socket.socket, "connect", side_effect=AssertionError("External network forbidden"))
        self.no_network.start()
        self.addCleanup(self.no_network.stop)

    def rejected(self, client, dataset="vehicles", **kwargs):
        with self.assertRaises(DmsClientError) as raised:
            collect(client, dataset, **kwargs)
        self.assertEqual(raised.exception.error_code, "ERR_DMS_SNAPSHOT_INCOMPLETE")
        self.assertNotIn("Synthetic", str(raised.exception))

    def test_complete_vehicles_preserve_cancelled_full_controls_and_children(self):
        client, transport = setup()
        state = collect(client)
        self.assertEqual(state["phase"], "done")
        self.assertEqual(state["count"], len(REGISTRY))
        self.assertEqual(state["processed"], state["total"])
        self.assertEqual(set(state["data"]["catalogues"]), set(REGISTRY))
        car = state["data"]["catalogues"]["car"]["1"]
        self.assertEqual(car["fields"]["selstatuscancel"], "1")
        self.assertEqual(car["labels"]["selstatuscancel"], "Cancelled")
        self.assertEqual(car["fields"]["chkcarpaint[]"], ["1", "3"])
        self.assertEqual(len(car["fields"]["txtfbproduct[]"]), 2)
        self.assertEqual(car["fields"]["txtslsdperiod[3][]"], ["1", "2"])
        self.assertNotIn("stsel", car["fields"])
        self.assertNotIn("idsel", car["fields"])
        self.assertEqual(state["data"]["catalogues"]["ctgrcampaign"]["1"]["fields"]["tanote"],
                         "Synthetic & note\nsecond line")
        self.assertTrue(all(value == 4 for value in transport.counts.values()))
        self.assertTrue(all(path.endswith(("/form.php", "/component/showdata.php")) for path, _ in transport.calls))

    def test_start_has_no_io_and_small_batches_equal_one_large_batch(self):
        client, transport = setup(records={"car": ["1", "2", "3"]})
        state = start_catalogues(client, "vehicles", LIMITS)
        self.assertFalse(transport.calls)
        original = deepcopy(state)
        out = advance_catalogues(client, state, LIMITS, max_nodes=1)
        self.assertEqual(state, original)
        self.assertEqual(len(transport.calls), 1)
        self.assertFalse(out["done"])
        first = collect(client, nodes=1)
        other, _ = setup(records={"car": ["1", "2", "3"]})
        second = collect(other, nodes=1000)
        self.assertEqual(first["data"], second["data"])
        self.assertEqual(first["counts"], second["counts"])

    def test_all_empty_tables_require_authoritative_zero_counts(self):
        client, transport = setup(records={kind: [] for kind in REGISTRY})
        state = collect(client)
        self.assertEqual(state["count"], 1)  # one native singleton
        self.assertFalse(any("sdtamt" in params for _, params in transport.calls))
        self.assertTrue(all(value == 4 for value in transport.counts.values()))

    def test_unknown_or_noncanonical_count_rejected(self):
        for value in ("", "ndt::", "dt::01", "dt::-1", "<html>Login</html>"):
            with self.subTest(value=value):
                client, transport = setup()
                transport.override = lambda path, data, body: value if path.endswith("showdata.php") and "sdtamt" not in data else body
                self.rejected(client)

    def test_partial_repeated_malformed_page_rejected(self):
        for value in (page(["1"]), page(["1", "1"]), page(["1", "2"]) + '<div data-val="3"'):
            with self.subTest(value=value):
                client, transport = setup(records={"car": ["1", "2", "3"]})
                transport.override = lambda path, data, body: value if path == "car/component/showdata.php" and "sdtamt" in data else body
                self.rejected(client)

    def test_final_changed_count_or_ids_rejected(self):
        for changed_count in (True, False):
            with self.subTest(changed_count=changed_count):
                client, transport = setup(records={"car": ["1", "2", "3"]})
                def change(path, data, body):
                    if path == "car/component/showdata.php" and transport.counts.get("car", 0) >= 3:
                        if changed_count and "sdtamt" not in data:
                            return "dt::4"
                        if not changed_count and data.get("sdtpage") == "1":
                            return page(["2", "1"])
                    return body
                transport.override = change
                self.rejected(client)

    def test_missing_unknown_duplicate_or_secret_control_rejected(self):
        changes = (
            lambda body: body.replace('name="txtcarbrandcode"', 'name="unexpected"'),
            lambda body: body.replace('</form>', inp('txtcarbrandcode', 'extra') + '</form>'),
            lambda body: body.replace('</form>', inp('password', 'never-copy', 'password') + '</form>'),
            lambda body: body.replace('name="txtcarbrandcode" type="text"', 'name="txtcarbrandcode" type="password"'),
        )
        for change in changes:
            with self.subTest(change=change):
                client, transport = setup()
                transport.override = lambda path, data, body: change(body) if path == "carbrand/form.php" else body
                self.rejected(client)

    def test_wrong_record_id_and_empty_form_rejected(self):
        for change in (lambda body: body.replace('name="idsel" type="hidden" value="1"', 'name="idsel" type="hidden" value="2"'), lambda body: ""):
            client, transport = setup()
            transport.override = lambda path, data, body: change(body) if path == "carbrand/form.php" else body
            self.rejected(client)

    def test_child_region_or_column_loss_rejected(self):
        for change in (
            lambda body: body.replace('id="showfreebies"', 'id="missing"'),
            lambda body: body.replace('name="txtfbunits[]"', 'name="txtfbunits_lost[]"', 1),
            lambda body: body.replace(inp('txtslsdperiod[1][]', '1'), ''),
            lambda body: body.replace(inp('lsdfinanceval[3]', 'Finance-3'), ''),
        ):
            client, transport = setup()
            transport.override = lambda path, data, body: change(body) if path == "car/form.php" else body
            self.rejected(client)

    def test_empty_child_regions_are_valid_without_invented_rows(self):
        import re
        client, transport = setup()
        transport.override = lambda path, data, body: re.sub(r'(<div id="show(?:freebies|carcampaign|loanschedule)">).*?(</div>)',
            r'\1\2', body) if path == "car/form.php" else body
        record = collect(client)["data"]["catalogues"]["car"]["1"]
        self.assertEqual(record["fields"]["txtfbunits[]"], [])
        self.assertFalse(any('lsdfinance' in name for name in record["fields"]))

    def test_failed_batch_does_not_mutate_input_checkpoint(self):
        client, transport = setup()
        original = start_catalogues(client, "vehicles", LIMITS)
        before = deepcopy(original)
        transport.override = lambda path, data, body: "" if path == "car/form.php" else body
        with self.assertRaises(DmsClientError):
            advance_catalogues(client, original, LIMITS, max_nodes=10)
        self.assertEqual(original, before)

    def test_total_request_bytes_and_pages_limits_survive_batches(self):
        for limits in (replace(LIMITS, max_requests=8), replace(LIMITS, max_total_bytes=1000),
                       replace(LIMITS, max_response_bytes=100), replace(LIMITS, max_pages=1)):
            with self.subTest(limits=limits):
                client, _ = setup(records={"car": ["1", "2", "3"]})
                self.rejected(client, limits=limits, nodes=1)

    def test_checkpoint_missing_detail_done_or_schema_change_rejected(self):
        client, _ = setup()
        state = collect(client)
        for change in (lambda item: item["data"]["catalogues"]["car"].clear(),
                       lambda item: item.update(schema="old"),
                       lambda item: item["data"]["catalogues"]["car"]["1"]["fields"].pop("txtmodelcode")):
            bad = deepcopy(state); change(bad)
            with self.assertRaises(DmsClientError):
                advance_catalogues(client, bad, LIMITS)

    def test_company_and_customer_aux_exclude_passwords_and_keep_native_semantics(self):
        for dataset in ("company", "customer_aux", "geo_aux"):
            with self.subTest(dataset=dataset):
                client, _ = setup(dataset)
                state = collect(client, dataset)
                self.assertNotIn("DO-NOT-CACHE-SYNTHETIC-SECRET", str(state))
                self.assertTrue(state["done"])
                if dataset == "company":
                    self.assertEqual(state["data"]["catalogues"]["setcbc"]["singleton"]["fields"]["cbhidefreebiesprice"], "1")

    def test_unknown_dataset_cannot_select_an_arbitrary_path(self):
        client, transport = setup()
        for name in ("../car", "users/edit.php", "arbitrary"):
            with self.assertRaises(DmsClientError):
                start_catalogues(client, name, LIMITS)
        self.assertFalse(transport.calls)

    def test_optional_user_status_is_absent_not_fabricated_and_unchecked_radio_is_empty(self):
        import re
        client, transport = setup("company")
        def change(path, data, body):
            if path != "users/form.php":
                return body
            body = re.sub(r'<select name="selstatususers">.*?</select>', '', body)
            return body.replace(inp('chkteamrdo', 'A', 'radio', True), inp('chkteamrdo', 'A', 'radio'))
        transport.override = change
        fields = collect(client, "company")["data"]["catalogues"]["users"]["1"]["fields"]
        self.assertNotIn("selstatususers", fields)
        self.assertEqual(fields["chkteamrdo"], "")
        self.assertNotIn("selstatususersgrant", fields)

    def test_radio_double_checked_and_unknown_singleton_context_rejected(self):
        for target, change in (
            ('users/form.php', lambda body: body.replace(inp('chkteamrdo', 'B', 'radio'), inp('chkteamrdo', 'B', 'radio', True))),
            ('setcbc/form.php', lambda body: body.replace(inp('status', 'e', 'hidden'), inp('status', 'delete', 'hidden'))),
        ):
            client, transport = setup("company")
            transport.override = lambda path, data, body: change(body) if path == target else body
            self.rejected(client, "company")

    def test_cumulative_time_limit_is_checked_before_any_more_io(self):
        client, transport = setup()
        limits = replace(LIMITS, max_total_seconds=1)
        state = start_catalogues(client, "vehicles", limits)
        state["usage"]["seconds"] = 1.1
        with self.assertRaises(DmsClientError):
            advance_catalogues(client, state, limits)
        self.assertFalse(transport.calls)

    def test_snapshot_limits_input_remains_supported(self):
        from pearnly_dms.client.snapshot_reader import SnapshotLimits
        client, _ = setup()
        limits = SnapshotLimits(page_size=2, max_seconds=30)
        state = collect(client, limits=limits)
        self.assertTrue(state["done"])
        self.assertEqual(state["limits"]["max_total_seconds"], 1800)

    def test_transport_exception_does_not_expose_native_error_body(self):
        import traceback
        client, transport = setup()
        def fail(path, data, body):
            raise RuntimeError("DO-NOT-LOG-SYNTHETIC-CREDENTIAL")
        transport.override = fail
        try:
            collect(client)
        except DmsClientError:
            rendered = traceback.format_exc()
        else:
            self.fail("Expected a failed read")
        self.assertNotIn("DO-NOT-LOG-SYNTHETIC-CREDENTIAL", rendered)

    def test_transport_failure_is_resumable_not_a_lost_generation(self):
        """传输抖动报可续读的码，调用方据此**保留**断点。

        2026-09-25：这个分支以前和其余失败共用一个码，而失败路径见到那个码就把整组
        断点清掉重来 —— 数据量大的租户一组要跑很多步，于是永远读不完。
        """
        client, transport = setup()
        def fail(path, data, body):
            raise RuntimeError("synthetic transport failure")
        transport.override = fail
        with self.assertRaises(DmsClientError) as caught:
            collect(client)
        self.assertEqual(caught.exception.error_code, "ERR_DMS_SNAPSHOT_RETRY")

    def test_campaign_applicable_models_preserve_group_ids_and_checkbox_selection(self):
        record = _parse_form(campaign_form(campaign_group() + campaign_group('3', checked_all=False)), REGISTRY['carcampaign'], '1')
        fields = record['fields']
        self.assertEqual(fields['typecarval[1]'], 'Type-1')
        self.assertEqual(fields['txttypecar[3]'], 'Synthetic type 3')
        self.assertEqual(fields['cbctall[1]'], 'on')
        self.assertEqual(fields['cbctall[3]'], '')
        self.assertEqual(fields['cbct[1][]'], ['11', '13'])
        self.assertEqual(fields['cbct[3][]'], ['11', '13'])
        self.assertNotIn('typecarval[2]', fields)

    def test_campaign_empty_region_and_zero_child_options_are_distinct_valid_states(self):
        empty = _parse_form(campaign_form(''), REGISTRY['carcampaign'], '1')['fields']
        self.assertFalse(any(name.startswith('cbct') for name in empty))
        group = _parse_form(campaign_form(campaign_group(children=False)), REGISTRY['carcampaign'], '1')['fields']
        self.assertEqual(group['cbctall[1]'], 'on')
        self.assertEqual(group['cbct[1][]'], [])

    def test_campaign_group_missing_or_duplicate_parent_checkbox_is_rejected(self):
        original = campaign_form(campaign_group())
        for body in (
            original.replace(inp('typecarval[1]', 'Type-1', 'hidden'), ''),
            original.replace(inp('txttypecar[1]', 'Synthetic type 1'), ''),
            original.replace('<input type="checkbox" name="cbctall[1]" checked>', ''),
            original.replace('<input type="checkbox" name="cbctall[1]" checked>', '<input type="checkbox" name="cbctall[1]" checked>' * 2),
            original.replace(inp('cbct[1][]', '12', 'checkbox'), inp('cbct[1][]', '11', 'checkbox')),
        ):
            with self.subTest(body_length=len(body)), self.assertRaises(DmsClientError):
                _parse_form(body, REGISTRY['carcampaign'], '1')

    def test_campaign_unknown_or_mistyped_child_cannot_widen_loan_parser(self):
        original = campaign_form(campaign_group())
        for body in (
            original.replace('name="cbct[1][]"', 'name="unexpected[1][]"', 1),
            original.replace(inp('cbct[1][]', '12', 'checkbox'), inp('cbct[1][]', '12', 'text')),
            original.replace('id="showcartype"', 'id="missing"'),
        ):
            with self.subTest(body_length=len(body)), self.assertRaises(DmsClientError):
                _parse_form(body, REGISTRY['carcampaign'], '1')
        body = form(REGISTRY['car'], '1').replace(inp('txtslsdperiod[1][]', '1'), inp('txtslsdperiod[1][]', '1', 'checkbox'))
        with self.assertRaises(DmsClientError):
            _parse_form(body, REGISTRY['car'], '1')

    def test_checked_controls_without_value_use_browser_on_not_empty(self):
        from pearnly_dms.client.company_catalogue_schema import REGISTRY as COMPANY
        for kind, name, control_type in (('setcbc', 'cbhidefreebiesprice', 'checkbox'), ('users', 'chkteamrdo', 'radio')):
            spec = COMPANY[kind]
            identity = 'singleton' if spec.get('singleton') else '1'
            body = form(spec, identity)
            old = inp(name, '1' if control_type == 'checkbox' else 'A', control_type, True)
            body = body.replace(old, '<input name="{}" type="{}" checked>'.format(name, control_type))
            self.assertEqual(_parse_form(body, spec, identity)['fields'][name], 'on')

    def test_campaign_checkpoint_requires_complete_typed_groups(self):
        client, transport = setup()
        state = collect(client, nodes=1)
        fields = state['data']['catalogues']['carcampaign']['1']['fields']
        self.assertEqual(fields['cbctall[1]'], 'on')
        self.assertEqual(fields['cbctall[3]'], '')
        self.assertEqual(fields['cbct[3][]'], ['11', '13'])
        calls = len(transport.calls)
        for change in (
            lambda item: item.pop('cbctall[1]'),
            lambda item: item.update({'cbct[1][]': '11'}),
            lambda item: item.update({'cbct[1][]': ['11', '11']}),
            lambda item: item.update({'cbctall[1]': ['on']}),
        ):
            bad = deepcopy(state)
            change(bad['data']['catalogues']['carcampaign']['1']['fields'])
            with self.assertRaises(DmsClientError):
                advance_catalogues(client, bad, LIMITS)
        self.assertEqual(len(transport.calls), calls)


if __name__ == "__main__":
    unittest.main(verbosity=2)
