"""这台电脑上任一份 Express 账套的主档（科目表、部门、供应商 / 客户、商品、存货科目组）在不在、要不要请电脑读。

一台事务所的电脑管着很多家客户的账套，CC 桌面端平时只按期上报它当前选的那一份；别的账套的主档要按账套读一次
（`refresh.lease` 交给够得到它的一台电脑、`collector` 存进那份账套自己的账套资料 `pool.facts`、`preparation` 按账套取；
多台电脑时哪台读的都一样，同一份物理账套，ADR-021）。要人选的项、推送准备、
定时任务的默认科目要用某份账套的主档时，先经这里：有就用；没有就请电脑读（同一份账套同一时刻只排一个，不打断正在读的），
并照实回「正在读 / 电脑离线 / 要升级 / 读不到」，不把空的科目表当成「没有科目」交给人选。

只做本地读写，不提交、不发外部请求；调用方持有连接行锁（`state.read` 默认加锁）并负责提交。
"""
from datetime import datetime, timedelta, timezone
import uuid

import frappe

from pearnly_core.express_rules.targets.identity import normalize_express_account_key
from . import access, model, pool, preparation, refresh, state

READY, READING, OFFLINE, UPDATE_NEEDED, UNAVAILABLE, FAILED = (
    "ready", "reading", "offline", "update_needed", "unavailable", "failed")
# 别的账套的主档读过多久后，用到时在后台再读一次（照旧先用读过的那份）：与电脑当前选的账套同一节奏
# （CC 桌面端每 30 分钟按期上报一次所选账套的目录，`worker._CATALOG_INTERVAL_S`）。
REREAD_AFTER = timedelta(minutes=30)
# 读失败（账套打不开、文件夹连不上）后多久再请电脑读：这期间照实回「读不到」，不每次都排一个新的。
RETRY_AFTER_FAILURE = timedelta(minutes=10)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _chart(found):
    """读到过这份账套的科目表、且不是空的（Express 账套总有科目表；空的是没读出来）。"""
    accounts = (found or {}).get("reported_accounts")
    return isinstance(accounts, list) and bool(accounts)


def _last_read(doc, key):
    """这份账套最近一次读（请求）的状态、结束时间与失败原因；没请求过回三个 None。"""
    head = state.heads(doc).get(key) or {}
    if not head.get("request_id"):
        return None, None, None
    row = frappe.db.get_value(state.REFRESH, head["request_id"],
                              ["connection", "connection_revision", "status", "completed_at", "error_code"], as_dict=True)
    if not row or row.connection != doc.name or int(row.connection_revision or 0) != int(doc.record_revision or 0):
        return None, None, None
    return row.status, row.completed_at, row.error_code


def view(doc, account, now=None):
    """这份账套主档此刻的状况（只读）：``{"state", "account_set", "selected", "present", "request", "error_code"?}``。

    ``present``：读到过这份账套的科目表（账套资料）。``state``：有且电脑在线是 ``ready``；电脑报过离线是
    ``offline``；没有时按情况是 ``reading``（在读或该读）/ ``failed``（刚读过还是没有）/ ``unavailable``（池里没有电脑
    够得到它、能写）/ ``update_needed``（电脑上的 CC 桌面端只读得了它当前选的那份）。
    ``selected``：这份资料是某台电脑按期上报它当前选的账套时带来的、还新（那台会自己接着报，不另排）。
    ``request`` 为 ``"new"`` 表示该请电脑读（没有，或别的账套读过很久了），否则 ``None``。
    在线与版本看池里够得到这份账套的电脑（`member.presence`，由 Companion 经钩子提供）：任一台在线就算在线，任一台的
    CC 桌面端读得了别的账套就不算要升级；池里还没有电脑报过账套名单时看池里任一台。"""
    now = now or _now()
    account = normalize_express_account_key(account)
    result = {"state": READY, "account_set": account, "selected": False, "present": True, "request": None}
    if doc.adapter != "express" or not account:
        return result
    peers = pool.presence(doc.name)
    reachers = [device for device in pool.reachers(doc.name, account, writable=True) if device in peers]
    pool_reported = pool.any_reach(doc.name)
    relevant = reachers if pool_reported else list(peers)
    online = any(peers[device].get("online") for device in relevant)
    facts = pool.facts(doc.name, account) or {}
    seen = facts.get("_seen_at")
    selected = bool(facts.get("_routine") and seen and now - seen < 2 * REREAD_AFTER)
    found = preparation.reported_masters(doc, account) or {}
    present = _chart(found)
    _, _, key = state.scope(account, doc.adapter)
    status, completed_at, error_code = _last_read(doc, key)
    pending = status in ("requested", "leased")
    result.update(selected=selected, present=present)
    if present:
        # 别的账套：读过很久了就在后台再读一次（照旧先用这份）；电脑当前选的那份由它按期上报。
        if not selected and not pending and (completed_at is None or now - completed_at >= REREAD_AFTER):
            result["request"] = "new"
        result["state"] = READY if online else OFFLINE
        return result
    if (relevant and not selected
            and not any(refresh.reads_any_account_set(peers[device].get("version")) for device in relevant)):
        result["state"] = UPDATE_NEEDED  # 旧版只读它当前选的那份：别的账套的请求不会交给它
        return result
    if pending:
        result["state"] = READING if online else OFFLINE
        return result
    read_empty = isinstance(found.get("reported_accounts"), list)  # 读到了、科目表是空的（出自当前凭据）
    if (completed_at is not None and now - completed_at < RETRY_AFTER_FAILURE
            and (status == "failed" or (status == "succeeded" and read_empty))):
        # 刚读过还是没有科目表（读失败，或那份账套读不出科目）：照实说，过一阵再请电脑读
        result.update(state=FAILED, error_code=error_code or "ERR_CHART_EMPTY")
        return result
    if peers and pool_reported and not reachers:
        result["state"] = UNAVAILABLE  # 池里没有电脑够得到它、能写：读不到，也推不了
        return result
    result.update(state=READING if online else OFFLINE, request="new")
    return result


def ensure(doc, account, actor, now=None):
    """要用这份账套的主档：缺的、读过很久的请电脑读一次（已经在读就不再排，也不打断它）；回 :func:`view`。

    ``doc`` 是已加锁的连接行；建了请求时 ``result["requested"]`` 为真（调用方据此提交）。"""
    now = now or _now()
    result = view(doc, account, now)
    if result.pop("request") != "new":
        return result
    kind, key, head_key = state.scope(result["account_set"], doc.adapter)
    identity = model.request_identity(frappe.local.site, actor, "refresh:" + doc.name, "auto:" + uuid.uuid4().hex)
    signature = model.fingerprint([doc.name, int(doc.record_revision), kind, key])
    refresh.create(doc, kind, key, head_key, actor=actor, identity=identity, signature=signature, now=now)
    access.write(doc)
    result["requested"] = True
    return result


__all__ = ["FAILED", "OFFLINE", "READING", "READY", "UNAVAILABLE", "UPDATE_NEEDED", "ensure", "view"]
