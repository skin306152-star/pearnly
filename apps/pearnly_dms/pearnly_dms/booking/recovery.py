"""Reconcile an existing submission and retry its receipt; never create an order.

The immutable attempt context is saved before the first external POST. Recovery
uses the same identity/critical-field readback contract as initial submission.
Legacy attempts without that evidence remain blocked for explicit reconciliation.

结果未知的提交证据**不留在会话里**（会话是一个 LINE 用户唯一的那一行，留着它整个 LINE 都被锁）：

· 会话路径 `reconcile`（扫描、或本人发消息时触发）：已核实的照旧 `finish`；过了执行体宽限期
  仍未核实的 `hold` —— 证据原样转存成 `Unverified` 记录、再清会话，LINE 立刻可用。
  这条路径**不登录 DMS**。
· 记录路径 `reconcile_record`：用**原提交人**的端点与身份指纹只读回读，**不看 LINE 绑定版本**
  （解绑、重绑之后照样核）。核到 → 台账改成功、记录转 Pending 交给 `delivery.deliver`
  （绑定变了按现有规则转 Review，不推给新绑定的人）；核不到 → 按 5/10/20/40/60 分钟退避；
  从登记起满 23 小时 → Review、停止核对，台账那行留"人工处理"和单号。
· `unverified_booking_no`：核对期间只拦"同一 DMS 系统、同一客户"再下单，其余一切照常。
"""
import datetime
import hashlib
import json
import logging
import uuid

import frappe

from pearnly_core import line_session as store
from pearnly_dms.booking import booking_attempt, booking_ledger, delivery
from pearnly_dms.booking.booking_submit import verify_created_booking
from pearnly_dms.contracts.endpoint import endpoint_name
from pearnly_dms.adapter.operations import run_logged_in
from pearnly_dms.line.identity import matches
from pearnly_dms.ocr.ocr_service import resolve_dms_endpoint

logger = logging.getLogger(__name__)
WORKER_GRACE_SECONDS = 660  # Existing worker timeout is 600 s.
# LINE retry keys expire at 24 h; an unknown result is also read back for at most this long.
RECEIPT_WINDOW_SECONDS = 23 * 3600
CHECK_SECONDS = 300  # 记录路径的核对间隔：5、10、20、40 分钟，之后每小时一次
CHECK_MAX_SECONDS = 3600
_RELEASE = "if redis.call('get',KEYS[1]) == ARGV[1] then return redis.call('del',KEYS[1]) else return 0 end"


def _state_store():
    """不淘汰的状态存储（``pearnly_core.execution.state``；用到时才导入，离线检查导入本模块不需要共用层）。"""
    from pearnly_core.execution import state as pearnly_state

    return pearnly_state.store()



def endpoint_fingerprint(endpoint):
    return delivery.identity_fingerprint(endpoint)


def _marker(line_user_id):
    return ((store.get_session(line_user_id) or {}).get('payload') or {}).get('booking_attempt') or {}


def _age(stamp):
    if not stamp:
        return float('inf')
    return (frappe.utils.get_datetime() - frappe.utils.get_datetime(stamp)).total_seconds()


def finish(line_user_id, nonce, qa, result):
    """Transfer verified success to durable delivery before clearing the draft."""
    marker = _marker(line_user_id)
    if (marker.get('nonce') != nonce or not result.get('ok') or not result.get('booking_id')
            or str(result.get('booking_no') or '') != str(marker.get('booking_no') or '')):
        return False
    result = {key: result[key] for key in ('ok', 'booking_no', 'booking_id', 'attach_ok',
                                         'attached', 'attach_failed') if key in result}
    if not store.update_booking_attempt(line_user_id, nonce, {'result': result}):
        return False
    marker = {**_marker(line_user_id), 'completed_at': frappe.utils.now()}
    name = delivery.retain(line_user_id, marker)
    if not store.clear_booking_attempt(line_user_id, nonce):
        return False  # The scanner can complete this handoff after a crash.
    return delivery.deliver(name)


def ready(marker):
    context = marker.get('context') or {}
    if not context.get('binding') or not context.get('submitted') or not context.get('endpoint'):
        return False
    if marker.get('result', {}).get('ok'):
        return True  # Even expired/rebound delivery must leave the active draft.
    # The original worker may still be running (600 s timeout). Holding reads nothing.
    return _age(marker.get('recorded_at')) > WORKER_GRACE_SECONDS


def request(line_user_id, marker=None):
    marker = marker or _marker(line_user_id)
    if ready(marker):
        frappe.enqueue('pearnly_dms.booking.recovery.reconcile', queue='long', timeout=600,
                       job_id='dms-reconcile-' + hashlib.sha256(line_user_id.encode()).hexdigest()[:24],
                       deduplicate=True, line_user_id=line_user_id)


def hold(line_user_id, nonce, *, notify=False):
    """结果未知：证据原样转存成 `Unverified` 记录（**先提交**），再清会话 —— LINE 立刻可用。

    执行体自己的分支传 `notify=False`：它随后自己推那句未知提示。扫描路径传 `notify=True`：
    执行体崩溃时操作员什么都没收到，所以**只有本次新建了记录**、且绑定没变时才补推一次，
    台账补一行"人工处理"（已有就不再加）。清会话抢不到锁时标记留着，下一轮扫描再转，
    那时记录已存在，不会重复推送。
    """
    marker = _marker(line_user_id)
    if not nonce or marker.get('nonce') != nonce or (marker.get('result') or {}).get('ok'):
        return False
    _name, created = delivery.hold(line_user_id, marker)
    cleared = store.clear_booking_attempt(line_user_id, nonce)
    if created and notify:
        context = marker.get('context') or {}
        binding = context.get('binding') or {}
        unknown = booking_attempt.unknown_result(marker['booking_no'])
        booking_ledger.log_booking(endpoint=context.get('endpoint'), payload={'qa': context.get('qa') or {}},
                                   result=unknown, actor=binding.get('user_id'))
        if matches(binding, line_user_id):
            from pearnly_dms import i18n
            from pearnly_dms.line import out

            with i18n.line_language(line_user_id):  # 定时任务发的：按收件人的手机语言
                out.push_text(line_user_id, booking_attempt.message(unknown))
    return cleared


def reconcile(line_user_id):
    # Bounded lease excludes another recovery worker. The conversation path never
    # reads DMS: verified results are finished, unknown ones are held as records.
    cache = _state_store()
    key = cache.make_key('pearnly:dms:reconcile:' + line_user_id)
    owner = uuid.uuid4().hex
    if not cache.set(key, owner, nx=True, ex=660):
        return
    try:
        marker = _marker(line_user_id)
        if not ready(marker):
            return
        result = marker.get('result') or {}
        if result.get('ok'):
            finish(line_user_id, marker['nonce'], (marker.get('context') or {}).get('qa') or {}, result)
            return
        hold(line_user_id, marker['nonce'], notify=True)
    finally:
        cache.eval(_RELEASE, 1, key, owner)


def _read_back(endpoint, marker):
    """原提交人的会话里只读回读（列表搜索 + 详情表单，绝不碰 `new.php`）。"""
    context = marker['context']
    qa = context.get('qa') or {}

    def read(client, adapter):
        booking_id = verify_created_booking(client, marker['booking_no'], context['submitted'])
        if not booking_id:
            return {'ok': False}
        form = client._parse_form_defaults(client._post_text(
            'drfcbc/form.php', {'status': 'e', 'id': booking_id}))
        names = {v for k, v in form.items() if k.startswith('fulcurrname')}
        files = qa.get('files') or {}
        expected = [label for key, label in (('id_card_mid', 'สำเนาบัตรประชาชน'),
                                            ('slip_mid', 'ใบโอนเงินจอง')) if files.get(key)]
        return {'ok': True, 'booking_id': booking_id, 'booking_no': marker['booking_no'],
                'attach_ok': all(name in names for name in expected),
                'attached': sum(name in names for name in expected)}

    # 后台任务（scheduler 派进 long 队列），没人等着看结果 → 预算走共用口径的
    # 后台那一档（90 秒），见 `pearnly_dms.config.pacing`。
    return run_logged_in(endpoint, read, background=True)


def _next_check(marker):
    """下一次核对：5 分钟起按次翻倍、封顶 1 小时，且不晚于 23 小时到期那一刻。"""
    checks = (marker.get('reconcile') or {}).get('attempts', 0)
    due = frappe.utils.get_datetime() + datetime.timedelta(
        seconds=min(CHECK_MAX_SECONDS, CHECK_SECONDS * 2 ** min(checks, 4)))
    if not marker.get('recorded_at'):
        return due
    expiry = frappe.utils.get_datetime(marker['recorded_at']) + datetime.timedelta(
        seconds=RECEIPT_WINDOW_SECONDS)
    return min(due, expiry)


def _park(name, marker, error, *, status=delivery.UNVERIFIED):
    frappe.db.set_value(delivery.DT, name, {
        'attempt': json.dumps(marker, ensure_ascii=False), 'status': status,
        'last_error': str(error or '')[:140], 'next_attempt_at': _next_check(marker)})
    frappe.db.commit()


def reconcile_record(name):
    """只读核对一条 `Unverified` 记录；绝不新建订单，也不看当前 LINE 绑定版本。"""
    cache = _state_store()
    key = cache.make_key('pearnly:dms:reconcile-record:' + name)
    owner = uuid.uuid4().hex
    if not cache.set(key, owner, nx=True, ex=660):
        return False
    try:
        doc = frappe.get_doc(delivery.DT, name)
        if doc.status != delivery.UNVERIFIED:
            return False
        marker = json.loads(doc.attempt)
        context = marker.get('context') or {}
        binding = context.get('binding') or {}
        if _age(marker.get('recorded_at')) >= RECEIPT_WINDOW_SECONDS:
            # 到期：停止核对、解除同客户拦截；台账那行留"人工处理"和单号。
            _park(name, marker, 'unverified_expired', status='Review')
            return False
        marker['reconcile'] = {'attempts': (marker.get('reconcile') or {}).get('attempts', 0) + 1}
        _park(name, marker, 'unverified')  # Persist the next check before any IO.
        endpoint = resolve_dms_endpoint(binding.get('user_id'), context.get('endpoint'))
        if (endpoint_name(endpoint) != context.get('endpoint') or
                endpoint_fingerprint(endpoint) != context.get('endpoint_fingerprint')):
            _park(name, marker, 'endpoint_changed')  # The original identity is gone: no DMS login.
            return False
        result = _read_back(endpoint, marker)
        if not result.get('ok'):
            _park(name, marker, result.get('error_code') or 'unverified')
            return False  # A missing/ambiguous result never authorizes a new POST.
        booking_ledger.log_booking(endpoint=context['endpoint'], payload={'qa': context.get('qa') or {}},
                                   result=result, actor=binding.get('user_id'))
        delivery.retain(doc.line_user_id, {**marker, 'result': result, 'completed_at': frappe.utils.now()})
        return delivery.deliver(name)
    finally:
        cache.eval(_RELEASE, 1, key, owner)


def unverified_booking_no(endpoint, qa):
    """同一 DMS 系统（服务地址）、同一 DMS 客户还有一张结果未知、正在核对的订车单 → 它的单号。

    平时没有这种记录，查询结果为空；有时只拦这一位客户（别的操作员给他下单也拦），
    核到或满 23 小时即解除。读不出客户或系统的记录不拦。
    """
    customer = str(((qa or {}).get('customer') or {}).get('id') or '')
    if not customer:
        return ''
    rows = frappe.get_all(delivery.DT, filters={'status': delivery.UNVERIFIED},
                          fields=['booking_no', 'attempt'], order_by='creation asc', limit_page_length=0)
    systems = {}

    def system(value):
        endpoint_id = endpoint_name(value)
        if endpoint_id not in systems:
            url = frappe.db.get_value('Pearnly ERP Endpoint', endpoint_id, 'system_url') if endpoint_id else ''
            systems[endpoint_id] = str(url or '').strip() or 'endpoint:' + endpoint_id
        return systems[endpoint_id]

    for row in rows:
        try:
            context = (json.loads(row.attempt or '{}') or {}).get('context') or {}
        except ValueError:
            continue
        if (str(((context.get('qa') or {}).get('customer') or {}).get('id') or '') == customer
                and context.get('endpoint') and system(context['endpoint']) == system(endpoint)):
            return str(row.booking_no or '')
    return ''


def reconcile_due():
    # Keyset traversal lets legacy/expired/rebound attempts remain safely stored
    # without starving all later recoverable submissions behind the first 50.
    cursor_key = 'pearnly:dms:reconcile-cursor'
    cursor = frappe.cache().get_value(cursor_key, use_local_cache=False) or ''
    rows = frappe.db.sql(
        "select name, line_user_id, payload from `tabPearnly LINE Session` "
        "where state='booking_review' and JSON_EXTRACT(payload, '$.booking_attempt') is not null "
        "and name > %s order by name asc limit 50", (cursor,), as_dict=True,
    )
    for row in rows:
        try:
            payload = json.loads(row.payload) if isinstance(row.payload, str) else row.payload
            request(row.line_user_id, (payload or {}).get('booking_attempt') or {})
        except Exception:
            logger.warning('Booking recovery scheduling failed; retained for next pass', exc_info=True)
    frappe.cache().set_value(cursor_key, rows[-1].name if len(rows) == 50 else '', expires_in_sec=86400)
    # 已转存、到点的结果未知记录：逐条只读核对（到期转 Review 也在 `reconcile_record` 里）。
    for name in frappe.get_all(delivery.DT, filters={'status': delivery.UNVERIFIED,
                                                     'next_attempt_at': ['<=', frappe.utils.now()]},
                               order_by='next_attempt_at asc', limit_page_length=50, pluck='name'):
        try:
            frappe.enqueue('pearnly_dms.booking.recovery.reconcile_record', queue='long', timeout=600,
                           job_id='dms-reconcile-record-' + name, deduplicate=True, name=name)
        except Exception:
            logger.warning('Unverified booking check scheduling failed; retained for next pass', exc_info=True)
