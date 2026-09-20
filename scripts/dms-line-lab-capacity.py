"""Real isolated MariaDB/Redis fault tests; provider HTTP is fully stubbed.

Creates only prefixed local work rows and its own Redis namespace, then removes
those exact artifacts. No external OCR, LINE, DMS or billing requests.
"""
import json
import os
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import patch

import frappe
import requests
from redis import Redis
from pearnly_core.execution import budget, provider, work, dispatch, resources, inputs
from pearnly_core.execution.errors import Deferred, QueueFull, Uncertain

os.chdir('/home/frappe/frappe-bench/sites')
SITES = ['dms-line-lab.localhost', 'dms-line-lab-b.localhost']
RUN = 'capacity-test-' + secrets.token_hex(5)
original_prefix = budget.PREFIX
budget.PREFIX = RUN + ':'
checks = []
created = []
lock = threading.Lock()

def check(value, label):
    assert value, label
    checks.append(label)
    print('PASS', label, flush=True)

@contextmanager
def site(name=SITES[1], tenant='tenant-a'):
    frappe.init(site=name, sites_path='.')
    frappe.connect()
    frappe.set_user('Administrator')
    assert frappe.conf.pearnly_line_channel_access_token == 'lab:' + name
    frappe.conf.pearnly_tenant = tenant
    frappe.conf.pearnly_ocr_budget = {**budget.DEFAULTS, 'rpm': 60000}
    try:
        yield
    finally:
        frappe.destroy()


def response(status=200, retry='0'):
    result = requests.Response()
    result.status_code = status
    result._content_consumed = True
    result.headers['Retry-After'] = retry
    result._content = json.dumps({'candidates':[{'content':{'parts':[{'text':'{"local":true}'}]}}],
                                 'usageMetadata': {'promptTokenCount':7,'candidatesTokenCount':3}}).encode()
    return result


def call():
    return provider.post('https://example.test/never-called', {}, {'local':RUN}, 10, 'test', 'test')


def add():
    with patch.object(work, 'handler', return_value='fixture'), patch.object(work, 'kick'):
        name = work.submit('dms.id_card', RUN + '-' + secrets.token_hex(4), {'synthetic':True}, 'Administrator')
    created.append((frappe.local.site, name))
    return name

try:
    active = 0
    peak = 0
    per_tenant = {}
    completed = []
    def participant(index):
        global active, peak
        who = 'tenant-' + str(index % 5)
        with site(SITES[index % 2], who):
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                try:
                    with budget.permit('test', 10):
                        with lock:
                            active += 1
                            peak = max(peak, active)
                            per_tenant[who] = per_tenant.get(who, 0) + 1
                            assert active <= 4 and per_tenant[who] <= 2
                        time.sleep(.04)
                        with lock:
                            active -= 1
                            per_tenant[who] -= 1
                            completed.append(index)
                        return
                except Deferred:
                    time.sleep(.01)
            raise AssertionError('starved tenant')
    with ThreadPoolExecutor(max_workers=20) as pool:
        list(pool.map(participant, range(100)))
    check(len(completed) == 100 and peak == 4, '100 requests, 20 concurrent, two Sites/five tenants: global 4, tenant 2, all finish')
    with site():
        client = budget.redis()
        check(client.zcard(budget.PREFIX+'active') == 0, 'all successful request leases released')
        cfg = dict(frappe.conf.pearnly_ocr_budget)
        frappe.conf.pearnly_ocr_budget = {**cfg, 'concurrency':8}
        try:
            with budget.permit('test', 10):
                raise AssertionError('policy mismatch accepted')
        except Deferred as exc:
            check(exc.reason == 'capacity_policy_mismatch', 'different App budget cannot silently increase shared maximum')
        frappe.conf.pearnly_ocr_budget = cfg
        with patch.object(budget, 'redis', side_effect=__import__('redis').exceptions.ConnectionError):
            try:
                with budget.permit('test', 10): pass
            except Deferred as exc:
                check(exc.reason == 'capacity_unavailable', 'Redis outage fails closed before provider IO')
        # A cooled-down provider must not block unrelated model/provider work.
        client.set(budget.PREFIX+'paused:pause', time.time()+100)
        try:
            with budget.permit('paused',1): pass
        except Deferred: pass
        frappe.conf.pearnly_tenant = 'another-tenant'
        with budget.permit('unrelated',1): pass
        check(True, 'one paused model does not block another model tenant')
        frappe.conf.pearnly_tenant = 'tenant-a'
        name=add()
        with work.context(name), patch.object(provider.requests, 'post', return_value=response(429,'8')) as post:
            try: call()
            except Deferred as exc:
                check(exc.reason=='provider_429' and exc.delay>=8, '429 defers without sleeping inside OCR worker')
            key=work.checkpoint_key({'local':RUN}, 'test')
            check(work.checkpoint(key)['state']=='rejected', 'explicit 429 rejection is safe to retry')
            try: call()
            except Deferred: pass
            check(post.call_count==1, 'shared cooldown blocks second actual provider call')
            client.delete(budget.PREFIX+'test:pause',budget.PREFIX+'test:next')
            post.return_value=response()
            check(call().status_code==200, 'same payload resumes after cooldown')
            check(call().json()['usageMetadata']['promptTokenCount']==7 and post.call_count==2,
                  'completed model result and usage survive replay without another paid call')
        name2=add()
        with work.context(name2), patch.object(provider.requests,'post',side_effect=requests.Timeout) as post:
            try: call()
            except Uncertain: pass
            client.delete(budget.PREFIX+'test:next')
            try: call()
            except Uncertain: pass
            check(post.call_count==1, 'ambiguous transport timeout is never automatically repeated')
        frappe.db.set_value(work.DT,name2,{'status':'running','lease_until':work.utcnow()-timedelta(seconds=1)})
        frappe.db.commit()
        with patch.object(work,'_notify'):
            work.run(name2)
        check(frappe.db.get_value(work.DT,name2,'status')=='uncertain','dead worker with in-flight provider call becomes uncertain')
        # Finished OCR checkpoint survives a worker dying before post-processing.
        frappe.db.set_value(work.DT,name,{'status':'running','lease_until':work.utcnow()-timedelta(seconds=1)})
        frappe.db.commit()
        with patch.object(work,'handler',return_value='fixture'), \
             patch.object(frappe,'get_attr',return_value=lambda **kw: (call(), {})[1]), \
             patch.object(provider.requests,'post') as post:
            work.run(name)
            check(post.call_count==0,'completed OCR resumes post-processing after worker restart')
        check(frappe.db.get_value(work.DT,name,'status')=='succeeded','recovered work finishes without duplicate recognition')
        name3=add()
        with patch.object(work,'handler',return_value='fixture'), \
             patch.object(frappe,'get_attr',return_value=lambda **kw: (_ for _ in ()).throw(Deferred('billing_unavailable',3))):
            work.run(name3)
        check(frappe.db.get_value(work.DT,name3,'status')=='deferred','billing outage preserves completed workflow for retry')
        conn=dispatch.get_redis_conn()
        member=json.dumps([frappe.local.site,name3])
        check(conn.zscore(dispatch.DUE,member) is not None,'deferred job has native queue delivery ETA')
        # Stable reference cannot be reused with different arguments.
        row=frappe.get_doc(work.DT,name3)
        with patch.object(work,'handler',return_value='fixture'):
            check(work.submit(row.kind,row.reference,{'synthetic':True},row.user)==name3,'duplicate submission returns the same durable work')
            try: work.submit(row.kind,row.reference,{'synthetic':False},row.user)
            except ValueError: checks.append('reference content mismatch refused')
            else: raise AssertionError('reference mismatch')
        # Concurrent delivery sees a running SQL claim and does not enter the workflow.
        frappe.db.set_value(work.DT,name3,{'status':'running','lease_until':work.utcnow()+timedelta(seconds=660), 'next_run':work.utcnow()})
        frappe.db.commit()
        with patch.object(frappe,'get_attr') as execute:
            work.run(name3)
            check(execute.call_count==0,'duplicate RQ delivery cannot claim active work')
        client.zadd(budget.PREFIX+'active',{'dead-worker:token':time.time()-1})
        client.delete(budget.PREFIX+'test:next')
        with budget.permit('test',1): pass
        check(client.zscore(budget.PREFIX+'active','dead-worker:token') is None,'expired process lease is reclaimed')
        from pearnly_dms.ocr import ocr_service
        from pearnly_core import billing_client
        with patch.object(billing_client, 'status', return_value={'allowed':False,'error_code':'lookup_error'}):
            try: ocr_service._billing_gate()
            except Deferred as exc:
                check(exc.reason=='billing_unavailable','actual DMS balance gate defers a transient lookup before OCR')
            else: raise AssertionError('balance lookup did not defer')
        with patch.object(billing_client, 'status', return_value={'allowed':False,'error_code':'insufficient_balance'}):
            try: ocr_service._billing_gate()
            except ocr_service.DmsOcrError as exc:
                check(exc.http_status==402,'insufficient balance remains a business refusal')
            else: raise AssertionError('insufficient balance accepted')
        for contents, expected in [(['MemAvailable: 1024 kB'],'host_memory_pressure'),
            (['MemAvailable: 4096000 kB',str(512*resources.MIB),str(400*resources.MIB)],'worker_memory_pressure')]:
            with patch.object(resources.Path,'read_text',side_effect=contents):
                try: resources.check_memory()
                except Deferred as exc: check(exc.reason==expected,expected+' postpones work')
                else: raise AssertionError('memory pressure ignored')
        # Admission is tested against the real shared Redis reservation, not a UI flag.
        member='other-bench-held-slot'
        old_cfg=dict(frappe.conf.pearnly_ocr_budget)
        frappe.conf.pearnly_ocr_budget={**old_cfg,'pending':1}
        client.zadd(budget.PREFIX+'pending',{member:time.time()+300})
        try:
            try: add()
            except QueueFull: check(True,'bounded global admission rejects overflow before accepting work')
            else: raise AssertionError('queue overflow accepted')
        finally:
            client.zrem(budget.PREFIX+'pending',member)
            frappe.conf.pearnly_ocr_budget=old_cfg
        # A terminal cancellation cannot be overwritten by a late completion.
        frappe.db.set_value(work.DT,name3,'status','cancelled');frappe.db.commit()
        work._finish(name3,'succeeded')
        check(frappe.db.get_value(work.DT,name3,'status')=='cancelled','late worker cannot overwrite cancellation')
        name4=add()
        with work.context(name4), patch.object(inputs, '_directory', wraps=inputs._directory), \
                patch('builtins.print'):
            from unittest.mock import Mock
            download=Mock(return_value=b'owned laboratory image')
            assert inputs.read('image',download,1024)==b'owned laboratory image'
            assert inputs.read('image',download,1024)==b'owned laboratory image'
        check(download.call_count==1,'deferred work downloads its bounded input only once')
        with work.context(name4), patch.object(inputs.shutil,'disk_usage',return_value=type('Disk',(),{'free':1})()):
            try: inputs.read('another-page',download,1024)
            except Deferred as exc: check(exc.reason=='disk_pressure','low disk defers before input download')
            else: raise AssertionError('low disk accepted')
        work._finish(name4,'cancelled')
        check(not inputs._directory(name4).exists(),'terminal work removes only its own retained input')
        # Slow body chunks cannot outlive the permit; idle read timeouts alone
        # would otherwise let a dribbling response release its capacity too soon.
        from types import SimpleNamespace
        from unittest.mock import Mock
        frappe.conf.pearnly_tenant = 'deadline-tenant'
        slow = add()
        client.delete(budget.PREFIX+'test:next')
        with work.context(slow), patch.object(provider.requests,'post',return_value=response()), \
                patch.object(provider,'time',SimpleNamespace(monotonic=Mock(side_effect=[0,11]))):
            try: call()
            except Uncertain:
                check(True,'slow response body hits elapsed-time check rather than recording success')
            else: raise AssertionError('slow response accepted')
            check(work.checkpoint(work.checkpoint_key({'local':RUN},'test'))['state']=='started',
                  'slow response retains unknown provider outcome without paid replay')
        leases=client.zrange(budget.PREFIX+'active:'+budget.tenant(),0,-1,withscores=True)
        check(len(leases)==1 and leases[0][1]-time.time()>work.JOB_TIMEOUT,
              'unknown provider lease covers the native hard job timeout')
        killed = add()
        client.delete(budget.PREFIX+'test:next')
        with work.context(killed), patch.object(provider.requests,'post',side_effect=RuntimeError('native timeout')):
            try: call()
            except RuntimeError: pass
            else: raise AssertionError('interruption not propagated')
        check(client.zcard(budget.PREFIX+'active:'+budget.tenant())==2,
              'unexpected worker interruption retains its upstream capacity reservation')
        # Old terminal receipts remain for idempotency; cleanup must advance
        # beyond its first bounded page when an earlier removal failed.
        old = [add() for _ in range(4)]
        for name in old:
            frappe.db.set_value(work.DT, name, {'status':'cancelled', 'next_run':'2000-01-01'})
            with work.context(name):
                inputs.read('retained', lambda: b'local fixture', 1024)
        frappe.db.commit()
        cursor_key = 'pearnly:ocr:prune_cursor'
        previous_cursor = frappe.cache.get_value(cursor_key)
        frappe.cache.delete_value(cursor_key)
        try:
            from datetime import datetime
            with patch.object(work, 'PRUNE_BATCH', 2), patch.object(work, 'utcnow', return_value=datetime(2001,1,1)):
                work.prune_checkpoints()
                check(sum(inputs._directory(name).exists() for name in old)==2,
                      'retained input cleanup obeys its batch limit')
                work.prune_checkpoints()
                check(not any(inputs._directory(name).exists() for name in old),
                      'retained input cleanup reaches later receipts without deleting audit rows')
        finally:
            if previous_cursor is None: frappe.cache.delete_value(cursor_key)
            else: frappe.cache.set_value(cursor_key, previous_cursor)
    print(json.dumps({'ok':True,'checks':len(checks),'peak':peak,'requests':len(completed)}))
finally:
    for target in SITES:
        with site(target):
            for saved_site,name in created:
                if saved_site==target:
                    inputs.remove(name)
                    frappe.db.delete(work.DT,{'name':name})
                    dispatch.get_redis_conn().zrem(dispatch.DUE,json.dumps([saved_site,name]))
            frappe.db.commit()
    client=Redis.from_url('redis://redis:6379/6',decode_responses=True)
    keys=list(client.scan_iter(match=budget.PREFIX+'*'))
    if keys: client.delete(*keys)
    budget.PREFIX=original_prefix
