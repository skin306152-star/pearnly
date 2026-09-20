"""Real MariaDB concurrency contract on owned local fixtures; no external DMS calls."""
from concurrent.futures import ThreadPoolExecutor
import os
import threading

import frappe

SITE = "dms-line-lab-b.localhost"
os.chdir("/home/frappe/frappe-bench/sites")

def connect(site=SITE):
    frappe.init(site=site, sites_path=".")
    frappe.connect()
    frappe.set_user("Administrator")

def trial(count, operation):
    from pearnly_core import line_session as store
    uid = "Ulab-concurrency-" + operation + "-" + str(count)
    nonce = "local-concurrency-fixture"
    connect()
    try:
        store.set_session(uid, "reviewing", {"nonce": nonce, "fixture": True})
    finally:
        frappe.destroy()
    barrier = threading.Barrier(count)
    def contender(index):
        connect()
        try:
            # Every contender first observes the same old state, like simultaneous requests.
            assert store.get_session(uid)["payload"]["nonce"] == nonce
            barrier.wait(timeout=20)
            if operation == "consume":
                result = store.consume_nonce(uid, "reviewing", nonce) is not None
            else:
                result = store.replace_review_payload(uid, nonce, {"nonce": "next-" + str(index)}, state="reviewing")
            frappe.db.commit()
            return result
        finally:
            frappe.destroy()
    with ThreadPoolExecutor(max_workers=count) as pool:
        winners = sum(pool.map(contender, range(count)))
    assert winners == 1, (count, operation, winners)
    print(f"PASS {count} concurrent {operation}: exactly one accepted")
    connect()
    try:
        store.clear_session(uid)
    finally:
        frappe.destroy()

for count in (10, 20):
    for operation in ("consume", "edit"):
        trial(count, operation)

# A LINE identity may be the same under two OAs on the same Provider. Their
# independent draft locks must not collide, and a stale owner must not unlock
# the replacement holder after its own lease has expired.
from pearnly_core import line_session as store
uid = "Ulab-lock-scope-fixture"
connect("dms-line-lab.localhost")
try:
    owner_a = store._lock_scope(uid)
    assert owner_a
finally:
    frappe.destroy()
connect()
try:
    owner_b = store._lock_scope(uid)
    assert owner_b, "One Site blocked an unrelated OA's draft"
    assert store._lock_scope(uid) is None
    store._unlock_scope(uid, "stale-owner")
    assert store._lock_scope(uid) is None, "A stale owner deleted the live lease"
    store._unlock_scope(uid, owner_b)
    replacement = store._lock_scope(uid)
    assert replacement
    store._unlock_scope(uid, owner_b)
    assert store._lock_scope(uid) is None, "Expired owner deleted its successor"
    store._unlock_scope(uid, replacement)
finally:
    frappe.destroy()
connect("dms-line-lab.localhost")
try:
    assert store._lock_scope(uid) is None
    store._unlock_scope(uid, owner_a)
finally:
    frappe.destroy()
print("PASS Site isolation and atomic owner-checked unlock in real Redis")

connect()
try:
    uid = "Ulab-cancel-fixture"
    store.set_session(uid, "booking_review", {"nonce": "new-card"})
    assert not store.cancel_review(uid, "booking_review", "old-card")
    assert store.get_session(uid)["payload"]["nonce"] == "new-card"
    assert store.cancel_review(uid, "booking_review", "new-card")
    store.set_session(uid, "booking_review", {"nonce": "submitting"})
    assert store.consume_nonce(uid, "booking_review", "submitting")
    assert store.record_booking_attempt(uid, "submitting", "LAB-NOT-A-REAL-BOOKING", "local-owner")
    # TTL is for disposable drafts, never for unknown external side effects.
    frappe.db.set_value(store.DT, {"line_user_id": uid}, "expires_at", frappe.utils.add_to_date(frappe.utils.now(), hours=-1))
    frappe.db.commit()
    assert store.get_session(uid)["payload"]["booking_attempt"]["nonce"] == "submitting"
    from pearnly_dms.line.identity import binding_changed
    binding_changed(uid)
    frappe.db.commit()
    assert store.get_session(uid)["payload"]["booking_attempt"]["nonce"] == "submitting"
    for operation in (lambda: store.clear_session(uid), lambda: store.set_session(uid, "collecting", {})):
        try:
            operation()
        except RuntimeError:
            frappe.db.rollback()
        else:
            raise AssertionError("An unresolved submission marker was discarded")
    assert store.get_session(uid)["payload"]["booking_attempt"]["nonce"] == "submitting"
    assert store.clear_booking_attempt(uid, "submitting")
finally:
    frappe.destroy()
print("PASS stale cancel, restart, expiry and binding changes cannot delete unresolved submission evidence")
