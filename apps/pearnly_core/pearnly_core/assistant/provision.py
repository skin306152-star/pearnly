"""按本站密钥，在其所属 Anthropic 账号里确保有本产品的智能体与运行环境（ADR-019）。

为什么站点自己做、不让人配编号：密钥由租户管理后台随时可换（换成另一个账号时，旧编号在新账号里不存在）。
站点按「密钥指纹 + 定义指纹」缓存结果；缓存没有就去账号里按元数据找，找不到才建，定义变了就更新——
同一个产品在同一账号里始终只有一个智能体，不会每次请求都新建。
"""
import hashlib

import frappe

from pearnly_core.assistant import client as anthropic_client
from pearnly_core.assistant import config, definition

CACHE_TTL = 6 * 3600
LOCK_TIMEOUT = 60


def _cache_key(spec: dict) -> str:
    key = hashlib.sha256(config.api_key().encode()).hexdigest()[:12]
    return f"pearnly_assistant:{key}:{spec['metadata']['pearnly_product']}:{spec['metadata']['pearnly_spec']}"


def ensure() -> tuple[str, str]:
    """返回 (agent_id, environment_id)。"""
    spec = definition.spec(config.product())
    key = _cache_key(spec)
    cached = frappe.cache.get_value(key)
    if cached:
        return cached["agent"], cached["environment"]
    with frappe.cache.lock(f"{frappe.local.site}:{key}:lock", timeout=LOCK_TIMEOUT):
        cached = frappe.cache.get_value(key)
        if cached:
            return cached["agent"], cached["environment"]
        api = anthropic_client.client()
        ids = {"agent": _agent(api, spec), "environment": _environment(api)}
        frappe.cache.set_value(key, ids, expires_in_sec=CACHE_TTL)
        return ids["agent"], ids["environment"]


def _agent(api, spec: dict) -> str:
    product = spec["metadata"]["pearnly_product"]
    for agent in api.beta.agents.list():
        meta = agent.metadata or {}
        if meta.get("pearnly_managed") == definition.MANAGED_BY and meta.get("pearnly_product") == product:
            if meta.get("pearnly_spec") != spec["metadata"]["pearnly_spec"]:
                api.beta.agents.update(agent.id, **spec)
            return agent.id
    return api.beta.agents.create(**spec).id


def _environment(api) -> str:
    for environment in api.beta.environments.list():
        if environment.name == definition.ENVIRONMENT_NAME:
            return environment.id
    return api.beta.environments.create(**definition.ENVIRONMENT).id
