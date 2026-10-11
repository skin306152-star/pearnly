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


TOOLS_TTL = 30 * 86400


def _tools_shape(brief) -> str:
    return _digest(json.dumps(brief["tools"], sort_keys=True), 16)


def tools_current(conversation: str) -> None:
    """刚按当前说明建的会话：记下它的工具已是最新，第一条消息不必再换。"""
    frappe.cache.set_value(f"pearnly_assistant:tools:{conversation}", _tools_shape(definition.brief(config.product())),
                           expires_in_sec=TOOLS_TTL)


def keep_tools_current(api, conversation: str) -> None:
    """老对话用上新工具：这段远端会话上次换上的工具与当前说明不同（或没记过）就整组换一次。换不了不影响发消息。"""
    brief = definition.brief(config.product())
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
