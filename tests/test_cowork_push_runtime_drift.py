"""Zero-cloud, zero-DB regression checks for the narrow v1 drift investigation."""
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ROOT = Path(__file__).resolve().parents[1]
drift = load("cowork_push_runtime_drift", ROOT / "scripts/cowork-push-runtime-drift-audit.py")
source_tests = load("drift_source_fixtures", ROOT / "tests/test_cowork_migration_baseline.py")
runtime_tests = load("drift_runtime_fixtures", ROOT / "tests/test_cowork_push_runtime_baseline.py")


def projection(data_type="timestamp with time zone"):
    return {
        "v1_identity": {"count": 2, "configuration_digest": "a" * 32},
        "v2_identity": {"count": 2, "configuration_digest": "b" * 32},
        "adapters": [{"adapter": "express", "count": 1}, {"adapter": "mrerp_dms", "count": 1}],
        "updated_at_present": data_type is not None, "updated_at_type": data_type,
        "updates_since_cutoff": [
            {"adapter": "express", "updated_count": 1,
             "earliest_update": "2026-01-02T00:00:00+00:00", "latest_update": "2026-01-02T00:00:00+00:00"},
            {"adapter": "mrerp_dms", "updated_count": 0, "earliest_update": None, "latest_update": None},
        ] if data_type == "timestamp with time zone" else None,
        "updated_at_authoritative": False,
    }


class Cursor:
    def __init__(self, data_type="timestamp with time zone"):
        self.data = projection(data_type)
        self.calls = []
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if sql.startswith("SET LOCAL"):
            return
        if sql == drift.V1_SQL:
            rows = [self.data["v1_identity"]]
        elif "'config',e.config" in sql:
            rows = [self.data["v2_identity"]]
        elif "information_schema" in sql:
            rows = [{"present": self.data["updated_at_present"], "data_type": self.data["updated_at_type"]}]
        elif "updated_count" in sql:
            rows = deepcopy(self.data["updates_since_cutoff"])
            for row in rows:
                for key in ("earliest_update", "latest_update"):
                    if row[key] is not None:
                        row[key] = datetime.fromisoformat(row[key])
        else:
            rows = self.data["adapters"]
        self.description = [(key,) for key in rows[0]]
        self.rows = [tuple(row.values()) for row in rows]
    def fetchall(self):
        return self.rows


class Connection:
    def __init__(self, cursor=None):
        self.value = cursor or Cursor()
        self.rolled_back = self.closed = False
    def set_session(self, **kwargs):
        self.options = kwargs
    def cursor(self):
        return self.value
    def rollback(self):
        self.rolled_back = True
    def close(self):
        self.closed = True


class DriftTests(unittest.TestCase):
    def test_v1_sql_exact_original_creation_patch_hash_and_order(self):
        # From the original v1 Add File tool payload, not guessed from v2 or
        # reserialized from a JSON equivalent. It intentionally omits user_id
        # and express_root; those fields cannot inherit this gate.
        self.assertEqual(hashlib.sha256(drift.V1_SQL.encode()).hexdigest(),
                         "9c215511d04f442259f459c926ec3ed8047521318d3774683a53373ccd0c82c7")
        self.assertEqual(drift.V1_KEYS, ("agent_token_hash", "method", "account_set", "account_dir", "account_root",
            "system_url", "comidyear", "seldb", "max_payload_version", "companion_version", "mapping"))
        self.assertNotIn("express_root", drift.V1_SQL)
        self.assertNotIn("user_id", drift.V1_SQL)

    def test_database_is_readonly_parameterized_and_closed(self):
        connection = Connection()
        fake = types.SimpleNamespace(connect=lambda *a, **kw: connection)
        with patch.dict(sys.modules, {"psycopg2": fake}):
            actual = drift.readonly_database({"DATABASE_URL": "PRIVATE"}, "2026-01-01T00:00:00+00:00")
        self.assertEqual(actual, projection())
        self.assertEqual(connection.options, {"readonly": True, "autocommit": False, "isolation_level": "REPEATABLE READ"})
        self.assertTrue(connection.closed and connection.rolled_back)
        self.assertEqual(connection.value.calls[1], (drift.V1_SQL, (list(drift.V1_KEYS),)))
        self.assertTrue(all(sql.startswith(("SELECT", "SET LOCAL statement_timeout")) for sql, _ in connection.value.calls))
        self.assertTrue(all("PRIVATE" not in sql for sql, _ in connection.value.calls))
        self.assertIn("ELSE 'other'", connection.value.calls[3][0])
        self.assertIn("mrerp_dms", connection.value.calls[3][1][0])

    def test_missing_or_naive_timestamp_column_does_not_invent_timezone(self):
        for data_type in (None, "timestamp without time zone", "unsupported"):
            connection = Connection(Cursor(data_type))
            with self.subTest(data_type=data_type), patch.dict(sys.modules,
                    {"psycopg2": types.SimpleNamespace(connect=lambda *a, **kw: connection)}):
                result = drift.readonly_database({"DATABASE_URL": "PRIVATE"}, "2026-01-01T00:00:00+00:00")
            self.assertIsNone(result["updates_since_cutoff"])
            self.assertFalse(any("updated_count" in sql for sql, _ in connection.value.calls))
            self.assertFalse(result["updated_at_authoritative"])

    def test_database_error_is_redacted_and_connection_closed(self):
        connection = Connection()
        connection.value.execute = lambda *a, **kw: (_ for _ in ()).throw(ValueError("PRIVATE_DSN_CUSTOMER"))
        with patch.dict(sys.modules, {"psycopg2": types.SimpleNamespace(connect=lambda *a, **kw: connection)}):
            with self.assertRaisesRegex(drift.Error, "^database_read_failed$"):
                drift.readonly_database({"DATABASE_URL": "PRIVATE"}, "2026-01-01T00:00:00+00:00")
        self.assertTrue(connection.closed and connection.rolled_back)

    def test_aggregate_projection_rejects_unexpected_values(self):
        for change in (
            lambda v: v["adapters"][0].update(adapter="PRIVATE_CUSTOMER"),
            lambda v: v["v1_identity"].update(configuration_digest="PRIVATE_TOKEN"),
            lambda v: v["v1_identity"].update(token="PRIVATE_TOKEN"),
            lambda v: v.update(updated_at_type="PRIVATE"),
            lambda v: v.update(updated_at_authoritative=True),
            lambda v: v["updates_since_cutoff"][0].update(earliest_update="PRIVATE"),
            lambda v: v["updates_since_cutoff"][0].update(updated_count=20),
            lambda v: v["adapters"][0].update(count=True),
        ):
            value = projection()
            change(value)
            with self.assertRaisesRegex(drift.Error, "^database_projection_invalid$"):
                drift.validate_projection(value)

    def fixture_files(self, directory):
        body = json.loads(json.dumps(source_tests.fixture()).replace("worker", "pearnly-worker").replace("web", "pearnly-web"))
        body["ledger"]["excerpt_sha256"] = drift.gate.digest(body["ledger"]["excerpt"])
        self.raw = {}
        for name, component in body["components"].items():
            component["revision"]["spec"]["containers"][0]["env"].append({"name": "PEARNLY_RUNTIME_ENV_FILE"})
            component["configuration"] = drift.gate.configuration(component["revision"])
            raw = deepcopy(component["revision"])
            raw["spec"]["containers"][0]["env"][-1]["value"] = "/secrets/runtime.env"
            self.raw[name] = raw
        source = {"evidence": body, "sha256": drift.gate.digest(body)}
        old = runtime_tests.PushRuntimeBaselineTests().envelope()
        old["evidence"]["schema"] = drift.runtime.LEGACY_SCHEMA
        old["evidence"]["captured_at"] = "2026-01-01T00:00:00+00:00"
        for component in old["evidence"]["components"].values():
            component["database"]["identity"]["missing_columns"]["erp_endpoints"] = []
            component["database"]["identity"]["endpoints"] = projection()["v1_identity"]
            component["database"]["observations"]["endpoints"] = []
        old["sha256"] = drift.runtime.digest(old["evidence"])
        previous = deepcopy(old)
        previous["evidence"]["schema"] = drift.runtime.SCHEMA
        previous["evidence"]["captured_at"] = "2026-01-02T00:00:00+00:00"
        for component in previous["evidence"]["components"].values():
            component["database"]["identity"]["endpoints"]["configuration_digest"] = "c" * 32
        previous["sha256"] = drift.runtime.digest(previous["evidence"])
        paths = [Path(directory) / (name + ".json") for name in ("source", "old", "previous")]
        for path, value in zip(paths, (source, old, previous)):
            path.write_text(json.dumps(value))
        self.services = {name: component["service"] for name, component in body["components"].items()}
        return paths

    def command(self, args):
        if args[1:3] == ["secrets", "versions"]:
            self.assertEqual(args[4], "4")
            return "PRIVATE_SECRET"
        if args[2] == "services":
            return json.dumps(self.services[args[4]])
        return json.dumps(self.raw[args[4].removesuffix("-live")])

    def collect_fake(self, paths, database=None, command=None):
        with patch.object(drift.runtime, "command", side_effect=command or self.command), \
             patch.object(drift.runtime, "merge_environment", return_value={"DATABASE_URL": "PRIVATE"}), \
             patch.object(drift, "readonly_database", side_effect=database or (lambda *a: projection())):
            return drift.collect(*paths)

    def test_same_v1_does_not_hide_current_v2_change_and_secret_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.collect_fake(self.fixture_files(directory))
        self.assertEqual(result["sha256"], drift.runtime.digest(result["evidence"]))
        for component in result["evidence"]["components"].values():
            self.assertTrue(component["legacy_v1_unchanged"])
            self.assertNotEqual(component["v1_identity"], component["v2_identity"])
        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_v1_mismatch_is_recorded_without_claiming_gate_pass(self):
        def database(*args):
            value = projection()
            value["v1_identity"]["configuration_digest"] = "d" * 32
            return value
        with tempfile.TemporaryDirectory() as directory:
            result = self.collect_fake(self.fixture_files(directory), database)
        self.assertTrue(all(not c["legacy_v1_unchanged"] for c in result["evidence"]["components"].values()))

    def test_end_traffic_drift_rejects_after_both_database_reads(self):
        calls = []
        def command(args):
            if args[2] == "services":
                calls.append(args[4])
                if len(calls) == 3:
                    self.services[args[4]]["status"]["traffic"][0]["revisionName"] = "other-live"
            return self.command(args)
        with tempfile.TemporaryDirectory() as directory:
            paths = self.fixture_files(directory)
            with self.assertRaisesRegex(drift.Error, "^source_traffic_changed$"):
                self.collect_fake(paths, command=command)

    def test_two_components_must_have_same_snapshot_projection(self):
        values = [projection(), projection()]
        values[1]["v2_identity"]["configuration_digest"] = "d" * 32
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(drift.Error, "^component_database_identity_changed$"):
                self.collect_fake(self.fixture_files(directory), values)

    def test_tampered_input_is_rejected_before_cloud_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = self.fixture_files(directory)
            body = json.loads(paths[0].read_text())
            body["sha256"] = "0" * 64
            paths[0].write_text(json.dumps(body))
            with patch.object(drift.runtime, "command") as command:
                with self.assertRaisesRegex(drift.Error, "^source_checksum_invalid$"):
                    drift.collect(*paths)
                command.assert_not_called()

    def test_existing_output_is_never_overwritten_or_recollected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.json"
            path.write_text("original")
            argv = ["drift", "collect-readonly", "--baseline", "x", "--legacy-v1", "y", "--previous-runtime", "z", "--output", str(path)]
            with patch.object(sys, "argv", argv), patch.object(drift, "collect") as collect, redirect_stdout(io.StringIO()):
                self.assertEqual(drift.main(), 1)
            self.assertEqual(path.read_text(), "original")
            collect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
