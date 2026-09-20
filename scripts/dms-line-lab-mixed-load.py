"""100 local identities / 2 Sites and mixed 10/20-client real-backend workloads.

Only two existing external TEST credentials are used. This is not a claim of
20 independent external accounts. Each round creates one uniquely marked test
booking after an editor save; other drafts stop before business submission.
OCR uses the repository's synthetic ID fixture. No real payment is performed.
"""
import argparse
import base64
import copy
import json
import os
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlencode

import frappe
import requests

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('phase', choices=('setup', 'run', 'cleanup'))
parser.add_argument('--clients', type=int, choices=(10, 20), default=10)
parser.add_argument('--label', choices=('', 'pool2'), default='')
parser.add_argument('--execute-lab-writes', action='store_true', required=True)
args = parser.parse_args()
SITES = ('dms-line-lab.localhost', 'dms-line-lab-b.localhost')
MARKER = 'E2E260920LOAD'
os.chdir('/home/frappe/frappe-bench/sites')


def connect(site=SITES[0]):
    frappe.init(site=site, sites_path='.')
    frappe.connect()
    frappe.set_user('Administrator')


def journal_path(site):
    return Path(site) / 'private/line-lab-load-fixtures.json'


def persist(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    path.chmod(0o600)


def setup():
    from pearnly_core import line_binding
    from pearnly_dms.operators import roster
    cfg = json.loads(Path('/lab-private/external-fixture.json').read_text())
    assert cfg['source_site'] == 'testdms.pearnly.com'
    for site in SITES:
        connect(site)
        try:
            path = journal_path(site)
            rows = json.loads(path.read_text()) if path.exists() else []
            for index in range(len(rows), 50):
                manager = index % 5 == 4
                creds = ({'username': cfg['endpoint']['admin_username'], 'password': cfg['endpoint']['admin_password']}
                         if manager else cfg['operators'][0])
                username = creds['username'] if site == SITES[0] else 'local-load-' + str(index)
                password = creds['password'] if site == SITES[0] else 'local-load-only'
                label = f'{MARKER} {index:03}'
                # Recover setup interruption without duplicating a native User.
                existing = frappe.db.get_value('DMS Operator', {'display_name': label}, 'name')
                op = (roster._row(frappe.get_doc('DMS Operator', existing)) if existing else
                      roster.create_operator(label, username, password, dms_role='admin' if manager else 'sales',
                                             can_query_dms=int(manager)))
                uid = f'Ulab-load-{index:03}'
                line_binding.bind(uid, op['user'], label)
                rows.append({'index': index, 'user': op['user'], 'line': uid, 'name': op['name'], 'manager': manager})
                persist(path, rows)
            frappe.db.commit()
            assert len(rows) == 50 and len({r['user'] for r in rows}) == 50
            print(json.dumps({'site': site, 'identities': len(rows), 'managers': sum(r['manager'] for r in rows)}), flush=True)
        finally:
            frappe.destroy()


def cleanup():
    from pearnly_core import line_session, line_binding
    from pearnly_dms.operators import roster
    for site in SITES:
        connect(site)
        try:
            for row in json.loads(journal_path(site).read_text()):
                session = line_session.get_session(row['line'])
                assert not ((session or {}).get('payload') or {}).get('booking_attempt'), 'Reconcile test submission before cleanup'
                line_session.clear_session(row['line'])
                line_binding.unbind(row['line'])
                if frappe.db.exists('DMS Operator', row['name']):
                    doc = frappe.get_doc('DMS Operator', row['name'])
                    assert doc.display_name.startswith(MARKER)
                    roster.delete_operator(row['name'])
            frappe.db.commit()
            print(json.dumps({'site': site, 'owned_operators_disabled': 50, 'external_accounts_deleted': 0}), flush=True)
        finally:
            frappe.destroy()


def run():
    from pearnly_core import line_session as store
    from pearnly_dms.ocr.ocr_service import resolve_dms_endpoint
    from pearnly_dms.line import cards
    from pearnly_dms.query import query_cards
    from pearnly_dms.booking.booking_readback import docno_equal
    from pearnly_dms.intake.service import run_logged_in
    from pearnly_dms.booking import delivery
    cfg = json.loads(Path('/lab-private/external-fixture.json').read_text())
    fixtures = json.loads(journal_path(SITES[0]).read_text())
    source = json.loads(Path(SITES[0] + '/private/line-lab-fields-expected.json').read_text())
    image = base64.b64encode(Path('/lab-private/ocr-load-sample.jpg').read_bytes()).decode()
    suffix = f'-{args.label}' if args.label else ''
    report_path = Path(SITES[0]) / f'private/line-lab-load-{args.clients}{suffix}.json'
    assert not report_path.exists(), 'Do not replay a round; inspect its existing evidence'
    connect()
    try:
        for row in fixtures[:args.clients]:
            assert not store.get_session(row['line']), 'Test identity has existing work'
            if row['index'] % 5 == 1:
                qa = copy.deepcopy(source)
                qa.update(endpoint_id=resolve_dms_endpoint(row['user'], None)['name'],
                          user_id=row['user'], customer_dirty=False, customer_edited=True, files={}, audit=[])
                qa['payments'] = [{'channel': 'cash', 'amount': '0.01', 'extra': {}}]
                qa['answers']['regis_name'] = f'{MARKER} {args.clients}{suffix}-{row["index"]} NO PAYMENT'
                row['nonce'] = secrets.token_hex(8)
                store.set_session(row['line'], 'booking_review', {'nonce': row['nonce'], 'qa': qa})
        persist(report_path, {'stage': 'prepared', 'clients': args.clients})
    finally:
        frappe.destroy()
    barrier = threading.Barrier(args.clients)
    start = time.monotonic()

    def contender(row):
        connect()
        session = requests.Session()
        session.headers['Host'] = SITES[0]
        request_times, event_ids = [], []
        began = time.monotonic()
        def post(path, values):
            tick = time.monotonic()
            response = session.post('http://simulator:8090' + path, json=values, timeout=210)
            request_times.append(time.monotonic() - tick)
            assert response.status_code == 200, (path.rsplit('.', 1)[-1], response.status_code)
            result = response.json()
            assert not result.get('exc_type'), result.get('exc_type')
            return result.get('message', result)
        def send(**values):
            result = post('/__lab/send', {'user': row['line'], **values})
            event_ids.append(result['event_id'])
            return result
        def api(domain, method, **values):
            return post('/api/method/pearnly_dms.' + domain + '.' + method, values)
        def messages():
            result = session.get('http://simulator:8090/__lab/messages', params={'user': row['line'], 'after': message_floor}, timeout=15)
            result.raise_for_status()
            return result.json()
        def wait_for(predicate):
            deadline = time.monotonic() + 480
            while time.monotonic() < deadline:
                frappe.db.commit()
                result = predicate()
                if result:
                    return result
                time.sleep(1)
            raise TimeoutError('No business completion within 480 seconds')
        def auth(domain='account.api', **extra):
            identity = post('/__lab/identity', {'user': row['line']})['id_token']
            method = 'authenticate' if domain == 'account.api' else 'browser_auth'
            return api(domain, method, id_token=identity, **extra)['ticket']
        activity = ('ocr', 'editor', 'portal', 'credentials', 'query')[row['index'] % 5]
        message_floor = 0
        try:
            message_floor = max((m['id'] for m in messages()), default=0)
            barrier.wait(timeout=30)
            began = time.monotonic()
            configuration = session.get('http://simulator:8090/api/method/pearnly_dms.editor.browser_api.browser_config', timeout=30)
            configuration.raise_for_status()
            session.headers['X-Frappe-CSRF-Token'] = configuration.json()['message']['csrf_token']
            if activity == 'ocr':
                send(text='เมนู'); send(text='1')
                send(image=image, mime='image/jpeg')
                send(text='0990000922')
                wait_for(lambda: (store.get_session(row['line']) or {}).get('state') == 'reviewing')
                assert (store.get_session(row['line'])['payload']).get('id_card')
                result = {'real_ocr_review_ready': True}
            elif activity == 'editor':
                nonce = row['nonce']
                ticket = auth('editor.browser_api', draft=nonce, editor='booking')
                loaded = api('editor.browser_api', 'browser_draft', ticket=ticket, nonce=nonce)
                form = loaded['form']
                # Match the browser's explicit reference-to-ID serialization.
                form['answers'] = {**form['answers'], **{
                    key + '_id': form['answers'][key]['id'] for key in ('place', 'car', 'paint', 'term', 'regis')}}
                label = f'{MARKER} {args.clients}{suffix}-{row["index"]} EDITED NO PAYMENT'
                form['answers']['regis_name'] = label
                updated = api('editor.browser_api', 'browser_save', ticket=ticket, nonce=nonce, form=form)
                assert updated['nonce'] != nonce
                if row['index'] == 1:
                    send(postback=urlencode({'action': cards.ACT_CONFIRM_BOOKING, 'nonce': updated['nonce']}))
                    def booked():
                        logs = frappe.get_all('Pearnly Push Log', filters={'actor': row['user'], 'status': 'success'},
                            fields=['name', 'business_key', 'request_body', 'response_body'])
                        return next((log for log in logs if json.loads(log.request_body).get('qa', {}).get('regis_name') == label), None)
                    log = wait_for(booked)
                    external = json.loads(log.response_body)
                    def read(cl, adapter):
                        form = cl._parse_form_defaults(cl._post_text('drfcbc/form.php', {'status': 'e', 'id': external['booking_id']}))
                        assert docno_equal(log.business_key, form) and form['txtregisname'] == label
                        assert form['cusval'] == '127' and form['usersval'] == source['advisor']['id']
                        assert float(form['txtearnestmoney'].replace(',', '')) == .01
                        return {'ok': True}
                    assert run_logged_in(resolve_dms_endpoint(row['user'], None), read)['ok']
                    wait_for(lambda: frappe.db.get_value(delivery.DT, {'line_user_id': row['line'], 'booking_no': log.business_key}, 'status') == 'Delivered')
                    result = {'booking_no': log.business_key, 'real_readback': True}
                else:
                    result = {'edited_nonce_rotated': True}
            elif activity in ('portal', 'credentials'):
                ticket = auth()
                actual = api('account.api', 'credentials', ticket=ticket)
                assert actual['username'] == cfg['operators'][0]['username'] and 'password' not in actual
                if activity == 'portal':
                    url = api('account.api', 'portal_ticket', ticket=ticket)['url']
                    response = session.get('http://simulator:8090' + url, timeout=30)
                    assert response.status_code == 200 and 'login/checklogin.php' in response.text
                    # The native browser POST was verified separately. Do not log
                    # in outside the writer lock during the shared-account load.
                    result = {'single_use_handoff': True, 'external_browser_login': False}
                else:
                    assert api('account.api', 'save_credentials', ticket=ticket,
                               username=cfg['operators'][0]['username'], password=cfg['operators'][0]['password'])['ok']
                    result = {'same_credentials_saved': True}
            else:
                send(postback=urlencode({'action': cards.ACT_MENU_QUERY}))
                send(postback=urlencode({'action': query_cards.ACT_QUERY_TYPE, 'kind': 'sales'}))
                wait_for(lambda: any(query_cards.ACT_QUERY_DIMENSION in json.dumps(m['body']) for m in messages() if m['kind'] != 'incoming'))
                before = max((m['id'] for m in messages()), default=0)
                send(postback=urlencode({'action': query_cards.ACT_QUERY_DIMENSION, 'dimension': 'latest'}))
                wait_for(lambda: any(m['id'] > before and '"type": "flex"' in json.dumps(m['body']) for m in messages() if m['kind'] != 'incoming'))
                result = {'external_query_card_received': True}
            frappe.db.commit()
            assert all(frappe.db.get_value('Pearnly LINE Event', event, 'status') == 'done' for event in event_ids)
            return {'index': row['index'], 'activity': activity, 'ok': True, 'elapsed': round(time.monotonic()-began, 3),
                    'requests': len(request_times), 'max_http_seconds': round(max(request_times), 3), 'result': result}
        except Exception as exc:
            return {'index': row['index'], 'activity': activity, 'ok': False, 'error': type(exc).__name__,
                    'detail': str(exc)[:240], 'elapsed': round(time.monotonic()-began, 3)}
        finally:
            session.close()
            frappe.destroy()

    monitoring_done = threading.Event()
    queue_samples = []
    job_ids = set()

    def monitor():
        from frappe.utils.background_jobs import get_redis_conn
        from rq import Queue
        connect()
        try:
            connection = get_redis_conn()
            while not monitoring_done.is_set():
                queues = Queue.all(connection=connection)
                row = {'seconds': round(time.monotonic() - start, 2), 'pending': 0, 'started': 0, 'failed': 0}
                for queue in queues:
                    row['pending'] += queue.count
                    row['started'] += queue.started_job_registry.count
                    row['failed'] += queue.failed_job_registry.count
                    job_ids.update(queue.get_job_ids())
                    job_ids.update(queue.started_job_registry.get_job_ids())
                queue_samples.append(row)
                monitoring_done.wait(.5)
        finally:
            frappe.destroy()

    monitor_thread = threading.Thread(target=monitor, daemon=True)
    monitor_thread.start()
    try:
        results = []
        with ThreadPoolExecutor(max_workers=args.clients) as pool:
            futures = [pool.submit(contender, row) for row in fixtures[:args.clients]]
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                persist(report_path, {'stage': 'running', 'clients': args.clients, 'results': results})
                print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        monitoring_done.set()
        monitor_thread.join(timeout=10)
    connect()
    try:
        from frappe.utils.background_jobs import get_redis_conn
        from rq import Worker
        from rq.job import Job
        worker_count = len(Worker.all(connection=get_redis_conn()))
        jobs = []
        for job_id in job_ids:
            job = Job.fetch(job_id, connection=get_redis_conn())
            if job.enqueued_at and job.started_at:
                jobs.append({'wait_seconds': round((job.started_at-job.enqueued_at).total_seconds(), 3),
                             'run_seconds': round((job.ended_at-job.started_at).total_seconds(), 3) if job.ended_at else None,
                             'status': str(job.get_status())})
    finally:
        frappe.destroy()
    connect(SITES[1])
    try:
        assert all(store.get_session(row['line']) is None for row in fixtures[:args.clients]), 'Cross-site session leak'
    finally:
        frappe.destroy()
    durations = sorted(r['elapsed'] for r in results)
    report = {'clients': args.clients, 'local_identities': 100, 'sites': 2, 'external_credentials': 2,
              'worker_processes': worker_count,
              'web_profile': 'gunicorn 2 workers x 4 threads' if args.label == 'pool2' else 'bench development threaded server',
              'elapsed': round(time.monotonic()-start, 2), 'p50_seconds': durations[args.clients//2],
              'p95_seconds': durations[int(args.clients*.95)-1], 'results': results,
              'passed': sum(r['ok'] for r in results),
              'real_bookings_read_back': sum(bool(r.get('result', {}).get('real_readback')) for r in results),
              'real_ocr_images_requested': args.clients//5, 'real_payments': 0,
              'cross_site_sessions_isolated': True, 'queue_samples': queue_samples, 'jobs': jobs}
    persist(report_path, report)
    print(json.dumps(report, ensure_ascii=False), flush=True)
    assert report['passed'] == args.clients


if args.phase == 'setup':
    setup()
elif args.phase == 'cleanup':
    cleanup()
else:
    run()
