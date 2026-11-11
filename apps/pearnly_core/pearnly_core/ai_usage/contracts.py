"""账本的纯函数：把 Anthropic 回的用量规整成账本的几列、算 CC 对话「当天增量」。不依赖 Frappe、不做 IO。"""
from decimal import Decimal

from pearnly_core.ai_usage import prices

PURPOSES = ("cc_chat", "cc_title", "operator", "ocr")
# 费用来源：官方会话累计的牌价费用 / 本地价目表 / 识别结果里已经记下的美元（不重算）
SOURCES = ("list_cost", "price_table", "ocr_result")
TOKENS = ("input_tokens", "output_tokens", "cache_write_tokens", "cache_read_tokens")
METRICS = TOKENS + ("active_seconds", "usd")
CENT = Decimal("0.01")


def _get(obj, key, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _int(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _writes(usage) -> tuple[int, int]:
    """写缓存的 token：(5 分钟, 1 小时)。有按时长拆开的就按它，没有的全按 5 分钟（Messages API 的老写法）。"""
    split = _get(usage, "cache_creation")
    five, hour = _int(_get(split, "ephemeral_5m_input_tokens")), _int(_get(split, "ephemeral_1h_input_tokens"))
    if five or hour:
        return five, hour
    return _int(_get(usage, "cache_creation_input_tokens")), 0


def call(model, usage) -> dict:
    """一次 Messages API 调用（标题、代录）的用量 → 账本的 token 列与按价目算的美元（`price_table`）。``usage`` 可以是字典或
    SDK 对象；不认识的模型美元记 None。"""
    five, hour = _writes(usage)
    row = {"model": str(model or ""), "input_tokens": _int(_get(usage, "input_tokens")),
           "output_tokens": _int(_get(usage, "output_tokens")), "cache_write_tokens": five + hour,
           "cache_read_tokens": _int(_get(usage, "cache_read_input_tokens")), "active_seconds": 0.0}
    row["usd"] = prices.usd(model, row["input_tokens"], row["output_tokens"], five, row["cache_read_tokens"], hour)
    row["usd_source"] = "price_table"
    return row


def ocr_stages(result) -> list[dict]:
    """一次真正付了费的识别结果（`claude_ocr.client` 的 read 结果，不是检查点复用）→ 账本行：每个模型一行，token 按各次调用
    相加；美元直接用结果里已经记下的 ``usd``（不重算，`ocr_result`），按各模型那几次的 ``usd`` 分到各行，合计等于结果的 ``usd``。
    结果里没有调用明细（旧版甲骨文）就记一行、模型留空。"""
    stages = [s for s in (_get(result, "stages") or []) if isinstance(s, dict)]
    total = Decimal(str(_get(result, "usd") or 0))
    if not stages:
        return [{"model": "", **{k: 0 for k in TOKENS}, "active_seconds": 0.0, "usd": total, "usd_source": "ocr_result"}] \
            if total else []
    rows = {}
    for stage in stages:
        model = str(stage.get("model") or "")
        usage = stage.get("usage") or {}
        row = rows.setdefault(model, {"model": model, **{k: 0 for k in TOKENS}, "active_seconds": 0.0,
                                      "usd": Decimal(0), "usd_source": "ocr_result"})
        row["input_tokens"] += _int(usage.get("input_tokens"))
        row["output_tokens"] += _int(usage.get("output_tokens"))
        row["cache_write_tokens"] += _int(usage.get("cache_creation_input_tokens"))
        row["cache_read_tokens"] += _int(usage.get("cache_read_input_tokens"))
        row["active_seconds"] += float(stage.get("seconds") or 0)
        row["usd"] += Decimal(str(stage.get("usd") or 0))
    out = list(rows.values())
    # 各次调用的美元是各自四舍五入到 6 位的，结果的合计也是：差额（最多几个 1e-6）记到最后一行，账本合计与结果一分不差
    out[-1]["usd"] += total - sum(r["usd"] for r in out)
    return out


def _model_of(session) -> str:
    """会话建时快照的智能体模型：字符串，或 ``{"id": ...}``。"""
    model = _get(_get(session, "agent"), "model")
    return model if isinstance(model, str) else str(_get(model, "id") or "")


def session_snapshot(session) -> dict | None:
    """Managed Agents 会话对象（字典，`to_dict(mode="json")`）的累计用量 → 账本的几列。``list_cost`` 是官方按牌价算的累计费用
    （分为单位的整数串，含会话运行费），有它就用它（`list_cost`）；还没有就按价目表估（`price_table`，几次请求的合计不分高档）。
    没有 ``usage`` 回 None。"""
    usage = _get(session, "usage")
    if not usage:
        return None
    model = _model_of(session)
    five, hour = _writes(usage)
    snap = {"model": model, "input_tokens": _int(_get(usage, "input_tokens")),
            "output_tokens": _int(_get(usage, "output_tokens")), "cache_write_tokens": five + hour,
            "cache_read_tokens": _int(_get(usage, "cache_read_input_tokens")),
            "active_seconds": float(_get(usage, "active_seconds") or 0)}
    cost = _get(usage, "list_cost")
    amount = _get(cost, "amount")
    if amount not in (None, "") and str(_get(cost, "currency") or "USD").upper() == "USD":
        snap["usd"], snap["usd_source"] = Decimal(str(amount)) * CENT, "list_cost"
    else:
        snap["usd"] = prices.usd(model, snap["input_tokens"], snap["output_tokens"], five, snap["cache_read_tokens"], hour,
                                 single_request=False)
        snap["usd_source"] = "price_table"
    return snap


def day_delta(snapshot: dict, earlier: list[dict]) -> dict:
    """一段远端会话「今天这一行」应记的数：会话累计 − 这段会话在别的日子已经记下的各行之和（每项不小于 0）。同一天反复算、
    反复覆盖同一行（幂等）；中间漏了几轮，下一次自然补齐。"""
    row = {k: snapshot.get(k) for k in ("model", "usd_source")}
    for key in METRICS:
        now = snapshot.get(key)
        if key == "usd":
            if now is None:
                row[key] = None
                continue
            before = sum((Decimal(str(r.get(key) or 0)) for r in earlier), Decimal(0))
            row[key] = max(Decimal(now) - before, Decimal(0))
        elif key == "active_seconds":
            row[key] = max(float(now or 0) - sum(float(r.get(key) or 0) for r in earlier), 0.0)
        else:
            row[key] = max(int(now or 0) - sum(int(r.get(key) or 0) for r in earlier), 0)
    return row
