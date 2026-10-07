"""识别的粗略成本（泰铢）：按实际发出的读字次数和模型 token 估，只作观察记录，不参与计费。

价格沿用线上 services/ocr/cost.py 的价目（2026-07）：没列出的模型按最贵的算（宁可估高）。读字（Vision 文字识别）
与 Document AI 读字同价，每页 0.0015 美元。
"""
THB_PER_USD = 35.0
# USD per million input / output tokens.
MODEL_PRICES_PER_M_USD = {"gemini-3.8-flash": (0.75, 3.75), "gemini-3.1-flash-lite": (0.25, 1.50)}
VISION_USD_PER_IMAGE = 0.0015


def model_cost_thb(model, input_tokens, output_tokens):
    price = next((value for prefix, value in MODEL_PRICES_PER_M_USD.items() if str(model or "").startswith(prefix)),
                 max(MODEL_PRICES_PER_M_USD.values()))
    usd = max(0, int(input_tokens or 0)) / 1e6 * price[0] + max(0, int(output_tokens or 0)) / 1e6 * price[1]
    return round(usd * THB_PER_USD, 6)


def calls_cost_thb(calls):
    """一次识别全部调用的成本：读字按次、模型按 token（调用已按检查点去重，续跑取回的不重复算）。"""
    total = 0.0
    for call in calls:
        if call.model == "vision":
            total += VISION_USD_PER_IMAGE * THB_PER_USD
        else:
            total += model_cost_thb(call.model, call.input_tokens, call.output_tokens)
    return round(total, 6)
