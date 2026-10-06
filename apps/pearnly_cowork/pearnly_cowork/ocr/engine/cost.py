"""识别的成本（泰铢）：按实际发出的读字次数和模型 token 算，只作观察记录，不参与计费。Claude 按回复里的分项用量
算实价（含缓存折扣）；谷歌模型按 token 估。

价格沿用线上 services/ocr/cost.py 的价目（2026-07）：没列出的模型按最贵的算（宁可估高）。读字（Vision 文字识别）
与 Document AI 读字同价，每页 0.0015 美元。
"""
THB_PER_USD = 35.0
# USD per million input / output tokens.
# Claude Sonnet 5.5（ADR-017）：Anthropic 官方标价 2 / 10 美元（2026-10-07 核对）。
MODEL_PRICES_PER_M_USD = {"gemini-3.8-flash": (0.75, 3.75), "gemini-3.1-flash-lite": (0.25, 1.50),
                          "claude-sonnet-5-5": (2.0, 10.0)}
VISION_USD_PER_IMAGE = 0.0015
# Claude 的提示词缓存按官方倍率计（读缓存 0.1 倍、写 5 分钟缓存 1.25 倍输入价）：回复里分开报了这三种输入 token，
# 按回复算出的是实价，不是估算（``response_cost_thb``）。
CLAUDE_CACHE_READ, CLAUDE_CACHE_WRITE = 0.1, 1.25


def model_cost_thb(model, input_tokens, output_tokens):
    price = next((value for prefix, value in MODEL_PRICES_PER_M_USD.items() if str(model or "").startswith(prefix)),
                 max(MODEL_PRICES_PER_M_USD.values()))
    usd = max(0, int(input_tokens or 0)) / 1e6 * price[0] + max(0, int(output_tokens or 0)) / 1e6 * price[1]
    return round(usd * THB_PER_USD, 6)


def claude_usage_usd(model, usage):
    """Claude 回复的 ``usage`` → 美元：未缓存输入、读缓存、写缓存、输出各按各的价。不是 Claude 或没有用量回 None。"""
    if not str(model or "").startswith("claude-") or not isinstance(usage, dict):
        return None
    price = next((value for prefix, value in MODEL_PRICES_PER_M_USD.items() if str(model).startswith(prefix)),
                 max(MODEL_PRICES_PER_M_USD.values()))

    def tokens(name):
        return max(0, int(usage.get(name) or 0))

    return (tokens("input_tokens") * price[0] + tokens("cache_read_input_tokens") * price[0] * CLAUDE_CACHE_READ
            + tokens("cache_creation_input_tokens") * price[0] * CLAUDE_CACHE_WRITE
            + tokens("output_tokens") * price[1]) / 1e6


def response_cost_thb(response):
    """一次模型调用（``ModelResponse`` / ``CallEvidence``）的成本：Claude 按回复里的分项用量算实价，其余按 token 估。"""
    usd = claude_usage_usd(response.model, (getattr(response, "raw", None) or {}).get("usage"))
    if usd is None:
        return model_cost_thb(response.model, response.input_tokens, response.output_tokens)
    return round(usd * THB_PER_USD, 6)


def calls_cost_thb(calls):
    """一次识别全部调用的成本：读字按次、模型按 token（调用已按检查点去重，续跑取回的不重复算）。"""
    total = 0.0
    for call in calls:
        if call.model == "vision":
            total += VISION_USD_PER_IMAGE * THB_PER_USD
        else:
            total += response_cost_thb(call)
    return round(total, 6)
