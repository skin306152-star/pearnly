"""One native machine report; no poll, ACK, external transport or session login."""
from datetime import datetime, timezone
from uuid import uuid4

import frappe
from frappe import _

from pearnly_cowork.connections import collector, model
from pearnly_cowork.connections.reports import ReportError, plan_report
from pearnly_cowork.push.native import service as ledger
from pearnly_cowork.targets.projection import ProjectionContractError
from pearnly_core.cc_desktop import release
from pearnly_core.express_operator import contracts as operator_contracts
from pearnly_core.transaction import conflicts
from . import credentials, devices, http_auth

# 每台电脑各自的（ADR-021）：上次报的所选账套、科目映射与版本取它自己的电脑行，不取连接配置 / 上报记录
_PER_COMPUTER = frozenset(('companion_version', 'max_payload_version'))
# MR.ERP 连接的心跳只收这台电脑自己的几项（ADR-026 第 3 步）：没有 Express 账套、科目、目录、所选可报
_OPERATOR_KEYS = ('offline', 'device', 'companion_version', 'max_payload_version', 'operator_targets')


def _receive_operator(context, doc, body):
    """MR.ERP 连接的密钥报到（MR.ERP 代录开着才走到，`http_auth.folder_only_allows`）：只记报到时间、电脑行上的版本与能代录的
    目标、本机授权的 MR.ERP 连接与用户名；不进账套采集（`collector.ingest`），不写连接的上报记录，回复不带账套与「没变」许可。"""
    device = credentials.device_of(context.credential_id) or context.credential_id
    known = devices.state(device, doc.name)
    plan = plan_report({key: body[key] for key in _OPERATOR_KEYS if key in body}, known, datetime.now(timezone.utc))
    credentials.record_seen(context.credential_id, context.token_generation, offline=plan.offline)
    devices.record_report(device, doc.name, plan)
    if not plan.offline:
        devices.record_mrerp_logins(device, body.get('mrerp_logins'))
        devices.record_mrerp_users(device, body.get('mrerp_users'))
        devices.record_link(device, body.get('link'))
    reply = {'connected': bool(plan.response.get('connected'))}
    latest = release.latest()
    if latest:
        reply['companion_latest'] = latest
    return reply


def _receive(context, body):
    # The HTTP hook establishes identity only. Recheck current token generation,
    # issuer/scope/grants and global switch inside each local write transaction.
    context = credentials.revalidate(context)
    doc = frappe.get_doc('Cowork ERP Connection', context.connection, for_update=True)
    if doc.adapter in credentials.FOLDER_ONLY_ADAPTERS:
        if not operator_contracts.MRERP_OPERATOR_ENABLED:
            http_auth._deny_permission()
        return _receive_operator(context, doc, body)
    device = credentials.device_of(context.credential_id) or context.credential_id
    known = devices.state(device, doc.name)
    shared = {key: value for key, value in model.parse(doc.get('report_json'), {}).items() if key not in _PER_COMPUTER}
    plan = plan_report(body, {**shared, **known}, datetime.now(timezone.utc))
    member = devices.member(device, doc.workspace, connection=doc.name,
                            version=plan.patch.get('companion_version') or known.get('companion_version'),
                            payload_version=plan.patch.get('max_payload_version') or known.get('max_payload_version'))
    # 心跳不再写连接配置（方案 2.3 第 1 条）：账套名单进可达行、主档进账套资料，所选与映射记在这台电脑上
    result = collector.ingest(doc, plan, credential_id=context.credential_id,
        token_generation=context.token_generation, issuer=context.issuer, member=member,
        protected=lambda: ledger.open_account_keys(doc.name))
    # 报到时间只记在这把密钥的小行上；连接的上报记录只在内容变了时才重写（`collector.ingest`）。
    credentials.record_seen(context.credential_id, context.token_generation, offline=plan.offline)
    # 电脑行在加锁顺序的最后（Connection → Credential → 池里各行 → Device）
    devices.record_report(device, doc.name, plan)
    # 这台电脑上已授权了哪些 MR.ERP 连接（ADR-026 第 7 条，只有连接号）：旧版不报不动
    if not plan.offline:
        devices.record_mrerp_logins(device, body.get('mrerp_logins'))
        devices.record_mrerp_users(device, body.get('mrerp_users'))
        # 经哪台电脑连上来（云端 Express 的来源标记，只给界面显示）：旧版不报不动
        devices.record_link(device, body.get('link'))
    # 告诉小助手：账套名单没变时可以只报「没变」（`plan_report` 的 `account_sets_unchanged`）。
    # 旧服务端不回这一项，小助手就照旧每次整份上传 —— 两边谁先上线都不会把网页上的账套目录清掉。
    reply = {**result, 'account_sets_unchanged_ok': True}
    # 这把密钥刚挂到电脑上（`device_hello`，ADR-021）：这一次不许只报「没变」，逼电脑下次整份重报，直到收到整份。
    if credentials.full_report_due(context.credential_id,
                                   full_report=not plan.offline and body.get('account_sets') is not None):
        reply.pop('account_sets_unchanged_ok')
    # 我们最新发布的小助手版本：小助手比它旧就提示并更新，不再自己定时去查（`release.py`，2026-10-09）。
    latest = release.latest()
    if latest:
        reply['companion_latest'] = latest
    return reply


def _attempt(context, body):
    point = 'cowork_agent_report_' + uuid4().hex
    frappe.db.savepoint(point)
    try:
        return _receive(context, body)
    except frappe.QueryDeadlockError:  # conflict-ok: 保存点：只把三种业务错误转成提示，其余异常（含保存前比对）往外抛，外层 retried 接得到
        # No external output has happened; `conflicts.retried` rolls the entire local unit back and retries it.
        raise
    except (ReportError, model.ConnectionError, ProjectionContractError) as error:
        frappe.db.rollback(save_point=point)
        frappe.throw(_(error.code))
    except Exception:
        frappe.db.rollback(save_point=point)
        raise


@frappe.whitelist(methods=['POST'])
def report(**ignored):
    # Route arguments cannot replace identity or the body frozen by auth_hooks.
    context, body = http_auth.current_request()
    frappe.local.cowork_private_response = True
    # 撞上 1020 / 1213 整笔回滚、退避后重来（`transaction.conflicts`）；用尽同原来回「忙」，CC 桌面版下一轮再报。
    try:
        return conflicts.retried(_attempt, context, body)
    except (conflicts.Exhausted, conflicts.RollbackFailed) as error:
        http_auth.raise_busy(error, stage='report')
