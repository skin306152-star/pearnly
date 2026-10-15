"""写账分派的纯规则（ADR-021，方案 2.5）：池里哪台电脑现在能领哪一张 Express 单。不导入框架、不做 IO。

准备时不选电脑，电脑来领任务（拉模式，每 3 秒一次）时才决定由谁写：

- 资格：这台电脑够得到这份账套（可达行状态正常、可写），它的 CC 桌面版支持这张单的载荷版本；
- 顺序：① 这台电脑上登录 CC 的人就是录入人 → ② 常驻电脑 → ③ 其他够得到的在线电脑。后一档只在前面各档都没有在线、
  支持这张单的电脑时才能领，或这张单排队（``queued_at``）已超过 :data:`HANDOFF_SECONDS`；
- 按账套黏住：一台电脑写过这份账套、主档索引还没重建（它领任务时报的 ``index_pending`` 里有这份账套的文件号；旧版不报，
  按「它最近 :data:`STICKY_SECONDS` 内写过」近似）且它在线 → 这份账套只交给它；
- 同一份账套严格按排队先后：只看每份账套的排头（:func:`heads`），排头不归这台、等锁或要更高的载荷版本，就跳过这份账套，
  去看别的账套，不再整条连接停下。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Mapping, NamedTuple

# 排头等了这么久还没有更合适的电脑来领，谁够得到谁写（与租约同长，`queue_contracts.LEASE_SECONDS`）
HANDOFF_SECONDS = 120
# 旧版 CC 桌面版不报 ``index_pending``：按「这台最近这么久内写过这份账套」近似它的主档索引还没重建
STICKY_SECONDS = 120
# 一次领取最多看这么多份账套的排头
MAX_HEADS = 50

OWN, ALWAYS_ON, OTHER, STICKY = "own", "always_on", "other", "sticky"
ROUTES = (OWN, ALWAYS_ON, OTHER, STICKY)
_TIER_NAMES = (OWN, ALWAYS_ON, OTHER)

# 不能领的原因（只给日志与测试；不进回复）
UNREACHABLE, STUCK, VERSION, NEEDS_UPDATE, BETTER = "unreachable", "sticky", "version", "needs_update", "better"


class Peer(NamedTuple):
    """池里一台电脑此刻的情况（companion 读好交进来）。"""
    # 这台电脑上最近登录 CC 的人（不是登记人，D7）
    user: str = ""
    always_on: bool = False
    online: bool = False
    # 它支持的最高载荷版本；None = 没报过（旧版），与今天一样不拿它拦
    payload_version: int | None = None
    # 它主档索引还没重建的账套文件号；None = 旧版不报这一项
    index_pending: frozenset | None = None


class Job(NamedTuple):
    """一份账套的排头。"""
    name: str
    account_key: str
    # 录入人（定时任务是规则负责人）：第 ① 档按它比
    actor: str
    # 进入排队的时刻（确认、激活、退回排队时记；旧行为空时按建立时间，:func:`queued`）
    queued_at: datetime
    payload_version: int = 1


class Account(NamedTuple):
    """这份账套的登记（黏住用）。"""
    uid: str = ""
    last_writer: str = ""
    last_written_at: datetime | None = None


class Decision(NamedTuple):
    take: bool
    # own / always_on / other / sticky（问责记录与 ``attended``）
    route: str = ""
    reason: str = ""


def queued(queued_at, created_at):
    """排队时刻：``queued_at`` 为空（这一批之前的行）时按建立时间。"""
    return queued_at or created_at


def tier(actor, peer: Peer | None) -> int:
    """0 = 这台电脑上登录 CC 的就是录入人，1 = 常驻电脑，2 = 其他。"""
    peer = peer or Peer()
    if actor and peer.user == actor:
        return 0
    return 1 if peer.always_on else 2


def supports(peer: Peer | None, version: int) -> bool:
    """这台电脑能写这个载荷版本：没报过最高版本的旧版不拦（与今天预检同一口径）。"""
    reported = (peer or Peer()).payload_version
    return reported is None or int(reported) >= int(version or 1)


def needs_update(version: int, reachers: Iterable[str], peers: Mapping[str, Peer]) -> bool:
    """够得到这份账套的电脑没有一台支持这个载荷版本：等不来，界面显示「需更新」。没有电脑够得到不算（那是「没有能写的电脑」）。"""
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
    holder = sticky_holder(account, reachers, peers, now, current=device)
    if holder and holder != device:
        return Decision(False, reason=STUCK)
    if not supports(peers[device], job.payload_version):
        return Decision(False, reason=NEEDS_UPDATE if needs_update(job.payload_version, reachers, peers) else VERSION)
    if holder == device:
        return Decision(True, STICKY)
    mine = tier(job.actor, peers[device])
    if now - job.queued_at >= timedelta(seconds=HANDOFF_SECONDS):
        return Decision(True, _TIER_NAMES[mine])
    able = [item for item in reachers if peers.get(item, Peer()).online and supports(peers.get(item), job.payload_version)]
    best = min(tier(job.actor, peers.get(item)) for item in able)
    if mine <= best:
        return Decision(True, _TIER_NAMES[mine])
    return Decision(False, reason=BETTER)


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


def attended(route: str, peer: Peer | None) -> bool:
    """这张单写的时候电脑前有没有录入人本人：只有第 ① 档、而且不是常驻电脑才算（常驻、替别人写都不弹框）。"""
    return route == OWN and not (peer or Peer()).always_on


__all__ = ["ALWAYS_ON", "Account", "Decision", "HANDOFF_SECONDS", "Job", "MAX_HEADS", "OTHER", "OWN", "Peer", "ROUTES",
           "STICKY", "STICKY_SECONDS", "attended", "decide", "heads", "needs_update", "queued", "sticky_holder",
           "supports", "tier"]
