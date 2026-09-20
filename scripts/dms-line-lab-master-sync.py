"""Real external readback of this lab's model/color CRUD; never submits an order.

Exercises the same bounded-fresh conversation, editor snapshot and preflight
entrypoints used by LINE. Private evidence contains only our marked fixture.
"""
import argparse
import json
import os
import time
from pathlib import Path

import frappe

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("phase", choices=("create", "rename", "disable", "delete"))
args = parser.parse_args()
SITE = "dms-line-lab.localhost"
os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site=SITE, sites_path=".")
frappe.connect()
frappe.set_user("Administrator")

from pearnly_dms.adapter import master_shared
from pearnly_dms.booking.booking_preflight import readonly_preflight
from pearnly_dms.booking.conversation.qa_util import car_label, row_name, find_row
from pearnly_dms.editor.edit_snapshot import read_edit_snapshot
from pearnly_dms.intake.service import run_logged_in
from pearnly_dms.masters.company_banks import fetch_payment_bank_masters
from pearnly_dms.ocr.ocr_service import resolve_dms_endpoint

try:
    private = Path(SITE) / "private"
    journal = json.loads((private / "line-lab-master-fixture.json").read_text())
    car, paint = journal["car"], journal["carpaint"]
    operator = frappe.get_doc("DMS Operator", {"display_name": "E2E DMS operator"})
    endpoint = resolve_dms_endpoint(operator.user, None)
    start = time.monotonic()
    # A normal new conversation must converge within its existing 90s bound.
    masters = master_shared.get_session_masters(endpoint)
    exists = args.phase in ("create", "rename")
    row = find_row(masters["cars"], car)
    if bool(row) != exists or (args.phase == "rename" and "renamed" not in car_label(row)):
        raise AssertionError("Conversation cache has not converged yet; rerun after the 90s freshness bound")
    paints = master_shared.get_session_paints(endpoint, car) if exists else []
    if exists:
        assert find_row(paints, paint)
        if args.phase == "rename":
            assert "สีใหม่" in row_name(find_row(paints, paint))
    snapshot = read_edit_snapshot(endpoint, car_ids=[car] if exists else [])
    assert snapshot and bool(find_row(snapshot["masters"]["cars"], car)) == exists
    if exists:
        assert find_row(snapshot["paints"][car], paint)

    qa_path = private / "line-lab-master-draft.json"
    if args.phase == "create":
        answers = {}
        for field, key in (("place", "place_books"), ("term", "term_sales"), ("regis", "regis_behalfs")):
            item = masters[key][0]
            answers[field] = {"id": str(item[0]), "name": row_name(item)}
        answers.update(car={"id": car, "label": car_label(row)},
                       paint={"id": paint, "name": row_name(find_row(paints, paint))},
                       delivery_date_be="21/10/2569", regis_name="E2E260920")
        advisor = masters["advisors"][0]
        qa = {"answers": answers, "advisor": {"id": str(advisor[0]), "name": row_name(advisor)},
              "payments": [{"channel": "cash", "amount": "1"}]}
        qa_path.write_text(json.dumps(qa, ensure_ascii=False))
        qa_path.chmod(0o600)
    else:
        qa = json.loads(qa_path.read_text())

    def check(client, adapter):
        return readonly_preflight(client, adapter, qa,
                                  fetch_masters=lambda cl: cl.fetch_masters(strict=True),
                                  fetch_payment_banks=lambda ad: fetch_payment_bank_masters(ad, client=client))
    preflight = run_logged_in(endpoint, check, authoritative_read=True)
    expected = "ok" if args.phase == "create" else "changed" if args.phase == "rename" else "unmatched"
    assert preflight.get("status") == expected, (args.phase, preflight.get("status"), preflight.get("error_code"))
    if not exists:
        assert preflight["field"] == "car"
    evidence = {"phase": args.phase, "model_id": car, "paint_id": paint,
                "conversation_present": bool(row), "editor_present": exists,
                "preflight": preflight["status"], "preflight_field": preflight.get("field"),
                "elapsed_seconds": round(time.monotonic() - start, 2), "external_orders_written": 0}
    out = private / ("line-lab-master-sync-" + args.phase + ".json")
    out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
    out.chmod(0o600)
    print(json.dumps(evidence, ensure_ascii=False))
finally:
    frappe.destroy()
