# -*- coding: utf-8 -*-
"""操作动作顺手把 DMS **使用记录**里的增量补回缓存。

一个 DMS 被几十上百人共用：别人新增/改了客户、车型、地址、员工，我们这边拿的是
缓存，不会自己知道。全量重扫又慢又贵，手动刷新还要求人记得点。DMS 自己为每次
增删改都记了一行使用记录（`client/eventlog_reader`），所以"哪几条变了"可以直接问它，
**只补读被点名的那几条**。

## 唯一的两个时点

1. **绑定成功** → 建一次基线（`seed`）：记下每个模块此刻的事件总数与流水号，
   本轮不补任何东西 —— 底座由同一次绑定的四组全量读取负责。
2. **操作员每次真实使用 DMS** → `catch_up`：拿事件总数和上次比，变了才翻流水，
   把被点名的记录补读回来。没变化时每个模块只花 1 个请求。

除此之外没有定时任务、没有手动按钮、没有第二个账号。

## 判断规则（宁可不判断，也不猜）

* **读不到＝新鲜度未知**：任何一个模块的计数读不到，本轮立刻停手，绝不把"读不到"
  当成"没变化"（对方的数据端点没有权限校验，随时可能被收紧）。
* **点名不到具体记录**（标识为空、或动作我们不认识）→ 不逐条猜，登记一次整组重读。
* **删除** → 不在这里猜"删掉了谁"，同样交给整组重读（按原生列表的真实成员收敛）。
* **积压太多**（一轮页数上限还没追上游标）→ 整组重读，不逐页硬啃。
* **计数变小**（可能被保留窗口截断）→ 同样按"未知"处理，整组重读。

## 并发与代价

* 一轮同步有硬预算：`PULL_SECONDS` 秒 / `PULL_REQUESTS` 个请求；用不完就下一轮接着来，
  并且从上次停下的模块轮着开始，不让后面的模块永远轮不上。
* 同一个连接同时只跑一轮（Redis 互斥，拿不到就跳过，不排队不等待）。
* 水位写在快照表自己的 `changes` 行上，按"更靠前"合并，绝不回退。
* 整组重读走既有队列（`manual.request`），**不阻塞操作员**，并以 `Administrator`
  的名义登记（服务自身的动作，不是某个操作员发起的）。

## 已知边界（如实登记，不当作已解决）

* 批量导入可能只记一条"导入"事件、不带明细 → 那时走整组重读兜底。
* 使用记录有保留窗口，太久以前的改动翻不到 → 水位断档时同样是整组重读。
* 我们缓存的表里还有几张（`typecardesc`/`cargear`/`businessowner`/`setcbc`/`cusbank`/
  `numdayspullcarback`）没找到对应的原生模块名，它们的变化暂时只能靠整组重读发现。
"""
from __future__ import annotations

import logging
import time

import frappe

from pearnly_dms.client import eventlog_reader
from pearnly_dms.client.eventlog_reader import EventLogLimits
from pearnly_dms.client.snapshot_reader import SnapshotLimits, read_customer_complete
from pearnly_dms.contracts.client_base import DmsClientError
from pearnly_dms.contracts.id_validate import normalize_thai_id
from pearnly_dms.snapshots import manual, store

logger = logging.getLogger(__name__)

VERSION = 1
#: 基线建不起来时，隔多久再试一次（别让每次操作都白花 2 秒重读一遍流水头）。
SEED_RETRY_SECONDS = 900
#: 同一个数据集两次整组重读之间至少隔这么久。
REFRESH_COOLDOWN_SECONDS = 600
#: 一轮同步的互斥时长（进程被杀由它自动解）。
LOCK_SECONDS = 120
#: 一轮同步的硬预算。
PULL_SECONDS = 6.0
PULL_REQUESTS = 90
#: 追水位最多翻几页；翻不完就是积压太多，交整组重读。
MAX_NEW_PAGES = 4
#: 服务自身的动作以谁的名义登记（Frappe 的 Administrator 具备全部角色）。
SERVICE_ACTOR = "Administrator"

#: 读"最新一条"用的最小请求（1 页 1 条）。
HEAD_LIMITS = EventLogLimits(page_size=1, max_pages=1, max_seconds=10.0)
#: 补读单个客户时的读取上限。
RECORD_LIMITS = SnapshotLimits(max_seconds=15)

#: 模块 → (归属的整组数据集, 是否支持按记录补读)。
#: 只登记**我们缓存里有的**表（覆盖度实测见任务记录），别的模块与我们无关。
MODULES = (
    ("ลูกค้า", "customers", True),               # 客户
    ("คำนำหน้านาม", "customers", False),          # 客户称谓（前缀）
    ("สาขาของลูกค้า", "customers", False),        # 客户分店
    ("รุ่นรถ", "vehicles", False),               # 车型
    ("สีรถ", "vehicles", False),                 # 颜色
    ("ยี่ห้อรถยนต์", "vehicles", False),          # 品牌
    ("ประเภทรถ", "vehicles", False),             # 车辆类型
    ("ประเภทย่อย", "vehicles", False),           # 车辆子类型
    ("เกรด", "vehicles", False),                 # 等级
    ("ขนาดกำลังเครื่องยนต์", "vehicles", False),   # 发动机排量
    ("รหัสเครื่องยนต์", "vehicles", False),       # 发动机号
    ("หมวดแคมเปญ", "vehicles", False),           # 活动分组
    ("แคมเปญรถ", "vehicles", False),             # 车辆活动
    ("สินค้า", "vehicles", False),               # 商品
    ("หน่วยสินค้า", "vehicles", False),           # 商品单位
    ("บริษัท", "company", False),                # 公司
    ("สาขา", "company", False),                  # 分店
    ("ทีมสาขา", "company", False),               # 分店团队
    ("คลัง", "company", False),                  # 仓库
    ("งาน", "company", False),                   # 工单
    ("ออเดอร์", "company", False),               # 订单
    ("พนักงาน", "company", False),               # 员工
    ("ธนาคารของบริษัท", "company", False),        # 公司银行
    ("บริษัทสินเชื่อ", "company", False),         # 金融公司
    ("จังหวัด", "geo", False),                   # 省
    ("อำเภอ", "geo", False),                     # 区
    ("ตำบล", "geo", False),                      # 镇
    ("รหัสไปรษณีย์", "geo", False),              # 邮编
)


def seeded(state):
    """基线是否已经建好。没建好就不能做增量判断。"""
    return bool((state or {}).get("seeded_at"))


# ── 互斥 ───────────────────────────────────────────────────────────────
def _lock_key(scope_key):
    return "dms-changes:" + scope_key


def _acquire(scope_key):
    """拿到牌子返回 token；别人在跑返回 None；Redis 不可用返回 ""（照做，不互斥）。"""
    token = frappe.generate_hash(length=16)
    try:
        if not frappe.cache().set(_lock_key(scope_key), token, nx=True, ex=LOCK_SECONDS):
            return None
    except Exception:
        logger.warning("[dms changes] 互斥不可用，本轮不加锁继续")
        return ""
    return token


def _release(scope_key, token):
    if not token:
        return
    try:
        cache = frappe.cache()
        if cache.get(_lock_key(scope_key)) in (token, token.encode()):
            cache.delete(_lock_key(scope_key))
    except Exception:
        logger.warning("[dms changes] 互斥释放失败（会自然过期）")


# ── 时点一：建基线 ─────────────────────────────────────────────────────
def seed(endpoint, actor=None):
    """把每个模块此刻的水位记下来。**本轮不补任何东西。**"""
    from pearnly_dms.adapter.operations import run_logged_in

    def capture(client, adapter):
        return _establish_baseline(endpoint, client=client, actor=actor or SERVICE_ACTOR)

    return run_logged_in(endpoint, capture, authoritative_read=True, background=True,
                         read_only_batch=True)


def _establish_baseline(endpoint, *, client, actor, base=None, expected_scope=None):
    base = (store.change_state(endpoint) if base is None else base) or {}
    modules = dict(base.get("modules") or {})
    limits, usage = EventLogLimits(), None
    complete = True
    for module, _dataset, _records in MODULES:
        try:
            total, usage = eventlog_reader.count(client, module, limits=limits, usage=usage)
            head, usage = eventlog_reader.page(client, module, page_number=1,
                                               limits=HEAD_LIMITS, usage=usage)
        except Exception:
            # 读不到就先别写：没有基线比"错的基线"安全得多。
            complete = False
            break
        modules[module] = {"count": total, "seq": head[0].seq if head else 0,
                           "dirty_since": 0, "checked_at": time.time()}
    now = time.time()
    incoming = {**base, "version": VERSION, "actor": base.get("actor") or actor,
                "modules": modules, "seed_attempt": now,
                "seeded_at": now if complete else float(base.get("seeded_at") or 0),
                "next_index": int(base.get("next_index") or 0) % len(MODULES)}
    store.merge_change_state(endpoint, incoming, base=base, expected_scope=expected_scope)
    logger.info("[dms changes] 基线%s（%s/%s 个模块）", "完成" if complete else "未完成",
                len(modules), len(MODULES))
    return {"ok": True, "seeded": complete, "modules": len(modules)}


# ── 时点二：操作顺手同步 ───────────────────────────────────────────────
def catch_up(endpoint, *, client):
    """**从不抛异常**：同步是顺手做的，坏了也不能让操作员那一笔变成失败。"""
    try:
        _catch_up(endpoint, client=client)
    except Exception:
        logger.warning("[dms changes] 顺手同步没做完（不影响本次操作）", exc_info=True)


def _catch_up(endpoint, *, client):
    try:
        expected_scope = store.scope(endpoint)
    except Exception:
        return                      # 没有可用凭据 → 谈不上新鲜度
    token = _acquire(expected_scope)
    if token is None:
        return                      # 有别人在同步，跳过
    try:
        state = store.change_state(endpoint) or {}
        if not seeded(state):
            if time.time() - float(state.get("seed_attempt") or 0) < SEED_RETRY_SECONDS:
                return
            _establish_baseline(endpoint, client=client, actor=SERVICE_ACTOR,
                                base=state, expected_scope=expected_scope)
            return
        _pull(endpoint, client=client, state=state, expected_scope=expected_scope)
    finally:
        _release(expected_scope, token)


def _pull(endpoint, *, client, state, expected_scope):
    started = time.monotonic()
    limits = EventLogLimits()
    usage = eventlog_reader.Usage(limits)
    modules = dict(state.get("modules") or {})
    order = list(MODULES)
    start = int(state.get("next_index") or 0) % len(order)
    order = order[start:] + order[:start]
    actor = state.get("actor") or SERVICE_ACTOR
    next_index = 0

    for index, (module, dataset, per_record) in enumerate(order):
        if time.monotonic() - started > PULL_SECONDS or usage.requests >= PULL_REQUESTS:
            next_index = (start + index) % len(MODULES)
            break
        entry = dict(modules.get(module) or {})
        try:
            total, usage = eventlog_reader.count(client, module, limits=limits, usage=usage)
        except Exception:
            # 读不到就整轮停手：**绝不把"读不到"当成"没变化"**。
            logger.warning("[dms changes] 使用记录读不到，本轮停止（模块 %s）", module)
            next_index = (start + index) % len(MODULES)
            break
        try:
            entry, usage = _advance(endpoint, client=client, module=module, dataset=dataset,
                                    per_record=per_record, entry=entry, total=total,
                                    limits=limits, usage=usage, actor=actor,
                                    expected_scope=expected_scope)
        except Exception:
            # 这一模块没处理成功：留着旧水位，下一轮再来；别的模块照常。
            logger.warning("[dms changes] 模块 %s 的增量处理失败，保留旧水位", module)
            continue
        modules[module] = entry

    incoming = {**state, "version": VERSION, "modules": modules,
                "next_index": max(0, next_index), "checked_at": time.time()}
    store.merge_change_state(endpoint, incoming, base=state, expected_scope=expected_scope)


def _advance(endpoint, *, client, module, dataset, per_record, entry, total,
             limits, usage, actor, expected_scope):
    """把一个模块推进到"此刻"。返回 `(新的水位条目, 用量)`。"""
    now = time.time()
    dirty = float(entry.get("dirty_since") or 0)
    if dirty:
        if _refreshed(endpoint, dataset, since=dirty):
            head, usage = eventlog_reader.page(client, module, page_number=1,
                                               limits=HEAD_LIMITS, usage=usage)
            return {"count": total, "seq": head[0].seq if head else int(entry.get("seq") or 0),
                    "dirty_since": 0, "checked_at": now}, usage
        if now - dirty > REFRESH_COOLDOWN_SECONDS and _request_refresh(endpoint, dataset, actor=actor):
            dirty = now
        return {"count": total, "seq": entry.get("seq") or 0,
                "dirty_since": dirty, "checked_at": now}, usage

    if entry.get("seq") is None:
        # 还没有基线（例如基线任务没跑成）：先记下此刻的水位，本轮不补东西。
        head, usage = eventlog_reader.page(client, module, page_number=1,
                                           limits=HEAD_LIMITS, usage=usage)
        return {"count": total, "seq": head[0].seq if head else 0,
                "dirty_since": 0, "checked_at": now}, usage

    cursor = int(entry.get("seq") or 0)
    previous = entry.get("count")
    if previous == total:
        return {**entry, "checked_at": now}, usage
    if previous is None:
        # 上一次的总数不明（例如半份基线）：这一段没法判断少了什么，按"未知"处理。
        return {"count": total, "seq": cursor,
                "dirty_since": _note_dirty(endpoint, dataset, entry, now, actor),
                "checked_at": now}, usage

    events, newest, truncated = _new_events(client, module, cursor=cursor, limits=limits,
                                            usage=usage)
    # 总数说应该有几条新事件，实际只翻到几条：翻不完或对不上账 —— 都是"未知"。
    short = len(events) < max(0, int(total) - int(previous))
    if truncated or short or not events:
        return {"count": total, "seq": cursor,
                "dirty_since": _note_dirty(endpoint, dataset, entry, now, actor),
                "checked_at": now}, usage
    if per_record and _apply_customers(client, events, endpoint=endpoint,
                                       expected_scope=expected_scope):
        return {"count": total, "seq": newest, "dirty_since": 0,
                "checked_at": now}, usage
    if _request_refresh(endpoint, dataset, actor=actor):
        # 整组重读已登记：水位推进到最新，dirty 标记等它落地才对账。
        return {"count": total, "seq": newest, "dirty_since": now,
                "checked_at": now}, usage
    return {**entry, "checked_at": now}, usage


def _note_dirty(endpoint, dataset, entry, now, actor):
    """需要整组重读：登记成功就记下时刻，登记不上就保持原样（下一轮再试）。"""
    since = float(entry.get("dirty_since") or 0)
    if since and now - since <= REFRESH_COOLDOWN_SECONDS:
        return since
    return now if _request_refresh(endpoint, dataset, actor=actor) else since


def _new_events(client, module, *, cursor, limits, usage):
    """游标之后的新事件。返回 `(事件, 见到的最新流水号, 是否还没追完)`。"""
    events, newest, pages = [], cursor, 0
    while pages < MAX_NEW_PAGES:
        pages += 1
        page, usage = eventlog_reader.page(client, module, page_number=pages,
                                           limits=limits, usage=usage)
        if not page:
            return events, newest, False
        newest = max(newest, max(event.seq for event in page))
        events.extend(event for event in page if event.seq > cursor)
        if min(event.seq for event in page) <= cursor:
            return events, newest, False
        if len(page) < limits.page_size:
            return events, newest, False
    # 页数用满还是没追到游标：说明积压太多。
    return events, newest, True


# ── 按记录补读（目前只有客户） ──────────────────────────────────────────
def _apply_customers(client, events, *, endpoint, expected_scope):
    """把流水点名的客户补读回来。返回 True＝这批变化已经落到缓存。

    删除：不逐条猜"删掉之后还剩什么"，但**必须先把自己这条 overlay 撤掉** ——
    overlay 会被下一次整组重读当成必须读到的目标，留着它会让客户缓存永久读不出来。
    撤掉之后仍然交整组重读（由它按原生列表决定这条客户到底还在不在）。
    """
    known = store.customer_index(endpoint)
    targets = []
    for event in events:
        if not event.names_record:
            return False            # 点不到记录 → 交给整组重读
        target = normalize_thai_id(event.target)
        if not target:
            return False
        if event.action == eventlog_reader.ACTION_DELETE:
            cid = known.get(target)
            if cid:
                store.forget_customer(endpoint, cid, expected_scope=expected_scope)
            continue
        if target in targets:
            continue
        targets.append(target)
    removed = any(event.action == eventlog_reader.ACTION_DELETE for event in events)
    for target in targets:
        cid = known.get(target) or _search_customer(client, target)
        if not cid:
            return False
        fields = read_customer_complete(client, cid, limits=RECORD_LIMITS)
        if normalize_thai_id(fields.get("people_id")) != target:
            return False            # 读回来的不是这个人 → 不写
        store.verified_customer(endpoint, cid, fields, expected_scope=expected_scope)
    return not removed


def _search_customer(client, target):
    """按身份证号反查客户号；找不到或有歧义都返回空（由 `snapshot_reader` 严格判定）。"""
    from pearnly_dms.client.snapshot_reader import search_customer_by_identity

    return search_customer_by_identity(client, target, limits=RECORD_LIMITS)


# ── 整组重读的登记 ─────────────────────────────────────────────────────
def _refreshed(endpoint, dataset, *, since):
    """这个数据集是不是在我们登记之后真的整组读过一遍。"""
    try:
        return float(manual.status(endpoint, dataset, reader="authority").get("completed_at") or 0) >= since
    except Exception:
        return False


def _request_refresh(endpoint, dataset, *, actor):
    """登记一次整组重读（走既有队列，不阻塞操作员）。登记不上返回 False。

    白天窗口内客户/地址这两个大组**不马上读**（一次可能一个多小时，会长时间占着账号），
    只算"已处理"：模块保持待重读，由凌晨全量完成后 `_refreshed` 自然对上账（ADR-014）。
    """
    from pearnly_dms.adapter import live_sync

    if live_sync.defer_heavy(dataset):
        logger.info("[dms changes] daytime: %s group read left for the nightly refresh", dataset)
        return True
    try:
        manual.request(endpoint, dataset, reader="authority", actor=actor or SERVICE_ACTOR)
        return True
    except Exception:
        logger.warning("[dms changes] 整组重读登记失败（%s）", dataset)
        return False
