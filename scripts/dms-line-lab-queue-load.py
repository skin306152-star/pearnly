"""Cost-free signed LINE -> native RQ -> OCR HTTP fixture -> real local billing load.

Run setup, restart lab services, run, then restore and restart. Only laboratory
Sites are accepted. No external OCR/DMS/LINE calls, real payments, or orders.
Reports retain exact work IDs; no customer documents/credentials are printed.
"""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import statistics
import threading
import time

import frappe
import requests
from pearnly_core.execution import work, budget
from pearnly_core import line_session

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('phase', choices=['setup','run','restore'])
parser.add_argument('--rounds', default='10,20,50')
parser.add_argument('--input-bytes', type=int, default=0,
                    help='Pad the synthetic JPEG for bounded transport/memory testing only')
a = parser.parse_args()
os.chdir('/home/frappe/frappe-bench/sites')
SITES = ['dms-line-lab.localhost', 'dms-line-lab-b.localhost']
KEYS = ['pearnly_ocr_backend','pearnly_ocr_api_key','pearnly_ocr_endpoint', 'pearnly_ocr_project',
        'pearnly_billing_api','pearnly_billing_host_header','pearnly_billing_token','pearnly_tenant']
MANIFEST = Path(SITES[0])/'private/queue-load-config.json'


def connect(site):
    frappe.init(site=site, sites_path='.')
    frappe.connect()
    frappe.set_user('Administrator')
    assert frappe.conf.pearnly_line_channel_access_token == 'lab:'+site


def save(path, data):
    path.write_text(json.dumps(data, indent=2))
    path.chmod(0o600)


if a.phase == 'setup':
    assert not MANIFEST.exists(), 'Restore previous run first'
    configurations = {site:json.loads((Path(site)/'site_config.json').read_text()) for site in SITES}
    save(MANIFEST, {site:{key:configurations[site].get(key) for key in KEYS} for site in SITES})
    # Give B an isolated local test wallet through the existing approval service.
    frappe.init(site='dms-line-lab-control.localhost', sites_path='.')
    frappe.connect(); frappe.set_user('Administrator')
    from pearnly_control import billing, billing_api
    from pearnly_control.billing_auth import tenant_token
    label = 'OCR capacity laboratory B'
    tenant = frappe.db.get_value('Pearnly Tenant', {'tenant_name':label}, 'name')
    if not tenant:
        tenant = frappe.get_doc({'doctype':'Pearnly Tenant','tenant_name':label}).insert().name
        doc = frappe.get_doc({'doctype':billing.TOPUP,'tenant':tenant,'status':'pending',
              'requested_thb':1000,'note':'Capacity test credit; no real funds','payer_name':label}).insert()
        billing_api.approve_local(doc.name,'Administrator',actual_amount_thb=1000,note='No real funds')
    btoken = tenant_token(billing_api.service_token(),tenant,'dms')
    frappe.db.commit(); frappe.destroy()
    for site, config in configurations.items():
        assert config['pearnly_line_channel_access_token'] == 'lab:'+site
        config.update(pearnly_ocr_backend='aistudio',pearnly_ocr_api_key='cost-free-lab-only',
                      pearnly_ocr_endpoint='http://ocr-fixture:8099/{model}',pearnly_ocr_project='capacity-lab')
        if site == SITES[1]:
            config.update(pearnly_tenant=tenant,pearnly_billing_api='http://site:8000',
                          pearnly_billing_host_header='dms-line-lab-control.localhost',pearnly_billing_token=btoken)
        save(Path(site)/'site_config.json',config)
    print('Configured two laboratory Sites; model fixture only. Restart lab before running.')

elif a.phase == 'restore':
    saved = json.loads(MANIFEST.read_text())
    for site, previous in saved.items():
        path=Path(site)/'site_config.json';config=json.loads(path.read_text())
        assert config['pearnly_line_channel_access_token']=='lab:'+site
        for key,value in previous.items():
            if value is None: config.pop(key,None)
            else: config[key]=value
        save(path,config)
    MANIFEST.unlink()
    print('Restored original lab configuration; restart lab before real-provider checks.')
else:
    assert MANIFEST.exists()
    for site in SITES:
        connect(site)
        assert frappe.conf.pearnly_ocr_endpoint.startswith('http://ocr-fixture:8099/')
        assert not frappe.db.count(work.DT, {'status':['in',work.ACTIVE]})
        frappe.destroy()
    spec=importlib.util.spec_from_file_location('password_lab','/workspace/scripts/dms-line-lab-password.py')
    password=importlib.util.module_from_spec(spec);spec.loader.exec_module(password)
    http=password.login(password.ORIGINAL)
    stop=threading.Event();web=[];statuses=[]
    def probe():
        while not stop.is_set():
            began=time.monotonic()
            try:
                result=password.request(http,'GET',password.BASE+'/api/method/pearnly_core.billing_www.home')
                assert 'balance_thb' in result.json()['message']
                web.append(round(time.monotonic()-began,3))
            except Exception as exc:
                response = getattr(exc, 'response', None)
                detail = {'type':type(exc).__name__, 'time':time.time(),
                          'seconds':round(time.monotonic()-began,3)}
                if response is not None:
                    detail['http'] = response.status_code
                    try:
                        body = response.json()
                        detail['exception'] = str(body.get('exception') or body.get('exc_type') or body.get('error') or '')[:300]
                    except ValueError:
                        detail['content_type'] = response.headers.get('Content-Type','')
                statuses.append(detail)
                print(json.dumps({'home_error':detail}),flush=True)
            stop.wait(2)
    thread=threading.Thread(target=probe,daemon=True);thread.start()
    content=Path('/lab-private/ocr-load-sample.jpg').read_bytes()
    assert 0 <= a.input_bytes <= 15*1024*1024
    if a.input_bytes:
        assert a.input_bytes >= len(content)
        content=content.ljust(a.input_bytes,b'\0')
    image=base64.b64encode(content).decode()
    reports=[];all_ids=[]
    try:
        for users in [int(x) for x in a.rounds.split(',')]:
            assert 1 <= users <= 100
            rows=[]
            for index in range(users):
                site=SITES[index%2]
                fixture=json.loads((Path(site)/'private/line-lab-load-fixtures.json').read_text())[index//2]
                rows.append({**fixture,'site':site})
            before=requests.get('http://ocr-fixture:8099/stats',timeout=10).json()
            start=time.monotonic()
            def send(row):
                connect(row['site'])
                try:
                    old=line_session.get_session(row['line'])
                    assert not ((old or {}).get('payload') or {}).get('booking_attempt')
                    line_session.set_session(row['line'],'collecting',{'mode':'customer'})
                    frappe.db.commit()
                finally: frappe.destroy()
                began=time.monotonic()
                response=requests.post('http://simulator:8090/__lab/send',headers={'Host':row['site']},
                                       json={'user':row['line'],'image':image},timeout=40)
                response.raise_for_status()
                event=response.json()['event_id']
                connect(row['site'])
                try:
                    receipt=frappe.db.get_value('Pearnly LINE Event',event,'status')
                    assert receipt=='done', str(receipt)
                    job=frappe.get_all(work.DT,filters={'user':row['user'],'status':['in',work.ACTIVE]},pluck='name')
                    assert len(job)==1, 'Expected one accepted durable job'
                    item = {**row,'work':job[0],'http_seconds':round(time.monotonic()-began,3)}
                    all_ids.append(item)
                    return item
                finally: frappe.destroy()
            with ThreadPoolExecutor(max_workers=users) as pool:
                accepted=list(pool.map(send,rows))
            completed={};samples=[]
            deadline=time.monotonic()+800
            while len(completed)<users and time.monotonic()<deadline:
                for site in SITES:
                    connect(site)
                    try:
                        for row in [r for r in accepted if r['site']==site]:
                            job=frappe.db.get_value(work.DT,row['work'],['status','last_code','attempts'],as_dict=True)
                            if job.status=='succeeded' and row['work'] not in completed:
                                session=line_session.get_session(row['line'])
                                assert (session or {}).get('payload',{}).get('id_card',{}).get('people_id')=='1000009200019', json.dumps({'work':row['work'],'site':site,'state':(session or {}).get('state'),'keys':list(((session or {}).get('payload') or {}).keys())})
                                body=work.stored_checkpoints(row['work'])
                                assert any(v.get('body',{}).get('modelVersion')=='cost-free-lab-fixture' for v in body.values())
                                completed[row['work']]=round(time.monotonic()-start,3)
                            assert job.status not in ('failed','uncertain','cancelled'), json.dumps({'work':row['work'],'site':site,'status':job.status,'code':job.last_code})
                        samples.append(work.metrics())
                    finally: frappe.destroy()
                time.sleep(1)
            assert len(completed)==users, 'Queue did not drain'
            after=requests.get('http://ocr-fixture:8099/stats',timeout=10).json()
            assert after['completed']-before['completed']==users, 'Repeated or lost paid call'
            durations=sorted(completed.values())
            report={'clients':users,'seconds':round(time.monotonic()-start,2),'passed':len(completed),
                    'provider_successes':after['completed']-before['completed'],
                    'provider_rejections':after['rejections']-before['rejections'],
                    'p95_seconds':durations[max(0,int(users*.95)-1)],
                    'webhook_max_seconds':max(r['http_seconds'] for r in accepted), 'provider_peak':after['peak']}
            reports.append(report);print(json.dumps(report),flush=True)
            save(Path(SITES[0])/'private/line-lab-queue-load.json',{'rounds':reports,'jobs':all_ids,'samples':samples,
                                                                'authenticated_home_seconds':web,'home_failures':statuses})
        assert not statuses and web
        print(json.dumps({'rounds':reports,'authenticated_home_samples':len(web),
                          'home_p95_seconds':sorted(web)[int(len(web)*.95)-1],'home_errors':len(statuses)}),flush=True)
    finally:
        stop.set();thread.join(timeout=35)
        save(Path(SITES[0])/'private/line-lab-queue-load.json',{'rounds':reports,'jobs':all_ids,
                        'authenticated_home_seconds':web,'home_failures':statuses})
        for site in SITES:
            connect(site)
            try:
                for row in [r for r in all_ids if r['site']==site]:
                    if len(reports) == len(a.rounds.split(',')):
                        line_session.clear_session(row['line'])
                frappe.db.commit()
            finally: frappe.destroy()
