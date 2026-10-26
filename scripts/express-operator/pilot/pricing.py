"""按 API 回的 usage 换算美元。价格取自官方价目页（2026-10-26 核对）：

| 模型 | 输入 | 5 分钟缓存写 | 1 小时缓存写 | 缓存读 | 输出 |
|---|---|---|---|---|---|
| Opus 5.5 | 4.00 | 5.00 | 8.00 | 0.20 | 20.00 |
| Haiku 5.5（提示 ≤ 10 万 token） | 0.10 | 0.125 | 0.20 | 0.01 | 0.50 |
| Haiku 5.5（提示 > 10 万 token） | 0.50 | 0.625 | 1.00 | 0.05 | 2.50 |

单位：美元 / 百万 token。Opus 5.5 全 1M 上下文同价；Haiku 5.5 按「这一次请求的提示长度」（输入 + 缓存读 + 缓存写）选价。
电脑操作工具集（computer_toolset_20260801）本身每次请求约多 4,500 个输入 token，已含在 usage 里，不另算。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Dict, Mapping

OPUS = "claude-opus-5-5"
HAIKU = "claude-haiku-5-5"
HAIKU_LONG_PROMPT = 100_000
USD_TO_THB = Decimal("33")  # 讨论记录里的粗估汇率，只用于报告里的泰铢换算

_M = Decimal(1_000_000)
RATES: Dict[str, Dict[str, Decimal]] = {
    OPUS: {"in": Decimal("4.00"), "cw5m": Decimal("5.00"), "cw1h": Decimal("8.00"), "cr": Decimal("0.20"),
           "out": Decimal("20.00")},
    HAIKU: {"in": Decimal("0.10"), "cw5m": Decimal("0.125"), "cw1h": Decimal("0.20"), "cr": Decimal("0.01"),
            "out": Decimal("0.50")},
    HAIKU + ":long": {"in": Decimal("0.50"), "cw5m": Decimal("0.625"), "cw1h": Decimal("1.00"), "cr": Decimal("0.05"),
                      "out": Decimal("2.50")},
}


def _get(usage, name: str) -> int:
    if isinstance(usage, Mapping):
        return int(usage.get(name) or 0)
    return int(getattr(usage, name, 0) or 0)


def normalize(usage) -> Dict[str, int]:
    """把 SDK 的 usage 对象（或字典）摊成整数字段；缓存写按 5 分钟 / 1 小时拆开（拿不到细分就全算 5 分钟）。"""
    created = _get(usage, "cache_creation_input_tokens")
    detail = usage.get("cache_creation") if isinstance(usage, Mapping) else getattr(usage, "cache_creation", None)
    cw1h = _get(detail, "ephemeral_1h_input_tokens") if detail else 0
    cw5m = _get(detail, "ephemeral_5m_input_tokens") if detail else created
    if detail and cw5m + cw1h != created:
        cw5m = created - cw1h
    return {"input": _get(usage, "input_tokens"), "output": _get(usage, "output_tokens"),
            "cache_read": _get(usage, "cache_read_input_tokens"), "cache_write_5m": cw5m, "cache_write_1h": cw1h}


def prompt_tokens(norm: Dict[str, int]) -> int:
    return norm["input"] + norm["cache_read"] + norm["cache_write_5m"] + norm["cache_write_1h"]


def rate_key(model: str, norm: Dict[str, int]) -> str:
    if model == HAIKU and prompt_tokens(norm) > HAIKU_LONG_PROMPT:
        return HAIKU + ":long"
    if model not in RATES:
        raise KeyError(f"没有 {model} 的价格")
    return model


def cost(model: str, usage) -> Decimal:
    norm = usage if isinstance(usage, dict) and "cache_write_5m" in usage else normalize(usage)
    r = RATES[rate_key(model, norm)]
    total = (norm["input"] * r["in"] + norm["cache_write_5m"] * r["cw5m"] + norm["cache_write_1h"] * r["cw1h"]
             + norm["cache_read"] * r["cr"] + norm["output"] * r["out"]) / _M
    return total.quantize(Decimal("0.000001"))


def worst_case(model: str, prompt: int, max_tokens: int) -> Decimal:
    """一次请求最多可能花多少（全按未缓存输入 + 输出打满算），用于发请求前的预算闸。"""
    key = HAIKU + ":long" if model == HAIKU and prompt > HAIKU_LONG_PROMPT else model
    r = RATES[key]
    return ((prompt * r["in"] + max_tokens * r["out"]) / _M).quantize(Decimal("0.000001"))
