"""ETA delivery into native Frappe/RQ, without occupying an OCR worker while waiting.

Locked Frappe explicitly disables the RQ scheduler. This small process only moves
ready durable IDs into its native queue; it never executes business/OCR code.
SQL repair also runs here, because product Sites may disable the general native
scheduler. It only repairs registered OCR work and never starts business writes.
"""
import json
import os
import time

import frappe
from frappe.utils.background_jobs import get_redis_conn

DUE = "pearnly:ocr:due"
_repaired = {}
_pruned = {}
_cursor = 0


def later(name, delay):
    member = json.dumps([frappe.local.site, name])
    get_redis_conn().zadd(DUE, {member: time.time() + delay})


def deliver(client):
    from .work import kick
    from frappe.utils import get_sites

    sites = set(get_sites())
    for member in client.zrangebyscore(DUE, "-inf", time.time(), start=0, num=20):
        try:
            site, name = json.loads(member)
            if not isinstance(site, str) or not isinstance(name, str):
                raise ValueError("Invalid queue hint")
        except (ValueError, TypeError):
            # Hints are repairable from SQL; one damaged hint must not starve all
            # later tenants in this bounded delivery batch.
            client.zrem(DUE, member)
            continue
        if site not in sites:
            client.zrem(DUE, member)
            continue
        try:
            frappe.init(site=site)
            frappe.connect()
            frappe.set_user("Administrator")
            if not frappe.conf.maintenance_mode and not frappe.conf.get("pearnly_ocr_paused"):
                if kick(name):
                    client.zrem(DUE, member)
        except Exception as exc:
            # SQL repair redelivers this item. A broken Site cannot monopolize
            # the first 20 hints and prevent another tenant from progressing.
            client.zrem(DUE, member)
            print("OCR hint deferred:", type(exc).__name__, flush=True)
        finally:
            frappe.destroy()


def repair_one():
    """Round-robin one Site per tick; at most one bounded sweep/minute per Site."""
    from frappe.utils import get_sites
    from .work import sweep, prune_checkpoints

    global _cursor
    sites = sorted(get_sites())
    if not sites:
        return
    site = sites[_cursor % len(sites)]
    _cursor += 1
    now = time.monotonic()
    if now - _repaired.get(site, -60) < 60:
        return
    _repaired[site] = now
    try:
        frappe.init(site=site)
        frappe.connect()
        frappe.set_user("Administrator")
        if (frappe.conf.maintenance_mode or frappe.conf.get("pearnly_ocr_paused")
                or not frappe.conf.get("pearnly_ocr_budget_redis")):
            return
        sweep()
        if now - _pruned.get(site, -86400) >= 86400:
            prune_checkpoints()
            frappe.db.commit()
            _pruned[site] = now
    except Exception as exc:
        print("OCR SQL repair deferred:", type(exc).__name__, flush=True)
    finally:
        frappe.destroy()


def main():
    os.chdir(os.path.join(os.environ.get("FRAPPE_BENCH", "/home/frappe/frappe-bench"), "sites"))
    client = get_redis_conn()  # Native workers also read common config without a Site.
    while True:
        try:
            deliver(client)
        except Exception as exc:
            print("OCR dispatcher retry:", type(exc).__name__, flush=True)
        repair_one()
        time.sleep(1)


if __name__ == "__main__":
    main()
