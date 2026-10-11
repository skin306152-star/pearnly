# -*- coding: utf-8 -*-
"""操作动作顺手把 DMS **使用记录**里的增量补回缓存。

一个 DMS 被几十上百人共用：别人新增/改了客户、车型、地址、员工，我们这边拿的是
缓存，不会自己知道。全量重扫又慢又贵，手动刷新还要求人记得点。DMS 自己为每次
增删改都记了一行使用记录（`client/eventlog_reader`），所以"哪几条变了"可以直接问它，
**只补读被点名的那几条**。

## 唯一的两个时点

1. **绑定成功** → 建一次基线（`seed`）：记下每个模块此刻最新一行的流水号，
   本轮不补任何东西 —— 底座由同一次绑定的四组全量读取负责。
2. **操作员每次真实使用 DMS** → `catch_up`：拿最新流水号和上次比，变了才翻流水，
   把被点名的记录补读回来。没变化时每个模块只花 1 个请求。

除此之外没有定时任务、没有手动按钮、没有第二个账号。

## 判断规则（宁可不判断，也不猜）

* **读不到＝新鲜度未知**：任何一个模块的最新流水号读不到，本轮立刻停手，绝不把"读不到"
  当成"没变化"（对方的数据端点没有权限校验，随时可能被收紧）。
* **点名不到具体记录**（标识为空、或动作我们不认识）→ 不逐条猜，登记一次整组重读。
* **删除** → 不在这里猜"删掉了谁"，同样交给整组重读（按原生列表的真实成员收敛）。
* **积压太多**（一轮页数上限还没追上游标）→ 整组重读，不逐页硬啃。
* **最新流水号比水位还小**、或翻到底没碰到水位（记录被删、被保留窗口截断）→ 同样按"未知"处理，整组重读。
* 为什么看流水号不看总数：2026-10-11 对方不再回总数、分页还多给一行（见 `client/eventlog_reader`），
  而且对方加了"按日期删除使用记录"，总数本来就会被删记录打乱；流水号只增不减。

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
#: 客户代码认不出时（新客户对读档会话的隐身期、读取超时等），之后每轮再试多久才交给凌晨全量。
PENDING_SECONDS = 1800
#: 同时挂着重试的客户代码最多几个（每个每轮最多一次搜索 + 一次读表单）。
PENDING_MAX = 10
#: 服务自身的动作以谁的名义登记（Frappe 的 Administrator 具备全部角色）。
SERVICE_ACTOR = "Administrator"

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

def _state_store():
    """不淘汰的状态存储（``pearnly_core.execution.state``；用到时才导入，离线检查导入本模块不需要共用层）。"""
    from pearnly_core.execution import state as pearnly_state

    return pearnly_state.store()


def _state_release(key, owner):
    """原子释放自己持有的锁（``pearnly_core.execution.state.release``）。"""
    from pearnly_core.execution import state as pearnly_state

    return pearnly_state.release(key, owner)



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
        store = _state_store()
        if not store.set(store.make_key(_lock_key(scope_key)), token, nx=True, ex=LOCK_SECONDS):
            return None
    except Exception:
        logger.warning("[dms changes] 互斥不可用，本轮不加锁继续")
        return ""
    return token


def _release(scope_key, token):
    if not token:
        return
    try:
        # 不淘汰的状态存储、按站点加前缀；只删自己那把（原先先读再删，中间别人刚拿到的锁会被误删）
        _state_release(_state_store().make_key(_lock_key(scope_key)), token)
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
            seq, usage = eventlog_reader.head(client, module, limits=limits, usage=usage)
        except Exception as exc:
            # 读不到就先别写：没有基线比"错的基线"安全得多。
            logger.warning("[dms changes] 基线读不到（模块 %s）：%s", module, exc)
            complete = False
            break
        modules[module] = {"seq": seq, "dirty_since": 0, "checked_at": time.time()}
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
            head, usage = eventlog_reader.head(client, module, limits=limits, usage=usage)
        except Exception as exc:
            # 读不到就整轮停手：**绝不把"读不到"当成"没变化"**。原因写进日志（错误文字不带响应正文），
            # 2026-10-11 对方改版时这里只说"读不到"，查了一晚上才知道是格式变了。
            logger.warning("[dms changes] 使用记录读不到，本轮停止（模块 %s）：%s", module, exc)
            next_index = (start + index) % len(MODULES)
            break
        try:
            advance = _advance_records if per_record else _advance
            entry, usage = advance(endpoint, client=client, module=module, dataset=dataset,
                                   entry=entry, head=head, limits=limits, usage=usage,
                                   actor=actor, expected_scope=expected_scope)
        except Exception:
            # 这一模块没处理成功：留着旧水位，下一轮再来；别的模块照常。
            logger.warning("[dms changes] 模块 %s 的增量处理失败，保留旧水位", module)
            continue
        modules[module] = entry

    incoming = {**state, "version": VERSION, "modules": modules,
                "next_index": max(0, next_index), "checked_at": time.time()}
    store.merge_change_state(endpoint, incoming, base=state, expected_scope=expected_scope)


def _advance(endpoint, *, client, module, dataset, entry, head,
             limits, usage, actor, expected_scope):
    """把一个整组模块（车型、公司、地址……）推进到"此刻"。返回 `(新的水位条目, 用量)`。

    `head` 是这个模块此刻最新一行的流水号。这些组小、整组重读几分钟就完，所以
    "有变化就登记整组重读、落地前不再翻流水"。客户组按记录补读，走 `_advance_records`。"""
    now = time.time()
    dirty = float(entry.get("dirty_since") or 0)
    if dirty:
        if _refreshed(endpoint, dataset, since=dirty):
            return {"seq": head or int(entry.get("seq") or 0),
                    "dirty_since": 0, "checked_at": now}, usage
        if now - dirty > REFRESH_COOLDOWN_SECONDS and _request_refresh(endpoint, dataset, actor=actor):
            dirty = now
        return {"seq": entry.get("seq") or 0,
                "dirty_since": dirty, "checked_at": now}, usage

    if entry.get("seq") is None:
        # 还没有基线（例如基线任务没跑成）：先记下此刻的水位，本轮不补东西。
        return {"seq": head, "dirty_since": 0, "checked_at": now}, usage

    cursor = int(entry.get("seq") or 0)
    if head == cursor:
        return {**entry, "checked_at": now}, usage
    if head < cursor:
        # 最新一行比水位还旧（记录被删）：这一段少了什么没法判断，按"未知"处理。
        return {"seq": head,
                "dirty_since": _note_dirty(endpoint, dataset, entry, now, actor),
                "checked_at": now}, usage

    events, newest, complete = _new_events(client, module, cursor=cursor, limits=limits,
                                           usage=usage)
    if not complete or not events:
        return {"seq": cursor,
                "dirty_since": _note_dirty(endpoint, dataset, entry, now, actor),
                "checked_at": now}, usage
    if _request_refresh(endpoint, dataset, actor=actor):
        # 整组重读已登记：水位推进到最新，dirty 标记等它落地才对账。
        return {"seq": newest, "dirty_since": now, "checked_at": now}, usage
    return {**entry, "checked_at": now}, usage


def _advance_records(endpoint, *, client, module, dataset, entry, head,
                     limits, usage, actor, expected_scope):
    """客户组：**每条改动各自补读，一条认不出不连累别的，也不冻结之后的改动**（2026-10-06）。

    以前一条认不出就把整组标成待重读，而待重读期间不再翻流水——白天出一次意外，当天后面
    所有客户改动都要等凌晨全量。现在：
    * 能认出的客户当场补进缓存；
    * 认不出的客户代码挂进 ``pending``，之后每轮再试，满 ``PENDING_SECONDS`` 仍不行才交凌晨全量；
    * 点不出记录（标识为空）、删除、积压翻不完、对不上账 → 照旧登记整组重读（白天留给凌晨），
      但水位照常前进、能认出的照常补；
    * 待重读期间新来的改动照常逐条处理，整组重读落地后清掉待重读标记和 pending；
    * 只有"该登记整组重读却登记不上"时才不推进水位，下一轮从原处重来，保证不悄悄丢。
    """
    now = time.time()
    dirty = float(entry.get("dirty_since") or 0)
    pending = dict(entry.get("pending") or {})
    if dirty and _refreshed(endpoint, dataset, since=dirty):
        dirty, pending = 0.0, {}   # 整组重读已经覆盖了这之前的一切

    if entry.get("seq") is None:
        return {"seq": head, "dirty_since": dirty,
                "pending": pending, "checked_at": now}, usage

    cursor = int(entry.get("seq") or 0)
    events, newest, gap = [], cursor, False
    if head < cursor:
        newest, gap = head, True   # 最新一行比水位还旧（记录被删）：按"未知"处理
    elif head > cursor:
        events, newest, complete = _new_events(client, module, cursor=cursor, limits=limits,
                                               usage=usage)
        gap = not complete or not events
    retry = {code: since for code, since in pending.items() if now - float(since) <= PENDING_SECONDS}
    expired = len(retry) < len(pending)
    if not events and not retry and not gap and not expired:
        # 没有新改动、也没有待重试的：只记一下检查时刻（不加载整份客户缓存）。
        return {"seq": cursor, "dirty_since": dirty, "pending": {},
                "checked_at": now}, usage
    unresolved, needs_group = _apply_customers(client, events, endpoint=endpoint,
                                               expected_scope=expected_scope, retry=list(retry))
    pending = {code: retry.get(code, now) for code in unresolved}
    if len(pending) > PENDING_MAX:
        pending = dict(sorted(pending.items(), key=lambda item: item[1])[-PENDING_MAX:])
        expired = True
    if gap or needs_group or expired:
        marked = _note_dirty(endpoint, dataset, {"dirty_since": dirty}, now, actor)
        if not marked:
            # 整组重读登记不上：不推进水位，下一轮从原处重来（补读过的客户再读一次无害）。
            return {**entry, "dirty_since": dirty, "pending": dict(entry.get("pending") or {}),
                    "checked_at": now}, usage
        dirty = marked
    return {"seq": newest, "dirty_since": dirty, "pending": pending,
            "checked_at": now}, usage


def _note_dirty(endpoint, dataset, entry, now, actor):
    """需要整组重读：登记成功就记下时刻，登记不上就保持原样（下一轮再试）。"""
    since = float(entry.get("dirty_since") or 0)
    if since and now - since <= REFRESH_COOLDOWN_SECONDS:
        return since
    return now if _request_refresh(endpoint, dataset, actor=actor) else since


def _new_events(client, module, *, cursor, limits, usage):
    """游标之后的新事件（按流水号从新到旧、去重）。返回 `(事件, 见到的最新流水号, 是否完整)`。

    "完整"＝**第 1 页**里就碰到了流水号不大于游标的行。对方按日期时间排、同一秒先后不定，分页又是按偏移
    （2026-10-11）：要翻到第 2 页时，压在页边的同一秒几行可能有一行两页都没出现，所以只要翻过第 1 页就算
    不完整（照常补翻到的，再交整组重读）。翻到底都没碰到游标（游标为 0 的除外）说明游标那段记录已被删，
    页数用满还没追到游标是积压太多 —— 也都不完整。平时每分钟的新改动远少于一页，碰不到这些情况。
    """
    events, seen, newest = [], set(), cursor
    for number in range(1, MAX_NEW_PAGES + 1):
        rows, usage = eventlog_reader.page(client, module, page_number=number,
                                           limits=limits, usage=usage)
        if not rows:
            return events, newest, cursor == 0 and number == 1
        newest = max(newest, rows[0].seq)
        for event in rows:
            if event.seq > cursor and event.seq not in seen:
                seen.add(event.seq)
                events.append(event)
        if rows[-1].seq <= cursor:
            return _newest_first(events), newest, number == 1
        if len(rows) < limits.page_size:
            return _newest_first(events), newest, cursor == 0 and number == 1
    return _newest_first(events), newest, False


def _newest_first(events):
    return sorted(events, key=lambda event: event.seq, reverse=True)


# ── 按记录补读（目前只有客户） ──────────────────────────────────────────
def _apply_customers(client, events, *, endpoint, expected_scope, retry=()):
    """把流水点名的客户（以及上几轮没认出、还在重试期内的 ``retry``）逐个补读回缓存。

    返回 ``(认不出的客户代码, 是否需要整组重读)``。每个客户各自成败，一个失败不影响其他。

    流水里点名客户用的是**客户代码**（รหัสลูกค้า），不是身份证号；本产品建的客户代码恰好
    等于证号，车行自编代码的不是（2026-10-06 实测，见 `search_customer_by_code`）。
    标识为空的事件点不到具体记录 → 需要整组重读。

    删除：不逐条猜"删掉之后还剩什么"，但**必须先把自己这条 overlay 撤掉** ——
    overlay 会被下一次整组重读当成必须读到的目标，留着它会让客户缓存永久读不出来。
    撤掉之后仍然交整组重读（由它按原生列表决定这条客户到底还在不在）。

    认不出（读档账号的列表里找不到、读取失败、读回来的代码对不上）→ 留日志、交回调用方挂起重试，
    不猜客户号。
    """
    known = store.customer_codes(endpoint)
    targets, needs_group = [], False
    for event in events:
        target = str(event.target or "").strip()
        if not event.names_record or not target:
            needs_group = True       # 点不到记录 → 交给整组重读
            continue
        if event.action == eventlog_reader.ACTION_DELETE:
            cid = known.get(target)
            if cid:
                store.forget_customer(endpoint, cid, expected_scope=expected_scope)
            needs_group = True
            if target in targets:
                targets.remove(target)
            continue
        if target not in targets:
            targets.append(target)
    for code in retry:
        if code not in targets:
            targets.append(code)
    unresolved = []
    for target in targets:
        try:
            cid = known.get(target) or _search_customer(client, target)
            fields = read_customer_complete(client, cid, limits=RECORD_LIMITS) if cid else None
            if not fields or str(fields.get("cuscode") or "").strip() != target:
                raise LookupError("not resolved")
            store.verified_customer(endpoint, cid, fields, expected_scope=expected_scope)
        except Exception:
            logger.warning("[dms changes] 客户代码 %s 这一轮补不上（读档账号看不到、读取失败或代码已改），"
                           "之后再试，过期交凌晨全量", target)
            unresolved.append(target)
    return unresolved, needs_group


def _search_customer(client, target):
    """按客户代码反查客户号；找不到或有歧义都返回空（由 `snapshot_reader` 严格判定）。"""
    from pearnly_dms.client.snapshot_reader import search_customer_by_code

    return search_customer_by_code(client, target, limits=RECORD_LIMITS)


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
