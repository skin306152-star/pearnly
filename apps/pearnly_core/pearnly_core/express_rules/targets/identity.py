"""Express 账套身份:路径归一 + "数据目录 → 程序目录"配对(零重依赖的叶子模块)。

单独成模块是为了避开循环 import:services/erp/express_push/__init__.py 需要在模块顶层用
配对口径,而它被 services/erp/push_exception_classify 链引用;若从 express_target_projection
(该模块顶层 import core.db)取,就会绕回 push_exception_classify 自身的半初始化状态
(2026-09-18 实测:直接 import services.erp.push_exception_classify 会 ImportError)。
本模块只依赖 ntpath,谁都能安全引。

多台电脑（ADR-021，方案 2.4）：一份账套按物理身份登记一次，规范键与规范根登记后永不改；`resolve` 是认定规则的
纯函数（目录文件号 → 网络路径 → 本机写法），由连接域的账套登记（`connections.pool`）调用。
"""

from __future__ import annotations

import ntpath
import re
from bisect import insort
from typing import Any, Iterable, Mapping, NamedTuple, Sequence


def normalize_express_account_key(value: Any) -> str:
    raw = str(value or "").strip().replace("/", "\\").rstrip("\\")
    return ntpath.normcase(ntpath.normpath(raw))[:500] if raw else ""


def reported_account_set_roots(rows: Any) -> dict[str, str]:
    """账套配对(数据目录 → 程序目录)· 键与值都已 normalize_express_account_key。

    数据目录与程序目录是两个独立字段:事务所常把数据放在盘符(S:\\2569\\EXP69\\69SINCER),
    Express 程序却装在网络共享(\\\\accserver\\ACCOUNT\\69EXP)· 两者的字符串形态甚至可能不同
    (同一目录既可用映射盘符、也可用 UNC 描述)。所以只认上报里成对给出的 root,
    **绝不从数据目录的路径反推父目录** —— 反推出来的"上一层"跟程序目录不是一回事。

    兼容两种行形状:Agent 心跳的 {path, root} 与投影快照的 {source_id, attributes:{path, root}}。
    只给出数据目录、没给出程序目录的行不入表(caller 自己决定缺配对时是放行还是拦)。
    """
    roots: dict[str, str] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, Mapping):
            continue
        attributes = row.get("attributes") if isinstance(row.get("attributes"), Mapping) else {}
        key = normalize_express_account_key(
            row.get("path") or attributes.get("path") or row.get("source_id")
        )
        root = normalize_express_account_key(row.get("root") or attributes.get("root"))
        if key and root:
            roots.setdefault(key, root)
    return roots


def account_set_company(row: Any) -> str:
    """账套目录一行所属的公司（目录里的公司名，比较用：去首尾空白、小写）；读不出是空串。
    同一家公司跨年度的账套公司名相同：POS 版式与收款科目都按它「问一次、记住」。"""
    attributes = row.get("attributes") if isinstance(row, Mapping) and isinstance(row.get("attributes"), Mapping) else {}
    return str(attributes.get("company") or attributes.get("company_name") or "").strip().casefold()


def same_company_account_sets(rows: Any, account_set: Any) -> list[str]:
    """同一份目录里与这份账套同属一家公司的其他账套键（已归一、排好序）；这份账套读不出公司名回空。"""
    key = normalize_express_account_key(account_set)
    found = [(normalize_express_account_key(row.get("source_id")), account_set_company(row))
             for row in rows if isinstance(row, Mapping) and row.get("source_id")] if isinstance(rows, list) else []
    mine = next((company for source, company in found if source == key), "")
    if not mine:
        return []
    return sorted({source for source, company in found if company == mine} - {key})


# ── 账套物理身份（方案 2.4）────────────────────────────────────────────────────────────

MATCH, PENDING, NEW = "match", "pending", "new"
_LOOPBACK = frozenset(("localhost", "127.0.0.1", "::1", "[::1]"))
_DRIVE = re.compile(r"^([a-z]):(\\.*)?$", re.IGNORECASE)


def clean_uid(value: Any) -> str:
    """目录文件号（卷序列号 + 64 位文件号，CC 桌面版只读打开取得）：不透明串，只去空白、小写、限长。"""
    text = str(value or "").strip().lower()
    return text[:80] if text and all(ch.isalnum() or ch in ":-_." for ch in text) else ""


def _unc(value: Any) -> tuple[str, str] | None:
    raw = str(value or "").strip().replace("/", "\\")
    if not raw.startswith("\\\\"):
        return None
    host, _, rest = raw[2:].partition("\\")
    host = host.strip().lower()
    return (host, rest) if host else None


def host_aliases(hosts: Mapping[str, Iterable[str]]) -> dict[str, str]:
    """``{电脑: host_names}`` → ``{别名: 规范主机名}``（都小写）。规范主机名 = 这台电脑报的第一个名字（本机名）；
    DNS 名的第一段也算别名。同一个别名指向两台电脑（WSL / Hyper-V 网段、VPN 地址在各台上可能重复）就丢掉，不猜。"""
    seen: dict[str, set[str]] = {}
    for names in hosts.values():
        names = [str(name or "").strip().lower() for name in (names or []) if str(name or "").strip()]
        names = [name for name in names if name not in _LOOPBACK]
        if not names:
            continue
        canonical = names[0]
        for name in names:
            for alias in {name, name.split(".")[0] if not name.replace(".", "").isdigit() else name}:
                seen.setdefault(alias, set()).add(canonical)
    return {alias: next(iter(owners)) for alias, owners in seen.items() if len(owners) == 1}


def normalize_location(share: Any, *, aliases: Mapping[str, str] | None = None, own_host: str = "") -> str:
    """网络路径（``\\\\主机\\共享\\…``）规整成比较用的写法：主机名按别名归一（``\\\\IP`` 与 ``\\\\主机名`` 是同一处），
    ``localhost`` / ``127.0.0.1`` 换成上报这台电脑自己的主机名；不是网络路径回空串。"""
    parts = _unc(share)
    if not parts:
        return ""
    host, rest = parts
    if host in _LOOPBACK and own_host:
        host = own_host.lower()
    host = (aliases or {}).get(host, host)
    return normalize_express_account_key("\\\\" + host + ("\\" + rest if rest else ""))


def host_qualified(path: Any, host: str) -> str:
    """只在本机的路径写成带主机名的路径形状（``C:\\EXP\\BOOK`` → ``\\\\主机\\c$\\exp\\book``）：各台唯一，仍是路径，
    文件夹名不变。已经是网络路径的原样规整。"""
    raw = str(path or "").strip().replace("/", "\\")
    if not raw or _unc(raw):
        return normalize_express_account_key(raw)
    found = _DRIVE.match(raw)
    if found and host:
        return normalize_express_account_key("\\\\" + host + "\\" + found.group(1) + "$" + (found.group(2) or ""))
    return normalize_express_account_key(raw)


def network_root(path: Any, location: str, root: Any, host: str, aliases: Mapping[str, str] | None = None) -> str:
    """规范根：程序目录也按数据目录那条网络路径的换算办法换成网络写法（映射盘 / 本机共享的前缀替换）；
    换不了（程序目录不在同一个共享下）就写成带主机名的路径。"""
    local, wanted = normalize_express_account_key(path), normalize_express_account_key(root)
    if not wanted:
        return ""
    if _unc(wanted):
        return normalize_location(wanted, aliases=aliases, own_host=host)
    if location and local:
        mine, theirs = local.split("\\"), location.split("\\")
        same = 0
        while (same < len(mine) - 1 and same < len(theirs) - 4
               and mine[-1 - same] == theirs[-1 - same]):
            same += 1
        prefix, network = "\\".join(mine[:len(mine) - same]), "\\".join(theirs[:len(theirs) - same])
        if wanted == prefix or wanted.startswith(prefix + "\\"):
            return normalize_express_account_key(network + wanted[len(prefix):])
    return host_qualified(wanted, host)


def content_key(attributes: Mapping[str, Any] | None) -> str:
    """内容键 = 税号 + 分店 + 期初；没有税号时用公司名（小写）+ 账套代码 + 期初；都没有就为空。
    只用于待认定的辅助判断与 company 兜底锁，不单独用来合并：备份副本的特征完全相同。"""
    values = attributes if isinstance(attributes, Mapping) else {}
    period = str(values.get("period_start") or "").strip()
    tax = re.sub(r"\D", "", str(values.get("tax_id") or ""))
    if tax:
        return "tax:%s:%s:%s" % (tax, str(values.get("branch") or "").strip(), period)
    company = str(values.get("company") or values.get("company_name") or "").strip().casefold()
    if company:
        return "name:%s:%s:%s" % (company, str(values.get("code") or "").strip().casefold(), period)
    return ""


class Registered(NamedTuple):
    """一份已登记的账套（`Cowork Express Account Set`）和它此刻的可达情况，由调用方从库里读好传入。"""

    account_key: str
    root_key: str = ""
    uid: str = ""
    location: str = ""
    content_key: str = ""
    # 别的（没退役的）电脑也够得到它
    reached_by_others: bool = False
    # 够得到它的别的电脑 7 天内报到过（待认定的退出条件：它们都被移除 / 退役或 7 天没报到）
    others_live: bool = False
    # 只属于某台电脑（它本机的账套）：那台电脑的设备编号；空 = 全池共用
    scope_device: str = ""
    # 它只属于另一台电脑、那台还够得到它：本机写法（没有网络路径）不能认成它（克隆盘上的同号拷贝、两台同一路径）
    foreign: bool = False
    # 别的电脑 7 天内按哪些文件号够到它（第 6 条：同一个路径串、文件号对不上时判冲突用）
    others_uids: frozenset = frozenset()

    @property
    def identified(self) -> bool:
        return bool(self.uid or self.location)


class Decision(NamedTuple):
    """一行上报的认定结果。``kind``：``match`` 就是这份登记；``pending`` 待认定（第 5 条等旧电脑报文件号，第 6 条
    文件号冲突；可达行记 ``pending_identity``，指向它在等的那份，不派写入）；``new`` 新登记（键与根由本函数给出）。``rule`` 是方案 2.4 的条号。
    ``uid`` / ``location`` 非空时写到登记上（只在 ``match`` / ``new``；规范键与规范根从不改）。"""

    kind: str
    account_key: str
    root_key: str
    rule: int
    uid: str = ""
    location: str = ""
    content_key: str = ""
    local: bool = False  # new：只在本机（键带主机名，或旧版报的路径），登记只属于这台电脑


class Registry:
    """:func:`resolve` 用的登记索引：按文件号、（规整后的网络路径，内容键）、内容键、规范键各建一份，一行上报只看命中的
    那几份，不再逐份扫整张登记、逐份规整网络路径（679 份的首次整份上报是 679 × 679 次规整，10-20 测试机约 1 秒、生产更慢，
    全程持着连接行锁）。同一条件下仍取登记顺序里的第一份，与逐份扫的结果相同。

    同一轮上报里新登记的（:meth:`put` 追加在最后）、补上了文件号 / 网络路径的（:meth:`put` 原位替换），后面的行马上认得。
    ``aliases`` 与 :func:`resolve` 的同一份；``normalized`` 可传一个字典在几份索引之间共用网络路径的规整结果。纯内存。"""

    def __init__(self, registered: Iterable[Registered] = (), *, aliases: Mapping[str, str] | None = None,
                 normalized: dict[str, str] | None = None) -> None:
        self.aliases = dict(aliases or {})
        self._normalized = {} if normalized is None else normalized
        self._items: list[Registered] = []
        self._position: dict[str, int] = {}
        self._by: dict[tuple, list[int]] = {}
        for item in registered:
            self.put(item)

    def __iter__(self):
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def get(self, account_key: str) -> Registered | None:
        position = self._position.get(account_key)
        return None if position is None else self._items[position]

    def location_of(self, item: Registered) -> str:
        if not item.location:
            return ""
        if item.location not in self._normalized:
            self._normalized[item.location] = normalize_location(item.location, aliases=self.aliases)
        return self._normalized[item.location]

    def _keys(self, item: Registered) -> list[tuple]:
        keys = []
        if item.uid:
            keys.append(("uid", item.uid))
        location = self.location_of(item)
        if location:
            keys.append(("location", location, item.content_key))
        if item.content_key:
            keys.append(("content", item.content_key))
        return keys

    def put(self, item: Registered) -> None:
        """登记一份：规范键已在就原位替换（顺序不变），否则追加在最后。"""
        position = self._position.get(item.account_key)
        if position is None:
            position = len(self._items)
            self._items.append(item)
            self._position[item.account_key] = position
        else:
            for key in self._keys(self._items[position]):
                self._by[key].remove(position)
            self._items[position] = item
        for key in self._keys(item):
            insort(self._by.setdefault(key, []), position)

    def first(self, key: tuple, accept) -> Registered | None:
        for position in self._by.get(key, ()):
            item = self._items[position]
            if accept(item):
                return item
        return None


def resolve(row: Mapping[str, Any], *, registered: Sequence[Registered] | Registry, own_reach: Mapping[str, str],
            legacy: bool, host: str, aliases: Mapping[str, str] | None = None) -> Decision:
    """设备 D 上报的一行账套（投影行 ``{source_id, attributes: {path, root, uid, share, …}}``）按方案 2.4 认定。

    ``registered``：已登记的账套，按登记顺序；整份名单逐行认定时传 :class:`Registry`（建一次、各行共用）。
    ``own_reach``：D 以前够到的 ``{本机路径（规整）: 规范键}``；``legacy``：D 是没认领过的旧版 CC 桌面版（只按路径认）；
    ``host``：D 的主机名（新登记的本机账套键用它）。不读库、不改任何东西。
    """
    attributes = row.get("attributes") if isinstance(row.get("attributes"), Mapping) else {}
    path = attributes.get("path") or row.get("source_id")
    local = normalize_express_account_key(path)
    uid = clean_uid(attributes.get("uid"))
    location = normalize_location(attributes.get("share"), aliases=aliases, own_host=host)
    content = content_key(attributes)
    index = registered if (isinstance(registered, Registry) and registered.aliases == dict(aliases or {})) \
        else Registry(registered, aliases=aliases)

    def match(item: Registered, rule: int) -> Decision:
        return Decision(MATCH, item.account_key, item.root_key, rule, uid, location, content)

    # 1 目录文件号相同。上报行没有网络路径时，只属于另一台还够得到它的电脑的登记不认：整盘克隆的两台电脑，
    #   卷序列号跟着复制，各自本机的拷贝可能是同一个文件号
    if uid:
        item = index.first(("uid", uid), lambda item: location or not item.foreign)
        if item is not None:
            return match(item, 1)
    # 2 网络路径相同（主机别名归一）且内容键相同：补上文件号
    if location:
        item = index.first(("location", location, content), lambda item: True)
        if item is not None:
            return match(item, 2)
    # 3 D 以前用同一本机路径够到过，且是同一处：那份登记还没报过文件号 / 网络路径（升级），或者网络路径（都没有也算）
    #   与内容键都相同（从备份恢复到原处）。盘符改映射到另一处（网络路径或内容键对不上）就不认，落到第 5、6 条。
    #   那份已带别的文件号、又有别的电脑够得到时也不认：那是两台各自本机同一路径的两份拷贝。
    #   这一行读不出身份（文件号、网络路径都没有：目录这会儿打不开 / 不在了、本机共享撤了）又没有对不上的内容键，没有
    #   反证就仍是 D 一直用这个路径够到的那份，不另登记一份带主机名的（10-20 测试机：账套目录改名后冒出
    #   `\\本机\c$\…` 一行，改回原名也回不去）。
    item = index.get(own_reach.get(local) or "")
    if item is not None:
        blind = not uid and not location and (not content or content == item.content_key)
        same_place = not item.identified or blind or (
            normalize_location(item.location, aliases=aliases) == location and item.content_key == content)
        if (same_place and not item.foreign
                and not (uid and item.uid and item.uid != uid and item.reached_by_others)):
            return match(item, 3)
    root = attributes.get("root")
    # 4 旧版桌面端（没有文件号也没有位置）：规范键与路径相同的就是它（今天的口径）；否则新建一份只属于 D 的登记。
    #   路径相同的那份只属于另一台还够得到它的电脑时，D 的这份是它自己本机的另一份拷贝：键带 D 的主机名
    if legacy and not uid and not location:
        item = index.get(local)
        if item is None:
            return Decision(NEW, local, normalize_express_account_key(root), 4, local=True, content_key=content)
        key = host_qualified(path, host)
        if not item.foreign or key == local:  # 网络路径（写不成带主机名的）对谁都是同一处
            return match(item, 4)
        mine = index.get(key)
        if mine is not None and not mine.foreign:
            return match(mine, 4)
        return Decision(NEW, key, host_qualified(root, host), 4, local=True, content_key=content)
    # 5 有文件号 / 位置但没认上，登记里有一份还没报过文件号 / 位置、内容键相同、上报它的电脑还在的：待认定
    if content:
        item = index.first(("content", content), lambda item: not item.identified and item.others_live)
        if item is not None:
            return Decision(PENDING, item.account_key, item.root_key, 5, uid, location, content)
    # 6 新登记：有网络路径的键 = 网络路径；只在本机的键 = 带主机名的路径。同一个路径串已经登记过就是同一处——
    #   除非双方文件号都有且不同、别的电脑 7 天内还按原文件号够到它（不同办公室 / VPN 下同一个 IP 的两台 NAS）：
    #   那是另一份物理账套，待认定（不派写入），等原文件号没人再报了再认
    key = location or host_qualified(path, host)
    item = index.get(key)
    if item is not None:
        if uid and item.uid and item.uid != uid and item.uid in item.others_uids:
            return Decision(PENDING, item.account_key, item.root_key, 6, uid, location, content)
        return match(item, 6)
    root_key = network_root(path, location, root, host, aliases) if location else host_qualified(root, host)
    return Decision(NEW, key, root_key, 6, uid, location, content, local=not location)


__all__ = ["MATCH", "NEW", "PENDING", "Decision", "Registered", "Registry", "account_set_company", "clean_uid",
           "content_key", "host_aliases", "host_qualified", "network_root", "normalize_express_account_key", "normalize_location",
           "reported_account_set_roots", "resolve", "same_company_account_sets"]
