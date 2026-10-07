"""Claude 批处理（Message Batches，同价减半，ADR-018）的协议层：交一批、查状态、取结果。

只讲 Anthropic 协议：不导入 HTTP 客户端、不读配置与密钥、不碰数据库。HTTP 由调用方注入（``post`` / ``get``，与
``requests`` 同签名），请求头由 ``AnthropicClient.headers()`` 给。请求体与检查点键来自 ``AnthropicClient.batch_entry``，
与实时调用同一份；结果里的 ``message`` 与实时回复同一格式，直接当检查点内容用。
"""
import json
import re

from pearnly_cowork.ocr.providers.client import ProviderError

BATCHES = "/v1/messages/batches"
# custom_id 用检查点键（sha256 十六进制，64 位），正好在接口允许的格式里。
CUSTOM_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
# 一批最多 10 万条、256 MB（官方上限）；一份文件最多 50 页，远低于此，这里只防程序错。
MAX_REQUESTS = 100_000
ENDED = "ended"
SUCCEEDED = "succeeded"


def _url(target, path):
    return str(target["base_url"]).rstrip("/") + path


def _json(response, code):
    if response.status_code != 200:
        raise ProviderError(code, http_status=response.status_code, provider_code=None)
    try:
        body = response.json()
    except ValueError:
        raise ProviderError("ocr.provider_invalid_response", http_status=200, provider_code=None) from None
    if not isinstance(body, dict) or body.get("type") == "error":
        raise ProviderError("ocr.provider_invalid_response", http_status=200, provider_code=None)
    return body


def create(post, target, headers, entries, *, timeout_s=60):
    """``entries``：``[(检查点键, 请求体)]``。返回批次（``id``、``processing_status`` …）。"""
    requests_ = [{"custom_id": key, "params": body} for key, body in entries]
    if not requests_ or len(requests_) > MAX_REQUESTS or len({r["custom_id"] for r in requests_}) != len(requests_) \
            or any(not CUSTOM_ID.fullmatch(r["custom_id"]) for r in requests_):
        raise ValueError("batch entries must be 1..100000 distinct checkpoint keys")
    response = post(_url(target, BATCHES), headers=headers, json={"requests": requests_}, timeout=(30, timeout_s))
    return _json(response, "ocr.provider_auth" if response.status_code in (401, 403) else "ocr.provider_http_error")


def retrieve(get, target, headers, batch_id, *, timeout_s=30):
    if not isinstance(batch_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", batch_id):
        raise ValueError("invalid batch id")
    response = get(_url(target, f"{BATCHES}/{batch_id}"), headers=headers, timeout=(30, timeout_s))
    return _json(response, "ocr.provider_auth" if response.status_code in (401, 403) else "ocr.provider_http_error")


def results(get, target, headers, batch, *, timeout_s=120):
    """结束了的批次的全部结果：``{检查点键: (类型, message 或 error)}``。类型是 succeeded / errored / canceled / expired。"""
    if batch.get("processing_status") != ENDED:
        raise ValueError("batch has not ended")
    url = batch.get("results_url") or _url(target, f"{BATCHES}/{batch['id']}/results")
    response = get(url, headers=headers, timeout=(30, timeout_s))
    if response.status_code != 200:
        raise ProviderError("ocr.provider_http_error", http_status=response.status_code, provider_code=None)
    return parse_results(response.text)


def parse_results(text):
    """结果文件（每行一个 JSON）→ ``{custom_id: (类型, 内容)}``。认不出的行跳过（那条按没有结果处理、改走实时）。"""
    found = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        result = item.get("result") if isinstance(item, dict) else None
        key = item.get("custom_id") if isinstance(item, dict) else None
        if not isinstance(key, str) or not isinstance(result, dict):
            continue
        kind = result.get("type")
        if kind == SUCCEEDED and isinstance(result.get("message"), dict):
            found[key] = (SUCCEEDED, result["message"])
        elif kind in ("errored", "canceled", "expired"):
            found[key] = (kind, result.get("error"))
    return found
