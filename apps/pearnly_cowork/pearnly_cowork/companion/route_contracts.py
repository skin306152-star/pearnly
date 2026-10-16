"""写账分派的纯规则（ADR-021 第 50–55 条，用户 10-16「谁的活谁的电脑做」）：池里哪台电脑现在能领哪一张 Express 单。
不导入框架、不做 IO。

准备时不选电脑，电脑来领任务（拉模式，每 3 秒一次）时才决定由谁写：

- 归属（:func:`owns`）：定时任务的单冻结了读文件夹的那台（``route_device``），只有它写；其余的单只由录入人自己登录过
  CC 的电脑写（`connections.member.serves`；一个登录记录都没有的电脑过渡期照旧谁的都写）。同事的电脑等多久都不领，
  没有档位、没有「排了多久谁都能领」；
- 资格：这台电脑够得到这份账套（可达行状态正常、可写），它的 CC 桌面版支持这张单的载荷版本；
- 按账套黏住：一台电脑写过这份账套、主档索引还没重建（它领任务时报的 ``index_pending`` 里有这份账套的文件号；旧版不报，
  按「它最近 :data:`STICKY_SECONDS` 内写过」近似）且它在线 → 这份账套只交给它；黏住的那台不归这张单时只挡、不接；
- 同一份账套按排队先后：每台电脑只在它能写的单里取各账套的排头（`queue_heads`），A 的电脑关着时 A 的单不挡 B 的单。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Mapping, NamedTuple

from pearnly_cowork.connections.member import serves

# 旧版 CC 桌面版不报 ``index_pending``：按「这台最近这么久内写过这份账套」近似它的主档索引还没重建
STICKY_SECONDS = 120
# 一次领取最多看这么多份账套的排头
MAX_HEADS = 50

# 按什么分派（问责记录）：录入人自己的电脑 / 定时任务读文件夹的那台 / 黏住的那台
OWN, READER, STICKY = "own", "reader", "sticky"
ROUTES = (OWN, READER, STICKY)

# 不能领的原因（只给日志与测试；不进回复）
UNREACHABLE, NOT_MINE, STUCK, VERSION, NEEDS_UPDATE = "unreachable", "not_mine", "sticky", "version", "needs_update"


class Peer(NamedTuple):
    """池里一台电脑此刻的情况（companion 读好交进来）。"""
    # 在这台电脑上登录过 CC 的人（空 = 没有登录记录，过渡期谁的单都写）
    users: frozenset = frozenset()
    online: bool = False
    # 它支持的最高载荷版本；None = 没报过（旧版），与今天一样不拿它拦
    payload_version: int | None = None
    # 它主档索引还没重建的账套文件号；None = 旧版不报这一项
    index_pending: frozenset | None = None


class Job(NamedTuple):
    """一份账套的排头。"""
    name: str
    account_key: str
    # 录入人（定时任务是规则负责人）
    actor: str
    payload_version: int = 1
    # 定时任务：读文件夹的那台电脑（只有它写）；空 = 录入人自己的电脑写
    route_device: str = ""


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


def owns(device: str, peer: Peer | None, actor: str, route_device: str = "") -> bool:
    """这张单归不归 ``device`` 写：有 ``route_device`` 只归那台；否则 ``actor`` 在这台电脑上登录过 CC 才归它。"""
    if route_device:
        return device == route_device
    return serves((peer or Peer()).users, actor)


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
           now: datetime) -> Decision:
    """``device`` 现在能不能领这份账套的排头 ``job``。``reachers``：够得到它（状态正常、可写）的电脑；``peers``：
    ``{设备: Peer}``，来领的这台本身就在线。"""
    reachers = sorted(set(reachers))
    if device not in reachers:
        return Decision(False, reason=UNREACHABLE)
    peers = {**peers, device: (peers.get(device) or Peer())._replace(online=True)}
    if not owns(device, peers[device], job.actor, job.route_device):
        return Decision(False, reason=NOT_MINE)
    holder = sticky_holder(account, reachers, peers, now, current=device)
    if holder and holder != device:
        return Decision(False, reason=STUCK)
    if not supports(peers[device], job.payload_version):
        mine = [item for item in reachers if owns(item, peers.get(item), job.actor, job.route_device)]
        return Decision(False, reason=NEEDS_UPDATE if needs_update(job.payload_version, mine, peers) else VERSION)
    if holder == device:
        return Decision(True, STICKY)
    return Decision(True, READER if job.route_device else OWN)


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
    """这张单写的时候电脑前可能有录入人本人（碰到账套被占用时弹询问框）：不是定时任务，而且录入人在这台电脑上登录过 CC。
    定时任务、没有登录记录的电脑（过渡期替谁都写）都不弹框，按「等账套」回报、空出来自动再写。"""
    return not job.route_device and bool(job.actor) and job.actor in (peer or Peer()).users


__all__ = ["Account", "Decision", "Job", "MAX_HEADS", "NEEDS_UPDATE", "NOT_MINE", "OWN", "Peer", "READER", "ROUTES",
           "STICKY", "STICKY_SECONDS", "STUCK", "UNREACHABLE", "VERSION", "attended", "decide", "heads", "needs_update",
           "owns", "sticky_holder", "supports"]
