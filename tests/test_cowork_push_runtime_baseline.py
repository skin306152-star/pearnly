"""Zero-cloud, zero-business-DB tests for ERP evidence safety and identity."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("cowork_push_runtime_baseline",
    Path(__file__).resolve().parents[1] / "scripts/cowork-push-runtime-baseline.py")
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


class PushRuntimeBaselineTests(unittest.TestCase):
    def envelope(self):
        database = {"identity": {"missing_columns": {key: sorted(value) for key, value in runtime.TABLES.items()},
            "flags": None, "endpoints": None, "bridges": None},
            "observations": {key: None for key in ("endpoints", "push_logs", "bridges", "bridge_jobs")}}
        body = {"schema": runtime.SCHEMA, "captured_at": "2026-09-21T00:00:00+00:00",
            "source_sha": "a" * 40, "source_baseline_sha256": "b" * 64,
            "components": {name: {"revision": name + "-test", "runtime_secret": {"name": name + "-env", "version": "3"},
                "environment": {}, "presence": dict.fromkeys(runtime.PRESENCE_KEYS, False),
                "database": deepcopy(database)} for name in ("pearnly-web", "pearnly-worker")}}
        return {"evidence": body, "sha256": runtime.digest(body)}

    def rehash(self, envelope):
        envelope["sha256"] = runtime.digest(envelope["evidence"])

    def test_rolling_observations_do_not_change_setting_identity(self):
        first, last = self.envelope(), self.envelope()
        for envelope in (first, last):
            for component in envelope["evidence"]["components"].values():
                component["database"]["identity"]["missing_columns"]["bridge_jobs"] = []
                component["database"]["observations"]["bridge_jobs"] = []
            self.rehash(envelope)
        last["evidence"]["components"]["pearnly-web"]["database"]["observations"]["bridge_jobs"] = [
            {"kind": "write", "status": "leased", "count": 3, "leased": 2, "expired_lease": 1}]
        self.rehash(last)
        self.assertEqual(runtime.compare(first, last), {"settings_unchanged": True, "observations_changed": True})

    def test_configuration_identity_change_is_blocked(self):
        for field, value in (("environment", {"ERP_PUSH_ENABLED": "1"}),
                ("runtime_secret", {"name": "pearnly-web-env", "version": "4"}),
                ("presence", {**dict.fromkeys(runtime.PRESENCE_KEYS, False), "PEARNLY_KMS_KEY": True})):
            with self.subTest(field=field):
                first, last = self.envelope(), self.envelope()
                last["evidence"]["components"]["pearnly-web"][field] = value
                self.rehash(last)
                with self.assertRaisesRegex(runtime.EvidenceError, "identity_changed"):
                    runtime.compare(first, last)

    def test_full_config_is_hashed_in_database_without_key_projection(self):
        class Cursor:
            description = [("count",), ("configuration_digest",)]
            def execute(self, sql, parameters):
                self.sql, self.parameters = sql, parameters
            def fetchall(self):
                return [(60, "a" * 32)]
        cursor = Cursor()
        result = runtime._endpoint_identity(cursor)
        # Selected-key SQL would silently miss future/nested controls. Retain
        # the complete JSON as an input to the SERVER-side aggregate instead.
        self.assertIn("'config',e.config", cursor.sql)
        self.assertNotIn("unnest", cursor.sql)
        self.assertNotIn("e.config->", cursor.sql)
        self.assertIn("md5(jsonb_build_object(", cursor.sql)
        self.assertIn("ORDER BY e.id", cursor.sql)
        self.assertEqual(cursor.parameters, ())
        self.assertEqual(result, {"count": 60, "configuration_digest": "a" * 32})
        runtime._safe_projection(result)

    def test_full_config_digest_change_blocks_same_count_endpoints(self):
        first = self.envelope()
        for component in first["evidence"]["components"].values():
            database = component["database"]
            database["identity"]["missing_columns"]["erp_endpoints"] = []
            database["identity"]["endpoints"] = {"count": 60, "configuration_digest": "a" * 32}
            database["observations"]["endpoints"] = []
        self.rehash(first)
        last = deepcopy(first)
        for component in last["evidence"]["components"].values():
            component["database"]["identity"]["endpoints"]["configuration_digest"] = "b" * 32
        self.rehash(last)
        with self.assertRaisesRegex(runtime.EvidenceError, "identity_changed"):
            runtime.compare(first, last)

    def test_old_schema_remains_verifiable_but_cannot_be_expanded_retroactively(self):
        first = self.envelope()
        first["evidence"]["schema"] = runtime.LEGACY_SCHEMA
        self.rehash(first)
        self.assertIsNotNone(runtime.validate(first))
        self.assertEqual(runtime.compare(first, first), {"settings_unchanged": True, "observations_changed": False})
        with self.assertRaisesRegex(runtime.EvidenceError, "schema_mismatch"):
            runtime.compare(first, self.envelope())

    def test_unknown_schema_cannot_claim_configuration_coverage(self):
        value = self.envelope()
        value["evidence"]["schema"] = "pearnly-cowork-push-runtime-v999"
        self.rehash(value)
        with self.assertRaisesRegex(runtime.EvidenceError, "incomplete"):
            runtime.validate(value)

    def test_membership_digest_detects_equal_count_configuration_change(self):
        first = self.envelope()
        for component in first["evidence"]["components"].values():
            identity = component["database"]["identity"]
            identity["missing_columns"]["platform_settings"] = []
            identity["missing_columns"]["platform_setting_allowlist"] = []
            identity["flags"] = [{"key": "erp_shared_express_endpoint", "enabled": True,
                "rollout": "allowlist", "allowlist_count": 2, "membership_digest": "a" * 32}]
        self.rehash(first)
        last = deepcopy(first)
        for component in last["evidence"]["components"].values():
            component["database"]["identity"]["flags"][0]["membership_digest"] = "b" * 32
        self.rehash(last)
        with self.assertRaisesRegex(runtime.EvidenceError, "identity_changed"):
            runtime.compare(first, last)

    def test_checksum_tampering_rejected(self):
        value = self.envelope()
        value["evidence"]["source_sha"] = "c" * 40
        with self.assertRaisesRegex(runtime.EvidenceError, "checksum"):
            runtime.validate(value)

    def test_missing_structure_or_secret_latest_rejected(self):
        for mutate in (lambda v: v["evidence"].pop("components"),
                lambda v: v["evidence"]["components"]["pearnly-web"]["database"].pop("identity"),
                lambda v: v["evidence"]["components"]["pearnly-web"]["presence"].clear(),
                lambda v: v["evidence"]["components"]["pearnly-web"]["runtime_secret"].update(version="latest")):
            value = self.envelope()
            mutate(value)
            self.rehash(value)
            with self.assertRaises(runtime.EvidenceError):
                runtime.validate(value)

    def test_missing_tables_are_evidence_not_table_creation(self):
        value = self.envelope()
        self.assertIsNotNone(runtime.validate(value))
        self.assertIn("secret_hash", value["evidence"]["components"]["pearnly-web"]["database"]["identity"]["missing_columns"]["erp_bridges"])

    def test_self_hashed_partial_identity_or_observation_is_rejected(self):
        for mutate in (
            lambda d: d["identity"].update(endpoints={"count": 4}),
            lambda d: d["identity"]["missing_columns"].update(erp_endpoints=[]),
            lambda d: d["observations"].update(bridge_jobs=[{"status": "leased"}]),
        ):
            value = self.envelope()
            mutate(value["evidence"]["components"]["pearnly-web"]["database"])
            self.rehash(value)
            with self.assertRaises(runtime.EvidenceError):
                runtime.validate(value)

    def test_safe_projection_never_retains_credentials_or_paths(self):
        actual = runtime.safe_environment({"PEARNLY_KMS_KEY": "PRIVATE", "DATABASE_URL": "PRIVATE",
            "ERP_SELLER_ROUTING_USERS": "PRIVATE", "ERP_PUSH_ENABLED": " true ", "ERP_PUSH_TIMEOUT_SEC": "30"})
        self.assertEqual(actual, {"ERP_PUSH_ENABLED": " true ", "ERP_PUSH_TIMEOUT_SEC": "30"})
        for value in ("PRIVATE_SECRET", "C:\\Business\\Account", "https://customer.invalid"):
            with self.assertRaises(runtime.EvidenceError) as raised:
                runtime._safe_projection({"agent_version": value})
            self.assertNotIn(value, str(raised.exception))

    def test_unexpected_environment_cannot_leak_error_text(self):
        with self.assertRaises(runtime.EvidenceError) as raised:
            runtime.safe_environment({"ERP_PUSH_ENABLED": "PRIVATE_SECRET"})
        self.assertNotIn("PRIVATE_SECRET", str(raised.exception))

    def test_relevant_independent_secrets_cannot_be_ignored(self):
        for key in runtime.ENV_KEYS | runtime.PRESENCE_KEYS:
            with self.assertRaises(runtime.EvidenceError):
                runtime.explicit_environment([{"name": key, "valueFrom": {"secretKeyRef": {"name": "PRIVATE", "key": "3"}}}])

    def test_pinned_dotenv_precedence_and_no_workstation_interpolation(self):
        fake = types.SimpleNamespace(dotenv_values=lambda *, stream, interpolate: {
            "ERP_PUSH_ENABLED": "0", "DATABASE_URL": "PRIVATE", "UNSET": None})
        with patch.dict(sys.modules, {"dotenv": fake}):
            values = runtime.merge_environment("ignored", {"ERP_PUSH_ENABLED": "1"})
        self.assertEqual(values["ERP_PUSH_ENABLED"], "1")
        self.assertNotIn("UNSET", values)
        fake.dotenv_values = lambda **kwargs: {"DATABASE_URL": "${PRIVATE}"}
        with patch.dict(sys.modules, {"dotenv": fake}), self.assertRaises(runtime.EvidenceError):
            runtime.merge_environment("ignored", {})

    def test_database_failure_is_redacted_and_connection_rolled_back(self):
        class Connection:
            rollback_called = close_called = False
            def set_session(self, **kwargs):
                self.options = kwargs
            def cursor(self):
                raise ValueError("PRIVATE_DSN_PRIVATE_CUSTOMER")
            def rollback(self):
                self.rollback_called = True
            def close(self):
                self.close_called = True
        connection = Connection()
        with patch.dict(sys.modules, {"psycopg2": types.SimpleNamespace(connect=lambda *a, **kw: connection)}):
            with self.assertRaisesRegex(runtime.EvidenceError, "^database_read_failed$"):
                runtime.read_database({"DATABASE_URL": "PRIVATE_DSN"})
        self.assertTrue(connection.options["readonly"])
        self.assertEqual(connection.options["isolation_level"], "REPEATABLE READ")
        self.assertTrue(connection.rollback_called and connection.close_called)

    def test_reverse_or_naive_time_is_rejected(self):
        first, last = self.envelope(), self.envelope()
        last["evidence"]["captured_at"] = "2026-09-20T00:00:00+00:00"
        self.rehash(last)
        with self.assertRaisesRegex(runtime.EvidenceError, "reversed"):
            runtime.compare(first, last)
        last["evidence"]["captured_at"] = "2026-09-21T00:00:00"
        self.rehash(last)
        with self.assertRaisesRegex(runtime.EvidenceError, "timestamp"):
            runtime.validate(last)


if __name__ == "__main__":
    unittest.main()
