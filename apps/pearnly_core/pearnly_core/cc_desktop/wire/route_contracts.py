"""写账分派的纯规则（ADR-021 第 50–55 条，用户 10-16「谁的活谁的电脑做」）：池里哪台电脑现在能领哪一张 Express 单。
不导入框架、不做 IO。

准备时不选电脑，电脑来领任务（拉模式，每 3 秒一次）时才决定由谁写：

- 归属（:func:`owns`）：定时任务的单冻结了读文件夹的那台（``route_device``），只有它写；其余的单只由录入人自己登录过
  CC 的电脑写（:func:`serves`；一个登录记录都没有的电脑过渡期照旧谁的都写）。同事的电脑等多久都不领，
  没有档位、没有「排了多久谁都能领」；
- 资格：这台电脑够得到这份账套（可达行状态正常、可写），它的 CC 桌面版支持这张单的载荷版本；
- 按账套黏住：一台电脑写过这份账套、主档索引还没重建（它领任务时报的 ``index_pending`` 里有这份账套的文件号；旧版不报，
  按「它最近 :data:`STICKY_SECONDS` 内写过」近似）且它在线 → 这份账套只交给它；黏住的那台不归这张单时只挡、不接；
- 同一份账套按排队先后：每台电脑只在它能写的单里取各账套的排头（`queue_heads`），A 的电脑关着时 A 的单不挡 B 的单；
- 代录到 Express 以外的目标（ADR-026，第一个是 MR.ERP 网页）：不看账套可达、不黏住，只交给发起人选定的那台（``route_device``），
  它的 CC 桌面版要在心跳 ``operator_targets`` 里报过这个目标、并支持这张单的载荷版本；旧版不报 = 只有 Express。Express 的单不看
  这一项（与以前一样）。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Iterable, Mapping, NamedTuple


# 旧版 CC 桌面版不报 ``index_pending``：按「这台最近这么久内写过这份账套」近似它的主档索引还没重建
STICKY_SECONDS = 120
# 一次领取最多看这么多份账套的排头
MAX_HEADS = 50

# 按什么分派（问责记录）：录入人自己的电脑 / 定时任务读文件夹的那台 / 黏住的那台
OWN, READER, STICKY = "own", "reader", "sticky"
# 替写：不是提交人自己的电脑、提交人自己没有在线的电脑时替他写（:func:`decide` 的 ``stand_in``，用户 11-17）
STAND_IN = "stand_in"
ROUTES = (OWN, READER, STICKY, STAND_IN)

# 不能领的原因（只给日志与测试；不进回复）
UNREACHABLE, NOT_MINE, STUCK, VERSION, NEEDS_UPDATE = "unreachable", "not_mine", "sticky", "version", "needs_update"
# 这台的 CC 桌面版不会代录到这个目标（没在心跳 ``operator_targets`` 里报过）：不派，单子等它（ADR-026）
NO_TARGET = "no_target"

# 代录的执行目标（ADR-026，与共用层 ``express_operator.contracts.TARGET_EXPRESS`` 同值）：Express 照以前的规则派
EXPRESS = "express"
# 心跳 ``operator_targets`` 最多记几个、每个多长（只收小写字母数字，认不出的不记）
MAX_TARGETS, _TARGET = 8, re.compile(r"^[a-z][a-z0-9_]{0,19}$")


def serves(users: Iterable[str] | None, actor: str) -> bool:
    """这台电脑写不写 ``actor`` 的活（ADR-021 第 50 条，用户 10-16「谁的活谁的电脑做」）：``users`` 是在这台电脑上登录过
    CC 的人，``actor`` 在里面才算他的电脑。一个登录记录都没有的电脑（补丁建的旧电脑、1.6 及更早、只认过电脑还没打开过
    对话页的）在过渡期照旧谁的活都写；一旦有人在上面登录 CC，就只归登录过的人。"""
    users = {user for user in users or () if user}
    return not users or bool(actor) and actor in users


class Peer(NamedTuple):
    """池里一台电脑此刻的情况（companion 读好交进来）。"""
    # 在这台电脑上登录过 CC 的人（空 = 没有登录记录，过渡期谁的单都写）
    users: frozenset = frozenset()
    online: bool = False
    # 它支持的最高载荷版本；None = 没报过（旧版），与今天一样不拿它拦
    payload_version: int | None = None
    # 它主档索引还没重建的账套文件号；None = 旧版不报这一项
    index_pending: frozenset | None = None
    # 它能代录到哪些目标（心跳 ``operator_targets``）；None = 旧版不报 = 只有 Express
    targets: frozenset | None = None


class Job(NamedTuple):
    """一份账套的排头。"""
    name: str
    account_key: str
    # 录入人（定时任务是规则负责人）
    actor: str
    payload_version: int = 1
    # 定时任务：读文件夹的那台电脑（只有它写）；空 = 录入人自己的电脑写
    route_device: str = ""
    # 无人值守的自动推（冻结了 ``automation``，数字会计员第 3 期第 5 条）：电脑前不会有人等它
    automated: bool = False
    # 代录的执行目标（ADR-026）；Express（直写也是）照以前的规则派，别的目标只交给 ``route_device``
    target: str = EXPRESS


class Account(NamedTuple):
    """这份账套的登记（黏住用）。"""
    uid: str = ""
    last_writer: str = ""
    last_written_at: datetime | None = None


class Decision(NamedTuple):
    take: bool
    # own / reader / sticky（问责记录）
    route: str = ""
    reason: str = ""


# 替写（用户 11-17 定稿「公司里只要有一台开着 CC、够得到那本账套、登录人有权限的电脑，任何成员提交的单就能写」，两产品同一条）：
# 提交人自己的电脑在线 → 用自己的；否则公司里在线、够得到、登录人有权限的那台替他写；都不在线就排队，谁先上线谁写。
# 在电脑来领的那一刻判（:func:`decide` 的 ``stand_in``），不在排队时写死；单上的提交人不变。


# 旧件闸（主会话 11-17）：替写只对切换时刻之后排的件生效；之前排着的件照旧只等提交人自己的电脑，不让别人的电脑把很久以前排着的件
# 悄悄写进去（等用户看了统计数字再定写还是作废）。切换时刻是站点配置（两产品同名，`scripts/impact/product-map/config.toml`）：
# 存量站点由随版本的迁移补丁在迁移那一刻写上（`pearnly_core.cc_desktop.writer_rule.stamp_since`，幂等）；没配 = 新规则对全部件
# 生效（新开户站点天然如此，不靠人配，用户 10-07「功能对所有租户直接生效」）；用户看过统计定了「旧件写」之后删掉这项即放开。
WRITER_RULE_SINCE = "pearnly_writer_rule_since"


def _utc(value) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def db_time(since) -> str:
    """切换时刻换成库里排队时刻的写法（UTC、``YYYY-MM-DD HH:MM:SS``，给排头查询按它筛）：没配回最早的时刻（全部件都能替）；
    配了却读不出回最晚的时刻（一件都不替，同 :func:`stand_in_open`）。纯函数。"""
    if since in (None, ""):
        return "1970-01-01 00:00:00"
    start = _utc(since)
    return start.strftime("%Y-%m-%d %H:%M:%S") if start else "9999-12-31 23:59:59"


def stand_in_open(queued_at, since) -> bool:
    """这件（排队时刻 ``queued_at``，库里存 UTC）能不能由别人的电脑替写：没配切换时刻 ``since``（站点配置
    :data:`WRITER_RULE_SINCE`）→ 能；配了（ISO 时刻，不带时区按 UTC）→ 之后排的才能；配了却读不出、或这件没有排队时刻 → 不能
    （宁可等提交人自己的电脑）。纯函数。"""
    if since in (None, ""):
        return True
    at, start = _utc(queued_at), _utc(since)
    return bool(at and start and at >= start)


# 排队原因「状态 → 显示」（两产品同一张，主会话 11-17）：按提交人看，替这件活干的电脑（他自己的 + 能替他写的）此刻怎样。
# 各产品把这几个状态换成自己的现有短句（Cowork `intake.agent.state.*` / `connect.state.connected`，ERP `express.cc_desktop_*`）。
WRITER_CONNECTED, WRITER_OFFLINE, WRITER_NO_WRITER, WRITER_MISSING, WRITER_NEEDS_UPDATE = (
    "connected", "offline", "no_writer", "missing", "needs_update")


def writer_status(*, online: bool, writers_known: bool, computers: bool, needs_update: bool = False) -> str:
    """``needs_update``（能写的电脑没有一台支持排头的载荷版本）→ 需更新；有一台能写的在线 → 已连接；有能写的、都不在线 → 电脑离线
    （仍可选，排队，谁先上线谁写）；有电脑却没有一台能写这本 → 没有能写的电脑；一台都没有 → 未连接电脑。纯函数。"""
    if needs_update:
        return WRITER_NEEDS_UPDATE
    if writers_known:
        return WRITER_CONNECTED if online else WRITER_OFFLINE
    return WRITER_NO_WRITER if computers else WRITER_MISSING


def pick_user(users: Iterable[str] | None, eligible, inviter: str = "") -> str:
    """这台电脑替别人写时记谁：在这台上登录过 CC、``eligible(用户)`` 为真（调用方核：此刻仍能用这条连接 / 这家公司、签发有效）
    的人里，添加提交人的那位（``inviter``）排前，其余按用户名；一个都没有回空串（这台不替别人写）。纯函数。"""
    found = sorted({user for user in users or () if user})
    for user in ([inviter] if inviter in found else []) + found:
        if eligible(user):
            return user
    return ""


def owner_online_elsewhere(device: str, actor: str, reachers: Iterable[str], peers: Mapping[str, Peer]) -> bool:
    """提交人自己另有一台够得到这份账套、此刻在线的电脑（不算 ``device``）：那就由它写，这台不替。"""
    return any(item != device and owns(item, peers.get(item), actor) and (peers.get(item) or Peer()).online
               for item in reachers)


def owns(device: str, peer: Peer | None, actor: str, route_device: str = "") -> bool:
    """这张单归不归 ``device`` 写：有 ``route_device`` 只归那台；否则 ``actor`` 在这台电脑上登录过 CC 才归它。"""
    if route_device:
        return device == route_device
    return serves((peer or Peer()).users, actor)


def stored_targets(value) -> str:
    """心跳报的 ``operator_targets`` → 存进电脑行的写法（排好、逗号分隔）；没报 / 不是列表 / 一个都认不出回空串（不改上次存的）。"""
    if not isinstance(value, (list, tuple)):
        return ""
    found = sorted({item for item in value if isinstance(item, str) and _TARGET.match(item)})
    return ",".join(found[:MAX_TARGETS])


# 心跳 ``mrerp_logins``（ADR-026 第 7 条）：这台电脑上已授权了哪些 MR.ERP 连接，只报连接号、不报账号。最多记几条、每条多长
MAX_LOGINS, _LOGIN = 50, re.compile(r"^[A-Za-z0-9._:-]{1,140}$")
_LOGINS_LENGTH = 4000


def stored_logins(value) -> str | None:
    """心跳报的 ``mrerp_logins`` → 存进电脑行的写法（排好、逗号分隔）。没报（旧版）/ 不是列表回 None（不改上次存的）；
    报了空列表回空串（这台电脑上一条都没有了，照存）。认不出的连接号不记。"""
    if not isinstance(value, (list, tuple)):
        return None
    found, length = [], 0
    for item in sorted({item for item in value if isinstance(item, str) and _LOGIN.match(item)})[:MAX_LOGINS]:
        length += len(item) + 1
        if length > _LOGINS_LENGTH:
            break
        found.append(item)
    return ",".join(found)


def logins_of(stored) -> frozenset:
    """电脑行上存的写法 → 这台电脑已授权的 MR.ERP 连接号。"""
    return frozenset(item for item in str(stored or "").split(",") if _LOGIN.match(item))


# 心跳 ``mrerp_users``（ADR-026 第 8 条，第 3 步）：这台电脑本机授权的「连接号 → MR.ERP 用户名」。用户名不是秘密（页面本来就显示
# 「已授权：用户名 · 电脑 · 时间」），不报密码。站点按「网址 + 用户名」给代录批次租账号时用它；没报的连接用站点连接上存的用户名。
_USER = re.compile(r"^[^\x00-\x1f\x7f]{1,140}$")
_USERS_LENGTH = 8000


def stored_users(value) -> str | None:
    """心跳报的 ``mrerp_users`` → 存进电脑行的写法（排好键的 JSON）。没报（旧版）/ 不是对象回 None（不改上次存的）；报了空对象回
    ``"{}"``（照存）。连接号认不出、用户名为空或带控制字符的不记；最多 ``MAX_LOGINS`` 条。"""
    import json

    if not isinstance(value, Mapping):
        return None
    found = {}
    for connection in sorted(item for item in value if isinstance(item, str) and _LOGIN.match(item))[:MAX_LOGINS]:
        user = value.get(connection)
        if isinstance(user, str) and _USER.match(user.strip()):
            found[connection] = user.strip()
    text = json.dumps(found, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    while len(text) > _USERS_LENGTH and found:
        found.pop(sorted(found)[-1])
        text = json.dumps(found, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return text


def users_of(stored) -> dict:
    """电脑行上存的写法 → ``{连接号: 用户名}``；没存 / 认不出回空。"""
    import json

    try:
        found = json.loads(stored) if isinstance(stored, str) and stored else {}
    except ValueError:
        return {}
    if not isinstance(found, dict):
        return {}
    return {key: value for key, value in found.items()
            if isinstance(key, str) and _LOGIN.match(key) and isinstance(value, str) and _USER.match(value)}


def with_login(stored, connection) -> str:
    """电脑行上已授权的清单再加一条（站点收下授权时记，不等下一次心跳）。"""
    return stored_logins(sorted(logins_of(stored) | {connection})) or ""


def targets_of(stored) -> frozenset | None:
    """电脑行上存的写法 → :class:`Peer` 的 ``targets``；没存过（旧版不报）回 None。"""
    found = frozenset(item for item in str(stored or "").split(",") if _TARGET.match(item))
    return found or None


def operates(peer: Peer | None, target: str) -> bool:
    """这台电脑能不能代录到 ``target``：Express 不看这一项（与以前一样）；别的目标要它报过，旧版不报 = 只有 Express。"""
    if not target or target == EXPRESS:
        return True
    return target in ((peer or Peer()).targets or frozenset())


def supports(peer: Peer | None, version: int) -> bool:
    """这台电脑能写这个载荷版本：没报过最高版本的旧版不拦（与今天预检同一口径）。"""
    reported = (peer or Peer()).payload_version
    return reported is None or int(reported) >= int(version or 1)


def needs_update(version: int, reachers: Iterable[str], peers: Mapping[str, Peer]) -> bool:
    """``reachers``（归这张单、又够得到这份账套的电脑）没有一台支持这个载荷版本：等不来，界面显示「需更新」。一台都没有
    不算（那是「没有能写的电脑」）。"""
    reachers = list(reachers)
    return bool(reachers) and not any(supports(peers.get(device), version) for device in reachers)


def sticky_holder(account: Account, reachers: Iterable[str], peers: Mapping[str, Peer], now: datetime,
                  current: str = "") -> str:
    """这份账套此刻黏在哪台电脑上（没有回空串）。``current``：正在领取的这台，它本身就在线。

    报过 ``index_pending`` 的电脑按文件号判；登记没有文件号、或那台不报这一项（旧版）时按「它最近写过」近似。"""
    reachers = sorted(set(reachers))

    def online(device):
        return device == current or (peers.get(device) or Peer()).online

    if account.uid:
        for device in reachers:
            pending = (peers.get(device) or Peer()).index_pending
            if pending is not None and account.uid in pending and online(device):
                return device
    writer = account.last_writer
    if writer and writer in reachers and online(writer) and account.last_written_at is not None:
        reported = (peers.get(writer) or Peer()).index_pending
        if (reported is None or not account.uid) and now - account.last_written_at < timedelta(seconds=STICKY_SECONDS):
            return writer
    return ""


def decide(device: str, job: Job, reachers: Iterable[str], peers: Mapping[str, Peer], account: Account,
           now: datetime, *, stand_in: str = "") -> Decision:
    """``device`` 现在能不能领这份账套的排头 ``job``。``reachers``：够得到它（状态正常、可写）的电脑；``peers``：
    ``{设备: Peer}``，来领的这台本身就在线。``stand_in``：这台上能替别人写的登录人（:func:`pick_user`，空 = 这台不替）——
    不是提交人自己的电脑时，提交人自己没有另一台在线、够得到这本的电脑才替他写（route ``stand_in``，定时任务不替）。"""
    if job.target and job.target != EXPRESS:
        return _decide_chosen(device, job, peers.get(device))
    reachers = sorted(set(reachers))
    if device not in reachers:
        return Decision(False, reason=UNREACHABLE)
    peers = {**peers, device: (peers.get(device) or Peer())._replace(online=True)}
    standing = False
    if not owns(device, peers[device], job.actor, job.route_device):
        if not stand_in or job.route_device or owner_online_elsewhere(device, job.actor, reachers, peers):
            return Decision(False, reason=NOT_MINE)
        standing = True
    holder = sticky_holder(account, reachers, peers, now, current=device)
    if holder and holder != device:
        return Decision(False, reason=STUCK)
    if not supports(peers[device], job.payload_version):
        mine = [item for item in reachers if owns(item, peers.get(item), job.actor, job.route_device)]
        return Decision(False, reason=NEEDS_UPDATE if needs_update(job.payload_version, mine, peers) else VERSION)
    if holder == device:
        return Decision(True, STAND_IN if standing else STICKY)
    return Decision(True, STAND_IN if standing else (READER if job.route_device else OWN))


def _decide_chosen(device: str, job: Job, peer: Peer | None) -> Decision:
    """代录到 Express 以外的目标（ADR-026）：只有发起人选定的那台（``route_device``）能领，不看账套可达、不黏住（网页账本是多用户
    服务器程序，没有「哪台够得到账套」）。它不会这个目标 → 不领、单子等它（不转人工：它更新 CC 桌面版后就会）；会这个目标、载荷版本
    不支持 → 「需更新」（只有它能写，同 Express 没有一台支持时的口径）。"""
    if not job.route_device or device != job.route_device:
        return Decision(False, reason=NOT_MINE)
    if not operates(peer, job.target):
        return Decision(False, reason=NO_TARGET)
    if not supports(peer, job.payload_version):
        return Decision(False, reason=NEEDS_UPDATE)
    return Decision(True, READER)


def heads(rows: Iterable[Mapping], limit: int = MAX_HEADS) -> list:
    """每份账套的排头：``rows`` 已按（建立时间，名字）排好的排队单，每个 ``account_key`` 取第一条，最多 ``limit`` 份，
    按排头的先后排。不能只取前 50 条：前 50 条都是同一份账套、排头又不归这台时，它永远看不到后面自己的账套。"""
    seen, result = set(), []
    for row in rows:
        key = row.get("account_key") if isinstance(row, Mapping) else getattr(row, "account_key", None)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(row)
        if len(result) >= limit:
            break
    return result


def attended(job: Job, peer: Peer | None) -> bool:
    """这张单写的时候电脑前可能有录入人本人（碰到账套被占用时弹询问框）：不是定时任务、不是自动推，而且录入人在这台电脑上
    登录过 CC。定时任务、自动推（第 3 期第 5 条）、没有登录记录的电脑（过渡期替谁都写）都不弹框，按「等账套」回报、空出来
    自动再写（同第 55 条）。"""
    return (not job.route_device and not job.automated and bool(job.actor)
            and job.actor in (peer or Peer()).users)


__all__ = ["Account", "Decision", "EXPRESS", "Job", "MAX_HEADS", "MAX_TARGETS", "NEEDS_UPDATE", "NOT_MINE", "NO_TARGET", "OWN",
           "Peer", "READER", "ROUTES", "STAND_IN", "STICKY", "STICKY_SECONDS", "STUCK", "UNREACHABLE", "VERSION", "WRITER_CONNECTED", "WRITER_MISSING",
           "WRITER_NEEDS_UPDATE", "WRITER_NO_WRITER", "WRITER_OFFLINE", "WRITER_RULE_SINCE", "attended", "db_time", "decide",
           "heads", "needs_update", "operates", "owner_online_elsewhere", "owns", "pick_user", "serves", "stand_in_open",
           "stored_targets", "sticky_holder", "supports", "targets_of", "writer_status"]
