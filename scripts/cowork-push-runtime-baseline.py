#!/usr/bin/env python3
"""Explicit read-only ERP runtime projection. Credentials exist in RAM only.

No application imports, table creation, customer payloads or write endpoints.
Settings identity and rolling observations deliberately have separate contracts.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import subprocess

SCHEMA = "pearnly-cowork-push-runtime-v2"
LEGACY_SCHEMA = "pearnly-cowork-push-runtime-v1"
BOOL_KEYS = {"ERP_PUSH_ENABLED", "ERP_BRIDGE_ENABLED", "ERP_SELLER_ROUTING"}
NUMBER_KEYS = {"ERP_BRIDGE_POLL_HOLD_SECONDS", "ERP_RETRY_TICK_SEC", "ERP_PUSH_TIMEOUT_SEC"}
ENV_KEYS = BOOL_KEYS | NUMBER_KEYS | {"PEARNLY_RUNTIME_ROLE"}
PRESENCE_KEYS = {"DATABASE_URL", "PEARNLY_KMS_KEY", "ERP_SELLER_ROUTING_USERS"}
FLAGS = ("erp_shared_express_endpoint", "erp_target_projection", "erp_portal")
TABLES = {
    "platform_settings": {"key", "enabled", "value"},
    "platform_setting_allowlist": {"setting_key", "user_id"},
    "erp_endpoints": {"id", "adapter", "enabled", "config", "tenant_id", "workspace_client_id",
        "binding_generation", "shared_scope", "revoked_at", "bound_account_set", "bound_profile_key",
        "live_account_set", "live_profile_key", "agent_last_seen_at", "agent_version"},
    "erp_push_logs": {"endpoint_id", "status", "created_at", "lease_owner", "lease_expires_at", "next_retry_at"},
    "erp_bridges": {"id", "tenant_id", "role", "effective_role", "books", "bridge_version", "last_seen_at", "secret_hash"},
    "bridge_jobs": {"kind", "status", "lease_owner", "lease_expires_at"},
}
ADAPTERS = {"express", "mrerp", "flowaccount", "peak", "xero", "quickbooks", "webhook", "custom", "other"}
STATUSES = {"pending", "retrying", "success", "skipped_dup", "manual", "failed", "queued", "leased", "done", "expired", "other"}
VERSION = r"^[0-9]+([.][0-9]+){1,3}$"
OBSERVATION_FIELDS = {
    "endpoints": set("adapter enabled branch agent_version companion_version payload_version count token_present unrevoked shared scoped account_present directory_present bound live_matches managed_fresh legacy_seen_present rollout_enabled".split()),
    "push_logs": set("adapter branch status count recent leased expired_lease scheduled".split()),
    "bridges": set("role effective_role bridge_version count secret_present recent book_count".split()),
    "bridge_jobs": set("kind status count leased expired_lease".split()),
}
TEXT_FIELDS = {"adapter", "branch", "status", "role", "effective_role", "kind",
               "agent_version", "companion_version", "payload_version", "bridge_version"}


class EvidenceError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def command(args):
    try:
        return subprocess.run(args, capture_output=True, text=True, check=True, timeout=45).stdout
    except (OSError, subprocess.SubprocessError):
        raise EvidenceError("cloud_read_failed") from None


def safe_environment(values):
    out = {}
    for key in sorted(ENV_KEYS):
        if key not in values:
            continue
        value = values[key]
        if not isinstance(value, str):
            raise EvidenceError("environment_invalid")
        clean = value.strip()
        valid = (not clean or bool(re.fullmatch(r"(?:[+-]?\d+(?:\.\d+)?|true|false|yes|no|on|off)", clean, re.I)))
        if key == "PEARNLY_RUNTIME_ROLE":
            valid = clean in {"web", "worker", "schema"}
        if not valid or len(value) > 80:
            raise EvidenceError("environment_invalid")
        out[key] = value
    return out


def explicit_environment(entries):
    out = {}
    relevant = ENV_KEYS | PRESENCE_KEYS | {"PGSSLMODE", "PEARNLY_RUNTIME_ENV_FILE"}
    for entry in entries:
        if "value" in entry:
            out[entry["name"]] = entry["value"]
        elif entry["name"] in relevant:
            raise EvidenceError("independent_runtime_secret_unsupported")
    return out


def merge_environment(payload, explicit):
    from dotenv import dotenv_values
    values = dotenv_values(stream=io.StringIO(payload), interpolate=False)
    if any("${" in value for value in values.values() if isinstance(value, str)):
        raise EvidenceError("runtime_interpolation_unsupported")
    values = {key: value for key, value in values.items() if value is not None}
    values.update(explicit)  # deployed load_dotenv(override=False)
    return values


def _rows(cursor, sql, params=()):
    cursor.execute(sql, params)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _inventory(cursor):
    rows = _rows(cursor, """SELECT c.relname AS table_name, a.attname AS column_name
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
        WHERE n.nspname='public' AND c.relname=ANY(%s)""", (list(TABLES),))
    found = {name: set() for name in TABLES}
    for row in rows:
        found[row["table_name"]].add(row["column_name"])
    return {name: sorted(expected - found[name]) for name, expected in TABLES.items()}


def _flag_settings(cursor):
    return _rows(cursor, """SELECT p.key, p.enabled,
        CASE WHEN p.value->>'rollout'='all' THEN 'all' ELSE 'allowlist' END AS rollout,
        (SELECT count(*) FROM platform_setting_allowlist a WHERE a.setting_key=p.key) AS allowlist_count,
        (SELECT md5(COALESCE(string_agg(md5(a.user_id::text), ',' ORDER BY a.user_id::text),''))
           FROM platform_setting_allowlist a WHERE a.setting_key=p.key) AS membership_digest
        FROM platform_settings p WHERE p.key=ANY(%s) ORDER BY p.key""", (list(FLAGS),))


def _endpoint_identity(cursor):
    # SQL computes a change detector; no identities, token hashes, paths or
    # account names cross the DB boundary. V2 binds the ENTIRE config JSON,
    # including unknown/future keys and any heartbeat/catalog values in config.
    # Rolling count/time-bucket observations remain separate. A changed config
    # timestamp deliberately invalidates identity rather than being discarded.
    rows = _rows(cursor, """SELECT count(*) AS count,
        md5(COALESCE(string_agg(md5(jsonb_build_object(
            'id', e.id, 'tenant',e.tenant_id,'workspace',e.workspace_client_id,
            'adapter',e.adapter,'enabled',e.enabled,'generation',e.binding_generation,
            'shared',e.shared_scope,'revoked',e.revoked_at IS NOT NULL,
            'account',e.bound_account_set,'profile',e.bound_profile_key,
            'agent_version',e.agent_version,
            'config',e.config
        )::text), ',' ORDER BY e.id),'')) AS configuration_digest FROM erp_endpoints e""")
    return rows[0]


def _endpoint_observations(cursor, flags_ready):
    effective = "FALSE"
    if flags_ready:
        effective = """EXISTS (SELECT 1 FROM platform_settings p
            WHERE p.key='erp_shared_express_endpoint' AND p.enabled AND
            (p.value->>'rollout'='all' OR EXISTS (SELECT 1 FROM platform_setting_allowlist a
             WHERE a.setting_key=p.key AND a.user_id::text=e.tenant_id::text)))"""
    return _rows(cursor, f"""SELECT
        CASE WHEN e.adapter=ANY(%s) THEN e.adapter ELSE 'other' END AS adapter,
        e.enabled, CASE WHEN e.binding_generation>0 THEN 'managed' ELSE 'legacy' END AS branch,
        CASE WHEN e.agent_version ~ %s THEN e.agent_version
             WHEN nullif(e.agent_version,'') IS NULL THEN 'absent' ELSE 'invalid' END AS agent_version,
        CASE WHEN e.config->>'companion_version' ~ %s THEN e.config->>'companion_version'
             WHEN nullif(e.config->>'companion_version','') IS NULL THEN 'absent' ELSE 'invalid' END AS companion_version,
        CASE WHEN e.config->>'max_payload_version' ~ '^[0-9]{{1,4}}$' THEN e.config->>'max_payload_version'
             WHEN nullif(e.config->>'max_payload_version','') IS NULL THEN 'absent' ELSE 'invalid' END AS payload_version,
        count(*) AS count,
        count(*) FILTER (WHERE nullif(e.config->>'agent_token_hash','') IS NOT NULL) AS token_present,
        count(*) FILTER (WHERE e.revoked_at IS NULL) AS unrevoked,
        count(*) FILTER (WHERE e.shared_scope) AS shared,
        count(*) FILTER (WHERE e.tenant_id IS NOT NULL AND e.workspace_client_id IS NOT NULL) AS scoped,
        count(*) FILTER (WHERE nullif(e.config->>'account_set','') IS NOT NULL) AS account_present,
        count(*) FILTER (WHERE nullif(e.config->>'account_dir','') IS NOT NULL) AS directory_present,
        count(*) FILTER (WHERE e.bound_account_set IS NOT NULL AND e.bound_profile_key IS NOT NULL) AS bound,
        count(*) FILTER (WHERE e.bound_account_set=e.live_account_set AND e.bound_profile_key=e.live_profile_key) AS live_matches,
        count(*) FILTER (WHERE e.agent_last_seen_at BETWEEN clock_timestamp()-interval '180 seconds'
                                                      AND clock_timestamp()+interval '5 seconds') AS managed_fresh,
        count(*) FILTER (WHERE nullif(e.config->>'agent_last_seen_at','') IS NOT NULL) AS legacy_seen_present,
        count(*) FILTER (WHERE e.enabled AND e.revoked_at IS NULL AND e.binding_generation>0 AND {effective}) AS rollout_enabled
        FROM erp_endpoints e GROUP BY 1,2,3,4,5,6 ORDER BY 1,2,3,4,5,6""",
        (sorted(ADAPTERS - {"other"}), VERSION, VERSION))


def _push_observations(cursor):
    return _rows(cursor, """SELECT CASE WHEN e.adapter=ANY(%s) THEN e.adapter ELSE 'other' END AS adapter,
        CASE WHEN e.binding_generation>0 THEN 'managed' ELSE 'legacy' END AS branch,
        CASE WHEN l.status=ANY(%s) THEN l.status ELSE 'other' END AS status,
        count(*) AS count, count(*) FILTER (WHERE l.created_at>clock_timestamp()-interval '7 days') AS recent,
        count(*) FILTER (WHERE l.lease_owner IS NOT NULL) AS leased,
        count(*) FILTER (WHERE l.lease_owner IS NOT NULL AND l.lease_expires_at<clock_timestamp()) AS expired_lease,
        count(*) FILTER (WHERE l.next_retry_at IS NOT NULL) AS scheduled
        FROM erp_push_logs l LEFT JOIN erp_endpoints e ON e.id=l.endpoint_id
        GROUP BY 1,2,3 ORDER BY 1,2,3""", (sorted(ADAPTERS - {"other"}), sorted(STATUSES - {"other"})))


def _bridge_identity(cursor):
    return _rows(cursor, """SELECT count(*) AS count,
        md5(COALESCE(string_agg(md5(jsonb_build_object('id',id,'tenant',tenant_id,
            'role',role,'effective_role',effective_role,'books',books,
            'version',bridge_version,'secret',secret_hash)::text),',' ORDER BY id),'')) AS configuration_digest
        FROM erp_bridges""")[0]


def _bridge_observations(cursor):
    return _rows(cursor, """SELECT CASE WHEN role IN ('read','write') THEN role ELSE 'other' END AS role,
        CASE WHEN effective_role IN ('read','write') THEN effective_role ELSE 'other' END AS effective_role,
        CASE WHEN bridge_version ~ %s THEN bridge_version
             WHEN nullif(bridge_version,'') IS NULL THEN 'absent' ELSE 'invalid' END AS bridge_version,
        count(*) AS count, count(*) FILTER (WHERE nullif(secret_hash,'') IS NOT NULL) AS secret_present,
        count(*) FILTER (WHERE last_seen_at>clock_timestamp()-interval '180 seconds') AS recent,
        COALESCE(sum(CASE WHEN jsonb_typeof(books)='array' THEN jsonb_array_length(books) ELSE 0 END),0)::bigint AS book_count
        FROM erp_bridges GROUP BY 1,2,3 ORDER BY 1,2,3""", (VERSION,))


def read_database(values):
    import psycopg2
    connection = None
    try:
        if not values.get("DATABASE_URL"):
            raise EvidenceError("database_configuration_missing")
        connection = psycopg2.connect(values["DATABASE_URL"], connect_timeout=8,
                                     sslmode=values.get("PGSSLMODE", "require"))
        connection.set_session(readonly=True, autocommit=False, isolation_level="REPEATABLE READ")
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = '8s'")
            missing = _inventory(cursor)
            ready = lambda *names: not any(missing[name] for name in names)
            identity = {"missing_columns": missing, "flags": None, "endpoints": None, "bridges": None}
            observations = {"endpoints": None, "push_logs": None, "bridges": None, "bridge_jobs": None}
            flags_ready = ready("platform_settings", "platform_setting_allowlist")
            if flags_ready:
                identity["flags"] = _flag_settings(cursor)
            if ready("erp_endpoints"):
                identity["endpoints"] = _endpoint_identity(cursor)
                observations["endpoints"] = _endpoint_observations(cursor, flags_ready)
                if ready("erp_push_logs"):
                    observations["push_logs"] = _push_observations(cursor)
            if ready("erp_bridges"):
                identity["bridges"] = _bridge_identity(cursor)
                observations["bridges"] = _bridge_observations(cursor)
            if ready("bridge_jobs"):
                observations["bridge_jobs"] = _rows(cursor, """SELECT
                    CASE WHEN kind IN ('query','write') THEN kind ELSE 'other' END AS kind,
                    CASE WHEN status=ANY(%s) THEN status ELSE 'other' END AS status,
                    count(*) AS count, count(*) FILTER (WHERE lease_owner IS NOT NULL) AS leased,
                    count(*) FILTER (WHERE lease_owner IS NOT NULL AND lease_expires_at<clock_timestamp()) AS expired_lease
                    FROM bridge_jobs GROUP BY 1,2 ORDER BY 1,2""", (sorted(STATUSES - {"other"}),))
            return {"identity": identity, "observations": observations}
    except EvidenceError:
        raise
    except Exception:
        raise EvidenceError("database_read_failed") from None
    finally:
        if connection is not None:
            connection.rollback()
            connection.close()


def _module(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def collect(baseline):
    gate = _module("cowork-migration-baseline")
    envelope = json.loads(Path(baseline).read_text())
    body = envelope["evidence"]
    gate.validate_body(body)
    if envelope["sha256"] != gate.digest(body):
        raise EvidenceError("source_checksum_invalid")
    def traffic():
        for name, component in body["components"].items():
            current = json.loads(command(["gcloud", "run", "services", "describe", name, "--project", body["project"],
                "--region", body["region"], "--format=" + gate.SERVICE_FORMAT]))
            if gate.active_revision(current) != component["revision"]["metadata"]["name"]:
                raise EvidenceError("source_traffic_changed")
    traffic()
    out = {"schema": SCHEMA, "captured_at": datetime.now(timezone.utc).isoformat(),
        "source_baseline_sha256": envelope["sha256"], "source_sha": body["source"]["sha"], "components": {}}
    for name, component in body["components"].items():
        revision = component["revision"]["metadata"]["name"]
        raw = json.loads(command(["gcloud", "run", "revisions", "describe", revision, "--project", body["project"],
                                  "--region", body["region"], "--format=json"]))
        if gate.project_revision(raw) != component["revision"]:
            raise EvidenceError("source_revision_changed")
        refs = [ref for ref in component["configuration"]["secret_references"]
                if ref["kind"] == "volume" and ref["target"] == "runtime/runtime.env"]
        if len(refs) != 1 or component["configuration"]["secret_aliases"]:
            raise EvidenceError("runtime_secret_unsupported")
        ref = refs[0]
        explicit = explicit_environment(raw["spec"]["containers"][0].get("env", []))
        if explicit.get("PEARNLY_RUNTIME_ENV_FILE") != "/secrets/runtime.env":
            raise EvidenceError("runtime_path_unsupported")
        payload = command(["gcloud", "secrets", "versions", "access", ref["version"], "--secret", ref["name"], "--project", body["project"]])
        values = merge_environment(payload, explicit)
        del payload, raw
        database = read_database(values)
        out["components"][name] = {"revision": revision, "runtime_secret": {"name": ref["name"], "version": ref["version"]},
            "environment": safe_environment(values), "presence": {key: bool(values.get(key)) for key in sorted(PRESENCE_KEYS)},
            "database": database}
        values.clear()
    traffic()
    identities = [component["database"]["identity"] for component in out["components"].values()]
    if identities[0] != identities[1]:
        raise EvidenceError("component_database_identity_changed")
    result = {"evidence": out, "sha256": digest(out)}
    validate(result)
    return result


def _safe_projection(value, key=""):
    if value is None or type(value) is bool:
        return
    if type(value) is int and value >= 0:
        return
    if isinstance(value, list):
        for item in value:
            _safe_projection(item, key)
        return
    if isinstance(value, dict):
        for name, item in value.items():
            if not re.fullmatch(r"[a-z_]{1,80}", name):
                raise EvidenceError("projection_invalid")
            _safe_projection(item, name)
        return
    allowed = set(TABLES) | set().union(*TABLES.values()) | set(FLAGS) | ADAPTERS | STATUSES
    allowed |= {"legacy", "managed", "read", "write", "query", "all", "allowlist", "absent", "invalid"}
    if isinstance(value, str) and (value in allowed or
            (key in {"configuration_digest", "membership_digest"} and re.fullmatch(r"[a-f0-9]{32}", value)) or
            (key in {"agent_version", "companion_version", "bridge_version", "payload_version"} and re.fullmatch(r"[0-9]{1,4}(?:[.][0-9]{1,4}){0,3}", value))):
        return
    raise EvidenceError("projection_invalid")


def _validate_database(database):
    if (set(database) != {"identity", "observations"} or
            set(database["identity"]) != {"missing_columns", "flags", "endpoints", "bridges"} or
            set(database["observations"]) != set(OBSERVATION_FIELDS)):
        raise EvidenceError("database_projection_incomplete")
    identity, observations = database["identity"], database["observations"]
    missing = identity["missing_columns"]
    if set(missing) != set(TABLES):
        raise EvidenceError("database_inventory_incomplete")
    for name, columns in missing.items():
        if not isinstance(columns, list) or sorted(set(columns)) != columns or set(columns) - TABLES[name]:
            raise EvidenceError("database_inventory_invalid")
    ready = lambda *names: not any(missing[name] for name in names)
    for name, table in (("endpoints", "erp_endpoints"), ("bridges", "erp_bridges")):
        value = identity[name]
        if ready(table):
            if (not isinstance(value, dict) or set(value) != {"count", "configuration_digest"}
                    or type(value["count"]) is not int or value["count"] < 0
                    or not re.fullmatch(r"[a-f0-9]{32}", value["configuration_digest"])):
                raise EvidenceError("configuration_identity_invalid")
        elif value is not None:
            raise EvidenceError("incomplete_table_cannot_prove_identity")
    flags = identity["flags"]
    if ready("platform_settings", "platform_setting_allowlist"):
        if not isinstance(flags, list):
            raise EvidenceError("flags_incomplete")
        keys = []
        for flag in flags:
            if (set(flag) != {"key", "enabled", "rollout", "allowlist_count", "membership_digest"}
                    or flag["key"] not in FLAGS or type(flag["enabled"]) is not bool
                    or flag["rollout"] not in {"all", "allowlist"}
                    or type(flag["allowlist_count"]) is not int or flag["allowlist_count"] < 0
                    or not re.fullmatch(r"[a-f0-9]{32}", flag["membership_digest"])):
                raise EvidenceError("flags_invalid")
            keys.append(flag["key"])
        if keys != sorted(set(keys)):
            raise EvidenceError("flags_invalid")
    elif flags is not None:
        raise EvidenceError("incomplete_table_cannot_prove_identity")
    for name, fields in OBSERVATION_FIELDS.items():
        required = {"endpoints": ("erp_endpoints",), "push_logs": ("erp_endpoints", "erp_push_logs"),
                    "bridges": ("erp_bridges",), "bridge_jobs": ("bridge_jobs",)}[name]
        rows = observations[name]
        if not ready(*required):
            if rows is not None:
                raise EvidenceError("incomplete_table_cannot_prove_observations")
            continue
        if not isinstance(rows, list):
            raise EvidenceError("observations_incomplete")
        for row in rows:
            if set(row) != fields:
                raise EvidenceError("observation_fields_invalid")
            for key, value in row.items():
                if key == "enabled":
                    valid = type(value) is bool
                elif key in TEXT_FIELDS:
                    valid = isinstance(value, str)
                else:
                    valid = type(value) is int and value >= 0
                if not valid:
                    raise EvidenceError("observation_type_invalid")
    _safe_projection(database)


def validate(envelope):
    try:
        body = envelope["evidence"]
        if set(envelope) != {"evidence", "sha256"} or envelope["sha256"] != digest(body):
            raise EvidenceError("evidence_checksum_invalid")
        if (set(body) != {"schema", "captured_at", "source_baseline_sha256", "source_sha", "components"}
                or body["schema"] not in {SCHEMA, LEGACY_SCHEMA}
                or not re.fullmatch(r"[a-f0-9]{40}", body["source_sha"])
                or not re.fullmatch(r"[a-f0-9]{64}", body["source_baseline_sha256"])
                or set(body["components"]) != {"pearnly-web", "pearnly-worker"}):
            raise EvidenceError("evidence_incomplete")
        timestamp = datetime.fromisoformat(body["captured_at"].replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            raise EvidenceError("timestamp_invalid")
        for name, component in body["components"].items():
            if set(component) != {"revision", "runtime_secret", "environment", "presence", "database"}:
                raise EvidenceError("component_incomplete")
            if not re.fullmatch(re.escape(name) + r"-[a-z0-9-]{1,200}", component["revision"]):
                raise EvidenceError("revision_invalid")
            ref = component["runtime_secret"]
            if (set(ref) != {"name", "version"} or ref["name"] != name + "-env"
                    or not re.fullmatch(r"[1-9][0-9]*", ref["version"])):
                raise EvidenceError("secret_reference_invalid")
            if safe_environment(component["environment"]) != component["environment"]:
                raise EvidenceError("environment_invalid")
            if set(component["presence"]) != PRESENCE_KEYS or any(type(v) is not bool for v in component["presence"].values()):
                raise EvidenceError("presence_invalid")
            _validate_database(component["database"])
        identities = [component["database"]["identity"] for component in body["components"].values()]
        if identities[0] != identities[1]:
            raise EvidenceError("component_database_identity_changed")
        return timestamp
    except EvidenceError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError):
        raise EvidenceError("evidence_invalid") from None


def compare(first, last):
    if validate(last) < validate(first):
        raise EvidenceError("timestamps_reversed")
    # V1 only bound a selected list of config keys. It cannot serve as the
    # starting snapshot for V2's broader identity, even with equal row counts.
    if first["evidence"]["schema"] != last["evidence"]["schema"]:
        raise EvidenceError("evidence_schema_mismatch")
    def identity(envelope):
        body = envelope["evidence"]
        return {"source_sha": body["source_sha"], "components": {name: {
            **{key: value for key, value in component.items() if key != "database"},
            "database_identity": component["database"]["identity"]} for name, component in body["components"].items()}}
    if identity(first) != identity(last):
        raise EvidenceError("push_runtime_identity_changed")
    return {"settings_unchanged": True, "observations_changed": any(
        first["evidence"]["components"][name]["database"]["observations"] !=
        last["evidence"]["components"][name]["database"]["observations"]
        for name in first["evidence"]["components"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    collect_parser = subs.add_parser("collect-readonly")
    collect_parser.add_argument("--baseline", required=True)
    collect_parser.add_argument("--output", required=True)
    comparison = subs.add_parser("compare")
    comparison.add_argument("first")
    comparison.add_argument("last")
    check = subs.add_parser("verify")
    check.add_argument("evidence")
    args = parser.parse_args()
    try:
        if args.command == "collect-readonly":
            if Path(args.output).exists():
                raise EvidenceError("output_already_exists")
            result = collect(args.baseline)
            with Path(args.output).open("x") as stream:
                stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            print("PASS push runtime projection saved", result["sha256"])
        elif args.command == "compare":
            print(json.dumps(compare(json.loads(Path(args.first).read_text()), json.loads(Path(args.last).read_text()))))
        else:
            validate(json.loads(Path(args.evidence).read_text()))
            print("PASS push runtime projection verified")
    except Exception as error:
        print(str(error) if isinstance(error, EvidenceError) else "evidence_operation_failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
