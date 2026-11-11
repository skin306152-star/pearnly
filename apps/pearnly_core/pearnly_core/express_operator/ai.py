"""代录 AI 接手的模型请求转发（多产品共用，2026-11-03「CC 通用疑难处理」第 3 块）。

CC 桌面版代录 Express 时碰到规则表认不出的画面，交给 AI 接手（`companion/express_operator/helper.py`：先 Haiku 5.5，
再 Opus 5.5，都处理不了转人工）。Claude 密钥只在服务端：电脑把 Messages API 请求体交到机器通道（Cowork 的
``companion.operator_api.operator_ai``、共用机器通道的 ``cc_desktop.machine.operator_ai``，都先认电脑密钥），这里转给
Anthropic，原样回响应。

把关（这里写死，不靠电脑）：
- 只认两种调用（``check`` 看图判断、``turn`` 电脑操作一轮）、两个模型、请求体字段白名单（多一项整件拒收）、``max_tokens`` 上限；
- 每个代录批次的费用上限 :data:`BATCH_BUDGET_USD`（按 :data:`PRICE` 估算累计），到了回 ``{"error": "budget"}``，
  CC 桌面版收到就停下 AI 接手、这张单转人工。
"""
from __future__ import annotations

import os
import frappe

from pearnly_core.ai_usage import contracts as usage_contracts, ledger

KINDS = ("check", "turn")
HAIKU, OPUS = "claude-haiku-5-5", "claude-opus-5-5"
MODELS = (HAIKU, OPUS)
# 美元 / 百万 token：输入、输出、写缓存、读缓存（只估算费用上限用；与识别引擎 `claude_ocr.engine.PRICE`、账本 `ai_usage.prices`
# 同一份数，测试比对，机器通道不为两行价目引用整个识别引擎）。技术债（11-11 主会话定）：下次动识别时三份并成 `ai_usage.prices`
PRICE = {HAIKU: (0.10, 0.50, 0.125, 0.01), OPUS: (4.0, 20.0, 5.0, 0.20)}
# ``context_management`` 不在内：它要 beta 头，本转发不发 beta 头，带了 Anthropic 回 400（11-05 真机 anthropic_400）；
# CC 桌面版 1.12.15 起不再发它，带了按 ``invalid_field`` 整件拒收（旧版 1.12.14 的 AI 通道没接上，走不到这里）
FIELDS = ("model", "max_tokens", "system", "messages", "tools", "thinking", "output_config", "cache_control")
MAX_TOKENS = 8000
# 一批代录的 AI 接手费用上限（美元）：设计估算每次接手约 0.05–0.5 美元，一批最多接手 20 次（`helper.ComputerUseHelper`）
BATCH_BUDGET_USD = 3.0
URL = "https://api.anthropic.com/v1/messages"
VERSION = "2023-06-01"
TIMEOUT = 120
SPENT = "pearnly:operator_ai_usd:"
SPENT_TTL = 3 * 86400
ERROR_MAX = 80        # 回给电脑的错误码（``anthropic_<状态码>:<error.type>``）总长上限
MESSAGE_LOG_MAX = 300  # 日志里 Anthropic 错误说明最多记几个字


class Invalid(ValueError):
    pass


def request_of(body) -> tuple[str, str, dict]:
    """电脑交来的 ``{batch, kind, request}`` → (批次, 调用, 只留白名单字段的请求体)；不合规抛 :class:`Invalid`。"""
    if not isinstance(body, dict):
        raise Invalid("body")
    batch, kind, request = str(body.get("batch") or "").strip(), body.get("kind"), body.get("request")
    if not batch or len(batch) > 140 or kind not in KINDS or not isinstance(request, dict):
        raise Invalid("request")
    if request.get("model") not in MODELS or not isinstance(request.get("messages"), list):
        raise Invalid("model")
    if set(request) - set(FIELDS):
        raise Invalid("field")  # 白名单之外的字段整件拒收（不悄悄去掉）：CC 桌面版只发这几项
    cleaned = dict(request)
    try:
        cleaned["max_tokens"] = max(1, min(int(cleaned.get("max_tokens") or MAX_TOKENS), MAX_TOKENS))
    except (TypeError, ValueError):
        raise Invalid("max_tokens") from None
    return batch, kind, cleaned


def spent(batch) -> float:
    try:
        return float(frappe.cache.get_value(SPENT + batch) or 0)
    except (TypeError, ValueError):
        return 0.0


def usd(model, usage) -> float:
    usage = usage if isinstance(usage, dict) else {}
    pin, pout, pw, pr = PRICE[model]
    return (int(usage.get("input_tokens") or 0) * pin + int(usage.get("output_tokens") or 0) * pout
            + int(usage.get("cache_creation_input_tokens") or 0) * pw
            + int(usage.get("cache_read_input_tokens") or 0) * pr) / 1e6


def refused(status, payload) -> tuple[str, str, str]:
    """Anthropic 非 200 的响应 → (回给电脑的错误码, error.type, error.message 前 :data:`MESSAGE_LOG_MAX` 字)。错误码
    ``anthropic_<状态码>``，正文里认得出 ``error.type`` 时接 ``:<type>``，总长 :data:`ERROR_MAX`（电脑只特判 ``budget``，
    其余照码转人工）。纯函数。"""
    error = payload.get("error") if isinstance(payload, dict) else None
    error = error if isinstance(error, dict) else {}
    kind = "".join(ch for ch in str(error.get("type") or "") if ch.isalnum() or ch in "_-")[:40]
    message = " ".join(str(error.get("message") or "").split())[:MESSAGE_LOG_MAX]
    code = f"anthropic_{int(status)}" + (f":{kind}" if kind else "")
    return code[:ERROR_MAX], kind, message


def _post(key, body) -> dict:
    """发一次。非 200：回 ``{"error": 码, "refused": {status, type, message}}``（正文里的错误类型与说明，由 :func:`forward` 记日志后
    去掉，不回给电脑）。"""
    import requests

    response = requests.post(URL, json=body, timeout=TIMEOUT,
                             headers={"x-api-key": key, "anthropic-version": VERSION, "content-type": "application/json"})
    if response.status_code != 200:
        try:
            payload = response.json()
        except ValueError:
            payload = None
        code, error_type, message = refused(response.status_code, payload)
        return {"error": code, "refused": {"status": response.status_code, "type": error_type, "message": message}}
    return response.json()


def forward(body, *, post=_post) -> dict:
    """转一次：回 Anthropic 的响应原样（不合规、没配密钥、超上限、对方出错回 ``{"error": 码}``）。"""
    try:
        batch, kind, request = request_of(body)
    except Invalid as error:
        return {"error": f"invalid_{error}"}
    already = spent(batch)
    if already >= BATCH_BUDGET_USD:
        return {"error": "budget"}
    key = os.environ.get("PEARNLY_ANTHROPIC_API_KEY", "").strip()
    if not key:
        return {"error": "not_configured"}
    try:
        response = post(key, request)
    except Exception as error:  # noqa: BLE001 — 网络 / 对方出错：回码，电脑把这张单转人工
        frappe.logger("pearnly.operator").warning("operator AI call failed: %s", type(error).__name__)
        return {"error": "unavailable"}
    if isinstance(response.get("refused"), dict):
        # Anthropic 拒了：只记状态码、错误类型与说明、模型、调用、批次（不记请求体与截图），回给电脑的只有码
        why = response["refused"]
        frappe.logger("pearnly.operator").warning(
            "operator AI refused: status %s type %s model %s kind %s batch %s: %s", why.get("status"),
            why.get("type") or "-", request["model"], kind, batch, str(why.get("message") or "")[:MESSAGE_LOG_MAX])
        return {"error": str(response.get("error") or "")[:ERROR_MAX]}
    if not response.get("error"):
        cost = usd(request["model"], response.get("usage"))
        frappe.cache.set_value(SPENT + batch, already + cost, expires_in_sec=SPENT_TTL)
        frappe.logger("pearnly.operator").info("operator AI %s %s batch %s usd %.4f", kind, request["model"], batch, cost)
        ledger.record("operator", [usage_contracts.call(request["model"], response.get("usage"))], ref=f"{batch}:{kind}")
    return response
