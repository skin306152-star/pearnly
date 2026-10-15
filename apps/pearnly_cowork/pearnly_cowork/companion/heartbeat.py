"""One native machine report; no poll, ACK, external transport or session login."""
from datetime import datetime, timezone
from uuid import uuid4

import frappe
from frappe import _

from pearnly_cowork.connections import collector, model
from pearnly_cowork.connections.reports import ReportError, plan_report
from pearnly_cowork.targets.projection import ProjectionContractError
from . import credentials, http_auth, release


def _receive(context, body):
    # The HTTP hook establishes identity only. Recheck current token generation,
    # issuer/scope/grants and global switch inside each local write transaction.
    context = credentials.revalidate(context)
    doc = frappe.get_doc('Cowork ERP Connection', context.connection, for_update=True)
    previous = {**model.parse(doc.get('report_json'), {}), **model.parse(doc.config_json, {})}
    plan = plan_report(body, previous, datetime.now(timezone.utc))
    result = collector.ingest(doc, plan, credential_id=context.credential_id,
        token_generation=context.token_generation, issuer=context.issuer)
    # 报到时间只记在这把密钥的小行上；连接的上报记录只在内容变了时才重写（`collector.ingest`）。
    credentials.record_seen(context.credential_id, context.token_generation, offline=plan.offline)
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


@frappe.whitelist(methods=['POST'])
def report(**ignored):
    # Route arguments cannot replace identity or the body frozen by auth_hooks.
    context, body = http_auth.current_request()
    frappe.local.cowork_private_response = True
    for attempt in range(3):
        point = 'cowork_agent_report_' + uuid4().hex
        frappe.db.savepoint(point)
        try:
            return _receive(context, body)
        except frappe.QueryDeadlockError as error:
            # No external output has happened; the entire local unit may retry.
            frappe.db.rollback()
            if attempt == 2:
                http_auth.raise_busy(error, stage='report')
        except (ReportError, model.ConnectionError, ProjectionContractError) as error:
            frappe.db.rollback(save_point=point)
            frappe.throw(_(error.code))
        except Exception:
            frappe.db.rollback(save_point=point)
            raise
