"""Pure identities and bounded snapshots for invoice correction events."""
import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime

from pearnly_cowork.ocr.feedback.contracts import TRACKED_FIELDS, compute_field_corrections, primary_seller

SCHEMA = "cowork-feedback-v1"
MAX_SAMPLES = 2000
MAX_SNAPSHOT_BYTES = 1024 * 1024


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def scope_key(site, workspace, client):
    return digest([site, workspace, client or ""])


def events(*, site, workspace, client, actor, history, version, ai_pages, edited_pages):
    if type(version) is not int or version < 2:
        raise ValueError("A saved edit revision is required")
    if not isinstance(ai_pages, list) or not isinstance(edited_pages, list):
        raise ValueError("Invoice page evidence is required")
    for page in ai_pages + edited_pages:
        if not isinstance(page, dict) or page.get("_document_type", "invoice") not in ("invoice", "auto"):
            raise ValueError("Correction learning accepts invoice pages only")
    tax, seller = primary_seller(edited_pages)
    if not tax:
        tax, seller = primary_seller(ai_pages)
    scope = scope_key(site, workspace, client)
    result = []
    for correction in compute_field_corrections(ai_pages, edited_pages):
        body = {"site_key": site, "workspace": workspace, "client": client or "", "scope_key": scope,
                "source_history": history, "history_revision": version, "seller_tax": tax or "",
                "seller_name": seller or "", **correction}
        key = digest([scope, history, version, correction["field_name"]])
        body.update(event_key=key, sample_key=digest([scope, tax or "", correction["field_name"],
                                                    correction["ai_value"] or ""]),
                    fingerprint=digest(body), actor=actor)
        result.append(body)
    return result


def snapshot(body):
    if (not isinstance(body, dict) or set(body) != {"schema", "site", "actor", "workspace", "client",
                                                  "captured_at", "examples_by_tax"}
            or body.get("schema") != SCHEMA):
        raise ValueError("Invalid correction snapshot schema")
    if (any(not isinstance(body[key], str) or not body[key] for key in ("site", "actor", "workspace", "captured_at"))
            or not isinstance(body["client"], str)):
        raise ValueError("Invalid correction snapshot scope")
    datetime.fromisoformat(body["captured_at"])
    samples = body.get("examples_by_tax")
    if (not isinstance(samples, dict) or any(not isinstance(tax, str) or not isinstance(rows, list)
            or len(rows) > 20 for tax, rows in samples.items())):
        raise ValueError("Invalid correction snapshot samples")
    if sum(len(rows) for rows in samples.values()) > MAX_SAMPLES:
        raise ValueError("Correction snapshot exceeds the sample limit")
    names = set()
    for rows in samples.values():
        for row in rows:
            if (not isinstance(row, dict) or set(row) != {"field_name", "ai_value", "corrected_value",
                    "use_count", "event_id", "event_fingerprint", "source_history"}
                    or row["field_name"] not in TRACKED_FIELDS
                    or row["ai_value"] is not None and not isinstance(row["ai_value"], str)
                    or not isinstance(row["corrected_value"], str) or not row["corrected_value"]
                    or type(row["use_count"]) is not int or row["use_count"] < 1
                    or not isinstance(row["event_id"], str) or not re.fullmatch(r"CWFC-[0-9a-f]{64}", row["event_id"])
                    or not isinstance(row["event_fingerprint"], str) or not re.fullmatch(r"[0-9a-f]{64}", row["event_fingerprint"])
                    or not isinstance(row["source_history"], str) or not row["source_history"]):
                raise ValueError("Invalid correction snapshot sample")
            if row["event_id"] in names:
                raise ValueError("Duplicate correction snapshot source")
            names.add(row["event_id"])
    if len(encode(body).encode()) > MAX_SNAPSHOT_BYTES:
        raise ValueError("Correction snapshot exceeds the byte limit")
    return {"body": deepcopy(body), "sha256": digest(body)}


def verify_snapshot(value):
    if not isinstance(value, dict) or set(value) != {"body", "sha256"}:
        raise ValueError("Invalid correction snapshot envelope")
    expected = snapshot(value["body"])
    if expected["sha256"] != value["sha256"]:
        raise ValueError("Correction snapshot changed")
    return value["body"]
