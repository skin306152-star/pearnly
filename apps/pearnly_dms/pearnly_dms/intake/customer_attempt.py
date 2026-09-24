"""Durable customer dispatch evidence in the native product push ledger.

Only a verified result can recover as success. A matching identity search is not
evidence that this request wrote it. Unknown dispatches survive reset/new nonce;
neither a worker redelivery nor another request may blindly replay that identity.
"""
import hashlib
import json
from urllib.parse import urlsplit, urlunsplit

import frappe

from pearnly_dms.contracts.customer_merge import unknown
from pearnly_dms.contracts.endpoint import endpoint_name
from pearnly_dms.contracts.id_validate import normalize_thai_id

LOG = "Pearnly Push Log"


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     default=str).encode()).hexdigest()


def _system(url):
    parts = urlsplit(str(url))
    path = parts.path.rstrip("/")
    if path.endswith("/index.php"):
        path = path[:-10]
    host = str(parts.hostname or "").lower()
    if parts.port and (parts.scheme.lower(), parts.port) not in (("https", 443), ("http", 80)):
        host += ":" + str(parts.port)
    return urlunsplit((parts.scheme.lower(), host, path.rstrip("/"), "", ""))


class Attempt:
    def __init__(self, endpoint, request_id, actor, *, fields, mode, customer_id, addresses, baseline):
        if not request_id or not actor:
            raise ValueError("A server-owned customer write request and actor are required")
        self.endpoint = endpoint_name(endpoint)
        self.actor = actor
        self.name = "CW-" + _digest([self.endpoint, actor, request_id])
        self.fingerprint = _digest([mode, customer_id, fields, addresses, baseline])
        url = frappe.db.get_value("Pearnly ERP Endpoint", self.endpoint, "system_url")
        if not url:
            raise ValueError("Customer write scope is missing")
        self.system = _digest(_system(url))
        targets = [("customer", str(customer_id or "")),
                   ("people", normalize_thai_id(fields.get("people_id") or "")),
                   ("people", normalize_thai_id((baseline or {}).get("people_id") or ""))]
        self.identities = sorted({_digest([self.system, kind, value]) for kind, value in targets if value})
        if not self.identities:
            raise ValueError("Customer write identity is missing")
        self.identity = "CW-ID-" + self.identities[0]

    def _pending(self):
        # One marker can reserve both old/new identities and the stable native
        # customer ID. Phone-only edits must not escape a previous ID-card write.
        for row in frappe.get_all(LOG, filters={"name": ["like", "CW-%"], "status": "manual"},
                                  fields=["request_body"], limit_page_length=0):
            data = json.loads(row.request_body or "{}")
            if set(data.get("identities") or []) & set(self.identities):
                return True
        return False

    def previous(self):
        row = frappe.db.get_value(LOG, self.name, ["status", "request_body", "response_body"], as_dict=True)
        if not row:
            if self._pending():
                raise unknown()
            return None
        request = json.loads(row.request_body or "{}")
        if request.get("fingerprint") != self.fingerprint or row.status != "success":
            raise unknown()
        result = json.loads(row.response_body or "{}")
        if not result.get("success") or not result.get("customer_id"):
            raise unknown()
        return {**result, "recovered": True, "customer_write_log": self.name}

    def begin(self):
        # Lock just the durable reservation, never hold a Redis lease across HTTP.
        cache = frappe.cache()
        with cache.lock(cache.make_key("dms-customer-dispatch:" + self.system),
                        timeout=15, blocking_timeout=5):
            frappe.db.commit()  # discard stale MariaDB repeatable-read view
            if frappe.db.exists(LOG, self.name) or self._pending():
                raise unknown()
            doc = frappe.new_doc(LOG)
            doc.update({"status": "manual", "endpoint": self.endpoint, "trigger": "line",
                        "actor": self.actor, "business_key": self.identity, "max_retries": 0,
                        "error_msg": "ERR_DMS_CUSTOMER_UNCONFIRMED",
                        "request_body": json.dumps({"operation": "customer_write",
                            "fingerprint": self.fingerprint, "identities": self.identities}),
                        "response_body": json.dumps({"submitted": True, "retry_safe": False})})
            doc.insert(ignore_permissions=True, set_name=self.name)
            frappe.db.commit()  # must exist durably BEFORE transport.post

    def finish(self, result):
        if not frappe.db.exists(LOG, self.name):
            return result  # preflight conflict / no-op, no dispatch occurred
        row_status = frappe.db.get_value(LOG, self.name, "status")
        if row_status == "success" and not result.get("success"):
            return self.previous()  # a later login/cache failure cannot erase proven success
        response = result.get("response_body") or {}
        rejected = bool(response.get("rejected") or response.get("customer_conflict"))
        unknown_result = not result.get("success") and not rejected
        if unknown_result:
            result = {**result, "ok": False, "success": False,
                      "error_code": "ERR_DMS_CUSTOMER_UNCONFIRMED",
                      "response_body": {**response, "submitted": True, "retry_safe": False}}
            from pearnly_dms.line.errors import friendly
            result["error_friendly"] = friendly(result["error_code"])
        values = {"response_body": json.dumps(result, ensure_ascii=False, default=str)}
        if not unknown_result:
            values.update(status="success" if result.get("success") else "failed",
                          business_key=str(result.get("customer_id") or ""),
                          error_msg=str(result.get("error_code") or ""),
                          http_status=200 if result.get("success") else 0)
        frappe.db.set_value(LOG, self.name, values)
        frappe.db.commit()
        return {**result, "customer_write_log": self.name}
