"""Real Desk/FieldGroup/Dialog browser smoke, only task-owned offline Site.

Requires a temporary bench serve on internal localhost:8000. Never starts a
worker: the button's exact UUID-owned queued jobs are inspected then deleted.
Native lab credentials are synthetic, not production secrets. No external IO.
The queued request and route hide/show recovery are native. Only the final
get_status response is replaced to exercise UI completion without running the
collector; the success toast is not evidence of a completed data collection.
"""
from pathlib import Path
import json
import runpy
import time
from urllib.parse import urlsplit

import frappe
from frappe.utils.background_jobs import get_queue
from playwright.sync_api import sync_playwright

lab = runpy.run_path(str(Path(__file__).with_name("test-dms-snapshot-store-native.py")))
Fixture = lab["SnapshotLabCase"]
STATUS = "pearnly_dms.connections.snapshots.get_status"
REFRESH = "pearnly_dms.connections.snapshots.request_refresh"
DATASETS = ("vehicles", "customers", "geo", "company")
CACHE_FIELDS = tuple(dataset + "_" + suffix
    for dataset in (*DATASETS, "masters")
    for suffix in ("state", "progress", "count", "as_of", "completed_at", "error_code"))


def no_cache_fields(page):
    # Inspect existence, not visibility: hiding the removed fields is not enough.
    fields = page.locator("[data-fieldname]").evaluate_all(
        "nodes => nodes.map(node => node.dataset.fieldname)")
    assert not set(fields).intersection(CACHE_FIELDS), fields
    assert not any(name.endswith("_state") for name in fields), fields


def count_requests(requests, method, api):
    return sum(verb == method and api in url for verb, url in requests)


def polling_stopped(page, requests):
    page.wait_for_timeout(250)  # allow an already completed route handler to settle
    before = count_requests(requests, "GET", STATUS)
    page.wait_for_timeout(3500)  # longer than the product's three-second poll
    assert count_requests(requests, "GET", STATUS) == before


def main():
    Fixture.setUpClass()
    browser = None
    queued = []
    Fixture.scope_keys.add(lab["store"].scope(Fixture.template))
    queue = get_queue("dms_sync")
    baseline = queue.get_job_ids()
    requests = []
    errors = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            context = browser.new_context(viewport={"width": 1365, "height": 1000}, locale="zh-CN",
                extra_http_headers={"X-Frappe-Site-Name": lab["SITE"], "Accept-Language": "zh-CN,zh;q=0.9"})
            context.route("**/*", lambda route: route.continue_()
                if urlsplit(route.request.url).hostname in {"127.0.0.1", "localhost"} else route.abort())
            reply = context.request.post("http://127.0.0.1:8000/api/method/login", data={
                "usr": "Administrator", "pwd": "unified-release-lab-only"})
            assert reply.ok, reply.status
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(error.stack))
            page.on("request", lambda request: requests.append((request.method, request.url)))
            with page.expect_response(lambda r: STATUS in r.url) as response:
                page.goto("http://127.0.0.1:8000/desk/pearnly-connect")
            assert response.value.ok
            assert tuple(row["dataset"] for row in response.value.json()["message"]["datasets"]) == DATASETS
            assert not any(row["state"] in {"queued", "running"}
                           for row in response.value.json()["message"]["datasets"])
            page.wait_for_function("typeof frappe !== 'undefined' && !!frappe.pearnly?.connect", timeout=30000)
            page.wait_for_selector('[data-fieldname="state"]', timeout=15000)
            # Use the same installed glossary and actual user language as Desk;
            # do not mutate Administrator's preview language to satisfy the test.
            labels = page.evaluate("""async () => {
                const t = await frappe.pearnly.texts('dms.connect.cache.');
                return {read: t('dms.connect.cache.read'),
                    processing: t('dms.connect.cache.processing'),
                    success: t('dms.connect.cache.state.succeeded'),
                    ...Object.fromEntries(['vehicles', 'customers', 'geo', 'company']
                        .map(key => [key, t('dms.connect.cache.dataset.' + key)]))};
            }""")
            assert all(value and not value.startswith("dms.connect.cache.") for value in labels.values())
            no_cache_fields(page)
            assert not page.locator("#freeze:visible").count()
            polling_stopped(page, requests)  # idle pages must not poll indefinitely
            page.get_by_role("button", name=labels["read"], exact=True).click()
            dialog = page.locator(".modal:visible")
            dialog.locator('input[data-fieldname="vehicles"]').wait_for(state="visible")
            fields = dialog.locator('input[type="checkbox"][data-fieldname]').evaluate_all(
                "nodes => nodes.map(node => node.dataset.fieldname)")
            assert tuple(fields) == DATASETS, fields
            for dataset in DATASETS:
                assert dialog.get_by_text(labels[dataset], exact=True).count() == 1
                dialog.locator(f'input[data-fieldname="{dataset}"]').check()
            with page.expect_response(lambda r: REFRESH in r.url) as response:
                dialog.get_by_role("button", name=labels["read"], exact=True).click()
            assert response.value.ok and response.value.request.method == "POST"
            payload = response.value.json()["message"]["datasets"]
            assert tuple(row["dataset"] for row in payload) == DATASETS, payload
            assert all(row["state"] == "queued" for row in payload), payload
            queued.extend(row["request_id"] for row in payload)
            assert len(set(queued)) == len(DATASETS), queued
            dialog.wait_for(state="hidden")
            page.locator("#freeze").wait_for(state="visible")
            assert labels["processing"] in page.locator("#freeze").inner_text()
            no_cache_fields(page)
            assert len(queue.get_job_ids()) == len(baseline) + len(DATASETS)
            own_jobs = set(queue.get_job_ids()) - set(baseline)
            assert count_requests(requests, "POST", REFRESH) == 1

            # Router lifecycle is real: hiding releases the overlay, returning
            # discovers the same server-owned request instead of submitting again.
            page.evaluate("frappe.set_route('pearnly-home')")
            page.wait_for_function("frappe.get_route()[0] === 'pearnly-home'")
            page.locator("#freeze").wait_for(state="hidden")
            polling_stopped(page, requests)
            with page.expect_response(lambda r: STATUS in r.url) as response:
                page.evaluate("frappe.set_route('pearnly-connect')")
            native_status = response.value.json()["message"]
            resumed = native_status["datasets"]
            assert tuple(row["dataset"] for row in resumed) == DATASETS, resumed
            assert [row["request_id"] for row in resumed] == queued, resumed
            assert all(row["state"] == "queued" for row in resumed), resumed
            page.locator("#freeze").wait_for(state="visible")
            assert set(queue.get_job_ids()) - set(baseline) == own_jobs
            assert count_requests(requests, "POST", REFRESH) == 1
            no_cache_fields(page)

            # Only terminal metadata is synthetic: no worker, collector or
            # cache write is used to manufacture a business-success assertion.
            terminal = {**native_status, "datasets": [
                {**row, "state": "succeeded", "count": 0, "as_of": time.time(),
                 "completed_at": time.time(), "stale": False, "error_code": ""}
                if row["dataset"] in DATASETS else row for row in native_status["datasets"]]}
            def complete_status(route):
                assert route.request.method == "GET"
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"message": terminal}))
            context.route("http://127.0.0.1:8000/api/method/" + STATUS + "*", complete_status)
            page.get_by_text(labels["success"], exact=True).wait_for(state="visible", timeout=10000)
            page.locator("#freeze").wait_for(state="hidden")
            polling_stopped(page, requests)
            assert count_requests(requests, "POST", REFRESH) == 1
            assert set(queue.get_job_ids()) - set(baseline) == own_jobs
            no_cache_fields(page)
            assert not errors, errors
            page.screenshot(path="/tmp/dms-manual-desk-native.png", full_page=True)
            print("PASS native Desk: four selected datasets, freeze, hide/show resumes the same requests without POST, "
                  "mocked completion unfreezes, no cache fields or idle polling; collector not executed")
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
