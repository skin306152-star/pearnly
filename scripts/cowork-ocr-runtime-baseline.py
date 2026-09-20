#!/usr/bin/env python3
"""Explicit read-only OCR configuration evidence; never serialize credentials.

Requires a verified source baseline plus gcloud, python-dotenv and psycopg2.
Pinned runtime.env is consumed only in memory. One policy row is read in a
read-only transaction, rolled back; no application startup code is imported.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess

MODE_KEYS = {"OCR_ENGINE_MODE", "OCR_LLM_BACKEND"}
MODEL_KEYS = {"OCR_FLASH_MODEL", "OCR_FLASHLITE_MODEL", "OCR_FALLBACK_MODEL", "OCR_ESCALATE_MODEL"}
LOCATION_KEYS = {"GCP_PROJECT", "GOOGLE_CLOUD_PROJECT", "VERTEX_PROJECT", "VERTEX_LOCATION", "VERTEX_LOCATION_25"}
TIMEZONE_KEYS = {"TZ"}
CREDENTIAL_KEYS = {"GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS"}
NUMBER_KEYS = set("""OCR_IMAGE_DIRECT OCR_IMAGE_DIRECT_MAX_PAGES OCR_IMAGE_DIRECT_WORKERS
OCR_PDF_PAGE_WORKERS OCR_IMAGE_FIRST OCR_FAST_PATH_ENABLED OCR_DOUBLE_READ
OCR_DOUBLE_READ_BIG_THB OCR_IMAGE_FIRST_ESCALATE_CONF OCR_PIPELINE_CONF_THRESHOLD
OCR_PIPELINE_AMOUNT_TOL OCR_CONF_AUTO OCR_CONF_REVIEW OCR_IMG_MAX_LONG_EDGE
OCR_DIRECT_TIMEOUT_SECONDS OCR_L1_TIMEOUT_SECONDS OCR_L2_TIMEOUT_SECONDS
OCR_L3_TIMEOUT_SECONDS OCR_TRANSIENT_RETRY OCR_FEWSHOT_ENABLED""".split())
MODES = {"direct35", "enterprise", "economy", "selfhost", "qwen", "auto"}
BACKENDS = {"aistudio", "vertex", "selfhost", "anthropic", "openai", "qwen"}
PLAN_KEYS = {"none", "S", "M", "L", "exempt"}


class EvidenceError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def safe_environment(values):
    out = {}
    for key in sorted(MODE_KEYS | MODEL_KEYS | LOCATION_KEYS | NUMBER_KEYS | TIMEZONE_KEYS):
        if key not in values:
            continue
        original = str(values[key])
        value = original.strip()
        valid = not value
        if key in NUMBER_KEYS:
            valid |= bool(re.fullmatch(r"(?:[+-]?\d+(?:\.\d+)?|true|false|yes|no|on|off)", value, re.I))
        elif key in MODE_KEYS:
            valid |= value in (MODES if key == "OCR_ENGINE_MODE" else BACKENDS)
        elif key in TIMEZONE_KEYS:
            valid |= bool(re.fullmatch(r"[A-Za-z0-9_./+-]{1,80}", value))
        else:
            valid |= bool(re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", value))
        if not valid:
            raise EvidenceError("unexpected OCR configuration value; inspect privately")
        # Whitespace changes a few legacy switches (notably fewshot).
        out[key] = original
    return out


def safe_policy(row):
    if row is None:
        return {"present": False}
    enabled, updated_at, kind, mode, invoice, plans = row
    if kind not in {"object", "array", "string", "number", "boolean", "null"}:
        raise EvidenceError("unexpected policy shape")
    for value in (mode, invoice):
        if value is not None and value not in MODES | {""}:
            raise EvidenceError("unexpected policy mode; inspect privately")
    if plans is not None and (not isinstance(plans, dict) or any(
        k not in PLAN_KEYS or v not in MODES | {""} for k, v in plans.items()
    )):
        raise EvidenceError("unexpected plan policy; inspect privately")
    return {"present": True, "enabled": bool(enabled), "updated_at": updated_at.isoformat(),
            "value_type": kind, "mode": mode, "invoice_override": invoice, "defaults_by_plan": plans}


def command(args):
    try:
        return subprocess.run(args, capture_output=True, check=True, text=True, timeout=45).stdout
    except (OSError, subprocess.SubprocessError):
        raise EvidenceError("read-only cloud command failed; provider output withheld") from None


def read_policy(values):
    import psycopg2
    connection = None
    try:
        if not values.get("DATABASE_URL"):
            raise EvidenceError("database configuration missing")
        connection = psycopg2.connect(values["DATABASE_URL"], connect_timeout=8,
                                     sslmode=values.get("PGSSLMODE", "require"))
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = '5s'")
            cursor.execute("""SELECT enabled, updated_at, jsonb_typeof(value), value->'mode',
                value->'overrides_by_task'->'invoice', value->'defaults_by_plan'
                FROM platform_settings WHERE key = %s""", ("ocr_engine_policy",))
            return safe_policy(cursor.fetchone())
    except EvidenceError:
        raise
    except Exception:
        raise EvidenceError("read-only policy query failed; connection details withheld") from None
    finally:
        if connection is not None:
            connection.rollback()
            connection.close()


def verify_traffic(body, gate):
    for name, component in body["components"].items():
        current = json.loads(command(["gcloud", "run", "services", "describe", name,
            "--project", body["project"], "--region", body["region"], "--format=" + gate.SERVICE_FORMAT]))
        if gate.active_revision(current) != component["revision"]["metadata"]["name"]:
            raise EvidenceError("production traffic changed; recollect the source baseline")


def explicit_environment(entries):
    explicit = {}
    relevant = MODE_KEYS | MODEL_KEYS | LOCATION_KEYS | NUMBER_KEYS | TIMEZONE_KEYS | CREDENTIAL_KEYS | {"DATABASE_URL", "PGSSLMODE"}
    for env in entries:
        if "value" in env:
            explicit[env["name"]] = env["value"]
        elif env["name"] in relevant:
            raise EvidenceError("OCR configuration has another secret source")
    return explicit


def collect(baseline):
    from dotenv import dotenv_values
    spec = importlib.util.spec_from_file_location("cowork_baseline", Path(__file__).with_name("cowork-migration-baseline.py"))
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    envelope = json.loads(Path(baseline).read_text())
    body = envelope["evidence"]
    gate.validate_body(body)
    if envelope["sha256"] != gate.digest(body):
        raise EvidenceError("source baseline checksum mismatch")
    verify_traffic(body, gate)
    out = {"schema": "pearnly-cowork-ocr-runtime-v1", "captured_at": datetime.now(timezone.utc).isoformat(),
           "source_baseline_sha256": envelope["sha256"], "source_sha": body["source"]["sha"], "components": {}}
    for name, component in body["components"].items():
        revision = component["revision"]["metadata"]["name"]
        raw = json.loads(command(["gcloud", "run", "revisions", "describe", revision, "--project", body["project"],
                                  "--region", body["region"], "--format=json"]))
        if gate.project_revision(raw) != component["revision"]:
            raise EvidenceError("revision differs from verified baseline")
        refs = [r for r in component["configuration"]["secret_references"]
                if r["kind"] == "volume" and r["target"] == "runtime/runtime.env"]
        if len(refs) != 1 or component["configuration"]["secret_aliases"]:
            raise EvidenceError("unsupported runtime secret mapping")
        ref = refs[0]
        explicit = explicit_environment(raw["spec"]["containers"][0].get("env", []))
        if explicit.get("PEARNLY_RUNTIME_ENV_FILE") != "/secrets/runtime.env":
            raise EvidenceError("unsupported runtime environment path")
        payload = command(["gcloud", "secrets", "versions", "access", ref["version"], "--secret", ref["name"],
                           "--project", body["project"]])
        # This collector does not reconstruct image ENV, injected Cloud Run
        # variables or unrelated secretKeyRefs. Refuse interpolation rather than
        # silently substituting workstation values or missing dependencies.
        uninterpolated = dotenv_values(stream=io.StringIO(payload), interpolate=False)
        if any("${" in v for v in uninterpolated.values() if isinstance(v, str)):
            raise EvidenceError("runtime env uses interpolation; effective configuration needs explicit proof")
        uninterpolated.clear()
        # Do not allow this workstation's env to alter dotenv interpolation.
        previous = os.environ
        try:
            os.environ = explicit.copy()
            values = {k: v for k, v in dotenv_values(stream=io.StringIO(payload)).items() if v is not None}
        finally:
            os.environ = previous
        values.update(explicit)
        del payload, raw
        out["components"][name] = {"revision": revision, "runtime_secret": {"name": ref["name"], "version": ref["version"]},
            "configured_environment": safe_environment(values), "policy": read_policy(values),
            "credential_presence": {k: bool(values.get(k)) for k in sorted(CREDENTIAL_KEYS)}}
        values.clear()
    verify_traffic(body, gate)
    policies = [v["policy"] for v in out["components"].values()]
    if any(p != policies[0] for p in policies[1:]):
        raise EvidenceError("components have different dynamic policies; review the split")
    result = {"evidence": out, "sha256": hashlib.sha256(canonical(out)).hexdigest()}
    validate(result)
    return result


def _timestamp(value):
    if not isinstance(value, str):
        raise EvidenceError("invalid evidence timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise EvidenceError("evidence timestamp must include a timezone")
    return result


def validate(envelope):
    """Require a complete safe projection; a self-consistent hash is not enough."""
    try:
        body = envelope["evidence"]
        if (set(envelope) != {"evidence", "sha256"}
                or envelope["sha256"] != hashlib.sha256(canonical(body)).hexdigest()):
            raise EvidenceError("invalid runtime evidence checksum or schema")
        if (set(body) != {"schema", "captured_at", "source_baseline_sha256", "source_sha", "components"}
                or body["schema"] != "pearnly-cowork-ocr-runtime-v1"
                or not re.fullmatch(r"[a-f0-9]{40}", body["source_sha"])
                or not re.fullmatch(r"[a-f0-9]{64}", body["source_baseline_sha256"])
                or set(body["components"]) != {"pearnly-web", "pearnly-worker"}):
            raise EvidenceError("incomplete runtime evidence")
        captured_at = _timestamp(body["captured_at"])
        for service, component in body["components"].items():
            if set(component) != {"revision", "runtime_secret", "configured_environment", "policy", "credential_presence"}:
                raise EvidenceError("incomplete component evidence")
            if not re.fullmatch(re.escape(service) + r"-[a-z0-9-]{1,200}", component["revision"]):
                raise EvidenceError("invalid component revision")
            ref = component["runtime_secret"]
            if (set(ref) != {"name", "version"} or not re.fullmatch(r"[a-zA-Z0-9_-]{1,255}", ref["name"])
                    or not re.fullmatch(r"[1-9][0-9]*", ref["version"])):
                raise EvidenceError("invalid pinned runtime secret")
            env = component["configured_environment"]
            if not isinstance(env, dict) or any(not isinstance(v, str) for v in env.values()) or safe_environment(env) != env:
                raise EvidenceError("invalid environment projection")
            credentials = component["credential_presence"]
            if set(credentials) != CREDENTIAL_KEYS or any(type(v) is not bool for v in credentials.values()):
                raise EvidenceError("invalid credential presence projection")
            policy = component["policy"]
            if policy == {"present": False} and type(policy["present"]) is bool:
                continue
            if (set(policy) != {"present", "enabled", "updated_at", "value_type", "mode", "invoice_override", "defaults_by_plan"}
                    or policy["present"] is not True or type(policy["enabled"]) is not bool):
                raise EvidenceError("incomplete dynamic policy")
            projected = safe_policy((policy["enabled"], _timestamp(policy["updated_at"]), policy["value_type"],
                                     policy["mode"], policy["invoice_override"], policy["defaults_by_plan"]))
            if projected != policy:
                raise EvidenceError("invalid dynamic policy projection")
        policies = [component["policy"] for component in body["components"].values()]
        if policies[0] != policies[1]:
            raise EvidenceError("components have different dynamic policies")
        return captured_at
    except EvidenceError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError):
        raise EvidenceError("invalid runtime evidence structure") from None


def compare(first, last):
    if validate(last) < validate(first):
        raise EvidenceError("runtime evidence timestamps are reversed")
    left, right = first["evidence"], last["evidence"]
    if left["source_sha"] != right["source_sha"] or left["components"] != right["components"]:
        raise EvidenceError("OCR runtime configuration changed; review before accepting parity")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    collector = commands.add_parser("collect-readonly")
    collector.add_argument("--baseline", required=True)
    collector.add_argument("--output", required=True)
    comparison = commands.add_parser("compare")
    comparison.add_argument("first")
    comparison.add_argument("last")
    args = parser.parse_args()
    try:
        if args.command == "compare":
            compare(json.loads(Path(args.first).read_text()), json.loads(Path(args.last).read_text()))
            print("PASS OCR runtime configuration unchanged; provider results and learned examples are separate")
            return 0
        result = collect(args.baseline)
        with Path(args.output).open("x") as stream:
            stream.write(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
        print("OCR runtime read-only evidence saved:", result["sha256"])
    except Exception as exc:
        print(str(exc) if isinstance(exc, EvidenceError) else "Evidence collection failed; diagnostic details withheld")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
