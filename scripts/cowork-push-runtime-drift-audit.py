#!/usr/bin/env python3
"""Read-only endpoint drift investigation; no config values leave PostgreSQL.

The exact v1 detector is recovered from the original collector creation patch.
New groups are present-time evidence, never a reconstruction of old v2 groups.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), Path(__file__).with_name(name + ".py"))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


runtime = module("cowork-push-runtime-baseline")
gate = module("cowork-migration-baseline")
Error = runtime.EvidenceError
SCHEMA = "pearnly-cowork-push-drift-v1"
# Byte-for-byte key order and SQL expression from original v1 collector.
V1_KEYS = ("agent_token_hash", "method", "account_set", "account_dir", "account_root",
    "system_url", "comidyear", "seldb", "max_payload_version", "companion_version", "mapping")
V1_SQL = """SELECT count(*) AS count,
        md5(COALESCE(string_agg(md5(jsonb_build_object(
            'id', e.id, 'tenant',e.tenant_id,'workspace',e.workspace_client_id,
            'adapter',e.adapter,'enabled',e.enabled,'generation',e.binding_generation,
            'shared',e.shared_scope,'revoked',e.revoked_at IS NOT NULL,
            'account',e.bound_account_set,'profile',e.bound_profile_key,
            'agent_version',e.agent_version,
            'config',(SELECT jsonb_object_agg(k,e.config->k) FROM unnest(%s::text[]) k)
        )::text), ',' ORDER BY e.id),'')) AS configuration_digest FROM erp_endpoints e"""
ADAPTERS = sorted(runtime.ADAPTERS | {"mrerp_dms", "dms"})


def validate_projection(value):
    """Only aggregate integers, allowlisted labels and timestamps may leave RAM."""
    try:
        if set(value) != {"v1_identity", "v2_identity", "adapters", "updated_at_present",
                          "updated_at_type", "updates_since_cutoff", "updated_at_authoritative"}:
            raise Error("database_projection_invalid")
        for key in ("v1_identity", "v2_identity"):
            item = value[key]
            if (set(item) != {"count", "configuration_digest"} or type(item["count"]) is not int
                    or item["count"] < 0 or not re.fullmatch(r"[0-9a-f]{32}", item["configuration_digest"])):
                raise Error("database_projection_invalid")
        if (type(value["updated_at_present"]) is not bool or value["updated_at_authoritative"] is not False
                or value["updated_at_type"] not in {None, "timestamp with time zone", "timestamp without time zone", "unsupported"}
                or value["updated_at_present"] != (value["updated_at_type"] is not None)):
            raise Error("database_projection_invalid")
        names = []
        for row in value["adapters"]:
            if (set(row) != {"adapter", "count"} or row["adapter"] not in ADAPTERS
                    or type(row["count"]) is not int or row["count"] < 0):
                raise Error("database_projection_invalid")
            names.append(row["adapter"])
        if names != sorted(set(names)) or sum(row["count"] for row in value["adapters"]) != value["v1_identity"]["count"]:
            raise Error("database_projection_invalid")
        if value["v1_identity"]["count"] != value["v2_identity"]["count"]:
            raise Error("database_projection_invalid")
        updates = value["updates_since_cutoff"]
        if value["updated_at_type"] != "timestamp with time zone":
            if updates is not None:
                raise Error("database_projection_invalid")
        else:
            if [row["adapter"] for row in updates] != names:
                raise Error("database_projection_invalid")
            for row, counts in zip(updates, value["adapters"]):
                if (set(row) != {"adapter", "updated_count", "earliest_update", "latest_update"}
                        or type(row["updated_count"]) is not int or not 0 <= row["updated_count"] <= counts["count"]):
                    raise Error("database_projection_invalid")
                timestamps = [row["earliest_update"], row["latest_update"]]
                if not row["updated_count"]:
                    if timestamps != [None, None]:
                        raise Error("database_projection_invalid")
                else:
                    parsed = [datetime.fromisoformat(item) for item in timestamps]
                    if any(item.tzinfo is None for item in parsed) or parsed[0] > parsed[1]:
                        raise Error("database_projection_invalid")
    except (TypeError, KeyError, ValueError, AttributeError):
        raise Error("database_projection_invalid") from None
    return value


def readonly_database(values, cutoff):
    import psycopg2
    connection = None
    try:
        if not values.get("DATABASE_URL"):
            raise Error("database_configuration_missing")
        connection = psycopg2.connect(values["DATABASE_URL"], connect_timeout=8,
                                     sslmode=values.get("PGSSLMODE", "require"))
        connection.set_session(readonly=True, autocommit=False, isolation_level="REPEATABLE READ")
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = '8s'")
            v1 = runtime._rows(cursor, V1_SQL, (list(V1_KEYS),))[0]
            v2 = runtime._endpoint_identity(cursor)
            adapters = runtime._rows(cursor, """SELECT CASE WHEN adapter=ANY(%s) THEN adapter ELSE 'other' END AS adapter,
                count(*) AS count FROM erp_endpoints GROUP BY 1 ORDER BY 1""", (ADAPTERS,))
            inventory = runtime._rows(cursor, """SELECT count(*)>0 AS present,
                max(CASE WHEN data_type IN ('timestamp with time zone','timestamp without time zone')
                    THEN data_type ELSE 'unsupported' END) AS data_type
                FROM information_schema.columns WHERE table_schema='public' AND table_name='erp_endpoints'
                AND column_name='updated_at'""")[0]
            updated = None
            if inventory["data_type"] == "timestamp with time zone":
                updated = runtime._rows(cursor, """SELECT CASE WHEN adapter=ANY(%s) THEN adapter ELSE 'other' END AS adapter,
                    count(*) FILTER (WHERE updated_at>%s::timestamptz) AS updated_count,
                    min(updated_at) FILTER (WHERE updated_at>%s::timestamptz) AS earliest_update,
                    max(updated_at) FILTER (WHERE updated_at>%s::timestamptz) AS latest_update
                    FROM erp_endpoints GROUP BY 1 ORDER BY 1""", (ADAPTERS, cutoff, cutoff, cutoff))
                for row in updated:
                    for key in ("earliest_update", "latest_update"):
                        value = row[key]
                        if value is not None:
                            if value.tzinfo is None:
                                raise Error("database_naive_timestamp")
                            row[key] = value.astimezone(timezone.utc).isoformat()
            return validate_projection({"v1_identity": v1, "v2_identity": v2, "adapters": adapters,
                    "updated_at_present": inventory["present"], "updated_at_type": inventory["data_type"],
                    "updates_since_cutoff": updated,
                    "updated_at_authoritative": False})
    except Error:
        raise
    except Exception:
        raise Error("database_read_failed") from None
    finally:
        if connection is not None:
            connection.rollback()
            connection.close()


def collect(baseline_path, old_v1_path, previous_runtime_path):
    source = json.loads(Path(baseline_path).read_text())
    body = source["evidence"]
    gate.validate_body(body)
    if source["sha256"] != gate.digest(body):
        raise Error("source_checksum_invalid")
    old = json.loads(Path(old_v1_path).read_text())
    previous = json.loads(Path(previous_runtime_path).read_text())
    old_time = runtime.validate(old)
    previous_time = runtime.validate(previous)
    if old_time > previous_time or previous_time > datetime.now(timezone.utc):
        raise Error("basis_timestamps_reversed")
    if old["evidence"]["schema"] != runtime.LEGACY_SCHEMA or old["evidence"]["source_sha"] != body["source"]["sha"]:
        raise Error("legacy_basis_invalid")
    if previous["evidence"]["source_sha"] != body["source"]["sha"]:
        raise Error("previous_source_mismatch")
    if set(body["components"]) != set(old["evidence"]["components"]):
        raise Error("source_components_mismatch")
    def traffic():
        for name, component in body["components"].items():
            current = json.loads(runtime.command(["gcloud", "run", "services", "describe", name,
                "--project", body["project"], "--region", body["region"], "--format=" + gate.SERVICE_FORMAT]))
            if gate.active_revision(current) != component["revision"]["metadata"]["name"]:
                raise Error("source_traffic_changed")
    traffic()
    evidence = {"schema": SCHEMA, "captured_at": datetime.now(timezone.utc).isoformat(),
        "source_sha": body["source"]["sha"], "source_baseline_sha256": source["sha256"],
        "legacy_v1_sha256": old["sha256"], "previous_runtime_sha256": previous["sha256"],
        "updated_since": previous["evidence"]["captured_at"], "v1_keys": list(V1_KEYS),
        "v1_sql_sha256": hashlib.sha256(V1_SQL.encode()).hexdigest(), "components": {}}
    for name, component in body["components"].items():
        raw = json.loads(runtime.command(["gcloud", "run", "revisions", "describe",
            component["revision"]["metadata"]["name"], "--project", body["project"],
            "--region", body["region"], "--format=json"]))
        if gate.project_revision(raw) != component["revision"]:
            raise Error("source_revision_changed")
        refs = [r for r in component["configuration"]["secret_references"]
                if r["kind"] == "volume" and r["target"] == "runtime/runtime.env"]
        if len(refs) != 1 or component["configuration"]["secret_aliases"]:
            raise Error("runtime_secret_unsupported")
        ref = refs[0]
        explicit = runtime.explicit_environment(raw["spec"]["containers"][0].get("env", []))
        if explicit.get("PEARNLY_RUNTIME_ENV_FILE") != "/secrets/runtime.env":
            raise Error("runtime_path_unsupported")
        payload = runtime.command(["gcloud", "secrets", "versions", "access", ref["version"],
                                  "--secret", ref["name"], "--project", body["project"]])
        values = runtime.merge_environment(payload, explicit)
        del payload, raw
        try:
            database = readonly_database(values, evidence["updated_since"])
        finally:
            values.clear()
        database["legacy_v1_unchanged"] = database["v1_identity"] == old["evidence"]["components"][name]["database"]["identity"]["endpoints"]
        evidence["components"][name] = database
    traffic()
    if len({runtime.digest(value) for value in evidence["components"].values()}) != 1:
        raise Error("component_database_identity_changed")
    return {"evidence": evidence, "sha256": runtime.digest(evidence)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["collect-readonly"])
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--legacy-v1", required=True)
    parser.add_argument("--previous-runtime", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        if Path(args.output).exists():
            raise Error("output_already_exists")
        result = collect(args.baseline, args.legacy_v1, args.previous_runtime)
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
        with os.fdopen(fd, "w") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        print("PASS drift projection saved", result["sha256"])
        print("legacy_v1_unchanged=" + str(all(c["legacy_v1_unchanged"] for c in result["evidence"]["components"].values())))
        return 0
    except Exception as error:
        print(str(error) if isinstance(error, Error) else "drift_operation_failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
