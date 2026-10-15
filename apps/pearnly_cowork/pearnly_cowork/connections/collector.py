"""Persist authenticated Express reports and lease local directory refresh work.

The machine ingress owns current credential validation and the transaction. This
module has no HTTP, authentication, commit or external IO. Directory observations
never attest the separate preparation facts needed to push an ERP document.

Several computers report into one connection (ADR-021, plan 2.3):

- a report never writes the connection's configuration (``config_json``) nor bumps
  its revision: what a computer selected and its six account mappings belong to that
  computer (Companion stores them on the computer record);
- a full account-set list goes through the identity rules (`pool.apply_report`) and
  replaces only this computer's reach; the published endpoint directory is the union
  of every computer's reach (`pool.catalog`). An empty list clears only this computer;
- masters read for an account set go into that account set's Account Facts, whichever
  computer read them; the connection's report keeps only the current reporter binding
  (v1 preparation evidence, until batch 3), its versions and the update-blocked note.
"""
from datetime import datetime, timezone

import frappe
from frappe import _
from pearnly_cowork.connections import desktop_update
from pearnly_cowork.targets.projection import normalize_projection
from . import access, model, pool, preparation, refresh, snapshots, state

# 心跳带来的每次都变、又没有读的人：不写进任何地方（报到时间记在密钥行，`companion.credentials.record_seen`）。
VOLATILE_KEYS = frozenset(('agent_last_seen_at', 'account_sets_seen_at', 'accounts_seen_at',
                           'catalog_seen_at', 'stock_acc_groups_seen_at'))
# 连接的上报记录里还留的：当前上报那台的版本（证据 v1 的载荷版本，第 3 批改为按领取的电脑判）与被拦的自动更新。
REPORT_KEYS = frozenset(('max_payload_version', 'companion_version', desktop_update.KEY))


def _slim(report):
    """上报记录只留绑定、版本与被拦的更新；账套名单、主档、旧的按账套缓存都不再放在连接这一行（ADR-021）。"""
    for key in [key for key in report if key not in REPORT_KEYS and key != '_preparation_binding']:
        report.pop(key, None)
    return report


def _masters(plan):
    return {key: value for key, value in plan.patch.items() if key in pool.FACT_KEYS and key not in VOLATILE_KEYS}


def _account_observation(doc, observation, account, observed_at):
    """按账套读回的主档（网页 / LINE 的下拉）发布到那份账套的规范键名下；名单换成池目录。"""
    return {**observation, 'account_set_key': account, 'observed_at': observed_at,
            'account_sets': pool.catalog(doc.name, lock=True)}


def ingest(doc, plan, *, credential_id, token_generation, issuer, member, protected=None, now=None):
    """``member``：这台电脑（`member.Member`，由 Companion 读好）；``protected()``：被未了结推送记录引用的账套键
    （账套资料不淘汰它们）。"""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    if (doc.adapter != 'express' or not doc.enabled or int(doc.binding_generation or 0) != 0):
        frappe.throw(_('connection.refresh_stale'))

    def canonical(local):
        return pool.canonical(doc.name, member.device, local)

    # Validate authority before any report mutation. An obsolete or foreign
    # completion cannot publish a directory as a side effect of being rejected.
    completed = refresh.validate_completion(doc, plan.completion, credential_id, token_generation, now,
                                            device=member.device, canonical=canonical)
    report = _slim(model.parse(doc.get('report_json'), {}))
    report.update({key: value for key, value in plan.patch.items() if key == desktop_update.KEY})
    # 当前上报这台的版本（它这次报的，没报就是它上次报的），证据 v1 按它判载荷版本
    for key, value in (('companion_version', member.version), ('max_payload_version', member.payload_version)):
        if value not in (None, ''):
            report[key] = value
        else:
            report.pop(key, None)
    preparation.bind_report(report, plan, credential_id, token_generation, issuer, member.device)
    encoded = model.dumps(report)
    if len(encoded.encode()) > 16 * 1024 * 1024:
        frappe.throw(_('connection.snapshot_too_large'))
    # 内容、上报凭据都没变就不写连接这一行。`report_received_at` 因此是「上报内容最近一次变化」的时间。
    if (encoded != (doc.get('report_json') or '') or doc.report_credential != credential_id
            or int(doc.report_generation or 0) != int(token_generation)):
        doc.report_json = encoded
        doc.report_received_at = now
        doc.report_credential = credential_id
        doc.report_generation = token_generation
        access.write(doc)
    result = {'ok': True, 'endpoint_id': doc.name, **plan.response,
              'connection': {'endpoint_id': doc.name, 'endpoint_name': doc.label or 'Express',
                             'pearnly_account': issuer}}
    if plan.offline:
        return result
    refresh.expire(doc, now, credential_id=credential_id, token_generation=token_generation, device=member.device)
    observed_at = now.replace(tzinfo=timezone.utc)
    published = {}
    changed = identified = False
    if plan.account_sets is not None:
        changed, identified = pool.apply_report(doc, member, plan.account_sets, now)
    else:
        pool.touch(doc.name, member.device, now)
    if pool.has_pending(doc.name, member.device):
        changed = pool.reconsider(doc, member, now) or changed
    if identified:
        # 一份登记刚补上文件号 / 网络路径：别的电脑等着它的待认定行这就能认上
        changed = pool.reconsider(doc, member, now, others=True) or changed
    if plan.account_sets is not None or changed:
        union = normalize_projection(pool.union_observation(doc.name, observed_at))
        published[('endpoint', '@endpoint')] = snapshots.publish(doc, union, preserve_pending=True)['revision']
    selected = canonical(plan.lease_selection) if plan.lease_selection else ''
    account = canonical(plan.refreshed_account) if plan.refreshed_account else selected
    values = _masters(plan)
    if values and account:
        pool.put_facts(doc, account, values, device=member.device, routine=account == selected, now=now,
                       protected=protected)
    for observation in plan.observations:
        if not observation.get('account_set_key'):
            continue  # 这台电脑自己的名单不单独发布：目录是池的并集（上面）
        key = canonical(observation['account_set_key'])
        normalized = normalize_projection(_account_observation(doc, observation, key, observed_at))
        published[('account_set', key)] = snapshots.publish(doc, normalized, preserve_pending=True)['revision']
    if plan.projection_error and plan.projection_error.get('scope') != 'endpoint':
        # 一台电脑报了空名单只清它自己的可达行，不把整个池目录标错；按账套读失败的照旧记在那份账套上
        error = plan.projection_error
        kind, key, head_key = state.scope(canonical(error['account_set']), doc.adapter)
        heads = state.heads(doc)
        heads[head_key] = {**heads.get(head_key, {}), 'status': 'error', 'error_code': str(error['error_code'])[:200]}
        doc.projection_heads = model.dumps(heads)
        access.write(doc)
    if completed is not None:
        revision = published.get((completed.scope_kind, completed.scope_key))
        refresh.finish(doc, completed, revision=revision, error=plan.completion.get('error_code'), now=now)
    leased = refresh.lease(doc, member, plan.lease_selection, credential_id, token_generation, now)
    if leased is not None:
        result['master_refresh'] = leased
    return result
