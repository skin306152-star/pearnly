"""Anthropic 适配层：只负责建客户端与把 SDK 对象转成可回给浏览器的纯 JSON。"""
import anthropic

from pearnly_core.assistant import config

# 浏览器等着这些请求，不能挂太久；SDK 自己会对 429 / 5xx 重试。
TIMEOUT = 30
RETRIES = 2


def client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=config.api_key(), timeout=TIMEOUT, max_retries=RETRIES)


def plain(obj) -> dict:
    return obj.to_dict(mode="json")


def gone(err) -> bool:
    """会话在 Anthropic 那边已不存在（删了 / 过期 / 编号不对）。"""
    return isinstance(err, anthropic.APIStatusError) and err.status_code in (400, 404, 410)
