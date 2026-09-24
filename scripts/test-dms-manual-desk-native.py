"""Real Desk/FieldGroup/Dialog browser smoke, only task-owned offline Site.

Requires a temporary bench serve on internal localhost:8000. Never starts a
worker: the button's exact UUID-owned queued jobs are inspected then deleted.
Native lab credentials are synthetic, not production secrets. No external IO.
"""
from pathlib import Path
import runpy
import time
from urllib.parse import urlsplit

import frappe
from frappe.utils.background_jobs import get_queue
from playwright.sync_api import sync_playwright

lab = runpy.run_path(str(Path(__file__).with_name("test-dms-snapshot-store-native.py")))
Fixture = lab["SnapshotLabCase"]


def main():
    Fixture.setUpClass()
    browser = None
    queued = []
    Fixture.scope_keys.add(lab["store"].scope(Fixture.template))
    queue = get_queue("dms_sync")
    baseline = queue.get_job_ids()
    requests = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            context = browser.new_context(viewport={"width": 1365, "height": 1000}, locale="en-US",
                extra_http_headers={"X-Frappe-Site-Name": lab["SITE"]})
            context.route("**/*", lambda route: route.continue_()
                if urlsplit(route.request.url).hostname in {"127.0.0.1", "localhost"} else route.abort())
            reply = context.request.post("http://127.0.0.1:8000/api/method/login", data={
                "usr": "Administrator", "pwd": "unified-release-lab-only"})
            assert reply.ok, reply.status
            page = context.new_page()
            page.on("pageerror", lambda error: print("DESK ERROR:", error.stack))
            page.on("request", lambda request: requests.append(request.url))
            page.goto("http://127.0.0.1:8000/desk/pearnly-connect")
            page.wait_for_function("typeof frappe !== 'undefined' && !!frappe.pearnly?.connect", timeout=30000)
            try:
                page.wait_for_selector('[data-fieldname="masters_state"]', timeout=15000)
            except Exception:
                print("DESK BODY:", page.locator("body").inner_text()[:5000])
                print("DESK LANG:", page.evaluate("frappe.boot.lang"))
                raise
            page.get_by_role("button", name="Read / refresh selected data", exact=True).click()
            dialog = page.locator(".modal:visible")
            dialog.locator('input[data-fieldname="customers"]').uncheck()
            dialog.locator('input[data-fieldname="geo"]').uncheck()
            with page.expect_response(lambda r: "connections.snapshots.request_refresh" in r.url) as response:
                dialog.get_by_role("button", name="Read / refresh selected data", exact=True).click()
            payload = response.value.json()["message"]["datasets"]
            assert len(payload) == 1 and payload[0]["dataset"] == "masters", payload
            assert payload[0]["state"] == "queued", payload
            queued.append(payload[0]["request_id"])
            dialog.wait_for(state="hidden")
            assert not page.locator("#freeze:visible").count()
            # Repeat through the actual native dialog: one accepted queue identity.
            page.get_by_role("button", name="Read / refresh selected data", exact=True).click()
            dialog = page.locator(".modal:visible")
            dialog.locator('input[data-fieldname="masters"]').check()
            dialog.locator('input[data-fieldname="customers"]').uncheck()
            dialog.locator('input[data-fieldname="geo"]').uncheck()
            with page.expect_response(lambda r: "connections.snapshots.request_refresh" in r.url) as response:
                dialog.get_by_role("button", name="Read / refresh selected data", exact=True).click()
            assert response.value.json()["message"]["datasets"][0]["request_id"] == queued[0]
            dialog.wait_for(state="hidden")
            page.locator(".modal-backdrop").wait_for(state="hidden")
            assert len(queue.get_job_ids()) == len(baseline) + 1
            page.screenshot(path="/tmp/dms-manual-desk-native.png", full_page=True)
            page.evaluate("frappe.set_route('pearnly-home')")
            page.wait_for_timeout(500)
            before = sum("connections.snapshots.get_status" in url for url in requests)
            page.wait_for_timeout(3500)
            assert sum("connections.snapshots.get_status" in url for url in requests) == before
            print("PASS native Desk: button/Dialog, selected dataset, no long freeze, queue dedup, route-leave polling stop")
            browser.close()
            browser = None
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass  # The Playwright context may have closed after an exception.
        for job_id in queue.get_job_ids():
            if job_id in baseline:
                continue
            job = queue.fetch_job(job_id)
            values = job.kwargs.get("kwargs", {})
            assert job.kwargs.get("site") == lab["SITE"]
            assert job.kwargs.get("method") == "pearnly_dms.snapshots.manual.execute"
            assert values.get("endpoint") == Fixture.template
            assert values.get("request_id") in queued
            queue.remove(job.id)
            job.delete()
        assert queue.get_job_ids() == baseline
        Fixture.cleanup()


if __name__ == "__main__":
    main()
