"""按站点配置取用引擎适配器，并缓存它在厂商账号里备好的资源（ADR-019）。

缓存键 = 引擎 + 密钥指纹 + 模型 + 智能体说明：任何一项变了（后台换了引擎 / 密钥 / 模型，或代码改了说明），
下次使用时适配器重新备一次；没变就直接用缓存，不每次请求都去厂商那边查。
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


def prepared(api) -> dict:
    """本站产品在当前引擎账号里的句柄（交给 `api.open`）。"""
    s = config.settings()
    brief = definition.brief(config.product())
    shape = _digest(json.dumps([s.model, brief], sort_keys=True), 16)
    key = f"pearnly_assistant:{s.engine.code}:{_digest(s.api_key, 12)}:{brief['product']}:{shape}"
    cached = frappe.cache.get_value(key)
    if cached:
        return cached
    with frappe.cache.lock(f"{frappe.local.site}:{key}:lock", timeout=LOCK_TIMEOUT):
        cached = frappe.cache.get_value(key)
        if not cached:
            cached = api.prepare(brief)
            frappe.cache.set_value(key, cached, expires_in_sec=CACHE_TTL)
        return cached
