"""多电脑并发压测（ADR-021 第 7 批 D9）：3 个租户 × 每家 10 台电脑，全在本任务的实验室里，服务端模拟。

只在 `infrastructure/compose/cowork-backend-lab.yml` 的专用实验站点上跑（`.cowork-backend-lab` 标记 +
`cowork-backend-load-{1,2,3}.localhost`）：内部网络、没有调度器、没有外部凭据；不起真 CC 桌面版、不调识别、
不连 Express / 测试机 / 生产。造的用户、电脑、密钥、票与推送记录都是带 ``cw-load-`` 标签的合成数据，只在实验卷里。

三步（都在 bench 容器里、以 `env/bin/python` 跑）：

1. ``setup [--history N]``：每个租户建 1 个管理员 + 10 个成员（录入角色）、站点归属下一条 Express 主连接，每个成员
   用 register v2 给自己的电脑登记（设备密钥、电脑名），电脑经 ``device_hello`` 认电脑、经真实心跳入口整份上报
   8 份共享盘账套（同一文件号、同一网络路径 → 池里同一份登记）+ 2 份本机账套；每人 30 张手工票经真实准备 + 确认
   成为排队单（谁录的谁的电脑写）。租户 1 另建第二条 Express 连接，第 1 台电脑用旧槽位密钥 + ``device_hello``
   挂上去（一台挂两条连接），那条连接上另有 10 张它的单。``--history`` 再按每个租户克隆这么多张已成功的旧推送记录
   （只为让推送记录表有生产量级的行数，排头查询照样要扫）。
2. ``run [--seconds S]``：起与生产 Cowork 网页同形的 gunicorn（6 进程 × 4 线程 gthread，`--preload`），每台电脑一个
   线程按 CC 桌面版 1.7.0 的节奏走真实 HTTP：每 3 秒领一次任务（带 ``index_pending``）、领到就「写」2 秒再回执成功、
   每 60 秒心跳（第一次整份、之后「没变」）、每 15 秒问一次按需请求；每个成员每 30 秒打开一次对话页（register v2）。
   压到一半时管理员经网页接口移除租户 3 的两台电脑（它们照常在发心跳、领任务）。gunicorn 进程里只给本实验加了计时与
   计数（`__config__` 段：数据库错误码、连接行加锁读、排头查询、写者锁加锁读、领取分派、register / remove 的耗时），
   产品代码不改。
3. ``report``：读回结果（每张单只写成一次、没有一张被两台领走、两条连接各一件），对排头查询跑 ``ANALYZE FORMAT=JSON``，
   打印 JSON 报告（数字写进多电脑记录「D9 并发压测」）。

跑法见 `docs/work/2026-10-15-multi-desktop-build.md`「D9 并发压测（10-19）」。
"""
import argparse
import http.client
import json
import os
import random
import re
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

SITES = ('cowork-backend-load-1.localhost', 'cowork-backend-load-2.localhost', 'cowork-backend-load-3.localhost')
SITE_ROOT = Path('/home/frappe/frappe-bench/sites')
STATE = 'private/concurrency-lab.json'
REDIS = 'redis://redis:6379/7'
HOST, PORT = '127.0.0.1', 8000
MEMBERS, SHARED, LOCAL, JOBS, SECOND_JOBS = 10, 8, 2, 30, 10
TAX = '0994000333444'
SELLER_TAX = '0107561000013'
MAPPING = {'fallback_acc': 'A', 'vat_input_acc': 'V', 'ap_acc': 'P', 'revenue_acc': 'S', 'vat_output_acc': 'O',
           'ar_acc': 'R'}
LEASE, ACK = '/api/method/pearnly_cowork.companion.queue_api.lease', '/api/method/pearnly_cowork.companion.queue_api.acknowledge'
BEAT = '/api/method/pearnly_cowork.companion.heartbeat.report'
NEXT = '/api/method/pearnly_cowork.companion.request_api.next_request'
REGISTER = '/api/method/pearnly_cowork.companion.api.register'
REMOVE = '/api/method/pearnly_cowork.companion.api.remove_device'
POLL_SECONDS, WRITE_SECONDS, BEAT_SECONDS, NEXT_SECONDS, REGISTER_SECONDS = 3.0, 2.0, 60.0, 15.0, 30.0


# ── gunicorn 配置（`gunicorn -c 本文件`：gunicorn 以 ``__config__`` 执行它）──────────────────────────────────
if __name__ == '__config__':
    bind = f'{HOST}:{PORT}'
    chdir = str(SITE_ROOT)
    workers = int(os.environ.get('PEARNLY_LOAD_WORKERS', '6'))
    threads = int(os.environ.get('PEARNLY_LOAD_THREADS', '4'))
    worker_class = 'gthread'
    timeout = graceful_timeout = 120
    preload_app = True
    worker_tmp_dir = '/dev/shm'
    accesslog = None
    errorlog = '-'
    loglevel = 'warning'

    def post_fork(server, worker):
        _instrument()


def _code(error):
    """数据库错误码：Frappe 把 MySQLdb 的异常包一层（QueryDeadlockError 的第一个参数是原来的异常）。"""
    for _ in range(3):
        first = error.args[0] if getattr(error, 'args', None) else None
        if isinstance(first, int):
            return first
        if not isinstance(first, BaseException):
            return None
        error = first
    return None


def _instrument():
    """只在本实验的 gunicorn 进程里：记数据库错误码、几类加锁读与查询、几个服务函数的耗时（毫秒）到实验 Redis。"""
    import frappe
    import redis
    from frappe.database.database import Database
    from pearnly_cowork.companion import credentials, heartbeat, transport
    from pearnly_core.cc_desktop.wire import route_contracts

    store = redis.Redis.from_url(REDIS)
    original = Database.sql

    def tag(query):
        low = ' '.join(str(query).lower().split())
        if 'for update' in low or 'lock in share mode' in low:
            if 'tabcowork erp connection' in low:
                return 'conn_lock'
            if 'tabcowork workspace' in low:
                return 'ws_lock'
            if 'tabcowork push writer lock' in low:
                return 'locks_read'
            table = re.search(r'from `?(tab[^`]+)`?', low)
            return 'lock:' + (table.group(1) if table else 'unknown')
        if 'min(r2.created_at)' in low:
            return 'q_heads'
        if 'tabcowork push writer lock' in low:
            return 'locks_plain'
        return ''

    def sql(self, query, *args, **kwargs):
        kind = tag(query)
        started = time.perf_counter()
        try:
            return original(self, query, *args, **kwargs)
        except Exception as error:
            code = _code(error)
            if code in (1020, 1205, 1213):
                store.hincrby(f'{frappe.local.site}:dberr', f'{code}:{kind or "other"}', 1)
            raise
        finally:
            if kind:
                store.rpush(f'{frappe.local.site}:sql:{kind}', round((time.perf_counter() - started) * 1000, 3))

    Database.sql = sql

    def timed(module, name, label):
        function = getattr(module, name)

        def wrapper(*args, **kwargs):
            started = time.perf_counter()
            try:
                return function(*args, **kwargs)
            except Exception as error:
                code = _code(error) or ''
                store.hincrby(f'{frappe.local.site}:fnerr', f'{label}:{type(error).__name__}:{code}', 1)
                raise
            finally:
                store.rpush(f'{frappe.local.site}:fn:{label}', round((time.perf_counter() - started) * 1000, 3))
        setattr(module, name, wrapper)

    timed(transport, '_claim', 'claim')
    timed(transport, 'lease', 'lease')
    timed(route_contracts, 'decide', 'decide')
    timed(credentials, 'register', 'register')
    timed(credentials, 'remove_device', 'remove')
    timed(heartbeat, '_receive', 'heartbeat')


# ── 实验站点守卫 ───────────────────────────────────────────────────────────────────────────────────────
def connect(site):
    import frappe
    if os.environ.get('PEARNLY_COWORK_BACKEND_LAB') != '1' or site not in SITES:
        raise SystemExit('Dedicated Cowork backend lab Site required')
    if not (SITE_ROOT / '.cowork-backend-lab').is_file():
        raise SystemExit('Dedicated lab Site marker required')
    os.chdir(SITE_ROOT)
    frappe.init(site=site, sites_path=str(SITE_ROOT))
    frappe.connect()
    if not frappe.conf.get('pearnly_backend_lab') or frappe.conf.get('db_host') != 'db':
        raise SystemExit('Refusing a non-lab Site')
    frappe.conf.throttle_user_limit = 1000
    frappe.set_user('Administrator')
    frappe.flags.mute_emails = True


def _state_path(site):
    return SITE_ROOT / site / STATE


def _account_sets(index):
    """一台电脑的账套名单：8 份共享盘（各台同一文件号、同一网络路径）+ 2 份只在它本机的。"""
    rows = [{'code': f'SET{k}', 'name': f'Shared {k}', 'path': f'S:\\EXP\\SET{k}', 'root': 'S:\\EXP',
             'uid': f'5a5a0001-{k:08x}', 'share': f'\\\\NAS\\EXP\\SET{k}', 'writable': True, 'mapping': dict(MAPPING)}
            for k in range(SHARED)]
    rows += [{'code': f'LOC{index}{k}', 'name': f'Local {index}.{k}', 'path': f'C:\\EXP\\LOC{index}{k}', 'root': 'C:\\EXP',
              'uid': f'c0c0{index:04x}-{k:08x}', 'writable': True, 'mapping': dict(MAPPING)} for k in range(LOCAL)]
    return rows


def _beat_body(index, *, full=True):
    body = {'account_set': 'SET0', 'express_root': 'S:\\EXP', 'companion_version': '1.9.0', 'max_payload_version': 3,
            'device': f'PC-{index:02d}'}
    if full:
        body.update(account_sets=_account_sets(index), accounts=[{'code': code} for code in sorted(set(MAPPING.values()))],
                    catalog={'products': [], 'customers': [], 'fingerprint': {'stock_master_count': 0}})
    else:
        body['account_sets_unchanged'] = True
    return body


# ── setup ─────────────────────────────────────────────────────────────────────────────────────────────
def setup(site, history_rows):
    import frappe
    from pearnly_cowork.companion import credentials, device_contracts, heartbeat
    from pearnly_cowork.connections import access, pool, service as connections
    from pearnly_cowork.push import choices as choice_store

    connect(site)
    if _state_path(site).exists():
        raise SystemExit(f'{site}: already set up (state file exists)')
    config_path = SITE_ROOT / site / 'site_config.json'
    config = json.loads(config_path.read_text())
    config.update(pearnly_cowork_erp_push_enabled=True,
                  pearnly_features=sorted(set(config.get('pearnly_features') or []) | {'intake', 'master_data', 'push_log'}))
    config_path.write_text(json.dumps(config, indent=2) + '\n')
    frappe.conf.pearnly_cowork_erp_push_enabled = True
    frappe.conf.pearnly_features = config['pearnly_features']
    suffix = uuid4().hex[:8]

    def user(name, roles):
        doc = frappe.get_doc({'doctype': 'User', 'email': name, 'first_name': 'Load lab', 'enabled': 1,
                              'send_welcome_email': 0, 'user_type': 'System User',
                              'roles': [{'role': role} for role in roles]}).insert()
        doc.api_key, secret = uuid4().hex[:15], uuid4().hex[:15]
        doc.api_secret = secret
        doc.save()
        return {'user': name, 'auth': f'token {doc.api_key}:{secret}'}

    for role in ('Cowork Push Log',):
        if not frappe.db.exists('Role', role):
            frappe.get_doc({'doctype': 'Role', 'role_name': role}).insert()
    admin = user(f'cw-load-admin-{suffix}@example.invalid', ['Cowork Admin'])
    members = [user(f'cw-load-m{i:02d}-{suffix}@example.invalid', ['Cowork Intake', 'Cowork Push Log'])
               for i in range(1, MEMBERS + 1)]
    workspace = access.default_workspace()
    frappe.db.set_value('Cowork Workspace', workspace, 'tax_id', TAX, update_modified=False)
    frappe.set_user(admin['user'])
    users = [admin['user'], *[m['user'] for m in members]]
    main = connections.configure(workspace, 'express', uuid4().hex, 'Express', {}, users=users)
    frappe.db.commit()

    devices = []
    for index, member in enumerate(members, 1):
        frappe.set_user(member['user'])
        key = uuid4().hex + uuid4().hex
        found = credentials.register(uuid4().hex, True, False, None, key, f'PC-{index:02d}')
        assert found['status'] == 'created', found
        context = credentials.authenticate(found['token'])
        hello = credentials.hello(context, device_contracts.hello_request(
            {'device_key': key, 'name': f'PC-{index:02d}', 'host_names': [f'pc-{index:02d}']}))
        heartbeat._receive(credentials.authenticate(found['token']), _beat_body(index))
        frappe.db.commit()
        devices.append({'index': index, 'user': member['user'], 'auth': member['auth'], 'device_key': key,
                        'device': found['device'], 'token': found['token'], 'hello': hello.get('status')})

    # 推送前要定的付款方式与科目：每份账套像会计在复核页选过一次（账套记忆）
    keys = sorted({pool.canonical(main['name'], d['device'], row['path']) for d in devices
                   for row in _account_sets(d['index'])})
    frappe.set_user(admin['user'])
    for key in keys:
        for kind, value in (('payment', 'credit'), ('account', 'A')):
            choice_store.record(main['name'], key, kind, 'purchase|' + SELLER_TAX, value)
    frappe.db.commit()

    started = time.time()
    state = {'site': site, 'suffix': suffix, 'workspace': workspace, 'connection': main['name'], 'admin': admin,
             'devices': devices, 'second': None, 'queued': [], 'history_rows': 0, 'round': 0}
    if site == SITES[0]:
        # 一台电脑挂两条连接：第二条 Express 连接，第 1 台电脑用旧槽位密钥 + device_hello 挂上去（改挂到同一台电脑）
        frappe.set_user(admin['user'])
        other = connections.configure(workspace, 'express', uuid4().hex, 'Express 2', {}, users=users)
        issued = credentials.issue(other['name'], uuid4().hex)
        first = devices[0]
        hello = credentials.hello(credentials.authenticate(issued['token']), device_contracts.hello_request(
            {'device_key': first['device_key'], 'name': 'PC-01', 'host_names': ['pc-01']}))
        heartbeat._receive(credentials.authenticate(issued['token']), _beat_body(1))
        frappe.db.commit()
        account = pool.canonical(other['name'], first['device'], 'S:\\EXP\\SET0')
        for kind, value in (('payment', 'credit'), ('account', 'A')):
            choice_store.record(other['name'], account, kind, 'purchase|' + SELLER_TAX, value)
        frappe.db.commit()
        state['second'] = {'connection': other['name'], 'token': issued['token'], 'hello': hello.get('status'),
                           'device': credentials.device_of(issued['credential_id']), 'account': account}
    _fill(state)
    state['history_rows'] = _clone_history(main['name'], history_rows) if history_rows else 0
    frappe.db.commit()
    state['setup_seconds'] = round(time.time() - started, 1)
    _save(state)
    print(json.dumps({'site': site, 'devices': len(devices), 'queued': len(state['queued']),
                      'history_rows': state['history_rows'], 'second_connection': bool(state['second']),
                      'second_hello': (state['second'] or {}).get('hello'),
                      'second_same_computer': bool(state['second']) and state['second']['device'] == devices[0]['device'],
                      'setup_seconds': state['setup_seconds']}))
    frappe.destroy()


def _save(state):
    path = _state_path(state['site'])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state))
    os.chmod(path, 0o600)


def _queue(state, connection, member, account, number):
    """一张手工票，经真实准备 + 确认成为 ``member`` 的排队单（谁录的谁的电脑写）。"""
    import frappe
    from pearnly_cowork.companion import submission
    from pearnly_cowork.history import storage
    from pearnly_cowork.push.application import api as push_api
    fields = {'direction': 'purchase', 'seller_name': 'Load seller', 'seller_tax': SELLER_TAX, 'buyer_tax': TAX,
              'buyer_name': 'Load buyer', 'subtotal': '100', 'vat': '7', 'total_amount': '107',
              'date': '2026-10-01', 'document_type': 'tax_invoice',
              'invoice_number': f'LD-{state["suffix"]}-{state["round"]}-{number}'}
    pages = [{'page_index': 1, 'fields': fields}]
    frappe.set_user(member)
    doc = storage.create_recognized(state['workspace'], pages, uuid4().hex, source='manual', confidence='high',
                                    ai_raw=pages)
    revision = frappe.db.get_value('Cowork ERP Connection', connection, 'record_revision')
    prepared = push_api.prepare(doc['name'], connection, uuid4().hex, doc['record_version'], revision,
                                account_set_key=account)
    if prepared.get('status') != 'prepared':
        raise SystemExit(f'prepare failed: {prepared.get("status")} {prepared.get("reason")}')
    confirmed = submission.confirm(prepared['name'], prepared['revision'], uuid4().hex)
    if confirmed.get('status') != 'pending':
        raise SystemExit(f'confirm failed: {confirmed}')
    frappe.db.commit()
    return prepared['name']


def _fill(state):
    """每台电脑的成员各排 :data:`JOBS` 张（七成在共享盘账套、三成在自己本机的），挂两条连接的那台在第二条上另排
    :data:`SECOND_JOBS` 张。"""
    from pearnly_cowork.connections import pool
    rng = random.Random(f'{state["site"]}:{state["round"]}')
    connection, number, queued = state['connection'], 0, []
    for d in state['devices']:
        shared = [pool.canonical(connection, d['device'], f'S:\\EXP\\SET{k}') for k in range(SHARED)]
        local = [pool.canonical(connection, d['device'], f'C:\\EXP\\LOC{d["index"]}{k}') for k in range(LOCAL)]
        for _ in range(JOBS):
            number += 1
            account = rng.choice(shared) if rng.random() < 0.7 else rng.choice(local)
            queued.append(_queue(state, connection, d['user'], account, number))
    if state['second']:
        for _ in range(SECOND_JOBS):
            number += 1
            queued.append(_queue(state, state['second']['connection'], state['devices'][0]['user'],
                                 state['second']['account'], number))
    state['queued'] = queued


def jobs(site):
    """再压一轮之前：上一轮被移除的电脑由人点「连接」重新加入（register v2 ``rejoin``，整份重报），再给每台排一轮新单。"""
    import frappe
    from pearnly_cowork.companion import credentials, devices as device_rows, heartbeat
    state = json.loads(_state_path(site).read_text())
    connect(site)
    frappe.conf.pearnly_cowork_erp_push_enabled = True
    rejoined = 0
    for d in state['devices']:
        if device_rows.retired(d['device']):
            frappe.set_user(d['user'])
            found = credentials.register(uuid4().hex, True, False, None, d['device_key'], f'PC-{d["index"]:02d}',
                                         False, True)
            assert found['status'] == 'created', found
            d['device'], d['token'] = found['device'], found['token']
            heartbeat._receive(credentials.authenticate(found['token']), _beat_body(d['index']))
            frappe.db.commit()
            rejoined += 1
    if state['second'] and not state['second'].get('account'):
        from pearnly_cowork.connections import pool
        state['second']['account'] = pool.canonical(state['second']['connection'], state['second']['device'],
                                                    'S:\\EXP\\SET0')
    state['round'] = int(state.get('round') or 0) + 1
    _fill(state)
    _save(state)
    print(json.dumps({'site': site, 'round': state['round'], 'queued': len(state['queued']), 'rejoined': rejoined}))
    frappe.destroy()


def _clone_history(connection, rows):
    """推送记录表补到生产量级：按这条连接上一张排队单克隆 ``rows`` 张已成功的旧记录（新的主键与唯一键，建立时间往前推），
    只占行数，不进排队（status = success）。"""
    import frappe
    columns = [row[0] for row in frappe.db.sql('SHOW COLUMNS FROM `tabCowork Push Record`')]
    unique = {row[4] for row in frappe.db.sql("SHOW INDEX FROM `tabCowork Push Record` WHERE Non_unique=0")} - {'name'}
    template = frappe.db.get_value('Cowork Push Record', {'connection': connection, 'status': 'pending'}, 'name')
    made = 0
    while made < rows:
        batch = min(2000, rows - made)
        select = []
        for column in columns:
            if column == 'name':
                select.append("CONCAT('CWP-load-', LPAD(seq, 9, '0'), '-', %(salt)s)")
            elif column in unique:
                select.append(f"CONCAT('load-', seq, '-', %(salt)s)" if column != 'active_key' else 'NULL')
            elif column == 'status':
                select.append("'success'")
            elif column in ('created_at', 'creation', 'modified', 'queued_at'):
                select.append("DATE_SUB(NOW(6), INTERVAL seq MINUTE)")
            elif column in ('lease_expires_at', 'active_attempt', 'route_device'):
                select.append('NULL')
            else:
                select.append(f'`{column}`')
        frappe.db.sql(
            f"INSERT INTO `tabCowork Push Record` ({', '.join('`' + c + '`' for c in columns)}) "
            f"SELECT {', '.join(select)} FROM `tabCowork Push Record` "
            f"CROSS JOIN (SELECT seq FROM seq_{made + 1}_to_{made + batch}) s WHERE name=%(template)s",
            {'salt': uuid4().hex[:8], 'template': template})
        made += batch
        frappe.db.commit()
    return made


def history(site, rows):
    """已准备好的租户再补旧推送记录（同 ``setup --history``）。"""
    import frappe
    state = json.loads(_state_path(site).read_text())
    connect(site)
    made = _clone_history(state['connection'], rows)
    state['history_rows'] = state.get('history_rows', 0) + made
    _state_path(site).write_text(json.dumps(state))
    print(json.dumps({'site': site, 'history_rows': state['history_rows'],
                      'push_records_total': frappe.db.count('Cowork Push Record')}))
    frappe.destroy()


# ── run ───────────────────────────────────────────────────────────────────────────────────────────────
class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.calls = {}
        self.codes = {}
        self.both_connections = 0
        self.events = []

    def add(self, site, kind, ms, status):
        with self.lock:
            self.calls.setdefault((site, kind), []).append(ms)
            self.codes.setdefault((site, kind, status), 0)
            self.codes[(site, kind, status)] += 1


def _post(conn, site, path, body, auth):
    """一次 POST（保持连接；服务端关了空闲连接就重连一次再发，同 CC 桌面版的 requests 会话）。"""
    headers = {'Host': site, 'Content-Type': 'application/json', 'Authorization': auth}
    started = time.perf_counter()
    for attempt in range(2):
        try:
            conn.request('POST', path, body=json.dumps(body), headers=headers)
            response = conn.getresponse()
            raw = response.read()
            break
        except (http.client.HTTPException, OSError):
            conn.close()
            if attempt:
                raise
    ms = (time.perf_counter() - started) * 1000
    try:
        data = json.loads(raw or b'{}')
    except ValueError:
        data = {}
    return response.status, ms, data.get('message', data) if isinstance(data, dict) else {}


class Computer(threading.Thread):
    def __init__(self, site, device, stats, stop, *, second=None):
        super().__init__(daemon=True)
        self.site, self.device, self.stats, self.stop, self.second = site, device, stats, stop, second
        self.removed = False
        self.written = []
        self.jobs = {}
        self.holding = 0

    def call(self, conn, kind, path, body, token):
        status, ms, data = _post(conn, self.site, path, body, 'Bearer ' + token)
        self.stats.add(self.site, kind, ms, status)
        if status == 401:
            self.removed = True
        return status, data

    def lease_once(self, conn, token, label):
        status, data = self.call(conn, label, LEASE, {'max': 1, 'agent_id': 'load', 'index_pending': []}, token)
        jobs = (data or {}).get('jobs') or [] if status == 200 else []
        return jobs[0] if jobs else None

    def run(self):
        conn = http.client.HTTPConnection(HOST, PORT, timeout=180)
        rng = random.Random(self.device['device'])
        time.sleep(rng.random() * POLL_SECONDS)
        token = self.device['token']
        next_beat = next_ask = time.monotonic()
        full = True
        while not self.stop.is_set() and not self.removed:
            now = time.monotonic()
            if now >= next_beat:
                status, data = self.call(conn, 'heartbeat', BEAT, _beat_body(self.device['index'], full=full), token)
                full = not (status == 200 and (data or {}).get('account_sets_unchanged_ok'))
                next_beat = now + BEAT_SECONDS
            if now >= next_ask:
                self.call(conn, 'request_next', NEXT, {}, token)
                next_ask = now + NEXT_SECONDS
            if self.work(conn, token, 'lease', 'ack'):
                continue  # 写完马上再领
            self.stop.wait(POLL_SECONDS)
        conn.close()

    def work(self, conn, token, lease_label, ack_label):
        job = self.lease_once(conn, token, lease_label)
        if not job:
            return False
        with self.stats.lock:
            self.holding += 1
            if self.holding > 1:
                self.stats.both_connections += 1
        time.sleep(WRITE_SECONDS)  # CC 桌面版写 Express 的时间
        with self.stats.lock:
            self.holding -= 1
        self.jobs[ack_label] = self.jobs.get(ack_label, 0) + 1
        self.call(conn, ack_label, ACK, {'log_id': job['log_id'], 'result': 'success',
                                         'express_docnum': 'LD' + uuid4().hex[:10]}, token)
        self.written.append(job['log_id'])
        return True


class SecondLane(threading.Thread):
    """同一台电脑在第二条连接上的那把密钥：CC 桌面版每个成员各一个取任务线程，与第一条连接的同时在跑。"""
    def __init__(self, computer):
        super().__init__(daemon=True)
        self.computer = computer

    def run(self):
        computer = self.computer
        conn = http.client.HTTPConnection(HOST, PORT, timeout=180)
        time.sleep(random.Random('second').random() * POLL_SECONDS)
        while not computer.stop.is_set():
            if computer.work(conn, computer.second['token'], 'lease_2nd', 'ack_2nd'):
                continue
            computer.stop.wait(POLL_SECONDS)
        conn.close()


class Member(threading.Thread):
    """成员每 30 秒打开一次 CC 对话页：register v2（设备密钥 + 电脑名）。"""
    def __init__(self, site, device, stats, stop):
        super().__init__(daemon=True)
        self.site, self.device, self.stats, self.stop = site, device, stats, stop

    def run(self):
        conn = http.client.HTTPConnection(HOST, PORT, timeout=180)
        time.sleep(random.Random(self.device['user']).random() * REGISTER_SECONDS)
        while not self.stop.is_set():
            body = {'request_key': uuid4().hex, 'express': 1, 'device_key': self.device['device_key'],
                    'device_name': f'PC-{self.device["index"]:02d}'}
            status, ms, data = _post(conn, self.site, REGISTER, body, self.device['auth'])
            self.stats.add(self.site, 'register', ms, f'{status}:{(data or {}).get("status", "")}')
            self.stop.wait(REGISTER_SECONDS)
        conn.close()


def _gunicorn():
    process = subprocess.Popen(['/home/frappe/frappe-bench/env/bin/gunicorn', '-c', __file__, 'frappe.app:application'],
                               cwd=str(SITE_ROOT), stdout=subprocess.DEVNULL, stderr=open('/tmp/load-gunicorn.log', 'w'))
    for _ in range(240):
        try:
            conn = http.client.HTTPConnection(HOST, PORT, timeout=5)
            conn.request('GET', '/api/method/ping', headers={'Host': SITES[0]})
            if conn.getresponse().status == 200:
                return process
        except OSError:
            pass
        time.sleep(1)
    process.kill()
    raise SystemExit('gunicorn did not start')


def _db_status():
    import frappe
    connect(SITES[0])
    names = ('Innodb_row_lock_waits', 'Innodb_row_lock_time', 'Innodb_row_lock_time_max', 'Innodb_deadlocks',
             'Innodb_row_lock_current_waits', 'Questions', 'Com_select', 'Com_update', 'Com_insert')
    rows = dict(frappe.db.sql('SHOW GLOBAL STATUS WHERE Variable_name IN %s', (names,)))
    frappe.destroy()
    return {key: int(value) for key, value in rows.items()}


def run(seconds):
    import redis
    states = [json.loads(_state_path(site).read_text()) for site in SITES]
    store = redis.Redis.from_url(REDIS)
    store.flushdb()
    before = _db_status()
    server = _gunicorn()
    stats, stop = Stats(), threading.Event()
    threads = []
    try:
        for state in states:
            for device in state['devices']:
                second = state['second'] if (state['second'] and device['index'] == 1) else None
                computer = Computer(state['site'], device, stats, stop, second=second)
                threads.append(computer)
                if second:
                    threads.append(SecondLane(computer))
                threads.append(Member(state['site'], device, stats, stop))
        started = time.monotonic()
        for thread in threads:
            thread.start()
        # 压到一半：管理员移除租户 3 的两台电脑（它们照常在发心跳、领任务）
        stop.wait(seconds / 2)
        conn = http.client.HTTPConnection(HOST, PORT, timeout=180)
        removal = []
        target = states[-1]
        for device in target['devices'][-2:]:
            status, ms, data = _post(conn, target['site'], REMOVE, {'device': device['device']}, target['admin']['auth'])
            removal.append({'status': status, 'ms': round(ms, 1), 'result': (data or {}).get('status')})
            stats.add(target['site'], 'remove_device', ms, status)
        conn.close()
        stop.wait(max(0.0, seconds - (time.monotonic() - started)))
        stop.set()
        for thread in threads:
            thread.join(timeout=60)
    finally:
        server.terminate()
        server.wait(timeout=60)
    after = _db_status()
    server_side = {}
    for state in states:
        site = state['site']
        timings = {}
        for key in store.scan_iter(f'{site}:*'):
            key = key.decode()
            if key.endswith((':dberr', ':fnerr')):
                continue
            values = [float(v) for v in store.lrange(key, 0, -1)]
            timings[key.split(':', 1)[1]] = _summary(values)
        errors = {k.decode(): int(v) for k, v in store.hgetall(f'{site}:dberr').items()}
        raised = {k.decode(): int(v) for k, v in store.hgetall(f'{site}:fnerr').items()}
        server_side[site] = {'timings_ms': timings, 'db_errors': errors, 'raised': raised}
    client = {}
    for (site, kind), values in sorted(stats.calls.items()):
        client.setdefault(site, {})[kind] = {**_summary(values), 'per_minute': round(len(values) / seconds * 60, 1),
                                             'status': {str(s): n for (si, k, s), n in stats.codes.items()
                                                        if si == site and k == kind}}
    result = {'seconds': seconds, 'workers': os.environ.get('PEARNLY_LOAD_WORKERS', '6'),
              'threads': os.environ.get('PEARNLY_LOAD_THREADS', '4'), 'client': client, 'server': server_side,
              'innodb_delta': {key: after.get(key, 0) - before.get(key, 0) for key in after},
              'innodb_after': after, 'removal': removal, 'both_connections_at_once': stats.both_connections,
              'written': {state['site']: sum(len(t.written) for t in threads
                                             if isinstance(t, Computer) and t.site == state['site']) for state in states},
              'jobs_by_connection': {state['site']: {label: sum(t.jobs.get(label, 0) for t in threads
                                                                if isinstance(t, Computer) and t.site == state['site'])
                                                     for label in ('ack', 'ack_2nd')} for state in states},
              'threads_alive_at_end': sum(t.is_alive() for t in threads)}
    out = SITE_ROOT / SITES[0] / 'private' / 'concurrency-run.json'
    out.write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1))


def _summary(values):
    if not values:
        return {'n': 0}
    ordered = sorted(values)

    def pct(p):
        return round(ordered[min(len(ordered) - 1, int(round(p / 100 * (len(ordered) - 1))))], 2)
    return {'n': len(values), 'p50': pct(50), 'p95': pct(95), 'p99': pct(99), 'max': round(ordered[-1], 2),
            'mean': round(statistics.fmean(values), 2)}


# ── report ────────────────────────────────────────────────────────────────────────────────────────────
def report(site):
    import frappe
    from pearnly_cowork.companion import queue_heads
    from pearnly_cowork.connections import pool
    state = json.loads(_state_path(site).read_text())
    connect(site)
    # 这个租户各轮排的全部单（不含克隆的旧记录）
    connections = tuple(filter(None, (state['connection'], (state['second'] or {}).get('connection'))))
    names = tuple(row[0] for row in frappe.db.sql('''SELECT name FROM `tabCowork Push Record`
        WHERE connection IN %s AND name NOT LIKE 'CWP-load-%%' ''', (connections,)))
    rows = frappe.db.sql('''SELECT status, COUNT(*) FROM `tabCowork Push Record` WHERE name IN %s GROUP BY status''',
                         (names,))
    double = frappe.db.sql('''SELECT push_record, COUNT(*) n FROM `tabCowork Companion Delivery`
        WHERE push_record IN %s AND fence>0 GROUP BY push_record, fence HAVING n > 1''', (names,))
    redelivered = frappe.db.sql('''SELECT COUNT(*) FROM (SELECT push_record FROM `tabCowork Companion Delivery`
        WHERE push_record IN %s GROUP BY push_record HAVING COUNT(*) > 1) x''', (names,))[0][0]
    attempts = frappe.db.sql('''SELECT COUNT(*) FROM `tabCowork Push Attempt` WHERE push_record IN %s''', (names,))[0][0]
    success_twice = frappe.db.sql('''SELECT push_record, COUNT(*) n FROM `tabCowork Push Attempt`
        WHERE push_record IN %s AND status='success' GROUP BY push_record HAVING n > 1''', (names,))
    writers = frappe.db.sql('''SELECT r.entered_by, d.device_owner, COUNT(*) FROM `tabCowork Push Record` r
        LEFT JOIN `tabCowork CC Desktop Device` d ON d.name=r.written_by_device
        WHERE r.name IN %s AND r.status='success' GROUP BY r.entered_by, d.device_owner''', (names,))
    foreign = sum(n for entered, owner, n in writers if owner and entered != owner)
    total = frappe.db.count('Cowork Push Record')
    first = state['devices'][0]
    reach = pool.writable_reach(state['connection'], first['device'])
    params = {'connection': state['connection'], 'workspace': state['workspace'], 'client': '',
              'keys': tuple(sorted(reach)), **queue_heads.scope(device=first['device'], users={first['user']})}
    query = frappe.db.mogrify(queue_heads.Q_HEADS, params)
    query = query.decode() if isinstance(query, bytes) else query
    plan = frappe.db.sql('ANALYZE FORMAT=JSON ' + query)[0][0]
    explain = frappe.db.sql('EXPLAIN ' + query, as_dict=True)
    timings = []
    for _ in range(20):
        started = time.perf_counter()
        frappe.db.sql(queue_heads.Q_HEADS, params)
        timings.append((time.perf_counter() - started) * 1000)
    print(json.dumps({'site': site, 'queued': len(names), 'status': dict(rows),
                      'same_attempt_delivered_twice': len(double), 'records_delivered_more_than_once': redelivered,
                      'attempts': attempts,
                      'success_twice': len(success_twice), 'written_by_someone_elses_computer': foreign,
                      'push_records_total': total, 'q_heads_ms': _summary(timings),
                      'q_heads_explain': [{k: row[k] for k in ('id', 'select_type', 'table', 'type', 'key', 'rows', 'Extra')}
                                          for row in explain],
                      'q_heads_analyze': json.loads(plan)}, default=str, indent=1))
    frappe.destroy()


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    one = sub.add_parser('setup'); one.add_argument('site', choices=SITES); one.add_argument('--history', type=int, default=0)
    two = sub.add_parser('run'); two.add_argument('--seconds', type=int, default=240)
    three = sub.add_parser('report'); three.add_argument('site', choices=SITES)
    four = sub.add_parser('history'); four.add_argument('site', choices=SITES); four.add_argument('rows', type=int)
    five = sub.add_parser('jobs'); five.add_argument('site', choices=SITES)
    args = parser.parse_args()
    if os.environ.get('PEARNLY_COWORK_BACKEND_LAB') != '1':
        raise SystemExit('Dedicated Cowork backend lab required')
    if args.command == 'setup':
        setup(args.site, args.history)
    elif args.command == 'run':
        run(args.seconds)
    elif args.command == 'history':
        history(args.site, args.rows)
    elif args.command == 'jobs':
        jobs(args.site)
    else:
        report(args.site)


if __name__ == '__main__':
    main()
