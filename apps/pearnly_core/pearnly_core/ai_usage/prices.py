"""Anthropic 公开价目（美元 / 百万 token，2026-11 官方价）：账本与代录用。纯数据，不依赖 Frappe。

每个模型四个价：输入、输出、写缓存（5 分钟，= 输入 × 1.25）、读缓存。写 1 小时缓存按输入 × 2（官方口径）。
Haiku 5.5 一次请求的提示超过 10 万 token 时输入 / 输出按高档价（官方只列了这两项，缓存读写按同一倍数估）。

技术债（主会话 11-11 定）：识别引擎 `claude_ocr.engine.PRICE` 与代录 `express_operator.ai.PRICE` 各有一份同样的数，这次不动
（动识别要重发甲骨文并跑回放）；`tests/test_ai_usage.py` 锁住三份一致，下次动识别时并成这一份。
"""
from decimal import Decimal

D = Decimal
MILLION = D(1_000_000)
HAIKU_TIER_TOKENS = 100_000
HAIKU_TIER_FACTOR = D(5)  # 0.10 → 0.50、0.50 → 2.50

PRICES = {
    "claude-opus-5-5": (D("4.0"), D("20.0"), D("5.0"), D("0.20")),
    "claude-sonnet-5-5": (D("2.0"), D("10.0"), D("2.5"), D("0.10")),
    "claude-haiku-5-5": (D("0.10"), D("0.50"), D("0.125"), D("0.01")),
    "claude-fable-5-1": (D("10.0"), D("50.0"), D("12.5"), D("0.25")),
}


def known(model) -> bool:
    return str(model or "") in PRICES


def usd(model, input_tokens=0, output_tokens=0, cache_write_tokens=0, cache_read_tokens=0,
        cache_write_1h_tokens=0, single_request=True) -> Decimal | None:
    """按价目算美元；不认识的模型回 None（账本记 token、不记钱，不猜价）。``cache_write_tokens`` 是 5 分钟缓存的写入，
    ``cache_write_1h_tokens`` 是 1 小时的（按输入 × 2）。Haiku 高档按一次请求的提示大小定：``single_request=False``（几次请求的
    合计）时分不出哪次超了，一律按普通档。"""
    price = PRICES.get(str(model or ""))
    if price is None:
        return None
    pin, pout, pwrite, pread = price
    tokens = [int(v or 0) for v in (input_tokens, output_tokens, cache_write_tokens, cache_read_tokens, cache_write_1h_tokens)]
    if single_request and str(model) == "claude-haiku-5-5" and tokens[0] + tokens[2] + tokens[3] + tokens[4] > HAIKU_TIER_TOKENS:
        pin, pout, pwrite, pread = (p * HAIKU_TIER_FACTOR for p in price)
    total = (tokens[0] * pin + tokens[1] * pout + tokens[2] * pwrite + tokens[3] * pread + tokens[4] * pin * 2)
    return total / MILLION
