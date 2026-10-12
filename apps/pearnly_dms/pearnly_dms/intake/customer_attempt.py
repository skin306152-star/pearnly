"""Durable customer dispatch evidence in the native product push ledger.

Only a verified result can recover as success. A matching identity search is not
evidence that this request wrote it. Unknown dispatches survive reset/new nonce;
neither a worker redelivery nor another request may blindly replay that identity.

An unknown dispatch is not a permanent lock: ``begin`` keeps a salted digest of the
values the write must read back, and ``reconcile`` (called by the next request that
touches the same customer, or by the operator's retry) re-reads DMS read-only and
closes it: the same values → success; read fine but different or cleanly absent, once
the original worker is certainly gone → not written, identity released; anything
unreadable → stays blocked. Nothing is ever re-sent.
"""
import hashlib
import json
import logging
from urllib.parse import urlsplit, urlunsplit

import frappe

from pearnly_dms.contracts.customer_merge import unknown
from pearnly_dms.contracts.endpoint import endpoint_name
from pearnly_dms.contracts.id_validate import normalize_thai_id

LOG = "Pearnly Push Log"
#: A dispatch older than this has certainly lost its worker (the LINE worker timeout is
#: 600 s, ``line/out.py``); same grace as ``booking.recovery.WORKER_GRACE_SECONDS``.
WORKER_GRACE_SECONDS = 660

logger = logging.getLogger(__name__)


def _state_store():
    """不淘汰的状态存储（``pearnly_core.execution.state``；用到时才导入，离线检查导入本模块不需要共用层）。"""
    from pearnly_core.execution import state as pearnly_state

    return pearnly_state.store()



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


def _age(creation):
    return (frappe.utils.get_datetime() - frappe.utils.get_datetime(creation)).total_seconds()


def _snapshot_customer(endpoint, people_id):
    """本地完整快照里这一证号：``(True, 客户号)`` 有；``(True, None)`` 确实没有；``(False, None)`` 快照用不了。"""
    from pearnly_dms.snapshots.directory import CustomerDetailPending, Directory

    try:
        look = Directory(endpoint).lookup_customer(people_id)
    except CustomerDetailPending as pending:
        return True, pending.customer_id
    except Exception:
        return False, None
    return True, (str(look["customer_id"]) if look["found"] else None)


class Attempt:
    def __init__(self, endpoint, request_id, actor, *, fields, mode, customer_id, addresses, baseline):
        if not request_id or not actor:
            raise ValueError("A server-owned customer write request and actor are required")
        self.endpoint = endpoint_name(endpoint)
        self.actor = actor
        self.mode = mode
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
        # Plain identity numbers stay in memory: a later check searches by the one whose
        # digest an earlier dispatch recorded; the ledger only ever holds digests.
        self._people = sorted({value for kind, value in targets if kind == "people" and value})

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

    def begin(self, verify=None):
        # Lock just the durable reservation, never hold a Redis lease across HTTP.
        cache = _state_store()
        with cache.lock(cache.make_key("dms-customer-dispatch:" + self.system),
                        timeout=15, blocking_timeout=5):
            frappe.db.commit()  # discard stale MariaDB repeatable-read view
            if frappe.db.exists(LOG, self.name) or self._pending():
                raise unknown()
            request = {"operation": "customer_write",
                       "fingerprint": self.fingerprint, "identities": self.identities}
            if verify:
                request["verify"] = self._evidence(verify)
            doc = frappe.new_doc(LOG)
            doc.update({"status": "manual", "endpoint": self.endpoint, "trigger": "line",
                        "actor": self.actor, "business_key": self.identity, "max_retries": 0,
                        "error_msg": "ERR_DMS_CUSTOMER_UNCONFIRMED",
                        "request_body": json.dumps(request),
                        "response_body": json.dumps({"submitted": True, "retry_safe": False})})
            doc.insert(ignore_permissions=True, set_name=self.name)
            frappe.db.commit()  # must exist durably BEFORE transport.post

    def _evidence(self, verify):
        """How to check this dispatch later, without plain personal data: the read mode,
        the native row ID (not personal), the field names and a digest salted with this
        ledger name of the values the write must read back."""
        expected = dict(verify.get("expected") or {})
        return {"mode": self.mode, "save_mode": verify.get("mode"),
                "customer_id": str(verify.get("customer_id") or ""),
                "keys": sorted(expected), "digest": _digest([self.name, expected])}

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

    # ── 结果未确认之后：只读核对、结案 ─────────────────────────────────────────
    def dispatched(self):
        """This request itself registered a dispatch (whatever its outcome)."""
        return bool(frappe.db.exists(LOG, self.name))

    def not_written(self):
        """This request's own dispatch is settled as not written (rejected, or checked absent)."""
        return frappe.db.get_value(LOG, self.name, "status") == "failed"

    def reconcile(self, client):
        """被挡时只读核对：挡着本请求的那几次结果未确认的写入，DMS 里到底写进去没有。

        对上 → 结案为成功（记下客户号与客户编号）；读得到但对不上、或建档确实查无此人，且登记
        已过 `WORKER_GRACE_SECONDS`（原任务一定已结束）→ 结案为没写进去，身份放行；读不到、
        还没到时限、没有核对依据的旧记录 → 原样留着继续挡。``client`` 只读、不重发任何写入；
        改状态只持短锁、只改仍是 manual 的行，锁里不发 HTTP。返回 ``{台账行: written/not_written}``。
        """
        settled = {}
        for row in self._blocking():
            try:
                verdict = self._check(client, row)
                if verdict and self._settle(row.name, *verdict):
                    settled[row.name] = verdict[0]
            except Exception as exc:  # a check is best effort: whatever fails keeps blocking
                logger.warning("DMS customer dispatch check failed (%s); kept blocked", type(exc).__name__)
        return settled

    def _blocking(self):
        rows = frappe.get_all(LOG, filters={"name": ["like", "CW-%"], "status": "manual"},
                              fields=["name", "request_body", "creation"], limit_page_length=0)
        return [row for row in rows
                if set(json.loads(row.request_body or "{}").get("identities") or []) & set(self.identities)]

    def _check(self, client, row):
        data = json.loads(row.request_body or "{}")
        verify = data.get("verify")
        if not isinstance(verify, dict) or not verify.get("digest"):
            return None  # no evidence to check against: stays blocked
        aged = _age(row.creation) > WORKER_GRACE_SECONDS
        keys = list(verify.get("keys") or [])
        absent_locally = False
        try:
            if verify.get("save_mode") == "create":
                recorded = set(data.get("identities") or [])
                people = [pid for pid in self._people if _digest([self.system, "people", pid]) in recorded]
                if len(people) != 1:
                    return None
                usable, known = _snapshot_customer(self.endpoint, people[0])
                absent_locally = usable and not known
                found = client.read_written_customer(mode="create", keys=keys, people_id=people[0],
                                                     known_id=known or "")
            else:
                found = client.read_written_customer(mode="overwrite", keys=keys,
                                                     customer_id=verify.get("customer_id") or "")
        except Exception as exc:
            logger.warning("DMS customer dispatch check unreadable (%s); kept blocked", type(exc).__name__)
            return None
        if found is None:
            # Search can miss a new customer for minutes; only a clean miss on both the live
            # search and the local snapshot, after the worker is gone, means not written.
            return ("not_written", None) if aged and absent_locally else None
        customer_id, cuscode, values = found
        if _digest([row.name, values]) == verify["digest"]:
            return "written", {"customer_id": customer_id, "cuscode": cuscode, "mode": verify.get("mode")}
        return ("not_written", None) if aged else None

    def _settle(self, name, verdict, found):
        if verdict == "written":
            result = {"ok": True, "success": True, **found, "reconciled": "written",
                      "response_body": {"adapter": "mrerp_dms", **found}}
            values = {"status": "success", "business_key": found["customer_id"],
                      "error_msg": "", "http_status": 200}
        else:
            result = {"ok": False, "success": False, "error_code": "ERR_DMS_CUSTOMER_SAVE",
                      "reconciled": "not_written",
                      "response_body": {"submitted": True, "reconciled": "not_written"}}
            values = {"status": "failed", "business_key": "",
                      "error_msg": "ERR_DMS_CUSTOMER_SAVE", "http_status": 0}
        values["response_body"] = json.dumps(result, ensure_ascii=False, default=str)
        cache = _state_store()
        with cache.lock(cache.make_key("dms-customer-dispatch:" + self.system),
                        timeout=15, blocking_timeout=5):
            frappe.db.commit()  # discard stale MariaDB repeatable-read view
            if frappe.db.get_value(LOG, name, "status") != "manual":
                return False  # the original worker or another check settled it first
            frappe.db.set_value(LOG, name, values)
            frappe.db.commit()
        return True
