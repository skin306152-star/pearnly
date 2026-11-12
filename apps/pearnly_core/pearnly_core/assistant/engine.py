"""按站点配置取用引擎适配器，并缓存它在厂商账号里备好的资源（ADR-019）。

缓存键 = 引擎 + 密钥指纹 + 适配器按说明要配成的样子（含模型，`Adapter.shape`）：任何一项变了（后台换了引擎 / 密钥 /
模型，代码改了说明，或适配器自己的配置变了，例如自带工具的权限），下次使用时适配器重新备一次；没变就直接用缓存，
不每次请求都去厂商那边查。
"""
import hashlib
import json

import frappe

from pearnly_core.assistant import config, definition

CACHE_TTL = 6 * 3600
LOCK_TIMEOUT = 60
# 适配器按「引擎 + 密钥 + 模型」在本进程复用：同一个 HTTP 客户端保持连接，不每个请求重新握手。
_adapters: dict = {}


def adapter():
    s = config.settings()
    key = (s.engine.code, _digest(s.api_key, 16), s.model)
    found = _adapters.get(key)
    if found is None:
        found = _adapters[key] = frappe.get_attr(s.engine.adapter)(s.api_key, s.model)
    return found


def _digest(value: str, size: int) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:size]


# ── 以前用过的密钥（11-12）：只用来读 ──────────────────────────────────────────────────────────────────────────
# 远端会话跟着建它的那把密钥：11-10 22:00 全平台换密钥后，旧密钥建的对话新密钥读不到（「Session not
# found（引擎回 404），11-11 erpcowork 388 条，打开旧对话历史出不来）。站点配置 `pearnly_assistant_previous_api_keys`（列表，同引擎、
# 由后台写入，没配等同以前）列着以前用过的密钥：当前密钥读不到的那段，按顺序用它们读会话状态、事件和文件；接着聊一律在
# 当前密钥下开新段（`sessions.segment_for_turn`）。旧密钥**只读**：建会话、发消息、传文件、改名、归档、删除、总结这些
# 会动远端或产生费用的调用一律拒绝（`ReadOnly`）。密钥不进日志、不进报错。
PREVIOUS_KEYS = "pearnly_assistant_previous_api_keys"
# 哪一段该用哪把密钥读：读到过就记一天；哪把都读不到记十分钟（远端一时不通的不记，下次再试）
READER_TTL = 24 * 3600
GONE_TTL = 10 * 60
CURRENT, GONE = "current", "gone"


class ReadOnlyKey(RuntimeError):
    """对以前用过的密钥做了读以外的调用（只报调用名，不带密钥）。"""


class ReadOnly:
    """以前用过的密钥的适配器，只放行读：会话状态、事件、文件列表、文件内容、会话说明。其余一律 `ReadOnlyKey`。"""

    READS = ("status", "events", "files", "read_file", "metadata", "gone")

    def __init__(self, inner, tag: str):
        self._inner = inner
        self.tag = tag  # 密钥指纹（缓存里记的就是它，不是密钥）

    def __getattr__(self, name):
        if name in self.READS:
            return getattr(self._inner, name)
        raise ReadOnlyKey(f"previous assistant keys only read; refused: {name}")


def gone(source, err) -> bool:
    """`source`（适配器或以前密钥的只读适配器）说这个错误是「这把密钥下没有这段会话」。"""
    check = getattr(source, "gone", None)
    return bool(check and check(err))


def previous_keys() -> list:
    """站点配置里以前用过的密钥（列表；也认一个字符串），去掉空的和当前这把。"""
    found = frappe.conf.get(PREVIOUS_KEYS) or []
    if isinstance(found, str):
        found = [found]
    current = str(frappe.conf.get("pearnly_assistant_api_key") or "").strip()
    keys = []
    for key in found:
        key = str(key or "").strip()
        if key and key != current and key not in keys:
            keys.append(key)
    return keys


def previous_readers() -> list:
    """以前用过的每把密钥一个只读适配器（同引擎、同模型，按进程复用）。"""
    keys = previous_keys()
    if not keys:
        return []
    s = config.settings()
    readers = []
    for key in keys:
        cache_key = ("previous", s.engine.code, _digest(key, 16), s.model)
        found = _adapters.get(cache_key)
        if found is None:
            found = _adapters[cache_key] = ReadOnly(frappe.get_attr(s.engine.adapter)(key, s.model), _digest(key, 16))
        readers.append(found)
    return readers


def reader(api, remote):
    """读这段远端会话用哪个适配器：当前密钥读得到 → `api`；当前读不到、以前的某把读得到 → 它的只读适配器；哪把都读不到
    （远端删了 / 过期）→ None。没配以前的密钥：直接回 `api`，不多试（等同以前）。判断记进缓存，同一段不每次都试。
    远端一时不通（不是「不存在」）照旧抛出。"""
    previous = previous_readers()
    if not previous:
        return api
    key = f"pearnly_assistant:reader:{_digest(config.settings().api_key, 16)}:{remote}"
    known = frappe.cache.get_value(key)
    if known == CURRENT:
        return api
    if known == GONE:
        return None
    for candidate in previous:
        if known == candidate.tag:
            return candidate
    for candidate, tag in ((api, CURRENT), *((p, p.tag) for p in previous)):
        try:
            candidate.status(remote)
        except Exception as err:
            if not candidate.gone(err):
                raise
            continue
        frappe.cache.set_value(key, tag, expires_in_sec=READER_TTL)
        return candidate
    frappe.cache.set_value(key, GONE, expires_in_sec=GONE_TTL)
    return None


PREWARM_JOB = "pearnly_core.assistant.engine.prewarm_now"
PREWARM_TIMEOUT = 120


def prewarm() -> None:
    """后台先把本站产品的智能体资源备好（批 A ②，2026-11-09）：部署后或换密钥后第一个新对话要先在厂商那边备一次（实验栈
    2.4 秒，生产冷的一次 13.7 秒），这一步提前到这两个时刻各做一次——迁移后（hooks 的 after_migrate）、下发智能体设置后
    （site-step 的 set_assistant）。只排一次、不做定时、不循环、不常驻（空闲零花费）；备资源只调厂商的管理接口，不花模型钱。
    没开通 CC 的站点不排；排不上只记日志，不挡迁移、不挡部署，第一个用户来了照常现备（`prepared`）。"""
    try:
        if config.unavailable():
            return
        frappe.enqueue(PREWARM_JOB, queue="short", timeout=PREWARM_TIMEOUT, deduplicate=True, enqueue_after_commit=True,
                       job_id=f"assistant-prewarm-{frappe.local.site}")
    except Exception:
        frappe.log_error(title="Assistant prewarm not queued")


def prewarm_now() -> None:
    """后台那一次：按当前设置备好资源、写进缓存（同 `prepared`）。厂商一时不通只记日志。"""
    try:
        prepared(adapter())
    except Exception:
        frappe.log_error(title="Assistant prewarm failed")


TOOLS_TTL = 30 * 86400


def _tools_shape(brief) -> str:
    return _digest(json.dumps(brief["tools"], sort_keys=True), 16)


def tools_current(conversation: str, mode: str = "chat") -> None:
    """刚按当前说明建的会话：智能体的全套工具正好就是这种会话该有的，记下已是最新，第一条消息不必再换；不是（有只属于另一种
    会话的工具）就不记，第一条消息前按模式整组换（:func:`keep_tools_current`）。"""
    product = config.product()
    wanted = _tools_shape(definition.brief(product, mode))
    if wanted == _tools_shape(definition.brief(product)):
        frappe.cache.set_value(f"pearnly_assistant:tools:{conversation}", wanted, expires_in_sec=TOOLS_TTL)


def keep_tools_current(api, conversation: str, mode: str = "chat") -> None:
    """这段远端会话的工具按它的模式（直写 / 代录，工具声明的 ``modes``）与当前说明对齐：上次换上的与此刻该有的不同（或没记过）
    就整组换一次（老对话用上新工具、代录会话不给直写工具，11-08 生产事故）。换不了不影响发消息，运行时仍按模式拒（`toolrun`）。"""
    brief = definition.brief(config.product(), mode)
    key = f"pearnly_assistant:tools:{conversation}"
    if frappe.cache.get_value(key) == _tools_shape(brief):
        return
    try:
        if api.refresh(conversation, brief):
            frappe.cache.set_value(key, _tools_shape(brief), expires_in_sec=TOOLS_TTL)
    except Exception:
        frappe.log_error(title="Assistant tools refresh failed")


def prepared(api) -> dict:
    """本站产品在当前引擎账号里的句柄（交给 `api.open`）。"""
    s = config.settings()
    brief = definition.brief(config.product())
    key = f"pearnly_assistant:{s.engine.code}:{_digest(s.api_key, 12)}:{brief['product']}:{api.shape(brief)}"
    cached = frappe.cache.get_value(key)
    if cached:
        return cached
    with frappe.cache.lock(f"{frappe.local.site}:{key}:lock", timeout=LOCK_TIMEOUT):
        cached = frappe.cache.get_value(key)
        if not cached:
            cached = api.prepare(brief)
            frappe.cache.set_value(key, cached, expires_in_sec=CACHE_TTL)
        return cached
